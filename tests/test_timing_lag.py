import math
import unittest

import numpy as np
import pandas as pd

from backend.backtest_walk_forward import prepare_daily_dataset
from backend.oil import PremiumWTIPredictor


CONTEXT_OFFSETS = {
    "BZ=F": 2.0,
    "DX-Y.NYB": 0.2,
    "^VIX": 20.0,
    "^OVX": 30.0,
    "^TNX": 4.0,
    "XLE": 10.0,
    "XOP": 8.0,
    "SPY": 15.0,
    "CLQ26": 1.0,
}

CONTEXT_FEATURES = [
    "vix_level",
    "brent_return_1d",
    "brent_wti_spread_change_5d",
    "term_spread_pct",
    "dxy_level_zscore_20d",
    "macro_stress_score",
]

TECHNICAL_FEATURES = ["return_20", "atr_pct_14", "price_zscore_60"]


class TimingLagContextTest(unittest.TestCase):
    """Guards for the --lag-context timing-leakage mode.

    Backtest entries are taken at the WTI settlement (~14:30 ET) while the
    cross-asset context closes print at 16:00 ET. lag_context_days=1 must give
    each row exactly the PREVIOUS trading day's fully-formed context features
    (so derived features like brent_wti_spread_change_5d are computed from
    lagged closes), while leaving WTI technical features and targets untouched.
    """

    def _build_predictor_and_data(self):
        predictor = PremiumWTIPredictor.__new__(PremiumWTIPredictor)
        predictor.market_context_period = "3y"
        predictor.daily_feature_lookback_bars = 40
        predictor.daily_target_mode = "return"

        index = pd.date_range("2025-06-01", periods=120, freq="D")
        base = pd.Series(
            [70 + (i * 0.3) + (4 * math.sin(i / 6.0)) for i in range(120)], index=index
        )
        wti_data = pd.DataFrame(
            {
                "Open": base - 0.5,
                "High": base + 1.5,
                "Low": base - 1.5,
                "Close": base,
                "Volume": [1000 + (i * 9) for i in range(120)],
            },
            index=index,
        )

        def fake_market_series(symbol, period="3y", interval="1d"):
            offset = CONTEXT_OFFSETS.get(symbol, 0.0)
            return pd.Series(
                [50 + offset + (i * 0.1) + (2 * math.sin(i / 4.0)) for i in range(120)],
                index=index,
            )

        predictor._fetch_market_series = fake_market_series
        predictor._get_next_wti_contract_symbol = lambda: "CLQ26"
        return predictor, wti_data

    def _build_datasets(self):
        predictor, wti_data = self._build_predictor_and_data()
        same_day, cols0 = prepare_daily_dataset(
            predictor, wti_data, "1w", feature_mode="no_macro", lag_context_days=0
        )
        lagged, cols1 = prepare_daily_dataset(
            predictor, wti_data, "1w", feature_mode="no_macro", lag_context_days=1
        )
        self.assertFalse(same_day.empty)
        self.assertEqual(len(same_day), len(lagged))
        self.assertTrue((same_day["timestamp"] == lagged["timestamp"]).all())
        self.assertEqual(cols0, cols1)
        return same_day, lagged

    def test_lagged_context_features_are_exactly_previous_day_values(self):
        same_day, lagged = self._build_datasets()

        for feature_name in CONTEXT_FEATURES:
            self.assertIn(feature_name, lagged.columns)
            np.testing.assert_allclose(
                lagged[feature_name].iloc[1:].to_numpy(),
                same_day[feature_name].iloc[:-1].to_numpy(),
                err_msg=f"{feature_name} is not the previous trading day's value",
            )

        # The lag must actually change something — a constant context column
        # would make the shift assertions above pass vacuously.
        self.assertFalse(
            np.allclose(lagged["vix_level"].to_numpy(), same_day["vix_level"].to_numpy())
        )

    def test_technical_features_and_targets_are_untouched_by_lag(self):
        same_day, lagged = self._build_datasets()

        for feature_name in TECHNICAL_FEATURES:
            self.assertIn(feature_name, lagged.columns)
            np.testing.assert_allclose(
                lagged[feature_name].to_numpy(),
                same_day[feature_name].to_numpy(),
                err_msg=f"technical feature {feature_name} changed under lag mode",
            )

        for col in ["reference_close", "target_1w", "actual_price_1w"]:
            np.testing.assert_allclose(
                lagged[col].astype(float).to_numpy(),
                same_day[col].astype(float).to_numpy(),
                err_msg=f"{col} changed under lag mode",
            )

    def test_lag_zero_matches_default_behavior(self):
        predictor, wti_data = self._build_predictor_and_data()
        default_ds, _ = prepare_daily_dataset(predictor, wti_data, "1w", feature_mode="no_macro")
        explicit_ds, _ = prepare_daily_dataset(
            predictor, wti_data, "1w", feature_mode="no_macro", lag_context_days=0
        )
        for feature_name in CONTEXT_FEATURES:
            np.testing.assert_allclose(
                default_ds[feature_name].to_numpy(),
                explicit_ds[feature_name].to_numpy(),
            )


if __name__ == "__main__":
    unittest.main()
