#!/usr/bin/env python
"""The daily shortlist (plan item 3): which ranking of at-support stocks works best?

    python shortlist.py --backtest     every ranking's top-N on the step-10 dates, judged like
                                       items 1-2 -> data/_shortlist_backtest.json (~4 min)

Asked 2026-11-08: a ranked daily shortlist plus a filter page; for the ranking, "try all and give
the best option and others too"; size "lets start with 20 but it should be dynamic and
compatible". Research screens, not investment advice.

BACKTEST DESIGN, fixed 2026-11-08 before any result was seen:
  WHO      the at-support name-dates of trade_rules_study.py (217 dates, 2018-01..2026-08),
           with their trades under the default rule (open, +5% / -5%, 10 sessions) and item 1's
           TRAIN-chosen rule (limit at the level + 0.5% for 3 sessions, 3 x ATR target and
           stop, 10 sessions).
  RANKINGS (RANKINGS below) each date's top N = 20 by the metric (ties by ticker), as of the
           date (scores: each module's latest stored session <= the date, <= 31 days old):
           limit_ready (distance above the level, largest first), combo_h20, combo_h60,
           fund_score, growth_score, gain_days (the 10-23 list's rule), rally_count,
           recovery_rate (>= 2 dips), insider_score, sentiment (sent_mean_30d), low_vol
           (63-day volatility, smallest first).
  MEASURE  per date, the top-N's mean trade return minus every at-support name's mean (the
           plain rule), t across dates; TRAIN before 2022 / TEST after; TRAIN halves at 2020;
           KEPT = both halves and TEST agree in sign and TEST |t| >= 2. The BEST ranking is
           the one with the highest TRAIN t under the chosen rule; the others are reported
           beside it. A top-20 is noisy, so the same is measured for 200 random 20s per date
           (the spread chance alone gives).
"""
from __future__ import annotations

import json
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import config

N_DEFAULT = 20
RANKINGS = {
    "limit_ready": ("dist", False),
    "combo_h20": ("combo_h20", False),
    "combo_h60": ("combo_h60", False),
    "fund_score": ("fund_score", False),
    "growth_score": ("growth_score", False),
    "gain_days": ("gain_days", False),
    "rally_count": ("rally_count", False),
    "recovery_rate": ("recovery_rate", False),
    "insider_score": ("insider_score", False),
    "sentiment": ("sent_mean_30d", False),
    "low_vol": ("vol63", True),
}
SCORE_METRICS = ["combo_h20", "combo_h60", "fund_score", "growth_score", "insider_score", "sent_mean_30d"]
SPLIT, HALF = "2022-01-01", "2020-01-01"
OUT_BT = config.DATA / "_shortlist_backtest.json"
RANDOM_DRAWS = 200


def _summary(x: pd.Series) -> dict:
    import trade_rules_study as TR
    return TR.summary(x)


def backtest(n: int = N_DEFAULT, verbose: bool = True) -> dict:
    warnings.filterwarnings("ignore")
    import metric_evidence as ME
    import trade_rules_study as TR
    t0 = time.time()
    T = pd.read_parquet(TR.OUT_TRADES)
    Z = np.load(TR.OUT_NPZ)
    R = list(Z["rules"])
    q1 = json.loads(TR.OUT.read_text(encoding="utf-8"))["q1"]["chosen"]
    T["y_default"] = Z["ret"][:, R.index(TR.DEFAULT)]
    T["y_q1"] = Z["ret"][:, R.index(q1)]
    T.loc[T.dips.fillna(0) < 2, "recovery_rate"] = np.nan
    dates = sorted(T.date.unique())
    names = T.groupby("date").ticker.agg(set).to_dict()
    S = ME.scores_asof(dates, names, verbose=False)
    S = S[["date", "ticker"] + [m for m in SCORE_METRICS if m in S.columns]]
    T = T.merge(S, on=["date", "ticker"], how="left")
    if verbose:
        print(f"loaded {len(T):,} at-support name-dates with scores; {time.time() - t0:.0f}s", flush=True)
    base = {y: T.groupby("date")[y].mean() for y in ("y_default", "y_q1")}
    out = {"measured_on": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "definitions": __doc__.strip(), "n": n, "q1_rule": q1, "rankings": {}}
    for name, (col, asc) in RANKINGS.items():
        top = (T[T[col].notna()].sort_values(["date", col, "ticker"], ascending=[True, asc, True])
               .groupby("date").head(n))
        r = {"metric": col, "ascending": asc,
             "names_per_date": int(top.groupby("date").size().median()) if len(top) else 0}
        for y in ("y_default", "y_q1"):
            m = top.groupby("date")[y].mean()
            r[y] = {"top_n": _summary(m), "top_minus_all": _summary(m - base[y]),
                    "fill_rate": round(float(top[y].notna().mean()), 3)}
        out["rankings"][name] = r
    rng = np.random.default_rng(20261108)
    rnd = {y: [] for y in ("y_default", "y_q1")}
    for D, g in T.groupby("date"):
        idx = g.index.to_numpy()
        if len(idx) < n:
            continue
        for y in rnd:
            v = g[y].to_numpy()
            draws = [np.nanmean(v[rng.choice(len(idx), n, replace=False)]) for _ in range(RANDOM_DRAWS)]
            rnd[y].append((D, float(np.nanstd(draws)), float(np.nanmean(v))))
    out["random_top_n_spread_pp"] = {y: round(float(np.mean([s for _, s, _ in v])) * 100, 3) for y, v in rnd.items()}
    trn = {k: v["y_q1"]["top_minus_all"].get("train_t") for k, v in out["rankings"].items()}
    best = max((k for k, v in trn.items() if v is not None), key=lambda k: trn[k], default=None)
    out["best_on_train"] = best
    out["train_t_by_ranking"] = trn
    return out


# ------------------------------------------------------------------------- the daily build
RECIPES_FILE = config.ROOT / "shortlist_recipes.json"
DIR = config.DATA / "shortlist"
LEDGER = DIR / "ledger.parquet"
OUTCOMES = DIR / "outcomes.parquet"
PAGE = config.REPORTS / "shortlist"
LIMIT_UP, LIMIT_DAYS, HOLD = 0.005, 3, 10
# Item 1's rule chosen on TRAIN dates once the 30-minute bars were in (2026-11-08): a buy limit at
# the level + 0.5% for 3 sessions, target +3% and stop -8% from the fill, out after 10 sessions.
# 2022-26 with fills that must trade 0.2% through the limit: +0.36% a trade (t 2.42), positive in
# 8 of 9 years. +5% / -5% is the symmetric alternative (+0.38%, t 2.37).
TGT, STP = 0.03, 0.08
TGT_ALT, STP_ALT = 0.05, 0.05
COST = 0.001


def recipes() -> dict:
    cfg = json.loads(RECIPES_FILE.read_text(encoding="utf-8"))
    names = [r["name"] for r in cfg["recipes"]]
    assert cfg["default"] in names, f"default {cfg['default']!r} is not a recipe"
    assert 1 <= int(cfg["n"]) <= int(cfg["max_n"]), "n must be between 1 and max_n"
    return cfg


# ------------------------------------------------------------- what the Settings page edits
# The four choices a reader makes; the rankings themselves stay in the file. The candidates end
# 6% above their level (gainers_at_support keeps zones 0-6% below the close), so a cap above 6
# would filter nothing.
SETTINGS_DEFAULTS = {"default": "fund_score", "n": 20, "exclude_warnings": True, "max_above_level_pct": 6.0}
ABOVE_LEVEL_RANGE = (0.5, 6.0)


def ui_state() -> dict:
    cfg = recipes()
    f = cfg.get("filters") or {}
    return {"default": cfg["default"], "n": int(cfg["n"]),
            "exclude_warnings": bool(f.get("exclude_warnings", False)),
            "max_above_level_pct": float(f.get("max_above_level_pct", ABOVE_LEVEL_RANGE[1]))}


def ui_meta() -> dict:
    """What the Settings page needs to draw the choices: every ranking with its evidence."""
    cfg = recipes()
    return {"max_n": int(cfg["max_n"]), "above_range": list(ABOVE_LEVEL_RANGE),
            "recipes": [{"name": r["name"], "label": r["label"], "evidence": r.get("evidence", "")}
                        for r in cfg["recipes"]]}


def apply_ui(values) -> tuple[dict, list[str]]:
    """Check what the Settings page sent and save it to shortlist_recipes.json. Returns (the
    page's new state, problems); nothing is saved when there is any problem.

    The file is written by hand too ("Edit freely"), so only the three edited values are
    replaced in place: a save shows in git as exactly the lines that changed. The result is
    parsed back and must equal the intended settings; if the file's layout ever defeats the
    in-place edit, it is rewritten whole rather than saved wrong."""
    import math
    import os
    import re
    if not isinstance(values, dict):
        return ui_state(), ["expected an object of settings"]
    want = set(SETTINGS_DEFAULTS)
    missing, extra = sorted(want - set(values)), sorted(set(values) - want)
    if missing or extra:
        return ui_state(), ([f"missing: {', '.join(missing)}"] if missing else []) + \
                           ([f"unknown: {', '.join(extra)}"] if extra else [])
    cfg = recipes()
    names = [r["name"] for r in cfg["recipes"]]
    errs = []
    if values["default"] not in names:
        errs.append(f"default must be one of: {', '.join(names)}")
    n = values["n"]
    if not (isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= int(cfg["max_n"])):
        errs.append(f"n must be a whole number from 1 to {cfg['max_n']}")
    if not isinstance(values["exclude_warnings"], bool):
        errs.append("exclude_warnings must be true or false")
    x = values["max_above_level_pct"]
    lo, hi = ABOVE_LEVEL_RANGE
    if not (isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and lo <= x <= hi):
        errs.append(f"max_above_level_pct must be a number from {lo} to {hi}")
    if errs:
        return ui_state(), errs

    filters = {**(cfg.get("filters") or {}), "exclude_warnings": values["exclude_warnings"],
               "max_above_level_pct": round(float(x), 2)}
    expect = {**cfg, "default": values["default"], "n": n, "filters": filters}
    raw = RECIPES_FILE.read_text(encoding="utf-8")
    text = re.sub(r'(?m)^(\s*"default"\s*:\s*)"[^"\n]*"', lambda m: m.group(1) + json.dumps(values["default"]), raw, count=1)
    text = re.sub(r'(?m)^(\s*"n"\s*:\s*)\d+', lambda m: m.group(1) + str(n), text, count=1)
    text = re.sub(r'(?m)^(\s*"filters"\s*:\s*)\{[^{}]*\}', lambda m: m.group(1) + json.dumps(filters), text, count=1)
    try:
        ok = json.loads(text) == expect
    except ValueError:
        ok = False
    if not ok:
        text = json.dumps(expect, indent=1, ensure_ascii=False) + "\n"
    tmp = RECIPES_FILE.with_suffix(".json.tmp")
    tmp.write_bytes(text.encode("utf-8"))            # bytes: the file's own line endings, untranslated
    os.replace(tmp, RECIPES_FILE)
    return ui_state(), []


def _rs_for(C: pd.DataFrame, V: pd.DataFrame, asof: str, tickers: list[str]) -> pd.DataFrame:
    """rs_sec_63 / rs_sec_252 / rs_mkt_63 / rs_sub_63 on `asof`, as rs_study.py (the stock left
    out of its own group), from the last 300 sessions only."""
    import glob
    import rs_study as RSS
    import sectors
    Cs, Vs = C.iloc[-300:], V.iloc[-300:]
    E = sectors.eligible(Cs, Vs)
    R = sectors.daily_returns(Cs)
    g = sectors.groups().drop_duplicates("ticker").set_index("ticker")
    ls = np.log1p(R)
    rel_sec = ls - np.log1p(RSS._loo_group(R, E, g["sector"].reindex(Cs.columns), sectors.MIN_MEMBERS))
    rel_sub = ls - np.log1p(RSS._loo_group(R, E, g["subind"].reindex(Cs.columns), sectors.MIN_MEMBERS))
    e = pd.concat(pd.read_parquet(p, columns=["ticker", "date", "close"])
                  for p in sorted(glob.glob(str(config.BARS_ETF / "*.parquet"))))
    e["date"] = e.date.astype(str).str[:10]
    spy = e[e.ticker == "SPY"].drop_duplicates("date").set_index("date").close.sort_index()
    rel_mkt = ls.sub(np.log1p(spy.pct_change(fill_method=None).reindex(Cs.index)), axis=0)

    def win(A: pd.DataFrame, w: int) -> pd.Series:
        x = A.iloc[-w:]
        return x.sum(min_count=int(0.8 * w)).reindex(tickers)
    return pd.DataFrame({"rs_sec_63": win(rel_sec, 63), "rs_sec_252": win(rel_sec, 252),
                         "rs_mkt_63": win(rel_mkt, 63), "rs_sub_63": win(rel_sub, 63)})


def candidates(asof: str) -> pd.DataFrame:
    """Every liquid name at or near support on `asof` (the Gainers-at-support universe), with
    every column the shortlist and its page use, and the trade plan of item 1's chosen rule."""
    import big_movers_study as BM
    import gainers_at_support as GA
    import sectors
    import store
    _, D = GA.build(asof)
    D = D.reset_index(drop=True)
    tk = D.ticker.tolist()
    sc = pd.read_parquet(config.DATA / "scores" / f"{asof[:7]}.parquet")
    sc = sc[sc.session.astype(str).str[:10] == asof]
    piv = sc.pivot_table(index="ticker", columns="metric", values="value", aggfunc="last")
    for m in ("combo_h60", "growth_score", "insider_score", "sent_mean_30d", "mktcap"):
        D[m] = D.ticker.map(piv[m]) if m in piv.columns else np.nan
    C, V = sectors.load_wide()
    C, V = C.loc[:asof], V.loc[:asof]
    r63 = C.iloc[-64:][tk].pct_change(fill_method=None).iloc[1:]
    D["vol63"] = r63.std(ddof=1).reindex(tk).to_numpy() * np.where(r63.notna().sum().reindex(tk) >= 50, 1, np.nan)
    rc, dips, rec = [], [], []
    for t in tk:
        c = C[t].to_numpy(dtype=float) if t in C.columns else np.array([])
        i = len(c) - 1
        ra = np.asarray(BM.rallies_of(c.tolist()), dtype=np.int64) if len(c) >= BM.YEAR else np.array([], dtype=np.int64)
        rc.append(float(((ra > i - BM.YEAR) & (ra <= i)).sum()) if len(c) >= BM.YEAR else np.nan)
        if len(c) >= BM.YEAR:
            dr, ok = BM.dips_of(c)
            m = (dr > i - BM.YEAR) & (dr <= i - BM.DIP_BACK)
            dips.append(float(m.sum()))
            rec.append(float(ok[m].mean()) if m.sum() >= 2 else np.nan)
        else:
            dips.append(np.nan)
            rec.append(np.nan)
    D["rally_count"], D["dips"], D["recovery_rate"] = rc, dips, rec
    lo = str((pd.Timestamp(asof) - pd.Timedelta(days=45)).date())
    b = store.read(interval="1d", start=lo, end=asof, tickers=tk, columns=["ticker", "date", "high", "low", "close"])
    b["date"] = b.date.astype(str).str[:10]
    b = b.sort_values(["ticker", "date"])
    b["pc"] = b.groupby("ticker").close.shift(1)
    b["tr"] = np.fmax(b.high, b.pc) - np.fmin(b.low, b.pc)
    atr = b.groupby("ticker").tail(20).groupby("ticker").agg(atr=("tr", "mean"), n=("tr", "count"))
    D["atr_pct"] = (D.ticker.map(atr.atr.where(atr.n >= 15)) / D.price)
    D = D.join(_rs_for(C, V, asof, tk), on="ticker")
    D["limit"] = D.level * (1 + LIMIT_UP)
    D["target"] = D.limit * (1 + TGT)
    D["stop"] = D.limit * (1 - STP)
    D["target_alt"] = D.limit * (1 + TGT_ALT)
    D["stop_alt"] = D.limit * (1 - STP_ALT)
    return D


def build(asof: str, verbose: bool = True) -> dict:
    cfg = recipes()
    D = candidates(asof)
    f = cfg.get("filters", {})
    pool = D
    if f.get("exclude_warnings"):
        pool = pool[pool.warning.fillna("") == ""]
    if "max_above_level_pct" in f:
        pool = pool[pool.above_level_pct <= float(f["max_above_level_pct"])]
    lists = {}
    for r in cfg["recipes"]:
        col = r["rank_by"]
        x = pool[pool[col].notna()].sort_values([col, "ticker"], ascending=[bool(r["ascending"]), True])
        lists[r["name"]] = x.head(int(cfg["max_n"])).assign(rank=lambda d: np.arange(1, len(d) + 1))
    out = DIR / asof
    out.mkdir(parents=True, exist_ok=True)
    D.to_parquet(out / "candidates.parquet", index=False)
    # relative strength for EVERY liquid name, not only those at support: the Explore page's
    # vs-sector / vs-market columns and filters read it (explore_columns)
    import sectors
    C, V = sectors.load_wide()
    C, V = C.loc[:asof], V.loc[:asof]
    dv20 = (C * V).rolling(20, min_periods=15).mean()
    liq = [t for t in C.columns if C.at[asof, t] >= 5 and dv20.at[asof, t] >= 20e6]
    _rs_for(C, V, asof, liq).rename_axis("ticker").reset_index().to_parquet(out / "rs_all.parquet", index=False)
    for name, x in lists.items():
        x.to_csv(out / f"{name}.csv", index=False)
    led = pd.concat([x.assign(date=asof, recipe=name)[["date", "recipe", "rank", "ticker", "price", "level",
                                                        "limit", "atr_pct"]]
                     for name, x in lists.items()], ignore_index=True)
    old = pd.read_parquet(LEDGER) if LEDGER.exists() else pd.DataFrame(columns=led.columns)
    old = old[old.date != asof]
    pd.concat([old, led], ignore_index=True).to_parquet(LEDGER, index=False)
    res = {"asof": asof, "candidates": int(len(D)), "pool": int(len(pool)),
           "lists": {k: int(len(v)) for k, v in lists.items()}}
    if verbose:
        print(f"shortlist {asof}: {len(D):,} liquid names at/near support, {len(pool):,} after filters; "
              f"{len(lists)} recipes -> {out}", flush=True)
    return res


def evaluate(verbose: bool = True) -> pd.DataFrame:
    """Score every ledger entry whose 3 limit days + 10 hold days have passed, under item 1's
    chosen rule and the default (open, +5% / -5%), with trade_rules_study's own engine."""
    import store
    import trade_rules_study as TR
    if not LEDGER.exists():
        return pd.DataFrame()
    L = pd.read_parquet(LEDGER)
    done = pd.read_parquet(OUTCOMES) if OUTCOMES.exists() else pd.DataFrame(columns=["date", "recipe", "ticker"])
    have = set(zip(done.date, done.recipe, done.ticker))
    todo = L[[(d, r, t) not in have for d, r, t in zip(L.date, L.recipe, L.ticker)]]
    if todo.empty:
        return done
    axis = TR._axis()
    pos = {d: i for i, d in enumerate(axis)}
    need = LIMIT_DAYS + HOLD + 2
    rows = []
    for D, g in todo.groupby("date"):
        i = pos.get(D)
        if i is None or i + need >= len(axis):
            continue                                             # its window has not passed yet
        u = g.drop_duplicates("ticker")
        sess = axis[i + 1:i + 1 + need]
        b = store.read(interval="1d", start=sess[0], end=sess[-1], tickers=u.ticker.tolist(),
                       columns=["ticker", "date", "open", "high", "low", "close"])
        b["date"] = b.date.astype(str).str[:10]
        P = {c: b.pivot_table(index="date", columns="ticker", values=c).reindex(index=sess, columns=u.ticker)
             .to_numpy(dtype=float) for c in ("open", "high", "low", "close")}
        E = TR.entries(P, u.level.to_numpy(dtype=float))
        a = u.atr_pct.to_numpy(dtype=float)
        out = {}
        e_px, e_row, start, gap_from, intra, ok0 = E["limit"]
        for tag, tg, sp in (("limit", TGT, STP), ("limit55", TGT_ALT, STP_ALT)):
            px, kk, hh = TR.simulate(P, start, gap_from, intra, e_row, e_px * (1 + tg), e_px * (1 - sp),
                                     e_row + HOLD - 1, ok0)
            out[f"{tag}_ret"] = np.where(ok0 & np.isfinite(px), px / e_px - 1 - COST, np.nan)
            out[f"{tag}_kind"] = np.where(ok0, kk, 0)
        out["limit_filled"] = ok0
        e_px, e_row, start, gap_from, intra, ok0 = E["open"]
        px, kk, hh = TR.simulate(P, start, gap_from, intra, e_row, e_px * 1.05, e_px * 0.95, e_row + HOLD - 1, ok0)
        out["open_ret"] = np.where(ok0 & np.isfinite(px), px / e_px - 1 - COST, np.nan)
        out["open_kind"] = np.where(ok0, kk, 0)
        res = pd.DataFrame({"ticker": u.ticker.to_numpy(), **out})
        rows.append(g[["date", "recipe", "rank", "ticker"]].merge(res, on="ticker", how="left"))
    if rows:
        done = pd.concat([done] + rows, ignore_index=True)
        done.to_parquet(OUTCOMES, index=False)
    if verbose:
        print(f"shortlist outcomes: {len(done):,} scored entries", flush=True)
    return done


# ------------------------------------------------------------------------- Explore's columns
EXPLORE_COLS = ["at_support", "above_level_pct", "buy_limit", "target", "stop", "atr_pct",
                "gain_days", "loss_days", "rally_count", "recovery_rate",
                "rs_sec_63", "rs_sec_252", "rs_sub_63", "rs_mkt_63", "narrative_now"]


def explore_columns(asof: str) -> pd.DataFrame:
    """Ticker-indexed numeric columns for explore.collect, from the newest shortlist build on or
    before `asof`: the support and trade-plan columns for names at support (blank elsewhere),
    relative strength for every liquid name, at_support 1/0 for every liquid name, and the
    group's weekly AI narrative ('now', -2..+2) once a checked snapshot exists."""
    import sectors
    days = sorted(p.name for p in DIR.glob("????-??-??") if p.name <= asof and (p / "candidates.parquet").exists())
    if not days:
        return pd.DataFrame(columns=EXPLORE_COLS)
    d = DIR / days[-1]
    D = pd.read_parquet(d / "candidates.parquet").drop_duplicates("ticker").set_index("ticker")
    out = pd.DataFrame(index=D.index)
    out["above_level_pct"] = D.above_level_pct
    out["buy_limit"], out["target"], out["stop"] = D.limit, D.target, D.stop
    for c in ("atr_pct", "gain_days", "loss_days", "rally_count", "recovery_rate"):
        out[c] = D[c]
    rs_file = d / "rs_all.parquet"
    if rs_file.exists():
        rs = pd.read_parquet(rs_file).drop_duplicates("ticker").set_index("ticker")
        out = out.join(rs, how="outer")
    out["at_support"] = np.where(out.index.isin(D.index), 1.0, 0.0)
    try:
        import narrative
        nl = narrative.latest(asof)
        if len(nl):
            by = {(r.level, r.group): float(r.now) for r in nl.itertuples()}
            g = sectors.groups().drop_duplicates("ticker").set_index("ticker")
            gg = g.reindex(out.index)
            out["narrative_now"] = [by.get(("subind", s), by.get(("sector", c)))
                                    for s, c in zip(gg.subind, gg.sector)]
    except Exception:                                            # noqa: BLE001
        pass
    out.attrs["asof"] = days[-1]
    return out.reindex(columns=EXPLORE_COLS)


# ------------------------------------------------------------------------- the page
COLS = [  # (key, header, kind) -- kind: t text, p price, pc percent (x100), n number, i int
    ("rank", "#", "i"), ("ticker", "Ticker", "t"), ("name", "Name", "t"), ("sector", "Sector", "t"),
    ("subind", "Sub-industry", "t"), ("price", "Price", "p"), ("level", "Level", "p"),
    ("above_level_pct", "Above level %", "n"), ("limit", "Buy limit", "p"), ("target", "Target +3%", "p"),
    ("stop", "Stop -8%", "p"), ("target_alt", "Alt target +5%", "p"), ("stop_alt", "Alt stop -5%", "p"), ("atr_pct", "ATR %", "pc"), ("fund", "Fund", "n"), ("combo_h60", "Combo 3m", "n"),
    ("combo_h20", "Combo 1m", "n"), ("gain_days", "Gainer days", "i"), ("rally_count", "Rallies", "i"),
    ("recovery_rate", "Dips won back", "pc"), ("rs_sec_63", "vs sector 3m", "pc"),
    ("rs_sub_63", "vs sub-ind 3m", "pc"), ("rs_mkt_63", "vs market 3m", "pc"), ("earn_sessions", "Earnings in (sessions)", "i"),
    ("warning", "Warning", "t"), ("insider", "Insider", "t"), ("narrative", "Narrative (group)", "t"),
]
# Column views (2026-11-09: 28 columns at once was "too much info"). Rank, ticker and name always
# show; earnings, a warning filing and insider buying ride on the ticker as tags in every view.
# (key, label, what it shows, columns -- None = all of them)
IDENTITY = ("rank", "ticker", "name")
VIEWS = (
    ("plan", "Trade plan", "the price, the support level and the tested plan: buy limit, target +3%, stop -8%",
     ["price", "level", "above_level_pct", "limit", "target", "stop", "atr_pct"]),
    ("alt", "Alternative plan", "the symmetric plan that tested about the same: target +5%, stop -5%",
     ["price", "level", "limit", "target_alt", "stop_alt", "atr_pct"]),
    ("scores", "Scores", "the business and combined scores, and how often the name runs and recovers",
     ["sector", "fund", "combo_h60", "combo_h20", "gain_days", "rally_count", "recovery_rate"]),
    ("strength", "Strength", "against its sector, sub-industry and the market, and the group's narrative",
     ["sector", "subind", "rs_sec_63", "rs_sub_63", "rs_mkt_63", "narrative"]),
    ("all", "All columns", "every column", None),
)
DEFAULT_VIEW = "plan"
# Off on a phone, in every view: the default view's table was 623px in a 309px box at 375 wide.
# What is left there -- ticker, price, buy limit, target, stop -- is the plan itself.
PHONE_HIDE = ("rank", "name", "level", "above_level_pct", "atr_pct")   # rows stay in rank order
CSS = """
.sl{font-size:13px}
.sl td.n,.sl th.n{text-align:right;font-variant-numeric:tabular-nums}
.sl td.tk{white-space:nowrap;font-weight:600}
.sl td.nm{color:var(--muted);max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ctl{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin:0 0 8px}
.ctl input[type=number]{width:64px}
.sl-ev{color:var(--muted);font-size:12px;margin:0 0 12px;max-width:90ch;line-height:1.45}
.sl-market{font-size:12.5px;color:var(--muted);margin:0 0 14px}
.sl-market b{color:var(--ink)}
.tg{margin-left:6px;padding:0 6px;border-radius:9px;font-size:10px;font-weight:600;white-space:nowrap}
.tg-e{background:var(--warnbg);color:var(--warn)}
.tg-w{background:var(--negbg);color:var(--neg)}
.tg-i{background:var(--blkbg);color:var(--blk)}
.sl-how dl{margin:0;display:grid;grid-template-columns:minmax(0,170px) minmax(0,1fr);gap:12px 20px}
.sl-how dt{font-weight:600}
.sl-how dd{margin:0;line-height:1.55;max-width:82ch}
@media (max-width:700px){
  .sl .ph{display:none}
  /* measured 430px of table in a 309px box at 375 wide: the tags sit under the ticker, the
     headers wrap, and the cells are tighter, so the plan fits without sideways scrolling */
  .sl td,.sl th{padding:6px}
  .sl th{white-space:normal}
  .sl td.tk{white-space:normal}
  .sl td.tk .tg{display:table;margin:2px 0 0}
  .ctl label:first-child{flex:1 1 100%}
  .ctl select{max-width:100%}
  .sl-how dl{grid-template-columns:minmax(0,1fr);gap:2px}
  .sl-how dd{margin-bottom:12px}
}
"""

PAGE_JS = r"""
const D = JSON.parse(document.getElementById('sl-data').textContent);
const sel = document.getElementById('sl-recipe'), num = document.getElementById('sl-n'),
      hideE = document.getElementById('sl-earn'), head = document.getElementById('sl-head'),
      tb = document.getElementById('sl-body'), ev = document.getElementById('sl-evidence'),
      cnt = document.getElementById('sl-count'), ncols = document.getElementById('sl-ncols');
const KIND = {}, LABEL = {};
D.cols.forEach(([k, h, kind]) => { KIND[k] = kind; LABEL[k] = h; });
const VKEYS = D.views.map(v => v.key), BUILT = new Set(D.built), PHONE = new Set(D.phone_hide);
// The view lives in this browser, like Explore's (scr.prefs.shortlist), and the Settings page edits it.
const PREFS = window.scrPrefs || {get: () => ({}), set: () => false};
function viewKey() { const s = PREFS.get().shortlist; const v = s && s.view; return VKEYS.indexOf(v) >= 0 ? v : D.default_view; }
D.recipes.forEach(r => { const o = document.createElement('option'); o.value = r.name;
  o.textContent = r.label + (r.name === D.default ? ' (first by your settings)' : ''); sel.appendChild(o); });
sel.value = D.default; num.value = D.n; num.max = D.max_n;
function fmt(v, k) { if (v === null || v === undefined || v === '' || (typeof v === 'number' && isNaN(v))) return '—';
  if (k === 'p') return Number(v).toFixed(2); if (k === 'pc') return (Number(v) * 100).toFixed(1) + '%';
  if (k === 'n') return Number(v).toFixed(1); if (k === 'i') return String(Math.round(Number(v))); return String(v); }
function esc(s) { return String(s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c])); }
// A stock page when one is built, else the stock list with the ticker filled in (never a dead link).
function href(t) { return BUILT.has(t) ? '../stock/' + encodeURIComponent(t) + '.html'
                                       : '../stock/index.html?t=' + encodeURIComponent(t); }
const WARN = [['unregistered', 'share sale'], ['material agreement', 'deal ended'], ['restat', 'restatement'],
              ['auditor', 'auditor change'], ['delist', 'delisting notice']];
function warnShort(t) { t = String(t).toLowerCase(); for (const [k, s] of WARN) if (t.indexOf(k) >= 0) return s; return 'warning'; }
const HOUR = {bmo: 'before the open', amc: 'after the close'};
function tags(x) {
  let h = '';
  const es = x.earn_sessions;
  if (es !== null && es !== undefined && es <= D.earn_soon && x.earnings) {
    const [d, hr] = String(x.earnings).split(' ');
    h += '<span class="tg tg-e" title="reports ' + esc(d) + (HOUR[hr] ? ' ' + HOUR[hr] : '') + '; the market reacts in '
       + Math.round(es) + ' session' + (Math.round(es) === 1 ? '' : 's') + '">earnings ' + esc(d.slice(5)) + '</span>';
  }
  if (x.warning) h += '<span class="tg tg-w" title="' + esc(x.warning) + '">⚠ ' + warnShort(x.warning) + '</span>';
  if (x.insider) h += '<span class="tg tg-i" title="' + esc(x.insider) + '">insider</span>';
  return h;
}
function draw() {
  const r = D.recipes.find(x => x.name === sel.value) || D.recipes[0];
  ev.textContent = r.evidence;
  const vk = viewKey(), view = D.views.find(v => v.key === vk);
  const cols = ['rank', 'ticker', 'name'].concat(view.cols);
  document.querySelectorAll('#sl-views button[data-view]').forEach(b => {
    const on = b.getAttribute('data-view') === vk; b.classList.toggle('on', on); b.setAttribute('aria-pressed', on ? 'true' : 'false'); });
  document.getElementById('sl-viewsel').value = vk;
  ncols.textContent = view.cols.length + ' of ' + (D.cols.length - 3) + ' columns';
  head.textContent = '';
  // Text columns carry `lab`: the shared table style right-aligns every cell that does not.
  cols.forEach(k => { const th = document.createElement('th'); th.textContent = LABEL[k];
    th.className = (KIND[k] !== 't' ? 'n' : 'lab') + (PHONE.has(k) ? ' ph' : ''); head.appendChild(th); });
  let rows = r.rows;
  if (hideE.checked) rows = rows.filter(x => !(x.earn_sessions !== null && x.earn_sessions <= D.earn_soon));
  const n = Math.max(1, Math.min(D.max_n, parseInt(num.value || D.n, 10)));
  rows = rows.slice(0, n);
  cnt.textContent = rows.length + ' of ' + r.rows.length + ' shown';
  const frag = document.createDocumentFragment();
  rows.forEach(x => { const tr = document.createElement('tr');
    cols.forEach(k => { const td = document.createElement('td');
      if (k === 'ticker') { td.className = 'tk lab'; td.innerHTML = '<a href="' + href(x.ticker) + '">' + esc(x.ticker) + '</a>' + tags(x); }
      else if (k === 'name') { td.className = 'nm lab ph'; td.textContent = x.name || ''; td.title = x.name || ''; }
      else { td.className = (KIND[k] !== 't' ? 'n' : 'lab') + (PHONE.has(k) ? ' ph' : ''); td.textContent = fmt(x[k], KIND[k]); }
      tr.appendChild(td); });
    frag.appendChild(tr); });
  tb.textContent = ''; tb.appendChild(frag);
}
function setView(v) { const p = PREFS.get(); p.shortlist = Object.assign({}, p.shortlist, {view: v}); PREFS.set(p); draw(); }
document.getElementById('sl-views').addEventListener('click', e => { const b = e.target.closest('button[data-view]'); if (b) setView(b.getAttribute('data-view')); });
document.getElementById('sl-viewsel').addEventListener('change', function () { setView(this.value); });
window.addEventListener('storage', e => { if (e.key === 'scr.prefs') draw(); });
sel.onchange = draw; num.oninput = draw; hideE.onchange = draw; draw();
"""


def _market_line(asof: str) -> str:
    """One line of market context, the same readings and words as Today (today_page), each
    dated when it lags the session. Notes behind the (i)."""
    import today_page as TP
    import ui
    bits = []
    try:
        t = TP.spy_trend(asof)
        if t:
            bits.append(f"S&amp;P 500 {t['vs50'] * 100:+.1f}% vs its 50-day, {t['vs200'] * 100:+.1f}% vs its 200-day"
                        + (f" (close of {TP._day(t['session'])})" if t["session"] != asof else ""))
    except Exception:                                            # noqa: BLE001
        pass
    try:
        r = TP.vix_reading(asof)
        if r:
            bits.append(f"VIX {r[0]:.1f}, " + ("20 or more: a nervous market" if r[0] >= 20 else "under 20: calm")
                        + (f" (reading of {TP._day(r[1])})" if r[1] != asof else ""))
    except Exception:                                            # noqa: BLE001
        pass
    if not bits:
        return ""
    note = ui.info("Measured on 2018-2026 bounce trades: trading only when the S&amp;P 500 was above its averages "
                   "did <b>not</b> help; a VIX of 20 or more helped in both periods but not reliably enough to keep. "
                   "Context, not a filter.")
    return "<b>Market</b> &middot; " + " &middot; ".join(bits) + note


FORWARD_NOTE = ("Net of 0.10% a round trip. The top N of each ranking per day (N = the size on the Settings page); a "
                "day counts once its 13-session window has passed (3 days for the limit to fill, 10 to hold). Scored "
                "on daily bars with the fill day pessimistic (its stop counts, its target does not), so a little "
                "below the 30-minute backtest.")


def _forward_table(cfg: dict) -> str:
    import ui
    if not OUTCOMES.exists():
        return ("<p class='muted' style='margin:0'>No entry scored yet: each needs its 3 limit days and 10 hold days "
                "to pass. The first scores appear about 13 sessions after the first list (2026-11-06).</p>")
    o = pd.read_parquet(OUTCOMES)
    o = o[o["rank"] <= int(cfg["n"])]
    if o.empty:
        return ("<p class='muted' style='margin:0'>No entry scored yet: each needs its 3 limit days and 10 hold "
                "days to pass.</p>")
    rows = []
    for r in cfg["recipes"]:
        x = o[o.recipe == r["name"]]
        if x.empty:
            continue
        lim = x.limit_ret.dropna()
        rows.append(f"<tr><td>{ui.esc(r['label'])}</td><td class='n'>{x.date.nunique()}</td><td class='n'>{len(x)}</td>"
                    f"<td class='n'>{x.limit_filled.mean() * 100:.0f}%</td>"
                    f"<td class='n'>{lim.mean() * 100:+.2f}%</td>"
                    f"<td class='n'>{(x.limit_kind == 1).sum() / max(len(lim), 1) * 100:.0f}% / {(x.limit_kind == -1).sum() / max(len(lim), 1) * 100:.0f}%</td>"
                    f"<td class='n'>{x.limit55_ret.dropna().mean() * 100:+.2f}%</td>"
                    f"<td class='n'>{x.open_ret.dropna().mean() * 100:+.2f}%</td></tr>")
    return ("<div class='scroll'><table class='sl'><thead><tr><th>Ranking</th><th class='n'>Days</th>"
            "<th class='n'>Entries</th><th class='n'>Limit filled</th><th class='n'>Limit +3/-8, mean a trade</th>"
            "<th class='n'>Target / stop hit</th><th class='n'>Limit +5/-5</th><th class='n'>Open +5/-5</th></tr></thead>"
            "<tbody>" + "".join(rows) + "</tbody></table></div>")


def render(asof: str) -> Path:
    import news_events as NE
    import ui
    cfg = recipes()
    d = DIR / asof
    D = pd.read_parquet(d / "candidates.parquet")
    narr = {}
    try:
        import narrative
        nl = narrative.latest(asof)
        narr = {(r.level, r.group): f"{r.now:+d} now / {r.m3:+d} 3m" for r in nl.itertuples()}
    except Exception:                                            # noqa: BLE001
        pass
    keys = [k for k, _, _ in COLS] + ["earnings"]           # `earnings` dates the earnings tag
    recs = []
    for r in cfg["recipes"]:
        p = d / f"{r['name']}.csv"
        x = pd.read_csv(p) if p.exists() else pd.DataFrame(columns=keys)
        x["narrative"] = [narr.get(("subind", s)) or narr.get(("sector", c)) for s, c in zip(x.get("subind", []), x.get("sector", []))]
        rows = [{k: (None if (isinstance(v, float) and not np.isfinite(v)) else v) for k, v in zip(keys, vals)}
                for vals in x.reindex(columns=keys).itertuples(index=False, name=None)]
        recs.append({"name": r["name"], "label": r["label"], "evidence": r["evidence"], "rows": rows})
    listed = {str(row["ticker"]) for rec in recs for row in rec["rows"] if row.get("ticker")}
    others = [k for k, _, _ in COLS if k not in IDENTITY]
    data = {"default": cfg["default"], "n": int(cfg["n"]), "max_n": int(cfg["max_n"]), "earn_soon": NE.EARN_SOON,
            "cols": COLS, "recipes": recs, "default_view": DEFAULT_VIEW,
            "views": [{"key": k, "label": label, "cols": list(cols) if cols else others}
                      for k, label, _h, cols in VIEWS],
            "built": sorted(listed & ui.built_profiles()), "phone_hide": list(PHONE_HIDE)}
    blob = json.dumps(data, default=str).replace("</", "<\\/")
    label = next((r["label"] for r in cfg["recipes"] if r["name"] == cfg["default"]), cfg["default"])

    # THREE TABS (2026-11-09: "too much info that needs to be organized properly and neatly"): the
    # list, its forward record, and how it works. Explanations sit in the last tab or behind (i).
    market = _market_line(asof)
    list_tab = (
        (f'<p class="sl-market">{market}</p>' if market else '')
        + '<div class="card"><div class="ctl">'
        '<label>Ranking <select id="sl-recipe"></select></label>'
        '<label>Show <input id="sl-n" type="number" min="1" inputmode="numeric"></label>'
        f'<label><input id="sl-earn" type="checkbox"> hide earnings within {NE.EARN_SOON} sessions</label>'
        '<span id="sl-count" class="muted"></span></div>'
        '<p id="sl-evidence" class="sl-ev"></p>'
        + ui.viewbar("sl-views", [(k, label_, h) for k, label_, h, _c in VIEWS], "sl-viewsel",
                     tools='<span id="sl-ncols" class="muted" style="font-size:12px"></span>')
        + '<div class="scroll"><table class="sl"><thead><tr id="sl-head"></tr></thead>'
          '<tbody id="sl-body"></tbody></table></div></div>')
    record_tab = ('<div class="card"><h3>How each ranking has done since tracking began'
                  f'{ui.info(FORWARD_NOTE)}</h3>{_forward_table(cfg)}</div>')
    how_tab = (
        '<div class="card sl-how"><dl>'
        '<dt>The trade plan</dt><dd>A buy limit at the support level + 0.5%, good for 3 sessions; then a target '
        'of +3% and a stop of &minus;8% from the fill, out after 10 sessions. Chosen on 2018-2021 dates with '
        '30-minute bars.</dd>'
        '<dt>What it measured</dt><dd>On 2022-2026, counting only fills that traded 0.2% through the limit, with '
        'the fill day in 30-minute order: +0.36% a trade after costs (t 2.42), positive in 8 of 9 years. About '
        '54% of orders fill. The stop is wider than the target, so it wins often and loses more when it '
        'loses.</dd>'
        '<dt>The alternative</dt><dd>+5% / &minus;5% did about the same: +0.38% a trade (t 2.37).</dd>'
        '<dt>When to buy</dt><dd>Buying at the next open earned about nothing after costs, and buying an hour '
        'after the open made no difference: the limit order at the level is what measured.</dd>'
        '<dt>The rankings</dt><dd>The first one shown is your choice on the <a href="../settings.html#shortlist">'
        'Settings</a> page (out of the box, the one that tested best). Every ranking is built and tracked forward '
        'each night, so the others keep competing: see Forward record. The size and the filters are set there '
        'too.</dd>'
        f'<dt>The tags</dt><dd><span class="tg tg-e" style="margin-left:0">earnings</span> reports within '
        f'{NE.EARN_SOON} sessions, inside the trade&rsquo;s window (more stop-outs: 41% against 34%). '
        '<span class="tg tg-w" style="margin-left:0">&#9888;</span> a recent warning filing, measured as negative '
        'for the weeks after. <span class="tg tg-i" style="margin-left:0">insider</span> a rare insider buy at '
        'the level.</dd>'
        '<dt>Narrative</dt><dd>The weekly AI view of the stock&rsquo;s sub-industry (else its sector), shown once '
        'the first weekly run has been checked. It carries no weight anywhere yet.</dd>'
        '</dl><p class="muted" style="margin:16px 0 0">Research screens, not investment advice.</p></div>')
    body = (ui.tabs("sltabs", [("list", "Today's list", "List", list_tab),
                               ("record", "Forward record", "Record", record_tab),
                               ("how", "How it works", "How", how_tab)], default="list")
            + f'<script type="application/json" id="sl-data">{blob}</script>')
    html = ui.page("Shortlist", body,
                   subtitle=(f"as of {asof} &middot; {len(D):,} liquid names at or near support &middot; "
                             f"first: <b>{ui.esc(label)}</b> (<a href=\"../settings.html#shortlist\">change</a>)"),
                   active="shortlist", depth=1, head=f"<style>{CSS}</style>", script=PAGE_JS)
    PAGE.mkdir(parents=True, exist_ok=True)
    p = PAGE / "latest.html"
    p.write_text(html, encoding="utf-8")
    return p


def selftest() -> int:
    """The Settings page's save, on a scratch copy of the recipes file (the real one is untouched):
    a save of unchanged values leaves the file byte for byte; a change touches only its own lines;
    bad input saves nothing; a layout the in-place edit cannot handle is rewritten, never saved wrong."""
    global RECIPES_FILE
    import shutil
    import tempfile
    real = RECIPES_FILE
    with tempfile.TemporaryDirectory() as td:
        RECIPES_FILE = Path(td) / "shortlist_recipes.json"
        shutil.copyfile(real, RECIPES_FILE)
        try:
            orig = RECIPES_FILE.read_bytes()
            st, errs = apply_ui(ui_state())
            assert not errs and RECIPES_FILE.read_bytes() == orig, ("a no-change save altered the file", errs)
            names = [r["name"] for r in recipes()["recipes"]]
            v = {"default": names[-1], "n": 7, "exclude_warnings": not st["exclude_warnings"], "max_above_level_pct": 4.5}
            st2, errs = apply_ui(v)
            assert not errs and st2 == v, (errs, st2)
            a, b = orig.decode().splitlines(), RECIPES_FILE.read_text(encoding="utf-8").splitlines()
            changed = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
            assert len(a) == len(b) and len(changed) == 3, f"changed lines {changed}"
            assert recipes()["recipes"] == json.loads(orig)["recipes"], "the rankings themselves changed"
            before = RECIPES_FILE.read_bytes()
            for bad in ({**v, "default": "nope"}, {**v, "n": 0}, {**v, "n": True}, {**v, "n": 3.0},
                        {**v, "n": recipes()["max_n"] + 1}, {**v, "max_above_level_pct": 7},
                        {**v, "max_above_level_pct": float("nan")}, {**v, "exclude_warnings": 1},
                        {**v, "extra": 1}, {k: x for k, x in v.items() if k != "n"}, [], None):
                _s, e = apply_ui(bad)
                assert e, f"accepted {bad!r:.100}"
                assert RECIPES_FILE.read_bytes() == before, "a refused change was saved"
            # a hand-made layout the in-place edit cannot follow: rewritten whole, still correct
            cfg = json.loads(before)
            cfg["filters"] = {"exclude_warnings": True, "nested": {"x": 1}, "max_above_level_pct": 6.0}
            RECIPES_FILE.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
            st3, errs = apply_ui({**v, "n": 9})
            got = recipes()
            assert not errs and got["n"] == 9 and got["filters"]["nested"] == {"x": 1}, (errs, got.get("filters"))
        finally:
            RECIPES_FILE = real
    print("  [shortlist] the Settings page's save: no-change save byte-identical, a change touches 3 "
          "lines, 12 kinds of bad input refused, an unfamiliar layout rewritten correctly")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    if "--build" in sys.argv:
        i = sys.argv.index("--build")
        asof = sys.argv[i + 1] if len(sys.argv) > i + 1 and not sys.argv[i + 1].startswith("-") else None
        if asof is None:
            import calendar_us
            asof = calendar_us.last_closed_session()
        build(asof)
        evaluate()
        print(f"page: {render(asof)}")
        return 0
    if "--backtest" in sys.argv:
        s = backtest()
        OUT_BT.write_text(json.dumps(s, indent=1, default=str), encoding="utf-8")
        print(f"wrote {OUT_BT.name}; best on TRAIN: {s['best_on_train']}")
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
