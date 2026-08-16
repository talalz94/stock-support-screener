"""
Second-opinion fundamentals from a commercial provider, for the numbers a
human actually looks up.

WHY THIS EXISTS
------------------
The SEC bulk Financial Statement Data Sets are the right backbone for this
project: they are the only free source of POINT-IN-TIME history, which is what
the study needs -- what was knowable on 2019-04-11, not what we know now. No
free API offers that, so SEC is not replaceable.

But they have two properties that made the screener look wrong:

  1. THEY LAG. The sets are published quarterly. On 2026-08-13 our newest
     stored row for VSBC was `ddate 2024-01-31` while the company had filed
     through 2026-04-30. The stored figure was CORRECT for its period and
     two years out of date, which on a page reads simply as wrong.
  2. THEY ARE RAW. Every displayed ratio is aggregation we perform ourselves,
     and each step is a place to be wrong -- which is where both 2026-08-13
     bugs lived (stale-annual TTM, share counts summed as flows).

Yahoo publishes the number Google shows. So when the user checks a P/E against
Google and it disagrees with ours, THAT is the comparison that matters, and it
is the one we can now make automatically instead of asking them to eyeball it.

WHAT THIS IS NOT
-------------------
Not a replacement for SEC, and not a source of truth to copy blindly. Yahoo is
unofficial, un-versioned, has no point-in-time history, and is wrong sometimes
too. Treating it as gospel would swap one unverified source for another.

It is a SECOND OPINION. Where the two agree, confidence is real because two
independent pipelines landed on the same number. Where they disagree, the page
says so rather than picking a winner silently -- disagreement is information,
and hiding it is how the last month went.

    python providers.py --tickers AAPL,KO      show both sides
    python providers.py --n 150 --compare      measure agreement at scale
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import warnings
from datetime import datetime

import pandas as pd

import config

warnings.filterwarnings("ignore")

CACHE = config.DATA / "_yahoo_fund.parquet"
# Fundamentals move on filing dates, not by the minute. A day-long cache keeps
# a page build from re-fetching what a batch step already pulled, and keeps us
# well clear of the IP rate limiting that has no documented limit to respect.
TTL_HOURS = 20
# Names per disk checkpoint during a long fetch. 100 keeps a crash cheap
# (seconds of work) without turning the run into a parquet-rewrite benchmark.
CHECKPOINT_EVERY = 100

# Yahoo key -> our column. Only fields that map to something we already compute,
# so the comparison is like for like.
FIELDS = {
    "currentPrice":       "price",
    "regularMarketPrice": "price_alt",
    "trailingPE":         "pe",
    "trailingEps":        "eps_ttm",
    "marketCap":          "mktcap",
    "sharesOutstanding":  "shares_out",
    "totalRevenue":       "revenue_ttm",
    "priceToBook":        "pb",
    "bookValue":          "book_per_share",
    "returnOnEquity":     "roe",
    "profitMargins":      "net_margin",
    "debtToEquity":       "debt_to_equity",
    "enterpriseValue":    "ev",
    "currency":           "currency",
    # --- everything the OVERLAY needs, so one cached fetch serves both ---
    "enterpriseToEbitda":          "ev_ebitda",
    "enterpriseToRevenue":         "ev_sales",
    "trailingPegRatio":            "peg",
    "returnOnAssets":              "roa",
    "grossMargins":                "gross_margin",
    "operatingMargins":            "op_margin",
    "ebitdaMargins":               "ebitda_margin",
    "currentRatio":                "current_ratio",
    "quickRatio":                  "quick_ratio",
    "revenueGrowth":               "revenue_growth",
    "earningsGrowth":              "eps_growth",
    "payoutRatio":                 "payout_ratio",
    "dividendYield":               "dividend_yield",
    "netIncomeToCommon":           "net_income_ttm",
    # NOTE: `trailingEps` is deliberately NOT repeated here. It already maps to
    # `eps_ttm` above, and a dict literal keeps only the LAST value for a
    # duplicated key -- listing it twice silently deleted `eps_ttm`, which the
    # comparison depends on. `eps_diluted_ttm` is aliased from it in `metrics`.
    "ebitda":                      "ebitda",
    "freeCashflow":                "fcf",
    "totalDebt":                   "total_debt",
    "totalCash":                   "total_cash",
}

# YAHOO NEEDS MULTIPLIERS TOO, and assuming it did not was a real bug.
#
# THESE ARE MEASURED, NOT DOCUMENTED -- and that distinction is the point.
#
# yfinance publishes NO field-level specification. Its own reference for
# `Ticker.info` (ranaroussi.github.io/yfinance) states only that it returns
# "a dict": no field list, no types, no units. It is an unofficial client for
# undocumented Yahoo endpoints, so there is nothing to read and the wire format
# can change without notice or version bump.
#
# Contrast the Finnhub table above, where every unit is taken from a published
# spec. The two multipliers below were found by COMPARING the two providers
# field by field, because that was the only method available:
#
#     debtToEquity     Yahoo 78.44  (PERCENT)   Finnhub 0.7844 (ratio)
#     dividendYield    Yahoo 0.36   (PERCENT)   Finnhub 0.357  (percent)
#
# Both were 100x out, and both are on the undocumented side -- the documented
# side was right first time. Neither showed up in a selftest that checked three
# hand-picked fields on one ticker; `_selftest_cross_scale` compares EVERY
# shared field across five tickers, which is what found them.
#
# BECAUSE THESE REST ON MEASUREMENT RATHER THAN SPEC, the cross-scale selftest
# is not optional decoration -- it is the only thing holding them true, and it
# runs before the nightly sweep writes anything.
#
# Convention for the store: ratios and margins are FRACTIONS (0.276, not 27.6).
YAHOO_SCALE: dict[str, float] = {
    "debt_to_equity": 0.01,        # percent -> ratio
    "dividend_yield": 0.01,        # percent -> fraction
}

# Plausible post-normalisation bounds. A value outside these is a unit error or
# garbage, not a surprising company, so it is DROPPED rather than stored --
# dropping leaves the field absent, and `overlay` then keeps our own value.
#
# Note what this can and cannot catch. Bounds catch percent-vs-fraction well
# (a 27.6 net margin is impossible as a fraction) but CANNOT catch the
# millions-vs-absolute market-cap bug on its own, because 4,476,472 is a
# perfectly plausible market cap for a microcap. That one is caught by the
# cross-provider scale test and by the price x shares identity instead.
SANE: dict[str, tuple[float, float]] = {
    "price": (0.0, 1e6),
    "pe": (0.0, 1e5),
    "pb": (-1e4, 1e4),
    "ps": (0.0, 1e4),
    "peg": (-1e3, 1e3),
    "ev_ebitda": (-1e4, 1e4),
    "ev_sales": (0.0, 1e4),
    "eps_ttm": (-1e4, 1e4),
    "eps_diluted_ttm": (-1e4, 1e4),
    "mktcap": (1e3, 1e15),
    "ev": (-1e14, 1e15),
    "shares_out": (1e3, 1e13),
    "revenue_ttm": (-1e13, 1e14),
    "net_income_ttm": (-1e13, 1e14),
    "ebitda": (-1e13, 1e14),
    "fcf": (-1e13, 1e14),
    "total_debt": (0.0, 1e14),
    "total_cash": (0.0, 1e14),
    "book_per_share": (-1e5, 1e5),
    "revenue_per_share": (-1e5, 1e5),
    # Fractions. A margin of 27.6 means someone sent percent.
    "roe": (-20.0, 20.0),
    "roa": (-20.0, 20.0),
    "gross_margin": (-5.0, 5.0),
    "op_margin": (-5.0, 5.0),
    "net_margin": (-5.0, 5.0),
    "ebitda_margin": (-5.0, 5.0),
    "revenue_growth": (-5.0, 50.0),
    "eps_growth": (-50.0, 50.0),
    "payout_ratio": (-5.0, 5.0),
    "dividend_yield": (0.0, 1.0),
    "debt_to_equity": (0.0, 100.0),
    "current_ratio": (0.0, 1e3),
    "quick_ratio": (0.0, 1e3),
    "beta": (-20.0, 20.0),
    "wk52_high": (0.0, 1e6),
    "wk52_low": (0.0, 1e6),
}

_DROPPED: dict[str, int] = {}


def _num(v):
    """Coerce to float, or None. NEVER use isinstance for this.

    `np.float64` IS a subclass of Python float, but **`np.int64` is not a
    subclass of `int`** -- so `isinstance(v, (int, float))` silently returns
    False for every integer field that has been through a DataFrame or a
    parquet round-trip. Market cap and share count are exactly those fields.
    That is how `_selftest_identity` came to skip itself without a word, which
    is the failure mode this whole section exists to prevent.
    """
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f                        # reject NaN


def _sane(row: dict) -> dict:
    """Drop any normalised value outside its declared bound, and count it.

    Dropping, not clamping. A clamped 27.6 net margin becomes a plausible-looking
    5.0 and enters the store as data; an absent one leaves our own figure in
    place and shows up in the drop tally.
    """
    for col, (lo, hi) in SANE.items():
        if col not in row:
            continue
        f = _num(row.get(col))
        if f is None or not (lo <= f <= hi):
            row.pop(col, None)
            _DROPPED[col] = _DROPPED.get(col, 0) + 1
    return row


def _log(msg: str) -> None:
    line = f"prov {datetime.now():%H:%M:%S} | {msg}"
    try:
        print(line, flush=True)
    except (ValueError, OSError):
        pass
    # ALSO TO A FILE. The 2026-08-13 prefetch hung for 53 minutes and the only
    # way to tell was sampling the process's CPU counter, because stdout went
    # to a hidden scheduled-task console and the cache is written in batches.
    # A long unattended fetch must leave a trail on disk.
    try:
        with (config.DATA / "_providers.log").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# ===========================================================================
# FINNHUB BACKEND
# ===========================================================================
# Official, keyed, 60 req/min documented. Measured 2026-08-13: 133 metrics in
# 0.89 s for AAPL -> ~58 min for the 3,480-name universe, and the rate is
# PREDICTABLE, which yfinance's is not. yfinance handled 24 and 150 names fine
# but the full sweep hung: 53 minutes for ~20 CPU-seconds with two sockets
# open and no timeout to break it. So Finnhub is the bulk source and yfinance
# is kept for on-demand work and for the fields Finnhub does not carry.
FINNHUB_URL = "https://finnhub.io/api/v1/stock/metric?symbol={sym}&metric=all"
FINNHUB_MIN_GAP = 1.05          # 60/min documented; stay just under it
FINNHUB_TIMEOUT = 20            # NEVER unbounded -- see the hang above

# Finnhub field -> (our column, multiplier).
#
# UNITS HERE ARE DOCUMENTED, NOT INFERRED.
# Source: https://finnhub.io/docs/api/company-basic-financials
#   * `marketCapitalization` is quoted in MILLIONS of the reporting currency.
#   * TTM ratio metrics (`roeTTM`, `roaTTM`, the margins, the growth rates,
#     `dividendYieldIndicatedAnnual`) are quoted as PERCENT.
# The multipliers below convert both to this project's convention: absolute
# currency, and ratios as FRACTIONS. Confirmed against live AAPL by
# `_selftest_units`, so the spec and the wire format are both checked.
#
# THE MULTIPLIERS ARE THE WHOLE POINT OF THIS TABLE.
#
# Finnhub and Yahoo publish the same quantities in DIFFERENT UNITS, and the
# difference is silent -- both are plain numbers:
#
#     marketCapitalization   Finnhub 4,476,472.5  (MILLIONS)
#                            Yahoo   4,439,768,301,568  (absolute)
#     roeTTM                 Finnhub 137.18       (PERCENT)
#                            Yahoo   1.488        (fraction)
#     grossMarginTTM         Finnhub 48.65        (PERCENT)
#                            Yahoo   0.48653      (fraction)
#
# Merging those without conversion is a 1,000,000x market-cap error and a 100x
# ROE error, landing in a store that feeds size buckets and every valuation
# screen. Converting HERE, once, in a declared table, is the only place it can
# be checked -- and `_selftest_units` asserts it against real AAPL numbers.
FINNHUB_FIELDS: dict[str, tuple[str, float]] = {
    "peTTM":                          ("pe", 1.0),
    "pbQuarterly":                    ("pb", 1.0),
    "psTTM":                          ("ps", 1.0),
    "epsTTM":                         ("eps_ttm", 1.0),
    "evToEbitdaTTM":                  ("ev_ebitda", 1.0),
    "currentRatioQuarterly":          ("current_ratio", 1.0),
    "quickRatioQuarterly":            ("quick_ratio", 1.0),
    "beta":                           ("beta", 1.0),
    # millions -> absolute
    "marketCapitalization":           ("mktcap", 1e6),
    "enterpriseValue":                ("ev", 1e6),
    # percent -> fraction
    "roeTTM":                         ("roe", 0.01),
    "roaTTM":                         ("roa", 0.01),
    "grossMarginTTM":                 ("gross_margin", 0.01),
    "operatingMarginTTM":             ("op_margin", 0.01),
    "netProfitMarginTTM":             ("net_margin", 0.01),
    "dividendYieldIndicatedAnnual":   ("dividend_yield", 0.01),
    "revenueGrowthTTMYoy":            ("revenue_growth", 0.01),
    "epsGrowthTTMYoy":                ("eps_growth", 0.01),
    "totalDebt/totalEquityQuarterly": ("debt_to_equity", 1.0),
    "revenuePerShareTTM":             ("revenue_per_share", 1.0),
    "52WeekHigh":                     ("wk52_high", 1.0),
    "52WeekLow":                      ("wk52_low", 1.0),
}

FINNHUB_CACHE = config.DATA / "_finnhub_fund.parquet"
_fh_last = [0.0]


def _finnhub_key() -> str:
    key = os.environ.get("FINNHUB_KEY", "").strip()
    if key:
        return key
    try:                                   # .env is not auto-loaded everywhere
        for line in (config.ROOT / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("FINNHUB_KEY="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def _finnhub_one(sym: str, key: str) -> dict | None:
    """One symbol's metrics, already unit-normalised. None on failure."""
    gap = time.monotonic() - _fh_last[0]
    if gap < FINNHUB_MIN_GAP:
        time.sleep(FINNHUB_MIN_GAP - gap)
    _fh_last[0] = time.monotonic()
    url = FINNHUB_URL.format(sym=urllib.parse.quote(sym)) + f"&token={key}"
    try:
        with urllib.request.urlopen(url, timeout=FINNHUB_TIMEOUT) as r:
            payload = json.loads(r.read())
    except Exception:                                            # noqa: BLE001
        return None
    m = (payload or {}).get("metric") or {}
    if not m:
        return None
    out: dict = {"ticker": sym, "fetched": pd.Timestamp.now()}
    for src, (col, mult) in FINNHUB_FIELDS.items():
        v = m.get(src)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[col] = float(v) * mult
    out = _sane(out)
    return out if len(out) > 2 else None


def fetch_finnhub(tickers: list[str], ttl_hours: float = TTL_HOURS,
                  verbose: bool = True) -> pd.DataFrame:
    """Bulk current fundamentals from Finnhub, cached and checkpointed."""
    key = _finnhub_key()
    if not key:
        _log("no FINNHUB_KEY in env or .env -- skipping finnhub")
        return pd.DataFrame()

    tickers = [t.strip().upper() for t in tickers if t and str(t).strip()]
    cached = pd.DataFrame()
    if FINNHUB_CACHE.exists():
        try:
            cached = _fresh(pd.read_parquet(FINNHUB_CACHE), ttl_hours)
        except Exception:                                        # noqa: BLE001
            cached = pd.DataFrame()
    have = set(cached["ticker"]) if not cached.empty else set()
    todo = [t for t in tickers if t not in have]
    if verbose:
        _log(f"finnhub: {len(todo):,} to fetch, {len(tickers) - len(todo):,} "
             f"cached (~{len(todo) * FINNHUB_MIN_GAP / 60:.0f} min)")

    rows, pending, failed = [], [], 0
    t0 = time.time()
    for i, t in enumerate(todo, 1):
        r = _finnhub_one(t, key)
        if r is None:
            failed += 1
        else:
            rows.append(r)
            pending.append(r)
        if len(pending) >= CHECKPOINT_EVERY:
            _write_cache(FINNHUB_CACHE, pending)
            pending = []
        if verbose and i % 100 == 0:
            rate = (time.time() - t0) / i
            _log(f"  finnhub {i:,}/{len(todo):,} ({failed} failed) "
                 f"~{(len(todo) - i) * rate / 60:.0f} min left")
    _write_cache(FINNHUB_CACHE, pending)
    if verbose:
        _log(f"finnhub done: {len(rows):,} ok, {failed} failed in "
             f"{(time.time() - t0) / 60:.1f} min")

    try:
        allrows = pd.read_parquet(FINNHUB_CACHE) if FINNHUB_CACHE.exists() \
            else pd.DataFrame(rows)
    except Exception:                                            # noqa: BLE001
        allrows = pd.DataFrame(rows)
    if allrows.empty:
        return allrows
    return allrows[allrows["ticker"].isin(tickers)]


def _write_cache(path, batch: list) -> None:
    """Merge a batch into a parquet cache, atomically. Shared by both backends."""
    if not batch:
        return
    try:
        prev = pd.read_parquet(path) if path.exists() else pd.DataFrame()
        merged = pd.concat([prev, pd.DataFrame(batch)], ignore_index=True) \
            if not prev.empty else pd.DataFrame(batch)
        merged = merged.sort_values("fetched").drop_duplicates("ticker",
                                                               keep="last")
        tmp = path.with_suffix(".parquet.tmp")
        merged.to_parquet(tmp, index=False)
        tmp.replace(path)
    except Exception as exc:                                     # noqa: BLE001
        _log(f"  cache write failed ({type(exc).__name__}) -- continuing")


def _fresh(df: pd.DataFrame, ttl_hours: float) -> pd.DataFrame:
    if df.empty or "fetched" not in df.columns:
        return df.iloc[0:0]
    age = (pd.Timestamp.now() - pd.to_datetime(df["fetched"])).dt.total_seconds()
    return df[age < ttl_hours * 3600]


def fetch(tickers: list[str], ttl_hours: float = TTL_HOURS,
          verbose: bool = True) -> pd.DataFrame:
    """Current fundamentals from Yahoo, cached on disk.

    Returns one row per ticker that resolved. Failures are DROPPED rather than
    filled with zeros -- an absent second opinion must not read as agreement.
    """
    import yfinance as yf

    tickers = [t.strip().upper() for t in tickers if t and str(t).strip()]
    cached = pd.DataFrame()
    if CACHE.exists():
        try:
            cached = _fresh(pd.read_parquet(CACHE), ttl_hours)
        except Exception:                                    # noqa: BLE001
            cached = pd.DataFrame()

    have = set(cached["ticker"]) if not cached.empty else set()
    todo = [t for t in tickers if t not in have]
    if verbose and todo:
        _log(f"fetching {len(todo)} ticker(s) from Yahoo "
             f"({len(tickers) - len(todo)} cached)")

    rows, failed = [], 0
    # CHECKPOINT TO DISK AS WE GO.
    #
    # This accumulated all 3,480 names in memory and wrote once at the end, so
    # a failure at name 3,400 -- a dropped connection, an IP rate-limit, the
    # laptop closing -- threw away ninety minutes of fetching and left no trace
    # of how far it had got. yfinance is an UNOFFICIAL client scraping endpoints
    # Yahoo can change without notice, which makes "the whole run or nothing"
    # exactly the wrong bet. Writing every CHECKPOINT_EVERY names makes a
    # crash cost seconds and lets a restart resume, since `todo` already skips
    # anything fresh in the cache.
    def _checkpoint(batch: list) -> None:
        if not batch:
            return
        try:
            prev = pd.read_parquet(CACHE) if CACHE.exists() else pd.DataFrame()
            merged = pd.concat([prev, pd.DataFrame(batch)], ignore_index=True) \
                if not prev.empty else pd.DataFrame(batch)
            merged = merged.sort_values("fetched").drop_duplicates("ticker",
                                                                   keep="last")
            tmp = CACHE.with_suffix(".parquet.tmp")
            merged.to_parquet(tmp, index=False)
            tmp.replace(CACHE)
        except Exception as exc:                             # noqa: BLE001
            _log(f"  checkpoint failed ({type(exc).__name__}) -- continuing")

    pending: list = []
    for i, t in enumerate(todo, 1):
        try:
            info = yf.Ticker(t).info or {}
        except Exception:                                    # noqa: BLE001
            failed += 1
            continue
        if not info or info.get("quoteType") == "NONE":
            failed += 1
            continue
        r = {"ticker": t, "fetched": pd.Timestamp.now()}
        for k, col in FIELDS.items():
            r[col] = info.get(k)
        # `currentPrice` is absent for some quote types; fall back rather than
        # dropping the whole row over one field.
        if r.get("price") is None:
            r["price"] = r.get("price_alt")
        r.pop("price_alt", None)
        for _c, _m in YAHOO_SCALE.items():          # percent -> fraction
            if isinstance(r.get(_c), (int, float)) and not isinstance(r[_c], bool):
                r[_c] = float(r[_c]) * _m
        r = _sane(r)
        rows.append(r)
        pending.append(r)
        if len(pending) >= CHECKPOINT_EVERY:
            _checkpoint(pending)
            pending = []
        if verbose and i % 25 == 0:
            _log(f"  {i}/{len(todo)} ({failed} failed, "
                 f"{failed / i * 100:.1f}%)")
        time.sleep(0.05)
    _checkpoint(pending)

    got = pd.DataFrame(rows)
    if verbose and todo:
        _log(f"got {len(got)}, failed {failed}")

    out = pd.concat([cached, got], ignore_index=True) if not cached.empty else got
    if not out.empty:
        out = out.sort_values("fetched").drop_duplicates("ticker", keep="last")
        try:
            prev = pd.read_parquet(CACHE) if CACHE.exists() else pd.DataFrame()
            merged = pd.concat([prev, out], ignore_index=True) \
                if not prev.empty else out
            merged = merged.sort_values("fetched").drop_duplicates("ticker",
                                                                   keep="last")
            merged.to_parquet(CACHE, index=False)
        except Exception:                                    # noqa: BLE001
            pass
    return out[out["ticker"].isin(tickers)] if not out.empty else out


# Fields worth comparing, and how far apart two honest pipelines may sit.
#
# Tolerances are NOT uniform. A price is the same number from both sides and
# should match to a cent. A P/E built on different TTM windows can legitimately
# differ by a few percent when a quarter lands between the two sources' cutoffs
# -- Yahoo often has a quarter our bulk files have not received yet.
COMPARE = {
    "price":       0.02,
    "mktcap":      0.05,
    "shares_out":  0.05,
    "eps_ttm":     0.05,
    "pe":          0.05,
    "revenue_ttm": 0.05,
    # DERIVED ROWS, added 2026-08-14 after COLL.
    #
    # These are computed by us from SEC and ALSO published by a provider, so
    # there is no reason they were ever unchecked. COLL's EBITDA read $4M
    # against a real $68M -- a single comparison against Finnhub or Yahoo would
    # have caught it instantly, and instead it took a user reading the page.
    #
    # Of the 15 derived rows on the profile page, 10 are published by a
    # provider we already call. Those 10 are checked here every night. The
    # remaining 5 (fcf_margin, asset_turnover, rnd_intensity, buyback_yield,
    # dna) genuinely have no provider equivalent and stay unverified -- said
    # plainly rather than glossed.
    #
    # Tolerances are wider than for a price because the two sides use different
    # windows: our TTM ends at the last filed quarter, a provider's may include
    # an estimate or a slightly different period. A 10% gap is window noise; a 10x
    # gap is a bug, and that is what this is for.
    "ebitda":         0.15,
    # fcf: INFORMATIONAL ONLY -- do NOT treat a gap here as our bug.
    #
    # Investigated 2026-08-14. Yahoo's `freeCashflow` is not consistently
    # CFO - capex: for PFE it exceeds our CFO outright, which that formula
    # makes impossible. Checked against SEC instead, our legs are exact --
    # MSFT's FY2026 10-K reports capex of $115,948,000,000 directly (the AI
    # datacenter buildout) while Yahoo's figure implies ~$166B. `verify_metrics`
    # passes cfo_ttm and capex_ttm at 100%.
    #
    # SEC is the authority; the provider is a second opinion. Where they
    # conflict and SEC agrees with us, WE ARE RIGHT and the tolerance here
    # exists only so a real 10x break still shows up.
    "fcf":            0.60,
    # roe: INFORMATIONAL. We divide TTM net income by ENDING equity; the
    # common vendor convention is AVERAGE equity over the period, which for a
    # company whose book value moved during the year gives a materially
    # different number without either being wrong. Median gap 9.8%.
    "roe":            0.30,
    # roa: INFORMATIONAL. Ours is SEC-verified net income over SEC-verified
    # total assets -- the textbook definition. Yahoo runs a consistent ~0.80x
    # of ours across AAPL (0.271 vs 0.336), MSFT (0.141 vs 0.176) and KO
    # (0.094 vs 0.131), which is an averaged denominator, not an error in
    # either. Both legs pass `verify_metrics`.
    "roa":            0.40,
    "net_margin":     0.10,
    "gross_margin":   0.10,
    # op_margin: INFORMATIONAL. Investigated 2026-08-16 -- NOT our bug.
    #
    # CELH: our TTM operating income is 160.3M on 3,047.3M revenue = 5.3%.
    # Both legs hand-checked against the filings and both PASS SEC
    # verification. The company took an 80.0M operating LOSS in Q3-2025; our
    # trailing twelve months includes it, Yahoo's 19.1% evidently does not.
    # Ours is the honest GAAP TTM. Directions are mixed across names (CLMT has
    # the opposite sign to Yahoo), so this is a definition gap, not an offset.
    "op_margin":      0.60,
    "current_ratio":  0.10,
    # debt_to_equity: INFORMATIONAL, and the cause is PROVEN rather than
    # guessed. Yahoo's own `totalDebt` divided by OUR equity reproduces Yahoo's
    # ratio exactly -- AAPL 84.3/107.5 = 0.784 against their 0.784, MSFT
    # 128.8/442.4 = 0.291 against their 0.291. So our EQUITY is right and the
    # gap is entirely the DEBT definition: Yahoo includes finance leases and
    # other borrowings that `debt_lt + debt_st` does not carry. Ours is the
    # narrower, XBRL-tagged figure.
    "debt_to_equity": 0.40,
}


def compare(tickers: list[str], asof: str | None = None,
            verbose: bool = True) -> pd.DataFrame:
    """Our computed fundamentals against Yahoo's, field by field.

    A row per (ticker, field) with both values and the relative gap, so the
    result can be read as evidence rather than as a verdict.
    """
    import fundamentals as FD
    import fund_metrics as FM
    import scores.fundamental as SF

    asof = asof or datetime.now().strftime("%Y-%m-%d")
    yah = fetch(tickers, verbose=verbose)
    if yah.empty:
        _log("no Yahoo data at all -- cannot compare")
        return pd.DataFrame()
    tickers = sorted(set(yah["ticker"]))

    facts = FD.facts_asof(asof, tickers)
    if facts.empty:
        _log("facts_asof returned nothing")
        return pd.DataFrame()
    px = SF._price_inputs(asof, tickers, facts)
    met = FM.compute(facts, facts, px)

    ours = pd.DataFrame({"ticker": facts["ticker"]})
    fi = facts.set_index("ticker")
    pi = px.set_index("ticker")
    mi = met.set_index("ticker") if "ticker" in met.columns else met
    ours = ours.set_index("ticker")
    ours["price"] = pi["price"]
    ours["mktcap"] = pi["mktcap"]
    ours["shares_out"] = pd.to_numeric(fi.get("shares_out"), errors="coerce")
    ours["eps_ttm"] = pd.to_numeric(fi.get("eps_diluted_ttm"), errors="coerce")
    ours["pe"] = mi["pe"] if "pe" in mi.columns else None
    ours["revenue_ttm"] = pd.to_numeric(fi.get("revenue_ttm"), errors="coerce")
    # DERIVED ROWS from our own arithmetic, so they can be checked against the
    # provider that publishes the same quantity. Without these the comparison
    # only ever covered inputs -- which is how COLL's EBITDA stayed wrong.
    for _c in ("ebitda", "fcf", "roe", "roa", "net_margin", "gross_margin",
               "op_margin", "current_ratio", "debt_to_equity"):
        if _c in mi.columns:
            ours[_c] = pd.to_numeric(mi[_c], errors="coerce")
    # `ebitda` and `fcf` are levels the metrics frame does not carry; rebuild
    # them from the same legs the page uses so the check tests what is shown.
    _d = lambda c: pd.to_numeric(fi.get(c), errors="coerce")
    _tot, _dep, _amo = _d("dna_ttm"), _d("deprec_ttm"), _d("amort_ttm")
    _comp = _dep.fillna(0) + _amo.fillna(0)
    _comp = _comp.where(_dep.notna() | _amo.notna())
    _dna = _tot.where(_tot.notna(), _comp)
    _op = _d("opinc_ttm")
    ours["ebitda"] = (_op + _dna).where(_op.notna() & _dna.notna())
    _cfo, _cap = _d("cfo_ttm"), _d("capex_ttm")
    ours["fcf"] = (_cfo - _cap).where(_cfo.notna() & _cap.notna())
    ours["our_period"] = fi.get("last_ddate")

    y = yah.set_index("ticker")
    rows = []
    for tk in tickers:
        if tk not in ours.index:
            continue
        for field, tol in COMPARE.items():
            # A field we do not compute is not a failure -- it is outside the
            # comparison. Say so explicitly so the coverage number stays
            # honest rather than crashing or silently shrinking.
            if field not in ours.columns:
                rows.append((tk, field, None, None, None, "we do not compute"))
                continue
            a = pd.to_numeric(pd.Series([ours.at[tk, field]]),
                              errors="coerce").iloc[0]
            b = pd.to_numeric(pd.Series([y.at[tk, field]]),
                              errors="coerce").iloc[0] if field in y.columns \
                else None
            if pd.isna(a) or b is None or pd.isna(b) or b == 0:
                rows.append((tk, field, a, b, None, "no comparison"))
                continue
            gap = abs(a - b) / abs(b)
            rows.append((tk, field, a, b, gap,
                         "agree" if gap <= tol else "DISAGREE"))
    out = pd.DataFrame(rows, columns=["ticker", "field", "ours", "yahoo",
                                      "rel_gap", "status"])
    out = out.merge(ours[["our_period"]].reset_index(), on="ticker", how="left")
    return out


# ===========================================================================
# PROVIDER-FIRST METRICS
# ===========================================================================
# Our metric name -> how to get it from one Yahoo `info` dict.
#
# WHY THESE COME FROM THE PROVIDER AND NOT FROM US
# ---------------------------------------------------
# Every one of these is a ratio Yahoo already publishes, computed by them from
# the same filings, and displayed by Google. Recomputing them from raw XBRL
# bought nothing except the opportunity to be wrong -- which is precisely what
# happened: `_ttm` preferring a stale annual, share counts summed as flows, a
# fact store that lagged SEC's own API by up to 925 days. Three separate faults,
# all of them in arithmetic that did not need to exist for these fields.
#
# So for the CURRENT displayed figures, the provider is the source and our
# calculation is the fallback. Not a cross-check -- the source.
#
# WHAT STAYS OURS, AND WHY IT MUST
# -----------------------------------
#  * Anything not listed here (roic, interest_cover, z_score, net_issuance,
#    cash_conversion_cycle, piotroski, beneish...). Yahoo does not publish them.
#  * EVERY HISTORICAL SESSION. Yahoo knows only today. Overlaying it onto a
#    2019 session would put 2026 knowledge into a 2019 row and quietly destroy
#    the point-in-time discipline the whole backtest rests on. The overlay is
#    applied to the latest session ONLY -- `scores/fundamental.py` enforces it.
DIRECT = {
    "pe":              "pe",
    "pb":              "pb",
    "ev_ebitda":       "ev_ebitda",
    "ev_sales":        "ev_sales",
    "peg":             "peg",
    "roe":             "roe",
    "roa":             "roa",
    "net_margin":      "net_margin",
    "gross_margin":    "gross_margin",
    "op_margin":       "op_margin",
    "ebitda_margin":   "ebitda_margin",
    "current_ratio":   "current_ratio",
    "quick_ratio":     "quick_ratio",
    "revenue_growth":  "revenue_growth",
    "eps_growth":      "eps_growth",
    "payout_ratio":    "payout_ratio",
    "dividend_yield":  "dividend_yield",
}

# Ratios Yahoo does not name directly but publishes both legs of. Computed from
# provider numbers only -- never mixing a provider numerator with our denominator,
# which is the currency-mixing mistake in a different costume.
def _derived(i: dict) -> dict:
    out = {}
    ev, fcf = i.get("enterpriseValue"), i.get("freeCashflow")
    if ev and fcf is not None and ev > 0:
        out["fcf_yield"] = fcf / ev
    debt, cash, ebitda = i.get("totalDebt"), i.get("totalCash"), i.get("ebitda")
    if ebitda and ebitda > 0 and debt is not None and cash is not None:
        out["net_debt_ebitda"] = (debt - cash) / ebitda
    return out


# Levels, as opposed to ratios. These feed the page and the size buckets.
LEVELS = {
    "mktcap": "mktcap", "shares_out": "shares_out",
    "revenue_ttm": "revenue_ttm", "ebitda": "ebitda",
    "eps_diluted_ttm": "eps_diluted_ttm", "net_income_ttm": "net_income_ttm",
}


def metrics(tickers: list[str], verbose: bool = True,
            backend: str = "auto") -> pd.DataFrame:
    """Provider-sourced metrics, one row per ticker, under OUR column names.

    `backend`:
      "auto"    Finnhub for the bulk, yfinance filling anything it lacks.
      "finnhub" / "yahoo"   force one.

    FINNHUB LEADS BECAUSE IT IS THE ONE THAT SCALES. Measured 2026-08-13:
    0.89 s/name and a documented 60/min ceiling, against yfinance handling 24
    and 150 names happily and then HANGING on the 3,480-name sweep -- 53
    minutes for ~20 CPU-seconds, no timeout, no progress output. yfinance is
    still worth keeping: it carries float, short interest, the next earnings
    date and analyst targets, none of which Finnhub's free tier returns.

    Only fields a provider actually returned appear. A metric neither publishes
    is left ABSENT rather than NaN, so `overlay` keeps our own value instead of
    blanking a column that was fine.
    """
    if backend in ("auto", "finnhub"):
        fh = fetch_finnhub(tickers, verbose=verbose)
        if backend == "finnhub" or not fh.empty:
            out = _shape(fh)
            if backend == "finnhub":
                return out
            # Fill only what Finnhub did not return, per ticker per field.
            missing = [t for t in tickers
                       if t not in set(out["ticker"])] if not out.empty else tickers
            if missing:
                yf_out = _shape(fetch(missing, verbose=verbose))
                if not yf_out.empty:
                    out = pd.concat([out, yf_out], ignore_index=True)
            return out
    return _shape(fetch(tickers, verbose=verbose))


def _shape(df: pd.DataFrame) -> pd.DataFrame:
    """Cached provider frame -> our metric columns. Shared by both backends."""
    if df.empty:
        return df
    # Bound on READ as well as on ingest. The cache outlives any one version
    # of this file -- rows written before `_sane` existed, or by a build with a
    # wrong multiplier, must not reach the store just because they are already
    # on disk.
    df = df.copy()
    for _c, (_lo, _hi) in SANE.items():
        if _c in df.columns:
            _v = pd.to_numeric(df[_c], errors="coerce")
            df[_c] = _v.where((_v >= _lo) & (_v <= _hi))

    out = pd.DataFrame({"ticker": df["ticker"].values})
    for ours in list(DIRECT) + list(LEVELS):
        if ours in df.columns:
            v = pd.to_numeric(df[ours], errors="coerce")
            if v.notna().any():
                out[ours] = v.values
    # `eps_diluted_ttm` is our name for what Yahoo calls trailingEps, which is
    # already cached as `eps_ttm`. Aliased rather than fetched twice.
    if "eps_ttm" in df.columns:
        v = pd.to_numeric(df["eps_ttm"], errors="coerce")
        if v.notna().any():
            out["eps_diluted_ttm"] = v.values

    # DERIVED FROM PROVIDER LEGS ONLY. Never a provider numerator over one of
    # our denominators -- that mixes two vintages of the same company and is
    # the currency-mixing mistake wearing a different hat.
    num = lambda c: pd.to_numeric(df[c], errors="coerce") \
        if c in df.columns else pd.Series(index=df.index, dtype=float)
    ev, fcf = num("ev"), num("fcf")
    y = (fcf / ev).where(ev > 0)
    if y.notna().any():
        out["fcf_yield"] = y.values
    debt, cash, ebitda = num("total_debt"), num("total_cash"), num("ebitda")
    nd = ((debt - cash) / ebitda).where(ebitda > 0)
    if nd.notna().any():
        out["net_debt_ebitda"] = nd.values
    return out


def overlay(mine: pd.DataFrame, tickers: list[str] | None = None,
            verbose: bool = True) -> tuple[pd.DataFrame, dict]:
    """Replace our computed metrics with the provider's where it has them.

    Returns the merged frame and a {metric: n_replaced} tally, because a
    silent overlay is just another unverifiable claim. The caller logs it.
    """
    if mine.empty or "ticker" not in mine.columns:
        return mine, {}
    tks = tickers or mine["ticker"].astype(str).tolist()
    prov = metrics(tks, verbose=verbose)
    if prov.empty:
        _log("provider returned nothing -- keeping our own figures")
        return mine, {}

    out = mine.copy()
    p = prov.set_index("ticker")
    tally: dict[str, int] = {}
    for col in p.columns:
        if col not in out.columns:
            continue
        src = p[col].reindex(out["ticker"].astype(str).values)
        src.index = out.index
        src = pd.to_numeric(src, errors="coerce")
        n = int(src.notna().sum())
        if n:
            out[col] = src.where(src.notna(), out[col])
            tally[col] = n
    return out, tally


def _report(cmp: pd.DataFrame) -> int:
    if cmp.empty:
        print("nothing compared")
        return 1
    # "we do not compute" is OUTSIDE the comparison, not a failure of it.
    # Counting it as a disagreement reported gross_margin at 0% agree when the
    # truth is that our metrics frame never carried gross_margin at all --
    # a coverage gap, which is a different fact and needs saying differently.
    comparable = cmp[~cmp["status"].isin(["no comparison", "we do not compute"])]
    dis = comparable[comparable["status"] == "DISAGREE"]
    print(f"\n{len(comparable)} field comparison(s) across "
          f"{cmp['ticker'].nunique()} ticker(s)")
    if not comparable.empty:
        print(f"agree: {(comparable['status'] == 'agree').mean() * 100:.1f}%   "
              f"disagree: {len(dis)}   "
              f"no comparison: {(cmp['status'] == 'no comparison').sum()}   "
              f"we do not compute: "
              f"{(cmp['status'] == 'we do not compute').sum()}")
    print("\nper field:")
    for f in COMPARE:
        s = comparable[comparable["field"] == f]
        if s.empty:
            n_nc = int((cmp["field"] == f).sum())
            why = "we do not compute this" if (
                (cmp[(cmp["field"] == f)]["status"] == "we do not compute").any()
            ) else "no provider value"
            print(f"   {f:14s} -- {why} (n={n_nc})")
            continue
        ok = (s["status"] == "agree").mean() * 100
        print(f"   {f:12s} {ok:5.1f}% agree  (n={len(s):3d}, "
              f"median gap {s['rel_gap'].median() * 100:5.2f}%)")

    if not dis.empty:
        # Staleness is the expected cause, so show it rather than assert it.
        d = dis.copy()
        d["our_period"] = pd.to_datetime(d["our_period"], errors="coerce")
        d["stale_days"] = (pd.Timestamp.now() - d["our_period"]).dt.days
        print(f"\ndisagreeing names, oldest stored period first:")
        top = (d.groupby("ticker")
                 .agg(fields=("field", "count"),
                      stale_days=("stale_days", "max"))
                 .sort_values("stale_days", ascending=False).head(15))
        print(top.to_string())
        print(f"\nmedian staleness where we disagree: "
              f"{d['stale_days'].median():.0f} days")
        agree_days = comparable[comparable["status"] == "agree"].copy()
        agree_days["our_period"] = pd.to_datetime(agree_days["our_period"],
                                                  errors="coerce")
        ad = (pd.Timestamp.now() - agree_days["our_period"]).dt.days
        print(f"median staleness where we agree   : {ad.median():.0f} days")
    dest = config.DATA / "_provider_compare.csv"
    cmp.to_csv(dest, index=False)
    print(f"\ndetail -> {dest}")
    return 0


def _selftest_units() -> None:
    """Both backends must land in the SAME units. Live call, real numbers.

    This is the one selftest that has to hit the network, because the bug it
    guards is a property of the provider's response, not of our code: Finnhub
    reports market cap in MILLIONS and ROE/margins in PERCENT, Yahoo reports
    absolute and fractions. A table of multipliers is only as good as the
    assertion that it was applied, and a synthetic fixture would happily agree
    with a table that had drifted from what the API actually sends.

    Ranges, not equality -- the two providers legitimately differ by a percent
    or two on a live price. What must never happen is being out by 10^6 or 10^2.
    """
    key = _finnhub_key()
    if not key:
        print("  [providers] no FINNHUB_KEY -- unit selftest skipped")
        return
    fh = _finnhub_one("AAPL", key)
    assert fh, "finnhub returned nothing for AAPL"
    mc, roe, gm = fh.get("mktcap"), fh.get("roe"), fh.get("gross_margin")
    # AAPL market cap is trillions. In raw Finnhub units it reads ~4.5e6.
    assert mc and 1e11 < mc < 1e14, f"mktcap {mc!r} not absolute (millions bug?)"
    # ROE as a fraction is ~1.4. Raw Finnhub sends ~137.
    assert roe and 0.05 < roe < 10, f"roe {roe!r} not a fraction (percent bug?)"
    assert gm and 0.05 < gm < 1.0, f"gross_margin {gm!r} not a fraction"
    print(f"  [providers] finnhub units OK "
          f"(mktcap {mc/1e12:.2f}T, roe {roe:.2f}, gross_margin {gm:.2f})")

    # And the two backends must agree on scale, which is the real risk.
    y = fetch(["AAPL"], verbose=False)
    if not y.empty and pd.notna(y["mktcap"].iloc[0]):
        ymc = float(y["mktcap"].iloc[0])
        ratio = mc / ymc
        assert 0.5 < ratio < 2.0, (
            f"finnhub/yahoo mktcap ratio {ratio:.4g} -- units disagree "
            f"({mc:,.0f} vs {ymc:,.0f})")
        print(f"  [providers] finnhub/yahoo mktcap agree within "
              f"{abs(1 - ratio) * 100:.1f}%")


def _selftest_declared() -> None:
    """Every field either backend can emit must have a declared bound.

    This is the guard that makes the others hold over time. Adding a row to
    FINNHUB_FIELDS or FIELDS without thinking about its unit is the way this
    breaks again, and an undeclared field would sail past `_sane` untouched.
    Failing here forces the question at the moment the field is added.
    """
    emitted = {col for col, _m in FINNHUB_FIELDS.values()}
    emitted |= {c for c in FIELDS.values() if c != "price_alt"}
    emitted -= {"currency"}                    # not numeric
    missing = sorted(emitted - set(SANE))
    assert not missing, (
        f"no SANE bound declared for {missing} -- add one (and check its "
        f"units) before this field can be stored")
    print(f"  [providers] {len(emitted)} emitted field(s), all bounded")


def _selftest_cross_scale(tickers=("AAPL", "MSFT", "KO", "JNJ", "XOM")) -> None:
    """EVERY field both backends emit must agree on SCALE, not just on value.

    THIS IS THE TEST THAT FINDS UNIT BUGS, and it exists because the earlier
    one did not. `_selftest_units` checked three hand-picked fields on one
    ticker and passed while two fields were silently 100x apart:

        debt_to_equity   finnhub 0.7844  yahoo 78.44   (yahoo is percent)
        dividend_yield   finnhub 0.00357 yahoo 0.36    (yahoo is percent)

    Comparing every shared field across several tickers is what surfaced them.
    The threshold is deliberately loose -- two providers legitimately differ by
    tens of percent on ROA or operating margin because they define the window
    differently. What must never happen is a factor of 100 or 1e6, so anything
    outside [0.25, 4] is a unit error rather than a disagreement.
    """
    key = _finnhub_key()
    if not key:
        print("  [providers] no FINNHUB_KEY -- cross-scale selftest skipped")
        return

    # A DROPPED FIELD MUST FAIL TOO, not quietly shrink the comparison.
    #
    # Found by deliberately breaking `netProfitMarginTTM`'s multiplier: the raw
    # 27.62 then failed its bound, `_sane` dropped it (correctly -- no corrupt
    # value reached the store), the field vanished from BOTH sides of the
    # comparison, and this test cheerfully reported "15 shared fields agree".
    # Safe data, useless test. These are large, liquid, well-behaved filers; a
    # single dropped value on them is a bug in the table, not a strange company.
    before = dict(_DROPPED)

    fh_rows = [r for r in (_finnhub_one(t, key) for t in tickers) if r]
    if not fh_rows:
        print("  [providers] finnhub unreachable -- cross-scale skipped")
        return
    fh = pd.DataFrame(fh_rows).set_index("ticker")
    yh = fetch(list(tickers), verbose=False)
    if yh.empty:
        print("  [providers] yahoo unreachable -- cross-scale skipped")
        return
    yh = yh.set_index("ticker")

    shared = sorted((set(fh.columns) & set(yh.columns)) - {"fetched", "currency"})
    bad, checked = [], 0
    for c in shared:
        a = pd.to_numeric(fh[c], errors="coerce")
        b = pd.to_numeric(yh[c], errors="coerce")
        both = pd.concat([a, b], axis=1).dropna()
        both = both[(both.iloc[:, 1].abs() > 1e-9)]
        if both.empty:
            continue
        ratio = (both.iloc[:, 0] / both.iloc[:, 1]).abs().median()
        checked += 1
        if not (0.25 <= ratio <= 4.0):
            bad.append(f"{c}: finnhub/yahoo median ratio {ratio:.4g}")
    assert not bad, "SCALE MISMATCH between providers -- " + "; ".join(bad)

    new_drops = {k: v - before.get(k, 0) for k, v in _DROPPED.items()
                 if v - before.get(k, 0) > 0}
    assert not new_drops, (
        f"field(s) dropped as out-of-bounds on large caps: {new_drops} -- "
        f"a wrong multiplier makes a value fail its bound and DISAPPEAR, "
        f"which shrinks this comparison instead of failing it")

    print(f"  [providers] {checked} shared field(s) agree on scale "
          f"across {len(fh)} ticker(s), 0 dropped")


def _selftest_identity(ticker: str = "AAPL") -> None:
    """mktcap must equal price x shares, from the provider's own numbers.

    The bound check cannot catch a millions-vs-absolute market cap on its own,
    because 4,476,472 is a plausible cap for a microcap. This identity can: it
    is dimensionally anchored to a price, which is unambiguous.
    """
    y = fetch([ticker], verbose=False)
    if y.empty:
        return
    row = y.iloc[0]
    mc, px, sh = (_num(row.get("mktcap")), _num(row.get("price")),
                  _num(row.get("shares_out")))
    if mc is None or px is None or sh is None or mc == 0:
        # SAY SO rather than returning quietly. A guard that skips in silence
        # is indistinguishable from one that passed -- see `_num`.
        print(f"  [providers] identity check SKIPPED for {ticker} "
              f"(mktcap={mc}, price={px}, shares={sh})")
        return
    implied = px * sh
    err = abs(implied - mc) / mc
    assert err < 0.10, (
        f"{ticker}: mktcap {mc:,.0f} but price x shares = {implied:,.0f} "
        f"({err*100:.1f}% apart) -- a units bug, not a data disagreement")
    print(f"  [providers] {ticker} mktcap == price x shares "
          f"({err*100:.2f}% apart)")


def selftest() -> None:
    _selftest_declared()
    _selftest_units()
    _selftest_identity()
    _selftest_cross_scale()
    if _DROPPED:
        print(f"  [providers] values dropped as out-of-bounds: {_DROPPED}")
    print("providers selftest OK")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tickers")
    ap.add_argument("--n", type=int, default=0,
                    help="random sample of N tickers from the universe")
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--backend", choices=("auto","finnhub","yahoo"),
                    default="auto")
    ap.add_argument("--prefetch", action="store_true",
                    help="warm the cache for the whole tradeable universe")
    ap.add_argument("--tradeable", action="store_true",
                    help="sample from the tradeable universe (what the app "
                         "shows) rather than every CIK in the SEC map")
    ap.add_argument("--asof")
    a = ap.parse_args()

    if a.selftest:
        selftest()
        return 0

    if a.prefetch:
        import bars
        uni = sorted(set(bars.tradeable_universe()))
        _log(f"prefetching {len(uni):,} tradeable names ({a.backend})")
        if a.backend == "yahoo":
            got = fetch(uni, verbose=True)
        else:
            got = fetch_finnhub(uni, verbose=True)
        _log(f"cache now holds {len(got):,} of {len(uni):,}")
        return 0

    if a.tickers:
        tks = [t.strip().upper() for t in a.tickers.split(",") if t.strip()]
    elif a.n:
        import numpy as np
        # THE POPULATION MATTERS. Sampling every CIK in the SEC ticker map
        # measured mostly shells and delisted names that are not in the app and
        # were never refreshed -- 68.1% agreement. The same measurement on the
        # tradeable universe, which is what a page can actually show, was
        # 84.7%. Default to the honest population.
        if a.tradeable:
            import bars
            pool = sorted(set(bars.tradeable_universe()))
        else:
            import fundamentals as FD
            pool = sorted(set(FD.ticker_map()["ticker"].astype(str)))
        rng = np.random.default_rng(20260813)
        tks = sorted(rng.choice(pool, size=min(a.n, len(pool)),
                                replace=False).tolist())
    else:
        tks = ["AAPL", "MSFT", "KO", "JNJ", "WMT", "NVDA"]

    if a.compare:
        return _report(compare(tks, a.asof))
    df = fetch(tks)
    print(df.to_string(index=False) if not df.empty else "nothing fetched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
