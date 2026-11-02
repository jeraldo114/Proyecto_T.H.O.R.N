# Asistente local - Bastion 8

## Estado real

T.H.O.R.N ya tiene alertas, memoria explicita, auditoria y un controlador de
acciones Windows. La integracion con Ollama esta implementada, pero necesita
un motor en ejecucion y el modelo configurado instalado. El HUD consulta su
estado real: no presenta respuestas simuladas cuando no estan disponibles.

La salida predeterminada ahora es Piper local, sin creditos: ver
[Autonomia local y sesiones](AUTONOMIA_LOCAL.md). ElevenLabs es compatibilidad
opcional, bloqueada por `THORN_LOCAL_AI_ONLY=1`: necesita API key privada, voz
autorizada y consentimiento por sesion. Es una excepcion al modo completamente
local: envia el texto seleccionado a la nube. El modulo de salida no recibe
audio del microfono. Vosk permite dictado manual editable o conversacion por turnos
con Silero local. En conversacion se consulta automaticamente al terminar una
frase; STT, IA y TTS transcurren con el dispositivo cerrado. Escuchar no autoriza
TTS en nube ni ejecucion Windows. Ver [Voz ElevenLabs](VOZ_ELEVENLABS.md) y
[Microfono local](MICROFONO_LOCAL.md).

## Que hace cada pieza

| Archivo | Funcion |
| --- | --- |
| core/alerts.py | Detecta presion sostenida y su recuperacion, sin avisos repetidos. |
| core/audit.py | Escribe historial, reglas y notas en MySQL con parametros SQL. |
| core/actions.py | Prepara acciones conocidas y exige una confirmacion de un solo uso. |
| core/local_llm.py | Consulta Ollama de forma async y valida su respuesta JSON. |
| core/personality.py | Define tono propio, verdad, permisos y perfil vocal original. |
| core/elevenlabs_voice.py | Sintetiza texto con HTTP async; mantiene audio acotado en RAM. |
| core/speech_input.py | Transcribe dictado local en workers, sin grabaciones persistentes. |
| tools/elevenlabs_voice.py | Lista voces y disena/guarda una voz original con confirmacion explicita. |
| core/services.py | Une estos servicios al ciclo de vida del runtime. |
| api/assistant.py | Protege las rutas privadas y valida sus contratos. |
| desktop.py | Abre una ventana Windows WebView2 con su propio servidor y bandeja. |
| requirements-desktop.txt | Dependencias Windows opcionales, fijadas con hashes. |

## Alertas entendibles

CPU: >=90 % durante 15 s; recuperacion <=80 %. RAM: >=90 % durante 8 s;
recuperacion <=85 %. Disco del proyecto: >=90 % ocupado; recuperacion <=87 %.
La separacion entre limite y recuperacion se llama histeresis: evita que una
lectura cerca del limite produzca avisos constantes de subida y bajada.

Solo se consideran muestras vigentes del sensor interno. Los picos breves,
lecturas repetidas, cifras invalidas y eventos externos no activan estas reglas.
Una interrupcion de muestreo reinicia la espera. Una alerta activa persiste
hasta una muestra de recuperacion; sin nuevas muestras no se afirma recuperacion.

Asistente permite cambiar limites, recuperacion, espera y habilitacion. Las reglas
se validan y guardan antes de aplicarse, y se cargan de nuevo al arrancar. Sin
MySQL siguen funcionando las reglas predeterminadas, pero no se guardan cambios.
Los fallos de auditoria/publicacion se cuentan; el bus no garantiza entrega durable.

## Memoria y privacidad

Historial muestra registros de arranque/cierre, transiciones de alertas,
consultas al modelo y acciones. No almacena cada muestra CPU ni el texto de
las conversaciones. Las consultas solo dejan modelo, ID y resultado.

En la misma vista puedes guardar expresamente hasta 20 notas de 1000 caracteres,
con un limite conjunto de 60 KB UTF-8. Una nota y su registro de auditoria se
confirman en una sola transaccion. Eliminar requiere confirmacion y afecta
solo a la nota seleccionada. El modelo recibe extractos de las cinco notas
mas recientes (400 caracteres por nota), identificadas como datos no confiables,
no como instrucciones para cambiar permisos.

La memoria y las reglas usan thorn_settings; los registros usan thorn_audit.
No se borran ni migran otras tablas. Los campos estan acotados y todos los valores
usan parametros SQL. Se necesitan permisos de creacion de estas tablas la primera
vez; despues se puede usar un usuario MySQL limitado a las tablas propias.

En esta PC se activo cifrado AES-256-GCM para notas, ajustes y detalles de
auditoria, con claves protegidas por DPAPI y respaldos autenticados recuperables.
Los metadatos operativos del historial siguen visibles. Ver
[Cifrado y respaldos](CIFRADO_Y_RESPALDOS.md) para limites, mantenimiento y
restauracion; esto no certifica el borrado de antiguas copias ni logs SQL.

## Acciones y confirmacion

El catalogo actual permite abrir Bloc de notas, Calculadora y la carpeta del
proyecto. No acepta ejecutables, argumentos, rutas, URLs, borrados, cambios de
firewall ni comandos cmd/PowerShell. Se usan rutas fijas del sistema y APIs Windows
desde un hilo; no se pide elevacion de administrador.

1. Preparar devuelve una propuesta y una confirmacion aleatoria valida durante 60 s.
2. La UI muestra la accion concreta y pide Confirmar y abrir. Cancelar no ejecuta nada.
3. El servidor consume la confirmacion una sola vez y escribe executing en MySQL.
4. Solo si esa escritura termina, solicita a Windows abrir el destino conocido.
5. Registra launched o failed. launched indica que Windows acepto la apertura,
   no que se haya observado una ventana ni que esta siga abierta.

Sin Windows o MySQL se bloquean las acciones. Tambien se bloquean si el proceso
se inicio como administrador, para evitar aperturas que hereden elevacion.
Ejecutar T.H.O.R.N desde una consola normal, no como administrador.
Un fallo entre el efecto OS y su
registro puede dejar executing; no existe una transaccion atomica entre MySQL
y Windows. Comprueba la aplicacion antes de repetir una accion de resultado incierto.
El modelo solo propone IDs del catalogo; nunca obtiene acceso al ejecutor.
Un evento command.received sigue siendo un mensaje, no una orden shell.

## Modelo de texto local

La configuracion predeterminada es THORN_LLM_URL=http://127.0.0.1:11434 y
THORN_LLM_MODEL=thorn:bastion8. Crear el perfil con tools/install_local_brain.py
sobre Qwen ya instalado. Instalar Ollama/modelos es un paso independiente;
el arranque de T.H.O.R.N no descarga nada. Con Ollama instalado, iniciarlo en
modo solo local en otra consola:

```powershell
$env:OLLAMA_NO_CLOUD = '1'
$env:OLLAMA_HOST = '127.0.0.1:11434'
ollama serve
```

Con autorizacion para descargar el modelo, ejecutar en otra consola:

```powershell
ollama pull qwen2.5:1.5b
```

Cambiar el `.env` de T.H.O.R.N no configura un proceso Ollama ya iniciado:
OLLAMA_NO_CLOUD debe estar en el proceso Ollama y requiere reiniciarlo. Ver
[modo local de Ollama](https://docs.ollama.com/faq#how-do-i-disable-ollama-cloud-features).

Para conservar el modo local en Windows, guarda estas dos variables en el
entorno del usuario, no solo en la consola o en el `.env` del proyecto:

```powershell
[Environment]::SetEnvironmentVariable('OLLAMA_NO_CLOUD', '1', 'User')
[Environment]::SetEnvironmentVariable('OLLAMA_HOST', '127.0.0.1:11434', 'User')
```

Reinicia Ollama despues del cambio. Tras instalarlo, abre una consola nueva
para que reconozca `ollama`, o abre la aplicacion desde el menu Inicio.
El servicio local no necesita iniciar sesion en una cuenta Ollama.
Para limitar la memoria, esta instalacion usa `OLLAMA_NUM_PARALLEL=1`,
`OLLAMA_MAX_LOADED_MODELS=1` y `OLLAMA_CONTEXT_LENGTH=2048` en el entorno del
usuario. El cliente tambien limita la concurrencia a uno y solicita un contexto
de 2048 tokens. El modelo se mantiene cargado dos minutos despues de cada
consulta; no se reserva memoria indefinidamente.

### Si el HUD muestra "Sin motor"

La voz de ElevenLabs, el dictado Vosk y el modelo de texto son servicios
independientes. Tener voz o microfono no instala el motor de respuestas.

- `engine_unavailable`: Ollama no responde en la direccion local configurada.
  Abre Ollama desde el menu Inicio o ejecuta `ollama serve` con las variables
  anteriores; no inicies un segundo servidor si ya hay uno escuchando.
- `model_missing`: Ollama responde, pero falta el modelo exacto configurado en
  `THORN_LLM_MODEL`. Para la configuracion inicial, usa
  `ollama pull qwen2.5:1.5b`; esta descarga necesita conexion a Internet.
- `ready`: el motor responde y el modelo local aparece instalado. Una consulta
  real todavia puede fallar por falta de memoria o tiempo de espera.

El HUD vuelve a consultar la disponibilidad; la comprobacion del servidor usa
una cache de 15 segundos. No es necesario reiniciar T.H.O.R.N tras iniciar
Ollama o completar la descarga del modelo, salvo que cambies su `.env`.
La primera respuesta puede tardar mas porque carga el modelo en memoria.
Los comandos reservados `estado`, `sistema` y `eventos` no necesitan Ollama.

El cliente admite solo HTTP hacia IP literal loopback, sin proxies de entorno,
redireccionamientos, credenciales en URL ni hosts remotos. Rechaza modelos marcados
como remotos y nombres cloud. El servicio Ollama debe ser confiable y ejecutarse
con la nube deshabilitada; validar su catalogo no sustituye proteger ese proceso.

Una consulta a la vez, plazo total de 60 s, contexto de 2048 tokens y salida
acotada. La respuesta debe ser JSON valido con reply y suggested_action. Se
entregan telemetria vigente, alertas, hora UTC y notas explicitas; nunca secretos,
archivos del equipo ni nombres de procesos. Un modelo puede equivocarse: sus
frases no son resultados verificados de un escaneo ni autorizaciones OS.

## Ventana y bandeja Windows

```powershell
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-desktop.txt
.\.venv\Scripts\python.exe desktop.py
```

Requiere WebView2. La ventana utiliza el mismo HUD, con servidor propio en un
puerto efimero ligado a 127.0.0.1. No reutiliza un servidor desconocido. La sesion
se entrega una vez a la pagina local y solo vive en memoria, sin cookies, URL o
almacenamiento persistente. No se expone una API Python general a JavaScript.

La bandeja ofrece mostrar, ocultar y salir. Cerrar la ventana detiene el servidor,
los servicios, el sensor, el bus y MySQL. Los informes usan un dialogo nativo de
guardado. WebView2 es un motor web embebido en una ventana Windows; esto no es
una interfaz WinUI escrita desde cero ni un ejecutable independiente de Python.
No se inicia con Windows por defecto. Opciones voluntarias:

```powershell
.\.venv\Scripts\python.exe desktop.py --enable-startup
.\.venv\Scripts\python.exe desktop.py --disable-startup
.\.venv\Scripts\python.exe desktop.py --smoke-test
```

Las dos primeras modifican solo THORN Bastion 8 en HKCU/Run del usuario actual.
La prueba oculta comprueba sesion, reloj y pixels del canvas y cierra la ventana.
No ejecutar multiples instancias con acciones al mismo tiempo: sus confirmaciones
son independientes. Todos los procesos del mismo usuario comparten la confianza
local; la API no es una barrera frente a malware que ya lea su .env o memoria.

## Rutas privadas

Todas requieren Authorization: Bearer. GET capabilities/alerts/history/memory;
POST alerts/configure, actions/prepare, actions/confirm, chat, memory/save y
memory/delete y voice/speech bajo /api/v1/. No hay CORS ni sesiones por cookies. History usa
before y limit (1..100) para paginacion por ID. Los cuerpos siguen limitados a 16 KiB.

## Verificar

```powershell
.\.venv\Scripts\python.exe -B -W error::ResourceWarning -m unittest discover -s tests
.\.venv\Scripts\python.exe -B manage.py check
.\.venv\Scripts\python.exe -m pip_audit --strict --disable-pip -r requirements-desktop.txt
```

La suite usa MySQL, ejecutor OS y modelo simulados; no abre aplicaciones ni usa
audio. El E2E usa telemetria real, prepara y cancela una accion, y comprueba memoria
SQL con una nota temporal que elimina por su ID. Los tests del modelo validan el
protocolo, no su precision. La inferencia real requiere instalar y probar el modelo.

Referencias: [API de chat Ollama](https://docs.ollama.com/api/chat),
[pywebview](https://pywebview.flowrl.com/api/),
[bandeja pystray](https://pystray.readthedocs.io/en/latest/usage.html).
