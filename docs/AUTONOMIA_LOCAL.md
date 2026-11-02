# T.H.O.R.N sin creditos externos

El modo predeterminado ejecuta texto, transcripcion y sintesis de voz en la PC.
No necesita una cuenta de IA ni saldo por consulta. Necesita los modelos ya
descargados, Python, Ollama y recursos de CPU/RAM. La electricidad y el hardware
siguen siendo recursos necesarios; no se promete latencia instantanea.

## Que es propio y que se reutiliza

T.H.O.R.N aporta su interfaz, personalidad, memoria explicita, eventos,
telemetria, herramientas permitidas y control de acceso. `thorn:bastion9` es un
perfil del modelo preentrenado **Qwen2.5 1.5B**, ejecutado por Ollama. Reutiliza
sus pesos; cambiar nombre, prompt y parametros no constituye entrenar un modelo
fundacional nuevo. Tampoco aprende pesos automaticamente de las conversaciones.

La transcripcion usa Vosk; la voz usa Piper CPU y `es_MX-ald-medium` con
fonemizacion latinoamericana `es-419`. Es otra voz local, no la voz remixeada
de ElevenLabs ni una clonacion de un actor. El resultado vocal puede ser menos
expresivo. El nucleo visual sigue las muestras reales del audio reproducido.

## Preparar otra instalacion

Desde la raiz del proyecto, con Python 3.12 (version de estos locks), Ollama
instalado y Qwen ya descargado:

```powershell
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-voice.txt
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-speech.txt
.\.venv\Scripts\python.exe tools/install_voice_model.py
.\.venv\Scripts\python.exe tools/install_speech_model.py
.\.venv\Scripts\python.exe tools/install_local_brain.py
```

La instalacion de modelos necesita Internet una vez. El instalador de voz fija
una revision y comprueba SHA-256, tamanos y configuracion antes de mover archivos.
El instalador del perfil solo reutiliza Qwen instalado; no descarga pesos.
Si falta el modelo base, instalarlo explicitamente con `ollama pull qwen2.5:1.5b`.
Los modelos quedan excluidos de Git y no se descargan al iniciar el HUD.

Valores de `.env` para el modo local:

```dotenv
THORN_LOCAL_AI_ONLY=1
THORN_LLM_URL=http://127.0.0.1:11434
THORN_LLM_MODEL=thorn:bastion9
THORN_BRAIN_AUTOSTART=1
THORN_VOICE_PROVIDER=piper
THORN_VOICE_ENABLED=1
THORN_MICROPHONE_ENABLED=1
THORN_CONVERSATION_AUTO_START=1
```

Si Ollama esta instalado en su ruta Windows habitual, T.H.O.R.N puede iniciar
`serve` sin ventana, restringido a loopback y con la nube deshabilitada. Solo
cierra el proceso que haya creado. Si ya hay un servidor, no lo reconfigura ni
lo cierra; el cliente rechaza endpoints externos y modelos declarados remotos.
Contexto de 2048 tokens, una consulta y un modelo residente a la vez.

La politica local bloquea la seleccion de ElevenLabs y la busqueda Brave aunque
existan claves privadas. No hay fallback de pago. Las claves anteriores se
conservan inactivas; no se necesita eliminarlas para usar Piper.

Internet de lectura es independiente de la IA: Wikipedia y paginas HTTPS
publicas se consultan solo con el consentimiento correspondiente. Para operar
sin esas consultas poner `THORN_INTERNET_ENABLED=0`. El chat normal y la voz no
necesitan Internet una vez instalados sus modelos.

## Sesiones propias y autoridad protegida

Los tokens de acceso son credenciales, distintos de los tokens de texto que
predice el modelo. Generar credenciales propias no crea creditos para otras APIs.
T.H.O.R.N los emite con `secrets.token_urlsafe(48)`, mantiene solo SHA-256 de
las sesiones en RAM y comprueba permisos por ruta. Ningun prompt puede emitir
una sesion, elevar permisos o saltarse la confirmacion de acciones Windows.

En Windows, el primer arranque migra la autoridad de `.env` a
`data/security/authority.dpapi`, protegida con DPAPI CurrentUser y ligada a la
ruta del proyecto. Solo despues de verificar el almacen deja vacio
`THORN_API_TOKEN` en `.env`. Un conflicto o un almacen corrupto impide arrancar;
no se reemplaza silenciosamente. La migracion adicional de almacenamiento
protege las otras credenciales en `vault.dpapi` y deja sus campos de `.env`
vacios; ver [Cifrado y respaldos](CIFRADO_Y_RESPALDOS.md).
Una autoridad explicita del proceso tiene prioridad para CI.

El escritorio entrega una sesion temporal al HUD mediante su canal interno.
El navegador nunca recibe la autoridad persistente en HTML, URLs ni storage.
Para conectar desde un navegador, con el servidor ya abierto:

```powershell
.\.venv\Scripts\python.exe tools/session_access.py --port 8004 --mostrar-token
```

Pegar la ultima linea en **Conectar**. Es acceso completo temporal; mantenerla
privada. La autoridad original sigue siendo compatible para herramientas locales
administrativas, pero el HUD la intercambia y no la conserva.

Las sesiones duran 15 minutos y el HUD renueva antes de solicitudes privadas,
rotando el secreto. La credencial anterior sirve durante 30 segundos para
solicitudes en vuelo, pero no puede volver a renovarse. Limite absoluto de 24
horas y 32 sesiones simultaneas. Desconectar revoca la familia; cerrar intenta
revocarla y la caducidad cubre cierres abruptos. Reiniciar el servidor invalida
todas las sesiones temporales. No hay bootstrap HTTP anonimo ni cookies.

No copiar el almacen a otra PC/usuario ni mover el proyecto esperando que siga
descifrando: depende de DPAPI y la ruta. Antes de moverlo, conservar un respaldo
protegido y preparar acceso en la nueva instalacion. No publicar `data/` ni `.env`.
Esta proteccion no defiende frente a un proceso malicioso ejecutado como el
mismo usuario Windows. La API se mantiene en loopback, con un solo worker.

## Voz, rendimiento y verificacion

Piper carga e infiere en un worker; ONNX usa dos hilos CPU. El texto esta limitado
a 2000 caracteres y el WAV a 8 MiB en RAM. Solo admite una sintesis simultanea.
Cancelar detiene la reproduccion inmediatamente, descarta resultados tardios y
espera que termine la llamada nativa antes de liberar el modelo. Es cancelacion
cooperativa, no un plazo duro para interrumpir una DLL. No se guardan textos ni
audio en la auditoria, solo metadatos de operacion.

La lectura automatica local se activa al conectar, y se puede desmarcar. La
captura sigue requiriendo permiso del dispositivo; se cierra mientras responde,
al ocultar la ventana, pausar o desconectar. Si el navegador bloquea la salida,
usar **Reproducir audio recibido** sin volver a sintetizar.

El primer mensaje puede tardar considerablemente en cargar pesos, especialmente
con poca RAM libre. El perfil actual prioriza caber en el equipo; no equivale a
la capacidad de un modelo grande. No se aumenta el contexto ni se instalan
dependencias de entrenamiento/CUDA automaticamente.

Pruebas de regresion: `python -m unittest discover -s tests`, `npm test` dentro
de frontend y `npm run test:local` con el servidor abierto. La ultima prueba usa
modelo y voz reales, revisa la salida WAV, el movimiento del nucleo y la
revocacion; no certifica el volumen fisico de los altavoces.

## Procedencia y licencias

- [Ollama: configuracion y ejecucion local](https://docs.ollama.com/faq).
- [Perfiles Modelfile](https://docs.ollama.com/modelfile): configuracion, no entrenamiento.
- [Modelo base Qwen2.5 1.5B](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct):
  autoria y licencia del modelo original, conservadas por el perfil local.
- [Piper](https://github.com/OHF-Voice/piper1-gpl): motor GPL-3.0-or-later.
- [Modelo es_MX-ald-medium y su ficha](https://huggingface.co/rhasspy/piper-voices/tree/b4e358ffcb232591bf73a540e70f7de528e4174a/es/es_MX/ald/medium):
  ficha declara dataset Unlicense y ajuste desde davefx; se conserva MODEL_CARD
  junto a los pesos. Revisar licencias de componentes antes de distribuir un paquete.
- [DPAPI CurrentUser](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata).
- [Generacion criptografica de credenciales](https://docs.python.org/3/library/secrets.html).

Los motores y modelos preentrenados conservan su autoria y licencia. No se
incluyen sus pesos ni secretos en el repositorio.
