"""
Swing pivots, horizontal support levels, and touch counting.

Two ideas carry most of the weight here.

1. EVERYTHING IS SCALE-FREE. Pivot detection runs on log price, so `prominence`
   is a percentage rather than dollars, and the threshold is scaled by the
   stock's own ATR. A flat 7% prominence yields ~5 pivots on a 2%-ATR utility and
   ~60 on a 9%-ATR small cap; this universe contains both.

2. A LEVEL MUST NOT COUNT THE CURRENT TEST AS EVIDENCE FOR ITSELF. Without that
   exclusion every fresh 52-week low registers one touch and looks like support,
   and the screen fills with falling knives. See `count_touches`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.signal import find_peaks

import config


@dataclass(frozen=True)
class Pivots:
    maj_pk: np.ndarray
    maj_tr: np.ndarray
    min_pk: np.ndarray
    min_tr: np.ndarray
    prom_major: float
    prom_minor: float
    atr_pct: float


@dataclass(frozen=True)
class TouchStats:
    visits: np.ndarray                 # (k, 2) [start, end] bar indices
    touches_total: int                 # DIAGNOSTIC ONLY -- never gate on this
    touches_prior: int                 # gate on THIS
    touches_pre_run: int
    touches_pre_peak: int
    cur_visit_start: int
    span_days: int
    gap_bars: int
    vol_ratio: float
    touch_mask: np.ndarray
    prior_visits: np.ndarray = field(default_factory=lambda: np.empty((0, 2), int))


def _safe_log(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """log of a strictly positive series, plus the validity mask.

    find_peaks misbehaves silently on -inf/nan, producing garbage prominences
    rather than an error, so non-positive and missing prices are removed and an
    index map back to original positions is kept.
    """
    x = np.asarray(x, dtype=float)
    ok = np.isfinite(x) & (x > 0)
    out = np.full(len(x), np.nan)
    out[ok] = np.log(x[ok])
    return out, ok


def _peaks_on(series: np.ndarray, prominence: float, distance: int,
              wlen: int) -> np.ndarray:
    """find_peaks over a series that may contain NaN, returned in original positions."""
    ok = np.isfinite(series)
    if ok.sum() < 5:
        return np.empty(0, dtype=int)
    pos = np.flatnonzero(ok)
    idx, _props = find_peaks(series[pos], prominence=prominence,
                             distance=distance, wlen=wlen)
    return pos[idx]


def atr_pct_of(atr14: np.ndarray, close: np.ndarray, n: int = 250) -> float:
    """Median ATR as a fraction of price, clipped. Drives prominence scaling."""
    a, c = np.asarray(atr14, float)[-n:], np.asarray(close, float)[-n:]
    m = np.isfinite(a) & np.isfinite(c) & (c > 0)
    if not m.any():
        return float(np.mean(config.ATR_PCT_CLIP))
    v = float(np.median(a[m] / c[m]))
    lo, hi = config.ATR_PCT_CLIP
    return float(np.clip(v, lo, hi))


def find_pivots(high: np.ndarray, low: np.ndarray, atr_pct: float,
                right_edge: int | None = None, cfg=config) -> Pivots:
    """Major and minor peaks/troughs.

    Uses `high` for peaks and `low` for troughs -- never `close` for both --
    because support lines are drawn at wick extremes. RDW's 7.76 bottom and its
    7.77 support line are both low-based.

    `right_edge` drops pivots within CONFIRM_BARS of the end. find_peaks needs
    strictly lower neighbours on BOTH sides, so the last bar can never be a peak
    and a peak flagged at n-2 today can be un-flagged tomorrow. Pivot identity is
    simply not stable near the edge.
    """
    lh, _ = _safe_log(high)
    nll, _ = _safe_log(low)
    nll = -nll

    prom_major = max(cfg.PROM_MAJOR_FLOOR, cfg.PROM_MAJOR_K * atr_pct)
    # Capped: minor pivots feed level clustering, which needs a dense supply.
    # Unbounded volatility scaling starves it on the very names that matter.
    prom_minor = min(max(cfg.PROM_MINOR_FLOOR, cfg.PROM_MINOR_K * atr_pct),
                     cfg.PROM_MINOR_MAX)

    maj_pk = _peaks_on(lh, prom_major, cfg.DIST_MAJOR, cfg.WLEN)
    maj_tr = _peaks_on(nll, prom_major, cfg.DIST_MAJOR, cfg.WLEN)
    min_pk = _peaks_on(lh, prom_minor, cfg.DIST_MINOR, cfg.WLEN)
    min_tr = _peaks_on(nll, prom_minor, cfg.DIST_MINOR, cfg.WLEN)

    if right_edge is not None:
        cut = right_edge - cfg.CONFIRM_BARS
        maj_pk = maj_pk[maj_pk <= cut]
        maj_tr = maj_tr[maj_tr <= cut]
        min_pk = min_pk[min_pk <= cut]
        min_tr = min_tr[min_tr <= cut]

    return Pivots(maj_pk, maj_tr, min_pk, min_tr,
                  float(prom_major), float(prom_minor), float(atr_pct))


def resolve_extreme(series: np.ndarray, i: int, radius: int = 2,
                    mode: str = "max") -> int:
    """Re-resolve a pivot to the actual extreme bar.

    scipy returns the MIDPOINT of a plateau, so `high[p]` can understate the true
    peak and with it `run_x`. Cheap to fix, and it matters: an understated peak
    can push a genuine 2.3x round trip below MIN_RUN_X.
    """
    lo = max(0, i - radius)
    hi = min(len(series), i + radius + 1)
    seg = series[lo:hi]
    if not np.isfinite(seg).any():
        return int(i)
    off = int(np.nanargmax(seg) if mode == "max" else np.nanargmin(seg))
    return lo + off


def level_tolerance(price: float, atr_pct: float, cfg=config) -> float:
    """Cluster half-width in log units.

    Widened for cheap stocks (at $0.80 a flat 2% is a couple of ticks) and for
    volatile ones, but always narrower than the touch band so a level stays a
    line and touches are the band around it.
    """
    tick_frac = 0.01 / max(price, 0.01)
    tol = max(cfg.LEVEL_TOL_LOG, 3.0 * tick_frac, cfg.LEVEL_TOL_ATR_K * atr_pct)
    return float(np.clip(tol, cfg.LEVEL_TOL_MIN, cfg.LEVEL_TOL_MAX))


def cluster_levels(px: np.ndarray, idx: np.ndarray, w: np.ndarray,
                   tol: float, min_pivots: int = 2) -> pd.DataFrame:
    """Group pivot prices into horizontal bands by mode-seeking on log price.

    Chosen over KDE and volume-histogram approaches for three reasons: a KDE
    bandwidth MOVES the estimated level price, which is fatal when the level is
    then compared to `bounce_low` at +/-3% tolerance; a density estimate is biased
    toward where the stock spent the most TIME, which for this pattern is
    mid-decline rather than the base, so levels land systematically too high; and
    neither yields the touch count or calendar span that level scoring needs.

    Mode-seeking with hard +/-tol membership is drift-free by construction -- every
    cluster is exactly tol wide around its seed, so single-linkage chaining
    cannot smear a "level" across 6%.
    """
    px = np.asarray(px, float)
    ok = np.isfinite(px) & (px > 0)
    if ok.sum() < min_pivots:
        return pd.DataFrame(columns=["level", "n_pivots", "w_pivots", "px_lo",
                                     "px_hi", "first_idx", "last_idx"])
    lp_all = np.log(px[ok])
    idx_all = np.asarray(idx)[ok]
    w_all = np.asarray(w, float)[ok]

    order = np.argsort(lp_all, kind="stable")
    lp, bi, ww = lp_all[order], idx_all[order], w_all[order]

    # Weighted neighbour mass around each pivot, via a prefix sum over the
    # sorted array -- O(k log k), no pairwise distances.
    cw = np.r_[0.0, np.cumsum(ww)]
    left = np.searchsorted(lp, lp - tol, side="left")
    right = np.searchsorted(lp, lp + tol, side="right")
    dens = cw[right] - cw[left]

    taken = np.zeros(len(lp), dtype=bool)
    rows = []
    for i in np.argsort(-dens, kind="stable"):
        if taken[i]:
            continue
        m = (~taken) & (np.abs(lp - lp[i]) <= tol)
        if int(m.sum()) < min_pivots:
            continue
        taken |= m
        rows.append({
            # median, not mean: one 6%-deep wick must not drag the line
            "level": float(np.exp(np.median(lp[m]))),
            "n_pivots": int(m.sum()),
            "w_pivots": float(ww[m].sum()),
            "px_lo": float(np.exp(lp[m].min())),
            "px_hi": float(np.exp(lp[m].max())),
            "first_idx": int(bi[m].min()),
            "last_idx": int(bi[m].max()),
        })
    return pd.DataFrame(rows)


def touch_mask(low: np.ndarray, close: np.ndarray, level: float,
               cfg=config) -> np.ndarray:
    """Bars that TESTED `level` (as opposed to breaking it).

    Defined on bars, not pivots: a level gets tested by plenty of bars that never
    register as swing lows.

    The `low >= level*0.97` clause is what distinguishes a touch from a break --
    a bar that crashed straight through is not evidence the level holds. The
    tolerances are asymmetric (2.5% above, 3.0% below) because support is tested
    by overshoot below more often than by respect above.
    """
    return ((low <= level * (1 + cfg.TOUCH_TOL_UP))
            & (low >= level * (1 - cfg.TOUCH_TOL_DN))
            & (close >= level * (1 - cfg.TOUCH_CLOSE_BREAK)))


def visits(tb: np.ndarray, sep: int = config.TOUCH_SEP) -> np.ndarray:
    """Collapse touch bars into distinct visits. Returns (k, 2) [start, end].

    sep=8 bars: long enough that a two-week base-building visit counts once,
    short enough that two genuinely separate tests a month apart both count.
    """
    i = np.flatnonzero(tb)
    if len(i) == 0:
        return np.empty((0, 2), dtype=int)
    starts_at = np.r_[True, np.diff(i) > sep]
    ends_at = np.r_[starts_at[1:], True]
    return np.column_stack([i[starts_at], i[ends_at]])


def count_touches(low: np.ndarray, close: np.ndarray, volume: np.ndarray,
                  dates: np.ndarray, level: float, asof: int,
                  low_i: int, b_lo: int | None, p: int | None,
                  cfg=config) -> TouchStats:
    """Touch statistics for one level, excluding the test in progress.

    THE trap this exists to avoid: if the current visit counts, then every fresh
    52-week low has "one touch" and therefore "support", and the screener becomes
    a falling-knife generator.

    The current visit is defined by the BOUNCE LOW, not by proximity to `asof`.
    Proximity fails on exactly the case that matters: a bottom 15 bars back that
    has since bounced clear of the band leaves no touch bar near the right edge,
    so an `asof`-based test would happily count the bottom itself as prior
    evidence. Anchoring on `low_i` -- and excluding everything from that visit
    onward -- is correct regardless of how far the bounce has travelled.

    Note that a genuine double bottom (bottomed, bounced three weeks, fell back,
    bottomed again) legitimately contributes a PRIOR visit that postdates the
    peak. That should count, and it does: it lands in `touches_prior` but not in
    `touches_pre_run`. Keeping the two counters separate means they can be
    weighted differently instead of one being sacrificed.
    """
    tb = touch_mask(low, close, level, cfg)
    tb[asof + 1:] = False
    v = visits(tb, cfg.TOUCH_SEP)

    if len(v) == 0:
        return TouchStats(v, 0, 0, 0, 0, asof + 1, 0, 10 ** 6, 0.0, tb)

    reaches_current = v[:, 1] >= (low_i - cfg.TOUCH_SEP)
    if reaches_current.any():
        cur_start = int(v[reaches_current, 0].min())
    else:
        cur_start = int(low_i)

    prior = v[v[:, 1] < cur_start]
    touches_prior = len(prior)

    if touches_prior:
        first_i, last_i = int(prior[:, 0].min()), int(prior[:, 1].max())
        try:
            span_days = int((pd.Timestamp(dates[last_i])
                             - pd.Timestamp(dates[first_i])).days)
        except (TypeError, ValueError):
            span_days = last_i - first_i          # fall back to bars
        gap_bars = asof - last_i
    else:
        span_days, gap_bars = 0, 10 ** 6

    pre_run = int((prior[:, 0] < b_lo).sum()) if (touches_prior and b_lo is not None) else 0
    pre_peak = int((prior[:, 0] < p).sum()) if (touches_prior and p is not None) else 0

    # Volume share transacted at the level vs bar share there: >1 means
    # above-average size changed hands here, i.e. real supply/demand memory.
    n = max(asof + 1, 1)
    vol = np.asarray(volume, float)[:asof + 1]
    tot = float(np.nansum(vol))
    at = float(np.nansum(vol[tb[:asof + 1]]))
    bar_share = float(tb[:asof + 1].sum()) / n
    vol_ratio = (at / tot) / bar_share if tot > 0 and bar_share > 0 else 0.0

    return TouchStats(v, len(v), touches_prior, pre_run, pre_peak, cur_start,
                      span_days, gap_bars, vol_ratio, tb, prior)


def score_level(ts: TouchStats, n_bars: int, cfg=config) -> tuple[float, dict]:
    """Level quality Q in [0, 1], with its components."""
    q_touch = min(ts.touches_prior, 5) / 5.0
    q_pre = 1.0 if ts.touches_pre_run >= 1 else 0.0
    # Span in CALENDAR days on purpose: "this level has held eight months" is a
    # calendar claim. Recency below is in BARS, because halted days are missing
    # rows and 168 bars is not eight months.
    q_span = min(ts.span_days / 365.0, 1.0)
    # Penalises staleness rather than rewarding recency: for this pattern the
    # touches SHOULD be old (pre-run), so a recency reward would be backwards.
    # This only kills levels whose sole evidence is more than two years old.
    q_fresh = 1.0 - min(ts.gap_bars / cfg.STALE_BARS, 1.0)
    q_vol = min(ts.vol_ratio / 3.0, 1.0)

    parts = {"q_touch": q_touch, "q_pre_run": q_pre, "q_span": q_span,
             "q_fresh": q_fresh, "q_vol": q_vol}
    Q = (0.32 * q_touch + 0.26 * q_pre + 0.16 * q_span
         + 0.10 * q_fresh + 0.16 * q_vol)
    return float(np.clip(Q, 0.0, 1.0)), parts


def support_broken(close: np.ndarray, level: float, start: int,
                   asof: int, cfg=config) -> bool:
    """Has the level failed on a closing basis since `start`?

    Two consecutive closes below level*0.98, OR one close below level*0.94.
    Deliberately not a single-bar rule: one-bar undercuts that close back above
    are exactly the stop-run flushes that precede the best bounces, so rejecting
    on them would throw away the setups this screener exists to find.
    """
    seg = np.asarray(close, float)[max(start, 0):asof + 1]
    if seg.size == 0:
        return False
    if np.nanmin(seg) < level * (1 - cfg.BREAK_HARD):
        return True
    below = (seg < level * (1 - cfg.BREAK_TOL)).astype(int)
    k = cfg.BREAK_CONSEC
    if below.size < k:
        return False
    return bool(np.convolve(below, np.ones(k, dtype=int), mode="valid").max() >= k)


def topology(high: np.ndarray, maj_pk: np.ndarray, p: int, b0: int,
             cfg=config) -> dict:
    """Describe the peak structure: single, double, stair-step, complex.

    Descriptive metadata, never a gate -- one-peak and multi-peak setups are both
    wanted. It goes in the output so the outcome log can later tell you whether
    shape predicts anything.
    """
    hi_p = float(high[p])
    win = maj_pk[(maj_pk >= b0) & (maj_pk <= p + cfg.MIN_DECLINE_BARS)]
    if len(win) == 0:
        return {"n_major": 1, "n_shoulder": 1, "shape": "SINGLE"}

    h = np.asarray(high, float)[win]
    n_major = int((h >= cfg.DOMINANCE_FRAC * hi_p).sum())
    n_shoulder = int((h >= cfg.SHOULDER_FRAC * hi_p).sum())
    stair = n_major >= 3 and bool(np.all(np.diff(h) > 0))

    shape = ("SINGLE" if n_major <= 1 else
             "DOUBLE" if n_major == 2 else
             "STAIR_STEP" if stair else "COMPLEX")
    return {"n_major": max(n_major, 1), "n_shoulder": max(n_shoulder, 1),
            "shape": shape}
