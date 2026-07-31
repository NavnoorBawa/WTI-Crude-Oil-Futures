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
    def test_corrupt_state_is_not_silently_replaced(self):
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "signal_state.json"
            state_path.write_text("{broken", encoding="utf-8")
            with (
                mock.patch.object(sa, "STATE_PATH", state_path),
                self.assertRaisesRegex(ValueError, "Refusing to overwrite"),
            ):
                sa.load_state()

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


class DeliveryOrderingTest(unittest.TestCase):
    def test_failed_configured_delivery_does_not_advance_state(self):
        current = {"stance": "LONG LEAN"}
        previous = {"stance": "NEUTRAL"}
        with (
            mock.patch.object(sa, "send_email", return_value=False),
            mock.patch.object(sa, "save_state") as save_state,
            self.assertRaisesRegex(RuntimeError, "state not persisted"),
        ):
            sa.process_signal(current, previous)

        save_state.assert_not_called()

    def test_successful_delivery_is_persisted_after_send(self):
        current = {"stance": "LONG LEAN"}
        previous = {"stance": "NEUTRAL"}
        events = []
        with (
            mock.patch.object(
                sa,
                "send_email",
                side_effect=lambda *_: events.append("sent") or True,
            ),
            mock.patch.object(
                sa,
                "save_state",
                side_effect=lambda *_: events.append("saved") or True,
            ),
        ):
            sa.process_signal(current, previous)

        self.assertEqual(events, ["sent", "saved"])


if __name__ == "__main__":
    unittest.main()
