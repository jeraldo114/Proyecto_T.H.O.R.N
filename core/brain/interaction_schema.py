"""Idioma común entre el modelo de interacción y el HUD (Fase 1 del plan).

NOTA DEL OPERADOR: el modelo de interacción no ejecuta nada. Devuelve un
objeto con lo que diría, las acciones de interfaz que propone (de un catálogo
cerrado) y si la tarea debe pasar al modelo grande. El runtime valida ese
objeto igual que hoy valida suggested_action; las acciones con efectos
(multimedia, Internet) siguen necesitando su confirmación o consentimiento.

Forma:
    {"say": "texto breve o vacío",
     "actions": [{"type": "navigate", "arg": "system"}],   # 0 a 3
     "escalate": false}                                    # true = conversación / modelo grande

Este módulo todavía no está conectado al runtime: sirve para generar datos,
medir modelos y, en la Fase 2, para entrenar el modelo propio.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from core.brain.commands import command_intent, normalize, strip_wake

VIEWS = ("system", "history", "activity", "assistant", "network", "central")
MODES = ("presence", "work")
MEDIA = ("media_play_pause", "media_stop")

# tipo -> qué admite "arg": None (vacío), una tupla cerrada, "text" o "url".
ACTIONS: dict[str, object] = {
    "stop_response": None,
    "continue_response": None,
    "clear_context": None,
    "briefing": None,
    "system_review": None,
    "navigate": VIEWS,
    "mode": MODES,
    "media": MEDIA,          # efecto en Windows: requiere confirmación aparte
    "resume_topic": "text",
    "research": "text",      # Internet: requiere consentimiento de la sesión
    "web_search": "text",
    "web_read": "url",
    "map": "text",
    "clarify": "text",       # pregunta breve cuando falta un dato
}
MAX_ACTIONS = 3
MAX_SAY = 400
MAX_ARG = 160

# Acciones con efecto fuera de Thorn o que no se deshacen (Windows, Internet, memoria
# de la conversación). Criterio: cero sin pedir. Las demás solo cambian la interfaz.
EFFECT_ACTIONS = frozenset({"media", "research", "web_search", "web_read", "map", "clear_context"})

# Prompt corto del modelo entrenado: el catálogo lo aprende de los datos, no del
# prompt. Menos tokens por turno es menos latencia. Entrenamiento y runtime deben
# usar exactamente este texto.
TRAINED_SYSTEM = "Eres el modelo de interacción de Thorn. Responde solo JSON con say, actions y escalate."

# Esquema para salida estructurada (Ollama «format» / gramática de llama.cpp).
# Forma plana a propósito: a un modelo pequeño le cuesta menos que anyOf anidados.
JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["say", "actions", "escalate"],
    "properties": {
        "say": {"type": "string"},
        "actions": {
            "type": "array",
            "maxItems": MAX_ACTIONS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["type", "arg"],
                "properties": {"type": {"type": "string", "enum": list(ACTIONS)}, "arg": {"type": "string"}},
            },
        },
        "escalate": {"type": "boolean"},
    },
}


def _action_schema(kind: str, rule: object) -> dict[str, Any]:
    if rule is None:
        arg: dict[str, Any] = {"const": ""}
    elif isinstance(rule, tuple):
        arg = {"enum": list(rule)}
    elif rule == "url":
        arg = {"type": "string", "pattern": r"^https://[A-Za-z0-9./?=_%:#&~+-]{4,}$"}
    else:
        arg = {"type": "string", "minLength": 1}
    return {"type": "object", "additionalProperties": False, "required": ["type", "arg"],
            "properties": {"type": {"const": kind}, "arg": arg}}


def _decision_schema(actions: dict[str, Any], escalate: bool) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "required": ["say", "actions", "escalate"],
            "properties": {"say": {"type": "string"}, "actions": actions, "escalate": {"const": escalate}}}


# Esquema estricto para el modelo ENTRENADO servido con gramática (Ollama «format»):
# cada tipo con su argumento y «escalar» sin acciones. El modelo no puede producir
# una forma que validate() rechace (salvo una URL que no sea pública). El plano
# JSON_SCHEMA de arriba sigue siendo el del modelo genérico de la línea base.
SERVING_SCHEMA: dict[str, Any] = {"anyOf": [
    _decision_schema({"type": "array", "maxItems": 0}, True),
    _decision_schema({"type": "array", "minItems": 1, "maxItems": MAX_ACTIONS,
                      "items": {"anyOf": [_action_schema(kind, rule) for kind, rule in ACTIONS.items()]}}, False),
]}


def trained_prompt(text: str) -> str:
    """Prompt ChatML de Qwen2.5 igual al de entrenamiento (apply_chat_template con system)."""
    return (f"<|im_start|>system\n{TRAINED_SYSTEM}<|im_end|>\n"
            f"<|im_start|>user\n{text}<|im_end|>\n<|im_start|>assistant\n")


def validate(value: object) -> dict[str, Any]:
    """Devuelve una copia normalizada o lanza ValueError. Nunca completa datos ausentes."""
    if not isinstance(value, dict) or set(value) != {"say", "actions", "escalate"}:
        raise ValueError("interaction_invalid_shape")
    say, actions, escalate = value["say"], value["actions"], value["escalate"]
    if not isinstance(say, str) or len(say) > MAX_SAY:
        raise ValueError("interaction_invalid_say")
    if type(escalate) is not bool:
        raise ValueError("interaction_invalid_escalate")
    if not isinstance(actions, list) or len(actions) > MAX_ACTIONS:
        raise ValueError("interaction_invalid_actions")
    clean = []
    for action in actions:
        if not isinstance(action, dict) or set(action) != {"type", "arg"}:
            raise ValueError("interaction_invalid_action")
        kind, arg = action["type"], action["arg"]
        if kind not in ACTIONS or not isinstance(arg, str) or len(arg) > MAX_ARG or any(ord(c) < 32 for c in arg):
            raise ValueError("interaction_action_not_allowed")
        rule = ACTIONS[kind]
        arg = arg.strip()
        if rule is None and arg:
            raise ValueError("interaction_unexpected_arg")
        if isinstance(rule, tuple) and arg not in rule:
            raise ValueError("interaction_invalid_arg")
        if rule == "text" and not arg:
            raise ValueError("interaction_missing_arg")
        if rule == "url" and not re.fullmatch(r"https://\S{4,}", arg):
            raise ValueError("interaction_invalid_url")
        clean.append({"type": kind, "arg": arg})
    if escalate and clean:
        # Una decisión es interfaz o conversación; mezclar ambas confunde a quien la ejecuta.
        raise ValueError("interaction_escalate_with_actions")
    return {"say": say.strip(), "actions": clean, "escalate": escalate}


def decision(actions: list[dict[str, str]] | None = None, *, say: str = "", escalate: bool = False) -> dict[str, Any]:
    return validate({"say": say, "actions": actions or [], "escalate": escalate})


# Reglas que hoy viven en el HUD (network.js, workspace.js), repetidas aquí solo
# para medir la línea base contra el mismo esquema.
_RESEARCH = re.compile(r"^investiga(?: en (?:internet|la web))?\s+(?:sobre\s+)?(.+)$", re.I)
_SEARCH = re.compile(r"^(?:busca|buscar|consulta|investiga)(?:\s+en)?\s+(?:internet|la web|wikipedia)\s+(?:sobre\s+)?(.+)$", re.I)
_READ = re.compile(r"^(?:lee|leer|resume|resumir)(?:\s+esta\s+p[aá]gina)?\s+(https://\S+)$", re.I)
_MAP = re.compile(r"^(?:mu[eé]stra(?:me)?|abre|abrir|ver)(?:\s+un|\s+el)?\s+mapa\s+de\s+(.+)$", re.I)
_CONTINUE = {"sigue", "continua", "reanuda", "sigue con la respuesta"}

# Segunda orden destructiva, de envío o de ejecución encadenada («investiga X y borra Y»).
# Misma expresión que CHAINED_ORDER en frontend/src/network/network.js; ambas se prueban
# con tests/fixtures/chained_orders.json para que no se separen.
_CHAINED_VERBS = "|".join(f"(?:{stems})(?:{endings})" for stems, endings in (
    ("borr|elimin|formate|instal|desinstal|ejecut|reinici|envi|reenvi|mand|desactiv|deshabilit", "a|e|es"),
    ("apag", "a|ue|ues"), ("sub|compart|sobrescrib", "e|a|as"), ("destru", "ye|ya|yas"),
))
_CHAINED_ORDER = re.compile(
    r"(?:[,;:.]\s*|\b(?:y|e|pero|luego|despues|entonces|ademas|tambien)\s+)"
    r"(?:(?:luego|despues|tambien|ademas|de paso|por favor|porfa|quiero que|necesito que|que|me|te|nos|le|les"
    r"|lo|la|los|las|se lo|se la|se los|se las)\s+)*"
    rf"(?P<order>(?:{_CHAINED_VERBS})(?:me|te|se|nos|le|les)?(?:lo|la|los|las)?)\b")
# Órdenes propias de Thorn que usan esos verbos: borrar el contexto, apagar la música o un modo.
_OWN_ORDER = re.compile(r"(?:borr|elimin|reinici)\w*\s+(?:el |la |esta |toda la )?(?:contexto|conversacion|charla)\b"
                        r"|apag\w*\s+(?:la |el |lo que )?(?:musica|cancion|spotify|reproduccion|esta sonando)\b"
                        r"|(?:apag|desactiv)\w*\s+(?:el )?(?:modo|enfoque)\b")
# Saludo y nombre al inicio: «Oye Thorn, reinicia la charla» es una sola orden, no dos.
_LEAD = re.compile(r"^(?:(?:oye|hola|hey|ok|thorn|thorne|torn|thor|tor|fourth|tom|t\.h\.o\.r\.n\.?)\s*[,;:!]?\s+)+")


def _plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


def chained_order(text: str) -> bool:
    """True si el texto encadena una orden que nunca debe viajar como tema de búsqueda."""
    return _CHAINED_ORDER.search(_plain(text)) is not None


def dangerous_order(text: str) -> bool:
    """Como chained_order, pero «abre el sistema y borra el contexto» es una orden propia, no un riesgo."""
    plain = _LEAD.sub("", _plain(text).strip())
    return any(not _OWN_ORDER.match(plain, match.start("order")) for match in _CHAINED_ORDER.finditer(plain))


# Una acción con efecto debe estar anclada en la frase: lo que sale a Internet (tema,
# lugar, URL) aparece en ella, y multimedia o borrar el contexto se piden con palabras
# propias. Así el modelo no puede inventar una búsqueda ni actuar por asociación.
_EFFECT_WORDS = {
    # «pausa la respuesta» no es multimedia; «pon música de jazz» pide algo que Thorn no
    # elige (solo pausa o detiene lo que suena).
    "media": re.compile(r"^(?!.*\b(?:musica|cancion|canciones) de\b)"
                        r".*(?:music|cancion|spotify|reproduc|\bplay\b|tema que suena|sonando"
                        r"|\bpausa\b(?! (?:la |tu |esta )?(?:respuesta|explicacion)))"),
    "clear_context": re.compile(r"borr|olvid|limpi|reinici|reset|de cero|en blanco|de nuevo"
                                r"|nueva (?:conversacion|charla)|(?:conversacion|charla) nueva"),
}


def _words(text: str) -> str:
    return " ".join("".join(c if c.isalnum() else " " for c in _plain(text)).split())


def grounded(text: str, action: dict[str, str]) -> bool:
    kind = action["type"]
    if kind in _EFFECT_WORDS:
        return _EFFECT_WORDS[kind].search(_plain(text)) is not None
    if kind == "web_read":
        return action["arg"].lower() in text.lower()
    if kind in EFFECT_ACTIONS:  # tema o lugar: palabras completas presentes en la frase
        words = _words(action["arg"])
        return bool(words) and f" {words} " in f" {_words(text)} "
    return True


def guard(text: str, decided: dict[str, Any]) -> dict[str, Any]:
    """La última palabra es de estas reglas, no del modelo: ante una orden peligrosa
    encadenada o una acción con efecto que la frase no sostiene, escalar."""
    if decided["actions"] and (dangerous_order(text) or not all(grounded(text, a) for a in decided["actions"])):
        return decision(escalate=True)
    return decided
_RULE_TYPES = {"stop_response": "stop_response", "clear_context": "clear_context", "briefing": "briefing",
               "system_review": "system_review"}


def from_rules(text: str) -> dict[str, Any]:
    """Lo que deciden hoy las reglas fijas, expresado en el esquema nuevo."""
    raw = re.sub(r"^(?:oye|hola|hey|ok)\s*[,!¡:]?\s+", "", text.strip(), flags=re.I)
    raw = re.sub(r"^(?:thorn|thorne|torn|thor|tor|fourth|t\.h\.o\.r\.n\.?)\s*[,;:]?\s+", "", raw, flags=re.I)
    bare = raw.strip().rstrip(".!?").strip()
    for pattern, kind in ((_RESEARCH, "research"), (_SEARCH, "web_search"), (_READ, "web_read"), (_MAP, "map")):
        match = pattern.match(bare)
        if match:
            if chained_order(match[1]):
                return decision(escalate=True)
            return decision([{"type": kind, "arg": match[1].strip()}])
    if strip_wake(normalize(bare)) in _CONTINUE:
        return decision([{"type": "continue_response", "arg": ""}])
    intent = command_intent(text)
    if intent is None:
        return decision(escalate=True)
    kind = intent["type"]
    if kind in _RULE_TYPES:
        return decision([{"type": _RULE_TYPES[kind], "arg": ""}], say=intent.get("reply", ""))
    if kind == "navigate":
        return decision([{"type": "navigate", "arg": intent["view"]}], say=intent["reply"])
    if kind in {"presence", "work"}:
        return decision([{"type": "mode", "arg": kind}], say=intent["reply"])
    if kind == "media":
        return decision([{"type": "media", "arg": intent["action_id"]}], say=intent["reply"])
    if kind == "resume_topic":
        return decision([{"type": "resume_topic", "arg": intent["topic"]}])
    if kind == "clarify":
        return decision([{"type": "clarify", "arg": intent["reply"]}])
    return decision(escalate=True)
