"""Ordenes de interfaz deterministas; el modelo no decide sus permisos."""

from __future__ import annotations

import re
import unicodedata


def normalize(text: str) -> str:
    value = "".join(char for char in unicodedata.normalize("NFD", text.lower())
                    if unicodedata.category(char) != "Mn")
    value = re.sub(r"[¿?¡!.,;:]", " ", value)
    return " ".join(value.split())


def command_intent(text: str) -> dict[str, str] | None:
    """Reconoce frases completas, no una palabra perdida dentro de una pregunta."""
    value = normalize(text)
    value = re.sub(r"^(?:thorn|t h o r n)\s+", "", value)
    value = re.sub(r"\s+(?:por favor|gracias)$", "", value)
    if value in {"silencio", "detente", "para de hablar", "deja de hablar", "deten la respuesta", "parar respuesta"}:
        return {"type": "stop_response", "reply": "Respuesta detenida."}
    if value in {"borra el contexto", "olvida esta conversacion", "limpia la conversacion", "reinicia la conversacion"}:
        return {"type": "clear_context", "reply": "Contexto de esta conversacion borrado."}
    if value in {"buenos dias", "buenas tardes", "buenas noches", "despierta", "ya llegue", "dame un resumen", "resumen de inicio"}:
        return {"type": "briefing", "reply": ""}
    if value in {"deten la musica", "para la musica", "deten multimedia"}:
        return {"type": "media", "action_id": "media_stop", "reply": "Confirma para enviar la tecla detener multimedia a Windows."}
    if value in {"pausa la musica", "pausar musica", "pausa multimedia"}:
        return {"type": "media", "action_id": "media_play_pause", "reply": "Confirma el control multimedia para enviar la tecla reproducir/pausar."}
    destinations = {"sistema": "system", "diagnostico": "system", "historial": "history",
                    "actividad": "activity", "asistente": "assistant", "red": "network",
                    "centro de mando": "central"}
    match = re.fullmatch(r"(?:abre|abrir|muestra|muestrame|mostrar|ver|ve a)(?: el| la)? (.+)", value)
    if match and match[1] in destinations:
        return {"type": "navigate", "view": destinations[match[1]], "reply": "Mostrando " + match[1] + "."}
    if value in {"modo presencia", "activa el modo presencia", "modo enfoque"}:
        return {"type": "presence", "reply": "Modo de enfoque activado."}
    if value in {"modo trabajo", "sal del modo presencia", "desactiva el modo enfoque"}:
        return {"type": "work", "reply": "Modo de trabajo activado."}
    return None
