"""
FINRA Reg SHO daily short-sale volume -> month-partitioned parquet.

    python finra.py --backfill --years 6     resumable, one file per session
    python finra.py --update                 anything since the watermark
    python finra.py --stats
    python finra.py --selftest

WHAT THIS IS, AND WHAT IT IS NOT
----------------------------------
This is **daily short VOLUME**: how many shares were sold short that session,
from FINRA's consolidated Reg SHO file. It is NOT short INTEREST (total shares
held short, which gives days-to-cover).

Short interest was the first choice and it is **not freely available any more**:
`cdn.finra.org/equity/otcmarket/biweekly/shrt<date>.txt` returns
**403 AccessDenied** as of 2026-08-07, tested across three settlement dates.
Every other free route to it (Nasdaq, exchange sites) is either a scrape behind
a bot wall or a paid API. So days-to-cover is a genuine gap, not an oversight.

Daily short volume is arguably the better input for a DAILY module anyway: it
measures the pressure that exists today, where biweekly short interest is up to
two weeks stale by the time it publishes.

HISTORY FLOOR, MEASURED
-------------------------
2020-01-02 works; 2017-01-03 and 2016-08-01 both return 403. So roughly six
years, which comfortably covers the 252-session baseline hype needs.

WHY ONLY UNIVERSE TICKERS ARE STORED
--------------------------------------
Each daily file carries ~9,900 symbols including OTC. Filtering to the tradeable
universe (~3,400) cuts the store from roughly 225 MB to ~80 MB for six years,
and the discarded names are ones no module scores.

READ THE RATIO, NOT THE LEVEL
-------------------------------
`short_vol / total_vol` is meaningful; `short_vol` alone is not, because the
denominator here is FINRA-reported volume, not full consolidated tape volume.
Comparing the raw level against the bar store's `volume` column would silently
mix two different denominators.
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from io import StringIO

import pandas as pd
import requests

import config
import store

BASE = "https://cdn.finra.org/equity/regsho/daily"
SCHEMA = ["date", "ticker", "short_vol", "short_exempt", "total_vol"]
HISTORY_FLOOR = "2020-01-01"     # measured: 2017 and 2016 return 403
WORKERS = 6                      # polite; the files are small and static
STATE_KEY = "finra_last"


def log(m: str) -> None:
    print(m, flush=True)


def part_path(month: str):
    return config.SHORTVOL / f"{month}.parquet"


def months() -> list[str]:
    if not config.SHORTVOL.exists():
        return []
    return sorted(p.stem for p in config.SHORTVOL.glob("*.parquet"))


def stored_dates() -> set[str]:
    out: set[str] = set()
    for m in months():
        try:
            out |= set(pd.read_parquet(part_path(m), columns=["date"])
                       ["date"].astype(str).unique())
        except Exception:                                        # noqa: BLE001
            continue
    return out


def fetch_day(d: str, session: requests.Session | None = None) -> pd.DataFrame:
    """One session's file. `d` is YYYY-MM-DD. Empty frame on 403/404 (holiday,
    or before the history floor) -- that is a normal outcome, not an error."""
    ymd = d.replace("-", "")
    url = f"{BASE}/CNMSshvol{ymd}.txt"
    hdr = {"User-Agent": config.SEC_UA}
    get = (session or requests).get
    try:
        r = get(url, headers=hdr, timeout=45)
    except requests.RequestException:
        return pd.DataFrame(columns=SCHEMA)
    if r.status_code != 200 or not r.text.startswith("Date|"):
        return pd.DataFrame(columns=SCHEMA)

    df = pd.read_csv(StringIO(r.text), sep="|", dtype=str,
                     on_bad_lines="skip")
    df = df.rename(columns={"Symbol": "ticker", "ShortVolume": "short_vol",
                            "ShortExemptVolume": "short_exempt",
                            "TotalVolume": "total_vol"})
    if "ticker" not in df.columns:
        return pd.DataFrame(columns=SCHEMA)
    df["date"] = d
    for c in ("short_vol", "short_exempt", "total_vol"):
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    df = df.dropna(subset=["ticker", "total_vol"])
    df = df[df["total_vol"] > 0]
    return df[SCHEMA]


def _typed(df: pd.DataFrame) -> pd.DataFrame:
    """Idempotent -- write() may call it twice. Same rule as store._typed."""
    df = df.copy()
    df["date"] = df["date"].astype(str)
    if not isinstance(df["ticker"].dtype, pd.CategoricalDtype):
        df["ticker"] = df["ticker"].astype(str).astype("category")
    for c in ("short_vol", "short_exempt", "total_vol"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float32")
    return df[SCHEMA]


def write(df: pd.DataFrame, verbose: bool = False) -> dict[str, int]:
    """Merge into month partitions, deduped on (date, ticker)."""
    if df.empty:
        return {}
    config.SHORTVOL.mkdir(parents=True, exist_ok=True)
    written: dict[str, int] = {}
    df = df.copy()
    df["_m"] = df["date"].str.slice(0, 7)
    for month, chunk in df.groupby("_m", observed=True):
        chunk = chunk.drop(columns=["_m"])
        p = part_path(str(month))
        if p.exists():
            try:
                chunk = pd.concat([pd.read_parquet(p), chunk], ignore_index=True)
            except Exception:                                    # noqa: BLE001
                pass
        chunk = _typed(chunk).drop_duplicates(subset=["date", "ticker"],
                                              keep="last")
        tmp = p.with_suffix(".parquet.tmp")
        chunk.to_parquet(tmp, compression=config.COMPRESSION,
                         compression_level=config.COMPRESSION_LEVEL, index=False)
        store.atomic_replace(tmp, p)     # retries the Windows sharing violation
        written[str(month)] = len(chunk)
        if verbose:
            log(f"      {month}.parquet  {len(chunk):,} rows")
    return written


def read(start: str | None = None, end: str | None = None,
         tickers: list[str] | None = None) -> pd.DataFrame:
    """Opens only the months that can contain [start, end]."""
    ms = months()
    if start:
        ms = [m for m in ms if m >= start[:7]]
    if end:
        ms = [m for m in ms if m <= end[:7]]
    if not ms:
        return pd.DataFrame(columns=SCHEMA)
    tset = set(tickers) if tickers else None
    out = []
    for m in ms:
        try:
            d = pd.read_parquet(part_path(m))
        except Exception:                                        # noqa: BLE001
            continue
        if tset is not None:
            d = d[d["ticker"].astype(str).isin(tset)]
        out.append(d)
    if not out:
        return pd.DataFrame(columns=SCHEMA)
    df = pd.concat(out, ignore_index=True)
    if start:
        df = df[df["date"] >= start]
    if end:
        df = df[df["date"] <= end]
    return df


def _sessions(years: float) -> list[str]:
    import calendar_us
    start = max(HISTORY_FLOOR,
                (date.today() - timedelta(days=int(years * 365.25))).isoformat())
    asof = calendar_us.last_closed_session()
    return [s for s in calendar_us.all_sessions() if start <= s <= asof]


def backfill(years: float | None = None, verbose: bool = True,
             universe_only: bool = True) -> dict:
    """Every session in the window. Resumable: stored dates are skipped."""
    years = config.SHORTVOL_YEARS if years is None else years
    todo = [d for d in _sessions(years) if d not in stored_dates()]
    if not todo:
        if verbose:
            log(f"  short volume: nothing to fetch ({len(months())} month(s))")
        return {"ok": True, "rows": 0, "days": 0}

    uni = None
    if universe_only:
        try:
            import bars
            uni = set(bars.tradeable_universe())
        except Exception:                                        # noqa: BLE001
            uni = None

    t0, rows, days, empty = time.time(), 0, 0, 0
    sess = requests.Session()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for i in range(0, len(todo), 40):
            batch = todo[i:i + 40]
            frames = list(ex.map(lambda d: fetch_day(d, sess), batch))
            good = [f for f in frames if not f.empty]
            empty += len(batch) - len(good)
            if good:
                df = pd.concat(good, ignore_index=True)
                if uni:
                    df = df[df["ticker"].astype(str).isin(uni)]
                write(df)
                rows += len(df)
                days += len(good)
            if verbose:
                el = (time.time() - t0) / 60
                done = min(i + 40, len(todo))
                log(f"    {done}/{len(todo)} session(s), {rows:,} rows, "
                    f"{el:.1f}m, eta {el / max(done,1) * (len(todo)-done):.0f}m")
    if verbose:
        log(f"  short volume: {rows:,} rows over {days} session(s), "
            f"{empty} unavailable, {store_bytes() / 1e6:.0f} MB, "
            f"{(time.time() - t0) / 60:.1f}m")
    return {"ok": True, "rows": rows, "days": days, "unavailable": empty}


def update(verbose: bool = True) -> dict:
    """Everything since the last stored date. The watermark is the STORE, not a
    state file -- a state file that drifts ahead creates a permanent hole."""
    have = stored_dates()
    if not have:
        return backfill(verbose=verbose)
    import calendar_us
    asof = calendar_us.last_closed_session()
    todo = [s for s in calendar_us.all_sessions()
            if s > max(have) and s <= asof]
    if not todo:
        return {"ok": True, "rows": 0, "days": 0}
    uni = None
    try:
        import bars
        uni = set(bars.tradeable_universe())
    except Exception:                                            # noqa: BLE001
        pass
    sess = requests.Session()
    frames = [fetch_day(d, sess) for d in todo[-20:]]
    good = [f for f in frames if not f.empty]
    if not good:
        return {"ok": True, "rows": 0, "days": 0}
    df = pd.concat(good, ignore_index=True)
    if uni:
        df = df[df["ticker"].astype(str).isin(uni)]
    write(df)
    if verbose:
        log(f"  short volume: +{len(df):,} rows over {len(good)} session(s)")
    return {"ok": True, "rows": len(df), "days": len(good)}


def store_bytes() -> int:
    return sum(p.stat().st_size for p in config.SHORTVOL.glob("*.parquet")) \
        if config.SHORTVOL.exists() else 0


def stats() -> None:
    ms = months()
    d = stored_dates()
    print(f"\n  short volume store: {len(ms)} month(s), {len(d):,} session(s), "
          f"{store_bytes() / 1e6:.1f} MB")
    if d:
        print(f"  span: {min(d)} -> {max(d)}")
    print()


def selftest(verbose: bool = True) -> None:
    # A 403/holiday must yield an EMPTY frame, never raise and never a partial.
    empty = fetch_day("1990-01-02")
    assert empty.empty and list(empty.columns) == SCHEMA, empty.columns.tolist()

    df = pd.DataFrame({"date": ["2026-08-05", "2026-08-05"],
                       "ticker": ["AAA", "AAA"],
                       "short_vol": [10.0, 11.0], "short_exempt": [0.0, 0.0],
                       "total_vol": [100.0, 100.0]})
    t = _typed(_typed(df))            # must be idempotent
    assert len(t) == 2 and str(t["ticker"].dtype) == "category"
    assert t.drop_duplicates(subset=["date", "ticker"], keep="last") \
        ["short_vol"].iloc[0] == 11.0, "dedupe must keep the LAST row"
    if verbose:
        print(f"  [finra] schema ok, 403 yields empty, _typed idempotent; "
              f"{len(months())} month(s) stored")


def main() -> int:
    ap = argparse.ArgumentParser(description="FINRA Reg SHO daily short volume.")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--years", type=float)
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    config.dirs()
    if a.selftest:
        selftest()
    elif a.backfill:
        backfill(a.years)
    elif a.update:
        update()
    elif a.stats:
        stats()
    else:
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
