"""
Macro and sector layer: breadth, sector ETFs, sector map, FRED, GPR/EPU, surprise.

    python macro.py --update        refresh everything, write data/_macro.parquet
    python macro.py --breadth       rebuild the breadth history from the bar store
    python macro.py --etfs          fetch sector + proxy ETF bars
    python macro.py --sectors       rebuild the SEC SIC sector map
    python macro.py --selftest
    python macro.py --show 2026-08-05

WHY THIS MODULE IS LOAD-BEARING AND NOT DECORATIVE
--------------------------------------------------
Measured: across the 30 live flags over 30 days, the MEDIAN name had 3 articles
and 4 of 30 had none. For most of the list, per-ticker sentiment does not exist.
The sector and macro reading is the only sentiment signal those names will ever
have, so it is a first-class input rather than a garnish on the report.

SOURCES, ORDERED BY RELIABILITY -- WHICH IS NOT THE SAME AS "FREE"
------------------------------------------------------------------
1. BREADTH FROM THE BAR STORE. 5,383 tickers already on disk. No key, no rate
   limit, no vendor, perfectly historical, and it cannot silently change shape
   under us. Everything else here is a dependency; this is arithmetic.

2. SECTOR / PROXY ETFs via the existing bars path. They are excluded from the
   universe by the ETF flag, so they are fetched explicitly into BARS_ETF. 24
   symbols is free next to a 5,383-name universe.

3. SEC EDGAR SIC CODES for the sector map. Free, no key, 10 req/s. Preferred over
   a yfinance sweep because it is an official filing attribute rather than a
   vendor's classification, and because _fundamentals.parquet only ever covers
   the ~30 daily flags.

4. FRED / ALFRED for actuals, market-implied expectations and release dates. Free
   key, 120 req/min. VINTAGES ARE THE POINT: every observation carries
   realtime_start, so what was KNOWN on a past date can be reconstructed. Macro
   series are revised, so using today's value in a 2024 backtest is look-ahead of
   exactly the kind replay.py --leaktest exists to catch.

5. GPR and EPU daily indices. Both verified downloading 2026-08-06 with no key.
   GPR is the Fed Board's own geopolitical risk index (Caldara & Iacoviello) and
   is the academic standard for the "Iran war" case; EPU is the policy and
   political channel.

DELIBERATELY NOT USED: GDELT. Measured 2026-08-06 -- one request per 5 seconds
enforced, and it 429'd on 3 of 4 attempts even at 20-second spacing, including
every timeline query. Free, but not reliable enough to sit on a scheduled path.
Recorded here so the next person does not re-derive it.

THE SURPRISE, WITHOUT PAYING FOR CONSENSUS
------------------------------------------
Street consensus (the Econoday/Bloomberg "expected" number) is the one thing in
this whole design that is not reliably free. It is also not needed:

    surprise = ATR-normalised move of the macro proxy basket on a session with a
               scheduled release.

The market's reaction IS the surprise, it is free, it is fully historical, and it
is the only part that transmits to a stock anyway. The honest caveat, which
belongs in the README: this measures REACTION, not DEVIATION FROM EXPECTATION.
The two differ when the market has pre-positioned.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import requests

import alpaca
import calendar_us
import config
import store

_SESSION = requests.Session()


def log(msg: str) -> None:
    print(msg, flush=True)


# ===========================================================================
# 1. Breadth -- from the bars already on disk
# ===========================================================================
def build_breadth(verbose: bool = True) -> pd.DataFrame:
    """Full breadth history, computed once over the whole store.

    Per-date rather than per-request: computing "% above the 50DMA" for one date
    costs the same store read as computing it for every date, and the backtest
    needs all of them. ~4.7M rows, three columns.
    """
    t0 = time.time()
    df = store.read(interval="1d", columns=["ticker", "date", "close"])
    if df.empty:
        return pd.DataFrame()

    df = df.sort_values(["ticker", "date"])
    g = df.groupby("ticker", observed=True)["close"]
    ma = g.transform(lambda s: s.rolling(config.BREADTH_MA, min_periods=config.BREADTH_MA).mean())
    ret1 = g.pct_change()
    hi = g.transform(lambda s: s.rolling(252, min_periods=60).max())
    lo = g.transform(lambda s: s.rolling(252, min_periods=60).min())

    df = df.assign(_above=(df["close"] > ma).astype("float32"),
                   _ret=ret1,
                   _nh=(df["close"] >= hi).astype("float32"),
                   _nl=(df["close"] <= lo).astype("float32"),
                   _valid=ma.notna().astype("float32"))

    by = df.groupby("date", observed=True)
    out = pd.DataFrame({
        "n_tickers": by["_valid"].sum(),
        "pct_above_ma": by.apply(
            lambda x: float(x.loc[x["_valid"] > 0, "_above"].mean())
            if (x["_valid"] > 0).any() else np.nan, include_groups=False),
        "median_ret": by["_ret"].median(),
        "adv_decl": by["_ret"].apply(
            lambda s: float((s > 0).sum()) / max(float((s < 0).sum()), 1.0)),
        "pct_new_high": by["_nh"].mean(),
        "pct_new_low": by["_nl"].mean(),
    }).reset_index().rename(columns={"date": "session"})

    # A breadth reading built from too few names is not a breadth reading.
    out.loc[out["n_tickers"] < config.BREADTH_MIN_TICKERS,
            ["pct_above_ma", "pct_new_high", "pct_new_low"]] = np.nan

    if verbose:
        log(f"  breadth: {len(out):,} session(s) in {time.time() - t0:.0f}s "
            f"({out['n_tickers'].iloc[-1]:.0f} tickers on the last one)")
    return out


# ===========================================================================
# 2. Sector and macro-proxy ETFs
# ===========================================================================
def etf_symbols() -> list[str]:
    return list(dict.fromkeys(list(config.SECTOR_ETFS) + list(config.MACRO_PROXIES)))


def fetch_etfs(years: float | None = None, verbose: bool = True) -> pd.DataFrame:
    """Sector + proxy ETF bars into BARS_ETF. One batch; 24 symbols is nothing."""
    years = config.HISTORY_YEARS if years is None else years
    syms = etf_symbols()
    start = (date.today() - timedelta(days=int(years * 365.25) + 10)).isoformat()
    end = calendar_us.bars_end_ts()

    payload = alpaca.fetch_bars(syms, start, end, "1d")
    df = store.bars_from_payload(payload, "1d")
    if df.empty:
        return df

    lcs = calendar_us.last_closed_session()
    df = df[df["date"] <= lcs]           # same partial-bar rule as the main store

    config.BARS_ETF.mkdir(parents=True, exist_ok=True)
    df["_m"] = df["date"].str.slice(0, 7)
    for m, chunk in df.groupby("_m", sort=True):
        p = config.BARS_ETF / f"{m}.parquet"
        chunk = chunk.drop(columns="_m")
        if p.exists():
            chunk = (pd.concat([pd.read_parquet(p), chunk], ignore_index=True)
                     .drop_duplicates(["ticker", "datetime"], keep="last"))
        tmp = p.with_suffix(".parquet.tmp")
        chunk.to_parquet(tmp, compression=config.COMPRESSION,
                         compression_level=config.COMPRESSION_LEVEL, index=False)
        store.atomic_replace(tmp, p)

    if verbose:
        log(f"  etfs: {len(df):,} bars, {df['ticker'].nunique()}/{len(syms)} symbols, "
            f"{df['date'].min()} -> {df['date'].max()}")
    return df


def read_etfs(start: str | None = None, end: str | None = None) -> pd.DataFrame:
    if not config.BARS_ETF.exists():
        return pd.DataFrame(columns=store.SCHEMA)
    parts = sorted(config.BARS_ETF.glob("*.parquet"))
    if not parts:
        return pd.DataFrame(columns=store.SCHEMA)
    df = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    df["ticker"] = df["ticker"].astype(str)
    if start:
        df = df[df["date"] >= start]
    if end:
        df = df[df["date"] <= end]
    return df.sort_values(["ticker", "date"]).reset_index(drop=True)


def sector_strength(asof: str, lookback: int = 20) -> dict[str, float]:
    """Each sector ETF's return over `lookback` sessions, as revealed sentiment.

    Price is the one sentiment measure that is never sparse, never revised and
    never rate-limited -- which is exactly what the no-news names need.
    """
    df = read_etfs(end=asof)
    if df.empty:
        return {}
    out = {}
    for sym, g in df.groupby("ticker"):
        c = g.sort_values("date")["close"].to_numpy(dtype="float64")
        if len(c) > lookback and c[-lookback - 1] > 0:
            out[str(sym)] = float(c[-1] / c[-lookback - 1] - 1.0)
    return out


# ===========================================================================
# 3. Sector map from SEC EDGAR SIC codes
# ===========================================================================
# Two-digit SIC -> a sector label that matches how the market actually groups
# these names. Coarser than the 4-digit code on purpose: the screener needs
# "is this a biotech" resolution, not "surgical vs dental instruments".
SIC_SECTOR = [
    ((100, 999), "Agriculture"), ((1000, 1099), "Mining"),
    ((1200, 1299), "Coal"), ((1300, 1399), "Energy"),
    ((1400, 1499), "Mining"), ((1500, 1799), "Construction"),
    ((2000, 2199), "Consumer Staples"), ((2200, 2399), "Consumer Discretionary"),
    ((2400, 2599), "Industrials"), ((2600, 2699), "Materials"),
    ((2700, 2799), "Communication"), ((2800, 2829), "Chemicals"),
    ((2830, 2836), "Biotech"), ((2840, 2899), "Chemicals"),
    ((2900, 2999), "Energy"), ((3000, 3199), "Materials"),
    ((3200, 3299), "Materials"), ((3300, 3399), "Materials"),
    ((3400, 3569), "Industrials"), ((3570, 3579), "Technology"),
    ((3580, 3599), "Industrials"), ((3600, 3639), "Technology"),
    ((3640, 3669), "Technology"), ((3670, 3679), "Semiconductors"),
    ((3680, 3689), "Technology"), ((3690, 3699), "Technology"),
    ((3700, 3716), "Consumer Discretionary"), ((3720, 3729), "Aerospace"),
    ((3730, 3759), "Industrials"),
    # 376x is guided missiles and SPACE VEHICLES -- this is the RDW range, and
    # the calibration anchor landing in a generic "Industrials" bucket would put
    # the whole space cohort under XLI instead of ITA.
    ((3760, 3769), "Aerospace"),
    ((3770, 3799), "Industrials"), ((3800, 3826), "Technology"),
    ((3827, 3851), "Healthcare"), ((3860, 3899), "Consumer Discretionary"),
    # 45xx is transportation BY AIR -- airlines, not aerospace. AAL (SIC 4512) was
    # mapping to ITA, an aerospace & defense ETF whose drivers it does not share.
    ((4000, 4499), "Transport"), ((4500, 4599), "Transport"),
    ((4600, 4699), "Energy"), ((4700, 4799), "Transport"),
    ((4800, 4899), "Communication"), ((4900, 4999), "Utilities"),
    ((5000, 5199), "Industrials"), ((5200, 5999), "Consumer Discretionary"),
    ((6000, 6499), "Financials"), ((6500, 6599), "Real Estate"),
    ((6700, 6799), "Financials"), ((7000, 7299), "Consumer Discretionary"),
    ((7370, 7379), "Technology"), ((7300, 7369), "Industrials"),
    ((7380, 7999), "Consumer Discretionary"), ((8000, 8099), "Healthcare"),
    ((8100, 8999), "Industrials"), ((9000, 9999), "Other"),
]

SECTOR_ETF_FOR = {
    "Technology": "XLK", "Semiconductors": "SMH", "Energy": "XLE",
    "Financials": "XLF", "Healthcare": "XLV", "Biotech": "IBB",
    "Industrials": "XLI", "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP", "Utilities": "XLU", "Materials": "XLB",
    "Chemicals": "XLB", "Real Estate": "XLRE", "Communication": "XLC",
    "Aerospace": "ITA", "Mining": "XLB", "Coal": "XLE", "Transport": "XLI",
    "Construction": "XLI", "Agriculture": "XLP", "Other": "SPY",
}


def sic_to_sector(sic) -> str:
    try:
        s = int(sic)
    except (TypeError, ValueError):
        return "Other"
    for (lo, hi), name in SIC_SECTOR:
        if lo <= s <= hi:
            return name
    return "Other"


def build_sector_map(limit: int = 0, verbose: bool = True,
                     force_scrape: bool = False) -> pd.DataFrame:
    """ticker -> CIK, SIC, sector, sector ETF.

    DELEGATES to fundamentals.sector_map() whenever the fact store has data.
    The scrape below issues one SEC submissions request PER TICKER -- measured
    ~2 req/s, ~44 minutes for the universe -- to fetch a SIC code that `sub.txt`
    in every quarterly fundamentals ZIP already contains. That was the single
    largest duplicated fetch in the project.

    The scrape is kept only for the case where no fundamentals have been
    downloaded yet, and must be asked for explicitly via `force_scrape`.
    """
    if not force_scrape:
        try:
            import fundamentals as FD
            if FD.stored_quarters():
                return FD.sector_map(verbose=verbose)
            if verbose:
                log("  sectors: fact store empty, falling back to the SEC scrape "
                    "(run `python fundamentals.py --backfill` to avoid this)")
        except Exception as exc:                                   # noqa: BLE001
            log(f"  sectors: fact-store path unavailable ({repr(exc)[:60]}); scraping")

    import universe

    hdr = {"User-Agent": config.SEC_UA, "Accept-Encoding": "gzip, deflate"}
    # Reuse the cached map rather than re-GETting company_tickers.json -- the
    # third duplicated fetch. fundamentals.ticker_map() caches to parquet.
    import fundamentals as _FD
    _tm = _FD.ticker_map()
    cik = dict(zip(_tm["ticker"].astype(str), _tm["cik"].astype(int)))

    uni = universe.load() if hasattr(universe, "load") else None
    tickers = (sorted(set(uni["ticker"].astype(str))) if uni is not None and not uni.empty
               else sorted(cik))
    if limit:
        tickers = tickers[:limit]

    existing = {}
    if config.SECTOR_MAP_FILE.exists():
        old = pd.read_parquet(config.SECTOR_MAP_FILE)
        existing = dict(zip(old["ticker"].astype(str), old["sic"]))

    rows, miss, t0 = [], 0, time.time()
    delay = 1.0 / config.SEC_RATE
    for i, t in enumerate(tickers):
        c = cik.get(t.upper())
        if c is None:
            miss += 1
            rows.append({"ticker": t, "cik": None, "sic": None,
                         "sector": "Other", "sector_etf": "SPY"})
            continue
        if t in existing and pd.notna(existing[t]):
            sic = existing[t]                       # sector does not churn
        else:
            try:
                time.sleep(delay)
                s = _SESSION.get(config.SEC_SUBMISSIONS_URL.format(cik=c),
                                 headers=hdr, timeout=30)
                sic = s.json().get("sic") if s.status_code == 200 else None
            except Exception:                                     # noqa: BLE001
                sic = None
        sec = sic_to_sector(sic)
        rows.append({"ticker": t, "cik": c, "sic": sic, "sector": sec,
                     "sector_etf": SECTOR_ETF_FOR.get(sec, "SPY")})
        if verbose and (i + 1) % 500 == 0:
            log(f"    {i + 1}/{len(tickers)} ({time.time() - t0:.0f}s)")

    df = pd.DataFrame(rows)
    tmp = config.SECTOR_MAP_FILE.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, compression=config.COMPRESSION, index=False)
    store.atomic_replace(tmp, config.SECTOR_MAP_FILE)
    if verbose:
        log(f"  sectors: {len(df):,} tickers, {miss} not in SEC's map, "
            f"{df['sector'].nunique()} sectors, {time.time() - t0:.0f}s")
    return df


def load_sector_map() -> pd.DataFrame:
    if config.SECTOR_MAP_FILE.exists():
        return pd.read_parquet(config.SECTOR_MAP_FILE)
    return pd.DataFrame(columns=["ticker", "cik", "sic", "sector", "sector_etf"])


# ===========================================================================
# 4. FRED / ALFRED
# ===========================================================================
def fred_key() -> str | None:
    return os.getenv(config.FRED_KEY_ENV) or None


def fred_series(series_id: str, start: str | None = None,
                vintage: bool = False) -> pd.DataFrame:
    """One FRED series. `vintage=True` returns ALFRED real-time rows.

    With vintage=True each observation carries realtime_start/realtime_end, so a
    backtest can ask "what was the published value of CPI on 2024-03-05" and get
    the number that existed THEN, not the twice-revised one that exists now.
    """
    key = fred_key()
    if not key:
        raise RuntimeError(
            f"{config.FRED_KEY_ENV} not set. Register free at "
            "https://fredaccount.stlouisfed.org/apikeys and put "
            f"{config.FRED_KEY_ENV}=... in .env"
        )
    params = {"series_id": series_id, "api_key": key, "file_type": "json"}
    if start:
        params["observation_start"] = start
    if vintage:
        params.update({"realtime_start": start or "1900-01-01",
                       "realtime_end": "9999-12-31"})

    r = _SESSION.get(f"{config.FRED_BASE}/series/observations",
                     params=params, timeout=60)
    r.raise_for_status()
    obs = r.json().get("observations", [])
    if not obs:
        return pd.DataFrame()
    df = pd.DataFrame(obs)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.rename(columns={"date": "obs_date"})
    keep = ["obs_date", "value"] + (
        ["realtime_start", "realtime_end"] if vintage else [])
    return df[keep].dropna(subset=["value"])


def fetch_fred(start: str | None = None, verbose: bool = True) -> pd.DataFrame:
    """Every configured series, wide by observation date. Best-effort per series."""
    if not fred_key():
        if verbose:
            log(f"  fred: {config.FRED_KEY_ENV} not set -- skipping (free key at "
                "fredaccount.stlouisfed.org)")
        return pd.DataFrame()

    frames, failed = [], []
    for sid, name in config.FRED_SERIES.items():
        try:
            d = fred_series(sid, start=start)
            if d.empty:
                continue
            frames.append(d.rename(columns={"value": name})[["obs_date", name]]
                          .set_index("obs_date"))
        except Exception as exc:                                  # noqa: BLE001
            failed.append(f"{sid}({repr(exc)[:40]})")

    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, axis=1).sort_index().reset_index()
    out = out.rename(columns={"obs_date": "session"})
    if verbose:
        log(f"  fred: {len(out.columns) - 1}/{len(config.FRED_SERIES)} series, "
            f"{len(out):,} dates" + (f"; failed {failed[:3]}" if failed else ""))
    return out


def release_dates(start: str, end: str, verbose: bool = True) -> pd.DataFrame:
    """Scheduled release dates, past AND upcoming, for the configured releases.

    This is what makes `event_proximity` possible: on any date the screener can
    say "CPI prints in 2 sessions", which is a different risk posture from
    "nothing scheduled for three weeks".
    """
    key = fred_key()
    if not key:
        return pd.DataFrame(columns=["date", "release"])
    rows = []
    for rid, name in config.FRED_RELEASES.items():
        try:
            r = _SESSION.get(f"{config.FRED_BASE}/release/dates",
                             params={"release_id": rid, "api_key": key,
                                     "file_type": "json",
                                     "realtime_start": start,
                                     "realtime_end": end,
                                     "include_release_dates_with_no_data": "true"},
                             timeout=60)
            if r.status_code != 200:
                continue
            for d in r.json().get("release_dates", []):
                rows.append({"date": d["date"], "release": name})
        except Exception:                                          # noqa: BLE001
            continue
    df = pd.DataFrame(rows)
    if verbose and not df.empty:
        log(f"  releases: {len(df):,} dates across {df['release'].nunique()} releases")
    return df.sort_values("date").reset_index(drop=True) if not df.empty else df


# ===========================================================================
# 5. GPR and EPU  (no key; both verified 2026-08-06)
# ===========================================================================
def fetch_epu(verbose: bool = True) -> pd.DataFrame:
    """Daily Economic Policy Uncertainty. Verified: 15,191 rows to 2026-08-04."""
    r = _SESSION.get(config.EPU_URL, headers={"User-Agent": config.SEC_UA}, timeout=60)
    r.raise_for_status()
    df = pd.read_csv(io.BytesIO(r.content))
    df = df.dropna(subset=["year", "month", "day"])
    df["session"] = (df["year"].astype(int).astype(str) + "-"
                     + df["month"].astype(int).astype(str).str.zfill(2) + "-"
                     + df["day"].astype(int).astype(str).str.zfill(2))
    out = df[["session", "daily_policy_index"]].rename(
        columns={"daily_policy_index": "epu"})
    if verbose:
        log(f"  epu: {len(out):,} days -> {out['session'].max()}")
    return out


def fetch_gpr(verbose: bool = True) -> pd.DataFrame:
    """Daily Geopolitical Risk index (Caldara & Iacoviello, Federal Reserve Board).

    Needs `xlrd`: the file is a real OLE2 .xls, not an xlsx. Returns empty rather
    than raising if the dependency or the host is missing -- this is an
    enrichment, and a scheduled run must not die for one.
    """
    try:
        r = _SESSION.get(config.GPR_URL, headers={"User-Agent": config.SEC_UA},
                         timeout=90)
        r.raise_for_status()
        df = pd.read_excel(io.BytesIO(r.content))
    except ImportError:
        if verbose:
            log("  gpr: needs `pip install xlrd` (the file is a real .xls) -- skipped")
        return pd.DataFrame(columns=["session", "gpr"])
    except Exception as exc:                                       # noqa: BLE001
        if verbose:
            log(f"  gpr: unavailable ({repr(exc)[:60]}) -- skipped")
        return pd.DataFrame(columns=["session", "gpr"])

    dcol = next((c for c in df.columns if str(c).lower().startswith("date")), df.columns[0])
    gcol = next((c for c in df.columns if str(c).upper() in ("GPRD", "GPR")), None)
    if gcol is None:
        return pd.DataFrame(columns=["session", "gpr"])

    out = pd.DataFrame({
        "session": pd.to_datetime(df[dcol], errors="coerce").dt.strftime("%Y-%m-%d"),
        "gpr": pd.to_numeric(df[gcol], errors="coerce"),
    }).dropna()
    if verbose:
        log(f"  gpr: {len(out):,} days -> {out['session'].max()} "
            f"(last ~{config.GPR_PROVISIONAL_DAYS}d provisional; monthly refresh)")
    return out


# ===========================================================================
# 6. Assemble
# ===========================================================================
def build(verbose: bool = True, skip_sectors: bool = True) -> pd.DataFrame:
    """Rebuild data/_macro.parquet from every available source."""
    t0 = time.time()
    parts: list[pd.DataFrame] = []

    br = build_breadth(verbose=verbose)
    if br.empty:
        raise RuntimeError("no bars in the store; run `python bars.py --backfill`")
    base = br

    try:
        fetch_etfs(verbose=verbose)
    except Exception as exc:                                       # noqa: BLE001
        log(f"  etfs: failed ({repr(exc)[:70]})")

    etf = read_etfs()
    if not etf.empty:
        piv = etf.pivot_table(index="date", columns="ticker", values="close")
        rets = piv.pct_change(20).add_prefix("rs20_").reset_index()
        rets = rets.rename(columns={"date": "session"})
        base = base.merge(rets, on="session", how="left")

        # Release-day surprise: the ATR-normalised move of the proxy basket. No
        # consensus number is needed -- the reaction IS the surprise.
        prox = [c for c in piv.columns if c in config.MACRO_PROXIES]
        if prox:
            r1 = piv[prox].pct_change()
            atr = r1.rolling(20, min_periods=10).std()
            z = (r1 / atr).abs()
            base = base.merge(
                pd.DataFrame({"session": piv.index,
                              "macro_shock": z.mean(axis=1).values}),
                on="session", how="left")

    for fn in (fetch_epu, fetch_gpr):
        try:
            d = fn(verbose=verbose)
            if not d.empty:
                base = base.merge(d, on="session", how="left")
        except Exception as exc:                                   # noqa: BLE001
            log(f"  {fn.__name__}: failed ({repr(exc)[:70]})")

    try:
        fr = fetch_fred(start=str(base["session"].min()), verbose=verbose)
        if not fr.empty:
            base = base.merge(fr, on="session", how="left")
    except Exception as exc:                                       # noqa: BLE001
        log(f"  fred: failed ({repr(exc)[:70]})")

    # Forward-fill every non-daily series, not just the two daily indices.
    #
    # CPI/PPI/NFP/PCE are MONTHLY and print on a release date, so on any other
    # session they are NaN -- which meant `at()` returned nan for cpi on ~95% of
    # dates and every macro feature built on it silently vanished. Weekly ETF and
    # Treasury series have the same shape on holidays.
    #
    # ffill is safe here and is NOT look-ahead: it carries the last value that
    # was ALREADY PUBLISHED forward. The look-ahead version would be bfill, or
    # using ALFRED's current vintage for a historical date -- which is why
    # fetch_fred asks for observations rather than latest values.
    skip = {"session", "releases"}
    for c in base.columns:
        if c in skip or not pd.api.types.is_numeric_dtype(base[c]):
            continue
        # Breadth is computed per session and is complete by construction; a
        # gap there means too few tickers, and filling it would hide that.
        if c in ("n_tickers", "pct_above_ma", "median_ret", "adv_decl",
                 "pct_new_high", "pct_new_low"):
            continue
        base[c] = base[c].ffill()

    try:
        rel = release_dates(str(base["session"].min()),
                            (date.today() + timedelta(days=120)).isoformat(),
                            verbose=verbose)
        if not rel.empty:
            flags = (rel.groupby("date")["release"]
                     .apply(lambda s: ",".join(sorted(set(s)))).reset_index()
                     .rename(columns={"date": "session", "release": "releases"}))
            base = base.merge(flags, on="session", how="left")
    except Exception as exc:                                       # noqa: BLE001
        log(f"  releases: failed ({repr(exc)[:70]})")

    tmp = config.MACRO_FILE.with_suffix(".parquet.tmp")
    base.to_parquet(tmp, compression=config.COMPRESSION,
                    compression_level=config.COMPRESSION_LEVEL, index=False)
    store.atomic_replace(tmp, config.MACRO_FILE)

    if verbose:
        cols = [c for c in base.columns if c != "session"]
        log(f"  macro: {len(base):,} sessions x {len(cols)} columns, "
            f"{time.time() - t0:.0f}s -> {config.MACRO_FILE.name}")
    return base


def load() -> pd.DataFrame:
    if config.MACRO_FILE.exists():
        return pd.read_parquet(config.MACRO_FILE)
    return pd.DataFrame(columns=["session"])


def at(asof: str) -> dict:
    """The macro row for `asof`, as a plain dict. Never reads past `asof`."""
    df = load()
    if df.empty:
        return {}
    df = df[df["session"] <= asof]
    if df.empty:
        return {}
    row = df.iloc[-1].to_dict()
    row["_stale_sessions"] = int((df["session"] < asof).sum() and
                                 (row["session"] != asof))
    return row


def regime(asof: str) -> tuple[str, float]:
    """(label, breadth) -- a one-word market posture for the report header."""
    r = at(asof)
    b = r.get("pct_above_ma")
    if b is None or not np.isfinite(b):
        return "UNKNOWN", float("nan")
    for lo, name in ((0.65, "RISK_ON"), (0.50, "CONSTRUCTIVE"),
                     (0.35, "MIXED"), (0.20, "RISK_OFF")):
        if b >= lo:
            return name, float(b)
    return "CAPITULATION", float(b)


# ===========================================================================
# CLI
# ===========================================================================
def selftest(verbose: bool = True) -> None:
    fails = []

    # SIC mapping must be total and stable at the boundaries.
    assert sic_to_sector(3674) == "Semiconductors", sic_to_sector(3674)
    assert sic_to_sector(2836) == "Biotech", sic_to_sector(2836)
    assert sic_to_sector(3721) == "Aerospace", sic_to_sector(3721)
    assert sic_to_sector(6021) == "Financials", sic_to_sector(6021)
    # Space vehicles are aerospace (RDW, the calibration anchor, lives here)...
    assert sic_to_sector(3760) == "Aerospace", sic_to_sector(3760)
    # ...and airlines are not, however adjacent the SIC codes look.
    assert sic_to_sector(4512) == "Transport", sic_to_sector(4512)
    for junk in (None, "", "abc", -1, 99999):
        assert sic_to_sector(junk) == "Other", junk
    for _, s in SIC_SECTOR:
        if s not in SECTOR_ETF_FOR:
            fails.append(f"sector {s!r} has no ETF mapping")

    df = load()
    if not df.empty:
        # `at()` must never return a row later than asof -- the whole point.
        mid = df["session"].iloc[len(df) // 2]
        r = at(mid)
        if r and r["session"] > mid:
            fails.append(f"at({mid}) returned a row from {r['session']}")
        if "pct_above_ma" in df.columns:
            v = df["pct_above_ma"].dropna()
            if len(v) and not ((v >= 0).all() and (v <= 1).all()):
                fails.append("pct_above_ma outside [0,1]")

    if fails:
        print("SELFTEST FAILURES:")
        for f in fails:
            print("  -", f)
        raise SystemExit(1)
    if verbose:
        print(f"macro selftest OK  ({len(SIC_SECTOR)} SIC ranges, "
              f"{'store present' if not df.empty else 'store empty'})")


def show(asof: str) -> None:
    r = at(asof)
    if not r:
        print("  (no macro data -- run `python macro.py --update`)")
        return
    lab, b = regime(asof)
    print(f"  session      {r.get('session')}   regime {lab}")
    for k, v in r.items():
        if k.startswith("_") or k == "session":
            continue
        if isinstance(v, float) and np.isfinite(v):
            print(f"    {k:22} {v:>10.4f}")
        elif isinstance(v, str):
            print(f"    {k:22} {v}")


def main() -> int:
    config.safe_console()
    ap = argparse.ArgumentParser(description="Macro / sector layer.")
    ap.add_argument("--update", action="store_true", help="rebuild _macro.parquet")
    ap.add_argument("--breadth", action="store_true")
    ap.add_argument("--etfs", action="store_true")
    ap.add_argument("--sectors", action="store_true", help="rebuild the SEC SIC map")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--show", metavar="DATE", default=None)
    a = ap.parse_args()

    config.dirs()

    if a.selftest:
        selftest()
    elif a.update:
        build()
    elif a.breadth:
        print(build_breadth().tail(10).to_string(index=False))
    elif a.etfs:
        fetch_etfs()
    elif a.sectors:
        build_sector_map(limit=a.limit)
    elif a.show:
        show(a.show)
    else:
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
