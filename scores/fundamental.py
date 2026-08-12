"""
Score module 2: fundamentals.

Joins the point-in-time SEC fact store to price-derived inputs (market cap, beta,
momentum) and emits every metric in fund_metrics.REGISTRY plus four pillar
scores and a composite.

WHERE THE MARKET-CAP COMES FROM, AND WHY IT MATTERS
----------------------------------------------------
Every valuation metric needs a market cap, and market cap = price x shares. The
price is from the bar store; the SHARE COUNT is from XBRL, point-in-time like
everything else. Using a current share count with a historical price -- the
obvious shortcut -- silently rewrites history for any company that has issued or
bought back stock, which in this universe is most of them. A micro-cap that
tripled its share count would have its 2024 market cap overstated threefold, and
every value metric computed from it would be wrong in the same direction.

BETA is measured, not bought: 60-month (or as much as exists) weekly regression
against SPY from the ETF store. It feeds WACC, which feeds the ROIC-WACC spread.

WHY THE PILLAR WEIGHTS ARE ALL 1.0
-----------------------------------
They are equal because nothing has been measured yet. factor_lab.py exists to
decide them, and until it has run there is no honest basis for preferring value
over quality. The bounce screener set W_SUPPORT to 20 by intuition and support
grade A then measured as the WEAKEST grade -- weighting before measuring is a
mistake this project has already made once and should not repeat.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import config
import fund_metrics as FM
import fundamentals as FD
import store
from scores import register

NAME = "fundamental"


class FundamentalModule:
    name = NAME

    def metrics(self) -> list[str]:
        m = list(FM.REGISTRY)
        m += [f"{p}_score" for p in FM.PILLARS]
        m += ["fund_score", "fund_cov", "has_fundamentals", "mktcap", "beta",
              "wacc", "sector", "last_filed", "days_since_filing",
              "currency", "reports_usd"]
        m += ["du_tax_burden", "du_interest_burden", "du_op_margin",
              "du_asset_turnover", "du_leverage"]
        return m

    # ------------------------------------------------------------------
    def compute(self, asof: str, tickers: list[str] | None = None,
                allow_partial: bool = False) -> pd.DataFrame:
        cur = FD.facts_asof(asof, tickers)
        if cur.empty:
            return pd.DataFrame(columns=["ticker", "metric", "value", "label"])

        # The year-ago frame drives every trend component: Piotroski's 5 trend
        # points, all of Beneish, and every growth metric. Taken as a SECOND
        # point-in-time query rather than by shifting the current one, so what
        # was visible a year ago is what gets used.
        prior = FD.facts_asof(_year_before(asof), tickers)
        if prior.empty:
            prior = cur.iloc[0:0]
        prior = _align(prior, cur["ticker"])

        px = _price_inputs(asof, cur["ticker"].tolist(), cur)
        m = FM.compute(cur, prior, px)

        # Carry the price-derived inputs into the output. `FM.compute` consumes
        # them (mktcap feeds every valuation ratio) but does not pass them
        # through, so `mktcap`, `beta` and `mom_12_1` were DECLARED in metrics()
        # and never written -- 0 stored rows. That silently broke any size
        # analysis, since there was no market cap to bucket on.
        for _c in ("mktcap", "beta", "mom_12_1", "price"):
            if _c in px.columns and _c not in m.columns:
                m = m.merge(px[["ticker", _c]], on="ticker", how="left")

        sector = _sector_for(m["ticker"])
        ranks = FM.rank_pillars(m, sector=sector.reset_index(drop=True))
        wide = m.merge(ranks, on="ticker", how="left")

        # "No fundamentals" and "not enough filings yet" look identical in the
        # output and mean completely different things -- the same confusion the
        # news-coverage guard exists to prevent. Caught during the build: with
        # one quarter stored, AAPL/MSFT/ORCL returned every metric NaN because
        # TTM flows need four quarterly periods or one annual, while CELH scored
        # fine purely because it happened to have filed a 10-K. Nothing errored.
        has_ttm = pd.Series(False, index=cur.index)
        for c in ("revenue_ttm", "net_income_ttm", "cfo_ttm"):
            if c in cur.columns:
                has_ttm |= pd.to_numeric(cur[c], errors="coerce").notna()

        rows: list[dict] = []
        emit = set(self.metrics())
        for t, ok in zip(cur["ticker"], has_ttm):
            rows.append({"ticker": t, "metric": "has_fundamentals",
                         "value": 1.0 if ok else 0.0, "label": None})
        for col in wide.columns:
            if col == "ticker" or col.startswith("r_") or col not in emit:
                continue
            v = pd.to_numeric(wide[col], errors="coerce")
            ok = v.notna() & np.isfinite(v)
            for t, x in zip(wide.loc[ok, "ticker"], v[ok]):
                rows.append({"ticker": t, "metric": col, "value": float(x),
                             "label": None})

        # Categorical / provenance
        meta = cur.set_index("ticker")
        # `_sector_for` returns a Series aligned to `wide`'s POSITIONAL index,
        # while the loop below looks up by TICKER. `sector.get("AAPL")` on a
        # 0..n index quietly returns None for every name, so the `sector` metric
        # was declared, computed, and emitted for exactly nobody. Re-key it once
        # here rather than at each call site.
        sector_by_ticker = dict(zip(wide["ticker"], sector)) \
            if len(sector) == len(wide) else {}
        for t in wide["ticker"]:
            s = sector_by_ticker.get(t)
            if isinstance(s, str):
                rows.append({"ticker": t, "metric": "sector",
                             "value": np.nan, "label": s})
            lf = meta["last_filed"].get(t) if "last_filed" in meta.columns else None
            if isinstance(lf, str):
                rows.append({"ticker": t, "metric": "last_filed",
                             "value": np.nan, "label": lf})
                # Staleness is a first-class output: a "current" fundamental
                # score built on a filing 400 days old is a different object
                # from one built on last week's 10-Q, and the reader cannot
                # tell them apart unless it is stated.
                rows.append({"ticker": t, "metric": "days_since_filing",
                             "value": float((pd.Timestamp(asof)
                                             - pd.Timestamp(lf)).days),
                             "label": None})

        # THE REPORTING CURRENCY IS A FIRST-CLASS OUTPUT, for the same reason
        # `days_since_filing` is. A non-USD filer is scored on the scale-free
        # metrics only -- no P/E, no P/B, no EV/EBITDA, no Altman Z, and
        # therefore no value pillar at all. Its `fund_score` can never rest on
        # more than 3 of 4 pillars, so it is being ranked against USD filers on
        # structurally less information.
        #
        # That is a defensible trade (the alternative is excluding 64 large
        # caps outright), but only if the reader can SEE it. Emitting the
        # currency and a plain 1/0 flag means a page can say "value metrics
        # withheld: reports in EUR" instead of leaving a gap that looks like
        # missing data.
        ccy_by_ticker = (dict(zip(cur["ticker"], cur["currency"]))
                         if "currency" in cur.columns else {})
        for t in wide["ticker"]:
            c = ccy_by_ticker.get(t)
            if not isinstance(c, str) or not c:
                continue
            rows.append({"ticker": t, "metric": "currency",
                         "value": np.nan, "label": c})
            rows.append({"ticker": t, "metric": "reports_usd",
                         "value": 1.0 if c == "USD" else 0.0, "label": None})
        return pd.DataFrame(rows)

    def selftest(self) -> None:
        _selftest()


# ---------------------------------------------------------------- helpers
def _year_before(asof: str) -> str:
    return (pd.Timestamp(asof) - pd.DateOffset(years=1)).strftime("%Y-%m-%d")


def _align(prior: pd.DataFrame, tickers: pd.Series) -> pd.DataFrame:
    p = prior.set_index("ticker").reindex(tickers.values)
    return p.reset_index().rename(columns={"index": "ticker"})


def _sector_for(tickers: pd.Series) -> pd.Series:
    """SIC-derived sector. Uses the fact store's own `sic` first -- it arrives
    with every filing, so it needs no separate scrape and is point-in-time."""
    try:
        import macro
        m = macro.load_sector_map()
        if not m.empty:
            return tickers.map(m.set_index("ticker")["sector"])
    except Exception:                                              # noqa: BLE001
        pass
    return pd.Series(np.nan, index=tickers.index)


def _price_inputs(asof: str, tickers: list[str], facts: pd.DataFrame) -> pd.DataFrame:
    """price, market cap, beta and 12-1 momentum as of `asof`."""
    start = (pd.Timestamp(asof) - pd.DateOffset(years=5)).strftime("%Y-%m-%d")
    px = store.read(interval="1d", start=start, end=asof, tickers=tickers,
                    columns=["ticker", "date", "close"])
    out = pd.DataFrame({"ticker": tickers})
    if px.empty:
        for c in ("price", "mktcap", "beta", "mom_12_1"):
            out[c] = np.nan
        return out

    px = px.sort_values(["ticker", "date"])
    last = px.groupby("ticker", observed=True)["close"].last()
    out["price"] = out["ticker"].map(last)

    # POINT-IN-TIME share count, from XBRL -- see the module docstring.
    # Delegated to fundamentals.share_count: this used to look for
    # `shares_diluted`/`shares_basic`, which facts_asof never emits under those
    # names (they come back `_ttm`-suffixed), so 549 of 2,873 names silently had
    # no market cap and therefore no valuation metrics at all.
    sh = facts.set_index("ticker")
    shares = FD.share_count(sh)
    out["mktcap"] = out["price"] * out["ticker"].map(shares)

    # 12-1 momentum: 12-month return excluding the most recent month, which is
    # the standard construction -- the excluded month is short-term reversal.
    piv = px.pivot_table(index="date", columns="ticker", values="close")
    if len(piv) > 252:
        mom = piv.iloc[-21] / piv.iloc[-252] - 1.0
        out["mom_12_1"] = out["ticker"].map(mom)
    else:
        out["mom_12_1"] = np.nan

    out["beta"] = _beta(piv, asof).reindex(out["ticker"]).values
    return out


def _beta(piv: pd.DataFrame, asof: str) -> pd.Series:
    """Weekly beta vs SPY over up to 5 years. Vectorised covariance ratio."""
    try:
        import macro
        etf = macro.read_etfs(end=asof)
        spy = etf[etf["ticker"] == "SPY"].set_index("date")["close"]
    except Exception:                                              # noqa: BLE001
        return pd.Series(dtype="float64")
    if spy.empty or piv.empty:
        return pd.Series(dtype="float64")

    wk = piv.resample("W", axis=0).last() if isinstance(piv.index, pd.DatetimeIndex) \
        else piv.iloc[::5]
    m = spy.reindex(piv.index).ffill()
    mw = m.iloc[::5] if not isinstance(piv.index, pd.DatetimeIndex) else m.resample("W").last()

    r = wk.pct_change()
    rm = mw.pct_change().reindex(r.index)
    var = rm.var()
    if not np.isfinite(var) or var == 0:
        return pd.Series(dtype="float64")
    cov = r.apply(lambda s: s.cov(rm))
    return (cov / var).clip(-3, 5)


MODULE = register(FundamentalModule())


def _selftest() -> None:
    declared = set(MODULE.metrics())
    for name in FM.REGISTRY:
        assert name in declared, f"metrics() omits registered metric {name!r}"
    for p in FM.PILLARS:
        assert f"{p}_score" in declared, f"metrics() omits {p}_score"
    assert "fund_score" in declared
    assert _year_before("2026-08-05") == "2025-08-05"
    FM.selftest(verbose=False)
    print(f"  [fundamental] {len(declared)} metrics, "
          f"{len(FM.REGISTRY)} raw + {len(FM.PILLARS)} pillars")
