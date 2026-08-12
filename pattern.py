"""
The pattern itself: parabolic run -> full retrace to the base -> base holds -> bounce.

Reuses `indicators.py` from the sibling project unmodified (it is Pine-validated,
byte-identical copy) for WaveTrend, Squeeze, colored MA and the EMA family, and
builds ATR/RSI on top of its existing Wilder `rma` rather than reimplementing it.

`ma_shift` is deliberately NOT used. Its rolling(1000, min_periods=250).quantile()
gives the same bar a different value depending on how much history is in the
window, which makes live and replayed values disagree -- the least
backtest-stable thing available. WaveTrend, Squeeze, colored_ma and the rma-based
ATR/RSI all converge to <1e-6 of their full-history values within ~250 bars, so a
FIXED IND_WARMUP makes replay exact.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

import config
import levels as lv
from indicators import colored_ma, ema, rma, sma, squeeze, wavetrend


# ============================================================ primitives
def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"],
                      (df["high"] - pc).abs(),
                      (df["low"] - pc).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return rma(true_range(df), n)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = rma(d.clip(lower=0), n)
    dn = rma((-d).clip(lower=0), n)
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def indicators_for(df: pd.DataFrame) -> dict[str, np.ndarray]:
    """Every indicator series the screen needs, as positional numpy arrays."""
    wt = wavetrend(df)
    sq = squeeze(df)
    cm = colored_ma(df, 8, "EMA")
    out = {
        "atr14": atr(df, 14).to_numpy(dtype=float),
        "rsi14": rsi(df["close"], 14).to_numpy(dtype=float),
        "ema8": cm["ma8"].to_numpy(dtype=float),
        "ema8_up": cm["ma8_up"].to_numpy(dtype=bool),
        "ema8_turn_up": cm["ma8_turn_up"].to_numpy(dtype=bool),
        "wt1": wt["wt1"].to_numpy(dtype=float),
        "wt2": wt["wt2"].to_numpy(dtype=float),
        "wt_cross_up": wt["wt_cross_up"].to_numpy(dtype=bool),
        "mom": sq["mom"].to_numpy(dtype=float),
        "mom_rising": sq["mom_rising"].to_numpy(dtype=bool),
        "sqz_on": sq["sqz_on"].to_numpy(dtype=bool),
        "sqz_release": sq["sqz_release"].to_numpy(dtype=bool),
    }
    return out


# ============================================================ structures
@dataclass(frozen=True)
class BaseZone:
    b0: int
    b_lo: int
    base_lo: float
    base_md: float
    base_hi: float
    base_center: float
    base_width: float
    atr_pct_base: float


@dataclass(frozen=True)
class RunStats:
    run_x: float
    run_z: float
    run_bars: int
    run_dd_max: float


# ============================================================ peak
def dominant_peak(high: np.ndarray, maj_pk: np.ndarray, asof: int,
                  cfg=config) -> int | None:
    """The peak this setup retraced from.

    Must be a LOCAL dominant peak, never the window argmax. Verified on RDW: the
    2-year window max is 26.66 from 2025-01-31, 376 sessions back and completely
    irrelevant, while the real peak is 26.64 on 2026-05-28. An argmax would pick
    the decoy and every downstream metric would be wrong.

    Ranked by raw `high`, not by prominence: prominence is capped by `wlen`, so it
    is not a proxy for "height above the base" -- that is `high[p] / base_lo`.
    """
    lo = asof - cfg.MAX_DECLINE_BARS
    hi = asof - cfg.MIN_DECLINE_BARS
    if hi <= max(lo, 0):
        return None

    cand = maj_pk[(maj_pk >= max(lo, 0)) & (maj_pk <= hi)]
    if len(cand) == 0:
        # A plateau or an edge case can leave the true peak off the pivot list.
        seg = np.asarray(high, float)[max(lo, 0):hi + 1]
        if not np.isfinite(seg).any():
            return None
        p = max(lo, 0) + int(np.nanargmax(seg))
    else:
        p = int(cand[np.nanargmax(np.asarray(high, float)[cand])])

    return lv.resolve_extreme(np.asarray(high, float), p, radius=2, mode="max")


# ============================================================ run start
def find_run_start(high: np.ndarray, low: np.ndarray, p: int,
                   min_tr: np.ndarray, cfg=config) -> tuple[int | None, int | None, float]:
    """Where the run began. Returns (b0, b_lo, max_drawdown_within_run).

    The obvious one-liner -- "the last bar before p with low <= high[p]/MIN_RUN_X"
    -- is WRONG, and wrong in a way that looks right. A parabolic move passes
    through peak/2.2 on the way UP, so that bar lands mid-parabola: for RDW,
    26.64/2.2 = 12.1, roughly ten bars before the peak. Both `base_lo` and
    `run_x` would then be computed off a point inside the run.

    Instead: walk back over minor troughs and take the LEFTMOST candidate whose
    advance to `p` never drew down more than MAX_RUN_PULLBACK, then pin to the
    actual low.
    """
    high = np.asarray(high, float)
    low = np.asarray(low, float)
    cand = min_tr[(min_tr >= max(p - cfg.MAX_RUN_LOOKBACK, 0))
                  & (min_tr <= p - cfg.MIN_RUN_BARS)]
    if len(cand) == 0:
        return None, None, np.nan

    def clean(limit: float) -> list[int]:
        ok = []
        for b in cand:
            seg_max = np.maximum.accumulate(high[b:p + 1])
            dd = np.nanmax(1.0 - low[b:p + 1] / np.where(seg_max > 0, seg_max, np.nan))
            if dd <= limit:
                ok.append(int(b))
        return ok

    limit = cfg.MAX_RUN_PULLBACK
    ok = clean(limit)
    # One retry at a looser limit: a name that goes 3.5x routinely takes a 25-30%
    # intermediate pullback, and if the segment breaks there b0 lands after the
    # interim peak and base_lo is corrupted.
    if ok and (p - min(ok)) < 15:
        limit += cfg.RUN_PULLBACK_RETRY
        ok = clean(limit) or ok
    if not ok:
        limit += cfg.RUN_PULLBACK_RETRY
        ok = clean(limit)
    if not ok:
        return None, None, np.nan

    b0 = int(min(ok))
    b_lo = b0 + int(np.nanargmin(low[b0:p + 1]))
    seg_max = np.maximum.accumulate(high[b0:p + 1])
    dd_max = float(np.nanmax(1.0 - low[b0:p + 1] / np.where(seg_max > 0, seg_max, np.nan)))
    return b0, int(b_lo), dd_max


def base_zone(low: np.ndarray, close: np.ndarray, atr14: np.ndarray,
              b0: int, b_lo: int, p: int, cfg=config) -> BaseZone:
    """The launch base as a ZONE, not a point.

    Window is centred slightly AFTER the swing low, because a launch base
    straddles its low rather than strictly preceding it, and clipped on the right
    so it can never leak into the run.
    """
    low = np.asarray(low, float)
    close = np.asarray(close, float)

    lo_i = max(b0, b_lo - (cfg.BASE_WIN - cfg.BASE_TAIL))
    hi_i = min(b_lo + cfg.BASE_TAIL, p - cfg.MIN_RUN_BARS)
    if hi_i <= lo_i:
        hi_i = min(b_lo + cfg.BASE_TAIL, p - 1)
    if hi_i <= lo_i:
        lo_i, hi_i = max(b_lo - 5, 0), max(b_lo + 1, 1)
    sl = slice(lo_i, hi_i + 1)

    base_lo = float(np.nanmin(low[sl]))
    base_md = float(np.nanmedian(close[sl]))
    base_hi = max(float(np.nanquantile(close[sl], cfg.BASE_HI_Q)), base_md)
    base_lo = max(base_lo, 1e-6)
    center = float(np.sqrt(base_lo * max(base_hi, base_lo)))

    a, c = atr14[sl], close[sl]
    m = np.isfinite(a) & np.isfinite(c) & (c > 0)
    atr_pct_base = float(np.median(a[m] / c[m])) if m.any() else np.nan

    return BaseZone(int(b0), int(b_lo), base_lo, base_md, base_hi, center,
                    base_hi / base_lo - 1.0, atr_pct_base)


def run_stats(high: np.ndarray, base: BaseZone, p: int, dd_max: float) -> RunStats:
    run_x = float(high[p]) / max(base.base_lo, 1e-9)
    run_bars = int(p - base.b_lo)
    denom = (base.atr_pct_base if np.isfinite(base.atr_pct_base) and base.atr_pct_base > 0
             else 0.04) * np.sqrt(max(run_bars, 1))
    run_z = float(np.log(max(run_x, 1e-9)) / denom) if denom > 0 else 0.0
    return RunStats(run_x, run_z, run_bars, float(dd_max))


# ============================================================ retrace
def bounce_low_since(low: np.ndarray, p: int, asof: int) -> tuple[int, float]:
    """The post-peak low. A pure backward statistic, causal by construction.

    Deliberately NOT sourced from find_peaks. A pivot trough at asof-3 was not
    knowable at asof-3 and can be un-flagged tomorrow; argmin over a closed
    backward window cannot. This asymmetry is intentional -- the peak (>=25 bars
    back, stable) comes from pivot detection, the low (right at the edge) never
    does.
    """
    seg = np.asarray(low, float)[p + 1:asof + 1]
    if seg.size == 0 or not np.isfinite(seg).any():
        return asof, float(low[asof])
    i = p + 1 + int(np.nanargmin(seg))
    return i, float(low[i])


def retrace_metrics(high: np.ndarray, close: np.ndarray, p: int,
                    base: BaseZone, low_i: int, bounce_low: float,
                    asof: int) -> dict:
    peak = float(high[p])
    span = max(peak - base.base_lo, 1e-9)
    post_close = np.asarray(close, float)[p + 1:asof + 1]
    min_close = float(np.nanmin(post_close)) if post_close.size else float(close[asof])
    return {
        "peak_high": peak,
        "dd_from_peak": 1.0 - bounce_low / peak if peak > 0 else 0.0,
        "retrace_of_run": (peak - bounce_low) / span,
        # The real rejector. retrace_of_run's upper bound is run-magnitude
        # dependent -- at 3.4x a retrace of 1.10 means 12% below the base, at 1.5x
        # only 2.5% -- so the absolute undercut pair does the actual work.
        "undercut": 1.0 - bounce_low / max(base.base_lo, 1e-9),
        "undercut_close": 1.0 - min_close / max(base.base_lo, 1e-9),
        "bars_since_low": int(asof - low_i),
    }


# ============================================================ bounce score
def bounce_score(o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray,
                 ind: dict, min_tr: np.ndarray, low_i: int, bounce_low: float,
                 asof: int, cfg=config) -> tuple[float, dict]:
    """Confirmation score B in [0, 100]. Scored, not gated.

    Weights favour signals already validated inside this codebase: `sweep.py`
    found the WaveTrend oversold cross at os=-53/-70 to be a top-ranked
    out-of-sample-robust config on daily bars, so it carries the single largest
    weight rather than a freshly invented rule.

    Volume is deliberately excluded and carried separately as `V`, so nothing is
    double-counted in the composite.
    """
    k = cfg.LOOKBACK_SIG
    lb = slice(max(asof - k + 1, 0), asof + 1)
    parts: dict[str, float] = {}

    def add(name: str, cond: bool, pts: float) -> None:
        parts[name] = float(pts) if cond else 0.0

    fin = lambda x: bool(np.isfinite(x))                                  # noqa: E731

    add("close_gt_ema8", fin(ind["ema8"][asof]) and c[asof] > ind["ema8"][asof], 12)
    add("ema8_slope_up", bool(ind["ema8_up"][asof]), 9)
    add("ema8_turned_up", bool(ind["ema8_turn_up"][lb].any()), 5)

    prior_tr = min_tr[min_tr < low_i]
    higher_low = False
    if len(prior_tr):
        higher_low = bounce_low > float(l[int(prior_tr[-1])])
    if not higher_low and asof - low_i >= 2:
        seg = l[max(asof - 2, 0):asof + 1]
        higher_low = bool(np.nanmin(seg) > bounce_low * 1.01)
    add("higher_low", higher_low, 9)

    r = ind["rsi14"]
    crossed = False
    for i in range(max(asof - k + 1, 1), asof + 1):
        if fin(r[i]) and fin(r[i - 1]) and r[i] > cfg.RSI_CROSS_LEVEL >= r[i - 1]:
            crossed = True
            break
    add("rsi_cross_up", crossed, 8)

    diverged = False
    if len(prior_tr):
        j = int(prior_tr[-1])
        if fin(r[low_i]) and fin(r[j]):
            diverged = bounce_low < float(l[j]) and r[low_i] > r[j] + 3
    add("rsi_divergence", diverged, 8)

    wt_os = bool((ind["wt_cross_up"][lb] & (ind["wt2"][lb] < cfg.WT_OVERSOLD)).any())
    add("wt_cross_oversold", wt_os, 14)
    add("wt_rising_out_of_os",
        fin(ind["wt1"][asof]) and ind["wt1"][asof] > ind["wt1"][asof - 1]
        and ind["wt1"][asof] > cfg.WT_OVERSOLD, 5)

    rel = slice(max(asof - 9, 0), asof + 1)      # releases lead price
    add("squeeze_release", bool(ind["sqz_release"][rel].any())
        and bool(ind["mom_rising"][asof]), 9)
    add("mom_improving", fin(ind["mom"][asof]) and fin(ind["mom"][asof - 1])
        and ind["mom"][asof] > ind["mom"][asof - 1], 3)

    rev = False
    for i in (low_i - 1, low_i, low_i + 1):
        if not (0 <= i <= asof):
            continue
        rng = h[i] - l[i]
        if rng <= 0:
            continue
        body = abs(c[i] - o[i])
        hammer = (min(o[i], c[i]) - l[i]) >= 2 * body and (c[i] - l[i]) >= 0.6 * rng
        engulf = i > 0 and c[i] > o[i] and c[i] > o[i - 1] and o[i] < c[i - 1] \
            and c[i - 1] < o[i - 1]
        upper = (c[i] - l[i]) >= 0.67 * rng
        if hammer or engulf or upper:
            rev = True
            break
    add("reversal_candle", rev, 4)

    run_hl = 0
    for i in range(asof, max(low_i, 1), -1):
        if l[i] > l[i - 1]:
            run_hl += 1
        else:
            break
    parts["consecutive_higher_lows"] = 7.0 * min(run_hl, 3) / 3.0

    post = h[low_i:asof]
    add("mini_breakout", post.size > 0 and c[asof] > float(np.nanmax(post)), 6)

    return float(sum(parts.values())), parts


def volume_signature(volume: np.ndarray, low_i: int, asof: int) -> tuple[float, dict]:
    """Dry-up into the low, expansion off it, today's participation. V in [0, 1].

    The baseline is median(volume[low_i-59 : low_i+1]) -- AT the low, NOT the
    trailing 60 bars. Using the trailing window puts the bounce's own expansion
    volume into its own denominator, which mechanically suppresses `expand` and
    makes the strongest setups score worst. A contaminated-baseline bug that is
    very easy to write and very hard to notice.
    """
    v = np.asarray(volume, float)
    base = float(np.nanmedian(v[max(low_i - 59, 0):low_i + 1]))
    if not np.isfinite(base) or base <= 0:
        return 0.0, {"dry": np.nan, "expand": np.nan, "today": np.nan}

    dry = float(np.nanmedian(v[max(low_i - 5, 0):low_i + 1])) / base
    post = v[low_i + 1:asof + 1]
    expand = float(np.nanmax(post)) / base if post.size else 0.0
    med20 = float(np.nanmedian(v[max(asof - 20, 0):asof]))
    today = v[asof] / med20 if med20 > 0 else 0.0

    V = (0.35 * np.clip((0.95 - dry) / 0.35, 0, 1)
         + 0.40 * np.clip((expand - 1.0) / 1.2, 0, 1)
         + 0.25 * np.clip((today - 0.9) / 0.9, 0, 1))
    return float(np.clip(V, 0, 1)), {"dry": dry, "expand": expand, "today": today}


def extension(close: np.ndarray, atr14: np.ndarray, low_i: int,
              bounce_low: float, asof: int, cfg=config) -> dict:
    """How far the bounce has travelled, in ATR units.

    ATR is sampled AT THE BOUNCE LOW, not at `asof`. The bounce prints the widest
    bars of the whole sequence, so current ATR(14) inflates the denominator 30-60%
    and makes LATE bounces read as EARLY -- precisely backwards. Measuring against
    the range that was normal when it bottomed answers the right question.

    A naive "% off the low" cutoff fails on exactly the motivating example: RDW at
    +24% in 3 sessions reads as "extended" against any fixed percentage that also
    admits a low-volatility name's +8% bounce. In ATR units it is ~1.9-3.1, i.e.
    CONFIRMED, across the whole plausible ATR range.
    """
    lo_close = float(close[low_i])
    a = float(atr14[low_i]) if np.isfinite(atr14[low_i]) else np.nan
    if not np.isfinite(a) or a <= 0:
        a = 0.05 * lo_close
    atr_eff = float(np.clip(a, cfg.ATR_PCT_FLOOR * lo_close, cfg.ATR_PCT_CAP * lo_close))

    ext_atr = (float(close[asof]) - bounce_low) / atr_eff
    ext_pct = float(close[asof]) / max(bounce_low, 1e-9) - 1.0
    bars = max(asof - low_i, 1)

    if ext_atr < cfg.EXT_TURNING:
        stage = "STILL_TESTING"
    elif ext_atr < cfg.EXT_CONFIRMED:
        stage = "TURNING"
    elif ext_atr < cfg.EXT_EXTENDED:
        stage = "CONFIRMED"
    elif ext_atr < cfg.EXT_GONE:
        stage = "EXTENDED"
    else:
        stage = "GONE"

    # Plateaus over the practical sweet spot rather than peaking at one point, so
    # the score does not swing on ATR estimation noise.
    stage_fit = float(np.clip(min((ext_atr - 0.60) / 1.15,
                                  (6.50 - ext_atr) / 2.50), 0.0, 1.0))
    return {"ext_atr": float(ext_atr), "ext_pct": ext_pct, "atr_at_low": atr_eff,
            "thrust": float(ext_atr / bars), "stage": stage, "stage_fit": stage_fit}


def suspect_split(close: np.ndarray, volume: np.ndarray, cfg=config) -> bool:
    """An unadjusted split manufactures a fake 10x run and a fake 90% retrace.

    Distinguished from a real news gap by volume: a genuine move that big comes
    with a volume explosion, an adjustment artefact comes with normal volume.
    """
    c = np.asarray(close, float)
    ok = np.isfinite(c) & (c > 0)
    if ok.sum() < 3:
        return False
    r = np.abs(np.diff(np.log(np.where(ok, c, np.nan))))
    big = np.nan_to_num(r, nan=0.0) > cfg.SUSPECT_SPLIT_RET
    if not big.any():
        return False
    v = np.asarray(volume, float)[1:]
    med = float(np.nanmedian(volume))
    return not bool(np.all(v[big] > cfg.SUSPECT_SPLIT_VOL_X * med)) if med > 0 else True


# ============================================================ composite
def composite(Q: float, retrace_of_run: float, run: RunStats, B: float, V: float,
              dist_low_level: float, base_width: float, stage_fit: float,
              adv_usd: float, market_cap: float | None, cfg=config) -> tuple[float, dict]:
    """Composite score 0-100. Every sub-score is [0,1] and independently readable."""
    retrace_fit = float(np.clip(
        min((retrace_of_run - cfg.RETRACE_LO) / (0.90 - cfg.RETRACE_LO),
            (1.12 - retrace_of_run) / (1.12 - 1.02)), 0.0, 1.0))

    run_quality = (0.65 * float(np.clip((run.run_x - cfg.MIN_RUN_X) / 0.8, 0, 1))
                   + 0.35 * float(np.clip((run.run_z - cfg.MIN_RUN_Z) / 3.0, 0, 1)))

    # Measures only the LOW's proximity to the level and the base's tightness.
    # It deliberately does not penalise the current close being far above the
    # level -- that is stage_fit's job, and double-counting it would penalise
    # every confirmed bounce twice.
    T = (0.65 * float(np.clip(1 - abs(dist_low_level) / 0.045, 0, 1))
         + 0.35 * float(np.clip(1 - base_width / 0.35, 0, 1)))

    lo, hi = np.log10(2e6), np.log10(1e8)
    Lq = float(np.clip((np.log10(max(adv_usd, 1.0)) - lo) / (hi - lo), 0, 1))

    if market_cap and market_cap > 0:
        mlo, mhi = np.log10(1e8), np.log10(2e11)
        Sz = float(np.clip((np.log10(market_cap) - mlo) / (mhi - mlo), 0, 1))
    else:
        Sz = Lq * 0.6        # ADV is a usable proxy when market cap is unknown

    w_size = cfg.W_SIZE * cfg.SIZE_BIAS_WEIGHT
    parts = {
        "support": cfg.W_SUPPORT * Q,
        "bounce": cfg.W_BOUNCE * (B / 100.0),
        "retrace": cfg.W_RETRACE * retrace_fit,
        "run": cfg.W_RUN * run_quality,
        "volume": cfg.W_VOLUME * V,
        "tightness": cfg.W_TIGHTNESS * T,
        "stage": cfg.W_STAGE * stage_fit,
        "liquidity": cfg.W_LIQUIDITY * Lq,
        "size": w_size * Sz,
    }
    total = sum(parts.values())
    denom = (cfg.W_SUPPORT + cfg.W_BOUNCE + cfg.W_RETRACE + cfg.W_RUN
             + cfg.W_VOLUME + cfg.W_TIGHTNESS + cfg.W_STAGE + cfg.W_LIQUIDITY
             + w_size)
    score = 100.0 * total / denom if denom > 0 else 0.0

    parts.update({"_retrace_fit": retrace_fit, "_run_quality": run_quality,
                  "_T": T, "_Lq": Lq, "_Sz": Sz, "_Q": Q, "_B": B, "_V": V,
                  "_stage_fit": stage_fit})
    return float(score), parts


def score_band(score: float, cfg=config) -> str:
    for cut, name in cfg.SCORE_BANDS:
        if score >= cut:
            return name
    return "WEAK"
