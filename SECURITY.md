# Seguridad de T.H.O.R.N

T.H.O.R.N Bastion 8 esta en desarrollo. Esta fase sirve una API en loopback y
coordina eventos; no ejecuta acciones de Windows ni comandos recibidos por HTTP.

## Configuracion privada

- .env permanece en cada maquina. La plantilla .env.example no contiene claves.
- Los tokens se envian en Authorization, nunca en URLs, issues, commits o logs.
- La API permanece en 127.0.0.1. Exponerla en red requiere revisar autenticacion,
  transporte TLS y permisos antes de cambiar esa configuracion.
- Para una instalacion estable, usar un usuario MySQL dedicado con permisos
  minimos. La configuracion root de la plantilla sirve para el desarrollo local.

## Reportar un problema

Usar el reporte privado de vulnerabilidades de GitHub si el propietario lo ha
habilitado. En caso contrario, contactar al propietario en privado. No incluir
credenciales ni detalles explotables en un issue publico.

## Antes de publicar

El control tools/check_repository.py revisa nombres de archivos, formatos de
tokens y coincidencias con las claves de .env. Tambien puede recorrer el historial.
Es una defensa adicional; no reemplaza los detectores especializados de GitHub
ni garantiza encontrar cualquier tipo de secreto.

Los workflows usan permisos de lectura, acciones fijadas a commits y paquetes
verificados por SHA-256. Dependabot propone actualizaciones; no se fusionan
automaticamente. pip-audit comprueba vulnerabilidades conocidas.

Si una clave se publica, revocarla y reemplazarla primero. Borrar el archivo en
un commit posterior no la elimina del historial ni de copias ya descargadas.

Los ajustes remotos de proteccion de rama y deteccion de secretos se configuran
en GitHub: su existencia no se deduce de estos archivos. Consultar
[la guia de publicacion](docs/PUBLICACION_GITHUB.md).
