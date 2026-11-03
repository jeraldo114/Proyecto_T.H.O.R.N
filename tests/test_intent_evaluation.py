"""El diagnóstico debe mostrar brechas y falsos positivos sin ejecutar acciones."""
import unittest
from unittest.mock import patch

from tools.evaluate_intents import evaluate, percentile


class IntentEvaluationTests(unittest.TestCase):
    def test_wrong_route_counts_as_false_positive_and_missed_intent(self):
        cases = [
            {"id": "ok", "group": "supported", "text": "a", "expected": {"type": "presence"}},
            {"id": "wrong", "group": "gap", "text": "b", "expected": {"type": "work"}},
            {"id": "unsafe", "group": "negative", "text": "c", "expected": None},
        ]
        with patch('tools.evaluate_intents.command_intent', return_value={"type": "presence", "reply": "dato"}):
            result = evaluate(cases, repeats=1)
        self.assertEqual(result['accuracy'], 1/3)
        self.assertEqual(result['command_precision'], 1/3)
        self.assertEqual(result['command_recall'], 1/2)
        self.assertEqual(result['negative_false_positives'], 1)
        self.assertNotIn('text', result['details'][0])

    def test_bad_cases_and_duplicate_ids_are_rejected(self):
        case = {"id": "one", "group": "negative", "text": "consulta", "expected": None}
        for cases in ([], [case, case], [{**case, 'text': ''}]):
            with self.assertRaises(ValueError):
                evaluate(cases, repeats=1)
        self.assertEqual(percentile([3, 1, 2], .95), 3)

    def test_no_predictions_has_explicit_undefined_precision(self):
        with patch('tools.evaluate_intents.command_intent', return_value=None):
            result = evaluate([{"id": "one", "group": "negative", "text": "una pregunta", "expected": None}], repeats=1)
        self.assertIsNone(result['command_precision'])
        self.assertIsNone(result['command_recall'])
        self.assertEqual(result['accuracy'], 1)
