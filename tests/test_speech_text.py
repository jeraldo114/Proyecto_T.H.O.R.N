"""Lo que se pronuncia: sin marcado ni citas, con unidades y siglas legibles."""

from __future__ import annotations

import unittest

from core.voice.speech_text import MAX_SPOKEN, for_speech


class SpeechTextTests(unittest.TestCase):
    def test_markup_citations_and_links_are_not_read_aloud(self) -> None:
        self.assertEqual(for_speech("**Importante:** revisa `systemctl status` [1]."),
                         "Importante: revisa systemctl status.")
        self.assertEqual(for_speech("Según el documento [p. 3], falta un dato [2, 3]."),
                         "Según el documento (página 3), falta un dato.")
        self.assertEqual(for_speech("Visita https://es.wikipedia.org/wiki/Python para más."),
                         "Visita un enlace de wikipedia.org para más.")
        self.assertEqual(for_speech("## Resumen\n- Primero\n- Segundo"), "Resumen\nPrimero\nSegundo")
        self.assertEqual(for_speech("Soy T.H.O.R.N, tu asistente."), "Soy Thorn, tu asistente.")

    def test_numbers_and_units_sound_like_spanish(self) -> None:
        self.assertEqual(for_speech("La CPU está al 45 % y la RAM usa 12.5 GiB."),
                         "La ce pe u está al 45 % y la ram usa 12 coma 5 gigabytes.")
        self.assertEqual(for_speech("DNS: 23 ms. Bajada a 2.4 MiB/s, 36.6 °C."),
                         "de ene ese: 23 milisegundos. Bajada a 2 coma 4 megabytes por segundo, 36 coma 6 grados.")
        self.assertEqual(for_speech("Puerta de enlace 192.168.1.1, 1.500 archivos."),
                         "Puerta de enlace 192 punto 168 punto 1 punto 1, 1500 archivos.")
        # Palabras que empiezan como una unidad no se tocan.
        self.assertEqual(for_speech("Tengo 2 sillas y 3 mesas."), "Tengo 2 sillas y 3 mesas.")

    def test_output_is_bounded(self) -> None:
        self.assertLessEqual(len(for_speech("12.5 GiB " * 400)), MAX_SPOKEN)


if __name__ == "__main__":
    unittest.main()
