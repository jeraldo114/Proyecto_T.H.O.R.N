# T.H.O.R.N — cada cosa en su puesto

Abre `THORN.code-workspace` desde **Archivo > Abrir area de trabajo desde archivo**
en Visual Studio Code. Tambien puedes ejecutar `code THORN.code-workspace` desde
la raiz. Los numeros ordenan las areas del explorador; las carpetas fisicas
conservan sus nombres para que los imports, scripts y datos sigan en su sitio.

## El tablero de trabajo

| Area en Visual Studio Code | Carpeta real | Aqui se trabaja en... |
| --- | --- | --- |
| 00 - Inicio | Raiz | README, plantillas, dependencias y puntos de entrada. |
| 01 - Nucleo asincrono | `core/` | Eventos, sensores, IA, voz, sesiones y cifrado. |
| 02 - API local | `api/` | Peticiones del HUD, permisos, validaciones y respuestas. |
| 03 - Servidor Django | `thorn_web/` | Rutas HTTP, configuracion y ciclo de vida ASGI. |
| 04 - HUD visual | `frontend/` | Interfaz, audio, microfono y nucleo 3D. |
| 05 - Herramientas | `tools/` | Instalacion de modelos, respaldos, compilacion y firma. |
| 06 - Pruebas | `tests/` | Contratos y casos de error del backend. Las del HUD viven en `frontend/tests/`. |
| 07 - Documentacion | `docs/` | Guias para entender, configurar y mantener T.H.O.R.N. |
| 08 - Perfil de IA | `models/` | El Modelfile del perfil local; no contiene los pesos del modelo. |
| 09 - GitHub y CI | `.github/` | Comprobaciones de GitHub y mantenimiento de dependencias. |
| 10 - Hooks de Git | `.githooks/` | Controles locales que se ejecutan antes de publicar. |

En Inicio, expande `requirements.txt` para ver las dependencias opcionales y
sus archivos `.in`. En el HUD, `package-lock.json` se agrupa bajo `package.json`.
Cada carpeta de codigo aparece una sola vez en la vista organizada.

## Por donde meter mano

- **Arranque:** `desktop.py` abre la ventana nativa. `serve.py` sirve el HUD en
  navegador. `main.py` permite probar el nucleo desde consola.
- **El nucleo:** comienza por `core/runtime.py`, sigue por `core/event_bus.py`
  y despues por `core/services.py`. El primero supervisa, el segundo reparte
  mensajes y el tercero conecta las capacidades del asistente.
- **Persistencia:** `core/db_core.py` presta conexiones; `core/audit.py` guarda
  historial y notas. `core/secure_storage.py` protege el contenido sensible.
- **Conversacion:** `frontend/src/conversation.js` coordina los turnos;
  `core/speech_input.py`, `core/local_llm.py` y `core/local_voice.py` reconocen,
  responden y sintetizan. `frontend/src/voice-playback.js` mide el audio que
  mueve el emblema de `frontend/src/core-scene.js`.
- **Aspecto y controles:** `frontend/index.html`, `frontend/src/hud.css` y
  `frontend/src/hud.js`. Cambia las fuentes de datos en la API o el sensor;
  conserva la distincion entre una lectura vigente y una muestra anterior.
- **Acciones Windows:** `core/actions.py` define el catalogo permitido.
  Una sugerencia del modelo pasa por preparacion y confirmacion antes de abrir
  una aplicacion; ampliar el catalogo requiere revisar ese recorrido completo.
- **Bastion 9:** `core/operator.py` coordina turnos y herramientas;
  `core/interaction.py` limita contexto y resultados por sesion. `core/documents.py`
  aisla el lector PDF; `core/maps.py` obtiene coordenadas con fuente. En el HUD,
  `operator-tools.js` conecta sus controles y `camera.js` administra el dispositivo;
  `vision-worker.js` ejecuta el detector de manos fuera del hilo de interfaz.
  La guia [Bastion 9](BASTION_9.md) une el recorrido completo.

## Lo privado y lo generado

El explorador oculta `.venv/`, `.tools/`, `.models/`, `.artifacts/`, `dist/`,
`node_modules/`, caches, `data/`, `backups/`, `logs/`, `.env` y accesos directos.
Siguen en disco. La exclusion visual y la de Git cumplen funciones distintas:
**ocultar un archivo no lo cifra ni impide publicarlo por otros medios**.
`.gitignore` y `tools/check_repository.py` mantienen el control de publicacion.

`models/` contiene configuracion compartible. `.models/`, con punto al inicio,
contiene los pesos locales descargados y queda fuera de Git.

Para editar la configuracion local, usa **Ctrl+P** y escribe la ruta completa
de `.env`, o **Archivo > Abrir archivo**. Es privado y no debe copiarse al
workspace, a comentarios ni a capturas. Las claves de recuperacion siguen
en `data/recovery/`, como pediste. No muevas ni renombres la carpeta de datos
para ordenar el proyecto: DPAPI vincula sus archivos al usuario y a esa ruta.

## Botones de mantenimiento

En **Terminal > Ejecutar tarea**, las tareas `THORN:` permiten ejecutar pruebas
Python, comprobar Django, revisar archivos para GitHub, probar/compilar el HUD
y lanzar una demo del nucleo sin MySQL. **Ctrl+Shift+B** compila el HUD.
La revision para GitHub incluye archivos nuevos sin hacer `git add` ni publicar.

En **Ejecutar y depurar** tienes una demo y una API de desarrollo en el puerto
**8005**, sin MySQL. El HUD puede abrirse en `http://127.0.0.1:8005/`; los modulos
que requieren persistencia estaran deshabilitados. La sesion habitual de esta
PC usa el **8004**. Deten la depuracion con **Shift+F5** al terminar.
Las configuraciones no arrancan al abrir el workspace.

El workspace recomienda las extensiones oficiales de Python, Pylance y el
depurador. Usa el entorno `.venv` del proyecto; si VS Code conserva otro
interprete elegido antes, selecciona `.venv/Scripts/python.exe` mediante
**Python: Select Interpreter**. No necesitas instalar extensiones de pago.

## Las notas, con nuestro toque

Busca **`NOTA DEL OPERADOR`** para encontrar las decisiones que conviene
recordar al ampliar T.H.O.R.N. El tono es directo: que hace, por que lo hace
y que se puede romper si se cambia. Ejemplo: el modelo propone y tu confirmas.
Las notas explican el codigo; las pruebas comprueban que siga cumpliendolo.

Para profundizar, sigue con [Notas del proyecto](NOTAS_DEL_PROYECTO.md),
[Autonomia local](AUTONOMIA_LOCAL.md) y [Cifrado y respaldos](CIFRADO_Y_RESPALDOS.md).
La organizacion utiliza las funciones de [workspaces de VS Code](https://code.visualstudio.com/docs/editing/workspaces/multi-root-workspaces)
y [variables por carpeta](https://code.visualstudio.com/docs/reference/variables-reference#_variables-scoped-per-workspace-folder).
