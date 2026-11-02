# Revision de PHP y XAMPP del 31 de octubre de 2026

## Alcance y resultado

T.H.O.R.N ejecuta Python/Django ASGI; no incorpora un runtime PHP. Esta PC tenia
XAMPP con PHP 8.2.12 y Apache 2.4.58. Apache estaba activo como servicio y
escuchaba en todas las interfaces, independientemente del HUD de T.H.O.R.N.

Cambios aplicados a `C:\xampp`, con privilegios administrativos:

| Componente | Antes | Despues |
| --- | --- | --- |
| PHP CLI y modulo Apache | 8.2.12 | 8.2.35, paquete Windows 8.2.35-1 |
| Apache HTTP Server | 2.4.58 | 2.4.69, Apache Lounge VS18 x64 |
| Decodificador Brotli de PHP y Apache/cURL | Versiones antiguas | 1.2.1 |
| Puertos web 80/443 | Todas las interfaces | 127.0.0.1 y ::1 |
| Ruta HTTP /php-cgi/ | Alias al interprete | Sin alias; denegada explicitamente |
| TLS local | Certificado RSA 1024 | Certificado localhost RSA 3072, SHA-256, SAN IPv4/IPv6 |

PHP publico correcciones para CVE-2026-109618 y CVE-2026-109619 el 21 de octubre.
El paquete Windows `-1` incorpora la actualizacion Brotli de CVE-2026-111497.
Se desactivo `session.upload_progress.enabled` siguiendo el aviso adicional
del 23 de octubre sobre CVE-2026-115560. Referencias:
[avisos oficiales de PHP](https://www.php.net/archive/2026.php),
[paquetes Windows 8.2 y SHA-256](https://www.php.net/downloads.php?os=windows&version=8.2).

Apache se actualizo por sus propias correcciones de seguridad:
[avisos del proyecto](https://httpd.apache.org/security/vulnerabilities_24.html).
El binario procede de [Apache Lounge](https://www.apachelounge.com/download/),
distribuidor de binarios Windows; Apache Software Foundation distribuye fuentes.

## Instalacion y configuracion

Se verificaron los ZIP antes de ejecutarlos:

| Paquete | SHA-256 |
| --- | --- |
| php-8.2.35-1-Win32-vs16-x64.zip | 811d13185e401ef6ec286b21a58dc004ab6d4e04494923d88add19551faa1117 |
| httpd-2.4.69-261002-Win64-VS18.zip | 77d715d0d9cee731e2251e7ddc2090a7a8e6d41fedef2e9827cd918593c5a652 |

La distribucion Apache todavia traia Brotli 1.2.0. Se reemplazaron
`brotlidec.dll` y `brotlicommon.dll` por los del ZIP oficial de PHP, que contiene
1.2.1. Se comprobo la carga efectiva en cURL y en PHP bajo Apache; no se asumio
que cambiar el numero de Apache actualizara todas sus dependencias.

Se conservaron php.ini, PEAR/extras y la configuracion XAMPP. Se instalaron
binarios/extensiones nuevos y un paquete de certificados CA actualizado desde
certifi del entorno local. Los archivos de los proyectos en htdocs y los datos
MySQL no se movieron.

Endurecimiento adicional:

- `session.use_strict_mode=1`, cookies HttpOnly y SameSite=Lax.
- Errores de PHP registrados en archivo, sin mostrarlos por HTTP.
- `expose_php=Off`; desactivada la presentacion de fuentes `.phps`.
- Denegado acceso HTTP a `.env`, `.git` y al interprete CGI.
- Deshabilitada la ruta AJP de demostracion `/examples`.
- `ServerTokens Prod`, `ServerSignature Off`, `TraceEnable Off`.
- TLS limitado a 1.2 y 1.3.

El primer arranque rechazo el certificado predeterminado de 1024 bits y activo
la reversion prevista. Se genero un certificado autofirmado de desarrollo para
localhost, valido un ano, y se verifico el segundo arranque. La clave privada
se restringio por ACL al usuario local, Administradores y SYSTEM. No se instalo
ninguna CA en el almacen de confianza de Windows. El navegador puede advertir
que el certificado es autofirmado; no es un certificado publico de confianza.

## Verificacion realizada

- Apache `httpd -t`: Syntax OK; servicio iniciado con la version nueva.
- PHP por HTTP: version 8.2.35, SAPI apache2handler, Brotli 1.2.1.
- Extensiones curl, mysqli, pdo_mysql, mbstring y openssl cargadas.
- CGI devuelve 403; TRACE devuelve 405; dashboard devuelve 200.
- HTTPS verificado contra el certificado local, sin desactivar validacion TLS.
- Nueve binarios principales cotejados con el contenido de los ZIP verificados.
- Listeners 80/443 exclusivamente loopback IPv4/IPv6.
- HUD/API de T.H.O.R.N disponible y su historial MySQL autenticado devuelve 200.
- Proceso MySQL conservado, sin reinicio. Modelo local y voz siguen disponibles.
- Eliminado el PHP temporal de diagnostico; su URL devuelve 404.

## Respaldo y limites

La instalacion previa completa permanece en
`C:\xampp\security-backups\thorn-20261031-r2\php` y `...\apache`.
Son copias privadas, fuera de htdocs y de Git. No ejecutar ni volver a publicar
esas versiones vulnerables. Los manifiestos de descarga y verificacion quedaron
en `.artifacts/security-20261031/`, tambien excluido de Git.

PHP 8.2 llega al final de soporte de seguridad el **31 de diciembre de 2026**;
programar su migracion de rama despues de comprobar los demas proyectos PHP.
[Calendario oficial](https://www.php.net/supported-versions.php).

Esto no es una auditoria de todas las aplicaciones PHP, extensiones de terceros,
Composer ni todos los servicios Windows. MySQL mantiene su listener previo;
conviene revisar por separado acceso de red, usuario de minimo privilegio y
actualizaciones del motor, con respaldo y prueba de restauracion de la base.

El cifrado de T.H.O.R.N se trata en [Plan de cifrado](PLAN_DE_CIFRADO.md).
