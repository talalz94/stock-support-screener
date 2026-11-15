#!/usr/bin/env python
"""Interim quarters from a provider for the companies whose SEC record holds year-ends only.

Asked 2026-11-11 ("Why was PAM 2026 earnings not available?") and started 2026-11-15. A foreign
private issuer files its annual report on Form 20-F (or 40-F from Canada), which carries XBRL, and
its quarterly results on Form 6-K, which does not; so the SEC fact store -- this project's backbone
-- holds only their year-ends. Measured 2026-11-15: 209 of 3,298 tradeable names with any figure
for a period ending 2025 or later (6.3%): 130 file a 20-F, 78 a 40-F, 1 a 10-K. Pampa Energia's
newest SEC period was 2025-12-31 while it had published three 2026 quarters.

WHAT THIS DOES: for exactly those names, Yahoo Finance's quarterly income statement, balance sheet
and cash flow (yfinance, the provider this project already uses for second opinions), mapped onto
the fact store's own concept names, stored in data/_provider_quarters.parquet with the currency the
company reports in. A probe of 10 of the names on 2026-11-15 found interim quarters for all 10,
2026 included, in seven currencies.

HOW IT IS USED -- DISPLAY ONLY: the stock page's quarterly table adds these quarters as columns
marked as Yahoo's, never overriding a figure the SEC store has, and never when the currency differs
from the one the company files in with the SEC. Nothing here reaches a score or a study: the house
rule is provider values for what a page shows today, never a provider's current values written into
history (these are not point in time).

Run: python provider_quarters.py --fetch [T ...]   fetch (all targets when no ticker is given)
     python provider_quarters.py --show T           the stored quarters for one ticker
     python provider_quarters.py --selftest
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from datetime import date, datetime

import numpy as np
import pandas as pd

import config

config.safe_console()

STORE = config.DATA / "_provider_quarters.parquet"
SOURCE = "Yahoo Finance"
PAUSE_S = 1.0                  # between names: a light, steady pace
TIMEOUT_S = 45                 # per name; yfinance has no timeout of its own
RECENT = "2025-01-01"          # "year-ends only" = no interim SEC figure for a period ending since then

# Yahoo's statement line -> the fact store's concept. Only what the page's table uses (its rows and
# the ratios derived from them: margins, EBITDA, FCF, ROE, debt to equity). The first present wins.
LINES = {
    "income": [("revenue", ("Total Revenue", "Operating Revenue")),
               ("gross_profit", ("Gross Profit",)),
               ("opinc", ("Operating Income", "Total Operating Income As Reported")),
               ("net_income", ("Net Income Common Stockholders", "Net Income")),
               ("eps_diluted", ("Diluted EPS",)),
               ("dna", ("Reconciled Depreciation",))],
    "cashflow": [("cfo", ("Operating Cash Flow",)),
                 ("capex", ("Capital Expenditure",))],
    "balance": [("assets", ("Total Assets",)),
                ("equity", ("Stockholders Equity", "Common Stock Equity")),
                ("debt_lt", ("Long Term Debt",)),
                ("debt_st", ("Current Debt",)),
                ("cash", ("Cash And Cash Equivalents",)),
                ("ppe", ("Net PPE",))],
}
FLOWS = ("revenue", "gross_profit", "opinc", "net_income")


def log(m: str) -> None:
    print(f"  [provider_quarters] {m}", flush=True)


# ============================================================ which names
def targets(asof: str | None = None) -> list[str]:
    """Tradeable names whose SEC store has an annual figure but no interim one (a period of 1-3
    quarters) ending since RECENT: the 20-F and 40-F filers above."""
    import bars
    import calendar_us
    import fundamentals as FD
    asof = asof or calendar_us.last_closed_session()
    uni = set(bars.tradeable_universe(asof))
    cmap = pd.read_parquet(config.DATA / "_cik_map.parquet").drop_duplicates("ticker")
    cmap = cmap[cmap.ticker.isin(uni)]
    f = FD.read(start_q=f"{int(RECENT[:4]) - 1}q3")
    dd = f.ddate.astype(str).str.replace("-", "")
    f = f[(dd >= RECENT.replace("-", "")) & f.qtrs.isin([1, 2, 3, 4])]
    g = f.groupby("cik").qtrs.agg(lambda s: (bool(s.isin([1, 2, 3]).any()), bool((s == 4).any())))
    year_end_only = {int(c) for c, (interim, annual) in g.items() if annual and not interim}
    return sorted(cmap[cmap.cik.astype(int).isin(year_end_only)].ticker.astype(str))


# ============================================================ fetching
def _statement(df, rows) -> dict[str, pd.Series]:
    out = {}
    if df is None or getattr(df, "empty", True):
        return out
    for concept, names in rows:
        for nm in names:
            if nm in df.index:
                s = pd.to_numeric(df.loc[nm], errors="coerce")
                if isinstance(s, pd.DataFrame):          # a duplicated line: the first
                    s = s.iloc[0]
                out[concept] = s
                break
    return out


def fetch_one(ticker: str) -> tuple[pd.DataFrame, str | None]:
    """One name's quarters as tidy rows (period, concept, value), and its reporting currency."""
    import yfinance as yf
    tk = yf.Ticker(ticker)
    series = {}
    for kind, attr in (("income", "quarterly_income_stmt"), ("cashflow", "quarterly_cashflow"),
                       ("balance", "quarterly_balance_sheet")):
        try:
            series.update(_statement(getattr(tk, attr), LINES[kind]))
        except Exception:                                        # noqa: BLE001
            continue
    try:
        ccy = (tk.info or {}).get("financialCurrency")
    except Exception:                                            # noqa: BLE001
        ccy = None
    rows = []
    for concept, s in series.items():
        for period, v in s.items():
            if v is None or not np.isfinite(float(v)):
                continue
            v = float(v)
            if concept == "capex":
                v = -v if v < 0 else v                  # Yahoo shows a payment negative; the fact store positive
            rows.append({"period": pd.Timestamp(period).strftime("%Y-%m-%d"), "concept": concept, "value": v})
    return pd.DataFrame(rows, columns=["period", "concept", "value"]), ccy


def _with_timeout(fn, *args, timeout: float = TIMEOUT_S):
    box = {}

    def run():
        try:
            box["v"] = fn(*args)
        except Exception as exc:                                 # noqa: BLE001
            box["e"] = exc
    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        raise TimeoutError(f"no answer in {timeout:.0f}s")
    if "e" in box:
        raise box["e"]
    return box["v"]


def fetch(tickers: list[str] | None = None, verbose: bool = True) -> dict:
    """Fetch and store. Each name's rows replace its old ones; a name that fails keeps them."""
    tickers = tickers or targets()
    old = pd.read_parquet(STORE) if STORE.exists() else pd.DataFrame(
        columns=["ticker", "period", "concept", "value", "currency", "fetched"])
    got, failed, empty = {}, [], []
    t0 = time.time()
    for i, t in enumerate(tickers):
        try:
            rows, ccy = _with_timeout(fetch_one, t)
        except Exception as exc:                                 # noqa: BLE001
            failed.append(f"{t}({type(exc).__name__})")
            continue
        if rows.empty:
            empty.append(t)
        else:
            got[t] = rows.assign(ticker=t, currency=ccy, fetched=date.today().isoformat())
        if verbose and (i + 1) % 25 == 0:
            log(f"{i + 1}/{len(tickers)} names, {time.time() - t0:.0f}s")
        time.sleep(PAUSE_S)
    keep = old[~old.ticker.isin(list(got))]
    frames = [f for f in [keep] + list(got.values()) if len(f)]
    new = pd.concat(frames, ignore_index=True) if frames else keep
    tmp = STORE.with_suffix(".parquet.tmp")
    new.to_parquet(tmp, index=False)
    import store
    store.atomic_replace(tmp, STORE)
    out = {"asked": len(tickers), "with_quarters": len(got), "none_published": empty, "failed": failed,
           "seconds": round(time.time() - t0)}
    if verbose:
        log(f"{len(got)} of {len(tickers)} name(s) with quarters; {len(empty)} with none; "
            f"{len(failed)} failed{': ' + ', '.join(failed[:8]) if failed else ''}; {out['seconds']}s")
    return out


# ============================================================ reading, for the stock page
_CACHE: dict = {}


def quarters(ticker: str) -> tuple[pd.DataFrame, str | None, str | None]:
    """(period x concept frame, the reporting currency, the fetch date) for one ticker; empty when
    nothing is stored."""
    if "all" not in _CACHE or _CACHE.get("mtime") != (STORE.stat().st_mtime if STORE.exists() else None):
        _CACHE["all"] = pd.read_parquet(STORE) if STORE.exists() else pd.DataFrame(
            columns=["ticker", "period", "concept", "value", "currency", "fetched"])
        _CACHE["mtime"] = STORE.stat().st_mtime if STORE.exists() else None
    a = _CACHE["all"]
    x = a[a.ticker == str(ticker).upper()]
    if x.empty:
        return pd.DataFrame(), None, None
    w = x.pivot_table(index="period", columns="concept", values="value", aggfunc="last").sort_index()
    return w, (x.currency.dropna().iloc[0] if x.currency.notna().any() else None), str(x.fetched.max())


def merge(h: pd.DataFrame, ticker: str, sec_currency: str | None) -> pd.DataFrame:
    """The SEC quarterly history `h` with the provider's quarters added, for a name whose SEC record
    has no interim figure: provider values fill only cells the SEC store leaves empty, and nothing
    is added when the provider reports in another currency than the SEC filings. The periods that
    took any provider value are listed in attrs["provider_periods"] (with attrs source / fetched)."""
    q, ccy, fetched = quarters(ticker)
    if q.empty:
        return h
    if sec_currency and ccy and str(sec_currency).upper() != str(ccy).upper():
        return h
    flows = [c for c in FLOWS if c in h.columns]
    if flows and (h.index.astype(str) >= RECENT).any():
        recent = h[h.index.astype(str) >= RECENT][flows]
        if recent.notna().any(axis=1).sum() >= 2:          # the SEC store has its interim quarters
            return h
    idx = sorted(set(map(str, h.index)) | set(map(str, q.index)))
    out = h.copy()
    out.index = out.index.astype(str)
    out = out.reindex(idx)
    took = set()
    for c in q.columns:
        if c not in out.columns:
            out[c] = np.nan
        src = q[c].reindex(idx)
        fill = out[c].isna() & src.notna()
        if fill.any():
            out.loc[fill, c] = src[fill]
            took |= set(out.index[fill])
    out.index.name = h.index.name
    out.attrs.update(h.attrs)
    out.attrs["provider_periods"] = sorted(took)
    out.attrs["provider_source"] = SOURCE
    out.attrs["provider_fetched"] = fetched
    out.attrs["provider_currency"] = ccy
    return out


# ============================================================ checks
def selftest() -> int:
    h = pd.DataFrame({"assets": [100.0, 120.0], "equity": [50.0, 60.0]}, index=["2024-12-31", "2025-12-31"])
    h.index.name = "ddate"
    q = pd.DataFrame({"revenue": [10.0, 11.0, 12.0], "net_income": [1.0, 1.5, 2.0], "assets": [999.0, 125.0, 130.0]},
                     index=["2025-12-31", "2026-03-31", "2026-06-30"])
    real = globals()["quarters"]
    try:
        globals()["quarters"] = lambda t: (q, "USD", "2026-11-15")
        m = merge(h, "XX", "USD")
        assert list(m.index) == ["2024-12-31", "2025-12-31", "2026-03-31", "2026-06-30"], list(m.index)
        assert m.at["2025-12-31", "assets"] == 120.0, "a provider value overrode the SEC's"
        assert m.at["2026-03-31", "assets"] == 125.0 and m.at["2025-12-31", "revenue"] == 10.0
        assert m.attrs["provider_periods"] == ["2025-12-31", "2026-03-31", "2026-06-30"], m.attrs
        assert merge(h, "XX", "EUR") is h, "currencies must not mix"
        hq = h.assign(revenue=[5.0, 6.0], net_income=[0.5, 0.6])
        hq.loc["2025-06-30"] = [110.0, 55.0, 4.0, 0.4]
        hq = hq.sort_index()
        assert merge(hq, "XX", "USD") is hq, "an SEC store with its interim quarters must be left alone"
    finally:
        globals()["quarters"] = real
    w, ccy, fetched = quarters("PAM")
    print(f"  [provider_quarters] SEC values never overridden, currencies never mixed, a filer with its own "
          f"interim quarters left alone; stored for PAM: {len(w)} quarter(s), {ccy}, fetched {fetched}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Interim quarters from a provider for year-end-only SEC filers.")
    ap.add_argument("--fetch", nargs="*", metavar="T")
    ap.add_argument("--show", metavar="T")
    ap.add_argument("--targets", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.targets:
        t = targets()
        print(f"  {len(t)} year-end-only name(s): {', '.join(t[:30])}{' ...' if len(t) > 30 else ''}")
        return 0
    if a.show:
        w, ccy, fetched = quarters(a.show)
        print(f"  {a.show}: {ccy}, fetched {fetched}\n{w.T.to_string() if len(w) else '  nothing stored'}")
        return 0
    if a.fetch is not None:
        fetch([t.upper() for t in a.fetch] or None)
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
