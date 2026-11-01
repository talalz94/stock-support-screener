#!/usr/bin/env python
"""What does each stock news EVENT predict? Measured, before anything is scored.

    python news_events_study.py            -> data/_events_study_summary.json + a printed table

EVENTS: data/news_events/edgar.parquet (news_events.py), one per filing x company, amendments
dropped, 8-K items split into one event per item (9.01, exhibits, carries none).
Earnings (8-K Item 2.02) also split by DIRECTION from the Benzinga headlines already
stored (sentiment.py's EARNINGS_BEAT / EARNINGS_MISS), on the filing's session or the
one before: beats > misses = beat, misses > beats = miss, else no read.

ENTRY: the close of `public_session` (the first close after the filing was accepted --
a pre-market 8-K is traded at that day's close, the earliest a daily-close system can
act). The REACTION is that session's own move (previous close -> public close),
measured separately: it is the part no daily-close entry can capture.

EXCESS: event_returns -- minus same-day stocks in the same liquidity fifth. t across
calendar quarters. One episode per ticker per 60 sessions per event type.

CHANCE BASELINE (the user's other project's rule: credit only what beats chance):
every episode gets up to 5 PLACEBO dates for the SAME stock -- random sessions within
a year, at least 10 sessions from any event of that type for that stock -- measured
the same way. `vs_own` is the episode's excess minus its placebos' mean, so a type
that merely lands on volatile or drifting stocks gets no credit.

SPLIT HALVES: before / from 2021-07-01, each with its own t. A result counts only if
it holds in both.

SURVIVORSHIP: the bar store holds names still listed or recently removed, so events in
companies that later vanished (bankruptcies above all) are under-counted. Event types
that precede delisting look BETTER here than they were.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import config
import event_returns as ER
import news_events as EV

OUT_SUMMARY = config.DATA / "_news_events_study_summary.json"
HORIZONS = (1, 5, 20, 60)
GAP = 60
SPLIT = "2021-07-01"
START = "2016-06-01"
N_PLACEBO = 5
PLACEBO_CLEAR = 10
SEED = 7
MIN_EPISODES = 30


def earnings_direction(ev: pd.DataFrame) -> pd.Series:
    """'beat' / 'miss' / '' per earnings event, from stored Benzinga headlines."""
    import news
    import sentiment as senti
    lo, hi = str(ev.public_session.min()), str(ev.public_session.max())
    sc = senti.load_cached(start=lo, end=hi)
    sc = sc[sc.event_type.isin(["EARNINGS_BEAT", "EARNINGS_MISS"])][["id", "event_type"]]
    art = news.read(start=lo, end=hi, columns=["id", "ts", "session", "symbols"])
    art = art.merge(sc, on="id", how="inner")
    art = art.explode("symbols").rename(columns={"symbols": "ticker"})
    art["beat"] = (art.event_type == "EARNINGS_BEAT").astype(int)
    art["miss"] = (art.event_type == "EARNINGS_MISS").astype(int)
    g = art.groupby(["ticker", "session"])[["beat", "miss"]].sum()
    sess = EV._sessions()
    pos = {s: i for i, s in enumerate(sess)}
    prev = {s: sess[i - 1] for s, i in pos.items() if i > 0}
    out = []
    for t, s in zip(ev.ticker, ev.public_session):
        b = m = 0
        for d in (s, prev.get(s)):
            if d is not None and (t, d) in g.index:
                r = g.loc[(t, d)]
                b += int(r.beat)
                m += int(r.miss)
        out.append("beat" if b > m else "miss" if m > b else "")
    return pd.Series(out, index=ev.index)


def latency(ev: pd.DataFrame) -> dict:
    """Earnings: the first Benzinga earnings headline vs the 8-K's acceptance."""
    import news
    import sentiment as senti
    e = ev[ev.event_type == "earnings"]
    lo, hi = str(e.public_session.min()), str(e.public_session.max())
    sc = senti.load_cached(start=lo, end=hi)
    sc = sc[sc.event_type.str.startswith("EARNINGS")][["id"]]
    art = news.read(start=lo, end=hi, columns=["id", "ts", "created_at", "symbols"]).merge(sc, on="id")
    art = art.explode("symbols").rename(columns={"symbols": "ticker"})
    art["created_at"] = pd.to_datetime(art.created_at, utc=True)
    m = e[["ticker", "accepted_utc"]].merge(art[["ticker", "created_at"]], on="ticker")
    m["lag_s"] = (m.created_at - pd.to_datetime(m.accepted_utc, utc=True)).dt.total_seconds()
    m = m[m.lag_s.abs() <= 86400]
    first = m.loc[m.groupby(["ticker", "accepted_utc"]).lag_s.apply(lambda s: s.abs().idxmin())]
    if first.empty:
        return {}
    return {"matched": int(len(first)), "of_earnings_8k": int(len(e)),
            "benzinga_first_pct": round(float((first.lag_s < 0).mean() * 100), 1),
            "median_lag_s": round(float(first.lag_s.median()), 0),
            "p10_s": round(float(first.lag_s.quantile(0.1)), 0),
            "p90_s": round(float(first.lag_s.quantile(0.9)), 0),
            "note": "lag = first Benzinga earnings headline minus 8-K acceptance; negative = Benzinga first"}


def placebos(eps: pd.DataFrame, all_type: pd.DataFrame, panel: ER.Panel, rng) -> pd.DataFrame:
    """Up to N_PLACEBO same-stock dates per episode, clear of that type's events."""
    sess = panel.dates
    taken = {}
    for t, s in zip(all_type.ticker, all_type.public_session):
        p = panel.pos.get(s)
        if p is not None:
            taken.setdefault(t, []).append(p)
    rows = []
    for k, (t, s) in enumerate(zip(eps.ticker, eps.public_session)):
        p = panel.pos.get(s)
        if p is None:
            continue
        busy = np.array(taken.get(t, []))
        cand = rng.integers(max(1, p - 250), min(len(sess) - 61, p + 250), size=N_PLACEBO * 4)
        ok = [c for c in cand if not len(busy) or np.min(np.abs(busy - c)) > PLACEBO_CLEAR][:N_PLACEBO]
        rows += [{"ep": eps.index[k], "ticker": t, "event_date": sess[c - 1]} for c in ok]
    return pd.DataFrame(rows)


def run(verbose: bool = True) -> dict:
    t0 = time.time()
    raw = EV.read(start=START)
    raw = raw[raw.ticker.notna() & raw.public_session.notna()]
    ev = EV.explode_items(raw)
    if verbose:
        print(f"events: {len(raw):,} filings -> {len(ev):,} typed events, "
              f"{ev.ticker.nunique():,} tickers; {time.time() - t0:.0f}s")
    earn = ev[ev.event_type == "earnings"].copy()
    earn["direction"] = earnings_direction(earn)
    beat, miss = earn[earn.direction == "beat"].copy(), earn[earn.direction == "miss"].copy()
    beat["event_type"], miss["event_type"] = "earnings_beat", "earnings_miss"
    ev = pd.concat([ev, beat, miss], ignore_index=True)
    if verbose:
        print(f"earnings with a read: {len(beat):,} beat, {len(miss):,} miss of {len(earn):,}; "
              f"{time.time() - t0:.0f}s")

    panel = ER.Panel(horizons=HORIZONS)
    sess = panel.dates
    pos = panel.pos
    ev = ev[ev.public_session.isin(pos)].copy()
    ev["event_date"] = [sess[pos[s] - 1] if pos[s] > 0 else None for s in ev.public_session]
    ev["react_date"] = [sess[pos[s] - 2] if pos[s] > 1 else None for s in ev.public_session]
    rng = np.random.default_rng(SEED)
    lat = latency(ev)
    if verbose:
        print(f"latency: {lat}")

    results = []
    for et in sorted(ev.event_type.unique()):
        sub = ev[ev.event_type == et]
        m = panel.measure(sub[["ticker", "event_date", "public_session", "accession"]])
        eps = ER.first_in_window(m, GAP, panel)
        if len(eps) < MIN_EPISODES:
            continue
        # the reaction day: entry one session earlier, horizon 1
        r = panel.measure(eps[["ticker", "public_session"]].assign(
            event_date=[sess[pos[s] - 2] if pos[s] > 1 else None for s in eps.public_session]))
        react = r["excess_1"]
        pl = placebos(eps, sub, panel, rng)
        pm = panel.measure(pl) if len(pl) else pl
        for h in HORIZONS:
            base = ER.summarize(eps, h, et)
            row = {"type": et, "h": h, **{k: v for k, v in base.items() if k != "test"},
                   "reaction_pp": round(float(react.mean() * 100), 2) if len(react) else None}
            row["first_half"] = ER.summarize(eps[eps.entry < SPLIT], h, et)["t"]
            row["second_half"] = ER.summarize(eps[eps.entry >= SPLIT], h, et)["t"]
            if len(pm):
                plm = pm.groupby("ep")[f"excess_{h}"].mean()
                d = eps.assign(**{f"excess_{h}": eps[f"excess_{h}"] - plm.reindex(eps.index).to_numpy()})
                vo = ER.summarize(d, h, et)
                row |= {"vs_own_pp": vo["mean_pp"], "vs_own_t": vo["t"],
                        "placebo_pp": round(float(plm.mean() * 100), 2)}
            results.append(row)
        if verbose:
            r20 = [x for x in results if x["type"] == et and x["h"] == 20][-1]
            print(f"  {et:24} episodes {r20['episodes']:>6,}  react {r20['reaction_pp']:+6.2f}pp  "
                  f"20d {r20['mean_pp']:+6.2f}pp t {r20['t']:+5.2f}  vs own {r20.get('vs_own_pp')}pp "
                  f"t {r20.get('vs_own_t')}  halves {r20['first_half']}/{r20['second_half']}  "
                  f"{time.time() - t0:.0f}s", flush=True)
    n = len(results)
    out = {"measured_on": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "events_from": str(ev.public_session.min()), "events_to": str(ev.public_session.max()),
           "tests": n, "expected_by_chance": round(n * 0.05, 1),
           "definitions": __doc__.strip(), "latency": lat, "results": results}
    OUT_SUMMARY.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    if verbose:
        print(f"\n{n} tests, ~{out['expected_by_chance']} expected at |t| >= 2 by chance; "
              f"{time.time() - t0:.0f}s -> {OUT_SUMMARY}")
    return out


if __name__ == "__main__":
    run()
    sys.exit(0)
