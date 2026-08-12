"""
US equity trading calendar, from Alpaca's /v2/calendar.

Why not the SPY-probe trick used in `Stock Screener/fetch.py`: it calls
yf.download, which drags a second data vendor (and a second failure mode) in for
one function, and it cannot distinguish "the market was closed" from "the SPY
fetch failed" -- exactly the ambiguity that makes a scheduled job silently do
nothing for a week.

Two distinct notions of "the end of the data", and conflating them is the single
most expensive mistake available here:

  last_closed_session()  a DATE. The most recent session that has finished
                         settling. Used to decide which bars to KEEP -- anything
                         dated later is an in-progress bar whose high/low/close
                         will still change.

  bars_end_ts()          a TIMESTAMP. What to pass as the API's `end`. It must be
                         a *timestamp*, not a date: a bare date resolves to
                         END-of-day, so `end=<today>` is in the future until
                         midnight and free-tier SIP 403s it even hours after the
                         close. A timestamp of now-20min is always both valid and
                         past the 15-minute SIP embargo.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone

import pandas as pd

import alpaca
import config

ET = "America/New_York"

# How long after the official close a session is treated as final. The SIP
# embargo is 15 minutes; 25 gives margin for late prints without pushing the
# 03:00 PKT run (= 17:00 ET) past its own window.
SETTLE_MINUTES = 25

# How recent an `end` timestamp may be. Must exceed the 15-minute SIP embargo.
END_LAG_MINUTES = 20

REFRESH_DAYS = 7
FORWARD_DAYS = 400
# Must reach back at least as far as the bar store, plus the indicator warmup and
# structural window the detector needs (IND_WARMUP + STRUCT_WIN = 890 sessions).
# A calendar shorter than the data silently clamps every lookback and every
# ticker fails SHORT_HISTORY -- which reads as "no history" rather than as a
# calendar problem.
BACK_DAYS = int(config.HISTORY_YEARS * 365.25) + 400


def _now_et() -> pd.Timestamp:
    return pd.Timestamp.now(tz=ET)


def load() -> pd.DataFrame:
    """Cached sessions: columns [date, open, close]. Empty frame if no cache."""
    if config.CALENDAR_FILE.exists():
        return pd.read_parquet(config.CALENDAR_FILE)
    return pd.DataFrame(columns=["date", "open", "close"])


def _save(df: pd.DataFrame) -> None:
    config.CALENDAR_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.CALENDAR_FILE.with_suffix(".parquet.tmp")
    df.sort_values("date").reset_index(drop=True).to_parquet(
        tmp, compression=config.COMPRESSION,
        compression_level=config.COMPRESSION_LEVEL, index=False)
    tmp.replace(config.CALENDAR_FILE)


def refresh(force: bool = False) -> pd.DataFrame:
    """Fetch the session window. Cheap; a weekly refresh is plenty."""
    today = date.today()
    need_back = (today - timedelta(days=BACK_DAYS)).isoformat()

    cached = load()
    if not force and not cached.empty:
        newest = pd.Timestamp(cached["date"].max())
        oldest = str(cached["date"].min())
        # BOTH ends must be adequate. Checking only the future end is how a cache
        # that no longer reaches back far enough survives indefinitely.
        reaches_forward = (newest - pd.Timestamp(today)).days > REFRESH_DAYS
        reaches_back = oldest <= need_back
        if reaches_forward and reaches_back:
            return cached

    rows = alpaca.fetch_calendar(today - timedelta(days=BACK_DAYS),
                                 today + timedelta(days=FORWARD_DAYS))
    df = pd.DataFrame(rows)
    if df.empty:
        return cached
    # Alpaca returns session_open/session_close (extended) plus open/close (regular).
    keep = ["date", "open", "close"]
    df = df[[c for c in keep if c in df.columns]].copy()
    df["date"] = df["date"].astype(str)
    _save(df)
    return df


def _sessions(df: pd.DataFrame | None = None) -> pd.DataFrame:
    df = refresh() if df is None else df
    if df.empty:
        raise RuntimeError(
            "No trading calendar available and the fetch failed. "
            "Run `python calendar_us.py --refresh` while online."
        )
    return df


def all_sessions() -> list[str]:
    return _sessions()["date"].tolist()


def last_closed_session(now: pd.Timestamp | None = None) -> str:
    """The most recent session that has finished settling, as 'YYYY-MM-DD'.

    Never returns today before today's close + SETTLE_MINUTES. That single
    property is both the partial-bar guard and half the SIP-403 guard.
    """
    df = _sessions()
    now = _now_et() if now is None else now.tz_convert(ET)
    today = now.date().isoformat()

    past = df[df["date"] < today]
    row = df[df["date"] == today]

    if not row.empty:
        close_s = str(row.iloc[0].get("close") or "16:00")
        try:
            hh, mm = (int(x) for x in close_s.split(":")[:2])
        except ValueError:
            hh, mm = 16, 0
        finalised = now.normalize() + pd.Timedelta(
            hours=hh, minutes=mm + SETTLE_MINUTES)
        if now >= finalised:
            return today

    if past.empty:
        raise RuntimeError("Calendar contains no session before today.")
    return str(past["date"].iloc[-1])


def bars_end_ts(now: datetime | None = None) -> str:
    """A UTC ISO timestamp safe to pass as the API's `end`.

    A timestamp, deliberately, not a date -- see the module docstring. Lagged by
    END_LAG_MINUTES so it always sits outside the free-tier SIP embargo.
    """
    now = datetime.now(timezone.utc) if now is None else now
    ts = now - timedelta(minutes=END_LAG_MINUTES)
    return ts.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sessions_between(start: str, end: str) -> list[str]:
    df = _sessions()
    m = (df["date"] >= str(start)) & (df["date"] <= str(end))
    return df.loc[m, "date"].tolist()


def session_offset(sessions: list[str], anchor: str, back: int) -> str:
    """The session `back` sessions before `anchor` (clamped to the earliest)."""
    try:
        i = sessions.index(anchor)
    except ValueError:
        return sessions[0]
    return sessions[max(0, i - back)]


def missing_sessions(stored: set[str], start: str, end: str) -> list[str]:
    """Sessions in [start, end] that are not present locally."""
    return [d for d in sessions_between(start, end) if d not in stored]


def start_for_history(years: int = config.HISTORY_YEARS) -> str:
    """Calendar start date for a `years`-long history window."""
    return (date.today() - timedelta(days=int(years * 365.25) + 10)).isoformat()


def main() -> int:
    config.safe_console()
    ap = argparse.ArgumentParser(description="US trading calendar cache.")
    ap.add_argument("--refresh", action="store_true", help="force a refetch")
    a = ap.parse_args()

    df = refresh(force=a.refresh)
    now = _now_et()
    lcs = last_closed_session()
    sess = df["date"].tolist()
    upcoming = [d for d in sess if d > lcs]

    print(f"sessions cached      {len(sess)}  ({sess[0]} -> {sess[-1]})")
    print(f"now (ET)             {now:%Y-%m-%d %H:%M:%S %Z}")
    print(f"last_closed_session  {lcs}")
    print(f"next session         {upcoming[0] if upcoming else '(none cached)'}")
    print(f"bars_end_ts          {bars_end_ts()}")

    today = now.date().isoformat()
    if lcs == today:
        print(f"  note: today ({today}) has closed and settled; it is included.")
    else:
        print(f"  note: today ({today}) is not final yet; bars dated >= {today} "
              "will be dropped.")

    assert lcs <= today, "last_closed_session must never be in the future"
    print("\nOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

