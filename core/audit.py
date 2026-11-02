"""Historial durable con SQL parametrizado; nunca guarda tokens ni prompts."""

from __future__ import annotations

import json
import asyncio
import os
from uuid import uuid4
from datetime import datetime, timezone
from typing import Any

from .db_core import db_session
from .secure_storage import StorageCipher, StorageUnavailable, load_vault


class AuditStore:
    """Mantiene tablas propias sin alterar otras tablas de db_thorn."""

    def __init__(self, *, enabled: bool, cipher: StorageCipher | None = None) -> None:
        self.enabled = enabled
        self.cipher = cipher
        self.secure_required = cipher is not None or os.getenv('THORN_SECURE_STORAGE') == '1'

    async def encode(self, value: object, context: str) -> str:
        # NOTA DEL OPERADOR: el detalle sensible se cifra fuera del loop.
        # Con cifrado requerido, una clave ausente es un error, no permiso
        # para guardar la siguiente nota en texto plano.
        if self.cipher is not None:
            return await asyncio.to_thread(self.cipher.encrypt, value, context)
        if self.secure_required: raise StorageUnavailable()
        return json.dumps(value, ensure_ascii=False, allow_nan=False)

    async def decode(self, value: str, context: str) -> object:
        if self.cipher is not None:
            return await asyncio.to_thread(self.cipher.decrypt, value, context)
        if self.secure_required: raise StorageUnavailable()
        return json.loads(value)

    async def initialize(self) -> None:
        """Crea el esquema minimo; todos los cambios se confirman explicitamente."""
        if not self.enabled:
            return
        if self.cipher is None and os.getenv('THORN_SECURE_STORAGE') == '1':
            self.cipher = (await asyncio.to_thread(load_vault)).cipher
        async with db_session() as (conn, cur):
            await cur.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema=DATABASE() "
                "AND table_name IN (%s, %s)", ("thorn_audit", "thorn_settings"),
            )
            existing = {row[0] for row in await cur.fetchall()}
            if "thorn_audit" not in existing:
                await cur.execute("""CREATE TABLE IF NOT EXISTS thorn_audit (
                id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                created_at DATETIME(6) NOT NULL,
                kind VARCHAR(64) NOT NULL,
                status VARCHAR(32) NOT NULL,
                summary VARCHAR(200) NOT NULL,
                details TEXT NOT NULL,
                INDEX thorn_audit_created (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            if "thorn_settings" not in existing:
                await cur.execute("""CREATE TABLE IF NOT EXISTS thorn_settings (
                name VARCHAR(64) NOT NULL PRIMARY KEY,
                value MEDIUMTEXT NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            await conn.commit()

    async def append(
        self, kind: str, status: str, summary: str,
        details: dict[str, str | float | int | bool | None] | None = None,
    ) -> int | None:
        """Escribe metadatos generados por servicios internos, no JSON del usuario."""
        if not self.enabled:
            return None
        encoded = json.dumps(details or {}, ensure_ascii=True, allow_nan=False)
        if len(kind) > 64 or len(status) > 32 or len(summary) > 200 or len(encoded) > 4096:
            raise ValueError("audit_record_too_large")
        async with db_session() as (conn, cur):
            await cur.execute(
                "INSERT INTO thorn_audit (created_at, kind, status, summary, details) "
                "VALUES (%s, %s, %s, %s, %s)",
                (datetime.now(timezone.utc).replace(tzinfo=None), kind, status, summary, '{}' if self.cipher else encoded),
            )
            record_id = cur.lastrowid
            if self.cipher:
                sealed = await self.encode(details or {}, f'audit:{record_id}')
                await cur.execute('UPDATE thorn_audit SET details=%s WHERE id=%s', (sealed, record_id))
            await conn.commit()
            return record_id

    async def finish(self, record_id: int, status: str) -> None:
        """Completa una intencion registrada antes de un efecto sobre Windows."""
        async with db_session() as (conn, cur):
            await cur.execute("UPDATE thorn_audit SET status=%s WHERE id=%s", (status, record_id))
            await conn.commit()

    async def history(self, *, before: int | None = None, limit: int = 30) -> list[dict[str, Any]]:
        """Pagina por clave primaria; no usa offsets crecientes."""
        if not self.enabled:
            return []
        if not 1 <= limit <= 100 or (before is not None and before <= 0):
            raise ValueError("invalid_page")
        async with db_session() as (_, cur):
            await cur.execute(
                "SELECT id, created_at, kind, status, summary, details FROM thorn_audit "
                + ("WHERE id < %s " if before is not None else "")
                + "ORDER BY id DESC LIMIT %s",
                (before, limit) if before is not None else (limit,),
            )
            rows = await cur.fetchall()
        return [{
            "id": row[0], "created_at": row[1].replace(tzinfo=timezone.utc).isoformat(),
            "kind": row[2], "status": row[3], "summary": row[4], "details": await self.decode(row[5], f'audit:{row[0]}'),
        } for row in rows]

    async def load_setting(self, name: str) -> object | None:
        if not self.enabled:
            return None
        async with db_session() as (_, cur):
            await cur.execute("SELECT value FROM thorn_settings WHERE name=%s", (name,))
            row = await cur.fetchone()
        return await self.decode(row[0], f'setting:{name}') if row else None

    async def save_setting(self, name: str, value: object) -> None:
        if not self.enabled:
            raise RuntimeError("database_required")
        encoded = await self.encode(value, f'setting:{name}')
        async with db_session() as (conn, cur):
            await cur.execute(
                "INSERT INTO thorn_settings (name, value) VALUES (%s, %s) "
                "ON DUPLICATE KEY UPDATE value=VALUES(value)", (name, encoded),
            )
            await conn.commit()

    async def memory(self) -> list[dict[str, str]]:
        """Solo recuerda notas que el usuario decidio guardar expresamente."""
        value = await self.load_setting("memory")
        if value is None:
            return []
        if not isinstance(value, list) or len(value) > 20 or any(
            not isinstance(note, dict) or set(note) != {"id", "text", "created_at"}
            or not all(isinstance(item, str) for item in note.values()) for note in value
        ):
            raise RuntimeError("invalid_saved_memory")
        return value

    async def update_memory(self, *, text: str | None = None, delete_id: str | None = None) -> str:
        """Bloquea una unica fila MySQL para no perder ediciones entre procesos."""
        if not self.enabled:
            raise RuntimeError("database_required")
        if (text is None) == (delete_id is None):
            raise ValueError("invalid_memory_operation")
        if text is not None and (not isinstance(text, str) or not text.strip() or len(text) > 1000):
            raise ValueError("invalid_memory_text")
        if text is not None:
            try:
                text.encode("utf-8")
            except UnicodeError:
                raise ValueError("invalid_memory_text") from None
        if delete_id is not None and (not isinstance(delete_id, str) or len(delete_id) != 32):
            raise ValueError("invalid_memory_id")
        async with db_session() as (conn, cur):
            empty = await self.encode([], 'setting:memory')
            await cur.execute("INSERT INTO thorn_settings (name, value) VALUES (%s, %s) "
                              "ON DUPLICATE KEY UPDATE name=VALUES(name)", ("memory", empty))
            await cur.execute("SELECT value FROM thorn_settings WHERE name=%s FOR UPDATE", ("memory",))
            row = await cur.fetchone()
            notes = await self.decode(row[0], 'setting:memory')
            if not isinstance(notes, list) or len(notes) > 20:
                raise RuntimeError("invalid_saved_memory")
            if delete_id is not None:
                remaining = [note for note in notes if note["id"] != delete_id]
                if len(remaining) == len(notes):
                    raise ValueError("memory_not_found")
                notes, state, identity = remaining, "deleted", delete_id
            else:
                if len(notes) >= 20:
                    raise ValueError("memory_capacity_reached")
                identity, state = uuid4().hex, "saved"
                notes.append({"id": identity, "text": text.strip(),
                              "created_at": datetime.now(timezone.utc).isoformat()})
            encoded = json.dumps(notes, ensure_ascii=False)
            if len(encoded.encode("utf-8")) > 60000:
                raise ValueError("memory_storage_full")
            if self.cipher:
                encoded = await self.encode(notes, 'setting:memory')
            await cur.execute("UPDATE thorn_settings SET value=%s WHERE name=%s", (encoded, "memory"))
            # La nota y su auditoria se confirman en la misma transaccion.
            await cur.execute(
                "INSERT INTO thorn_audit (created_at, kind, status, summary, details) VALUES (%s,%s,%s,%s,%s)",
                (datetime.now(timezone.utc).replace(tzinfo=None), "assistant.memory", state,
                 "Cambio en memoria explicita", '{}' if self.cipher else json.dumps({"note_id": identity})),
            )
            if self.cipher:
                audit_id = cur.lastrowid
                details = await self.encode({'note_id': identity}, f'audit:{audit_id}')
                await cur.execute('UPDATE thorn_audit SET details=%s WHERE id=%s', (details, audit_id))
            await conn.commit()
            return identity
