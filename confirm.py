"""
Stage 2: optional enrichment of the shortlist.

Two independent, both OPTIONAL and both NON-FATAL. Neither can change a
pass/fail verdict -- they only annotate -- so a failure here degrades the report
rather than losing the day's scan.

  hourly annotations   Daily bars hide things that matter: whether an hourly CLOSE
                       pierced the support line while the daily close held, and
                       whether the bounce day closed on its high or faded into the
                       close. Only affordable for a shortlist: intraday pages are
                       ~250 bars regardless of `limit` (measured 116 bars/s vs
                       6,857 for daily -- 59x slower), so hourly for 2,600 names
                       is ~6 hours and hourly for 24 names is ~30 seconds.

  market cap           Alpaca has no market cap, so this comes from yfinance for
                       the flagged names ONLY, cached 30 days. It powers the size
                       tier and the size component of the score. Enriching the
                       whole universe would mean a second vendor dependency on the
                       critical path for no benefit.

    python confirm.py --date 2026-08-03
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

import bars
import calendar_us
import config
import store


# ------------------------------------------------------------------ market cap
def load_fundamentals() -> pd.DataFrame:
    if config.FUNDAMENTALS_FILE.exists():
        return pd.read_parquet(config.FUNDAMENTALS_FILE)
    return pd.DataFrame(columns=["ticker", "market_cap", "sector", "industry",
                                 "shares_out", "asof"])


def _save_fundamentals(df: pd.DataFrame) -> None:
    config.FUNDAMENTALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.FUNDAMENTALS_FILE.with_suffix(".parquet.tmp")
    df.drop_duplicates("ticker", keep="last").to_parquet(
        tmp, compression=config.COMPRESSION, index=False)
    tmp.replace(config.FUNDAMENTALS_FILE)


def enrich_fundamentals(tickers: list[str], verbose: bool = True) -> pd.DataFrame:
    """Market cap / sector for `tickers`, using the cache where it is fresh."""
    have = load_fundamentals()
    fresh_cut = (date.today() - timedelta(days=config.FUNDAMENTALS_TTL_DAYS)).isoformat()
    fresh = set(have.loc[have["asof"] >= fresh_cut, "ticker"].astype(str)) \
        if not have.empty else set()
    todo = [t for t in tickers if t not in fresh]

    if not todo:
        if verbose:
            print(f"  fundamentals: all {len(tickers)} cached")
        return have
    if verbose:
        print(f"  fundamentals: {len(todo)} to fetch, {len(tickers) - len(todo)} cached")

    try:
        import yfinance as yf
    except ImportError:
        print("  ! yfinance not installed; skipping market-cap enrichment")
        return have

    rows, t0 = [], time.time()
    for i, t in enumerate(todo, 1):
        # yfinance wants the dash form for class shares; our canonical key is the
        # Alpaca dot form, so translate on the way OUT only.
        ysym = t.replace(".", "-")
        mc = sector = industry = shares = None
        try:
            info = yf.Ticker(ysym).info or {}
            mc = info.get("marketCap")
            sector = info.get("sector")
            industry = info.get("industry")
            shares = info.get("sharesOutstanding")
        except Exception as exc:                       # noqa: BLE001
            if verbose and i <= 3:
                print(f"    ! {t}: {repr(exc)[:70]}")
        rows.append({"ticker": t,
                     "market_cap": float(mc) if mc else np.nan,
                     "sector": sector or "", "industry": industry or "",
                     "shares_out": float(shares) if shares else np.nan,
                     "asof": date.today().isoformat()})
        if verbose and (i % 10 == 0 or i == len(todo)):
            print(f"    {i}/{len(todo)}  {time.time() - t0:.0f}s")

    new = pd.DataFrame(rows)
    # Drop all-NA frames before concat: pandas 2.x deprecates inferring dtypes
    # across them and would change the column types under us.
    pieces = [d for d in (have, new) if not d.empty and not d.isna().all().all()]
    out = pd.concat(pieces, ignore_index=True) if pieces else new
    _save_fundamentals(out)
    got = int(pd.DataFrame(rows)["market_cap"].notna().sum())
    if verbose:
        print(f"  fundamentals: got market cap for {got}/{len(todo)}")
    return load_fundamentals()


# ------------------------------------------------------------------ hourly
def hourly_annotations(flags: pd.DataFrame, days: int | None = None,
                       verbose: bool = True) -> pd.DataFrame:
    """Per-ticker hourly checks. Returns a frame keyed by ticker (may be empty)."""
    if flags is None or flags.empty:
        return pd.DataFrame()
    tickers = flags["ticker"].astype(str).tolist()
    days = days or config.CONFIRM_DAYS

    t0 = time.time()
    try:
        bars.fetch_hourly(tickers, days)
    except Exception as exc:                           # noqa: BLE001
        print(f"  ! hourly fetch failed ({repr(exc)[:90]}); annotations skipped")
        return pd.DataFrame()

    lcs = calendar_us.last_closed_session()
    sessions = calendar_us.all_sessions()
    start = calendar_us.session_offset(sessions, lcs, days)
    h1 = store.read("1h", start=start, end=lcs, tickers=tickers)
    if h1.empty:
        return pd.DataFrame()
    if verbose:
        print(f"  hourly: {len(h1):,} bars for {h1['ticker'].nunique()} tickers "
              f"in {time.time() - t0:.0f}s")

    rows = []
    for t, g in h1.groupby("ticker", sort=False, observed=True):
        f = flags[flags["ticker"].astype(str) == str(t)]
        if f.empty:
            continue
        r = f.iloc[0]
        level = float(r.get("level") or 0)
        low_date = str(r.get("bounce_low_date") or "")
        if level <= 0 or not low_date:
            continue

        g = g.sort_values("datetime")
        post = g[g["date"] > low_date]
        at_low = g[g["date"] == low_date]

        # Successive higher hourly lows since the daily bounce low: separates a
        # real turn from one wide green daily bar.
        hl = 0
        lows = post["low"].to_numpy(float)
        for i in range(len(lows) - 1, 0, -1):
            if lows[i] > lows[i - 1]:
                hl += 1
            else:
                break

        base_v = float(np.nanmedian(g["volume"].to_numpy(float)))
        bounce_v = float(np.nanmean(post["volume"].to_numpy(float))) if len(post) else np.nan

        # An hourly CLOSE below the level that the daily close hid entirely.
        pierce = bool((post["close"].to_numpy(float) < level * (1 - config.BREAK_TOL)).any())

        # Did the bounce day close on its high, or fade into the close?
        rej = np.nan
        if len(at_low) or len(post):
            day = post[post["date"] == post["date"].min()] if len(post) else at_low
            if len(day):
                hi = float(day["high"].max())
                lo = float(day["low"].min())
                cl = float(day["close"].iloc[-1])
                if hi > lo:
                    rej = float((hi - cl) / (hi - lo))

        rows.append({
            "ticker": str(t),
            "h1_higher_lows": int(hl),
            "h1_bounce_vol_ratio": (bounce_v / base_v) if base_v > 0 else np.nan,
            "h1_base_pierce": pierce,
            "h1_intraday_reject": rej,
            "h1_bars": int(len(g)),
        })
    return pd.DataFrame(rows)


def apply(flags: pd.DataFrame, do_hourly: bool = True, do_fundamentals: bool = True,
          verbose: bool = True) -> pd.DataFrame:
    """Annotate `flags` in place-ish. Never raises; always returns a usable frame."""
    if flags is None or flags.empty:
        return flags
    out = flags.copy()
    tickers = out["ticker"].astype(str).tolist()

    if do_fundamentals:
        try:
            f = enrich_fundamentals(tickers, verbose)
            if not f.empty:
                f = f.drop_duplicates("ticker", keep="last").set_index("ticker")
                for col in ("market_cap", "sector", "industry"):
                    if col in f:
                        mapped = out["ticker"].astype(str).map(f[col])
                        if col == "market_cap":
                            # Only fill: never overwrite a value the screen used.
                            out[col] = out[col].fillna(mapped) if col in out else mapped
                        else:
                            out[col] = mapped
        except Exception as exc:                       # noqa: BLE001
            print(f"  ! fundamentals enrichment failed ({repr(exc)[:90]})")

    if do_hourly:
        try:
            h = hourly_annotations(out, verbose=verbose)
            if not h.empty:
                out = out.merge(h, on="ticker", how="left")
        except Exception as exc:                       # noqa: BLE001
            print(f"  ! hourly annotation failed ({repr(exc)[:90]})")

    return out


def main() -> int:
    config.safe_console()
    ap = argparse.ArgumentParser(description="Stage-2 enrichment of the shortlist.")
    ap.add_argument("--date", default=None)
    ap.add_argument("--no-hourly", action="store_true")
    ap.add_argument("--no-fundamentals", action="store_true")
    a = ap.parse_args()

    config.dirs()
    asof = a.date or calendar_us.last_closed_session()
    p = config.FLAGS / f"{asof}.parquet"
    if not p.exists():
        print(f"no flags for {asof} -- run `python screen.py` first")
        return 1

    flags = pd.read_parquet(p)
    print(f"enriching {len(flags)} flag(s) for {asof}")
    out = apply(flags, not a.no_hourly, not a.no_fundamentals)

    import screen
    tmp = p.with_suffix(".parquet.tmp")
    # Same serialisation as screen.write_outputs, or list columns come back as
    # numpy arrays and break the report.
    screen.serialise_lists(out).to_parquet(
        tmp, compression=config.COMPRESSION, index=False)
    tmp.replace(p)

    cols = [c for c in ("ticker", "market_cap", "sector", "h1_higher_lows",
                        "h1_bounce_vol_ratio", "h1_base_pierce",
                        "h1_intraday_reject") if c in out]
    show = out[cols].copy()
    if "market_cap" in show:
        show["market_cap"] = show["market_cap"].map(
            lambda x: f"{x / 1e9:.2f}B" if pd.notna(x) else "-")
    for c in ("h1_bounce_vol_ratio", "h1_intraday_reject"):
        if c in show:
            show[c] = show[c].map(lambda x: f"{x:.2f}" if pd.notna(x) else "-")
    print("\n" + show.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

