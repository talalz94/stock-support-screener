"""
Empirical severity calibration: how much does each KIND of news actually move a stock?

    python events.py --calibrate      measure from the store, write _event_severity.parquet
    python events.py --show           the measured table
    python events.py --selftest

THE POINT
---------
`sentiment.EVENTS` ships a hand-written `prior` severity per event class. Those
numbers are guesses, and this project has a standing rule about guesses: MIN_RUN_Z
was planned at 3.0 and measured 1.25; support grade A was given the largest score
weight and measured the weakest grade. A severity score built from intuition
would be the third instance of the same mistake.

So severity is MEASURED. For every (article, ticker) pair in the store, take the
return over the session the article was attributed to, normalise it by that
stock's ATR at the time, and take the distribution per event class.

WHY ATR-NORMALISED AND NOT PERCENT
----------------------------------
The same reasoning that makes `ext_atr` the right extension measure. A 5% move is
enormous for ORCL and inside the daily noise for DPRO. Raw percent would rank
every event class on micro-caps by volatility rather than by importance, and the
"most severe" events would simply be the ones that happen to cheap stocks.

WHY |RETURN| AND NOT RETURN
---------------------------
Severity is magnitude; direction is the sentiment score's job. A guidance cut and
a buyback are both major events and only one is bad news. Conflating them makes
"very negative" and "very important" indistinguishable, which is precisely the
distinction the report needs to draw. The signed mean is reported too, as a
CHECK: if EARNINGS_BEAT does not come out positive, the taxonomy is broken.

CAUSALITY
---------
The article is attributed to session S by news.attribute_session (published after
S's close -> S+1). The return measured is S's OPEN to S's CLOSE, which is
tradeable given the signal, and the ATR denominator is taken from the session
BEFORE S so the move being measured cannot inflate its own normaliser.
"""

from __future__ import annotations

import argparse
import sys
import time

import numpy as np
import pandas as pd

import config
import news
import sentiment as senti
import store

MIN_N = 30          # below this a mean is not a measurement


def _bar_panel(start: str, end: str) -> pd.DataFrame:
    """(ticker, date) -> intraday return and the ATR% known BEFORE that session."""
    df = store.read(interval="1d", start=start, end=end,
                    columns=["ticker", "date", "open", "high", "low", "close"])
    if df.empty:
        return df

    df = df.sort_values(["ticker", "date"])
    g = df.groupby("ticker", observed=True)

    prev_close = g["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    # SHIFTED: the ATR must not include the session whose move it normalises,
    # or a big day divides itself down and every large event looks average.
    atr = (tr.groupby(df["ticker"], observed=True)
             .transform(lambda s: s.rolling(14, min_periods=7).mean()).shift(1))

    df["ret"] = df["close"] / df["open"] - 1.0
    df["atr_pct"] = (atr / prev_close).replace([np.inf, -np.inf], np.nan)
    df["ret_atr"] = df["ret"] / df["atr_pct"]
    return df[["ticker", "date", "ret", "atr_pct", "ret_atr"]]


def calibrate(start: str | None = None, end: str | None = None,
              verbose: bool = True) -> pd.DataFrame:
    """Measure |return|/ATR per event class over the whole news + bar store."""
    t0 = time.time()

    months = news.months()
    if not months:
        raise RuntimeError("news store is empty; run `python news.py --backfill`")
    start = start or f"{months[0]}-01"
    end = end or str(pd.read_parquet(news.part_path(months[-1]),
                                     columns=["session"])["session"].dropna().max())

    art = news.read(start=start, end=end)
    if art.empty:
        raise RuntimeError(f"no articles between {start} and {end}")
    art = _attach(art)
    art = art[art["is_company"]]

    ex = news.explode(art)
    if ex.empty:
        raise RuntimeError("no (article, ticker) pairs after exploding")

    # ONE EVENT, ONE ROW. Benzinga republishes the same story with reworded
    # headlines: CPHI's 2026-07-21 circuit-breaker halt appears SIX times, and
    # measured across the store 27.6% of (article, ticker) pairs are repeats of
    # the same (ticker, session, event_type) -- 1.98x for HALT alone.
    #
    # An earlier check on exact/templated headline text put duplication at 1%,
    # which was simply the wrong lens: the rewrites are not textually similar,
    # they are the same EVENT. Left in, a single dramatic day would carry the
    # weight of six, and the class means would measure Benzinga's editorial
    # habits rather than the market's response.
    # Sorted by `id` rather than `ts`: _attach drops the timestamp, and Benzinga
    # ids are monotonic with publication, so "first" is still the breaking story
    # rather than an arbitrary rewrite.
    ex = (ex.sort_values("id")
            .drop_duplicates(["ticker", "session", "event_type"], keep="first"))

    bars = _bar_panel(start, end)
    if bars.empty:
        raise RuntimeError("no bars in that range")

    m = ex.merge(bars, left_on=["ticker", "session"],
                 right_on=["ticker", "date"], how="inner")
    if m.empty:
        raise RuntimeError("no (article, session) pair matched a bar")

    m = m[np.isfinite(m["ret_atr"])]
    # A |ret|/ATR above 20 is a data artefact (a split we have not adjusted, or a
    # halted name reopening), not an event response. Clipped rather than dropped
    # so the count stays honest.
    m["abs_ret_atr"] = m["ret_atr"].abs().clip(upper=20.0)

    g = m.groupby("event_type")
    out = pd.DataFrame({
        "n": g.size(),
        "abs_ret_atr": g["abs_ret_atr"].mean(),
        "abs_ret_atr_med": g["abs_ret_atr"].median(),
        "signed_ret_atr": g["ret_atr"].mean(),
        "abs_ret_pct": g["ret"].apply(lambda s: float(s.abs().mean())),
        "signed_ret_pct": g["ret"].mean(),
        "p90_abs_atr": g["abs_ret_atr"].quantile(0.90),
    }).reset_index().sort_values("abs_ret_atr", ascending=False)

    # The all-events baseline. Without it, "OFFERING moves a stock 1.3 ATR" is
    # not interpretable -- the question is always 1.3 against WHAT.
    #
    # MEDIAN, not mean, and this is the same lesson the 2026-08-05 log entry
    # records in the opposite direction. Event responses are violently
    # right-skewed: 6.4% of HALT rows exceed |100%| and CPHI alone printed +842%,
    # which drags the HALT mean to +25.8% against a +3.3% median. For SEVERITY --
    # "how much does this kind of news typically move a stock" -- the median is
    # the honest statistic and the mean measures the tail. p90 is kept alongside
    # so the tail stays visible rather than being discarded.
    base = float(m["abs_ret_atr"].median())
    out["lift"] = out["abs_ret_atr_med"] / base
    out.attrs["baseline_abs_ret_atr_med"] = base

    tmp = config.EVENT_SEVERITY_FILE.with_suffix(".parquet.tmp")
    out.to_parquet(tmp, compression=config.COMPRESSION, index=False)
    store.atomic_replace(tmp, config.EVENT_SEVERITY_FILE)

    if verbose:
        log_n = len(m)
        print(f"  calibrated on {log_n:,} (article, session) pairs "
              f"over {start} .. {end}, {time.time() - t0:.0f}s")
        print(f"  baseline |ret|/ATR across all events: {base:.3f}")
        print(f"  {(out['n'] >= MIN_N).sum()}/{len(out)} classes reach n>={MIN_N} "
              "and will override their hand prior")
    # THE SCORE CACHE DEPENDS ON THIS TABLE. data/senti/*.parquet stores a
    # `severity` column computed from whatever priors were in force when it was
    # written, so a recalibration silently invalidates it -- caught here: the
    # cache still held hand-prior severities up to 5.0 ATR, which put more
    # articles in CRITICAL than in HIGH, an impossible shape for a severity
    # ladder. Recalibrating and NOT rebuilding is not an option, so it is not
    # left to the caller.
    senti._SEV_CACHE = None
    if verbose:
        print("  rebuilding the score cache against the new severity table...")
    senti.build_cache(rebuild=True, verbose=False)
    return out


def _attach(art: pd.DataFrame) -> pd.DataFrame:
    """Article frame + scores, reusing the cache when warm."""
    cached = senti.load_cached(start=str(art["session"].min()),
                              end=str(art["session"].max()))
    if cached.empty:
        sc = senti.score_frame(art)
    else:
        cached = cached.drop_duplicates("id", keep="last")
        hit = art["id"].isin(set(cached["id"]))
        parts = [cached[cached["id"].isin(set(art.loc[hit, "id"]))]]
        if (~hit).any():
            parts.append(senti.score_frame(art[~hit]))
        sc = pd.concat(parts, ignore_index=True)
    keep = ["id", "session", "headline", "n_symbols", "symbols"]
    return art[keep].merge(sc.drop(columns=["session"]), on="id", how="inner")


def load() -> pd.DataFrame:
    if config.EVENT_SEVERITY_FILE.exists():
        return pd.read_parquet(config.EVENT_SEVERITY_FILE)
    return pd.DataFrame()


def show() -> None:
    df = load()
    if df.empty:
        print("  (not calibrated -- run `python events.py --calibrate`)")
        return
    priors = {n: p for n, _, _, p in senti.EVENTS}
    df = df.sort_values("abs_ret_atr_med", ascending=False)
    print(f"  {'event_type':<18} {'n':>7} {'MED|r|/ATR':>11} {'lift':>6} {'p90':>6} "
          f"{'mean':>6} {'signed%':>8} {'prior':>6}  status")
    for _, r in df.iterrows():
        used = r["n"] >= MIN_N
        print(f"  {r['event_type']:<18} {int(r['n']):>7,} {r['abs_ret_atr_med']:>11.3f} "
              f"{r['lift']:>6.2f} {r['p90_abs_atr']:>6.2f} {r['abs_ret_atr']:>6.2f} "
              f"{r['signed_ret_pct'] * 100:>+7.2f}% {priors.get(r['event_type'], 0.8):>6.1f}  "
              f"{'MEASURED' if used else f'n<{MIN_N}, prior kept'}")
    print("\n  lift is vs the all-event MEDIAN. `mean` is shown only to expose skew:\n"
          "  where mean >> med the class is tail-driven (HALT most of all).")


def selftest(verbose: bool = True) -> None:
    fails = []
    df = load()

    if df.empty:
        if verbose:
            print("events selftest OK (not yet calibrated -- hand priors in use)")
        return

    if (df["abs_ret_atr"] < 0).any():
        fails.append("negative |ret|/ATR")
    if not np.isfinite(df["abs_ret_atr"]).all():
        fails.append("non-finite |ret|/ATR")

    # Direction check on the taxonomy itself: if a measured BEAT is not
    # positive on average, the classifier is mislabelling, not the market.
    for ev, want in (("EARNINGS_BEAT", 1), ("EARNINGS_MISS", -1),
                     ("GUIDANCE_RAISE", 1), ("GUIDANCE_CUT", -1)):
        row = df[df["event_type"] == ev]
        if row.empty or int(row["n"].iloc[0]) < MIN_N:
            continue
        got = float(row["signed_ret_pct"].iloc[0])
        if np.sign(got) != want:
            fails.append(f"{ev} signed return {got:+.3%} but taxonomy says "
                         f"{'positive' if want > 0 else 'negative'}")

    if fails:
        print("SELFTEST FAILURES:")
        for f in fails:
            print("  -", f)
        raise SystemExit(1)
    if verbose:
        print(f"events selftest OK ({len(df)} classes, "
              f"{(df['n'] >= MIN_N).sum()} measured)")


def main() -> int:
    config.safe_console()
    ap = argparse.ArgumentParser(description="Empirical event severity calibration.")
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    a = ap.parse_args()

    config.dirs()
    if a.calibrate:
        calibrate(start=a.start, end=a.end)
        show()
    elif a.show:
        show()
    elif a.selftest:
        selftest()
    else:
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
