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
        m += ["eps_diluted_ttm"]          # provider-overlaid; see compute()
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

        # A THIRD point-in-time query, one QUARTER back, for the sequential
        # growth metrics. Exactly the same move as the year-back frame above
        # and for the same reason: what was visible then, not a shift of what
        # is visible now.
        #
        # Cheap: this step's median is 39.5s against a 10,800s budget, and the
        # read cache in `fundamentals.read` serves a narrower window from the
        # wider one already loaded.
        prior_q = FD.facts_asof(_quarter_before(asof), tickers)
        if prior_q.empty:
            prior_q = cur.iloc[0:0]
        prior_q = _align(prior_q, cur["ticker"])

        # And a fourth, three years back, for the CAGR metrics.
        prior_3y = FD.facts_asof(_years_before(asof, 3), tickers)
        if prior_3y.empty:
            prior_3y = cur.iloc[0:0]
        prior_3y = _align(prior_3y, cur["ticker"])

        px = _price_inputs(asof, cur["ticker"].tolist(), cur)
        m = FM.compute(cur, prior, px, prior_q=prior_q, prior_3y=prior_3y)

        # Carry the price-derived inputs into the output. `FM.compute` consumes
        # them (mktcap feeds every valuation ratio) but does not pass them
        # through, so `mktcap`, `beta` and `mom_12_1` were DECLARED in metrics()
        # and never written -- 0 stored rows. That silently broke any size
        # analysis, since there was no market cap to bucket on.
        for _c in ("mktcap", "beta", "mom_12_1", "price"):
            if _c in px.columns and _c not in m.columns:
                m = m.merge(px[["ticker", _c]], on="ticker", how="left")

        # EPS IS A RATIO, SO IT COMES FROM THE PROVIDER LIKE THE OTHERS.
        #
        # Carried in from the facts frame purely so the overlay below has a
        # column to replace -- `overlay` only touches names present in BOTH
        # frames, and `eps_diluted_ttm` was in neither the registry nor here, so
        # it was the one displayed ratio the provider layer could never reach.
        #
        # Two filers make the case. CMP did not tag annual diluted EPS in its
        # FY2025 10-K at all (SEC's companyfacts has NetIncomeLoss for the
        # period and zero EarningsPerShareDiluted rows), so our EPS rolls to a
        # window ending 2025-06-30 beside a 2026-06-30 net income and reads
        # -2.90 against a reported +0.17. NCDL stopped tagging
        # EarningsPerShareDiluted after 2025-12-31 and its BDC replacement,
        # InvestmentCompanyInvestmentIncomeLossFromOperationsPerShare, is NET
        # INVESTMENT income per share -- 1.30 against 1.86 for the same FY2025 --
        # a different measure, so aliasing it would repeat the EE mistake.
        #
        # Neither is fixable by a refetch: the data is absent at the source. The
        # provider computes TTM EPS itself and has both.
        #
        # STRICTLY ADDITIVE: our SEC figure stays wherever the provider has
        # none, and no existing metric changes. `pe` is overlaid separately and
        # does not read this column.
        if "eps_diluted_ttm" in cur.columns and "eps_diluted_ttm" not in m.columns:
            m = m.merge(cur[["ticker", "eps_diluted_ttm"]], on="ticker",
                        how="left")

        # PROVIDER FIRST, FOR THE CURRENT SESSION ONLY.
        #
        # Yahoo publishes trailingPE, priceToBook, enterpriseToEbitda, margins,
        # returnOnEquity and the rest directly, computed from the same filings
        # and shown by Google. Deriving them ourselves from raw XBRL added no
        # accuracy and three separate bugs: `_ttm` preferring a stale annual
        # (94% of filers, median 181 days), weighted-average share counts summed
        # as flows (AAPL: -30,150,480,000 diluted shares), and a fact store
        # lagging SEC's own API by up to 925 days. So where the provider has the
        # number, the provider is the source; our calculation is the fallback
        # for everything it does not publish.
        #
        # ONLY ON THE LATEST SESSION. Yahoo knows today and nothing else -- it
        # cannot say what a P/E was in 2019. Overlaying it onto a historical
        # session would write 2026 knowledge into a 2019 row, which is exactly
        # the look-ahead the point-in-time design exists to prevent and would
        # silently invalidate every backtest. The guard is the asof comparison
        # below, not a comment.
        _cur = _is_current(asof)
        _on = getattr(config, "PROVIDER_OVERLAY", True)
        if not (_cur and _on):
            # SAY SO. A skipped overlay used to look exactly like a successful
            # one in the log, which is how this went unnoticed indefinitely.
            print(f"  [fundamental] provider overlay DECLINED on {asof} "
                  f"(current={_cur}, enabled={_on}) -- figures are our own",
                  flush=True)
        if _cur and _on:
            try:
                import providers
                m, tally = providers.overlay(m, verbose=False)
                if tally:
                    top = ", ".join(f"{k}={v}" for k, v in
                                    sorted(tally.items(), key=lambda kv: -kv[1])[:6])
                    print(f"  [fundamental] provider overlay on {asof}: {top}",
                          flush=True)
            except Exception as exc:                             # noqa: BLE001
                # A provider outage must not fail the run -- fall through to
                # our own figures, and SAY so rather than looking identical to
                # a successful overlay.
                print(f"  [fundamental] provider overlay unavailable "
                      f"({type(exc).__name__}); using our own figures",
                      flush=True)

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
def _is_current(asof: str) -> bool:
    """True only for the most recent session the market has closed.

    The provider overlay hangs off this. Anything older is HISTORY and must
    stay exactly as the filings knew it at the time -- see the overlay comment
    in `compute`. Written as a function so the rule is testable, and erring
    towards False so a calendar hiccup declines the overlay rather than
    contaminating a historical row.
    """
    # COMPARED AGAINST OUR OWN DATA FRONTIER, NOT A LIVE CLOCK.
    #
    # The clock version was `asof >= calendar_us.last_closed_session()`, and it
    # was False on EVERY run. The orchestrator fixes `asof` at 05:00, but the
    # fundamental step runs hours later -- 16h49m on 2026-08-20 -- and by then
    # `last_closed_session()` had rolled to the NEXT day. asof=2026-08-19 was
    # compared against 2026-08-20 and lost.
    #
    # It failed SILENTLY: the tally print only fires when the overlay actually
    # ran, so a declined overlay logged nothing. Measured 2026-08-21: zero
    # occurrences of "provider overlay on" in the entire run log. The provider
    # layer existed, passed its selftests, and had never once replaced a
    # displayed number.
    #
    # The newest session in the bar store is the right reference: it is what
    # this run actually ingested, it does not move while the run is in flight,
    # and it is correctly False for a BACKFILLED session -- which matters now
    # that `fundamental` backfills, because a 2026-07-16 row must never be
    # given today's provider snapshot.
    try:
        import store
        stored = store.stored_dates("1d")
        if not stored:
            return False
        return str(asof)[:10] >= max(stored)[:10]
    except Exception:                                            # noqa: BLE001
        return False


def _year_before(asof: str) -> str:
    return (pd.Timestamp(asof) - pd.DateOffset(years=1)).strftime("%Y-%m-%d")


def _years_before(asof: str, n: int) -> str:
    """`n` years back, for the compound-rate metrics."""
    return (pd.Timestamp(asof) - pd.DateOffset(years=n)).strftime("%Y-%m-%d")


def _quarter_before(asof: str) -> str:
    """One quarter back, for the sequential growth metrics.

    `DateOffset(months=3)` rather than 91 days so the anniversary lands on the
    same day of the month regardless of month length -- the fact store is
    keyed on period ends, and a drifting probe date would sometimes land a
    filing early or late for no reason connected to the company.
    """
    return (pd.Timestamp(asof) - pd.DateOffset(months=3)).strftime("%Y-%m-%d")


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
