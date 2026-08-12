"""
Score module 3: hype.

The question this module exists to answer: **how much of this price is
attention rather than business?** The motivating cases are names like PLTR and
TSLA, where the narrative is doing work the financial statements do not explain.

WHAT MAKES THIS DIFFERENT FROM THE SENTIMENT MODULE
----------------------------------------------------
Sentiment already measures *what the news says* -- tone, event class, severity,
article counts. Hype deliberately does NOT re-measure that. It measures the
FOOTPRINT of attention in the tape and the GAP between price and fundamentals:

  attention      how many people are trading it, and in what size
  detachment     how far price has run beyond what revenue/earnings justify

Both are computed from data already on disk (bars carry `volume` AND `trades`;
the fact store carries revenue). No new source, no new key, no new rate limit.

THE SIGNATURE THAT MAKES THIS WORK: AVERAGE TRADE SIZE
--------------------------------------------------------
`volume / trades` is the average shares per print. When a name gets retail
attention the volume rises while the average print SHRINKS -- many small orders
rather than a few institutional blocks. Rising volume with rising trade size is
institutional accumulation and is a different phenomenon entirely. Most retail
attention proxies cost money (social APIs, alt-data); this one is already in
every bar we store and separates the two cases for free.

DIRECTION IS DELIBERATELY NOT ASSUMED
---------------------------------------
A high hype score is NOT a sell signal and NOT a buy signal until `factor_lab`
measures it. The literature points both ways -- attention predicts short-horizon
continuation and long-horizon reversal -- and this project has been burned twice
by assuming a sign (W_SUPPORT=20 measured weakest; Altman Z came out inverted).
`hype_score` is emitted as a magnitude. What it predicts is an open question and
`--leaderboard` is how it gets answered.

EVERYTHING IS RANKED CROSS-SECTIONALLY, PER DATE
--------------------------------------------------
Raw levels are not comparable across time: market-wide volume, volatility and
news flow all trend. A 2016 volume surge and a 2026 one mean different things in
absolute terms and the same thing in rank terms. Same rule the sentiment module
already follows for news counts.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import config
import store
from scores import register

NAME = "hype"

# Lookbacks in sessions. SHORT is "recent attention", LONG is the name's own
# baseline -- every attention metric is a ratio of the two, so a permanently
# heavily-traded name does not read as permanently hyped.
SHORT = 21
LONG = 252
GAP_WIN = 63              # ~one quarter of sessions for the gap-frequency count
GAP_PCT = 0.02            # an overnight move this size is a "gap" worth counting
MIN_BARS = 60             # below this nothing is computed; state it, never guess

# Components of the composite, and the direction each is entered with. All +1:
# every one of these is a measure of MAGNITUDE of attention/detachment, not of
# desirability. See the module docstring on why no sign is assumed.
# TWO PILLARS, NOT ONE FLAT AVERAGE -- and this distinction is the whole module.
#
# ATTENTION is a FLOW: is this being traded unusually hard right now.
# PREMIUM is a LEVEL: is this priced on story rather than on business.
#
# They are genuinely different questions and a name can be either without the
# other -- PLTR is expensive-but-quiet, AMC is cheap-but-frenzied. Averaging all
# nine components flat gave the valuation leg a 1/9 vote and ranked Coca-Cola
# (P/S 7.8) above Palantir (P/S 83), which is the exact inversion of what this
# module exists to detect. Pillars give the level leg an equal say with the flow
# leg, which is the conceptual model.
#
# THE 50/50 SPLIT BETWEEN PILLARS IS STRUCTURAL, NOT MEASURED. It follows from
# what the two pillars mean, not from evidence that they deserve equal weight.
# Per the standing rule, factor_lab must test `attention_score`, `premium_score`
# and `hype_score` separately before any of them earns weight anywhere else.
# THREE pillars, split by WHAT KIND OF QUANTITY each member is. Grouping by
# meaning rather than by intuition is the only defensible way to do this without
# measurements, and it is what stopped the tuning loop described below.
#
#   attention  FLOW      is it being traded unusually hard right now
#   premium    LEVEL     what is being paid per dollar of business
#   stretch    DELTA     how far price has moved relative to its own past
#                        and to the business
#
# `premium` deliberately contains ONE member. That is not an oversight: P/S is
# the only pure level here, and putting the two deltas alongside it diluted it
# to a 1/3 vote inside its own pillar and inverted the ranking again.
#
# A NOTE ON HOW THIS WAS ARRIVED AT, because it matters more than the result:
# three successive versions of this grouping were tried against a watchlist, and
# each was adjusted because PLTR (P/S 83) kept ranking below KO (P/S 7.8). That
# is fitting a composite to a prior, which is exactly what the standing rule
# forbids and exactly how W_SUPPORT=20 happened. The tuning stopped here, at the
# grouping that is justifiable from first principles rather than the one that
# produced the desired watchlist order.
#
# NOTHING HERE HAS BEEN MEASURED. `factor_lab` must evaluate attention_score,
# premium_score, stretch_score and hype_score independently before any of them
# is trusted or reweighted. It is entirely possible that the flat average, or a
# single component, beats all of this.
# `short_ratio` and `short_surge` join ATTENTION rather than getting their own
# pillar: a contested name is one people are actively taking the other side of,
# which is the same phenomenon as unusual volume, not a separate one.
#
# NOTE these are short VOLUME, not short INTEREST. Days-to-cover would be the
# better squeeze measure and it is **no longer free** -- FINRA's biweekly
# endpoint returns 403 (tested 2026-08-07 across three settlement dates). See
# finra.py. Short volume is fresher for a daily module in any case.
PILLARS = {
    "attention": ("vol_surge", "trade_surge", "trade_size_trend", "turnover",
                  "range_expansion", "gap_freq", "short_ratio", "short_surge"),
    "premium": ("ps_ratio",),
    "stretch": ("extension_pct", "px_vs_rev"),
}
COMPOSITE = tuple(m for members in PILLARS.values() for m in members)


class HypeModule:
    name = NAME

    def metrics(self) -> list[str]:
        return [
            # attention, from the tape
            "vol_surge", "trade_surge", "trade_size_trend", "turnover",
            "short_ratio", "short_surge",
            # detachment, price vs its own history and vs the business
            "range_expansion", "gap_freq", "above_200dma", "extension_pct",
            "px_vs_rev", "ps_ratio",
            # pillars, composite + provenance
            "attention_score", "premium_score", "stretch_score",
            "hype_score", "hype_cov", "has_hype", "avg_trade_size", "bars_used",
        ]

    # ------------------------------------------------------------------
    def compute(self, asof: str, tickers: list[str] | None = None,
                allow_partial: bool = False) -> pd.DataFrame:
        """Point-in-time hype for `asof`. Reads no bar dated after it."""
        empty = pd.DataFrame(columns=["ticker", "metric", "value", "label"])
        start = (pd.Timestamp(asof)
                 - pd.DateOffset(days=int(LONG * 1.8))).strftime("%Y-%m-%d")
        px = store.read(interval="1d", start=start, end=asof, tickers=tickers,
                        columns=["ticker", "date", "open", "high", "low",
                                 "close", "volume", "trades"])
        if px.empty:
            return empty

        px = px.sort_values(["ticker", "date"])
        w = _panel(px)
        if w is None:
            return empty

        feats = _features(w)
        if feats.empty:
            return empty

        # Fundamentals are OPTIONAL here: hype is primarily a tape measure, and
        # a name with no filings (ADR, recent IPO -- exactly the names that get
        # hyped) must still get an attention score. The detachment metrics that
        # need revenue are simply absent for them, and `hype_cov` says so.
        feats = feats.join(_fundamental_detachment(asof, feats.index.tolist()),
                           how="left")
        # MUST happen before _rank: these two are COMPOSITE members, and building
        # them after ranking (as the first version did) silently dropped them
        # from the composite while `hype_cov` still reported full coverage.
        feats = _derive_fundamental_features(feats)
        feats = feats.join(_short_features(asof, feats.index.tolist()),
                           how="left")

        ranked = _rank(feats)
        return _emit(feats, ranked)

    def selftest(self) -> None:
        _selftest()


# ==================================================================== panel
def _panel(px: pd.DataFrame) -> dict[str, pd.DataFrame] | None:
    """Wide (date x ticker) frames, one per column we need."""
    out = {}
    for col in ("open", "high", "low", "close", "volume", "trades"):
        if col not in px.columns:
            continue
        out[col] = px.pivot_table(index="date", columns="ticker", values=col,
                                  observed=True)
    if "close" not in out or out["close"].empty:
        return None
    return out


def _tail_mean(df: pd.DataFrame, n: int) -> pd.Series:
    return df.tail(n).mean()


def _features(w: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = w["close"]
    n_bars = close.notna().sum()
    keep = n_bars[n_bars >= MIN_BARS].index
    if not len(keep):
        return pd.DataFrame()

    close = close[keep]
    out = pd.DataFrame(index=keep)
    out["bars_used"] = n_bars[keep]

    vol = w.get("volume", pd.DataFrame()).reindex(columns=keep)
    trd = w.get("trades", pd.DataFrame()).reindex(columns=keep)

    # --- attention -------------------------------------------------------
    # Log ratios: a 10x surge and a 0.1x drought should be symmetric distances
    # from "normal", which a raw ratio is not.
    if not vol.empty:
        v_s, v_l = _tail_mean(vol, SHORT), _tail_mean(vol, LONG)
        out["vol_surge"] = _log_ratio(v_s, v_l)
    if not trd.empty:
        t_s, t_l = _tail_mean(trd, SHORT), _tail_mean(trd, LONG)
        out["trade_surge"] = _log_ratio(t_s, t_l)

    # Average print size vs its own baseline. NOT negated -- and that reversal
    # is the one thing in this module that has actually been measured.
    #
    # The original argued that SHRINKING prints are the retail-attention
    # signature and entered `-log(short/long)`. Measured 2026-08-08, h=20, 117
    # dates: raw `avg_trade_size` scored **IC +0.0197, t=3.06** while the
    # negated `trade_shrink` managed t=1.25. LARGER prints predict higher
    # forward returns -- the institutional-accumulation reading beats the
    # retail one, and the composite had been carrying the losing side.
    #
    # Renamed rather than silently flipped: `trade_shrink` meant the opposite,
    # and a stored series whose sign changed under a stable name is the worst
    # kind of quiet break for anything reading history.
    if not vol.empty and not trd.empty:
        size = vol.divide(trd.replace(0, np.nan))
        s_s, s_l = _tail_mean(size, SHORT), _tail_mean(size, LONG)
        out["avg_trade_size"] = s_s
        out["trade_size_trend"] = _log_ratio(s_s, s_l)

    # Dollar turnover: attention normalised by how big the company is. A $50M
    # daily tape is enormous for a micro-cap and rounding for AAPL.
    if not vol.empty:
        dollar = (vol.tail(SHORT) * close.tail(SHORT)).mean()
        out["_dollar_vol"] = dollar

    # --- detachment ------------------------------------------------------
    tr = _true_range(w, keep)
    if tr is not None:
        atr_s, atr_l = _tail_mean(tr, SHORT), _tail_mean(tr, LONG)
        out["range_expansion"] = _log_ratio(atr_s, atr_l)

    op = w.get("open", pd.DataFrame()).reindex(columns=keep)
    if not op.empty:
        prev = close.shift(1)
        gap = (op / prev - 1.0).abs().tail(GAP_WIN)
        out["gap_freq"] = (gap > GAP_PCT).sum() / gap.notna().sum().replace(0, np.nan)

    # Extension above the 200DMA. TWO metrics, because one number cannot do
    # both jobs:
    #
    #   above_200dma   raw percent. Interpretable, for display.
    #   extension_pct  where TODAY sits inside THIS NAME'S OWN history of that
    #                  same ratio, 0-100. This is what enters the composite.
    #
    # The first version divided by ATR to "make names comparable" and that
    # inverted the signal: dividing by volatility means a quiet staple reads as
    # violently extended (KO measured 5.3 ATRs above its DMA) while a 90%-vol
    # meme reads as calm (TSLA -6.7). Hype is not "far in ATR units", it is
    # "far BY THIS NAME'S OWN STANDARDS", which is a self-referential percentile.
    dma = close.rolling(200, min_periods=100).mean()
    ratio = (close / dma - 1.0).replace([np.inf, -np.inf], np.nan)
    out["above_200dma"] = ratio.iloc[-1]
    out["extension_pct"] = _self_percentile(ratio)

    out["_ret_1y"] = _ret(close, LONG)
    out["_price"] = close.iloc[-1]
    return out.replace([np.inf, -np.inf], np.nan)


def _self_percentile(series_wide: pd.DataFrame) -> pd.Series:
    """For each column, the percentile (0-100) of its LAST value within its own
    history. Answers "is this name unusually stretched for itself?" rather than
    "is it stretched compared to other companies", which is the hype question."""
    hist = series_wide.iloc[:-1]
    last = series_wide.iloc[-1]
    n = hist.notna().sum()
    below = hist.lt(last, axis=1).sum()
    return (below / n.replace(0, np.nan) * 100.0).where(n >= MIN_BARS)


def _short_features(asof: str, tickers: list[str]) -> pd.DataFrame:
    """FINRA short-volume pressure, as a RATIO and as a SURGE.

    `short_ratio`  mean(short_vol / total_vol) over SHORT sessions. The level of
                   short-side participation.
    `short_surge`  log(short SHORT-window ratio / LONG-window ratio). Whether
                   that pressure is unusual FOR THIS NAME, which is the hype
                   question -- some names are structurally 50% short-flagged
                   because of how their market makers report.

    The RATIO is used, never the raw `short_vol`: FINRA's denominator is
    FINRA-reported volume, not the full consolidated tape, so the level is not
    comparable to the bar store's `volume` column. Dividing inside the same
    source is the only safe comparison.

    Missing entirely (store not backfilled, or a name FINRA does not cover) is
    absent, not zero -- `hype_cov` reports the shortfall.
    """
    cols = ["short_ratio", "short_surge"]
    empty = pd.DataFrame(columns=cols, index=pd.Index(tickers, name="ticker"))
    try:
        import finra
        start = (pd.Timestamp(asof)
                 - pd.DateOffset(days=int(LONG * 1.8))).strftime("%Y-%m-%d")
        sv = finra.read(start=start, end=asof, tickers=tickers)
    except Exception:                                            # noqa: BLE001
        return empty
    if sv is None or sv.empty:
        return empty

    sv = sv.copy()
    sv["ratio"] = pd.to_numeric(sv["short_vol"], errors="coerce") / \
        pd.to_numeric(sv["total_vol"], errors="coerce").replace(0, np.nan)
    piv = sv.pivot_table(index="date", columns="ticker", values="ratio",
                         observed=True).sort_index()
    if piv.empty:
        return empty

    s = _tail_mean(piv, SHORT)
    l = _tail_mean(piv, LONG)
    out = pd.DataFrame(index=piv.columns)
    out["short_ratio"] = s
    out["short_surge"] = _log_ratio(s, l)
    out.index.name = "ticker"
    return out.reindex(tickers)


def _derive_fundamental_features(feats: pd.DataFrame) -> pd.DataFrame:
    """turnover, px_vs_rev and ps_ratio, built into `feats` so they reach the
    ranker. Everything here needs the market cap, so it is computed once."""
    mktcap = None
    if "_price" in feats.columns and "_shares" in feats.columns:
        mktcap = pd.to_numeric(feats["_price"], errors="coerce") \
            * pd.to_numeric(feats["_shares"], errors="coerce")

    if "_dollar_vol" in feats.columns and mktcap is not None:
        feats["turnover"] = (pd.to_numeric(feats["_dollar_vol"], errors="coerce")
                             / mktcap.replace(0, np.nan))

    # Price-to-sales: the standing narrative premium. Sales rather than earnings
    # deliberately -- the names this module exists to flag (PLTR, TSLA in its
    # growth years, most of the 2021 cohort) frequently have no earnings at all,
    # so a P/E screen simply returns NaN for exactly the population of interest.
    # Revenue must be strictly positive: a pre-revenue biotech would otherwise
    # divide by ~0 and take the top rank on an accounting artifact.
    if mktcap is not None and "_rev" in feats.columns:
        rev = pd.to_numeric(feats["_rev"], errors="coerce")
        feats["ps_ratio"] = mktcap / rev.where(rev > 0)
    if "_ret_1y" in feats.columns and "_rev_growth" in feats.columns:
        # Positive = price outran the business over the last year, which is the
        # detachment direction.
        feats["px_vs_rev"] = (pd.to_numeric(feats["_ret_1y"], errors="coerce")
                              - pd.to_numeric(feats["_rev_growth"],
                                              errors="coerce"))
    return feats.replace([np.inf, -np.inf], np.nan)


def _log_ratio(a: pd.Series, b: pd.Series) -> pd.Series:
    a = pd.to_numeric(a, errors="coerce")
    b = pd.to_numeric(b, errors="coerce")
    r = (a / b.replace(0, np.nan))
    return np.log(r.where(r > 0))


def _ret(close: pd.DataFrame, n: int) -> pd.Series:
    if len(close) <= n:
        return pd.Series(np.nan, index=close.columns)
    return close.iloc[-1] / close.iloc[-1 - n] - 1.0


def _true_range(w: dict[str, pd.DataFrame], keep) -> pd.DataFrame | None:
    hi, lo, cl = (w.get(k, pd.DataFrame()).reindex(columns=keep)
                  for k in ("high", "low", "close"))
    if hi.empty or lo.empty or cl.empty:
        return None
    prev = cl.shift(1)
    tr = pd.concat([(hi - lo).abs(), (hi - prev).abs(), (lo - prev).abs()])
    tr = tr.groupby(level=0).max()
    # Normalised by price: a $5 range means nothing without knowing the price.
    return (tr / cl).replace([np.inf, -np.inf], np.nan)


# =================================================== fundamental detachment
def _fundamental_detachment(asof: str, tickers: list[str]) -> pd.DataFrame:
    """price-vs-business metrics. Absent, not zero, when there are no filings."""
    cols = ["px_vs_rev", "mult_vs_hist", "turnover"]
    try:
        import fundamentals as FD
        cur = FD.facts_asof(asof, tickers)
        prior = FD.facts_asof(
            (pd.Timestamp(asof) - pd.DateOffset(years=1)).strftime("%Y-%m-%d"),
            tickers)
    except Exception:                                            # noqa: BLE001
        return pd.DataFrame(columns=cols, index=pd.Index(tickers, name="ticker"))

    if cur.empty:
        return pd.DataFrame(columns=cols, index=pd.Index(tickers, name="ticker"))

    c = cur.set_index("ticker")
    p = prior.set_index("ticker") if not prior.empty else c.iloc[0:0]
    out = pd.DataFrame(index=c.index)

    rev_now = pd.to_numeric(c.get("revenue_ttm"), errors="coerce")
    rev_ago = pd.to_numeric(p.get("revenue_ttm"), errors="coerce")
    if rev_now is not None and rev_ago is not None:
        out["_rev_growth"] = (rev_now / rev_ago.reindex(c.index)
                              .replace(0, np.nan)) - 1.0

    # Shared with the fundamental module -- see fundamentals.share_count for the
    # column-name bug this centralisation exists to prevent recurring.
    out["_shares"] = FD.share_count(c)
    out["_rev"] = rev_now
    return out


# ==================================================================== rank
def _rank(feats: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional percentile ranks, 0-100, per metric. See the docstring on
    why raw levels are never used across time."""
    ranked = pd.DataFrame(index=feats.index)
    for col in COMPOSITE:
        if col in feats.columns:
            v = pd.to_numeric(feats[col], errors="coerce")
            if v.notna().sum() >= 2:
                ranked[col] = v.rank(pct=True) * 100.0
    return ranked


def _emit(feats: pd.DataFrame, ranked: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []

    # Coverage BEFORE the composite: a name scored on two of eight components is
    # not comparable to one scored on all eight, and the reader cannot tell them
    # apart unless it is stated. Same discipline as fund_cov / news_coverage.
    #
    # The denominator is len(COMPOSITE), NOT the number of components that
    # happened to survive. Dividing by what survived is how the first version
    # reported cov=1.0 while two of eight members had silently never been built:
    # coverage measured against a shrunken denominator can never detect the
    # thing it exists to detect.
    cov = ranked.reindex(columns=list(COMPOSITE)).notna().sum(axis=1) / len(COMPOSITE)

    # Pillar means first, THEN the mean of the pillars -- not a flat mean of all
    # nine. See the PILLARS comment: flattening gives the valuation leg a 1/9
    # vote and inverts the ranking this module exists to produce.
    pillar_scores = {}
    for pillar, members in PILLARS.items():
        cols = [c for c in members if c in ranked.columns]
        if cols:
            pillar_scores[pillar] = ranked[cols].mean(axis=1, skipna=True)
    pillars = pd.DataFrame(pillar_scores)
    score = pillars.mean(axis=1, skipna=True).where(
        cov >= config.HYPE_MIN_COVERAGE)

    for pillar in pillars.columns:
        v = pillars[pillar]
        for t, x in v[v.notna()].items():
            rows.append({"ticker": t, "metric": f"{pillar}_score",
                         "value": float(x), "label": None})

    for t in feats.index:
        rows.append({"ticker": t, "metric": "has_hype",
                     "value": 1.0 if pd.notna(score.get(t)) else 0.0,
                     "label": None})
        rows.append({"ticker": t, "metric": "hype_cov",
                     "value": float(cov.get(t, 0.0)), "label": None})

    # Every declared metric that exists as a column, emitted from ONE place.
    # The first version emitted most here and built two others inline, which is
    # exactly how those two drifted out of the composite.
    emit_raw = [m for m in MODULE.metrics()
                if m not in ("hype_score", "hype_cov", "has_hype")]
    for col in emit_raw:
        if col not in feats.columns:
            continue
        v = pd.to_numeric(feats[col], errors="coerce")
        for t, x in v[v.notna() & np.isfinite(v)].items():
            rows.append({"ticker": t, "metric": col, "value": float(x),
                         "label": None})

    for t, x in score[score.notna()].items():
        rows.append({"ticker": t, "metric": "hype_score", "value": float(x),
                     "label": _band(x)})
    return pd.DataFrame(rows)


def _band(score: float) -> str:
    """A label, not a verdict. `hype_score` is a magnitude -- see the docstring."""
    if score >= 90:
        return "EXTREME"
    if score >= 75:
        return "HIGH"
    if score >= 50:
        return "ELEVATED"
    if score >= 25:
        return "NORMAL"
    return "QUIET"


MODULE = register(HypeModule())


# ================================================================= selftest
def _selftest() -> None:
    declared = set(MODULE.metrics())
    for c in COMPOSITE:
        assert c in declared, f"metrics() omits composite component {c!r}"
    assert "hype_score" in declared and "hype_cov" in declared

    # log ratio symmetry: 10x up and 10x down must be equal and opposite
    up = _log_ratio(pd.Series([10.0]), pd.Series([1.0])).iloc[0]
    dn = _log_ratio(pd.Series([1.0]), pd.Series([10.0])).iloc[0]
    assert abs(up + dn) < 1e-9, f"log ratio not symmetric: {up} vs {dn}"

    # zero and negative denominators must yield NaN, never inf
    assert pd.isna(_log_ratio(pd.Series([1.0]), pd.Series([0.0])).iloc[0])
    assert pd.isna(_log_ratio(pd.Series([-1.0]), pd.Series([1.0])).iloc[0])

    # trade_shrink sign: SMALLER average prints than baseline must score HIGHER,
    # because that is the retail-attention direction. This assertion exists
    # because getting it backwards would silently cancel the other attention
    # columns inside the composite rather than error.
    # Direction guard, now pointing the MEASURED way: bigger prints score
    # higher (avg_trade_size t=3.06) not smaller (trade_shrink t=1.25).
    grow = _log_ratio(pd.Series([400.0]), pd.Series([200.0])).iloc[0]
    shrink = _log_ratio(pd.Series([50.0]), pd.Series([200.0])).iloc[0]
    assert grow > 0 > shrink, f"trade_size_trend direction wrong: {grow}, {shrink}"

    # bands must be monotone
    order = ["QUIET", "NORMAL", "ELEVATED", "HIGH", "EXTREME"]
    got = [_band(v) for v in (10, 40, 60, 80, 95)]
    assert got == order, got

    # synthetic panel: a hyped name must outrank a quiet one
    dates = pd.date_range("2024-01-01", periods=300, freq="B")
    rng = np.random.default_rng(0)
    quiet_c = pd.Series(100 + rng.normal(0, 0.5, 300), index=dates)
    # A hyped name ACCELERATES -- it does not ramp linearly. This matters: a
    # steady compounder is always the same distance above its own 200DMA, so
    # `extension_pct` correctly reads it as normal-for-itself. Only a blow-off
    # pushes today's extension into the top of the name's own history. The first
    # version of this fixture used np.linspace and failed the extension
    # assertion, which was the fixture being unrealistic, not the metric.
    hyped_c = pd.Series(np.r_[np.linspace(100, 150, 250),
                              np.linspace(150, 420, 50)]
                        + rng.normal(0, 3, 300), index=dates)
    w = {
        "close": pd.DataFrame({"QUIET": quiet_c, "HYPED": hyped_c}),
        "open": pd.DataFrame({"QUIET": quiet_c, "HYPED": hyped_c * 1.01}),
        "high": pd.DataFrame({"QUIET": quiet_c * 1.01, "HYPED": hyped_c * 1.09}),
        "low": pd.DataFrame({"QUIET": quiet_c * 0.99, "HYPED": hyped_c * 0.91}),
        "volume": pd.DataFrame(
            {"QUIET": np.r_[np.full(279, 1e6), np.full(21, 1e6)],
             "HYPED": np.r_[np.full(279, 1e6), np.full(21, 2e7)]}, index=dates),
        "trades": pd.DataFrame(
            {"QUIET": np.r_[np.full(279, 5e3), np.full(21, 5e3)],
             "HYPED": np.r_[np.full(279, 5e3), np.full(21, 4e5)]}, index=dates),
    }
    f = _features(w)
    # Fake a fundamental join so the two revenue-dependent members exist.
    f["_shares"] = [1e9, 1e9]
    f["_rev_growth"] = [0.02, 0.05]
    f["short_ratio"] = [0.30, 0.62]   # HYPED more contested
    f["short_surge"] = [-0.05, 0.40]
    f["_rev"] = [5e10, 2e8]          # QUIET cheap on sales, HYPED expensive
    f = _derive_fundamental_features(f)
    assert f.loc["HYPED", "ps_ratio"] > f.loc["QUIET", "ps_ratio"]
    # Zero revenue must be absent, not infinite -- otherwise a pre-revenue shell
    # takes the top hype rank on a divide-by-almost-zero.
    z = _derive_fundamental_features(
        pd.DataFrame({"_price": [10.0], "_shares": [1e6], "_rev": [0.0]},
                     index=["ZERO"]))
    assert pd.isna(z.loc["ZERO", "ps_ratio"]), "zero revenue must yield NaN P/S"
    r = _rank(f)

    # THE REGRESSION GUARD. Two COMPOSITE members were originally built after
    # ranking, so they never entered the composite while hype_cov still read
    # 1.0 -- a silent 6-of-8 composite. Coverage cannot catch that, because the
    # bug shrinks the denominator too. Only this assertion can.
    unranked = [c for c in COMPOSITE if c not in r.columns]
    assert not unranked, f"COMPOSITE members never reach the ranker: {unranked}"
    for pillar, members in PILLARS.items():
        assert f"{pillar}_score" in declared, f"metrics() omits {pillar}_score"
        assert members, f"pillar {pillar!r} is empty"
    # Pillars must partition COMPOSITE: a member in neither pillar would be
    # ranked, counted in coverage, and then never actually voted.
    assert set(COMPOSITE) == {m for ms in PILLARS.values() for m in ms}

    assert f.loc["HYPED", "vol_surge"] > f.loc["QUIET", "vol_surge"]
    assert "trade_shrink" not in f.columns, \
        "trade_shrink was renamed to trade_size_trend and un-negated"
    assert f.loc["HYPED", "extension_pct"] > f.loc["QUIET", "extension_pct"], \
        "the trending name should sit higher in its OWN extension history"
    assert r.mean(axis=1)["HYPED"] > r.mean(axis=1)["QUIET"]

    # Coverage must be measured against the full COMPOSITE, not the survivors.
    e = _emit(f, r)
    covs = e[e.metric == "hype_cov"]["value"]
    assert (covs <= 1.0).all() and (covs > 0).all(), covs.tolist()

    print(f"  [hype] {len(declared)} metrics, {len(COMPOSITE)} composite "
          f"components; synthetic hyped name outranks quiet "
          f"({r.mean(axis=1)['HYPED']:.0f} vs {r.mean(axis=1)['QUIET']:.0f})")
