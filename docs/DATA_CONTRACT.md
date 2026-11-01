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
- `vol_lab`: `data/vol_lab.json` (backend/vol_lab.py) with `var_backtest` reduced to `{grid0, summary}`.
  Key fields: `sample{n_days,n_weeks,first,last}`, `config{champion,challenger,...}`, `models[]`,
  `leaderboard{models[], mcs_qlike_included[], mcs_mse_included[], mcs_qlike_range_proxy_included[],
  mcs_excludes[], mcs_note, spa_qlike_vs_har_iv{statistic,best_model|null,t_stats{},p_consistent,p_lower,p_upper,
  non_studentized{p_lower,p_consistent,p_upper},models[],note},
  spa_robustness_qlike{block_10,block_40,<years>{n_days,...}: {p_consistent,best_model|null,p_consistent_non_studentized}},
  realized_utility_max_pct, mcs_qlike_by_grid{}, mcs_robustness_qlike{}, n_days, base_rate_pct,
  mean_reversion_acc_pct}`. Each `leaderboard.models[]` row has `timing_control` (true for the
  timing control, `har_iv_sameday`, which `mcs_excludes` lists and `mcs_note` explains); for that
  row the MCS fields (`mcs_qlike_p`, `in_mcs_qlike`, `mcs_mse_p`, `in_mcs_mse`, `in_mcs_qlike_grids`,
  `in_mcs_qlike_range_proxy`) are null, because it is in no confidence set. Also `oos_selection`,
  `var_backtest{grid0{evaluation_weeks,methods[]}, summary{}}`, `conformal{<model>{coverage_pct,...}}`,
  `yearly[]`, `noise_floor`, `har_iv_coefficients{terms[]}`, `vol_cone{windows[], since, as_of,
  implied_ovx}`, `ovx_source_agreement`, `cl_source_agreement` (`CL=F` against EIA's contract 1 and
  Cushing spot: `tolerance_usd`, `contract1{first, last, common_days, share_same_cent,
  share_within_one_tick, misses, misses_by_year, largest_gaps[], eia_sessions_missing_from_cl[],
  since_sample_first}`, `spot{...}`), `roll_convention_audit` (`eia_through, contracts_checked,
  on_lab_roll_day, mismatches[], indeterminate[], roll_days_after_eia`), `generated_from` (the data
  snapshot manifest with SHA-256
  digests), and `series` (weekly, grid 0): `date, actual, rv5, ovx, har_iv, har_iv_median, challenger,
  lo80, hi80, ret5, var95, var99, ovx_elasticity, rv_elasticity` (percent units; elasticities
  unitless).
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
  `inconclusive`), `rows_in_multiplicity_family`, the lists of supported rows surviving each
  adjustment, `selected_after_search` and `supported_selected_after_search` (rows left out of the
  multiplicity family because the model they test was picked by a search of the same sample) and
  `underpowered`; a row is `{id, date, family, hypothesis, test, statistic_name, statistic_value,
  p_value, q_bh, p_holm, survives_bh, clears_t3, pre_specified, status, artifact}`, with `status`
  one of those four keys, and `q_bh`, `p_holm`, `survives_bh` and `clears_t3` null for a row
  selected after a search.

`build_research_bundle.validate` requires the schema version, every `series` array to have
`sample.n_weeks` entries, leaderboard rows with `key` and `qlike`, both post-mortem equity curves,
ledger rows, the supply study's groups, and the leak anatomy's analytic sign agreement.
