#!/usr/bin/env python
"""Horizontal support zones for ANY stock, and what price did at them before.

The bounce screen answers "is this the pattern?". This answers a looser
question: "is this stock at a level that has mattered, and what happened there
before?". A stock can sit on a nine-touch shelf and be invisible to the bounce
screen because there was no parabolic run into it -- AAON on 2026-08-27 stopped
at RUN_TOO_SMALL, before the level code ever ran -- or because the level found
was not the pre-run base, which is what discarded AMSC's nine-touch, Q=0.836
level at stage 4.

NOTHING HERE REIMPLEMENTS THE LEVEL MATH. Pivots, clustering, tolerance and
touch detection are `levels.py` exactly as the screen uses them, so a zone found
here is the zone the screen would find. Verified on DXYZ: this module and the
stored screen output agree on 20.93 against a line drawn by hand at 20.64.

WHAT THE COLUMNS ARE WORTH -- MEASURED, NOT ASSUMED
===================================================
325,061 episodes / 3,277 tickers (phase 1) and 37,854 point-in-time
observations over 12 non-overlapping dates (phase 2). Both are printed on the
page beside the columns they judge, because a filter that implies an edge it
does not have is worse than no filter.

  TOUCH COUNT DOES NOT PREDICT A BIGGER BOUNCE. It predicts a SMALLER one,
  monotonically: 0 prior touches -> +17.2% median 40-bar rally, 8+ -> +12.8%.
  Spearman -0.0855. It survives inside every volatility quartile, so it is not
  a volatility artifact. Filtering on touches for upside actively hurts:
  AT+8 touches measured t=+1.61 against plain AT at t=+2.59.

  TOUCH COUNT DOES PREDICT A SMALLER DRAWDOWN, also monotonically: 0 touches
  -> -9.0% median, 8+ -> -6.0%, and the share breaking more than 10% falls from
  46.6% to 35.6%. A well-tested level is a better STOP, not a bigger target.
  That is why `dd_median` and `dd_break_rate` exist beside the bounce columns.

  BOUNCE SIZE IS MOSTLY VOLATILITY. Median 40-bar rally by volatility quartile:
  10.8% / 15.0% / 18.7% / 24.7% -- a ~14pp spread against touch count's ~3pp.
  Ranking on raw `bounce_median` is an ATR ranking wearing a disguise, so
  `bounce_med_atr` divides it out and is the column to sort on.

  THE STRONGEST EFFECT IS ABSENCE, NOT PRESENCE. Names with NO zone within 16%
  underperformed the same-day pool by -2.57pp over 40 bars (t=-4.17), roughly
  six times the size of the positive signal (+0.42pp, t=+4.20). Hence
  `no_support`. CAVEAT KEPT IN VIEW: "no support within 16%" structurally means
  the stock has run far above its last consolidation, so this may be
  re-deriving "extended stocks fall back" by an expensive route. Untested.
"""
from __future__ import annotations

import argparse
import sys
import time

import numpy as np
import pandas as pd

import config
import levels as lv
import pattern as pt

# Provisional, and deliberately not one threshold. MEASURED against levels drawn
# by hand on 2026-08-27: AAON sat +9.4% above its line and AMSC +15.4%, and both
# were called "at a major zone", while the screen's entry tolerance is ~2%. An
# order of magnitude apart, so the band is reported and the caller decides.
# Phase 2 found the edge does NOT decay cleanly with distance (0-1.5% t=+2.69,
# 1.5-3% t=+0.60, 3-5% t=+2.73) -- that is noise, not a curve, so these edges
# stay round numbers rather than pretending to be fitted.
BAND_AT = 0.025
BAND_NEAR = 0.06
BAND_APPROACHING = 0.16

HORIZON = 40                  # bars after a touch; 20/40/60 agreed on direction
MIN_TOUCHES = 2
MAX_LEVELS = 8                # per ticker, nearest first
MIN_BARS = config.IND_WARMUP + 60

EVIDENCE = {
    "touches": ("more touches -> SMALLER bounces (-0.0855 rank corr) but "
                "SMALLER drawdowns (-9.0% -> -6.0%). A stop, not a target."),
    "bounce_median": ("mostly volatility: 10.8%/15.0%/18.7%/24.7% by ATR "
                      "quartile. Sort on bounce_med_atr instead."),
    "bounce_med_atr": "volatility-adjusted rally. The comparable one.",
    "dd_median": "measured: 8+ touches hold better. -6.0% vs -9.0%.",
    "no_support": ("no zone within 16%: -2.57pp vs the same-day pool over 40 "
                   "bars, t=-4.17. The strongest effect in the study."),
    "band": "AT +0.46pp t=+2.59 | any zone +0.42pp t=+4.20. Real but small.",
}


def _episodes(close: np.ndarray, vis: np.ndarray, level: float,
              horizon: int = HORIZON) -> list[dict]:
    """Distinct rallies from `level`. One entry per EPISODE, not per touch.

    THE TRAP THIS EXISTS FOR. Touches 8+ bars apart are separate visits, but
    their forward windows overlap, so one rally gets counted once per touch.
    Measured on AMSC: 2024-06-17 and 2024-07-05 both scored +29.9%, 2024-10-17
    and 2024-11-01 both +47.4%, 2025-01-13 and 2025-02-05 both +37.2%. Nine
    "bounces" were five or six, and a ranking built on that count would quietly
    favour stocks whose touches happen to cluster.

    A visit starting inside the previous episode's window is the SAME episode.

    Only episodes with a COMPLETE forward window are returned. A touch ten bars
    from the right edge has not had its chance to rally, and scoring it as a
    weak bounce would punish the most recent -- and most relevant -- test.
    """
    out: list[dict] = []
    if level <= 0 or len(vis) == 0:
        return out
    n = len(close)
    covered_to = -(10 ** 9)
    for s, e in vis:
        s, e = int(s), int(e)
        if s <= covered_to:
            continue
        stop = e + 1 + horizon
        covered_to = e + horizon
        if stop > n:
            continue
        w = close[e + 1:stop]
        if len(w) == 0:
            continue
        out.append({"start": s, "end": e,
                    "rally": float(w.max() / level - 1.0),
                    "worst": float(w.min() / level - 1.0)})
    return out


def zones_for(ticker: str, bars: pd.DataFrame, asof: str | None = None,
              cfg=config) -> pd.DataFrame:
    """Every support zone below price for one stock, with its history."""
    if bars is None or len(bars) == 0:
        return pd.DataFrame()
    b = bars.sort_values("date").reset_index(drop=True)
    if asof:
        b = b[b["date"].astype(str) <= str(asof)]
    if len(b) < MIN_BARS:
        return pd.DataFrame()

    h = b["high"].to_numpy(dtype=float)
    l = b["low"].to_numpy(dtype=float)
    c = b["close"].to_numpy(dtype=float)
    dates = b["date"].astype(str).to_numpy()
    price = float(c[-1])
    a = len(c) - 1
    if not np.isfinite(price) or price <= 0:
        return pd.DataFrame()

    # `pattern.atr` directly, NOT `indicators_for`: the latter also computes
    # wavetrend, squeeze and a colored MA, none of which a zone needs, and this
    # runs over the whole universe rather than the ~679 the screen reaches.
    atr14 = pt.atr(b, 14).to_numpy(dtype=float)
    atr_pct = lv.atr_pct_of(atr14, c)

    piv = lv.find_pivots(h, l, atr_pct, right_edge=a, cfg=cfg)
    px = np.concatenate([l[piv.min_tr], h[piv.min_pk]])
    idx = np.concatenate([piv.min_tr, piv.min_pk])
    wt = np.concatenate([np.ones(len(piv.min_tr)),
                         np.full(len(piv.min_pk), cfg.PEAK_PIVOT_WEIGHT)])
    if len(px) == 0:
        return pd.DataFrame()

    tol = lv.level_tolerance(price, atr_pct, cfg)
    cand = lv.cluster_levels(px, idx, wt, tol, cfg.MIN_PIVOTS_PER_LEVEL)
    if cand.empty:
        return pd.DataFrame()

    rows = []
    for L in sorted(cand["level"].astype(float), reverse=True):
        if not np.isfinite(L) or L <= 0 or L > price:
            continue
        dist = price / L - 1.0
        if dist > BAND_APPROACHING:
            continue
        vis = lv.visits(lv.touch_mask(l, c, L, cfg), cfg.TOUCH_SEP)
        if len(vis) < MIN_TOUCHES:
            continue
        eps = _episodes(c, vis, L)
        r = np.array([e["rally"] for e in eps], dtype=float)
        dd = np.array([e["worst"] for e in eps], dtype=float)
        first, last = int(vis[0][0]), int(vis[-1][1])
        med = float(np.median(r)) if len(r) else np.nan
        rows.append({
            "ticker": ticker, "asof": dates[-1], "price": price,
            "level": L, "dist_pct": dist,
            "band": ("AT" if dist <= BAND_AT else
                     "NEAR" if dist <= BAND_NEAR else "APPROACHING"),
            "touches": int(len(vis)),
            "bounce_n": int(len(r)),
            "bounce_median": med,
            "bounce_max": float(r.max()) if len(r) else np.nan,
            # Volatility-adjusted, because raw bounce size is ~5x more explained
            # by ATR than by anything about the level.
            "bounce_med_atr": (med / atr_pct if len(r) and atr_pct > 0
                               else np.nan),
            "bounce_hit": float((r >= 0.15).mean()) if len(r) else np.nan,
            # The risk half, and the half the evidence actually supports.
            "dd_median": float(np.median(dd)) if len(dd) else np.nan,
            "dd_break_rate": float((dd <= -0.10).mean()) if len(dd) else np.nan,
            "atr_pct": atr_pct,
            "span_days": int((pd.Timestamp(dates[last])
                              - pd.Timestamp(dates[first])).days),
            "last_touch": dates[last],
            "bars_since_touch": int(a - last),
        })
        if len(rows) >= MAX_LEVELS:
            break
    return pd.DataFrame(rows)


def scan(tickers: list[str] | None = None, asof: str | None = None,
         chunk: int = 250, verbose: bool = True) -> pd.DataFrame:
    """Every zone for every tradeable name. One row per (ticker, level).

    Names with NO zone within `BAND_APPROACHING` get a single row with
    `no_support` set and every zone column null -- they are the finding, not an
    omission, and dropping them would hide the strongest measured effect.
    """
    import bars as B
    import calendar_us
    import dataset

    asof = asof or calendar_us.last_closed_session()
    if tickers is None:
        ps = B.load_panel_stats()
        ps = ps[(ps["n_bars"] >= 400)
                & (ps["last_close"] >= config.MIN_PRICE)
                & (ps["dollar_vol_20"] >= config.MIN_DOLLAR_VOL)
                & (ps["last_date"] >= asof)]
        tickers = ps["ticker"].astype(str).tolist()

    start = (pd.Timestamp(asof) - pd.DateOffset(years=6)).strftime("%Y-%m-%d")
    out, empty, t0 = [], [], time.time()
    for i in range(0, len(tickers), chunk):
        part = tickers[i:i + chunk]
        try:
            d = dataset.panel(part, "1d", start=start, end=asof)
        except Exception as exc:                                 # noqa: BLE001
            if verbose:
                print(f"  ! chunk {i} failed to load: {repr(exc)[:80]}")
            continue
        for t in part:
            b = d.get(t) if isinstance(d, dict) else None
            z = zones_for(t, b, asof) if b is not None else pd.DataFrame()
            if z.empty:
                empty.append(t)
            else:
                out.append(z)
        if verbose:
            print(f"  zones {min(i + chunk, len(tickers)):>5}/{len(tickers)}  "
                  f"{time.time() - t0:5.0f}s", flush=True)

    df = pd.concat(out, ignore_index=True) if out else pd.DataFrame()
    if not df.empty:
        df["no_support"] = False
    if empty:
        blank = pd.DataFrame({"ticker": empty})
        blank["asof"] = asof
        blank["no_support"] = True
        for c in df.columns if not df.empty else []:
            if c not in blank.columns:
                blank[c] = np.nan
        df = pd.concat([df, blank], ignore_index=True) if not df.empty else blank
    return df


def selftest() -> int:
    """`python zones.py --selftest`."""
    # --- episode merging, the only genuinely new arithmetic ---
    # A flat series with ONE spike at bar 20. Deliberately a spike, not a ramp:
    # the first draft ramped to its peak just outside the 40-bar window from bar
    # 5, so the right answer was 0.449 and the assertion expecting 0.50 was the
    # thing that was wrong. A fixture whose expected value needs arithmetic to
    # defend is a bad fixture.
    c = np.full(210, 100.0)
    c[20] = 150.0

    vis = np.array([[5, 5], [17, 17]])           # windows overlap -> one episode
    assert len(_episodes(c, vis, 100.0, 40)) == 1, "overlapping visits must merge"

    vis = np.array([[5, 5], [100, 100]])         # far apart -> two
    eps = _episodes(c, vis, 100.0, 40)
    assert len(eps) == 2, "separated visits must both count"
    assert abs(eps[0]["rally"] - 0.5) < 1e-9, "first episode must catch the spike"
    assert abs(eps[1]["rally"]) < 1e-9, "second episode sees a flat series"

    vis = np.array([[len(c) - 5, len(c) - 5]])   # no complete window -> excluded
    assert _episodes(c, vis, 100.0, 40) == [], "incomplete window must not count"

    # drawdown is recorded, and is negative on a series that dips
    c2 = np.full(120, 100.0); c2[30] = 80.0
    e2 = _episodes(c2, np.array([[5, 5]]), 100.0, 40)[0]
    assert abs(e2["worst"] + 0.20) < 1e-9, f"worst {e2['worst']} != -0.20"

    # --- guards: nothing may fabricate a row from nothing ---
    assert zones_for("X", pd.DataFrame()).empty, "empty bars must give no zones"
    short = pd.DataFrame({"date": ["2026-01-0%d" % (i + 1) for i in range(5)],
                          "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0})
    assert zones_for("X", short).empty, "too-short history must give no zones"

    print("zones selftest OK (merge, separation, right-edge, magnitude, "
          "drawdown, empty and short input)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Support zones and their history.")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--ticker", default=None)
    ap.add_argument("--scan", action="store_true", help="whole universe")
    ap.add_argument("--asof", default=None)
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    import calendar_us
    import dataset
    asof = a.asof or calendar_us.last_closed_session()

    if a.scan:
        df = scan(asof=asof)
        print(f"\n{len(df):,} rows, {df.ticker.nunique():,} tickers, "
              f"{int(df.no_support.sum()):,} with no support within "
              f"{BAND_APPROACHING:.0%}")
        return 0

    if not a.ticker:
        ap.error("give --ticker, --scan or --selftest")
    start = (pd.Timestamp(asof) - pd.DateOffset(years=6)).strftime("%Y-%m-%d")
    d = dataset.panel([a.ticker], "1d", start=start, end=asof)
    b = d.get(a.ticker) if isinstance(d, dict) else d
    z = zones_for(a.ticker, b, asof)
    if z.empty:
        print(f"{a.ticker}: NO SUPPORT within {BAND_APPROACHING:.0%} below price")
        return 0
    s = z.copy()
    for c in ("dist_pct", "bounce_median", "bounce_max", "bounce_hit",
              "dd_median", "dd_break_rate"):
        s[c] = (s[c] * 100).round(1)
    s["bounce_med_atr"] = s["bounce_med_atr"].round(2)
    print(s[["level", "dist_pct", "band", "touches", "bounce_n",
             "bounce_median", "bounce_med_atr", "dd_median", "dd_break_rate",
             "span_days", "last_touch"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
