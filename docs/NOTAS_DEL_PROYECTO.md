# Notas para entender T.H.O.R.N

T.H.O.R.N es el asistente. Bastion 8 es el nombre de su version de actualizacion.
El backend se ejecuta localmente en Python; Django agrega una puerta HTTP para
conectar una futura interfaz con ese backend.

## Que hace cada archivo

| Archivo | Responsabilidad en palabras sencillas |
| --- | --- |
| `requirements.txt` | Lista los paquetes que hay que instalar. asyncio ya viene con Python. |
| `.env` | Configuracion privada: conexion MySQL y claves de la API. No se publica en Git. |
| `.env.example` | Plantilla sin claves privadas para configurar otra instalacion. |
| `core/events.py` | Define el formato de un mensaje: que paso, origen, datos, prioridad e identificador. |
| `core/event_bus.py` | Guarda mensajes en una cola y los entrega a las funciones suscritas. |
| `core/runtime.py` | Arranca el pool y los consumidores; los cierra en el orden correcto. |
| `core/db_core.py` | Presta conexiones MySQL y asegura su devolucion al pool. |
| `core/logging_config.py` | Envia los logs a un hilo que escribe en consola. |
| `main.py` | Ejecuta el nucleo desde consola, sin servir HTTP. |
| `serve.py` | Ejecuta Django y el nucleo juntos, en un unico proceso local. |
| `manage.py` | Permite ejecutar comprobaciones y otros comandos de Django. |
| `thorn_web/config.py` | Lee .env y genera las claves locales que falten. |
| `thorn_web/settings.py` | Configura Django: hosts, limites, idioma y uso de MySQL. |
| `thorn_web/urls.py` | Asocia una direccion HTTP con la funcion que debe responder. |
| `thorn_web/asgi.py` | Construye la aplicacion HTTP y registra los manejadores iniciales. |
| `thorn_web/lifespan.py` | Conecta el arranque/cierre del servidor con el runtime. |
| `api/middleware.py` | Valida que la peticion use un host local permitido. |
| `api/views.py` | Valida peticiones y devuelve JSON: salud, estado o evento aceptado. |
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
los workers. Solo despues confirma que el servicio esta listo.

Al cerrar con Ctrl+C, Uvicorn deja de aceptar peticiones, espera las activas y
envia `lifespan.shutdown`. El runtime cierra publicaciones nuevas, procesa lo
pendiente hasta el plazo configurado, cancela trabajo restante y cierra MySQL.
Finalmente se vacian los logs. Un arranque fallido tambien libera recursos.

No se crean conexiones dentro de AppConfig.ready() ni un pool por peticion.
Las vistas reciben el runtime que ya existe mediante el scope ASGI, un
diccionario que acompana a cada peticion.

## Como interpretar la API

| Respuesta | Significado |
| --- | --- |
| `200` en health/status | El runtime esta arrancado; health no comprueba MySQL en cada llamada. |
| `202` en events | El evento entro a la cola; no garantiza el exito del manejador. |
| `400` | JSON, datos del evento o host incorrectos. |
| `401` | Falta el token Bearer o es incorrecto. |
| `405` | Se uso un metodo HTTP incorrecto para esa ruta. |
| `413` | El cuerpo supera los 16 KiB permitidos. |
| `415` | El contenido no se envio como application/json. |
| `503` | El runtime no esta disponible o la cola no acepta el evento a tiempo. |

Las consultas de salud/estado son locales y de lectura. Enviar eventos exige
`Authorization: Bearer <THORN_API_TOKEN>`. El token se genera aleatoriamente,
se guarda en .env y no se incluye en las respuestas. No se usa autenticacion
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

El ejemplo requiere una tabla existente; esta fase no crea tablas. Los valores
recibidos del usuario se pasan como parametros SQL, nunca concatenados al SQL.

## Que ya existe y que sigue pendiente

Ya existen el pool, el bus, el supervisor, logs y la API. La demo y el receptor
HTTP registran eventos; todavia no hay motor de lenguaje, voz, sensores de
Windows ni ejecutor de acciones sobre la PC.

La cola vive en memoria: una caida del proceso puede perder mensajes. El pool
no convierte los eventos en registros persistidos automaticamente. Una fase
posterior definira las tablas de memoria y auditoria y la entrega durable.

El siguiente modulo previsto es un sensor local que publique el estado del
equipo en el bus. Se podra consultar desde esta API sin cambiar el nucleo.

## Referencias de las decisiones

- [Soporte asincrono de Django 5.2](https://docs.djangoproject.com/en/5.2/topics/async/)
- [Despliegue ASGI de Django](https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/)
- [Colas de asyncio](https://docs.python.org/3.11/library/asyncio-queue.html)
