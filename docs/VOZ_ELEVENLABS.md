# Voz de T.H.O.R.N / Bastion 8

## Que esta preparado

T.H.O.R.N conserva su identidad. Su personalidad toma rasgos generales de un
asistente tactico: sereno, preciso, elegante y con humor seco ocasional. No
afirma ser J.A.R.V.I.S ni imita la identidad vocal de un actor. El prompt completo
esta en `core/personality.py`, junto con el perfil vocal original y texto de prueba.
El modelo Ollama debe estar instalado para producir respuestas con ese estilo;
ElevenLabs solo lee texto, no cambia la personalidad ni ejecuta comandos.

No se descargan ni suben audios de peliculas. La integracion no crea una voz
automaticamente: necesita una API key privada y el ID de una voz autorizada
o de una muestra original elegida en Voice Design. No hay un ID de voz inventado.

## Configuracion privada

### Crear la API key paso a paso

1. Inicia sesion y abre [API Keys](https://elevenlabs.io/app/settings/api-keys).
   Tambien puedes entrar por Developers > API Keys.
2. Pulsa Create key y usa el nombre THORN-Bastion8.
3. Manten activado Restrict Key; no concedas todos los permisos.
4. Habilita Text to Speech, Voice Generation y Voices con lectura/escritura.
   Sus permisos internos son text_to_speech, voice_generation, voices_read y
   voices_write. No hacen falta permisos de clonacion, agentes ni transcripcion.
5. Establece un limite de creditos pequeno para las pruebas, dentro de tu saldo.
6. Crea la clave e introduce su valor completo en el .env privado. No uses el
   key_id, la contrasena ni los ultimos cuatro caracteres como credencial.

El archivo privado es `.env`, dentro de tu carpeta de T.H.O.R.N.
Busca `ELEVENLABS_API_KEY=` e
introduce la clave despues de su signo igual. No la pongas en
`.env.example`, en THORN_API_TOKEN, en JavaScript ni en una captura de pantalla.
Deja THORN_VOICE_ENABLED=0 y ELEVENLABS_VOICE_ID vacio hasta elegir y guardar
la voz original; disenar muestras no requiere activar la reproduccion del HUD.

Guia oficial: [API keys](https://help.elevenlabs.io/hc/en-us/articles/14599447207697-How-do-I-authorize-myself-using-an-API-key)
y [permisos del esquema oficial](https://api.elevenlabs.io/openapi.json).

Despues del diseno
revoca permisos de escritura que ya no necesites. No uses tu contrasena de cuenta
como API key. Introduce la clave solo en `.env`, nunca en el chat ni en GitHub.

```dotenv
THORN_VOICE_ENABLED=1
ELEVENLABS_API_KEY=
ELEVENLABS_VOICE_ID=
ELEVENLABS_MODEL_ID=eleven_flash_v2_5
```

Los campos vacios deben contener tu clave y el ID de una voz de tu cuenta antes
de activar la sintesis. Reinicia T.H.O.R.N despues de cambiar el `.env`.
La plantilla publica `.env.example` deja la voz apagada. Una voz masculina de
la biblioteca con idioma espanol/acento latinoamericano es otra opcion: revisa
su licencia y agregala a tu cuenta. El nombre de una voz no acredita derechos
para clonar la interpretacion de un actor.

## Plan Free: creacion desde la web

Tener Voice Generation habilitado en la clave no elimina las restricciones
del plan. Si el endpoint de creacion devuelve HTTP 403 y el diagnostico
paid_plan_required, no repitas la solicitud ni cambies la suscripcion sin
decidirlo expresamente. La herramienta no compra planes ni reintenta cargos.

La documentacion de ElevenLabs incluye Voice Design y tres cupos personalizados
en Free desde su web. Crear muestras sigue consumiendo creditos; comprueba los
que muestra la pagina antes de generar. No uses Instant/Professional Voice Clone.

1. Abre [ElevenLabs](https://elevenlabs.io/app) con tu sesion iniciada.
2. Entra en Voices > My Voices > Add a new voice > Voice Design.
3. Usa esta descripcion, que define una identidad ORIGINAL:

```text
Native Spanish, neutral Latin American pronunciation with a neutral Mexican
base. Use seseo for z and c before e/i, without a Castilian theta sound or
European Spanish intonation. Male, around 40. Studio quality.
Persona: composed, analytical tactical assistant. Emotion: calm, attentive,
quietly confident. Smooth, rich baritone with clear consonants, natural breathing
and measured conversational pacing. Elegant delivery with understated warmth
and subtle dry wit. Short, precise phrasing, never theatrical or metallic.
Create a distinct original voice, not an imitation of any actor or character.
```

4. En Text to preview, usa el siguiente texto de prueba, no datos del equipo:

```text
Soy Thorn. Esta es una prueba de mi voz, no un informe del equipo. Antes de
recomendar una accion, comprobare los datos disponibles. Si una lectura no esta
vigente, lo dire con claridad. La precision suele ser mas util que el entusiasmo.
Estoy listo para su siguiente consulta.
```

5. Genera las muestras, escucha las opciones y guarda la elegida como
   T.H.O.R.N - Bastion 8 / Latino en My Voices.
6. Indica que ya esta guardada. La herramienta puede listar las voces de tu
   cuenta para obtener su ID; no hace falta compartir tu API key otra vez.

Guardar desde la web no confirma por si solo acceso de sintesis por API.
Comprobaremos disponibilidad y modelo con una prueba explicita antes de activar
la reproduccion del HUD; si el proveedor la rechaza, se informara el limite real.

Fuentes: [Voice Design en la web](https://elevenlabs.io/docs/eleven-creative/voices/voice-design)
y [cupos de Free](https://elevenlabs.io/docs/overview/administration/billing).

## Si la voz tiene acento de Espana

El idioma del texto y `language_code=es` no seleccionan el acento regional.
El prompt de personalidad tampoco reentrena la voz: el acento depende de la
identidad vocal. No cambies la escritura del texto para disimular pronunciaciones.

Si la voz original se creo con Voice Design y aparece la opcion Remix en su
ficha, puedes intentar cambiar su acento conservando el timbre. La elegibilidad,
los creditos y la disponibilidad de esa opcion dependen de la voz y de tu cuenta;
no se asume que este habilitada en Free ni se cambia la suscripcion.

Usa esta descripcion del cambio en Voice Remixing:

```text
Change the regional pronunciation to native neutral Latin American Spanish,
with a neutral Mexican base. Use seseo for z and c before e/i, without a
Castilian theta sound or European Spanish intonation. Preserve the original
male baritone timbre, calm analytical delivery, natural pace and restrained
warmth. Maintain an original identity; do not imitate an actor or character.
```

Para la muestra, usa texto espanol con palabras que permitan escuchar la
pronunciacion. Es una prueba, no un informe real del equipo:

```text
Soy Thorn. Esta es una prueba de voz. Voy a revisar el centro de seguridad,
la conexion y los procesos. Si necesito tu autorizacion, te la pedire antes
de actuar. Gracias; podemos comenzar cuando estes listo.
```

Empieza con intensidad media si la interfaz permite elegirla. Una intensidad
mayor puede seguir mejor el cambio, pero tambien alterar el timbre. Escucha
antes de guardar: no hay garantia de acento exacto. Remix crea una variante
nueva; no modifica ni elimina la original y guardar puede ocupar otro cupo.

Si Remix no aparece o esta restringido, crea otra identidad original con
Voice Design usando el perfil anterior. No ejecutes repetidamente el endpoint
de creacion por API ya rechazado por el plan. No activaremos una voz distinta
sin identificarla primero por nombre o ID y comprobar que es la elegida.

Fuentes: [idioma y acento](https://elevenlabs.io/docs/es/help-center/product/core-capabilities/text-to-speech/how-do-i-select-the-language-and-accent)
y [Voice Remixing](https://elevenlabs.io/docs/overview/capabilities/voice-remixing).

## Crear una voz original sin muestras ajenas

La herramienta usa los endpoints oficiales Voice Design. No acepta archivos
de entrada ni tiene una funcion para extraer o subir voces de peliculas.
La cuenta, sus permisos, creditos y cupos determinan la disponibilidad. Estos
comandos de creacion requieren que el proveedor permita el endpoint en tu plan;
el acceso para listar voces no garantiza el acceso para disenar voces por API.

```powershell
# Lista voces de TU cuenta, no sintetiza audio.
.\.venv\Scripts\python.exe -B tools/elevenlabs_voice.py voices --search latino

# Genera muestras originales; envia el perfil y texto, y consume creditos.
.\.venv\Scripts\python.exe -B tools/elevenlabs_voice.py design --accept-cloud-cost
```

El comando design genera una carpeta unica en `data/voice-design/` con MP3 y
`previews.json`. Este directorio esta excluido de Git. Escucha las muestras y
elige el `generated_voice_id` de la preferida; no es todavia un `voice_id` final.
La calidad y el acento deben comprobarse escuchando, no por el nombre del perfil.

```powershell
# Sustituye ID_DE_LA_MUESTRA por el generado en previews.json.
.\.venv\Scripts\python.exe -B tools/elevenlabs_voice.py save --preview ID_DE_LA_MUESTRA --accept-account-change
```

Guardar anade una voz a My Voices y ocupa un cupo de la cuenta. El comando muestra
el `ELEVENLABS_VOICE_ID` final, sin modificar automaticamente tu `.env`. No se
reintenta ninguna operacion: si falla la respuesta, revisa tu cuenta antes de
repetir, porque el proveedor puede haber completado la operacion o cobrado.

## Uso en el HUD

Abre Asistente, habilita el consentimiento de envio de texto para esta sesion
y pulsa Probar voz. El icono de altavoz de una respuesta lee ese texto; el
cuadrado detiene audio y la solicitud pendiente.

Para leer nuevas respuestas automaticamente, marca tambien
"Leer nuevas respuestas automaticamente". Esta opcion empieza desactivada y
no se guarda al recargar o desconectar. Solo envia nuevas respuestas correctas
de Ollama a partir de la activacion; no envia errores, diagnosticos, historial,
respuestas anteriores ni el audio del microfono. Cada lectura puede consumir
creditos, y una respuesta puede contener datos del PC o notas personales.
No la actives para consultas que no quieras enviar a ElevenLabs.

Si el navegador bloquea `play()`, el audio queda en memoria y aparece un boton
triangular para reproducirlo con una pulsacion. No se solicita otra sintesis ni
se generan mas creditos por esa reanudacion. Detener, desconectar o retirar
la autorizacion libera el audio. No se cambian las politicas del navegador.

El control Volumen regula esta salida, no el mezclador global de Windows.
"Comprobar altavoces" emite un tono local de 0.4 segundos sin usar ElevenLabs.
Si no lo oyes, revisa si la pestana esta silenciada, el volumen del navegador
en el mezclador de Windows y el dispositivo de salida seleccionado. "Tono local
finalizado" no certifica que el altavoz fisico emitio sonido.
La entrada de microfono es un modulo LOCAL separado: no envia audio a ElevenLabs.
Admite dictado manual o conversacion por turnos con Silero; no tiene palabra de
activacion. Reabre entrada solo despues de terminar la salida autorizada, con
pausa de 400 ms para reducir eco. El nucleo 3D sigue el RMS del MP3 real, no una
pulsacion decorativa. Ver [Microfono](MICROFONO_LOCAL.md).
Desconectar borra consentimiento, lectura automatica, texto de prueba y audio.

Estados como Falta API key, Falta voz o Configurada / sin probar no simulan una
voz activa. Sintesis verificada significa que la ultima peticion entrego audio,
no garantiza el acento, la disponibilidad futura ni que el usuario lo escucho.
Un fallo de credenciales, voz inexistente o audio invalido requiere revisar la
configuracion y reiniciar. Los fallos de red/limite permiten reintento manual.

## Privacidad, latencia y limites

- ElevenLabs es un proveedor en la nube: recibe el texto seleccionado al pulsar
  reproducir o las nuevas respuestas si autorizas expresamente su lectura
  automatica. Una respuesta puede contener datos del PC o notas; revisala antes
  de enviarla usando lectura manual. Ollama y la telemetria siguen siendo locales.
- No se envia audio del microfono ni se exponen API keys al navegador. El backend
  usa HTTPS al dominio fijo `api.elevenlabs.io`, sin proxies ni redirecciones.
- La API privada `POST /api/v1/voice/speech/` exige Bearer y JSON con `text` y
  `cloud_consent: true`; limita texto a 2000 caracteres, audio a 2 MiB, una solicitud
  concurrente y 45 s de peticion remota. No se truncan respuestas largas en silencio.
- La sintesis usa httpx asincrono y Flash v2.5 por defecto; tambien admite
  Multilingual v2. No bloquea el event loop. Esta primera version recibe los chunks
  del proveedor y reune el MP3 en RAM antes de reproducirlo: NO ofrece todavia
  reproduccion progresiva de extremo a extremo ni latencia garantizada.
- El MP3 de reproduccion no se escribe a disco; sus URLs blob se revocan al
  terminar, detener o salir. La auditoria registra ID, cantidad de caracteres,
  modelo y resultado, nunca texto, clave o audio. Errores de auditoria se exponen
  en las capacidades; la voz no exige MySQL.
- No se garantiza retencion cero por parte de ElevenLabs. Su opcion de zero
  retention depende del plan Enterprise. Revisa las politicas y ajustes de tu
  cuenta. Cancelar una solicitud no garantiza cancelar el consumo ya facturado.
- Las pruebas unitarias y del navegador interceptan la sintesis y no consumen
  creditos. El E2E puede reutilizar un MP3 privado mediante
  `THORN_VOICE_FIXTURE_MP3`; comprueba su senal y la reproduccion del navegador,
  no los altavoces fisicos. No sustituye una prueba autorizada del proveedor.

## Fuentes oficiales

- [Permisos y resultados de HTMLMediaElement.play()](https://developer.mozilla.org/en-US/docs/Web/API/HTMLMediaElement/play).
- [Politicas de reproduccion de Chrome](https://developer.chrome.com/blog/autoplay).

- [Derechos y consentimiento para Instant Voice Cloning](https://elevenlabs.io/docs/eleven-creative/voices/voice-cloning/instant-voice-cloning).
- [Voice Design](https://elevenlabs.io/docs/api-reference/text-to-voice/design).
- [Guardar voz original](https://elevenlabs.io/docs/api-reference/text-to-voice/create).
- [Sintesis y condiciones de retencion](https://elevenlabs.io/docs/api-reference/text-to-speech/stream).
- [Listar voces de la cuenta](https://elevenlabs.io/docs/api-reference/voices/search).
