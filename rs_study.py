#!/usr/bin/env python
"""Is a stock that has been running ABOVE its sector a healthier bounce? (plan item 2)

    python rs_study.py --build    RS metrics for every liquid name-date of the step-10 study
                                  -> data/_rs_metrics.parquet (~3 min)
    python rs_study.py            the pre-registered tests -> data/_rs_study.json

Asked 2026-11-02: "how up/down is the stock relative to the market and its sector? like we would
imagine the stock line to follow its index/sector performance line but if its been above its
sector, thats good healthy sign." Research screens, not investment advice.

DESIGN, fixed 2026-11-08 before any result was seen:

  WHO      the step-10 events of big_movers_study.py (every 10th session from 2018-01, liquid
           names); the bounce trades of trade_rules_study.py for the at-support ones.
  INDEXES  the market = SPY (macro's ETF store). The sector and the sub-industry = equal-weight
           daily returns of the names eligible the prior day (sectors.eligible: the screener's
           own price and dollar-volume gates; returns clipped at +/-50% as sectors.py), WITH
           THE STOCK ITSELF LEFT OUT, >= 8 other members (sectors.MIN_MEMBERS), today's map.
  METRICS  at the signal close, from closes up to it:
             rs_sec_w   the stock's log return minus its sector's over w = 21 / 63 / 126 / 252
             rs_mkt_63  minus SPY's over 63;  rs_sub_63  minus its sub-industry's over 63
             line_up    the RS line (stock / sector, log) above its own 50-session average
             above_126  the share of the last 126 sessions the stock's line, rebased with its
                        sector's 126 sessions ago, sat above the sector's
             rs_high    the RS line within 2% of its 252-session high
             resid_252  the stock's 252-session return left after its beta to SPY
           PRIMARY: rs_sec_63 ("above its sector" over three months).
  TESTS    per date, t across dates, TRAIN = before 2022-01-01 / TEST after, TRAIN halves at
           2020-01-01; KEPT only if all three agree in sign and the TEST t >= 2 (as item 1).
    R1 at support, the bounce trade (the default open +5/-5/10, and item 1's chosen rule): each
       metric's top third minus its bottom third (binary metrics: on minus off).
    R2 the hot third at support ("a leader pulling back"): rs_sec_63 top minus bottom third.
    R3 the factor view over ALL liquid names: Spearman IC with the 20-session excess return
       (event_returns' liquidity-fifth pool); dates 10 apart, 20-session horizons overlap, so
       the t is Newey-West with one lag.
    BASELINE plain momentum (mom_12_1) through the same tests, and rs_sec_63 momentum-neutral
       (inside momentum thirds) -- does "above its sector" add anything momentum does not?
  CAVEATS  today's sector map; survivorship; equal weight (a sector ETF is cap-weighted).
"""
from __future__ import annotations

import json
import sys
import time
import warnings
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import config

EVENTS = config.DATA / "_big_movers_events_s10.parquet"
OUT_METRICS = config.DATA / "_rs_metrics.parquet"
OUT = config.DATA / "_rs_study.json"
WINDOWS = (21, 63, 126, 252)
SPLIT, HALF = "2022-01-01", "2020-01-01"
METRICS = ["rs_sec_21", "rs_sec_63", "rs_sec_126", "rs_sec_252", "rs_mkt_63", "rs_sub_63",
           "line_up", "above_126", "rs_high", "resid_252"]
BINARY = {"line_up", "rs_high"}
PRIMARY = "rs_sec_63"


def _loo_group(R: pd.DataFrame, E: pd.DataFrame, lab: pd.Series, min_members: int) -> pd.DataFrame:
    """Each stock's group return that day WITHOUT the stock: (sum - own) / (count - own),
    over names eligible the prior day; NaN under `min_members` others."""
    elig = E.shift(1).fillna(False).astype(bool)
    Rm = R.where(elig)
    out = pd.DataFrame(np.nan, index=R.index, columns=R.columns)
    for g in lab.dropna().unique():
        cols = [c for c in lab.index[lab == g] if c in R.columns]
        if len(cols) < 2:
            continue
        x = Rm[cols]
        s, n = x.sum(axis=1, min_count=1), x.notna().sum(axis=1)
        own = x.notna()
        loo_n = n.to_numpy()[:, None] - own.to_numpy()
        loo_s = s.to_numpy()[:, None] - x.fillna(0.0).to_numpy()
        v = np.where(loo_n >= min_members, loo_s / np.maximum(loo_n, 1), np.nan)
        out[cols] = v
    return out


def build(verbose: bool = True) -> pd.DataFrame:
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    import glob
    import sectors
    t0 = time.time()
    C, V = sectors.load_wide()
    C.index = C.index.astype(str).str[:10]
    V.index = C.index
    E = sectors.eligible(C, V)
    R = sectors.daily_returns(C)
    g = sectors.groups().drop_duplicates("ticker").set_index("ticker")
    sec = g["sector"].reindex(C.columns)
    sub = g["subind"].reindex(C.columns)
    Rsec = _loo_group(R, E, sec, sectors.MIN_MEMBERS)
    Rsub = _loo_group(R, E, sub, sectors.MIN_MEMBERS)
    e = pd.concat(pd.read_parquet(p, columns=["ticker", "date", "close"])
                  for p in sorted(glob.glob(str(config.BARS_ETF / "*.parquet"))))
    e["date"] = e.date.astype(str).str[:10]
    spy = e[e.ticker == "SPY"].drop_duplicates("date").set_index("date").close.sort_index()
    rspy = spy.pct_change(fill_method=None).reindex(C.index)
    if verbose:
        print(f"loaded {C.shape[0]:,} sessions x {C.shape[1]:,} names; group returns {time.time() - t0:.0f}s", flush=True)
    ls = np.log1p(R)                                   # the stock (NaN where no return)
    rel_sec = ls - np.log1p(Rsec)
    rel_sub = ls - np.log1p(Rsub)
    rel_mkt = ls.sub(np.log1p(rspy), axis=0)
    ev = pd.read_parquet(EVENTS, columns=["date", "ticker"])
    dates = sorted(ev.date.unique())
    pos = {d: i for i, d in enumerate(C.index)}
    RS, RSUB, RMKT = rel_sec.to_numpy(), rel_sub.to_numpy(), rel_mkt.to_numpy()
    LS, RM = ls.to_numpy(), np.log1p(rspy).to_numpy()
    cols = list(C.columns)
    parts = []
    for d in dates:
        i = pos.get(d)
        if i is None or i < 300:
            continue
        def win(A, w, need=0.8):
            x = A[i - w + 1:i + 1]
            n = np.isfinite(x).sum(axis=0)
            return np.where(n >= need * w, np.nansum(x, axis=0), np.nan)
        row = {f"rs_sec_{w}": win(RS, w) for w in WINDOWS}
        row["rs_mkt_63"] = win(RMKT, 63)
        row["rs_sub_63"] = win(RSUB, 63)
        # the RS line (log) over the last 252 sessions, ending at 0 today
        x = RS[i - 251:i + 1]
        ok = np.isfinite(x).sum(axis=0) >= 0.8 * 252
        line = np.nancumsum(np.nan_to_num(x, nan=0.0), axis=0)
        ma50 = line[-50:].mean(axis=0)
        row["line_up"] = np.where(ok, (line[-1] > ma50).astype(float), np.nan)
        row["rs_high"] = np.where(ok, (line[-1] >= line.max(axis=0) - np.log(1.02)).astype(float), np.nan)
        y = RS[i - 125:i + 1]
        oky = np.isfinite(y).sum(axis=0) >= 0.8 * 126
        cum = np.nancumsum(np.nan_to_num(y, nan=0.0), axis=0)
        row["above_126"] = np.where(oky, (cum > 0).mean(axis=0), np.nan)
        # beta-adjusted 252-session return vs SPY
        a, m = LS[i - 251:i + 1], RM[i - 251:i + 1][:, None]
        both = np.isfinite(a) & np.isfinite(m)
        nb = both.sum(axis=0)
        am = np.where(both, a, np.nan)
        mm = np.where(both, np.broadcast_to(m, a.shape), np.nan)
        mu_a, mu_m = np.nanmean(am, axis=0), np.nanmean(mm, axis=0)
        cov = np.nanmean((am - mu_a) * (mm - mu_m), axis=0)
        var = np.nanmean((mm - mu_m) ** 2, axis=0)
        beta = cov / np.where(var > 0, var, np.nan)
        row["resid_252"] = np.where(nb >= 0.8 * 252, np.nansum(am - beta * mm, axis=0), np.nan)
        f = pd.DataFrame(row)
        f.insert(0, "ticker", cols)
        f.insert(0, "date", d)
        parts.append(f)
    M = pd.concat(parts, ignore_index=True)
    M = ev.merge(M, on=["date", "ticker"], how="left")
    M.to_parquet(OUT_METRICS, index=False)
    if verbose:
        cov = {c: round(float(M[c].notna().mean()), 3) for c in METRICS}
        print(f"built {len(M):,} name-dates x {len(METRICS)} metrics; coverage {cov}; {time.time() - t0:.0f}s", flush=True)
    return M


# ------------------------------------------------------------------------- the tests
def _t(x) -> float | None:
    x = pd.Series(x, dtype=float).dropna()
    if len(x) < 3 or x.std(ddof=1) == 0:
        return None
    return round(float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))), 2)


def _nw_t(x, lags: int = 1) -> float | None:
    x = pd.Series(x, dtype=float).dropna().to_numpy()
    n = len(x)
    if n < 5:
        return None
    u = x - x.mean()
    v = u @ u / n
    for L in range(1, lags + 1):
        v += 2 * (1 - L / (lags + 1)) * (u[L:] @ u[:-L]) / n
    return round(float(x.mean() / np.sqrt(v / n)), 2) if v > 0 else None


def _pp(x, scale=100) -> float | None:
    return round(float(x) * scale, 3) if x is not None and np.isfinite(x) else None


def summary(x: pd.Series, nw: bool = False, scale: float = 100) -> dict:
    x = x.dropna()
    tf = _nw_t if nw else _t
    tr, te = x[x.index < SPLIT], x[x.index >= SPLIT]
    h1, h2 = tr[tr.index < HALF], tr[tr.index >= HALF]
    s = {"dates": int(len(x)), "train": _pp(tr.mean(), scale), "train_t": tf(tr),
         "train_h1": _pp(h1.mean(), scale), "train_h2": _pp(h2.mean(), scale),
         "test": _pp(te.mean(), scale), "test_t": tf(te), "test_dates": int(len(te))}
    sg = [np.sign(s[k] or 0) for k in ("train_h1", "train_h2", "test")]
    s["kept"] = bool(sg[0] != 0 and sg[0] == sg[1] == sg[2] and abs(s["test_t"] or 0) >= 2)
    return s


def _thirds(df: pd.DataFrame, score: str, y: str, binary: bool, min_side: int = 10) -> pd.Series:
    out = {}
    for d, g in df[df[score].notna() & df[y].notna()].groupby("date"):
        x = g[score]
        if binary:
            top, bot = x == 1.0, x == 0.0
        else:
            lo, hi = x.quantile(1 / 3), x.quantile(2 / 3)
            top, bot = x > hi, x <= lo
            if top.sum() < min_side:
                top = x >= hi
        if top.sum() < min_side or bot.sum() < min_side or bool((top & bot).any()):
            continue
        out[d] = g.loc[top, y].mean() - g.loc[bot, y].mean()
    return pd.Series(out, dtype=float).sort_index()


def _neutral(df: pd.DataFrame, score: str, y: str, control: str) -> pd.Series:
    out = {}
    for d, g in df[df[score].notna() & df[y].notna() & df[control].notna()].groupby("date"):
        if len(g) < 45:
            continue
        cq = g[control].rank(pct=True, method="first")
        parts = []
        for a, b in ((0, 1 / 3), (1 / 3, 2 / 3), (2 / 3, 1.0)):
            s = g[(cq > a) & (cq <= b)]
            lo, hi = s[score].quantile(1 / 3), s[score].quantile(2 / 3)
            top, bot = s[score] > hi, s[score] <= lo
            if top.sum() >= 5 and bot.sum() >= 5:
                parts.append(s.loc[top, y].mean() - s.loc[bot, y].mean())
        if len(parts) >= 2:
            out[d] = float(np.mean(parts))
    return pd.Series(out, dtype=float).sort_index()


def analyse() -> dict:
    import trade_rules_study as TR
    M = pd.read_parquet(OUT_METRICS)
    ev = pd.read_parquet(EVENTS, columns=["date", "ticker", "excess_20", "mom_12_1", "rally_count"])
    M = M.merge(ev, on=["date", "ticker"], how="left")
    T = pd.read_parquet(TR.OUT_TRADES, columns=["date", "ticker", "hot_all"])
    Z = np.load(TR.OUT_NPZ)
    R = list(Z["rules"])
    q1 = json.loads(TR.OUT.read_text(encoding="utf-8"))["q1"]["chosen"] if TR.OUT.exists() else TR.DEFAULT
    T["ret_default"] = Z["ret"][:, R.index(TR.DEFAULT)]
    T["ret_q1"] = Z["ret"][:, R.index(q1)]
    A = T.merge(M, on=["date", "ticker"], how="left")
    out = {"measured_on": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "definitions": __doc__.strip(), "q1_rule": q1,
           "coverage_at_support": {c: round(float(A[c].notna().mean()), 3) for c in METRICS}}
    tests = 0
    r1 = {}
    for c in METRICS + ["mom_12_1"]:
        r1[c] = {lab: summary(_thirds(A, c, y, c in BINARY)) for lab, y in (("default", "ret_default"), ("q1_rule", "ret_q1"))}
        tests += 2
    r1[f"{PRIMARY} momentum-neutral"] = {"default": summary(_neutral(A, PRIMARY, "ret_default", "mom_12_1"))}
    tests += 1
    out["r1_at_support_trade"] = r1
    hot = A[A.hot_all.astype(bool)]
    out["r2_hot_third"] = {"default": summary(_thirds(hot, PRIMARY, "ret_default", False)),
                           "names_per_date": int(hot.groupby("date").size().median())}
    tests += 1
    r3 = {}
    for c in METRICS + ["mom_12_1"]:
        ic = {}
        for d, g in M[M[c].notna() & M.excess_20.notna()].groupby("date"):
            if len(g) >= 100:
                ic[d] = g[c].rank().corr(g.excess_20.rank())
        r3[c] = summary(pd.Series(ic, dtype=float).sort_index(), nw=True, scale=1)
        tests += 1
    out["r3_factor_ic"] = r3
    out["tests_counted"] = tests
    out["expected_abs_t_ge_2_by_chance"] = round(tests * 0.05, 1)
    return out


def main() -> int:
    if "--build" in sys.argv:
        build()
        return 0
    s = analyse()
    OUT.write_text(json.dumps(s, indent=1, default=str), encoding="utf-8")
    print(f"wrote {OUT.name}: {s['tests_counted']} comparisons, ~{s['expected_abs_t_ge_2_by_chance']} by chance")
    return 0


if __name__ == "__main__":
    sys.exit(main())
