"""Property-based tests (Hypothesis) for the evaluation statistics, the contract calendar, the
feature builder's timing and the research lab's realized measures.

The example-based tests pin known answers; these state what must hold for EVERY input: losses are
non-negative and vanish only at the truth, Diebold-Mariano is antisymmetric, backtest p-values are
probabilities, conformal intervals never read an outcome before it is observed, contract rolls
follow the CME rule, and no feature at row t moves when the future does.

Deterministic: every test runs with derandomize=True and no example database, so CI sees the same
examples on every run (Hypothesis' own .hypothesis/ cache is self-gitignored). Set
HYPOTHESIS_MAX_EXAMPLES for a deeper local run. Each property was run against a deliberately broken implementation and fails on
it; the mutation is named in the test's docstring ("Catches: ...").
"""

import os
import unittest
from datetime import date, datetime, time, timedelta, timezone

import numpy as np
import pandas as pd

try:
    from hypothesis import HealthCheck, assume, given, settings
    from hypothesis import strategies as st
    from hypothesis.extra import numpy as hnp
except ImportError:  # pragma: no cover - a dev dependency (requirements-dev.txt)
    raise unittest.SkipTest("hypothesis is not installed: pip install -r requirements-dev.txt") from None

from backend import contract_calendar as cc
from backend import forecast_eval as fe
from backend import vol_forecast as vf
from backend import vol_lab as lab

MAX_EXAMPLES = int(os.environ.get("HYPOTHESIS_MAX_EXAMPLES", "40"))


def _settings(examples=MAX_EXAMPLES):
    return settings(max_examples=examples, derandomize=True, database=None, deadline=None,
                    print_blob=False, suppress_health_check=[HealthCheck.too_slow])


PROPERTY = _settings()
SLOW_PROPERTY = _settings(max(5, MAX_EXAMPLES // 4))   # each example runs thousands of conformal steps

VOL = st.floats(min_value=1e-3, max_value=1e3, allow_nan=False, allow_infinity=False)
LOSS = st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False)
SEED = st.integers(min_value=0, max_value=2**32 - 1)
POW2 = st.sampled_from([0.25, 0.5, 2.0, 4.0])      # exact in binary floating point
CALENDAR_DATES = st.dates(min_value=date(1995, 1, 1), max_value=date(2099, 12, 31))
CONTRACTS = st.tuples(st.integers(min_value=1995, max_value=2099), st.integers(min_value=1, max_value=12))


def _pair(data, elements, min_size=1, max_size=40):
    n = data.draw(st.integers(min_value=min_size, max_value=max_size), label="n")
    a = data.draw(hnp.arrays(np.float64, n, elements=elements), label="a")
    b = data.draw(hnp.arrays(np.float64, n, elements=elements), label="b")
    return a, b


def _previous_contract(year, month):
    return (year - 1, 12) if month == 1 else (year, month - 1)


# ------------------------------------------------------------------------------------------------
# forecast_eval: losses, Diebold-Mariano, VaR backtests, conformal intervals
# ------------------------------------------------------------------------------------------------

class LossProperties(unittest.TestCase):
    @PROPERTY
    @given(st.data())
    def test_qlike_is_nonnegative_and_zero_only_at_the_truth(self, data):
        """Catches: the loss with its sign flipped (log r - r + 1), or without the -1 offset."""
        actual, forecast = _pair(data, VOL)
        loss = fe.qlike(actual, forecast)
        self.assertTrue((loss >= 0).all(), loss)
        np.testing.assert_array_equal(fe.qlike(actual, actual), 0.0)
        apart = np.abs(np.log(actual / forecast)) > 1e-6
        self.assertTrue((loss[apart] > 0).all(), loss[apart])

    @PROPERTY
    @given(st.data(), POW2)
    def test_qlike_is_scale_free(self, data, c):
        """Catches: a loss on vol differences rather than the variance ratio (a - f, not (a / f)^2)."""
        actual, forecast = _pair(data, VOL)
        np.testing.assert_array_equal(fe.qlike(c * actual, c * forecast), fe.qlike(actual, forecast))

    @PROPERTY
    @given(st.data(), POW2)
    def test_mse_var_is_a_nonnegative_symmetric_loss_on_variances(self, data, c):
        """Catches: the squared error taken on vols ((a - f)^2: homogeneous of degree 2, not 4) and
        the unsquared variance difference (a^2 - f^2: negative when the forecast is high)."""
        actual, forecast = _pair(data, VOL)
        loss = fe.mse_var(actual, forecast)
        self.assertTrue((loss >= 0).all())
        np.testing.assert_array_equal(fe.mse_var(actual, actual), 0.0)
        np.testing.assert_array_equal(loss, fe.mse_var(forecast, actual))
        np.testing.assert_array_equal(fe.mse_var(c * actual, c * forecast), c ** 4 * loss)


class DieboldMarianoProperties(unittest.TestCase):
    @PROPERTY
    @given(st.data(), st.integers(min_value=1, max_value=10))
    def test_swapping_the_arguments_flips_the_sign(self, data, horizon):
        """Catches: a statistic on |mean(d)| (one-sided magnitude), which loses the direction."""
        a, b = _pair(data, LOSS, min_size=1, max_size=80)
        ab, ba = fe.dm_test(a, b, horizon), fe.dm_test(b, a, horizon)
        self.assertEqual(ab["stat"], -ba["stat"])
        self.assertEqual(ab["mean_diff"], -ba["mean_diff"])
        self.assertEqual(ab["p_value"], ba["p_value"])
        self.assertTrue(0.0 <= ab["p_value"] <= 1.0)
        same = fe.dm_test(a, a, horizon)
        self.assertEqual((same["stat"], same["p_value"]), (0.0, 1.0))


class VarBacktestProperties(unittest.TestCase):
    EXCEEDANCES = hnp.arrays(np.bool_, st.integers(min_value=1, max_value=300))
    LEVEL = st.floats(min_value=0.001, max_value=0.5)

    @PROPERTY
    @given(EXCEEDANCES, LEVEL)
    def test_kupiec_is_a_valid_likelihood_ratio_test(self, exceed, p):
        """Catches: dropping the k > 0 guard in the unrestricted log-likelihood (0 * log 0 = NaN when
        there is no exceedance, so the p-value is NaN)."""
        res = fe.kupiec(exceed, p)
        self.assertGreaterEqual(res["lr"], 0.0)
        self.assertTrue(0.0 <= res["p_value"] <= 1.0, res)
        self.assertEqual((res["n"], res["exceedances"]), (exceed.size, int(exceed.sum())))

    @PROPERTY
    @given(st.integers(min_value=2, max_value=400), st.data())
    def test_kupiec_lr_vanishes_when_the_observed_rate_is_the_null(self, n, data):
        """Catches: the null log-likelihood with log(p) and log(1 - p) swapped."""
        k = data.draw(st.integers(min_value=1, max_value=n - 1), label="k")
        exceed = np.zeros(n, bool)
        exceed[data.draw(st.permutations(range(n)), label="order")[:k]] = True
        res = fe.kupiec(exceed, k / n)
        self.assertEqual(res["lr"], 0.0)
        self.assertEqual(res["p_value"], 1.0)

    @PROPERTY
    @given(EXCEEDANCES, LEVEL)
    def test_christoffersen_p_values_are_probabilities(self, exceed, p):
        """Catches: dropping the `if zeros:` guard in the Markov log-likelihood (0 * log 0 on a run of
        exceedances turns the statistic into NaN)."""
        res = fe.christoffersen(exceed, p)
        self.assertGreaterEqual(res["lr_ind"], 0.0)
        self.assertGreaterEqual(res["lr_cc"], res["lr_ind"] - 1e-3)       # both rounded to 3 dp
        self.assertTrue(0.0 <= res["p_independence"] <= 1.0, res)
        self.assertTrue(0.0 <= res["p_conditional_coverage"] <= 1.0, res)
        self.assertEqual(res["consecutive_exceedances"], int((exceed[:-1] & exceed[1:]).sum()))


class ConformalProperties(unittest.TestCase):
    @SLOW_PROPERTY
    @given(SEED, st.sampled_from([0.1, 0.2, 0.3]), st.floats(min_value=1e-3, max_value=1e3))
    def test_long_run_coverage_is_one_minus_alpha_on_iid_noise(self, seed, alpha, scale):
        """Catches: the coverage feedback inverted (a hit counted as a miss), in either function."""
        e = np.random.default_rng(seed).standard_normal(2500) * scale
        aci = fe.adaptive_conformal(e, alpha=alpha)
        self.assertAlmostEqual(float(np.nanmean(aci["hit"])), 1 - alpha, delta=0.035)
        delayed = fe.delayed_aci(e, delay=5, alpha=alpha, window=500, warmup=200)
        self.assertAlmostEqual(float(np.nanmean(delayed["hit"])), 1 - alpha, delta=0.035)

    @PROPERTY
    @given(SEED, st.integers(min_value=1, max_value=10), st.data())
    def test_delayed_aci_never_reads_an_error_newer_than_the_delay(self, seed, delay, data):
        """Catches: the newest matured row taken as i - delay + 1 (one outcome before it is observed)."""
        rng = np.random.default_rng(seed)
        e = rng.standard_normal(400)
        i = data.draw(st.integers(min_value=0, max_value=e.size - 1), label="i")
        moved = e.copy()
        moved[max(i - delay + 1, 0):] = rng.standard_normal(e.size - max(i - delay + 1, 0)) * 50 + 7
        base = fe.delayed_aci(e, delay=delay, window=120, warmup=60)
        other = fe.delayed_aci(moved, delay=delay, window=120, warmup=60)
        for key in ("lo", "hi"):
            np.testing.assert_array_equal(base[key][: i + 1], other[key][: i + 1], err_msg=key)

    @PROPERTY
    @given(SEED, st.data())
    def test_adaptive_conformal_interval_uses_only_past_errors(self, seed, data):
        """Catches: the quantile window extended to include the current error, e[i - window: i + 1]."""
        rng = np.random.default_rng(seed)
        e = rng.standard_normal(300)
        i = data.draw(st.integers(min_value=0, max_value=e.size - 1), label="i")
        moved = e.copy()
        moved[i:] = rng.standard_normal(e.size - i) * 50 + 7
        base, other = fe.adaptive_conformal(e), fe.adaptive_conformal(moved)
        for key in ("lo", "hi"):
            np.testing.assert_array_equal(base[key][: i + 1], other[key][: i + 1], err_msg=key)


# ------------------------------------------------------------------------------------------------
# contract_calendar: last trade dates, trading dates, the front contract
# ------------------------------------------------------------------------------------------------

class ContractCalendarProperties(unittest.TestCase):
    @PROPERTY
    @given(CONTRACTS)
    def test_last_trade_date_follows_the_cme_rule(self, contract):
        """Catches: counting back 3 business days even when the 25th is not a business day."""
        ltd = cc.last_trade_date(*contract)
        prior = _previous_contract(*contract)
        the_25th = date(prior[0], prior[1], 25)
        self.assertTrue(cc.is_business_day(ltd))
        self.assertLess(ltd, the_25th)
        self.assertEqual((ltd.year, ltd.month), prior)
        strictly_between = sum(cc.is_business_day(ltd + timedelta(days=k)) for k in range(1, (the_25th - ltd).days))
        self.assertEqual(strictly_between, 2 if cc.is_business_day(the_25th) else 3)

    @PROPERTY
    @given(CALENDAR_DATES)
    def test_trading_date_of_a_date_is_the_first_business_day_on_or_after_it(self, day):
        """Catches: a calendar date returned unchanged (weekend and holiday sessions)."""
        session = cc.trading_date(day)
        self.assertTrue(cc.is_business_day(session))
        self.assertGreaterEqual(session, day)
        self.assertFalse(any(cc.is_business_day(day + timedelta(days=k)) for k in range((session - day).days)))

    @PROPERTY
    @given(st.datetimes(min_value=datetime(1995, 1, 1), max_value=datetime(2099, 12, 31),
                        timezones=st.just(timezone.utc)))
    def test_trading_date_of_a_moment_rolls_at_the_globex_reopen(self, moment):
        """Catches: dropping the 18:00 ET roll (an evening quote assigned to the session that closed)."""
        local = moment.astimezone(cc.EXCHANGE_TZ)
        session = cc.trading_date(moment)
        self.assertTrue(cc.is_business_day(session))
        self.assertGreaterEqual(session, local.date())
        self.assertLessEqual(session - local.date(), timedelta(days=5))
        if local.time() >= time(18, 0):
            self.assertGreater(session, local.date())
        elif cc.is_business_day(local.date()):
            self.assertEqual(session, local.date())

    @PROPERTY
    @given(st.one_of(CALENDAR_DATES, st.datetimes(min_value=datetime(1995, 1, 1), max_value=datetime(2099, 12, 31),
                                                   timezones=st.just(timezone.utc))))
    def test_front_contract_is_the_first_one_still_trading(self, moment):
        """Catches: the roll loop advancing while LTD <= session (the expiring contract dropped on its
        own last trade date)."""
        session = cc.trading_date(moment) if isinstance(moment, datetime) else moment
        front = cc.front_contract(moment)
        self.assertGreaterEqual(cc.last_trade_date(*front), session)
        self.assertLess(cc.last_trade_date(*_previous_contract(*front)), session)

    @PROPERTY
    @given(CONTRACTS)
    def test_a_contract_is_front_through_its_last_trade_date_and_rolls_the_next_session(self, contract):
        """Catches: the same off-by-one roll (<= for <), and a roll one session late."""
        ltd = cc.last_trade_date(*contract)
        self.assertEqual(cc.front_contract(ltd), contract)
        self.assertEqual(cc.front_contract(cc.shift_business_days(ltd, 1)), cc.next_contract(*contract))


# ------------------------------------------------------------------------------------------------
# vol_forecast._build_matrix: features at t use returns <= t; targets use t+1..t+H
# ------------------------------------------------------------------------------------------------

def _returns(rng, n, zero_share):
    r = rng.normal(0.0, 0.02, n)
    r[rng.random(n) < zero_share] = 0.0                  # flat sessions exercise the zero-vol skips
    return r


class FeatureTimingProperties(unittest.TestCase):
    @PROPERTY
    @given(SEED, st.integers(min_value=75, max_value=180), st.floats(min_value=0.0, max_value=0.6),
           st.data())
    def test_features_at_t_ignore_everything_after_t(self, seed, n, zero_share, data):
        """Catches: a feature window that peeks one session ahead (r[: t + 2]), and OVX read at t + 1."""
        rng = np.random.default_rng(seed)
        r = _returns(rng, n, zero_share)
        ovx = rng.uniform(15, 90, n)
        ovx[rng.random(n) < 0.1] = np.nan
        dates = pd.bdate_range("2015-01-02", periods=n)
        t = data.draw(st.integers(min_value=66, max_value=n - 1), label="t")
        r2, ovx2 = r.copy(), ovx.copy()
        r2[t + 1:] = rng.normal(0.0, 0.2, n - t - 1)        # a completely different future
        ovx2[t + 1:] = rng.uniform(100, 300, n - t - 1)
        X, y, _, pos = vf._build_matrix(r, dates, ovx, return_positions=True)
        X2, y2, _, pos2 = vf._build_matrix(r2, dates, ovx2, return_positions=True)
        # A row whose test day is <= t has the same features; its existence and target may depend on
        # the future only through t+1..t+H, so rows <= t - H are identical in every respect.
        rows, rows2 = dict(zip(pos.tolist(), X)), dict(zip(pos2.tolist(), X2))
        for p in set(rows) & set(rows2):
            if p <= t:
                np.testing.assert_array_equal(rows[p], rows2[p], err_msg=f"row {p}, t={t}")
        settled, settled2 = pos <= t - vf.H, pos2 <= t - vf.H
        np.testing.assert_array_equal(pos[settled], pos2[settled2])
        np.testing.assert_array_equal(y[settled], y2[settled2])

    @PROPERTY
    @given(SEED, st.integers(min_value=75, max_value=180), st.data())
    def test_target_at_t_is_the_realized_vol_of_t_plus_1_through_t_plus_h(self, seed, n, data):
        """Catches: a target window that includes the test day (r[t: t + H]) or runs one session past
        the horizon (r[t + 1: t + 2 + H])."""
        rng = np.random.default_rng(seed)
        r = rng.normal(0.0, 0.02, n)
        dates = pd.bdate_range("2015-01-02", periods=n)
        _, y, _, pos = vf._build_matrix(r, dates, None, return_positions=True)
        i = data.draw(st.integers(min_value=0, max_value=len(pos) - 1), label="row")
        p = int(pos[i])
        for k in range(vf.H + 2):
            if p + k >= n:
                continue
            bumped = r.copy()
            bumped[p + k] *= 3.0
            _, yb, _, posb = vf._build_matrix(bumped, dates, None, return_positions=True)
            moved = yb[np.flatnonzero(posb == p)[0]] != y[i]
            self.assertEqual(moved, 1 <= k <= vf.H, f"session t+{k} {'moved' if moved else 'did not move'} the target")


# ------------------------------------------------------------------------------------------------
# vol_lab: the range-based realized measure and the roll calendar
# ------------------------------------------------------------------------------------------------

def _market(rng, start, n, unusable_share, inconsistent_share):
    """A positive OHLC path on business days, with some unusable bars (no positive open/high/low,
    range blanked) and some inconsistent ones (a high between the open and the close, as in raw
    2001-2002 Yahoo data), on which the raw Rogers-Satchell term is negative."""
    dates = pd.bdate_range(start, periods=n)
    close = 70 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    open_ = np.r_[70.0, close[:-1]] * np.exp(rng.normal(0, 0.01, n))
    high = np.maximum(open_, close) * np.exp(np.abs(rng.normal(0, 0.01, n)))
    low = np.minimum(open_, close) * np.exp(-np.abs(rng.normal(0, 0.01, n)))
    bad = rng.random(n) < inconsistent_share
    high[bad] = (open_[bad] + close[bad]) / 2
    low[bad] = np.minimum(open_[bad], close[bad])
    blank = rng.random(n) < unusable_share
    open_[blank] = high[blank] = low[blank] = np.nan
    ohlc = pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close}, index=dates)
    r = np.r_[0.0, np.diff(np.log(close))]
    return dates, ohlc, r, blank


MARKETS = st.tuples(SEED, st.dates(min_value=date(2001, 1, 1), max_value=date(2035, 12, 31)),
                    st.integers(min_value=30, max_value=320), st.floats(min_value=0.0, max_value=0.2),
                    st.floats(min_value=0.0, max_value=0.3))


# The full-bar measure (the range-based target) and the part of it known at the settlement (the
# decision day's term in the features) share the overnight term, the roll handling and the fallback.
RANGE_MEASURES = (lab.range_variance, lab.settlement_range_variance)


class RangeVarianceProperties(unittest.TestCase):
    @PROPERTY
    @given(MARKETS)
    def test_nonnegative_finite_with_the_squared_return_fallback(self, market):
        """Catches: the Rogers-Satchell term used unclipped (negative on an inconsistent bar)."""
        seed, start, n, unusable, inconsistent = market
        dates, ohlc, r, blank = _market(np.random.default_rng(seed), start, n, unusable, inconsistent)
        for measure in RANGE_MEASURES:
            for given_dates in (None, dates):
                v = measure(ohlc, r, given_dates)
                self.assertTrue(np.isfinite(v).all())
                self.assertTrue((v >= 0).all(), v.min())
                np.testing.assert_array_equal(v[blank], r[blank] ** 2)

    @PROPERTY
    @given(MARKETS, POW2)
    def test_invariant_to_the_price_level(self, market, c):
        """Catches: the estimator computed on price levels instead of log prices."""
        seed, start, n, unusable, inconsistent = market
        dates, ohlc, r, _ = _market(np.random.default_rng(seed), start, n, unusable, inconsistent)
        for measure in RANGE_MEASURES:
            np.testing.assert_allclose(measure(ohlc * c, r, dates), measure(ohlc, r, dates), rtol=1e-9, atol=1e-15)

    @PROPERTY
    @given(MARKETS, st.data())
    def test_element_t_uses_only_bars_up_to_t(self, market, data):
        """Catches: the roll-day overnight proxy read from the trailing median shifted the wrong way
        (shift(-1): the next session's gap)."""
        seed, start, n, unusable, inconsistent = market
        rng = np.random.default_rng(seed)
        dates, ohlc, r, _ = _market(rng, start, n, unusable, inconsistent)
        t = data.draw(st.integers(min_value=0, max_value=n - 1), label="t")
        moved, r2 = ohlc.copy(), r.copy()
        moved.iloc[t + 1:, :] = moved.iloc[t + 1:, :].to_numpy() * rng.uniform(0.5, 2.0, (n - t - 1, 4))
        r2[t + 1:] = rng.normal(0, 0.1, n - t - 1)
        for measure in RANGE_MEASURES:
            np.testing.assert_array_equal(measure(ohlc, r, dates)[: t + 1], measure(moved, r2, dates)[: t + 1])

    @PROPERTY
    @given(MARKETS, SEED)
    def test_settlement_part_never_reads_the_high_or_low(self, market, seed2):
        """Catches: the Rogers-Satchell term of the decision day's own bar, whose High and Low run to
        the 17:00 ET close, after the 14:30 ET settlement."""
        seed, start, n, unusable, inconsistent = market
        dates, ohlc, r, _ = _market(np.random.default_rng(seed), start, n, unusable, inconsistent)
        stretch = np.exp(np.abs(np.random.default_rng(seed2).normal(0, 0.05, n)))
        wide = ohlc.assign(High=ohlc["High"] * stretch, Low=ohlc["Low"] / stretch)
        np.testing.assert_array_equal(lab.settlement_range_variance(wide, r, dates),
                                      lab.settlement_range_variance(ohlc, r, dates))

    @PROPERTY
    @given(MARKETS, st.data())
    def test_range_features_at_t_ignore_session_ts_high_and_low(self, market, data):
        """Catches: the range features built from the full-bar measure through day t (the look-ahead
        this construction removes)."""
        seed, start, n, unusable, inconsistent = market
        dates, ohlc, r, _ = _market(np.random.default_rng(seed), start, n, unusable, inconsistent)
        assume(n > 66)
        t = data.draw(st.integers(min_value=65, max_value=n - 1), label="t")
        wide = ohlc.copy()
        wide.iloc[t, wide.columns.get_loc("High")] *= 1.5
        wide.iloc[t, wide.columns.get_loc("Low")] /= 1.5

        def features(bars):
            return lab.range_features(lab.range_variance(bars, r, dates), lab.settlement_range_variance(bars, r, dates), t)

        self.assertEqual(features(wide), features(ohlc))


class RollDayProperties(unittest.TestCase):
    @PROPERTY
    @given(st.dates(min_value=date(2001, 1, 1), max_value=date(2040, 12, 31)),
           st.integers(min_value=2, max_value=500), SEED, st.floats(min_value=0.0, max_value=0.3))
    def test_flags_exactly_the_sessions_where_the_front_contract_changes(self, start, n, seed, missing):
        """Catches: searchsorted(side="left"), which flags the last trade date itself."""
        dates = pd.bdate_range(start, periods=n)
        keep = np.random.default_rng(seed).random(n) >= missing      # sessions missing from the history
        keep[0] = True
        dates = dates[keep]
        mask = lab.roll_days(dates)
        self.assertEqual(mask.shape, (len(dates),))
        self.assertFalse(mask[0])
        days = [d.date() for d in dates]
        expected = [False] + [cc.spans_roll(a, b) for a, b in zip(days[:-1], days[1:])]
        np.testing.assert_array_equal(mask, expected)


if __name__ == "__main__":
    unittest.main()
