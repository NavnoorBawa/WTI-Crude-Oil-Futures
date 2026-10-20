# Dashboard data contract

The static dashboard reads two JSON files from its own origin, plus an optional live quote:

| File | Built by | Refreshed | Contents |
|---|---|---|---|
| `data.json` | `freeze.py` (GitHub Actions `refresh.yml`, build job) | every refresh (~4–6 h) | the live snapshot: market quote, chart history, the live volatility forecast, its validation, the live track record |
| `research.json` | `scripts/build_research_bundle.py` (called by `freeze.py`) | when an artifact changes | the reproducible research, copied from committed artifacts only |
| `price.json` | `scripts/refresh_live_price.py` (`price.yml`) and `freeze.py` | best effort | the newest quote, accepted only for the snapshot's contract |

Every statistic on the page is read from these files. Nothing is hardcoded in the frontend.

## `data.json` (schema_version 3)

```jsonc
{
  "schema_version": 3,
  "frozen_at": "2026-10-20T11:00:00+00:00",          // when the snapshot was frozen (UTC)
  "feed_status": "SNAPSHOT",
  "last_update": "...",                               // server render time
  "contract": {                                       // the CL contract the quote belongs to
    "symbol": "CLZ26", "description": "...", "expiry_date": "2026-11-19", "last_trade_date": "2026-11-19",
    "days_to_expiry": 30, "market_time": "2026-10-20T08:16:36Z", "contract_basis": "calendar|quote_match|contract_symbol",
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
      "prob_vol_rises_pct": 58.3,                     // P(next 5-session RV > current 5-session RV)
      "direction": "RISING|FALLING",
      "current_realized_vol_5d_annualized_pct": 36.8,
      "implied_vol_ovx_pct": 50.7,                    // the (lagged) OVX print the model used
      "interval_80": { "low_pct": 23.3, "high_pct": 63.9, "method": "..." },
      "challenger": { "key": "har_iv_range", "label": "...", "forecast_next_week_vol_annualized_pct": 45.1,
                      "forecast_median_pct": 38.0, "as_of": "..." } | null,
      "model": "HAR-IV (RV5,RV22,RV66,OVX)", "model_version": "har-iv/2",
      "as_of": "2026-10-19",                          // last completed session the forecast uses
      "issued_at": "2026-10-20T11:11:27Z",
      "inputs": { "rv5_pct": 36.76, "rv22_pct": 36.81, "rv66_pct": 46.96, "ovx": 50.74,
                  "ovx_session": "2026-10-16", "ovx_last_print": "2026-10-19", "ovx_source": "yahoo ^OVX" },
      "coefficients": [], "smearing_factor": 1.41, "residual_sd": 0.41
    },
    "validation": { /* data/vol_forecast_validation.json "overall": n, sample_start/end, har_qlike,
                       har_qlike_median_forecast, persistence_qlike, har_vs_persistence_qlike_dm,
                       har_mincer_zarnowitz{intercept,slope,slope_se,r2,p_unbiased}, ovx_only_qlike,
                       har_vs_ovx_only_qlike_dm(_p_value), robustness_same_day_ovx{qlike,qlike_dm_vs_lagged,dir_acc_pct},
                       har_dir_acc_pct, majority_class_pct, mean_reversion_dir_acc_pct, har_vs_mean_reversion_z_hac,
                       direction_noise_floor{sims,dir_acc_pct_mean,dir_acc_pct_range}, qlike_by_grid_offset[5],
                       interval_80_coverage_pct, interval_80_n, ovx_lag_sessions, ... */ },
    "economic": { "returns": {}, "vol_target_overlay": {}, "variance_risk_premium": {} },
    "recent_oos": { "date": [], "actual": [], "forecast": [], "median": [], "rv5": [], "lo80": [], "hi80": [] },
    "live_record": {                                  // backend/vol_record.py summary (see below)
      "started": "2026-10-20", "n_recorded": 0, "n_resolved": 0, "n_pending": 0, "n_skipped_roll": 0, "n_independent": 0,
      "champion": { "model_version": "har-iv/2", "qlike": null, "interval_80_coverage_pct": null,
                    "direction_hit_pct": null, "mean_forecast_pct": null, "mean_realized_pct": null },
      "challenger": { "key": "har_iv_range", "qlike": null },
      "recent": [ { "as_of": "...", "issued_at": "...", "target_start": "...", "target_end": "...",
                    "forecast_pct": 47.6, "median_pct": 40.0, "lo80_pct": 23.3, "hi80_pct": 63.9,
                    "challenger_pct": 45.1, "realized_pct": null, "status": "pending|resolved|skipped_roll" } ],
      "promotion_rule": "..."
    }
  },
  "supply_shock_playbook": { /* backend/supply_shock_playbook.py get_playbook_for_api() */ },
  "retracted_direction_model": {                      // the 1-week direction model is not run in production
    "status": "RETRACTED", "reason": "look-ahead leak; purged, the signal is a coin flip",
    "evidence": "research.json postmortem"
  }
}
```

## `research.json` (schema_version 1)

Built from committed artifacts by `scripts/build_research_bundle.py`; see that script for the exact
copy. Top-level keys:

- `vol_lab`: `data/vol_lab.json` (backend/vol_lab.py) with `var_backtest` reduced to `{grid0, summary}`.
  Key fields: `sample{n_days,n_weeks,first,last}`, `config{champion,challenger,...}`,
  `leaderboard{models[], mcs_qlike_included[], mcs_qlike_by_grid{}, mcs_robustness_qlike{}, spa_qlike_vs_har_iv{},
  base_rate_pct, mean_reversion_acc_pct, realized_utility_max_pct, mcs_qlike_range_proxy_included[]}`,
  `oos_selection`, `var_backtest{grid0{evaluation_weeks,methods[]}, summary{}}`, `conformal{<model>{coverage_pct,...}}`,
  `yearly[]`, `noise_floor`, `har_iv_coefficients{terms[]}`, `vol_cone{windows[], implied_ovx}`,
  `ovx_source_agreement`, `generated_from` (the data snapshot manifest with SHA-256 digests), and
  `series` (weekly, grid 0): `date, actual, rv5, ovx, har_iv, har_iv_median, challenger, lo80, hi80, ret5,
  var95, var99, ovx_elasticity, rv_elasticity` (percent units; elasticities unitless).
- `postmortem`: `five_year` and `ten_year`, each `{purged, leaked, config}`; a run is
  `{n, direction_acc_pct, p_value, sharpe, total_pnl_usd, max_drawdown_usd, mae_vs_best_baseline_pct, first, last,
  equity: [[date, cumulative_pnl_usd], ...]}`.
- `carry`: `data/carry_signal_test.json` `{method, full_sample, pre_2007, post_2007, verdict}`.
