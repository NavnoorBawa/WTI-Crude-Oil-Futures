"""Network-free unit test for backend/supply_shock_playbook.realized_move.

The event study's headline claims (physical-loss vs threat-only price response) all flow through
realized_move, which computes the move around an event from the EIA daily series. This pins its
arithmetic on a synthetic series with known answers, so the computation is guarded without hitting
the EIA API.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend import supply_shock_playbook as sp
from backend.supply_shock_playbook import realized_move


class RealizedMoveTest(unittest.TestCase):
    def setUp(self):
        # 30 consecutive days; flat at 100 through the event, a +20% spike one day later,
        # then a 110 plateau. Baseline (5 days before the event) is exactly 100.
        self.dates = [f"2020-01-{d:02d}" for d in range(1, 31)]
        self.series = {}
        for k, d in enumerate(self.dates):
            self.series[d] = 100.0 if k <= 10 else (120.0 if k == 11 else 110.0)

    def test_known_response(self):
        rm = realized_move(self.dates, self.series, "2020-01-11", before=5, window=20, settle=10)
        self.assertEqual(rm["base"], 100.0)
        self.assertEqual(rm["peak_pct"], 20.0)        # 100 -> 120
        self.assertEqual(rm["peak_day"], 1)           # spike is one day after the event
        self.assertEqual(rm["trough_pct"], 0.0)       # never dips below baseline
        self.assertEqual(rm["settle_pct"], 10.0)      # price at event+10 days is 110
        self.assertEqual(rm["trajectory"][0], 0.0)    # day 0 == baseline
        self.assertEqual(rm["trajectory"][1], 20.0)   # day 1 == the spike

    def test_event_after_series_returns_none(self):
        self.assertIsNone(realized_move(self.dates, self.series, "2099-01-01"))

    def test_event_without_enough_forward_data_is_not_scored(self):
        self.assertIsNone(realized_move(self.dates, self.series, "2020-01-25", settle=10))

    def test_day0_reaction_and_further_rise_exclude_day0(self):
        # Day 0 (Jan 11) jumps 100 -> 100 (flat) here; the +20% spike comes the next day, so the
        # further rise after day 0 is 20%, measured from the day-0 close, not from the baseline.
        rm = realized_move(self.dates, self.series, "2020-01-11", before=5, window=20, settle=10)
        self.assertEqual(rm["day0_pct"], 0.0)
        self.assertEqual(rm["further_rise_after_day0_pct"], 20.0)

    def test_threshold_applies_to_the_unrounded_move(self):
        series = {d: (100.0 if k < 10 else 102.995) for k, d in enumerate(self.dates)}
        rm = realized_move(self.dates, series, "2020-01-11", before=5, window=20, settle=10)
        self.assertEqual(rm["day0_pct"], 3.0)          # displayed rounded ...
        self.assertFalse(rm["strong_day0"])            # ... but +2.995% is below the 3% threshold

    def test_big_day0_move_does_not_count_as_further_upside(self):
        # A +10% day 0 followed by a flat tape: the old peak-including-day-0 statistic reported a
        # 10% "eventual peak" for this event; the further rise after day 0 is correctly 0.
        series = {d: (100.0 if k < 10 else 110.0) for k, d in enumerate(self.dates)}
        rm = realized_move(self.dates, series, "2020-01-11", before=5, window=20, settle=10)
        self.assertEqual(rm["day0_pct"], 10.0)
        self.assertEqual(rm["peak_pct"], 10.0)
        self.assertEqual(rm["further_rise_after_day0_pct"], 0.0)


def _committed_rows(api_key=None, use_cache=True):
    """The committed EIA cache, read directly: no network, and nothing written into the repo."""
    cached = json.loads(sp._CACHE.read_text(encoding="utf-8"))
    return [(r[0], float(r[1])) for r in cached["rows"]]


class ClassificationTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(sp, "fetch_wti_daily", side_effect=_committed_rows)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_threat_only_excludes_gluts_and_demand_events(self):
        payload = sp.get_playbook_for_api()
        threat_n = payload["distributions"]["threat_only"]["n"]
        manual = [e for e in sp.SHOCK_EVENTS
                  if e["supply_mbpd"] == 0.0 and e["type"] not in sp.NON_THREAT_TYPES]
        self.assertEqual(threat_n, len(manual))
        glut_ids = {e["id"] for e in sp.SHOCK_EVENTS if e["type"] in sp.NON_THREAT_TYPES}
        self.assertIn("saudi_russia_price_war_2020", glut_ids)
        self.assertEqual(payload["event_count"], payload["defined_event_count"])

    def test_priced_in_groups_split_on_the_unrounded_day0_move(self):
        # Soleimani (2020-01-03): 61.17 -> 63.00 is +2.99%, which displays as 3.0 but is below the
        # 3% threshold. Classifying the rounded value moved it into the 7-event strong group.
        events = {e["id"]: e for e in sp.build_playbook()}
        soleimani = next(e for e in events.values() if e["date"] == "2020-01-03")
        self.assertEqual(soleimani["day0_pct"], 3.0)
        self.assertFalse(soleimani["strong_day0"])
        stats = sp.get_playbook_for_api()["priced_in_stats"]
        strong = [e for e in events.values() if e["strong_day0"]]
        self.assertEqual(stats["strong_day0_n"], len(strong))
        self.assertTrue(all(e["day0_pct"] >= 3.0 for e in strong))


def _business_dates(n, start="2001-01-01"):
    import datetime as dt
    d, out = dt.date.fromisoformat(start), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def _random_walk_series(n=3000, seed=7, daily_vol=0.02):
    import numpy as np
    rng = np.random.default_rng(seed)
    prices = 60.0 * np.exp(np.cumsum(rng.normal(0.0, daily_vol, n)))
    return list(zip(_business_dates(n), map(float, prices)))


def _fake_events(series_list, positions):
    dates = [d for d, _ in series_list]
    return [{"id": f"ev{k}", "date": dates[i], "event": "synthetic", "type": "supply_cut", "drivers": ["opec"],
             "supply_mbpd": 1.0, "strait_risk": False, "source": "test"} for k, i in enumerate(positions)]


class PlaceboTest(unittest.TestCase):
    def test_placebo_excludes_every_session_whose_path_touches_an_event(self):
        dates = _business_dates(300)
        kept = sp.placebo_positions(dates, [50, 200], radius=20)
        self.assertEqual(min(kept), 70)                 # first placebo date: 20 sessions after an event
        self.assertEqual(max(kept), 180)                # last: 20 sessions before the final event
        self.assertTrue(all(abs(j - e) >= 20 for j in kept for e in (50, 200)))
        self.assertIn(70, kept)
        self.assertNotIn(69, kept)                      # its base window would hold the event's path

    def test_two_sided_p_counts_ties_against_the_hypothesis(self):
        import numpy as np
        null = np.arange(100.0)
        self.assertAlmostEqual(sp._two_sided_p(null, 99.0), 2 * 2 / 101)
        self.assertAlmostEqual(sp._two_sided_p(null, 1000.0), 2 * 1 / 101)
        self.assertEqual(sp._two_sided_p(null, 50.0), 1.0)

    def test_peak_is_positive_by_construction_and_the_placebo_catches_it(self):
        # On a pure random walk nothing happens on the "event" dates, yet their median 20-session peak
        # is well above zero. Without the placebo that looked like a market reaction; with it, the group
        # is indistinguishable from ordinary dates.
        series = _random_walk_series()
        events = _fake_events(series, range(300, 2800, 200))
        with mock.patch.object(sp, "SHOCK_EVENTS", events):
            study = sp.run_study(series)
        group = next(g for g in study["groups"] if g["key"] == "supply_lost")
        self.assertGreater(study["placebo"]["median_peak_pct"], 2.0)
        self.assertGreater(group["median_peak_pct"], 2.0)
        self.assertGreater(group["p_value_vs_placebo"]["peak"], 0.05)
        self.assertGreater(group["p_value_vs_year_matched_placebo"]["peak"], 0.05)

    def test_a_real_reaction_is_detected(self):
        series = _random_walk_series()
        positions = list(range(300, 2800, 200))
        prices = [p for _, p in series]
        for i in positions:                             # a +12% spike on each event's day 1 only
            prices[i + 1] *= 1.12
        bumped = list(zip([d for d, _ in series], prices))
        with mock.patch.object(sp, "SHOCK_EVENTS", _fake_events(bumped, positions)):
            study = sp.run_study(bumped)
        group = next(g for g in study["groups"] if g["key"] == "supply_lost")
        self.assertLess(group["p_value_vs_placebo"]["peak"], 0.01)
        self.assertGreater(group["p_value_vs_placebo"]["settle"], 0.01)   # the spike does not persist

    def test_year_matched_pools_use_the_event_year_and_widen_when_thin(self):
        placebo = [{"date": f"{y}-06-01", "peak_pct": float(y)} for y in (2000,) * 30 + (2001,) * 5 + (2002,) * 30]
        pools = sp._year_pools(placebo, "peak_pct", [2000, 2001], min_size=20)
        self.assertEqual(set(pools[0]), {2000.0})
        self.assertEqual(set(pools[1]), {2000.0, 2001.0, 2002.0})     # 2001 alone has 5 dates

    def test_group_keys_are_stable_and_labels_precise(self):
        # The dashboard keys its table on these names (src/App.jsx SUPPLY_LABELS); they must not change.
        self.assertEqual([k for k, *_ in sp.GROUPS], ["supply_lost", "threat_only", "strait_risk", "iran_driven",
                                                       "opec_cut", "weather", "conflict", "sanctions"])
        removal = [e for e in sp.SHOCK_EVENTS if sp._is_supply_removal(e)]
        self.assertTrue(any("opec" in e["drivers"] for e in removal))   # announced cuts are in the group ...
        self.assertTrue(sp.GROUP_LABELS["supply_lost"].startswith("Announced or physical"))   # ... so it says so
        self.assertIn("no supply removed", sp.GROUP_LABELS["threat_only"])


class StudyArtifactTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(sp, "fetch_wti_daily", side_effect=_committed_rows)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.study = sp.run_study()

    def test_committed_study_reproduces_from_the_committed_series(self):
        from backend.safe_paths import data_json_path
        artifact = json.loads(data_json_path(sp.STUDY_ARTIFACT).read_text(encoding="utf-8"))
        self.assertEqual({k: v for k, v in self.study.items() if k != "generated_at"},
                         {k: v for k, v in artifact.items() if k != "generated_at"})

    def test_api_payload_keeps_every_field_and_carries_the_placebo(self):
        payload = sp.get_playbook_for_api(current_drivers=["iran"])
        for key in ("event_count", "defined_event_count", "first_event_year", "last_event_year",
                    "distributions", "analogues", "priced_in_stats"):
            self.assertIn(key, payload)
        for key, dist in payload["distributions"].items():
            self.assertTrue({"n", "peak", "settle"} <= set(dist), key)
            self.assertTrue({"n", "median", "min", "max"} <= set(dist["peak"]), key)
        for key in ("strong_day0_n", "strong_day0_median_further_rise_pct", "weak_day0_n",
                    "weak_day0_median_further_rise_pct", "threshold_pct", "definition"):
            self.assertIn(key, payload["priced_in_stats"])
        self.assertEqual(payload["placebo"], self.study["placebo"])
        groups = {g["key"]: g for g in self.study["groups"]}
        lost = payload["distributions"]["supply_lost"]
        self.assertEqual(lost["label"], groups["supply_lost"]["name"])
        self.assertEqual(lost["vs_placebo"]["p_value_vs_placebo"], groups["supply_lost"]["p_value_vs_placebo"])
        self.assertEqual(lost["peak"]["median"], groups["supply_lost"]["median_peak_pct"])
        self.assertEqual(payload["conclusion"], self.study["conclusion"])

    def test_threat_only_moves_no_more_than_an_ordinary_date(self):
        # The published "threats peak +4.0%" is below the placebo's median peak: the study must say so.
        threat = next(g for g in self.study["groups"] if g["key"] == "threat_only")
        self.assertLess(threat["median_peak_pct"], self.study["placebo"]["median_peak_pct"])
        self.assertGreater(threat["p_value_vs_placebo"]["peak"], 0.05)
        self.assertIn("indistinguishable from an ordinary date", self.study["conclusion"])


class CacheSafetyTest(unittest.TestCase):
    def setUp(self):
        sp._last_failed_attempt = None                                     # each test starts with no failed attempt
        self.addCleanup(setattr, sp, "_last_failed_attempt", None)

    def test_a_failed_attempt_is_not_repeated_within_the_window(self):
        # A freeze asks twice (the CL=F source check, then this study); a failing EIA costs one wait.
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "spot.json"
            cache.write_text(json.dumps({"fetched_at": "2000-01-01", "rows": [["2020-01-02", 61.2]]}))
            with mock.patch.object(sp, "_CACHE", cache), \
                    mock.patch.object(sp.requests, "get", side_effect=ConnectionError("down")) as get:
                first = sp.fetch_wti_daily(api_key="test-key")
                second = sp.fetch_wti_daily(api_key="test-key")
                self.assertEqual(get.call_count, 1)
                self.assertEqual(first, second)
                with mock.patch.object(sp.time, "monotonic", return_value=sp._last_failed_attempt + 601):
                    sp.fetch_wti_daily(api_key="test-key")
                self.assertEqual(get.call_count, 2)                           # tried again after the window

    def test_empty_eia_response_never_replaces_a_good_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "spot.json"
            good = {"fetched_at": "2000-01-01", "rows": [["2020-01-02", 61.2], ["2020-01-03", 63.0]]}
            cache.write_text(json.dumps(good))
            empty = mock.Mock(is_redirect=False, is_permanent_redirect=False)
            empty.json.return_value = {"response": {"data": []}}
            with mock.patch.object(sp, "_CACHE", cache), \
                    mock.patch.object(sp.requests, "get", return_value=empty):
                rows = sp.fetch_wti_daily(api_key="test-key")
            self.assertEqual(rows, [("2020-01-02", 61.2), ("2020-01-03", 63.0)])
            self.assertEqual(json.loads(cache.read_text()), good)


if __name__ == "__main__":
    unittest.main()
