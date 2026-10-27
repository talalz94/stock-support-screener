#!/usr/bin/env python
"""Does corporate insider buying predict returns in our own data? An event study.

DEFINITIONS, fixed before any result was seen (2026-10-27):
  purchase   insider.purchases(): SEC code P, acquired, price > 0, by a DIRECTOR or
             OFFICER (10%-only holders, "Other" relationships and entities are out);
             and a believable reported price (insider.price_ok: within 0.5-2x of the same
             company's other insider trades that fortnight, no filing over $500M) --
             filers do type the total into the price field ($40M "per share"). Only
             tickers that have been in our universe; unmapped rows still count as history.
  event      (ticker, filing date): the first day the public could know. Entry is the
             close of the first session after it (event_returns.Panel). Window features
             look back 30 calendar days of FILINGS as known that day.
  episode    one per ticker per 60 sessions (the longest horizon): overlapping signals
             are counted once.
  benchmark  same-day stocks in the same liquidity fifth (event_returns).
  rare       the buyer's first open-market purchase in this company in 2+ years
             (history from 2006q1).
  routine /  after Cohen, Malloy & Pomorski (2012), on purchases only: a buyer who bought
  opportun.  in each of the 3 prior calendar years is ROUTINE if each of those years
             includes a purchase in this calendar month, else OPPORTUNISTIC; buyers
             without three years of history are unclassified.
  track      a buyer's mean 60-session excess over his/her previous measured purchases
  record     whose 60 sessions had ENDED before this filing (point in time), 2+ needed.
TESTS: 20 signals x horizons 20/40/60 = 60 tests, so about 3 would pass |t| >= 2 by
chance; each is also reported for 2016-09..2021-06 and 2021-07..2026 separately.

Writes data/_insider_events.parquet (every event with its features and returns) and
data/_insider_study_summary.json.
Run: python insider_study.py
"""
from __future__ import annotations

import bisect
import json
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import config
import event_returns as ER
import insider as INS

GAP = 60                      # sessions between episodes of the same ticker
SPLIT = "2021-07-01"
OUT_EVENTS = config.DATA / "_insider_events.parquet"
OUT_SUMMARY = config.DATA / "_insider_study_summary.json"


def history_features(buys: pd.DataFrame) -> pd.DataFrame:
    """Per purchase FILING by one owner in one issuer: rare / routine / opportunistic."""
    f = (buys.groupby(["owner_cik", "issuer_cik", "filing_date"], as_index=False)
             .agg(ticker=("ticker", "first"), value=("value", "sum"), shares=("shares", "sum"),
                  shares_after=("shares_after", "max"), role=("role", "first"),
                  planned=("aff10b5one", "max")))
    f = f.sort_values(["owner_cik", "issuer_cik", "filing_date"]).reset_index(drop=True)
    d = pd.to_datetime(f.filing_date)
    prev = d.groupby([f.owner_cik, f.issuer_cik]).shift(1)
    f["days_since_prev"] = (d - prev).dt.days
    f["rare"] = f.days_since_prev.isna() | (f.days_since_prev >= 730)
    f["own_chg"] = (f.shares / (f.shares_after - f.shares)).where(f.shares_after > f.shares)
    # Routine / opportunistic: months of purchase per (owner, issuer, year).
    f["y"], f["m"] = d.dt.year, d.dt.month
    months = f.groupby(["owner_cik", "issuer_cik", "y"]).m.agg(set)
    cls = []
    for o, i, y, m in zip(f.owner_cik, f.issuer_cik, f.y, f.m):
        yrs = [months.get((o, i, y - k)) for k in (1, 2, 3)]
        if any(s is None for s in yrs):
            cls.append("unclassified")
        else:
            cls.append("routine" if all(m in s for s in yrs) else "opportunistic")
    f["cmp"] = cls
    return f


def track_records(f: pd.DataFrame, panel: ER.Panel) -> pd.Series:
    """Point-in-time mean 60-session excess of each filing's owner's PREVIOUS purchases."""
    m = panel.measure(f[f.ticker.notna()][["ticker", "filing_date", "owner_cik"]].rename(
        columns={"filing_date": "event_date"}))
    m = m[m.excess_60.notna()].copy()
    m["end"] = [panel.dates[min(panel.pos[e] + 60, len(panel.dates) - 1)] for e in m.entry]
    by = {}
    for o, end, x in zip(m.owner_cik, m.end, m.excess_60):
        by.setdefault(o, []).append((end, x))
    for o in by:
        by[o].sort()
    out = []
    for o, d in zip(f.owner_cik, f.filing_date):
        lst = by.get(o, [])
        k = bisect.bisect_left(lst, (d,))
        past = [x for _, x in lst[:k]]
        out.append(np.mean(past) if len(past) >= 2 else np.nan)
    return pd.Series(out, index=f.index)


def window_events(f: pd.DataFrame, sales: pd.DataFrame) -> pd.DataFrame:
    """One row per (ticker, filing date) with the 30-day window as known that day."""
    rows = []
    f = f[f.ticker.notna()].sort_values(["ticker", "filing_date"])
    for t, g in f.groupby("ticker", sort=False):
        d = pd.to_datetime(g.filing_date).values.astype("datetime64[D]")
        days = sorted(g.filing_date.unique())
        di = pd.to_datetime(pd.Series(days)).values.astype("datetime64[D]")
        for k, day in enumerate(days):
            cut = di[k] - np.timedelta64(30, "D")
            w = g[(d > cut) & (d <= di[k])]
            today = g[g.filing_date == day]
            rows.append({
                "ticker": t, "event_date": day,
                "n_buyers_30": int(w.owner_cik.nunique()), "value_30": float(w.value.sum()),
                "value_today": float(today.value.sum()),
                "max_own_chg": float(w.own_chg.max()) if w.own_chg.notna().any() else np.nan,
                "ceo_cfo": bool(w.role.isin(["CEO", "CFO"]).any()),
                "directors_only": bool((w.role.isin(["Director", "Chair"])).all()),
                "rare": bool(w.rare.any()), "opportunistic": bool((w.cmp == "opportunistic").any()),
                "routine": bool((w.cmp == "routine").any() and not (w.cmp == "opportunistic").any()),
                "best_track": float(w.track.max()) if w.track.notna().any() else np.nan,
                "planned_share": (float(w.loc[w.planned == 1, "value"].sum() / w.value.sum())
                                  if w.planned.notna().any() and w.value.sum() > 0 else np.nan),
            })
    ev = pd.DataFrame(rows)
    # Cluster SELLING, the mirror test.
    s = sales[sales.ticker.notna() & ~(sales.aff10b5one == 1)]
    s = s.groupby(["ticker", "filing_date"], as_index=False).agg(sellers=("owner_cik", "nunique"),
                                                                  sold=("value", "sum"))
    srows = []
    for t, g in s.sort_values("filing_date").groupby("ticker", sort=False):
        d = pd.to_datetime(g.filing_date).values.astype("datetime64[D]")
        for k in range(len(g)):
            w = (d > d[k] - np.timedelta64(30, "D")) & (d <= d[k])
            srows.append({"ticker": t, "event_date": g.filing_date.iloc[k],
                          "n_sellers_30": int(g.sellers[w].sum()), "sold_30": float(g.sold[w].sum())})
    return ev, pd.DataFrame(srows)


SIGNALS = {
    "any purchase >= $10K":            lambda e: e.value_30 >= 1e4,
    "2+ buyers in 30 days":            lambda e: e.n_buyers_30 >= 2,
    "3+ buyers in 30 days":            lambda e: e.n_buyers_30 >= 3,
    ">= $250K bought in 30 days":      lambda e: e.value_30 >= 2.5e5,
    ">= $1M bought in 30 days":        lambda e: e.value_30 >= 1e6,
    "CEO or CFO bought":               lambda e: e.ceo_cfo & (e.value_30 >= 1e4),
    "only directors bought":           lambda e: e.directors_only & (e.value_30 >= 1e4),
    "first buy in 2+ years (rare)":    lambda e: e.rare & (e.value_30 >= 1e4),
    "opportunistic buyer":             lambda e: e.opportunistic & (e.value_30 >= 1e4),
    "routine buyer":                   lambda e: e.routine & (e.value_30 >= 1e4),
    "holding raised 20%+":             lambda e: (e.max_own_chg >= 0.2) & (e.value_30 >= 1e4),
    "buyer with a good track record":  lambda e: (e.best_track > 0) & (e.value_30 >= 1e4),
    "buyer with a poor track record":  lambda e: (e.best_track <= 0) & (e.value_30 >= 1e4),
    "beaten down (< 70% of 1y high)":  lambda e: (e.pct_hi < 0.7) & (e.value_30 >= 1e4),
    "small (bottom 2 liquidity fifths)": lambda e: (e.bucket <= 1) & (e.value_30 >= 1e4),
    "large (top liquidity fifth)":     lambda e: (e.bucket == 4) & (e.value_30 >= 1e4),
    "Ross: 2+ buyers, $250K+, not largest": lambda e: (e.n_buyers_30 >= 2) & (e.value_30 >= 2.5e5) & (e.bucket <= 3),
    "planned (10b5-1) purchases, 2023+": lambda e: (e.planned_share >= 0.5) & (e.event_date >= "2023-04-01"),
    "unplanned purchases, 2023+":      lambda e: (e.planned_share < 0.5) & (e.event_date >= "2023-04-01") & (e.value_30 >= 1e4),
}
SELL_SIGNAL = "3+ sellers, $1M+, unplanned (selling)"


def run(verbose: bool = True) -> dict:
    t0 = time.time()
    buys = INS.purchases(INS.read(codes=("P",)))
    raw_s = INS.read(start="2016-06-01", codes=("S",))
    ok = INS.price_ok(buys, pd.concat([buys[["issuer_cik", "accession", "trans_date", "price"]],
                                       raw_s[["issuer_cik", "accession", "trans_date", "price"]]]))
    dropped = int((~ok).sum())
    buys = buys[ok]
    sales = raw_s[(raw_s.acq_disp == "D") & (raw_s.price > 0) & (raw_s.is_director | raw_s.is_officer)]
    sales = sales[INS.price_ok(sales, raw_s)]
    del raw_s
    uni = set(pd.read_parquet(config.UNIVERSE_FILE)["ticker"].astype(str))
    buys = buys[buys.ticker.isna() | buys.ticker.isin(uni)]       # history keeps unmapped rows
    sales = sales[sales.ticker.isin(uni)]
    if verbose:
        print(f"purchases kept {len(buys):,} (since {buys.filing_date.min()}; {dropped:,} dropped as unbelievable prices), "
              f"sales {len(sales):,}; {time.time() - t0:.0f}s")
    panel = ER.Panel()
    f = history_features(buys)
    f["track"] = track_records(f, panel)
    ev, sev = window_events(f[f.filing_date >= "2016-06-01"], sales[sales.filing_date >= "2016-06-01"])
    ev = panel.measure(ev)
    sev = panel.measure(sev)
    ev.to_parquet(OUT_EVENTS, index=False)
    if verbose:
        print(f"events: {len(ev):,} buy-days, {len(sev):,} sell-days measured; {time.time() - t0:.0f}s")
    results = []
    for name, rule in list(SIGNALS.items()) + [(SELL_SIGNAL, None)]:
        base = sev if rule is None else ev
        pick = (base.n_sellers_30 >= 3) & (base.sold_30 >= 1e6) if rule is None else rule(base)
        eps = ER.first_in_window(base[pick.fillna(False)], GAP, panel)
        for h in ER.HORIZONS:
            r = ER.summarize(eps, h, name)
            r["first_half"] = ER.summarize(eps[eps.entry < SPLIT], h, name)
            r["second_half"] = ER.summarize(eps[eps.entry >= SPLIT], h, name)
            results.append(r)
    n_tests = len(results)
    out = {"measured_on": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "events_from": str(ev.entry.min()), "events_to": str(ev.entry.max()),
           "tests": n_tests, "expected_by_chance": round(n_tests * 0.05, 1),
           "definitions": __doc__.split("TESTS:")[0].strip(), "results": results}
    OUT_SUMMARY.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    if verbose:
        tab = pd.DataFrame([{k: v for k, v in r.items() if k not in ("first_half", "second_half")}
                            | {"t_1st": r["first_half"]["t"], "t_2nd": r["second_half"]["t"]} for r in results])
        pd.set_option("display.width", 220)
        print(tab.to_string(index=False))
        print(f"\n{n_tests} tests, ~{out['expected_by_chance']} expected to reach |t| >= 2 by chance; {time.time() - t0:.0f}s")
    return out


if __name__ == "__main__":
    run()
    sys.exit(0)
