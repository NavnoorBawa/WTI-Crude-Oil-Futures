"""Regression guard for the walk-forward purge — the fix that retracted the headline.

The original Sharpe 2.44 was a look-ahead leak: the backtest trained on rows whose 5-day targets
matured AFTER the prediction point. The fix drops the last (horizon_steps - 1) training rows. These
tests pin that invariant on the engine itself (evaluate_horizon), so a future refactor of its
training slice cannot silently reintroduce the leak. Network-free.
"""

import unittest

import pandas as pd

from backend.backtest_walk_forward import evaluate_horizon, horizon_steps_for, purge_count


class RecordingPredictor:
    """Stands in for PremiumWTIPredictor: records each training frame and trains nothing."""

    daily_target_mode = "return"

    def __init__(self):
        self.training_frames = []

    def train_prediction_models(self, train_input, target_col, target_mode="price"):
        self.training_frames.append(train_input)
        return {}, {}, None, None, [], [], {}   # no models: the engine skips the prediction


def consecutive_bar_dataset(horizon, rows=60):
    """One row per consecutive daily bar, as prepare_daily_dataset builds it: row i's label is the
    Close horizon_steps bars later, so it matures at row i + horizon_steps."""
    return pd.DataFrame({
        "timestamp": [str(ts) for ts in pd.bdate_range("2026-01-05", periods=rows)],
        "feature": [float(i) for i in range(rows)],
        "reference_close": [70.0 + 0.1 * i for i in range(rows)],
        f"target_{horizon}": [0.01] * rows,
        f"actual_price_{horizon}": [70.5 + 0.1 * i for i in range(rows)],
    })


class PurgeCountTest(unittest.TestCase):
    def test_values_per_horizon(self):
        # 1-day target lands on the very next bar and cannot overlap the next prediction -> no purge.
        self.assertEqual(purge_count("1d"), 0)
        # 1-week (5-day) target overlaps the prior 4 rows -> drop 4.
        self.assertEqual(purge_count("1w"), 4)
        self.assertEqual(horizon_steps_for("1d"), 1)
        self.assertEqual(horizon_steps_for("1w"), 5)


class EngineTrainingWindowTest(unittest.TestCase):
    """What evaluate_horizon actually hands the model at every walk-forward step."""

    MIN_TRAIN = 20
    STEP = 3

    def _training_windows(self, horizon, train_window=0):
        dataset = consecutive_bar_dataset(horizon)
        predictor = RecordingPredictor()
        evaluate_horizon(predictor, dataset, ["feature"], horizon, self.MIN_TRAIN, self.STEP,
                         train_window=train_window)
        prediction_rows = list(range(self.MIN_TRAIN, len(dataset), self.STEP))
        self.assertEqual(len(predictor.training_frames), len(prediction_rows))
        return list(zip(prediction_rows, predictor.training_frames))

    def test_no_training_label_matures_after_the_prediction_point(self):
        for horizon in ("1d", "1w"):
            h = horizon_steps_for(horizon)
            for end_idx, frame in self._training_windows(horizon):
                label_matures_at = int(frame.index.max()) + h   # bar of the last training label
                self.assertLessEqual(
                    label_matures_at, end_idx,
                    msg=f"{horizon}: a training label matures at row {label_matures_at}, after the "
                        f"prediction at row {end_idx} - look-ahead leak",
                )
                # Not over-purged either: the newest label still known at the decision is used.
                self.assertEqual(label_matures_at, end_idx, msg=f"{horizon}: purge larger than necessary")
                self.assertEqual(list(frame.columns), ["feature", f"target_{horizon}"])

    def test_rolling_window_trains_on_the_window_minus_the_purge(self):
        for end_idx, frame in self._training_windows("1w", train_window=10):
            self.assertEqual(int(frame.index.min()), end_idx - 10)
            self.assertEqual(len(frame), 10 - purge_count("1w"))

    def test_unpurged_mode_reproduces_the_leak_it_is_kept_for(self):
        dataset = consecutive_bar_dataset("1w")
        predictor = RecordingPredictor()
        evaluate_horizon(predictor, dataset, ["feature"], "1w", self.MIN_TRAIN, self.STEP, purge=False)
        last_label = int(predictor.training_frames[0].index.max()) + horizon_steps_for("1w")
        self.assertEqual(last_label, self.MIN_TRAIN + purge_count("1w"))


if __name__ == "__main__":
    unittest.main()
