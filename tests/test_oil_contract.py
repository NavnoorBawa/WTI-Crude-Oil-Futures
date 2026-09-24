"""Contract label, expiry and roll-day daily change of backend.oil.get_current_wti_contract.

Network-free: Yahoo is replaced by fixed frames shaped like yfinance's daily history (index at
midnight America/New_York). The scenario is the real CLV26 -> CLX26 roll: CLV26's last trade date
is Tuesday 2026-09-22, so CL=F shows CLV26 through that session and CLX26 from 2026-09-23.
"""

import math
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd

from backend import contract_calendar, oil

SESSIONS = ["2026-09-16", "2026-09-17", "2026-09-18", "2026-09-21", "2026-09-22", "2026-09-23"]
# Continuous CL=F: CLV26 closes through its last trade date, then CLX26 (an unadjusted splice).
CL_F_CLOSES = [102.43, 101.91, 100.30, 95.78, 94.59, 91.59]
CLX26_CLOSES = [97.51, 97.23, 96.08, 92.37, 90.52, 91.59]


def _frame(closes, sessions=SESSIONS, quote_time=None):
    index = pd.DatetimeIndex(sessions[: len(closes)]).tz_localize("America/New_York")
    frame = pd.DataFrame({"Close": closes, "Volume": [1000 + i for i in range(len(closes))]}, index=index)
    if quote_time is not None:
        frame.attrs["quote_time"] = quote_time
    return frame


class ContractLabelTest(unittest.TestCase):
    def setUp(self):
        # get_current_wti_contract caches module-wide; never leak these fixtures to other tests.
        self.addCleanup(oil._contract_cache.update, {"fetched_at": 0.0, "data": None})

    def _quote(self, now, responses):
        requested = []

        def fake_history(symbol, **kwargs):
            requested.append(symbol)
            response = responses.get(symbol)
            if isinstance(response, Exception):
                raise response
            if response is None:
                return pd.DataFrame()
            return response.copy() if hasattr(response, "copy") else response

        with (
            patch.object(oil, "_yf_history_with_retry", side_effect=fake_history),
            patch.object(oil, "_utc_now", return_value=now),
            patch.object(oil.time, "sleep"),
        ):
            payload = oil.get_current_wti_contract(force_refresh=True)
        return payload, requested

    def test_expiry_delegates_to_the_cme_rule_for_every_contract(self):
        for year in range(2024, 2029):
            for month in range(1, 13):
                self.assertEqual(
                    oil.calculate_wti_expiry_date(year, month),
                    contract_calendar.last_trade_date(year, month),
                )
        # The 25th of Sept 2026 is a Friday (business day): 3 business days back is Tue 22nd.
        self.assertEqual(oil.calculate_wti_expiry_date(2026, 10).isoformat(), "2026-09-22")

    def test_quote_is_labelled_with_the_expiring_contract_through_its_last_trade_date(self):
        # The old ">= 7 days to expiry" rule labelled these CLV26 prices as CLX26.
        for session_count, now, days_left in [
            (4, datetime(2026, 9, 21, 15, tzinfo=timezone.utc), 1),  # Monday 11:00 ET
            (5, datetime(2026, 9, 22, 15, tzinfo=timezone.utc), 0),  # Tuesday = last trade date
        ]:
            payload, requested = self._quote(now, {"CL=F": _frame(CL_F_CLOSES[:session_count])})
            self.assertEqual(payload["symbol"], "CLV26")
            self.assertEqual(payload["expiry_date"], "2026-09-22")
            self.assertEqual(payload["contract_last_trade_date"], "2026-09-22")
            self.assertEqual(payload["days_to_expiry"], days_left)
            self.assertEqual(payload["price_change_quality"], "daily_close")
            self.assertEqual(payload["history_symbol"], "CL=F")
            self.assertEqual(requested, ["CL=F"])
        self.assertAlmostEqual(payload["current_price"], 94.59)
        self.assertAlmostEqual(payload["price_change_percent"], round((94.59 / 95.78 - 1) * 100, 2))

    def test_first_session_after_the_roll_uses_the_new_contracts_previous_close(self):
        payload, requested = self._quote(
            datetime(2026, 9, 23, 15, tzinfo=timezone.utc),
            {"CL=F": _frame(CL_F_CLOSES), "CLX26.NYM": _frame(CLX26_CLOSES)},
        )

        self.assertEqual(payload["symbol"], "CLX26")
        self.assertEqual(payload["days_to_expiry"], 27)  # CLX26 last trades 2026-10-20
        self.assertAlmostEqual(payload["current_price"], 91.59)
        # vs CLX26's own 09-22 close (+1.18%), not CLV26's 94.59 (the spliced -3.17%).
        self.assertAlmostEqual(payload["previous_close"], 90.52)
        self.assertAlmostEqual(payload["price_change"], 1.07)
        self.assertAlmostEqual(payload["price_change_percent"], 1.18)
        self.assertEqual(payload["price_change_quality"], "daily_close_new_contract")
        self.assertEqual(payload["previous_close_symbol"], "CLX26.NYM")
        self.assertEqual(requested, ["CL=F", "CLX26.NYM"])
        self.assertNotIn("CLX26", requested)  # the bare code never resolves on Yahoo

    def test_roll_day_change_is_unavailable_rather_than_spliced_when_new_contract_history_fails(self):
        payload, _ = self._quote(
            datetime(2026, 9, 23, 15, tzinfo=timezone.utc),
            {"CL=F": _frame(CL_F_CLOSES), "CLX26.NYM": RuntimeError("rate limited")},
        )

        self.assertEqual(payload["symbol"], "CLX26")
        self.assertIsNone(payload["previous_close"])
        self.assertIsNone(payload["price_change"])
        self.assertIsNone(payload["price_change_percent"])
        self.assertEqual(payload["price_change_quality"], "unavailable_contract_roll")

    def test_quote_time_after_the_evening_reopen_labels_the_next_session(self):
        # Yahoo keeps the calendar date on the evening Globex bar: at 19:30 ET on the last trade
        # date the bar dated 09-22 already holds CLX26 prices for the 09-23 session.
        quote_time = datetime(2026, 9, 22, 23, 30, tzinfo=timezone.utc)  # 19:30 ET
        closes = CL_F_CLOSES[:4] + [90.80]
        payload, requested = self._quote(
            datetime(2026, 9, 22, 23, 35, tzinfo=timezone.utc),
            {"CL=F": _frame(closes, quote_time=quote_time), "CLX26.NYM": _frame(CLX26_CLOSES[:5])},
        )

        self.assertEqual(payload["symbol"], "CLX26")
        self.assertEqual(payload["market_time"], "2026-09-22T23:30:00Z")
        self.assertEqual(payload["market_session_date"], "2026-09-22")  # Yahoo's label
        self.assertEqual(payload["quote_session_date"], "2026-09-23")   # the CME session quoted
        # The previous CL=F bar (09-21) was CLV26, so the baseline comes from CLX26's own 09-21 close.
        self.assertEqual(payload["price_change_quality"], "daily_close_new_contract")
        self.assertAlmostEqual(payload["previous_close"], 92.37)
        self.assertIn("CLX26.NYM", requested)

    def test_nan_last_close_never_reaches_the_payload(self):
        frame = _frame(CL_F_CLOSES[:4] + [float("nan")])
        payload, _ = self._quote(datetime(2026, 9, 22, 15, tzinfo=timezone.utc), {"CL=F": frame})

        self.assertTrue(math.isfinite(payload["current_price"]))
        self.assertAlmostEqual(payload["current_price"], 95.78)
        self.assertEqual(payload["market_session_date"], "2026-09-21")
        for key in ("current_price", "previous_close", "price_change", "price_change_percent"):
            value = payload[key]
            self.assertTrue(value is None or math.isfinite(value), key)

    def test_fallback_quotes_exchange_suffixed_symbols_starting_at_the_front_contract(self):
        payload, requested = self._quote(
            datetime(2026, 9, 23, 15, tzinfo=timezone.utc),
            {"CL=F": RuntimeError("CL=F down"), "CLX26.NYM": _frame(CLX26_CLOSES)},
        )

        self.assertEqual(requested, ["CL=F", "CLX26.NYM"])
        self.assertEqual(payload["symbol"], "CLX26")
        self.assertEqual(payload["yfinance_symbol"], "CLX26.NYM")
        self.assertEqual(payload["history_symbol"], "CL=F")
        self.assertEqual(payload["data_source"], "yfinance_specific")
        # A single contract's own series has no splice: its previous close is used directly.
        self.assertAlmostEqual(payload["previous_close"], 90.52)
        self.assertEqual(payload["price_change_quality"], "daily_close")

    def test_fallback_moves_to_the_next_contract_only_when_the_front_fails(self):
        _, requested = self._quote(
            datetime(2026, 9, 23, 15, tzinfo=timezone.utc),
            {
                "CL=F": RuntimeError("down"),
                "CLX26.NYM": RuntimeError("down"),
                "CLZ26.NYM": _frame(CLX26_CLOSES),
            },
        )
        self.assertEqual(requested, ["CL=F", "CLX26.NYM", "CLZ26.NYM"])


if __name__ == "__main__":
    unittest.main()
