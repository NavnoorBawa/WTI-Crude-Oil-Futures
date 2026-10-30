"""docs/PREREGISTRATION.md agrees with the code that runs the promotion test.

Its tables, the rule and the power are cog blocks (checked by `cog --check` in CI); this test covers
the hand-written sections: every value they state must be the constant's, and every name they cite
in backend/vol_record.py must exist, so a renamed function or a changed constant fails here instead
of leaving a stale pre-registration."""

import re
import unittest
from pathlib import Path

from backend import vol_record as vr

ROOT = Path(__file__).resolve().parents[1]

def _text() -> str:
    """The page with its line wraps undone, so a phrase matches wherever the paragraph breaks."""
    return " ".join((ROOT / "docs" / "PREREGISTRATION.md").read_text(encoding="utf-8").split())


class PreregistrationTest(unittest.TestCase):
    def test_hand_written_values_are_the_constants(self):
        text = _text()
        for phrase in (
            f"version `{vr.PROMOTION_MODEL_VERSION}` with a challenger forecast from `{vr.PROMOTION_CHALLENGER}`",
            f"The sample is the first {vr.PROMOTION_WEEKS} entries taken",
            f"closes are still missing more than {vr.MAX_WAIT_SESSIONS} sessions after its window ends",
            f"Student's t with n − 1 = {vr.PROMOTION_WEEKS - 1} degrees of freedom",
            f"the p-value is below {vr.PROMOTION_ALPHA:.2f}",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_every_cited_name_exists_in_the_record_module(self):
        text = _text()
        cited = set(re.findall(r"`(PROMOTION_[A-Z_]+)`", text)) | set(re.findall(r"`vol_record\.([A-Za-z_]\w*)`", text))
        self.assertTrue({"PROMOTION_MODEL_VERSION", "PROMOTION_CHALLENGER", "PROMOTION_WEEKS", "PROMOTION_ALPHA"} <= cited)
        for name in sorted(cited):
            with self.subTest(name=name):
                self.assertTrue(hasattr(vr, name), f"docs/PREREGISTRATION.md cites vol_record.{name}, which does not exist")

    def test_the_summary_fields_it_cites_are_the_promotion_results(self):
        text = _text()
        fields = set(vr.promotion_test([]))
        for field in ("n_independent_paired", "required"):
            self.assertIn(f"`{field}`", text)
            self.assertIn(field, fields)
        self.assertIn("`promotion.n_excluded_*`", text)
        self.assertTrue({f for f in fields if f.startswith("n_excluded_")})

    def test_the_statuses_it_names_are_the_records(self):
        text = _text()
        for status in ("skipped_roll", "expired", "late_commit"):
            self.assertIn(status, vr.STATUSES)
            self.assertIn(f"`{status}`", text)
        for verdict in ("promote", "keep", "accruing"):
            self.assertIn(f"**{verdict}**", text)


if __name__ == "__main__":
    unittest.main()
