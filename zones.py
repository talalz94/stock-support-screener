#!/usr/bin/env python
"""Horizontal support zones for ANY stock, and what price did at them before.

The bounce screen answers "is this the pattern?". This answers a different and
looser question: "is this stock at a level that has mattered, and how hard did
it bounce from there before?". A stock can be sitting on a nine-touch shelf and
be invisible to the bounce screen because there was no parabolic run into it --
AAON on 2026-08-27 stopped at RUN_TOO_SMALL, before the level code ever ran --
or because the level it found was not the pre-run base, which is what discarded
AMSC's nine-touch, Q=0.836 level at stage 4.

NOTHING HERE REIMPLEMENTS THE LEVEL MATH. Pivot finding, clustering, tolerance
and touch detection are `levels.py` exactly as the screen uses them, so a zone
found here is the same zone the screen would find. Verified on DXYZ: this module
and the stored screen output agree on 20.93, against a line the user drew by
hand at 20.64.

Two things ARE new:

  * it runs without run context. `levels.count_touches` takes `b_lo` and `p` as
    `int | None`, so the touch primitives were always usable standalone; only
    the screen's gates needed a run.
  * BOUNCE MAGNITUDE per visit, which nothing measured before. That is the
    ranking input: a level price merely touched is not a level price bounced
    from.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config
import levels as lv
import pattern as pt

# Provisional, and deliberately not one threshold. MEASURED against the levels
# a human drew on 2026-08-27: AAON +9.4% above its line, AMSC +15.4%, and both
# were called "at a major zone" -- while the screen's entry tolerance is ~2%.
# One number cannot serve both readings, so the band is reported and the caller
# decides. These edges are guesses until the hit-rate-versus-distance study
# replaces them; they are constants here so that study has something to move.
BAND_AT = 0.025
BAND_NEAR = 0.06
BAND_APPROACHING = 0.16

HORIZON = 40                  # bars after a touch to look for the rally
MIN_TOUCHES = 2
MAX_LEVELS = 12               # per ticker, nearest-first


def _episodes(close: np.ndarray, vis: np.ndarray, level: float,
              horizon: int = HORIZON) -> list[dict]:
    """Distinct rallies from `level`. One entry per EPISODE, not per touch.

    THE TRAP THIS EXISTS FOR. Touches 8+ bars apart are separate visits, but
    their forward windows overlap, so the same rally gets counted once per
    touch. Measured on AMSC: 2024-06-17 and 2024-07-05 both scored +29.9%,
    2024-10-17 and 2024-11-01 both +47.4%, 2025-01-13 and 2025-02-05 both
    +37.2% -- three rallies reported as six. Nine "bounces" were really five or
    six, and any ranking built on that count would systematically favour stocks
    whose touches happen to cluster.

    A visit starting inside the previous episode's forward window is therefore
    the SAME episode, anchored on its first touch.

    Only episodes with a complete forward window are returned. A touch 10 bars
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
        if s <= covered_to:                 # same rally as the previous episode
            continue
        stop = e + 1 + horizon
        covered_to = e + horizon
        if stop > n:                        # incomplete window -- not evidence
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
    """Every support zone below price for one stock, with its bounce history.

    `bars` is a single ticker's daily frame with date/open/high/low/close/volume.
    """
    b = bars.sort_values("date").reset_index(drop=True)
    if asof:
        b = b[b["date"].astype(str) <= str(asof)]
    if len(b) < cfg.IND_WARMUP + 60:
        return pd.DataFrame()

    h = b["high"].to_numpy(dtype=float)
    l = b["low"].to_numpy(dtype=float)
    c = b["close"].to_numpy(dtype=float)
    dates = b["date"].astype(str).to_numpy()
    price = float(c[-1])
    a = len(c) - 1

    # `pattern.atr` directly, NOT `indicators_for`: the latter also computes
    # wavetrend, squeeze and a colored MA, none of which a zone needs, and this
    # runs over the whole universe rather than the ~679 that reach the screen's
    # pattern math.
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
        if L <= 0 or L > price:             # support only: at or below price
            continue
        dist = price / L - 1.0
        if dist > BAND_APPROACHING:
            continue
        tb = lv.touch_mask(l, c, L, cfg)
        vis = lv.visits(tb, cfg.TOUCH_SEP)
        if len(vis) < MIN_TOUCHES:
            continue
        eps = _episodes(c, vis, L)
        r = np.array([e["rally"] for e in eps], dtype=float)
        first, last = int(vis[0][0]), int(vis[-1][1])
        rows.append({
            "ticker": ticker, "asof": dates[-1], "price": price,
            "level": L, "dist_pct": dist,
            "band": ("AT" if dist <= BAND_AT else
                     "NEAR" if dist <= BAND_NEAR else "APPROACHING"),
            "touches": int(len(vis)),
            "bounce_n": int(len(r)),
            "bounce_median": float(np.median(r)) if len(r) else np.nan,
            "bounce_max": float(r.max()) if len(r) else np.nan,
            "bounce_min": float(r.min()) if len(r) else np.nan,
            # Share of episodes that rallied 15%+. Reported beside the median so
            # a level that bounced huge once is distinguishable from one that
            # bounced decently every time.
            "bounce_hit": float((r >= 0.15).mean()) if len(r) else np.nan,
            "span_days": int((pd.Timestamp(dates[last])
                              - pd.Timestamp(dates[first])).days),
            "last_touch": dates[last],
            "bars_since_touch": int(a - last),
        })
        if len(rows) >= MAX_LEVELS:
            break
    return pd.DataFrame(rows)


def selftest() -> int:
    """`python zones.py --selftest`. Checks the episode logic, which is the only
    genuinely new arithmetic here."""
    # A flat series with ONE spike to 150 at bar 20. Deliberately a spike and
    # not a ramp: the first draft ramped to 150 by bar 49, which sits outside
    # the 40-bar window from bar 5, so the correct answer was 0.449 and the
    # assertion expecting 0.50 was the thing that was wrong. A fixture whose
    # expected value needs arithmetic to defend is a bad fixture.
    c = np.full(210, 100.0)
    c[20] = 150.0

    # two touches 12 bars apart -> ONE episode, because their windows overlap
    vis = np.array([[5, 5], [17, 17]])
    eps = _episodes(c, vis, 100.0, horizon=40)
    assert len(eps) == 1, f"overlapping visits must merge, got {len(eps)}"

    # far apart -> two episodes, and the second sees no spike
    vis = np.array([[5, 5], [100, 100]])
    eps = _episodes(c, vis, 100.0, horizon=40)
    assert len(eps) == 2, f"separated visits must both count, got {len(eps)}"
    assert abs(eps[0]["rally"] - 0.5) < 1e-9, "first episode should catch the spike"
    assert abs(eps[1]["rally"]) < 1e-9, "second episode should see a flat series"

    # a touch too close to the right edge has no complete window -> excluded
    vis = np.array([[len(c) - 5, len(c) - 5]])
    assert _episodes(c, vis, 100.0, horizon=40) == [], "incomplete window counted"

    # the rally is measured against the LEVEL, not the touch bar
    vis = np.array([[5, 5]])
    e = _episodes(c, vis, 100.0, horizon=40)[0]
    assert abs(e["rally"] - 0.5) < 0.02, f"rally {e['rally']:.3f} != ~0.50"

    print("zones selftest OK (episode merge, separation, right-edge, magnitude)")
    return 0


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--ticker", default=None)
    ap.add_argument("--asof", default=None)
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.ticker:
        ap.error("give --ticker or --selftest")

    import calendar_us
    import dataset
    asof = a.asof or calendar_us.last_closed_session()
    start = (pd.Timestamp(asof) - pd.DateOffset(years=6)).strftime("%Y-%m-%d")
    d = dataset.panel([a.ticker], "1d", start=start, end=asof)
    b = d[a.ticker] if isinstance(d, dict) else d
    z = zones_for(a.ticker, b, asof)
    if z.empty:
        print(f"{a.ticker}: no zones below price")
        return 0
    show = z.copy()
    for col in ("dist_pct", "bounce_median", "bounce_max", "bounce_min",
                "bounce_hit"):
        show[col] = (show[col] * 100).round(1)
    print(show[["level", "dist_pct", "band", "touches", "bounce_n",
                "bounce_median", "bounce_max", "bounce_hit", "span_days",
                "last_touch"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
