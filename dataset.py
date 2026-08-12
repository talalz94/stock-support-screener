"""
Read-only seam over the bar store.

Everything downstream (screening, replay, ad-hoc analysis in a notebook) goes
through here rather than touching parquet paths, so the storage layout stays an
implementation detail. Same role as `Stock Screener/dataset.py`.

    python dataset.py            print a store summary
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

import config
import store

# The per-ticker contract every detection function expects: one ticker, sorted by
# datetime, fresh 0..N-1 RangeIndex. Inherited from the sibling project's
# indicators.py so `compute_all` can be used unmodified.
OHLCV = ["ticker", "datetime", "date", "open", "high", "low", "close",
         "volume", "trades", "vwap"]


def load(interval: str = "1d", start: str | None = None, end: str | None = None,
         tickers: list[str] | None = None,
         columns: list[str] | None = None) -> pd.DataFrame:
    """Long-format bars. Opens only the month partitions in range."""
    return store.read(interval, start=start, end=end, tickers=tickers,
                      columns=columns)


def history(ticker: str, interval: str = "1d", start: str | None = None,
            end: str | None = None) -> pd.DataFrame:
    """One ticker, ready for the indicator contract."""
    df = load(interval, start=start, end=end, tickers=[ticker])
    return df.reset_index(drop=True)


def panel(tickers: list[str], interval: str = "1d", start: str | None = None,
          end: str | None = None) -> dict[str, pd.DataFrame]:
    """{ticker: frame} with each frame satisfying the indicator contract.

    One store read, then a groupby -- far cheaper than a read per ticker.
    """
    df = load(interval, start=start, end=end, tickers=tickers)
    if df.empty:
        return {}
    return {str(t): g.reset_index(drop=True)
            for t, g in df.groupby("ticker", sort=False, observed=True)}


def arrays(df: pd.DataFrame, cols: list[str] | None = None) -> dict[str, np.ndarray]:
    """Frame -> {col: ndarray}.

    The detection code works in integer positions on numpy arrays; mixing those
    with label-indexed Series is a silent-misalignment bug waiting to happen, so
    the conversion is done once, here.
    """
    cols = cols or [c for c in ("open", "high", "low", "close", "volume",
                               "trades", "vwap") if c in df.columns]
    out = {c: df[c].to_numpy(dtype=float) for c in cols}
    if "datetime" in df.columns:
        out["datetime"] = df["datetime"].to_numpy()
    if "date" in df.columns:
        out["date"] = df["date"].to_numpy(dtype=object)
    return out


def latest(interval: str = "1d") -> pd.DataFrame:
    """The most recent bar per ticker."""
    ms = store.months(interval)
    if not ms:
        return pd.DataFrame(columns=OHLCV)
    df = store.read(interval, start=f"{ms[-1]}-01")
    if df.empty and len(ms) > 1:
        df = store.read(interval, start=f"{ms[-2]}-01")
    if df.empty:
        return df
    return df.sort_values("datetime").groupby("ticker", observed=True).tail(1)


def available(interval: str = "1d") -> list[str]:
    ms = store.months(interval)
    if not ms:
        return []
    df = store.read(interval, start=f"{ms[-1]}-01", columns=["ticker"])
    return sorted(df["ticker"].astype(str).unique())


def coverage(interval: str = "1d") -> pd.DataFrame:
    return store.coverage(interval)


def summary(interval: str = "1d") -> dict:
    cov = store.coverage(interval)
    if cov.empty:
        return {"months": 0, "rows": 0, "bytes": 0}
    return {
        "months": len(cov),
        "first_month": cov["month"].iloc[0],
        "last_month": cov["month"].iloc[-1],
        "rows": int(cov["rows"].sum()),
        "sessions": int(cov["sessions"].sum()),
        "max_tickers_in_a_month": int(cov["tickers"].max()),
        "bytes": int(cov["bytes"].sum()),
    }


def main() -> int:
    config.safe_console()
    import bars

    for interval in ("1d", "1h"):
        s = summary(interval)
        if not s["months"]:
            print(f"[{interval}] empty")
            continue
        print(f"[{interval}] {s['months']} month file(s) "
              f"{s['first_month']} -> {s['last_month']}")
        print(f"       {s['rows']:,} rows, {s['bytes'] / 1e6:.0f} MB, "
              f"up to {s['max_tickers_in_a_month']:,} tickers in a month")

    ps = bars.load_panel_stats()
    if not ps.empty:
        print(f"\npanel stats  {len(ps):,} tickers, "
              f"newest bar {ps['last_date'].max()}")
        gates = {
            f"price >= {config.MIN_PRICE}":
                int((ps["last_close"] >= config.MIN_PRICE).sum()),
            f"$vol >= {config.MIN_DOLLAR_VOL / 1e6:.0f}M":
                int((ps["dollar_vol_20"] >= config.MIN_DOLLAR_VOL).sum()),
            f"bars >= {config.MIN_BARS}":
                int((ps["n_bars"] >= config.MIN_BARS).sum()),
            f"<= {config.MAX_PCT_OF_250D_HIGH:.0%} of 250d high":
                int((ps["pct_of_250d_high"] <= config.MAX_PCT_OF_250D_HIGH).sum()),
            f"250d range >= {config.MIN_250D_RANGE_X}x":
                int((ps["range_250_x"] >= config.MIN_250D_RANGE_X).sum()),
        }
        print("  individual gate pass counts:")
        for k, v in gates.items():
            print(f"    {k:32} {v:>6,}")
    else:
        print("\npanel stats  (none -- run `python bars.py --panel-stats`)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

