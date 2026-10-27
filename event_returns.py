#!/usr/bin/env python
"""Excess returns after dated events -- one measurement shared by every event study.

An EVENT is (ticker, the day it became public). Entry is the close of the first
session AFTER that day (an evening filing cannot be traded at that day's close).
The excess return at horizon h is the event stock's return from entry to h sessions
later, minus the mean return over the same sessions of every stock in the SAME
LIQUIDITY FIFTH that day (20-day average dollar volume, among stocks at $2+ trading
$1M+ a day). Insider and congressional buying lean to small companies, and small and
large stocks behave differently, so the whole market would be the wrong yardstick.

Significance is a t-statistic across CALENDAR QUARTERS (each quarter's mean excess is
one observation): events on the same days share a market, so counting them as
independent would overstate t. Survivorship: the bar store holds names still listed
or recently removed, so events in companies that later vanished are under-counted --
the bias flatters event returns more than the pool's.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import sectors as S

MIN_PX, MIN_DV = 2.0, 1e6
HORIZONS = (20, 40, 60)
N_BUCKETS = 5


class Panel:
    """Closes, liquidity buckets and per-bucket pool returns, built once per study."""

    def __init__(self, horizons=HORIZONS):
        C, V = S.load_wide()
        self.C = C
        self.dates = list(C.index)
        self._dates = np.asarray(self.dates)
        self.pos = {d: i for i, d in enumerate(self.dates)}
        dv = (C * V).rolling(20, min_periods=15).mean()
        elig = (C >= MIN_PX) & (dv >= MIN_DV)
        self.bucket = (dv.where(elig).rank(axis=1, pct=True) * N_BUCKETS).clip(upper=N_BUCKETS - 1e-9)
        self.bucket = np.floor(self.bucket)            # 0 (least liquid) .. 4 (most), NaN = not eligible
        self.elig = elig
        self.hi252 = C.rolling(252, min_periods=150).max()
        self.fwd, self.pool = {}, {}
        for h in horizons:
            f = C.shift(-h) / C - 1
            f = f.where(elig)
            self.fwd[h] = f
            self.pool[h] = pd.DataFrame({b: f.where(self.bucket == b).mean(axis=1) for b in range(N_BUCKETS)})

    def entry(self, day: str) -> str | None:
        """The first session strictly after `day`."""
        i = int(np.searchsorted(self._dates, day, side="right"))
        return self.dates[i] if i < len(self.dates) else None

    def measure(self, ev: pd.DataFrame, date_col: str = "event_date") -> pd.DataFrame:
        """ev[ticker, <date_col>] -> + entry, bucket, pct_hi, and excess_<h> / ret_<h> per horizon."""
        ev = ev.copy()
        # Vectorised `entry`: per row it converted the 2,500-date list to an array, which
        # was most of a 650K-event measurement (2026-10-27).
        i = np.searchsorted(self._dates, ev[date_col].astype(str).to_numpy(), side="right")
        ok_i = i < len(self.dates)
        ev["entry"] = np.where(ok_i, self._dates[np.minimum(i, len(self.dates) - 1)], None)
        ev.loc[ev[date_col].isna(), "entry"] = None
        ok = ev.entry.notna() & ev.ticker.isin(self.C.columns)
        ev = ev[ok].copy()
        cols = {t: i for i, t in enumerate(self.C.columns)}
        ri = np.array([self.pos[d] for d in ev.entry])
        ci = np.array([cols[t] for t in ev.ticker])
        b = self.bucket.values[ri, ci]
        ev["bucket"] = b
        ev["eligible"] = self.elig.values[ri, ci]
        ev["pct_hi"] = (self.C.values[ri, ci] / self.hi252.values[ri, ci])
        for h, f in self.fwd.items():
            r = f.values[ri, ci]
            pm = self.pool[h].values
            pool = np.where(np.isnan(b), np.nan, pm[ri, np.nan_to_num(b, nan=0).astype(int)])
            ev[f"ret_{h}"] = r
            ev[f"excess_{h}"] = r - pool
            ev[f"pool_up_{h}"] = np.nan            # filled by summarize from the pool's own hit rate
        return ev


def tstat(x) -> float:
    x = pd.Series(x).dropna()
    if len(x) < 3 or x.std(ddof=1) == 0:
        return np.nan
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x))))


def summarize(ev: pd.DataFrame, h: int, label: str) -> dict:
    """One test: episodes, mean/median excess, share up, t across quarters."""
    e = ev[ev[f"excess_{h}"].notna() & ev.eligible.astype(bool)]
    if len(e) < 10:
        return {"test": label, "h": h, "episodes": int(len(e)), "mean_pp": np.nan, "median_pp": np.nan,
                "up_pct": np.nan, "quarters": 0, "t": np.nan}
    q = pd.PeriodIndex(pd.to_datetime(e.entry), freq="Q")
    by_q = e.groupby(q)[f"excess_{h}"].mean()
    return {"test": label, "h": h, "episodes": int(len(e)),
            "mean_pp": round(float(e[f"excess_{h}"].mean() * 100), 2),
            "median_pp": round(float(e[f"excess_{h}"].median() * 100), 2),
            "up_pct": round(float((e[f"ret_{h}"] > 0).mean() * 100), 1),
            "beat_pool_pct": round(float((e[f"excess_{h}"] > 0).mean() * 100), 1),
            "quarters": int(len(by_q)), "t": round(tstat(by_q), 2)}


def first_in_window(ev: pd.DataFrame, gap_sessions: int, panel: Panel, date_col: str = "entry") -> pd.DataFrame:
    """One episode per ticker per `gap_sessions`: the first event, then nothing for that
    ticker until the window has passed -- so overlapping signals are not counted twice."""
    ev = ev.sort_values(["ticker", date_col])
    keep, last = [], {}
    for idx, t, d in zip(ev.index, ev.ticker, ev[date_col]):
        p = panel.pos.get(d)
        if p is None:
            continue
        if t in last and p - last[t] < gap_sessions:
            continue
        last[t] = p
        keep.append(idx)
    return ev.loc[keep]
