"""
Alpaca REST access: raw `requests`, no SDK (matching the sibling project, and
alpaca-py is not installed).

Adapted from `Trading Analysis/WaveTrend and Squeeze Indicator/alpaca_fetch.py`,
which contributed the token-bucket limiter, the thread-local Session, and the
pagination-that-raises-rather-than-truncating property. Three changes were
required, each fixing a bug that would otherwise be silent or fatal:

  1. RETRY 403. The original calls raise_for_status() on any non-429/non-5xx, so
     the free-tier SIP "recent data" 403 crashes the run. We retry it, but
     inspect the body first: "recent SIP data" means the request is permanently
     wrong for this feed, so it fails fast with a diagnostic naming the offending
     `end` rather than burning 15 attempts.

  2. HANDLE 400 BY BISECTION. One malformed symbol 400s an entire 400-symbol
     batch. We parse the offending symbol out of the body, drop it, and retry
     once, so one bad ticker never costs 400 good ones.

  3. ACCUMULATE, DON'T ASSIGN. Pagination resumes MID-SYMBOL (a page token
     decodes to `NVDA|D|1717387200000`), so a symbol's bars can span two pages.
     The original fetched one symbol at a time and so never hit this; batched
     requests must `extend` per-symbol lists, never replace them.

Nothing here prints or logs a secret.
"""

from __future__ import annotations

import collections
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone

import requests

import config

TF_MAP = {"1d": "1Day", "1h": "1Hour", "15m": "15Min"}

_BAD_SYMBOL_RE = re.compile(r"invalid symbol[s]?[:\s]+([A-Za-z0-9.\-/,\s]+)", re.I)


class SipWindowError(RuntimeError):
    """The request's `end` falls inside the free tier's 15-minute SIP embargo.

    Raised instead of retrying because no amount of waiting fixes it -- the
    caller passed a timestamp that is too recent for this subscription. The
    message names the offending `end` so the fix is obvious.
    """


class RateLimiter:
    """Global cap of `rate` requests per `per` seconds, shared across threads."""

    def __init__(self, rate: int = config.RATE_LIMIT, per: float = config.RATE_PER):
        self.rate, self.per = rate, per
        self.lock = threading.Lock()
        self.times: collections.deque[float] = collections.deque()

    def acquire(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                while self.times and now - self.times[0] > self.per:
                    self.times.popleft()
                if len(self.times) < self.rate:
                    self.times.append(now)
                    return
                wait = self.per - (now - self.times[0])
            time.sleep(min(max(wait, 0.01), 1.0))


LIMITER = RateLimiter()

_local = threading.local()


def _sess() -> requests.Session:
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
    return _local.s


def _headers() -> dict[str, str]:
    kid, sec = config.creds()
    return {"APCA-API-KEY-ID": kid, "APCA-API-SECRET-KEY": sec}


def _get(url: str, params: dict | None = None, attempts: int = config.HTTP_ATTEMPTS) -> dict:
    """One GET, robustly.

    Retries 429/403/5xx/network with capped backoff. RAISES on hard failure so a
    truncated series can never be mistaken for a complete one -- a silently short
    history produces a WRONG pattern verdict rather than a visible error, which
    is far worse than a crash.
    """
    s = _sess()
    last: object = None
    for attempt in range(attempts):
        LIMITER.acquire()
        try:
            r = s.get(url, headers=_headers(), params=params, timeout=config.HTTP_TIMEOUT)
        except requests.RequestException as exc:
            last = exc
            time.sleep(min(1.5 * (attempt + 1), 20))
            continue

        if r.status_code == 403:
            body = r.text[:400]
            if "recent sip data" in body.lower():
                raise SipWindowError(
                    "Alpaca refused the request: free-tier SIP cannot query the "
                    f"last 15 minutes. end={(params or {}).get('end')!r}. Pass an "
                    "`end` from calendar_us.last_closed_session(), or set "
                    f"config.FEED = {config.FEED_FALLBACK!r}."
                )
            last = body
            time.sleep(min(1.5 * (attempt + 1), 20))
            continue

        if r.status_code == 429 or r.status_code >= 500:
            last = f"HTTP {r.status_code}"
            time.sleep(min(1.5 * (attempt + 1), 20))
            continue

        if r.status_code == 400:
            # Surfaced to the caller with the body intact so a batch fetch can
            # extract the offending symbol and retry without it.
            raise requests.HTTPError(f"400 {r.text[:400]}", response=r)

        r.raise_for_status()
        return r.json()

    raise RuntimeError(f"request failed after {attempts} attempts: {last}")


def bad_symbols_from_400(exc: Exception) -> list[str]:
    """Extract symbols Alpaca called invalid out of a 400 body."""
    m = _BAD_SYMBOL_RE.search(str(exc))
    if not m:
        return []
    return [s.strip().upper() for s in m.group(1).split(",") if s.strip()]


# ---------------------------------------------------------------- bars
def _as_iso(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return str(v)


# Free-tier SIP refuses anything inside this window. 16 rather than 15 so a
# borderline clock skew fails our own check (clear message) instead of Alpaca's.
_SIP_EMBARGO = timedelta(minutes=16)


def _assert_end_ok(end_iso: str) -> None:
    """Reject an `end` that free-tier SIP will 403, with a message that says why."""
    if config.FEED != "sip":
        return
    now = datetime.now(timezone.utc)

    if len(end_iso) == 10:            # bare date -> resolves to END of that day
        eod = datetime.fromisoformat(end_iso).replace(
            hour=23, minute=59, second=59, tzinfo=timezone.utc)
        if eod > now - _SIP_EMBARGO:
            raise SipWindowError(
                f"end={end_iso!r} is a bare date, which resolves to END-of-day "
                f"({eod:%Y-%m-%d %H:%M}Z) -- still inside the free-tier SIP "
                "embargo, so this would 403. Pass a timestamp from "
                "calendar_us.bars_end_ts() instead, or use an older date."
            )
        return

    try:
        ts = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
    except ValueError:
        return                        # unparseable: let the API adjudicate
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    if ts > now - _SIP_EMBARGO:
        raise SipWindowError(
            f"end={end_iso!r} is within {_SIP_EMBARGO.seconds // 60} minutes of "
            "now; free-tier SIP would 403. Use calendar_us.bars_end_ts()."
        )


def fetch_bars(
    symbols: list[str],
    start,
    end,
    timeframe: str = "1d",
    feed: str | None = None,
    limit: int = 10_000,
) -> dict[str, list[dict]]:
    """Batched multi-symbol bars, fully paginated.

    Returns {symbol: [bar, ...]} keyed by the symbol Alpaca echoed back (dot
    form). Symbols with no data are simply absent -- a well-formed but
    nonexistent symbol is silently omitted by the API with a 200, so absence is
    not an error.

    `end` must not reach into the free tier's 15-minute SIP embargo. Note that a
    bare DATE resolves to the END of that day, so `end=<today>` is in the future
    until midnight and 403s even hours after the close -- pass a timestamp from
    calendar_us.bars_end_ts() when you want today's session. Checked here rather
    than discovered as a mystery 403 at runtime.
    """
    if not symbols:
        return {}

    end_iso = _as_iso(end)
    if end_iso:
        _assert_end_ok(end_iso)

    tf = TF_MAP[timeframe]
    out: dict[str, list[dict]] = {}
    token: str | None = None
    url = f"{config.DATA_BASE}/v2/stocks/bars"

    while True:
        params = {
            "symbols": ",".join(symbols),
            "timeframe": tf,
            "start": _as_iso(start),
            "limit": limit,
            "feed": feed or config.FEED,
            "adjustment": config.ADJUSTMENT,
            "sort": "asc",
        }
        if end_iso:
            params["end"] = end_iso
        if token:
            params["page_token"] = token

        payload = _get(url, params)
        for sym, arr in (payload.get("bars") or {}).items():
            # EXTEND, never assign: a symbol's bars can span pages.
            out.setdefault(sym, []).extend(arr or [])

        token = payload.get("next_page_token")
        if not token:
            return out


# ---------------------------------------------------------------- assets
def fetch_assets(status: str = "active", asset_class: str = "us_equity") -> list[dict]:
    """Full asset list. One request, no pagination (~6.4 MB, ~14,180 rows).

    Uses TRADING_BASE, which defaults to paper-api because the keys in the
    sibling .env are paper keys and live api.alpaca.markets returns 401 for them.
    The asset payload is identical on both hosts.
    """
    return _get(
        f"{config.TRADING_BASE}/v2/assets",
        {"status": status, "asset_class": asset_class},
    )


# ---------------------------------------------------------------- calendar
def fetch_calendar(start, end) -> list[dict]:
    """Trading sessions. Public endpoint; works on either host."""
    return _get(
        f"{config.CALENDAR_BASE}/v2/calendar",
        {"start": _as_iso(start), "end": _as_iso(end)},
    )


# ---------------------------------------------------------------- diagnostics
def whoami() -> dict:
    """Print the resolved configuration and prove the keys work. No secrets."""
    kid, _ = config.creds()
    info: dict[str, object] = {
        "trading_base": config.TRADING_BASE,
        "data_base": config.DATA_BASE,
        "feed": config.FEED,
        "adjustment": config.ADJUSTMENT,
        "key_id_prefix": kid[:4] + "..." + kid[-2:],
    }

    try:
        acct = _get(f"{config.TRADING_BASE}/v2/account", attempts=3)
        info["account_id_prefix"] = str(acct.get("id", ""))[:8] + "..."
        info["account_status"] = acct.get("status")
        info["is_paper"] = "paper" in config.TRADING_BASE
        info["account_ok"] = True
    except Exception as exc:
        info["account_ok"] = False
        info["account_error"] = repr(exc)[:200]

    try:
        cal = fetch_calendar("2026-01-02", "2026-01-06")
        info["calendar_ok"] = len(cal) > 0
        info["calendar_sample"] = cal[0].get("date") if cal else None
    except Exception as exc:
        info["calendar_ok"] = False
        info["calendar_error"] = repr(exc)[:200]

    for k, v in info.items():
        print(f"  {k:22} {v}")
    return info


if __name__ == "__main__":
    import sys

    config.safe_console()
    print("Alpaca configuration:")
    result = whoami()
    ok = result.get("account_ok") and result.get("calendar_ok")
    print("\n" + ("OK" if ok else "FAILED"))
    sys.exit(0 if ok else 1)

