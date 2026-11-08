#!/usr/bin/env python
"""Which bounce-trade rule works, for which stocks, in which markets? (plan item 1)

    python trade_rules_study.py --build    every rule on every at-support name-date (~5 min):
                                           data/_trade_rules.npz + data/_trade_rules_trades.parquet
    python trade_rules_study.py            the pre-registered questions -> data/_trade_rules_study.json
    python trade_rules_study.py --check    20 random trades recomputed from the raw store bars, and
                                           the hourly check of the open against an hour after it
    python trade_rules_study.py --selftest the bracket engine on made-up paths
    python trade_rules_study.py --fetch-intraday   the 30-minute bars the intraday questions use
                                           (~1.5 h at the free tier's pace; resumable)
    python trade_rules_study.py --build-intraday   open+1h and limit-in-30-minute-order rules
    python trade_rules_study.py --intraday         I1 / I2 -> data/_trade_rules_intraday.json

Asked 2026-11-08, after the bounce-trade test (big_movers_study.py --trades): try different
target/stop levels, "by market cap and other stuff, may be different levels work for different
stocks"; the entry is "usually next morning open like after an hour", maybe later in the day "in
case the price is edging closer"; and for the market filter, "research and try the best ones".
Research screens, not investment advice.

DESIGN, fixed 2026-11-08 before any result of these tests was seen (the step-10 events were built
first; no trade outcome on them had been looked at):

  DATA     big_movers_study.py --step 10 events: every 10th session from 2018-01-02 to the last
           with 60 sessions after it; liquid names (close >= $5, 20-session median dollar volume
           >= $20M); AT SUPPORT = the nearest zone 0-6% below the close. Daily OHLC from the store
           (split and spin-off adjusted, as nightly). Market cap = the scores store's `mktcap`,
           the latest on or before the date. ETFs from macro's store; VIX and the high-yield
           spread from FRED (`_macro.parquet`); breadth = the share of stocks above their 50-day.
  SPLIT    TRAIN = dates before 2022-01-01, TEST = from 2022-01-01. Every choice is made on TRAIN
           and reported on TEST beside fixed baselines; TEST never chooses anything.
  ENTRIES  daily bars only (the store's hourly bars cover 88 names for 4 months: they only check
           how far the price an hour after the open sits from the open, `--check`):
             open    the next session's open (the user's "next morning, about an hour after");
             limit   a buy limit at the level + 0.5%, live for 3 sessions: filled at the open when
                     the open is at or under it, else at the limit when the low reaches it; no
                     fill, no trade. On a day it filled coming DOWN to the limit, that day's stop
                     counts and its target does not (its high may have come before the fill);
             close   the next session's close.
  EXITS    a bracket from the entry price on daily bars: a gap through either side fills at that
           open; a day reaching both is the STOP (the order inside a day is unknowable); else the
           close of the last hold session (the entry session counts as the first for open and
           limit entries; for a close entry the hold starts the session after).
             fixed   targets 3 / 5 / 8 / 10% x stops 3 / 5 / 8% x holds 5 / 10 sessions  (24)
             atr     target, stop = (1,1) (2,1) (2,2) (3,2) (3,3) x the 20-session ATR % on the
                     signal date, holds 5 / 10                                            (10)
             level   stop 3% under the level (no trade if the entry is already under it), target
                     +5% or +2 ATR, hold 10                                                (2)
           36 exits x 3 entries = 108 RULES. DEFAULT = open, +5% / -5%, 10 sessions.
  COSTS    0.10% a round trip off every trade (spread and slippage on $20M+/day names).
  SEGMENTS point in time: market cap (< $2B small, $2-10B mid, $10-200B large, > $200B mega);
           volatility third (63-day st.dev. among the date's at-support names); hotness
           (top-20 gainer days in the year: 0 / 1-4 / 5-14 / 15+); sector (today's map); band
           (AT 0-2.5% above the level / NEAR 2.5-6%).
  QUESTIONS, each judged by the per-date mean trade return (net), t across dates:
    Q1 BEST RULE: the rule with the highest TRAIN t (trading on >= 80% of TRAIN dates) is
       chosen; its TEST mean against the default's, per TEST date.
    Q2 DIFFERENT LEVELS FOR DIFFERENT STOCKS: for each segment family, each segment's best
       TRAIN rule (>= 30 TRAIN dates with >= 5 trades); on TEST the per-segment portfolio
       (each trade under its segment's rule) against the one Q1 rule and the default on the
       same trades and dates. Per-segment rules count only if they beat the Q1 rule on TEST.
    Q3 MARKET FILTERS -- research: Connors & Alvarez trade pullbacks only above the 200-day
       average; Nagel (2012, RFS) found short-term reversal returns rise with the VIX; breadth
       gauges are vendor-tested only. Binary, known at the signal close:
         m1 SPY > 200-day   m2 SPY > 50-day   m3 QQQ > 200-day   m4 QQQ > 50-day
         m5 breadth > 50%   m6 VIX >= 20      m7 VIX < its 10-day average
         m8 high-yield spread < its 50-day average   m9 SPY > 200-day AND VIX >= 20
         m10 the stock's sector ETF > 50-day (stock level)   m11 the stock > its 200-day
       For the default and the Q1 rule: mean when on minus when off; the filtered strategy
       against the unfiltered one. Chosen on TRAIN by t, judged on TEST.
    Q4 EARNINGS IN THE HOLD: an 8-K Item 2.02 public in the 10 sessions after the signal vs
       none (the filing date stands in for the announced date, published weeks ahead).
    Q5 DIPS WON BACK AMONG HOT NAMES: within the hot third (rally_count among all liquid names
       that date), recovery_rate's top third minus bottom third (>= 2 dips).
    Q6 AT vs NEAR: AT minus NEAR, every at-support name and the hot third.
  KEPT only if it holds in both TRAIN halves (2018-19, 2020-21) AND on TEST. Every comparison
  is counted; about 5% reach |t| >= 2 by chance.
  CHECKS   20 random trades recomputed from raw store bars; the hourly open check; each regime
           series' coverage; market-cap coverage per date.
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

SPLIT, HALF = "2022-01-01", "2020-01-01"
LIMIT_UP, LIMIT_DAYS = 0.005, 3
LEVEL_STOP = 0.03
COST = 0.001
FIXED_T, FIXED_S, HOLDS = (0.03, 0.05, 0.08, 0.10), (0.03, 0.05, 0.08), (5, 10)
ATR_PAIRS = ((1, 1), (2, 1), (2, 2), (3, 2), (3, 3))
ATR_N = 20
ENTRIES = ("open", "limit", "close")
DEFAULT = "open|f5_5_h10"
CAP_EDGES = (2e9, 10e9, 200e9)
CAP_NAMES = ("small", "mid", "large", "mega")
MIN_TRAIN_COVER = 0.80
SEG_MIN_DATES, SEG_MIN_TRADES = 30, 5
EVENTS = config.DATA / "_big_movers_events_s10.parquet"
OUT_NPZ = config.DATA / "_trade_rules.npz"
OUT_TRADES = config.DATA / "_trade_rules_trades.parquet"
OUT = config.DATA / "_trade_rules_study.json"


# ------------------------------------------------------------------------- the exits
def exits() -> list[tuple[str, str, float, float, int]]:
    """(name, kind, a, b, hold): fixed a = target, b = stop (fractions); atr a, b = multiples;
    level a = target (fraction, or -k for k ATRs), b unused."""
    out = [(f"f{int(t * 100)}_{int(s * 100)}_h{h}", "fixed", t, s, h)
           for t in FIXED_T for s in FIXED_S for h in HOLDS]
    out += [(f"atr{a}_{b}_h{h}", "atr", float(a), float(b), h) for a, b in ATR_PAIRS for h in HOLDS]
    out += [("lvl_t5_h10", "level", 0.05, 0.0, 10), ("lvl_t2atr_h10", "level", -2.0, 0.0, 10)]
    return out


def rules() -> list[str]:
    return [f"{e}|{x[0]}" for e in ENTRIES for x in exits()]


# ------------------------------------------------------------------------- the engine
def simulate(P: dict, start: np.ndarray, gap_from: np.ndarray, intraday: np.ndarray,
             entry_row: np.ndarray, tgt: np.ndarray, stp: np.ndarray, last: np.ndarray,
             ok: np.ndarray, close_entry: bool = False):
    """One bracket on daily paths (rows = sessions after the signal, columns = names).

    start     first row the bracket is live (the entry row for open/limit, entry + 1 for close)
    gap_from  first row whose OPEN can gap through a side (rows after the entry)
    intraday  True where the fill came DOWN to a limit inside its row: on that row the stop
              counts and the target does not
    last      the last hold row: exit at its close when nothing hit (a missing bar: the last
              close seen while live; no bar at all: no result)
    -> (exit price, kind 1 target / -1 stop / 0 time, sessions held)"""
    O, H, L, C = P["open"], P["high"], P["low"], P["close"]
    n_d, k = O.shape
    px = np.full(k, np.nan)
    kind = np.zeros(k, dtype=np.int8)
    xrow = np.full(k, -1, dtype=np.int64)
    done = ~ok
    lastc = np.full(k, np.nan)
    lastr = np.full(k, -1, dtype=np.int64)
    fin = np.isfinite(O) & np.isfinite(H) & np.isfinite(L) & np.isfinite(C)
    with np.errstate(invalid="ignore"):
        for d in range(n_d):
            live = ~done & (d >= start) & (d <= last) & fin[d]
            if not live.any():
                if not (~done & (d < last)).any():
                    break
                continue
            gap = live & (d >= gap_from)
            g_dn = gap & (O[d] <= stp)
            g_up = gap & ~g_dn & (O[d] >= tgt)
            s_hit = live & ~g_dn & ~g_up & (L[d] <= stp)
            t_hit = live & ~g_dn & ~g_up & ~s_hit & (H[d] >= tgt) & ~(intraday & (d == start))
            for m, p, kd in ((g_dn, O[d], -1), (g_up, O[d], 1), (s_hit, stp, -1), (t_hit, tgt, 1)):
                if m.any():
                    px[m] = p[m]
                    kind[m] = kd
                    xrow[m] = d
                    done = done | m
            upd = live & ~done
            lastc[upd] = C[d][upd]
            lastr[upd] = d
    te = ok & (xrow < 0)
    px[te] = lastc[te]
    xrow[te] = lastr[te]
    none = te & ~np.isfinite(px)
    px[none] = np.nan
    held = np.where(ok & (xrow >= 0), xrow - entry_row + (0 if close_entry else 1), 0)
    return px, kind, held.astype(np.int16)


def entries(P: dict, level: np.ndarray):
    """Per entry kind: (entry price, entry row, start row, gap_from row, intraday, ok)."""
    O, L, C = P["open"], P["low"], P["close"]
    k = O.shape[1]
    out = {}
    zero = np.zeros(k, dtype=np.int64)
    ok = np.isfinite(O[0]) & (O[0] > 0)
    out["open"] = (O[0].copy(), zero, zero, zero + 1, np.zeros(k, bool), ok)
    okc = np.isfinite(C[0]) & (C[0] > 0)
    out["close"] = (C[0].copy(), zero, zero + 1, zero + 1, np.zeros(k, bool), okc)
    lim = level * (1 + LIMIT_UP)
    e_px = np.full(k, np.nan)
    e_row = np.full(k, -1, dtype=np.int64)
    intra = np.zeros(k, dtype=bool)
    with np.errstate(invalid="ignore"):
        for d in range(min(LIMIT_DAYS, O.shape[0])):
            todo = e_row < 0
            at_open = todo & np.isfinite(O[d]) & (O[d] <= lim)
            dipped = todo & ~at_open & np.isfinite(L[d]) & (L[d] <= lim)
            e_px[at_open], e_row[at_open] = O[d][at_open], d
            e_px[dipped], e_row[dipped], intra[dipped] = lim[dipped], d, True
    okl = (e_row >= 0) & np.isfinite(e_px) & (e_px > 0)
    e_row_s = np.where(okl, e_row, 0)
    out["limit"] = (e_px, e_row_s, e_row_s, e_row_s + 1, intra, okl)
    return out


def atr_pct(W: dict, i: int, js: np.ndarray) -> np.ndarray:
    """The 20-session average true range on the signal date, as a share of its close."""
    H = W["high"][i - ATR_N + 1:i + 1][:, js].astype(np.float64)
    L = W["low"][i - ATR_N + 1:i + 1][:, js].astype(np.float64)
    Cp = W["close"][i - ATR_N:i][:, js].astype(np.float64)
    with np.errstate(invalid="ignore"):
        tr = np.fmax(H, Cp) - np.fmin(L, Cp)
        n = np.isfinite(tr).sum(axis=0)
        a = np.where(n >= 15, np.nanmean(tr, axis=0), np.nan)
    return a / W["close"][i, js].astype(np.float64)


# ------------------------------------------------------------------------- the build
def _mktcap(dates: list[str]) -> pd.DataFrame:
    """(date, ticker) -> market cap: the scores store's latest `mktcap` on or before each date."""
    import glob
    parts = []
    for f in sorted(glob.glob(str(config.DATA / "scores" / "*.parquet"))):
        d = pd.read_parquet(f, columns=["ticker", "session", "metric", "value"],
                            filters=[("metric", "==", "mktcap")])
        if len(d):
            parts.append(d[["ticker", "session", "value"]])
    m = pd.concat(parts, ignore_index=True)
    m["session"] = pd.to_datetime(m.session.astype(str).str[:10])
    m = m.dropna(subset=["value"]).sort_values("session")
    grid = pd.DataFrame({"date": pd.to_datetime(sorted(dates))})
    out = []
    for t, g in m.groupby("ticker", sort=False):
        j = pd.merge_asof(grid, g.rename(columns={"session": "date"})[["date", "value"]],
                          on="date", direction="backward")
        j["ticker"] = t
        out.append(j.dropna(subset=["value"]))
    r = pd.concat(out, ignore_index=True).rename(columns={"value": "mktcap"})
    r["date"] = r.date.dt.strftime("%Y-%m-%d")
    return r


def _earnings_by_ticker() -> dict[str, np.ndarray]:
    import news_events as NE
    e = NE.explode_items(NE.read(start="2017-06-01"))
    e = e[(e.event_type == "earnings") & e.ticker.notna()]
    out = {}
    for t, g in e.groupby(e.ticker.astype(str)):
        out[t] = np.array(sorted(set(g.public_session.astype(str))))
    return out


def build(verbose: bool = True) -> None:
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    import big_movers_study as BM
    import sectors
    t0 = time.time()
    ev = pd.read_parquet(EVENTS)
    dates, ticks, W = BM.load_ohlc()
    di = {d: i for i, d in enumerate(dates)}
    ti = {t: j for j, t in enumerate(ticks)}
    # all-liquid cutoffs for "hot" (Q5): rally_count's 2/3 point among every liquid name
    hot_cut = ev.groupby("date").rally_count.quantile(2 / 3)
    at = ev[ev.at_support].copy()
    at = at[at.ticker.isin(ti)]
    sec = sectors.groups().drop_duplicates("ticker").set_index("ticker")["sector"]
    at["sector"] = at.ticker.map(sec).fillna("Other")
    caps = _mktcap(sorted(at.date.unique()))
    at = at.merge(caps, on=["date", "ticker"], how="left")
    at["cap"] = pd.cut(at.mktcap, [0, *CAP_EDGES, np.inf], labels=CAP_NAMES, right=False).astype(object)
    at["vol_third"] = at.groupby("date").vol63.transform(
        lambda s: pd.qcut(s.rank(method="first"), 3, labels=["low", "mid", "high"]) if s.notna().sum() >= 3 else np.nan)
    at["hot"] = pd.cut(at.gain_days, [-0.5, 0.5, 4.5, 14.5, np.inf], labels=["0", "1-4", "5-14", "15+"]).astype(object)
    at["band2"] = np.where(at.dist <= 0.025, "AT", "NEAR")
    at["hot_all"] = at.rally_count > at.date.map(hot_cut)
    earn = _earnings_by_ticker()
    import macro
    etf = _etf_closes()
    sector_etf = {s: macro.SECTOR_ETF_FOR.get(s, "SPY") for s in at.sector.unique()}
    ex = exits()
    R = rules()
    n = len(at)
    ret = np.full((n, len(R)), np.nan, dtype=np.float32)
    kind = np.zeros((n, len(R)), dtype=np.int8)
    held = np.zeros((n, len(R)), dtype=np.int16)
    at = at.sort_values(["date", "ticker"]).reset_index(drop=True)
    extra = {"atr_pct": np.full(n, np.nan), "earn_in_10": np.zeros(n, bool),
             "above_200d": np.full(n, np.nan), "sector_etf_up": np.full(n, np.nan),
             "limit_filled": np.zeros(n, bool), "limit_row": np.full(n, -1, dtype=np.int16)}
    nmax = LIMIT_DAYS + max(HOLDS) + 2
    for n_done, (D, g) in enumerate(at.groupby("date", sort=True)):
        rows = g.index.to_numpy()
        i = di[D]
        js = g.ticker.map(ti).to_numpy()
        P = {c: W[c][i + 1:i + 1 + nmax][:, js].astype(np.float64) for c in ("open", "high", "low", "close")}
        lvl = g.level.to_numpy(dtype=float)
        a = atr_pct(W, i, js)
        extra["atr_pct"][rows] = a
        c200 = W["close"][i - 199:i + 1][:, js].astype(np.float64)
        sma = np.where(np.isfinite(c200).sum(axis=0) >= 190, np.nanmean(c200, axis=0), np.nan)
        extra["above_200d"][rows] = np.where(np.isfinite(sma), (W["close"][i, js] > sma).astype(float), np.nan)
        up = etf.get("up50", {})
        extra["sector_etf_up"][rows] = [up.get((sector_etf[s], D), np.nan) for s in g.sector]
        win_hi = str(dates[min(i + 10, len(dates) - 1)])
        win_lo = str(dates[i + 1])
        extra["earn_in_10"][rows] = [bool(((e := earn.get(t)) is not None) and
                                          np.any((e >= win_lo) & (e <= win_hi))) for t in g.ticker]
        E = entries(P, lvl)
        extra["limit_filled"][rows] = E["limit"][5]
        extra["limit_row"][rows] = np.where(E["limit"][5], E["limit"][1], -1)
        r_i = 0
        for en in ENTRIES:
            e_px, e_row, start, gap_from, intra, ok0 = E[en]
            for name, kd, xa, xb, h in ex:
                if kd == "fixed":
                    tgt, stp = e_px * (1 + xa), e_px * (1 - xb)
                    ok = ok0.copy()
                elif kd == "atr":
                    tgt, stp = e_px * (1 + xa * a), e_px * (1 - xb * a)
                    ok = ok0 & np.isfinite(a) & (a > 0)
                else:
                    tgt = e_px * (1 + xa) if xa > 0 else e_px * (1 + (-xa) * a)
                    stp = lvl * (1 - LEVEL_STOP)
                    ok = ok0 & np.isfinite(stp) & (e_px > stp) & np.isfinite(tgt)
                last = e_row + h - 1 if en != "close" else e_row + h
                px, kk, hh = simulate(P, start, gap_from, intra, e_row, tgt, stp, last, ok,
                                      close_entry=(en == "close"))
                rr = np.where(ok & np.isfinite(px), px / e_px - 1.0 - COST, np.nan)
                ret[rows, r_i] = rr
                kind[rows, r_i] = kk
                held[rows, r_i] = hh
                r_i += 1
        if verbose and n_done % 20 == 0:
            print(f"  {D}: {len(rows):,} at support; {time.time() - t0:.0f}s", flush=True)
    for k, v in extra.items():
        at[k] = v
    keep = ["date", "ticker", "price", "level", "dist", "band", "band2", "vol63", "mom_12_1", "gain_days",
            "rally_count", "recovery_rate", "dips", "hot_all", "sector", "mktcap", "cap", "vol_third", "hot",
            *extra.keys()]
    at[keep].to_parquet(OUT_TRADES, index=False)
    np.savez_compressed(OUT_NPZ, ret=ret, kind=kind, held=held, rules=np.array(R))
    if verbose:
        print(f"built {n:,} at-support name-dates x {len(R)} rules over {at.date.nunique()} dates; "
              f"{time.time() - t0:.0f}s -> {OUT_NPZ.name}, {OUT_TRADES.name}", flush=True)


def _etf_closes() -> dict:
    """{'up50': {(etf, date): 1.0/0.0}} -- each ETF's close above its 50-day average."""
    import glob
    e = pd.concat(pd.read_parquet(p, columns=["ticker", "date", "close"])
                  for p in sorted(glob.glob(str(config.BARS_ETF / "*.parquet"))))
    e["date"] = e.date.astype(str).str[:10]
    e = e.drop_duplicates(["ticker", "date"]).sort_values(["ticker", "date"])
    e["sma50"] = e.groupby("ticker").close.transform(lambda s: s.rolling(50, min_periods=50).mean())
    up = {(t, d): float(c > m) for t, d, c, m in zip(e.ticker, e.date, e.close, e.sma50) if np.isfinite(m)}
    return {"up50": up}


def regimes(dates: list[str]) -> pd.DataFrame:
    """Market-level filters m1..m9 per signal date, from data known at that close."""
    import glob
    e = pd.concat(pd.read_parquet(p, columns=["ticker", "date", "close"])
                  for p in sorted(glob.glob(str(config.BARS_ETF / "*.parquet"))))
    e["date"] = e.date.astype(str).str[:10]
    e = e.drop_duplicates(["ticker", "date"])
    px = e.pivot(index="date", columns="ticker", values="close").sort_index()
    m = pd.read_parquet(config.DATA / "_macro.parquet")
    m["session"] = m.session.astype(str).str[:10]
    m = m.drop_duplicates("session").set_index("session").sort_index()
    out = pd.DataFrame(index=sorted(dates))
    spy, qqq = px["SPY"], px["QQQ"]
    out["m1"] = (spy > spy.rolling(200, min_periods=200).mean()).where(spy.rolling(200, min_periods=200).mean().notna())
    out["m2"] = (spy > spy.rolling(50, min_periods=50).mean()).where(spy.rolling(50, min_periods=50).mean().notna())
    out["m3"] = (qqq > qqq.rolling(200, min_periods=200).mean()).where(qqq.rolling(200, min_periods=200).mean().notna())
    out["m4"] = (qqq > qqq.rolling(50, min_periods=50).mean()).where(qqq.rolling(50, min_periods=50).mean().notna())
    vix = m["vix"].ffill()
    hy = m["hy_spread"].ffill().shift(1)            # ICE publishes the OAS a day late on FRED
    out["m5"] = (m["pct_above_ma"] > 0.5).where(m["pct_above_ma"].notna())
    out["m6"] = (vix >= 20).where(vix.notna())
    out["m7"] = (vix < vix.rolling(10, min_periods=10).mean()).where(vix.rolling(10, min_periods=10).mean().notna())
    out["m8"] = (hy < hy.rolling(50, min_periods=50).mean()).where(hy.rolling(50, min_periods=50).mean().notna())
    out["m9"] = (out["m1"].astype(float) * out["m6"].astype(float)).where(out["m1"].notna() & out["m6"].notna())
    return out.reindex(sorted(dates)).astype(float)


# ------------------------------------------------------------------------- the questions
def _t(x) -> float | None:
    x = pd.Series(x, dtype=float).dropna()
    if len(x) < 3 or x.std(ddof=1) == 0:
        return None
    return round(float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))), 2)


def _welch(a, b) -> float | None:
    a, b = pd.Series(a, dtype=float).dropna(), pd.Series(b, dtype=float).dropna()
    if len(a) < 3 or len(b) < 3:
        return None
    se = np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    return round(float((a.mean() - b.mean()) / se), 2) if se > 0 else None


def _pp(x) -> float | None:
    return round(float(x) * 100, 3) if x is not None and np.isfinite(x) else None


def per_date(T: pd.DataFrame, ret: np.ndarray, mask=None) -> pd.DataFrame:
    """Per date, per rule: the mean net trade return over the trades that happened."""
    r = ret if mask is None else np.where(mask[:, None], ret, np.nan)
    df = pd.DataFrame(r)
    df["date"] = T.date.to_numpy()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return df.groupby("date").mean()


def summary(x: pd.Series) -> dict:
    """mean, t and both TRAIN halves and TEST of a per-date series, and whether it is KEPT."""
    s = _summary(x)
    s["kept"] = kept(s)
    return s


def _summary(x: pd.Series) -> dict:
    x = x.dropna()
    tr, te = x[x.index < SPLIT], x[x.index >= SPLIT]
    h1, h2 = tr[tr.index < HALF], tr[tr.index >= HALF]
    return {"dates": int(len(x)), "train_pp": _pp(tr.mean()), "train_t": _t(tr),
            "train_h1_pp": _pp(h1.mean()), "train_h1_t": _t(h1), "train_h2_pp": _pp(h2.mean()),
            "train_h2_t": _t(h2), "test_pp": _pp(te.mean()), "test_t": _t(te), "test_dates": int(len(te))}


def kept(s: dict) -> bool:
    """Holds in both TRAIN halves and on TEST: the same sign, the TEST t >= 2 in that sign."""
    sg = [np.sign(s.get(k) or 0) for k in ("train_h1_pp", "train_h2_pp", "test_pp")]
    return bool(sg[0] != 0 and sg[0] == sg[1] == sg[2] and abs(s.get("test_t") or 0) >= 2)


def analyse() -> dict:
    T = pd.read_parquet(OUT_TRADES)
    Z = np.load(OUT_NPZ)
    ret, kind, held, R = Z["ret"], Z["kind"], Z["held"], list(Z["rules"])
    ri = {r: k for k, r in enumerate(R)}
    d0 = ri[DEFAULT]
    out = {"measured_on": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "definitions": __doc__.strip(), "rules": len(R), "trades": int(len(T)),
           "dates": int(T.date.nunique()), "train_dates": int((T.date.drop_duplicates() < SPLIT).sum()),
           "test_dates": int((T.date.drop_duplicates() >= SPLIT).sum())}
    n_tests = 0
    PD = per_date(T, ret)
    train = PD[PD.index < SPLIT]
    # ---- Q1: the best rule
    cover = train.notna().mean()
    tt = train.apply(_t)
    ok = cover >= MIN_TRAIN_COVER
    cand = tt[ok].dropna().sort_values(ascending=False)
    best = int(cand.index[0])
    rows = []
    for k in list(cand.index[:12]) + ([d0] if d0 not in cand.index[:12] else []):
        s = summary(PD[k])
        fills = float(np.isfinite(ret[:, k]).mean())
        w = kind[:, k][np.isfinite(ret[:, k])]
        rows.append({"rule": R[k], **s, "trade_rate": round(fills, 3),
                     "win_pct": round(float((w == 1).mean()) * 100, 1) if len(w) else None,
                     "stop_pct": round(float((w == -1).mean()) * 100, 1) if len(w) else None,
                     "held_median": float(np.median(held[:, k][np.isfinite(ret[:, k])])) if len(w) else None})
    diff = PD[best] - PD[d0]
    out["q1"] = {"chosen": R[best], "default": DEFAULT, "top_train": rows,
                 "chosen_minus_default": summary(diff),
                 "rules_scored": int(ok.sum())}
    n_tests += 1
    # every rule's TRAIN and TEST, for the record
    out["q1"]["all_rules"] = {R[k]: {"train_pp": _pp(train[k].mean()), "train_t": _t(train[k]),
                                     "test_pp": _pp(PD[k][PD.index >= SPLIT].mean()),
                                     "test_t": _t(PD[k][PD.index >= SPLIT]),
                                     "train_cover": round(float(cover[k]), 3)} for k in range(len(R))}
    # ---- Q2: different levels for different stocks
    q2 = {}
    for fam in ("cap", "vol_third", "hot", "sector", "band2"):
        segs = [s for s in T[fam].dropna().unique()]
        chosen = {}
        # per variant (each segment's own rule / the one Q1 rule / the default) the sum of trade
        # returns and the number of trades, over the same segments: rules fill different counts
        acc = {v: [pd.Series(0.0, index=PD.index), pd.Series(0.0, index=PD.index)] for v in ("seg", "glob", "def")}
        detail = {}
        for s in sorted(segs, key=str):
            m = (T[fam] == s).to_numpy()
            Ps = per_date(T, ret, m)
            Ns = pd.DataFrame(np.isfinite(np.where(m[:, None], ret, np.nan))).assign(date=T.date.to_numpy()).groupby("date").sum()
            trn = Ps[Ps.index < SPLIT]
            okd = (Ns[Ns.index < SPLIT] >= SEG_MIN_TRADES).sum() >= SEG_MIN_DATES
            ts = trn.apply(_t)[okd].dropna()
            if ts.empty:
                continue
            k = int(ts.idxmax())
            chosen[str(s)] = R[k]
            # the segment's trades under its own rule, the global Q1 rule and the default
            for v, kk in (("seg", k), ("glob", best), ("def", d0)):
                tot = (Ps[kk] * Ns[kk]).reindex(PD.index).fillna(0.0)
                cnt = Ns[kk].reindex(PD.index).fillna(0.0)
                acc[v] = [acc[v][0] + tot, acc[v][1] + cnt]
            detail[str(s)] = {"rule": R[k], "segment": summary(Ps[k]),
                              "default_in_segment": summary(Ps[d0]),
                              "rule_minus_default": summary(Ps[k] - Ps[d0])}
        seg_mean, glob_mean, def_mean = (acc[v][0] / acc[v][1].replace(0, np.nan) for v in ("seg", "glob", "def"))
        q2[fam] = {"chosen": chosen, "detail": detail,
                   "per_segment_minus_global": summary(seg_mean - glob_mean),
                   "per_segment_minus_default": summary(seg_mean - def_mean)}
        n_tests += 2
    out["q2"] = q2
    # ---- Q3: market filters
    reg = regimes(sorted(T.date.unique()))
    q3 = {"coverage": {c: round(float(reg[c].notna().mean()), 3) for c in reg.columns},
          "on_share": {c: round(float(reg[c].mean()), 3) for c in reg.columns}}
    for label, k in (("default", d0), ("q1_rule", best)):
        res = {}
        for c in reg.columns:
            on = PD[k][reg[c] == 1.0]
            off = PD[k][reg[c] == 0.0]
            tr_on, tr_off = on[on.index < SPLIT], off[off.index < SPLIT]
            te_on, te_off = on[on.index >= SPLIT], off[off.index >= SPLIT]
            h1 = lambda z: z[z.index < HALF]
            h2 = lambda z: z[(z.index >= HALF) & (z.index < SPLIT)]
            gap = {"train_h1_pp": _pp(h1(tr_on).mean() - h1(tr_off).mean()),
                   "train_h2_pp": _pp(h2(tr_on).mean() - h2(tr_off).mean()),
                   "test_pp": _pp(te_on.mean() - te_off.mean()), "test_t": _welch(te_on, te_off)}
            res[c] = {"on_minus_off": gap, "kept": kept(gap),
                      "train_on_pp": _pp(tr_on.mean()), "train_off_pp": _pp(tr_off.mean()),
                      "train_welch_t": _welch(tr_on, tr_off),
                      "test_on_pp": _pp(te_on.mean()), "test_off_pp": _pp(te_off.mean()),
                      "test_welch_t": _welch(te_on, te_off),
                      "test_filtered_pp": _pp(te_on.mean()), "test_unfiltered_pp": _pp(PD[k][PD.index >= SPLIT].mean()),
                      "test_on_dates": int(len(te_on)), "test_off_dates": int(len(te_off))}
            n_tests += 1
        for c, col in (("m10", "sector_etf_up"), ("m11", "above_200d")):
            m_on = (T[col] == 1.0).to_numpy()
            m_off = (T[col] == 0.0).to_numpy()
            d_on = per_date(T, ret[:, [k]], m_on)[0]
            d_off = per_date(T, ret[:, [k]], m_off)[0]
            res[c] = {"on_minus_off": summary(d_on - d_off), "on": summary(d_on), "off": summary(d_off)}
            n_tests += 1
        trn = {c: (v.get("train_welch_t") if "train_welch_t" in v else v["on_minus_off"].get("train_t"))
               for c, v in res.items()}
        pick = max((c for c, v in trn.items() if v is not None), key=lambda c: trn[c], default=None)
        q3[label] = {"filters": res, "chosen_on_train": pick, "train_t": trn}
    out["q3"] = q3
    # ---- Q4: earnings in the hold
    q4 = {}
    for label, k in (("default", d0), ("q1_rule", best)):
        e1 = per_date(T, ret[:, [k]], T.earn_in_10.to_numpy())[0]
        e0 = per_date(T, ret[:, [k]], ~T.earn_in_10.to_numpy())[0]
        q4[label] = {"no_earnings_minus_earnings": summary(e0 - e1), "with_earnings": summary(e1),
                     "without": summary(e0), "share_with_earnings": round(float(T.earn_in_10.mean()), 3)}
        g = np.isfinite(ret[:, k])
        st = kind[:, k] == -1
        q4[label]["stop_pct_with"] = round(float(st[g & T.earn_in_10.to_numpy()].mean()) * 100, 1)
        q4[label]["stop_pct_without"] = round(float(st[g & ~T.earn_in_10.to_numpy()].mean()) * 100, 1)
        n_tests += 1
    out["q4"] = q4
    # ---- Q5: dips won back among hot names
    hot = T[T.hot_all & (T.dips >= 2) & T.recovery_rate.notna()]
    rows5 = []
    for D, g in hot.groupby("date"):
        if len(g) < 30:
            continue
        x = g.recovery_rate
        lo, hi = x.quantile(1 / 3), x.quantile(2 / 3)
        top, bot = x > hi, x <= lo
        if top.sum() < 10:
            top = x >= hi
        if top.sum() < 10 or bot.sum() < 10 or bool((top & bot).any()):
            continue
        r = ret[g.index.to_numpy(), d0]
        rows5.append((D, np.nanmean(r[top.to_numpy()]) - np.nanmean(r[bot.to_numpy()])))
    s5 = pd.Series({d: v for d, v in rows5}).sort_index()
    out["q5"] = {"top_minus_bottom": summary(s5), "names_per_date": int(hot.groupby("date").size().median()) if len(hot) else 0}
    n_tests += 1
    # ---- Q6: AT vs NEAR
    q6 = {}
    for label, m in (("every_at_support", np.ones(len(T), bool)), ("hot_third", T.hot_all.to_numpy())):
        a = per_date(T, ret[:, [d0]], m & (T.band2 == "AT").to_numpy())[0]
        b = per_date(T, ret[:, [d0]], m & (T.band2 == "NEAR").to_numpy())[0]
        q6[label] = {"at_minus_near": summary(a - b), "at": summary(a), "near": summary(b)}
        n_tests += 1
    out["q6"] = q6
    out["tests_counted"] = n_tests
    out["expected_abs_t_ge_2_by_chance"] = round(n_tests * 0.05, 1)
    return out


# ------------------------------------------------------------------------- intraday (30-minute bars)
# INTRADAY, fixed 2026-11-08 before any 30-minute bar was downloaded (the user: "yes download the
# hourly data if its free"). Alpaca's free tier serves SIP history but pages intraday bars at 248
# a request across all symbols (measured: 1Hour, 1 or 5 symbols), so the study fetches only what
# it uses: 30-minute REGULAR-SESSION bars (09:30-16:00 ET, 13 a day, so 09:30 is the official
# open and 10:30 is exactly "an hour after") for the ENTRY DAY of every at-support name-date and
# for the limit's fill day when it filled on day 2 or 3. One file a day in data/trade_rules_30m/
# (the study's own: the nightly 1h store is pruned by retention).
#   E4 open+1h  enter at the 10:30 bar's open (the next bar's if it has none); the rest of that
#               day in 30-minute order -- a later bar opening beyond a side exits at that open,
#               a bar reaching both is the stop -- then the daily engine from the next session.
#   E2' limit   the same limit as `limit`, its fill day in 30-minute order: the fill bar is the
#               first whose open is at or under the limit (filled at that open) or whose low
#               reaches it (filled at the limit, inside the bar: there the stop counts and the
#               target does not); later bars that day in order; then daily. A fill the daily
#               engine saw but the 30-minute bars do not falls back to the daily result (counted).
#   Every exit of the grid, so 72 more rules. QUESTIONS: I1 open+1h minus open, I2 limit (30-
#   minute order) minus limit (pessimistic), per exit, per date, TRAIN halves and TEST, kept as
#   above; and whether Q1's best rule changes.
BARS30 = config.DATA / "trade_rules_30m"
PER_REQ = 19                     # 13 bars a name x 19 = 247: one 248-bar page holds a request
WORKERS = 6
SLOTS = 13                       # 09:30 .. 15:30 ET
PLUS1H_SLOT = 2                  # 10:30
OUT_NPZ_I = config.DATA / "_trade_rules_intraday.npz"


def _axis() -> np.ndarray:
    import store
    d = store.read(interval="1d", columns=["date"])
    return np.array(sorted(d.date.astype(str).str[:10].unique()))


def _session_utc(day: str) -> tuple[str, str]:
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")
    a = pd.Timestamp(f"{day} 09:30", tz=ny).tz_convert("UTC")
    b = pd.Timestamp(f"{day} 15:59", tz=ny).tz_convert("UTC")
    return a.strftime("%Y-%m-%dT%H:%M:%SZ"), b.strftime("%Y-%m-%dT%H:%M:%SZ")


def intraday_pairs(axis: np.ndarray | None = None) -> dict[str, list[str]]:
    """{day: tickers}: the entry day of every at-support name-date, and the limit's fill day
    when it filled on day 2 or 3."""
    axis = _axis() if axis is None else axis
    pos = {d: i for i, d in enumerate(axis)}
    T = pd.read_parquet(OUT_TRADES, columns=["date", "ticker", "limit_row"])
    out: dict[str, set] = {}
    for d, t, lr in zip(T.date, T.ticker, T.limit_row):
        if "~" in t:
            continue                                  # an archived listing: no Alpaca symbol
        i = pos[d]
        out.setdefault(str(axis[i + 1]), set()).add(t)
        if lr in (1, 2):
            out.setdefault(str(axis[i + 1 + int(lr)]), set()).add(t)
    return {d: sorted(v) for d, v in sorted(out.items())}


def fetch_intraday(verbose: bool = True) -> None:
    """Download the 30-minute bars the intraday questions use; resumable (a day's file is
    written whole or not at all, and an existing one is skipped)."""
    import requests
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import alpaca
    alpaca.TF_MAP.setdefault("30m", "30Min")
    BARS30.mkdir(parents=True, exist_ok=True)
    pairs = intraday_pairs()
    todo = [(d, ts) for d, ts in pairs.items() if not (BARS30 / f"{d}.parquet").exists()]
    n_req = sum((len(ts) + PER_REQ - 1) // PER_REQ for _, ts in todo)
    if verbose:
        print(f"intraday: {len(pairs)} days, {sum(len(v) for v in pairs.values()):,} name-days; "
              f"{len(todo)} days to fetch, ~{n_req:,} requests (~{n_req / config.RATE_LIMIT:.0f} min "
              f"at {config.RATE_LIMIT}/min)", flush=True)

    def one_day(day: str, tickers: list[str]) -> tuple[str, int, int]:
        a, b = _session_utc(day)
        rows, missing = [], 0
        for k in range(0, len(tickers), PER_REQ):
            syms = tickers[k:k + PER_REQ]
            for _ in range(3):
                try:
                    got = alpaca.fetch_bars(syms, a, b, "30m")
                    break
                except requests.HTTPError as exc:     # a symbol Alpaca rejects: drop it, retry
                    bad = [s for s in alpaca.bad_symbols_from_400(exc) if s in syms]
                    if not bad:
                        raise
                    syms = [s for s in syms if s not in bad]
                    missing += len(bad)
                    got = {}
            for t, arr in got.items():
                rows.extend((t, x["t"], x["o"], x["h"], x["l"], x["c"], x["v"]) for x in arr)
        df = pd.DataFrame(rows, columns=["ticker", "ts", "open", "high", "low", "close", "volume"])
        tmp = BARS30 / f"{day}.parquet.tmp"
        df.to_parquet(tmp, index=False)
        tmp.replace(BARS30 / f"{day}.parquet")
        return day, len(tickers), len(df)

    t0, done = time.time(), 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(one_day, d, ts) for d, ts in todo]
        for f in as_completed(futs):
            day, n_t, n_b = f.result()
            done += 1
            if verbose and (done % 10 == 0 or done == len(todo)):
                print(f"  {done}/{len(todo)} days ({day}: {n_t} names, {n_b:,} bars); "
                      f"{time.time() - t0:.0f}s", flush=True)


def _bars30(day: str, tickers: np.ndarray) -> dict[str, np.ndarray]:
    """One day's 30-minute bars as (SLOTS x names) arrays, NaN where a slot had no trade."""
    from zoneinfo import ZoneInfo
    k = len(tickers)
    out = {c: np.full((SLOTS, k), np.nan) for c in ("open", "high", "low", "close")}
    f = BARS30 / f"{day}.parquet"
    if not f.exists():
        return out
    b = pd.read_parquet(f)
    if b.empty:
        return out
    ts = pd.to_datetime(b.ts, utc=True).dt.tz_convert(ZoneInfo("America/New_York"))
    slot = ((ts.dt.hour * 60 + ts.dt.minute) - (9 * 60 + 30)) // 30
    b = b.assign(slot=slot.to_numpy())
    b = b[(b.slot >= 0) & (b.slot < SLOTS)]
    col = {t: j for j, t in enumerate(tickers)}
    j = b.ticker.map(col)
    b = b[j.notna()]
    jj, ss = j[j.notna()].astype(int).to_numpy(), b.slot.to_numpy()
    for c in out:
        out[c][ss, jj] = b[c].to_numpy(dtype=float)
    return out


def intraday_day(B: dict, e_slot: np.ndarray, inside: np.ndarray, tgt: np.ndarray,
                 stp: np.ndarray, ok: np.ndarray):
    """The entry day in 30-minute order from the entry slot. -> (exit price, kind, done)."""
    O, H, L = B["open"], B["high"], B["low"]
    k = O.shape[1]
    px = np.full(k, np.nan)
    kind = np.zeros(k, dtype=np.int8)
    done = np.zeros(k, dtype=bool)
    with np.errstate(invalid="ignore"):
        for sl in range(SLOTS):
            act = ok & ~done & (sl >= e_slot) & np.isfinite(O[sl]) & np.isfinite(H[sl]) & np.isfinite(L[sl])
            aft = act & (sl > e_slot)
            g_dn = aft & (O[sl] <= stp)
            g_up = aft & ~g_dn & (O[sl] >= tgt)
            s_hit = act & ~g_dn & ~g_up & (L[sl] <= stp)
            t_hit = act & ~g_dn & ~g_up & ~s_hit & (H[sl] >= tgt) & ~(inside & (sl == e_slot))
            for m, p, kd in ((g_dn, O[sl], -1), (g_up, O[sl], 1), (s_hit, stp, -1), (t_hit, tgt, 1)):
                if m.any():
                    px[m] = p[m]
                    kind[m] = kd
                    done |= m
    return px, kind, done


def _exit_prices(kd: str, xa: float, xb: float, e_px, a, lvl):
    if kd == "fixed":
        return e_px * (1 + xa), e_px * (1 - xb), np.ones(len(e_px), bool)
    if kd == "atr":
        return e_px * (1 + xa * a), e_px * (1 - xb * a), np.isfinite(a) & (a > 0)
    tgt = e_px * (1 + xa) if xa > 0 else e_px * (1 + (-xa) * a)
    stp = lvl * (1 - LEVEL_STOP)
    return tgt, stp, np.isfinite(stp) & (e_px > stp) & np.isfinite(tgt)


def build_intraday(verbose: bool = True) -> None:
    """E4 (open+1h) and E2' (limit in 30-minute order) for every exit -> OUT_NPZ_I."""
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    import big_movers_study as BM
    t0 = time.time()
    T = pd.read_parquet(OUT_TRADES)
    dates, ticks, W = BM.load_ohlc()
    di = {d: i for i, d in enumerate(dates)}
    ti = {t: j for j, t in enumerate(ticks)}
    ex = exits()
    names = [f"{e}|{x[0]}" for e in ("open1h", "limit30") for x in ex]
    n = len(T)
    ret = np.full((n, len(names)), np.nan, dtype=np.float32)
    kind = np.zeros((n, len(names)), dtype=np.int8)
    held = np.zeros((n, len(names)), dtype=np.int16)
    fallback = np.zeros(n, dtype=bool)
    no_entry = np.zeros(n, dtype=bool)
    nmax = LIMIT_DAYS + max(HOLDS) + 2
    for n_done, (D, g) in enumerate(T.groupby("date", sort=True)):
        rows = g.index.to_numpy()
        i = di[D]
        tk = g.ticker.to_numpy()
        js = g.ticker.map(ti).to_numpy()
        P = {c: W[c][i + 1:i + 1 + nmax][:, js].astype(np.float64) for c in ("open", "high", "low", "close")}
        lvl = g.level.to_numpy(dtype=float)
        a = g.atr_pct.to_numpy(dtype=float)
        B0 = _bars30(str(dates[i + 1]), tk)
        k = len(rows)
        # E4: the 10:30 bar's open, else the first later bar with one
        e_slot = np.full(k, -1)
        e_px4 = np.full(k, np.nan)
        for sl in range(PLUS1H_SLOT, SLOTS):
            m = (e_slot < 0) & np.isfinite(B0["open"][sl])
            e_slot[m], e_px4[m] = sl, B0["open"][sl][m]
        ok4 = e_slot >= 0
        no_entry[rows] = ~ok4
        # E2': where the daily engine filled the limit, find the fill inside that day
        lr = g.limit_row.to_numpy()
        lim = lvl * (1 + LIMIT_UP)
        e_px2 = np.full(k, np.nan)
        s2 = np.full(k, -1)
        in2 = np.zeros(k, bool)
        B2 = {}
        for r in (0, 1, 2):
            m = lr == r
            if not m.any():
                continue
            Br = B0 if r == 0 else _bars30(str(dates[i + 1 + r]), tk)
            B2[r] = Br
            for sl in range(SLOTS):
                todo = m & (s2 < 0)
                o, l = Br["open"][sl], Br["low"][sl]
                at_o = todo & np.isfinite(o) & (o <= lim)
                dip = todo & ~at_o & np.isfinite(l) & (l <= lim)
                e_px2[at_o], s2[at_o] = o[at_o], sl
                e_px2[dip], s2[dip], in2[dip] = lim[dip], sl, True
        fb = (lr >= 0) & (s2 < 0)                   # daily saw a fill the 30-minute bars do not
        fallback[rows] = fb
        for c_i, (nm, kd, xa, xb, h) in enumerate(ex):
            # ---- E4
            tgt, stp, okx = _exit_prices(kd, xa, xb, e_px4, a, lvl)
            ok = ok4 & okx & np.isfinite(e_px4)
            px, kk, dn = intraday_day(B0, e_slot, np.zeros(k, bool), tgt, stp, ok)
            zero = np.zeros(k, dtype=np.int64)
            px2, kk2, hh2 = simulate(P, zero + 1, zero + 1, np.zeros(k, bool), zero, tgt, stp,
                                     zero + h - 1, ok & ~dn)
            pxa = np.where(dn, px, px2)
            kka = np.where(dn, kk, kk2)
            hha = np.where(dn, 1, hh2)
            col = c_i
            ret[rows, col] = np.where(ok & np.isfinite(pxa), pxa / e_px4 - 1.0 - COST, np.nan)
            kind[rows, col], held[rows, col] = kka, hha
            # ---- E2'
            tgt, stp, okx = _exit_prices(kd, xa, xb, e_px2, a, lvl)
            ok = (s2 >= 0) & okx & np.isfinite(e_px2)
            pxa = np.full(k, np.nan)
            kka = np.zeros(k, dtype=np.int8)
            hha = np.zeros(k, dtype=np.int16)
            for r, Br in B2.items():
                m = ok & (lr == r)
                if not m.any():
                    continue
                px, kk, dn = intraday_day(Br, s2, in2, tgt, stp, m)
                er = zero + r
                px2, kk2, hh2 = simulate(P, er + 1, er + 1, np.zeros(k, bool), er, tgt, stp,
                                         er + h - 1, m & ~dn)
                pxa = np.where(m & dn, px, np.where(m, px2, pxa))
                kka = np.where(m & dn, kk, np.where(m, kk2, kka))
                hha = np.where(m & dn, 1, np.where(m, hh2, hha))
            col = len(ex) + c_i
            ret[rows, col] = np.where(ok & np.isfinite(pxa), pxa / e_px2 - 1.0 - COST, np.nan)
            kind[rows, col], held[rows, col] = kka, hha
        if verbose and n_done % 20 == 0:
            print(f"  {D}: {k:,} names; {time.time() - t0:.0f}s", flush=True)
    np.savez_compressed(OUT_NPZ_I, ret=ret, kind=kind, held=held, rules=np.array(names),
                        fallback=fallback, no_entry=no_entry)
    if verbose:
        print(f"built intraday: {n:,} name-dates x {len(names)} rules; open+1h had no entry on "
              f"{int(no_entry.sum()):,}; limit fills the 30-minute bars did not show: "
              f"{int(fallback.sum()):,}; {time.time() - t0:.0f}s", flush=True)


def analyse_intraday() -> dict:
    """I1 open+1h minus open, I2 limit (30-minute order) minus limit (pessimistic), per exit."""
    T = pd.read_parquet(OUT_TRADES)
    Z, ZI = np.load(OUT_NPZ), np.load(OUT_NPZ_I)
    R, RI = list(Z["rules"]), list(ZI["rules"])
    reti = ZI["ret"].copy()
    fb = ZI["fallback"]
    for x in exits():                 # a fill the 30-minute bars do not show keeps its daily result
        reti[fb, RI.index(f"limit30|{x[0]}")] = Z["ret"][fb, R.index(f"limit|{x[0]}")]
    PD, PI = per_date(T, Z["ret"]), per_date(T, reti)
    out = {"i1_open1h_minus_open": {}, "i2_limit30_minus_limit": {},
           "open1h_no_entry": int(ZI["no_entry"].sum()), "limit_fallbacks": int(ZI["fallback"].sum())}
    for x in exits():
        nm = x[0]
        out["i1_open1h_minus_open"][nm] = summary(PI[RI.index(f"open1h|{nm}")] - PD[R.index(f"open|{nm}")])
        out["i2_limit30_minus_limit"][nm] = summary(PI[RI.index(f"limit30|{nm}")] - PD[R.index(f"limit|{nm}")])
    allp = pd.concat([PD.rename(columns=dict(enumerate(R))), PI.rename(columns=dict(enumerate(RI)))], axis=1)
    trn = allp[allp.index < SPLIT]
    cover = trn.notna().mean()
    tt = trn.apply(_t)[cover >= MIN_TRAIN_COVER].dropna().sort_values(ascending=False)
    out["best_rule_with_intraday"] = {"chosen": tt.index[0], "train_t": float(tt.iloc[0]),
                                      "chosen_summary": summary(allp[tt.index[0]]),
                                      "minus_default": summary(allp[tt.index[0]] - allp[DEFAULT])}
    return out


# ------------------------------------------------------------------------- the checks
def check(n: int = 20, seed: int = 20261108) -> dict:
    """Random trades recomputed straight from the store's bars (not the wide arrays), and the
    hourly check of the open against the price about an hour after it."""
    import store
    T = pd.read_parquet(OUT_TRADES)
    Z = np.load(OUT_NPZ)
    R = list(Z["rules"])
    rng = np.random.default_rng(seed)
    out = {"trades": []}
    picks = rng.choice(len(T), n, replace=False)
    names = [DEFAULT, "limit|f5_5_h10", "close|f5_5_h10", "open|atr2_1_h10", "open|lvl_t5_h10"]
    bars = {}
    for p in picks:
        r = T.iloc[p]
        if r.ticker not in bars:
            b = store.read(interval="1d", tickers=[r.ticker], columns=["ticker", "date", "open", "high", "low", "close"])
            b["date"] = b.date.astype(str).str[:10]
            bars[r.ticker] = b.sort_values("date").reset_index(drop=True)
        b = bars[r.ticker]
        # the store's own session axis (the build's rows), so a missing bar is a gap, not a shift
        end = str((pd.Timestamp(r.date) + pd.Timedelta(days=40)).date())
        cal = sorted(store.read(interval="1d", start=r.date, end=end, columns=["date"])
                     .date.astype(str).str[:10].unique())
        sess = [x for x in cal if x > r.date][:LIMIT_DAYS + max(HOLDS) + 2]
        after = b.set_index("date").reindex(sess).reset_index()
        for nm in names:
            k = R.index(nm)
            mine = manual(after, r, nm)
            got = (float(Z["ret"][p, k]), int(Z["kind"][p, k]), int(Z["held"][p, k]))
            same = (np.isnan(mine[0]) and np.isnan(got[0])) or (
                abs(mine[0] - got[0]) < 1e-4 and mine[1] == got[1] and mine[2] == got[2])
            out["trades"].append({"date": r.date, "ticker": r.ticker, "rule": nm, "manual": mine,
                                  "built": got, "same": bool(same)})
    out["all_same"] = all(x["same"] for x in out["trades"])
    out["hourly"] = hourly_check()
    return out


def manual(after: pd.DataFrame, r: pd.Series, rule: str):
    """One trade, walked bar by bar in plain Python -- the reference for the engine."""
    en, ex = rule.split("|")
    O, H, L, C = (after[c].astype(float).tolist() for c in ("open", "high", "low", "close"))
    if not O:
        return (np.nan, 0, 0)
    bar = [all(np.isfinite(v) for v in (O[d], H[d], L[d], C[d])) for d in range(len(O))]
    a = float(r.atr_pct)
    if en == "open":
        if not np.isfinite(O[0]):
            return (np.nan, 0, 0)
        e_row, e_px, intra = 0, O[0], False
    elif en == "close":
        if not np.isfinite(C[0]):
            return (np.nan, 0, 0)
        e_row, e_px, intra = 0, C[0], False
    else:
        lim = float(r.level) * (1 + LIMIT_UP)
        e_row = None
        for d in range(min(LIMIT_DAYS, len(O))):
            if np.isfinite(O[d]) and O[d] <= lim:
                e_row, e_px, intra = d, O[d], False
                break
            if np.isfinite(L[d]) and L[d] <= lim:
                e_row, e_px, intra = d, lim, True
                break
        if e_row is None:
            return (np.nan, 0, 0)
    spec = {x[0]: x for x in exits()}[ex]
    _, kd, xa, xb, h = spec
    if kd == "fixed":
        tgt, stp = e_px * (1 + xa), e_px * (1 - xb)
    elif kd == "atr":
        tgt, stp = e_px * (1 + xa * a), e_px * (1 - xb * a)
    else:
        tgt = e_px * (1 + xa) if xa > 0 else e_px * (1 + (-xa) * a)
        stp = float(r.level) * (1 - LEVEL_STOP)
        if not e_px > stp:
            return (np.nan, 0, 0)
    start = e_row + 1 if en == "close" else e_row
    last = e_row + h if en == "close" else e_row + h - 1
    lastc, lastr = None, None
    for d in range(start, min(last, len(O) - 1) + 1):
        if not bar[d]:
            continue
        o, hi, lo, c = O[d], H[d], L[d], C[d]
        if d > e_row or en == "close":
            if o <= stp:
                return (o / e_px - 1 - COST, -1, d - e_row + (0 if en == "close" else 1))
            if o >= tgt:
                return (o / e_px - 1 - COST, 1, d - e_row + (0 if en == "close" else 1))
        if lo <= stp:
            return (stp / e_px - 1 - COST, -1, d - e_row + (0 if en == "close" else 1))
        if hi >= tgt and not (intra and d == start):
            return (tgt / e_px - 1 - COST, 1, d - e_row + (0 if en == "close" else 1))
        lastc, lastr = c, d
    if lastc is None:
        return (np.nan, 0, 0)
    return (lastc / e_px - 1 - COST, 0, lastr - e_row + (0 if en == "close" else 1))


def hourly_check() -> dict:
    """The store's hourly bars (88 names, Jul-Nov 2026): the day's open against the price about
    an hour later (the mid of the 10:00 bar), per name-day."""
    import store
    fr = [pd.read_parquet(store.part_path(m, "1h")) for m in store.months("1h")]
    h = pd.concat(fr, ignore_index=True)
    h["date"] = h.date.astype(str).str[:10]
    h["hour"] = pd.to_datetime(h.datetime).dt.hour
    first = h[h.hour == 9].set_index(["ticker", "date"])["open"]
    ten = h[h.hour == 10].set_index(["ticker", "date"])
    mid = (ten["open"] + ten["close"]) / 2
    j = pd.concat([first.rename("open"), mid.rename("mid")], axis=1).dropna()
    d = j["mid"] / j["open"] - 1
    return {"name_days": int(len(d)), "names": int(j.index.get_level_values(0).nunique()),
            "mean_pct": round(float(d.mean()) * 100, 3), "median_abs_pct": round(float(d.abs().median()) * 100, 3),
            "share_abs_over_2pct": round(float((d.abs() > 0.02).mean()), 3),
            "p10_pct": round(float(d.quantile(0.1)) * 100, 2), "p90_pct": round(float(d.quantile(0.9)) * 100, 2)}


def selftest() -> int:
    """The engine against hand-worked paths: target, stop, gaps, both-touched, a limit that
    filled coming down (its day's target ignored), a close entry, and a time exit."""
    nan = np.nan
    def run(O, H, L, C, en, tgt_pct, stp_pct, hold, level=None):
        P = {k: np.array(v, dtype=float).reshape(-1, 1) for k, v in (("open", O), ("high", H), ("low", L), ("close", C))}
        lv = np.array([level if level is not None else nan])
        e_px, e_row, start, gap_from, intra, ok = entries(P, lv)[en]
        tgt, stp = e_px * (1 + tgt_pct), e_px * (1 - stp_pct)
        last = e_row + hold - 1 if en != "close" else e_row + hold
        px, k, h = simulate(P, start, gap_from, intra, e_row, tgt, stp, last, ok, close_entry=(en == "close"))
        return round(float(px[0] / e_px[0] - 1), 4) if ok[0] else None, int(k[0]), int(h[0])
    cases = [
        ("target day 2", run([100, 101, 102], [101, 102, 106], [99, 100, 101], [100, 101, 105], "open", .05, .05, 10), (0.05, 1, 3)),
        ("stop day 1", run([100, 99], [101, 100], [99, 94], [100, 95], "open", .05, .05, 10), (-0.05, -1, 2)),
        ("gap down day 1", run([100, 93], [101, 94], [99, 92], [100, 93], "open", .05, .05, 10), (-0.07, -1, 2)),
        ("gap up day 1", run([100, 107], [101, 108], [99, 92], [100, 107], "open", .05, .05, 10), (0.07, 1, 2)),
        ("both touched", run([100, 100], [101, 106], [99, 94], [100, 100], "open", .05, .05, 10), (-0.05, -1, 2)),
        ("time exit", run([100, 101, 102], [101, 102, 103], [99, 100, 101], [100, 101, 102], "open", .05, .05, 3), (0.02, 0, 3)),
        ("limit at open", run([99, 100], [100, 106], [98, 99], [99, 105], "limit", .05, .05, 10, level=100), (0.05, 1, 2)),
        ("limit intraday: target ignored on its day", run([103, 104], [106, 109], [100, 103], [104, 108], "limit", .05, .05, 10, level=100), (0.05, 1, 2)),
        ("limit intraday: stop counts on its day", run([103], [104], [94], [96], "limit", .05, .05, 10, level=100), (-0.05, -1, 1)),
        ("limit never filled", run([110, 111, 112], [111, 112, 113], [105, 106, 107], [110, 111, 112], "limit", .05, .05, 10, level=100), (None, 0, 0)),
        ("close entry: day-0 range ignored", run([100, 101], [110, 106], [90, 100], [100, 101], "close", .05, .05, 10), (0.05, 1, 1)),
    ]
    def day(O, H, L, e_slot, inside, tgt, stp):
        pad = lambda v: np.array(v + [np.nan] * (SLOTS - len(v)), dtype=float).reshape(-1, 1)
        B = {"open": pad(O), "high": pad(H), "low": pad(L)}
        px, k, dn = intraday_day(B, np.array([e_slot]), np.array([inside]), np.array([tgt]),
                                 np.array([stp]), np.array([True]))
        return (round(float(px[0]), 3) if dn[0] else None, int(k[0]), bool(dn[0]))
    cases += [
        ("30m: stop in a later bar", day([99, 99, 100, 99], [99, 99, 101, 100], [98, 98, 99, 94], 2, False, 105, 95), (95.0, -1, True)),
        ("30m: a later bar opens over the target", day([99, 99, 100, 106], [99, 99, 101, 107], [98, 98, 99, 104], 2, False, 105, 95), (106.0, 1, True)),
        ("30m: one bar reaching both is the stop", day([99, 99, 100], [99, 99, 106], [98, 98, 94], 2, False, 105, 95), (95.0, -1, True)),
        ("30m: filled inside a bar, its target ignored", day([103, 102], [106, 106], [100, 101], 0, True, 105.525, 95.475), (105.525, 1, True)),
        ("30m: nothing hit that day", day([99, 99, 100, 101], [99, 99, 101, 102], [98, 98, 99, 100], 2, False, 105, 95), (None, 0, False)),
        ("30m: bars before the entry bar ignored", day([99, 90, 100, 101], [110, 99, 101, 102], [80, 89, 99, 100], 2, False, 105, 95), (None, 0, False)),
    ]
    bad = 0
    for name, got, want in cases:
        okc = (got[0] == want[0] or (got[0] is not None and want[0] is not None and abs(got[0] - want[0]) < 1e-3)) and got[1:] == want[1:]
        bad += not okc
        print(f"  {'ok ' if okc else 'BAD'} {name}: got {got}, want {want}")
    print("selftest:", "PASS" if not bad else f"{bad} FAILED")
    return 1 if bad else 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    if "--build" in sys.argv:
        build()
        return 0
    if "--fetch-intraday" in sys.argv:
        fetch_intraday()
        return 0
    if "--build-intraday" in sys.argv:
        build_intraday()
        return 0
    if "--intraday" in sys.argv:
        s = analyse_intraday()
        f = config.DATA / "_trade_rules_intraday.json"
        f.write_text(json.dumps(s, indent=1, default=str), encoding="utf-8")
        print(f"wrote {f.name}")
        return 0
    if "--check" in sys.argv:
        c = check()
        bad = [x for x in c["trades"] if not x["same"]]
        print(json.dumps({"checked": len(c["trades"]), "all_same": c["all_same"], "mismatches": bad[:10],
                          "hourly": c["hourly"]}, indent=1, default=str))
        return 0 if c["all_same"] else 1
    s = analyse()
    OUT.write_text(json.dumps(s, indent=1, default=str), encoding="utf-8")
    print(f"wrote {OUT.name}: {s['tests_counted']} comparisons, ~{s['expected_abs_t_ge_2_by_chance']} expected "
          f"at |t| >= 2 by chance")
    return 0


if __name__ == "__main__":
    sys.exit(main())
