# T.H.O.R.N — Bastion 9

Actualizacion funcional, 1 de noviembre de 2026. Version del codigo: `0.9.0`.
El [escritorio visual](HUD_BASTION_9.md) conecta estas capacidades mediante
Presencia/Trabajo, menu radial, dock y ventanas de resultados. La vista
**Asistente** conserva los controles de servicio bajo **BASTION 9 / HERRAMIENTAS**.

**NOTA DEL OPERADOR:** primero tiene que hacer lo que promete. Cuando demos
el salto visual, cada panel ya tendra datos, permisos y un cierre real detras.

## Que puedes hacer

| Capacidad | Uso | De donde sale el resultado |
| --- | --- | --- |
| Conversacion con continuidad | Menciona un proyecto y pregunta despues por su nombre. | Ollama recibe pares de turnos recientes de esa misma sesion. |
| Resumen de inicio | Boton «Resumen de inicio» o «Buenos dias». | Hora y zona configuradas, CPU/RAM vigentes y alertas registradas; sin noticias inventadas. |
| Ordenes directas | «Muestra el sistema», «Abre el historial», «Modo presencia», «Modo trabajo». | Reconocedor de frases completas; estas ordenes no esperan inferencia. |
| Detener y olvidar | Escape, «Detener respuesta», «Silencio» o «Borra el contexto». | Cancela trabajo de la sesion; borrar elimina turnos, resultados y PDF de su RAM. |
| Preferencias | Guarda nombre, zona horaria, saludo y uso de notas en Asistente. | SQL con el cifrado existente; no guarda autorizaciones de camara o nube. |
| PDF local | Elige un archivo y pulsa «Leer documento»; luego formula una pregunta. | Extraccion local de texto y consulta a Ollama con extractos acotados y pagina. |
| Lugares y mapas | «Muestra el mapa de Londres» o formulario «Mapas con fuente». | Coordenadas de articulos de Wikipedia y enlaces a OpenStreetMap con atribucion. |
| Camara local | Marca su autorizacion y pulsa «Iniciar camara». | Vista del dispositivo en tu navegador; audio desactivado en esta captura. |
| Gestos experimentales | Activa seguimiento, manten la palma abierta 0,6 s y haz pinza. | Detector local de 21 puntos, en un worker; actualiza posicion de un resultado seleccionado. |
| Multimedia de Windows | «Deten la musica» o «Pausa la musica», y confirma la propuesta. | Catalogo cerrado: tecla Detener o Reproducir/Pausar, con auditoria. |

Las teclas multimedia dependen del reproductor activo. Pausa es una tecla
alternante, por eso se describe como **reproducir/pausar**. Las acciones OS
conservan las restricciones existentes, incluida la ejecucion sin elevacion.
Una sugerencia o un gesto nunca confirma una accion de Windows.

## Contexto, memoria y resultados

El contexto conversacional vive solo en RAM: hasta ocho mensajes breves
completados, con limite adicional de bytes. El envio al modelo aplica otro
presupuesto para conservar sus 2048 tokens de contexto configurados; no se
promete recordar una conversacion ilimitada. Una renovacion del token conserva
la identidad de sesion; otra sesion no ve esos turnos. Revocar, desconectar,
caducar o borrar libera su contenido. La limpieza periodica revisa cada 10 s;
el contexto inactivo caduca a los 900 s.

Las notas explicitas siguen en la memoria cifrada existente. Solo se adjuntan
hasta dos notas breves con coincidencias de palabras cuando esta preferencia y
el permiso lo permiten. Es una recuperacion sencilla, no memoria semantica ni
entrenamiento del modelo. No se persisten conversaciones completas por defecto.

Cada sesion admite ocho resultados con tipo, titulo, origen, fecha, estado y
posicion normalizada. Hay cierre, enfoque, minimizado, restauracion y movimiento.
El escritorio permite arrastrarlos, ampliarlos, minimizarlos y restaurarlos.
Presencia conserva sus posiciones; Trabajo vuelve a mostrarlos. El movimiento
es local mientras arrastras y se guarda al soltar, sin peticiones por cada frame.

## Documentos y mapas: limites claros

Los PDF admiten hasta 8 MiB y 40 paginas, con un maximo de 6000 caracteres de
texto extraido. Hay dos documentos por sesion y 32 MiB globales de originales
retenidos en RAM. El lector corre en un proceso separado, con timeout de 15 s,
salida limitada y vigilancia de memoria de 256 MiB. Esta vigilancia no equivale
a una sandbox del sistema operativo ni garantiza un techo duro de memoria.
Cancelar termina el proceso que pertenece a esa lectura. No recibe claves ni
variables privadas del servidor.

El texto de un PDF es contenido no confiable. Sus consultas no reciben notas
privadas ni capacidad de proponer acciones OS. La seleccion para una pregunta
prioriza coincidencias locales y limita los extractos a 3600 caracteres; puede
omitir informacion, por lo que se indica truncamiento. No hay OCR, interpretacion
de circuitos o imagenes ni ejecucion de scripts PDF. El visor grafico usa PDF.js
con workers locales, paginas y zoom; el texto extraido sigue disponible. Ver un
esquema no implica que el modelo de texto interprete su circuito o sus imagenes.

Los mapas requieren habilitar Internet y el consentimiento de la sesion en
**Red e Internet**. No consultan GPS ni hacen geocodificacion de direcciones.
Una coordenada corresponde al articulo encontrado, no a tu ubicacion o a una
direccion precisa. Si no existe una coordenada valida, se informa sin inventarla.
El boton «Cargar mapa» solicita las teselas visibles a OpenStreetMap y permite
zoom y desplazamiento dentro del HUD. Requiere la autorizacion de Internet y
envia el origen local como Referer; no envia notas ni archivos. Ocultar el panel
pausa la cartografia. No hay rutas ni navegacion GPS.

## Camara y privacidad

La camara esta apagada al iniciar. Requiere una autorizacion separada y el
permiso del navegador. Las imagenes pasan al worker local; no se envian al API,
al modelo de lenguaje o a un proveedor. No se graba video. Apagar, desconectar,
ocultar la pagina o perder el dispositivo libera las pistas y termina el worker.
Un permiso que llega despues de cancelar tampoco deja abierto el dispositivo.

La vista previa usa hasta 15 FPS y el detector procesa aproximadamente ocho
imagenes por segundo, una pendiente a la vez, en CPU. La palma arma el cursor;
la pinza mueve resultados y el nucleo dentro del HUD con un cursor visible.
No mueve el puntero de Windows ni identifica personas. Cerrar o minimizar la
ventana de camara, o cambiar a Presencia, apaga su captura.
El seguimiento es experimental y puede fallar por iluminacion u oclusiones.

El detector es MediaPipe Hand Landmarker, descargado una vez desde Google con
SHA-256 fijo y servido despues desde el mismo servidor local. La
[documentacion oficial](https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker/index)
y su [ficha de modelo](https://storage.googleapis.com/mediapipe-assets/Model%20Card%20Hand%20Tracking%20%28Lite_Full%29%20with%20Fairness%20Oct%202021.pdf)
describen la procedencia, licencia y limites. No es un modelo creado desde cero
por este proyecto. Los resultados de sus puntos no demuestran comprension visual general.

## Preparar y ejecutar

En esta PC ya se instalo `thorn:bastion9`, reutilizando los pesos de Qwen
existentes. El servidor habitual sigue en `http://127.0.0.1:8004/`.

En una instalacion nueva, desde la raiz:

```powershell
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.txt
.\.venv\Scripts\python.exe tools/install_local_brain.py
.\.venv\Scripts\python.exe tools/install_vision_model.py
cd frontend
npm ci
npm run build
cd ..
.\.venv\Scripts\python.exe serve.py --port 8004
```

Configura `THORN_LLM_MODEL=thorn:bastion9` y conserva `THORN_LOCAL_AI_ONLY=1`.
La instalacion de voz/dictado y MySQL sigue en sus guias anteriores. El detector
de manos es opcional: sin su archivo, la interfaz informa que no esta disponible
al solicitar seguimiento; no lo descarga ni abre la camara al iniciar.

La entrada de escritorio es `desktop.py`. Para distribuirla:

```powershell
.\.venv\Scripts\python.exe tools/build_desktop.py --jobs 2
.\.venv\Scripts\python.exe tools/verify_compiled.py
.\.venv\Scripts\python.exe tools/package_release.py
powershell -NoProfile -File tools/create_shortcut.ps1
```

El ZIP de esta version se llama `THORN-Bastion9-Windows-x64.zip` y queda en
`.artifacts/`. Incluye recursos publicos del HUD y, si se preparo antes de
compilar, el detector de manos; no las claves ni los modelos privados de voz,
dictado o lenguaje. El acceso directo Bastion 9 usa el perfil cifrado existente.
El empaquetador exige que la version y el SHA-256 del ejecutable coincidan
con la comprobacion real anterior; una compilacion distinta debe probarse otra vez.

**No renombres los identificadores de cifrado, la carpeta de datos ni sus
dominios Bastion 8.** Se conservan deliberadamente para poder leer secretos,
respaldos y notas anteriores. El nombre de version visible y el formato de
almacenamiento son contratos diferentes. Las claves de datos se conservan;
la autoridad de acceso puede rotarse por separado sin volver a cifrar tus notas.

## Comprobar

```powershell
.\.venv\Scripts\python.exe -W error::ResourceWarning -m unittest discover -s tests -v
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe tools/check_repository.py --working-tree
cd frontend
npm test
$env:THORN_TEST_URL = 'http://127.0.0.1:8004'
npm run test:e2e
npm run test:network
```

La prueba adicional `npm run test:bastion9` usa un servidor local ya preparado,
Ollama real y fixtures privados en `.artifacts/bastion9-fixtures/`. Comprueba
extraccion PDF, una pregunta de seguimiento y el detector con una imagen
inyectada como camara de prueba. No abre la camara fisica ni usa servicios de pago.
Su resumen y captura quedan en esa misma carpeta. No sustituye la comprobacion
manual de comodidad de gestos, latencia o audio en el equipo del operador.

El [HUD Bastion 9](HUD_BASTION_9.md) integra estos contratos. El pulso del nucleo
usa el audio reproducido real; su rotacion de reposo es decorativa.

## Validacion de esta entrega

- 202 pruebas de backend y 45 de modulos del frontend correctas.
- Django sin observaciones; auditorias de dependencias Python y npm sin
  vulnerabilidades conocidas al realizar esta comprobacion.
- Flujo real en navegador: PDF extraido, pregunta de seguimiento recordada,
  detector de manos con imagen de prueba y borrado completo del contexto.
  La prueba no utilizo la camara fisica ni envio imagenes fuera del navegador.
- Ejecutable probado desde una carpeta temporal: MySQL cifrado, Piper, Vosk,
  lectura PDF en su proceso hijo y continuidad con `thorn:bastion9`.
- Ventana WebView2, sesion, reloj, canvas, bandeja y cierre seguro verificados.
- Recursos del frontend compilado identicos a los del codigo preparado.
  ZIP firmado de 1396 archivos, verificado con la clave publica existente.
- Acceso `T.H.O.R.N - Bastion 9.lnk` creado y servidor local activo en 8004.

Estas comprobaciones verifican funciones y aislamiento; no son una certificacion
de seguridad ni una medicion completa de latencia o comodidad de los gestos.
