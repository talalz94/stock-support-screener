#!/usr/bin/env python
"""Do the bounce trade's filters that each passed alone add up? (the bounce-trade refinements' last step)

    python bounce_filters_study.py --build      the plan trade with real-order fills on every at-support
                                                name-date -> data/_bounce_filters_trades.npz
    python bounce_filters_study.py              the combinations -> data/_bounce_filters_study.json
    python bounce_filters_study.py --selftest   the fills on made-up bars

Planned 2026-11-02 ("Filters are combined only after each passes alone, and a combination is chosen
on pre-2022 dates and judged after 2022 (walk-forward)"); asked again 2026-11-15 ("do both narrative
work and bounce trade refinements"). Research screens, not investment advice.

DESIGN, fixed 2026-11-15 before any combination was measured:
  DATA     trade_rules_study's at-support name-dates (data/_trade_rules_trades.parquet: 175,937 on 217
           dates, 2018-01-02 .. 2026-08-07). TRAIN before 2022-01-01 (halves 2018-19 and 2020-21),
           TEST from 2022.
  TRADE    the shortlist's plan: a buy limit at the level + 0.5%, live 3 sessions; target +3% and
           stop -8% from the fill; out at the 10th session's close; 0.10% a round trip.
           FILLS as a resting limit order would get them, in the repo for the first time (the 11-08
           figure, +0.36% t 2.42, came from a one-off script): an open at or under the limit fills at
           that open; otherwise the low must trade 0.2% THROUGH the limit, filled at the limit. The
           fill day runs in 30-minute order from the fill bar (on that bar the stop counts and the
           target does not), then daily bars; a fill day without 30-minute bars runs on daily bars
           the same way (counted). The same code with no trade-through must give back the 11-08
           `limit30` rule exactly: the check of the code (`--build` prints it).
           Also measured with every combination, as checks: the same trade with touch fills (the
           11-08 `limit30|f3_8_h10`) and the default (next open, +5% / -5%, 10 sessions).
  FILTERS  the three that each passed alone, point in time, each cut into thirds AT THE SIGNAL among
           that date's at-support names (metric_evidence cut `dist` among the filled trades, which is
           not known at the signal):
             D  distance: the furthest third above the level (metric_evidence: kept with the limit
                rule, TEST +0.35pp, t 2.39);
             B  sector breadth: the stock's sector in the top third of sectors by the share of members
                above their 200-day, on the newest monthly panel at or before the date (rotation_study:
                kept with this trade, TEST +0.15pp, t 2.34);
             F  fund_score: the top third among the names with a score (the shortlist's default
                ranking; its top 20 was kept under the default trade, TEST +0.22pp t 2.21, but not
                under the limit rule, t 1.02).
  COMBOS   every AND of D, B and F: 7, the three singles included. Each against every at-support
           name under the same trade: per date the filtered mean trade minus the mean of all, t
           across dates.
  CHOICE   on TRAIN only, with the real-order trade: the combination with the highest TRAIN t among
           those with a filled trade on >= 80% of TRAIN dates. KEPT by the house rule (both TRAIN
           halves and TEST the same sign, TEST |t| >= 2). A combination of two or three must also
           beat its best single filter on TEST to add anything.
  CAVEAT   each filter was picked with the 2022-26 TEST in view (1 of 36, 3 of 321, the best of 11
           rankings), so TEST is not clean for any of them: a pass is a lead, and only trades after
           the studies -- the shortlist's forward ledger -- can confirm it.
  COUNT    7 combinations x 3 trades = 21 comparisons, plus the chosen one against its best single
           under each trade (3): about 1.2 reach |t| >= 2 by chance.

RESULT, 2026-11-15 (data/_bounce_filters_study.json; --build 37 s, the combinations 120 s):
  CHECK    with no trade-through the code gives back the 11-08 `limit30` rule on all 175,937
           name-dates: the same 99,787 trades, every return identical. Fill rates 56.7% (touch) and
           53.9% (0.2% through), as on 11-08.
  TRADE    every at-support name, real-order fills: TRAIN +0.45% a trade (t 2.60; 2018-19 +0.25,
           2020-21 +0.66), TEST +0.34% (t 2.25), positive in 8 of 9 years (2018 -0.23): KEPT. +5/-5
           +0.35% (t 2.19). The one-off script of 11-08 had said +0.36% (t 2.42) and +0.38% (t 2.37).
  CHOSEN   on TRAIN: F alone, fund_score's top third (TRAIN +0.49pp over every at-support name,
           t 5.14). TEST +0.11pp (t 2.05): KEPT, just. Touch fills +0.14pp (t 2.70), the default
           +0.06pp (t 2.17). The chosen rule is a single filter, so the "beats its best single" check
           did not arise: 21 comparisons counted, 1.1 expected at |t| >= 2 by chance; F and B+F kept
           under all three trades (the same names each time, so not six separate passes).
  NOT ADDED  D alone TEST +0.07pp (t 0.77); B alone +0.13pp (t 2.06, 2020-21 +0.00: not kept); B+F
           kept on TEST (+0.21pp, t 2.12) but not the TRAIN choice (t 2.83 against F's 5.14). POST HOC
           (after seeing TEST, so a lead at most): B+F minus F alone, TEST +0.10pp (t 1.03), 2020-21
           -0.29: sector breadth adds nothing on top of fund_score.
  DISTANCE  metric_evidence's KEPT for `dist` came from cutting the thirds among the FILLED trades
           (reproduced exactly: TEST +0.35pp, t 2.39); cut at the signal, the same rule gives +0.22pp
           (t 1.18) with 2018-19 negative. metric_evidence cuts at the signal from 2026-11-15.
  SO       for the shortlist, no change: it already ranks by fund_score, and neither the distance nor
           the breadth filter adds to it. Its forward ledger records every ranking (limit_ready is the
           distance ranking) and each entry's breadth third, so all three are judged again on trades
           made after these studies.
"""
from __future__ import annotations

import json
import sys
import time
import warnings
from datetime import datetime, timezone
from itertools import combinations

import numpy as np
import pandas as pd

import config
import trade_rules_study as TR

THRU = 0.002
EXITS = ("f3_8_h10", "f5_5_h10")          # the plan, and the symmetric alternative the pages show
PLAN = "f3_8_h10"
OUT_NPZ = config.DATA / "_bounce_filters_trades.npz"
OUT = config.DATA / "_bounce_filters_study.json"
FILTERS = ("D", "B", "F")
NAMES = {"D": "furthest third above the level", "B": "sector breadth top third", "F": "fund_score top third"}
MIN_TRAIN_COVER = 0.80


# ------------------------------------------------------------------------- the fills
def fill_day(O: np.ndarray, L: np.ndarray, lim: np.ndarray, thru: float, days: int = TR.LIMIT_DAYS):
    """The fill on daily bars: the first of `days` sessions whose open is at or under the limit
    (filled at that open) or whose low trades `thru` through it (filled at the limit, coming down
    inside the bar). With thru 0 this is trade_rules_study.entries' limit. -> (row, price, inside)"""
    k = O.shape[1]
    row = np.full(k, -1, dtype=np.int64)
    px = np.full(k, np.nan)
    inside = np.zeros(k, dtype=bool)
    with np.errstate(invalid="ignore"):
        for d in range(min(days, O.shape[0])):
            todo = row < 0
            at_o = todo & np.isfinite(O[d]) & (O[d] <= lim)
            dip = todo & ~at_o & np.isfinite(L[d]) & (L[d] <= lim * (1 - thru))
            px[at_o], row[at_o] = O[d][at_o], d
            px[dip], row[dip], inside[dip] = lim[dip], d, True
    return row, px, inside


def fill_bar(B: dict, m: np.ndarray, lim: np.ndarray, thru: float):
    """The fill inside its day, among the names in `m`: the first 30-minute bar whose open is at or
    under the limit (that open) or whose low trades `thru` through it (the limit, inside the bar).
    With thru 0 this is build_intraday's E2'. -> (slot, price, inside)"""
    k = len(lim)
    slot = np.full(k, -1, dtype=np.int64)
    px = np.full(k, np.nan)
    inside = np.zeros(k, dtype=bool)
    with np.errstate(invalid="ignore"):
        for sl in range(B["open"].shape[0]):
            todo = m & (slot < 0)
            o, lo = B["open"][sl], B["low"][sl]
            at_o = todo & np.isfinite(o) & (o <= lim)
            dip = todo & ~at_o & np.isfinite(lo) & (lo <= lim * (1 - thru))
            px[at_o], slot[at_o] = o[at_o], sl
            px[dip], slot[dip], inside[dip] = lim[dip], sl, True
    return slot, px, inside


def day_returns(P: dict, bars: dict, lvl: np.ndarray, atr: np.ndarray, thru: float,
                exits: tuple[str, ...] = EXITS) -> tuple[dict, np.ndarray, np.ndarray]:
    """One signal date's names: the limit's fill and every exit's net return.
    P     daily paths, rows = the sessions after the signal (row 0 the next session)
    bars  {fill row: that session's 30-minute bars}, read only for the rows that fill
    -> ({exit: net return}, filled, fell back to daily bars)"""
    spec = {x[0]: x for x in TR.exits()}
    k = len(lvl)
    lim = lvl * (1 + TR.LIMIT_UP)
    drow, dpx, din = fill_day(P["open"], P["low"], lim, thru)
    s2 = np.full(k, -1, dtype=np.int64)
    px2 = np.full(k, np.nan)
    in2 = np.zeros(k, dtype=bool)
    for r, B in bars.items():
        s, p, ins = fill_bar(B, drow == r, lim, thru)
        got = s >= 0
        s2[got], px2[got], in2[got] = s[got], p[got], ins[got]
    fb = (drow >= 0) & (s2 < 0)                  # daily bars saw the fill, the 30-minute bars did not
    e_px = np.where(s2 >= 0, px2, dpx)
    zero = np.zeros(k, dtype=np.int64)
    out = {}
    for e in exits:
        _, kd, xa, xb, h = spec[e]
        tgt, stp, okx = TR._exit_prices(kd, xa, xb, e_px, atr, lvl)
        ok = (drow >= 0) & okx & np.isfinite(e_px) & (e_px > 0)
        res = np.full(k, np.nan)
        for r, B in bars.items():
            m = ok & (drow == r) & (s2 >= 0)
            if not m.any():
                continue
            px, _, dn = TR.intraday_day(B, s2, in2, tgt, stp, m)
            er = zero + r
            pxd, _, _ = TR.simulate(P, er + 1, er + 1, np.zeros(k, bool), er, tgt, stp, er + h - 1, m & ~dn)
            res = np.where(m & dn, px, np.where(m, pxd, res))
        m = ok & fb
        if m.any():
            er = np.where(m, drow, 0)
            pxd, _, _ = TR.simulate(P, er, er + 1, din, er, tgt, stp, er + h - 1, m)
            res = np.where(m, pxd, res)
        with np.errstate(invalid="ignore", divide="ignore"):
            out[e] = np.where(ok & np.isfinite(res), res / e_px - 1.0 - TR.COST, np.nan)
    return out, drow >= 0, fb


def build(verbose: bool = True) -> dict:
    """Every at-support name-date's plan trade with the 0.2% trade-through fill, and the same code
    with none (the check against the 11-08 `limit30` rule) -> OUT_NPZ."""
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    import big_movers_study as BM
    t0 = time.time()
    T = pd.read_parquet(TR.OUT_TRADES)
    dates, ticks, W = BM.load_ohlc()
    if verbose:
        print(f"loaded the store: {len(dates):,} sessions x {len(ticks):,} names; {time.time() - t0:.0f}s", flush=True)
    di = {d: i for i, d in enumerate(dates)}
    ti = {t: j for j, t in enumerate(ticks)}
    nmax = TR.LIMIT_DAYS + max(TR.HOLDS) + 2
    n = len(T)
    res = {f"{v}|{e}": np.full(n, np.nan, dtype=np.float32) for v in ("thru", "touch") for e in EXITS}
    flags = {f"{w}_{v}": np.zeros(n, dtype=bool) for w in ("filled", "fallback") for v in ("thru", "touch")}
    for n_done, (D, g) in enumerate(T.groupby("date", sort=True)):
        rows = g.index.to_numpy()
        i = di[D]
        tk = g.ticker.to_numpy()
        js = g.ticker.map(ti).to_numpy()
        P = {c: W[c][i + 1:i + 1 + nmax][:, js].astype(np.float64) for c in ("open", "high", "low", "close")}
        lvl = g.level.to_numpy(dtype=float)
        atr = g.atr_pct.to_numpy(dtype=float)
        lim = lvl * (1 + TR.LIMIT_UP)
        # the 30-minute bars of every session either fill could fall on (read once per date)
        need = set()
        for v, thru in (("thru", THRU), ("touch", 0.0)):
            r0, _, _ = fill_day(P["open"], P["low"], lim, thru)
            need |= {int(r) for r in np.unique(r0[r0 >= 0])}
        bars = {r: TR._bars30(str(dates[i + 1 + r]), tk) for r in sorted(need)}
        for v, thru in (("thru", THRU), ("touch", 0.0)):
            r0, _, _ = fill_day(P["open"], P["low"], lim, thru)
            out, filled, fb = day_returns(P, {r: bars[r] for r in np.unique(r0[r0 >= 0])}, lvl, atr, thru)
            for e in EXITS:
                res[f"{v}|{e}"][rows] = out[e]
            flags[f"filled_{v}"][rows], flags[f"fallback_{v}"][rows] = filled, fb
        if verbose and n_done % 20 == 0:
            print(f"  {D}: {len(rows):,} at support; {time.time() - t0:.0f}s", flush=True)
    np.savez_compressed(OUT_NPZ, **{k.replace("|", "__"): v for k, v in res.items()}, **flags,
                        thru=np.array(THRU), built_at=np.array(datetime.now(timezone.utc).isoformat(timespec="seconds")))
    rep = check_against_limit30()
    rep["seconds"] = round(time.time() - t0)
    if verbose:
        print(json.dumps(rep, indent=1))
    return rep


def load() -> dict:
    Z = np.load(OUT_NPZ)
    return {k.replace("__", "|"): Z[k] for k in Z.files}


def check_against_limit30() -> dict:
    """The code's check: with no trade-through it must give the 11-08 `limit30` rule back (the
    30-minute bars where they show the fill, the daily rule where they do not, as analyse_intraday)."""
    Z, ZI, B = np.load(TR.OUT_NPZ), np.load(TR.OUT_NPZ_I), load()
    R, RI = list(Z["rules"]), list(ZI["rules"])
    out = {"thru": float(B["thru"]), "name_dates": int(len(B["filled_thru"]))}
    for e in EXITS:
        ref = ZI["ret"][:, RI.index(f"limit30|{e}")].astype(float)
        fb = ZI["fallback"]
        ref[fb] = Z["ret"][fb, R.index(f"limit|{e}")]
        new = B[f"touch|{e}"].astype(float)
        both = np.isfinite(ref) & np.isfinite(new)
        out[e] = {"same_trade_or_none": int((np.isfinite(ref) == np.isfinite(new)).sum()),
                  "only_11_08": int((np.isfinite(ref) & ~np.isfinite(new)).sum()),
                  "only_now": int((~np.isfinite(ref) & np.isfinite(new)).sum()),
                  "return_within_1e-6": int((np.abs(ref[both] - new[both]) <= 1e-6).sum()),
                  "both_traded": int(both.sum()),
                  "max_abs_diff": float(np.nanmax(np.abs(ref[both] - new[both]))) if both.any() else None}
    out["fill_rate_touch"] = round(float(B["filled_touch"].mean()), 4)
    out["fill_rate_thru"] = round(float(B["filled_thru"].mean()), 4)
    out["fallback_thru"] = int(B["fallback_thru"].sum())
    return out


# ------------------------------------------------------------------------- the filters
def filter_flags(T: pd.DataFrame) -> pd.DataFrame:
    """D, B and F per at-support name-date (1.0 in the filter, 0.0 not, NaN not knowable), point in time."""
    import metric_evidence as ME
    import rotation_study as RS
    out = pd.DataFrame(index=T.index)
    # D: thirds of the distance above the level, among the date's at-support names
    pct = T.groupby("date")["dist"].rank(pct=True)
    out["D"] = np.where(T.dist.notna(), (pct > 2 / 3).astype(float), np.nan)
    # B: rotation_study's own sector tercile of breadth (3 = top)
    S = RS.trade_states(T[["date", "ticker", "sector"]], {lv: RS.load_panel(lv) for lv in ("sector", "subind")})
    b = S["sector:breadth200"].to_numpy(dtype=float)
    out["B"] = np.where(np.isnan(b), np.nan, (b == 3).astype(float))
    # F: fund_score's thirds among the date's at-support names with a score
    dates = sorted(T.date.unique())
    names = T.groupby("date").ticker.agg(set).to_dict()
    Sc = ME.scores_asof(dates, names, verbose=False)
    f = T[["date", "ticker"]].merge(Sc[["date", "ticker", "fund_score"]], on=["date", "ticker"], how="left")["fund_score"]
    f.index = T.index
    fp = f.groupby(T.date).rank(pct=True)
    out["F"] = np.where(f.notna(), (fp > 2 / 3).astype(float), np.nan)
    return out


def trades() -> dict[str, np.ndarray]:
    """The three trades every combination is measured under, one return per at-support name-date."""
    Z, ZI, B = np.load(TR.OUT_NPZ), np.load(TR.OUT_NPZ_I), load()
    R, RI = list(Z["rules"]), list(ZI["rules"])
    touch = ZI["ret"][:, RI.index(f"limit30|{PLAN}")].astype(float)
    fb = ZI["fallback"]
    touch[fb] = Z["ret"][fb, R.index(f"limit|{PLAN}")]
    return {"plan_real_fills": B[f"thru|{PLAN}"].astype(float),
            "plan_touch_fills": touch,
            "default_open_5_5": Z["ret"][:, R.index(TR.DEFAULT)].astype(float)}


# ------------------------------------------------------------------------- the questions
def combos() -> list[tuple[str, ...]]:
    return [c for n in (1, 2, 3) for c in combinations(FILTERS, n)]


def measure(T: pd.DataFrame, ret: np.ndarray, Fl: pd.DataFrame) -> dict:
    """Every combination under one trade: the filtered per-date mean minus every at-support name's."""
    date = T.date.to_numpy()
    base = pd.Series(ret).groupby(date).mean()
    res = {}
    for c in combos():
        m = np.ones(len(T), dtype=bool)
        for f in c:
            m &= Fl[f].to_numpy() == 1.0
        x = pd.Series(np.where(m, ret, np.nan))
        per = x.groupby(date).mean()
        cnt = x.notna().groupby(date).sum()
        tr_dates = base.index < TR.SPLIT
        res["+".join(c)] = {
            "minus_all": TR.summary(per - base), "filtered": TR.summary(per),
            "names_per_date": int(pd.Series(m).groupby(date).sum().median()),
            "trades_per_date": float(cnt.median()),
            "train_cover": round(float((cnt[tr_dates] > 0).mean()), 3),
            "_per": per}
    res["_all"] = {"all": TR.summary(base), "_per": base}
    return res


def analyse(verbose: bool = True) -> dict:
    warnings.filterwarnings("ignore")
    t0 = time.time()
    T = pd.read_parquet(TR.OUT_TRADES)
    Fl = filter_flags(T)
    if verbose:
        print(f"filters on {len(T):,} name-dates: " + ", ".join(
            f"{f} {Fl[f].eq(1).mean():.1%} in, {Fl[f].isna().mean():.1%} unknown" for f in FILTERS)
              + f"; {time.time() - t0:.0f}s", flush=True)
    X = trades()
    out = {"measured_on": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "definitions": __doc__.strip(), "name_dates": int(len(T)), "dates": int(T.date.nunique()),
           "train_dates": int((T.date.drop_duplicates() < TR.SPLIT).sum()),
           "test_dates": int((T.date.drop_duplicates() >= TR.SPLIT).sum()),
           "filter_share": {f: round(float(Fl[f].eq(1).mean()), 4) for f in FILTERS},
           "filter_unknown": {f: round(float(Fl[f].isna().mean()), 4) for f in FILTERS},
           "filter_overlap": {f"{a}&{b}": round(float((Fl[a].eq(1) & Fl[b].eq(1)).mean()), 4)
                              for a, b in combinations(FILTERS, 2)},
           "trades": {}}
    n_tests = 0
    chosen = None
    for name, ret in X.items():
        M = measure(T, ret, Fl)
        if name == "plan_real_fills":
            ok = {k: v for k, v in M.items() if not k.startswith("_") and v["train_cover"] >= MIN_TRAIN_COVER
                  and v["minus_all"]["train_t"] is not None}
            chosen = max(ok, key=lambda k: ok[k]["minus_all"]["train_t"]) if ok else None
        sec = {k: {kk: vv for kk, vv in v.items() if kk != "_per"} for k, v in M.items()}
        sec["fill_rate"] = round(float(np.isfinite(ret).mean()), 4)
        n_tests += len(combos())
        if chosen and "+" in chosen:
            singles = chosen.split("+")
            best1 = max(singles, key=lambda s: M[s]["minus_all"]["train_t"] or -99)
            sec["chosen_minus_best_single"] = {"single": best1, **TR.summary(M[chosen]["_per"] - M[best1]["_per"])}
            n_tests += 1
        out["trades"][name] = sec
    out["chosen_on_train"] = chosen
    out["tests_counted"] = n_tests
    out["expected_abs_t_ge_2_by_chance"] = round(n_tests * 0.05, 1)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    tmp.replace(OUT)
    if verbose:
        report(out)
        print(f"wrote {OUT.name}; {time.time() - t0:.0f}s")
    return out


def _f(x, spec: str) -> str:
    """A number for the printout; a missing one shows as n/a, never as a zero."""
    return "n/a" if x is None else format(x, spec)


def report(out: dict) -> None:
    print(f"\n{out['name_dates']:,} at-support name-dates on {out['dates']} dates "
          f"(TRAIN {out['train_dates']}, TEST {out['test_dates']}); chosen on TRAIN: {out['chosen_on_train']}")
    print("in each filter:", out["filter_share"], "| unknown:", out["filter_unknown"], "| overlaps:", out["filter_overlap"])
    for name, sec in out["trades"].items():
        a = sec["_all"]["all"]
        print(f"\n{name}: fill rate {sec['fill_rate']:.1%}; every at-support name: TRAIN {a['train_pp']}% "
              f"(t {a['train_t']}), TEST {a['test_pp']}% (t {a['test_t']})")
        print(f"  {'filter':<6} {'names':>5} {'trades':>6} {'cover':>5} | filtered minus all, pp a trade: "
              f"{'2018-19':>8} {'2020-21':>8} {'TRAIN':>8} {'t':>6} {'TEST':>8} {'t':>6}  kept")
        for c in ["+".join(x) for x in combos()]:
            s, m = sec[c], sec[c]["minus_all"]
            print(f"  {c:<6} {s['names_per_date']:>5} {s['trades_per_date']:>6.0f} {s['train_cover']:>5.2f} | "
                  f"{'':>32}{_f(m['train_h1_pp'], '+8.3f'):>8} {_f(m['train_h2_pp'], '+8.3f'):>8} "
                  f"{_f(m['train_pp'], '+8.3f'):>8} {_f(m['train_t'], '6.2f'):>6} {_f(m['test_pp'], '+8.3f'):>8} "
                  f"{_f(m['test_t'], '6.2f'):>6}  {m['kept']}")
        if "chosen_minus_best_single" in sec:
            c = sec["chosen_minus_best_single"]
            print(f"  {out['chosen_on_train']} minus {c['single']} alone: TRAIN {c['train_pp']} (t {c['train_t']}), "
                  f"TEST {c['test_pp']} (t {c['test_t']}), kept {c['kept']}")


# ------------------------------------------------------------------------- the self-test
def selftest() -> int:
    """The fills on made-up bars, each case worked by hand."""
    bad = 0

    def ok(name: str, cond: bool, got=None) -> None:
        nonlocal bad
        bad += not cond
        print(f"  {'ok ' if cond else 'BAD'} {name}" + ("" if cond else f": {got}"))

    lim = np.array([100.0])
    nan = np.nan
    # daily fills: (opens, lows) over 4 sessions -> (row, price, inside)
    cases = [("an open under the limit fills at the open", [99.0, 99, 99, 99], [98, 98, 98, 98], 0.002, (0, 99.0, False)),
             ("a low that only touches does not fill with trade-through", [101, 101, 101, 101], [100.0, 100, 100, 100], 0.002, (-1, nan, False)),
             ("... and fills at the limit without it", [101, 101, 101, 101], [100.0, 100, 100, 100], 0.0, (0, 100.0, True)),
             ("a low 0.25% through fills at the limit, inside the bar", [101, 101, 101, 101], [101, 99.75, 99, 99], 0.002, (1, 100.0, True)),
             ("a low 0.21% through fills", [101, 101, 101, 101], [101, 101, 99.79, 99], 0.002, (2, 100.0, True)),
             ("only the first 3 sessions count", [101, 101, 101, 99], [101, 101, 101, 98], 0.002, (-1, nan, False))]
    for name, o, lo, thru, want in cases:
        r, p, ins = fill_day(np.array(o, float)[:, None], np.array(lo, float)[:, None], lim, thru)
        got = (int(r[0]), float(p[0]), bool(ins[0]))
        same = got[0] == want[0] and got[2] == want[2] and (np.isnan(want[1]) and np.isnan(got[1]) or got[1] == want[1])
        ok(name, same, got)
    # inside the day: 4 bars
    B = {"open": np.array([[101.0], [100.4], [99.9], [99.0]]), "low": np.array([[100.1], [99.85], [99.5], [98.0]]),
         "high": np.array([[101.5], [100.6], [100.2], [99.5]]), "close": np.array([[100.4], [99.9], [99.6], [99.2]])}
    s, p, ins = fill_bar(B, np.array([True]), lim, 0.002)
    ok("bar 2's low (0.15% through) does not fill; bar 3 opens under the limit: filled at its open",
       (int(s[0]), float(p[0]), bool(ins[0])) == (2, 99.9, False), (s, p, ins))
    s, p, ins = fill_bar(B, np.array([True]), lim, 0.0)
    ok("with touch fills bar 2 fills at the limit, inside", (int(s[0]), float(p[0]), bool(ins[0])) == (1, 100.0, True), (s, p, ins))
    s, p, ins = fill_bar(B, np.array([False]), lim, 0.0)
    ok("a name outside the mask is left alone", int(s[0]) == -1, s)
    def day(**cols) -> dict:          # a session's 30-minute bars: the given ones, then no trades
        return {c: np.array([[v] for v in cols[c]] + [[nan]] * (TR.SLOTS - len(cols[c]))) for c in cols}

    # a whole trade: the fill comes down inside bar 1 of session 0; that bar's high would reach a 3%
    # target but cannot count; the target is reached on session 1's daily bar -> +3% less costs
    lvl = np.array([100.0 / (1 + TR.LIMIT_UP)])
    P = {"open": np.array([[101.0], [101.5], [101.0]] + [[101.0]] * 12),
         "high": np.array([[103.5], [103.2], [101.0]] + [[101.0]] * 12),
         "low": np.array([[99.7], [100.5], [100.5]] + [[100.5]] * 12),
         "close": np.array([[101.0], [102.0], [101.0]] + [[101.0]] * 12)}
    B0 = day(open=[101.0, 100.5], high=[101.2, 103.5], low=[100.6, 99.7], close=[100.6, 101.0])
    out, filled, fb = day_returns(P, {0: B0}, lvl, np.array([0.02]), 0.002, exits=("f3_8_h10",))
    ok("the fill bar's high does not count; the next session's does: +3% less costs",
       bool(filled[0]) and not bool(fb[0]) and abs(float(out["f3_8_h10"][0]) - (0.03 - TR.COST)) < 1e-9, (out, filled, fb))
    # the same day without 30-minute bars: the daily bars, the same way
    out, filled, fb = day_returns(P, {}, lvl, np.array([0.02]), 0.002, exits=("f3_8_h10",))
    ok("no 30-minute bars: the daily bars give the same trade, counted as a fallback",
       bool(fb[0]) and abs(float(out["f3_8_h10"][0]) - (0.03 - TR.COST)) < 1e-9, (out, filled, fb))
    # a stop on the fill bar does count: the fill bar's low reaches -8%
    B1 = day(open=[101.0, 100.5], high=[101.2, 100.6], low=[100.6, 91.0], close=[100.6, 92.0])
    P1 = {**P, "low": np.array([[91.0], [100.5], [100.5]] + [[100.5]] * 12)}
    out, filled, fb = day_returns(P1, {0: B1}, lvl, np.array([0.02]), 0.002, exits=("f3_8_h10",))
    ok("a stop inside the fill bar counts: -8% less costs",
       abs(float(out["f3_8_h10"][0]) - (-0.08 - TR.COST)) < 1e-9, out)
    print("selftest:", "PASS" if not bad else f"{bad} FAILED")
    return 1 if bad else 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    if "--build" in sys.argv:
        build()
        return 0
    if "--check" in sys.argv:
        print(json.dumps(check_against_limit30(), indent=1))
        return 0
    analyse()
    return 0


if __name__ == "__main__":
    sys.exit(main())
