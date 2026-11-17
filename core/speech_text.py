"""Texto para hablar: lo que se lee en pantalla no siempre se dice igual.

NOTA DEL OPERADOR: medido con Piper es_MX (sintetizar y transcribir con Vosk):
sin esta capa decia «asterisco asterisco», deletreaba T.H.O.R.N como «te hache
o erre ene», leia las citas [1] y las URL completas, y destrozaba «ms», «GiB» y
«MiB/s». Esta funcion no cambia lo que muestra el HUD, solo lo que se pronuncia.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

MAX_SPOKEN = 3000

ACRONYMS = {
    "CPU": "ce pe u", "GPU": "ge pe u", "RAM": "ram", "PDF": "pe de efe", "DNS": "de ene ese",
    "HTTPS": "hache te te pe ese", "HTTP": "hache te te pe", "URL": "u erre ele", "HUD": "jud",
    "OCR": "o ce erre", "API": "a pe i", "IP": "i pe", "IPv4": "i pe ve cuatro", "IPv6": "i pe ve seis",
    "USB": "u ese be", "SSD": "ese ese de", "VPN": "uve pe ene", "LLM": "ele ele eme", "STT": "ese te te",
    "TTS": "te te ese", "SQL": "ese cu ele", "MySQL": "mai ese cu ele", "CVE": "ce uve e", "SSH": "ese ese hache",
    "TCP": "te ce pe", "UDP": "u de pe", "ICMP": "i ce eme pe", "VRAM": "uve ram", "IA": "i a",
}
UNITS = [
    (r"(?:TiB|TB)/s", "terabytes por segundo"), (r"(?:GiB|GB)/s", "gigabytes por segundo"),
    (r"(?:MiB|MB)/s", "megabytes por segundo"), (r"(?:KiB|KB|kB)/s", "kilobytes por segundo"),
    (r"TiB|TB", "terabytes"), (r"GiB|GB", "gigabytes"), (r"MiB|MB", "megabytes"), (r"KiB|KB|kB", "kilobytes"),
    (r"Gbps", "gigabits por segundo"), (r"Mbps", "megabits por segundo"), (r"kbps|Kbps", "kilobits por segundo"),
    (r"GHz", "gigahercios"), (r"MHz", "megahercios"), (r"ms", "milisegundos"), (r"°C", "grados"),
    (r"s", "segundos"), (r"h", "horas"), (r"min", "minutos"),
]
_UNIT = re.compile(r"(\d)\s*(" + "|".join(pattern for pattern, _ in UNITS) + r")(?![A-Za-z0-9])")
_UNIT_WORDS = [(re.compile(rf"^(?:{pattern})$"), words) for pattern, words in UNITS]
_ACRONYM = re.compile(r"\b(" + "|".join(sorted(map(re.escape, ACRONYMS), key=len, reverse=True)) + r")\b")


def _unit(match: re.Match[str]) -> str:
    for pattern, words in _UNIT_WORDS:
        if pattern.match(match[2]):
            return f"{match[1]} {words}"
    return match[0]


def _link(match: re.Match[str]) -> str:
    host = (urlsplit(match[0]).hostname or "").removeprefix("www.")
    labels = host.split(".")
    return "un enlace de " + ".".join(labels[-2:]) if len(labels) >= 2 else "un enlace"


def for_speech(text: str) -> str:
    value = text.replace("\r", "")
    value = re.sub(r"https?://[^\s)\]>]+", _link, value)
    # Citas: [p. 3] ayuda al oido; [1] ya se ve en pantalla junto a la fuente.
    value = re.sub(r"\[p\.?\s*(\d+)(?:\s*[-–]\s*(\d+))?\]",
                   lambda m: f"(página {m[1]}{' a ' + m[2] if m[2] else ''})", value)
    value = re.sub(r"\[\d+(?:\s*[,–-]\s*\d+)*\]", "", value)
    # Marcado de texto: negritas, codigo, encabezados y viñetas no se pronuncian.
    value = re.sub(r"[*`]{1,3}", "", value).replace("_", " ")
    value = re.sub(r"^\s*(?:#{1,6}|[-•>])\s+", "", value, flags=re.M)
    value = re.sub(r"\bT\.H\.O\.R\.N\.?|\bTHORN\b", "Thorn", value)
    # Numeros: IP con «punto», miles sin separador y decimales con «coma».
    value = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", lambda m: m[0].replace(".", " punto "), value)
    value = re.sub(r"(?<![\d.,])([1-9]\d{0,2})\.(\d{3})(?![\d.,])", r"\1\2", value)
    value = re.sub(r"(?<![\d.])(\d+)\.(\d+)(?![\d.])", r"\1 coma \2", value)
    value = _UNIT.sub(_unit, value)
    value = _ACRONYM.sub(lambda m: ACRONYMS[m[1]], value)
    value = re.sub(r"\s+/\s+", ", ", value).replace(" & ", " y ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" ([.,;:])", r"\1", value)
    value = re.sub(r"\s*\n\s*", "\n", value).strip()
    return value[:MAX_SPOKEN]
