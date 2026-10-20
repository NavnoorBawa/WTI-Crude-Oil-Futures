# Datasheet: pinned market-data snapshot (`data/market/`)

After Gebru et al. (2021), *Datasheets for Datasets*. Generated facts come from
`data/vol_lab.json` (`scripts/readme_blocks.py`, checked in CI).

## Motivation

The volatility research must reproduce bit for bit, offline, years from now. Yahoo Finance revises
history (holiday-dated partial bars come and go, and past closes of the continuous series have
changed between downloads), so the research runs on a frozen copy whose SHA-256 digests are pinned
in `MANIFEST.json`. `backend/market_data.load_snapshot()` refuses any file that does not match.

## Composition

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("provenance_table") ]]] -->

| File | Rows | First | Last | SHA-256 | Source |
|---|---:|---|---|---|---|
| `data/market/cl_f_daily.csv` | 6,564 | 2000-08-23 | 2026-10-19 | `2e0c57f697484b46…` | Yahoo Finance CL=F daily OHLC (yfinance), finished NYMEX sessions only |
| `data/market/ovx_yahoo_daily.csv` | 4,892 | 2007-05-10 | 2026-10-19 | `b641a9753df21f21…` | Yahoo Finance ^OVX daily close (yfinance), finished sessions only |
| `data/market/ovxcls_fred.csv` | 4,893 | 2007-05-10 | 2026-10-16 | `2117700ea77ca6b1…` | FRED OVXCLS (https://fred.stlouisfed.org/series/OVXCLS), keyless fredgraph CSV |
<!-- [[[end]]] -->

- `cl_f_daily.csv`: Date, Open, High, Low, Close (USD per barrel), Volume (contracts). One row per
  finished NYMEX session; `CL=F` is Yahoo's continuous front-month series, **unadjusted**: it
  switches contracts the session after each last trade date and books the calendar spread as a
  return on that day.
- `ovx_yahoo_daily.csv`: the CBOE Crude Oil ETF Volatility Index close (annualized percent), the
  series the production model uses.
- `ovxcls_fred.csv`: the same index as redistributed by FRED, used only as an independent check.

## Collection and cleaning

<!-- [[[cog import scripts.readme_blocks as b; b.print_block("data_quality_block") ]]] -->

- `CL=F` sessions kept: 6,564. Bars with a non-positive open, high or low (the session after the 2020-04-20 negative settlement): 1, close kept, range blanked. Bars whose high was below the open or close: 3; whose low was above them: 4 (repaired by widening the range; closes are never changed).
- OVX, Yahoo vs FRED: 4,891 common days, 99.88% within 0.05 points, correlation 0.999996, largest differences 2009-11-27 (Yahoo 40.73, FRED 43.36), 2010-04-27 (Yahoo 30.34, FRED 28.02), 2014-12-09 (Yahoo 38.53, FRED 37.56).
- Snapshot retrieved 2026-10-20T11:01:46.215851Z, finished sessions through 2026-10-19.
<!-- [[[end]]] -->

- Only finished sessions are kept (`contract_calendar.last_completed_bar_date`): Yahoo's newest
  daily bar is the session still trading, and from the 18:00 ET reopen to midnight it carries the
  next session's prices under today's date.
- Bars dated on exchange holidays are partial sessions and are dropped.
- The 2020-04-20 settlement (−$37.63) is dropped because log returns need positive prices.
- Range-based variance on roll days replaces the overnight gap (the calendar spread) with the
  median overnight move of the previous 250 non-roll sessions (`backend/vol_lab.range_variance`).

## Uses

Research only: `backend/vol_lab.py` and its tests. The live pipeline downloads fresh data every
refresh; this snapshot does not feed the dashboard's live numbers.

## Distribution and maintenance

Committed to the repository (about 0.6 MB). `python -m backend.vol_lab --refresh` downloads a new
snapshot (free sources, no API keys) and rewrites the manifest; the research artifact is then
regenerated and the change is reviewed like code. Terms: Yahoo Finance data is for personal use;
FRED series are public; CBOE owns the OVX index.
