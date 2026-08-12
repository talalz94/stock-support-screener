"""
News fetch and store: Alpaca /v1beta1/news, month-partitioned parquet.

    python news.py --update            incremental from the watermark (~10s)
    python news.py --backfill          full history, resumable, chunked by week
    python news.py --dry-run           page/volume estimate, writes nothing
    python news.py --stats             what is on disk
    python news.py --explain RDW       every stored article for one ticker

MEASURED against the live API on 2026-08-06 (not assumed -- house convention):

    firehose volume   1,580 articles on 2026-08-04 | 1,145 on 2024-03-05
                        371 on 2016-06-10   -> coverage grew ~4x since 2016
    history depth     >= 2015-01-05
    page size         limit caps at 50 (bars allow 10,000)
    one session       ~32 pages, ~8s burst
    `symbols=`        3,000 symbols in one param: OK
    universe overlap  90% of articles tag >=1 universe ticker
    near-duplicates   1% -- there is no cheap way to shrink the corpus
    source            'benzinga' on 100% of recent rows

TWO DECISIONS THAT FOLLOW FROM THOSE MEASUREMENTS

1. FETCH THE FIREHOSE, NOT PER-SYMBOL. 90% of articles tag a universe ticker, so
   per-symbol querying would issue thousands of requests to retrieve substantially
   the same rows. One unfiltered pass per date range gets everything, and each
   article's own `symbols` list does the routing.

2. NEVER STORE `content`. Bodies are 4-10 KB each (measured on ORCL). Storing them
   takes the store from ~120 MB to ~10 GB and buys nothing: headline + summary is
   what gets scored.

WHY THE NEWS STORE LIVES HERE AND NOT IN store.py
   store.py is the BAR store -- its SCHEMA, its typing and its partial-bar
   chokepoint are all bar-shaped. News has a different schema and a different
   chokepoint (session attribution, below), so folding it in would give that
   module two reasons to change. The partitioning idiom is deliberately identical.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

import alpaca
import calendar_us
import config
import store

SCHEMA = ["id", "ts", "session", "created_at", "updated_at", "headline",
          "summary", "source", "author", "url", "symbols", "n_symbols"]

_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")

ET = "America/New_York"


# ===========================================================================
# Session attribution -- THE look-ahead chokepoint
# ===========================================================================
def _session_closes() -> tuple[list[str], np.ndarray]:
    """(session dates, their close timestamps in ET) from the trading calendar.

    Cached per process: the calendar is a small parquet but this is called once
    per fetched batch and re-reading it per article would dominate the run.
    """
    global _CLOSES_CACHE
    if _CLOSES_CACHE is not None:
        return _CLOSES_CACHE

    cal = calendar_us.refresh()
    if cal.empty:
        raise RuntimeError(
            "No trading calendar available, so news cannot be attributed to a "
            "session. Run `python calendar_us.py --refresh` while online."
        )
    sessions = cal["date"].astype(str).tolist()

    closes = []
    for _, row in cal.iterrows():
        hhmm = str(row.get("close") or config.NEWS_SESSION_CUTOFF_ET)
        try:
            hh, mm = (int(x) for x in hhmm.split(":")[:2])
        except ValueError:
            hh, mm = 16, 0
        closes.append(pd.Timestamp(row["date"]) + pd.Timedelta(hours=hh, minutes=mm))

    _CLOSES_CACHE = (sessions, np.array(closes, dtype="datetime64[ns]"))
    return _CLOSES_CACHE


_CLOSES_CACHE: tuple[list[str], np.ndarray] | None = None


def attribute_session(ts_utc: pd.Series) -> pd.Series:
    """Map article timestamps to the session whose signal may use them.

    THE single biggest look-ahead risk in this module, and a silent one.

    An article stamped 21:00Z is 17:00 ET -- an hour AFTER the close. Attributing
    it to that day's session would let a screen computed on that day's close
    "know" news that had not been published when the close printed. Nothing
    errors; the backtest just quietly improves, which is the worst possible
    failure mode and exactly what replay.py --leaktest exists to catch.

    THE RULE, applied here and nowhere else:

        an article belongs to session S iff its timestamp < S's close;
        anything at or after S's close belongs to S+1.

    This composes with backtest.py's next-open entry: signal at S's close, entry
    at S+1's open, so an article published at 17:00 ET on S is available for the
    signal computed at S+1's close and traded at S+2's open. Never earlier.

    Articles later than the last calendar session get NaT rather than being
    clamped to it -- clamping would silently attribute future news to the last
    known date, which is the leak this function exists to prevent.
    """
    sessions, closes = _session_closes()

    ts = pd.to_datetime(ts_utc, utc=True, errors="coerce")
    et = ts.dt.tz_convert(ET).dt.tz_localize(None)

    # side="right" -> index of the first close STRICTLY greater than ts, which is
    # precisely "the first session whose close had not yet printed".
    idx = np.searchsorted(closes, et.to_numpy(dtype="datetime64[ns]"), side="right")

    out = np.full(len(idx), None, dtype=object)
    ok = (idx < len(sessions)) & et.notna().to_numpy()
    arr = np.asarray(sessions, dtype=object)
    out[ok] = arr[idx[ok]]
    return pd.Series(out, index=ts_utc.index, dtype=object)


# ===========================================================================
# Fetch
# ===========================================================================
def _url() -> str:
    return f"{config.DATA_BASE}{config.NEWS_URL_PATH}"


def fetch_range(start, end, symbols: list[str] | None = None,
                verbose: bool = False, max_pages: int = 100_000) -> list[dict]:
    """Every article in [start, end], fully paginated.

    Pagination is by opaque `page_token`, and unlike the bars endpoint there is no
    mid-symbol resumption to worry about because the firehose is a flat stream.
    RAISES on hard failure (via alpaca._get) so a truncated fetch can never be
    mistaken for a quiet news day -- which would read as "no sentiment" rather
    than as an error.
    """
    out: list[dict] = []
    token: str | None = None
    pages = 0
    t0 = time.time()

    while pages < max_pages:
        params = {
            "start": alpaca._as_iso(start),
            "end": alpaca._as_iso(end),
            "limit": config.NEWS_PAGE_LIMIT,
            "sort": "asc",
            "include_content": str(bool(config.NEWS_INCLUDE_CONTENT)).lower(),
        }
        if symbols:
            params["symbols"] = ",".join(symbols)
        if token:
            params["page_token"] = token

        payload = alpaca._get(_url(), params)
        batch = payload.get("news") or []
        out.extend(batch)
        pages += 1

        token = payload.get("next_page_token")
        if not token:
            break

    if verbose:
        print(f"    {len(out):,} article(s), {pages} page(s), {time.time() - t0:.0f}s")
    return out


def to_frame(articles: list[dict]) -> pd.DataFrame:
    """Raw API payload -> typed frame in SCHEMA order, session already attributed."""
    if not articles:
        return pd.DataFrame(columns=SCHEMA)

    df = pd.DataFrame(articles)
    for c in ("headline", "summary", "source", "author", "url"):
        if c not in df.columns:
            df[c] = ""
    if "symbols" not in df.columns:
        df["symbols"] = [[] for _ in range(len(df))]

    created = pd.to_datetime(df["created_at"], utc=True, errors="coerce")
    updated = pd.to_datetime(df.get("updated_at"), utc=True, errors="coerce")

    # Availability is max(created, updated). Benzinga revises in place; measured
    # drift was 0/50 sampled articles, but scoring the REVISED text while
    # attributing it to the ORIGINAL timestamp is look-ahead, and it costs
    # nothing to be right about it.
    if config.NEWS_USE_UPDATED_AT:
        ts = pd.concat([created, updated], axis=1).max(axis=1)
    else:
        ts = created

    out = pd.DataFrame({
        "id": pd.to_numeric(df["id"], errors="coerce").astype("int64"),
        "ts": ts,
        "session": attribute_session(ts),
        "created_at": created,
        "updated_at": updated,
        "headline": df["headline"].fillna("").astype(str),
        "summary": df["summary"].fillna("").astype(str),
        "source": df["source"].fillna("").astype(str),
        "author": df["author"].fillna("").astype(str),
        "url": df["url"].fillna("").astype(str),
        # Comma-joined, not a list column: the exploded (id, ticker) view is built
        # on read, and a scalar string round-trips through parquet without the
        # pyarrow list-type friction that screen.serialise_lists exists to dodge.
        "symbols": df["symbols"].apply(
            lambda v: ",".join(map(str, v)) if isinstance(v, (list, tuple)) else ""),
    })
    out["n_symbols"] = out["symbols"].apply(lambda s: 0 if not s else len(s.split(",")))
    return out[SCHEMA]


# ===========================================================================
# Store  (month partitions, same idiom as store.py)
# ===========================================================================
def part_path(month: str):
    return config.NEWS / f"{month}.parquet"


def months() -> list[str]:
    if not config.NEWS.exists():
        return []
    return sorted(p.stem for p in config.NEWS.glob("*.parquet")
                  if _MONTH_RE.match(p.stem))


def _typed(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce to the stored dtypes. MUST be idempotent -- write() calls it once on
    the incoming chunk and again on the merge, so every cast has to survive being
    applied to already-typed data. (`fillna("")` on an existing Categorical raises
    rather than no-opping, which is why the str round-trip is not redundant.)"""
    df = df.copy()
    df["id"] = pd.to_numeric(df["id"], errors="coerce").astype("int64")
    df["n_symbols"] = pd.to_numeric(df["n_symbols"], errors="coerce").fillna(0).astype("int16")
    for c in ("headline", "summary", "url", "symbols"):
        df[c] = df[c].astype(str).fillna("")
    for c in ("source", "author"):
        df[c] = df[c].astype(str).fillna("").astype("category")
    df["session"] = df["session"].astype(object)
    return df.sort_values("ts").reset_index(drop=True)


def write(df: pd.DataFrame, verbose: bool = False) -> dict[str, int]:
    """Merge into the month partitions the articles span. Returns {month: rows}.

    Partitioned by the article's own MONTH OF PUBLICATION, not by session, so a
    late-evening article on the last day of a month stays in that month's file
    even though its session is the 1st of the next. Partition layout and session
    semantics are deliberately independent: re-attributing sessions after a
    calendar correction must never have to move rows between files.
    """
    if df.empty:
        return {}

    df = _typed(df)
    df["_m"] = df["ts"].dt.tz_convert("UTC").dt.strftime("%Y-%m")

    written: dict[str, int] = {}
    config.NEWS.mkdir(parents=True, exist_ok=True)

    for month, chunk in df.groupby("_m", sort=True):
        path = part_path(str(month))
        chunk = chunk.drop(columns="_m")
        if path.exists():
            old = pd.read_parquet(path)
            # `chunk` last so a re-fetch overwrites a stored article -- required
            # because Benzinga revises in place and the revision is the version
            # whose text we scored.
            merged = pd.concat([old, chunk], ignore_index=True)
            merged = merged.drop_duplicates("id", keep="last")
        else:
            merged = chunk
        merged = _typed(merged)

        tmp = path.with_suffix(".parquet.tmp")
        merged.to_parquet(tmp, compression=config.COMPRESSION,
                          compression_level=config.COMPRESSION_LEVEL, index=False)
        store.atomic_replace(tmp, path)   # retries the Windows sharing violation
        written[str(month)] = len(merged)
        if verbose:
            print(f"      {month}.parquet  {len(merged):,} rows")

    return written


def read(start: str | None = None, end: str | None = None,
         tickers: list[str] | None = None,
         columns: list[str] | None = None,
         by_session: bool = True) -> pd.DataFrame:
    """Read the store, opening only the months that can contain [start, end].

    `by_session=True` filters on the ATTRIBUTED session, which is what every
    caller downstream actually means by "news up to this date". Because an
    article can be attributed to the following month, the month window is widened
    by one on each side before filtering.
    """
    want = months()
    if start:
        lo = (pd.Timestamp(start) - pd.offsets.MonthBegin(1)).strftime("%Y-%m")
        want = [m for m in want if m >= lo]
    if end:
        hi = (pd.Timestamp(end) + pd.offsets.MonthBegin(1)).strftime("%Y-%m")
        want = [m for m in want if m <= hi]
    if not want:
        return pd.DataFrame(columns=columns or SCHEMA)

    key = "session" if by_session else None
    frames = []
    for m in want:
        df = pd.read_parquet(part_path(m), columns=columns)
        if key and key in df.columns:
            if start:
                df = df[df[key].notna() & (df[key].astype(str) >= start)]
            if end:
                df = df[df[key].notna() & (df[key].astype(str) <= end)]
        if not df.empty:
            frames.append(df)

    if not frames:
        return pd.DataFrame(columns=columns or SCHEMA)
    out = pd.concat(frames, ignore_index=True)

    if tickers:
        tset = set(tickers)
        keep = out["symbols"].apply(
            lambda s: bool(tset & set(s.split(","))) if s else False)
        out = out[keep]

    return out.sort_values("ts").reset_index(drop=True)


def explode(df: pd.DataFrame, tickers: list[str] | None = None) -> pd.DataFrame:
    """(article x symbol) long view: one row per (id, ticker).

    Built on read rather than stored. Articles average ~2.5 symbols, so storing
    the exploded form would carry every headline string 2.5 times for a join that
    costs milliseconds.
    """
    if df.empty:
        return df.assign(ticker=pd.Series(dtype=str))
    out = df.copy()
    out["ticker"] = out["symbols"].str.split(",")
    out = out.explode("ticker")
    out = out[out["ticker"].astype(str).str.len() > 0]
    if tickers:
        out = out[out["ticker"].isin(set(tickers))]
    return out.reset_index(drop=True)


def stored_sessions() -> set[str]:
    seen: set[str] = set()
    for m in months():
        s = pd.read_parquet(part_path(m), columns=["session"])["session"]
        seen |= set(s.dropna().astype(str))
    return seen


def store_bytes() -> int:
    if not config.NEWS.exists():
        return 0
    return sum(p.stat().st_size for p in config.NEWS.glob("*.parquet"))


# ===========================================================================
# State / watermark
# ===========================================================================
def load_state() -> dict:
    if config.NEWS_STATE_FILE.exists():
        try:
            return json.loads(config.NEWS_STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_state(st: dict) -> None:
    config.NEWS_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.NEWS_STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(st, indent=2, default=str), encoding="utf-8")
    store.atomic_replace(tmp, config.NEWS_STATE_FILE)


def _max_stored_ts() -> pd.Timestamp | None:
    ms = months()
    if not ms:
        return None
    last = pd.read_parquet(part_path(ms[-1]), columns=["ts"])["ts"]
    return None if last.empty else pd.to_datetime(last.max(), utc=True)


# ===========================================================================
# Update / backfill
# ===========================================================================
def update(verbose: bool = True) -> dict:
    """Fetch everything published since the watermark.

    The watermark is re-derived from the STORE, not trusted from the state file:
    a state file that drifts ahead of the data silently creates a permanent hole,
    and the store is the thing that is actually true.
    """
    st = load_state()
    end = calendar_us.bars_end_ts()

    since = _max_stored_ts()
    if since is None:
        lookback = int(config.NEWS_HISTORY_YEARS * 365.25)
        start = (datetime.now(timezone.utc) - timedelta(days=lookback))
        if verbose:
            print(f"  no news stored; use --backfill for history. "
                  f"Fetching the last day only.")
            start = datetime.now(timezone.utc) - timedelta(days=1)
    else:
        # 1s overlap, deduped on `id` at write: a boundary article must never be
        # skipped, and re-fetching one is free.
        start = (since - pd.Timedelta(seconds=1)).to_pydatetime()

    if verbose:
        print(f"news update: {start:%Y-%m-%d %H:%M}Z -> {end}")

    arts = fetch_range(start, end, verbose=verbose)
    df = to_frame(arts)
    written = write(df, verbose=verbose)

    unattributed = int(df["session"].isna().sum()) if not df.empty else 0
    st.update({
        "last_update": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "last_ts": str(_max_stored_ts()),
        "months": len(months()),
        "store_bytes": store_bytes(),
    })
    save_state(st)

    if verbose and unattributed:
        print(f"    {unattributed} article(s) beyond the last calendar session "
              "(left unattributed rather than clamped)")
    return {"ok": True, "fetched": len(arts), "written": written,
            "unattributed": unattributed}


def backfill(years: float | None = None, verbose: bool = True,
             chunk_days: int | None = None) -> dict:
    """Full history in resumable week-sized chunks.

    MEASURED cost: ~1,300 articles/day over 4 years is ~1.3M articles, ~26,000
    pages, ~2.5h under the 170 req/min limiter. Chunked so a 403 or a closed
    laptop costs one week, not the whole run, and so progress survives a restart.
    Chunks already fully covered by the store are skipped.
    """
    years = config.NEWS_HISTORY_YEARS if years is None else years
    chunk_days = config.NEWS_BACKFILL_CHUNK_DAYS if chunk_days is None else chunk_days

    st = load_state()
    first = datetime.now(timezone.utc) - timedelta(days=int(years * 365.25))
    last = pd.Timestamp(calendar_us.bars_end_ts()).to_pydatetime()

    have = stored_sessions()
    sessions = set(calendar_us.sessions_between(first.date().isoformat(),
                                                last.date().isoformat()))

    t0 = time.time()
    total = 0
    cur = first
    n_chunks = int((last - first).days / chunk_days) + 1
    i = 0

    while cur < last:
        nxt = min(cur + timedelta(days=chunk_days), last)
        i += 1

        window = {d for d in sessions
                  if cur.date().isoformat() <= d < nxt.date().isoformat()}
        if window and window <= have:
            if verbose:
                print(f"  [{i}/{n_chunks}] {cur:%Y-%m-%d} .. {nxt:%Y-%m-%d}  skip (stored)")
            cur = nxt
            continue

        arts = fetch_range(cur, nxt)
        df = to_frame(arts)
        write(df)
        total += len(arts)
        have |= set(df["session"].dropna().astype(str)) if not df.empty else set()

        if verbose:
            el = time.time() - t0
            rate = total / el if el else 0
            print(f"  [{i}/{n_chunks}] {cur:%Y-%m-%d} .. {nxt:%Y-%m-%d}  "
                  f"{len(arts):>5,} art  ({total:>8,} total, {rate:.0f}/s, "
                  f"{el / 60:.0f}m elapsed)")

        st.update({"backfill_through": nxt.date().isoformat(),
                   "backfill_years": years})
        save_state(st)
        cur = nxt

    st.update({"backfill_done": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "store_bytes": store_bytes(), "months": len(months())})
    save_state(st)

    if verbose:
        print(f"\nbackfill: {total:,} article(s) in {(time.time() - t0) / 60:.1f} min, "
              f"{store_bytes() / 1e6:.0f} MB, {len(months())} month file(s)")
    return {"ok": True, "fetched": total}


# ===========================================================================
# Diagnostics
# ===========================================================================
def stats() -> pd.DataFrame:
    rows = []
    for m in months():
        p = part_path(m)
        df = pd.read_parquet(p, columns=["session", "n_symbols", "source"])
        rows.append({"month": m, "articles": len(df),
                     "sessions": df["session"].dropna().nunique(),
                     "mean_syms": round(float(df["n_symbols"].mean()), 2),
                     "bytes": p.stat().st_size})
    return pd.DataFrame(rows)


def dry_run() -> None:
    """Estimate the incremental cost without writing anything."""
    since = _max_stored_ts()
    end = calendar_us.bars_end_ts()
    print(f"  store          : {len(months())} month(s), {store_bytes() / 1e6:.1f} MB")
    print(f"  last article   : {since if since is not None else '(empty)'}")
    print(f"  would fetch to : {end}")
    if since is None:
        print("  -> store is empty; run --backfill first")
        return
    start = (since - pd.Timedelta(seconds=1)).to_pydatetime()
    days = max((pd.Timestamp(end) - since).total_seconds() / 86400, 0)
    print(f"  window         : {days:.2f} day(s)")
    print(f"  estimate       : ~{days * 1580:,.0f} article(s), "
          f"~{days * 1580 / config.NEWS_PAGE_LIMIT:,.0f} page(s) "
          f"(measured 1,580 art/day on 2026-08-04)")


def explain(ticker: str, days: int = 30) -> None:
    end = calendar_us.last_closed_session()
    start = (pd.Timestamp(end) - pd.Timedelta(days=days)).date().isoformat()
    df = read(start=start, end=end, tickers=[ticker])
    print(f"{ticker}: {len(df)} article(s) attributed to sessions {start} .. {end}")
    if df.empty:
        print("  (none -- this is normal: measured 4/30 live flags had zero news "
              "in 30 days, which is why `has_news` is an explicit state)")
        return
    for _, r in df.iterrows():
        print(f"  {r['session']}  {str(r['ts'])[:16]}  [{r['n_symbols']}syms] "
              f"{r['headline'][:88]}")


def main() -> int:
    config.safe_console()
    ap = argparse.ArgumentParser(description="Alpaca news fetch and store.")
    ap.add_argument("--update", action="store_true", help="incremental fetch")
    ap.add_argument("--backfill", action="store_true", help="full history, resumable")
    ap.add_argument("--years", type=float, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--explain", metavar="SYM", default=None)
    a = ap.parse_args()

    config.dirs()

    if a.dry_run:
        dry_run()
    elif a.backfill:
        backfill(years=a.years)
    elif a.update:
        update()
    elif a.explain:
        explain(a.explain.upper())
    elif a.stats:
        s = stats()
        print(s.to_string(index=False) if not s.empty else "(store is empty)")
        if not s.empty:
            print(f"\n  {s['articles'].sum():,} articles, "
                  f"{s['bytes'].sum() / 1e6:.1f} MB, {len(s)} month(s)")
    else:
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
