# field-data: the shadow field log (unsealed)

This branch holds `vol_field_record.jsonl`, the unsealed shadow log of the whole benchmark field
(ROADMAP 1.8). After the seed commit only the Refresh workflow writes here (it appends), and nothing on the site
or in the promotion test reads it.

It is **not** sealed evidence. The sealed forward record is `runtime-state/vol_track_record.json` on
the `live-data` branch, and the pinned 52-week promotion test of `har_iv` against `har_iv_range` is the
only test of that pair. Comparisons drawn from this log are descriptive.

The format, and what `python backend/field_record.py --validate` checks, is in `docs/DATA_CONTRACT.md`
on `main` ("The shadow field log (unsealed)"). The file only grows: check a copy against an earlier one
with `--validate --log NEW --previous OLD`.
