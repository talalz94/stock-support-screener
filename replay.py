"""
Historical replay and look-ahead leak detection.

    python replay.py --leaktest                 the assertions -- run these first
    python replay.py --start 2025-01 --sample 250
    python replay.py --catchup 2026-07-29 2026-07-30

WHY THE LEAK TESTS MATTER MORE THAN THE REPLAY ITSELF

`scipy.signal.find_peaks` is not causal. A peak's prominence is
min(left drop, right drop), so computed over the full series a peak at asof-30 can
be flagged ONLY BECAUSE of a plunge at asof+5. Filtering pivot indices to <= asof
does NOT fix this -- the prominence values themselves are contaminated. The pivot
set must be recomputed from a TRUNCATED series at every asof, which is what
`screen_one`'s slice-once-at-the-top structure guarantees.

The failure mode is nasty because it is invisible: a leaking screener produces a
beautiful backtest and mediocre live results, and nothing errors. So the two tests
below are the real deliverable of this module:

  truncation equivalence   screen_one(df, a) must equal screen_one(df[:a+1], a)
  future scramble          replace every bar after asof with noise; the output
                           must not move by even one field

Test 2 is the one that catches the prominence leak specifically. Test 1 catches
almost everything else (negative indexing into the parent frame, warmup that
depends on total history length, a metric reading close[-1] instead of close[a]).

SURVIVORSHIP: Alpaca's asset list is CURRENT-ONLY, so a historical replay silently
omits names that have since delisted -- and the ones that delist are
disproportionately the failed bounces. Replay results are therefore an UPPER
BOUND, not an estimate. Forward tracking in state.py has no such bias, which is
why both exist.
"""

from __future__ import annotations

import argparse
import sys
import time

import numpy as np
import pandas as pd

import calendar_us
import classify
import config
import dataset
import screen

# Fields excluded from equality checks: they are provenance, not signal.
_IGNORE = {"asof_date"}


def _norm(v):
    """Normalise a metric value so NaN == NaN and 1 == 1.0."""
    if isinstance(v, (list, tuple)):
        return tuple(_norm(x) for x in v)
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, np.integer)):
        return float(v)
    if isinstance(v, (float, np.floating)):
        f = float(v)
        return "NaN" if f != f else round(f, 9)
    if v is None:
        return None
    return str(v)


def diff_metrics(a: dict, b: dict) -> list[str]:
    """Field-by-field differences between two screen_one outputs."""
    out = []
    for k in sorted(set(a) | set(b)):
        if k in _IGNORE:
            continue
        va, vb = _norm(a.get(k)), _norm(b.get(k))
        if va != vb:
            out.append(f"{k}: {va!r} != {vb!r}")
    return out


def leaktest(tickers: list[str] | None = None, n_dates: int = 12,
             seed: int = 7, verbose: bool = True) -> int:
    """Run both look-ahead assertions. Returns the number of failures."""
    rng = np.random.default_rng(seed)
    asof_date = calendar_us.last_closed_session()
    start = calendar_us.session_offset(
        calendar_us.all_sessions(), asof_date,
        config.IND_WARMUP + config.STRUCT_WIN + 5)

    if not tickers:
        # A deliberately mixed sample: the calibration anchor, a large cap, a
        # cheap volatile name, plus a few flagged names.
        tickers = ["RDW", "ORCL", "POET", "LAC", "TYGO", "NVTS"]
    tickers = [t for t in tickers]

    frames = dataset.panel(tickers, "1d", start=start, end=asof_date)
    if not frames:
        print("  no history available; cannot run leak tests")
        return 1

    fails = 0
    checked = 0

    for t, df in frames.items():
        n = len(df)
        lo = max(config.MIN_BARS, config.IND_WARMUP + 60)
        if n <= lo + 10:
            if verbose:
                print(f"  {t}: only {n} bars, skipping")
            continue
        picks = sorted(rng.choice(np.arange(lo, n), size=min(n_dates, n - lo),
                                  replace=False).tolist())

        for a in picks:
            checked += 1
            full = screen.screen_one(df, a)

            # --- TEST 1: truncation equivalence -------------------------------
            trunc = screen.screen_one(df.iloc[:a + 1].reset_index(drop=True), a)
            d1 = diff_metrics(full, trunc)
            if d1:
                fails += 1
                print(f"  LEAK [truncation] {t} @ idx {a} ({df['date'].iloc[a]})")
                for line in d1[:6]:
                    print(f"      {line}")

            # --- TEST 2: future scramble --------------------------------------
            # If any output depends on a bar after `asof`, replacing those bars
            # with noise must change it. This is the test that catches
            # find_peaks' prominence borrowing future bars.
            k = len(df) - (a + 1)
            if k > 0:
                # Build whole columns and assign them at once, preserving the
                # store's float32/int64 dtypes. Slice-assigning float64 into a
                # float32 column is deprecated in pandas 2.x and buries the test
                # output in warnings.
                d2f = df.copy()
                mult = rng.uniform(0.4, 2.5, size=k)
                cols = {}
                for col in ("open", "high", "low", "close"):
                    v = df[col].to_numpy(dtype="float64").copy()
                    v[a + 1:] *= mult
                    cols[col] = v
                # Keep high/low coherent so the scramble stays a plausible series
                # and any difference is attributable to look-ahead, not to bars
                # that could never exist.
                hi_ = np.maximum.reduce([cols["open"], cols["close"], cols["high"]])
                lo_ = np.minimum.reduce([cols["open"], cols["close"], cols["low"]])
                cols["high"], cols["low"] = hi_, lo_
                for col, v in cols.items():
                    d2f[col] = v.astype(df[col].dtype)
                vol = df["volume"].to_numpy(dtype="float64").copy()
                vol[a + 1:] *= rng.uniform(0.2, 5.0, size=k)
                d2f["volume"] = vol.astype(df["volume"].dtype)

                scram = screen.screen_one(d2f, a)
                d2 = diff_metrics(full, scram)
                if d2:
                    fails += 1
                    print(f"  LEAK [future scramble] {t} @ idx {a} "
                          f"({df['date'].iloc[a]}) -- output depends on FUTURE bars")
                    for line in d2[:6]:
                        print(f"      {line}")

    if verbose:
        print(f"\n  checked {checked} (ticker, asof) pair(s) across "
              f"{len(frames)} ticker(s)")
        print("  " + ("bounce leak tests PASSED" if not fails
                      else f"{fails} LEAK(S) DETECTED"))

    fails += sentiment_leaktest(verbose=verbose)

    if verbose:
        print("\n  " + ("ALL LEAK TESTS PASSED -- no look-ahead detected" if not fails
                        else f"{fails} LEAK(S) DETECTED"))
    return fails


def sentiment_leaktest(n_dates: int = 6, seed: int = 11,
                       verbose: bool = True) -> int:
    """Look-ahead assertions for the sentiment score module.

    Same shape as the bounce tests above, because the failure mode is identical
    and equally silent: a sentiment series that quietly includes tomorrow's
    headlines produces a beautiful backtest and nothing errors.

      1. SESSION ATTRIBUTION -- no article timestamped at or after session S's
         close may be attributed to S. This is THE one. An article stamped 21:00Z
         is 17:00 ET, an hour after the close, and treating it as known at that
         close is a full day of hindsight on every after-hours story.
      2. TRUNCATION EQUIVALENCE -- compute(asof) over the full store must equal
         compute(asof) over a store truncated at asof.
      3. FUTURE SCRAMBLE -- corrupt every article after asof; no metric may move.
    """
    import news
    import scores as _scores
    import sentiment as _senti

    if not news.months():
        if verbose:
            print("\n  sentiment leak tests: news store empty, skipped")
        return 0

    rng = np.random.default_rng(seed)
    fails = 0

    # --- TEST 1: session attribution ---------------------------------------
    art = news.read(columns=["ts", "session"])
    art = art[art["session"].notna()]
    if not art.empty:
        cal = calendar_us.refresh()
        closes = {}
        for _, r in cal.iterrows():
            hh, mm = 16, 0
            try:
                hh, mm = (int(x) for x in str(r.get("close") or "16:00").split(":")[:2])
            except ValueError:
                pass
            closes[str(r["date"])] = (pd.Timestamp(r["date"])
                                      + pd.Timedelta(hours=hh, minutes=mm))
        et = (pd.to_datetime(art["ts"], utc=True)
              .dt.tz_convert("America/New_York").dt.tz_localize(None))
        bound = art["session"].astype(str).map(closes)
        bad = int((et >= bound).sum())
        if bad:
            fails += 1
            ex = art[(et >= bound).to_numpy()].head(3)
            print(f"  LEAK [session attribution] {bad:,} article(s) attributed to a "
                  "session whose close had ALREADY printed")
            for _, r in ex.iterrows():
                print(f"      ts={r['ts']} -> session {r['session']}")
        elif verbose:
            print(f"\n  [session attribution] {len(art):,} article(s), none "
                  "attributed at or after their session's close")

    # --- TESTS 2 and 3: truncation and scramble ----------------------------
    _scores.load_all()
    mod = _scores.get("sentiment")
    stored = sorted(news.stored_sessions())
    if len(stored) < config.SENTI_WINDOWS[1] + 5:
        if verbose:
            print("  [truncation/scramble] not enough stored sessions, skipped")
        return fails

    pool = stored[config.SENTI_WINDOWS[1]:]
    picks = sorted(rng.choice(pool, size=min(n_dates, len(pool)),
                              replace=False).tolist())
    tick = ["RDW", "ORCL", "NVTS", "JOBY", "POET", "AAPL", "TSLA", "AMD"]

    real_read = news.read
    checked = 0
    for asof in picks:
        base = mod.compute(asof, tick, allow_partial=True)
        if base.empty:
            continue
        checked += 1

        def truncated(*a, **kw):
            df = real_read(*a, **kw)
            return df[df["session"].notna() & (df["session"].astype(str) <= asof)]

        def scrambled(*a, **kw):
            df = real_read(*a, **kw).copy()
            fut = df["session"].isna() | (df["session"].astype(str) > asof)
            if fut.any():
                # Replace future headlines with text that would score very
                # differently if it were ever read.
                df.loc[fut, "headline"] = "Company Files For Chapter 11 Bankruptcy"
                df.loc[fut, "summary"] = "going concern doubt, delisting, fraud"
            return df

        for name, patch in (("truncation", truncated), ("future scramble", scrambled)):
            news.read = patch
            try:
                got = mod.compute(asof, tick, allow_partial=True)
            finally:
                news.read = real_read
            d = _diff_score_rows(base, got)
            if d:
                fails += 1
                print(f"  LEAK [{name}] sentiment @ {asof}")
                for line in d[:6]:
                    print(f"      {line}")

    if verbose:
        print(f"  [truncation/scramble] checked {checked} asof date(s) "
              f"x {len(tick)} ticker(s)")
    return fails


def _diff_score_rows(a: pd.DataFrame, b: pd.DataFrame) -> list[str]:
    """Differences between two tidy score frames, as readable lines."""
    ka = a.set_index(["ticker", "metric"])
    kb = b.set_index(["ticker", "metric"])
    out = []
    for k in sorted(set(ka.index) | set(kb.index)):
        if k not in ka.index:
            out.append(f"{k[0]}.{k[1]}: absent -> {kb.loc[k, 'value']}")
            continue
        if k not in kb.index:
            out.append(f"{k[0]}.{k[1]}: {ka.loc[k, 'value']} -> absent")
            continue
        va, vb = ka.loc[k, "value"], kb.loc[k, "value"]
        la, lb = ka.loc[k, "label"], kb.loc[k, "label"]
        if pd.notna(va) and pd.notna(vb):
            if abs(float(va) - float(vb)) > 1e-9:
                out.append(f"{k[0]}.{k[1]}: {va} -> {vb}")
        elif pd.isna(va) != pd.isna(vb):
            out.append(f"{k[0]}.{k[1]}: {va} -> {vb}")
        if (la or "") != (lb or ""):
            out.append(f"{k[0]}.{k[1]} label: {la!r} -> {lb!r}")
    return out


def replay(dates: list[str], tickers: list[str] | None = None,
           sample: int = 0, verbose: bool = True) -> pd.DataFrame:
    """Screen many historical sessions. Returns one row per (date, passing ticker).

    Loads each ticker's history ONCE and re-slices per date, which is what makes a
    multi-hundred-date replay affordable. Pivots are still recomputed per asof
    inside screen_one -- that is not an optimisation target, it is the correctness
    requirement.
    """
    sessions = calendar_us.all_sessions()
    dates = [d for d in dates if d in sessions]
    if not dates:
        print("  no valid sessions to replay")
        return pd.DataFrame()

    if not tickers:
        import bars
        ps = bars.load_panel_stats()
        # Prefilter once, on the LATEST stats, purely to bound the work. This does
        # introduce mild selection bias (today's liquid names), which is noted in
        # the module docstring alongside the survivorship caveat.
        tickers, _rej = screen.prefilter(ps, min(dates))
        if sample and len(tickers) > sample:
            rng = np.random.default_rng(11)
            tickers = sorted(rng.choice(tickers, size=sample, replace=False).tolist())

    lookback = config.IND_WARMUP + config.STRUCT_WIN + 5
    start = calendar_us.session_offset(sessions, min(dates), lookback)
    frames = dataset.panel(tickers, "1d", start=start, end=max(dates))
    if verbose:
        print(f"  replaying {len(dates)} session(s) x {len(frames)} ticker(s)")

    t0 = time.time()
    rows: list[dict] = []
    for di, d in enumerate(dates, 1):
        for t, df in frames.items():
            pos = df.index[df["date"].astype(str) <= d]
            if len(pos) < config.MIN_BARS:
                continue
            a = int(pos[-1])
            if str(df["date"].iloc[a]) != d:
                continue                     # ticker did not trade that session
            m = screen.screen_one(df, a)
            if m.get("passed"):
                m["replay_date"] = d
                rows.append(m)
        if verbose and (di % 5 == 0 or di == len(dates)):
            print(f"    {di}/{len(dates)} sessions, {len(rows)} flag(s), "
                  f"{time.time() - t0:.0f}s")

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return classify.apply(out)


def forward_returns(flags: pd.DataFrame, horizons=(5, 10, 20)) -> pd.DataFrame:
    """Attach forward returns to replayed flags, from bars after the flag date."""
    if flags is None or flags.empty:
        return flags
    tickers = sorted(flags["ticker"].astype(str).unique())
    end = calendar_us.last_closed_session()
    frames = dataset.panel(tickers, "1d",
                           start=str(flags["replay_date"].min()), end=end)
    out = flags.copy()
    for h in horizons:
        out[f"fwd_{h}d"] = np.nan
    out["fwd_mae"] = np.nan
    out["fwd_mfe"] = np.nan

    for i, r in out.iterrows():
        g = frames.get(str(r["ticker"]))
        if g is None or g.empty:
            continue
        pos = g.index[g["date"].astype(str) == str(r["replay_date"])]
        if len(pos) == 0:
            continue
        a = int(pos[0])
        base = float(g["close"].iloc[a])
        if base <= 0:
            continue
        fwd = g["close"].to_numpy(float)[a + 1:a + 1 + max(horizons)]
        if fwd.size == 0:
            continue
        for h in horizons:
            if fwd.size >= h:
                out.at[i, f"fwd_{h}d"] = fwd[h - 1] / base - 1.0
        out.at[i, "fwd_mfe"] = float(np.nanmax(fwd)) / base - 1.0
        out.at[i, "fwd_mae"] = float(np.nanmin(fwd)) / base - 1.0
    return out


def summarise(flags: pd.DataFrame, by: str = "bucket") -> pd.DataFrame:
    if flags is None or flags.empty or by not in flags.columns:
        return pd.DataFrame()
    cols = [c for c in ("fwd_5d", "fwd_10d", "fwd_20d", "fwd_mfe", "fwd_mae")
            if c in flags]
    g = flags.groupby(by, observed=True)
    t = pd.DataFrame({"n": g.size()})
    for c in cols:
        t[f"med_{c}"] = g[c].median()
    if "fwd_10d" in flags:
        t["win_10d"] = g["fwd_10d"].apply(lambda s: float((s > 0).mean()))
    return t.sort_values("n", ascending=False)


def catchup(dates: list[str], verbose: bool = True) -> int:
    """Backfill flag state for sessions that were missed (laptop asleep).

    Uses the ALREADY-LOCAL bars, so it costs no network. Its only purpose is to
    make `first_flagged` and `days_on_list` honest after a gap: without it a setup
    you were away for reads "NEW" when it has actually been holding for three
    sessions, and that counter is how you tell a fresh test from a stale one.
    """
    import report
    import state

    done = 0
    for d in sorted(dates):
        p = config.FLAGS / f"{d}.parquet"
        if p.exists():
            flags = pd.read_parquet(p)
        else:
            f, rej = screen.screen_universe(d, verbose=False)
            screen.write_outputs(f, rej, d)
            flags = f
        if flags is None or flags.empty:
            if verbose:
                print(f"    {d}: no flags")
            continue
        tagged = classify.apply(flags)
        state.update(tagged, d)
        done += 1
        if verbose:
            print(f"    {d}: {len(flags)} flag(s) reconciled")
    return done


def main() -> int:
    config.safe_console()
    ap = argparse.ArgumentParser(description="Historical replay and leak tests.")
    ap.add_argument("--leaktest", action="store_true",
                    help="run the look-ahead assertions (do this first)")
    ap.add_argument("--only", nargs="*", metavar="SYM")
    ap.add_argument("--dates", type=int, default=12,
                    help="asof points per ticker for the leak test")
    ap.add_argument("--start", default=None, help="replay from YYYY-MM or YYYY-MM-DD")
    ap.add_argument("--end", default=None)
    ap.add_argument("--every", type=int, default=5, help="replay every Nth session")
    ap.add_argument("--sample", type=int, default=250, help="cap tickers replayed")
    ap.add_argument("--catchup", nargs="*", metavar="DATE")
    a = ap.parse_args()

    config.dirs()

    if a.leaktest:
        print("look-ahead leak tests")
        return 1 if leaktest(a.only, a.dates) else 0

    if a.catchup is not None:
        print(f"catch-up reconcile: {a.catchup}")
        n = catchup(a.catchup)
        print(f"  {n} session(s) reconciled")
        return 0

    if not a.start:
        ap.error("pass --leaktest, --catchup, or --start")

    end = a.end or calendar_us.last_closed_session()
    start = a.start if len(a.start) == 10 else f"{a.start}-01"
    sess = calendar_us.sessions_between(start, end)[::max(a.every, 1)]
    print(f"replay {start} -> {end}, every {a.every} session(s) "
          f"({len(sess)} points)")

    flags = replay(sess, a.only, a.sample)
    if flags.empty:
        print("  no flags in the replay window")
        return 0

    flags = forward_returns(flags)
    print(f"\n  {len(flags)} flag(s) across {flags['replay_date'].nunique()} session(s)"
          f"   ({len(flags) / max(flags['replay_date'].nunique(), 1):.1f} per session)")

    for by in ("bucket", "stage", "support_grade", "age_band", "price_tier"):
        t = summarise(flags, by)
        if not t.empty:
            print(f"\nby {by}:")
            print(t.round(4).to_string())

    print("\n  CAVEAT: Alpaca's asset list is current-only, so names that have since"
          "\n  delisted are absent -- and those skew toward failed bounces. Treat"
          "\n  these numbers as an UPPER BOUND. state.py's forward tracking has no"
          "\n  such bias.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

