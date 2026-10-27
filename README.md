# live-data

This branch holds only the dashboard's mutable state, kept off the protected `main` branch:

- `price.json`: the latest WTI front-month quote, written by the **Refresh live price** workflow
  (`scripts/refresh_live_price.py`) and read by the dashboard from raw.githubusercontent.com.
- `runtime-state/vol_track_record.json`: the sealed live forecast record. The **Refresh frozen
  dashboard** workflow restores it, appends the new forecast, seals it and pushes it back
  (`backend/vol_record.py`). Its commit history is the record's audit trail:
  https://github.com/NavnoorBawa/WTI-Crude-Oil-Futures/commits/live-data/runtime-state/vol_track_record.json
- `runtime-state/.initialized-v1` and the older state files beside it are kept as they were, so
  the state tree the workflows check stays the same.

The code, research and documentation live on `main`. Earlier versions of this branch also carried
a copy of the original codebase; that copy is still in this branch's git history.
