# Datasheet: pinned market-data snapshot (`data/market/`)

After Gebru et al. (2021), *Datasheets for Datasets*. Generated facts come from
`data/vol_lab.json` (`scripts/readme_blocks.py`, checked in CI).

## Motivation

The benchmark field (`data/vol_lab.json`) must reproduce offline, years from now. Yahoo Finance
revises history (holiday-dated partial bars come and go, and past closes of the continuous series
have changed between downloads), so the lab runs on a frozen copy whose SHA-256 digests are pinned
in `MANIFEST.json`. `backend/market_data.load_snapshot()` refuses any file that does not match.

The snapshot pins the Yahoo and FRED market data, and copies of EIA's contracts 1 and 2 and
Cushing spot that serve only as a second source for the `CL=F` closes and rolls (no model reads
them). The carry and supply-shock studies read the original EIA files
(`data/eia_wti_futures_curve.json`, `data/eia_wti_spot_daily.json`). The production validation
(`data/vol_forecast_validation.json`) and the live pipeline download fresh data and do not read
this snapshot.

## Composition

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("provenance_table") ]]] -->

| File | Rows | First | Last | SHA-256 | Source |
|---|---:|---|---|---|---|
| `data/market/cl_f_daily.csv` | 6,564 | 2000-08-23 | 2026-10-19 | `2e0c57f697484b46…` | Yahoo Finance CL=F daily OHLC (yfinance), finished NYMEX sessions only |
| `data/market/ovx_yahoo_daily.csv` | 4,892 | 2007-05-10 | 2026-10-19 | `b641a9753df21f21…` | Yahoo Finance ^OVX daily close (yfinance), finished sessions only |
| `data/market/ovxcls_fred.csv` | 4,893 | 2007-05-10 | 2026-10-16 | `2117700ea77ca6b1…` | FRED OVXCLS (https://fred.stlouisfed.org/series/OVXCLS), keyless fredgraph CSV |
| `data/market/eia_rclc_daily.csv` | 9,492 | 1985-01-02 | 2024-04-05 | `658cd031d5aab815…` | EIA NYMEX WTI futures settlement, contracts 1 and 2 (RCLC1, RCLC2), final through 2024-04-05; from data/eia_wti_futures_curve.json, a second source only |
| `data/market/eia_rwtc_daily.csv` | 10,186 | 1986-01-02 | 2026-06-15 | `99490110a75be443…` | EIA Cushing WTI spot price (RWTC); from data/eia_wti_spot_daily.json, a second source only |
<!-- [[[end]]] -->

- `cl_f_daily.csv`: Date, Open, High, Low, Close (USD per barrel), Volume (contracts). One row per
  finished NYMEX session; `CL=F` is Yahoo's continuous front-month series, **unadjusted**: it
  switches contracts the session after each last trade date and books the calendar spread as a
  return on that day. The Close is the 14:30 ET settlement; the High and Low cover the session to
  its 17:00 ET close, after the settlement; the Open is the session's first trade, the evening
  before.
- `ovx_yahoo_daily.csv`: the Cboe Crude Oil ETF Volatility Index close (annualized percent), the
  series the production model uses.
- `ovxcls_fred.csv`: the same index as redistributed by FRED, used only as an independent check.

## Collection and cleaning

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("data_quality_block") ]]] -->

- `CL=F` sessions kept: 6,564. Bars with a non-positive open, high or low (the session after the 2020-04-20 negative settlement): 1, close kept, range blanked. Bars whose high was below the open or close: 3; whose low was above them: 4 (repaired by widening the range; closes are never changed).
- OVX, Yahoo vs FRED: 4,891 common days, 99.88% within 0.05 points, correlation 0.999996, largest differences 2009-11-27 (Yahoo 40.73, FRED 43.36), 2010-04-27 (Yahoo 30.34, FRED 28.02), 2014-12-09 (Yahoo 38.53, FRED 37.56).
- `CL=F` vs EIA's contract-1 settlement (RCLC1, the front month, final through 2024-04-05): 5,805 common sessions from 2000-08-23, 96.28% equal to the cent and 96.92% within one tick ($0.01); 179 sessions further apart (2000: 55, 2001: 46, 2002: 74, 2004: 1, 2006: 1, 2010: 2), the largest 2010-10-11 (Yahoo 82.21, EIA 81.33). In the scored sample's sessions EIA covers (2008-05-08 to 2024-04-05): 2 of 3,990 further apart than a tick (2010-10-11, 2010-10-13). Sessions EIA has that the snapshot's `CL=F` lacks: 2012-10-29, 2012-10-30, 2018-12-05 (gaps in Yahoo's series), and 2020-04-20 (a non-positive settlement, dropped by rule).
- `CL=F` vs EIA's Cushing spot (RWTC, a physical price, so a loose check that cannot tell contracts apart), 2000-08-23 to 2026-06-15: 6,457 common days, median absolute log gap 0.0012, 99th percentile 0.0397, largest 0.354 (2020-03-30). Later sessions have no second source.
- Contract rolls: of the 283 contracts that expire while both `CL=F` and EIA's contracts 1 and 2 have data (2000-08-23 to 2024-04-05), `CL=F` equals EIA's contract 1 on the lab's last session before the roll and on its roll day for 259; it followed the next contract before the roll for: CLV02 from 2002-09-20, 1 session before the lab's roll day; 23 cannot be told (EIA lacks a row, the two contracts are within a tick, or `CL=F` matches neither). The 30 roll days after 2024-04-05 are not checked. This shows Yahoo rolling with EIA's contract 1, not that the lab's roll calendar is right: that comes from `contract_calendar.last_trade_date`, tested against known expiry dates.
- Snapshot retrieved 2026-10-20T11:01:46.215851Z, finished sessions through 2026-10-19.
<!-- [[[end]]] -->

- Only finished sessions are kept (`contract_calendar.last_completed_bar_date`): Yahoo's newest
  daily bar is the session still trading, and from the 18:00 ET reopen to midnight it carries the
  next session's prices under today's date.
- Bars dated on exchange holidays are partial sessions and are dropped.
- The 2020-04-20 settlement (−$37.63) is dropped because log returns need positive prices.
- Range-based variance on roll days replaces the overnight gap (the calendar spread) with the
  median squared overnight return over the non-roll sessions among the previous 250 sessions: a
  rolling 250-session window ending the session before, with roll days left out of the median, and
  at least 20 non-roll sessions required (zero before that; `backend/vol_lab._overnight_variance`,
  used by `range_variance` and `settlement_range_variance`).
- **Decision-time use of the bar.** A forecast is made at session *t*'s 14:30 ET settlement, before
  its high and low are final. The lab's range-based features therefore use the full bar of earlier
  sessions and, for session *t*, only the overnight return and the open-to-settlement return
  (`backend/vol_lab.range_features`, `settlement_range_variance`). The full-bar range variance of
  later sessions is used only as the second realized-volatility proxy, never as a feature.

## Uses

Research only, offline:

- `backend/vol_lab.py` and its tests (all three files): the benchmark field, `data/vol_lab.json`.
- `backend/leak_anatomy.py` and its tests (`cl_f_daily.csv`): the block-shuffled and real-window
  `CL=F` returns of the leak demonstration, `data/leak_anatomy.json`.

The live pipeline downloads fresh data every refresh; this snapshot does not feed the dashboard's
live numbers.

## Distribution and maintenance

Committed to the repository (about 0.6 MB). `python -m backend.vol_lab --refresh` downloads a new
snapshot (free sources, no API keys) and rewrites the manifest; the research artifacts are then
regenerated and the change is reviewed like code. Terms: Yahoo Finance data is for personal use;
FRED series are public; Cboe owns the OVX index.
