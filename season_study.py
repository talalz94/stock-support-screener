#!/usr/bin/env python
"""Is there a seasonal pattern worth scoring -- by sector, sub-industry and stock?

Asked 2026-10-14: "seasonal score for stock and sector, e.g. energy stocks like oil,
natural gas, refinery, heating demand". Price seasonality first (the user's choice);
demand data (EIA storage, heating-degree days) only if this shows something.

SEASONAL SCORE, fixed before any result: for a group (or stock) and a calendar month
M of year Y, the mean of its EXCESS return over the equal-weight universe in month M
of years Y-1, Y-2, ... (all available prior years, at least 4). Prior years only --
no look-ahead. Month returns need >= 15 valid sessions.

TESTS, declared (5; ~0.25 false positives expected at 5%):
  sector       Spearman IC across sectors, seasonal score vs that month's excess,
               t across months
  sub-industry the same across sub-industries
  stock        the same across stocks (eligible at the month start), t across months
  + the top-third minus bottom-third spread for sector and sub-industry
DESCRIPTIVE (not tests): each energy sub-industry's calendar-month profile over every
available year -- mean excess, years, % positive, t per month. 12 months x 9 groups
= 108 cells, so ~5 will clear |t| >= 2 by chance; nothing there is proof on its own.

LIMITS: the bars start 2016-08, so a month has at most ~10 years and the scored test
window starts once 4 prior years exist (~2021). Survivorship as everywhere.

Run: python season_study.py
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time

import numpy as np
import pandas as pd

import config
import sectors as S

MIN_YEARS = 4
MIN_SESSIONS = 15
SUMMARY = config.DATA / "_season_study_summary.json"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def log(m: str) -> None:
    print(f"  {m}", flush=True)


def tstat(a) -> float:
    a = np.asarray([x for x in a if np.isfinite(x)])
    return float(a.mean() / (a.std(ddof=1) / np.sqrt(len(a)))) if len(a) >= 5 else float("nan")


def monthly(R: pd.DataFrame) -> pd.DataFrame:
    """Calendar-month compounded return per column; blank under MIN_SESSIONS."""
    ym = R.index.str[:7]
    lg = np.log1p(R)
    s = lg.groupby(ym).sum(min_count=1)
    n = R.notna().groupby(ym).sum()
    return np.expm1(s).where(n >= MIN_SESSIONS)


def seasonal_scores(M: pd.DataFrame) -> pd.DataFrame:
    """For each (month row, column): mean of the same calendar month in prior years."""
    out = pd.DataFrame(index=M.index, columns=M.columns, dtype=float)
    mon = M.index.str[5:7]
    for mm in sorted(set(mon)):
        rows = M.index[mon == mm]
        sub = M.loc[rows]
        prior_sum = sub.cumsum().shift(1)
        prior_n = sub.notna().cumsum().shift(1)
        mean = prior_sum / prior_n
        out.loc[rows] = mean.where(prior_n >= MIN_YEARS)
    return out


def ic_test(score: pd.DataFrame, real: pd.DataFrame, min_n: int) -> dict:
    ics, spread = {}, []
    for m in score.index:
        a, b = score.loc[m], real.loc[m] if m in real.index else None
        if b is None:
            continue
        j = pd.concat([a, b], axis=1, keys=["s", "r"]).dropna()
        if len(j) >= min_n and j.s.nunique() > 2:
            ics[m] = j.s.rank().corr(j.r.rank())
            k = max(1, len(j) // 3)
            o = j.sort_values("s")
            spread.append(o.r.tail(k).mean() - o.r.head(k).mean())
    ic = pd.Series(ics, dtype=float)
    return {"months": int(len(ic)), "first": str(ic.index.min()) if len(ic) else None,
            "last": str(ic.index.max()) if len(ic) else None, "ic": float(ic.mean()) if len(ic) else None,
            "t": tstat(ic.values), "spread": float(np.mean(spread)) if spread else None,
            "spread_t": tstat(spread)}


def main() -> int:
    t0 = time.time()
    C, V = S.load_wide()
    R = S.daily_returns(C)
    E = S.eligible(C, V)
    U = R.where(E.shift(1).fillna(False).astype(bool)).mean(axis=1)
    Um = monthly(U.to_frame("u"))["u"]
    summary = {"measured_on": dt.date.today().isoformat(), "tests": 5, "levels": {}, "energy": {}}

    for level in ("sector", "subind"):
        GR, _N = S.group_returns(R, E, level)
        Gm = monthly(GR).sub(Um, axis=0)
        sc = seasonal_scores(Gm)
        res = ic_test(sc, Gm, 8)
        summary["levels"][level] = res
        print(f"\n{level.upper()}: {Gm.shape[1]} groups; seasonal score vs that month's excess over "
              f"{res['months']} months {res['first']}..{res['last']}: IC {res['ic']:+.3f}, t {res['t']:+.2f}; "
              f"top-minus-bottom third {res['spread'] * 100:+.2f}pp, t {res['spread_t']:+.2f}")
        # the outlook for the coming month, from every completed prior year
        nxt = (pd.Timestamp(Gm.index.max() + "-01") + pd.DateOffset(months=1)).strftime("%m")
        hist = Gm[Gm.index.str[5:7] == nxt]
        summary["levels"][level]["next_month"] = MONTHS[int(nxt) - 1]
        summary["levels"][level]["outlook"] = {
            g: {"mean": float(hist[g].mean()), "years": int(hist[g].notna().sum()),
                "pos": float((hist[g] > 0).mean()) if hist[g].notna().any() else None}
            for g in hist.columns if hist[g].notna().sum() >= MIN_YEARS}
        if level == "subind":
            print(f"\nENERGY CALENDAR PROFILES (descriptive; 108 cells, ~5 pass |t|>=2 by chance)")
            for g in S.ENERGY:
                if g not in Gm.columns:
                    continue
                x = Gm[g].dropna()
                prof = {}
                line = f"  {g:30}"
                for i, mname in enumerate(MONTHS, 1):
                    v = x[x.index.str[5:7] == f"{i:02d}"]
                    t = tstat(v.values)
                    prof[mname] = {"mean": float(v.mean()) if len(v) else None, "years": int(len(v)),
                                   "pos": float((v > 0).mean()) if len(v) else None, "t": t}
                    flag = "*" if np.isfinite(t) and abs(t) >= 2 else " "
                    line += f" {mname} {v.mean() * 100:+5.1f}{flag}" if len(v) else f" {mname}   -  "
                summary["energy"][g] = prof
                print(line)

    # stock level: each stock's own same-month history
    Rm = monthly(R.where(E.shift(1).fillna(False).astype(bool))).sub(Um, axis=0)
    sc = seasonal_scores(Rm)
    el_month = E.groupby(E.index.str[:7]).first()
    sc = sc.where(el_month.reindex(index=sc.index, columns=sc.columns).fillna(False).astype(bool))
    res = ic_test(sc, Rm, 100)
    summary["levels"]["stock"] = res
    print(f"\nSTOCK: own same-month history vs that month's excess over {res['months']} months "
          f"{res['first']}..{res['last']}: IC {res['ic']:+.4f}, t {res['t']:+.2f}")
    SUMMARY.write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    log(f"wrote {SUMMARY.name}; {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
