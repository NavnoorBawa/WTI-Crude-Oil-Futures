"""Network-free unit tests for backend/signal_alert.py stance logic.

The most important guarantee here is post-retraction: a model that is NOT statistically significant
must never surface a LONG/SHORT lean, no matter how large its forecast. That is what keeps the
emailed alert and the dashboard honest now that the direction edge is retracted. (Email sending
itself is not tested — it requires SMTP; only the pure stance logic is.)
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from backend import signal_alert as sa


def payload(pct, significant):
    return {
        "performance_metrics": {"by_horizon": {"1w": {"wf_is_significant": significant}}},
        "multi_horizon_predictions": {"percentage_changes": {"1w": pct}},
        "current_price": 80.0,
        "contract": {"symbol": "CLN26"},
        "frozen_at": "2026-06-20T00:00:00+00:00",
    }


class ExtractSignalTest(unittest.TestCase):
    def test_non_significant_is_neutral_even_with_a_strong_forecast(self):
        # The retraction guarantee: the purged model is non-significant, so a -2.4% forecast
        # must still read NEUTRAL. If this ever flips, the site would re-assert a dead signal.
        self.assertEqual(sa.extract_signal(payload(-2.4, False))["stance"], "NEUTRAL")
        self.assertEqual(sa.extract_signal(payload(3.0, False))["stance"], "NEUTRAL")

    def test_significant_leans_follow_the_forecast_sign(self):
        self.assertEqual(sa.extract_signal(payload(1.5, True))["stance"], "LONG LEAN")
        self.assertEqual(sa.extract_signal(payload(-1.5, True))["stance"], "SHORT LEAN")

    def test_significant_but_low_conviction_is_neutral(self):
        # Within the +/-0.6% band there is no lean even when significant.
        self.assertEqual(sa.extract_signal(payload(0.3, True))["stance"], "NEUTRAL")


class SaveStateTest(unittest.TestCase):
    def test_unchanged_stance_does_not_rewrite_state(self):
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "signal_state.json"
            state_path.write_text('{"stance": "NEUTRAL", "updated_at": "original"}')
            original = state_path.read_text()

            with mock.patch.object(sa, "STATE_PATH", state_path):
                changed = sa.save_state(
                    {"stance": "NEUTRAL", "fc_pct": 1.25},
                    {"stance": "NEUTRAL"},
                )

            self.assertFalse(changed)
            self.assertEqual(state_path.read_text(), original)

    def test_stance_change_is_persisted(self):
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "signal_state.json"
            with mock.patch.object(sa, "STATE_PATH", state_path):
                changed = sa.save_state(
                    {"stance": "LONG LEAN", "fc_pct": 1.25},
                    {"stance": "NEUTRAL"},
                )

            self.assertTrue(changed)
            self.assertIn('"LONG LEAN"', state_path.read_text())


if __name__ == "__main__":
    unittest.main()
