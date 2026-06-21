"""Regression guard for the walk-forward purge — the fix that retracted the headline.

The original Sharpe 2.44 was a look-ahead leak: the backtest trained on rows whose 5-day targets
matured AFTER the prediction point. The fix drops the last (horizon_steps - 1) training rows. These
tests pin that invariant so a future refactor cannot silently reintroduce the leak. Network-free.
"""

import unittest

from backend.backtest_walk_forward import horizon_steps_for, purge_count


class PurgeCountTest(unittest.TestCase):
    def test_values_per_horizon(self):
        # 1-day target lands on the very next bar and cannot overlap the next prediction -> no purge.
        self.assertEqual(purge_count("1d"), 0)
        # 1-week (5-day) target overlaps the prior 4 rows -> drop 4.
        self.assertEqual(purge_count("1w"), 4)
        self.assertEqual(horizon_steps_for("1d"), 1)
        self.assertEqual(horizon_steps_for("1w"), 5)

    def test_no_training_label_matures_after_the_prediction_point(self):
        """The leak-free invariant, stated as arithmetic over consecutive daily rows.

        In a dataset of consecutive daily rows, row i's label is at index i + horizon_steps. The
        prediction is made at end_idx, whose reference is at index end_idx. After the purge, the
        last training row is at end_idx - purge - 1, so its label index must be <= end_idx (known
        at the decision moment). If purge were ever too small, some label index would exceed end_idx
        — that is exactly the leak.
        """
        for horizon in ("1d", "1w"):
            h = horizon_steps_for(horizon)
            purge = purge_count(horizon)
            for end_idx in range(50, 300, 7):
                last_train_row = end_idx - purge - 1          # last row kept for training
                last_label_index = last_train_row + h          # the bar its label references
                self.assertLessEqual(
                    last_label_index, end_idx,
                    msg=f"{horizon}: training label at index {last_label_index} matures after "
                        f"prediction point {end_idx} — look-ahead leak",
                )
                # And it must not be needlessly over-purged: the last kept label should reach
                # exactly the prediction point, not stop short (that would waste valid data).
                self.assertEqual(last_label_index, end_idx,
                                 msg=f"{horizon}: purge is larger than necessary")


if __name__ == "__main__":
    unittest.main()
