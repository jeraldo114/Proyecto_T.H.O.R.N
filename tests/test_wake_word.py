"""El nombre se reconoce igual en el HUD y en el servidor."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from core.brain.commands import WAKE_ALIASES, command_intent, strip_wake

ROOT = Path(__file__).resolve().parents[1]


class WakeWordTests(unittest.TestCase):
    def test_frontend_and_backend_share_the_same_aliases(self) -> None:
        source = (ROOT / "frontend" / "src" / "voice" / "wake-word.js").read_text(encoding="utf-8")
        match = re.search(r"export const WAKE_ALIASES = (\[[^\]]*\]);", source)
        self.assertIsNotNone(match, "wake-word.js debe exportar WAKE_ALIASES como lista literal")
        aliases = json.loads(match[1].replace("'", '"'))
        self.assertEqual(tuple(aliases), WAKE_ALIASES)

    def test_measured_stt_errors_reach_the_same_command(self) -> None:
        for prompt in ("Thorn, revisa el equipo", "Fourth revisa el equipo", "Thorne, revisa el equipo",
                       "T.H.O.R.N. revisa el equipo"):
            self.assertEqual(command_intent(prompt), {"type": "system_review", "reply": ""}, prompt)
        self.assertEqual(strip_wake("fourth deshaz el ultimo cambio"), "deshaz el ultimo cambio")
        # Palabras reales que empiezan igual no pierden su comienzo.
        self.assertEqual(strip_wake("tornillo suelto"), "tornillo suelto")
        self.assertIsNone(command_intent("Tom revisa el equipo"), "en texto escrito Tom puede ser un nombre")


if __name__ == "__main__":
    unittest.main()
