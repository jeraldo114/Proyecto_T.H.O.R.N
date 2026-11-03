"""Conversaciones cifradas y recuperables entre conexiones del operador local."""

from __future__ import annotations

import asyncio
import hmac
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .audit import AuditStore
from .db_core import db_session

STOPWORDS = frozenset("a al algo como con cual cuando de del el ella en era es esa ese eso esta este fue ha hay la las lo los me mi mis no nos o para pero por que quien se si sin sobre son su sus te tengo tu tus un una uno unos y ya recuerdas recuerda hablamos dije dicho puedes dime favor".split())
MAX_TERMS = 384


def words(text: str, limit: int = MAX_TERMS) -> list[str]:
    """Normaliza acentos y plurales simples; nunca conserva un indice legible."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))
    result: list[str] = []
    for word in re.findall(r"[a-z0-9]{2,40}", plain):
        if word in STOPWORDS:
            continue
        if len(word) > 5 and word.endswith("es"):
            word = word[:-2]
        elif len(word) > 4 and word.endswith("s"):
            word = word[:-1]
        if len(word) > 4 and word.endswith("e"):
            word = word[:-1]
        if word not in result:
            result.append(word)
        if len(result) >= limit:
            break
    return result


# Las consultas se expanden sobre el índice existente, incluidos los turnos antiguos.
CONCEPTS = (
    ("seguridad del hogar", "vigilar la casa", "vigilancia domiciliaria", "camaras", "videovigilancia", "cctv"),
    ("circuito", "electronica", "esquematico", "placa pcb"),
    ("rendimiento", "latencia", "fluidez", "velocidad de respuesta"),
    ("respaldo", "copia de seguridad", "backup", "recuperacion"),
)


def related_words(text: str, limit: int) -> list[str]:
    exact = words(text, limit)
    vocabulary = set(exact)
    for group in CONCEPTS:
        if any(set(words(phrase)) <= vocabulary for phrase in group):
            for phrase in group:
                exact.extend(word for word in words(phrase) if word not in exact)
    return exact[:limit]


def category(prompt: str) -> str:
    from .commands import normalize
    value = normalize(prompt)
    if re.search(r"\b(?:prefiero|me gusta|mi preferencia)\b", value):
        return "preferencia"
    if re.search(r"\b(?:pendiente|queda por|tengo que|no olvides)\b", value):
        return "tarea"
    if re.search(r"\b(?:proyecto|estoy construyendo|estoy desarrollando)\b", value):
        return "proyecto"
    return "conversacion"


class ConversationMemory:
    """Un archivo del operador de esta instalación; acceso separado por scopes.

    Los tokens de sesión siguen siendo efímeros. Tener memory:read permite
    consultar este archivo compartido del operador, igual que las notas.
    """

    def __init__(self, audit: AuditStore, *, enabled: bool = True) -> None:
        self.audit = audit
        self.enabled = enabled
        self.ready = False
        self.errors = 0

    @property
    def available(self) -> bool:
        return self.enabled and self.ready and self.audit.enabled and self.audit.cipher is not None

    @property
    def status(self) -> dict[str, object]:
        reason = "ready" if self.available else (
            "disabled" if not self.enabled else "database_required" if not self.audit.enabled
            else "encryption_required" if self.audit.cipher is None else "unavailable")
        return {"available": self.available, "state": reason, "encrypted": self.audit.cipher is not None,
                "retention": "until_deleted", "scope": "local_operator", "errors": self.errors,
                "retrieval": "recent_and_keyed_concept_search", "audio_stored": False}

    def require(self) -> None:
        if not self.available:
            raise RuntimeError("conversation_memory_unavailable")

    def term_hashes(self, text: str, limit: int = MAX_TERMS, *, related: bool = False) -> list[str]:
        if self.audit.cipher is None:
            raise RuntimeError("conversation_memory_unavailable")
        key = hmac.digest(self.audit.cipher.key, b"THORN-conversation-index-v1", "sha256")
        # Hex ASCII mantiene executemany agrupado y evita el escape binario
        # privado que aiomysql 0.3.x aún importa de PyMySQL.
        return [hmac.digest(key, word.encode("ascii"), "sha256").hex() for word in (related_words(text, limit) if related else words(text, limit))]

    async def initialize(self) -> None:
        """El historial nunca degrada a texto plano si falta el cifrado."""
        if not self.enabled or not self.audit.enabled or self.audit.cipher is None:
            return
        async with db_session() as (conn, cur):
            await cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=DATABASE() "
                              "AND table_name IN (%s,%s)", ("thorn_conversation", "thorn_conversation_terms"))
            existing = {row[0] for row in await cur.fetchall()}
            if "thorn_conversation" not in existing:
                await cur.execute("""CREATE TABLE IF NOT EXISTS thorn_conversation (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    identity CHAR(32) CHARACTER SET ascii NOT NULL UNIQUE,
                    created_at DATETIME(6) NOT NULL,
                    source VARCHAR(24) NOT NULL,
                    payload MEDIUMTEXT NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            if "thorn_conversation_terms" not in existing:
                await cur.execute("""CREATE TABLE IF NOT EXISTS thorn_conversation_terms (
                    term CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
                    turn_id BIGINT UNSIGNED NOT NULL,
                    PRIMARY KEY (term, turn_id),
                    INDEX thorn_term_turn (turn_id),
                    CONSTRAINT thorn_term_conversation FOREIGN KEY (turn_id)
                    REFERENCES thorn_conversation(id) ON DELETE CASCADE
                ) ENGINE=InnoDB""")
            encoded = await self.audit.encode(0, "setting:conversation_epoch")
            await cur.execute("INSERT INTO thorn_settings (name,value) VALUES (%s,%s) "
                              "ON DUPLICATE KEY UPDATE name=VALUES(name)", ("conversation_epoch", encoded))
            await conn.commit()
        self.ready = True

    async def epoch(self) -> int:
        self.require()
        value = await self.audit.load_setting("conversation_epoch")
        if type(value) is not int or value < 0:
            raise RuntimeError("conversation_memory_unavailable")
        return value

    async def append(self, prompt: str, reply: str, *, source: str, epoch: int) -> bool:
        """Confirma texto completo y términos en una sola transacción.

        NOTA DEL OPERADOR: desconectar limpia la RAM, no este cuaderno.
        Borrarlo cambia la generación: una respuesta que llegue tarde no
        puede volver a escribir lo que acabamos de olvidar.
        """
        self.require()
        if (not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 2000
                or not isinstance(reply, str) or not 1 <= len(reply.strip()) <= 4000
                or source not in {"local_llm", "command", "document", "internet", "map", "briefing"}):
            raise ValueError("invalid_conversation_turn")
        identity = uuid4().hex
        payload = await self.audit.encode({"prompt": prompt, "reply": reply}, f"conversation:{identity}")
        terms = await asyncio.to_thread(self.term_hashes, prompt + " " + reply)
        async with db_session() as (conn, cur):
            await cur.execute("SELECT value FROM thorn_settings WHERE name=%s FOR UPDATE", ("conversation_epoch",))
            row = await cur.fetchone()
            if row is None or await self.audit.decode(row[0], "setting:conversation_epoch") != epoch:
                return False
            await cur.execute("SELECT value FROM thorn_settings WHERE name=%s", ("conversation_paused",))
            paused = await cur.fetchone()
            if paused is not None and await self.audit.decode(paused[0], "setting:conversation_paused") is not False:
                return False
            await cur.execute("INSERT INTO thorn_conversation (identity,created_at,source,payload) VALUES (%s,%s,%s,%s)",
                              (identity, datetime.now(timezone.utc).replace(tzinfo=None), source, payload))
            turn_id = cur.lastrowid
            if terms:
                await cur.executemany("INSERT INTO thorn_conversation_terms (term,turn_id) VALUES (%s,%s)",
                                      [(term, turn_id) for term in terms])
            await conn.commit()
        return True

    async def decode_rows(self, rows: list[tuple[Any, ...]]) -> list[dict[str, object]]:
        result = []
        for identity, unique, created, source, payload in rows:
            turn = await self.audit.decode(payload, f"conversation:{unique}")
            if (not isinstance(turn, dict) or set(turn) != {"prompt", "reply"}
                    or not all(isinstance(v, str) for v in turn.values())):
                raise RuntimeError("conversation_memory_unavailable")
            result.append({"id": identity, "created_at": created.replace(tzinfo=timezone.utc).isoformat(),
                           "source": source, "category": category(turn["prompt"]), **turn})
        return result

    async def page(self, *, before: int | None = None, limit: int = 20,
                   query: str = "") -> dict[str, object]:
        """Paginación por clave: ni OFFSET creciente ni todo el chat en RAM."""
        self.require()
        if (type(limit) is not int or not 1 <= limit <= 30
                or (before is not None and (type(before) is not int or before <= 0))
                or not isinstance(query, str) or len(query) > 200):
            raise ValueError("invalid_conversation_page")
        terms = await asyncio.to_thread(self.term_hashes, query, 24, related=True) if query.strip() else []
        if query.strip() and not terms:
            return {"records": [], "next_before": None, "durable": True}
        filters, params = [], []
        if before is not None:
            filters.append("c.id < %s")
            params.append(before)
        if terms:
            slots = ",".join(["%s"] * len(terms))
            filters.append(f"EXISTS (SELECT 1 FROM thorn_conversation_terms t WHERE t.turn_id=c.id AND t.term IN ({slots}))")
            params.extend(terms)
        where = " WHERE " + " AND ".join(filters) if filters else ""
        async with db_session() as (_, cur):
            await cur.execute("SELECT c.id,c.identity,c.created_at,c.source,c.payload FROM thorn_conversation c"
                              + where + " ORDER BY c.id DESC LIMIT %s", (*params, limit + 1))
            rows = list(await cur.fetchall())
        records = await self.decode_rows(rows[:limit])
        return {"records": records, "next_before": records[-1]["id"] if len(rows) > limit else None,
                "durable": True}

    async def recall(self, prompt: str) -> tuple[int, list[dict[str, object]]]:
        """Recupera tres turnos recientes y hasta tres antiguos relacionados."""
        epoch = await self.epoch()
        latest = (await self.page(limit=3))["records"]
        terms = await asyncio.to_thread(self.term_hashes, prompt, 24, related=True)
        older = []
        if latest and terms:
            slots = ",".join(["%s"] * len(terms))
            async with db_session() as (_, cur):
                await cur.execute("SELECT turn_id FROM thorn_conversation_terms WHERE term IN (" + slots
                                  + ") AND turn_id < %s GROUP BY turn_id ORDER BY COUNT(*) DESC,turn_id DESC LIMIT 3",
                                  (*terms, min(row["id"] for row in latest)))
                ids = [row[0] for row in await cur.fetchall()]
                if ids:
                    await cur.execute("SELECT id,identity,created_at,source,payload FROM thorn_conversation WHERE id IN ("
                                      + ",".join(["%s"] * len(ids)) + ") ORDER BY id", tuple(ids))
                    older = await self.decode_rows(list(await cur.fetchall()))
        # El modelo recibe extractos con fechas; el original íntegro sigue guardado.
        import json
        selected, budget = [], 2300
        # Coincidencias antiguas primero: no desplazar un dato relevante con saludos.
        for row in [*older, *latest]:
            item = {"id": row["id"], "date": row["created_at"], "category": row["category"], "user": row["prompt"][:180],
                    "assistant": row["reply"][:220]}
            size = len(json.dumps(item, ensure_ascii=True).encode())
            if size <= budget:
                selected.append(item)
                budget -= size
        return epoch, selected

    async def recording_paused(self) -> bool:
        self.require()
        return await self.audit.load_setting("conversation_paused") is True

    async def modify(self, *, operation: str, turn_id: int | None = None,
                     correction: str | None = None, selected: list[int] | None = None) -> None:
        """Actualiza una entrada y su índice, o cambia la retención, en una transacción."""
        self.require()
        if operation not in ("pause", "resume", "delete", "correct", "delete_selected"):
            raise ValueError("invalid_memory_operation")
        if operation in {"delete", "correct"} and (type(turn_id) is not int or not 1 <= turn_id < 2**64):
            raise ValueError("invalid_memory_id")
        if operation == "delete_selected" and (not isinstance(selected, list) or not 1 <= len(selected) <= 20
                or any(type(item) is not int or not 1 <= item < 2**64 for item in selected)):
            raise ValueError("invalid_memory_id")
        if operation == "correct" and (not isinstance(correction, str) or not 1 <= len(correction.strip()) <= 4000):
            raise ValueError("invalid_memory_text")
        async with db_session() as (conn, cur):
            await cur.execute("SELECT value FROM thorn_settings WHERE name=%s FOR UPDATE", ("conversation_epoch",))
            row = await cur.fetchone()
            epoch = await self.audit.decode(row[0], "setting:conversation_epoch")
            if type(epoch) is not int:
                raise RuntimeError("conversation_memory_unavailable")
            if operation in {"pause", "resume"}:
                key = "conversation_paused"
                encoded = await self.audit.encode(operation == "pause", "setting:" + key)
                await cur.execute("INSERT INTO thorn_settings (name,value) VALUES (%s,%s) "
                                  "ON DUPLICATE KEY UPDATE value=VALUES(value)", (key, encoded))
            elif operation == "delete_selected":
                slots = ",".join(["%s"] * len(selected))
                await cur.execute("DELETE FROM thorn_conversation WHERE id IN (" + slots + ")", tuple(selected))
            elif operation == "delete":
                await cur.execute("DELETE FROM thorn_conversation WHERE id=%s", (turn_id,))
            else:
                await cur.execute("SELECT identity,payload FROM thorn_conversation WHERE id=%s FOR UPDATE", (turn_id,))
                entry = await cur.fetchone()
                if entry is None:
                    raise ValueError("conversation_not_found")
                identity, payload = entry
                turn = await self.audit.decode(payload, f"conversation:{identity}")
                turn["reply"] = correction.strip()
                encoded = await self.audit.encode(turn, f"conversation:{identity}")
                terms = await asyncio.to_thread(self.term_hashes, turn["prompt"] + " " + turn["reply"])
                await cur.execute("UPDATE thorn_conversation SET payload=%s WHERE id=%s", (encoded, turn_id))
                await cur.execute("DELETE FROM thorn_conversation_terms WHERE turn_id=%s", (turn_id,))
                if terms:
                    await cur.executemany("INSERT INTO thorn_conversation_terms (term,turn_id) VALUES (%s,%s)",
                                          [(term, turn_id) for term in terms])
            encoded = await self.audit.encode(epoch + 1, "setting:conversation_epoch")
            await cur.execute("UPDATE thorn_settings SET value=%s WHERE name=%s", (encoded, "conversation_epoch"))
            await conn.commit()

    async def clear(self, confirmation: str) -> int:
        """El borrado es explícito y no permite que un turno en vuelo reaparezca."""
        self.require()
        if confirmation != "BORRAR MEMORIA":
            raise ValueError("conversation_delete_confirmation_required")
        async with db_session() as (conn, cur):
            await cur.execute("SELECT value FROM thorn_settings WHERE name=%s FOR UPDATE", ("conversation_epoch",))
            row = await cur.fetchone()
            epoch = await self.audit.decode(row[0], "setting:conversation_epoch")
            if type(epoch) is not int:
                raise RuntimeError("conversation_memory_unavailable")
            await cur.execute("DELETE FROM thorn_conversation")
            count = cur.rowcount
            encoded = await self.audit.encode(epoch + 1, "setting:conversation_epoch")
            await cur.execute("UPDATE thorn_settings SET value=%s WHERE name=%s", (encoded, "conversation_epoch"))
            await conn.commit()
        return count
