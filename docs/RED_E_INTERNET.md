# Red e Internet - Bastion 8

## Que esta disponible

La vista Red separa dos cosas que no significan lo mismo:

- Red local: interfaces, direcciones IP, ruta IPv4 y contadores de subida/bajada
  de ESTA PC. No busca vecinos, no captura paquetes y no escanea puertos.
- Internet: busqueda o lectura HTTPS bajo demanda y con consentimiento. Ninguna
  pagina decide que herramientas usar ni que acciones ejecutar en Windows.

El modelo sigue en Ollama local. Las fuentes externas reciben solamente la
consulta o URL elegida y la IP publica de la conexion, no el contexto del modelo,
notas, telemetria, audio, API token de T.H.O.R.N ni cookies del navegador.
Cada proveedor tiene sus propias politicas de registro y privacidad.

## Usarlo

1. Abre el HUD, conecta la sesion y entra en Red.
2. Los contadores locales aparecen sin autorizar trafico externo. La primera
   muestra no muestra velocidad: hacen falta dos observaciones reales.
3. Marca la autorizacion de Internet de esa sesion para buscar, leer una URL
   HTTPS publica o pulsar Probar conexion HTTPS.
4. Las respuestas muestran las fuentes con enlaces y fecha de CONSULTA, que
   no debe confundirse con la fecha de publicacion. Los enlaces son externos.

Los resultados de busqueda son extractos recibidos del proveedor: no se visitan
automaticamente todos los enlaces. Resultado y Lectura identifican su procedencia;
la fecha de un resultado corresponde a la respuesta del buscador, no certifica
que el sitio original se haya actualizado o este disponible en ese instante.

Tambien se aceptan ordenes explicitas en la consola o en conversacion:

```text
busca en internet sobre cifrado
lee https://example.com/
resume https://www.python.org/about/
```

Esas ordenes requieren la misma autorizacion de Internet en Red. Una pregunta
normal no se manda automaticamente a un buscador. La autorizacion de ElevenLabs
sigue siendo independiente: leer una fuente no autoriza sintetizarla en la nube.

Cancelar, retirar el consentimiento, pausar, desconectar u ocultar la ventana
cancela solicitudes pendientes y descarta respuestas tardias. Volver a autorizar
no repite solicitudes anteriores. No se guarda consentimiento en browser storage.
La cancelacion no puede retirar una consulta que un proveedor ya recibio.

## Fuentes y configuracion

En el .env privado:

```dotenv
THORN_INTERNET_ENABLED=1
THORN_SEARCH_PROVIDER=wikipedia
BRAVE_SEARCH_API_KEY=
```

La plantilla publica deja Internet apagado. Hay que reiniciar el servidor al
cambiar esas opciones. No compartir el .env: contiene otras credenciales privadas.

Wikipedia funciona sin otra cuenta ni API key, pero busca SOLO en la enciclopedia
en espanol. No es una busqueda general de la web ni un servicio de ultimas noticias.
La lectura directa de URLs HTTPS publicas funciona independientemente del buscador.

Para busqueda general opcional, crea tu propia clave de Brave Search API y usa:

```dotenv
THORN_SEARCH_PROVIDER=brave
BRAVE_SEARCH_API_KEY=
```

Coloca el valor real de la clave unicamente en la segunda linea del .env privado.
El modulo no crea cuentas, no contrata planes ni habilita cargos automaticamente.
Revisa las condiciones y cuotas de Brave antes de configurarlo. Si falta la clave,
la UI informa FALTA API KEY y permite seguir leyendo URLs publicas. No se cambia
de proveedor de forma silenciosa ni se usa scraping de buscadores como alternativa.

## Como esta dividido

| Archivo | Responsabilidad |
| --- | --- |
| core/network_sensor.py | Lee psutil y rutas nativas en un worker cada 2 segundos. |
| core/public_web.py | GET HTTPS, TLS, DNS validado y conexion fijada a IP publica. |
| core/internet.py | Consentimiento, limites, buscadores, extraccion de texto y auditoria. |
| core/services.py | Resume fuentes sin incluir memoria ni datos privados del operador. |
| core/local_llm.py | Para resumen web solo acepta suggested_action=null. |
| frontend/src/network.js | Presenta tasas, enlaces seguros y ordenes explicitas de red. |
| api/assistant.py | Rutas privadas con contratos de campos cerrados. |

El parser HTML es el de la biblioteca estandar y se ejecuta fuera del loop.
No se usa un navegador remoto: no ejecuta JavaScript ni completa formularios.
Ollama puede producir un resumen; sin el modelo se entregan extractos identificados
como tales. El resumen de fuentes no recibe notas, telemetria ni herramientas OS.
Esto reduce el alcance de una inyeccion de instrucciones, no garantiza que un
modelo nunca interprete mal o invente informacion. Hay que revisar las fuentes.

## Limites y medidas

- Solo GET HTTPS en puerto 443, sin credenciales incrustadas, proxies de entorno,
  cookies, cabeceras de sesion o acceso a sockets Unix.
- Se valida el resultado DNS completo; cualquier IP privada, loopback, link-local,
  multicast o traduccion IPv6 ambigua rechaza la conexion. La IP aprobada se pasa
  al socket; Host y TLS siguen verificando el dominio original.
- Hasta tres redirecciones, validadas de nuevo. Las llamadas con clave Brave no
  siguen redirecciones ni llevan esa clave a otros hosts.
- Hasta 1 MiB de respuesta, contenido HTML/texto o JSON del buscador, sin compresion;
  limite total HTTP de 15 s. Paginas grandes, protegidas, con login, PDF o que
  dependen de JavaScript pueden no ser legibles. No se intenta eludir restricciones.
- Una operacion externa a la vez, minimo 2 s entre inicios, maximo 60 operaciones/h
  por proceso; el presupuesto se reinicia al reiniciar T.H.O.R.N. No hay reintentos
  de pago ni consultas de conectividad externas automaticas en el polling.
- Busquedas de hasta 240 caracteres y tres fuentes. La lectura conserva hasta
  6000 caracteres de texto en RAM; el resumen usa un fragmento aun menor. No es
  una lectura exhaustiva garantizada de documentos largos.

Las tasas son bytes/s observados entre muestras, no la velocidad contratada ni
un test de ancho de banda. La suma excluye loopback; adaptadores virtuales/VPN
pueden contar de nuevo trafico que tambien pasa por una interfaz fisica. Reinicios
de contadores, interfaces nuevas y muestras obsoletas muestran tasa desconocida.
La ruta mostrada es IPv4 y puede variar con VPN o configuraciones de rutas complejas.

La prueba externa usa un destino fijo, https://example.com/. DNS y tiempo HTTPS
son mediciones reales de esa prueba: HTTPS incluye resolucion/TLS si necesita una
conexion nueva y lectura de la respuesta. No es ping ICMP, no mide perdida de
paquetes y no certifica que todo Internet sea accesible o que la PC sea segura.

El bus y la auditoria conservan solo tipo de operacion, identificador, proveedor
y resultado, no consultas, URLs ni texto de paginas. Las fuentes y respuestas
viven en RAM; el sistema operativo puede usar swap, y los proveedores registran
segun sus politicas. Abrir un enlace externo usa las reglas de tu navegador.
El HUD y la API siguen enlazados solo a 127.0.0.1; no se abren puertos externos.

## Verificacion

`tests/test_network.py` comprueba SSRF, DNS mixto/rebinding, Host/TLS, redirecciones,
clave Brave, tamano, consentimiento, auditoria, contadores y limites. Los contratos
HTTP estan en `tests/test_assistant_api.py`. Las pruebas unitarias no usan Internet.

`npm run test:network` en frontend necesita el servidor local ejecutandose. Usa
contadores Windows reales y fuentes/diagnostico externo simulados: no consulta
proveedores ni consume creditos. Comprueba consentimiento, texto remoto sin HTML
ejecutable, cancelacion, consola y pantallas de 320 a 1920 px. Las capturas de prueba
quedan excluidas de Git. Las comprobaciones externas reales deben autorizarse y
no equivalen a una validacion de todos los sitios ni de una cuenta Brave.

## Referencias

- [Transporte y backends HTTP asincronos](https://www.encode.io/httpcore/network-backends/).
- [MediaWiki TextExtracts](https://www.mediawiki.org/wiki/Extension:TextExtracts).
- [Brave Search API](https://api-dashboard.search.brave.com/app/documentation/web-search/get-started).
- [IP Helper GetIpForwardTable](https://learn.microsoft.com/en-us/windows/win32/api/iphlpapi/nf-iphlpapi-getipforwardtable).
- [OWASP SSRF](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html).
- [OWASP inyeccion de instrucciones](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html).
