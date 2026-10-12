"""Backtest engine inputs and P&L statistics: cross-asset context must cover the whole window,
'--features all' must really load macro, and drawdown counts from the starting equity. Network-free."""

import math
import unittest

import numpy as np
import pandas as pd

from backend.backtest_walk_forward import compute_pnl_metrics, prepare_daily_dataset
from backend.oil import PremiumWTIPredictor

CONTEXT_OFFSETS = {"BZ=F": 2.0, "DX-Y.NYB": 0.2, "^VIX": 20.0, "^OVX": 30.0, "^TNX": 4.0,
                   "XLE": 10.0, "XOP": 8.0, "SPY": 15.0}
BARS = 120
RECENT_BARS = 40   # what a fixed look-back fetch ('3y' in production) covers of this window


def wti_bars(bars=BARS):
    index = pd.bdate_range("2016-06-20", periods=bars).tz_localize("America/New_York")
    close = pd.Series([70 + 0.2 * i + 4 * math.sin(i / 6.0) for i in range(bars)], index=index)
    return pd.DataFrame({"Open": close - 0.5, "High": close + 1.5, "Low": close - 1.5, "Close": close,
                         "Volume": [1000.0 + 9 * i for i in range(bars)]}, index=index)


def yahoo_like_fetcher(index, starts=None):
    """Context closes over the whole index, but like Yahoo a fixed look-back period ('3y', '2y', ...)
    returns only the most recent RECENT_BARS. `starts` delays a symbol's first print to a bar."""
    calls = []

    def fetch(symbol, period="2y", interval="1d"):
        calls.append((symbol, period))
        offset = CONTEXT_OFFSETS[symbol]
        series = pd.Series([50 + offset + 0.1 * i + 2 * math.sin(i / 4.0) for i in range(len(index))], index=index)
        series = series.iloc[(starts or {}).get(symbol, 0):]
        return series if period == "max" else series.iloc[-RECENT_BARS:]

    return fetch, calls


def backtest_predictor(fetch):
    predictor = PremiumWTIPredictor.__new__(PremiumWTIPredictor)
    predictor.market_context_period = "3y"           # the live model's look-back, counted from today
    predictor.daily_feature_lookback_bars = 40
    predictor.daily_target_mode = "return"
    predictor.use_historical_external_features_in_training = False   # the env default
    predictor._fetch_market_series = fetch
    return predictor


class ContextCoversTheWindowTest(unittest.TestCase):
    def test_context_is_real_from_the_first_row_of_a_window_longer_than_the_live_lookback(self):
        wti = wti_bars()
        fetch, calls = yahoo_like_fetcher(wti.index)
        predictor = backtest_predictor(fetch)
        dataset, _ = prepare_daily_dataset(predictor, wti, "1w", feature_mode="no_macro", lag_context_days=1)

        self.assertGreater(len(dataset), RECENT_BARS)   # the window is longer than the live fetch
        self.assertEqual({period for _, period in calls}, {"max"})
        # Row 0 is bar 39 and, lagged one day, carries bar 38's VIX close, not the 20.0 fill value.
        expected_vix = 50 + CONTEXT_OFFSETS["^VIX"] + 0.1 * 38 + 2 * math.sin(38 / 4.0)
        self.assertAlmostEqual(dataset["vix_level"].iloc[0], expected_vix, places=9)
        self.assertNotEqual(dataset["dxy_level"].iloc[0], 100.0)
        early = dataset.iloc[: len(dataset) - RECENT_BARS]
        for column in ("vix_level", "dxy_level", "brent_wti_spread", "tnx_level", "xle_return_5d"):
            self.assertGreater(early[column].nunique(), 1, msg=f"{column} is constant before the live look-back")
        self.assertEqual(dataset.attrs["rows_without_context"], 0)

        # The live predictor is left as it was.
        self.assertEqual(predictor.market_context_period, "3y")
        self.assertIs(predictor._fetch_market_series, fetch)

    def test_rows_before_a_symbols_first_print_are_counted_as_without_context(self):
        wti = wti_bars()
        fetch, _ = yahoo_like_fetcher(wti.index, starts={"^OVX": 70})
        dataset, _ = prepare_daily_dataset(backtest_predictor(fetch), wti, "1w", feature_mode="no_macro",
                                           lag_context_days=1)
        # Context dates are bars 38, 39, ...: those before bar 70 (OVX's first close) lack OVX.
        self.assertEqual(dataset.attrs["rows_without_context"], 70 - 38)

    def test_price_only_fetches_no_context(self):
        wti = wti_bars()
        fetch, calls = yahoo_like_fetcher(wti.index)
        dataset, feature_cols = prepare_daily_dataset(backtest_predictor(fetch), wti, "1w", feature_mode="price_only")
        self.assertEqual(calls, [])
        self.assertNotIn("vix_level", feature_cols)
        self.assertEqual(dataset.attrs["rows_without_context"], 0)


class MacroFeatureModeTest(unittest.TestCase):
    def _predictor(self, wti):
        fetch, _ = yahoo_like_fetcher(wti.index)
        predictor = backtest_predictor(fetch)
        predictor._historical_external_mem_cache = {}
        predictor.eia_release_lag_days = 5
        self.macro_calls = []
        weeks = pd.date_range("2016-01-01", periods=60, freq="W-FRI")
        months = pd.date_range("2014-06-01", periods=40, freq="MS")
        days = pd.bdate_range("2015-01-01", "2017-06-30")

        def eia():
            self.macro_calls.append("EIA")
            return pd.Series([400.0 + 3 * math.sin(i) for i in range(len(weeks))], index=weeks)

        def fred(series_id, start_date=None, end_date=None):
            self.macro_calls.append(series_id)
            if series_id == "T10Y2Y":
                return pd.Series([0.5 + 0.01 * i for i in range(len(days))], index=days)
            return pd.Series([100.0 + i + (i % 3) for i in range(len(months))], index=months)

        predictor._fetch_eia_weekly_stocks_series = eia
        predictor._fetch_fred_csv_series = fred
        return predictor

    def test_all_loads_macro_even_with_the_predictor_flag_off(self):
        wti = wti_bars()
        predictor = self._predictor(wti)
        dataset, feature_cols = prepare_daily_dataset(predictor, wti, "1w", feature_mode="all", lag_context_days=1)

        macro_cols = [c for c in feature_cols if c.startswith("hist_")]
        self.assertEqual(len(macro_cols), 14)
        for column in ("hist_eia_stocks_level", "hist_fred_curve_slope", "hist_fred_indpro_yoy"):
            self.assertGreater(dataset[column].nunique(), 1, msg=f"{column} is a constant default")
        self.assertIn("EIA", self.macro_calls)
        self.assertFalse(predictor.use_historical_external_features_in_training)   # restored

    def test_no_macro_neither_fetches_nor_includes_macro(self):
        wti = wti_bars()
        predictor = self._predictor(wti)
        _, feature_cols = prepare_daily_dataset(predictor, wti, "1w", feature_mode="no_macro", lag_context_days=1)
        self.assertFalse([c for c in feature_cols if c.startswith("hist_")])
        self.assertEqual(self.macro_calls, [])


class MaxDrawdownTest(unittest.TestCase):
    def test_losses_from_the_first_trade_count_from_zero_equity(self):
        # The opening trades of the committed unpurged 1d run: equity 0 -> -19,430 is the drawdown.
        self.assertEqual(compute_pnl_metrics([-2050.0, -9030.0, -8350.0], 50.4)["max_drawdown_usd"], 19430.0)
        self.assertEqual(compute_pnl_metrics([-2050.0, -9030.0, -8350.0, 25000.0], 50.4)["max_drawdown_usd"], 19430.0)

    def test_drawdown_after_a_peak_and_a_curve_that_only_rises(self):
        self.assertEqual(compute_pnl_metrics([1000.0, -3000.0, 500.0], 50.4)["max_drawdown_usd"], 3000.0)
        self.assertEqual(compute_pnl_metrics([100.0, 200.0], 50.4)["max_drawdown_usd"], 0.0)
        self.assertTrue(np.isfinite(compute_pnl_metrics([-5.0], 50.4)["max_drawdown_usd"]))


if __name__ == "__main__":
    unittest.main()
