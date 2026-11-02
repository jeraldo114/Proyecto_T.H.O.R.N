# Plan de cifrado y proteccion del codigo de T.H.O.R.N

**Estado: cifrado de datos y secretos aplicado.** El siguiente texto conserva el
diseno original; las operaciones y estado actual se documentan en
[Cifrado y respaldos](CIFRADO_Y_RESPALDOS.md).
La revision de PHP/XAMPP ya esta aplicada y documentada en
[el informe de seguridad](SEGURIDAD_PHP_2026-10-31.md).

El objetivo acordado cubre dos necesidades: mantener privados los datos y
credenciales, y dificultar copiar o inspeccionar el codigo que se distribuya.
Las medidas siguientes mantienen el funcionamiento local sin cuotas externas.

## Protecciones que ya existen

| Elemento | Estado observado en esta PC | Limite |
| --- | --- | --- |
| Disco C: | BitLocker FullyEncrypted, Protection On, XTS-AES 128 | Protege el volumen en reposo; una sesion desbloqueada puede leer sus archivos. |
| Autoridad de acceso T.H.O.R.N | DPAPI CurrentUser, data/security/authority.dpapi | Un proceso del mismo usuario puede acceder a DPAPI. |
| Sesiones del HUD | Temporales, solo hashes en RAM del servidor, renovables/revocables | Siguen siendo credenciales privadas mientras son validas. |
| Configuracion .env | Valores secretos migrados a vault.dpapi | Variables de configuracion publica permanecen en .env. |
| Memoria y ajustes MySQL | AES-256-GCM, con restauracion verificada | Datos descifrados disponibles al proceso autorizado en uso. |
| Codigo Python y HUD | Ejecutable standalone y manifiesto Ed25519 verificados | Las fuentes de desarrollo siguen en el proyecto; publicar esas fuentes las hace visibles. |

La comprobacion de BitLocker solo leyo estado; no cambio protectores ni accedio
a claves de recuperacion. No hace falta volver a cifrar el disco para empezar.
El alcance de estas protecciones esta explicado por
[Microsoft sobre BitLocker](https://learn.microsoft.com/en-us/windows/security/operating-system-security/data-protection/bitlocker/)
y [DPAPI](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata).

## Primera fase: credenciales y memoria

1. Crear una clave aleatoria AES de 256 bits para los datos. Protegerla con
   DPAPI CurrentUser y ACL privada, separada del token de acceso. El token del
   HUD no debe ser una clave de cifrado ni poder exportarla.
2. Migrar MYSQL_PASSWORD, DJANGO_SECRET_KEY y claves de proveedores a un almacen
   protegido. Dejar en .env solo configuracion no secreta. Mantener prioridades
   explicitas de variables del proceso para CI, sin mostrar valores en logs.
3. Cifrar las notas antes del INSERT/UPDATE SQL mediante AES-256-GCM. Usar un
   nonce nuevo de 12 bytes por cifrado, con version de formato e identificador
   de clave. Autenticar tambien el nombre del registro y su contexto con AAD,
   para impedir intercambiar ciphertexts entre registros.
4. Descifrar solo al leer una nota autorizada. Mantener los limites de entrada,
   la memoria explicita y la ausencia de prompts/audio persistidos. No guardar
   nuevas conversaciones como parte de esta migracion.
5. Ejecutar acceso a claves/archivos, migracion e inferencia en workers; mantener
   SQL asincrono y concurrencia acotada. Medir la latencia del HUD durante la
   lectura y escritura de notas cifradas.

Usar `AESGCM` de la dependencia cryptography existente. El cifrado autenticado
permite detectar modificaciones; una clave, nonce o AAD incorrectos deben fallar.
No reutilizar nonces ni inventar un algoritmo propio.
[Contrato de AESGCM](https://cryptography.io/en/50.0.2/hazmat/primitives/aead/#cryptography.hazmat.primitives.ciphers.aead.AESGCM).

La migracion debe reconocer los registros antiguos, comprobar su conversion en
una transaccion y permitir restaurarlos. Si falta la clave o falla la integridad,
el modulo debe indicar almacenamiento protegido no disponible. No generar otra
clave ni devolver datos en claro como recuperacion automatica.

Piezas previstas:

| Pieza | Responsabilidad |
| --- | --- |
| core/secure_storage.py | Formato versionado, AES-GCM y limites de datos. |
| thorn_web/credentials.py | Proteccion Windows de claves; ampliar sin mezclar autoridad y cifrado. |
| core/audit.py | Cifrar/descifrar notas al cruzar la persistencia. |
| thorn_web/config.py | Leer secretos protegidos antes de arrancar Django/MySQL. |
| tools/migrate_secure_storage.py | Migracion explicita con verificacion y recuperacion. |

## Segunda fase: respaldos recuperables

El cifrado local por DPAPI depende de la identidad Windows. Un respaldo util debe
poder restaurarse tambien si se pierde esa instalacion.

- Generar respaldos cifrados con una via de recuperacion separada: por ejemplo,
  una clave publica de recuperacion cuya clave privada se conserve fuera de la PC.
- Conservar la clave necesaria para los datos junto al respaldo, envuelta con
  esa clave de recuperacion; nunca incluirla en claro ni junto con su propia
  clave privada. Versionar formato, motor y esquema.
- Probar una restauracion en una carpeta/base separada ANTES de retirar copias
  antiguas. La clave de recuperacion debe quedar bajo control del propietario.
- Incluir rotacion y prueba de una copia alterada/truncada, sin sobrescribir una
  instalacion valida por un respaldo que no se pudo autenticar.

Esta fase requiere definir donde conservaras la recuperacion. Cifrar sin una
recuperacion comprobada puede hacer irrecuperables tus notas al perder Windows.

## Tercera fase: distribuir el codigo compilado

Para la version distribuible, compilar el backend y el lanzador Windows con
Nuitka en modo standalone. Conservar el proyecto fuente de desarrollo por
separado. Nuitka requiere un compilador C compatible y comprobar imports/DLLs;
el empaquetado debe probarse con WebView2, pystray, Django, Vosk, Piper y ONNX.
[Manual oficial](https://nuitka.net/user-documentation/user-manual.html),
[problemas habituales de DLLs y archivos de datos](https://nuitka.net/user-documentation/common-issue-solutions.html).

Para el ASUS TUF F15 se prefiere inicialmente una carpeta standalone: permite
verificar dependencias y evita extraer un paquete grande al iniciar. Los modelos
permanecen como recursos locales separados, con sus licencias y procedencia.
No incrustar claves privadas, archivos .env, notas, respaldo o autoridad DPAPI
en el ejecutable. Mantener el HUD minificado, sin sourcemaps publicados.

Agregar firma e integridad verificable a las versiones finales. Una firma ayuda
a detectar cambios; no oculta los secretos. El JavaScript que recibe un WebView
puede inspeccionarse. La compilacion aumenta el esfuerzo de ingenieria inversa,
pero no vuelve inaccesible el codigo para el administrador de la maquina.
El cifrado de datos no depende de que el algoritmo o el codigo sean secretos.

## Criterios antes de activarlo

- Las notas conservan exactamente su contenido despues de migrar y restaurar.
- Una consulta SQL directa no muestra el texto de las notas cifradas.
- Clave incorrecta, registro alterado o AAD intercambiado producen rechazo.
- Un reinicio normal reutiliza las claves existentes; no regenera autoridad ni datos.
- Los secretos no aparecen en Git, logs, informes, URLs ni archivos del build.
- Un respaldo restaura en otro entorno mediante su recuperacion independiente.
- La distribucion compilada inicia modelo, voz, microfono y HUD sin APIs de pago.
- La API sigue respondiendo durante las operaciones de disco y cifrado.

Orden recomendado: almacen de secretos, notas cifradas y recuperacion probada;
despues empaquetado compilado. El acceso de minimo privilegio a MySQL y los
permisos de Windows complementan el cifrado y se revisan como cambios separados.
