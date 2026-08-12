"""
Exact LazyBear indicator implementations, matching the screenshot settings.

WaveTrend  (WT_CROSS_LB): Channel=10, Average=21, OB 60/53, OS -60/-53
Squeeze    (SQZMOM_LB):   BB 20 x2.0, KC 20 x1.5, TrueRange=on

All functions are vectorized and operate on a single ticker's OHLC frame that
is already sorted by datetime with a fresh 0..N-1 RangeIndex.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    # Pine ema() = RMA-free classic EMA with alpha = 2/(n+1)
    return s.ewm(span=n, adjust=False).mean()


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def _linreg_endpoint(y: pd.Series, length: int) -> pd.Series:
    """
    Vectorized Pine linreg(y, length, 0): value of the least-squares line at the
    current (most recent) bar. x runs 0..length-1 inside each rolling window.
    """
    n = length
    x_sum = n * (n - 1) / 2.0
    x2_sum = (n - 1) * n * (2 * n - 1) / 6.0
    denom = n * x2_sum - x_sum * x_sum

    idx = np.arange(len(y), dtype=float)          # global position 0..N-1
    ky = pd.Series(idx * y.values, index=y.index)  # k * y

    B = y.rolling(n).sum()                          # Σ y over window
    A = ky.rolling(n).sum()                         # Σ (k*y) over window
    # window's first global index t-L+1 = (current_pos) - (n-1); current_pos = idx
    t0 = pd.Series(idx - (n - 1), index=y.index)
    xy_sum = A - t0 * B                             # Σ (x*y) with local x=0..n-1

    slope = (n * xy_sum - x_sum * B) / denom
    intercept = (B - slope * x_sum) / n
    return intercept + slope * (n - 1)


def wavetrend(df: pd.DataFrame, n1: int = 10, n2: int = 21) -> pd.DataFrame:
    ap = (df["high"] + df["low"] + df["close"]) / 3.0
    esa = ema(ap, n1)
    d = ema((ap - esa).abs(), n1)
    ci = (ap - esa) / (0.015 * d.replace(0, np.nan))
    tci = ema(ci, n2)
    wt1 = tci
    wt2 = sma(wt1, 4)
    out = pd.DataFrame(index=df.index)
    out["wt1"] = wt1
    out["wt2"] = wt2
    diff = wt1 - wt2
    prev = diff.shift(1)
    out["wt_cross_up"] = (diff > 0) & (prev <= 0)
    out["wt_cross_dn"] = (diff < 0) & (prev >= 0)
    return out


def squeeze(df: pd.DataFrame, bb_len: int = 20, bb_mult: float = 2.0,
            kc_len: int = 20, kc_mult: float = 1.5, use_tr: bool = True) -> pd.DataFrame:
    close, high, low = df["close"], df["high"], df["low"]

    basis = sma(close, bb_len)
    dev = bb_mult * close.rolling(bb_len).std(ddof=0)   # Pine stdev = population
    upperBB, lowerBB = basis + dev, basis - dev

    if use_tr:
        prev_c = close.shift(1)
        tr = pd.concat([(high - low),
                        (high - prev_c).abs(),
                        (low - prev_c).abs()], axis=1).max(axis=1)
        rng = tr
    else:
        rng = high - low
    ma = sma(close, kc_len)
    rangema = sma(rng, kc_len)
    upperKC, lowerKC = ma + rangema * kc_mult, ma - rangema * kc_mult

    sqz_on = (lowerBB > lowerKC) & (upperBB < upperKC)
    sqz_off = (lowerBB < lowerKC) & (upperBB > upperKC)

    hh = high.rolling(kc_len).max()
    ll = low.rolling(kc_len).min()
    avg_ = ((hh + ll) / 2.0 + sma(close, kc_len)) / 2.0
    val = _linreg_endpoint(close - avg_, kc_len)

    out = pd.DataFrame(index=df.index)
    out["mom"] = val
    prev = val.shift(1)
    # color code: 2=lime(pos rising) 1=green(pos falling) -1=maroon(neg rising) -2=red(neg falling)
    color = np.select(
        [(val > 0) & (val > prev), (val > 0) & (val <= prev),
         (val <= 0) & (val < prev), (val <= 0) & (val >= prev)],
        [2, 1, -2, -1], default=0)
    out["mom_color"] = color
    out["mom_rising"] = val > prev
    out["sqz_on"] = sqz_on
    out["sqz_off"] = sqz_off
    out["sqz_release"] = sqz_on.shift(1, fill_value=False) & sqz_off  # squeeze just fired
    return out


# ============================================================ new indicators (phase 2)
def wma(s: pd.Series, n: int) -> pd.Series:
    from numpy.lib.stride_tricks import sliding_window_view
    x = s.to_numpy(dtype=float)
    w = np.arange(1, n + 1, dtype=float)
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = (sliding_window_view(x, n) * w).sum(axis=1) / w.sum()
    return pd.Series(out, index=s.index)

def rma(s: pd.Series, n: int) -> pd.Series:          # Wilder's smoothing
    return s.ewm(alpha=1 / n, adjust=False).mean()

def vwma(price: pd.Series, vol: pd.Series, n: int) -> pd.Series:
    return (price * vol).rolling(n).sum() / vol.rolling(n).sum()

def hma(s: pd.Series, n: int) -> pd.Series:          # Hull MA
    half, sq = int(n / 2), int(np.sqrt(n))
    return wma(2 * wma(s, half) - wma(s, n), sq)

def _ma(df, kind, n, src):
    if kind == "EMA": return ema(src, n)
    if kind == "SMA": return sma(src, n)
    if kind == "WMA": return wma(src, n)
    if kind == "RMA": return rma(src, n)
    if kind == "VWMA": return vwma(src, df["volume"], n)
    raise ValueError(kind)


def colored_ma(df: pd.DataFrame, length: int = 8, kind: str = "EMA") -> pd.DataFrame:
    """Robert Nance colored MA: slope-flip is the signal (green=up, red=down)."""
    out = _ma(df, kind, length, df["close"])
    prev = out.shift(1)
    up = out > prev
    dn = out < prev
    res = pd.DataFrame(index=df.index)
    res[f"ma{length}"] = out
    res[f"ma{length}_up"] = up                                  # slope positive
    res[f"ma{length}_turn_up"] = up & ~up.shift(1, fill_value=False)   # just turned green
    res[f"ma{length}_turn_dn"] = dn & ~dn.shift(1, fill_value=False)   # just turned red
    return res


def ma_shift(df: pd.DataFrame, length: int = 40, ma_type: str = "SMA",
             osc_len: int = 15, perc_win: int = 1000, perc: float = 99,
             threshold: float = 0.5, min_periods: int = 250) -> pd.DataFrame:
    """ChartPrime MA Shift oscillator + diamond signals, faithful to the Pine."""
    src = (df["high"] + df["low"]) / 2.0                        # hl2
    MA = _ma(df, ma_type, length, src)
    diff = src - MA
    perc_r = diff.rolling(perc_win, min_periods=min_periods).quantile(perc / 100.0, interpolation="linear")
    norm = diff / perc_r.replace(0, np.nan)
    osc = hma(norm - norm.shift(osc_len), 10)                   # HMA of ta.change(norm, osc_len)
    osc2 = osc.shift(2)
    res = pd.DataFrame(index=df.index)
    res["mashift_osc"] = osc
    res["mashift_regime_up"] = (src >= MA)                      # trend regime (candle colour)
    res["mashift_sig_up"] = (osc > osc2) & (osc.shift(1) <= osc2.shift(1)) & (osc < -threshold)
    res["mashift_sig_dn"] = (osc < osc2) & (osc.shift(1) >= osc2.shift(1)) & (osc > threshold)
    res["mashift_up"] = osc > 0
    return res


def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    """df: single-ticker OHLC sorted by datetime. Returns df + indicator cols."""
    df = df.reset_index(drop=True)
    wt = wavetrend(df)
    sq = squeeze(df)
    cma = colored_ma(df, 8, "EMA")
    msh = ma_shift(df)
    return pd.concat([df, wt, sq, cma, msh], axis=1)
