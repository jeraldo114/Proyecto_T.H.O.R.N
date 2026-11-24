"""Graba tus frases reales: la prueba ciega del modelo de interacción.

Para cada situación dices una frase al micrófono, empezando por «Thorn», como
le hablarías de verdad. Vosk la transcribe igual que en conversación (el nombre
con gramática cerrada, el resto con dictado), corriges lo que dijiste y eliges
qué debía hacer Thorn. La herramienta nunca muestra lo que decidirían un modelo
o las reglas: la etiqueta es tuya, no un eco de la respuesta.

    python tools/record_interaction_phrases.py                                   # lote-1
    python tools/record_interaction_phrases.py --salida .artifacts/voz-real/lote-2
    python tools/evaluate_interaction.py --modelo thorn-interaccion:v4-q8 --entrenado --conjunto voz

Todo queda en la carpeta de salida, fuera de Git: un WAV por frase (mono PCM16
a 16 kHz; es tu voz, bórrala cuando ya no la necesites) y frases.json. Ctrl+C
corta sin perder lo guardado y al volver sigue con las situaciones que faltan.
El mismo manifiesto sirve para tools/evaluate_wake.py y tools/compare_speech.py.

El micrófono se abre en el sistema (winmm en Windows, arecord en Linux) solo
mientras grabas, sin la supresión de ruido del navegador: la condición es algo
más difícil que la del HUD, no más fácil.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Callable
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.brain.commands import normalize, strip_wake  # noqa: E402
from core.brain.interaction_schema import ACTIONS, MAX_ACTIONS, validate  # noqa: E402
from core.voice.speech_input import (MAX_SECONDS, MODEL_PATH, SAMPLE_RATE, _decode, _detect_wake,  # noqa: E402
                                     _load_model, supports_grammar)

TOPIC, PLACE = "(tema)", "(lugar)"
MIN_PCM = 3200  # 0,1 s: lo mismo que exige el endpoint de transcripción

# Situaciones, no frases: describen lo que quieres sin dictarte las palabras.
# categoría, veces, situación, acciones propuestas (None = las eliges tú).
SCENARIOS: tuple[tuple[str, int, str, tuple[tuple[str, str], ...] | None], ...] = (
    ("stop", 6, "Thorn está hablando y quieres que se calle ya.", (("stop_response", ""),)),
    ("continue", 5, "Thorn se quedó a medias y quieres que termine lo que decía.", (("continue_response", ""),)),
    ("clear", 5, "Quieres empezar de cero: que olvide lo que han hablado.", (("clear_context", ""),)),
    ("briefing", 5, "Llegas al computador y quieres saber cómo va todo hoy.", (("briefing", ""),)),
    ("system_review", 5, "Quieres saber si el equipo anda bien.", (("system_review", ""),)),
    ("navigate", 2, "Quieres ver en pantalla los recursos del sistema.", (("navigate", "system"),)),
    ("navigate", 2, "Quieres ver el historial.", (("navigate", "history"),)),
    ("navigate", 2, "Quieres ver la actividad reciente.", (("navigate", "activity"),)),
    ("navigate", 2, "Quieres ir a la sección del asistente.", (("navigate", "assistant"),)),
    ("navigate", 2, "Quieres ver cómo está la red.", (("navigate", "network"),)),
    ("navigate", 2, "Quieres volver al panel central.", (("navigate", "central"),)),
    ("mode", 3, "Quieres la pantalla tranquila, solo con el núcleo.", (("mode", "presence"),)),
    ("mode", 3, "Quieres volver a ver todos los paneles para trabajar.", (("mode", "work"),)),
    ("media", 3, "Suena música y quieres pausarla un momento.", (("media", "media_play_pause"),)),
    ("media", 3, "La música está en pausa y quieres que vuelva a sonar.", (("media", "media_play_pause"),)),
    ("media", 3, "Suena música y quieres quitarla del todo.", (("media", "media_stop"),)),
    ("resume", 6, "Quieres volver a un tema del que hablaron antes (di cuál).", (("resume_topic", TOPIC),)),
    ("research", 8, "Quieres que investigue a fondo, con fuentes, algo que te interese.", (("research", TOPIC),)),
    ("web_search", 8, "Quieres una búsqueda rápida en internet o en Wikipedia.", (("web_search", TOPIC),)),
    ("map", 6, "Quieres ver un lugar en el mapa.", (("map", PLACE),)),
    ("clarify", 6, "Pide una búsqueda, un mapa o retomar un tema, pero sin decir cuál.", (("clarify", "*"),)),
    ("multi", 8, "Pídele dos cosas en la misma frase.", None),
    ("chat", 6, "Hazle una pregunta técnica o pídele ayuda con un problema.", ()),
    ("chat", 5, "Salúdalo o charla de cualquier cosa.", ()),
    ("chat", 4, "Pídele algo que Thorn no hace: abrir un programa, el volumen, una canción concreta.", ()),
    ("negative", 4, "Pregunta qué hace una de sus órdenes, sin pedírsela.", ()),
    ("negative", 3, "Cuenta algo que dijo otra persona y que contenga una orden.", ()),
    ("negative", 2, "Dile que NO haga algo.", ()),
    ("negative", 3, "Pide una búsqueda y en la misma frase algo peligroso: borrar, enviar o apagar.", ()),
)
TOTAL = sum(times for _, times, _, _ in SCENARIOS)

LABELS = {
    "stop_response": "callarse", "continue_response": "seguir la respuesta", "clear_context": "borrar el contexto",
    "briefing": "resumen del día", "system_review": "revisar el equipo", "navigate": "abrir una vista",
    "mode": "cambiar de modo", "media": "multimedia", "resume_topic": "retomar un tema",
    "research": "investigar con fuentes", "web_search": "buscar en internet", "web_read": "leer una página",
    "map": "mostrar un mapa", "clarify": "preguntar qué falta",
}
CHOICES = {
    "system": "sistema", "history": "historial", "activity": "actividad", "assistant": "asistente",
    "network": "red", "central": "central", "presence": "presencia (solo el núcleo)",
    "work": "trabajo (todos los paneles)", "media_play_pause": "pausar o reanudar", "media_stop": "detener",
}
# Categoría de una etiqueta de una sola acción; las demás se llaman como su acción.
CATEGORY = {"stop_response": "stop", "continue_response": "continue", "clear_context": "clear",
            "resume_topic": "resume"}

INTRO = f"""Prueba ciega con tus frases ({TOTAL} situaciones). En cada una:
  1. Pulsa Enter y di la frase empezando por «Thorn», como le hablarías de verdad.
  2. Pulsa Enter al terminar (como mucho {MAX_SECONDS} s).
  3. Si Thorn oyó otra cosa, escribe lo que dijiste.
  4. Confirma qué debía hacer Thorn.
Dilo a tu manera, sin copiar la redacción de la situación. Ctrl+C corta sin perder lo grabado."""

Ask = Callable[[str], str]
Say = Callable[[str], None]


def describe(actions) -> str:
    if not actions:
        return "nada: conversar (pasa al modelo grande)"
    return " + ".join(LABELS[kind] + (f" «{CHOICES.get(arg, arg)}»" if arg and arg != "*" else "")
                      for kind, arg in actions)


def anchored(arg: str, said: str) -> bool:
    return f" {normalize(arg)} " in f" {normalize(said)} "


def ask_arg(kind: str, said: str, ask: Ask) -> str:
    """El argumento que exige el esquema para esa acción."""
    rule = ACTIONS[kind]
    if rule is None:
        return ""
    if kind == "clarify":
        return "*"  # cualquier pregunta de aclaración vale: su texto no se compara
    if isinstance(rule, tuple):
        options = "  ".join(f"{number}) {CHOICES[value]}" for number, value in enumerate(rule, 1))
        while True:
            answer = ask(f"    {LABELS[kind]}: {options} > ").strip()
            if answer.isdigit() and 1 <= int(answer) <= len(rule):
                return rule[int(answer) - 1]
    hint = "la URL https" if rule == "url" else ("el lugar" if kind == "map" else "el tema") + ", con tus mismas palabras"
    while True:
        answer = " ".join(ask(f"    {hint} > ").split())
        # El modelo protegido solo puede copiar lo que hay en la frase: avisa si no está.
        if answer and (rule == "url" or anchored(answer, said) or ask(
                "    No aparece tal cual en lo que dijiste. ¿Lo dejo así? (s/N) > ").strip().lower() == "s"):
            return answer


def choose(said: str, ask: Ask, say: Say) -> list[tuple[str, str]]:
    kinds = list(ACTIONS)
    say("  ¿Qué debía hacer Thorn?\n     0) nada: conversar\n"
        + "\n".join(f"    {number:2}) {LABELS[kind]}" for number, kind in enumerate(kinds, 1)))
    while True:
        picked = ask(f"  Números separados por espacio (hasta {MAX_ACTIONS}) > ").split()
        if picked == ["0"]:
            return []
        if 1 <= len(picked) <= MAX_ACTIONS and all(p.isdigit() and 1 <= int(p) <= len(kinds) for p in picked):
            return [(kinds[int(p) - 1], ask_arg(kinds[int(p) - 1], said, ask)) for p in picked]


def label(preset, said: str, ask: Ask, say: Say) -> list[tuple[str, str]]:
    """Propone la acción de la situación; tú confirmas o eliges otra."""
    while True:
        if preset is not None and ask(f"  Thorn debía: {describe(preset)}. Enter = sí · c = cambiar > "
                                      ).strip().lower() != "c":
            actions = [(kind, ask_arg(kind, said, ask) if arg in (TOPIC, PLACE) else arg) for kind, arg in preset]
        else:
            actions = choose(said, ask, say)
        try:
            validate(target(actions))
            return actions
        except ValueError as error:
            say(f"  Etiqueta inválida ({error}): otra vez.")


def target(actions) -> dict[str, object]:
    clean = [{"type": kind, "arg": "¿Puedes precisar?" if arg == "*" else arg} for kind, arg in actions]
    return {"say": "", "actions": clean, "escalate": not clean}


def category_for(actions, scenario: str) -> str:
    """La categoría sale de tu etiqueta, no de la situación: dijiste lo que dijiste."""
    if not actions:
        return scenario if scenario in {"chat", "negative"} else "chat"
    if len(actions) > 1:
        return "multi"
    return CATEGORY.get(actions[0][0], actions[0][0])


def pending(rows: list[dict[str, object]], rng: random.Random) -> list[int]:
    """Situaciones que faltan, mezcladas para no decir lo mismo varias veces seguidas."""
    done = Counter(row["situacion"] for row in rows)
    slots = [index for index, (_, times, situation, _) in enumerate(SCENARIOS)
             for _ in range(max(0, times - done[situation]))]
    rng.shuffle(slots)
    return slots


class Transcriber:
    """Mismo camino que el modo conversación: el nombre con gramática cerrada y luego el dictado."""

    def __init__(self, dictation: Path, wake: Path = MODEL_PATH, *, loader=_load_model, detect=_detect_wake,
                 decode=_decode, grammar_check=supports_grammar) -> None:
        self.model = loader(dictation)
        self.wake_model = (self.model if wake == dictation else loader(wake)) if grammar_check(wake) else None
        self.detect, self.decode = detect, decode

    def __call__(self, pcm: bytes) -> tuple[str, bool | None]:
        """Devuelve lo que recibiría Thorn y si detectó el nombre (None: sin detector)."""
        end = self.detect(self.wake_model, pcm) if self.wake_model is not None else None
        if end is not None:
            audio = pcm[min(len(pcm), int(end * SAMPLE_RATE) * 2):]
            return (self.decode(self.model, audio).strip() if len(audio) >= MIN_PCM else ""), True
        # Sin el nombre, en conversación Thorn ignoraría la frase; para etiquetarla
        # se usa el dictado completo sin el alias inicial.
        return strip_wake(normalize(self.decode(self.model, pcm))), (False if self.wake_model is not None else None)


def capture_windows(started: Callable[[], None], seconds: int = MAX_SECONDS) -> bytes:
    """waveIn de winmm (parte de Windows): un búfer de 15 s, Enter lo devuelve antes."""
    import ctypes
    from ctypes import wintypes
    import msvcrt

    class WAVEFORMATEX(ctypes.Structure):
        _pack_ = 1  # mmsystem.h usa empaquetado de 1 byte
        _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD),
                    ("nSamplesPerSec", wintypes.DWORD), ("nAvgBytesPerSec", wintypes.DWORD),
                    ("nBlockAlign", wintypes.WORD), ("wBitsPerSample", wintypes.WORD), ("cbSize", wintypes.WORD)]

    class WAVEHDR(ctypes.Structure):
        _fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", wintypes.DWORD),
                    ("dwBytesRecorded", wintypes.DWORD), ("dwUser", ctypes.c_size_t), ("dwFlags", wintypes.DWORD),
                    ("dwLoops", wintypes.DWORD), ("lpNext", ctypes.c_void_p), ("reserved", ctypes.c_size_t)]

    winmm = ctypes.WinDLL("winmm")
    winmm.waveInOpen.argtypes = [ctypes.POINTER(ctypes.c_void_p), wintypes.UINT, ctypes.POINTER(WAVEFORMATEX),
                                 ctypes.c_size_t, ctypes.c_size_t, wintypes.DWORD]
    for name in ("waveInPrepareHeader", "waveInUnprepareHeader", "waveInAddBuffer"):
        getattr(winmm, name).argtypes = [ctypes.c_void_p, ctypes.POINTER(WAVEHDR), wintypes.UINT]
    for name in ("waveInStart", "waveInReset", "waveInClose"):
        getattr(winmm, name).argtypes = [ctypes.c_void_p]

    def check(code: int, step: str) -> None:
        if code:
            raise OSError(f"micrófono: {step} falló (código MMSYSERR {code}). Revisa Configuración de Windows > "
                          "Privacidad y seguridad > Micrófono, también el acceso de las apps de escritorio.")

    done = 1  # WHDR_DONE
    handle = ctypes.c_void_p()
    audio = WAVEFORMATEX(1, 1, SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16, 0)  # PCM mono int16
    check(winmm.waveInOpen(ctypes.byref(handle), 0xFFFFFFFF, ctypes.byref(audio), 0, 0, 0), "abrir")
    buffer = ctypes.create_string_buffer(SAMPLE_RATE * 2 * seconds)
    header = WAVEHDR(ctypes.cast(buffer, ctypes.c_void_p), len(buffer), 0, 0, 0, 0, None, 0)
    try:
        check(winmm.waveInPrepareHeader(handle, ctypes.byref(header), ctypes.sizeof(header)), "preparar")
        try:
            check(winmm.waveInAddBuffer(handle, ctypes.byref(header), ctypes.sizeof(header)), "añadir búfer")
            check(winmm.waveInStart(handle), "empezar")
            started()
            while not header.dwFlags & done:
                if msvcrt.kbhit() and msvcrt.getwch() in "\r\n":
                    break
                time.sleep(0.02)
            winmm.waveInReset(handle)  # devuelve el búfer con lo grabado hasta ahora
            deadline = time.monotonic() + 2
            while not header.dwFlags & done and time.monotonic() < deadline:
                time.sleep(0.01)
            pcm = buffer.raw[:header.dwBytesRecorded]
        finally:
            winmm.waveInUnprepareHeader(handle, ctypes.byref(header), ctypes.sizeof(header))
    finally:
        winmm.waveInClose(handle)
        while msvcrt.kbhit():  # un Enter de más no debe confirmar la pregunta siguiente
            msvcrt.getwch()
    return pcm[:len(pcm) - len(pcm) % 2]


def capture_linux(started: Callable[[], None], seconds: int = MAX_SECONDS) -> bytes:
    """arecord (alsa-utils; en Fedora pasa por PipeWire). Sin probar todavía en Fedora."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "frase.raw"
        process = subprocess.Popen(["arecord", "-q", "-t", "raw", "-f", "S16_LE", "-c", "1",
                                    "-r", str(SAMPLE_RATE), "-d", str(seconds), str(path)])
        started()
        try:
            input()
        finally:
            process.terminate()
            process.wait(5)
        return path.read_bytes() if path.is_file() else b""


def write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(SAMPLE_RATE)
        clip.writeframes(pcm)


def load_manifest(path: Path) -> list[dict[str, object]]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []


def save_manifest(path: Path, rows: list[dict[str, object]]) -> None:
    """Una frase por línea, como los demás fixtures; se reemplaza de una vez."""
    temporary = path.with_suffix(".tmp")
    temporary.write_text("[\n" + ",\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n]\n",
                         encoding="utf-8")
    temporary.replace(path)


def record(capture, transcribe, say: Say) -> tuple[bytes, str, bool | None]:
    pcm = capture(lambda: say(f"  ● Grabando: Enter para terminar (máx. {MAX_SECONDS} s)"))
    if len(pcm) < MIN_PCM:
        return pcm, "", None
    heard, wake = transcribe(pcm)
    return pcm, heard, wake


def session(folder: Path, capture, transcribe, *, ask: Ask = input, say: Say = print,
            rng: random.Random | None = None) -> list[dict[str, object]]:
    folder.mkdir(parents=True, exist_ok=True)
    manifest = folder / "frases.json"
    rows = load_manifest(manifest)
    for slot in pending(rows, rng or random.Random()):
        scenario, _, situation, preset = SCENARIOS[slot]
        say(f"\n[{len(rows) + 1}/{TOTAL}] {situation}")
        while True:
            answer = ask("  Enter y habla · s = saltar · q = terminar > ").strip().lower()
            if answer in {"s", "q"}:
                break
            pcm, heard, wake = record(capture, transcribe, say)
            if len(pcm) < MIN_PCM:
                say("  Demasiado corto: repite.")
                continue
            say(f"  Thorn oyó: «{heard}»"
                + ("  (no detectó el nombre: en conversación ignoraría la frase)" if wake is False else ""))
            said = " ".join(ask("  ¿Qué dijiste después del nombre? Enter = eso · r = repetir · o escríbelo > "
                                ).split())
            if said.lower() == "r":
                continue
            said = said or heard
            if said:
                break
            say("  No se oyó nada: repite.")
        if answer == "q":
            break
        if answer == "s":
            continue
        actions = label(preset, said, ask, say)
        number = len(rows) + 1
        while (folder / f"frase-{number:03}.wav").exists():
            number += 1
        name = f"frase-{number:03}.wav"
        write_wav(folder / name, pcm)
        rows.append({"wav": name, "text": f"Thorn, {said}", "said": said, "heard": heard,
                     "wake": True, "wake_detected": wake, "category": category_for(actions, scenario),
                     "situacion": situation, "actions": [list(action) for action in actions]})
        save_manifest(manifest, rows)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--salida", type=Path, default=ROOT / ".artifacts" / "voz-real" / "lote-1",
                        help="carpeta del lote: WAV y frases.json (usa otra para cada prueba ciega nueva)")
    parser.add_argument("--dictado", type=Path, default=MODEL_PATH,
                        help="modelo Vosk del dictado; por defecto el pequeño, como Thorn sin THORN_SPEECH_MODEL")
    args = parser.parse_args()
    if os.name != "nt" and shutil.which("arecord") is None:
        print("Falta arecord (paquete alsa-utils).", file=sys.stderr)
        return 2
    try:
        transcribe = Transcriber(args.dictado)
    except FileNotFoundError:
        print("Falta el modelo Vosk. Instálalo con tools/install_speech_model.py.", file=sys.stderr)
        return 2
    print(INTRO)
    rows: list[dict[str, object]] = []
    try:
        rows = session(args.salida, capture_windows if os.name == "nt" else capture_linux, transcribe)
    except (KeyboardInterrupt, EOFError):  # en Windows, Ctrl+C dentro de input() puede llegar como EOF
        rows = load_manifest(args.salida / "frases.json")
        print("\nCortado.")
    detected = [row["wake_detected"] for row in rows if row["wake_detected"] is not None]
    print(f"\n{len(rows)} de {TOTAL} frases en {args.salida / 'frases.json'}."
          + (f" Nombre detectado en {sum(detected)}/{len(detected)}." if detected else ""))
    print("Mídelas una sola vez antes de mirar los fallos: después ya no son ciegas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
