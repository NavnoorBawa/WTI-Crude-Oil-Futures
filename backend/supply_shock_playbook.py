#!/usr/bin/env python3
"""
Oil supply-shock playbook — rigorous, EIA-sourced historical reference.

Design principle: NO hand-entered price numbers. We store only the verified event
date + supply-at-risk (with a source note), then COMPUTE every realized price move
(peak %, day-to-peak, settle %, trough %, and the day-by-day run-up trajectory) from
EIA's official daily WTI Cushing spot series (RWTC, ~1986-present, free, authoritative).

This is a decision-support reference for a discretionary commodities/macro PM during a
geopolitical event — "how have structurally similar shocks actually resolved" — NOT a
price prediction. It also empirically tests whether a "priced-in vs. history at the
same stage" read carries any signal before we build anything on top of it.
"""

import bisect
import datetime as dt
import json
import os
import statistics
from pathlib import Path

import requests

EIA_BASE = "https://api.eia.gov/v2/petroleum/pri/spt/data/"
_CACHE = Path(__file__).parent.parent / "data" / "eia_wti_spot_daily.json"


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
            resp = requests.get(EIA_BASE, params=params, timeout=30)
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
    try:
        _CACHE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE.write_text(json.dumps({"fetched_at": dt.date.today().isoformat(), "rows": out}))
    except Exception:
        pass
    return out


def realized_move(dates, series, event_date, before=5, window=20, settle=10):
    """Compute the real price response around an event from the daily series."""
    i = bisect.bisect_left(dates, event_date)
    if i >= len(dates):
        return None
    prior = [series[dates[k]] for k in range(max(0, i - before), i)]
    base = sum(prior) / len(prior) if prior else series[dates[i]]
    fwd = [(dates[k], series[dates[k]]) for k in range(i, min(len(dates), i + window))]
    if not fwd:
        return None
    peak_date, peak_px = max(fwd, key=lambda x: x[1])
    trough_date, trough_px = min(fwd, key=lambda x: x[1])
    settle_px = series[dates[min(len(dates) - 1, i + settle)]]
    # day-by-day cumulative move vs baseline (the run-up trajectory)
    traj = {k: round((px - base) / base * 100, 2) for k, (_, px) in enumerate(fwd) if k in (0, 1, 2, 3, 5, 10, 15)}
    return {
        "base": round(base, 2),
        "peak_pct": round((peak_px - base) / base * 100, 1),
        "peak_day": (dt.date.fromisoformat(peak_date) - dt.date.fromisoformat(fwd[0][0])).days,
        "trough_pct": round((trough_px - base) / base * 100, 1),
        "settle_pct": round((settle_px - base) / base * 100, 1),
        "trajectory": traj,
    }


def build_playbook(api_key=None):
    series_list = fetch_wti_daily(api_key)
    dates = [d for d, _ in series_list]
    series = dict(series_list)
    out = []
    for ev in SHOCK_EVENTS:
        rm = realized_move(dates, series, ev["date"])
        if rm:
            out.append({**ev, **rm})
    return out


def _summary(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return {"n": len(vals), "median": round(statistics.median(vals), 1),
            "min": round(min(vals), 1), "max": round(max(vals), 1)}


def get_playbook_for_api(api_key=None, current_drivers=None, top_n=5):
    """
    Build the full playbook and return structured data for the API response.
    Cache-first: uses data/eia_wti_spot_daily.json if fresh; falls back to live EIA
    only when the cache is stale and api_key is available.

    current_drivers: list of driver strings (e.g. ["iran","conflict"]) to prioritise
                     when ranking historical analogues. Defaults to no preference.
    """
    try:
        events = build_playbook(api_key)
    except RuntimeError:
        return None
    if not events:
        return None

    def dist(grp):
        if not grp:
            return None
        ps = _summary([e["peak_pct"] for e in grp])
        ss = _summary([e["settle_pct"] for e in grp])
        return {"n": len(grp), "peak": ps, "settle": ss}

    distributions = {
        "supply_lost":  dist([e for e in events if e.get("supply_mbpd", 0) > 0.5]),
        "threat_only":  dist([e for e in events if e.get("supply_mbpd", 0) == 0.0]),
        "strait_risk":  dist([e for e in events if e.get("strait_risk")]),
        "iran_driven":  dist([e for e in events if "iran" in e.get("drivers", [])]),
        "opec_cut":     dist([e for e in events if "opec" in e.get("drivers", []) and e.get("supply_mbpd", 0) > 0]),
        "weather":      dist([e for e in events if "weather" in e.get("drivers", [])]),
        "conflict":     dist([e for e in events if "conflict" in e.get("drivers", [])]),
        "sanctions":    dist([e for e in events if "sanctions" in e.get("drivers", [])]),
    }

    # Priced-in stats: does strong day-0 reaction predict the eventual peak?
    rows = [(e.get("trajectory", {}).get(0, 0.0), e["peak_pct"]) for e in events]
    big   = [pk for d0, pk in rows if d0 >= 3.0]
    small = [pk for d0, pk in rows if d0 < 3.0]
    priced_in_stats = {
        "strong_day0_n":           len(big),
        "strong_day0_median_peak": round(statistics.median(big), 1) if big else None,
        "weak_day0_n":             len(small),
        "weak_day0_median_peak":   round(statistics.median(small), 1) if small else None,
        "threshold_pct":           3.0,
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

    return {
        "event_count":      len(events),
        "distributions":    {k: v for k, v in distributions.items() if v and v["n"] > 0},
        "analogues":        top_analogues,
        "priced_in_stats":  priced_in_stats,
    }


if __name__ == "__main__":
    pb = build_playbook()
    print(f"\nPlaybook: {len(pb)} events, all moves computed from EIA daily WTI spot\n")
    print(f"{'event':<42}{'base':>8}{'peak%':>8}{'@day':>6}{'settle%':>9}  supply")
    for e in sorted(pb, key=lambda x: x["date"]):
        print(f"{e['date']} {e['event'][:30]:<31}{e['base']:>8}{e['peak_pct']:>7}%{e['peak_day']:>5}d{e['settle_pct']:>8}%   {e['supply_mbpd']} mbpd")

    print("\n── Distributions by structure ──")
    for label, pred in [
        ("Actual supply lost (>0.5 mbpd)", lambda e: e["supply_mbpd"] > 0.5),
        ("Threat-only (no supply lost)", lambda e: e["supply_mbpd"] == 0.0),
        ("Strait/Hormuz risk", lambda e: e["strait_risk"]),
        ("Iran-driven", lambda e: "iran" in e["drivers"]),
    ]:
        grp = [e for e in pb if pred(e)]
        ps, ss = _summary([e["peak_pct"] for e in grp]), _summary([e["settle_pct"] for e in grp])
        if ps:
            print(f"  {label:<34} peak {ps['median']:>5}% [{ps['min']}..{ps['max']}]   settle {ss['median']:>5}% [{ss['min']}..{ss['max']}]  (n={ps['n']})")

    print("\n── 'Priced-in' premise check: does day-0 reaction predict the eventual peak? ──")
    rows = [(e["trajectory"].get(0, 0.0), e["peak_pct"]) for e in pb if e["trajectory"].get(0) is not None]
    big_day0 = [pk for d0, pk in rows if d0 >= 3]
    small_day0 = [pk for d0, pk in rows if d0 < 3]
    print(f"  events with strong day-0 move (>=+3%): median eventual peak {round(statistics.median(big_day0),1) if big_day0 else 'n/a'}% (n={len(big_day0)})")
    print(f"  events with weak day-0 move  (<+3%):   median eventual peak {round(statistics.median(small_day0),1) if small_day0 else 'n/a'}% (n={len(small_day0)})")
