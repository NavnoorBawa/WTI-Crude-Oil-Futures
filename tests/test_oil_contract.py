"""Contract label, expiry and daily change of backend.oil.get_current_wti_contract.

Network-free: Yahoo is replaced by fixed frames shaped like yfinance's daily history (index at
midnight America/New_York). The scenario is the real CLV26 -> CLX26 roll: CLV26's last trade date
is Tuesday 2026-09-22. CL=F's finalized daily bars are CLV26 settlements through that session and
CLX26 from 2026-09-23, but Yahoo's live CL=F quote already moved to CLX26 at ~03:00 ET on Friday
2026-09-18 (its 1h bars equal CLX26.NYM's from then on), so near the roll the label has to come from
the quote, not from the calendar.
"""

import math
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd

from backend import contract_calendar, oil

SESSIONS = ["2026-09-16", "2026-09-17", "2026-09-18", "2026-09-21", "2026-09-22", "2026-09-23"]
# Continuous CL=F's settled bars: CLV26 through its last trade date, then CLX26 (an unadjusted splice).
CL_F_CLOSES = [102.43, 101.91, 100.30, 95.78, 94.59, 91.59]
CLV26_CLOSES = [102.43, 101.91, 100.30, 95.78, 94.59]
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

    def test_settled_expiring_contract_is_labelled_as_such_through_its_last_trade_date(self):
        # The old ">= 7 days to expiry" rule labelled these CLV26 settlements as CLX26.
        for session_count, quote_time, now, days_left in [
            # Saturday after Friday 09-18's settlement (quote time: Friday's last trade).
            (3, datetime(2026, 9, 18, 20, 59, tzinfo=timezone.utc), datetime(2026, 9, 19, 14, tzinfo=timezone.utc), 3),
            # Tuesday = last trade date, after the 17:00 ET settlement.
            (5, datetime(2026, 9, 22, 21, 0, tzinfo=timezone.utc), datetime(2026, 9, 22, 21, 30, tzinfo=timezone.utc), 0),
        ]:
            payload, requested = self._quote(now, {
                "CL=F": _frame(CL_F_CLOSES[:session_count], quote_time=quote_time),
                "CLV26.NYM": _frame(CLV26_CLOSES[:session_count]),
                "CLX26.NYM": _frame(CLX26_CLOSES[:session_count]),
            })
            self.assertEqual(payload["symbol"], "CLV26")
            self.assertEqual(payload["contract_basis"], "quote_match")
            self.assertEqual(payload["expiry_date"], "2026-09-22")
            self.assertEqual(payload["contract_last_trade_date"], "2026-09-22")
            self.assertEqual(payload["days_to_expiry"], days_left)
            self.assertEqual(payload["price_change_quality"], "daily_close")
            self.assertEqual(payload["history_symbol"], "CL=F")
            self.assertEqual(requested, ["CL=F", "CLV26.NYM", "CLX26.NYM"])
        self.assertAlmostEqual(payload["current_price"], 94.59)
        self.assertAlmostEqual(payload["price_change_percent"], round((94.59 / 95.78 - 1) * 100, 2))

    def test_live_quote_on_the_next_contract_before_the_last_trade_date_is_labelled_and_changed_as_it(self):
        # Monday 2026-09-21 11:07 ET, the real 15:07Z snapshot: CL=F's live row is CLX26 (92.16) while
        # its settled bars are still CLV26's. Labelled by the calendar, this was published as
        # "CLV26 92.16, -8.12% daily_close": the CLV26/CLX26 spread presented as a market move.
        payload, requested = self._quote(datetime(2026, 9, 21, 15, 7, tzinfo=timezone.utc), {
            "CL=F": _frame([102.43, 101.91, 100.30, 92.16], quote_time=datetime(2026, 9, 21, 14, 57, tzinfo=timezone.utc)),
            "CLV26.NYM": _frame([102.43, 101.91, 100.30, 95.90]),
            "CLX26.NYM": _frame([97.51, 97.23, 96.08, 92.16]),
        })

        self.assertEqual(payload["symbol"], "CLX26")
        self.assertEqual(payload["contract_basis"], "quote_match")
        self.assertEqual(payload["expiry_date"], "2026-10-20")
        self.assertAlmostEqual(payload["current_price"], 92.16)
        # Against CLX26's own 09-18 settlement: the move CLX26 actually made.
        self.assertAlmostEqual(payload["previous_close"], 96.08)
        self.assertAlmostEqual(payload["price_change_percent"], -4.08)
        self.assertEqual(payload["price_change_quality"], "daily_close_new_contract")
        self.assertEqual(payload["previous_close_symbol"], "CLX26.NYM")
        self.assertEqual(payload["quote_session_date"], "2026-09-21")
        # The CLX26 history fetched for the check is reused for the baseline (no second request).
        self.assertEqual(requested, ["CL=F", "CLV26.NYM", "CLX26.NYM"])

    def test_quote_is_not_checked_against_the_contracts_outside_the_roll_window(self):
        payload, requested = self._quote(datetime(2026, 10, 13, 14, tzinfo=timezone.utc), {
            "CL=F": _frame([91.49, 91.85, 91.20, 92.10], sessions=["2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13"]),
        })
        self.assertEqual(payload["symbol"], "CLX26")   # last trades 2026-10-20: 5 sessions away
        self.assertEqual(payload["contract_basis"], "calendar")
        self.assertEqual(requested, ["CL=F"])

    def test_unverifiable_quote_near_the_roll_is_replaced_by_the_contracts_own_quote(self):
        # CL=F's contract cannot be established (CLV26.NYM unavailable), so a possibly mislabelled
        # CL=F price is not published: the contracts are quoted directly, reusing what was fetched.
        payload, requested = self._quote(datetime(2026, 9, 21, 15, 7, tzinfo=timezone.utc), {
            "CL=F": _frame([102.43, 101.91, 100.30, 92.16]),
            "CLV26.NYM": RuntimeError("rate limited"),
            "CLX26.NYM": _frame([97.51, 97.23, 96.08, 92.16]),
        })
        self.assertEqual(requested, ["CL=F", "CLV26.NYM", "CLX26.NYM", "CLV26.NYM"])
        self.assertEqual(payload["symbol"], "CLX26")
        self.assertEqual(payload["yfinance_symbol"], "CLX26.NYM")
        self.assertEqual(payload["contract_basis"], "contract_symbol")
        self.assertEqual(payload["data_source"], "yfinance_specific")
        self.assertAlmostEqual(payload["previous_close"], 96.08)

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

    def test_evening_quote_is_never_compared_with_the_close_two_sessions_back(self):
        # Tue 2026-10-13 19:30 ET: Yahoo's bar dated 10-13 holds Wednesday's live prices and
        # Tuesday's settlement is not in the frame until midnight. Monday's close (91.20) was
        # published as the "daily_close" change: +0.99% when the move since Tuesday's settle was -0.33%.
        evening = ["2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13"]
        payload, requested = self._quote(datetime(2026, 10, 13, 23, 35, tzinfo=timezone.utc), {
            "CL=F": _frame([91.49, 91.85, 91.20, 92.10], sessions=evening,
                           quote_time=datetime(2026, 10, 13, 23, 30, tzinfo=timezone.utc)),
        })
        self.assertEqual(payload["market_session_date"], "2026-10-13")
        self.assertEqual(payload["quote_session_date"], "2026-10-14")
        self.assertIsNone(payload["previous_close"])
        self.assertIsNone(payload["price_change_percent"])
        self.assertEqual(payload["price_change_quality"], "unavailable_previous_settlement")
        self.assertEqual(requested, ["CL=F"])

        # After midnight Tuesday's settled bar is back, and the change is measured against it.
        after_midnight = ["2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14"]
        payload, _ = self._quote(datetime(2026, 10, 14, 4, 35, tzinfo=timezone.utc), {
            "CL=F": _frame([91.85, 91.20, 92.40, 92.10], sessions=after_midnight,
                           quote_time=datetime(2026, 10, 14, 4, 30, tzinfo=timezone.utc)),
        })
        self.assertAlmostEqual(payload["previous_close"], 92.40)
        self.assertAlmostEqual(payload["price_change_percent"], -0.32)
        self.assertEqual(payload["price_change_quality"], "daily_close")

    def test_evening_bar_without_a_quote_time_is_still_the_next_session(self):
        # Yahoo's metadata (and so the quote time) can be missing; the clock then says the bar dated
        # today is the next session's, and Tuesday's settlement is not out: no two-session change.
        evening = ["2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13"]
        payload, _ = self._quote(datetime(2026, 10, 13, 23, 35, tzinfo=timezone.utc), {
            "CL=F": _frame([91.49, 91.85, 91.20, 92.10], sessions=evening),
        })
        self.assertEqual(payload["quote_session_date"], "2026-10-14")
        self.assertIsNone(payload["price_change_percent"])
        self.assertEqual(payload["price_change_quality"], "unavailable_previous_settlement")

    def test_settlement_just_after_midnight_is_not_the_next_sessions_quote(self):
        # Wed 00:05 ET: Tuesday's row is back to its 92.40 settlement and Wednesday's row has no close
        # yet, while Yahoo's (delayed) quote time still reads Tue 23:55 ET.
        payload, _ = self._quote(datetime(2026, 10, 14, 4, 5, tzinfo=timezone.utc), {
            "CL=F": _frame([91.85, 91.20, 92.40], sessions=["2026-10-09", "2026-10-12", "2026-10-13"],
                           quote_time=datetime(2026, 10, 14, 3, 55, tzinfo=timezone.utc)),
        })
        self.assertEqual(payload["quote_session_date"], "2026-10-13")
        self.assertEqual(payload["market_time"], "2026-10-13T04:00:00Z")      # the bar, not a live time
        self.assertAlmostEqual(payload["previous_close"], 91.20)
        self.assertEqual(payload["price_change_quality"], "daily_close")

    def test_expired_contracts_settlement_after_midnight_keeps_its_own_label(self):
        # The night after CLV26's last trade date (09-22): the newest settled row is CLV26's final
        # 94.59. It used to take the next session from the delayed quote time and be labelled CLX26.
        payload, _ = self._quote(datetime(2026, 9, 23, 4, 5, tzinfo=timezone.utc), {
            "CL=F": _frame(CL_F_CLOSES[:5], quote_time=datetime(2026, 9, 23, 3, 55, tzinfo=timezone.utc)),
            "CLV26.NYM": _frame(CLV26_CLOSES),
            "CLX26.NYM": _frame(CLX26_CLOSES[:5]),
        })
        self.assertEqual(payload["symbol"], "CLV26")
        self.assertAlmostEqual(payload["current_price"], 94.59)
        self.assertEqual(payload["quote_session_date"], "2026-09-22")

    def test_quote_time_after_the_evening_reopen_labels_the_next_session(self):
        # Yahoo keeps the calendar date on the evening Globex bar: at 19:30 ET on the last trade
        # date the bar dated 09-22 already holds CLX26 prices for the 09-23 session (in CLX26.NYM's
        # own frame too), and CLX26's 09-22 settlement is in neither frame until midnight.
        quote_time = datetime(2026, 9, 22, 23, 30, tzinfo=timezone.utc)  # 19:30 ET
        payload, requested = self._quote(datetime(2026, 9, 22, 23, 35, tzinfo=timezone.utc), {
            "CL=F": _frame(CL_F_CLOSES[:4] + [90.80], quote_time=quote_time),
            "CLX26.NYM": _frame(CLX26_CLOSES[:4] + [90.80], quote_time=quote_time),
        })

        self.assertEqual(payload["symbol"], "CLX26")
        self.assertEqual(payload["market_time"], "2026-09-22T23:30:00Z")
        self.assertEqual(payload["market_session_date"], "2026-09-22")  # Yahoo's label
        self.assertEqual(payload["quote_session_date"], "2026-09-23")   # the CME session quoted
        # Not CLX26's 09-21 close (a two-session change) and not CLV26's 09-21 close (a splice).
        self.assertIsNone(payload["previous_close"])
        self.assertEqual(payload["price_change_quality"], "unavailable_contract_roll")
        self.assertIn("CLX26.NYM", requested)

        # After midnight CLX26's 09-22 settlement is back: the change is CLX26's own.
        quote_time = datetime(2026, 9, 23, 4, 30, tzinfo=timezone.utc)  # 00:30 ET
        payload, _ = self._quote(datetime(2026, 9, 23, 4, 35, tzinfo=timezone.utc), {
            "CL=F": _frame(CL_F_CLOSES[:5] + [90.90], quote_time=quote_time),
            "CLX26.NYM": _frame(CLX26_CLOSES[:5] + [90.90], quote_time=quote_time),
        })
        self.assertAlmostEqual(payload["previous_close"], 90.52)
        self.assertAlmostEqual(payload["price_change_percent"], 0.42)
        self.assertEqual(payload["price_change_quality"], "daily_close_new_contract")

    def test_nan_last_close_never_reaches_the_payload(self):
        frame = _frame(CL_F_CLOSES[:4] + [float("nan")])
        payload, _ = self._quote(datetime(2026, 9, 22, 15, tzinfo=timezone.utc), {
            "CL=F": frame, "CLV26.NYM": _frame(CLV26_CLOSES[:4]), "CLX26.NYM": _frame(CLX26_CLOSES[:4]),
        })

        self.assertTrue(math.isfinite(payload["current_price"]))
        self.assertAlmostEqual(payload["current_price"], 95.78)
        self.assertEqual(payload["market_session_date"], "2026-09-21")
        for key in ("current_price", "previous_close", "price_change", "price_change_percent"):
            value = payload[key]
            self.assertTrue(value is None or math.isfinite(value), key)

    def test_stale_close_never_inherits_a_fresh_quote_time(self):
        # 2026-09-23 09:50 ET, the first session after the roll: Yahoo's newest row has no finite
        # close, so the latest close is CLV26's last-trade-date settlement. It used to go out as
        # "CLX26 94.59 +2.4% LIVE" (a CLV26 price, a cross-contract change and a fresh quote time).
        frame = _frame(CL_F_CLOSES[:5] + [float("nan")], quote_time=datetime(2026, 9, 23, 13, 50, tzinfo=timezone.utc))
        payload, _ = self._quote(datetime(2026, 9, 23, 14, tzinfo=timezone.utc), {
            "CL=F": frame, "CLV26.NYM": _frame(CLV26_CLOSES), "CLX26.NYM": _frame(CLX26_CLOSES),
        })

        self.assertEqual(payload["symbol"], "CLV26")
        self.assertAlmostEqual(payload["current_price"], 94.59)
        self.assertEqual(payload["market_time"], "2026-09-22T04:00:00Z")  # the bar, not the quote
        self.assertEqual(payload["market_session_date"], "2026-09-22")
        self.assertEqual(payload["quote_session_date"], "2026-09-22")
        self.assertAlmostEqual(payload["previous_close"], 95.78)
        self.assertEqual(payload["price_change_quality"], "daily_close")

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
        self.assertEqual(payload["contract_basis"], "contract_symbol")
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
