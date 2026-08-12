"""
The effectiveness study: every metric, every horizon, every size bucket.

    python study.py                 run it (resumable, ~4-5h)
    python study.py --summary       read what has been measured so far
    python study.py --horizons 1,20 narrower

WHY FOUR HORIZONS AND NOT ONE
-------------------------------
A single horizon manufactures false negatives. Sentiment's only
overlap-corrected signal is at **h=1** and reads dead at h=20 -- judging a fast
signal at a slow horizon says "useless" when the truth is "measured in the wrong
place". Attention and gap frequency are the same shape. So h = 1, 5, 20, 60.

WHY SIZE BUCKETS
------------------
The dip thesis was flagged from the start as something that may only work for
large caps with long histories. Terciles of `mktcap` are what turns that from an
opinion into a measurement.

RESULTS ARE PERSISTED, NOT TRANSCRIBED
----------------------------------------
Everything lands in `data/_factor_study.parquet` and `metrics_doc.MEASURED` is
generated from it. The previous 24 numbers were hand-copied from two terminal
runs, which is why 73 of 97 metrics read "not tested" when 87 were testable.

RESUMABLE. Each (module, metric, horizon, size) row is written as it completes,
so a stop costs one cell rather than the run.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd

import config

config.safe_console()

import factor_lab                                               # noqa: E402
import scores                                                   # noqa: E402
import store                                                    # noqa: E402

OUT = config.DATA / "_factor_study.parquet"

# Below this many bucketed sessions, size terciles cannot be point-in-time and
# fall back to a snapshot. 24 is ~2 years of the monthly-ish fundamental series:
# enough that a company's bucket can actually change within the window.
MIN_PIT_SESSIONS = 24
HORIZONS = (1, 5, 20, 60)
SIZES = ("all", "large", "mid", "small")
COLUMNS = ["module", "metric", "horizon", "size", "ic", "t", "hit",
           "ic_random", "spread", "n_dates", "measured_at"]


def log(m: str) -> None:
    line = f"study {datetime.now():%Y-%m-%d %H:%M:%S} | {m}"
    try:
        print(line, flush=True)
    except (ValueError, OSError):
        pass
    try:
        with config.LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def read() -> pd.DataFrame:
    if not OUT.exists():
        return pd.DataFrame(columns=COLUMNS)
    try:
        return pd.read_parquet(OUT)
    except Exception:                                            # noqa: BLE001
        return pd.DataFrame(columns=COLUMNS)


def _append(rows: list[dict]) -> None:
    if not rows:
        return
    df = pd.concat([read(), pd.DataFrame(rows)], ignore_index=True)
    df = df.drop_duplicates(subset=["module", "metric", "horizon", "size"],
                            keep="last")
    tmp = OUT.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, compression=config.COMPRESSION,
                  compression_level=config.COMPRESSION_LEVEL, index=False)
    store.atomic_replace(tmp, OUT)


def size_bucket_frame(asof: str) -> pd.DataFrame:
    """(session, ticker, size) -- terciles computed WITHIN each session.

    A SNAPSHOT HERE IS A LOOK-AHEAD, AND RIGHT NOW A SNAPSHOT IS ALL THERE IS.
    Bucketing every date by the NEWEST session's terciles does not measure "large
    caps" -- it measures "companies that WOULD BECOME large", with "small" being
    those that stayed small or shrank. That is a survivorship and momentum bias
    pointing in the direction that flatters large and punishes small.

    The honest fix is one tercile set per session, which is what this function
    returns whenever the data allows. But `mktcap` is currently stored for
    **1 of 182** fundamental sessions: it was declared long before
    `FM.compute()` actually passed price inputs through, so every backfilled
    session has the column and no values.

    So this degrades explicitly rather than silently. With >= MIN_PIT_SESSIONS
    bucketed sessions it is point-in-time; below that it falls back to the
    single snapshot, LOGS the bias, and stamps `pit=False` on the frame so
    callers and the results table can say which one they got. Backfilling
    historical mktcap turns the fallback off with no further code change.
    """
    try:
        df = scores.read(module="fundamental", metrics=["mktcap"], end=asof)
    except Exception as exc:                                     # noqa: BLE001
        log(f"  ! size buckets unavailable ({exc!r}); running 'all' only")
        return pd.DataFrame(columns=["session", "ticker", "size"])
    if df is None or df.empty:
        return pd.DataFrame(columns=["session", "ticker", "size"])

    df = df[pd.to_numeric(df["value"], errors="coerce").notna()]
    parts = []
    for sess, g in df.groupby("session", observed=True):
        mc = pd.to_numeric(g.set_index("ticker")["value"], errors="coerce").dropna()
        mc = mc[mc > 0]
        if len(mc) < 30:
            continue
        lo, hi = mc.quantile([1 / 3, 2 / 3])
        lab = pd.Series("mid", index=mc.index, dtype="object")
        lab[mc <= lo] = "small"
        lab[mc > hi] = "large"
        parts.append(pd.DataFrame({"session": str(sess),
                                   "ticker": mc.index.astype(str),
                                   "size": lab.values}))
    if not parts:
        return pd.DataFrame(columns=["session", "ticker", "size"])
    out = pd.concat(parts, ignore_index=True)
    n = out["session"].nunique()
    out.attrs["pit"] = n >= MIN_PIT_SESSIONS
    if out.attrs["pit"]:
        log(f"  size buckets: POINT-IN-TIME across {n} session(s), "
            f"{len(out):,} (session,ticker) pair(s)")
    else:
        log(f"  size buckets: only {n} session(s) carry mktcap, so buckets are a "
            f"SNAPSHOT applied to all dates. Per-size results inherit a "
            f"look-ahead bias (favours 'large'). Backfill mktcap to fix.")
    return out


def tag_sizes(rows: pd.DataFrame, bmap: pd.DataFrame) -> pd.DataFrame:
    """Attach each row's own-date size bucket. One as-of join per metric.

    Backward direction only WHEN the bucketing is point-in-time: a row dated
    2019-03-01 takes the bucketing from the latest fundamental session on or
    before it. When only one session carries mktcap, backward matching would
    drop 99% of rows and empty every bucket, so that case is handled as an
    explicit static join instead -- biased, logged, but not silently empty.
    """
    if rows is None or rows.empty or bmap.empty:
        r = rows.copy() if rows is not None else pd.DataFrame()
        if len(r):
            r["_size"] = np.nan
        return r
    if not bmap.attrs.get("pit", False):
        # Static fallback: one bucket per ticker, from the only session there is.
        latest = bmap[bmap["session"] == bmap["session"].max()]
        m = dict(zip(latest["ticker"].astype(str), latest["size"]))
        out = rows.copy()
        out["_size"] = out["ticker"].astype(str).map(m)
        return out

    left = rows.copy()
    left["_d"] = pd.to_datetime(left["date"], errors="coerce")
    left = left[left["_d"].notna()].sort_values("_d")
    right = bmap.copy()
    right["_d"] = pd.to_datetime(right["session"], errors="coerce")
    right = right[right["_d"].notna()].sort_values("_d")
    left["ticker"] = left["ticker"].astype(str)
    right["ticker"] = right["ticker"].astype(str)
    merged = pd.merge_asof(left, right[["_d", "ticker", "size"]],
                           on="_d", by="ticker", direction="backward")
    merged = merged.rename(columns={"size": "_size"})
    return merged.drop(columns=["_d"])


def run(horizons=HORIZONS, modules=None, min_dates: int = 10) -> int:
    t0 = time.time()
    scores.load_all()
    modules = modules or list(config.SCORE_MODULES)

    import calendar_us
    asof = calendar_us.last_closed_session()
    bmap = size_bucket_frame(asof)
    have = set(bmap["size"]) if not bmap.empty else set()
    sizes = ["all"] + [s for s in ("large", "mid", "small") if s in have]

    done = read()
    seen = set(zip(done.get("module", []), done.get("metric", []),
                   done.get("horizon", []), done.get("size", [])))

    todo = []
    for mod in modules:
        for metric in scores.get(mod).metrics():
            if not factor_lab.is_signal(metric):
                continue
            for h in horizons:
                for sz in sizes:
                    if (mod, metric, h, sz) not in seen:
                        todo.append((mod, metric, h, sz))

    log(f"STUDY START | {len(todo)} cell(s) to measure "
        f"({len(modules)} modules x {len(horizons)} horizons x {len(sizes)} sizes)")
    if not todo:
        log("nothing to do -- delete data/_factor_study.parquet to redo")
        return 0

    # Group by (module, metric) so each metric's rows are loaded ONCE and reused
    # across every horizon and bucket. Loading per cell would multiply the cost
    # by len(horizons) x len(sizes).
    by_metric: dict[tuple, list] = {}
    for mod, metric, h, sz in todo:
        by_metric.setdefault((mod, metric), []).append((h, sz))

    batch, n_done = [], 0
    for (mod, metric), cells in by_metric.items():
        try:
            rows = factor_lab.load_metric(mod, metric)
        except Exception as exc:                                 # noqa: BLE001
            log(f"  {mod}/{metric}: load failed {exc!r}"[:120])
            n_done += len(cells)
            continue
        if rows is None or rows.empty:
            n_done += len(cells)
            continue

        # Tag once per metric, not once per cell: the as-of join is the same for
        # every horizon and bucket of a given metric.
        tagged = rows if all(sz == "all" for _h, sz in cells) \
            else tag_sizes(rows, bmap)

        for h, sz in cells:
            sub = rows if sz == "all" else tagged[tagged["_size"] == sz]
            rec = {"module": mod, "metric": metric, "horizon": h, "size": sz,
                   "ic": np.nan, "t": np.nan, "hit": np.nan,
                   "ic_random": np.nan, "spread": np.nan, "n_dates": 0,
                   "measured_at": datetime.now().isoformat(timespec="seconds")}
            try:
                if not sub.empty and sub["date"].nunique() >= min_dates:
                    res = factor_lab.evaluate(sub, horizons=(h,), by=None)
                    ic = res.get("ic")
                    if ic is not None and not ic.empty:
                        r = ic.iloc[0]
                        q = res.get("quantiles")
                        rec.update({
                            "ic": float(r["ic"]), "t": float(r["t"]),
                            "hit": float(r["hit"]),
                            "ic_random": float(r["ic_random"]),
                            "spread": (float(q.iloc[0]["spread"])
                                       if q is not None and not q.empty
                                       else np.nan),
                            "n_dates": int(r.get("n_dates", 0) or 0)})
            except Exception as exc:                             # noqa: BLE001
                # Recorded as NaN rather than skipped: an absent cell and a cell
                # that could not be computed are different states.
                log(f"  {mod}/{metric} h={h} {sz}: {type(exc).__name__}"[:110])
            batch.append(rec)
            n_done += 1

        if len(batch) >= 40:
            _append(batch)
            batch = []
            el = (time.time() - t0) / 60
            log(f"  {n_done}/{len(todo)} cells, {el:.1f}m, "
                f"eta {el / max(n_done, 1) * (len(todo) - n_done):.0f}m")

    _append(batch)
    log(f"STUDY DONE in {(time.time() - t0) / 60:.1f}m | {n_done} cell(s)")
    return 0


def best_by_metric(df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Strongest |t| per metric across horizons, on the full universe.

    This is what `metrics_doc.MEASURED` is generated from: a metric is judged at
    the horizon where it actually works, not at one arbitrary horizon.
    """
    df = read() if df is None else df
    if df.empty:
        return df
    a = df[(df["size"] == "all") & df["t"].notna()].copy()
    if a.empty:
        return a
    a["abs_t"] = a["t"].abs()
    return (a.sort_values("abs_t", ascending=False)
             .drop_duplicates(subset=["metric"], keep="first")
             .drop(columns=["abs_t"]))


def summary() -> int:
    df = read()
    if df.empty:
        print("\n  no study yet -- run `python study.py`\n")
        return 0
    best = best_by_metric(df)
    # int()/str() the numpy scalars: their repr leaks as `np.int64(1)`, the same
    # way it did into the generated markdown before docs.py cast them.
    print(f"\n  {len(df):,} cells measured across "
          f"{df['metric'].nunique()} metrics, "
          f"{sorted(int(h) for h in df['horizon'].unique())} horizons, "
          f"{sorted(str(s) for s in df['size'].unique())} buckets\n")
    strong = best[best["t"].abs() >= 2].sort_values("t", key=abs,
                                                    ascending=False)
    print(f"  {len(strong)} metric(s) reach |t| >= 2 at their best horizon:\n")
    print(f"  {'metric':<22}{'mod':<13}{'h':>3} {'IC':>9} {'t':>7} {'hit':>6}")
    for _, r in strong.head(30).iterrows():
        print(f"  {r['metric']:<22}{r['module']:<13}{int(r['horizon']):>3} "
              f"{r['ic']:>+9.4f} {r['t']:>7.2f} {r['hit']:>5.0%}")
    print()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Factor effectiveness study.")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--horizons", help="comma-separated, default 1,5,20,60")
    ap.add_argument("--module", action="append")
    a = ap.parse_args()
    if a.summary:
        return summary()
    hs = tuple(int(x) for x in a.horizons.split(",")) if a.horizons else HORIZONS
    return run(horizons=hs, modules=a.module)


if __name__ == "__main__":
    sys.exit(main())
