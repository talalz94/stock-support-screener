#!/usr/bin/env python
"""Does how a sector (or sub-industry) is performing predict what it does next?

Asked 2026-10-14: "we need a sector wise score too, like how sector is performing".
Groups and point-in-time equal-weight returns come from sectors.py.

COMPONENTS, fixed before any result (each a cross-sectional rank across groups on
the first session of each month):
  rs21, rs63, rs126, rs252   group return minus the equal-weight universe over
                             1, 3, 6, 12 months (momentum)
  breadth200                 share of eligible members above their 200-day average
  hilo                       share within 2% of their 252-day high minus share within
                             2% of their 252-day low
  fund                       median fundamental score of members (nearest scored
                             session within 30 days; blank before scores exist)
OUTCOMES: the group's next 21 and 63 sessions, minus the universe.

TESTS, declared (2 levels x 7 components x 2 horizons = 28, plus a walk-forward
composite per level and horizon = 4; ~1.6 false positives expected at 5%):
  Spearman IC across groups per date; t ACROSS DATES. 21-session outcomes use every
  month (non-overlapping); 63-session outcomes every third month. A composite is
  built ONLY from components whose |t| >= 2 on the FIRST half of the dates, signed
  as measured there, and evaluated on the SECOND half -- the in-sample t of the
  same composite would be circular.

LIMITS: ~20 sectors and ~50 sub-industries is a small cross-section, so IC is noisy
and power is low; history starts 2017 (the bars start 2016-08 and rs252 needs a
year). Survivorship as everywhere in this project.

Run: python sector_study.py
"""
from __future__ import annotations

import json
import sys
import time
import datetime as dt

import numpy as np
import pandas as pd

import calendar_us
import config
import sectors as S

COMPONENTS = ["rs21", "rs63", "rs126", "rs252", "breadth200", "hilo", "fund"]
HORIZONS = (21, 63)
SUMMARY = config.DATA / "_sector_study_summary.json"
MIN_GROUPS = 8


def log(m: str) -> None:
    print(f"  {m}", flush=True)


def tstat(a) -> float:
    a = np.asarray([x for x in a if np.isfinite(x)])
    return float(a.mean() / (a.std(ddof=1) / np.sqrt(len(a)))) if len(a) >= 5 else float("nan")


def month_starts(idx: pd.Index) -> list[int]:
    s = pd.Series(range(len(idx)), index=idx)
    first = s.groupby(idx.str[:7]).min()
    return sorted(first.values.tolist())


def fund_history() -> pd.DataFrame:
    """date x ticker fund_score from the score store (all stored sessions)."""
    fr = []
    for f in sorted(config.SCORES.glob("*.parquet")):
        s = pd.read_parquet(f, columns=["session", "module", "metric", "ticker", "value"])
        s = s[(s.module == "fundamental") & (s.metric == "fund_score")]
        if len(s):
            fr.append(s[["session", "ticker", "value"]])
    if not fr:
        return pd.DataFrame()
    s = pd.concat(fr)
    return s.pivot_table(index="session", columns="ticker", values="value", aggfunc="first").sort_index()


def build_panel(level: str, C, R, E, FH) -> pd.DataFrame:
    """One row per (date, group): components and forward outcomes."""
    GR, N = S.group_returns(R, E, level)
    U = R.where(E.shift(1).fillna(False).astype(bool)).mean(axis=1)
    lg = np.log1p(GR.fillna(0.0)).where(GR.notna())
    lu = np.log1p(U.fillna(0.0))
    cum_g = lg.fillna(0.0).cumsum()
    cum_u = lu.cumsum()
    sma200 = C.rolling(200, min_periods=200).mean()
    hi252 = C.rolling(252, min_periods=200).max()
    lo252 = C.rolling(252, min_periods=200).min()
    g = S.groups().set_index("ticker")[level]
    idx = C.index
    rows = []
    for i in month_starts(idx):
        if i < 252 or i + 21 >= len(idx):
            continue
        D = idx[i]
        el = E.iloc[i]
        mem_all = el[el].index
        fund_row = None
        if FH is not None and len(FH):
            ok = FH.index[FH.index <= D]
            if len(ok) and (pd.Timestamp(D) - pd.Timestamp(ok[-1])).days <= 30:
                fund_row = FH.loc[ok[-1]]
        lab = g.reindex(mem_all)
        by_group = {k: list(v) for k, v in lab.groupby(lab).groups.items()}
        for name in GR.columns:
            if N[name].iloc[i] < S.MIN_MEMBERS:
                continue
            mem = by_group.get(name, [])
            if len(mem) < S.MIN_MEMBERS:
                continue
            r = {"date": D, "group": name, "n": len(mem)}
            for k in (21, 63, 126, 252):
                r[f"rs{k}"] = float((cum_g[name].iloc[i] - cum_g[name].iloc[i - k])
                                    - (cum_u.iloc[i] - cum_u.iloc[i - k]))
            c = C.iloc[i][mem]
            r["breadth200"] = float((c > sma200.iloc[i][mem]).mean())
            r["hilo"] = float((c >= hi252.iloc[i][mem] * 0.98).mean() - (c <= lo252.iloc[i][mem] * 1.02).mean())
            r["fund"] = float(fund_row.reindex(mem).median()) if fund_row is not None else np.nan
            for h in HORIZONS:
                if i + h < len(idx):
                    r[f"fwd{h}"] = float((cum_g[name].iloc[i + h] - cum_g[name].iloc[i])
                                         - (cum_u.iloc[i + h] - cum_u.iloc[i]))
                else:
                    r[f"fwd{h}"] = np.nan
            rows.append(r)
    return pd.DataFrame(rows)


def ic_series(p: pd.DataFrame, comp: str, h: int, dates=None) -> pd.Series:
    out = {}
    q = p if dates is None else p[p.date.isin(dates)]
    for D, g in q.groupby("date"):
        g = g[[comp, f"fwd{h}"]].dropna()
        if len(g) >= MIN_GROUPS and g[comp].nunique() > 2:
            out[D] = g[comp].rank().corr(g[f"fwd{h}"].rank())
    return pd.Series(out, dtype=float)


def analyse(level: str, p: pd.DataFrame, summary: dict) -> None:
    print(f"\n=== {level.upper()}: {p.group.nunique()} groups, {p.date.nunique()} monthly dates "
          f"{p.date.min()}..{p.date.max()}, median {p.groupby('date').size().median():.0f} groups/date")
    dates_all = sorted(p.date.unique())
    res = {}
    print(f"  {'component':12} {'h':>3} {'dates':>5} {'mean IC':>8} {'t':>6} | {'top-bottom third':>16}")
    for h in HORIZONS:
        ds = dates_all if h == 21 else dates_all[::3]
        for comp in COMPONENTS:
            ic = ic_series(p, comp, h, ds)
            # top third minus bottom third of groups, next-h excess
            spread = []
            for D, g in p[p.date.isin(ds)].groupby("date"):
                g = g[[comp, f"fwd{h}"]].dropna()
                if len(g) >= MIN_GROUPS:
                    k = max(1, len(g) // 3)
                    o = g.sort_values(comp)
                    spread.append(o[f"fwd{h}"].tail(k).mean() - o[f"fwd{h}"].head(k).mean())
            res[f"{comp}_h{h}"] = {"dates": int(len(ic)), "ic": float(ic.mean()) if len(ic) else None,
                                   "t": tstat(ic.values), "spread": float(np.mean(spread)) if spread else None,
                                   "spread_t": tstat(spread)}
            r = res[f"{comp}_h{h}"]
            if r["dates"] >= 5:
                print(f"  {comp:12} {h:>3} {r['dates']:>5} {r['ic']:>+8.3f} {r['t']:>+6.2f} | "
                      f"{r['spread'] * 100:>+8.2f}pp t {r['spread_t']:+.2f}")
            else:
                print(f"  {comp:12} {h:>3} {r['dates']:>5}  too few dates")
    # walk-forward composite
    comp_res = {}
    for h in HORIZONS:
        ds = dates_all if h == 21 else dates_all[::3]
        half = len(ds) // 2
        first, second = ds[:half], ds[half:]
        chosen = {}
        for comp in COMPONENTS:
            ic = ic_series(p, comp, h, first)
            t = tstat(ic.values)
            if np.isfinite(t) and abs(t) >= 2:
                chosen[comp] = float(np.sign(t))
        if not chosen:
            print(f"  composite h{h}: no component reached |t| >= 2 on the first half -- nothing to build")
            comp_res[f"h{h}"] = {"chosen": {}, "oos_ic": None, "oos_t": None}
            continue
        q = p[p.date.isin(second)].copy()
        q["score"] = 0.0
        for comp, sg in chosen.items():
            q["score"] += sg * q.groupby("date")[comp].rank(pct=True)
        q["score"] /= len(chosen)
        ic = ic_series(q, "score", h)
        comp_res[f"h{h}"] = {"chosen": chosen, "oos_ic": float(ic.mean()) if len(ic) else None,
                             "oos_t": tstat(ic.values), "oos_dates": int(len(ic)),
                             "train": [str(first[0]), str(first[-1])], "test": [str(second[0]), str(second[-1])]}
        print(f"  composite h{h}: chosen on {first[0]}..{first[-1]} = {chosen}; OUT OF SAMPLE "
              f"{second[0]}..{second[-1]}: IC {ic.mean():+.3f}, t {tstat(ic.values):+.2f} over {len(ic)} dates")
    summary[level] = {"groups": int(p.group.nunique()), "dates": int(p.date.nunique()),
                      "first": str(p.date.min()), "last": str(p.date.max()),
                      "components": res, "composite": comp_res}


def main() -> int:
    t0 = time.time()
    C, V = S.load_wide()
    log(f"wide matrix {C.shape[0]} dates x {C.shape[1]} tickers ({time.time() - t0:.0f}s)")
    R = S.daily_returns(C)
    E = S.eligible(C, V)
    FH = fund_history()
    log(f"fund_score history: {len(FH)} sessions")
    summary = {"measured_on": dt.date.today().isoformat(), "tests": 32}
    for level in ("sector", "subind"):
        p = build_panel(level, C, R, E, FH)
        p.to_parquet(config.DATA / f"_sector_panel_{level}.parquet", index=False)
        analyse(level, p, summary)
    SUMMARY.write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    log(f"wrote {SUMMARY.name}; 32 tests, ~1.6 expected to pass by chance; {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
