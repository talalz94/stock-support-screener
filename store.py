"""
Month-partitioned parquet bar store.

Layout: data/bars/<interval>/YYYY-MM.parquet

Why months and not days (measured on 400 tickers x 751 sessions, scaled to 5,381):

    single file   75 MB,   1 file  -- but every append rewrites all of it
    month        109 MB,  37 files -- append touches one ~3 MB file
    day          203 MB, 751 files -- 2.7x the bytes, 751 opens EVERY run

This screener reads ~4 years for every ticker on every run, the opposite of
`Stock Screener`'s recent-days access pattern, so day-partitioning would pay 751
file opens for nothing. Per-file parquet footers and dictionary pages dominate at
day granularity, which is where the 2.7x size penalty comes from. Months also
make a backfill resumable per month, which matters given transient 403/504s.

Retention is AGE-based, not size-capped. Stock Screener's 10 GB oldest-first
prune would be actively harmful here: it would delete the very history the
pattern needs and start failing MIN_BARS silently, which surfaces to the user as
"the screener found nothing today" rather than as an error.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pandas as pd

import config

SCHEMA = ["ticker", "datetime", "date", "open", "high", "low", "close",
          "volume", "trades", "vwap"]

_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def atomic_replace(tmp, path, attempts: int = 40, delay: float = 0.25) -> None:
    """`tmp` -> `path`, retrying the Windows sharing violation.

    On POSIX os.replace over an open file always succeeds; on Windows it raises
    PermissionError (WinError 5) if ANY other process holds the target open,
    including a plain reader. That is not a corner case here -- the sentiment
    screener reads the news store on its interval while a backfill writes it, and
    a 10-hour backfill died exactly this way at chunk 128/209:

        PermissionError: [WinError 5] Access is denied:
          data\\news\\2025-01.parquet.tmp -> data\\news\\2025-01.parquet

    The bar pipeline never hit this because it is one-shot and single-process.
    Readers hold their handle for milliseconds, so a bounded retry closes the
    race without a lock file. Still atomic: os.replace either happens or does
    not, and the .tmp is left behind on give-up rather than a half-written file.
    """
    import time as _time

    for i in range(attempts):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            _time.sleep(delay)


def part_dir(interval: str = "1d"):
    return config.BARS / interval


def part_path(month: str, interval: str = "1d"):
    return part_dir(interval) / f"{month}.parquet"


def months(interval: str = "1d") -> list[str]:
    d = part_dir(interval)
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.parquet") if _MONTH_RE.match(p.stem))


def bars_from_payload(payload: dict[str, list[dict]], interval: str = "1d",
                      rth_only: bool = True) -> pd.DataFrame:
    """Alpaca's {symbol: [bar,...]} -> a typed frame in SCHEMA order."""
    frames = []
    for sym, arr in payload.items():
        if not arr:
            continue
        df = pd.DataFrame(arr)
        df["datetime"] = pd.to_datetime(df["t"], utc=True)
        et = df["datetime"].dt.tz_convert("America/New_York")
        if interval != "1d" and rth_only:
            df = df[et.dt.hour.between(9, 15)]
            et = et[df.index]
        if df.empty:
            continue
        df = df.rename(columns={"o": "open", "h": "high", "l": "low",
                                "c": "close", "v": "volume", "n": "trades",
                                "vw": "vwap"})
        # Store the ET calendar date, not the UTC one: a 1Day bar is stamped
        # 04:00Z, which is the SAME day in ET, but intraday bars are not.
        df["date"] = et.dt.date.astype(str)
        df["datetime"] = et.dt.tz_localize(None)
        df["ticker"] = sym
        for c in ("trades", "vwap"):
            if c not in df.columns:
                df[c] = 0
        frames.append(df[SCHEMA])

    if not frames:
        return pd.DataFrame(columns=SCHEMA)
    return pd.concat(frames, ignore_index=True)


def _typed(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ticker"] = df["ticker"].astype("category")
    for c in ("open", "high", "low", "close", "vwap"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float32")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype("int64")
    df["trades"] = pd.to_numeric(df["trades"], errors="coerce").fillna(0).astype("int32")
    return df.sort_values(["ticker", "datetime"]).reset_index(drop=True)


def write(df: pd.DataFrame, interval: str = "1d",
          max_date: str | None = None, verbose: bool = False) -> dict[str, int]:
    """Merge `df` into the month partitions it spans. Returns {month: rows}.

    THE partial-bar chokepoint. Any bar dated >= max_date is dropped here rather
    than in each caller, because an in-progress bar poisons everything
    downstream: a mid-session low becomes a false "bounce low", a mid-session
    high a false peak, and the screen changes at 15:59. Verified live -- at 11:04
    ET, RDW's 2026-08-04 bar showed n=46,662 against ~112,000 for a full session.
    """
    if df.empty:
        return {}

    if max_date is None:
        import calendar_us
        max_date = calendar_us.last_closed_session()

    n_before = len(df)
    df = df[df["date"] <= max_date]
    dropped = n_before - len(df)
    if dropped and verbose:
        print(f"    dropped {dropped:,} bar(s) dated > {max_date} (in-progress)")
    if df.empty:
        return {}

    df = _typed(df)
    df["_m"] = df["date"].str.slice(0, 7)

    written: dict[str, int] = {}
    part_dir(interval).mkdir(parents=True, exist_ok=True)

    for month, chunk in df.groupby("_m", sort=True, observed=True):
        path = part_path(str(month), interval)
        chunk = chunk.drop(columns="_m")
        if path.exists():
            old = pd.read_parquet(path)
            # `chunk` last so a re-fetch overwrites stored bars -- required
            # because split adjustment is retroactive and rewrites history.
            merged = pd.concat([old, chunk], ignore_index=True)
            merged = merged.drop_duplicates(["ticker", "datetime"], keep="last")
        else:
            merged = chunk
        merged = _typed(merged)

        tmp = path.with_suffix(".parquet.tmp")
        merged.to_parquet(tmp, compression=config.COMPRESSION,
                          compression_level=config.COMPRESSION_LEVEL, index=False)
        atomic_replace(tmp, path)  # atomic; retries the Windows sharing violation
        written[str(month)] = len(merged)

    return written


def read(interval: str = "1d", start: str | None = None, end: str | None = None,
         tickers: list[str] | None = None,
         columns: list[str] | None = None) -> pd.DataFrame:
    """Read the store, opening only the month files inside [start, end]."""
    want = months(interval)
    if start:
        want = [m for m in want if m >= start[:7]]
    if end:
        want = [m for m in want if m <= end[:7]]
    if not want:
        return pd.DataFrame(columns=columns or SCHEMA)

    tset = set(tickers) if tickers else None
    frames = []
    for m in want:
        df = pd.read_parquet(part_path(m, interval), columns=columns)
        if tset is not None:
            df = df[df["ticker"].astype(str).isin(tset)]
        if start:
            df = df[df["date"] >= start]
        if end:
            df = df[df["date"] <= end]
        if not df.empty:
            frames.append(df)

    if not frames:
        return pd.DataFrame(columns=columns or SCHEMA)
    out = pd.concat(frames, ignore_index=True)
    if "ticker" in out.columns:
        out["ticker"] = out["ticker"].astype(str)
    # A projected read may not include `datetime`; sort by whatever ordering keys
    # are actually present so callers can ask for narrow column subsets.
    keys = [c for c in ("ticker", "datetime", "date") if c in out.columns]
    if "datetime" in keys and "date" in keys:
        keys.remove("date")
    return out.sort_values(keys).reset_index(drop=True) if keys else out


def stored_dates(interval: str = "1d") -> set[str]:
    """Every session date present in the store. Used for gap detection."""
    seen: set[str] = set()
    for m in months(interval):
        seen |= set(pd.read_parquet(part_path(m, interval),
                                    columns=["date"])["date"].unique())
    return seen


def coverage(interval: str = "1d") -> pd.DataFrame:
    """Per-month row/ticker/date summary. Cheap: reads 3 columns."""
    rows = []
    for m in months(interval):
        p = part_path(m, interval)
        df = pd.read_parquet(p, columns=["ticker", "date"])
        rows.append({"month": m, "rows": len(df),
                     "tickers": df["ticker"].astype(str).nunique(),
                     "sessions": df["date"].nunique(),
                     "bytes": p.stat().st_size})
    return pd.DataFrame(rows)


def store_bytes(interval: str | None = None) -> int:
    root = part_dir(interval) if interval else config.BARS
    if not root.exists():
        return 0
    return sum(p.stat().st_size for p in root.rglob("*.parquet"))


def prune(interval: str = "1d", years: float | None = None) -> list[str]:
    """Drop month partitions older than the retention window. AGE, not size."""
    years = config.HISTORY_YEARS if years is None else years
    cutoff = (date.today() - timedelta(days=int(years * 365.25) + 40)).strftime("%Y-%m")
    dropped = []
    for m in months(interval):
        if m < cutoff:
            part_path(m, interval).unlink()
            dropped.append(m)
    return dropped


def prune_1h(keep_days: int | None = None) -> list[str]:
    keep = config.CONFIRM_KEEP_DAYS if keep_days is None else keep_days
    cutoff = (date.today() - timedelta(days=keep)).strftime("%Y-%m")
    dropped = []
    for m in months("1h"):
        if m < cutoff:
            part_path(m, "1h").unlink()
            dropped.append(m)
    return dropped


def prune_dated(directory, keep_days: int, patterns=("*.parquet",)) -> list[str]:
    """Retention for any directory of `<YYYY-MM-DD>.<ext>` files.

    `patterns` exists because this was called on `reports/` while hard-coded to
    `*.parquet`, so it matched nothing and REPORT RETENTION NEVER RAN -- despite
    a comment in the caller describing the ~93 MB/yr it was supposedly
    reclaiming. A function whose caller believes it is working is worse than one
    that is obviously unused.

    Files whose stem is not a date are left alone, which is what protects
    `latest.html` and `index.html` from a directory that mixes both.
    """
    if not directory.exists():
        return []
    cutoff = (date.today() - timedelta(days=keep_days)).isoformat()
    dropped = []
    for pat in patterns:
        for p in sorted(directory.glob(pat)):
            stem = p.stem
            # A DATE, not just "sorts before the cutoff". `index` and `latest`
            # would both compare < a cutoff string and be deleted.
            if len(stem) != 10 or stem[4] != "-" or stem[7] != "-":
                continue
            if not stem.replace("-", "").isdigit():
                continue
            if stem < cutoff:
                p.unlink()
                dropped.append(f"{directory.name}/{p.name}")
    return dropped
