# T.H.O.R.N - Bastion 8

Asistente local para Windows. Bastion 8 identifica la version de actualizacion.
Esta fase implementa persistencia, contratos de eventos, supervision del ciclo
de vida y una API local con Django ASGI. Requiere Python 3.11 o superior.

La guia [Notas para entender T.H.O.R.N](docs/NOTAS_DEL_PROYECTO.md) explica cada
componente, el recorrido de los eventos y los limites de esta fase.
La guia [Publicacion segura en GitHub](docs/PUBLICACION_GITHUB.md) explica las
exclusiones, el control de secretos, CI y mantenimiento de dependencias.

## Ejecutar

Desde la carpeta del proyecto, con Python instalado:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.txt
.\.venv\Scripts\python.exe main.py --demo --sin-db
```

`asyncio` pertenece a Python; requirements.txt instala las dependencias externas.
El entorno .venv ya esta preparado en esta maquina.

Para usar persistencia, configurar `.env` y tener MySQL disponible con la base
`db_thorn` creada. El runtime no crea bases ni tablas:

```sql
CREATE DATABASE IF NOT EXISTS db_thorn CHARACTER SET utf8mb4;
```

```powershell
.\.venv\Scripts\python.exe main.py --demo
.\.venv\Scripts\python.exe main.py
```

La segunda ejecucion permanece activa hasta Ctrl+C. Se puede usar `--sin-db`
tambien en modo continuo para desarrollar modulos sin un servidor MySQL.

## API Django local

```powershell
.\.venv\Scripts\python.exe serve.py
```

El servidor escucha en http://127.0.0.1:8000. La primera ejecucion genera
DJANGO_SECRET_KEY y THORN_API_TOKEN dentro de .env, sin imprimir sus valores.
Si el puerto esta ocupado, usar `--port 8001`. Para desarrollar sin MySQL,
usar `--sin-db`.

| Metodo | Ruta | Resultado |
| --- | --- | --- |
| GET | `/api/v1/health/` | Confirma que termino el arranque; no hace un ping SQL. |
| GET | `/api/v1/status/` | Estado del runtime y contadores del bus. |
| POST | `/api/v1/events/` | Acepta un evento JSON autenticado con token Bearer. |

Enviar un evento desde otra consola PowerShell:

```powershell
$thornToken = .\.venv\Scripts\python.exe -c 'from dotenv import dotenv_values; print(dotenv_values(".env")["THORN_API_TOKEN"])'
$thornBody = @{ event_type = "command.received"; payload = @{ command = "status" } } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/events/ -ContentType "application/json" -Headers @{ Authorization = "Bearer $thornToken" } -Body $thornBody
```

`202 Accepted` confirma la entrada a la cola. El receptor actual registra el
evento; aun no ejecuta comandos del sistema. Solo se admiten `command.received`
y `system.alert`; los eventos de arranque/cierre pertenecen al supervisor.
El cuerpo esta limitado a 16 KiB y la espera de publicacion a un segundo.

`serve.py` carga Django en un hilo de apoyo y usa Uvicorn con un worker. Django
gestiona rutas y JSON; `aiomysql` conserva el acceso SQL asincrono. No hay modelos,
admin ni migraciones ORM en esta fase. El arranque y cierre usan lifespan ASGI,
por eso `manage.py runserver` redirige al uso de `serve.py`.

```powershell
.\.venv\Scripts\python.exe manage.py check
```

main.py y serve.py son entradas alternativas. Sus buses son independientes si
se ejecutan en dos procesos; no comparten eventos por el hecho de usar la misma DB.

## Registrar un modulo

```python
import asyncio

from core.events import EventType, ThornEvent
from core.runtime import ThornRuntime


async def handle_command(event: ThornEvent) -> None:
    command = event.payload.get("command")
    if command == "status":
        # Consultar servicios mediante APIs asincronas del modulo.
        return


async def main() -> None:
    runtime = ThornRuntime(use_database=False)
    runtime.bus.subscribe(EventType.COMMAND_RECEIVED, handle_command)
    async with runtime:
        await runtime.bus.publish(ThornEvent(
            EventType.COMMAND_RECEIVED, "console", {"command": "status"},
        ))
        await runtime.bus.join()


asyncio.run(main())
```

## Contratos de ejecucion

- Cola limitada a 1024 eventos y 2 consumidores por defecto. Ambos son configurables.
- Menor prioridad numerica sale antes; la misma prioridad conserva el orden de
  extraccion. Con varios consumidores, la finalizacion puede cambiar de orden.
  Un flujo estricto puede usar `EventBus(worker_count=1)`.
- Cada evento llega a todos los suscriptores de su tema. Se ejecutan en orden
  de registro y disponen de 5 segundos cada uno por defecto. Un fallo se registra
  y permite continuar con los demas; no se reintenta automaticamente.
- `publish()` confirma la entrada a la cola, no el exito del procesamiento.
  La cola es volatil y puede perder eventos ante caidas. La durabilidad de
  eventos y un outbox transaccional corresponden a una fase posterior.
- Los productores externos esperan espacio de forma asincrona. Un manejador
  que publica con la cola llena recibe `asyncio.QueueFull` para evitar un bloqueo
  entre consumidores. Los productores pendientes se despiertan al cerrar.
- Los manejadores solicitan el cierre con `runtime.request_shutdown()` mientras
  `runtime.run()` actua como supervisor. No deben llamar `stop()` o `join()`.
- El cierre rechaza publicaciones nuevas, drena la cola y cancela trabajo que
  exceda el plazo. Los manejadores deben respetar `CancelledError`; no ejecutar
  I/O sincronico ni calculos largos dentro del event loop. Usar APIs async,
  `asyncio.to_thread()` para I/O heredado y procesos para computacion pesada.
- La lectura de `.env` y las importaciones diferidas de MySQL usan un hilo de
  apoyo. Los logs de `main.py` se escriben desde un `QueueListener`. Ser local
  y nativo significa ejecutar e integrar con Windows; Python sigue siendo el
  entorno de ejecucion del backend.
- Una instancia de runtime por proceso administra el pool MySQL global. Las
  escrituras usan `await conn.commit()` explicitamente dentro de `db_session()`;
  las transacciones sin confirmar se revierten al salir.

## Verificar

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Instalar requirements.txt antes de ejecutar la suite completa. Las pruebas
unitarias usan dobles de persistencia y no necesitan un servidor MySQL.
La prueba real del servidor sigue en `core/db_core.py`.
