#!/usr/bin/env python
"""Today's sector and sub-industry readings, and the per-stock sector fields.

THE SCORE is the one sector_study.py validated out of sample: the average, across
sectors, of the percentile ranks of `rs21` (1-month return vs the universe) and
`breadth200` (share of members above their 200-day average). Those two were chosen
on 2017-09..2022-02 and then measured on 2022-03..2026-09, which they had never
seen: IC +0.141, t +4.10 over 55 months (+0.106, t +2.95 before the sector map was
repaired the same day -- see NEXT_SESSION); the top third of sectors beat the bottom
third by ~1pp a month over the full history. 0-100, higher = stronger.

SUB-INDUSTRIES are shown with the same components as CONTEXT, not scored: over the
full history rs252 (t +3.07) and breadth200 (t +3.19) predict at that level too, but
the walk-forward selection on the first half found nothing, so no validated score.

SEASONALITY is CONTEXT ONLY: season_study.py found no predictive value at any level
(sector t -0.37, sub-industry t -0.95, stock t +0.16 over 72 months). The coming
month's history is shown so the reader can see it -- never used in a ranking.

Writes data/sectors/<asof>.parquet (per ticker) and data/sectors/groups_<asof>.parquet.
Run: python sector_score.py [--asof D] [--selftest]
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd

import calendar_us
import config
import sectors as S

COMPOSITE = ("rs21", "breadth200")
OUT = config.DATA / "sectors"
SEASON = config.DATA / "_season_study_summary.json"
STUDY = config.DATA / "_sector_study_summary.json"
KEEP_DAYS = 400


def _fund_latest(asof: str) -> pd.Series:
    s = pd.read_parquet(config.SCORES / f"{asof[:7]}.parquet",
                        columns=["session", "module", "metric", "ticker", "value"])
    s = s[(s.module == "fundamental") & (s.metric == "fund_score") & (s.session <= asof)]
    if s.empty:
        return pd.Series(dtype=float)
    return s[s.session == s.session.max()].set_index("ticker")["value"]


def components_at(i: int, level: str, C, R, E, fund: pd.Series | None) -> pd.DataFrame:
    """Components for every group at row i -- the SAME formulas as
    sector_study.build_panel (the selftest checks they agree)."""
    GR, N = S.group_returns(R, E, level)
    U = R.where(E.shift(1).fillna(False).astype(bool)).mean(axis=1)
    lg = np.log1p(GR.fillna(0.0))
    lu = np.log1p(U.fillna(0.0))
    cg, cu = lg.cumsum(), lu.cumsum()
    # Same windows and minimum counts as the study's rolling(200, min_periods=200)
    # and rolling(252, min_periods=200) -- a mean over fewer days is a different number.
    w200 = C.iloc[max(0, i - 199):i + 1]
    sma = w200.mean().where(w200.count() >= 200)
    win = C.iloc[max(0, i - 251):i + 1]
    ok = win.count() >= 200
    hi, lo = win.max().where(ok), win.min().where(ok)
    g = S.groups().set_index("ticker")[level]
    el = E.iloc[i]
    mem_all = el[el].index
    lab = g.reindex(mem_all)
    by = {k: list(v) for k, v in lab.groupby(lab).groups.items()}
    c = C.iloc[i]
    rows = []
    for name in GR.columns:
        mem = by.get(name, [])
        if N[name].iloc[i] < S.MIN_MEMBERS or len(mem) < S.MIN_MEMBERS:
            continue
        r = {"group": name, "n": len(mem)}
        for k in (21, 63, 126, 252):
            r[f"rs{k}"] = float((cg[name].iloc[i] - cg[name].iloc[i - k]) - (cu.iloc[i] - cu.iloc[i - k])) \
                if i >= k else np.nan
        r["breadth200"] = float((c[mem] > sma[mem]).mean())
        r["hilo"] = float((c[mem] >= hi[mem] * 0.98).mean() - (c[mem] <= lo[mem] * 1.02).mean())
        r["fund"] = float(fund.reindex(mem).median()) if fund is not None and len(fund) else np.nan
        rows.append(r)
    return pd.DataFrame(rows)


def compute(asof: str | None = None):
    asof = asof or calendar_us.last_closed_session()
    C, V = S.load_wide()
    if asof not in C.index:
        raise RuntimeError(f"no bars on {asof}")
    i = int(np.nonzero(C.index == asof)[0][0])
    R, E = S.daily_returns(C), S.eligible(C, V)
    fund = _fund_latest(asof)
    season = json.loads(SEASON.read_text(encoding="utf-8")) if SEASON.exists() else {}
    tables = {}
    for level in ("sector", "subind"):
        t = components_at(i, level, C, R, E, fund)
        if level == "sector" and len(t):
            t["score"] = (sum(t[c].rank(pct=True) for c in COMPOSITE) / len(COMPOSITE) * 100).round(1)
            t["rank"] = t["score"].rank(ascending=False, method="min").astype(int)
        out = season.get("levels", {}).get(level, {}).get("outlook", {})
        t["season_month"] = season.get("levels", {}).get(level, {}).get("next_month")
        t["season_mean"] = t["group"].map(lambda gname: out.get(gname, {}).get("mean"))
        t["season_years"] = t["group"].map(lambda gname: out.get(gname, {}).get("years"))
        t["season_pos"] = t["group"].map(lambda gname: out.get(gname, {}).get("pos"))
        t["level"] = level
        tables[level] = t
    g = S.groups()
    sec = tables["sector"].set_index("group")
    sub = tables["subind"].set_index("group")
    per = pd.DataFrame({"ticker": g.ticker, "sector": g.sector, "subind": g.subind})
    per["sector_score"] = per.sector.map(sec["score"]) if "score" in sec else np.nan
    per["sector_rank"] = per.sector.map(sec["rank"]) if "rank" in sec else np.nan
    per["sector_n_groups"] = len(sec)
    per["subind_rs252"] = per.subind.map(sub["rs252"])
    per["subind_breadth200"] = per.subind.map(sub["breadth200"])
    per["session"] = asof
    groups = pd.concat(tables.values(), ignore_index=True).assign(session=asof)
    return per, groups


def write(asof: str | None = None):
    asof = asof or calendar_us.last_closed_session()
    per, groups = compute(asof)
    OUT.mkdir(parents=True, exist_ok=True)
    per.to_parquet(OUT / f"{asof}.parquet", index=False)
    groups.to_parquet(OUT / f"groups_{asof}.parquet", index=False)
    return per, groups


def load(asof: str | None = None) -> pd.DataFrame | None:
    """Per-ticker fields from the newest file on or before asof (None if none)."""
    if not OUT.exists():
        return None
    files = sorted(p for p in OUT.glob("20*.parquet") if p.stem <= (asof or "9999"))
    return pd.read_parquet(files[-1]) if files else None


def load_groups(asof: str | None = None) -> pd.DataFrame | None:
    if not OUT.exists():
        return None
    files = sorted(p for p in OUT.glob("groups_20*.parquet") if p.stem[7:] <= (asof or "9999"))
    return pd.read_parquet(files[-1]) if files else None


def selftest() -> int:
    """The live components must equal the study's on a date both computed."""
    import sector_study as SS
    p = pd.read_parquet(config.DATA / "_sector_panel_sector.parquet")
    D = str(p.date.max())
    C, V = S.load_wide()
    i = int(np.nonzero(C.index == D)[0][0])
    R, E = S.daily_returns(C), S.eligible(C, V)
    live = components_at(i, "sector", C, R, E, None).set_index("group")
    ref = p[p.date == D].set_index("group")
    common = live.index.intersection(ref.index)
    assert len(common) >= 10, f"only {len(common)} sectors in common on {D}"
    for c in ("rs21", "rs63", "rs126", "rs252", "breadth200", "hilo"):
        d = (live.loc[common, c] - ref.loc[common, c]).abs().max()
        assert d < 1e-6, f"{c} differs from the study by {d} on {D}"
    print(f"sector_score selftest OK  ({len(common)} sectors on {D} match the study exactly)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--asof", default=None)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    per, groups = write(a.asof)
    s = groups[groups.level == "sector"].sort_values("score", ascending=False)
    print(s[["group", "n", "score", "rs21", "rs252", "breadth200", "season_mean"]].round(3).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
