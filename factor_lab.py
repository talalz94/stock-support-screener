"""
Factor lab: does ANY metric predict forward returns, for whom, and when?

    python factor_lab.py --module fundamental --metric roic
    python factor_lab.py --module sentiment --all --horizon 20
    python factor_lab.py --module fundamental --metric f_score --by sector
    python factor_lab.py --leaderboard --module fundamental
    python factor_lab.py --selftest

THIS MODULE IS DELIBERATELY METRIC-AGNOSTIC.

It knows nothing about fundamentals, sentiment, hype or price structure. It takes
(ticker, session, value) rows from the tidy score table and answers the only
question that matters about any of them:

    holding this metric constant-ranked, what happened to the price afterwards --
    at what horizon, in which sector, in which year, at which size?

That is why it exists as its own module rather than inside the fundamental
screener. Every future score module gets tested by exactly this code, on exactly
the same forward returns, so their results are comparable. A per-module bespoke
backtest would guarantee they are not.

WHAT IT REPORTS, AND WHY EACH ONE
----------------------------------
IC (Spearman rank correlation of metric vs forward return, per date, then
    averaged with a t-stat over dates).
    The headline number. Cross-sectional and rank-based, so it is unaffected by
    the metric's units or by market-wide drift -- an IC of 0.03 means the metric
    orders stocks slightly better than chance, every date, independently of
    whether the market went up.

QUANTILE SPREAD (top decile mean return minus bottom decile).
    What you would actually capture trading it. Reported WITH the long and short
    legs separately, because a spread that is entirely short-leg is not a
    strategy most people can run.

DECAY (IC across horizons).
    Whether the signal is a 5-day pop or a 6-month drift. Determines holding
    period, and a metric whose IC peaks at 1 day and vanishes by 20 is
    microstructure, not fundamentals.

COVERAGE and TURNOVER.
    A metric present for 12% of the universe with 90% monthly turnover can have
    a beautiful IC and be untradeable.

THE CONTROL IS BUILT IN
-----------------------
Every number is reported against a random-ranking baseline over the same names
and dates. This project has been burned twice by a positive-looking result with
no control: the bounce screen's +2.16% was +1.60% for a random pick from the
same pool, and the sentiment screen's +1.02% came with t=1.85. An IC without a
null distribution is a story.

LOOK-AHEAD
----------
Forward returns are measured from the NEXT session's open, never the signal
bar's close, matching backtest.py. The metric value used on date D is the one
stored for D, which every score module computes from data visible at D.
"""

from __future__ import annotations

import argparse
import sys
import time

import numpy as np
import pandas as pd

import config
import scores
import store

HORIZONS = (1, 5, 20, 60, 120)
DEFAULT_QUANTILES = 5


def log(m: str) -> None:
    print(m, flush=True)


# ===========================================================================
# Forward returns
# ===========================================================================
_PX_CACHE: dict[str, pd.DataFrame] = {}


def forward_returns(start: str, end: str,
                    horizons: tuple[int, ...] = HORIZONS) -> pd.DataFrame:
    """(ticker, date) -> forward return over each horizon, entered at NEXT open.

    Entry at the next open rather than the signal close is the same discipline
    backtest.py uses, and for the same reason: the signal is computed FROM the
    close, so the close is not a price you could have paid.
    """
    key = f"{start}|{end}|{horizons}"
    if key in _PX_CACHE:
        return _PX_CACHE[key]

    px = store.read(interval="1d", start=start, end=end,
                    columns=["ticker", "date", "open", "close"])
    if px.empty:
        return pd.DataFrame()

    px = px.sort_values(["ticker", "date"]).reset_index(drop=True)
    g = px.groupby("ticker", observed=True)
    # Entry is the open of the session AFTER the signal date.
    entry = g["open"].shift(-1)
    out = px[["ticker", "date"]].copy()
    out["entry"] = entry.values
    for h in horizons:
        exit_px = g["close"].shift(-h)
        out[f"fwd_{h}"] = (exit_px.values / entry.values) - 1.0
    _PX_CACHE[key] = out
    return out


def load_metric(module: str, metric: str, start: str | None = None,
                end: str | None = None,
                frame: pd.DataFrame | None = None) -> pd.DataFrame:
    """(ticker, session, value) for one metric from the tidy score table.

    `frame` is rows this caller has ALREADY read for this metric. Passing it
    skips `scores.read`, which opens every month partition on disk on every
    call -- see `leaderboard`, which reads its module once instead of once per
    metric for exactly that reason.
    """
    df = (frame if frame is not None
          else scores.read(module=module, metrics=[metric],
                           start=start, end=end))
    if df.empty:
        return df
    df = df[df["value"].notna()]
    return df[["session", "ticker", "value"]].rename(columns={"session": "date"})


def _sector_map() -> pd.Series:
    try:
        import macro
        m = macro.load_sector_map()
        if not m.empty:
            return m.set_index("ticker")["sector"]
    except Exception:                                              # noqa: BLE001
        pass
    return pd.Series(dtype=object)


# ===========================================================================
# Evaluation
# ===========================================================================
def evaluate(metric_rows: pd.DataFrame, horizons: tuple[int, ...] = HORIZONS,
             quantiles: int = DEFAULT_QUANTILES, by: str | None = None,
             seed: int = 0, fwd: pd.DataFrame | None = None) -> dict:
    """The full report for one metric. Returns a dict of frames.

    `fwd` is a forward-return table the caller has already built, covering at
    least this metric's dates. It exists because `_PX_CACHE` keys on the exact
    `start|end` pair: metrics sharing a date range hit it, but any metric scored
    over a different span misses and re-reads the whole daily bar store to
    rebuild a table it mostly already had. `leaderboard` builds one table per
    module and hands it to every metric, so the span no longer matters.

    Passing a WIDER table cannot change an interior result: the merge below is
    an inner join on (ticker, date), so rows outside this metric's dates are
    dropped. It can only add forward legs at the tail that a narrow window
    would have truncated to NaN -- which is the same thing `_shift_end` is
    already there to prevent.
    """
    if metric_rows.empty:
        return {}

    # Reported in the result dict either way, so they are computed here rather
    # than only on the cache-miss path.
    start, end = str(metric_rows["date"].min()), str(metric_rows["date"].max())
    if fwd is None:
        fwd = forward_returns(start, _shift_end(end, max(horizons)), horizons)
    if fwd is None or fwd.empty:
        return {}

    df = metric_rows.merge(fwd, on=["ticker", "date"], how="inner")
    if df.empty:
        return {}

    rng = np.random.default_rng(seed)
    df["_rand"] = rng.random(len(df))

    if by == "sector":
        df["_slice"] = df["ticker"].map(_sector_map()).fillna("?")
    elif by == "year":
        df["_slice"] = df["date"].str.slice(0, 4)
    elif by == "month":
        df["_slice"] = df["date"].str.slice(0, 7)
    else:
        df["_slice"] = "ALL"

    out = {"n_rows": len(df), "n_dates": df["date"].nunique(),
           "n_tickers": df["ticker"].nunique(), "start": start, "end": end}

    out["ic"] = _ic_table(df, horizons)
    out["quantiles"] = _quantile_table(df, horizons, quantiles)
    out["slices"] = _slice_table(df, horizons, quantiles) if by else pd.DataFrame()
    out["turnover"] = _turnover(metric_rows)
    return out


def _shift_end(end: str, h: int) -> str:
    """Extend the price window so the last signal date still has a forward leg."""
    import calendar_us

    s = calendar_us.all_sessions()
    try:
        i = s.index(end)
    except ValueError:
        return end
    return s[min(i + h + 2, len(s) - 1)]


def _spearman(a: pd.Series, b: pd.Series) -> float:
    """Rank correlation, NaN-safe. Returns NaN below 5 usable pairs, because a
    3-point correlation is noise wearing a number's clothes."""
    m = a.notna() & b.notna()
    if m.sum() < 5:
        return np.nan
    return float(a[m].rank().corr(b[m].rank()))


def _spacing(dates) -> float:
    """Median trading-session gap between scored dates.

    Feeds the overlap correction: with dates 3 sessions apart, a 120-session
    forward window is shared by ~40 consecutive observations, and treating them
    as independent inflates every long-horizon t-stat by ~sqrt(40).
    """
    import calendar_us

    s = calendar_us.all_sessions()
    idx = {d: i for i, d in enumerate(s)}
    pos = sorted(idx[d] for d in dates if d in idx)
    if len(pos) < 2:
        return 1.0
    gaps = np.diff(pos)
    return float(np.median(gaps)) if len(gaps) else 1.0


def _ic_table(df: pd.DataFrame, horizons) -> pd.DataFrame:
    """Per-date IC, then its mean, t-stat and hit rate -- against a random control."""
    rows = []
    for h in horizons:
        col = f"fwd_{h}"
        per_date = df.groupby("date", observed=True).apply(
            lambda g: pd.Series({
                "ic": _spearman(g["value"], g[col]),
                "ic_rand": _spearman(g["_rand"], g[col]),
                "n": int(g[col].notna().sum()),
            }), include_groups=False)
        ic = per_date["ic"].dropna()
        icr = per_date["ic_rand"].dropna()
        if ic.empty:
            continue
        # t over DATES, not over observations: stocks within a date share market
        # moves, so pooling would treat one market day as hundreds of
        # independent samples and inflate significance enormously.
        # A zero-variance IC series (every date identical) has an undefined
        # t; report inf rather than raising a divide-by-zero mid-run.
        sd = ic.std(ddof=1) if len(ic) > 1 else np.nan

        # OVERLAP CORRECTION -- without it every long horizon looks miraculous.
        # A 120-session forward window sampled every 3 sessions overlaps its
        # neighbour by ~97%, so 151 dates carry nowhere near 151 independent
        # observations. Measured on real sentiment scores, the naive t at h=120
        # was 10.98; dividing the variance by the true independent count takes
        # it to ~1.7. The naive number is not a stronger result, it is the same
        # result counted forty times.
        #
        # n_eff = n_dates / (horizon / median spacing between dates), floored at
        # 2. This is the standard non-overlapping-block approximation; a full
        # Newey-West would be tighter but needs the return series, not the ICs.
        n_eff = max(2.0, len(ic) / max(1.0, h / max(_spacing(per_date.index), 1.0)))
        t = (float(ic.mean() / (sd / np.sqrt(n_eff)))
             if np.isfinite(sd) and sd > 0 else
             (np.inf * np.sign(ic.mean()) if len(ic) > 1 else np.nan))
        rows.append({
            "horizon": h, "ic": ic.mean(), "ic_std": ic.std(ddof=1),
            "t": t, "hit": float((ic > 0).mean()),
            "ic_random": icr.mean() if len(icr) else np.nan,
            "n_dates": len(ic), "avg_n": float(per_date["n"].mean()),
            "n_eff": n_eff,
            # Grinold's IR: IC scaled by breadth, the standard comparability unit.
            "ir": float(ic.mean() / sd) if np.isfinite(sd) and sd > 0 else np.nan,
        })
    return pd.DataFrame(rows)


def _quantile_table(df: pd.DataFrame, horizons, q: int) -> pd.DataFrame:
    """Top-minus-bottom quantile spread, with both legs shown separately."""
    rows = []
    for h in horizons:
        col = f"fwd_{h}"
        d = df[df[col].notna()].copy()
        if d.empty:
            continue
        d["_q"] = d.groupby("date", observed=True)["value"].transform(
            lambda s: pd.qcut(s.rank(method="first"), q, labels=False,
                              duplicates="drop") if s.notna().sum() >= q * 3 else np.nan)
        d = d[d["_q"].notna()]
        if d.empty:
            continue
        per = d.groupby(["date", "_q"], observed=True)[col].mean().unstack()
        if per.shape[1] < 2:
            continue
        lo, hi = per.columns.min(), per.columns.max()
        spread = per[hi] - per[lo]
        t = (float(spread.mean() / (spread.std(ddof=1) / np.sqrt(len(spread))))
             if len(spread) > 1 and spread.std(ddof=1) else np.nan)
        rows.append({"horizon": h, "top": per[hi].mean(), "bottom": per[lo].mean(),
                     "spread": spread.mean(), "t": t,
                     "all": d[col].mean(), "n_dates": len(spread)})
    return pd.DataFrame(rows)


def _slice_table(df: pd.DataFrame, horizons, q: int) -> pd.DataFrame:
    """The same IC and spread, cut by sector / year / whatever `by` selected."""
    h = horizons[min(2, len(horizons) - 1)]          # the 20d view by default
    col = f"fwd_{h}"
    rows = []
    for name, g in df.groupby("_slice", observed=True):
        if g[col].notna().sum() < 60:
            continue
        per_date = g.groupby("date", observed=True).apply(
            lambda x: _spearman(x["value"], x[col]), include_groups=False).dropna()
        if per_date.empty:
            continue
        t = (float(per_date.mean() / (per_date.std(ddof=1) / np.sqrt(len(per_date))))
             if len(per_date) > 1 and per_date.std(ddof=1) else np.nan)
        rows.append({"slice": str(name), "horizon": h, "ic": per_date.mean(),
                     "t": t, "n_obs": int(g[col].notna().sum()),
                     "n_dates": len(per_date),
                     "mean_fwd": float(g[col].mean())})
    return pd.DataFrame(rows).sort_values("ic", ascending=False) if rows else pd.DataFrame()


def _turnover(rows: pd.DataFrame) -> float:
    """Mean fraction of the top quintile that changes between dates.

    A high-IC metric with 90% turnover is a costs problem, not a strategy.
    """
    dates = sorted(rows["date"].unique())
    if len(dates) < 2:
        return np.nan
    prev, chg = None, []
    for d in dates:
        g = rows[rows["date"] == d]
        if len(g) < 10:
            continue
        top = set(g.nlargest(max(len(g) // 5, 1), "value")["ticker"])
        if prev:
            chg.append(1.0 - len(top & prev) / max(len(top), 1))
        prev = top
    return float(np.mean(chg)) if chg else np.nan


# ===========================================================================
# Reporting
# ===========================================================================
def report(module: str, metric: str, res: dict) -> None:
    if not res:
        log(f"  {module}/{metric}: no overlapping (metric, price) rows")
        return
    log(f"\n  {module} / {metric}")
    log(f"    {res['n_rows']:,} obs | {res['n_dates']} date(s) | "
        f"{res['n_tickers']:,} ticker(s) | {res['start']} .. {res['end']} | "
        f"turnover {res['turnover']:.0%}" if np.isfinite(res.get("turnover", np.nan))
        else f"    {res['n_rows']:,} obs | {res['n_dates']} date(s)")

    ic = res.get("ic")
    if ic is not None and not ic.empty:
        log(f"\n    {'h':>4} {'IC':>8} {'t':>7} {'hit':>6} {'IR':>7} "
            f"{'IC_rand':>8} {'dates':>6} {'avg n':>7}")
        for _, r in ic.iterrows():
            log(f"    {int(r['horizon']):>4} {r['ic']:>+8.4f} {r['t']:>7.2f} "
                f"{r['hit']:>5.0%} {r['ir']:>7.3f} {r['ic_random']:>+8.4f} "
                f"{int(r['n_dates']):>6} {r.get('n_eff', float('nan')):>6.0f} "
                f"{r['avg_n']:>7.0f}")
        log("    t* is overlap-corrected: n_eff = dates / (horizon / spacing).")

    q = res.get("quantiles")
    if q is not None and not q.empty:
        log(f"\n    {'h':>4} {'top':>8} {'bottom':>8} {'spread':>8} {'t':>7} {'all':>8}")
        for _, r in q.iterrows():
            log(f"    {int(r['horizon']):>4} {r['top']:>+7.2%} {r['bottom']:>+7.2%} "
                f"{r['spread']:>+7.2%} {r['t']:>7.2f} {r['all']:>+7.2%}")

    s = res.get("slices")
    if s is not None and not s.empty:
        log(f"\n    by slice (h={int(s['horizon'].iloc[0])}):")
        log(f"    {'slice':<24} {'IC':>8} {'t':>7} {'obs':>7} {'mean fwd':>9}")
        for _, r in s.head(14).iterrows():
            log(f"    {r['slice'][:24]:<24} {r['ic']:>+8.4f} {r['t']:>7.2f} "
                f"{int(r['n_obs']):>7,} {r['mean_fwd']:>+8.2%}")

    _verdict(ic)


def _verdict(ic: pd.DataFrame | None) -> None:
    """One honest sentence. The whole point of the module."""
    if ic is None or ic.empty:
        return
    best = ic.loc[ic["ic"].abs().idxmax()]
    t = best["t"]
    if not np.isfinite(t):
        return
    if abs(t) >= 3:
        v = "STRONG -- survives a demanding threshold"
    elif abs(t) >= 2:
        v = "suggestive; |t|>2 but that is one test among many, so treat as a hypothesis"
    else:
        v = "NOT distinguishable from random ranking at this sample size"
    log(f"\n    verdict: best IC {best['ic']:+.4f} at h={int(best['horizon'])} "
        f"(t={t:.2f}) -- {v}")


# Columns that are PROVENANCE, not signal. They rank high because a name with
# complete data and a long history is an established company -- which is a
# survivorship fact about the dataset, not a prediction about returns.
#
# Measured 2026-08-08: `hype_cov` scored t=3.27 and `bars_used` t=2.78, ABOVE
# every real metric in the hype module. Left in, the leaderboard's top rows
# recommend "has complete data" as a factor.
NON_SIGNAL = frozenset({
    "hype_cov", "fund_cov", "dip_cov", "news_coverage",
    "has_hype", "has_fundamentals", "has_news", "has_dip",
    "bars_used", "last_filed", "sector",
})


def is_signal(metric: str) -> bool:
    """False for provenance/coverage columns -- see NON_SIGNAL."""
    return metric not in NON_SIGNAL


def leaderboard(module: str, horizon: int = 20, start: str | None = None,
                end: str | None = None, min_dates: int = 10,
                budget_s: float | None = None,
                verbose: bool = True) -> pd.DataFrame:
    """Every metric in a module, ranked by |IC|. The screen's own scoreboard.

    THE STORE IS READ ONCE, NOT ONCE PER METRIC
    -------------------------------------------
    `load_metric` calls `scores.read`, and `scores.read` opens EVERY month
    partition and filters afterwards -- 121 files, 238 MB, on every single call.
    MEASURED 2026-08-16: 25.5s per call, whatever the metric. Looping it per
    metric therefore paid that 25.5s 119 times across the five modules -- about
    50 minutes spent re-reading a store already read -- before a single IC.

    That was affordable when the store held two years. After the full-depth
    backfill to 2016 it was not: on 2026-08-16 this step ran **9h31m without
    printing one line**, against a 90-minute budget and a 53-minute measurement
    taken before the backfill. It held the run lock past the task's 12-hour
    ExecutionTimeLimit, so the next day's run could not have started.

    The cost split roughly in half, and only one half was the store read
    (measured on `dip`, 2026-08-16): 25.5s re-reading the store per metric,
    26.9s actually evaluating it. So reading once removes half; the rest is the
    shared forward-return table below, and what remains after both is real
    arithmetic that has to be paid.

    Four changes, each aimed at one part of that:

      read once   the module's rows are read in a single pass and sliced in
                  memory -- one store scan instead of one per metric
      one fwd     a single forward-return table per module, because `_PX_CACHE`
                  keys on each metric's own date range and so missed on nearly
                  every metric, re-reading the whole bar store each time
      log         a line per metric, because silence is what made a 9-hour
                  overrun indistinguishable from a deadlock
      budget_s    stops the loop and returns what it has. The orchestrator's
                  `timeout=` is only a LABEL compared against elapsed time
                  after a step returns, so a step that never returns is never
                  caught -- the limit has to live in here.
    """
    scores.load_all()
    mod = scores.get(module)
    # Provenance, not signal -- see NON_SIGNAL. These outranked every real
    # metric in the hype module (hype_cov t=3.27, bars_used t=2.78) because
    # complete data correlates with being an established company. Reporting
    # them invites reading "has data" as a factor.
    sigs = [m for m in mod.metrics() if is_signal(m)]
    if not sigs:
        return pd.DataFrame()

    t0 = time.time()
    sd = scores.read(module=module, metrics=sigs, start=start, end=end)
    if sd.empty:
        return pd.DataFrame()
    # Drop what the evaluation never reads before grouping. `module` is
    # constant here and `label` is for report legends, so carrying them just
    # multiplies the peak footprint of an 18M-row frame.
    sd = sd[["session", "ticker", "metric", "value"]]
    sd = sd[sd["value"].notna()]
    by_metric = {str(k): g for k, g in sd.groupby("metric", observed=True)}
    t_read = time.time() - t0

    # ONE forward-return table for the whole module, covering every metric's
    # dates. `_PX_CACHE` keys on the exact date pair, so it hits only for
    # metrics that happen to share a range and MISSES for any metric scored
    # over a different span -- and each miss re-reads the whole daily bar store.
    #
    # MEASURED on dip (2026-08-16), where 7 of 8 metrics share 219 dates: the
    # shared table saved 15s of 146.5s overall, but 13.2s of that came from the
    # single metric whose range differs (not_extended, 210 dates, 33.9s ->
    # 20.7s). So this is worth most on modules whose metrics were introduced at
    # different times -- fundamental's 48 -- and near-free on uniform ones.
    # Proven not to change any number: max |d_ic| = 0 across dip's 8 metrics.
    fwd = forward_returns(str(sd["session"].min()),
                          _shift_end(str(sd["session"].max()), horizon),
                          (horizon,))
    if verbose:
        log(f"    [{module}] {len(sd):,} row(s), {len(by_metric)} of "
            f"{len(sigs)} metric(s) present, {len(fwd):,} forward-return row(s)"
            f" -- setup {time.time() - t0:.1f}s (read {t_read:.1f}s)")

    rows, stopped = [], None
    for i, metric in enumerate(sigs, 1):
        if budget_s is not None and (time.time() - t0) > budget_s:
            stopped = metric
            break
        g = by_metric.get(metric)
        if g is None or g.empty:
            continue
        try:
            ts = time.time()
            mr = load_metric(module, metric, frame=g)
            if mr.empty or mr["date"].nunique() < min_dates:
                continue
            res = evaluate(mr, horizons=(horizon,), by=None, fwd=fwd)
            ic = res.get("ic")
            if ic is None or ic.empty:
                continue
            r = ic.iloc[0]
            q = res.get("quantiles")
            rows.append({
                "metric": metric, "ic": r["ic"], "t": r["t"], "hit": r["hit"],
                "ic_random": r["ic_random"],
                "spread": q["spread"].iloc[0] if q is not None and not q.empty else np.nan,
                "n_dates": int(r["n_dates"]), "avg_n": r["avg_n"],
                "turnover": res.get("turnover", np.nan),
            })
            if verbose:
                log(f"      {i:3d}/{len(sigs)} {metric:<28s} "
                    f"{time.time() - ts:6.1f}s")
        except Exception as exc:                                   # noqa: BLE001
            log(f"    ! {metric}: {repr(exc)[:80]}")

    if stopped is not None:
        log(f"    [{module}] BUDGET {budget_s:.0f}s reached at {stopped!r} -- "
            f"{len(rows)} of {len(sigs)} metric(s) evaluated, returning partial")
    elif verbose:
        log(f"    [{module}] {len(rows)} metric(s) in {time.time() - t0:.1f}s")

    if not rows:
        return pd.DataFrame()
    return (pd.DataFrame(rows)
            .assign(abs_ic=lambda d: d["ic"].abs())
            .sort_values("abs_ic", ascending=False)
            .drop(columns="abs_ic").reset_index(drop=True))


# ===========================================================================
# Selftest
# ===========================================================================
def selftest(verbose: bool = True) -> None:
    fails = []

    # A metric that IS the forward return must show IC ~ +1; its negation ~ -1.
    rng = np.random.default_rng(7)
    dates = [f"2024-01-{d:02d}" for d in range(2, 12)]
    tick = [f"T{i}" for i in range(40)]
    recs = []
    for d in dates:
        r = rng.normal(0, 0.05, len(tick))
        for tk, x in zip(tick, r):
            recs.append({"date": d, "ticker": tk, "value": x, "fwd_5": x,
                         "fwd_1": -x, "_rand": rng.random()})
    df = pd.DataFrame(recs)
    df["_slice"] = "ALL"
    ic = _ic_table(df, (5, 1))
    if not (ic.loc[ic["horizon"] == 5, "ic"].iloc[0] > 0.99):
        fails.append("perfect predictor did not produce IC ~ +1")
    if not (ic.loc[ic["horizon"] == 1, "ic"].iloc[0] < -0.99):
        fails.append("perfectly inverted predictor did not produce IC ~ -1")
    if abs(ic.loc[ic["horizon"] == 5, "ic_random"].iloc[0]) > 0.35:
        fails.append("random control IC is implausibly large")

    # Quantile spread must be positive for a perfect predictor.
    q = _quantile_table(df, (5,), 5)
    if q.empty or q["spread"].iloc[0] <= 0:
        fails.append("perfect predictor produced a non-positive quantile spread")

    # _spearman must refuse to answer on too-few pairs.
    if not np.isnan(_spearman(pd.Series([1.0, 2, 3]), pd.Series([1.0, 2, 3]))):
        fails.append("_spearman answered on 3 pairs; must require >=5")

    # Turnover: a constant ranking is 0, a reshuffled one is high.
    const = pd.DataFrame([{"date": d, "ticker": t, "value": i}
                          for d in dates for i, t in enumerate(tick)])
    if _turnover(const) > 1e-9:
        fails.append(f"constant ranking turnover {_turnover(const):.3f}, want 0")

    if fails:
        print("SELFTEST FAILURES:")
        for f in fails:
            print("  -", f)
        raise SystemExit(1)
    if verbose:
        print(f"factor_lab selftest OK (horizons {HORIZONS})")


def main() -> int:
    config.safe_console()
    ap = argparse.ArgumentParser(description="Metric -> forward-return attribution.")
    ap.add_argument("--module", default="fundamental")
    ap.add_argument("--metric", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--leaderboard", action="store_true")
    ap.add_argument("--by", choices=["sector", "year", "month"], default=None)
    ap.add_argument("--horizon", type=int, default=20)
    ap.add_argument("--quantiles", type=int, default=DEFAULT_QUANTILES)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    config.dirs()

    if a.selftest:
        return selftest() or 0

    if a.leaderboard or a.all:
        t0 = time.time()
        lb = leaderboard(a.module, a.horizon, a.start, a.end)
        if lb.empty:
            log(f"  no scored metrics for module {a.module!r} "
                "-- has the screener run and written score rows?")
            return 0
        log(f"\n  {a.module} metric leaderboard, h={a.horizon} "
            f"({time.time() - t0:.0f}s)\n")
        log(f"  {'metric':<22} {'IC':>8} {'t':>7} {'hit':>6} {'rand':>8} "
            f"{'spread':>8} {'dates':>6} {'turn':>6}")
        for _, r in lb.iterrows():
            log(f"  {r['metric']:<22} {r['ic']:>+8.4f} {r['t']:>7.2f} {r['hit']:>5.0%} "
                f"{r['ic_random']:>+8.4f} "
                f"{r['spread']:>+7.2%} {int(r['n_dates']):>6} "
                f"{r['turnover']:>5.0%}" if np.isfinite(r["turnover"]) else "")
        log("\n  |t| >= 2 is suggestive, >= 3 is strong. These are many tests on "
            "correlated\n  metrics, so the leaderboard's TOP row is the one most "
            "likely to be luck.")
        return 0

    if not a.metric:
        ap.error("pass --metric NAME, or --leaderboard for all of them")
    mr = load_metric(a.module, a.metric, a.start, a.end)
    if mr.empty:
        log(f"  no stored rows for {a.module}/{a.metric}")
        return 0
    res = evaluate(mr, by=a.by, quantiles=a.quantiles)
    report(a.module, a.metric, res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
