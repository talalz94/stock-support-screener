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
        # Our own SEC EPS, kept BEFORE the overlay replaces it: the tie-breaker when
        # the provider's P/E and EPS disagree (see `_row_consistency`).
        _sec_eps = (pd.to_numeric(m["eps_diluted_ttm"], errors="coerce").copy()
                    if "eps_diluted_ttm" in m.columns else None)
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

        # MARKET CAP IS WITHHELD FOR A NON-USD FILER: nobody converts it.
        #
        # The provider reports it in the filer's own currency and every page
        # printed it with a dollar sign: TM 3.4e13 is JPY 34 trillion (~$230B),
        # not $34 trillion; FMX 6.7e11 is MXN 665B (~$36B). Our own fallback, USD
        # price x ordinary shares, is wrong by the ADR ratio instead (one TM ADR
        # is ten shares). Found by the 2026-10-11 audit across ~150 names.
        #
        # Its P/E and P/B are NOT withheld here: the provider's were checked to be
        # currency-consistent (price / pe / local EPS gives the real exchange
        # rate: EUR 1.10-1.24, CAD 0.70-0.73, GBP 1.36, DKK 0.155 on 8 names).
        # Display only -- no score reads mktcap except through metrics that are
        # already withheld for these filers.
        if "mktcap" in m.columns and "currency" in cur.columns:
            ccy = m["ticker"].map(dict(zip(cur["ticker"], cur["currency"])))
            foreign = ccy.notna() & (ccy.astype(str) != "USD")
            m.loc[foreign, "mktcap"] = np.nan

        # No row may contradict itself (P/E vs EPS, margin vs EPS) -- see
        # `_row_consistency`. After the overlay, before ranking.
        m, _cons = _row_consistency(m, _sec_eps)
        if any(_cons.values()):
            print(f"  [fundamental] row consistency on {asof}: "
                  + ", ".join(f"{k}={v}" for k, v in _cons.items()), flush=True)

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


def _row_consistency(m: pd.DataFrame, sec_eps: pd.Series | None = None) -> tuple[pd.DataFrame, dict]:
    """A DISPLAYED ROW MUST NOT CONTRADICT ITSELF.

    The provider overlay replaces our figures FIELD BY FIELD, so one row can carry
    Finnhub's EPS beside our own P/E: every field agrees with its own source and the
    row is still impossible. Found 2026-10-14 on session 10-12, 88 of 3,360 names:
      * 67 showed a POSITIVE P/E next to a LOSS EPS (WIX: EPS -3.54, P/E 84, which
        is our older SEC EPS of +0.88 -- the provider publishes no P/E on a loss, so
        ours stayed);
      * 37 showed a net margin whose sign contradicts the EPS (SOFI: Finnhub net
        margin -19.8% and op margin -19.9% beside Finnhub's own EPS of +0.47 -- its
        margin divides by a bank's fee income, not its revenue).
    The field-by-field audits all passed, because none compared a row with itself.

    A THIRD CASE, found by the nightly `rows` check on 10-13: 214 rows where the
    provider's OWN P/E and EPS disagree by more than 25%. Against our SEC EPS the
    provider's EPS was right in 10 of 12 checked (IBKR EPS 2.512 vs SEC 2.52, P/E
    131 implying 0.67 -- a split the P/E never absorbed; APH, BX, DVN alike), but for
    ADRs the P/E is right and the EPS is per ORDINARY share (HSBC 2.204 x 5 shares
    = 11.0 per ADR, matching its P/E; BP, PAM, PBR alike).

    Rules, applied after the overlay and before ranking:
      P/E      EPS <= 0 -> no P/E (a P/E on a loss is not a number anyone should
               read). EPS > 0 and the P/E implies an EPS more than 25% away: our
               SEC EPS breaks the tie -- whichever of the two is closer to it is
               kept and the other is recomputed from it (P/E = price / EPS, or
               EPS = price / P/E). With no SEC EPS to judge by (often an ADR), the
               P/E is kept and the EPS is WITHHELD, because a per-ordinary-share
               EPS beside an ADR price is the likelier error.
      margins  a net margin whose sign contradicts the EPS is WITHHELD, and the
               operating margin with it -- both share the revenue denominator that
               produced the contradiction. Blank, never zero.
    """
    out = m.copy()
    n = {"pe_blank_on_loss": 0, "pe_recomputed": 0, "eps_recomputed": 0,
         "eps_withheld": 0, "margins_withheld": 0}
    if "eps_diluted_ttm" not in out.columns:
        return out, n
    eps = pd.to_numeric(out["eps_diluted_ttm"], errors="coerce")
    sec = (pd.to_numeric(sec_eps, errors="coerce").reindex(out.index)
           if sec_eps is not None else pd.Series(np.nan, index=out.index))
    if "pe" in out.columns:
        pe = pd.to_numeric(out["pe"], errors="coerce")
        loss = (pe > 0) & (eps <= 0)
        out.loc[loss, "pe"] = np.nan
        n["pe_blank_on_loss"] = int(loss.sum())
        if "price" in out.columns:
            price = pd.to_numeric(out["price"], errors="coerce")
            implied = price / pe
            off = (pe > 0) & (eps > 0) & price.notna() & ((implied - eps).abs() > 0.25 * eps)
            judge = off & sec.notna() & (sec > 0)
            eps_wins = judge & ((eps - sec).abs() <= (implied - sec).abs())
            pe_wins = judge & ~eps_wins
            out.loc[eps_wins, "pe"] = (price / eps)[eps_wins]
            out.loc[pe_wins, "eps_diluted_ttm"] = implied[pe_wins]
            no_judge = off & ~judge
            out.loc[no_judge, "eps_diluted_ttm"] = np.nan
            n["pe_recomputed"] = int(eps_wins.sum())
            n["eps_recomputed"] = int(pe_wins.sum())
            n["eps_withheld"] = int(no_judge.sum())
            eps = pd.to_numeric(out["eps_diluted_ttm"], errors="coerce")
    if "net_margin" in out.columns:
        nm = pd.to_numeric(out["net_margin"], errors="coerce")
        bad = ((nm < 0) & (eps > 0)) | ((nm > 0) & (eps < 0))
        for c in ("net_margin", "op_margin"):
            if c in out.columns:
                out.loc[bad, c] = np.nan
        n["margins_withheld"] = int(bad.sum())
    return out, n


def _selftest() -> None:
    # Row consistency: each case below was a real row on 2026-10-12.
    t = pd.DataFrame({"ticker": ["WIX", "SOFI", "OK", "IBKR", "HSBC", "NOSEC"],
                      "eps_diluted_ttm": [-3.54, 0.47, 2.0, 2.512, 2.204, 2.0],
                      "pe": [84.28, 30.9, 10.0, 131.465, 8.503, 30.0],
                      "price": [74.17, 15.23, 20.0, 87.49, 94.85, 20.0],
                      "net_margin": [-0.08, -0.198, 0.10, 0.2, 0.2, 0.1],
                      "op_margin": [-0.10, -0.199, 0.12, 0.2, 0.2, 0.1]})
    sec = pd.Series([0.88, 0.49, 2.0, 2.52, 11.0, np.nan], index=t.index)
    got, cnt = _row_consistency(t, sec)
    g = got.set_index("ticker")
    assert pd.isna(g.at["WIX", "pe"]), "a positive P/E survived next to a loss EPS"
    assert abs(g.at["SOFI", "pe"] - 30.9) < 1e-9, "a consistent P/E was changed"
    assert pd.isna(g.at["SOFI", "net_margin"]) and pd.isna(g.at["SOFI", "op_margin"]), \
        "a margin contradicting the EPS survived"
    assert g.at["OK", "pe"] == 10.0 and g.at["OK", "net_margin"] == 0.10, "a clean row was touched"
    assert abs(g.at["IBKR", "pe"] - 87.49 / 2.512) < 1e-9, "IBKR: the EPS SEC agrees with must win"
    assert abs(g.at["HSBC", "eps_diluted_ttm"] - 94.85 / 8.503) < 1e-9, "HSBC: the P/E SEC agrees with must win"
    assert pd.isna(g.at["NOSEC", "eps_diluted_ttm"]) and g.at["NOSEC", "pe"] == 30.0, \
        "with no SEC EPS the P/E is kept and the EPS withheld"
    assert cnt == {"pe_blank_on_loss": 1, "pe_recomputed": 1, "eps_recomputed": 1,
                   "eps_withheld": 1, "margins_withheld": 1}, cnt

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
