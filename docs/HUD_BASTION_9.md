# El escritorio de T.H.O.R.N — Bastion 9

La referencia de los reels se aplica a la composicion: espacio libre, anillos
azules, reloj de presencia y contenido en ventanas. Los recursos y la identidad
son propios de T.H.O.R.N. No se incluyen videos, logos o audios del otro proyecto.

**NOTA DEL OPERADOR:** el nucleo acompana el trabajo; no tiene que taparlo.
Si un mapa o un documento necesita espacio, se lo damos. Los brillos adornan;
las cifras y los estados tienen que tener una fuente.

## Como usarlo

| Control | Resultado |
| --- | --- |
| Nucleo: clic o Enter/Espacio | Abre el menu radial de documentos, mapas, camara, diagnostico, memoria y preferencias. |
| Arrastrar el nucleo | Cambia su posicion dentro del HUD, sin mover el puntero de Windows. |
| Boton Presencia/Trabajo o F2 | Alterna reloj/nucleo y escritorio de resultados. Preserva las ventanas en RAM; Presencia apaga la camara. |
| Dock inferior | Abre las vistas y herramientas, sin activar dispositivos automaticamente. |
| Encabezado de una ventana | Arrastra por raton o usa flechas. Shift+flecha mueve mas rapido. |
| Controles de ventana | Minimizar, ampliar/restaurar y cerrar. Los chips del escritorio restauran resultados minimizados. |
| Ctrl+K | Enfoca la consola. |
| Ctrl+punto | Abre/cierra el menu radial. |
| Alt+1 a Alt+6 | Presencia, Sistema, Actividad, Asistente, Historial y Red. |
| Escape | Cierra primero el menu radial si esta abierto; el control de voz existente permite detener la respuesta. |

Presencia muestra la hora real del navegador/sistema, el estado observable del
asistente y el ultimo saludo local. En pantallas bajas, el reloj pasa a un lado
del nucleo. Trabajo coloca el nucleo en una esquina y deja el centro a los
resultados. Los valores CPU y RAM proceden de las muestras autenticadas; sin
una lectura vigente se muestra un guion. El diagnostico completo sigue disponible.

## Ventanas y controles reales

Los resultados de respuesta, fuentes, resumen, sistema, PDF y mapas proceden
de la API de Bastion 9. Conservan titulo, fecha y origen. El contenido del usuario
y de Internet se coloca como texto seguro, sin convertirlo en HTML ejecutable.
Hay hasta ocho resultados por sesion. Los paneles de controles reutilizan los
mismos formularios y permisos; no crean una segunda sesion ni copian tokens.

Al mover una ventana, el dibujo se actualiza en el navegador. Al soltar se envia
una sola posicion normalizada al servidor. Las escrituras se ordenan para que
mover y cerrar no se crucen; una consulta vieja no borra un resultado nuevo.
Las posiciones son temporales y pertenecen a esa sesion. Cambiar de modo no
borra el contexto; cerrar un documento libera su original y su visor.

## PDF

Al subir un PDF aparecen su representacion grafica, paginas, zoom, texto,
consulta y descarga. PDF.js se carga al necesitarlo, desde recursos locales,
con workers de lectura y renderizado. No se ejecutan scripts ni acciones del
documento. El canvas esta limitado a 2048 pixeles por lado y las imagenes del
visor a cuatro megapixeles. Una pagina incompatible informa el fallo y conserva
el acceso al texto y al original.

El parser del backend sigue aislado y aplica los limites documentados en
[Bastion 9](BASTION_9.md): 8 MiB, 40 paginas y extraccion textual acotada.
La consulta usa ese texto, no las imagenes representadas. No hay OCR ni analisis
visual de circuitos por el modelo de lenguaje.

## Mapas

Una busqueda obtiene coordenadas de Wikipedia; no pide ubicacion GPS. Primero
autoriza Internet en **Red**. Luego elige **Cargar mapa · OpenStreetMap** en el
resultado. Esa accion permite solicitar las teselas visibles a
`https://tile.openstreetmap.org`. Se puede ampliar, desplazar y recentrar.

La atribucion de OpenStreetMap permanece visible. Cada tesela usa un Referer
del origen local, sin token, consultas privadas ni rutas de documentos. El HUD
respeta la cache del navegador, no precarga regiones ni permite descarga masiva.
Ocultar/minimizar el panel o cambiar a Presencia retira el mapa activo; revocar
Internet detiene su capa y exige cargarla de nuevo. No hay un mapa ficticio si
el proveedor falla: se conservan las coordenadas y se indica la indisponibilidad.

La politica de origen solo admite ese host adicional para imagenes; el resto
de conexiones del HUD sigue limitado al servidor local. Las URLs de fuentes
mantienen la validacion existente. Ver [politica de teselas de OSM](https://operations.osmfoundation.org/policies/tiles/)
y [opcion Referer de Leaflet](https://leafletjs.com/reference.html#tilelayer-referrerpolicy).

## Camara, gestos y voz

La camara necesita su autorizacion y el permiso del navegador. El seguimiento
de manos es optativo. Palma abierta durante 0,6 s arma el cursor; pinza arrastra
una ventana o el nucleo. El gesto actua sobre el escritorio, nunca confirma una
accion Windows. Cerrar, minimizar o pasar a Presencia apaga la captura. Las
imagenes no se envian a proveedores ni al backend.

El nucleo distingue reposo, escucha, recepcion, transcripcion, consulta,
reproduccion, audio bloqueado y errores de conversacion. La expansion de sus
anillos usa RMS real del audio de salida o el nivel de entrada durante una
frase. La rotacion de reposo es ornamental. Se mantienen limite de 30 FPS,
pausa al ocultar y movimiento reducido. Si el navegador bloquea el audio, el
boton **Reproducir audio listo** reutiliza el buffer recibido.

## Donde editar y comprobar

- `desktop.js` compone los modos, el menu y las herramientas.
- `floating-panels.js` administra ventanas; `panel-layout.js` acota posiciones.
- `panel-content.js` presenta datos; `pdf-viewer.js` y `map-viewer.js` cargan los
  visores solo cuando hacen falta.
- `core-scene.js` dibuja anillos; `desktop.css` define el escritorio y sus
  adaptaciones. `hud.js` conserva la integracion con los servicios.

```powershell
cd frontend
npm ci
npm test
npm run build
$env:THORN_TEST_URL = 'http://127.0.0.1:8005'
npm run test:desktop
```

La prueba visual necesita el servidor local de ensayo, Ollama/fuentes disponibles
y las imagenes de prueba privadas existentes en `.artifacts/bastion9-fixtures/`.
Comprueba cartografia real, PDF con texto visible, paginas, zoom, movimiento por
raton y teclado, restauracion, seguimiento de manos con una imagen y pinza
simulada. No utiliza camara o microfono fisicos. Guarda capturas y resultados
en `.artifacts/hud-visual/`. La comodidad de gestos con tu camara requiere prueba
manual; el seguimiento sigue siendo experimental.

La entrega se comprobo con 202 pruebas de backend, 45 de modulos del frontend,
la suite general del HUD y pruebas de voz local, microfono, red y escritorio.
El ejecutable final paso WebView2 y mantiene los mismos recursos verificados.
La distribucion de 1396 archivos esta firmada con la clave publica existente.
Las capturas de la prueba incluyen datos reales y documentos de ensayo
identificados; no se presentan como mediciones inventadas del equipo.
