# Cifrado, recuperacion y version compilada

El cifrado de aplicacion esta activo en esta PC desde el 31 de octubre de 2026.
La migracion hizo un respaldo cifrado, verifico una restauracion en otra base y
carpeta, convirtio los registros y retiro los valores secretos del `.env`.
La interfaz muestra **MEMORIA PROTEGIDA** cuando el backend usa AES-256-GCM.

## Que se protege

- MYSQL_PASSWORD, DJANGO_SECRET_KEY, ELEVENLABS_API_KEY y BRAVE_SEARCH_API_KEY:
  almacen Windows DPAPI CurrentUser. Los nombres siguen en `.env`, sin valores.
- Todas las filas de `thorn_settings.value`, incluidas notas y ajustes:
  AES-256-GCM con nonce aleatorio nuevo y contexto autenticado por registro.
- `thorn_audit.details`: AES-256-GCM, ligado al ID SQL. Fecha, tipo, estado y
  resumen operativo del evento siguen disponibles como metadatos.
- Autoridad del HUD: conserva su archivo DPAPI y es distinta de la clave AES.
- Respaldos `.thornbak`: AES-256-GCM, clave de archivo envuelta con RSA-OAEP
  SHA-256 y firma Ed25519. Se autentican antes de restaurarlos.

SQL sigue usando aiomysql y consultas parametrizadas. Acceso a claves, archivos
y cifrado ocurren en workers; no se amplian los permisos del asistente. Las
preguntas, respuestas y audio siguen sin guardarse como conversaciones.
Una clave o registro incorrecto produce `secure_storage_unavailable`, sin
fallback a texto plano ni regeneracion de claves de datos.

## Archivos privados y Git

| Ruta | Contenido |
| --- | --- |
| data/security/vault.dpapi | Clave AES y cuatro secretos, sellados para el usuario Windows. |
| data/security/authority.dpapi | Autoridad persistente del acceso local. |
| data/security/release-signing.dpapi | Clave privada de firma de distribuciones, protegida con DPAPI. |
| data/security/release-signing.pub | Clave publica confiable para verificar una distribucion. |
| data/recovery/thorn-recovery.key | Claves portables privadas para descifrar y autenticar respaldos. |
| data/recovery/thorn-recovery.pub | Informacion publica de esas claves de recuperacion. |
| backups/*.thornbak | Respaldos cifrados de las tablas propias, configuracion y claves necesarias. |
| dist/desktop.dist/ | Distribucion standalone; no debe incluir datos privados. |

`data/`, `backups/`, `dist/`, `.env`, claves y archivos cifrados estan excluidos
de Git. El control de publicacion tambien los rechaza si se intenta forzar su
inclusion, y compara las fuentes con los secretos cargados del almacen sellado.
Las claves se guardan dentro del proyecto por decision del propietario. Para
recuperarse de una perdida de la PC hay que conservar aparte el respaldo y
`thorn-recovery.key`; GitHub no recibe ninguno de los dos.

La clave portable no esta ligada a DPAPI ni requiere el Windows original. Su
archivo esta protegido por ACL del usuario/SYSTEM y por BitLocker en esta PC.
Copiarla junto con un respaldo permite descifrar ese respaldo: mantenerla privada.

## Operaciones habituales

Desde la raiz del proyecto:

```powershell
# Crear y verificar un respaldo, con prueba en otra base y carpeta.
.\.venv\Scripts\python.exe tools/migrate_secure_storage.py --backup

# Comprobar uno existente. Sustituir el nombre por un archivo real de backups.
.\.venv\Scripts\python.exe tools/migrate_secure_storage.py --verify backups/NOMBRE.thornbak

# Ver solo que secretos estan configurados, nunca sus valores.
.\.venv\Scripts\python.exe tools/secret_store.py --list

# Cambiar un secreto sin pegarlo en .env, argumentos o chat.
.\.venv\Scripts\python.exe tools/secret_store.py --set MYSQL_PASSWORD
```

El ultimo comando solicita el valor sin eco en una terminal privada. Reiniciar
el asistente despues de cambiar un secreto. Para conectar en el navegador seguir
usando `tools/session_access.py --port 8004 --mostrar-token`: muestra solo una
sesion temporal, no la autoridad ni la clave de datos.

Para una primera instalacion existente sin cifrar, detener todas las instancias
del asistente y ejecutar `--apply`. La herramienta bloquea entradas de servidor
detectadas y serializa operaciones de mantenimiento. Sigue siendo necesario no
arrancar otra instancia durante la migracion.

```powershell
# Solo con T.H.O.R.N detenido. MySQL debe permanecer disponible.
.\.venv\Scripts\python.exe tools/migrate_secure_storage.py --apply

# Rotacion explicita de la clave de datos, tambien con el servidor detenido.
.\.venv\Scripts\python.exe tools/migrate_secure_storage.py --rotate-key
```

La rotacion conserva la clave anterior hasta confirmar SQL y prepara una clave
sellada siguiente. Si se interrumpe, `migration.pending` impide un arranque normal.
Repetir `--apply` permite completar la operacion con las claves conservadas.
No eliminar `vault-next.dpapi` ni el marcador para forzar el arranque.

## Restaurar sin el Windows original

Se necesitan el archivo `.thornbak`, `thorn-recovery.key`, Python/dependencias
y un servidor MySQL accesible con permiso para crear una base nueva. No se
necesita descifrar el `vault.dpapi` original. Si las credenciales del servidor
destino son distintas, proporcionarlas como variables del proceso en una terminal
privada. La recuperacion abre una conexion administrativa a la base `mysql`.

```powershell
.\.venv\Scripts\python.exe tools/migrate_secure_storage.py `
  --restore backups/NOMBRE.thornbak `
  --recovery-key data/recovery/thorn-recovery.key `
  --destination C:\Ruta\THORN_recuperado
```

La carpeta destino debe ser nueva. Se crea una base con nombre
`db_thorn_restore_` y un identificador aleatorio, sin sobrescribir la base actual.
Los registros se insertan cifrados y se verifican contra el respaldo. Las claves
se sellan otra vez para el usuario y ruta destino. `.env` recuperado contiene
solo configuracion publica y el nombre de la base nueva.

Los modelos de voz, microfono y Ollama no se duplican dentro del respaldo: son
recursos grandes reinstalables. Para usar el perfil recuperado, preparar sus
modelos y ejecutar el escritorio con `--data-dir C:\Ruta\THORN_recuperado`.
La verificacion `--verify` crea y elimina exclusivamente su base temporal de
prueba; la restauracion explicita conserva la nueva base.

## Compilar y comprobar la distribucion

```powershell
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-build.txt
.\.venv\Scripts\python.exe tools/build_desktop.py --jobs 2
.\.venv\Scripts\python.exe tools/sign_release.py --sign dist/desktop.dist
.\.venv\Scripts\python.exe tools/sign_release.py --verify dist/desktop.dist `
  --trusted-key data/security/release-signing.pub

# Avisos de dependencias, firma y ZIP para distribuir, sin datos privados.
.\.venv\Scripts\python.exe tools/package_release.py
```

El compilador usa MSVC y Nuitka standalone, con dos jobs por defecto. Solo copia
el HUD compilado, la plantilla publica y recursos declarados de dependencias.
La clave de firma se genera mediante `--initialize` o al firmar la primera vez;
los respaldos posteriores la incluyen protegida dentro de su contenido cifrado.

La firma corresponde al manifiesto Ed25519 de todos los archivos del paquete.
**No es una firma Authenticode de un editor reconocido por Windows**. Una clave
publica incluida en el mismo paquete no establece confianza por si sola: usar
la clave publica confiable conservada por el propietario o su huella fijada.
La verificacion detecta archivos modificados, agregados, fuentes y datos privados
accidentalmente incluidos. Tambien busca los valores privados reales antes de
firmar. Una firma no cifra el contenido del ejecutable.

La version local puede abrirse con:

```powershell
.\dist\desktop.dist\THORN.exe --data-dir .
```

La entrega original genero `T.H.O.R.N - Bastion 8.lnk` en la raiz. Bastion 9
agrega su acceso `T.H.O.R.N - Bastion 9.lnk` y su propio ZIP firmado; consulta
[Bastion 9](BASTION_9.md). El acceso
directo usa este perfil cifrado y esta excluido de Git. Se puede regenerar con
`tools/create_shortcut.ps1`. El ZIP distribuible queda en
`.artifacts/THORN-Bastion8-Windows-x64.zip`.

La clave publica exportada para incluir en el repositorio esta en
`docs/RELEASE_SIGNING_PUBLIC_KEY.json`; es intencionalmente publica. Puede usarse
con `--trusted-key` si se obtuvo por un canal confiable. La clave privada
permanece en `data/security/release-signing.dpapi`.

El modo standalone evita entregar directamente el backend `.py` y no requiere
Python instalado para ejecutarlo. Conserva requisitos de Windows/WebView2,
Ollama, MySQL y modelos locales. El proyecto fuente de desarrollo permanece
separado. El JavaScript visible al WebView y los binarios pueden inspeccionarse;
la compilacion aumenta el esfuerzo necesario y no ofrece secreto absoluto.

## Limites y comprobaciones

BitLocker ya estaba activo y no se modificaron sus protectores. AES de aplicacion
protege los registros actuales; no certifica el borrado de antiguas copias, logs
SQL, bloques del disco o snapshots de Windows. El administrador de la PC y un
proceso malicioso del mismo usuario pueden acceder a datos descifrados en uso.

Las pruebas incluyen claves equivocadas, AAD intercambiado, alteracion de
ciphertext, firma incorrecta, restauracion real en base separada y SQL sin texto
de notas. La interfaz, MySQL y voz local se comprueban despues de migrar.

Referencias: [AESGCM](https://cryptography.io/en/50.0.2/hazmat/primitives/aead/#cryptography.hazmat.primitives.ciphers.aead.AESGCM),
[RSA OAEP](https://cryptography.io/en/50.0.2/hazmat/primitives/asymmetric/rsa/),
[Ed25519](https://cryptography.io/en/50.0.2/hazmat/primitives/asymmetric/ed25519/),
[DPAPI](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata),
[Nuitka](https://nuitka.net/user-documentation/user-manual.html).
