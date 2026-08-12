"""
Full historical backtest with realistic entry and exit rules.

    python backtest.py --start 2023-01 --every 2          the main run
    python backtest.py --start 2023-01 --every 2 --exits  + exit-rule comparison
    python backtest.py --base-rates                        write the daily base-rate table

Three things this does that `replay.py` did not:

1. **Per-date eligibility, computed without look-ahead.** `replay.py` picked its
   universe from *today's* panel stats, so it was conditioned on names that have
   fallen by today. Here the gates (price, dollar volume, bar count, distance below
   the 250-day high) are evaluated with rolling windows AS OF each test date, so
   the universe at 2024-03-15 is the one that was actually eligible then.

2. **Entry at the next session's OPEN, not the flag day's close.** The flag is
   produced from a completed session, so its close is already gone by the time the
   screen runs. Entering at the next open is the earliest fill actually available,
   and it matters: these are gapping names, so the difference is not cosmetic.

3. **Exit rules.** The first replay measured fixed 5/10/20-session holds and found
   median MFE +9% against MAE -11% -- the move exists but is not capturable by
   holding. This tests whether any stop/target rule converts that excursion into a
   result, which is the actual open question.

Survivorship, stated plainly: the universe comes from Alpaca's CURRENT asset list,
so companies that delisted during the test window were never fetched and cannot
appear. Delistings skew heavily toward the worst performers, so every number here
is optimistic. `--survivorship` estimates the size of the hole.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from datetime import datetime

import numpy as np
import pandas as pd

import config

config.safe_console()

import calendar_us                                              # noqa: E402
import classify                                                 # noqa: E402
import dataset                                                  # noqa: E402
import screen                                                   # noqa: E402
import store                                                    # noqa: E402

BASE_RATE_FILE = config.DATA / "_base_rates.parquet"
MAX_HOLD = 25          # sessions of forward path kept per flag


# ===================================================================== universe
def eligibility(interval: str = "1d") -> pd.DataFrame:
    """Per (ticker, date) gate flags from rolling windows -- no look-ahead.

    One vectorised pass over the whole store. This is what lets the backtest ask
    "which names were eligible on 2024-03-15?" instead of "which are eligible
    today?", and it removes the selection bias that made the first replay's
    absolute numbers uninterpretable.
    """
    df = store.read(interval, columns=["ticker", "date", "high", "low", "close",
                                       "volume", "trades"])
    if df.empty:
        return df
    df = df.sort_values(["ticker", "date"]).reset_index(drop=True)
    g = df.groupby("ticker", sort=False, observed=True)

    dv = (df["close"] * df["volume"]).astype("float64")
    df["adv20"] = dv.groupby(df["ticker"], observed=True).transform(
        lambda s: s.rolling(20, min_periods=10).median()).astype("float32")
    df["trades20"] = g["trades"].transform(
        lambda s: s.rolling(20, min_periods=10).median()).astype("float32")
    df["hi250"] = g["high"].transform(
        lambda s: s.rolling(250, min_periods=200).max()).astype("float32")
    df["lo250"] = g["low"].transform(
        lambda s: s.rolling(250, min_periods=200).min()).astype("float32")
    df["nbars"] = g.cumcount() + 1

    hi = df["hi250"].replace(0, np.nan)
    df["pct_hi"] = (df["close"] / hi).astype("float32")
    df["range_x"] = (hi / df["lo250"].replace(0, np.nan)).astype("float32")

    df["eligible"] = (
        (df["close"] >= config.MIN_PRICE)
        & (df["adv20"] >= config.MIN_DOLLAR_VOL)
        & (df["trades20"] >= config.MIN_TRADES_20D)
        & (df["nbars"] >= config.MIN_BARS)
        & (df["pct_hi"] <= config.MAX_PCT_OF_250D_HIGH)
        & (df["range_x"] >= config.MIN_250D_RANGE_X)
    ).fillna(False)

    return df[["ticker", "date", "eligible"]]


# ====================================================================== the run
def run(dates: list[str], elig: pd.DataFrame, verbose: bool = True) -> pd.DataFrame:
    """Screen every eligible ticker on every test date. Entry is the NEXT open."""
    by_date: dict[str, set[str]] = {
        d: set(g["ticker"].astype(str))
        for d, g in elig[elig["eligible"]].groupby("date", observed=True)
        if d in set(dates)
    }
    wanted = sorted({t for s in by_date.values() for t in s})
    if verbose:
        print(f"  {len(dates)} test date(s), {len(wanted):,} distinct eligible "
              f"ticker(s), "
              f"{np.mean([len(by_date.get(d, ())) for d in dates]):.0f} avg per date")

    lookback = config.IND_WARMUP + config.STRUCT_WIN + 5
    start = calendar_us.session_offset(calendar_us.all_sessions(),
                                       min(dates), lookback)
    frames = dataset.panel(wanted, "1d", start=start,
                           end=calendar_us.last_closed_session())
    # Positional lookup per ticker: date -> row index, so each test date is O(1).
    pos = {t: {str(d): i for i, d in enumerate(g["date"].astype(str))}
           for t, g in frames.items()}

    rows: list[dict] = []
    t0 = time.time()
    for di, d in enumerate(dates, 1):
        for t in by_date.get(d, ()):
            g = frames.get(t)
            if g is None:
                continue
            a = pos[t].get(d)
            if a is None or a + 1 >= len(g):      # need a next session to enter on
                continue
            m = screen.screen_one(g, a)
            if not m.get("passed"):
                continue

            o = g["open"].to_numpy(float)
            h = g["high"].to_numpy(float)
            lw = g["low"].to_numpy(float)
            c = g["close"].to_numpy(float)

            entry = float(o[a + 1])
            if not np.isfinite(entry) or entry <= 0:
                continue

            end = min(a + 1 + MAX_HOLD, len(g))
            m.update({
                "test_date": d,
                "entry_date": str(g["date"].iloc[a + 1]),
                "signal_close": float(c[a]),
                "entry": entry,
                # The cost of not being able to trade the signal bar's close.
                "gap_pct": entry / float(c[a]) - 1.0,
                "atr_entry": float(m.get("atr_at_low") or 0.05 * entry),
                "path_h": h[a + 1:end].tolist(),
                "path_l": lw[a + 1:end].tolist(),
                "path_c": c[a + 1:end].tolist(),
            })
            rows.append(m)
        if verbose and (di % 20 == 0 or di == len(dates)):
            print(f"    {di}/{len(dates)} dates, {len(rows)} flag(s), "
                  f"{time.time() - t0:.0f}s")

    out = pd.DataFrame(rows)
    return classify.apply(out) if not out.empty else out


# =================================================================== exit rules
def _hold(path_c, n):
    p = path_c[:n]
    return (p[-1], len(p)) if p else (np.nan, 0)


def _stop_then_hold(path_h, path_l, path_c, entry, atr, k_stop, n):
    """Exit at the stop if touched intrabar, else the close of session n."""
    stop = entry - k_stop * atr
    for i in range(min(n, len(path_c))):
        if path_l[i] <= stop:
            return stop, i + 1
    return _hold(path_c, n)


def _bracket(path_h, path_l, path_c, entry, atr, k_stop, k_tgt, n):
    """Stop and target both live. Stop wins on a bar that touches both -- the
    pessimistic assumption, since intrabar order is unknowable from daily bars."""
    stop, tgt = entry - k_stop * atr, entry + k_tgt * atr
    for i in range(min(n, len(path_c))):
        if path_l[i] <= stop:
            return stop, i + 1
        if path_h[i] >= tgt:
            return tgt, i + 1
    return _hold(path_c, n)


def _trail(path_h, path_l, path_c, entry, atr, k, n):
    """Trailing stop k*ATR below the highest close so far."""
    peak = entry
    for i in range(min(n, len(path_c))):
        if path_l[i] <= peak - k * atr:
            return peak - k * atr, i + 1
        peak = max(peak, path_c[i])
    return _hold(path_c, n)


def _below_level(path_c, entry, level, n):
    """Exit on the first close below the support level that produced the signal."""
    for i in range(min(n, len(path_c))):
        if level and path_c[i] < level * (1 - config.BREAK_TOL):
            return path_c[i], i + 1
    return _hold(path_c, n)


EXITS = {
    "hold_3": lambda r: _hold(r["path_c"], 3),
    "hold_5": lambda r: _hold(r["path_c"], 5),
    "hold_10": lambda r: _hold(r["path_c"], 10),
    "hold_20": lambda r: _hold(r["path_c"], 20),
    "stop1_hold20": lambda r: _stop_then_hold(r["path_h"], r["path_l"], r["path_c"],
                                              r["entry"], r["atr_entry"], 1.0, 20),
    "stop2_hold20": lambda r: _stop_then_hold(r["path_h"], r["path_l"], r["path_c"],
                                              r["entry"], r["atr_entry"], 2.0, 20),
    "bracket_1x2": lambda r: _bracket(r["path_h"], r["path_l"], r["path_c"],
                                      r["entry"], r["atr_entry"], 1.0, 2.0, 20),
    "bracket_1x1.5": lambda r: _bracket(r["path_h"], r["path_l"], r["path_c"],
                                        r["entry"], r["atr_entry"], 1.0, 1.5, 20),
    "bracket_2x3": lambda r: _bracket(r["path_h"], r["path_l"], r["path_c"],
                                      r["entry"], r["atr_entry"], 2.0, 3.0, 20),
    # A 1-ATR trail is the tightest of the three, and the one the log singles
    # out: MFE +9.2% against MAE -11.0% says the favourable excursion exists but
    # is given back, so the question is how tight a stop can be before it starts
    # cutting winners rather than losers.
    "trail_1": lambda r: _trail(r["path_h"], r["path_l"], r["path_c"],
                                r["entry"], r["atr_entry"], 1.0, 20),
    "trail_1.5": lambda r: _trail(r["path_h"], r["path_l"], r["path_c"],
                                  r["entry"], r["atr_entry"], 1.5, 20),
    "trail_2": lambda r: _trail(r["path_h"], r["path_l"], r["path_c"],
                                r["entry"], r["atr_entry"], 2.0, 20),
    "below_level": lambda r: _below_level(r["path_c"], r["entry"],
                                          r.get("level"), 20),
}


def apply_exits(flags: pd.DataFrame) -> pd.DataFrame:
    """Return one row per (flag, exit rule) with the realised return."""
    if flags is None or flags.empty:
        return pd.DataFrame()
    recs = []
    for r in flags.to_dict("records"):
        if not r.get("path_c"):
            continue
        for name, fn in EXITS.items():
            try:
                px, held = fn(r)
            except Exception:                              # noqa: BLE001
                continue
            if not np.isfinite(px) or held == 0:
                continue
            recs.append({
                "exit": name, "ticker": r["ticker"], "test_date": r["test_date"],
                "bucket": r.get("bucket"), "stage": r.get("stage"),
                "price_tier": r.get("price_tier"), "age_band": r.get("age_band"),
                "support_grade": r.get("support_grade"),
                "ret": px / r["entry"] - 1.0, "held": held,
            })
    return pd.DataFrame(recs)


def summarise_exits(ex: pd.DataFrame) -> pd.DataFrame:
    if ex is None or ex.empty:
        return pd.DataFrame()
    g = ex.groupby("exit", observed=True)
    t = pd.DataFrame({
        "n": g.size(),
        "mean": g["ret"].mean(),
        "median": g["ret"].median(),
        "win": g["ret"].apply(lambda s: float((s > 0).mean())),
        "med_held": g["held"].median(),
        "p25": g["ret"].quantile(0.25),
        "p75": g["ret"].quantile(0.75),
    })
    # Expectancy per trade is the number that decides whether a rule is usable.
    t["expectancy"] = t["mean"]
    return t.sort_values("expectancy", ascending=False)


# =================================================================== base rates
def build_base_rates(flags: pd.DataFrame, ex: pd.DataFrame,
                     best_exit: str) -> pd.DataFrame:
    """Historical outcome per tag combination, for display on the daily list.

    This is what makes the daily report self-evidencing: each card can say what
    setups sharing its characteristics actually did, instead of leaving you to
    guess.
    """
    if ex is None or ex.empty:
        return pd.DataFrame()
    e = ex[ex["exit"] == best_exit]
    out = []
    for keys in (["bucket"], ["stage"], ["price_tier"], ["age_band"],
                 ["support_grade"], ["bucket", "stage"], ["stage", "price_tier"]):
        g = e.groupby(keys, observed=True)
        for k, chunk in g:
            if len(chunk) < 8:                 # too thin to quote
                continue
            out.append({
                "keys": "|".join(keys),
                "value": "|".join(k) if isinstance(k, tuple) else str(k),
                "n": len(chunk),
                "mean_ret": float(chunk["ret"].mean()),
                "median_ret": float(chunk["ret"].median()),
                "win_rate": float((chunk["ret"] > 0).mean()),
                "exit": best_exit,
            })
    df = pd.DataFrame(out)
    if not df.empty:
        tmp = BASE_RATE_FILE.with_suffix(".parquet.tmp")
        df.to_parquet(tmp, compression=config.COMPRESSION, index=False)
        tmp.replace(BASE_RATE_FILE)
    return df


def load_base_rates() -> pd.DataFrame:
    if BASE_RATE_FILE.exists():
        return pd.read_parquet(BASE_RATE_FILE)
    return pd.DataFrame()


# ================================================================ survivorship
def survivorship(verbose: bool = True) -> dict:
    """Size the hole left by names that are not in today's asset list.

    Cannot be measured directly -- the missing names are missing. But tickers whose
    stored history STOPS well before the last session are the ones that died while
    still listed, and they give a floor on the rate.
    """
    lcs = calendar_us.last_closed_session()
    df = store.read("1d", columns=["ticker", "date"])
    last = df.groupby("ticker", observed=True)["date"].max()
    sessions = calendar_us.all_sessions()
    cut_30 = calendar_us.session_offset(sessions, lcs, 30)
    cut_250 = calendar_us.session_offset(sessions, lcs, 250)

    stale_30 = int((last < cut_30).sum())
    stale_250 = int((last < cut_250).sum())
    total = int(len(last))
    info = {
        "tickers_in_store": total,
        "stopped_trading_30d_ago_or_more": stale_30,
        "stopped_trading_250d_ago_or_more": stale_250,
        "observable_attrition_pct": round(100 * stale_30 / max(total, 1), 2),
    }
    if verbose:
        print("\nSURVIVORSHIP")
        for k, v in info.items():
            print(f"  {k:36} {v}")
        print("  These are names that died WHILE STILL in Alpaca's list. Names")
        print("  removed from the list entirely were never fetched and cannot be")
        print("  counted here, so real attrition is higher. US delistings run")
        print("  ~4-6%/yr overall and materially higher for micro-caps, which is")
        print("  most of this universe. Every return below is therefore optimistic.")
    return info


# ======================================================================== main
def main() -> int:
    ap = argparse.ArgumentParser(description="Full historical backtest.")
    ap.add_argument("--start", default="2023-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--every", type=int, default=2, help="test every Nth session")
    ap.add_argument("--exits", action="store_true", help="compare exit rules")
    ap.add_argument("--survivorship", action="store_true")
    ap.add_argument("--base-rates", action="store_true",
                    help="write data/_base_rates.parquet for the daily report")
    ap.add_argument("--out", default=None, help="parquet path for the raw flags")
    a = ap.parse_args()

    config.dirs()
    t0 = time.time()

    if a.survivorship:
        survivorship()
        return 0

    end = a.end or calendar_us.last_closed_session()
    start = a.start if len(a.start) == 10 else f"{a.start}-01"
    dates = calendar_us.sessions_between(start, end)[::max(a.every, 1)]
    # Leave room for the forward path, or the last flags have nothing to measure.
    dates = [d for d in dates
             if len(calendar_us.sessions_between(d, end)) > MAX_HOLD]
    print(f"backtest {start} -> {end}, every {a.every} session(s) "
          f"-> {len(dates)} test dates")

    print("  building per-date eligibility (rolling, no look-ahead)...")
    elig = eligibility()
    print(f"    {len(elig):,} (ticker, date) rows, "
          f"{int(elig['eligible'].sum()):,} eligible")

    flags = run(dates, elig)
    if flags.empty:
        print("  no flags")
        return 0

    n_dates = flags["test_date"].nunique()
    print(f"\n  {len(flags):,} flag(s) over {n_dates} date(s) "
          f"({len(flags) / max(n_dates, 1):.1f} per date)")
    print(f"  median entry gap vs signal close: "
          f"{flags['gap_pct'].median():+.2%}   "
          f"(this is the cost of not being able to trade the signal bar)")

    if a.out:
        keep = [c for c in flags.columns
                if c not in ("path_h", "path_l", "path_c", "failed_gates",
                             "prior_visits", "touch_dates")]
        flags[keep].to_parquet(a.out, compression=config.COMPRESSION, index=False)
        print(f"  wrote {a.out}")

    ex = apply_exits(flags)
    if ex.empty:
        print("  no exits computed")
        return 0

    t = summarise_exits(ex)
    print("\nEXIT RULES (return per trade, entry at next open)")
    print(t.round(4).to_string())

    # PERSIST IT. This comparison costs ~33 minutes and used to exist only as
    # stdout: the one overnight run of it scrolled its own headline table out of
    # the log and left nothing on disk, so the most expensive analysis here
    # produced no artifact anyone could read afterwards.
    try:
        out_t = t.reset_index()
        out_t.columns = ["exit"] + list(out_t.columns[1:])
        out_t["measured_at"] = datetime.now().isoformat(timespec="seconds")
        out_t["n_flags"] = int(len(flags))
        tmp = config.EXIT_RULES_FILE.with_suffix(".parquet.tmp")
        out_t.to_parquet(tmp, compression=config.COMPRESSION,
                         compression_level=config.COMPRESSION_LEVEL, index=False)
        store.atomic_replace(tmp, config.EXIT_RULES_FILE)
        print(f"  wrote {config.EXIT_RULES_FILE.name} "
              f"({len(out_t)} rule(s))")
    except Exception as exc:                                     # noqa: BLE001
        print(f"  ! could not persist the exit table ({exc!r})"[:130])

    best = str(t.index[0])
    print(f"\n  best by expectancy: {best}  "
          f"mean {t.loc[best, 'mean']:+.2%}  median {t.loc[best, 'median']:+.2%}  "
          f"win {t.loc[best, 'win']:.0%}")
    if t.loc[best, "mean"] <= 0:
        print("  !! NO exit rule has positive expectancy. The pattern is not")
        print("     tradeable as specified -- this is the answer, not a bug.")

    for by in ("bucket", "stage", "price_tier", "age_band", "support_grade"):
        sub = ex[ex["exit"] == best]
        g = sub.groupby(by, observed=True)
        tt = pd.DataFrame({"n": g.size(), "mean": g["ret"].mean(),
                           "median": g["ret"].median(),
                           "win": g["ret"].apply(lambda s: float((s > 0).mean()))})
        print(f"\n{best} by {by}:")
        print(tt.round(4).sort_values("mean", ascending=False).to_string())

    if a.base_rates:
        br = build_base_rates(flags, ex, best)
        print(f"\n  wrote {BASE_RATE_FILE.name}: {len(br)} base-rate row(s)")

    survivorship()
    print(f"\ndone in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
