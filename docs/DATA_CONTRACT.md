# Dashboard data contract

The static dashboard reads two JSON files from its own origin, plus an optional live quote:

| File | Built by | Refreshed | Contents |
|---|---|---|---|
| `data.json` | `freeze.py` (GitHub Actions `refresh.yml`, build job) | every refresh (scheduled every 4 hours, best effort) | the live snapshot: market quote, chart history, the live volatility forecast, its validation recomputed at the freeze, the live track record |
| `research.json` | `scripts/build_research_bundle.py` (called by `freeze.py`) | every refresh, copied unchanged from the committed artifacts | the research, copied from committed artifacts only |
| `price.json` | `scripts/refresh_live_price.py` (`price.yml`) and `freeze.py` | best effort (scheduled every 15 minutes) | the newest quote, accepted only for the snapshot's contract |

Every statistic on the page is read from these files. Nothing is hardcoded in the frontend.
`scripts/validate_frozen_payload.py` enforces the invariants below before anything is deployed (it
runs `build_research_bundle.validate` on `research.json`).

## `data.json` (schema_version 3)

```jsonc
{
  "schema_version": 3,
  "frozen_at": "2026-10-20T11:00:00+00:00",          // when the snapshot was frozen (UTC)
  "feed_status": "SNAPSHOT",
  "last_update": "...",                               // server render time
  "contract": {                                       // the CL contract the quote belongs to (backend/oil.py)
    "symbol": "CLZ26", "description": "...", "expiry_date": "2026-11-20", "last_trade_date": "2026-11-20",
    "days_to_expiry": 31, "market_time": "2026-10-20T12:09:51Z", "contract_basis": "calendar|quote_match|contract_symbol",
    "quote_symbol": "CL=F", "history_symbol": "CL=F", "market_session_date": "2026-10-20", "quote_session_date": "2026-10-20"
  },
  "current_price": 98.71, "price_change": 0.17, "price_change_percent": 0.17, "price_change_quality": "...",
  "volume": 15800, "volume_display": "15.8K",
  "unified_data": {                                   // price chart history (CL=F continuous, unadjusted)
    "actual": { "values": [], "timestamps": [], "volumes": [], "kinds": [] }   // kinds: "1d" | "1h" | "live"
  },
  "vol_forecast": {                                   // backend/vol_forecast.py forecast_bundle(), schema_version 2
    "schema_version": 2,
    "live": {
      "forecast_next_week_vol_annualized_pct": 47.6,  // variance-consistent (expected) next-5-session vol
      "forecast_median_pct": 40.0,                    // median; decides the direction call
      "prob_vol_rises_pct": 58.3,                     // model-implied P(next 5-session RV > current), not calibrated
      "direction": "RISING|FALLING",
      "current_realized_vol_5d_annualized_pct": 36.8,
      "implied_vol_ovx_pct": 50.7,                    // the (lagged) OVX print the model used
      "interval_80": { "low_pct": 23.3, "high_pct": 63.9, "method": "..." },
      "challenger": { "key": "har_iv_range", "label": "...", "forecast_next_week_vol_annualized_pct": 45.1,
                      "forecast_median_pct": 38.0, "as_of": "..." } | null,
      "challenger_error": null,                       // why the challenger is missing, when it is: a non-empty string or null
      "ovx_error": null,                              // why the first OVX source (or both) failed: a non-empty string or null
      "model": "HAR-IV (RV5,RV22,RV66,OVX)",
      "model_version": "har-iv/2",                    // "har/2" for the plain-HAR fallback forecast when no lagged OVX print exists
      "as_of": "2026-10-19",                          // last completed session the forecast uses
      "issued_at": "2026-10-20T11:11:27Z",
      "inputs": { "rv5_pct": 36.76, "rv22_pct": 36.81, "rv66_pct": 46.96, "ovx": 50.74,
                  "ovx_session": "2026-10-16", "ovx_last_print": "2026-10-19", "ovx_source": "yahoo ^OVX" },
      "coefficients": [], "smearing_factor": 1.41, "residual_sd": 0.41
    },
    "validation": { /* recomputed at every freeze from that run's download, so it is not the committed
                       data/vol_forecast_validation.json, though it has the same fields as its "overall"
                       block: n, sample_start/end, har_qlike, har_qlike_median_forecast, persistence_qlike,
                       har_vs_persistence_qlike_dm, har_mincer_zarnowitz{intercept,slope,slope_se,r2,p_unbiased},
                       ovx_only_qlike, har_vs_ovx_only_qlike_dm(_p_value), robustness_same_day_ovx{qlike,
                       qlike_dm_vs_lagged,dir_acc_pct}, har_dir_acc_pct, majority_class_pct,
                       mean_reversion_dir_acc_pct, har_vs_mean_reversion_z_hac,
                       direction_noise_floor{sims,dir_acc_pct_mean,dir_acc_pct_range,mean_reversion_dir_acc_pct_mean},
                       qlike_by_grid_offset[5], ovx_lag_sessions, ...
                       plus two fields the committed artifact does not have:
                       interval_80_coverage_pct, interval_80_n (the conformal interval's coverage) */ },
    "economic": {                                     // recomputed likewise
      "returns": {}, "vol_target_overlay": {},
      "variance_risk_premium": {                      // also: zero_premium_null{share_positive_pct, mean_premium_vol_pts,
                                                      //   mean_ratio_realized_to_implied_variance, excess_share_pct,
                                                      //   p_value_one_sided, bootstrap{reps, mean_block_periods, seed},
                                                      //   method} and vol_points_caveat (a string)
      },
      "unavailable_reason": "..."                     // present only when the economic tests were skipped (then the
                                                      // other three blocks are absent)
    },
    "recent_oos": { "date": [], "actual": [], "forecast": [], "median": [], "rv5": [], "lo80": [], "hi80": [] },
    "recent_closes": { "date": [], "close": [] },     // the last 40 finished-session CL=F closes (the record scores on these)
    "source_check": {                                 // optional; report only (market_data.freeze_source_check), or
                                                      // {"status": "unavailable", "reason": "<exception class>"}
      "snapshot": { "status": "pass", "first": "2000-08-23", "last": "2026-10-19", "sessions": 6564, "tolerance_usd": 0.01,
                    "n_missing": 0, "n_added": 0, "n_beyond_tick": 0, "dates": [] },
      "spot": { "status": "pass", "tolerance_abs_log_gap": 0.4, "after": "2026-06-15", "sessions_after": 95,
                "spot_last": "2026-10-27", "paired": 90, "max_abs_log_gap": 0.0809, "n_above": 0, "dates": [] }
    },
    "live_record": {                                  // backend/vol_record.py summarize(), added after the freeze
      "started": "2026-10-20",
      "n_recorded": 0, "n_resolved": 0, "n_pending": 0, "n_skipped_roll": 0, "n_expired": 0,
      "n_late_commit": 0,                             // entries not sealed in time: never scored
      "n_independent": 0,                             // resolved entries whose windows do not overlap
      "next_result_due": null,                        // ISO date: earliest target_end among pending entries whose windows do not span a roll; null if none
      "champion": {                                   // the resolved entries of the pinned model version only
        "model_version": "har-iv/2", "n": 0,          // vol_record.PROMOTION_MODEL_VERSION and its resolved entries
        "qlike": null, "interval_80_coverage_pct": null,
        "direction_hit_pct": null, "mean_forecast_pct": null, "mean_realized_pct": null
      },
      "challenger": { "key": "har_iv_range", "qlike": null, "n": 0, "champion_qlike": null },  // descriptive: every resolved entry of the pinned pair
      "promotion": {                                  // vol_record.promotion_test: the single fixed-sample test
        "status": "accruing|promote|keep", "n_independent_paired": 0, "required": 52,
        "dm_stat": null, "p_value": null, "mean_qlike_difference": null,  // null while accruing
        "model_version": "har-iv/2", "challenger_key": "har_iv_range",   // the pinned pair
        "n_excluded_other_version": 0,                // resolved entries left out of the test, by reason
        "n_excluded_other_challenger": 0, "n_excluded_no_challenger": 0
      },
      "chain": { "n_linked": 0, "breaks": [] },     // optional; the hash chain: entries carrying a link, as_of of each broken link
      "recent": [ { "as_of": "...", "issued_at": "...", "model_version": "har-iv/2",
                    "recorded_at": "...", "sealed_at": "...",          // optional; sealed_at null until sealed
                    "base_session": "...",                           // optional; its 14:30 ET settlement opens the window
                    "target_start": "...", "target_end": "...",
                    "forecast_pct": 47.6, "median_pct": 40.0, "lo80_pct": 23.3, "hi80_pct": 63.9,
                    "prob_vol_rises_pct": 58.3,                      // optional, or null
                    "challenger_pct": 45.1, "realized_pct": null,
                    "inputs": { "ovx": 50.74, "ovx_session": "2026-10-16",   // optional; null when the entry
                                "rv5_pct": 36.76, "rv22_pct": 36.81, "rv66_pct": 46.96 },  // stored no inputs
                    "coefficients": [-4.883566, 0.038819, 0.170853, -0.151549, 1.03495],  // optional; the entry's
                    "smearing_factor": 1.4128,                       // stored fit, null for entries recorded before it
                    "spans_roll": false,                             // optional; the window spans a CL roll: never scored
                    "status": "pending|resolved|skipped_roll|expired|late_commit" } ],   // newest first, up to 12
      "promotion_rule": "...",                        // vol_record.PROMOTION_RULE
      "note": "the scoring-window and seal rule", "direction_note": "the direction noise floor"
    }
  },
  "supply_shock_playbook": { /* backend/supply_shock_playbook.py get_playbook_for_api() */ },
  "retracted_direction_model": {                      // the 1-week direction model is not run in production
    "status": "RETRACTED", "reason": "look-ahead leak; purged, the signal is a coin flip",
    "evidence": "research.json postmortem"
  }
}
```

The validator requires, among other checks: a positive quote and a contract symbol; when the quote
carries its exchange time, a chart whose newest market bar is within 5% of it; a finite, positive
forecast, median and
current RV; a direction of `RISING` or `FALLING`; a probability between 0 and 100; an interval that
contains the median; a forecast `as_of` no more than four business days before the quote's session;
a positive `validation.n` and a finite `validation.har_qlike`; and no field of the retracted
direction model. `live.challenger_error`, `live.ovx_error` and `economic.unavailable_reason` may each
be absent, null or a non-empty string; a missing challenger forecast and a skipped economic block
are not errors, but each adds a note (the payload's own reason text is never echoed into the notes,
which CI prints as workflow commands). When `live_record` is present, every count above must be a
non-negative integer, every `recent[].status` one of the five statuses and every
`recent[].model_version` a non-empty string, and `promotion` consistent: `accruing` with fewer than
`required` weeks and no statistics, or a verdict on exactly `required` weeks with a mean difference
(and a statistic with its p-value); `promote` needs a positive mean difference. `promotion` must name
its pair (`model_version`, `challenger_key`: non-empty strings) and give the three excluded counts
(non-negative integers); `champion.model_version` and `challenger.key` must be that pair; and the
resolved entries must add up: `n_resolved = champion.n + promotion.n_excluded_other_version`, and
`champion.n = challenger.n + promotion.n_excluded_other_challenger +
promotion.n_excluded_no_challenger`. `vol_forecast.source_check` is optional and only reports: the
freeze's check of the live `CL=F` closes against the pinned snapshot (status `pass` or
`beyond_tolerance`) and against a fresh EIA Cushing spot series after the pinned spot copy ends
(`pass`, `beyond_tolerance` or `no_spot_rows`, never read as a pass), or `{status: "unavailable",
reason}` with the exception's class name only. Counts are non-negative integers, `dates` at most ten
ISO dates, `max_abs_log_gap` finite or null; a malformed block fails validation, and a finding is
only a note (Health lists it). It is outside `live`, so no sealed record entry carries it.
`chain` is optional (summaries written before the hash chain
omit it); when present, `n_linked` is a non-negative integer no larger than `n_recorded` and
`breaks` a list of ISO dates, empty unless a sealed entry's link is missing or does not match the entry before it. `next_result_due` must be present: an ISO date (`YYYY-MM-DD`)
or null, and null when no entry is pending. The `recent[]` audit fields are optional (summaries
written before they existed omit them) and each may be null; when present, `recorded_at` and
`sealed_at` must be ISO timestamps with a UTC offset, `base_session` an ISO date,
`prob_vol_rises_pct` a percentage, `inputs` an object whose `ovx`, `rv5_pct`, `rv22_pct` and
`rv66_pct` are finite numbers (vol points) or null and whose `ovx_session` is an ISO date or null,
`coefficients` a list of 1 to 8 finite numbers, `smearing_factor` a positive number, and `spans_roll`
a boolean. They repeat what each record entry already stores (`vol_record._recent`), so the
dashboard can show each call's seal time against the settlement that opens its window, how the
inputs moved from one call to the next, and, for entries that stored their fit, recompute each call
and split the change between two calls into input moves and the refit. `spans_roll` is the test resolution skips a window
with (`contract_calendar.spans_roll` on `base_session`..`target_end`), so a pending call that will
never be scored is labelled as such before resolution closes it as `skipped_roll`.

### The live record file

The summary above is computed from `runtime-state/vol_track_record.json` on the `live-data`
branch (`{schema_version: 1, started, entries: [...]}`), which `vol_record.load_record` validates
before any job uses it. Each entry carries `as_of`, `issued_at`, `recorded_at`, `sealed_at` (null
until the publish job's seal), `frozen_at`, `contract`, `model_version`, `model`, `forecast_pct`,
`median_pct`, `prob_vol_rises_pct`, `direction`, `rv5_pct`, `lo80_pct`, `hi80_pct`, `inputs`,
`challenger` (`{key, forecast_pct, median_pct}` or null), the window (`base_session`,
`target_start`, `target_end`), `status` and `realized_pct`. Entries sealed from 2026-11-02 on also
carry `previous_sha256`, set by the seal: the SHA-256 of the sealed part of the entry before it
(`vol_record.entry_digest`), or null for the record's first entry; entries sealed before the chain
do not have the key. Entries recorded from 2.2.0 on also
store the fit that produced the forecast: `coefficients` (the log-log regression's intercept, then
its slopes on ln RV5, ln RV22 and ln RV66 in decimals and ln OVX in index points; four numbers for
the plain-HAR fallback; null when the live block had none) and `smearing_factor` (Duan's, positive,
or null), so that median = 100·exp(b0 + Σ bk·ln xk) and forecast = median·√smearing can be checked
from the sealed entry alone. They are descriptive: recording, sealing, resolution and scoring never
read them, and entries recorded earlier do not have them. A resolved entry adds `resolved_at` and
the six `closes` it was scored on; a `late_commit`, `skipped_roll` or `expired` entry adds
`closed_at` (an expired one also `missing_sessions`). Only an entry sealed more than five minutes
before the settlement that opens its window can be resolved, skipped or expired.

### The shadow field log (unsealed)

`vol_field_record.jsonl` (backend/field_record.py) is JSON Lines, append-only, one canonical object
per line (sorted keys, compact, ASCII, no NaN, each line ending in `\n`), every line with
`sealed: false`. A `forecast` line: `as_of` (a session, `YYYY-MM-DD`), `issued_at`, `appended_at`,
`model_key` (one of `field_record.FIELD_KEYS`, the lab's field without the timing control),
`forecast_pct` and `median_pct` (positive), `inputs_sha256` (the exact inputs, never outputs),
`ovx_source`, `code_sha` (the commit, or null), `spec_sha256` (backend/vol_lab.py and
backend/vol_forecast.py), and the window `base_session`, `target_start`, `target_end`: the record's
rule (`vol_record.target_window`) applied to the later of `issued_at` and `appended_at`, plus the
one-hour commit margin. One line per (`as_of`, `model_key`), one window per `as_of`, sessions never
going back. An `outcome` line: `base_session` (a window a line above opened), `status`, `closes`,
`realized_pct`, `resolved_at`; one per window. `skipped_roll` exactly when the window spans a
contract roll; `resolved` carries the six closes dated from `base_session` to `target_end` and the
`realized_pct` they give, after the last of them settled; `expired` only more than ten sessions
after `target_end`; the other two carry null `closes` and `realized_pct`. Lines are in time order
(`appended_at`, `resolved_at`). The file lives on the `field-data` branch (with the marker
`.initialized-v1` and a README), and the Refresh workflow's `field` job appends to it after each
successful publish; after each push `field_anchor` tries to attest the log in Sigstore's Rekor log
(best effort: a failed attestation leaves that version without an outside timestamp and is a Health
incident). `python backend/field_record.py --validate --log FILE [--previous
EARLIER]` checks all of this from the file alone, and that the earlier copy is a byte prefix; what it
cannot check is which closes the payload held when a window was settled. It is not sealed evidence,
never feeds the promotion test, and no tracked file names it but the docs, its own module and tests,
and the field jobs that keep it (a test checks).

## `research.json` (schema_version 1)

Built from committed artifacts by `scripts/build_research_bundle.py`; see that script for the exact
copy. Nothing in it is computed at deploy time. Top-level keys:

- `schema_version`: 1.
- `vol_lab`: `data/vol_lab.json` (backend/vol_lab.py), copied whole except `var_backtest`, which is reduced
  to `{grid0, summary}`. Key fields: `sample{n_days,n_weeks,first,last}`, `config{champion,challenger,...}`, `models[]`,
  `leaderboard{models[], mcs_qlike_included[], mcs_mse_included[], mcs_qlike_range_proxy_included[],
  mcs_excludes[], mcs_note, spa_qlike_vs_har_iv{statistic,best_model|null,t_stats{},p_consistent,p_lower,p_upper,
  non_studentized{p_lower,p_consistent,p_upper},models[],note},
  spa_robustness_qlike{block_10,block_40,<years>{n_days,...}: {p_consistent,best_model|null,p_consistent_non_studentized}},
  realized_utility_max_pct, mcs_qlike_by_grid{}, mcs_robustness_qlike{}, n_days, base_rate_pct,
  mean_reversion_acc_pct}`. Each `leaderboard.models[]` row has `timing_control` (true for the
  timing control, `har_iv_sameday`, which `mcs_excludes` lists and `mcs_note` explains); for that
  row the MCS fields (`mcs_qlike_p`, `in_mcs_qlike`, `mcs_mse_p`, `in_mcs_mse`, `in_mcs_qlike_grids`,
  `in_mcs_qlike_range_proxy`) are null, because it is in no confidence set. Also `oos_selection`,
  `var_backtest{grid0{evaluation_weeks,methods[]}, summary{}}`, `conformal{<model>{coverage_pct,...}}` (each
  banded model also carries `coverage_by_regime_pct, n_days_by_regime, miss_above_by_regime_pct,
  miss_below_by_regime_pct, interval_score_by_regime, log_interval_score, log_interval_score_by_regime`
  and `conditional_coverage{<split>{wald, df, p_value, hac_lags, cells[{regime, n_days, inside,
  coverage_pct, hac_z, hac_p, n_by_grid[], inside_by_grid[], binomial_p_by_grid[]}], robustness{hac_lags_20,
  hac_lags_40, all_cells, by_grid{hac_lags[], p_value[]}}}}`; each split's counts sum to the model's
  `n_days`, the days with a band), `regimes{what, decision, variables{ovx_tercile, ovx_rv_gap,
  rv5_tercile, target_spans_roll}, n_days_by_regime, cut_points{first, last}, test, note}` (each split's
  counts sum to `sample.n_days`), each `leaderboard.models[]` row's `qlike_by_regime{<split>{<level>}}`,
  each row's `gw_qlike_vs_har_iv` and `fluctuation_qlike_vs_har_iv` and the top-level `stability` (below),
  `yearly[]`, `noise_floor`, `qlike_floor` (the proxy's QLIKE noise floor, never subtracted from a
  loss: `what, horizon_sessions, sims, seed, bar_seed_offset, seeds, rows_per_sim`,
  `five_return{proxy, normal{closed_form, formula, lower_bound, simulated_mean, simulated_range},
  student_t5{df, kurtosis, process, exact, simulated_mean, simulated_range}, kurtosis_matched{df,
  kurtosis, exact, source}, garch_t_dynamics{process, simulated_mean, simulated_range}}`,
  `range{proxy, bar, normal{simulated_mean, simulated_range, proxy_to_true_variance},
  student_t5{...}, data_proxy_to_close_variance}`), `har_iv_coefficients{terms[]}`, `vol_cone{windows[], since, as_of,
  implied_ovx}`, `ovx_source_agreement`, `cl_source_agreement` (`CL=F` against EIA's contract 1 and
  Cushing spot: `tolerance_usd`, `contract1{first, last, common_days, share_same_cent,
  share_within_one_tick, misses, misses_by_year, largest_gaps[], eia_sessions_missing_from_cl[],
  since_sample_first}`, `spot{...}`), `roll_convention_audit` (`eia_through, contracts_checked,
  on_lab_roll_day, mismatches[], indeterminate[], roll_days_after_eia`), `generated_from` (the data
  snapshot manifest with SHA-256
  digests), and `series` (weekly, grid 0): `date, actual, rv5, ovx, har_iv, har_iv_median, challenger,
  lo80, hi80, ret5, var95, var99, ovx_elasticity, rv_elasticity, ovx_rv_gap, ovx_rv_gap_median` (percent
  units; the OVX/RV5 log gap and its trailing median are unitless; elasticities
  unitless), plus the seven stability arrays below.

  Conditional predictive ability and stability over time (`vol_lab.stability_report`) test each model's
  daily QLIKE difference with the model of record, d = QLIKE(`har_iv`) − QLIKE(model), on every
  out-of-sample day. It is positive when the model's loss is lower: the sign of `dm_qlike_vs_har_iv`. Both
  tests share one null, the conditional one: given what was known at each decision, the two forecasts have
  equal expected QLIKE at every date (so d has no autocorrelation beyond the 5-session overlap of the
  targets). Both tests' theory assumes forecasts re-estimated on rolling windows of bounded size, which is
  what lets it cover nested models. The lab's windows expand, and with expanding windows the usual limits
  fail for nested models (HAR-RV and the OVX-only regression are nested in HAR-IV), so every p-value here is
  approximate. Nothing else in the lab reads these fields: not the challenger's choice, the band or the
  leaderboard.
  - `leaderboard.models[].gw_qlike_vs_har_iv{stat, df, p_value, n_days, lags, kernel, conditional{stat, df,
    p_value}, coef_levels{constant, d_lag, ln_ovx, ln_rv5}, robustness{bartlett_10{p_value,
    conditional_p_value}, bartlett_40{p_value, conditional_p_value}, by_grid{p_value[5],
    conditional_p_value[5]}}}`, null for `har_iv` (as its DM fields are): Giacomini-White (2006). The
    top-level `stat`, `df` and `p_value` are the χ²(4) test with a constant. With the constant it mostly
    repeats the DM test, so it is context and not in the ledger. `conditional` uses the same instruments
    without the constant, around their means (χ²(3); a constant shift of d does not change it): its
    `p_value` is the ledger's. Both use an uncentred HAC matrix (`kernel`, `lags`) on the `n_days` rows that
    have a matured lag of d. `coef_levels` is the OLS fit of d on a constant, d of 5 sessions earlier, ln
    OVX and ln RV5: descriptive, with no test per coefficient. `robustness` repeats both p-values with
    Bartlett kernels at 10 and 40 lags, and on each weekly grid alone (`by_grid`: 0 lags, the lag of d being
    the grid's previous row). It is context, not entered in the ledger. `stat` is rounded to 3 dp and `coef_levels` to
    4 dp; p-values are full floats. When either main HAC matrix (truncated kernel; the full test's or the
    conditional part's) is not positive definite, `stat`, `p_value`, `conditional.stat` and
    `conditional.p_value` are null, `robustness` holds only the two Bartlett entries with null p-values,
    `coef_levels` and `by_grid` are absent, and `not_computed` names the reason. A Bartlett variant that
    fails alone has null p-values and its own `not_computed`; a grid that fails has null entries.
  - `leaderboard.models[].fluctuation_qlike_vs_har_iv`, null for `har_iv`: Giacomini-Rossi (2010), as a
    rolling DM t over windows of `stability.fluctuation.window_sessions` days. Each window is divided by its
    own long-run variance (that block's `kernel` and `lags`, around the window's mean) and dated at its last
    day. `sup`, `sign` (+1 where the model's loss is lower) and `at`: the largest |t|, its sign and its
    window's last day; `window_start`: that window's first day. `p_value`: this window length's p against
    the simulated null, (1 + draws at or above `sup`) / (draws + 1). `rejects_5pct` and `rejects_10pct` are
    `p_value` < 0.05 and < 0.10, so `rejects_5pct` agrees with the band
    ±`stability.fluctuation.critical_values.five_pct` by construction; `rejects_5pct_gr2010` compares `sup`
    with Giacomini and Rossi's Table 1 value instead. `search_adjusted{over_mu, min_p, at_mu, p_value}`: the
    smallest p over the window lengths in `over_mu` (each a share of the out-of-sample days), the share that
    gave it, and that minimum calibrated on the same simulation; this `p_value` is the ledger's. `max`,
    `max_at`, `min`, `min_at`: the path's extremes and their window ends; `crosses_both_sides`: the path
    leaves the band on both sides. `outside_5pct[{from, to, sign, sessions, week_from, week_to}]`: each run
    of window ends outside the band, with its first and last window end, its sign and its length in
    sessions. `week_from` and `week_to` index the `series` arrays: the last weekly date at or before the
    run's first window end, and the first at or after its last (or the last weekly date), never before the
    first non-null `fluct_t_*` value. They snap outward, so a run that falls between two weekly dates maps
    to the segment between them. Each window is scaled by its own variability, so the peak is where the difference was most consistent,
    not where it was largest: `peak_window_mean_diff` is the mean daily d in the peak's window, and
    `largest_same_sign_window{mean_diff, window_end, t}` is the window with the largest mean of the peak's
    sign. `by_mu{mu_0_1, mu_0_3, mu_0_4, mu_0_5}`: the other window lengths, each `{window_sessions, sup,
    sign, at, p_value, rejects_5pct}` with its own p (context; the shortest window's p is anti-conservative
    under the null). `robustness{bartlett_40, full_sample_variance}`: the same window length with each
    window's Bartlett variance at 40 lags, and with Giacomini and Rossi's single full-sample variance, each
    `{sup, sign, at, p_value, rejects_5pct}`; `robustness.by_grid{signed_sup[5], p_value[5], outside_5pct}`:
    each weekly grid alone, against its own null (`stability.fluctuation.grid`), with the signed peak t, its
    p and the number of grids with p < 0.05. The robustness is context, not entered in the ledger. `sup`, `max`, `min`
    and the `by_mu` and robustness sups are rounded to 3 dp, window means to 4 dp,
    `largest_same_sign_window.t` and `signed_sup` to 2 dp; p-values are unrounded. When one window's
    variance, at any of the five window lengths, is not positive, these fields are null, every field the
    ledger reads among them: `sup`, `sign`, `at`, `window_start`, `p_value`, the three `rejects_*`,
    `search_adjusted.min_p`, `.at_mu` and `.p_value`, each `by_mu.<mu>.p_value`,
    `robustness.bartlett_40.p_value`, `robustness.full_sample_variance.p_value` and
    `robustness.by_grid.outside_5pct`. Each `by_mu.<mu>` keeps only `window_sessions` and `p_value`, each
    `robustness` entry only those nulls, the path fields from `max` to `largest_same_sign_window` are
    absent, and `not_computed` names the reason. A robustness variance that fails alone has a null
    `p_value` and its own `not_computed`; a grid that fails has null entries.
  - `stability{differential, null, gw, fluctuation, cumulative, series}`: the settings and reference values
    of both tests. `differential` and `null` state d's sign and the shared null in words (the simulation is
    `fluctuation.null`). `gw{instruments[], d_lag_sessions, lags, kernel, variance, n_days, first,
    conditional, registered, coef_levels, robustness[]}`: the instruments (all known at the decision), the
    lag of d, the HAC settings, the rows tested and the first of them, and in words the conditional part,
    what the ledger enters, the levels fit and the robustness variants. `fluctuation{mu, window_sessions,
    n_days, first_window_end, lags, kernel, variance, dated, two_sided, critical_values{five_pct, ten_pct,
    mc_se_five_pct}, null{draws, seed[], method}, search{over_mu[], method}, gr2010_table1{five_pct,
    ten_pct}, context{mu_0_1, mu_0_3, mu_0_4, mu_0_5}, robustness{bartlett_40, full_sample_variance},
    grid{window_weeks, lags, critical_values_five_pct{<grid length>}}}`: the drawn window (`mu` of the
    `n_days` out-of-sample days, `window_sessions` long, the first ending on `first_window_end`) and its
    variance. `critical_values` are the two-sided 5% and 10% values of sup |t| from the simulated null at
    this sample's length, rounded to 4 dp so that the band and `rejects_5pct` stay consistent;
    `mc_se_five_pct` is the Monte Carlo standard error of the 5% value. `null`: the number of draws, the
    seed (`[vol_lab.SEED, 23, n_days]`, fixed before its values were computed) and the method, one set of
    Gaussian random walks for every window length. `search`: the window lengths the adjusted p covers, and
    how. `gr2010_table1`: Giacomini and Rossi's published Table 1 values, for comparison.
    `context.<mu>{window_sessions, critical_values{five_pct, ten_pct}, gr2010_table1{five_pct, ten_pct}}`:
    the same for the other window lengths. `robustness`: the two variance variants in words. `grid`: the
    per-grid test's window in weeks, its lags and its 5% value for each grid length (keys are the lengths as
    strings; each length has its own null). `cumulative{end_date, end_all_days{challenger, ovx_only,
    raw_ovx, persistence}}`: the sum of d over every out-of-sample day up to `end_date`, at full precision.
    It equals `sample.n_days` × the compared model's `dm_qlike_vs_har_iv.mean_diff` up to floating-point
    rounding, and no test reads it. `series{fluct_t[], cum_qlike_diff[]}`: the comparisons behind the seven
    series arrays.
  - The seven `series` arrays, one value per weekly date: `fluct_t_{challenger, ovx_only, raw_ovx}`, the t
    of the drawn window ending on that date (2 dp; null before the first full window, and throughout for a
    test not computed), and `cum_qlike_diff_{challenger, ovx_only, raw_ovx, persistence}`, the every-day sum
    of d up to that date (3 dp). The last weekly date can fall before `stability.cumulative.end_date`, so
    the last value can differ from `end_all_days`. In these names, and in `stability.cumulative` and
    `stability.series`, `challenger` is the lab's challenger (`config.challenger`), not a fixed model key.
- The whole predictive distribution (`vol_lab.distribution_report`; ROADMAP 2.4):
  `distribution{config, n_days, first, last, weeks_per_grid[], models[], mcs_crps, quantile_har_field}`.
  Every score is on the `n_days` days the interval band is scored on (`conformal.<model>.n_days`), not
  on every out-of-sample day. Scores are in vol points (annualized vol × 100).
  - `config`: `taus[]` (the 19 levels), `pinball_taus[]`, `crps` and `scale` in words,
    `gaussian_ratio_k19` (the 19-level score over the closed-form CRPS under a correct normal forecast),
    `residual_window_days, delay_sessions, warmup_days` (the trailing quantiles), `garch{method, draws,
    sequence, seed[], pit}`, `ewma`, `quantile_har{design, estimator, refit_weeks, rearranged,
    point_forecast}`, `berkowitz{per, form, combine, reference{trailing_rank, parametric, quantile_har},
    null{draws, seed[], returns, lr_mean_by_grid[], lr_q95_by_grid[]}, null_size_pct{simulated, chi2}}`
    (`null_size_pct`: the share of the null draws each rule rejects at 5%, each draw against all draws for
    the simulated reference), `dm{registered, groups, context[]}`, `pit_bins`, `chance_band`.
  - `models[]`, in the order model of record, then `DISTRIBUTION_MODELS` in `backend/research_ledger.py`
    (challenger, `ovx_only`, `garch`, `gjr`, `ewma`, `quantile_har` today; `garch` and `gjr` only with the
    slow walk, whose parameter columns they need; without them `mcs_crps` is null, and `quantile_har_field` needs every model's column, so the block as published needs the slow walk; the
    quantile HAR is never in `leaderboard`): `key, label, method,
    pit_construction` (`trailing_rank`, `exact` or `estimated_quantiles`), `crps_grid_pct, crps_grid_log,
    pinball_10_pct, pinball_90_pct` (3 dp; the log score 4 dp), `coverage_10_90_pct` (1 dp),
    `mae_median_pct` (the CRPS a point forecast at the predictive median would score), `pit_decile_share_pct[10]`
    (the share of days between consecutive predicted deciles, 1 dp; descriptive, no bin is tested),
    `pit_chance_center_pct[10]` and `pit_chance_band_pct[10]` (the null mean of each share and 1.96 times its
    standard deviation across the first 2,000 null draws, for the model's PIT construction), and
    `dm_vs_har_iv` (null for the model of record): `{crps_grid, pinball_10, pinball_90}` each `{stat,
    p_value, groups, mean_diff, periods_model_lower, periods_har_iv_lower, period_means[], groups_8{stat,
    p_value}, groups_16{...}, dm_test{stat, p_value, lags}, nw94{lags, stat, p_value}, by_grid[{stat,
    p_value}], grid_bonferroni_p, acf{5, 10, 20, 40}}` with `concentration{share_2020_pct,
    share_largest_1pct_days_pct}` on `crps_grid` (shares of the total difference; meaningless when the
    total is near zero), plus `crps_grid_log{stat, p_value, dm_test{stat, p_value}}`. `stat` and `p_value`
    are the ledger's batch-means test; d = the model of record's score − the model's (positive: the
    model scores lower). `berkowitz{registered` (`lr3` or `censored`), `reference, p_value` (Bonferroni over
    the grids), `p_cauchy, grids_below_5pct, by_grid[{grid, n_weeks, lr, p_value, mu, sigma` and `rho`, or
    `below` and `above` for the censored form; `p_chi2` for a trailing-rank PIT`}]}`; a trailing-rank model
    adds `mc_se, p_chi2` (the χ²(3) Bonferroni, context) and `window_exceedances{days, null_mean,
    null_q95}`; the GARCH models (`PARAMETRIC_DISTRIBUTION_MODELS`) add `p_count_pit, clamp_days, extreme_days`; the quantile HAR adds
    `randomized_tail{p_value, seed[], seed_min, seed_max, seeds, share_below_5pct}`. The two banded models
    add `band_split{pinball_lower_pct, pinball_upper_pct, interval_score_pct}` (10 × the sum is the band's
    interval score), the GARCH models `alternatives{static_t{crps_grid_pct, berkowitz_p}}`, and the
    quantile HAR `quantile_har{refits, crossing_days_oos, crossing_days_evaluation, tail_days}`.
  - `mcs_crps{block_20, block_40, block_80{included[], p_values{}}, note}`: a Model Confidence Set on the
    daily grid CRPS (context; null without the GARCH models).
  - `quantile_har_field`: the QLIKE comparisons rerun with the quantile HAR's point forecast added to the
    feasible field, on every out-of-sample day (context; nothing reads it): `n_days, qlike,
    dm_qlike_vs_har_iv{batch_means{stat, p_value}, dm_test{stat, p_value, lags}, nw94{lags, p_value},
    by_grid_p[]}, mcs{included[], p_values{}}, mcs_block_10, mcs_block_40{included[], p_quantile_har},
    mcs_by_grid{<grid>{included[], p_quantile_har}}, mcs_<first year>_<last year>{n_days, included[],
    p_quantile_har}` (two sub-periods, as in `mcs_robustness_qlike`), `spa{p_consistent, best_model,
    models[]}, alt_point_forecast{method, qlike, batch_means_p, dm_test_p, mcs_included, mcs_p}, note`.
- `postmortem`: `five_year` and `ten_year`, each `{purged, leaked, config}`; a run is
  `{n, direction_acc_pct, p_value, sharpe, total_pnl_usd, max_drawdown_usd, mae_vs_best_baseline_pct, first, last,
  equity: [[date, cumulative_pnl_usd], ...]}`.
- `carry`: `data/carry_signal_test.json` `{method, full_sample, pre_2007, post_2007, verdict, sign_split,
  power_post_2007, regime_change_test, field_notes}`; each sample block carries its own
  `sharpe_difference` test.
- `leak_anatomy`: `data/leak_anatomy.json` `{design, analytic, experiments{<data>{<model>{unpurged,
  purged}}}, retracted_reference, conclusion}` without the per-seed runs; each arm keeps `n_runs,
  accuracy_pct_mean, accuracy_pct_range, sharpe_mean, sharpe_range, pooled_binomial_p_value_vs_50,
  base_rate_pct_mean, runs_beating_base_rate_5pct` where the artifact has them. `design.sharpe` says
  the experiments' Sharpe ratios are gross (no costs) and `retracted_reference.sharpe_basis` that the
  retracted model's is after costs. Plus flat headline fields for the synthetic random walk:
  `headline_model, unpurged_dir_acc_pct_mean, purged_dir_acc_pct_mean, unpurged_sharpe_mean, sims,
  analytic_sign_agreement_pct`.
- `supply_study`: `data/supply_shock_study.json` `{method, placebo, groups, between_groups, priced_in,
  overlapping_events, superseded_claims, conclusion}`.
- `ledger`: `data/research_ledger.json` `{method, alpha, hlz_t, summary, rows[]}`; `summary` includes
  `by_status` and each `by_family.<family>.by_status` (keys `supported`, `not_supported`, `retracted`,
  `inconclusive`, `registered`), `rows_in_multiplicity_family`, the lists of supported rows surviving each
  adjustment, `selected_after_search` and `supported_selected_after_search` (rows left out of the
  multiplicity family because the model they test was picked by a search of the same sample),
  `underpowered`, `rows_tested` (`rows` less every row with status `registered`, pending or withdrawn),
  `registered` (only the registered rows still waiting for a result), `registered_withdrawn` (registered
  rows withdrawn before a result existed), `registered_before_result` (every row with a registration,
  whatever its status) and `online`; a row is `{id, date, family, hypothesis, test, statistic_name,
  statistic_value, p_value, q_bh, p_holm, survives_bh, clears_t3, pre_specified, status, artifact,
  entered_on, online_position, online_alpha_lordpp, online_rejected_lordpp, online_alpha_saffron,
  online_rejected_saffron, registration}`, with `status` one of those five keys, and `q_bh`, `p_holm`,
  `survives_bh` and `clears_t3` null for a row selected after a search. `date` is the source artifact's
  own date (for `vol_lab` rows, the pinned snapshot's retrieval date, which can precede the day the test
  was added or registered; null for a registered row without a result); the order rows entered the ledger is `entered_on`, the UTC date of the merge to main that first
  held the row, or its registration date. The online view (ROADMAP 2.12) runs LORD++ and SAFFRON at a
  target false discovery rate of 0.05 over the rows in that order: `online_position` is the row's place
  in the stream (1-based), and each procedure's `online_alpha_*` is the level the row was tested at and
  `online_rejected_*` its decision. The online fields are null outside the stream (a row selected after a
  search, whatever its status, a registered one included: what follows is for the rows in the stream).
  While a registered row waits for its result (`registration.withdrawn` null), its
  `online_rejected_*` is null, and its `online_alpha_*` is published, fixed before the result exists, once
  every earlier row is decided; every later row's level and decision are null until it resolves.
  `online_rejected_*` is false for a retracted row and for a withdrawn registration, which are tested at
  p = 1 with their level set once every earlier row is decided (after a pending row both are null, as
  every later row's are), and a withdrawn registration does not hold up later rows. `registration`
  is `{on, field, withdrawn}` (the date, the artifact field that will hold the result, and a withdrawal
  reason) or null. A registered row's result exists once `registration.field` holds a value, or a null
  that the row's test reads as optional (a test the artifact ran and could not compute, as the lab's
  `not_computed` fluctuation fields: the row then takes the status of any row whose test could not be
  computed, `inconclusive`, or `not_supported` when its stored estimate points the wrong way, with a
  null `p_value`, and, unless it was selected after a search, is tested online at p = 1); until then, or while its artifact file is missing,
  the row is pending. A
  registered row, pending or withdrawn, has a null `p_value`, `q_bh`, `p_holm`, `survives_bh` and
  `clears_t3`; its online decision is null while it is pending, and false once it is withdrawn and every
  earlier row is decided (null while it sits after a pending row). The full artifact also has, per row,
  `online_p` (the p the row is tested on: `p_two_sided`, or 1 for a retracted row, a row whose test could
  not be computed and a withdrawn registration), `online_candidate_saffron` (`online_p` ≤ λ; null for a pending row, whose `online_p` is null (the
  first pending row's levels are published), and, like the levels and decisions, for every row after a
  pending one) and
  `registration.spec_sha256` (the hash of the declared test). `summary.online` is `{alpha, reference,
  order, rows_in_stream, rows_decided, waiting_on, legacy_batches[{entered_on, pr, rows_in_stream}],
  lordpp{w0, gamma_c, gamma, rejected[], wealth[], next_level}, saffron{w0, lambda, gamma_c, gamma_s,
  candidates, rejected[], wealth[], next_level}, legacy_order_sensitivity{draws, seed, scope,
  legacy_rows, lordpp{min, max, same_set_share}, saffron{min, max, same_set_share}}}`: `waiting_on` names
  the first registered row in the stream without a result (null when none), `wealth` runs over the decided rows,
  `next_level` is the level a row appended now would be tested at (null while a row waits), and
  `legacy_order_sensitivity` summarises `draws` random within-batch orders (a sample with a fixed seed:
  its range and `same_set_share`, the share of draws that give the published rejection set, are
  estimates, and the published order need not be among the draws). `vol_lab`'s
  keys named `registered` (`gw_*.registered`, `dm.registered`, `berkowitz.registered`) name the form the
  ledger reads and are unrelated to the status. Two families test each model against the model of record, one row per
  model: "conditional predictive ability" (`vol_gw_<model>`, the Giacomini-White conditional p) and
  "stability over time" (`vol_fluct_<model>`, the fluctuation test's search-adjusted p). The χ²(4)
  Giacomini-White form and every robustness variant stay in `vol_lab` as context; they are not rows.
  Two families score the whole predictive distribution (ROADMAP 2.4): "predictive distribution"
  (`vol_crps_<model>`, `vol_pinball10_<model>`, `vol_pinball90_<model>`, each model against the model of
  record by the batch-means test, `distribution.models[key=<model>].dm_vs_har_iv.<score>`) and
  "distribution calibration" (`vol_berkowitz_<model>`, the model of record included, Bonferroni over the
  five grids' `berkowitz.by_grid[].p_value`). The quantile HAR has rows in both but is never in the
  benchmark field. The rows that test the challenger, which was picked on this sample, are outside the
  family: its full-sample, conditional and over-time rows (`vol_challenger_qlike`,
  `vol_gw_har_iv_range` and `vol_fluct_har_iv_range`), its three score rows (`vol_crps_har_iv_range`,
  `vol_pinball10_har_iv_range`, `vol_pinball90_har_iv_range`) and any challenger row a later family
  registers, all listed in `selected_after_search`. A row whose artifact could not
  compute its test (a stored p-value of null) has a null `p_value`, is `inconclusive` (`not_supported`
  when its stored estimate points the wrong way), and is outside the family too, with null `q_bh`, `p_holm`, `survives_bh` and `clears_t3`. Rows on the same loss
  difference are dependent: Benjamini-Hochberg assumes positive dependence (PRDS) and Holm holds under
  any dependence, and strongly significant near-duplicate rows lower other rows' q-values.

`build_research_bundle.validate` requires the schema version, every `series` array to have
`sample.n_weeks` entries, leaderboard rows with `key` and `qlike`, both post-mortem equity curves,
ledger rows (and, when `ledger.summary.online` exists, an `online_position` key on every ledger row),
the supply study's groups, and the leak anatomy's analytic sign agreement.
