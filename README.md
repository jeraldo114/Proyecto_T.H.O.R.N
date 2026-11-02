# T.H.O.R.N — Bastion 9

Este es mi proyecto para construir mi propio asistente para la PC. La idea nació
de esas ganas de tener algo estilo J.A.R.V.I.S. o E.V., que pudiera conversar
conmigo, mostrar lo que pasa en el equipo y ayudarme con tareas del día a día.

Lo estoy armando de a poco en un ASUS TUF F15. También es una forma de aprender
sobre backend, programación asíncrona, interfaces y seguridad. Voy probando,
corrigiendo errores y dejando notas para entender lo que hice cuando vuelva
al código. Todavía está en desarrollo y hay bastante por mejorar.

**T.H.O.R.N es el nombre del asistente. Bastion 9 es la actualización actual.**

![HUD de T.H.O.R.N en modo Presencia](docs/assets/hud-bastion9.png)

*Captura real del HUD en modo Presencia, sin conectar la sesión de datos privados.*

## Qué hace por ahora

- Conversa usando **Ollama y un perfil local de Qwen2.5 1.5B**. Conserva un
  contexto corto para seguir el hilo entre preguntas de la misma sesión.
- Tiene voz local con **Piper** y reconocimiento de voz con **Vosk**. La
  conversación funciona por turnos: cierra el micrófono mientras responde.
- Muestra hora, CPU, RAM, disco, procesos y actividad con datos del equipo.
  Si falta una lectura, lo indica.
- Guarda notas, preferencias y auditoría en MySQL. Incluye cifrado para datos
  y secretos, respaldos recuperables y herramientas para firmar distribuciones.
- Lee texto de PDF y permite ver el documento con páginas y zoom.
- Busca información y lugares con fuentes. Los mapas usan OpenStreetMap y
  requieren autorización para conectarse a Internet.
- Incluye cámara local y gestos experimentales para mover elementos del HUD.
- Propone algunas acciones de Windows dentro de un catálogo limitado, con
  confirmación y auditoría antes de ejecutarlas.

El HUD tiene modos **Presencia** y **Trabajo**, un menú radial y ventanas que
se pueden mover, ampliar, minimizar y cerrar. El núcleo reacciona al audio
que se está reproduciendo. Pulsa el núcleo para abrir el menú o **F2** para
cambiar de modo. Los controles están en la [guía del HUD](docs/HUD_BASTION_9.md).

## La parte de la IA

El perfil `thorn:bastion9` reutiliza un modelo preentrenado de Qwen, ejecutado
localmente por Ollama. La personalidad, la interfaz y las herramientas se
conectan desde T.H.O.R.N. Cambiar el nombre o el prompt de un modelo no equivale
a entrenarlo desde cero; esto también forma parte de lo que estoy aprendiendo.

Mi intención es que las conversaciones, el dictado y la voz funcionen en la PC
sin depender de créditos por consulta. El modo predeterminado es local. Hay
integraciones opcionales con ElevenLabs y Brave, que quedan bloqueadas mientras
`THORN_LOCAL_AI_ONLY=1`. Los modelos sí necesitan descargarse al preparar el equipo.

La [guía de autonomía local](docs/AUTONOMIA_LOCAL.md) explica la instalación y
la procedencia de cada motor.

## Si quieres probarlo

Estos pasos están pensados para Windows y PowerShell. Uso **Python 3.12** y
**Node.js 24**; los archivos de dependencias incluyen versiones y hashes.
Para conversar hace falta instalar Ollama y el modelo. Para guardar datos hace
falta MySQL. Puedes empezar viendo el HUD sin base de datos ni modelos.

```powershell
git clone https://github.com/jeraldo114/Proyecto_T.H.O.R.N.git Proyecto_Thorn
cd Proyecto_Thorn
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.txt

# Crear la configuración solo si todavía no existe.
if (-not (Test-Path -LiteralPath .env)) {
    Copy-Item -LiteralPath .env.example -Destination .env
}

cd frontend
npm ci
npm run build
cd ..

.\.venv\Scripts\python.exe serve.py --sin-db --port 8000
```

Abre [el HUD local](http://127.0.0.1:8000/). La pantalla puede abrirse sin sesión;
los datos privados y las herramientas necesitan conectarse. Desde otra terminal
en la carpeta del proyecto puedes obtener una sesión temporal:

```powershell
.\.venv\Scripts\python.exe tools/session_access.py --port 8000 --mostrar-token
```

Pega el token en **Conectar** y mantenlo privado. El token vive en memoria,
caduca y se renueva durante el uso. El lanzador de escritorio conecta su sesión
automáticamente. Si necesitas detener el servidor, usa **Ctrl+C**.

### Para usar MySQL y los motores locales

Crea la base `db_thorn` en MySQL y completa los campos `MYSQL_*` de tu `.env`:

```sql
CREATE DATABASE IF NOT EXISTS db_thorn CHARACTER SET utf8mb4;
```

Después inicia `serve.py` sin `--sin-db`. Los servicios con persistencia preparan
sus tablas auxiliares al arrancar. Conviene usar un usuario MySQL propio para
T.H.O.R.N; el `root` de la plantilla es una configuración inicial de desarrollo.

La voz, el micrófono y los gestos tienen sus dependencias y modelos separados.
Dejé los pasos en [Autonomía local](docs/AUTONOMIA_LOCAL.md),
[Micrófono](docs/MICROFONO_LOCAL.md) y [Bastion 9](docs/BASTION_9.md).
El cifrado también se configura en cada instalación: sigue la
[guía de cifrado y respaldos](docs/CIFRADO_Y_RESPALDOS.md).

### Ventana de escritorio

```powershell
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-desktop.txt
.\.venv\Scripts\python.exe desktop.py
```

La ventana usa WebView2 y tiene bandeja del sistema. También hay herramientas
para compilar un ejecutable y preparar un paquete firmado. El repositorio
comparte el código y las plantillas; los datos, las claves, los modelos y los
binarios generados se mantienen fuera de Git.

## Lo que sigo afinando

Todavía estoy trabajando en la fluidez, el consumo de recursos y la utilidad
de cada herramienta. Los gestos siguen siendo experimentales. El lector PDF
extrae texto y muestra páginas; el modelo de texto no interpreta automáticamente
los circuitos o imágenes que aparezcan en ellas. Tampoco hay OCR ni una palabra
de activación para el micrófono.

Las notas son memoria explícita, y el contexto de conversación es corto y
temporal. La IA puede equivocarse. Las acciones de Windows conservan su
confirmación independiente de lo que responda el modelo.

## Para no perderme entre las carpetas

| Carpeta | Qué estoy poniendo ahí |
| --- | --- |
| `core/` | Núcleo asíncrono, sensores, IA, voz, sesiones y persistencia. |
| `api/` y `thorn_web/` | API local, permisos y arranque del servidor Django ASGI. |
| `frontend/` | HUD, ventanas, audio, cámara y gestos. |
| `tools/` | Instaladores, acceso local, respaldos, compilación y firma. |
| `tests/` | Pruebas del backend; las del HUD están en `frontend/tests/`. |
| `docs/` | Notas, decisiones y pasos de instalación. |

Si usas VS Code, abre `THORN.code-workspace`. El
[mapa del proyecto](docs/MAPA_DEL_PROYECTO.md) explica la organización.
En el código vas a encontrar comentarios con **NOTA DEL OPERADOR**: son las
cosas que quiero recordar al seguir metiendo mano.

## Cómo compruebo los cambios

```powershell
.\.venv\Scripts\python.exe -W error::ResourceWarning -m unittest discover -s tests -v
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe tools/check_repository.py --working-tree
cd frontend
npm test
npm run build
```

Bastion 9 cuenta con **202 pruebas de backend y 45 de módulos del frontend**,
además de pruebas de los flujos del HUD. Pasarlas ayuda a encontrar errores;
todavía queda probar el uso cotidiano en distintos equipos.

Para profundizar están las [notas del proyecto](docs/NOTAS_DEL_PROYECTO.md),
la [guía de seguridad](SECURITY.md) y los pasos de
[publicación en GitHub](docs/PUBLICACION_GITHUB.md).

Si encuentras algo que no funciona o tienes una idea, puedes abrir un issue.
Me sirve conocer qué probaste y qué pasó. Si se trata de una vulnerabilidad o
incluye información privada, revisa primero [SECURITY.md](SECURITY.md).
