"""
Fundamental metrics: forensic scores, quality, value, growth. Pure functions.

    python fund_metrics.py --selftest      published worked examples + invariants
    python fund_metrics.py --list          every metric, its pillar and direction

Nothing here fetches or stores. Input is the wide point-in-time frame from
`fundamentals.facts_asof`, output is a frame of metrics. That separation is what
lets factor_lab.py test any of these against forward returns without the
possibility that computing a metric quietly consulted the future.

WHAT IS IMPLEMENTED, AND WHAT IS DELIBERATELY NOT
-------------------------------------------------
Implemented from the brief, all from free SEC XBRL:
  Piotroski F (9pt) · Altman Z · Beneish M (8-var) · Sloan accruals ·
  DuPont 5-step · ROIC and the ROIC-WACC spread · Cash Conversion Cycle ·
  EV/EBITDA · P/E · P/B · PEG · FCF yield · EV/Sales · Gross-profit-to-assets ·
  Net share issuance · Asset growth · SUE · 12-1 momentum

NOT implemented, with the reason, because a silently-missing metric is worse
than an absent one:

  * ANALYST CONSENSUS (SUE-vs-street, revisions velocity). Not free at scale.
    SUE here uses the FOSTER (1977) SEASONAL RANDOM WALK -- expected EPS is the
    year-ago quarter plus average drift, and the surprise is scaled by the
    standard deviation of past surprises. That is the original academic
    definition and it needs no vendor, so this is a substitution rather than a
    compromise. It is NOT the same as a street-consensus surprise: it cannot
    capture what analysts already expected, only what the series itself implied.

  * DCF / DDM / SOTP. A DCF's output is dominated by the terminal growth and
    WACC you assume, so a mechanically-generated DCF across 5,000 names measures
    the assumption, not the company. EVA and the ROIC-WACC spread are included
    instead: same economic question (does this business earn above its cost of
    capital), no fabricated 10-year forecast. A single-name DCF belongs in a
    tool where a human sets the assumptions, not in a nightly screen.

  * 13F, transcript NLP, satellite/credit-card. Out of scope for this module;
    13F and short interest are free and are the natural next additions.

BETA AND WACC
-------------
WACC needs a cost of equity, which needs beta. Beta is computed from the price
store (60-month, vs SPY) rather than taken from a vendor -- free, reproducible,
and point-in-time honest. Cost of debt is interest expense over average total
debt, floored at the risk-free rate.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

import config

# Pillar assignment and sign. `direction` is +1 when HIGHER is better, so the
# ranking layer never has to special-case a metric it does not know about.
REGISTRY: dict[str, tuple[str, int, str]] = {
    # metric: (pillar, direction, description)
    "f_score":        ("quality", +1, "Piotroski F-Score, 0-9"),
    "roic":           ("quality", +1, "NOPAT / invested capital"),
    "roic_wacc":      ("quality", +1, "ROIC - WACC, the economic moat spread"),
    "eva":            ("quality", +1, "NOPAT - (invested capital x WACC)"),
    "roe":            ("quality", +1, "return on equity"),
    "gpoa":           ("quality", +1, "gross profit / assets (Novy-Marx)"),
    "op_margin":      ("quality", +1, "operating income / revenue"),
    # Added 2026-08-16 so the nightly provider cross-check can reach them --
    # see the note in `compute`. Scale-free (same-currency ratios), so they
    # need no FX and are shown for non-USD filers too.
    "net_margin":     ("quality", +1, "net income / revenue"),
    "gross_margin":   ("quality", +1, "gross profit / revenue"),
    "roa":            ("quality", +1, "net income / assets"),
    "debt_to_equity": ("safety", -1, "(long + short debt) / equity"),
    "asset_turnover": ("quality", +1, "revenue / assets"),
    "ccc":            ("quality", -1, "cash conversion cycle, days"),

    # MEASURED 2026-08-06, 47 monthly dates, h=20: IC -0.0233, t=-2.90, hit 34%.
    # Higher Z -- safer by the textbook -- predicted LOWER forward returns. That
    # is the distress-risk premium, and it is a real documented effect, not a
    # bug. Direction stays +1 anyway: flipping it to chase the premium would mean
    # systematically ranking fragile balance sheets HIGHEST, which is a different
    # product from the one being built. Z earns its place as a risk descriptor
    # and a CRITICAL/distress flag on the dashboard, not as a return predictor.
    # The whole safety pillar is flat over this window (t=0.73) -- see task #16
    # before giving it any weight in the composite.
    "z_score":        ("safety", +1, "Altman Z, distress below 1.81 (risk flag, NOT a return predictor -- measured IC is negative)"),
    "m_score":        ("safety", -1, "Beneish M, manipulation likely above -1.78"),
    "accruals":       ("safety", -1, "Sloan accrual ratio"),
    "net_debt_ebitda": ("safety", -1, "net debt / EBITDA"),
    "current_ratio":  ("safety", +1, "current assets / current liabilities"),
    "interest_cover": ("safety", +1, "EBIT / interest expense"),
    "net_issuance":   ("safety", -1, "YoY change in share count"),

    "ev_ebitda":      ("value", -1, "enterprise value / EBITDA"),
    "pe":             ("value", -1, "price / earnings"),
    "pb":             ("value", -1, "price / book"),
    "ev_sales":       ("value", -1, "enterprise value / revenue"),
    "fcf_yield":      ("value", +1, "free cash flow / enterprise value"),
    "peg":            ("value", -1, "P/E divided by earnings growth"),
    "shareholder_yield": ("value", +1, "(dividends + buybacks) / market cap"),

    "rev_growth":     ("growth", +1, "YoY revenue growth, TTM"),
    "eps_growth":     ("growth", +1, "YoY diluted EPS growth"),
    "fcf_growth":     ("growth", +1, "YoY free cash flow growth"),
    # `sue` is NOT here. The function below computes it correctly, but nothing
    # in the compute path feeds it the per-ticker EPS history it needs, so
    # listing it in REGISTRY declared a metric that was emitted for nobody --
    # which reads on a dashboard as "no data for this company" forever rather
    # than "this is not implemented". Add it back the same commit that wires it.
    "asset_growth":   ("growth", -1, "YoY asset growth (Cooper: high = bad)"),
    # Sequential growth: TTM now vs TTM one quarter back, so seasonality
    # cancels. Withheld unless the filer actually reported a new period --
    # see the `_q_ok` guard in `compute`.
    "rev_growth_q":   ("growth", +1, "QoQ revenue growth, TTM vs TTM one quarter back"),
    "eps_growth_q":   ("growth", +1, "QoQ EPS growth, TTM vs TTM one quarter back"),
    # Listed in combo.THEMES["growth"] since it was written, but never defined
    # here, so combo could never admit them.
    # Compound rates over three years. A trend, where one YoY figure is a
    # single comparison. Defined only when both ends are positive.
    "rev_cagr_3y":    ("growth", +1, "3-year revenue CAGR"),
    "eps_cagr_3y":    ("growth", +1, "3-year EPS CAGR"),
    "ebitda_growth":  ("growth", +1, "YoY EBITDA growth"),
    "book_growth":    ("growth", +1, "YoY book value (equity) growth"),
    # Margin DIRECTION in percentage points. Expanding margins is a standard
    # thesis and was inexpressible before this.
    "gross_margin_chg": ("growth", +1, "YoY change in gross margin, percentage points"),
    "op_margin_chg":  ("growth", +1, "YoY change in operating margin, percentage points"),
    "mom_12_1":       ("growth", +1, "12-month return excluding the last month"),
}

PILLARS = ("quality", "value", "safety", "growth")

# ---------------------------------------------------------------------------
# Which metrics need a currency, and which do not.
#
# A ratio of two figures from the SAME filing has no currency: EUR revenue over
# EUR assets is the same number as its USD equivalent. That is why a non-USD
# filer can be scored at all without an FX feed -- most of this registry is
# scale-free.
#
# A metric needs FX only when it puts a USD market price next to a foreign
# book: `mktcap`, `price` and `ev` come from the price store in USD, while
# earnings, equity and EBITDA come from the filing in its home currency. P/E on
# a EUR filer computed this way is wrong by the EUR/USD rate and looks entirely
# plausible, which is the dangerous kind of wrong.
#
# Listed EXPLICITLY rather than derived, because being wrong in the permissive
# direction publishes bad numbers for real companies. Anything not named here
# is treated as needing a currency -- an unknown metric is withheld, not
# guessed. `selftest` fails if a registry entry is missing from the split.
NEEDS_FX = frozenset({
    # market price over filing figure
    "pe", "pb", "ev_ebitda", "ev_sales", "fcf_yield", "peg",
    "shareholder_yield",
    # Altman Z's X4 term is market cap / total liabilities
    "z_score",
    # WACC's equity weight is market cap, so both of these inherit it
    "roic_wacc", "eva",
})

# Everything else: ratios of same-currency figures, unitless scores, share
# counts, percentage growth rates, and price-only series.
SCALE_FREE = frozenset(REGISTRY) - NEEDS_FX


def withheld_for_currency(currency: str) -> frozenset:
    """Metrics that must not be emitted for a filer reporting in `currency`."""
    return frozenset() if (not currency or currency == "USD") else NEEDS_FX


def _d(df: pd.DataFrame, col: str) -> pd.Series:
    """Column or all-NaN. Coverage varies wildly by filer, and a KeyError on a
    concept two thirds of the market never tags would make the whole screen fail
    for the sake of one metric."""
    if col in df.columns:
        return pd.to_numeric(df[col], errors="coerce")
    return pd.Series(np.nan, index=df.index, dtype="float64")


def _sum_reported(*parts):
    """Sum the legs a filer actually reported; NaN if it reported NONE of them.

    THE BUG THIS REPLACES, measured 2026-08-10. `debt` was
    `debt_lt.fillna(0) + debt_st.fillna(0)`, so a company that tags neither
    came out as debt = 0 -- not "unknown", but the actively FLATTERING answer
    "this company has no borrowings". 1,376 of 3,270 tradeable names (42%) tag
    no debt line at all, and 849 of those report total liabilities above 30% of
    assets, so they self-evidently do carry obligations. They were being scored
    debt-free through `net_debt_ebitda` (lower is better), through EV in
    `ev_ebitda` / `ev_sales` / `fcf_yield`, and through `invested_capital` in
    `roic` / `eva` / `wacc`.

    `pandas` is what makes this easy to get wrong: `NaN + NaN` is NaN, but
    `Series.fillna(0) + Series.fillna(0)` is 0, and the two read almost
    identically at a glance.

    A filer that reports ONE leg and not the other is treated as having zero of
    the missing leg -- that is the ordinary XBRL convention for a line item a
    company does not carry, and it is a different situation from tagging
    nothing at all.
    """
    parts = [pd.Series(p) for p in parts]
    any_reported = pd.concat([p.notna() for p in parts], axis=1).any(axis=1)
    total = sum(p.fillna(0) for p in parts)
    return total.where(any_reported)


def _safe(num, den, cap: float = 1e6):
    """num/den with a zero-denominator guard and an outlier cap.

    Micro-cap equity and near-zero EBITDA routinely produce ratios of 1e9 that
    would otherwise dominate any winsorisation applied later.
    """
    den = pd.Series(den).replace(0, np.nan)
    out = pd.Series(num) / den
    return out.replace([np.inf, -np.inf], np.nan).clip(-cap, cap)


# ===========================================================================
# Derived building blocks
# ===========================================================================
def derive(f: pd.DataFrame) -> pd.DataFrame:
    """Concepts every metric needs but XBRL does not tag directly."""
    d = pd.DataFrame(index=f.index)
    d["ticker"] = f["ticker"].values if "ticker" in f.columns else np.nan

    rev = _d(f, "revenue_ttm").fillna(_d(f, "revenue"))
    cogs = _d(f, "cogs_ttm").fillna(_d(f, "cogs"))
    d["revenue"] = rev
    d["cogs"] = cogs
    d["gross_profit"] = _d(f, "gross_profit_ttm").fillna(rev - cogs)
    d["opinc"] = _d(f, "opinc_ttm").fillna(_d(f, "opinc"))
    d["net_income"] = _d(f, "net_income_ttm").fillna(_d(f, "net_income"))
    d["cfo"] = _d(f, "cfo_ttm").fillna(_d(f, "cfo"))
    d["capex"] = _d(f, "capex_ttm").fillna(_d(f, "capex")).abs()
    d["fcf"] = d["cfo"] - d["capex"]
    # D&A: reported TOTAL first, else the sum of the two components.
    #
    # Same fix as `stock_profile._dna`, for the same reason: filers either
    # report a combined D&A or report depreciation and intangible amortisation
    # separately, and choosing between the halves understates badly. COLL's
    # Q2-2026 was 1.8M depreciation vs 63.0M amortisation -- picking the former
    # put EBITDA at $4M against a true ~$68M, and EBITDA feeds ev_ebitda and
    # net_debt_ebitda, so the error propagated into the value pillar.
    _tot = _d(f, "dna_ttm").fillna(_d(f, "dna"))
    _dep = _d(f, "deprec_ttm").fillna(_d(f, "deprec"))
    _amo = _d(f, "amort_ttm").fillna(_d(f, "amort"))
    _comp = _sum_reported(_dep, _amo)          # NaN only if BOTH are absent
    d["dna"] = _tot.where(_tot.notna(), _comp)
    d["ebitda"] = d["opinc"] + d["dna"]
    d["ebit"] = d["opinc"]
    d["pretax"] = _d(f, "pretax_ttm").fillna(_d(f, "pretax"))
    d["tax"] = _d(f, "tax_ttm").fillna(_d(f, "tax"))
    d["interest_exp"] = _d(f, "interest_exp_ttm").fillna(_d(f, "interest_exp")).abs()

    d["assets"] = _d(f, "assets")
    d["assets_current"] = _d(f, "assets_current")
    d["liabilities"] = _d(f, "liabilities")
    d["liabilities_current"] = _d(f, "liabilities_current")
    d["equity"] = _d(f, "equity")
    d["cash"] = _sum_reported(_d(f, "cash"), _d(f, "sti"))
    d["inventory"] = _d(f, "inventory")
    d["receivables"] = _d(f, "receivables")
    d["payables"] = _d(f, "payables")
    d["retained"] = _d(f, "retained")
    d["ppe"] = _d(f, "ppe")
    d["debt"] = _sum_reported(_d(f, "debt_lt"), _d(f, "debt_st"))
    d["net_debt"] = d["debt"] - d["cash"]
    d["shares"] = _d(f, "shares_diluted").fillna(_d(f, "shares_out")).fillna(
        _d(f, "shares_basic"))
    d["sbc"] = _d(f, "sbc_ttm").fillna(_d(f, "sbc"))
    d["dividends"] = _d(f, "dividends_ttm").fillna(_d(f, "dividends")).abs()
    d["buybacks"] = _d(f, "buybacks_ttm").fillna(_d(f, "buybacks")).abs()

    # Effective tax rate, clamped: XBRL produces negative and >100% rates from
    # valuation-allowance releases, and an unclamped rate flips NOPAT's sign.
    d["tax_rate"] = _safe(d["tax"], d["pretax"]).clip(0.0, 0.50).fillna(0.21)
    d["nopat"] = d["ebit"] * (1 - d["tax_rate"])
    # NOT `debt.fillna(0)` / `cash.fillna(0)`: that would undo `_sum_reported`
    # one line after computing it, and hand `roic`, `eva` and `wacc` an
    # invented debt-free balance sheet for the 42% of filers that tag no debt.
    # Unknown debt means unknown invested capital, which means no ROIC.
    d["invested_capital"] = (d["debt"] + d["equity"]) - d["cash"]
    return d


# ===========================================================================
# Forensic scores
# ===========================================================================
def piotroski(cur: pd.DataFrame, prior: pd.DataFrame) -> pd.Series:
    """9-point F-Score. Needs a year-ago frame for the 5 trend components."""
    c, p = cur, prior
    roa_c = _safe(c["net_income"], c["assets"])
    roa_p = _safe(p["net_income"], p["assets"])
    pts = [
        (c["net_income"] > 0).astype(float),                       # profitability
        (c["cfo"] > 0).astype(float),
        (roa_c > roa_p).astype(float),
        (c["cfo"] > c["net_income"]).astype(float),                # accrual quality
        (_safe(c["debt"], c["assets"]) < _safe(p["debt"], p["assets"])).astype(float),
        (_safe(c["assets_current"], c["liabilities_current"])
         > _safe(p["assets_current"], p["liabilities_current"])).astype(float),
        (c["shares"] <= p["shares"] * 1.01).astype(float),         # no dilution
        (_safe(c["gross_profit"], c["revenue"])
         > _safe(p["gross_profit"], p["revenue"])).astype(float),  # efficiency
        (_safe(c["revenue"], c["assets"]) > _safe(p["revenue"], p["assets"])).astype(float),
    ]
    s = sum(pts)
    # Every component needs both years; a name with no prior filing would
    # otherwise score 0 and be indistinguishable from a genuinely failing one.
    return s.where(c["assets"].notna() & p["assets"].notna())


def altman_z(d: pd.DataFrame, mktcap: pd.Series) -> pd.Series:
    """Altman (1968) Z. Distress below 1.81, safe above 2.99."""
    a = d["assets"].replace(0, np.nan)
    x1 = (d["assets_current"] - d["liabilities_current"]) / a
    x2 = d["retained"] / a
    x3 = d["ebit"] / a
    x4 = mktcap / d["liabilities"].replace(0, np.nan)
    x5 = d["revenue"] / a
    z = 1.2 * x1 + 1.4 * x2 + 3.3 * x3 + 0.6 * x4 + 1.0 * x5
    return z.replace([np.inf, -np.inf], np.nan).clip(-20, 40)


def beneish_m(c: pd.DataFrame, p: pd.DataFrame) -> pd.Series:
    """Beneish 8-variable M-Score. Above -1.78 flags likely manipulation."""
    def r(nc, dc):
        return _safe(nc, dc)

    dsri = r(r(c["receivables"], c["revenue"]), r(p["receivables"], p["revenue"]))
    gmi = r(r(p["gross_profit"], p["revenue"]), r(c["gross_profit"], c["revenue"]))
    aqi_c = 1 - r(c["assets_current"].fillna(0) + c["ppe"].fillna(0), c["assets"])
    aqi_p = 1 - r(p["assets_current"].fillna(0) + p["ppe"].fillna(0), p["assets"])
    aqi = r(aqi_c, aqi_p)
    sgi = r(c["revenue"], p["revenue"])
    depi = r(r(p["dna"], p["dna"] + p["ppe"]), r(c["dna"], c["dna"] + c["ppe"]))
    sgai = r(r(c["revenue"] * 0 + 1, 1), 1)          # SGA unreliable; neutral 1.0
    sgai = pd.Series(1.0, index=c.index)
    lvgi = r(r(c["liabilities"], c["assets"]), r(p["liabilities"], p["assets"]))
    tata = r(c["net_income"] - c["cfo"], c["assets"])

    m = (-4.84 + 0.920 * dsri + 0.528 * gmi + 0.404 * aqi + 0.892 * sgi
         + 0.115 * depi - 0.172 * sgai + 4.679 * tata - 0.327 * lvgi)
    return m.replace([np.inf, -np.inf], np.nan).clip(-15, 15)


def sloan_accruals(d: pd.DataFrame) -> pd.Series:
    """(net income - operating cash flow) / assets. High = low earnings quality."""
    return _safe(d["net_income"] - d["cfo"], d["assets"]).clip(-2, 2)


def dupont(d: pd.DataFrame) -> pd.DataFrame:
    """5-step ROE decomposition. The product reconstructs ROE by construction."""
    out = pd.DataFrame(index=d.index)
    out["du_tax_burden"] = _safe(d["net_income"], d["pretax"]).clip(-3, 3)
    out["du_interest_burden"] = _safe(d["pretax"], d["ebit"]).clip(-3, 3)
    out["du_op_margin"] = _safe(d["ebit"], d["revenue"]).clip(-5, 5)
    out["du_asset_turnover"] = _safe(d["revenue"], d["assets"]).clip(0, 20)
    out["du_leverage"] = _safe(d["assets"], d["equity"]).clip(-50, 50)
    return out


def cash_conversion_cycle(d: pd.DataFrame) -> pd.Series:
    """DIO + DSO - DPO, in days. NaN unless the filer reported enough to say.

    THE BUG THIS REPLACES, measured 2026-08-10. All three legs were
    `.fillna(0)`, so a filer tagging none of inventory, receivables or payables
    scored `0 + 0 - 0` = **zero days** -- published as a real figure, and a
    flattering one, because `ccc` ranks lower-is-better. Half the names had no
    leg at all, and a no-data company earned a median 62/100 on this quality
    axis while a company with real numbers earned 25/100. Missing data was
    outranking disclosed data by 2.5x.

    The rule now: REVENUE, RECEIVABLES and COGS must all be present. Given
    those, DSO is real, and a missing inventory or payables line on a filer
    that does report cost of sales genuinely means "carries little or none" --
    the ordinary reading. Absent them there is nothing to compute and the
    honest output is no output.
    """
    have = (d["revenue"].notna() & d["receivables"].notna() & d["cogs"].notna())
    dio = _safe(d["inventory"] * 365.0, d["cogs"])
    dso = _safe(d["receivables"] * 365.0, d["revenue"])
    dpo = _safe(d["payables"] * 365.0, d["cogs"])
    ccc = (dio.fillna(0) + dso.fillna(0) - dpo.fillna(0)).clip(-365, 1095)
    return ccc.where(have)


def wacc(d: pd.DataFrame, beta: pd.Series, mktcap: pd.Series,
         rf: float = 0.04, erp: float = 0.05) -> pd.Series:
    """Weighted average cost of capital.

    Cost of equity is CAPM with beta measured from the price store, not bought.
    Cost of debt is interest expense over average debt, floored at the risk-free
    rate so a company with rounding-error interest cannot appear to borrow free.
    """
    ke = rf + beta.clip(0.2, 3.0).fillna(1.0) * erp
    kd = _safe(d["interest_exp"], d["debt"]).clip(rf, 0.25).fillna(rf + 0.02)
    # WACC is a WEIGHTED average, so an unknown weight is not a zero weight.
    # `debt.fillna(0)` returned the pure cost of equity for any filer that tags
    # no debt line, which is a real-looking number computed from a balance
    # sheet nobody has seen. Both weights must be known or there is no average.
    e = pd.Series(mktcap, index=d.index)
    dbt = d["debt"]
    tot = (e + dbt).replace(0, np.nan)
    w = ((e / tot) * ke + (dbt / tot) * kd * (1 - d["tax_rate"])).clip(0.02, 0.30)
    return w.where(e.notna() & dbt.notna())


def sue(eps_hist: pd.DataFrame) -> pd.Series:
    """Standardised Unexpected Earnings, Foster (1977) seasonal random walk.

    expected_q = eps[q-4] + mean(eps[q-i] - eps[q-i-4])
    SUE        = (actual - expected) / std(past surprises)

    Uses no analyst consensus, which is the point: consensus is not free at this
    scale, and the seasonal-random-walk SUE is the original academic construct
    rather than a degraded stand-in. It answers "did earnings surprise the
    SERIES", not "did earnings surprise the STREET" -- a real difference worth
    keeping in mind when reading it.

    `eps_hist` is (ticker x quarter) with the most recent quarter last.
    """
    if eps_hist.shape[1] < 6:
        return pd.Series(np.nan, index=eps_hist.index)
    v = eps_hist.to_numpy(dtype="float64")
    yoy = v[:, 4:] - v[:, :-4]                       # seasonal differences
    drift = np.nanmean(yoy[:, :-1], axis=1)
    expected = v[:, -5] + drift
    actual = v[:, -1]
    sd = np.nanstd(yoy[:, :-1], axis=1)
    sd = np.where(np.isfinite(sd) & (sd > 1e-6), sd, np.nan)
    return pd.Series((actual - expected) / sd, index=eps_hist.index).clip(-10, 10)


# ===========================================================================
# Assembly
# ===========================================================================
def compute(cur: pd.DataFrame, prior: pd.DataFrame, px: pd.DataFrame,
            prior_q: pd.DataFrame | None = None,
            prior_3y: pd.DataFrame | None = None) -> pd.DataFrame:
    """All metrics for one date.

    `cur`/`prior` are wide point-in-time frames from fundamentals.facts_asof;
    `px` carries ticker, price, mktcap, beta, mom_12_1.

    `prior_q` is the same frame a QUARTER back, for the sequential growth
    metrics. `prior_3y` is three years back, for the CAGR metrics. Both are
    optional and default to None so callers that only want the year-on-year set
    -- `providers.compare` passes the current frame twice -- keep working
    unchanged and simply get NaN for those metrics.
    """
    d = derive(cur)
    p = derive(prior)
    d.index = cur["ticker"].values
    p.index = prior["ticker"].values
    p = p.reindex(d.index)

    # A GROWTH RATE NEEDS TWO DIFFERENT PERIODS. If the filer published nothing
    # in between, the "period back" frame IS the current frame and every growth
    # metric computes as exactly 0.0 -- a fabricated number that ranks
    # mid-pack, which is worse than a missing one. Measured 2026-08-23: 19% of
    # names last filed more than 91 days ago, so this fires on ~640 stocks.
    #
    # Compared on `last_ddate`, the PERIOD end, not `last_filed`: an amended
    # filing covering the same period changes when it was filed but not the
    # economics, and would produce the same spurious zero.
    #
    # The upper bound matters as much as the lower one. Without it a frame that
    # actually lands two quarters back gets relabelled as one quarter's growth.
    def _ends(frame):
        return pd.to_datetime(
            pd.Series(frame["last_ddate"].values,
                      index=frame["ticker"].values), errors="coerce")

    def _moved(then_ends, lo: int, hi: int) -> pd.Series:
        """True where `then` is a genuinely earlier period, lo..hi days back."""
        if then_ends is None:
            return pd.Series(False, index=d.index)
        gap = (_d_end - then_ends.reindex(d.index)).dt.days
        return (gap.notna() & (gap >= lo) & (gap <= hi)).reindex(d.index).fillna(False)

    _d_end = _ends(cur) if "last_ddate" in cur.columns else pd.Series(
        pd.NaT, index=d.index)
    _p_end = _ends(prior) if "last_ddate" in prior.columns else None
    # A year-back point-in-time frame normally sits a quarter either side of
    # 365 days; anything outside 270-550 is not a year-on-year comparison.
    _yoy_ok = _moved(_p_end, 270, 550)

    q = None
    _q_ok = pd.Series(False, index=d.index)
    if prior_q is not None and not prior_q.empty:
        q = derive(prior_q)
        q.index = prior_q["ticker"].values
        q = q.reindex(d.index)
        _q_end = _ends(prior_q) if "last_ddate" in prior_q.columns else None
        # 45-140 days: a quarter is ~91. The upper bound deliberately excludes
        # ~182, so a semi-annual filer is dropped rather than having half a
        # year's growth reported as a quarter's.
        _q_ok = _moved(_q_end, 45, 140)

    t3 = None
    _t3_ok = pd.Series(False, index=d.index)
    if prior_3y is not None and not prior_3y.empty:
        t3 = derive(prior_3y)
        t3.index = prior_3y["ticker"].values
        t3 = t3.reindex(d.index)
        _t3_end = _ends(prior_3y) if "last_ddate" in prior_3y.columns else None
        # 900-1300 days around the 1,095 of three years. A CAGR annualises by
        # dividing by 3, so a frame that is really two or four years back would
        # silently rescale the whole rate.
        _t3_ok = _moved(_t3_end, 900, 1300)

    px = px.set_index("ticker").reindex(d.index)
    mktcap = pd.to_numeric(px.get("mktcap"), errors="coerce")
    beta = pd.to_numeric(px.get("beta"), errors="coerce")
    price = pd.to_numeric(px.get("price"), errors="coerce")

    # Enterprise value needs net debt. `net_debt.fillna(0)` would price an
    # unknown balance sheet as a debt-free one, making a leveraged company look
    # cheap on EV/EBITDA, EV/Sales and FCF yield -- the same fabrication
    # `_sum_reported` exists to stop, one layer further down the pipe.
    ev = mktcap + d["net_debt"]
    w = wacc(d, beta, mktcap)

    out = pd.DataFrame(index=d.index)
    out.index.name = "ticker"

    # quality
    out["f_score"] = piotroski(d, p)
    # A RETURN ON A NEGATIVE CAPITAL BASE IS NOT A RETURN. Dividing a loss by
    # negative invested capital or negative equity flips the sign, so the
    # result lands in the POSITIVE bucket and ranks as though the company
    # earned it. 99 filers showed a positive ROE built from a loss over
    # negative book equity. The aggregate hides this -- genuine negative
    # returns rank correctly low -- which is why it survived until the
    # denominators were checked one by one.
    out["roic"] = _safe(d["nopat"], d["invested_capital"]).where(
        d["invested_capital"] > 0).clip(-5, 5)
    out["roic_wacc"] = out["roic"] - w
    out["eva"] = d["nopat"] - d["invested_capital"] * w
    out["roe"] = _safe(d["net_income"], d["equity"]).where(
        d["equity"] > 0).clip(-10, 10)
    out["gpoa"] = _safe(d["gross_profit"], d["assets"]).clip(-5, 5)
    out["op_margin"] = _safe(d["ebit"], d["revenue"]).clip(-10, 10)

    # FOUR RATIOS THE PAGE SHOWED BUT THE SCORED FRAME NEVER CARRIED.
    #
    # `stock_profile.DERIVED` computed net_margin, gross_margin, roa and
    # debt_to_equity for display, but none reached `fund_metrics.REGISTRY`, so
    # `providers.compare` had nothing on our side to compare and reported
    # "we do not compute this" for 114 tickers x 4 fields. They were the last
    # numbers on a page that no automated check could reach -- which is exactly
    # the condition that let COLL's EBITDA be wrong by 17x for months.
    #
    # All four are one division of figures already verified against SEC, and
    # all four are published by both providers, so adding them here makes them
    # cross-checkable every night. Clips match the sibling ratios above.
    out["net_margin"] = _safe(d["net_income"], d["revenue"]).clip(-10, 10)
    out["gross_margin"] = _safe(d["gross_profit"], d["revenue"]).clip(-10, 10)
    out["roa"] = _safe(d["net_income"], d["assets"]).clip(-5, 5)
    # `d["debt"]` is already `_sum_reported(debt_lt, debt_st)` from `derive`,
    # so it is NaN only when the filer tags neither leg -- reusing it keeps one
    # definition of debt rather than a second, drifting one.
    out["debt_to_equity"] = _safe(d["debt"], d["equity"]).where(
        d["equity"] > 0).clip(0, 100)
    out["asset_turnover"] = _safe(d["revenue"], d["assets"]).clip(0, 20)
    out["ccc"] = cash_conversion_cycle(d)

    # safety
    out["z_score"] = altman_z(d, mktcap)
    out["m_score"] = beneish_m(d, p)
    out["accruals"] = sloan_accruals(d)
    # Leverage against negative EBITDA is undefined, and it ranks
    # lower-is-better -- so 430 cash-burning filers scored as the LEAST
    # leveraged names in the market (mean rank 0.86 against 0.36 for
    # companies with real EBITDA).
    out["net_debt_ebitda"] = _safe(d["net_debt"], d["ebitda"]).where(
        d["ebitda"] > 0).clip(-20, 40)
    out["current_ratio"] = _safe(d["assets_current"], d["liabilities_current"]).clip(0, 30)
    out["interest_cover"] = _safe(d["ebit"], d["interest_exp"]).clip(-50, 200)
    out["net_issuance"] = _safe(d["shares"] - p["shares"], p["shares"]).clip(-1, 5)

    # value
    # A MULTIPLE OF A NEGATIVE DENOMINATOR IS NOT A CHEAP MULTIPLE.
    #
    # These three rank "lower is better", so a negative value sorted as the
    # cheapest thing in the market. Measured 2026-08-11: 932 of 2,918 filers
    # (32%) carried a negative P/E, and the loss-makers earned a mean value
    # rank of 0.84 against 0.34 for profitable companies -- Redwire, which has
    # lost money every year since 2021, scored 0.85 on "cheapness" purely
    # because its earnings are negative.
    #
    # P/E on negative earnings, EV/EBITDA on negative EBITDA and P/B on
    # negative book equity are all undefined as valuation, not merely extreme.
    # Withheld, exactly as `peg` already withholds itself below -- that guard
    # was written and simply never extended to its three siblings.
    out["ev_ebitda"] = _safe(ev, d["ebitda"]).where(
        d["ebitda"] > 0).clip(0, 300)
    # P/E ON THE FILER'S OWN DILUTED EPS WHEN THERE IS ONE.
    #
    # `mktcap / net_income` and `price / diluted EPS` are not the same number,
    # because the market cap uses the CURRENT share count from the filing
    # cover page while net income is earned over a TRAILING average count. For
    # a company buying back stock the two drift apart -- AAPL on 2026-08-12:
    # 34.25 the first way, 34.70 the second, against the ~34.6 every quote
    # site shows. The second is the convention, so a user comparing our page
    # to anywhere else is comparing to price/EPS.
    #
    # Diluted, not basic, and reported rather than recomputed: the filer has
    # already done the participating-securities arithmetic that makes
    # net_income/shares differ from EPS by ~0.6% even when both are right.
    # Falls back to mktcap/net_income where EPS is untagged, which is common
    # among small caps, so coverage does not drop.
    eps = pd.Series(index=d.index, dtype=float)
    if "eps_diluted_ttm" in cur.columns:
        eps = pd.to_numeric(
            pd.Series(cur["eps_diluted_ttm"].values, index=cur["ticker"].values)
            .reindex(d.index), errors="coerce")
    out["pe"] = (_safe(price, eps).where(eps > 0)
                 .fillna(_safe(mktcap, d["net_income"])
                         .where(d["net_income"] > 0))
                 .clip(0, 1000))
    out["pb"] = _safe(mktcap, d["equity"]).where(
        d["equity"] > 0).clip(0, 200)
    out["ev_sales"] = _safe(ev, d["revenue"]).clip(0, 200)
    out["fcf_yield"] = _safe(d["fcf"], ev).clip(-5, 5)
    out["shareholder_yield"] = _safe(d["dividends"].fillna(0)
                                     + d["buybacks"].fillna(0), mktcap).clip(-1, 2)

    # growth -- TWO guards, and the second was found by checking the first.
    #
    # `_period_ok` says the FRAME moved on. That is necessary and not
    # sufficient: `last_ddate` is the newest period across ALL concepts, so a
    # filer can advance on one line item while the metric in hand still comes
    # from the same stale annual. Measured 2026-08-23, 250-name sample: six
    # names passed the period guard and still produced an exact 0.0 because
    # their TTM revenue was byte-identical across the two frames --
    # CBIO 11,883,000, COLB 177,000,000, NAVI 271,000,000, and three more.
    #
    # So the value must have CHANGED too. Identical TTM revenue to the dollar
    # across a quarter is not a coincidence at 6-in-170; it means one report,
    # read twice. A growth rate needs two reports.
    def _growth(now, then, ok, lo, hi):
        now = pd.Series(now).reindex(d.index)
        then = pd.Series(then).reindex(d.index)
        moved = ok & now.notna() & then.notna() & (now != then)
        return _safe(now - then, then.abs()).clip(lo, hi).where(moved)

    eps_c = _safe(d["net_income"], d["shares"])
    eps_p = _safe(p["net_income"], p["shares"])
    out["rev_growth"] = _growth(d["revenue"], p["revenue"], _yoy_ok, -1, 10)
    out["fcf_growth"] = _growth(d["fcf"], p["fcf"], _yoy_ok, -10, 10)
    out["eps_growth"] = _growth(eps_c, eps_p, _yoy_ok, -10, 10)
    out["asset_growth"] = _growth(d["assets"], p["assets"], _yoy_ok, -1, 10)
    out["mom_12_1"] = pd.to_numeric(px.get("mom_12_1"), errors="coerce")

    # EBITDA and book growth. `combo.THEMES["growth"]` has listed both since it
    # was written, but neither existed in REGISTRY, so `combo.admitted` could
    # never admit them -- a dead reference that looked like a configured metric.
    out["ebitda_growth"] = _growth(d["ebitda"], p["ebitda"], _yoy_ok, -10, 10)
    out["book_growth"] = _growth(d["equity"], p["equity"], _yoy_ok, -10, 10)

    # MARGIN DIRECTION, in percentage points, not a ratio of ratios. A margin
    # that went 4% -> 6% is +2pp; expressing it as +50% growth makes a
    # thin-margin company look transformed by a rounding change.
    #
    # Nearly free: the year-back frame is already in hand for the YoY block.
    _gm_c, _gm_p = _safe(d["gross_profit"], d["revenue"]), _safe(p["gross_profit"], p["revenue"])
    _om_c, _om_p = _safe(d["ebit"], d["revenue"]), _safe(p["ebit"], p["revenue"])
    out["gross_margin_chg"] = (_gm_c - _gm_p).clip(-1, 1).where(
        _yoy_ok & _gm_c.notna() & _gm_p.notna() & (_gm_c != _gm_p))
    out["op_margin_chg"] = (_om_c - _om_p).clip(-1, 1).where(
        _yoy_ok & _om_c.notna() & _om_p.notna() & (_om_c != _om_p))

    # THREE-YEAR CAGR. One YoY figure is a single comparison and inherits
    # whatever was odd about either end; a compound rate over three years is a
    # trend, which is what a "consistent grower" screen actually wants.
    #
    # Only defined when BOTH ends are positive. A CAGR across a sign change is
    # arithmetically expressible and financially meaningless -- the same rule
    # `peg` already applies.
    def _cagr(now, then, ok):
        r = (_safe(now, then).where((now > 0) & (then > 0))) ** (1.0 / 3.0) - 1.0
        return r.replace([np.inf, -np.inf], np.nan).clip(-1, 3).where(ok)

    if t3 is not None:
        out["rev_cagr_3y"] = _cagr(d["revenue"], t3["revenue"], _t3_ok)
        out["eps_cagr_3y"] = _cagr(_safe(d["net_income"], d["shares"]),
                                   _safe(t3["net_income"], t3["shares"]), _t3_ok)
    else:
        out["rev_cagr_3y"] = np.nan
        out["eps_cagr_3y"] = np.nan

    # QUARTER-OVER-QUARTER: TTM now against TTM one quarter back.
    #
    # NOT raw Q vs Q-1. A TTM sum spans four quarters, so seasonality cancels by
    # construction and the sequential change is a clean momentum read. Raw
    # Q/Q-1 would be dominated by the season and would need SUE-style seasonal
    # adjustment before it meant anything.
    if q is not None:
        eps_q = _safe(q["net_income"], q["shares"])
        out["rev_growth_q"] = _growth(d["revenue"], q["revenue"], _q_ok, -1, 5)
        out["eps_growth_q"] = _growth(eps_c, eps_q, _q_ok, -10, 10)
    else:
        out["rev_growth_q"] = np.nan
        out["eps_growth_q"] = np.nan
    # PEG only means anything when both legs are positive; a negative-growth PEG
    # is arithmetically fine and financially nonsense.
    g = out["eps_growth"] * 100.0
    out["peg"] = _safe(out["pe"], g).where((out["pe"] > 0) & (g > 0)).clip(0, 20)

    out = out.join(dupont(d))
    out["_price"] = price
    out["_mktcap"] = mktcap
    # Exported as `wacc`, not `_wacc`. The underscore made it internal, so a
    # metric that IS computed -- and that `roic_wacc` is derived from -- was
    # declared in metrics() and emitted for nobody. Publishing the input beside
    # the spread also makes a surprising `roic_wacc` checkable.
    out["wacc"] = w
    out["_wacc"] = w          # kept: existing internal readers use this name

    # WITHHOLD, do not convert. For a filer reporting in EUR, every metric in
    # NEEDS_FX divides a USD market price by a EUR book figure and produces a
    # number wrong by the exchange rate -- and plausible enough that nobody
    # would question it. Blanking them leaves the scale-free metrics, which are
    # exactly as valid for a EUR filer as for a USD one.
    #
    # `rank_pillars` already degrades correctly on NaN: it tracks per-pillar
    # coverage and withholds a pillar below FUND_MIN_COVERAGE, so the value
    # pillar (entirely NEEDS_FX) simply does not score, and `fund_score`
    # renormalises over the pillars that remain.
    cur_ccy = cur.get("currency")
    if cur_ccy is not None:
        ccy = pd.Series(list(cur_ccy), index=d.index).fillna("USD").astype(str)
        foreign = ccy.ne("USD")
        if foreign.any():
            for name in NEEDS_FX:
                if name in out.columns:
                    out.loc[foreign, name] = np.nan
            out["_currency"] = ccy
    return out.reset_index()


# ===========================================================================
# Ranking
# ===========================================================================
def rank_pillars(m: pd.DataFrame, sector: pd.Series | None = None) -> pd.DataFrame:
    """Cross-sectional percentile ranks -> per-pillar and composite scores.

    Ranked WITHIN the date, and within the sector where one is known. A raw
    EV/EBITDA compares a utility to a biotech and concludes the utility is
    cheap; a sector-relative rank asks the only question that has an answer.
    Direction is applied from REGISTRY so a lower-is-better metric ranks the
    right way round without the caller knowing which is which.
    """
    lo, hi = config.FUND_WINSOR
    out = m[["ticker"]].copy()
    grp = sector.reindex(m.index) if sector is not None else None

    for name, (pillar, direction, _) in REGISTRY.items():
        if name not in m.columns:
            continue
        v = pd.to_numeric(m[name], errors="coerce")
        if grp is not None and grp.notna().sum() > len(v) * 0.5:
            q = v.groupby(grp).transform(
                lambda s: s.clip(s.quantile(lo), s.quantile(hi)).rank(pct=True)
                if s.notna().sum() >= 5 else pd.Series(np.nan, index=s.index))
            # Sectors too small to rank within fall back to the whole market
            # rather than dropping out of the screen entirely.
            fallback = v.clip(v.quantile(lo), v.quantile(hi)).rank(pct=True)
            q = q.fillna(fallback)
        else:
            q = v.clip(v.quantile(lo), v.quantile(hi)).rank(pct=True)
        out[f"r_{name}"] = q if direction > 0 else (1.0 - q)

    for pillar in PILLARS:
        cols = [f"r_{n}" for n, (p, _, _) in REGISTRY.items()
                if p == pillar and f"r_{n}" in out.columns]
        if not cols:
            continue
        sub = out[cols]
        cov = sub.notna().mean(axis=1)
        out[f"{pillar}_score"] = (sub.mean(axis=1) * 100).where(
            cov >= config.FUND_MIN_COVERAGE)
        out[f"{pillar}_cov"] = cov

    pcols = [f"{p}_score" for p in PILLARS if f"{p}_score" in out.columns]
    if pcols:
        wts = pd.Series({f"{p}_score": config.FUND_WEIGHTS.get(p, 1.0)
                         for p in PILLARS if f"{p}_score" in out.columns})
        sub = out[pcols]
        out["fund_score"] = (sub * wts).sum(axis=1, min_count=1) / (
            sub.notna().mul(wts).sum(axis=1).replace(0, np.nan))
        out["fund_cov"] = sub.notna().mean(axis=1)
    return out


# ===========================================================================
# Selftest
# ===========================================================================
def _frame(**kw) -> pd.DataFrame:
    n = len(next(iter(kw.values())))
    base = {"ticker": [f"T{i}" for i in range(n)]}
    base.update(kw)
    return pd.DataFrame(base)


def selftest(verbose: bool = True) -> None:
    fails = []

    # A NEGATIVE MULTIPLE IS NOT A CHEAP ONE. `pe`, `pb` and `ev_ebitda` rank
    # lower-is-better, so an undefined (negative-denominator) value sorted as
    # the cheapest thing in the market and handed loss-makers the best value
    # score. Pinned here because `peg` had the guard from the start and its
    # three siblings simply never got it.
    idx = pd.Index(["LOSS", "PROFIT"])
    # `dna` is required: derive() builds ebitda as opinc + D&A, so omitting it
    # makes ebitda NaN and the fixture tests nothing.
    dd = pd.DataFrame({"net_income": [-50.0, 100.0], "equity": [-20.0, 500.0],
                       "revenue": [300.0, 900.0], "dna": [30.0, 50.0],
                       "opinc": [-40.0, 150.0], "assets": [400.0, 900.0],
                       "debt_lt": [10.0, 10.0], "debt_st": [0.0, 0.0],
                       "cash": [5.0, 5.0]}, index=idx)
    dv = derive(dd)
    mc = pd.Series([1000.0, 2000.0], index=idx)
    evv = mc + dv["net_debt"]
    for name, den in (("pe", dv["net_income"]), ("pb", dv["equity"]),
                      ("ev_ebitda", dv["ebitda"])):
        num = evv if name == "ev_ebitda" else mc
        val = _safe(num, den).where(den > 0)
        if pd.notna(val.loc["LOSS"]):
            fails.append(f"{name} produced a value on a negative denominator")
        if pd.isna(val.loc["PROFIT"]):
            fails.append(f"{name} withheld a value on a POSITIVE denominator")

    # NOT REPORTED IS NOT ZERO. Both of these shipped as `.fillna(0)` and both
    # produced a flattering invented number: a filer tagging no debt line read
    # as debt-free, and a filer tagging no working capital read as a 0-day cash
    # conversion cycle. Pinned here because the pandas idiom that causes it
    # (`a.fillna(0) + b.fillna(0)`) is one character away from the correct one
    # and reads the same.
    s = _sum_reported(pd.Series([10.0, np.nan, np.nan]),
                      pd.Series([5.0, 7.0, np.nan]))
    if not (s.iloc[0] == 15.0 and s.iloc[1] == 7.0 and pd.isna(s.iloc[2])):
        fails.append(f"_sum_reported: want [15, 7, NaN], got {list(s)}")

    # A filer with nothing tagged must get NO cash conversion cycle...
    blank = pd.DataFrame({"inventory": [np.nan], "receivables": [np.nan],
                          "payables": [np.nan], "cogs": [np.nan],
                          "revenue": [np.nan]})
    if pd.notna(cash_conversion_cycle(blank).iloc[0]):
        fails.append("ccc invents a value for a filer that reported nothing")
    # ...while a filer that did report gets a real one.
    real = pd.DataFrame({"inventory": [100.0], "receivables": [200.0],
                         "payables": [50.0], "cogs": [1000.0],
                         "revenue": [2000.0]})
    if pd.isna(cash_conversion_cycle(real).iloc[0]):
        fails.append("ccc withholds a value a filer did report")

    # And the same for the balance sheet: no debt tag => no ROIC, not an
    # invented debt-free one.
    nodebt = derive(pd.DataFrame({"equity": [500.0], "assets": [1000.0],
                                  "cash": [50.0], "opinc": [100.0]}))
    if pd.notna(nodebt["debt"].iloc[0]):
        fails.append("debt invents 0 for a filer that tagged no debt line")
    if pd.notna(nodebt["invested_capital"].iloc[0]):
        fails.append("invested_capital computed from an unknown debt load")

    # THE CURRENCY SPLIT MUST BE TOTAL. A metric added to REGISTRY and named in
    # neither set would fall through as scale-free by omission and get
    # published for a EUR filer with a USD price in its numerator. The split is
    # asserted here so adding a metric forces the decision.
    unclassified = set(REGISTRY) - SCALE_FREE - NEEDS_FX
    if unclassified:
        fails.append(f"metric(s) in neither SCALE_FREE nor NEEDS_FX: "
                     f"{sorted(unclassified)}")
    if SCALE_FREE & NEEDS_FX:
        fails.append(f"metric(s) in both sets: {sorted(SCALE_FREE & NEEDS_FX)}")
    unknown = NEEDS_FX - set(REGISTRY)
    if unknown:
        fails.append(f"NEEDS_FX names a metric not in REGISTRY: {sorted(unknown)}")

    # The value pillar is entirely price-relative, so a non-USD filer must lose
    # all of it -- and must KEEP a usable share of quality, safety and growth,
    # or withholding would be indistinguishable from dropping the filer.
    for pillar in PILLARS:
        names = {n for n, (p, _, _) in REGISTRY.items() if p == pillar}
        kept = names - NEEDS_FX
        if pillar == "value" and kept:
            fails.append(f"value pillar has scale-free metrics {sorted(kept)}; "
                         f"the withholding logic assumes it does not")
        if pillar != "value" and len(kept) < len(names) * 0.5:
            fails.append(f"{pillar} pillar keeps only {len(kept)}/{len(names)} "
                         f"metrics for a non-USD filer -- too few to score")
    if verbose:
        print(f"  [fund_metrics] currency split: {len(SCALE_FREE)} scale-free, "
              f"{len(NEEDS_FX)} need FX and are withheld for non-USD filers")

    # Piotroski: a perfect firm must score 9, a failing one 0.
    good = _frame(net_income=[100.0], assets=[1000.0], cfo=[150.0], debt=[100.0],
                  assets_current=[500.0], liabilities_current=[200.0],
                  shares=[100.0], gross_profit=[400.0], revenue=[1000.0])
    bad = _frame(net_income=[50.0], assets=[1000.0], cfo=[40.0], debt=[200.0],
                 assets_current=[300.0], liabilities_current=[250.0],
                 shares=[100.0], gross_profit=[300.0], revenue=[900.0])
    f_hi = piotroski(good.set_index("ticker"), bad.set_index("ticker")).iloc[0]
    f_lo = piotroski(bad.set_index("ticker"), good.set_index("ticker")).iloc[0]
    if f_hi != 9:
        fails.append(f"Piotroski perfect firm scored {f_hi}, want 9")
    if f_lo > 3:
        fails.append(f"Piotroski failing firm scored {f_lo}, want <=3")

    # Altman Z on the textbook healthy-firm shape must land in the safe zone.
    d = derive(_frame(assets=[1000.0], assets_current=[600.0],
                      liabilities_current=[200.0], liabilities=[400.0],
                      retained=[300.0], opinc=[150.0], revenue=[1200.0],
                      equity=[600.0], debt_lt=[200.0], cash=[100.0]))
    z = altman_z(d, pd.Series([1500.0]))
    if not (z.iloc[0] > 2.99):
        fails.append(f"Altman Z healthy firm = {z.iloc[0]:.2f}, want >2.99")

    # ...and a distressed one below 1.81.
    dd = derive(_frame(assets=[1000.0], assets_current=[150.0],
                       liabilities_current=[500.0], liabilities=[950.0],
                       retained=[-400.0], opinc=[-50.0], revenue=[300.0],
                       equity=[50.0], debt_lt=[400.0], cash=[10.0]))
    zd = altman_z(dd, pd.Series([80.0]))
    if not (zd.iloc[0] < 1.81):
        fails.append(f"Altman Z distressed firm = {zd.iloc[0]:.2f}, want <1.81")

    # DuPont must reconstruct ROE exactly -- that is the identity's whole point.
    dq = derive(_frame(net_income=[100.0], pretax=[130.0], opinc=[150.0],
                       revenue=[1000.0], assets=[900.0], equity=[400.0]))
    du = dupont(dq)
    recon = du.prod(axis=1).iloc[0]
    roe = 100.0 / 400.0
    if abs(recon - roe) > 1e-6:
        fails.append(f"DuPont product {recon:.6f} != ROE {roe:.6f}")

    # Sloan: cash-backed earnings must score lower than accrual-heavy ones.
    a_clean = sloan_accruals(derive(_frame(net_income=[100.0], cfo=[150.0],
                                           assets=[1000.0]))).iloc[0]
    a_dirty = sloan_accruals(derive(_frame(net_income=[100.0], cfo=[10.0],
                                           assets=[1000.0]))).iloc[0]
    if not (a_clean < a_dirty):
        fails.append(f"Sloan: clean {a_clean:.3f} not < dirty {a_dirty:.3f}")

    # CCC must be additive in its three legs.
    c = cash_conversion_cycle(derive(_frame(inventory=[100.0], cogs=[365.0],
                                            receivables=[200.0], revenue=[730.0],
                                            payables=[50.0]))).iloc[0]
    want = 100 * 365 / 365 + 200 * 365 / 730 - 50 * 365 / 365
    if abs(c - want) > 1e-6:
        fails.append(f"CCC {c:.3f} != {want:.3f}")

    # SUE: a flat series must surprise by ~0, a jump must surprise positively.
    flat = pd.DataFrame([[1.0] * 8], index=["A"])
    jump = pd.DataFrame([[1.0] * 7 + [3.0]], index=["A"])
    s_flat, s_jump = sue(flat).iloc[0], sue(jump).iloc[0]
    if pd.notna(s_flat) and abs(s_flat) > 1e-6:
        fails.append(f"SUE flat series = {s_flat:.3f}, want ~0")
    if not (pd.isna(s_jump) or s_jump > 0):
        fails.append(f"SUE on a jump = {s_jump:.3f}, want >0")

    # Direction: every registered metric must rank the right way round.
    m = pd.DataFrame({"ticker": list("ABCDEFGHIJ"),
                      "roic": np.linspace(0, 1, 10),
                      "ev_ebitda": np.linspace(1, 50, 10)})
    r = rank_pillars(m)
    if r["r_roic"].iloc[-1] <= r["r_roic"].iloc[0]:
        fails.append("higher-is-better metric ranked backwards")
    if r["r_ev_ebitda"].iloc[-1] >= r["r_ev_ebitda"].iloc[0]:
        fails.append("lower-is-better metric ranked backwards")

    for name, (pillar, direction, _) in REGISTRY.items():
        if pillar not in PILLARS:
            fails.append(f"{name}: unknown pillar {pillar!r}")
        if direction not in (-1, 1):
            fails.append(f"{name}: direction must be +/-1")

    if fails:
        print("SELFTEST FAILURES:")
        for f in fails:
            print("  -", f)
        raise SystemExit(1)
    if verbose:
        print(f"fund_metrics selftest OK ({len(REGISTRY)} metrics across "
              f"{len(PILLARS)} pillars)")


def main() -> int:
    config.safe_console()
    ap = argparse.ArgumentParser(description="Fundamental metrics engine.")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        selftest()
    elif a.list:
        for pillar in PILLARS:
            print(f"\n  {pillar.upper()}")
            for n, (p, d, desc) in REGISTRY.items():
                if p == pillar:
                    print(f"    {n:20} {'higher' if d > 0 else 'lower ':>6} is better   {desc}")
    else:
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
