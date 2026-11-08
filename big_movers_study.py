#!/usr/bin/env python
"""Does a stock that OFTEN runs -- among the day's biggest movers, or rallying again and
again -- bounce better at support?

    python big_movers_study.py              ~1 h in the background; writes
                                            data/_big_movers_study.json and
                                            data/_big_movers_events.parquet
    python big_movers_study.py --summarise  re-reads the events file, rewrites the summary
    python big_movers_study.py --smoke      6 dates x 120 names, writes nothing (code check)
    python big_movers_study.py --trades     the bounce trade on the saved events (~1 min);
                                            data/_big_movers_trades.json and .parquet

Asked 2026-11-02: "a stock that is more in the daily gainers list is more likely to go up
at key levels?" Measured on 2026-10-30 first: how often a liquid stock was a top-20 daily
gainer correlates 0.82 with how often it was a top-20 LOSER (the 50 most frequent gainers
had a median 10 loser days vs 1 for all names), so a gainer count is mostly a volatility
count. The literature points the other way on average (Bali, Cakici & Whitelaw 2011:
stocks with big one-day gains earn LOWER returns next; reportedly not when the jumps are
earnings news). Nobody we found tested it AT SUPPORT, so it is measured here.

The same day: one huge jump seen a few times is NOT the pattern wanted. It is FREQUENCY:
names (AI ones, say) that go up, come down and go up again, over and over through the
year. Every burst score below already counts DAYS, so a +80% day counts once, like a +9%
one; the CYCLE scores were added for the up-down-up pattern itself, before any result.

DESIGN, fixed before any result was seen:

  DATES   every 42nd session from 2018-01, up to the last with 60 sessions after it.
          The level outcome runs 40 sessions from entry, so no two dates' windows overlap.
  WHO     point-in-time liquid names (close >= $5, 20-session median dollar volume >=
          $20M, from the bars up to the date; prices passing zones.is_suspect). Zones from
          up to 6 years of the name's own bars, as the nightly scan. AT SUPPORT = the
          nearest zone 0-6% below the close (bands AT/NEAR), the lists' definition.
  BURSTS  a day the stock was among the biggest movers, by two definitions:
            top20  market-wide top-20 daily % gainers (losers) among names liquid the day
                   before -- the lists the user reads;
            fifth  top (bottom) 1% of daily returns within its liquidity fifth ($2+,
                   $1M+/day, as event_returns), so a large company can qualify.
          counted over three windows: the flat last 252 sessions (w252), and decayed with
          a half-life of 21 (e21) or 63 (e63) sessions. G = up-bursts, L = down-bursts.
  SCORES  activity A = G + L; TILT T = (G - L) / (G + L + 2) (shrunk: one lucky day is
          not a pattern); gain_days = top20 w252 G, the ranking behind the 10-23 list.
          EARNINGS: a burst on a session with an 8-K Item 2.02 public that day or the day
          before is an earnings burst; T_other is the tilt from the other bursts only
          (top20/fifth, e63).
  CYCLES  rally_count    the 10% zigzag of closes (a leg ends when the close turns 10%
                         from its extreme); a RALLY is an up-leg of >= 20% trough to
                         peak, counted when its 10% turn is in the last 252 sessions --
                         one +200% run counts once, five +25% runs count five.
          recovery_rate  a DIP is the first close >= 15% under the 63-session high;
                         recovered = a close back at that high within 63 sessions; the
                         share recovered over the dips of the last 252 sessions whose 63
                         have passed (gate: >= 2 dips) -- "comes down, goes up again".
          burst_months   the 21-session blocks (12 in the year) with at least one
                         up-burst (top20, fifth); gated on >= 2 up-bursts it separates a
                         year of repeated runs from one hot stretch of the same count.
          peer_rally_count  the mean rally_count of the other liquid names in the same
                         sub-industry that date (>= 5 of them): a theme that keeps running
                         (today's sub-industry map: a small look-ahead).
  CONTROLS vol63 (st.dev. of 63 daily returns) and mom_12_1 (the close 21 sessions ago
          over the close 252 ago). Every count above rises with volatility, and a volatile
          name moves further both ways, so the main scores are also measured
          VOLATILITY-NEUTRAL: each date's names split into volatility thirds, the score's
          top-minus-bottom taken inside each third, averaged.
  OUTCOME entry the next close; excess return vs the same liquidity fifth at 20 and 60
          sessions (event_returns); FAIL = a close <= 90% of the level within 40 sessions
          (the zones study's break), HIT = a close >= 115% of it (bounce_hit's rule).
  TESTS   per date, the score's top third minus its bottom third (>= 30 names, >= 10 a
          side); ties stay together -- above the 2/3 point vs at or below the 1/3 point,
          so a count with many zeros compares any vs none. t across dates; halves before
          / from 2022-01-01. Burst tilts are gated on >= 2 bursts in the year.
          PRIMARY: tilt, rally_count and recovery_rate -> excess_20 and FAIL, plain and
          volatility-neutral. WALK-FORWARD: the score (6 tilt configurations,
          rally_count, recovery_rate, burst_months x 2) is CHOSEN on dates before 2022 by
          its excess_20 spread t; its after-2022 result is reported against two baselines
          on the same dates: every at-support name (the plain rule) and gain_days (the
          current method). Secondary, counted: activity, gain_days, earnings-free tilt,
          burst_months, peers, momentum and volatility (references), and spreads over
          ALL liquid names (the MAX effect, for context).
  CAVEATS survivorship (vanished names are under-represented: flatters the volatile);
          a split error in the store would show as a false burst on both sides; early
          dates have fewer years of bars for their zones.

THE BOUNCE TRADE (`--trades`; added 2026-11-02 when the user said the aim is a quick %
profit off the level, not a hold -- designed after the 20/60-session results above were
seen, before any trade result):

  ENTRY   the next session's OPEN (the list is built after the close; backtest.py's rule).
  EXIT    a bracket on daily bars: target +X% and stop -X% from the entry; a gap through
          either side fills at that open; a day reaching both counts as the STOP (the
          order inside a day is unknowable from daily bars); otherwise the close of
          session N. No commissions or slippage beyond the gaps.
          PRIMARY b5_10: +/-5%, 10 sessions. Also b3_5, b3_10, b8_10, b5_5, b5_20, and
          lv5_10: +5% target, stop 3% under the support level (no trade when the open is
          already under it).
  GROUPS  at support: the score's top third vs its bottom third (per date, ties
          together); the LIST RULE (up to 10 per sector by gain_days, as the 10-23 list);
          every at-support name; every liquid name (any stock, any day). LEVEL VALUE: hot
          names (the score's top third among ALL liquid names that date) at support
          minus the same hot names elsewhere.
  MEASURE average trade return (pp: the expectancy), win rate (target first), stop
          rate; t across dates (10-session trades from dates 42 apart never overlap);
          halves before / from 2022.
  PRIMARY gain_days (the list's own ranking) and rally_count -> the b5_10 trade return:
          hot minus cold at support, and hot at support minus hot elsewhere.
  WALK-FORWARD the score chosen on pre-2022 dates by its b5_10 hot-minus-cold t, tested
          after 2022 against the list rule, every at-support name and every liquid name.
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
import event_returns as ER
import zones as Z
import zonelen_study as ZL

STEP = 42
HOLD = 40
START = "2018-01-01"
SPLIT = "2022-01-01"
MIN_CLOSE, MIN_DV = 5.0, 20e6          # the user's liquid universe
ER_PX, ER_DV = 2.0, 1e6                # event_returns' eligibility, for the fifths
TOP_N, TOP_PCT = 20, 0.01
HALF_LIVES = (21, 63)
MIN_BURSTS = 2
ZZ_TURN, ZZ_RALLY = 0.10, 0.20         # zigzag turn; a rally is an up-leg of >= 20%
DIP, DIP_HI, DIP_BACK = 0.15, 63, 63   # 15% under the 63-session high; back within 63
YEAR, BLOCK = 252, 21                  # 12 blocks of 21 sessions
MIN_SIDE = 10
SMOKE_DATES, SMOKE_NAMES = 6, 120
OUT = config.DATA / "_big_movers_study.json"
OUT_EVENTS = config.DATA / "_big_movers_events.parquet"
# the bounce trade: name -> (target, stop, sessions); stop "level" = 3% under the level
BRACKETS = {"b5_10": (0.05, 0.05, 10), "b3_5": (0.03, 0.03, 5), "b3_10": (0.03, 0.03, 10),
            "b8_10": (0.08, 0.08, 10), "b5_5": (0.05, 0.05, 5), "b5_20": (0.05, 0.05, 20),
            "lv5_10": (0.05, "level", 10)}
PRIMARY = "b5_10"
LEVEL_STOP = 0.03
LIST_PER_SECTOR = 10
TRADES_OUT = config.DATA / "_big_movers_trades.json"
TRADES_EVENTS = config.DATA / "_big_movers_trades.parquet"


def use_step(n: int) -> None:
    """Space the dates `n` sessions apart instead of 42, into files of their own (the
    42-step study's files are never overwritten). At n < 40 the 40-session level
    windows and the 20/60-session returns overlap, so only trades no longer than n
    sessions keep independent dates."""
    global STEP, OUT, OUT_EVENTS, TRADES_OUT, TRADES_EVENTS
    STEP = n
    if n != 42:
        OUT = config.DATA / f"_big_movers_study_s{n}.json"
        OUT_EVENTS = config.DATA / f"_big_movers_events_s{n}.parquet"
        TRADES_OUT = config.DATA / f"_big_movers_trades_s{n}.json"
        TRADES_EVENTS = config.DATA / f"_big_movers_trades_s{n}.parquet"


def earnings_rows(dates: np.ndarray, ticks: np.ndarray) -> set[tuple[int, int]]:
    """(row, ticker index) pairs that are an earnings day: an 8-K Item 2.02 public that
    session or the one before (public_session is the first close after acceptance)."""
    try:
        import news_events as NE
        e = NE.explode_items(NE.read(start=str(dates[0])))
        e = e[(e.event_type == "earnings") & e.ticker.notna()]
    except Exception:                                            # noqa: BLE001
        return set()
    ti = {t: k for k, t in enumerate(ticks)}
    out = set()
    pos = np.searchsorted(dates, e.public_session.astype(str).to_numpy())
    for t, p in zip(e.ticker.astype(str), pos):
        j = ti.get(t)
        if j is None or p >= len(dates):
            continue
        out.add((int(p), j))
        if p + 1 < len(dates):
            out.add((int(p) + 1, j))           # an after-close release reacts next session
    return out


def burst_panel(dates, ticks, W, study_rows: set[int], verbose=True):
    """One pass over every session: daily bursts by both definitions, flat 252-session
    counts, burst months and decayed sums, recorded at the study rows only."""
    n_d, n_t = W["close"].shape
    C, V = W["close"], W["volume"]
    dv = C * V
    earn = earnings_rows(dates, ticks)
    earn_by_row: dict[int, list[int]] = {}
    for r, j in earn:
        earn_by_row.setdefault(r, []).append(j)
    keys = [(d, s) for d in ("top20", "fifth") for s in ("up", "dn")]
    hist = {k: np.zeros((n_d, n_t), dtype=np.bool_) for k in keys}        # for w252 counts
    dec = {(k, h): np.zeros(n_t, dtype=np.float64) for k in keys for h in HALF_LIVES}
    dec_other = {k: np.zeros(n_t, dtype=np.float64) for k in keys}      # e63, non-earnings
    fac = {h: 0.5 ** (1.0 / h) for h in HALF_LIVES}
    rec = {}
    t0 = time.time()
    for i in range(1, n_d):
        r = C[i] / C[i - 1] - 1.0
        ok_r = np.isfinite(r)
        lo = max(0, i - 20)
        med = np.nanmedian(dv[lo:i], axis=0) if i - lo >= 15 else np.full(n_t, np.nan)
        mean = np.nanmean(dv[lo:i], axis=0) if i - lo >= 15 else np.full(n_t, np.nan)
        liq = ok_r & (C[i - 1] >= MIN_CLOSE) & (med >= MIN_DV)
        el = ok_r & (C[i - 1] >= ER_PX) & (mean >= ER_DV)
        day = {k: np.zeros(n_t, dtype=np.bool_) for k in keys}
        idx = np.flatnonzero(liq)
        if len(idx) >= 2 * TOP_N:
            o = idx[np.argsort(r[idx])]
            day[("top20", "dn")][o[:TOP_N]] = True
            day[("top20", "up")][o[-TOP_N:]] = True
        idx = np.flatnonzero(el)
        if len(idx) >= 100:
            q = np.argsort(np.argsort(mean[idx])) / len(idx)          # liquidity rank 0..1
            fifth = np.minimum((q * 5).astype(int), 4)
            for b in range(5):
                m = idx[fifth == b]
                if len(m) < 50:
                    continue
                k = max(1, int(round(len(m) * TOP_PCT)))
                o = m[np.argsort(r[m])]
                day[("fifth", "dn")][o[:k]] = True
                day[("fifth", "up")][o[-k:]] = True
        is_earn = np.zeros(n_t, dtype=np.bool_)
        if i in earn_by_row:
            is_earn[earn_by_row[i]] = True
        for k in keys:
            hist[k][i] = day[k]
            for h in HALF_LIVES:
                dec[(k, h)] = dec[(k, h)] * fac[h] + day[k]
            dec_other[k] = dec_other[k] * fac[63] + (day[k] & ~is_earn)
        if i in study_rows:
            lo252 = max(0, i - YEAR + 1)
            snap = {}
            for k in keys:
                snap[(k, "w252")] = hist[k][lo252:i + 1].sum(axis=0).astype(np.float32)
                for h in HALF_LIVES:
                    snap[(k, f"e{h}")] = dec[(k, h)].astype(np.float32)
                snap[(k, "e63_other")] = dec_other[k].astype(np.float32)
                if k[1] == "up" and i + 1 - lo252 == YEAR:
                    blk = hist[k][lo252:i + 1].reshape(YEAR // BLOCK, BLOCK, n_t)
                    snap[(k, "months")] = blk.any(axis=1).sum(axis=0).astype(np.float32)
            rec[i] = snap
        if verbose and i % 500 == 0:
            print(f"  bursts: {i:,}/{n_d:,} sessions, {time.time() - t0:.0f}s", flush=True)
    return rec


def rallies_of(c: list[float]) -> list[int]:
    """Rows where a rally was COMPLETED: an up-leg of the 10% zigzag of at least 20%
    trough to peak, dated at the 10% turn that ends it (nothing after the row is used)."""
    out = []
    mode, hi, lo, trough = 0, None, None, None
    for t, x in enumerate(c):
        if not x > 0:                           # NaN (no bar) or a bad print
            continue
        if hi is None:
            hi = lo = x
            continue
        if mode >= 0 and x > hi:
            hi = x
        if mode <= 0 and x < lo:
            lo = x
        if mode >= 0 and x <= hi * (1 - ZZ_TURN):           # the peak at `hi` is in
            if mode == 1 and hi >= trough * (1 + ZZ_RALLY):
                out.append(t)
            mode, lo = -1, x
        elif mode <= 0 and x >= lo * (1 + ZZ_TURN):          # the trough at `lo` is in
            mode, hi, trough = 1, x, lo
    return out


def dips_of(c: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Rows of dips (the first close >= 15% under the 63-session high close) and whether
    each closed back at that high within 63 sessions. The next dip is looked for after
    the recovery, or after the 63 sessions when there was none."""
    H = pd.Series(c).rolling(DIP_HI, min_periods=20).max().to_numpy()
    cand = np.flatnonzero(np.isfinite(c) & np.isfinite(H) & (c <= H * (1 - DIP)))
    rows, rec, nxt = [], [], 0
    for t in cand:
        if t < nxt:
            continue
        if t + DIP_BACK >= len(c):
            break
        k = np.flatnonzero(c[t + 1:t + 1 + DIP_BACK] >= H[t])
        rows.append(t)
        rec.append(bool(len(k)))
        nxt = t + 2 + int(k[0]) if len(k) else t + 1 + DIP_BACK
    return np.asarray(rows, dtype=np.int64), np.asarray(rec, dtype=bool)


def cycle_panel(W, study_rows: list[int], verbose=True) -> dict[str, np.ndarray]:
    """rally_count, dips and recovered at each study row (study rows x tickers)."""
    C = W["close"]
    n_t = C.shape[1]
    R = np.asarray(study_rows, dtype=np.int64)
    out = {k: np.full((len(R), n_t), np.nan, dtype=np.float32)
           for k in ("rally_count", "dips", "recovered")}
    t0 = time.time()
    for j in range(n_t):
        c = C[:, j].astype(np.float64)
        if np.isfinite(c).sum() < YEAR:
            continue
        ra = np.asarray(rallies_of(c.tolist()), dtype=np.int64)
        out["rally_count"][:, j] = np.searchsorted(ra, R, "right") - np.searchsorted(ra, R - YEAR, "right")
        dr, ok = dips_of(c)
        cum = np.concatenate([[0], np.cumsum(ok)])
        hi = np.searchsorted(dr, R - DIP_BACK, "right")       # dips at rows <= i - 63 ...
        lo = np.searchsorted(dr, R - YEAR, "right")           # ... and > i - 252
        out["dips"][:, j] = hi - lo
        out["recovered"][:, j] = cum[hi] - cum[lo]
        if verbose and j % 1000 == 0:
            print(f"  cycles: {j:,}/{n_t:,} tickers, {time.time() - t0:.0f}s", flush=True)
    return out


def tilt(g, l):
    return (g - l) / (g + l + 2.0)


def run(verbose=True, smoke=False) -> pd.DataFrame:
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    t0 = time.time()
    dates, ticks, W = ZL.load()
    valid = np.isfinite(W["close"])
    dv = W["close"] * W["volume"]
    i0 = int(np.searchsorted(dates, START))
    rows_d = list(range(i0, len(dates) - 61, STEP))
    if smoke:
        rows_d = rows_d[::max(1, len(rows_d) // SMOKE_DATES)][:SMOKE_DATES]
    if verbose:
        print(f"loaded {len(dates):,} sessions x {len(ticks):,} tickers; {len(rows_d)} dates "
              f"{dates[rows_d[0]]}..{dates[rows_d[-1]]}; {time.time() - t0:.0f}s", flush=True)
    B = burst_panel(dates, ticks, W, set(rows_d), verbose)
    CY = cycle_panel(W, rows_d, verbose)
    rng = np.random.default_rng(20261102)
    C = W["close"]
    parts = []                                       # one frame per date: 220 dates of dicts would not fit
    for n_i, i in enumerate(rows_d):
        D = str(dates[i])
        six = str((pd.Timestamp(D) - pd.DateOffset(years=6)).date())
        lo6 = int(np.searchsorted(dates, six))
        med = np.nanmedian(dv[max(0, i - 19):i + 1], axis=0)
        ok = valid[i] & (C[i] >= MIN_CLOSE) & (med >= MIN_DV)
        r63 = C[i - 62:i + 1] / C[i - 63:i] - 1.0
        vol = np.where(np.isfinite(r63).sum(axis=0) >= 50, np.nanstd(r63, axis=0, ddof=1), np.nan)
        mom = C[i - 21] / C[i - YEAR] - 1.0
        snap = B[i]
        names = np.flatnonzero(ok)
        if smoke and len(names) > SMOKE_NAMES:
            names = np.sort(rng.choice(names, SMOKE_NAMES, replace=False))
        n_at, day = 0, []
        for j in names:
            st = ZL.listing_start(valid[:, j], i)
            rows = np.flatnonzero(valid[max(st, lo6):i + 1, j]) + max(st, lo6)
            price = float(C[i, j])
            row = {"date": D, "ticker": str(ticks[j]), "price": price,
                   "vol63": float(vol[j]), "mom_12_1": float(mom[j])}
            for (k, s), v in snap.items():
                d_, side = k
                row[f"{d_}_{side}_{s}"] = float(v[j])
            for k, v in CY.items():
                row[k] = float(v[n_i, j])
            lvl = np.nan
            if len(rows) >= Z.MIN_BARS:
                b = ZL.frame(dates, W, j, rows)
                if not Z.is_suspect(b):
                    z = Z.zones_for(str(ticks[j]), b)
                    if z is not None and not z.empty:
                        lv = z["level"].astype(float).to_numpy()
                        dist = price / lv - 1.0
                        k = np.flatnonzero((dist >= 0) & (dist <= 0.06))
                        if len(k):
                            m = k[np.argmin(dist[k])]
                            lvl = float(lv[m])
                            row.update(dist=float(dist[m]), touches=float(z["touches"].iloc[m]),
                                       band=ZL.band(float(dist[m])))
            row["level"] = lvl
            row["at_support"] = bool(np.isfinite(lvl))
            n_at += row["at_support"]
            c = C[i + 1:i + 1 + HOLD, j]
            c = c[np.isfinite(c)]
            if np.isfinite(lvl) and len(c) >= HOLD * 0.9:
                row["fail"] = bool(c.min() <= lvl * 0.90)
                row["hit"] = bool(c.max() >= lvl * 1.15)
            day.append(row)
        parts.append(pd.DataFrame(day))
        if verbose:
            print(f"  {D}: {len(names):,} liquid, {n_at:,} at support; {time.time() - t0:.0f}s", flush=True)
    del W, B, CY, valid, dv, C                       # the panel below loads its own prices
    ev = pd.concat(parts, ignore_index=True)
    del parts
    ev["recovery_rate"] = np.where(ev.dips >= 1, ev.recovered / ev.dips.where(ev.dips >= 1), np.nan)
    import sectors
    sub = sectors.groups().drop_duplicates("ticker").set_index("ticker")["subind"]
    ev["subind"] = ev.ticker.map(sub)
    agg = (ev.dropna(subset=["subind", "rally_count"]).groupby(["date", "subind"])["rally_count"]
           .agg(peer_sum="sum", peer_n="count"))
    ev = ev.join(agg, on=["date", "subind"])
    peers = ev.peer_n - 1
    ev["peer_rally_count"] = np.where(peers >= 5, (ev.peer_sum - ev.rally_count) / peers.where(peers >= 1), np.nan)
    ev = ev.drop(columns=["peer_sum", "peer_n"])
    panel = ER.Panel(horizons=(20, 60))
    m = panel.measure(ev[["ticker", "date"]].rename(columns={"date": "event_date"}))
    ev = ev.merge(m[["ticker", "event_date", "entry", "excess_20", "excess_60", "bucket"]]
                  .rename(columns={"event_date": "date"}), on=["ticker", "date"], how="left")
    for d_ in ("top20", "fifth"):
        for s in ("w252", "e21", "e63", "e63_other"):
            g, l = ev[f"{d_}_up_{s}"], ev[f"{d_}_dn_{s}"]
            ev[f"T_{d_}_{s}"] = tilt(g, l)
            ev[f"A_{d_}_{s}"] = g + l
    ev["gain_days"] = ev["top20_up_w252"]
    if not smoke:
        ev.to_parquet(OUT_EVENTS, index=False)
    if verbose:
        print(f"events: {len(ev):,} name-dates, {int(ev.at_support.sum()):,} at support; "
              f"{time.time() - t0:.0f}s", flush=True)
    return ev


def _sides(x: pd.Series, min_side: int):
    """The score's top and bottom thirds with ties kept together: above the 2/3 point, and
    at or below the 1/3 point (a count with many zeros compares any vs none). None when a
    side is too small or the two overlap."""
    lo_q, hi_q = x.quantile(1 / 3), x.quantile(2 / 3)
    top = x > hi_q
    if top.sum() < min_side:
        top = x >= hi_q
    bot = x <= lo_q
    if top.sum() < min_side or bot.sum() < min_side or bool((top & bot).any()):
        return None
    return top, bot


def _t(x: pd.Series):
    x = x.dropna()
    if len(x) < 3 or x.std(ddof=1) == 0:
        return None
    return round(float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))), 2)


def _pp(v):
    return round(float(v) * 100, 2) if v is not None and pd.notna(v) else None


def _report(per: list) -> dict:
    if len(per) < 5:
        return {"dates": len(per)}
    p = pd.DataFrame(per, columns=["date", "diff", "hi", "lo", "n"])
    a, b = p[p.date < SPLIT], p[p.date >= SPLIT]
    return {"dates": len(p), "names_per_date": int(p.n.median()),
            "top_minus_bottom": _pp(p["diff"].mean()), "t": _t(p["diff"]),
            "top": _pp(p.hi.mean()), "bottom": _pp(p.lo.mean()),
            "t_before_2022": _t(a["diff"]), "t_from_2022": _t(b["diff"]),
            "diff_before_2022": _pp(a["diff"].mean()) if len(a) else None,
            "diff_from_2022": _pp(b["diff"].mean()) if len(b) else None}


def _subset(ev, score, outcome, gate, control=None):
    e = ev[ev[outcome].notna() & ev[score].notna()]
    if control:
        e = e[e[control].notna()]
    if gate:
        e = e[e[gate] >= MIN_BURSTS]
    return e


def spread(ev: pd.DataFrame, score: str, outcome: str, gate: str | None = None) -> dict:
    """Per date: the score's top third minus its bottom third (names passing `gate`)."""
    per = []
    for d, g in _subset(ev, score, outcome, gate).groupby("date"):
        if len(g) < 30:
            continue
        s = _sides(g[score], MIN_SIDE)
        if s is None:
            continue
        y = g[outcome].astype(float)
        hi, lo = y[s[0]].mean(), y[s[1]].mean()
        per.append((d, hi - lo, hi, lo, len(g)))
    return _report(per)


def spread_neutral(ev: pd.DataFrame, score: str, outcome: str, gate: str | None = None,
                   control: str = "vol63") -> dict:
    """The same inside each third of `control` on each date, averaged: what the score
    adds among names of about the same volatility (or momentum)."""
    per = []
    for d, g in _subset(ev, score, outcome, gate, control).groupby("date"):
        if len(g) < 45:
            continue
        cq = g[control].rank(pct=True, method="first")
        y = g[outcome].astype(float)
        parts = []
        for a, b in ((0, 1 / 3), (1 / 3, 2 / 3), (2 / 3, 1.0)):
            m = (cq > a) & (cq <= b)
            s = _sides(g.loc[m, score], 5)
            if s is not None:
                yy = y[m]
                parts.append((yy[s[0]].mean(), yy[s[1]].mean()))
        if len(parts) >= 2:
            hi, lo = float(np.mean([p[0] for p in parts])), float(np.mean([p[1] for p in parts]))
            per.append((d, hi - lo, hi, lo, len(g)))
    return _report(per)


def _per_date_mean(e: pd.DataFrame, col: str):
    x = e[e[col].notna()]
    if not len(x):
        return None
    return _pp(x.assign(_y=x[col].astype(float)).groupby("date")["_y"].mean().mean())


def buckets(at: pd.DataFrame, score: str, edges: list[float], labels: list[str],
            gate: str | None = None) -> list[dict]:
    """Plain group averages of a score (pooled over dates), for reading the tests."""
    e = at[at[score].notna()]
    if gate:
        e = e[e[gate] >= MIN_BURSTS]
    grp = pd.cut(e[score], edges, labels=labels, include_lowest=True)
    out = []
    for lab in labels:
        x = e[grp == lab]
        out.append({"group": lab, "name_dates": int(len(x)),
                    "excess_20": _pp(x.excess_20.mean()) if x.excess_20.notna().any() else None,
                    "fail_pct": _pp(x.fail.dropna().astype(float).mean()) if x.fail.notna().any() else None,
                    "hit_pct": _pp(x.hit.dropna().astype(float).mean()) if x.hit.notna().any() else None,
                    "vol63_median_pct": _pp(x.vol63.median()) if x.vol63.notna().any() else None})
    return out


def summarise(ev: pd.DataFrame) -> dict:
    at = ev[ev.at_support]
    out = {"measured_on": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "dates": int(ev.date.nunique()), "first": str(ev.date.min()), "last": str(ev.date.max()),
           "name_dates": int(len(ev)), "at_support_name_dates": int(len(at)),
           "definitions": __doc__.strip(), "tests": {}}
    out["baseline_per_date_mean"] = {
        o: {"at_support": _per_date_mean(at, o),
            "all_liquid": _per_date_mean(ev, o) if o.startswith("excess") else None}
        for o in ("excess_20", "excess_60", "fail", "hit")}
    tests = {}
    outs = ("excess_20", "fail", "hit", "excess_60")
    configs = [(d_, s) for d_ in ("top20", "fifth") for s in ("w252", "e21", "e63")]
    for d_, s in configs:
        for o in outs:
            tests[f"AT tilt {d_} {s} -> {o}"] = spread(at, f"T_{d_}_{s}", o, f"A_{d_}_w252")
            tests[f"AT activity {d_} {s} -> {o}"] = spread(at, f"A_{d_}_{s}", o)
    for o in outs:
        tests[f"AT rally_count -> {o}"] = spread(at, "rally_count", o)
        tests[f"AT rally_count vol-neutral -> {o}"] = spread_neutral(at, "rally_count", o)
        tests[f"AT recovery_rate -> {o}"] = spread(at, "recovery_rate", o, "dips")
        tests[f"AT recovery_rate vol-neutral -> {o}"] = spread_neutral(at, "recovery_rate", o, "dips")
        tests[f"AT recovery_rate momentum-neutral -> {o}"] = spread_neutral(at, "recovery_rate", o, "dips", "mom_12_1")
        tests[f"AT tilt fifth e63 vol-neutral -> {o}"] = spread_neutral(at, "T_fifth_e63", o, "A_fifth_w252")
        for d_ in ("top20", "fifth"):
            tests[f"AT burst_months {d_} (>= 2 up-bursts) -> {o}"] = spread(at, f"{d_}_up_months", o, f"{d_}_up_w252")
            tests[f"AT tilt {d_} e63 earnings-free -> {o}"] = spread(at, f"T_{d_}_e63_other", o, f"A_{d_}_w252")
        tests[f"AT gain_days -> {o}"] = spread(at, "gain_days", o)
        tests[f"AT gain_days (>= 2 up-bursts) -> {o}"] = spread(at, "gain_days", o, "top20_up_w252")
        tests[f"AT gain_days vol-neutral -> {o}"] = spread_neutral(at, "gain_days", o)
        tests[f"AT peer_rally_count -> {o}"] = spread(at, "peer_rally_count", o)
        tests[f"AT momentum 12-1 (reference) -> {o}"] = spread(at, "mom_12_1", o)
        tests[f"AT volatility 63 (reference) -> {o}"] = spread(at, "vol63", o)
        if o.startswith("excess"):
            tests[f"ALL tilt fifth e63 -> {o}"] = spread(ev, "T_fifth_e63", o, "A_fifth_w252")
            tests[f"ALL gain_days -> {o}"] = spread(ev, "gain_days", o)
            tests[f"ALL rally_count -> {o}"] = spread(ev, "rally_count", o)
    out["tests"] = tests
    # WALK-FORWARD: the score chosen on dates before 2022 by its excess_20 spread t
    cands = {f"tilt {d_} {s}": (f"T_{d_}_{s}", f"A_{d_}_w252") for d_, s in configs}
    cands.update({"rally_count": ("rally_count", None), "recovery_rate": ("recovery_rate", "dips"),
                  "burst_months top20": ("top20_up_months", "top20_up_w252"),
                  "burst_months fifth": ("fifth_up_months", "fifth_up_w252")})
    pre, post = at[at.date < SPLIT], at[at.date >= SPLIT]
    train = {k: spread(pre, sc, "excess_20", gt).get("t") for k, (sc, gt) in cands.items()}
    best = max((k for k, v in train.items() if v is not None), key=lambda k: train[k], default=None)
    wf = {"train_t_excess_20": train, "chosen": best}
    if best:
        sc, gt = cands[best]
        wf["test_excess_20"] = spread(post, sc, "excess_20", gt)
        wf["test_excess_20_vol_neutral"] = spread_neutral(post, sc, "excess_20", gt)
        wf["test_fail"] = spread(post, sc, "fail", gt)
        wf["baseline_gain_days_excess_20"] = spread(post, "gain_days", "excess_20")
        wf["baseline_gain_days_fail"] = spread(post, "gain_days", "fail")
        wf["baseline_plain_at_support"] = {"excess_20_pp": _per_date_mean(post, "excess_20"),
                                           "fail_pct": _per_date_mean(post, "fail")}
    out["walk_forward"] = wf
    out["buckets_pooled"] = {
        "rally_count": buckets(at, "rally_count", [-0.5, 0.5, 2.5, 4.5, 1e9], ["0", "1-2", "3-4", "5+"]),
        "recovery_rate (>= 2 dips)": buckets(at, "recovery_rate", [-0.01, 0.34, 0.66, 1.0],
                                             ["0-33%", "34-66%", "67-100%"], "dips"),
        "burst_months top20": buckets(at, "top20_up_months", [-0.5, 0.5, 2.5, 5.5, 12.5], ["0", "1-2", "3-5", "6+"]),
        "gain_days": buckets(at, "gain_days", [-0.5, 0.5, 4.5, 14.5, 1e9], ["0", "1-4", "5-14", "15+"]),
    }
    n = sum(1 for v in tests.values() if v.get("t") is not None)
    out["tests_with_t"] = n
    out["expected_abs_t_ge_2_by_chance"] = round(n * 0.05, 1)
    return out


# ------------------------------------------------------------------- the bounce trade
def load_ohlc():
    """The store as wide float64 arrays with the OPEN too (ZL.load reads no open)."""
    import store
    frames = []
    for m in store.months("1d"):
        d = pd.read_parquet(store.part_path(m), columns=["ticker", "date", "open", "high", "low", "close"])
        d["ticker"] = d["ticker"].astype(str)
        frames.append(d)
    df = pd.concat(frames, ignore_index=True)
    dates = np.array(sorted(df["date"].astype(str).unique()))
    ticks = np.array(sorted(df["ticker"].unique()))
    di = np.searchsorted(dates, df["date"].astype(str).to_numpy())
    ti = np.searchsorted(ticks, df["ticker"].to_numpy())
    W = {}
    for c in ("open", "high", "low", "close"):
        a = np.full((len(dates), len(ticks)), np.nan, dtype=np.float32)
        a[di, ti] = df[c].to_numpy(dtype=np.float32)
        W[c] = a
    return dates, ticks, W


def bracket(P: dict, tgt: np.ndarray, stp: np.ndarray, n: int):
    """First exit of a target/stop bracket entered at the open of row 0 of the paths.
    A gap through either side fills at that open; a day reaching both is the STOP;
    otherwise the last close within n sessions. -> (exit price, kind: 1 target,
    -1 stop, 0 time, sessions held)."""
    O, H, L, C = (P[c][:n] for c in ("open", "high", "low", "close"))
    cols = np.arange(O.shape[1])
    with np.errstate(invalid="ignore"):
        gap_dn = np.zeros(O.shape, dtype=bool)
        gap_up = np.zeros(O.shape, dtype=bool)
        gap_dn[1:] = O[1:] <= stp
        gap_up[1:] = O[1:] >= tgt
        hit_s, hit_t = L <= stp, H >= tgt
    anyx = gap_dn | gap_up | hit_s | hit_t
    has = anyx.any(axis=0)
    d = np.where(has, anyx.argmax(axis=0), 0)
    gd, gu, hs = gap_dn[d, cols], gap_up[d, cols], hit_s[d, cols]
    px = np.where(gd | gu, O[d, cols], np.where(hs, stp, tgt))
    kind = np.where(gd | (hs & ~gu), -1, 1)
    fin = np.isfinite(C)
    last = np.where(fin.any(axis=0), C.shape[0] - 1 - np.argmax(fin[::-1], axis=0), 0)
    px = np.where(has, px, C[last, cols])
    kind = np.where(has, kind, 0)
    held = np.where(has, d + 1, last + 1)
    return px, kind, held


def trades(verbose=True) -> pd.DataFrame:
    """Every saved name-date traded with every bracket; writes TRADES_EVENTS."""
    t0 = time.time()
    ev = pd.read_parquet(OUT_EVENTS)
    dates, ticks, W = load_ohlc()
    di = {d: i for i, d in enumerate(dates)}
    ti = {t: j for j, t in enumerate(ticks)}
    nmax = max(b[2] for b in BRACKETS.values())
    parts = []
    for D, g in ev.groupby("date"):
        i = di[D]
        js = g.ticker.map(ti).to_numpy()
        P = {c: W[c][i + 1:i + 1 + nmax][:, js].astype(np.float64) for c in ("open", "high", "low", "close")}
        entry = P["open"][0]
        good = np.isfinite(entry) & (entry > 0)
        res = {"date": D, "ticker": g.ticker.to_numpy()}
        for name, (tg, sp, n) in BRACKETS.items():
            if sp == "level":
                stp = g.level.to_numpy(dtype=float) * (1 - LEVEL_STOP)
                ok = good & np.isfinite(stp) & (entry > stp)
            else:
                stp, ok = entry * (1 - sp), good
            px, kind, held = bracket(P, entry * (1 + tg), stp, n)
            res[f"{name}_ret"] = np.where(ok, px / entry - 1.0, np.nan)
            res[f"{name}_win"] = np.where(ok, (kind == 1).astype(float), np.nan)
            res[f"{name}_stop"] = np.where(ok, (kind == -1).astype(float), np.nan)
            res[f"{name}_held"] = np.where(ok, held.astype(float), np.nan)
        parts.append(pd.DataFrame(res))
    tv = ev.merge(pd.concat(parts, ignore_index=True), on=["date", "ticker"], how="left")
    tv.to_parquet(TRADES_EVENTS, index=False)
    if verbose:
        print(f"trades: {len(tv):,} name-dates x {len(BRACKETS)} brackets; {time.time() - t0:.0f}s", flush=True)
    return tv


def list_rule(at: pd.DataFrame) -> pd.DataFrame:
    """The 10-23 list's selection on each date: up to 10 per sector by gain_days."""
    import sectors
    sec = sectors.groups().drop_duplicates("ticker").set_index("ticker")["sector"]
    a = at.assign(sector=at.ticker.map(sec).fillna("Other"))
    a = a.sort_values(["date", "sector", "gain_days", "ticker"], ascending=[True, True, False, True])
    return a.groupby(["date", "sector"], group_keys=False).head(LIST_PER_SECTOR)


def level_value(tv: pd.DataFrame, score: str, outcome: str, gate: str | None = None) -> dict:
    """Hot names (the score's top third among ALL liquid names that date) at support
    minus the same-hot names elsewhere: what the level adds. top = at support."""
    per = []
    for d, g in _subset(tv, score, outcome, gate).groupby("date"):
        x = g[score]
        hot = x > x.quantile(2 / 3)
        if hot.sum() < 2 * MIN_SIDE:
            hot = x >= x.quantile(2 / 3)
        a, b = g[hot & g.at_support], g[hot & ~g.at_support]
        if len(a) < MIN_SIDE or len(b) < MIN_SIDE:
            continue
        ya, yb = a[outcome].astype(float).mean(), b[outcome].astype(float).mean()
        per.append((d, ya - yb, ya, yb, int(hot.sum())))
    return _report(per)


def vs_base(sel: pd.DataFrame, base: pd.DataFrame, outcome: str) -> dict:
    """Per date: the selected names' mean minus the base group's mean."""
    a = sel[sel[outcome].notna()].groupby("date")[outcome].mean()
    b = base[base[outcome].notna()].groupby("date")[outcome].mean()
    j = pd.concat([a.rename("a"), b.rename("b")], axis=1).dropna()
    per = [(d, r.a - r.b, r.a, r.b, 0) for d, r in j.iterrows()]
    return _report(per)


def _trade_row(df: pd.DataFrame, k: str) -> dict:
    return {"trade_pp": _per_date_mean(df, f"{k}_ret"), "win_pct": _per_date_mean(df, f"{k}_win"),
            "stop_pct": _per_date_mean(df, f"{k}_stop"),
            "held_median": float(df[f"{k}_held"].median()) if df[f"{k}_held"].notna().any() else None,
            "trades": int(df[f"{k}_ret"].notna().sum())}


def summarise_trades(tv: pd.DataFrame) -> dict:
    at = tv[tv.at_support]
    lr = list_rule(at)
    out = {"measured_on": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "dates": int(tv.date.nunique()), "trades_at_support": int(at[f"{PRIMARY}_ret"].notna().sum()),
           "brackets": {k: list(v) for k, v in BRACKETS.items()}, "primary": PRIMARY}
    out["groups_per_date_mean"] = {k: {"all_liquid": _trade_row(tv, k), "at_support": _trade_row(at, k),
                                       "list_rule": _trade_row(lr, k)} for k in BRACKETS}
    scores = {"gain_days": (None,), "rally_count": (None,), "recovery_rate": ("dips",),
              "tilt fifth e63": ("A_fifth_w252",), "burst_months top20": ("top20_up_w252",)}
    col = {"gain_days": "gain_days", "rally_count": "rally_count", "recovery_rate": "recovery_rate",
           "tilt fifth e63": "T_fifth_e63", "burst_months top20": "top20_up_months"}
    tests = {}
    # a trade longer than the date spacing overlaps the next date's: no independent t
    for k in [k for k, b in BRACKETS.items() if b[2] <= STEP]:
        for nm, (gate,) in scores.items():
            for o in ("ret", "win") if k == PRIMARY else ("ret",):
                tests[f"{k} {o}: AT hot minus cold, {nm}"] = spread(at, col[nm], f"{k}_{o}", gate)
                tests[f"{k} {o}: hot AT support minus hot ELSEWHERE, {nm}"] = level_value(tv, col[nm], f"{k}_{o}", gate)
            if k == PRIMARY:
                tests[f"{k} ret: AT hot minus cold, {nm}, vol-neutral"] = spread_neutral(at, col[nm], f"{k}_ret", gate)
        for o in ("ret", "win"):
            tests[f"{k} {o}: list rule minus every at-support name"] = vs_base(lr, at, f"{k}_{o}")
            tests[f"{k} {o}: every at-support name minus every liquid name"] = vs_base(at, tv, f"{k}_{o}")
            tests[f"{k} {o}: list rule minus every liquid name"] = vs_base(lr, tv, f"{k}_{o}")
    out["tests"] = tests
    out["buckets_pooled_" + PRIMARY] = {
        nm: _trade_buckets(at, c, e, l, g) for nm, c, e, l, g in (
            ("gain_days", "gain_days", [-0.5, 0.5, 4.5, 14.5, 1e9], ["0", "1-4", "5-14", "15+"], None),
            ("rally_count", "rally_count", [-0.5, 0.5, 2.5, 4.5, 1e9], ["0", "1-2", "3-4", "5+"], None),
            ("recovery_rate (>= 2 dips)", "recovery_rate", [-0.01, 0.34, 0.66, 1.0], ["0-33%", "34-66%", "67-100%"], "dips"))}
    out["buckets_pooled_elsewhere_" + PRIMARY] = {
        "gain_days": _trade_buckets(tv[~tv.at_support], "gain_days", [-0.5, 0.5, 4.5, 14.5, 1e9], ["0", "1-4", "5-14", "15+"], None),
        "rally_count": _trade_buckets(tv[~tv.at_support], "rally_count", [-0.5, 0.5, 2.5, 4.5, 1e9], ["0", "1-2", "3-4", "5+"], None)}
    pre, post = at[at.date < SPLIT], at[at.date >= SPLIT]
    train = {nm: spread(pre, col[nm], f"{PRIMARY}_ret", gate).get("t") for nm, (gate,) in scores.items()}
    best = max((k for k, v in train.items() if v is not None), key=lambda k: train[k], default=None)
    wf = {"train_t": train, "chosen": best}
    if best:
        gate = scores[best][0]
        wf["test_hot_minus_cold"] = spread(post, col[best], f"{PRIMARY}_ret", gate)
        e = _subset(post, col[best], f"{PRIMARY}_ret", gate)
        hot = pd.concat([g[_sides(g[col[best]], MIN_SIDE)[0]] for _, g in e.groupby("date")
                         if len(g) >= 30 and _sides(g[col[best]], MIN_SIDE) is not None])
        wf["test_hot_third"] = _trade_row(hot, PRIMARY)
        wf["test_list_rule"] = _trade_row(lr[lr.date >= SPLIT], PRIMARY)
        wf["test_every_at_support"] = _trade_row(post, PRIMARY)
        wf["test_every_liquid"] = _trade_row(tv[tv.date >= SPLIT], PRIMARY)
    out["walk_forward"] = wf
    n = sum(1 for v in tests.values() if v.get("t") is not None)
    out["tests_with_t"] = n
    out["expected_abs_t_ge_2_by_chance"] = round(n * 0.05, 1)
    return out


def _trade_buckets(df, score, edges, labels, gate):
    e = df[df[score].notna()]
    if gate:
        e = e[e[gate] >= MIN_BURSTS]
    grp = pd.cut(e[score], edges, labels=labels, include_lowest=True)
    out = []
    for lab in labels:
        x = e[grp == lab]
        r = x[f"{PRIMARY}_ret"].dropna()
        out.append({"group": lab, "trades": int(len(r)),
                    "trade_pp": _pp(r.mean()) if len(r) else None,
                    "win_pct": _pp(x[f"{PRIMARY}_win"].mean()) if len(r) else None,
                    "stop_pct": _pp(x[f"{PRIMARY}_stop"].mean()) if len(r) else None})
    return out


def main() -> int:
    if "--step" in sys.argv:
        use_step(int(sys.argv[sys.argv.index("--step") + 1]))
    if "--trades" in sys.argv:
        tv = pd.read_parquet(TRADES_EVENTS) if "--summarise" in sys.argv and TRADES_EVENTS.exists() else trades()
        if "--build-only" in sys.argv:            # results stay unseen until a test's rules are fixed
            print(f"built {TRADES_EVENTS.name}: {len(tv):,} name-dates over {tv.date.nunique()} dates")
            return 0
        s = summarise_trades(tv)
        TRADES_OUT.write_text(json.dumps(s, indent=1, default=str), encoding="utf-8")
        print(json.dumps({k: s[k] for k in ("dates", "trades_at_support", "tests_with_t",
                                            "expected_abs_t_ge_2_by_chance")}))
        return 0
    smoke = "--smoke" in sys.argv
    if "--summarise" in sys.argv and OUT_EVENTS.exists():
        ev = pd.read_parquet(OUT_EVENTS)
    else:
        ev = run(smoke=smoke)
    if STEP < HOLD:
        print(f"step {STEP}: events only ({OUT_EVENTS.name}); the 20/60-session and 40-session-level "
              f"summary needs dates at least {HOLD} sessions apart, so it is not drawn here")
        return 0
    s = summarise(ev)
    if not smoke:
        OUT.write_text(json.dumps(s, indent=1, default=str), encoding="utf-8")
    rows = [{"test": k, **{c: v.get(c) for c in ("dates", "names_per_date", "top", "bottom", "top_minus_bottom",
                                                    "t", "t_before_2022", "t_from_2022")}}
            for k, v in s["tests"].items()]
    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 500)
    print(pd.DataFrame(rows).to_string(index=False))
    print("\nbaseline:", json.dumps(s["baseline_per_date_mean"]))
    print("walk-forward:", json.dumps(s["walk_forward"], default=str))
    print("buckets:", json.dumps(s["buckets_pooled"], default=str))
    print(f"{s['tests_with_t']} tests with a t; ~{s['expected_abs_t_ge_2_by_chance']} expected at |t| >= 2 by chance")
    return 0


if __name__ == "__main__":
    sys.exit(main())
