# Seguridad de T.H.O.R.N

T.H.O.R.N es un proyecto personal que voy construyendo y aprendiendo a cuidar.
En Bastion 9 ya hay sesiones, cifrado y permisos separados, pero sigue siendo
un proyecto en desarrollo. Un reporte bien explicado me ayuda a corregir errores
y a entender qué tengo que mejorar.

## Si encuentras una vulnerabilidad

Prefiero que los detalles se compartan por privado para poder revisarlos antes
de dejarlos públicos.

1. Si GitHub muestra la opción **Report a vulnerability** en la pestaña
   **Security**, úsala para enviarme el reporte privado.
2. Si esa opción no aparece y no tienes un canal privado conmigo, abre un issue
   pidiendo uno. Deja fuera los detalles explotables y la información sensible.
3. Cuenta qué versión o commit probaste, qué pasos seguiste y qué resultado
   esperabas. Usa datos de prueba y ejemplos pequeños cuando puedas.

Por favor, no adjuntes contraseñas, tokens, tu `.env`, claves de recuperación,
archivos DPAPI, respaldos ni capturas con información personal. Para probar un
fallo, usa solamente tu instalación y tus propios datos.

Por ahora me concentro en el código de `main`. No tengo un calendario fijo para
los parches; revisaré los reportes según el problema y la información disponible.

## Lo que estoy haciendo para cuidarlo

- **Acceso local.** El servidor escucha en `127.0.0.1`. Las herramientas privadas
  requieren una sesión con permisos; los tokens se envían en `Authorization`,
  fuera de las URLs. El HUD conserva sus sesiones en RAM, con renovación,
  caducidad y revocación.
- **Acciones con confirmación.** Hay un catálogo limitado de acciones Windows.
  Lo que diga el modelo no concede permisos ni sustituye la confirmación y la
  auditoría. El asistente no recibe una shell de uso libre.
- **Datos y secretos.** La instalación puede proteger notas y datos sensibles
  con AES-256-GCM, y las claves con DPAPI del usuario Windows. El cifrado se
  prepara en cada equipo; clonar el repositorio no lo activa por sí solo.
- **Voz y dispositivos.** La configuración predeterminada usa motores locales.
  La cámara necesita autorización propia. Las imágenes del seguimiento de manos
  se procesan en el navegador. Las integraciones remotas opcionales requieren
  su configuración y sus consentimientos.
- **Internet separado.** Las consultas web necesitan autorización. Los mapas
  cargan teselas de OpenStreetMap cuando se solicitan, con atribución y el
  origen local como Referer. No se envían notas ni documentos con esas consultas.
- **Revisión antes de subir cambios.** Hay controles del índice de Git, del
  historial y del código publicable, además de pruebas, hashes de dependencias
  y auditorías de vulnerabilidades conocidas.

Estos controles ayudan, y aun así puede haber fallos. La IA puede dar una
respuesta equivocada y el detector de gestos sigue siendo experimental.
El cifrado protege los datos almacenados; el programa necesita descifrarlos
en memoria para utilizarlos. El código es público y compilarlo no lo hace
imposible de analizar.

## Las cosas privadas se quedan en tu PC

`.gitignore` y el control `tools/check_repository.py` excluyen `.env`, `data/`,
`backups/`, los modelos, logs, entornos, ejecutables y archivos generados. La
plantilla compartible es `.env.example`, con los campos privados vacíos.

Ocultar una carpeta o ignorarla en Git no cifra lo que contiene. Guarda tus
claves de recuperación con cuidado y conserva una copia recuperable fuera del
equipo cuando prepares tus respaldos. Los detalles están en
[Cifrado y respaldos](docs/CIFRADO_Y_RESPALDOS.md).

Para MySQL conviene preparar un usuario exclusivo con los permisos necesarios.
La cuenta `root` de la plantilla está pensada como punto de partida para pruebas
locales. Si quieres acceder desde otra máquina, revisa autenticación, TLS y
permisos antes de cambiar el enlace local del servidor.

## Si una clave termina publicada

Lo primero es revocarla y crear otra. Después hay que revisar dónde se utilizó
y si hace falta limpiar el historial. Quitarla en un commit nuevo no la elimina
de los anteriores, de los forks ni de las copias descargadas.

Los diagnósticos de las pruebas del HUD ocultan credenciales; si aparece alguna
en otro registro, trátala como expuesta y reemplázala. Comparte el reporte ya
saneado para poder investigar el problema.

## Sobre GitHub

La CI usa permisos de lectura, acciones fijadas a commits y pruebas en Windows
y Linux. Dependabot propone actualizaciones para revisarlas. Los escaneos
locales comparan también el código con secretos de la instalación, sin mostrarlos.

Las protecciones de rama, los reportes privados y la detección de secretos son
ajustes de GitHub: no se activan por tener este archivo. La
[guía de publicación](docs/PUBLICACION_GITHUB.md) explica cómo revisarlos.
