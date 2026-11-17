# El cuaderno de T.H.O.R.N y las mejoras de uso

La idea es que al volver a conectar no tengamos que empezar desde cero. También
quiero que hablarle y mover sus ventanas resulte cómodo en el F15, sin sumar
servicios de pago ni mantener motores pesados abiertos porque sí.

## Qué recuerda ahora

Cada turno completado de chat, consulta web, mapa o pregunta a un PDF se guarda
con fecha, pregunta y respuesta completas. Los comandos hablados o escritos que
pasan por el chat también quedan registrados. Los botones de la interfaz y los
resúmenes automáticos no se convierten en conversaciones inventadas.

El archivo usa MySQL y AES-256-GCM, con la clave de datos de la instalación
protegida por DPAPI. Las tablas `thorn_conversation` y `thorn_conversation_terms`
se preparan al arrancar. No reutilizamos los tokens de sesión como identidad
durable ni guardamos esos tokens junto al texto.

Es el cuaderno de **un operador local por instalación**. Las sesiones con
`memory:read` pueden consultarlo; las que solo tienen `assistant:chat` no reciben
recuerdos de otras conexiones. Se exige `memory:write` para registrar o borrar
turnos. Para varios usuarios hace falta añadir perfiles y separación propia;
no hay que entregar esos permisos a alguien que no deba leer el cuaderno.

La opción `THORN_CONVERSATION_MEMORY=1` está activada por defecto. Si faltan MySQL
o cifrado, se conserva el contexto temporal y el HUD indica que el archivo no
está disponible. Si falla una escritura de una memoria activa, aparece un aviso.
No se cambia silenciosamente a texto plano. Con `0` se desactiva esta memoria
durable, sin borrar lo que ya estaba guardado.

**No guarda audio ni reconstruye conversaciones antiguas que solo vivieron en
RAM.** Tampoco archiva turnos cancelados, fallidos o incompletos como si hubieran
terminado correctamente. El guardado se confirma antes de entregar la respuesta.

## Recuperación sin llenar la RAM

Al preguntar, se buscan hasta tres turnos recientes y tres antiguos relacionados.
El índice utiliza palabras normalizadas y HMAC con una clave derivada: no almacena
un vocabulario legible en la base. Se indexan hasta 384 términos por turno y hasta
24 por búsqueda. No es una búsqueda semántica ni un sistema de embeddings.

Los extractos y el contexto enviados a Qwen tienen un presupuesto de tamaño. El
archivo íntegro puede crecer en disco sin entrar entero en el prompt. La IA puede
no recuperar un dato o interpretar mal un recuerdo; para comprobarlo, abre
**Memoria e historial**, busca palabras del tema y consulta el texto original.
La vista carga 20 turnos por página y reemplaza la página anterior en el navegador.
La búsqueda viaja en JSON, sin dejar sus palabras en una URL.

En Preferencias puedes quitar **Usar notas y conversaciones archivadas como
contexto**. Eso detiene su uso en el prompt, pero conserva el guardado automático.
Para desactivar también el guardado, cambia la opción `.env` y reinicia el programa.

**Borrar contexto del chat** limpia la RAM y los resultados de esa sesión. Para
eliminar el archivo entra en Memoria, despliega **Olvidar el historial**, escribe
`BORRAR MEMORIA` y pulsa el botón. Se conservan las notas explícitas y los respaldos
anteriores. El borrado cambia una generación persistente para impedir que una
respuesta pendiente vuelva a escribir recuerdos eliminados.

Los respaldos cifrados y la rotación de claves incluyen las conversaciones y
reconstruyen su índice. Siguen teniendo límites: 10.000 turnos por respaldo y
32 MiB de contenido total, además del límite de auditoría existente. Si se
superan, la herramienta falla explícitamente; no recorta el archivo en silencio.

## Hablarle por su nombre

1. Conecta la sesión y permite usar el micrófono.
2. Activa **Conversación**. **Activar por «Thorn»** viene marcado; quítalo antes
   de iniciar si prefieres conversación continua.
3. Di «Thorn, ¿cómo va mi proyecto?» o «Oye Thorn». Si dices solo su nombre,
   espera tu siguiente frase sin llamar al modelo.
4. Después de una respuesta, puedes seguir durante 30 segundos sin repetir el
   nombre. Tras esa ventana vuelve a esperar «Thorn».

### Cómo se reconoce el nombre

«Thorn» no es una palabra del español. En texto libre Vosk la escribe como «tom»,
«son», «don», «fourth» o la omite. Por eso, mientras espera el nombre, el servidor
usa una **gramática cerrada** sobre el modelo pequeño (`core/speech_input.py`):
solo puede elegir entre «thorn», «oye/hola thorn», palabras comunes que suenan
parecido («toma», «torre», «tormenta», «son», «por»…) y `[unk]`. Si el nombre
abre la frase, recorta el audio en ese punto y transcribe **solo lo dicho
después**. Si no está, no transcribe nada: la charla ambiental no se convierte
en texto.

Medido con voz sintética de Piper: la gramática detectó 7/7 frases con el nombre
y 0/12 falsas alarmas; el filtro anterior por alias, 2/7. La voz sintética no es
la tuya: mide con tus grabaciones antes de confiar en ella.

```powershell
.\.venv\Scripts\python.exe tools\evaluate_wake.py .artifacts\nombre.json
```

El manifiesto es una lista `{"wav": "...", "wake": true|false}` con WAV mono PCM16
de 16 kHz. Incluye negativas que empiecen parecido («toma nota», «torre», «son
las tres»). Si falta el modelo pequeño, el HUD vuelve al filtro por alias
(`frontend/src/wake-word.js`), cuya lista debe coincidir con `WAKE_ALIASES` de
`core/commands.py`; `tests/test_wake_word.py` lo comprueba. Al detectar el nombre
suena un tono breve si la lectura automática está activa.

No es un detector dedicado de bajo consumo: el VAD y el micrófono siguen activos
mientras el modo está encendido. Se detiene al desconectar, ocultar la ventana o
retirar el permiso. El primer permiso o gesto de audio del navegador sigue
siendo necesario.

### Interrumpir mientras habla

Marca **Interrumpir hablando** antes de iniciar la conversación. Durante la
respuesta el micrófono queda abierto con cancelación de eco y:

1. Al primer indicio de voz, Thorn baja su volumen al 15 % (no espera la transcripción).
2. Se transcribe lo que dijiste y se clasifica (`frontend/src/barge-in.js`):
   - «Espera», «para», «silencio», con o sin el nombre: **pausa**; «sigue» la
     retoma, incluidas las frases que faltaban.
   - Eco de su propia voz (la mayoría de las palabras ya estaban en la respuesta),
     asentimientos («sí», «ajá») o ruido: recupera el volumen y continúa.
   - Cualquier otra frase: **corta la respuesta** y atiende la nueva orden.
3. La escucha se rearma tras cada fragmento. Antes quedaba sorda después del
   primero (por ejemplo, tras captar su propio eco), y por eso a veces no se dejaba
   interrumpir.

Con parlantes y sin buena cancelación de eco puede bajar el volumen por su propio
eco. Para probar, usa primero audífonos.

El final de frase se decide tras 1,05 s de silencio y la reapertura tras una
respuesta espera 250 ms para reducir el eco. Son valores de funcionamiento,
no una promesa de latencia total: también cuentan STT, el modelo, TTS y el equipo.

### Voz por frases

Con Piper, la respuesta se sintetiza frase a frase: la primera suena antes y la
siguiente se prepara mientras suena la actual (`frontend/src/speech-queue.js`).
Antes de sintetizar, `core/speech_text.py` quita marcado, citas `[1]` y URL,
pronuncia «12,5 gigabytes», «23 milisegundos» y «Thorn» en lugar de deletrear
«T.H.O.R.N». Lo que ves en el HUD no cambia.

## OCR de una página escaneada

1. Abre **Documentos** y carga un PDF.
2. En el visor, selecciona la página y pulsa **Leer página escaneada**.
3. Revisa **Texto** y usa **Preguntar**. Si hace falta, repite en otra página.

Se utiliza [Tesseract.js](https://github.com/naptha/tesseract.js/blob/master/docs/local-installation.md)
con español, worker y WASM locales, sin CDN. Se carga al pedirlo y se reutiliza
un único worker; se libera tras 15 segundos inactivo. Cancelar, cerrar o minimizar
el visor interrumpe la operación. La página se representa con un límite de
2,4 millones de píxeles y la operación tiene un plazo de 45 segundos. El visor
admite imágenes de origen de hasta 12 MP para cubrir escaneos habituales de
300 dpi sin dejar que crezcan indefinidamente.

Se adjuntan hasta 3.000 caracteres por página y 6.000 por documento. El original
no se modifica ni se conserva al cerrar la sesión. El OCR puede sustituir la
lectura de texto de esa página: revisa el resultado antes de confiar en cifras,
códigos o nombres de componentes. Leer etiquetas no equivale a entender el
funcionamiento de un circuito, su topología o sus riesgos eléctricos.

## Gestos y consumo del HUD

El cursor usa suavizado temporal y una pequeña zona muerta para el temblor.
La palma debe mantenerse durante 600 ms con detecciones continuas; la pinza y
su apertura necesitan al menos 80 ms. Si se pierde la mano, se corta el arrastre.
Si el detector no responde durante 2,5 segundos, se libera su worker y se informa.
La cámara conserva su consentimiento propio y los gestos solo mueven el HUD.

El núcleo tiene un presupuesto de 20 FPS en reposo animado, 12 con carga CPU alta,
2 con animaciones apagadas o reducidas y hasta 30 al reaccionar al audio.
Se pausa al ocultar la ventana. El FPS real depende de la GPU y de la carga;
estos límites no son un benchmark del F15.

## Comprobaciones

Se añadieron pruebas de cifrado, búsquedas, recuperación entre instancias,
permisos, borrado con respuestas pendientes, nombre de activación, OCR y gestos.
También se probó con MySQL real en una base temporal: cierre y reapertura del
pool, recuperación de un turno antiguo, respuesta con Ollama real, restauración y borrado. La prueba de
navegador reconoce un PDF de imagen real y comprueba que no haya solicitudes
externas. Ninguna prueba abre tu cámara o micrófono físicos ni compra síntesis.
