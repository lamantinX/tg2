import unittest
from unittest.mock import patch

from app.decision_engine import DecisionContext, decision_engine


class DecisionEngineLoopTests(unittest.TestCase):
    def test_looping_question_chain_is_skipped(self) -> None:
        messages = [
            {"sender": "Allie", "text": "kto eshche idet s pesikom?"},
            {"sender": "Wizard", "text": "kto eshche idet k vhodu s pesikom?"},
            {"sender": "Ann", "text": "a kto eshche idet k vhodu?"},
            {"sender": "Sergey", "text": "kto budet u vhoda s pesikom?"},
            {"sender": "Allie", "text": "kto eshche idet k vhodu s pesikom?"},
            {"sender": "Wizard", "text": "kto idet k vhodu s pesikom?"},
        ]
        with patch("app.decision_engine.random.randint", return_value=0):
            result = decision_engine.decide(DecisionContext(messages=messages))
        self.assertFalse(result.should_send)
        self.assertIn("loop detected", result.reason)

    def test_loop_can_force_topic_change_when_directly_mentioned(self) -> None:
        messages = [
            {"sender": "Allie", "text": "Wizard kto eshche idet s pesikom?"},
            {"sender": "Wizard", "text": "kto eshche idet k vhodu s pesikom?"},
            {"sender": "Ann", "text": "a kto eshche idet k vhodu?"},
            {"sender": "Sergey", "text": "kto budet u vhoda s pesikom?"},
            {"sender": "Allie", "text": "Wizard kto eshche idet k vhodu s pesikom?"},
        ]
        with patch("app.decision_engine.random.randint", return_value=3):
            result = decision_engine.decide(DecisionContext(messages=messages, bot_name="Wizard"))
        self.assertTrue(result.should_send)
        self.assertEqual(result.reaction_type, "topic_change")


if __name__ == "__main__":
    unittest.main()
