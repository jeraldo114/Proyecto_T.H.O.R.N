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

Vosk español puede escribir el nombre como «Tor», «Torn» o «Thor»; aceptamos esas
variantes al principio de la frase. No activamos el modelo por encontrar esas
letras dentro de «tornado» u otra palabra. Aun así, puede haber falsos positivos.
Esto es un filtro sobre STT local, **no un detector dedicado de bajo consumo**.
El micrófono y la transcripción siguen trabajando mientras el modo está activo;
las frases ambientales descartadas no pasan al modelo, al campo de transcripción
ni al archivo de conversación.

La entrada se cierra durante la respuesta, no hay escucha simultánea para
interrumpir la voz. Se detiene al desconectar, ocultar la ventana o retirar el
permiso. El primer permiso o gesto de audio del navegador sigue siendo necesario.

El final de frase se decide tras 650 ms de silencio y la reapertura tras una
respuesta espera 250 ms para reducir el eco. Son valores de funcionamiento,
no una promesa de latencia total: también cuentan STT, el modelo, TTS y el equipo.

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
