# Notas para entender T.H.O.R.N

T.H.O.R.N es el asistente. Bastion 8 es el nombre de su version de actualizacion.
El backend se ejecuta localmente en Python; Django agrega una puerta HTTP para
conectar el HUD con ese backend. La ventana Windows reutiliza el HUD mediante WebView2.

Para recorrer el codigo en Visual Studio Code, abre `THORN.code-workspace` y
sigue el [mapa del proyecto](MAPA_DEL_PROYECTO.md). Las `NOTA DEL OPERADOR`
explican decisiones concretas con el tono de T.H.O.R.N: directo y tactico.

## Que hace cada archivo

| Archivo | Responsabilidad en palabras sencillas |
| --- | --- |
| `requirements.txt` | Lista los paquetes que hay que instalar. asyncio ya viene con Python. |
| `.env` | Configuracion local; los valores secretos migrados quedan vacios. No se publica en Git. |
| `.env.example` | Plantilla sin claves privadas para configurar otra instalacion. |
| `core/events.py` | Define el formato de un mensaje: que paso, origen, datos, prioridad e identificador. |
| `core/event_bus.py` | Guarda mensajes en una cola y los entrega a las funciones suscritas. |
| `core/runtime.py` | Arranca el pool y los consumidores; los cierra en el orden correcto. |
| `core/system_sensor.py` | Lee indicadores del equipo en un hilo y conserva la ultima muestra. |
| `core/db_core.py` | Presta conexiones MySQL y asegura su devolucion al pool. |
| `core/alerts.py` | Distingue picos breves, presion sostenida y recuperacion. |
| `core/audit.py` | Guarda historial, notas voluntarias y reglas en tablas propias. |
| `core/actions.py` | Abre solo destinos conocidos, despues de confirmar y registrar la intencion. |
| `core/local_llm.py` | Habla con Ollama local; no ejecuta lo que responda un modelo. |
| `core/services.py` | Arranca y cierra los nuevos servicios junto al nucleo. |
| `desktop.py` | Ventana, bandeja y servidor propio; no habilita inicio automatico por defecto. |
| `core/logging_config.py` | Envia los logs a un hilo que escribe en consola. |
| `main.py` | Ejecuta el nucleo desde consola, sin servir HTTP. |
| `serve.py` | Ejecuta Django y el nucleo juntos, en un unico proceso local. |
| `manage.py` | Permite ejecutar comprobaciones y otros comandos de Django. |
| `thorn_web/config.py` | Lee configuracion y secretos protegidos; rechaza claves ausentes en modo cifrado. |
| `thorn_web/settings.py` | Configura Django: hosts, limites, idioma y uso de MySQL. |
| `thorn_web/urls.py` | Asocia una direccion HTTP con la funcion que debe responder. |
| `thorn_web/asgi.py` | Construye la aplicacion HTTP y registra los manejadores iniciales. |
| `thorn_web/lifespan.py` | Conecta el arranque/cierre del servidor con el runtime. |
| `api/middleware.py` | Valida que la peticion use un host local permitido. |
| `api/views.py` | Valida peticiones y devuelve JSON: salud, estado o evento aceptado. |
| `api/hud.py` | Sirve solo archivos compilados autorizados; nunca lee archivos en el loop. |
| `api/assistant.py` | Autentica y valida alertas, memoria, historial, consultas y confirmaciones. |
| `frontend/` | HUD visual conectado a la API. La guia HUD.md detalla sus archivos y controles. |
| `tests/` | Comprueba los contratos: errores, cancelacion, HTTP y liberacion de recursos. |

## Como viaja un evento

1. Un modulo detecta algo y crea un ThornEvent. La API tambien puede crearlo
   a partir de una peticion JSON autenticada.
2. `await bus.publish(event)` intenta ponerlo en la cola. Si no hay espacio,
   el productor espera y deja trabajar a los demas modulos.
3. Un consumidor, llamado worker, retira el evento segun su prioridad.
4. El worker llama a cada funcion registrada con `bus.subscribe(...)` para ese
   tipo de evento. Estas funciones se llaman manejadores o handlers.
5. El manejador usa APIs async para consultar o actuar. Si necesita guardar
   datos, abre una db_session y ejecuta SQL con el cursor.
6. Al terminar, el worker marca el mensaje como procesado y toma otro.

`event_id` identifica un mensaje. `correlation_id` permite relacionar varios
mensajes que pertenecen a la misma operacion, como una consulta y su resultado.

## Que significa asincrono

Una funcion `async def` puede ceder el control con `await` mientras espera una
operacion. El event loop aprovecha esa espera para ejecutar otras tareas.
Por ejemplo, esperar una consulta MySQL no debe impedir recibir otra peticion.

Escribir `async def` por si solo no convierte codigo bloqueante en asincrono.
Un `time.sleep()`, una lectura normal de archivos o una llamada sincronica a
otro servicio seguirian deteniendo ese hilo. Por eso el bootstrap que lee .env
y carga Django usa `asyncio.to_thread()`; las consultas usan aiomysql.

Django admite vistas async bajo ASGI, pero tambien contiene componentes
sincronicos que adapta a hilos. Esta fase utiliza vistas y middleware async,
mantiene el SQL en aiomysql y no promete que todo el framework sea async nativo.
Las funciones async del ORM no sustituyen nuestra capa de persistencia.

Los dos workers del bus son tareas cooperativas, no dos nucleos de CPU. Un
calculo pesado debe ejecutarse en otro proceso para no retrasar todo el loop.

## Que pasa al arrancar y cerrar

`serve.py` prepara las variables y carga Django en un hilo de apoyo. Uvicorn
envia `lifespan.startup`; el puente crea el runtime y espera el pool MySQL y
los workers. Tambien programa el sensor sin esperar su primera lectura.
Solo despues confirma que el servicio HTTP esta listo; la telemetria puede
necesitar unos instantes adicionales.

Al cerrar con Ctrl+C, Uvicorn deja de aceptar peticiones, espera las activas y
envia `lifespan.shutdown`. El runtime detiene primero el sensor y espera la
lectura nativa activa. Luego cierra publicaciones nuevas, procesa lo
pendiente hasta el plazo configurado, cancela trabajo restante y cierra MySQL.
Finalmente se vacian los logs. Un arranque fallido tambien libera recursos.

No se crean conexiones dentro de AppConfig.ready() ni un pool por peticion.
Las vistas reciben el runtime que ya existe mediante el scope ASGI, un
diccionario que acompana a cada peticion.

## Como interpretar la API

| Respuesta | Significado |
| --- | --- |
| `200` en health/status | El runtime esta arrancado; health no comprueba MySQL en cada llamada. |
| `200` en system | Hay una muestra vigente; la respuesta no consulta el OS. |
| `202` en events | El evento entro a la cola; no garantiza el exito del manejador. |
| `400` | JSON, datos del evento o host incorrectos. |
| `401` | Falta el token Bearer o es incorrecto. |
| `405` | Se uso un metodo HTTP incorrecto para esa ruta. |
| `413` | El cuerpo supera los 16 KiB permitidos. |
| `415` | El contenido no se envio como application/json. |
| `503` | El runtime no esta disponible o la cola no acepta el evento a tiempo. |

Las consultas de salud/estado son locales y de lectura. Consultar system o enviar eventos exige
`Authorization: Bearer <THORN_API_TOKEN>`. El token se genera aleatoriamente,
se conserva con DPAPI y el HUD recibe sesiones temporales. No se usa autenticacion
por cookies en estas rutas; por eso no necesitan sesiones ni formularios CSRF.

## Guardar datos correctamente

`db_session()` presta conexion y cursor. Una escritura debe confirmar su
transaccion con `await conn.commit()` antes de salir. Si no se confirma,
se revierte. Si falla una consulta o se cancela, la conexion se descarta para
evitar reutilizar una comunicacion MySQL incompleta.

```python
from core.db_core import db_session


async def contar_filas() -> int:
    async with db_session() as (_, cursor):
        await cursor.execute("SELECT COUNT(*) FROM una_tabla_existente")
        row = await cursor.fetchone()
        return int(row[0])
```

El ejemplo requiere una tabla existente; el asistente crea solo thorn_audit y
thorn_settings para sus propias funciones. Los valores
recibidos del usuario se pasan como parametros SQL, nunca concatenados al SQL.

## Que ya existe y que sigue pendiente

Ya existen pool, bus, supervisor, logs, sensor, API, HUD, alertas y auditoria.
Hay memoria voluntaria y acciones Windows cerradas con confirmacion. Ollama
esta integrado, pero necesita motor/modelo instalados; no se inventan respuestas
si faltan. La voz ElevenLabs esta integrada como salida opcional en la nube,
sin microfono. Requiere credenciales privadas, voz autorizada y consentimiento
de envio del texto; no se ha creado ninguna voz en la cuenta sin API key.
`core/personality.py` define como habla T.H.O.R.N sin cambiar sus permisos.
`core/elevenlabs_voice.py` usa HTTP asincrono y audio limitado en RAM; la herramienta
`tools/elevenlabs_voice.py` prepara una voz original, no copias del doblaje.
Ver [Voz ElevenLabs](VOZ_ELEVENLABS.md) para pasos, privacidad y consumo.

El microfono tiene dictado LOCAL manual y conversacion por turnos: el HUD pide
permiso, captura hasta 15 segundos por frase y Vosk convierte el audio a texto
en un worker. El dictado permite corregir antes de consultar. En conversacion,
Silero detecta el final de frase en un worker local y se consulta Ollama sin otra
pulsacion. El microfono se cierra durante la respuesta y vuelve a abrirse al
terminar; el indicador y el boton cuadrado permiten detenerlo. Autoarranque al
conectar es una preferencia .env independiente del consentimiento ElevenLabs.
El nucleo 3D recibe nivel RMS de la salida real; silencio o cancelacion lo dejan
en reposo. Ningun modo concede permisos adicionales para acciones Windows.
Ver [Microfono local](MICROFONO_LOCAL.md) para entender archivos, cierre y limites.

La cola vive en memoria: una caida del proceso puede perder mensajes. El pool
no convierte los eventos en registros persistidos automaticamente. Una fase
posterior podra agregar entrega durable del bus: la auditoria actual registra
operaciones seleccionadas, no convierte toda la cola en un outbox.

El sensor publica `system.snapshot`: CPU, RAM, disco del proyecto y cantidad
de procesos. No inspecciona el contenido de esos procesos. Si Windows tarda
en responder, el hilo de apoyo espera mientras HTTP y el bus siguen trabajando.
Si la lectura falla, emite `module.error`, marca la muestra como no vigente y
reintenta despues de la pausa configurada. Una cola llena puede perder el evento
de telemetria, pero no borra la muestra almacenada en memoria.

`/api/v1/system/` exige el token incluso siendo una consulta local. Un `503`
puede significar sensor desactivado, primera muestra pendiente, fallo o datos
viejos. El campo `fresh` evita confundir una lectura anterior con el estado
actual. No significa que el equipo sea seguro: son indicadores, no un antivirus.

La limpieza conserva `.env`, `.venv`, Git, tests, CI y las herramientas de
seguridad: cada uno sigue teniendo una funcion. Se eliminan caches generadas
fuera de `.venv` y logs antiguos sin un servidor activo; Python y el servidor
pueden generarlos de nuevo. No se borran datos privados ni dependencias a ciegas.

El catalogo actual permite abrir Bloc de notas, Calculadora y la carpeta del
proyecto. No conectar texto libre a una shell. [Asistente local](ASISTENTE_LOCAL.md)
explica los limites, las notas privadas y los resultados inciertos de acciones.

## Referencias de las decisiones

La vista Red distingue contadores locales e Internet: la primera no genera
trafico externo; la segunda necesita consentimiento por sesion. Wikipedia es una
fuente enciclopedica sin clave, no un buscador general. Se pueden leer URLs HTTPS
publicas y configurar Brave por separado. Fuentes web se resumen sin memoria ni
herramientas Windows. [Red e Internet](RED_E_INTERNET.md) explica que hace cada
archivo y por que se valida DNS antes de conectar a una IP publica.

- [Soporte asincrono de Django 5.2](https://docs.djangoproject.com/en/5.2/topics/async/)
- [Despliegue ASGI de Django](https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/)
- [Colas de asyncio](https://docs.python.org/3.11/library/asyncio-queue.html)
- [API de psutil: ventanas de CPU y consultas del sistema](https://psutil.readthedocs.io/stable/index.html)
