# Microfono y dictado local

## Lo que hace

El microfono convierte una frase en texto editable mediante Vosk en esta PC.
No usa Web Speech Recognition del navegador, ElevenLabs Speech to Text ni
servicios externos. La salida predeterminada ahora es Piper local, sin creditos.
Ver [Autonomia local](AUTONOMIA_LOCAL.md). ElevenLabs queda como compatibilidad opcional.

Hay dictado por pulsacion y conversacion manos libres por turnos. La opción
**Activar por «Thorn»** filtra la transcripción local y admite seguimientos
durante 30 segundos. Ver [Memoria y fluidez](MEMORIA_Y_FLUIDEZ.md).
La transcripcion llega despues de terminar la captura, no es una
transcripcion progresiva. No se promete latencia fija ni precision perfecta.

## Usarlo

### Conversacion manos libres

El interruptor Conversacion y su estado estan visibles en todas las vistas.
Con `THORN_CONVERSATION_AUTO_START=1` en el .env privado, se activa una sola vez
al conectar una sesion autenticada. La plantilla publica lo deja en 0. Windows
y el navegador deben permitir el dispositivo; la primera vez puede hacer falta
activar el interruptor con un clic. No se elude un rechazo ni se reintenta solo.

Silero VAD detecta voz y aproximadamente 800 ms de silencio en el navegador.
Su modelo, worklet y runtime WASM se sirven desde este proyecto, sin CDN;
la inferencia usa un worker con un solo hilo. Cada frase tiene un limite de
15 s. Al terminar, se cierran los tracks ANTES de enviar PCM a Vosk. El texto
reconocido se consulta automaticamente en Ollama. No se ejecutan propuestas
Windows sin su confirmacion independiente.

Para una conversacion hablada local, Piper lee las nuevas respuestas al conectar;
se puede desmarcar Leer nuevas respuestas automaticamente. No necesita consentimiento
de nube ni creditos. Solo si se configura ElevenLabs expresamente se necesita
su autorizacion independiente y se envia texto al proveedor.
Durante STT, IA y TTS no se captura entrada. Al acabar la voz y esperar 400 ms se
reabre el microfono. Es conversacion por turnos, no interrupcion simultanea de
la voz. Si el navegador bloquea el audio, se espera a Reproducir con entrada cerrada.

El boton cuadrado detiene el modo, cierra el dispositivo y cancela pendientes.
Desconectar, pausar u ocultar la ventana tambien lo detienen. Volver a mostrarla
no lo reactiva automaticamente. Cambiar de vista SI conserva la conversacion,
con su indicador visible. Un fallo de entrada/STT/IA/TTS exige activarlo de nuevo.

### Dictado manual

1. Abre el HUD y conecta la sesion privada. La ventana desktop.py la conecta sola.
2. Pulsa el icono de microfono de la consola, o entra en Asistente > Entrada.
3. Autoriza el permiso de microfono que soliciten el navegador/WebView2 y Windows.
4. Habla: el nivel y el tiempo muestran la captura real. El boton cuadrado
   termina y transcribe; X cancela sin enviar el clip. El limite es 15 segundos.
5. Revisa o corrige la transcripcion y pulsa Consultar. No se envia sola al modelo
   ni ejecuta acciones Windows. Las acciones siguen exigiendo su confirmacion.

Puedes probar `estado`, `sistema` o `eventos` sin un modelo de IA. Las consultas
libres necesitan Ollama y el modelo de texto instalado; el dictado no los instala.
Para escuchar una respuesta, autoriza el texto en Identidad vocal y pulsa su
altavoz. Captura y reproduccion no funcionan simultaneamente.

El dispositivo predeterminado funciona sin guardar su nombre. Despues de conceder
permiso se muestran otros microfonos para elegirlos en el selector. No se conserva
esa seleccion ni IDs del hardware en localStorage, cookies o informes.

Si el permiso falla: revisa Configuracion de Windows > Privacidad y seguridad >
Microfono, el acceso para aplicaciones de escritorio, el sitio local del navegador
y el dispositivo seleccionado. El programa no cambia permisos del sistema ni
elude un rechazo. Una peticion de permiso pendiente se puede cancelar; si llega
tarde, el stream se cierra inmediatamente.

## Instalar en otra PC

```powershell
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-speech.txt
.\.venv\Scripts\python.exe -B tools/install_speech_model.py
```

En el .env privado activa `THORN_MICROPHONE_ENABLED=1` y reinicia T.H.O.R.N.
La plantilla publica deja esa opcion apagada. El instalador conserva un modelo
existente; descarga solo el ZIP oficial pequeno espanol, rechaza rutas ZIP fuera
del modelo y no descarga automaticamente durante el arranque del asistente.
La huella SHA-256 guardada es la observada al descargar, no una firma del autor.

El modelo espanol `vosk-model-small-es-0.42` ocupa aproximadamente 39 MB de descarga
y tiene licencia Apache-2.0. La memoria de trabajo es mayor que el tamano del ZIP.
El motor, modelos y capturas de las pruebas quedan excluidos de GitHub; las
dependencias opcionales estan fijadas con hashes y se auditan en CI.

## Como esta dividido

| Archivo | Responsabilidad |
| --- | --- |
| frontend/src/microphone.js | Permiso, dispositivo, captura limitada y cierre de tracks/contexto. |
| frontend/src/microphone-worklet.js | Convierte muestras a PCM en el hilo de audio, sin reproducirlas. |
| frontend/src/conversation.js | Serializa turnos y cancela resultados tardios. |
| frontend/src/conversation-vad.js | Silero local, deteccion de silencio, PCM limitado y cierre de entrada. |
| frontend/src/conversation-loader.js | Carga Silero solamente si se activa conversacion. |
| frontend/src/vad-ort-session.js | Copia tensores antes de transferirlos al worker, preservando el estado de Silero. |
| core/speech_input.py | Carga Vosk y reconoce PCM en workers; no abre dispositivos OS. |
| api/assistant.py | Endpoint autenticado de transcripcion, sin llamadas a la nube. |
| thorn_web/lifespan.py | Limita los bytes reales antes de crear buffers Django. |
| tools/install_speech_model.py | Descarga y descomprime el modelo oficial, fuera del event loop. |

El endpoint `POST /api/v1/microphone/transcribe/` exige Bearer y el header
`X-Thorn-Microphone-Consent: local`. Recibe `application/octet-stream` con PCM
mono int16 little-endian a 16000 Hz: de 3200 a 480000 bytes (0.1 a 15 segundos).
Los otros contratos HTTP conservan su limite de 16 KiB; no se amplian para audio.
La respuesta JSON contiene texto y `cloud: false`, con Cache-Control no-store.

## Privacidad y cierre

Audio y transcripcion viven en RAM; el dictado normal no escribe grabaciones a
disco ni almacena transcripciones en MySQL. La auditoria contiene ID, numero de
bytes, modelo y resultado, no el audio ni el texto. RAM y cierre de buffers no
son una garantia de borrado forense de memoria, swap o herramientas del OS.

Cancelar, desconectar, pausar, ocultar la pestana o cerrar la pagina detiene el
dispositivo y descarta capturas pendientes. Cambiar de vista cancela solo el
dictado manual. En conversacion se reabre entrada entre turnos autorizados;
nunca tras detenerla ni tras un permiso o respuesta tardios. El reconocimiento
ya iniciado puede terminar en su worker; no reactiva la UI ni envia una consulta.
Python no puede matar de forma segura una llamada DLL; el cierre espera a que
termine antes de liberar el modelo. Un bloqueo nativo exigiria aislamiento en
otro proceso para imponer un plazo duro.

## Verificacion

Las pruebas unitarias no necesitan hardware ni descargan modelos. El test
`frontend/tests/microphone-e2e.mjs` usa una fuente simulada de Chrome y comprueba
permisos, cancelacion, PCM, cierre de dispositivos, limites y consulta manual.
Con `THORN_MIC_LIVE=1` usa el Vosk instalado, no un resultado simulado. Su fixture
se guarda en .artifacts, excluido de Git. Esto NO comprueba el microfono fisico
del usuario, que requiere permiso y una frase real en su ventana.

`frontend/tests/conversation-e2e.mjs` usa un MP3 de prueba existente convertido
localmente a una fuente de microfono simulada. Verifica Silero real, Vosk real
con `THORN_MIC_LIVE=1`, autoarranque autenticado, cierre de tracks antes de STT,
IA y TTS, reapertura por turnos, RMS real de salida, cancelacion tardia y pantallas
320-1920 px. Intercepta IA/TTS: no consulta proveedores ni consume creditos.

Silero/vad-web y ONNX Runtime estan fijados en package-lock.json; el build sirve
solo los recursos necesarios en una lista cerrada. CSP permite WASM y workers
locales/blob, no eval JavaScript general ni conexiones a CDN.
El adaptador ONNX evita buffers desprendidos al reutilizar el tensor sample-rate
de Silero; no modifica dependencias instaladas. Los frames se procesan en orden,
con una inferencia en vuelo, cola maxima de 8 frames y plazo de 4 s por inferencia.
Sobrecarga o error cierra la entrada, sin colas ilimitadas ni reintentos ocultos.

Fuentes: [modelos y licencia Vosk](https://alphacephei.com/vosk/models),
[instalacion oficial](https://alphacephei.com/vosk/install),
[AudioWorklet](https://developer.mozilla.org/en-US/docs/Web/API/AudioWorklet) y
[permiso getUserMedia](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia).
Detector: [documentacion oficial de vad-web](https://github.com/ricky0123/vad/blob/master/docs/user-guide/browser.md).
