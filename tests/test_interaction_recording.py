"""Grabación de frases reales: etiquetas válidas, reanudación y el mismo camino de Vosk que Thorn."""

from __future__ import annotations

import json
from pathlib import Path
import random
import tempfile
import unittest
import wave

from core.brain.interaction_schema import ACTIONS, validate
from tools.evaluate_interaction import voice_rows
from tools.record_interaction_phrases import (CHOICES, LABELS, PLACE, SCENARIOS, TOPIC, TOTAL, Transcriber,
                                              category_for, label, pending, session, target)

CATEGORIES = {"stop", "continue", "clear", "briefing", "system_review", "navigate", "mode", "media", "resume",
              "research", "web_search", "web_read", "map", "clarify", "multi", "chat", "negative"}


def scripted(*answers: str):
    queue = list(answers)

    def ask(_prompt: str) -> str:
        return queue.pop(0)
    return ask, queue


class CatalogTests(unittest.TestCase):
    def test_menu_covers_the_schema(self) -> None:
        self.assertEqual(set(LABELS), set(ACTIONS))
        closed = {value for rule in ACTIONS.values() if isinstance(rule, tuple) for value in rule}
        self.assertEqual(set(CHOICES), closed)

    def test_every_situation_proposes_a_valid_label(self) -> None:
        self.assertEqual(TOTAL, 122)
        for scenario, times, situation, preset in SCENARIOS:
            self.assertIn(scenario, CATEGORIES)
            self.assertGreater(times, 0)
            if preset is not None:
                actions = [(kind, "docker" if arg in (TOPIC, PLACE) else arg) for kind, arg in preset]
                validate(target(actions))
                self.assertEqual(category_for(actions, scenario), scenario, situation)


class LabelTests(unittest.TestCase):
    def test_confirming_the_proposal_asks_only_for_the_topic(self) -> None:
        ask, left = scripted("", "el kernel de linux")
        actions = label((("research", TOPIC),), "investígate el kernel de Linux", ask, lambda _: None)
        self.assertEqual(actions, [("research", "el kernel de linux")])
        self.assertEqual(left, [])

    def test_a_topic_not_in_the_phrase_needs_confirmation(self) -> None:
        ask, left = scripted("", "nginx", "", "docker")
        self.assertEqual(label((("web_search", TOPIC),), "busca docker", ask, lambda _: None),
                         [("web_search", "docker")])
        self.assertEqual(left, [])

    def test_changing_the_label_uses_the_menu(self) -> None:
        kinds = list(ACTIONS)
        stop, navigate = kinds.index("stop_response") + 1, kinds.index("navigate") + 1
        network = ACTIONS["navigate"].index("network") + 1
        ask, left = scripted("c", "99", f"{stop} {navigate}", str(network))
        actions = label((("briefing", ""),), "cállate y muéstrame la red", ask, lambda _: None)
        self.assertEqual(actions, [("stop_response", ""), ("navigate", "network")])
        self.assertEqual(category_for(actions, "briefing"), "multi")
        self.assertEqual(left, [])

    def test_nothing_to_do_keeps_chat_or_negative(self) -> None:
        ask, _ = scripted("c", "0")
        self.assertEqual(label((("map", PLACE),), "qué lindo es arica", ask, lambda _: None), [])
        self.assertEqual(category_for([], "map"), "chat")
        self.assertEqual(category_for([], "negative"), "negative")


class SessionTests(unittest.TestCase):
    def test_records_labels_and_resumes(self) -> None:
        pcm = b"\x01\x00" * 16000
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            # Enter graba; Enter acepta lo oído; «c» + «0» = nada (también vale si la situación no propone
            # acciones, porque el menú repite la pregunta ante una respuesta inválida); q termina.
            ask, left = scripted("", "", "c", "0", "q")
            rows = session(out, lambda started: started() or pcm, lambda _: ("qué hora es", True),
                           ask=ask, say=lambda _: None, rng=random.Random(3))
            self.assertEqual(left, [])
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual((row["said"], row["heard"], row["text"], row["actions"]),
                             ("qué hora es", "qué hora es", "Thorn, qué hora es", []))
            self.assertTrue(row["wake"] and row["wake_detected"])
            with wave.open(str(out / row["wav"]), "rb") as clip:
                self.assertEqual((clip.getnchannels(), clip.getframerate(), clip.getnframes()), (1, 16000, 16000))
            saved = json.loads((out / "frases.json").read_text(encoding="utf-8"))
            self.assertEqual(saved, rows)
            self.assertEqual(len(pending(saved, random.Random(0))), TOTAL - 1)
            # Un lote recién grabado ya sirve para el evaluador.
            self.assertEqual(voice_rows(out / "frases.json", "heard")[0]["target"],
                             {"say": "", "actions": [], "escalate": True})

    def test_short_or_repeated_takes_are_not_saved(self) -> None:
        takes = [b"", b"\x01\x00" * 4000]
        with tempfile.TemporaryDirectory() as folder:
            ask, left = scripted("", "", "r", "q")
            rows = session(Path(folder), lambda started: takes.pop(0), lambda _: ("algo", True),
                           ask=ask, say=lambda _: None, rng=random.Random(1))
            self.assertEqual((rows, left), ([], []))
            self.assertFalse(any(Path(folder).glob("*.wav")))


class TranscriberTests(unittest.TestCase):
    def setUp(self) -> None:
        self.decoded: list[bytes] = []

    def make(self, end, grammar=True) -> Transcriber:
        def decode(_model, audio: bytes) -> str:
            self.decoded.append(audio)
            return "Thorn pausa la música" if len(audio) == 32000 else "pausa la música"
        return Transcriber(Path("pequeño"), Path("pequeño"), loader=lambda path: path, detect=lambda *_: end,
                           decode=decode, grammar_check=lambda _: grammar)

    def test_dictation_starts_after_the_name_like_conversation(self) -> None:
        self.assertEqual(self.make(0.5)(b"\x00" * 32000), ("pausa la música", True))
        self.assertEqual(len(self.decoded[0]), 32000 - 16000)

    def test_without_the_name_it_strips_the_alias(self) -> None:
        self.assertEqual(self.make(None)(b"\x00" * 32000), ("pausa la musica", False))
        self.assertEqual(self.make(None, grammar=False)(b"\x00" * 32000)[1], None)


class VoiceRowsTests(unittest.TestCase):
    def test_heard_and_said_are_separate_sets(self) -> None:
        cases = [{"heard": "", "said": "muéstrame un mapa de valdivia", "category": "map",
                  "actions": [["map", "valdivia"]]},
                 {"heard": "busca", "said": "búscame", "category": "clarify", "actions": [["clarify", "*"]]}]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "frases.json"
            path.write_text(json.dumps(cases), encoding="utf-8")
            heard, said = voice_rows(path, "heard"), voice_rows(path, "said")
        self.assertEqual([row["text"] for row in heard], ["busca"])  # sin texto oído no hay decisión que medir
        self.assertEqual(len(said), 2)
        self.assertEqual(said[1]["target"]["actions"][0]["type"], "clarify")
        self.assertFalse(said[0]["target"]["escalate"])


if __name__ == "__main__":
    unittest.main()
