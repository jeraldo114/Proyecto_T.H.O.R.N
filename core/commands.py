"""Ordenes de interfaz deterministas; el modelo no decide sus permisos."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import re
import unicodedata


# NOTA DEL OPERADOR: formas en que el STT escribe «Thorn». La misma lista vive en
# frontend/src/wake-word.js; tests/test_wake_word.py falla si dejan de coincidir.
# «tom» y «ton» solo se aceptan en el HUD al abrir una frase dictada, no aqui:
# en texto escrito pueden ser un nombre real («Tom Hanks»).
WAKE_ALIASES = ("thorn", "thorne", "torn", "thor", "tor", "fourth")
_WAKE_PREFIX = re.compile(r"^(?:" + "|".join(WAKE_ALIASES) + r"|t\.h\.o\.r\.n\.?)\s*[,;:]?\s+", re.I)
_WAKE_PREFIX_NORMALIZED = re.compile(r"^(?:" + "|".join(WAKE_ALIASES) + r"|t h o r n) ")


def normalize(text: str) -> str:
    value = "".join(char for char in unicodedata.normalize("NFD", text.lower())
                    if unicodedata.category(char) != "Mn")
    value = re.sub(r"[¿?¡!.,;:]", " ", value)
    return " ".join(value.split())


def strip_wake(text: str) -> str:
    """Quita el nombre al inicio de un texto ya normalizado."""
    return _WAKE_PREFIX_NORMALIZED.sub("", text)


def command_intent(text: str) -> dict[str, str] | None:
    """Reconoce frases completas, no una palabra perdida dentro de una pregunta."""
    raw = _WAKE_PREFIX.sub("", text.strip())
    resume = re.fullmatch(r"(?:retoma|retomemos|continuemos con)\s+(?:el tema de\s+)?([^\r\n]{1,160})", raw, re.I)
    if resume:
        return {"type": "resume_topic", "topic": resume[1].strip(), "reply": ""}
    value = re.sub(r"^t h o r n\s+", "", normalize(raw))
    value = re.sub(r"\s+(?:por favor|gracias)$", "", value)
    if value == "revisa el equipo":
        return {"type": "system_review", "reply": ""}
    clarification = {
        "retoma": "¿Qué tema quieres retomar?",
        "retomemos": "¿Qué tema quieres retomar?",
        "investiga": "¿Qué tema quieres investigar?",
        "abre": "¿Qué vista o documento quieres abrir?",
    }.get(value)
    if clarification:
        intent = {"type": "clarify", "reply": clarification}
        if value in {"retoma", "retomemos", "investiga"}:
            intent["topic_for"] = "research" if value == "investiga" else "resume"
        return intent
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
    route: Literal["control", "interface", "memory", "os_proposal", "reasoning", "clarify"]
    intent: dict[str, str] | None


def coordinate(prompt: str) -> Decision:
    """Clasifica texto sin ejecutar herramientas ni conceder permisos."""
    intent = command_intent(prompt)
    if intent is None:
        return Decision("reasoning", None)
    if intent["type"] in {"stop_response", "clear_context"}:
        return Decision("control", intent)
    if intent["type"] == "clarify":
        return Decision("clarify", intent)
    if intent["type"] == "media":
        return Decision("os_proposal", intent)
    if intent.get("view") == "history" or intent["type"] == "resume_topic":
        return Decision("memory", intent)
    return Decision("interface", intent)
