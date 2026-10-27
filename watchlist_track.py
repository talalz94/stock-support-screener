#!/usr/bin/env python
"""Track a fixed watchlist from its entry date, every session, so it can be judged later.

Built for "Gainers at support" (2026-10-25): 181 stocks at or near a support level on
the 2026-10-23 close, ranked within each sector by how often they were among the
market's biggest one-day gainers over the past year. The question it exists to
answer later: did those picks do better than the average liquid stock?

    data/watchlists/<name>/list.csv      the list as picked (ticker, price = entry close,
                                         level = the support level, sector, ...)
    data/watchlists/<name>/tracking.csv  one row per ticker as of the latest session
    data/watchlists/<name>/summary.json  the list against the liquid pool, same dates
    data/watchlists/<name>/history.csv   one row per ticker per session since entry

STATUSES, fixed before any result was seen:
    per plan       latest close above the entry close AND at/above the support level
    held           at/above the support level, not above the entry close
    below support  latest close under the level, no low more than 10% under it yet
    failed         a low more than 10% under the level since entry -- the zones
                   study's own definition of a failed support level (it is final:
                   a stock that fails stays failed whatever it does next)
BENCHMARK: the equal-weight mean of every stock that met the list's own eligibility at
entry ($5+ close, $20M+ 20-day average dollar volume), over the same dates.

Everything is recomputed from the bar store on each run (no appending), so a re-run is
harmless and a late or corrected bar is picked up. A session counts only once most
names have a bar for it: a part-filled day (a backfill of a few names before the
nightly fetch) would otherwise be read as the latest close for the few it covers.

Run: python watchlist_track.py --name gainers_at_support_20261023 --entry 2026-10-23
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import config
import store

ROOT = config.DATA / "watchlists"
MIN_PRICE, MIN_DV = 5.0, 20e6           # the list's own eligibility, for the benchmark
FAIL_BELOW = 0.90                       # a low under 90% of the level = failed
FULL_DAY = 0.90                         # a session counts once 90% of entry-day names have a bar


def _status(close: float, entry: float, level: float, failed: bool) -> str:
    if failed:
        return "failed"
    if not np.isfinite(close):
        return "no bar"
    if close < level:
        return "below support"
    return "per plan" if close > entry else "held"


def track(name: str, entry: str, verbose: bool = True) -> dict:
    d = ROOT / name
    lst = pd.read_csv(d / "list.csv")
    assert lst["ticker"].is_unique, "list.csv: ticker must be unique"
    start = (pd.Timestamp(entry) - pd.Timedelta(days=45)).strftime("%Y-%m-%d")
    px = store.read(interval="1d", start=start, columns=["ticker", "date", "close", "low", "volume"])
    px["date"] = px["date"].astype(str)
    C = px.pivot_table(index="date", columns="ticker", values="close", aggfunc="last").sort_index()
    L = px.pivot_table(index="date", columns="ticker", values="low", aggfunc="last").sort_index()
    V = px.pivot_table(index="date", columns="ticker", values="volume", aggfunc="last").sort_index()
    if entry not in C.index:
        raise RuntimeError(f"no bars on the entry date {entry}")
    full = C.notna().sum(axis=1) >= FULL_DAY * C.loc[entry].notna().sum()
    after = [x for x in C.index if x > entry and full[x]]
    latest = after[-1] if after else entry

    # Per ticker, per session since entry.
    tk = list(lst["ticker"])
    ent = lst.set_index("ticker")["price"].astype(float)
    lvl = lst.set_index("ticker")["level"].astype(float)
    hist = []
    failed_by = {t: False for t in tk}
    for day in after:
        for t in tk:
            c = C.at[day, t] if t in C.columns else np.nan
            lo = L.at[day, t] if t in L.columns else np.nan
            if np.isfinite(lo) and lo < FAIL_BELOW * lvl[t]:
                failed_by[t] = True
            hist.append({"date": day, "ticker": t, "close": c,
                         "since_pct": (c / ent[t] - 1) * 100 if np.isfinite(c) else np.nan,
                         "status": _status(c, ent[t], lvl[t], failed_by[t])})
    H = pd.DataFrame(hist, columns=["date", "ticker", "close", "since_pct", "status"])

    rows = []
    for t in tk:
        h = H[H.ticker == t]
        c = C.at[latest, t] if (t in C.columns and latest != entry) else np.nan
        lows = L.loc[after, t] if (t in L.columns and after) else pd.Series(dtype=float)
        closes = C.loc[after, t] if (t in C.columns and after) else pd.Series(dtype=float)
        rows.append({
            "ticker": t,
            "entry_date": entry, "entry_close": round(ent[t], 4), "level": round(lvl[t], 4),
            "latest_date": latest, "latest_close": round(float(c), 4) if np.isfinite(c) else np.nan,
            "since_pct": round((c / ent[t] - 1) * 100, 2) if np.isfinite(c) else np.nan,
            "best_close_pct": round((closes.max() / ent[t] - 1) * 100, 2) if closes.notna().any() else np.nan,
            "worst_low_pct": round((lows.min() / ent[t] - 1) * 100, 2) if lows.notna().any() else np.nan,
            "sessions": len(after),
            "status": h["status"].iloc[-1] if len(h) else "no bar",
        })
    T = pd.DataFrame(rows)

    # The benchmark over the same dates.
    dv = (C * V).rolling(20, min_periods=15).mean().loc[entry]
    elig = (C.loc[entry] >= MIN_PRICE) & (dv >= MIN_DV)
    pool = (C.loc[latest] / C.loc[entry] - 1)[elig & C.loc[latest].notna()] * 100 if after else pd.Series(dtype=float)
    lr = T["since_pct"].dropna()
    counts = T["status"].value_counts()
    S = {
        "name": name, "entry": entry, "latest": latest, "sessions": len(after),
        "stocks": int(len(T)),
        "list_avg_pct": round(float(lr.mean()), 2) if len(lr) else None,
        "list_median_pct": round(float(lr.median()), 2) if len(lr) else None,
        "list_up_pct": round(float((lr > 0).mean() * 100), 1) if len(lr) else None,
        "pool_stocks": int(len(pool)),
        "pool_avg_pct": round(float(pool.mean()), 2) if len(pool) else None,
        "pool_median_pct": round(float(pool.median()), 2) if len(pool) else None,
        "pool_up_pct": round(float((pool > 0).mean() * 100), 1) if len(pool) else None,
        "per_plan": int(counts.get("per plan", 0)), "held": int(counts.get("held", 0)),
        "below_support": int(counts.get("below support", 0)), "failed": int(counts.get("failed", 0)),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    T.to_csv(d / "tracking.csv", index=False)
    H.to_csv(d / "history.csv", index=False)
    (d / "summary.json").write_text(json.dumps([S], indent=1), encoding="utf-8")
    if verbose:
        print(f"{name}: entry {entry} -> {latest} ({len(after)} session(s)); list {S['list_avg_pct']:+}% "
              f"vs pool {S['pool_avg_pct']:+}% (n={S['pool_stocks']}); per plan {S['per_plan']}, held {S['held']}, "
              f"below support {S['below_support']}, failed {S['failed']}" if after else
              f"{name}: no full session after {entry} yet")
    return S


def selftest() -> int:
    assert _status(105, 100, 95, False) == "per plan"
    assert _status(99, 100, 95, False) == "held"
    assert _status(94, 100, 95, False) == "below support"
    assert _status(120, 100, 95, True) == "failed", "a failure is final"
    assert _status(np.nan, 100, 95, False) == "no bar"
    print("watchlist_track selftest OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name")
    ap.add_argument("--entry")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not (a.name and a.entry):
        ap.error("--name and --entry are required")
    track(a.name, a.entry)
    return 0


if __name__ == "__main__":
    sys.exit(main())
