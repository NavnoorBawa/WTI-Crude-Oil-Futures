"""Network-free unit tests for backend/signal_alert.py stance logic.

The most important guarantee here is post-retraction: a model that is NOT statistically significant
must never surface a LONG/SHORT lean, no matter how large its forecast. That is what keeps the
emailed alert and the dashboard honest now that the direction edge is retracted. (Email sending
itself is not tested — it requires SMTP; only the pure stance logic is.)
"""

import email
import ssl
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


class LiveCountTest(unittest.TestCase):
    def test_live_count_comes_from_the_committed_record(self):
        data = payload(0.2, False)
        data["live_record"] = {"n_calls": 106, "n_resolved_directional": 3, "n_independent_directional": 1}
        sig = sa.extract_signal(data)
        self.assertEqual(sig["live_n"], 1)
        self.assertEqual(sig["live_calls"], 106)


class SendEmailTest(unittest.TestCase):
    def _send(self, cur):
        smtp_cls = mock.MagicMock()
        with (
            mock.patch.object(sa, "GMAIL_APP_PASSWORD", "app-password"),
            mock.patch.object(sa.smtplib, "SMTP_SSL", smtp_cls),
        ):
            self.assertTrue(sa.send_email("NEUTRAL", cur))
        return smtp_cls

    def test_smtp_connection_verifies_the_certificate(self):
        # smtplib's default context skips verification; the credential must only ever travel
        # over a certificate- and hostname-verified channel.
        smtp_cls = self._send(sa.extract_signal(payload(0.2, False)))
        context = smtp_cls.call_args.kwargs["context"]
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_body_uses_payload_statistics_not_hardcoded_text(self):
        data = payload(0.2, False)
        data["performance_metrics"]["by_horizon"]["1w"].update({"wf_samples": 450, "wf_p_value": 0.27})
        smtp_cls = self._send(sa.extract_signal(data))
        raw = smtp_cls.return_value.__enter__.return_value.sendmail.call_args.args[2]
        body = email.message_from_string(raw).get_payload(decode=True).decode("utf-8")
        self.assertIn("450 OOS", body)
        self.assertIn("p = 0.27", body)
        self.assertNotIn("199", body)


if __name__ == "__main__":
    unittest.main()
