import unittest

import pytest

from tradingagents.graph.signal_processing import SignalProcessor


@pytest.mark.unit
class SignalProcessorTests(unittest.TestCase):
    def test_parses_buy_rating(self):
        sp = SignalProcessor()
        result = sp.process_signal("**Rating**: Buy\nSome detail here")
        self.assertEqual(result, "Buy")

    def test_parses_sell_rating(self):
        sp = SignalProcessor()
        result = sp.process_signal("**Rating**: Sell\nExit position")
        self.assertEqual(result, "Sell")

    def test_parses_hold_rating(self):
        sp = SignalProcessor()
        result = sp.process_signal("**Rating**: Hold\nMaintain position")
        self.assertEqual(result, "Hold")

    def test_parses_overweight_rating(self):
        sp = SignalProcessor()
        result = sp.process_signal("**Rating**: Overweight\nIncrease position")
        self.assertEqual(result, "Overweight")

    def test_parses_underweight_rating(self):
        sp = SignalProcessor()
        result = sp.process_signal("**Rating**: Underweight\nReduce position")
        self.assertEqual(result, "Underweight")

    def test_accepts_llm_for_backwards_compat(self):
        sp = SignalProcessor(quick_thinking_llm="not_used")
        result = sp.process_signal("**Rating**: Buy\nGood setup")
        self.assertEqual(result, "Buy")

    def test_stores_quick_thinking_llm(self):
        """The constructor stores quick_thinking_llm as an attribute."""
        sp = SignalProcessor(quick_thinking_llm="my-llm")
        self.assertEqual(sp.quick_thinking_llm, "my-llm")

    def test_quick_thinking_llm_defaults_to_none(self):
        sp = SignalProcessor()
        self.assertIsNone(sp.quick_thinking_llm)

    def test_handles_empty_string(self):
        sp = SignalProcessor()
        result = sp.process_signal("")
        self.assertIn(result, ["Buy", "Sell", "Hold", "Overweight", "Underweight"])


if __name__ == "__main__":
    unittest.main()
