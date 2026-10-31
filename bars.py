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

import numpy as np
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
                shrink: _Shrink | None = None,
                asof: str | None = None) -> tuple[dict, list[str], int]:
    """One batch. Returns (payload, retired_symbols, http_errors)."""
    retired: list[str] = []
    errors = 0
    try:
        return (alpaca.fetch_bars(symbols, start, end, interval, asof=asof),
                retired, errors)
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
        return alpaca.fetch_bars(keep, start, end, interval, asof=asof), retired, 1
    except alpaca.SipWindowError:
        raise
    except Exception:
        errors = 1
        if shrink is not None:
            shrink.penalise()
        raise


def fetch_many(symbols: list[str], start, end, interval: str = "1d",
               batch: int | None = None, workers: int | None = None,
               label: str = "", asof: str | None = None,
               ) -> tuple[pd.DataFrame, set[str], float]:
    """Fetch `symbols` in parallel batches. Returns (frame, found, error_rate).

    Every frame passes through `apply_joins`, so bars from before a declared
    listing change come back on today's share basis however they were asked for.
    """
    batch = batch or config.BATCH_BACKFILL
    workers = workers or config.FETCH_WORKERS
    shrink = _Shrink(batch)

    groups = _chunks(symbols, batch)
    frames: list[pd.DataFrame] = []
    found: set[str] = set()
    attempts = errors = 0
    t0 = time.time()

    def run(group: list[str]):
        return fetch_batch(group, start, end, interval, shrink, asof)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(run, g): g for g in groups}
        done = 0
        for fut in as_completed(futs):
            g = futs[fut]
            attempts += 1
            try:
                payload, _retired, err = fut.result()
                errors += err
                df = apply_joins(store.bars_from_payload(payload, interval))
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


# ------------------------------------------------------------------ history joins
PRICE_COLS = ("open", "high", "low", "close", "vwap")


def apply_joins(df: pd.DataFrame, joins: dict | None = None) -> pd.DataFrame:
    """Bars dated before a declared listing change, on today's share basis.

    AZN's ADSs were half an ordinary share each, and Alpaca's series switches
    from one to the other on 2026-02-02 without adjusting: +103% overnight.
    Prices before the join are multiplied by `factor` and volume divided by it,
    so the series is in ordinary shares throughout. A factor of 1.0 is a no-op.
    """
    joins = config.HISTORY_JOINS if joins is None else joins
    if df is None or df.empty:
        return df
    out = df
    for t, j in joins.items():
        f = float(j.get("factor", 1.0))
        if f == 1.0:
            continue
        m = ((out["ticker"].astype(str) == t)
             & (out["date"].astype(str) < str(j["join"])))
        if not m.any():
            continue
        if out is df:
            out = df.copy()
        for c in PRICE_COLS:
            if c in out.columns:
                out[c] = out[c].astype("float64")
                out.loc[m, c] = out.loc[m, c] * f
        out["volume"] = out["volume"].astype("float64")
        out.loc[m, "volume"] = (out.loc[m, "volume"] / f).round()
    return out


def _z_at(close: pd.Series, i: int) -> tuple[float, float]:
    """(log return into position i, that return in sds of the 60 before it)."""
    c = close.astype(float).to_numpy()
    r = np.diff(np.log(c))                     # r[k] is the move into c[k + 1]
    if i < 1 or i >= len(c):
        return float("nan"), float("nan")
    prior = r[max(0, i - 61):i - 1]
    sd = float(np.std(prior, ddof=1)) if len(prior) >= 20 else float("nan")
    ret = float(r[i - 1])
    return ret, (ret / sd if sd > 0 else float("nan"))


def join_check(src: pd.DataFrame, held: pd.DataFrame, sessions: list[str],
               max_z: float | None = None, max_gap: int | None = None) -> dict:
    """May `src` -- the source's series for ONE ticker -- extend `held` back?

    Every condition is a way a wrong join was actually seen on 2026-10-31:
      nothing earlier       a real new listing; the source starts where we do
      overlap disagrees     the source no longer means what we stored
      gap                   a reused symbol (FLY: Fly Leasing, then Firefly)
      step at the join      an unadjusted share-basis change (AZN, +103%)
      step in our first bars  a spin-off just after a rename (MSGK, -20.5%)
    `ok` is True only when none applies; the reason is always stated.
    """
    max_z = config.JOIN_MAX_Z if max_z is None else max_z
    max_gap = config.JOIN_MAX_GAP if max_gap is None else max_gap
    res = {"ok": False, "rows": 0}
    if held is None or held.empty:
        res["why"] = "nothing stored to join to"
        return res
    held = held.sort_values("date").reset_index(drop=True)
    src = src.sort_values("date").reset_index(drop=True)
    first = str(held["date"].iloc[0])
    pre = src[src["date"].astype(str) < first]
    res.update(first_held=first, rows=len(pre))
    if pre.empty:
        res["why"] = "source holds nothing earlier"
        return res
    res["source_first"] = str(pre["date"].iloc[0])

    common = pd.merge(src[["date", "close"]], held[["date", "close"]],
                      on="date", suffixes=("_s", "_h"))
    res["overlap"] = len(common)
    if len(common) < 5:
        res["why"] = f"only {len(common)} overlapping session(s) to compare"
        return res
    diff = float((common["close_s"].astype(float)
                  / common["close_h"].astype(float) - 1).abs().max())
    res["max_diff"] = diff
    if not diff <= 1e-4:
        res["why"] = f"source disagrees with stored bars (max |close diff| {diff:.2e})"
        return res

    pos = {s: k for k, s in enumerate(sessions)}
    last_pre = str(pre["date"].iloc[-1])
    if last_pre in pos and first in pos:
        gap = pos[first] - pos[last_pre] - 1
    else:
        gap = (pd.Timestamp(first) - pd.Timestamp(last_pre)).days
    res["gap"] = int(gap)
    if gap > max_gap:
        res["why"] = f"{gap} session(s) missing between {last_pre} and {first}"
        return res

    joined = pd.concat([pre[["date", "close"]], held[["date", "close"]]],
                       ignore_index=True)
    k = len(pre)
    ret, z = _z_at(joined["close"], k)
    res.update(ret=ret, z=z)
    if not np.isfinite(z):
        res["why"] = "too little earlier history to measure the join"
        return res
    if abs(z) > max_z:
        res["why"] = f"step at the join: {np.expm1(ret):+.1%} (z {z:+.1f})"
        return res
    window = [abs(_z_at(joined["close"], i)[1])
              for i in range(k + 1, min(k + 10, len(joined)))]
    wz = max([w for w in window if np.isfinite(w)], default=0.0)
    res["window_z"] = float(wz)
    if wz > max_z:
        res["why"] = f"a step inside our first bars (|z| {wz:.1f})"
        return res
    res.update(ok=True, why="joins cleanly")
    return res


def _ledger_add(recs: list[dict]) -> None:
    import json
    path = config.HISTORY_LEDGER
    old = []
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            old = []
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(old + recs, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def history(tickers: list[str] | None = None, write: bool = True,
            mode: str = "manual", rebuild: bool = True) -> list[dict]:
    """Extend stored series back through a listing change, when it joins cleanly.

        python bars.py --history               every config.HISTORY_JOINS entry
        python bars.py --history VMRK,SKYD     named tickers
        python bars.py --history AZN --dry-run measure, write nothing

    The source series (Alpaca, with the join's `asof` and `factor`) must agree
    with what we hold on the overlap and meet our first bar with no gap and no
    step (`join_check`); a declared join is ALSO checked at its own date. Only
    bars dated before our first bar are written, so nothing held is replaced.
    Each write is recorded in config.HISTORY_LEDGER with what was measured.
    """
    joins = config.HISTORY_JOINS
    tickers = [universe.normalize(t) for t in (tickers or list(joins))]
    if not tickers:
        return []
    # From the store's own floor, so a joined series starts where every other
    # name's does rather than at today's 10-year mark.
    ms = store.months("1d")
    start = min([calendar_us.start_for_history(config.HISTORY_YEARS)]
                + ([f"{ms[0]}-01"] if ms else []))
    end = calendar_us.bars_end_ts()
    lcs = calendar_us.last_closed_session()
    sessions = calendar_us.all_sessions()

    ps = load_panel_stats()
    firsts = (ps.set_index("ticker")["first_date"].reindex(tickers).dropna()
              if not ps.empty else pd.Series(dtype=str))
    lo = str(firsts.min()) if len(firsts) else start
    held_all = store.read("1d", start=lo, tickers=tickers)

    by_asof: dict = {}
    for t in tickers:
        by_asof.setdefault(joins.get(t, {}).get("asof"), []).append(t)
    frames = []
    for asof, names in by_asof.items():
        df, _found, _err = fetch_many(names, start, end, "1d",
                                      batch=config.BATCH_BACKFILL, asof=asof,
                                      label="history ")
        if not df.empty:
            frames.append(df)
    src_all = (pd.concat(frames, ignore_index=True) if frames
               else pd.DataFrame(columns=store.SCHEMA))

    out, to_write, ledger = [], [], []
    for t in tickers:
        src = src_all[src_all["ticker"].astype(str) == t]
        held = held_all[held_all["ticker"].astype(str) == t]
        res = join_check(src, held, sessions)
        res.update(ticker=t, mode=mode, declared=t in joins)
        j = joins.get(t)
        if res["ok"] and j:
            joined = pd.concat([src[src["date"] < res["first_held"]], held],
                               ignore_index=True).sort_values("date")
            d = joined["date"].astype(str).reset_index(drop=True)
            hit = d[d == str(j["join"])].index
            if len(hit):
                r, z = _z_at(joined["close"].reset_index(drop=True), int(hit[0]))
                res.update(declared_join=j["join"], declared_ret=r, declared_z=z)
                if not (np.isfinite(z) and abs(z) <= config.JOIN_MAX_Z):
                    res.update(ok=False, why=f"step at the declared join {j['join']}: "
                                             f"{np.expm1(r):+.1%} (z {z:+.1f})")
            else:
                res.update(ok=False, why=f"declared join {j['join']} not in the series")
        if res["ok"] and write:
            pre = src[src["date"].astype(str) < res["first_held"]]
            to_write.append(pre[store.SCHEMA])
            ledger.append({k: res.get(k) for k in (
                "ticker", "mode", "declared", "source_first", "first_held",
                "rows", "overlap", "max_diff", "gap", "ret", "z", "window_z",
                "declared_join", "declared_z")}
                | {"asof": (j or {}).get("asof"), "factor": (j or {}).get("factor", 1.0),
                   "written": lcs})
        if res["rows"] or mode == "manual":
            log(f"  history {t:6} {'JOIN' if res['ok'] else 'skip'}: {res['why']}"
                + (f" | {res['rows']:,} bar(s) from {res.get('source_first')}"
                   f", join {np.expm1(res['ret']):+.2%} z {res['z']:+.2f}"
                   if res.get("z") is not None and np.isfinite(res.get("z", np.nan))
                   else ""))
        out.append(res)

    if to_write:
        store.write(pd.concat(to_write, ignore_index=True), "1d", max_date=lcs)
        _ledger_add(ledger)
        log(f"history: joined {len(to_write)} ticker(s), "
            f"{sum(len(f) for f in to_write):,} bar(s)")
        if rebuild:
            build_panel_stats()
    return out


def history_audit(min_dollar_vol: float = 20e6, min_close: float = 5.0) -> pd.DataFrame:
    """Liquid names too short for the screen, and what the source holds for them.

    The question the 2026-10-31 review asked of 81 names, as a command. For each
    liquid name with fewer than config.MIN_BARS stored bars, the source's first
    bar WITH symbol mapping (follows renames) and WITHOUT it (the literal
    symbol). Mapping earlier than us = history `--history` can try to join.
    Only the literal symbol earlier = a reused symbol (FIG, Q, SPCX: other
    companies) or an entity Alpaca restarted at a listing change (TEVA) -- the
    filings decide which, so check them before declaring a join.
    """
    ps = load_panel_stats()
    if ps.empty:
        return pd.DataFrame()
    lcs = calendar_us.last_closed_session()
    pop = ps[(ps["dollar_vol_20"] >= min_dollar_vol) & (ps["last_close"] >= min_close)
             & (ps["last_date"] >= lcs) & (ps["n_bars"] < config.MIN_BARS)]
    names = sorted(pop["ticker"].astype(str))
    if not names:
        return pd.DataFrame()
    ms = store.months("1d")
    start = f"{ms[0]}-01" if ms else calendar_us.start_for_history()
    end = calendar_us.bars_end_ts()
    out = pop.set_index("ticker")[["n_bars", "first_date"]].rename(
        columns={"first_date": "stored_first"})
    for label, asof in (("mapped", None), ("literal", "-")):
        df, _f, _e = fetch_many(names, start, end, "1d", asof=asof, label=f"{label} ")
        g = (df.groupby("ticker")["date"].agg(["min", "size"]) if not df.empty
             else pd.DataFrame(columns=["min", "size"]))
        out[f"{label}_first"] = g["min"].reindex(out.index)
        out[f"{label}_n"] = g["size"].reindex(out.index)
    m_early = out["mapped_first"].fillna("9999") < out["stored_first"]
    l_early = out["literal_first"].fillna("9999") < out["stored_first"]
    out["verdict"] = np.where(m_early, "source holds earlier (mapped): try --history",
                     np.where(l_early, "literal symbol only: check the filings",
                              "source starts where we do"))
    return out.reset_index().sort_values(["verdict", "stored_first"])


def verify_joins(asof: str | None = None,
                 frames: dict[str, pd.DataFrame] | None = None) -> list[str]:
    """Problems with any join we made. Empty list = every join still holds.

    For each declared join, and each join in the ledger: the stored series must
    reach well before it, and the close-to-close move across it must be within
    JOIN_MAX_Z of the stock's own volatility. This is what catches a refetch
    that restored the unadjusted AZN series, or a store that lost the history.
    `frames` lets a test hand in a defective series instead of the store.
    """
    import json
    checks = {t: str(j["join"]) for t, j in config.HISTORY_JOINS.items()}
    if frames is None and config.HISTORY_LEDGER.exists():
        try:
            for r in json.loads(config.HISTORY_LEDGER.read_text(encoding="utf-8")):
                checks.setdefault(str(r["ticker"]), str(r["first_held"]))
        except (json.JSONDecodeError, OSError, KeyError, TypeError) as exc:
            return [f"history ledger unreadable: {exc!r}"[:120]]
    probs = []
    if frames is None:
        # The read below only reaches ~100 bars before each join; the panel
        # stats say whether the years before that are still there.
        ps = load_panel_stats()
        first = (dict(zip(ps["ticker"].astype(str), ps["first_date"].astype(str)))
                 if not ps.empty else {})
        for t, jd in sorted(checks.items()):
            f = first.get(t)
            if f and f > (pd.Timestamp(jd) - pd.DateOffset(years=1)).strftime("%Y-%m-%d") \
                    and t in config.HISTORY_JOINS:
                probs.append(f"{t}: history starts {f}, under a year before the {jd} join")
        lo = (pd.Timestamp(min(checks.values())) - pd.DateOffset(days=150)
              ).strftime("%Y-%m-%d")
        got = store.read("1d", start=lo, end=asof, tickers=list(checks),
                         columns=["ticker", "date", "close"])
        frames = {t: g for t, g in got.groupby("ticker")}
    for t, jd in sorted(checks.items()):
        g = frames.get(t)
        if g is None or g.empty:
            probs.append(f"{t}: no stored bars")
            continue
        g = g.sort_values("date").reset_index(drop=True)
        d = g["date"].astype(str)
        before = int((d < jd).sum())
        if before < 60:
            probs.append(f"{t}: only {before} bar(s) before the {jd} join")
            continue
        hit = d[d == jd].index
        if not len(hit):
            continue                          # join after `asof`: nothing to check
        r, z = _z_at(g["close"], int(hit[0]))
        if not (np.isfinite(z) and abs(z) <= config.JOIN_MAX_Z):
            probs.append(f"{t}: step at the {jd} join {np.expm1(r):+.1%} (z {z:+.1f})")
    return probs


def selftest() -> int:
    """`python bars.py --selftest`: the join rules, each shown to refuse."""
    ses = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2025-01-01", periods=200)]
    rng = np.random.default_rng(7)
    px = 50 * np.exp(np.cumsum(rng.normal(0, 0.01, 200)))

    def frame(dates, close, t="X"):
        return pd.DataFrame({"ticker": t, "date": dates, "close": close,
                             "open": close, "high": close, "low": close,
                             "vwap": close, "volume": 1000, "trades": 10})

    src = frame(ses, px)
    held = frame(ses[150:], px[150:])
    assert join_check(src, held, ses)["ok"], "a clean join must pass"
    assert join_check(src[src.date >= ses[150]], held, ses)["why"] == \
        "source holds nothing earlier", "a new listing must be left alone"

    half = src.copy()
    half.loc[half.index < 150, "close"] = half.loc[half.index < 150, "close"] / 2
    r = join_check(half, held, ses)
    assert not r["ok"] and r["why"].startswith("step at the join"), r

    bad = src.copy()
    bad.loc[bad.index >= 150, "close"] *= 1.01
    assert "disagrees" in join_check(bad, held, ses)["why"], "overlap must agree"

    gap = src.drop(index=range(140, 150))
    assert "missing between" in join_check(gap, held, ses)["why"], "gap must refuse"

    spin = px.copy()
    spin[153:] *= 0.8                          # a spin-off three bars in
    r = join_check(frame(ses, spin), frame(ses[150:], spin[150:]), ses)
    assert not r["ok"] and "first bars" in r["why"], r

    # factor: before the join only, prices up, volume down
    j = {"X": {"join": ses[150], "factor": 2.0}}
    a = apply_joins(half, j)
    assert np.allclose(a["close"], src["close"]), "x2 must undo the halving"
    assert a.loc[0, "volume"] == 500 and a.loc[199, "volume"] == 1000
    assert join_check(a, held, ses)["ok"], "the adjusted series must join"

    # verify_joins: a held join passes, an unadjusted one FAILS
    saved = config.HISTORY_JOINS
    try:
        config.HISTORY_JOINS = {"X": {"join": ses[150], "factor": 2.0}}
        assert verify_joins(frames={"X": src}) == [], "a clean series must pass"
        p = verify_joins(frames={"X": half})
        assert p and "step at the" in p[0], p
        p = verify_joins(frames={"X": held})
        assert p and "before the" in p[0], p
    finally:
        config.HISTORY_JOINS = saved

    print("bars selftest OK (clean join, new listing, step, overlap, gap, "
          "spin-off, factor, verify pass/step/lost)")
    return 0


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

    prev = load_panel_stats()
    before = store.stored_dates("1d")
    store.write(df, "1d", max_date=lcs, verbose=True)
    after = store.stored_dates("1d")
    new_sessions = sorted(after - before)

    healthy = universe.is_healthy(found, tickers, err)
    universe.record_outcome(found, tickers, healthy)

    # A name whose first bar falls inside this window is new TO THE STORE, and
    # `--backfill` would count it done from tomorrow. Ask the source once for
    # anything earlier; `history` writes it only if it joins cleanly. A real
    # new listing costs one batched request and writes nothing. Best effort:
    # this must never fail the step.
    try:
        known = (dict(zip(prev["ticker"].astype(str), prev["first_date"].astype(str)))
                 if not prev.empty else {})
        fresh = (sorted(t for t in found if known.get(t, "9999") >= start)
                 if config.HISTORY_AUTO_JOIN else [])
        if fresh and len(fresh) <= 200:
            log(f"update: {len(fresh)} name(s) new to the store; "
                f"asking the source for earlier history")
            history(fresh, mode="auto", rebuild=False)
        elif fresh:
            log(f"update: {len(fresh)} names new to the store -- too many for the "
                "nightly check; run `bars.py --history-audit`")
    except Exception as exc:                                     # noqa: BLE001
        log(f"  ! history check skipped: {repr(exc)[:110]}")

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
    ap.add_argument("--history", nargs="?", const="", default=None,
                    metavar="T1,T2",
                    help="join earlier history (default: config.HISTORY_JOINS)")
    ap.add_argument("--history-audit", action="store_true",
                    help="liquid names whose source history starts before ours")
    ap.add_argument("--dry-run", action="store_true", help="--history: write nothing")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--years", type=float, default=None)
    ap.add_argument("--limit", type=int, default=0, help="cap #tickers (smoke test)")
    ap.add_argument("--batch", type=int, default=None)
    ap.add_argument("--window", type=int, default=5)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    config.dirs()
    t0 = time.time()

    if a.history is not None:
        names = [s.strip() for s in a.history.split(",") if s.strip()] or None
        history(names, write=not a.dry_run)
        bad = verify_joins() if not a.dry_run else []
        for p in bad:
            log(f"  !! {p}")
        return 1 if bad else 0
    if a.history_audit:
        print(history_audit().to_string(index=False))
        return 0
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

