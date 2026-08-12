"""
Bar fetch orchestration.

    python bars.py --backfill            4 years, resumable, ~3-5 min
    python bars.py --backfill --limit 400   smoke test, ~40 s
    python bars.py --update              daily delta, ~20-30 s
    python bars.py --panel-stats         rebuild the prefilter cache from the store

Batching is what makes this affordable. The `symbols` param takes a
comma-separated list and `limit` caps TOTAL bars per page across all symbols, so
one request can carry 400 tickers. Measured: 400 symbols x 3y daily = 25 pages,
246,850 bars, 36 s. One symbol per request -- what the sibling project does --
would be 5,381 requests, a 32-minute floor at the rate limit. Batching is a
10-15x improvement and turns a full re-backfill from a chore into something you
can re-run whenever HISTORY_YEARS changes.

Failure handling, all three cases measured against the live API:

  400  a format-invalid symbol kills the WHOLE batch. Parse the offender out of
       the body, retire it, retry once. One bad ticker must never cost 400 good
       ones.
  403/504  transient under sustained batch pulling. Halve the batch size for the
       rest of the run and carry on.
  a well-formed but nonexistent symbol is silently omitted with a 200, so absence
  from the response is NOT an error.
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests

import alpaca
import calendar_us
import config
import store
import universe


def log(msg: str) -> None:
    from datetime import datetime
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} | {msg}"
    print(line, flush=True)
    config.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with config.LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _chunks(seq: list[str], n: int) -> list[list[str]]:
    return [seq[i:i + n] for i in range(0, len(seq), n)]


def _read_state() -> dict:
    import json
    if config.STATE_FILE.exists():
        try:
            return json.loads(config.STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _merge_state(**kv) -> None:
    import json
    st = _read_state()
    st.update(kv)
    config.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(st, indent=2), encoding="utf-8")
    tmp.replace(config.STATE_FILE)


class _Shrink:
    """Adaptive batch size, shared across worker threads."""

    def __init__(self, size: int):
        import threading
        self.size = size
        self.strikes = 0
        self.lock = threading.Lock()

    def penalise(self) -> int:
        with self.lock:
            self.strikes += 1
            if self.strikes >= 2 and self.size > 25:
                self.size = max(25, self.size // 2)
                self.strikes = 0
                log(f"  ! shrinking batch size to {self.size} after repeated 403/504")
            return self.size


def fetch_batch(symbols: list[str], start, end, interval: str,
                shrink: _Shrink | None = None) -> tuple[dict, list[str], int]:
    """One batch. Returns (payload, retired_symbols, http_errors)."""
    retired: list[str] = []
    errors = 0
    try:
        return alpaca.fetch_bars(symbols, start, end, interval), retired, errors
    except requests.HTTPError as exc:
        bad = [b for b in alpaca.bad_symbols_from_400(exc) if b in set(symbols)]
        if not bad:
            raise
        retired = bad
        keep = [s for s in symbols if s not in set(bad)]
        log(f"  ! 400 on batch; dropping {bad} and retrying {len(keep)} symbols")
        universe.mark_dead(bad, "alpaca called it invalid")
        if not keep:
            return {}, retired, 1
        return alpaca.fetch_bars(keep, start, end, interval), retired, 1
    except alpaca.SipWindowError:
        raise
    except Exception:
        errors = 1
        if shrink is not None:
            shrink.penalise()
        raise


def fetch_many(symbols: list[str], start, end, interval: str = "1d",
               batch: int | None = None, workers: int | None = None,
               label: str = "") -> tuple[pd.DataFrame, set[str], float]:
    """Fetch `symbols` in parallel batches. Returns (frame, found, error_rate)."""
    batch = batch or config.BATCH_BACKFILL
    workers = workers or config.FETCH_WORKERS
    shrink = _Shrink(batch)

    groups = _chunks(symbols, batch)
    frames: list[pd.DataFrame] = []
    found: set[str] = set()
    attempts = errors = 0
    t0 = time.time()

    def run(group: list[str]):
        return fetch_batch(group, start, end, interval, shrink)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(run, g): g for g in groups}
        done = 0
        for fut in as_completed(futs):
            g = futs[fut]
            attempts += 1
            try:
                payload, _retired, err = fut.result()
                errors += err
                df = store.bars_from_payload(payload, interval)
                if not df.empty:
                    frames.append(df)
                    found |= set(df["ticker"].unique())
            except alpaca.SipWindowError:
                raise
            except Exception as exc:
                errors += 1
                log(f"  !! batch of {len(g)} failed: {repr(exc)[:110]}")
            done += 1
            if done % 5 == 0 or done == len(groups):
                rows = sum(len(f) for f in frames)
                log(f"  {label}{done}/{len(groups)} batches, {len(found):,} tickers, "
                    f"{rows:,} bars, {time.time() - t0:.0f}s")

    out = (pd.concat(frames, ignore_index=True) if frames
           else pd.DataFrame(columns=store.SCHEMA))
    return out, found, (errors / max(attempts, 1))


# ------------------------------------------------------------------ panel stats
def build_panel_stats(interval: str = "1d") -> pd.DataFrame:
    """Per-ticker prefilter inputs, so the daily run never reads the full store.

    Without this the panel prefilter needs all ~5.4M rows decompressed (~20 s of
    zstd-9); with it, full history is loaded only for the ~400 names that survive
    the cheap filter. Single biggest saving in the daily run.
    """
    need = ["ticker", "date", "high", "low", "close", "volume", "trades"]
    df = store.read(interval, columns=need)
    if df.empty:
        return pd.DataFrame()

    df = df.sort_values(["ticker", "date"])
    g = df.groupby("ticker", sort=False, observed=True)

    tail250 = g.tail(250)
    g250 = tail250.groupby("ticker", sort=False, observed=True)
    tail20 = g.tail(20).copy()
    tail20["dv"] = tail20["close"] * tail20["volume"]
    g20 = tail20.groupby("ticker", sort=False, observed=True)

    last = g.tail(1).set_index("ticker")

    out = pd.DataFrame({
        "last_date": last["date"],
        "last_close": last["close"].astype("float32"),
        "n_bars": g.size(),
        "first_date": g["date"].min(),
        "hi250": g250["high"].max().astype("float32"),
        "lo250": g250["low"].min().astype("float32"),
        "dollar_vol_20": g20["dv"].median().astype("float64"),
        "trades_20": g20["trades"].median().astype("float64"),
    })
    out.index.name = "ticker"
    out = out.reset_index()
    out["ticker"] = out["ticker"].astype(str)
    out["range_250_x"] = (out["hi250"] / out["lo250"].replace(0, pd.NA)).astype("float32")
    out["pct_of_250d_high"] = (
        out["last_close"] / out["hi250"].replace(0, pd.NA)).astype("float32")

    tmp = config.PANEL_STATS_FILE.with_suffix(".parquet.tmp")
    out.to_parquet(tmp, compression=config.COMPRESSION,
                   compression_level=config.COMPRESSION_LEVEL, index=False)
    tmp.replace(config.PANEL_STATS_FILE)
    return out


def tradeable_universe(asof: str | None = None) -> list[str]:
    """Names clearing the price / liquidity floor. THE single definition.

    Was duplicated verbatim in senti_screen.universe() and fund_screen.universe().
    Two copies of a filter is two places for the screeners to silently disagree
    about what pool they drew from -- which would make their backtests
    non-comparable for a reason nobody would think to look for.
    """
    ps = load_panel_stats()
    if ps.empty:
        return []
    ok = ps[(ps["last_close"] >= config.MIN_PRICE)
            & (ps["dollar_vol_20"] >= config.MIN_DOLLAR_VOL)
            & (ps["trades_20"] >= config.MIN_TRADES_20D)]
    return sorted(ok["ticker"].astype(str))


def load_panel_stats() -> pd.DataFrame:
    if config.PANEL_STATS_FILE.exists():
        return pd.read_parquet(config.PANEL_STATS_FILE)
    return pd.DataFrame()


# ------------------------------------------------------------------ entrypoints
def _backfill_todo(tickers: list[str], start: str, lcs: str,
                   force: bool) -> tuple[list[str], str]:
    """Which tickers still need fetching, and why.

    Resumption must be per-TICKER, not per-session. Checking only "which session
    dates exist in the store" would mark the job complete after a --limit 400
    smoke test, silently leaving 4,979 tickers unfetched -- and a short history
    produces a WRONG pattern verdict rather than a visible error.

    A ticker counts as done when its newest stored bar is recent. Recency alone is
    trusted only if the previous backfill reached at least as far back as this one
    asks for; widening HISTORY_YEARS forces a full refetch.
    """
    if force:
        return tickers, "forced"

    ps = load_panel_stats()
    if ps.empty:
        return tickers, "no panel stats yet"

    prev = _read_state().get("backfill_start")
    if not prev or prev > start:
        return tickers, f"history widened ({prev or 'unknown'} -> {start})"

    sessions = calendar_us.all_sessions()
    # A halted or thinly-traded name may legitimately lag the latest session by a
    # few days; requiring an exact match would refetch it on every run forever.
    tol = calendar_us.session_offset(sessions, lcs, 3)
    done = set(ps.loc[ps["last_date"] >= tol, "ticker"].astype(str))
    todo = [t for t in tickers if t not in done]
    return todo, f"{len(done):,} already current"


def backfill(years: float | None = None, limit: int = 0,
             batch: int | None = None, force: bool = False) -> None:
    """Full history. Resumable per ticker, so an aborted run resumes cheaply."""
    years = years or config.HISTORY_YEARS
    tickers = universe.fetchable()
    if not tickers:
        log("universe empty -- run `python universe.py --refresh` first")
        return
    if limit:
        tickers = tickers[:limit]

    start = calendar_us.start_for_history(years)
    end = calendar_us.bars_end_ts()
    lcs = calendar_us.last_closed_session()

    todo, why = _backfill_todo(tickers, start, lcs, force)
    if not todo:
        log(f"backfill: already complete -- {why}")
        return

    log(f"backfill: {len(todo):,}/{len(tickers):,} tickers to fetch ({why}), "
        f"{years}y from {start}")

    df, found, err = fetch_many(todo, start, end, "1d", batch=batch)
    if df.empty:
        log("backfill: fetch returned nothing")
        return

    written = store.write(df, "1d", max_date=lcs, verbose=True)
    healthy = universe.is_healthy(found, tickers, err)
    universe.record_outcome(found, tickers, healthy)

    log(f"backfill: wrote {len(written)} month(s), {len(df):,} bars, "
        f"{len(found):,}/{len(tickers):,} tickers, error_rate={err:.3f}"
        f"{'' if healthy else '  (unhealthy; fail counters untouched)'}")

    dropped = store.prune("1d", years)
    if dropped:
        log(f"  pruned {len(dropped)} month(s) older than {years}y: {dropped}")

    log("  rebuilding panel stats...")
    ps = build_panel_stats()
    log(f"  panel stats: {len(ps):,} tickers")

    # Recorded only after the bars are on disk, so an aborted run never claims a
    # deeper history than it actually stored. Only advanced on a full-universe
    # run: a --limit smoke test must not mark the whole universe current.
    if not limit:
        _merge_state(backfill_start=start, backfill_years=years,
                     backfill_done=lcs)


def update(window: int = 5, force: bool = False) -> dict:
    """Daily delta. Calendar-driven, so a laptop off for a week just fills the gap.

    Also re-fetches a SPLIT_RECHECK_DAYS trailing window and overwrites, because
    split adjustment is retroactive: a split announced yesterday rewrites every
    prior bar, and stored bars would otherwise disagree with the API forever.
    """
    tickers = universe.fetchable()
    if not tickers:
        log("universe empty -- run `python universe.py --refresh` first")
        return {"ok": False, "rows": 0, "sessions": 0}

    lcs = calendar_us.last_closed_session()
    sessions = calendar_us.all_sessions()
    have = store.stored_dates("1d")

    lookback = max(window, config.SPLIT_RECHECK_DAYS)
    scan_from = calendar_us.session_offset(sessions, lcs, lookback)
    missing = calendar_us.missing_sessions(have, scan_from, lcs)

    # The split-recheck exists because split adjustment is retroactive, so stored
    # bars silently drift out of agreement with the API. It needs to happen once
    # per session, not once per invocation -- otherwise the at-logon catch-up
    # trigger costs ~45s of network every time you unlock the laptop, when the
    # whole point of that trigger is that it is free on days you were online.
    rechecked = _read_state().get("last_split_recheck")
    if not missing and not force and rechecked == lcs:
        log(f"update: up to date through {lcs}; split-recheck already done "
            "for this session -- nothing to do")
        return {"ok": True, "rows": 0, "sessions": 0, "tickers": 0,
                "healthy": True, "new_sessions": [], "skipped": True}

    if missing:
        log(f"update: {len(missing)} missing session(s) "
            f"{missing[0]} -> {missing[-1]}")
    else:
        log(f"update: no missing sessions; running the "
            f"{config.SPLIT_RECHECK_DAYS}-session split-recheck for {lcs}")

    start = calendar_us.session_offset(sessions, lcs, lookback)
    end = calendar_us.bars_end_ts()

    df, found, err = fetch_many(tickers, start, end, "1d",
                                batch=config.BATCH_DAILY, label="")
    if df.empty:
        log("update: fetch returned nothing")
        return {"ok": False, "rows": 0, "sessions": 0}

    before = store.stored_dates("1d")
    store.write(df, "1d", max_date=lcs, verbose=True)
    after = store.stored_dates("1d")
    new_sessions = sorted(after - before)

    healthy = universe.is_healthy(found, tickers, err)
    universe.record_outcome(found, tickers, healthy)

    log(f"update: {len(df):,} bars, {len(found):,}/{len(tickers):,} tickers, "
        f"{len(new_sessions)} new session(s){' ' + str(new_sessions) if new_sessions else ''}, "
        f"error_rate={err:.3f}"
        f"{'' if healthy else '  (unhealthy; fail counters untouched)'}")

    store.prune("1d")
    build_panel_stats()
    # Recorded only after the bars are on disk and the stats rebuilt, so a crash
    # mid-run leaves the recheck pending rather than falsely marked done.
    _merge_state(last_split_recheck=lcs)
    return {"ok": True, "rows": int(len(df)), "sessions": len(new_sessions),
            "tickers": len(found), "healthy": healthy, "new_sessions": new_sessions,
            "skipped": False}


def fetch_hourly(symbols: list[str], days: int | None = None) -> pd.DataFrame:
    """Stage-2 hourly bars for a shortlist only.

    Intraday pages are ~250 bars regardless of `limit` (measured: 20 symbols x
    60 d = 51 pages, 12,354 bars, 107 s -> 116 bars/s vs 6,857 for daily). 59x
    slower per bar, so hourly for the whole universe is ~6 hours. The two-stage
    split is not an optimisation, it is the only feasible design.
    """
    if not symbols:
        return pd.DataFrame(columns=store.SCHEMA)
    days = days or config.CONFIRM_DAYS
    sessions = calendar_us.all_sessions()
    lcs = calendar_us.last_closed_session()
    start = calendar_us.session_offset(sessions, lcs, days)
    end = calendar_us.bars_end_ts()

    df, _found, _err = fetch_many(symbols, start, end, "1h",
                                  batch=config.BATCH_HOURLY, label="1h ")
    if not df.empty:
        store.write(df, "1h", max_date=lcs)
        store.prune_1h()
    return df


def main() -> int:
    config.safe_console()
    ap = argparse.ArgumentParser(description="Fetch daily bars into the store.")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--panel-stats", action="store_true")
    ap.add_argument("--years", type=float, default=None)
    ap.add_argument("--limit", type=int, default=0, help="cap #tickers (smoke test)")
    ap.add_argument("--batch", type=int, default=None)
    ap.add_argument("--window", type=int, default=5)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    config.dirs()
    t0 = time.time()

    if a.backfill:
        backfill(a.years, a.limit, a.batch, a.force)
    elif a.update:
        update(a.window, a.force)
    elif a.panel_stats:
        ps = build_panel_stats()
        log(f"panel stats rebuilt: {len(ps):,} tickers")
    else:
        ap.error("pass one of --backfill / --update / --panel-stats")

    log(f"done in {time.time() - t0:.0f}s | store {store.store_bytes() / 1e6:.0f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())

