"""Ordenes de interfaz deterministas; el modelo no decide sus permisos."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import re
import unicodedata


def normalize(text: str) -> str:
    value = "".join(char for char in unicodedata.normalize("NFD", text.lower())
                    if unicodedata.category(char) != "Mn")
    value = re.sub(r"[¿?¡!.,;:]", " ", value)
    return " ".join(value.split())


def command_intent(text: str) -> dict[str, str] | None:
    """Reconoce frases completas, no una palabra perdida dentro de una pregunta."""
    raw = re.sub(r"^(?:thorn|t\.h\.o\.r\.n\.?)\s*[,;:]?\s+", "", text.strip(), flags=re.I)
    resume = re.fullmatch(r"(?:retoma|retomemos|continuemos con)\s+(?:el tema de\s+)?([^\r\n]{1,160})", raw, re.I)
    if resume:
        return {"type": "resume_topic", "topic": resume[1].strip(), "reply": ""}
    value = normalize(text)
    if value in {"revisa el equipo", "thorn revisa el equipo"}:
        return {"type": "system_review", "reply": ""}
    value = re.sub(r"^(?:thorn|t h o r n)\s+", "", value)
    value = re.sub(r"\s+(?:por favor|gracias)$", "", value)
    if value in {"silencio", "detente", "para de hablar", "deja de hablar", "deten la respuesta", "parar respuesta", "espera"}:
        return {"type": "stop_response", "reply": "Respuesta detenida."}
    if value in {"borra el contexto", "olvida esta conversacion", "limpia la conversacion", "reinicia la conversacion"}:
        return {"type": "clear_context", "reply": "Contexto de esta conversacion borrado."}
    if value in {"buenos dias", "buenas tardes", "buenas noches", "despierta", "ya llegue", "dame un resumen", "resumen de inicio", "resume el equipo"}:
        return {"type": "briefing", "reply": ""}
    if value in {"deten la musica", "para la musica", "deten multimedia"}:
        return {"type": "media", "action_id": "media_stop", "reply": "Confirma para enviar la tecla detener multimedia a Windows."}
    if value in {"pausa la musica", "pausar musica", "pausa multimedia", "puedes pausar la musica"}:
        return {"type": "media", "action_id": "media_play_pause", "reply": "Confirma el control multimedia para enviar la tecla reproducir/pausar."}
    destinations = {"sistema": "system", "diagnostico": "system", "historial": "history", "mi memoria": "history",
                    "estado del equipo": "system",
                    "actividad": "activity", "asistente": "assistant", "red": "network",
                    "centro de mando": "central"}
    match = re.fullmatch(r"(?:abre|abrir|muestra|muestrame|mostrar|ver|ve a)(?: el| la)? (.+)", value)
    if match and match[1] in destinations:
        return {"type": "navigate", "view": destinations[match[1]], "reply": "Mostrando " + match[1] + "."}
    if value in {"modo presencia", "activa el modo presencia", "modo enfoque"}:
        return {"type": "presence", "reply": "Modo de enfoque activado."}
    if value in {"modo trabajo", "pon modo de trabajo", "sal del modo presencia", "desactiva el modo enfoque"}:
        return {"type": "work", "reply": "Modo de trabajo activado."}
    return None


@dataclass(frozen=True, slots=True)
class Decision:
    route: Literal["control", "interface", "memory", "os_proposal", "reasoning"]
    intent: dict[str, str] | None


def coordinate(prompt: str) -> Decision:
    """Clasifica texto sin ejecutar herramientas ni conceder permisos."""
    intent = command_intent(prompt)
    if intent is None:
        return Decision("reasoning", None)
    if intent["type"] in {"stop_response", "clear_context"}:
        return Decision("control", intent)
    if intent["type"] == "media":
        return Decision("os_proposal", intent)
    if intent.get("view") == "history" or intent["type"] == "resume_topic":
        return Decision("memory", intent)
    return Decision("interface", intent)
