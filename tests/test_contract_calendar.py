"""Tests for backend/contract_calendar.py — the single source of truth for WTI contract rolls.

Expected last trade dates come from two independent sources: ICE's published WTI Crude Futures
expiry table (ICE WTI expires one business day before NYMEX CL, so each NYMEX date below is the
ICE date plus one business day) and well-documented historical expiries.
"""

import unittest
from datetime import date, datetime, timezone

from backend import contract_calendar as cc


class LastTradeDateTest(unittest.TestCase):
    # (delivery year, month) -> NYMEX last trade date. 2026-2027 rows are ICE's table + 1 business day.
    KNOWN = {
        (2020, 5): date(2020, 4, 21),    # the contract behind the -$37.63 print on 2020-04-20
        (2011, 5): date(2011, 4, 19),    # Good Friday (Apr 22) inside the count-back window
        (2021, 12): date(2021, 11, 19),  # 25th is Thanksgiving -> 4 business days back
        (2022, 1): date(2021, 12, 20),   # Christmas observed Fri 24th -> 25th non-business
        (2024, 7): date(2024, 6, 20),
        (2025, 1): date(2024, 12, 19),
        (2026, 10): date(2026, 9, 22),
        (2026, 11): date(2026, 10, 20),  # 25th is a Sunday -> 4 business days back
        (2026, 12): date(2026, 11, 20),
        (2027, 1): date(2026, 12, 21),
        (2027, 2): date(2027, 1, 20),
        (2027, 3): date(2027, 2, 22),
        (2027, 7): date(2027, 6, 22),
        (2027, 12): date(2027, 11, 19),  # Thanksgiving Nov 25, 2027
    }

    def test_known_expiries(self):
        for (year, month), expected in self.KNOWN.items():
            with self.subTest(contract=cc.contract_code(year, month)):
                self.assertEqual(cc.last_trade_date(year, month), expected)

    def test_custom_business_calendar(self):
        # An observed exchange calendar (e.g. from a price history) overrides the rule-based one.
        closed = {date(2026, 9, 22)}
        ltd = cc.last_trade_date(2026, 10, lambda d: d.weekday() < 5 and d not in closed)
        self.assertEqual(ltd, date(2026, 9, 21))


class HolidayTest(unittest.TestCase):
    def test_2026_holidays(self):
        self.assertEqual(sorted(cc.nymex_holidays(2026)), [
            date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
            date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
            date(2026, 11, 26), date(2026, 12, 25),
        ])

    def test_saturday_new_year_is_not_observed_on_friday(self):
        self.assertTrue(cc.is_business_day(date(2021, 12, 31)))

    def test_columbus_and_veterans_day_trade(self):
        self.assertTrue(cc.is_business_day(date(2026, 10, 12)))
        self.assertTrue(cc.is_business_day(date(2026, 11, 11)))


class TradingDateAndFrontContractTest(unittest.TestCase):
    @staticmethod
    def _utc(text):
        return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)

    def test_session_opens_at_1800_et(self):
        self.assertEqual(cc.trading_date(self._utc("2026-09-22T21:59:00")), date(2026, 9, 22))
        self.assertEqual(cc.trading_date(self._utc("2026-09-22T22:00:00")), date(2026, 9, 23))

    def test_weekend_maps_to_next_session(self):
        self.assertEqual(cc.trading_date(self._utc("2026-09-19T15:00:00")), date(2026, 9, 21))

    def test_cl_f_tracks_expiring_contract_through_last_trade_date(self):
        # CLV26's last trade date is 2026-09-22; CL=F printed the CLV26 price through that day and
        # rolled to CLX26 on the 23rd. The old code relabelled CL=F as CLX26 from 2026-09-16.
        self.assertEqual(cc.front_contract_code(date(2026, 9, 16)), "CLV26")
        self.assertEqual(cc.front_contract_code(date(2026, 9, 22)), "CLV26")
        self.assertEqual(cc.front_contract_code(date(2026, 9, 23)), "CLX26")
        self.assertEqual(cc.front_contract_code(self._utc("2026-09-22T23:00:00")), "CLX26")

    def test_market_hours(self):
        is_open = cc.market_is_open
        self.assertTrue(is_open(self._utc("2026-09-23T14:00:00")))    # Wed 10:00 ET
        self.assertFalse(is_open(self._utc("2026-09-23T21:30:00")))   # Wed 17:30 ET daily break
        self.assertTrue(is_open(self._utc("2026-09-23T22:30:00")))    # Wed 18:30 ET, Thursday session
        self.assertFalse(is_open(self._utc("2026-09-25T21:30:00")))   # Fri 17:30 ET weekend close
        self.assertFalse(is_open(self._utc("2026-09-26T16:00:00")))   # Saturday
        self.assertFalse(is_open(self._utc("2026-09-27T21:00:00")))   # Sun 17:00 ET, not yet open
        self.assertTrue(is_open(self._utc("2026-09-27T22:30:00")))    # Sun 18:30 ET, Monday session
        self.assertFalse(is_open(self._utc("2026-04-03T15:00:00")))   # Good Friday
        self.assertFalse(is_open(self._utc("2026-04-02T23:00:00")))   # Thu evening before Good Friday

    def test_spans_roll(self):
        self.assertTrue(cc.spans_roll(date(2026, 9, 16), date(2026, 9, 23)))
        self.assertFalse(cc.spans_roll(date(2026, 9, 23), date(2026, 9, 30)))

    def test_business_day_arithmetic(self):
        self.assertEqual(cc.shift_business_days(date(2026, 9, 4), 1), date(2026, 9, 8))  # Labor Day
        self.assertEqual(cc.business_days_between(date(2026, 9, 4), date(2026, 9, 11)), 4)
        self.assertEqual(cc.business_days_between(date(2026, 9, 11), date(2026, 9, 4)), -4)

    def test_yahoo_symbol_has_exchange_suffix(self):
        self.assertEqual(cc.yahoo_symbol(2026, 11), "CLX26.NYM")


if __name__ == "__main__":
    unittest.main()
