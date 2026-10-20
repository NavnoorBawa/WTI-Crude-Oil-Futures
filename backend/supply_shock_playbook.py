#!/usr/bin/env python3
"""
Oil supply-shock playbook: an EIA-sourced historical reference.

Design principle: NO hand-entered price numbers. We store only the verified event
date + supply-at-risk (with a source note), then COMPUTE every realized price move
(peak %, day-to-peak, settle %, trough %, and the day-by-day run-up trajectory) from
EIA's official daily WTI Cushing spot series (RWTC, ~1986-present, free, authoritative).

This is a decision-support reference for a discretionary commodities/macro PM during a
geopolitical event — "how have structurally similar shocks actually resolved" — NOT a
price prediction. It also empirically tests whether a "priced-in vs. history at the
same stage" read carries any signal before we build anything on top of it.

Placebo (2026-10). The event "peak" is the highest close of a 20-session path against the mean of
the 5 sessions before it, so it is positive on most dates by construction, events or not (the
artifact's placebo block gives its median on non-event dates). A group's median move therefore
means nothing on its own. Every group is compared with the same statistic on every non-event date
in the events' span (the placebo), with a resampling p-value (how often n random non-event dates
have a median at least as extreme), the same test with each event matched to non-event dates of its
own calendar year (volatility regime), a Mann-Whitney test, and bootstrap confidence intervals.
Events close enough in time to share sessions are listed (overlapping_events): they are not
independent draws. `python -m backend.supply_shock_playbook` prints the table and writes
data/supply_shock_study.json from the committed EIA series (offline).
"""

import bisect
import datetime as dt
import json
import logging
import os
import statistics
from datetime import timezone
from pathlib import Path

import numpy as np
import requests
from scipy.stats import mannwhitneyu

EIA_BASE = "https://api.eia.gov/v2/petroleum/pri/spt/data/"
_CACHE = Path(__file__).parent.parent / "data" / "eia_wti_spot_daily.json"
STUDY_ARTIFACT = "supply_shock_study.json"
logger = logging.getLogger(__name__)
# Event types that carry supply_mbpd == 0 but are not supply THREATS (they are bearish supply
# increases or demand shocks), so they are excluded from the threat-only distribution.
NON_THREAT_TYPES = frozenset({"supply_glut", "demand_catalyst"})
DAY0_THRESHOLD_PCT = 3.0
# The path definition, shared by events and placebo dates.
BASE_SESSIONS = 5          # baseline = mean close of the 5 sessions before day 0
PATH_SESSIONS = 20         # peak / trough over sessions 0..19
SETTLE_SESSION = 10        # settle = close 10 sessions after day 0
# Resampling: fixed seed and draw counts, so the study and the API payload are deterministic.
STUDY_SEED = 20261020
NULL_REPS = 20000          # random n-date placebo groups per test
CI_REPS = 10000            # bootstrap resamples per confidence interval
ALPHA = 0.05


# ── Verified events ───────────────────────────────────────────────────────────
# date = the trading day the market first reacted. drivers/type describe STRUCTURE
# (so analogue matching is transparent, not a black box). supply_mbpd = peak crude
# supply removed or credibly at risk, with a source note. Price moves are computed.
SHOCK_EVENTS = [
    # ── 1990s ────────────────────────────────────────────────────────────────
    {"id": "kuwait_1990", "date": "1990-08-02", "event": "Iraq invades Kuwait (Gulf War I)",
     "type": "war", "drivers": ["conflict"], "supply_mbpd": 4.3, "strait_risk": False,
     "source": "EIA: ~4.3 mbpd Kuwaiti+Iraqi crude removed; Saudi spare capacity absorbed it"},
    {"id": "opec_agreement_1999", "date": "1999-03-23", "event": "OPEC + non-OPEC cut ends 1998 oil glut",
     "type": "supply_cut", "drivers": ["opec"], "supply_mbpd": 2.1, "strait_risk": False,
     "source": "OPEC: 2.1 mbpd combined cut; Brent rose from ~$10 to $25+ by Q3 1999; ended worst glut since 1986"},

    # ── 2000s ────────────────────────────────────────────────────────────────
    {"id": "iraq_war_2003", "date": "2003-03-20", "event": "US-led invasion of Iraq",
     "type": "war", "drivers": ["conflict"], "supply_mbpd": 2.0, "strait_risk": False,
     "source": "EIA: ~2 mbpd Iraqi exports disrupted; classic buy-the-rumor/sell-the-news"},
    {"id": "hurricane_katrina_2005", "date": "2005-08-29", "event": "Hurricane Katrina; US Gulf of Mexico shut-in",
     "type": "weather", "drivers": ["weather"], "supply_mbpd": 1.5, "strait_risk": False,
     "source": "EIA/BSEE: ~1.5 mbpd US GoM production shut in; largest natural-disaster supply loss on record"},
    {"id": "hurricane_rita_2005", "date": "2005-09-24", "event": "Hurricane Rita compounds Katrina GoM outage",
     "type": "weather", "drivers": ["weather"], "supply_mbpd": 1.4, "strait_risk": False,
     "source": "EIA/BSEE: additional ~1.4 mbpd GoM shut-in; WTI sustained $60+ on compounding effect"},
    {"id": "hurricane_ike_2008", "date": "2008-09-13", "event": "Hurricane Ike disrupts US Gulf of Mexico",
     "type": "weather", "drivers": ["weather"], "supply_mbpd": 1.0, "strait_risk": False,
     "source": "EIA/BSEE: ~1.0 mbpd GoM shut-in; ~18% of US GoM production affected"},
    {"id": "opec_cut_2008", "date": "2008-12-17", "event": "OPEC emergency 2.2 mbpd cut amid global financial crisis",
     "type": "supply_cut", "drivers": ["opec"], "supply_mbpd": 2.2, "strait_risk": False,
     "source": "OPEC: record 2.2 mbpd cut at December 2008 emergency meeting; response to GFC demand collapse"},

    # ── 2010s ────────────────────────────────────────────────────────────────
    {"id": "libya_2011", "date": "2011-02-21", "event": "Libyan civil war (Arab Spring)",
     "type": "civil_war", "drivers": ["conflict"], "supply_mbpd": 1.6, "strait_risk": False,
     "source": "EIA/IEA: ~1.6 mbpd Libyan output lost"},
    {"id": "iran_sanctions_2012", "date": "2012-07-01", "event": "EU oil embargo on Iran",
     "type": "sanctions", "drivers": ["sanctions", "iran"], "supply_mbpd": 1.0, "strait_risk": True,
     "source": "EIA: Iranian exports fell ~1 mbpd; Iran threatened to close the Strait"},
    {"id": "ukraine_crimea_2014", "date": "2014-03-03", "event": "Russia annexes Crimea; Western sanctions threat",
     "type": "sanctions", "drivers": ["conflict", "sanctions"], "supply_mbpd": 0.0, "strait_risk": False,
     "source": "IEA/Reuters: Russia kept supplying; mild initial sanctions; no physical crude disruption"},
    {"id": "isis_2014", "date": "2014-06-12", "event": "ISIS overruns northern Iraq",
     "type": "conflict", "drivers": ["conflict"], "supply_mbpd": 0.0, "strait_risk": False,
     "source": "Threat to Iraqi output; southern fields stayed online, no actual loss"},
    {"id": "opec_no_cut_2014", "date": "2014-11-27", "event": "OPEC refuses to cut; supply glut maintained",
     "type": "supply_glut", "drivers": ["opec"], "supply_mbpd": 0.0, "strait_risk": False,
     "source": "OPEC: Saudi Arabia blocked cuts at November 2014 meeting; WTI fell from $75 to $53 in weeks"},
    {"id": "nigeria_militants_2016", "date": "2016-05-04", "event": "Niger Delta Avengers; Nigerian output collapse",
     "type": "supply_attack", "drivers": ["conflict"], "supply_mbpd": 0.8, "strait_risk": False,
     "source": "EIA/Reuters: attacks cut Nigerian output to ~1.4 mbpd vs ~2.2 mbpd normal (−0.8 mbpd)"},
    {"id": "opec_deal_2016", "date": "2016-11-30", "event": "First OPEC cut deal in 8 years; OPEC+ born",
     "type": "supply_cut", "drivers": ["opec"], "supply_mbpd": 1.2, "strait_risk": False,
     "source": "OPEC: 1.2 mbpd quota cut; first cut since 2008; created the OPEC+ framework with Russia"},
    {"id": "hurricane_harvey_2017", "date": "2017-08-25", "event": "Hurricane Harvey; Texas refinery shutdown",
     "type": "weather", "drivers": ["weather"], "supply_mbpd": 0.5, "strait_risk": False,
     "source": "EIA: ~0.5 mbpd upstream shut-in; 4.4 mbpd refining capacity idled → net demand-bearish initially"},
    {"id": "jcpoa_exit_2018", "date": "2018-05-08", "event": "US exits Iran nuclear deal, reimposes sanctions",
     "type": "sanctions", "drivers": ["sanctions", "iran"], "supply_mbpd": 1.5, "strait_risk": False,
     "source": "EIA: Iranian exports fell ~1.5 mbpd into late 2018"},
    {"id": "venezuela_pdvsa_sanctions_2019", "date": "2019-01-28", "event": "US sanctions PDVSA; Venezuelan exports targeted",
     "type": "sanctions", "drivers": ["sanctions"], "supply_mbpd": 0.8, "strait_risk": False,
     "source": "US Treasury: PDVSA sanctions cut Venezuelan crude exports ~0.8 mbpd; Canada/India diverted supplies"},
    {"id": "iran_max_pressure_2019", "date": "2019-04-22", "event": "US ends Iran sanctions waivers; max pressure",
     "type": "sanctions", "drivers": ["sanctions", "iran"], "supply_mbpd": 1.2, "strait_risk": True,
     "source": "US State Dept: ended all waivers May 2, 2019; Iranian exports fell to ~0.3 mbpd vs ~2.5 mbpd pre-sanctions"},
    {"id": "tanker_attacks_2019", "date": "2019-06-13", "event": "Gulf of Oman tanker attacks",
     "type": "strait_tension", "drivers": ["iran", "conflict"], "supply_mbpd": 0.0, "strait_risk": True,
     "source": "Limpet-mine attacks near Hormuz; no actual supply cut"},
    {"id": "abqaiq_2019", "date": "2019-09-16", "event": "Abqaiq/Khurais Saudi facility attack",
     "type": "supply_attack", "drivers": ["conflict", "iran"], "supply_mbpd": 5.7, "strait_risk": False,
     "source": "EIA: ~5.7 mbpd Saudi processing knocked out; largest single-day shock; restored in weeks"},

    # ── 2020s ────────────────────────────────────────────────────────────────
    {"id": "soleimani_2020", "date": "2020-01-03", "event": "US airstrike kills IRGC Gen. Soleimani",
     "type": "military_escalation", "drivers": ["iran", "conflict"], "supply_mbpd": 0.0, "strait_risk": True,
     "source": "Escalation fear only; no physical supply impact; premium faded in ~2 weeks"},
    {"id": "saudi_russia_price_war_2020", "date": "2020-03-09", "event": "Saudi-Russia price war; production surge after OPEC+ collapse",
     "type": "supply_glut", "drivers": ["opec"], "supply_mbpd": 0.0, "strait_risk": False,
     "source": "Aramco: Saudi discount + production surge to 10+ mbpd; WTI fell 25% in one day; worst crash since 1991"},
    {"id": "opec_record_cut_2020", "date": "2020-04-13", "event": "OPEC+ record 9.7 mbpd COVID cut",
     "type": "supply_cut", "drivers": ["opec"], "supply_mbpd": 9.7, "strait_risk": False,
     "source": "OPEC+: historic 9.7 mbpd cut to arrest COVID demand collapse; effective May 1, 2020"},
    {"id": "hurricane_ida_2021", "date": "2021-08-29", "event": "Hurricane Ida; largest US Gulf shut-in since Katrina",
     "type": "weather", "drivers": ["weather"], "supply_mbpd": 1.7, "strait_risk": False,
     "source": "BSEE: 1.74 mbpd US GoM production shut in; largest since Katrina 2005"},
    {"id": "russia_ukraine_2022", "date": "2022-02-24", "event": "Russia invades Ukraine; sanctions on Russian oil",
     "type": "war", "drivers": ["conflict", "sanctions"], "supply_mbpd": 3.0, "strait_risk": False,
     "source": "EIA/IEA: up to ~3 mbpd Russian crude+products at risk from sanctions/self-sanctioning"},
    {"id": "opec_cut_2022", "date": "2022-10-05", "event": "OPEC+ announces 2 mbpd quota cut",
     "type": "supply_cut", "drivers": ["opec"], "supply_mbpd": 2.0, "strait_risk": False,
     "source": "OPEC+: headline 2 mbpd quota cut (~1.0-1.1 mbpd real)"},
    {"id": "china_covid_reopen_2022", "date": "2022-12-07", "event": "China drops COVID-Zero; demand outlook reversal",
     "type": "demand_catalyst", "drivers": ["demand"], "supply_mbpd": 0.0, "strait_risk": False,
     "source": "China NHC: ended mandatory quarantine; IEA/EIA raised 2023 demand forecasts; first demand-led oil rally since 2021"},
    {"id": "opec_cut_2023", "date": "2023-04-03", "event": "Surprise OPEC+ voluntary cut (~1.6 mbpd)",
     "type": "supply_cut", "drivers": ["opec"], "supply_mbpd": 1.6, "strait_risk": False,
     "source": "OPEC+: surprise ~1.16-1.6 mbpd voluntary cuts announced over Easter weekend"},
    {"id": "iran_tanker_seizure_2023", "date": "2023-05-04", "event": "Iran IRGC seizes tanker near Strait of Hormuz",
     "type": "strait_tension", "drivers": ["iran"], "supply_mbpd": 0.0, "strait_risk": True,
     "source": "IRGC seized Marshall Islands-flagged tanker Advantage Sweet in Gulf of Oman; third Hormuz seizure in 2 years"},
    {"id": "saudi_extra_cut_2023", "date": "2023-07-03", "event": "Saudi Arabia extends voluntary 1 mbpd cut",
     "type": "supply_cut", "drivers": ["opec"], "supply_mbpd": 1.0, "strait_risk": False,
     "source": "Saudi Energy Ministry: extended voluntary 1 mbpd cut through September, later extended to year-end"},
    {"id": "hamas_2023", "date": "2023-10-09", "event": "Hamas attack on Israel; Gaza war begins",
     "type": "conflict", "drivers": ["conflict", "iran"], "supply_mbpd": 0.0, "strait_risk": False,
     "source": "No direct oil supply hit; risk premium on wider escalation"},
    {"id": "houthi_redsea_2023", "date": "2023-12-18", "event": "Houthi Red Sea shipping attacks escalate",
     "type": "strait_tension", "drivers": ["conflict", "iran"], "supply_mbpd": 0.0, "strait_risk": True,
     "source": "Shipping rerouted via Cape of Good Hope; oil supply unaffected, freight spiked"},
    {"id": "iran_israel_apr_2024", "date": "2024-04-12", "event": "Iran's first direct strike on Israel",
     "type": "military_escalation", "drivers": ["iran", "conflict"], "supply_mbpd": 0.0, "strait_risk": True,
     "source": "Direct Iran-Israel exchange; rapid de-escalation, no supply impact"},
    {"id": "lebanon_hezbollah_2024", "date": "2024-09-23", "event": "Israel launches large-scale strikes on Hezbollah",
     "type": "military_escalation", "drivers": ["conflict", "iran"], "supply_mbpd": 0.0, "strait_risk": False,
     "source": "Reuters: ~1,600 Israeli airstrikes on Hezbollah; no direct oil supply impact; Iran proxy escalation fear elevated"},
    {"id": "iran_israel_oct_2024", "date": "2024-10-01", "event": "Iran missile barrage on Israel",
     "type": "military_escalation", "drivers": ["iran", "conflict"], "supply_mbpd": 0.0, "strait_risk": True,
     "source": "~180 missiles; market priced strikes on Iranian energy that did not materialize"},
]


def fetch_wti_daily(api_key=None, use_cache=True):
    """Return sorted [(YYYY-MM-DD, price)] of EIA daily WTI Cushing spot, paginated + cached.

    A stale cache is a valid fallback, not an error: every event in SHOCK_EVENTS is
    historical, so the computed moves are identical whether the series ends today or
    last week. Live EIA is only needed to extend the series, never to correct it.
    """
    api_key = api_key or os.getenv("EIA_API_KEY", "")
    cached_rows = None
    if use_cache and _CACHE.exists():
        try:
            cached = json.loads(_CACHE.read_text())
            cached_rows = [(r[0], float(r[1])) for r in cached["rows"]]
            if cached.get("fetched_at", "") >= (dt.date.today() - dt.timedelta(days=1)).isoformat():
                return cached_rows
        except Exception:
            cached_rows = None

    if not api_key:
        if cached_rows:
            return cached_rows
        raise RuntimeError("EIA_API_KEY required to fetch WTI spot history")

    rows = {}
    try:
        for offset in range(0, 11000, 5000):
            params = {
                "api_key": api_key, "frequency": "daily", "data[0]": "value",
                "facets[series][]": "RWTC", "sort[0][column]": "period",
                "sort[0][direction]": "asc", "length": 5000, "offset": offset,
            }
            resp = requests.get(EIA_BASE, params=params, timeout=30, allow_redirects=False)
            # raise_for_status() covers 4xx/5xx but not 3xx, and api_key rides in the query
            # string, so treat a redirect as a hard failure instead of forwarding the key.
            if resp.is_redirect or resp.is_permanent_redirect:
                raise RuntimeError("EIA API attempted a redirect; refusing to forward the key")
            resp.raise_for_status()
            data = resp.json().get("response", {}).get("data", [])
            if not data:
                break
            for d in data:
                if d.get("value") is not None:
                    rows[d["period"]] = float(d["value"])
    except Exception:
        if cached_rows:
            return cached_rows
        raise
    out = sorted(rows.items())
    # A 200 with an empty or truncated body (schema change, error payload) must never replace a
    # good history: every event below is historical, so a shorter series can only lose events.
    if cached_rows and len(out) < len(cached_rows):
        logger.warning("EIA returned %d rows (< %d cached); keeping the cache", len(out), len(cached_rows))
        return cached_rows
    if not out:
        raise RuntimeError("EIA returned no WTI spot rows")
    try:
        _CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CACHE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"fetched_at": dt.date.today().isoformat(), "rows": out}))
        os.replace(tmp, _CACHE)          # atomic: a concurrent reader never sees a torn file
    except OSError as exc:
        logger.warning("Could not update EIA spot cache %s: %s", _CACHE, exc)
    return out


def _path_at(dates, series, i, before=BASE_SESSIONS, window=PATH_SESSIONS, settle=SETTLE_SESSION):
    """Unrounded path statistics for the session at position i (the event path definition).

    peak = highest close over sessions [i, i + window) vs the mean close of the `before` sessions
    ahead of i; settle = the close `settle` sessions after i vs the same base. Because the peak is
    the maximum of a 20-session path, it is positive on most dates whether or not anything
    happened: only a comparison with the same statistic on non-event dates (the placebo) says
    whether an event moved prices more than an ordinary day does.
    """
    if i >= len(dates) or i + settle >= len(dates):
        return None                     # not enough forward data to score the date yet
    prior = [series[dates[k]] for k in range(max(0, i - before), i)]
    base = sum(prior) / len(prior) if prior else series[dates[i]]
    fwd = [(dates[k], series[dates[k]]) for k in range(i, min(len(dates), i + window))]
    peak_date, peak_px = max(fwd, key=lambda x: x[1])
    trough_date, trough_px = min(fwd, key=lambda x: x[1])
    day0_px = fwd[0][1]
    prev_close = series[dates[i - 1]] if i > 0 else None
    # The "is it already priced in?" test must not use a peak that includes day 0 itself: any event
    # with a big day-0 move would then have a big peak by construction. Measure the reaction against
    # the previous close, and the FURTHER rise strictly after the day-0 close.
    further_rise = max(px for _, px in fwd[1:]) / day0_px - 1 if len(fwd) > 1 else None
    return {
        "base": base,
        "fwd": fwd,
        "peak_date": peak_date,
        "peak": (peak_px - base) / base * 100,
        "trough": (trough_px - base) / base * 100,
        "settle": (series[dates[i + settle]] - base) / base * 100,
        "day0": (day0_px / prev_close - 1) * 100 if prev_close else None,
        "further_rise": further_rise * 100 if further_rise is not None else None,
    }


def realized_move(dates, series, event_date, before=BASE_SESSIONS, window=PATH_SESSIONS, settle=SETTLE_SESSION):
    """Compute the real price response around an event from the daily series.

    An event dated on a non-trading day (e.g. a weekend hurricane landfall) is measured from the
    next trading day. Moves are relative to the average of the `before` sessions ahead of it.
    """
    raw = _path_at(dates, series, bisect.bisect_left(dates, event_date), before, window, settle)
    if raw is None:
        return None
    base, fwd, day0, further_rise = raw["base"], raw["fwd"], raw["day0"], raw["further_rise"]
    # day-by-day cumulative move vs baseline (the run-up trajectory)
    traj = {k: round((px - base) / base * 100, 2) for k, (_, px) in enumerate(fwd) if k in (0, 1, 2, 3, 5, 10, 15)}
    return {
        "base": round(base, 2),
        "peak_pct": round(raw["peak"], 1),
        "peak_day": (dt.date.fromisoformat(raw["peak_date"]) - dt.date.fromisoformat(fwd[0][0])).days,
        "trough_pct": round(raw["trough"], 1),
        "settle_pct": round(raw["settle"], 1),
        "day0_pct": round(day0, 1) if day0 is not None else None,
        # Classified on the unrounded move: +2.99% must not round into the >= 3% group.
        "strong_day0": day0 >= DAY0_THRESHOLD_PCT if day0 is not None else None,
        "further_rise_after_day0_pct": round(further_rise, 1) if further_rise is not None else None,
        "trajectory": traj,
    }




def load_committed_series():
    """The committed EIA cache, read directly: no network and nothing written. The study runs on
    this, so data/supply_shock_study.json reproduces offline."""
    cached = json.loads(_CACHE.read_text(encoding="utf-8"))
    return [(r[0], float(r[1])) for r in cached["rows"]]


def _score_events(series_list):
    dates = [d for d, _ in series_list]
    series = dict(series_list)
    out = []
    for ev in SHOCK_EVENTS:
        rm = realized_move(dates, series, ev["date"])
        if rm:
            out.append({**ev, **rm})
    return out


def build_playbook(api_key=None):
    return _score_events(fetch_wti_daily(api_key))


def _summary(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    # "+ 0.0" folds a rounded -0.0 into 0.0 so no distribution renders as "-0.0%".
    return {"n": len(vals), "median": round(statistics.median(vals), 1) + 0.0,
            "min": round(min(vals), 1) + 0.0, "max": round(max(vals), 1) + 0.0}


# ── Event groups ──────────────────────────────────────────────────────────────
# (key, label, definition, predicate). The keys are the dashboard payload's distribution keys and
# never change; the labels say exactly what each group holds. "supply_lost" was described as "real
# barrels lost", but it also holds announced OPEC(+) quota cuts and sanctions whose export losses
# unfold over months, so its label is "announced or physical supply removal".
def _is_supply_removal(e):
    return e.get("supply_mbpd", 0) > 0.5


def _is_threat_only(e):
    return e.get("supply_mbpd", 0) == 0.0 and e.get("type") not in NON_THREAT_TYPES


GROUPS = (
    ("supply_lost", "Announced or physical supply removal >0.5 mbpd",
     "supply_mbpd > 0.5: physical outages (wars, attacks, hurricane shut-ins), sanctioned export "
     "losses and announced OPEC(+) quota cuts", _is_supply_removal),
    ("threat_only", "Geopolitical threat, no supply removed",
     "supply_mbpd == 0, excluding bearish supply gluts and demand events: conflict, strait and "
     "escalation scares", _is_threat_only),
    ("strait_risk", "Hormuz / transit-strait risk", "strait_risk is true",
     lambda e: bool(e.get("strait_risk"))),
    ("iran_driven", "Iran-driven", "'iran' among the drivers",
     lambda e: "iran" in e.get("drivers", [])),
    ("opec_cut", "Announced OPEC(+) production cut", "'opec' driver with supply_mbpd > 0",
     lambda e: "opec" in e.get("drivers", []) and e.get("supply_mbpd", 0) > 0),
    ("weather", "Hurricane / weather shut-in", "'weather' driver",
     lambda e: "weather" in e.get("drivers", [])),
    ("conflict", "Armed conflict", "'conflict' driver",
     lambda e: "conflict" in e.get("drivers", [])),
    ("sanctions", "Sanctions", "'sanctions' driver",
     lambda e: "sanctions" in e.get("drivers", [])),
)
# Reported in the study artifact only (the dashboard table lists the structural groups).
STUDY_ONLY_GROUPS = (
    ("all_events", "All defined events", "every scored event, bearish and demand events included",
     lambda e: True),
)
GROUP_LABELS = {key: label for key, label, _, _ in GROUPS + STUDY_ONLY_GROUPS}


# ── Placebo: the same path statistic on non-event dates ──────────────────────
def placebo_positions(dates, event_positions, radius=PATH_SESSIONS):
    """Every session from the first event to the last that lies more than `radius - 1` sessions
    from every event, so a placebo path (its 5-session base, its 20-session window and its settle)
    never contains an event day and never sits inside an event's own window."""
    excluded = set()
    for i in event_positions:
        excluded.update(range(i - radius + 1, i + radius))
    return [j for j in range(min(event_positions), max(event_positions) + 1) if j not in excluded]


def _placebo_moves(dates, series, event_positions):
    """Rounded like the event table (0.1%), so events and placebo are compared on equal terms."""
    out = []
    for j in placebo_positions(dates, event_positions):
        raw = _path_at(dates, series, j)
        if raw is None:
            continue
        out.append({"date": dates[j], "peak_pct": round(raw["peak"], 1), "settle_pct": round(raw["settle"], 1),
                    "day0": raw["day0"], "further_rise_pct": round(raw["further_rise"], 1)})
    return out


def _r(x, nd=1):
    return round(float(x), nd) + 0.0      # "+ 0.0" folds -0.0 into 0.0


def _two_sided_p(null, observed):
    """Two-sided resampling p-value: twice the smaller tail, ties counted against the hypothesis."""
    reps = null.size
    upper = (1 + int(np.sum(null >= observed))) / (reps + 1)
    lower = (1 + int(np.sum(null <= observed))) / (reps + 1)
    return min(1.0, 2.0 * min(upper, lower))


def _null_medians(pools, reps, rng):
    """Median of one draw from each pool (one pool per event), `reps` times."""
    draws = np.column_stack([pool[rng.integers(0, pool.size, reps)] for pool in pools])
    return np.median(draws, axis=1)


def _bootstrap_ci(values, rng):
    v = np.asarray(values, dtype=float)
    meds = np.median(v[rng.integers(0, v.size, (CI_REPS, v.size))], axis=1)
    lo, hi = np.percentile(meds, [2.5, 97.5])
    return [_r(lo), _r(hi)]


def _year_pools(placebo, metric, years, min_size=20):
    """Placebo values from each event's own calendar year (widened a year at a time until there
    are `min_size` dates), so a 2008 or 2020 event is compared with 2008 or 2020 volatility."""
    by_year = {}
    for p in placebo:
        by_year.setdefault(int(p["date"][:4]), []).append(p[metric])
    pools = []
    for year in years:
        width, vals = 0, list(by_year.get(year, []))
        while len(vals) < min_size and width < 50:
            width += 1
            vals = [v for y in range(year - width, year + width + 1) for v in by_year.get(y, [])]
        pools.append(np.asarray(vals, dtype=float))
    return pools


def _group_stats(index, members, placebo, placebo_arrays):
    years = [int(e["date"][:4]) for e in members]
    res = {}
    for m, name in enumerate(("peak", "settle")):
        metric = f"{name}_pct"
        values = np.array([e[metric] for e in members], dtype=float)
        observed = float(np.median(values))
        pool = placebo_arrays[metric]
        rng = np.random.default_rng([STUDY_SEED, index, m])
        null = _null_medians([pool] * len(members), NULL_REPS, rng)
        null_matched = _null_medians(_year_pools(placebo, metric, years), NULL_REPS, rng)
        res[name] = {
            "median": _r(observed),
            "ci": _bootstrap_ci(values, rng),
            "excess": _r(observed - np.median(pool)),
            "null_band": [_r(q) for q in np.percentile(null, [5, 95])],
            "p": round(_two_sided_p(null, observed), 4),
            "p_matched": round(_two_sided_p(null_matched, observed), 4),
            "p_mw": round(float(mannwhitneyu(values, pool, alternative="two-sided").pvalue), 4),
        }
    pk, st = res["peak"], res["settle"]
    return {
        "n": len(members),
        "median_peak_pct": pk["median"], "median_settle_pct": st["median"],
        "ci_peak": pk["ci"], "ci_settle": st["ci"],
        "excess_peak_vs_placebo_pct": pk["excess"], "excess_settle_vs_placebo_pct": st["excess"],
        "p_value_vs_placebo": {"peak": pk["p"], "settle": st["p"]},
        "p_value_vs_year_matched_placebo": {"peak": pk["p_matched"], "settle": st["p_matched"]},
        "mann_whitney_p": {"peak": pk["p_mw"], "settle": st["p_mw"]},
        "placebo_group_median_p05_p95": {"peak": pk["null_band"], "settle": st["null_band"]},
        "event_ids": [e["id"] for e in members],
    }


def _between_groups(a, b, index):
    """Permutation test of equal medians between two event groups (labels shuffled)."""
    out = {}
    for m, metric in enumerate(("peak_pct", "settle_pct")):
        x = np.array([e[metric] for e in a], dtype=float)
        y = np.array([e[metric] for e in b], dtype=float)
        pooled = np.concatenate([x, y])
        observed = float(np.median(x) - np.median(y))
        rng = np.random.default_rng([STUDY_SEED, index, m])
        perm = np.argsort(rng.random((NULL_REPS, pooled.size)), axis=1)
        shuffled = pooled[perm]
        null = np.median(shuffled[:, :x.size], axis=1) - np.median(shuffled[:, x.size:], axis=1)
        name = metric.replace("_pct", "")
        out[f"median_{name}_difference_pct"] = _r(observed)
        out.setdefault("p_value", {})[name] = round(_two_sided_p(null, observed), 4)
    return out


_COMPARISON_MEMO = {}


def shared_session_pairs(dates, events):
    """Event pairs fewer than BASE_SESSIONS + PATH_SESSIONS sessions apart: the later event's base or
    path lies inside the earlier event's path, so the two measurements share prices."""
    by_pos = sorted((bisect.bisect_left(dates, e["date"]), e["id"]) for e in events)
    return [{"events": [a_id, b_id], "sessions_apart": b - a}
            for k, (a, a_id) in enumerate(by_pos) for b, b_id in by_pos[k + 1:]
            if b - a < BASE_SESSIONS + PATH_SESSIONS]


def placebo_comparison(series_list, events):
    """Every group against the placebo, plus the two-group and priced-in tests. Deterministic
    (seeded), and memoized on the data it depends on, so the API and the artifact agree."""
    dates = [d for d, _ in series_list]
    series = dict(series_list)
    positions = sorted({bisect.bisect_left(dates, e["date"]) for e in events})
    span = (positions[0], min(len(dates) - 1, positions[-1] + PATH_SESSIONS))
    key = (dates[span[0]], dates[span[1]], hash(tuple(series[dates[k]] for k in range(*span))),
           tuple((e["id"], e["peak_pct"], e["settle_pct"], e.get("further_rise_after_day0_pct")) for e in events))
    if key in _COMPARISON_MEMO:
        return _COMPARISON_MEMO[key]

    placebo = _placebo_moves(dates, series, positions)
    arrays = {m: np.array([p[m] for p in placebo], dtype=float) for m in ("peak_pct", "settle_pct")}
    pct = {m: np.percentile(arrays[m], [10, 90]) for m in arrays}
    placebo_block = {
        "n": len(placebo),
        "span": [placebo[0]["date"], placebo[-1]["date"]],
        "exclusion": (f"sessions within {PATH_SESSIONS - 1} of an event (its base, window or settle "
                      "would touch the event) are excluded"),
        "median_peak_pct": _r(np.median(arrays["peak_pct"])),
        "median_settle_pct": _r(np.median(arrays["settle_pct"])),
        "p10_peak_pct": _r(pct["peak_pct"][0]), "p90_peak_pct": _r(pct["peak_pct"][1]),
        "p10_settle_pct": _r(pct["settle_pct"][0]), "p90_settle_pct": _r(pct["settle_pct"][1]),
        "share_peak_positive_pct": _r(np.mean(arrays["peak_pct"] > 0) * 100),
    }
    groups = []
    for index, (key_, label, definition, pred) in enumerate(GROUPS + STUDY_ONLY_GROUPS):
        members = [e for e in events if pred(e)]
        if not members:
            continue
        groups.append({"key": key_, "name": label, "definition": definition,
                       **_group_stats(index, members, placebo, arrays)})
    lost = [e for e in events if _is_supply_removal(e)]
    threat = [e for e in events if _is_threat_only(e)]
    between = {"groups": ["supply_lost", "threat_only"], "test": "permutation of group labels, medians",
               **(_between_groups(lost, threat, 99) if lost and threat else {})}

    # Priced-in check, with its own placebo: ordinary days with a >= 3% first-day move.
    rows = [(e["strong_day0"], e["further_rise_after_day0_pct"]) for e in events
            if e.get("strong_day0") is not None and e.get("further_rise_after_day0_pct") is not None]
    big = [fr for strong, fr in rows if strong]
    small = [fr for strong, fr in rows if not strong]
    p_big = [p["further_rise_pct"] for p in placebo if p["day0"] is not None and p["day0"] >= DAY0_THRESHOLD_PCT]
    p_small = [p["further_rise_pct"] for p in placebo if p["day0"] is not None and p["day0"] < DAY0_THRESHOLD_PCT]
    priced_in = {
        "strong_day0_n": len(big), "weak_day0_n": len(small),
        "strong_day0_median_further_rise_pct": round(statistics.median(big), 1) if big else None,
        "weak_day0_median_further_rise_pct": round(statistics.median(small), 1) if small else None,
        "strong_vs_weak_mann_whitney_p": (round(float(mannwhitneyu(big, small, alternative="two-sided").pvalue), 4)
                                          if big and small else None),
        "placebo_strong_day0_n": len(p_big),
        "placebo_strong_day0_median_further_rise_pct": _r(np.median(p_big)) if p_big else None,
        "placebo_weak_day0_n": len(p_small),
        "placebo_weak_day0_median_further_rise_pct": _r(np.median(p_small)) if p_small else None,
    }
    overlapping = {"definition": (f"event pairs fewer than {BASE_SESSIONS + PATH_SESSIONS} sessions apart, so the "
                                  f"later event's {BASE_SESSIONS}-session base or {PATH_SESSIONS}-session path lies "
                                  "inside the earlier event's path"),
                   "pairs": shared_session_pairs(dates, events)}
    result = {"placebo": placebo_block, "groups": groups, "between_groups": between, "priced_in": priced_in,
              "overlapping_events": overlapping}
    if len(_COMPARISON_MEMO) > 8:
        _COMPARISON_MEMO.clear()
    _COMPARISON_MEMO[key] = result
    return result


def _fmt(x):
    return f"{x:+.1f}%"


def study_conclusion(comparison):
    """The study's conclusion, written from its own numbers."""
    pl = comparison["placebo"]
    by_key = {g["key"]: g for g in comparison["groups"]}
    lost, threat = by_key.get("supply_lost"), by_key.get("threat_only")
    parts = [f"On a random non-event date the same statistics are {_fmt(pl['median_peak_pct'])} (peak) and "
             f"{_fmt(pl['median_settle_pct'])} (settle): a 20-session maximum is positive on "
             f"{pl['share_peak_positive_pct']:.0f}% of ordinary dates, so a group's median peak only means "
             "something relative to that."]
    for g in (lost, threat):
        if not g:
            continue
        p, pm = g["p_value_vs_placebo"], g["p_value_vs_year_matched_placebo"]
        sig = [f"{name} ({'above' if g[f'excess_{name}_vs_placebo_pct'] > 0 else 'below'})"
               for name in ("peak", "settle") if p[name] < ALPHA and pm[name] < ALPHA]
        verdict = ("differs from an ordinary date on " + " and ".join(sig) if sig
                   else "is indistinguishable from an ordinary date")
        parts.append(f"{g['name']} (n={g['n']}): median peak {_fmt(g['median_peak_pct'])}, settle "
                     f"{_fmt(g['median_settle_pct'])} (p = {p['peak']:.4f} / {p['settle']:.4f} vs placebo; "
                     f"{pm['peak']:.4f} / {pm['settle']:.4f} year-matched): it {verdict}.")
    bg = comparison["between_groups"].get("p_value")
    if bg:
        parts.append(f"Supply removal vs threat-only directly: p = {bg['peak']:.4f} (peak), "
                     f"{bg['settle']:.4f} (settle).")
    others = [g for g in comparison["groups"] if g["key"] not in ("supply_lost", "threat_only", "all_events")
              and g["p_value_vs_placebo"]["peak"] < ALPHA and g["excess_peak_vs_placebo_pct"] > 0]
    # The same rule as the verdict above: the year-matched placebo controls for the volatility regime.
    robust = [g for g in others if g["p_value_vs_year_matched_placebo"]["peak"] < ALPHA]
    fragile = [g for g in others if g not in robust]
    if robust:
        parts.append("Other groups with a peak above placebo at 5% on both tests (unadjusted for the number "
                     "of groups): " + ", ".join(f"{g['name']} (n={g['n']})" for g in robust) + ".")
    year_only = [g for g in comparison["groups"] if g["key"] not in ("supply_lost", "threat_only")
                 and g["p_value_vs_year_matched_placebo"]["peak"] < ALPHA <= g["p_value_vs_placebo"]["peak"]
                 and g["excess_peak_vs_placebo_pct"] > 0]
    if fragile:
        parts.append("Above the unmatched placebo at 5% but not the year-matched one: "
                     + ", ".join(f"{g['name']} (n={g['n']}, peak p = {g['p_value_vs_placebo']['peak']:.4f} vs "
                                 f"{g['p_value_vs_year_matched_placebo']['peak']:.4f} year-matched)"
                                 for g in fragile) + ".")
    if year_only:
        parts.append("Above the year-matched placebo at 5% but not the unmatched one: "
                     + ", ".join(f"{g['name']} (n={g['n']}, peak p = {g['p_value_vs_placebo']['peak']:.4f} vs "
                                 f"{g['p_value_vs_year_matched_placebo']['peak']:.4f} year-matched)"
                                 for g in year_only) + ".")
    pairs = comparison["overlapping_events"]["pairs"]
    shared = ("; some events are close enough that their windows share sessions ("
              + "; ".join(f"{pr['events'][0]} and {pr['events'][1]}, {pr['sessions_apart']} sessions apart"
                          for pr in pairs)
              + "), so members of a group are not all independent") if pairs else ""
    parts.append("Caveats: event dates and groupings were chosen with hindsight; the groups overlap" + shared
                 + "; and these p-values are not adjusted for the number of groups tested (see "
                 "data/research_ledger.json).")
    return " ".join(parts)


def get_playbook_for_api(api_key=None, current_drivers=None, top_n=5):
    """
    Build the full playbook and return structured data for the API response.
    Cache-first: uses data/eia_wti_spot_daily.json if fresh; falls back to live EIA
    only when the cache is stale and api_key is available.

    current_drivers: list of driver strings (e.g. ["iran","conflict"]) to prioritise
                     when ranking historical analogues. Defaults to no preference.
    """
    try:
        series_list = fetch_wti_daily(api_key)
    except RuntimeError:
        return None
    events = _score_events(series_list)
    if not events:
        return None
    comparison = placebo_comparison(series_list, events)
    vs = {g["key"]: g for g in comparison["groups"]}

    def dist(key, label, definition, grp):
        if not grp:
            return None
        ps = _summary([e["peak_pct"] for e in grp])
        ss = _summary([e["settle_pct"] for e in grp])
        g = vs.get(key, {})
        return {"n": len(grp), "peak": ps, "settle": ss, "label": label, "definition": definition,
                "vs_placebo": {k: g[k] for k in ("ci_peak", "ci_settle", "excess_peak_vs_placebo_pct",
                                                  "excess_settle_vs_placebo_pct", "p_value_vs_placebo",
                                                  "p_value_vs_year_matched_placebo") if k in g}}

    # A supply THREAT with no barrels lost. Bearish supply gluts (price wars, OPEC refusing to cut) and
    # demand catalysts also carry supply_mbpd == 0 but are not threats; counting them dragged the
    # "threats fade" settle median down with unrelated sell-offs.
    distributions = {key: dist(key, label, definition, [e for e in events if pred(e)])
                     for key, label, definition, pred in GROUPS}

    # Priced-in check: after a strong first-day reaction, is there MORE upside left, or less? The
    # group medians, the test and its placebo come from placebo_comparison (one computation).
    pi = comparison["priced_in"]
    priced_in_stats = {
        "strong_day0_n":                       pi["strong_day0_n"],
        "strong_day0_median_further_rise_pct": pi["strong_day0_median_further_rise_pct"],
        "weak_day0_n":                         pi["weak_day0_n"],
        "weak_day0_median_further_rise_pct":   pi["weak_day0_median_further_rise_pct"],
        "threshold_pct":                       DAY0_THRESHOLD_PCT,
        "definition": ("day-0 move vs the previous close; further rise = highest close over the next "
                       "19 trading days vs the day-0 close (day 0 itself excluded)"),
        "strong_vs_weak_mann_whitney_p":       pi["strong_vs_weak_mann_whitney_p"],
        **{k: v for k, v in pi.items() if k.startswith("placebo_")},
    }

    # Rank analogues: driver overlap first, recency breaks ties.
    cur = set(current_drivers or [])
    def _rank(e):
        overlap  = len(cur & set(e.get("drivers", [])))
        recency  = int(e.get("date", "2000")[:4])
        return (overlap * 1000 + recency)

    top_analogues = []
    for e in sorted(events, key=_rank, reverse=True)[:top_n]:
        top_analogues.append({
            "id":          e["id"],
            "date":        e["date"],
            "event":       e["event"],
            "type":        e.get("type"),
            "drivers":     e.get("drivers", []),
            "supply_mbpd": e.get("supply_mbpd", 0),
            "strait_risk": e.get("strait_risk", False),
            "source":      e.get("source", ""),
            "base":        e.get("base"),
            "peak_pct":    e.get("peak_pct"),
            "peak_day":    e.get("peak_day"),
            "settle_pct":  e.get("settle_pct"),
            "trough_pct":  e.get("trough_pct"),
        })

    years = sorted(int(e["date"][:4]) for e in events)
    return {
        "event_count":      len(events),
        "defined_event_count": len(SHOCK_EVENTS),
        "first_event_year": years[0],
        "last_event_year":  years[-1],
        "distributions":    {k: v for k, v in distributions.items() if v and v["n"] > 0},
        "analogues":        top_analogues,
        "priced_in_stats":  priced_in_stats,
        "placebo":          comparison["placebo"],
        "group_comparison": comparison["between_groups"],
        "conclusion":       study_conclusion(comparison),
    }


def run_study(series_list=None):
    """The event study against its placebo, from the committed EIA series by default (offline)."""
    series_list = load_committed_series() if series_list is None else series_list
    events = _score_events(series_list)
    comparison = placebo_comparison(series_list, events)
    return {
        "method": (f"Each event's path: peak = highest close over sessions 0-{PATH_SESSIONS - 1}, settle = close "
                   f"at session {SETTLE_SESSION}, both vs the mean close of the {BASE_SESSIONS} sessions before "
                   "day 0 (EIA RWTC daily spot). The placebo applies the identical statistics to every "
                   "non-event session between the first and last event. p_value_vs_placebo: two-sided "
                   f"resampling test, {NULL_REPS} groups of n random placebo dates (with replacement) vs the "
                   "group's median; p_value_vs_year_matched_placebo: the same with each event's draw taken "
                   "from placebo dates of its own calendar year; mann_whitney_p: two-sided rank test vs all "
                   "placebo dates (placebo paths overlap, so it is approximate); ci_*: 95% percentile "
                   f"bootstrap of the group median ({CI_REPS} resamples). Values at the event table's 0.1% "
                   "resolution; ties count against the hypothesis. The tests treat a group's events as "
                   "independent draws; events listed in overlapping_events share sessions, so they are not."),
        "data": {"source": "EIA RWTC daily WTI Cushing spot, data/eia_wti_spot_daily.json",
                 "events_scored": len(events), "events_defined": len(SHOCK_EVENTS)},
        "resampling": {"seed": STUDY_SEED, "null_reps": NULL_REPS, "ci_reps": CI_REPS},
        "placebo": comparison["placebo"],
        "groups": comparison["groups"],
        "between_groups": comparison["between_groups"],
        "priced_in": comparison["priced_in"],
        "overlapping_events": comparison["overlapping_events"],
        "superseded_claims": {
            "threat_only_peak": ("'Threat-only events peak +4.0% and settle near flat' described the "
                                 "statistic, not the events: an ordinary date peaks higher."),
            "supply_lost_label": ("'Real barrels lost' also counted announced OPEC(+) cuts and sanctions; "
                                  "the group is 'announced or physical supply removal'."),
        },
        "conclusion": study_conclusion(comparison),
        "generated_by": "python -m backend.supply_shock_playbook",
        "generated_at": dt.datetime.now(timezone.utc).isoformat(),
    }


def main():
    try:
        from .safe_paths import data_json_path
    except ImportError:  # Direct invocation: python backend/supply_shock_playbook.py
        from safe_paths import data_json_path

    series_list = load_committed_series()
    pb = _score_events(series_list)
    print(f"\nPlaybook: {len(pb)} events, all moves computed from EIA daily WTI spot\n")
    print(f"{'event':<42}{'base':>8}{'peak%':>8}{'@day':>6}{'settle%':>9}  supply")
    for e in sorted(pb, key=lambda x: x["date"]):
        print(f"{e['date']} {e['event'][:30]:<31}{e['base']:>8}{e['peak_pct']:>7}%{e['peak_day']:>5}d{e['settle_pct']:>8}%   {e['supply_mbpd']} mbpd")

    study = run_study(series_list)
    pl = study["placebo"]
    print(f"\n── Groups vs placebo (n={pl['n']} non-event dates: peak {_fmt(pl['median_peak_pct'])}, "
          f"settle {_fmt(pl['median_settle_pct'])}) ──")
    for g in study["groups"]:
        p, pm = g["p_value_vs_placebo"], g["p_value_vs_year_matched_placebo"]
        print(f"  {g['name']:<48} n={g['n']:>2}  peak {_fmt(g['median_peak_pct']):>7} {g['ci_peak']} p={p['peak']:.4f}"
              f" ({pm['peak']:.4f})  settle {_fmt(g['median_settle_pct']):>7} {g['ci_settle']} p={p['settle']:.4f}"
              f" ({pm['settle']:.4f})")
    pi = study["priced_in"]
    print(f"\n── Priced-in check: strong vs weak day 0, Mann-Whitney p = {pi['strong_vs_weak_mann_whitney_p']}; "
          f"ordinary >=+{DAY0_THRESHOLD_PCT}% days (n={pi['placebo_strong_day0_n']}) rose a median "
          f"{_fmt(pi['placebo_strong_day0_median_further_rise_pct'])} further ──")
    print(f"\n-> {study['conclusion']}")
    path = data_json_path(STUDY_ARTIFACT)
    path.write_text(json.dumps(study, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote data/{STUDY_ARTIFACT}")


if __name__ == "__main__":
    main()
