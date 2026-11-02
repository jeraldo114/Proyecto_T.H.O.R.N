# HUD de T.H.O.R.N - Bastion 9

El escritorio actual esta explicado en [HUD Bastion 9](HUD_BASTION_9.md).
El [analisis de Huw Prosser](REFERENCIA_HUW_PROSSER.md) documenta su referencia.
Esta guia conserva los contratos de datos y controles de la interfaz base.

## Que se construyo

La raiz del servidor abre un centro de mando local, no una pagina publicitaria.
La geometria central es un emblema circular de Three.js con anillos y particulas
ornamentales. Representa estados reales y recibe amplitud del audio; no es un
mapa fisico de componentes ni una visualizacion de neuronas.

Presencia deja el reloj y el nucleo en el centro. Trabajo abre ventanas de
resultados y desplaza el nucleo a una esquina. Diagnostico y Actividad conservan
los sensores, el bus y las graficas. Los avisos de uso de recursos no son una
deteccion de malware.

## Origen de cada dato

| Dato | Fuente real |
| --- | --- |
| Hora, fecha y zona | Reloj y zona horaria del navegador/sistema local. |
| CPU, RAM, disco y procesos | `/api/v1/system/`, recogido por psutil en el backend. |
| Graficas | Hasta 60 muestras observadas durante esta sesion del HUD. |
| Contadores, consumidores y tiempo activo | `/api/v1/status/`, leidos del runtime. |
| Latencia API | Tiempo HTTP de ida y vuelta medido con performance.now(). |
| Actividad | `/api/v1/activity/`, metadatos de hasta 120 eventos entregados al bus. |

Las graficas arrancan vacias. Consultar varias veces la misma muestra no crea
puntos nuevos; el eje horizontal usa su fecha real. La vista breve agrupa
las muestras consecutivas, mientras que el registro permite ver cada evento.
Ni los datos ausentes ni los errores se convierten en valores inventados.
Cuando el enlace falla o la muestra envejece, los ultimos datos se atenuan y
se identifican como no vigentes. Desconectar borra los datos privados de la UI.

## Controles disponibles

- Navegacion: centro de mando, detalle del sistema y registro de actividad.
- Actualizar, pausar/reanudar consultas y filtrar eventos.
- Diagnostico: publica un evento real `command.received` con correlacion;
  confirma su aceptacion en la cola, no una ejecucion ni un escaneo antivirus.
- Informe: descarga JSON con estado, muestras observadas y metadatos de eventos.
- Consola: `estado`, `sistema`, `eventos` y `limpiar`; no ejecuta texto en una shell.
- Preferencias: intervalo de consulta, animacion y movimiento reducido.
- Modo enfoque y pantalla completa en escritorio.

Asistente muestra disponibilidad real del modelo, acciones Windows y reglas de
alertas. Historial agrega auditoria MySQL y memoria explicita. La consola entrega
las preguntas no reservadas a Ollama, si motor y modelo estan instalados. No ejecuta
las propuestas del modelo: una accion siempre tiene un dialogo de confirmacion.
La salida predeterminada usa Piper local, con lectura automatica al conectar y
control de reproduccion/detencion. Se puede desmarcar la lectura automatica.
ElevenLabs opcional requiere autorizacion independiente y comienza sin lectura.
Ver [Autonomia local](AUTONOMIA_LOCAL.md). Una salida bloqueada
por el navegador conserva el audio para reproducirlo sin otra sintesis.
Incluye volumen y una prueba local de altavoces sin creditos. El dictado usa Vosk
local con dictado por pulsacion o conversacion por turnos; termina cada frase a
los 15 s como maximo. El dictado permite corregir el texto antes de consultar;
el modo conversacion lo consulta automaticamente. Ver [Voz](VOZ_ELEVENLABS.md) y
[Microfono](MICROFONO_LOCAL.md).
desktop.py abre el HUD en una ventana Windows WebView2
con bandeja; tambien se distribuye compilado como THORN.exe. Ver [Asistente local](ASISTENTE_LOCAL.md).

## Autenticacion y privacidad

`Conectar` abre un campo de contrasena para el token de `.env`. Se valida contra
la API y se conserva exclusivamente en memoria mientras la pagina esta abierta.
No se escribe en cookies, localStorage, sessionStorage, URL, HTML ni informes.
La recarga requiere volver a conectar. No compartir el token ni publicar informes
del equipo sin revisarlos: la telemetria sigue siendo informacion privada.

El codigo, fuentes e iconos se sirven desde el mismo host. No se consulta un CDN,
el microfono no se abre al cargar la pagina ni se envia telemetria por defecto
a servicios externos. El audio de dictado se procesa localmente, sin guardarse.
Solo si se desactiva el modo local y se elige ElevenLabs, envia el texto elegido o las nuevas respuestas
si autorizas expresamente su lectura automatica; pueden incluir cifras del PC
o notas personales. El servidor
sigue ligado a 127.0.0.1. Una politica CSP restringe scripts y conexiones al mismo
origen; style-src admite estilos inline para dimensiones dinamicas y Three.js.

## Archivos y ejecucion

| Archivo | Responsabilidad |
| --- | --- |
| `frontend/index.html` | Estructura del centro de mando y sus dialogos. |
| `frontend/src/hud.css` | Diseno responsive, colores, tipografia y estados. |
| `frontend/src/hud.js` | Consultas async, autenticacion en memoria y controles. |
| `frontend/src/core-scene.js` | Emblema Three.js, interaccion y limite a 30 FPS. |
| `frontend/src/model.js` | Formatos y reglas de historial, sin muestras artificiales. |
| `frontend/build.mjs` | Compila recursos locales y un manifiesto cerrado. |
| `api/hud.py` | Lee los recursos en un hilo, los cachea y rechaza rutas desconocidas. |

```powershell
cd frontend
npm ci
npm run build
npm test
cd ..
.\.venv\Scripts\python.exe serve.py --port 8002
```

Abrir http://127.0.0.1:8002/. Los recursos compilados se cachean en memoria:
despues de cambiar el frontend, recompilar y reiniciar el servidor.
`node_modules`, `dist`, capturas e informes de prueba no se publican en Git.
El repositorio conserva fuentes y package-lock.json para reconstruirlos.

## Verificacion

Con el servidor abierto, `npm run test:e2e` desde frontend valida datos reales,
token incorrecto/correcto, ausencia de credenciales almacenadas, pixels y movimiento
del canvas, pausa, filtros, consola, descargas, navegacion, desconexion y fallo de red.
Comprueba anchos desde 320 hasta 1920 px y guarda capturas en `.artifacts/`.

En Windows usa Chrome instalado. `THORN_BROWSER` permite indicar otro ejecutable,
`THORN_TEST_URL` cambia el servidor y `THORN_PYTHON` cambia Python. En Linux instalar
Chromium con `npx playwright install --with-deps chromium`. CI arranca un servidor
aislado sin MySQL con claves efimeras; las muestras siguen viniendo de psutil.

Las pruebas de backend comprueban el cache de recursos, autenticacion, cabeceras,
limite de actividad y exclusion de payloads. Actividad sigue siendo volatil;
Historial es la vista separada de auditoria durable y no registra cada muestra.

## Referencias

- [Three.js WebGLRenderer](https://threejs.org/docs/pages/WebGLRenderer.html)
- [Iconos Lucide](https://lucide.dev/guide/packages/lucide)
- [Async en Django](https://docs.djangoproject.com/en/5.2/topics/async/)
- [Capturas con Playwright](https://playwright.dev/docs/screenshots)
## Voz Y Conversacion

El nucleo Three.js recibe amplitud RMS del MP3 real mediante Web Audio; expansion
y luminosidad siguen sus muestras, sin numeros aleatorios. Detener, finalizar,
silenciar o cancelar vuelve el nivel a cero. Movimiento reducido evita expansion
pero conserva una indicacion luminosa. La animacion se puede detener por separado.

El interruptor Conversacion muestra escucha, recepcion, transcripcion, consulta,
respuesta o bloqueo. No muestra escucha mientras el dispositivo esta cerrado.
Consulta [MICROFONO_LOCAL.md](MICROFONO_LOCAL.md) para permisos y turnos; la voz
Piper es local; ElevenLabs opcional sigue requiriendo autorizacion explicita por sesion.
