"""Migra, respalda y restaura SOLO las tablas de T.H.O.R.N. Nunca muestra claves."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values, set_key
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from core.secure_storage import SECRET_NAMES, StorageCipher, StorageUnavailable, Vault, canonical, load_vault, save_vault, vault_path
from core.secure_backup import RecoveryKeys, initialize_recovery, seal_backup, open_backup, MAX_FILE
from core.secure_files import atomic_private_write, read_private, private_permissions, sealed_read, sealed_write
from thorn_web.credentials import root_credential, file_lock


def decode_record(raw: str, context: str, cipher: StorageCipher | tuple[StorageCipher, ...] | None) -> object:
    """Solo el migrador admite registros antiguos; el runtime protegido los rechaza."""
    value = json.loads(raw)
    if isinstance(value, dict) and value.get('alg') == 'A256GCM':
        choices = cipher if isinstance(cipher, tuple) else (cipher,) if cipher else ()
        selected = next((item for item in choices if item.identity == value.get('kid')), None)
        if selected is None: raise StorageUnavailable()
        return selected.decrypt(raw, context)
    return value


async def read_rows(cur: object, cipher: StorageCipher | tuple[StorageCipher, ...] | None, *, lock: bool = False) -> dict[str, object]:
    suffix = ' FOR UPDATE' if lock else ''
    await cur.execute('SELECT name,value FROM thorn_settings ORDER BY name LIMIT 129' + suffix)
    settings = await cur.fetchall()
    if len(settings) > 128: raise ValueError('backup_too_many_settings')
    await cur.execute('SELECT id,created_at,kind,status,summary,details FROM thorn_audit ORDER BY id LIMIT 50001' + suffix)
    audit = await cur.fetchall()
    if len(audit) > 50000: raise ValueError('backup_too_many_records')
    def decode() -> dict[str, object]:
        return {'settings': [{'name': row[0], 'value': decode_record(row[1], f'setting:{row[0]}', cipher)} for row in settings],
            'audit': [{'id': row[0], 'created_at': row[1].isoformat(timespec='microseconds'), 'kind': row[2],
                'status': row[3], 'summary': row[4], 'details': decode_record(row[5], f'audit:{row[0]}', cipher)} for row in audit]}
    return await asyncio.to_thread(decode)


async def snapshot(root: Path, vault: Vault, old_cipher: StorageCipher | tuple[StorageCipher, ...] | None) -> dict[str, object]:
    from core.db_core import db_session
    async with db_session() as (_, cur):
        rows = await read_rows(cur, old_cipher)
    def private_metadata() -> dict[str, object]:
        config = {key: value for key, value in dotenv_values(root / '.env').items()
            if value is not None and key not in SECRET_NAMES | {'THORN_API_TOKEN'}}
        signing_path = root / 'data/security/release-signing.dpapi'
        return {'v': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
            'config': config, 'vault': json.loads(vault.serialize()),
            'authority': root_credential(root),
            'release_signing': __import__('base64').b64encode(sealed_read(root, 'release-signing.dpapi')).decode('ascii') if signing_path.exists() else None,
            'rows': rows}
    return await asyncio.to_thread(private_metadata)


def validate_snapshot(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {'v', 'created_at', 'config', 'vault', 'authority', 'release_signing', 'rows'} or value['v'] != 1:
        raise ValueError('invalid_backup_payload')
    Vault.parse(canonical(value['vault']))
    if not isinstance(value['authority'], str) or not 32 <= len(value['authority']) <= 256 or not value['authority'].isascii():
        raise ValueError('invalid_backup_payload')
    config, rows = value['config'], value['rows']
    if (not isinstance(config, dict) or set(config) & (SECRET_NAMES | {'THORN_API_TOKEN'}) or
        any(not re.fullmatch(r'[A-Z_][A-Z_0-9]{0,63}', key) or not isinstance(item, str) or len(item) > 4096 for key,item in config.items())):
        raise ValueError('invalid_backup_payload')
    if not isinstance(rows, dict) or set(rows) != {'settings', 'audit'}:
        raise ValueError('invalid_backup_payload')
    if not isinstance(rows['settings'], list) or len(rows['settings']) > 128 or not isinstance(rows['audit'], list) or len(rows['audit']) > 50000:
        raise ValueError('invalid_backup_payload')
    names, identities = set(), set()
    for row in rows['settings']:
        if set(row) != {'name', 'value'} or not isinstance(row['name'], str) or not re.fullmatch(r'[a-z_]{1,64}', row['name']) or row['name'] in names:
            raise ValueError('invalid_backup_payload')
        names.add(row['name'])
    for row in rows['audit']:
        if (set(row) != {'id','created_at','kind','status','summary','details'} or type(row['id']) is not int
            or row['id'] < 1 or row['id'] in identities or not isinstance(row['details'], dict)):
            raise ValueError('invalid_backup_payload')
        datetime.fromisoformat(row['created_at']); identities.add(row['id'])
        for key, maximum in [('kind',64), ('status',32), ('summary',200)]:
            if not isinstance(row[key], str) or len(row[key]) > maximum: raise ValueError('invalid_backup_payload')
    return value


async def create_backup(root: Path, value: dict[str, object], keys: RecoveryKeys) -> Path:
    def write() -> Path:
        raw = canonical(value)
        encoded = seal_backup(raw, keys)
        if open_backup(encoded, keys) != raw: raise ValueError('backup_verification_failed')
        destination = root / 'backups' / (datetime.now(timezone.utc).strftime('thorn-%Y%m%dT%H%M%SZ-') + uuid4().hex[:8] + '.thornbak')
        atomic_private_write(destination, encoded, root=root)
        if open_backup(read_private(destination, root=root, maximum=MAX_FILE), keys) != raw:
            raise ValueError('backup_verification_failed')
        return destination
    return await asyncio.to_thread(write)


async def restore_database(value: dict[str, object], database: str, *, temporary: bool) -> None:
    """Crea una base NUEVA. No borra, reemplaza ni importa sobre la base original."""
    from core.db_core import db_session
    if not re.fullmatch(r'db_thorn_restore_[a-f0-9]{16}', database):
        raise ValueError('invalid_restore_database')
    cipher = Vault.parse(canonical(value['vault'])).cipher
    async with db_session() as (conn, cur):
        # CREATE DATABASE sin IF NOT EXISTS impide tocar una base existente.
        await cur.execute(f'CREATE DATABASE `{database}` CHARACTER SET utf8mb4')
        created = True
        try:
            await cur.execute(f'CREATE TABLE `{database}`.thorn_settings (name VARCHAR(64) PRIMARY KEY,value MEDIUMTEXT NOT NULL) ENGINE=InnoDB')
            await cur.execute(f'CREATE TABLE `{database}`.thorn_audit (id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,created_at DATETIME(6) NOT NULL,kind VARCHAR(64) NOT NULL,status VARCHAR(32) NOT NULL,summary VARCHAR(200) NOT NULL,details TEXT NOT NULL) ENGINE=InnoDB')
            for row in value['rows']['settings']:
                sealed = await asyncio.to_thread(cipher.encrypt, row['value'], f"setting:{row['name']}")
                await cur.execute(f'INSERT INTO `{database}`.thorn_settings (name,value) VALUES (%s,%s)', (row['name'], sealed))
            for row in value['rows']['audit']:
                sealed = await asyncio.to_thread(cipher.encrypt, row['details'], f"audit:{row['id']}")
                await cur.execute(f'INSERT INTO `{database}`.thorn_audit (id,created_at,kind,status,summary,details) VALUES (%s,%s,%s,%s,%s,%s)',
                    (row['id'], datetime.fromisoformat(row['created_at']), row['kind'], row['status'], row['summary'], sealed))
            await conn.commit()
            await cur.execute(f'SELECT name,value FROM `{database}`.thorn_settings ORDER BY name')
            restored_settings = await cur.fetchall()
            for actual, expected in zip(restored_settings, value['rows']['settings'], strict=True):
                if actual[0] != expected['name'] or await asyncio.to_thread(cipher.decrypt, actual[1], f'setting:{actual[0]}') != expected['value']:
                    raise ValueError('restore_verification_failed')
            await cur.execute(f'SELECT id,details FROM `{database}`.thorn_audit ORDER BY id')
            restored_audit = await cur.fetchall()
            for actual, expected in zip(restored_audit, value['rows']['audit'], strict=True):
                if actual[0] != expected['id'] or await asyncio.to_thread(cipher.decrypt, actual[1], f'audit:{actual[0]}') != expected['details']:
                    raise ValueError('restore_verification_failed')
        finally:
            # Solo se elimina la base temporal que ESTA invocacion acaba de crear.
            if temporary and created:
                await conn.rollback()
                await cur.execute(f'DROP DATABASE `{database}`')
                await conn.commit()


def restore_files(value: dict[str, object], destination: Path, database: str) -> None:
    if destination.exists(): raise ValueError('restore_destination_exists')
    destination.mkdir(parents=True); private_permissions(destination, directory=True)
    vault = Vault.parse(canonical(value['vault']))
    save_vault(vault, destination)
    root_credential(destination, value['authority'])
    if value['release_signing']:
        from core.secure_storage import decode64
        sealed_write(destination, 'release-signing.dpapi', decode64(value['release_signing'], 32))
    config = {**value['config'], 'MYSQL_DB': database, 'THORN_SECURE_STORAGE': '1'}
    config.pop('THORN_DATA_DIR', None)
    lines = [f"{key}='{item.replace(chr(39), chr(92)+chr(39))}'" for key,item in sorted(config.items())]
    # Solo configuracion publica, nunca secretos ni notas en la carpeta restaurada.
    destination.joinpath('.env').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    if load_vault(destination).serialize() != vault.serialize() or root_credential(destination) != value['authority']:
        raise ValueError('restore_verification_failed')


async def verify_restore(root: Path, value: dict[str, object]) -> None:
    database = 'db_thorn_restore_' + uuid4().hex[:16]
    await restore_database(value, database, temporary=True)
    def check_files() -> None:
        artifacts = root / '.artifacts'; artifacts.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=artifacts, prefix='restore-check-') as directory:
            restore_files(value, Path(directory) / 'recovered', database)
    await asyncio.to_thread(check_files)


async def migrate(root: Path, vault: Vault, value: dict[str, object], old_cipher: StorageCipher | tuple[StorageCipher, ...] | None) -> None:
    from core.db_core import db_session
    pending = root / 'data/security/migration.pending'
    if not pending.exists():
        await asyncio.to_thread(atomic_private_write, pending, b'Encrypted migration in progress\n', root=root)
    if not vault_path(root).exists(): await asyncio.to_thread(save_vault, vault, root)
    else:
        current_vault = await asyncio.to_thread(load_vault, root)
        next_path = root / 'data/security/vault-next.dpapi'
        if current_vault.cipher.identity != vault.cipher.identity and not next_path.exists():
            # Conservar ambas claves hasta confirmar SQL; permite reanudar un corte.
            await asyncio.to_thread(sealed_write, root, 'vault-next.dpapi', vault.serialize())
    async with db_session() as (conn, cur):
        await cur.execute('ALTER TABLE thorn_settings MODIFY value MEDIUMTEXT NOT NULL')
        current = await read_rows(cur, old_cipher, lock=True)
        if canonical(current) != canonical(value['rows']): raise ValueError('database_changed_during_migration')
        for row in current['settings']:
            sealed = await asyncio.to_thread(vault.cipher.encrypt, row['value'], f"setting:{row['name']}")
            await cur.execute('UPDATE thorn_settings SET value=%s WHERE name=%s', (sealed, row['name']))
        for row in current['audit']:
            sealed = await asyncio.to_thread(vault.cipher.encrypt, row['details'], f"audit:{row['id']}")
            await cur.execute('UPDATE thorn_audit SET details=%s WHERE id=%s', (sealed, row['id']))
        await conn.commit()
    await asyncio.to_thread(save_vault, vault, root, replace=True)
    def activate() -> None:
        for key in sorted(SECRET_NAMES | {'THORN_API_TOKEN'}): set_key(str(root / '.env'), key, '')
        set_key(str(root / '.env'), 'THORN_SECURE_STORAGE', '1')
        private_permissions(root / '.env')
        (root / 'data/security/vault-next.dpapi').unlink(missing_ok=True)
        pending.unlink()
    await asyncio.to_thread(activate)
    os.environ['THORN_SECURE_STORAGE'] = '1'


async def run(args: argparse.Namespace) -> None:
    root = args.data_dir.resolve()
    if args.verify or args.restore:
        # Recuperar ANTES de tocar DPAPI/configuracion originales. Esta rama puede
        # ejecutarse en otro perfil Windows con solo respaldo + clave de recuperacion.
        source = (args.verify or args.restore).resolve()
        key_path = args.recovery_key or root / 'data/recovery/thorn-recovery.key'
        keys = await asyncio.to_thread(RecoveryKeys.load, key_path.resolve())
        encoded = await asyncio.to_thread(read_private, source, root=source.parent, maximum=MAX_FILE)
        value = validate_snapshot(json.loads(await asyncio.to_thread(open_backup, encoded, keys)))
        if args.restore and (not args.destination or args.destination.resolve().exists()):
            raise ValueError('new_restore_destination_required')
        with tempfile.TemporaryDirectory(prefix='thorn-recovery-bootstrap-') as directory:
            os.environ['THORN_DATA_DIR'] = directory
            recovered = Vault.parse(canonical(value['vault']))
            for name in ('MYSQL_HOST', 'MYSQL_PORT', 'MYSQL_USER'):
                os.environ.setdefault(name, value['config'].get(name, {'MYSQL_HOST':'localhost','MYSQL_PORT':'3306','MYSQL_USER':'root'}[name]))
            for name, secret in recovered.secrets.items(): os.environ.setdefault(name, secret)
            os.environ['MYSQL_DB'] = 'mysql'
            os.environ['THORN_SECURE_STORAGE'] = '0'
            from core.db_core import inicializar_motor_datos, cerrar_motor_datos
            await inicializar_motor_datos()
            try:
                if args.verify:
                    await verify_restore(root, value)
                    print('Respaldo autenticado y restauracion independiente de DPAPI original verificada.')
                else:
                    database = 'db_thorn_restore_' + uuid4().hex[:16]
                    await restore_database(value, database, temporary=False)
                    await asyncio.to_thread(restore_files, value, args.destination.resolve(), database)
                    print('Restauracion verificada en carpeta nueva. Base:', database)
                return
            finally:
                await cerrar_motor_datos()
    os.environ['THORN_DATA_DIR'] = str(root)
    from thorn_web.config import prepare_environment
    from core.db_core import inicializar_motor_datos, cerrar_motor_datos
    await asyncio.to_thread(prepare_environment, allow_pending=True)
    await inicializar_motor_datos(allow_pending=True)
    try:
        from core.audit import AuditStore
        await AuditStore(enabled=True).initialize()
        key_path = root / 'data/recovery/thorn-recovery.key'
        if args.apply or args.rotate_key or args.backup:
            key_path = await asyncio.to_thread(initialize_recovery, root)
        keys = await asyncio.to_thread(RecoveryKeys.load, key_path)
        old = await asyncio.to_thread(load_vault, root) if vault_path(root).exists() else None
        next_path = root / 'data/security/vault-next.dpapi'
        staged = Vault.parse(await asyncio.to_thread(sealed_read, root, 'vault-next.dpapi')) if next_path.exists() else None
        vault = staged or (Vault(StorageCipher(AESGCM.generate_key(bit_length=256)), old.secrets) if args.rotate_key and old else old)
        vault = vault or Vault(StorageCipher(AESGCM.generate_key(bit_length=256)), {key: os.getenv(key, '') for key in SECRET_NAMES})
        readers = tuple(item.cipher for item in (old, staged) if item)
        value = await snapshot(root, vault, readers or None)
        validate_snapshot(value)
        # NOTA DEL OPERADOR: primero respaldo y restauracion de prueba.
        # Solo despues migramos o rotamos; crear un archivo no prueba que vuelva.
        backup = await create_backup(root, value, keys)
        await verify_restore(root, value)
        if args.apply or args.rotate_key:
            await migrate(root, vault, value, readers or None)
            print('Cifrado activado; secretos protegidos y registros migrados.')
        print('Respaldo cifrado y restauracion verificada:', backup)
        print('Clave de recuperacion privada (no publicar):', key_path)
    finally:
        await cerrar_motor_datos()


def require_offline(root: Path) -> None:
    """Comprobacion conservadora de las entradas soportadas, ademas del modo mantenimiento."""
    import psutil
    for process in psutil.process_iter(['pid', 'name', 'cmdline', 'cwd']):
        try:
            info = process.info
            if info['pid'] == os.getpid(): continue
            command = info['cmdline'] or []
            if any('nuitka' in item.lower() for item in command): continue
            names = {Path(item).name.lower() for item in command}
            if not names & {'desktop.py', 'serve.py', 'main.py', 'thorn.exe'}: continue
            if str(root).lower() in ' '.join(command).lower() or info['cwd'] and Path(info['cwd']).resolve() == root:
                raise RuntimeError('runtime_must_be_stopped')
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


async def run_locked(args: argparse.Namespace) -> None:
    if args.verify or args.restore:
        await run(args); return
    root = args.data_dir.resolve()
    if args.apply or args.rotate_key: await asyncio.to_thread(require_offline, root)
    def directory() -> Path:
        target = root / 'data/security'
        target.mkdir(parents=True, exist_ok=True); private_permissions(target, directory=True)
        return target / 'maintenance.lock'
    lock = file_lock(await asyncio.to_thread(directory))
    await asyncio.to_thread(lock.__enter__)
    try: await run(args)
    finally: await asyncio.to_thread(lock.__exit__, None, None, None)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=ROOT)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--apply', action='store_true', help='Servidor detenido: migrar con respaldo y prueba de restauracion.')
    group.add_argument('--backup', action='store_true')
    group.add_argument('--rotate-key', action='store_true', help='Servidor detenido: rotar clave con respaldo y reanudacion.')
    group.add_argument('--verify', type=Path)
    group.add_argument('--restore', type=Path)
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--recovery-key', type=Path, help='Clave portable; permite recuperar sin el almacen DPAPI original.')
    args = parser.parse_args()
    try:
        asyncio.run(run_locked(args)); return 0
    except Exception as exc:
        # No repetir excepciones SQL/DPAPI: podrian contener parametros privados.
        reason = str(exc) if str(exc) in {'runtime_must_be_stopped', 'new_restore_destination_required', 'secure_storage_unavailable'} else type(exc).__name__
        print('Operacion detenida:', reason, '. No iniciar el servidor si existe migration.pending.'); return 1


if __name__ == '__main__':
    raise SystemExit(main())
