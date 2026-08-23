"""
Score module 5: three combined scores, one per horizon.

    combo_h1     built from h=1 evidence   (measured best at h=1,  t=+1.96)
    combo_h20    built from h=20 evidence  (measured best at h=20, t=+3.59)
    combo_h60    built from h=60 evidence  (measured best at h=20, t=+4.75)

The suffix is the evidence horizon, NOT a claim about where the score works --
`combo_h60` peaks at h=20, which is exactly why the old `combo_h60` name had
to go.

WHY THREE AND NOT ONE
-----------------------
Because the measurement says three. Sentiment's only overlap-corrected signal is
at **h=1** and reads dead at h=20; most fundamentals peak at **h=20**; `z_score`
is strongest at **h=60** (t=-5.44 against -3.37 at h=20). Averaging those into a
single number would blend a one-day signal with a three-month one and produce a
score that predicts neither. One score per horizon keeps "what does this
predict" answerable.

EVERY WEIGHT COMES FROM `study.py`, NOT FROM JUDGEMENT
-------------------------------------------------------
A metric enters a horizon's score only if its study cell at THAT horizon
(size="all") clears |t| >= 2 **and** its |IC| beats the random control's. The
sign is the measured sign, never an assumption -- this project has twice been
wrong by deciding a direction before measuring it.

THE THREE THINGS THAT WOULD OTHERWISE GO WRONG
------------------------------------------------
1. **Double counting.** 44 metrics clear |t| >= 2, but they are not 44
   independent signals: 22 pairs correlate above 0.7 and four pairs are exact
   duplicates (rho = 1.00) -- `asset_turnover`/`du_asset_turnover`,
   `op_margin`/`du_op_margin`, `sent_mean_30d`/`sent_rank`,
   `fund_score`/`fund_rank`. Weighting by t alone would count profitability
   three times and sentiment four. So near-duplicates are CLUSTERED and one
   representative survives.

2. **Composites of composites.** `fund_score` is already a weighted blend of 29
   metrics; feeding it into another blend counts its inputs twice, once raw and
   once bundled. Only RAW metrics are admitted -- see `COMPOSITE`.

3. **Metrics that predict for the wrong reason.** `days_since_filing` clears
   the bar at h=20 (t=+2.44) and h=60 (t=+2.84) and is still excluded -- but
   the reason written here for a while was WRONG, and it is worth recording why.

   The original claim was that it is significant only among small caps and dead
   in large and mid. That came from size buckets built by applying ONE session's
   market-cap terciles to ten years of history, and when the buckets were made
   point-in-time the picture inverted: it is now significant in every bucket
   (large +2.42, mid +2.58, small +4.18).

   The real mechanism is SIZE. Measured: Spearman(days_since_filing, mktcap) =
   **+0.355**, and median market cap rises $0.66B -> $2.11B -> $3.75B -> $6.38B
   across filing-recency quintiles. SEC deadlines are why -- large accelerated
   filers must file within 60 days of fiscal year end, accelerated 75,
   non-accelerated 90 -- so "filed longer ago" is a marker of being a big
   established company. `mktcap` is already excluded for exactly this reason, so
   admitting its proxy would smuggle the same size bet back in.

NO DATA IS NOT A LOW SCORE
----------------------------
If the study has never run, this module emits NOTHING rather than falling back
to equal weights -- an unmeasured composite presented as a measured one is the
exact thing the standing rule forbids. Same for a ticker below
`COMBO_MIN_COVERAGE`: no score, not a bad one.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import config
from scores import register

NAME = "combo"

# METRIC SUFFIX -> the study horizon whose evidence built it.
#
# Named for the EVIDENCE, not for a promise. These were `short`/`medium`/`long`
# and that was misleading: `combo_h60` is assembled from metrics that measured
# significant at h=60, but it predicts BEST at h=20 (t=+4.75 vs +4.23). A name
# that implies a horizon the score does not actually peak at is the kind of
# thing a reader acts on without checking.
#
# `combo_h60` says only "built from 60-day evidence", which is true and
# checkable. Where each one actually peaks is measured, and stated in
# metrics_doc rather than encoded in the name.
HORIZONS = {"h1": 1, "h20": 20, "h60": 60}

# Theme sub-scores are published for ONE horizon -- all three would be 21 extra
# series for a page that shows one radar. Keyed by the horizon NUMBER, never by
# the label: the previous version tested `lab == "medium"`, and renaming the
# labels silently stopped every theme from being emitted while 2.5M stale
# `th_*` rows stayed in the store, so the pages went on rendering a frozen
# number as if it were current. A comparison against a renameable string is a
# comparison waiting to break quietly.
THEME_HORIZON = 20

# Thresholds live in config with the rest of the tunables, not here.
MIN_T = config.COMBO_MIN_T
DEDUP_RHO = config.COMBO_DEDUP_RHO
MIN_COVERAGE = config.COMBO_MIN_COVERAGE

# Composites, ranks of composites, and presence flags. A blend of blends
# double-counts; a presence flag is not a signal.
COMPOSITE = frozenset({
    "fund_score", "quality_score", "value_score", "safety_score",
    "growth_score", "hype_score", "dip_score", "attention_score",
    "premium_score", "stretch_score", "fund_rank",
    "growth_rank", "sent_rank", "dip_gate", "dip_cov", "fund_cov",
    "has_dip", "has_fundamentals", "has_news", "news_coverage",
    "combo_h1", "combo_h20", "combo_h60",
    # COMBO'S OWN OUTPUTS. The theme sub-scores and coverage fractions are
    # produced BY this module, so admitting one would feed the composite back
    # into itself -- a metric built from `th_profitability` would count every
    # profitability metric twice and then call the result independent evidence.
    # The unthemed-metric warning in `selftest` is what surfaced this.
    "th_profitability", "th_valuation", "th_safety", "th_growth",
    "th_efficiency", "th_sentiment", "th_attention",
    "combo_h1_cov", "combo_h20_cov", "combo_h60_cov",
    "combo_h1_n", "combo_h20_n", "combo_h60_n",
})

# Excluded for a REASON, not for a weak t-stat. Each entry is a metric that
# measures something other than the company.
EXCLUDED = {
    "days_since_filing":
        "a SIZE proxy, not a company quality. Spearman +0.355 with mktcap, and "
        "median cap rises $0.66B -> $6.38B across filing-recency quintiles, "
        "because SEC deadlines scale with filer status (60/75/90 days). mktcap "
        "is already excluded, so its proxy must be too.",
    "bars_used":
        "a count of the input rows available, not a property of the company.",
    "mktcap":
        "size itself. Already the study's control variable, and admitting it "
        "would make the score partly a size bet wearing a factor's name.",
    "sent_age":
        "how old OUR newest article is -- a property of the news feed, not of "
        "the company, and the same category error as days_since_filing. It is "
        "a diagnostic for READING a sentiment score, which is why it sits next "
        "to one on every page, not an input to a composite.",
    "sent_stale":
        "the boolean form of sent_age; excluded for the same reason.",
    "news_coverage":
        "what fraction of the window the news store covers. Measures our "
        "fetching, not the company.",
}

# What each metric is ABOUT. Weight is assigned per theme, so that a theme with
# eight metrics does not outvote one with two purely by being easier to measure.
# A metric with no theme is refused loudly -- see selftest.
THEMES = {
    "profitability": {"roe", "roa", "roic", "roic_wacc", "gpoa", "eva",
                      "op_margin", "du_op_margin", "gross_margin", "net_margin",
                      "f_score", "du_tax_burden", "du_interest_burden"},
    "valuation":     {"pe", "pb", "ps", "ps_ratio", "ev_sales", "ev_ebitda",
                      "fcf_yield", "earnings_yield", "shareholder_yield", "peg"},
    "safety":        {"z_score", "m_score", "net_debt_ebitda", "interest_cover",
                      "du_leverage", "net_issuance", "debt_equity",
                      "current_ratio", "quick_ratio", "altman_z"},
    "growth":        {"rev_growth", "eps_growth", "fcf_growth", "ebitda_growth",
                      "book_growth", "mom_12_1",
                      # Sequential growth and margin direction, added
                      # 2026-08-23. `ebitda_growth` and `book_growth` were
                      # listed here long before they existed in
                      # `fund_metrics.REGISTRY`, so `admitted` could never
                      # take them; they are real now.
                      "rev_growth_q", "eps_growth_q",
                      "gross_margin_chg", "op_margin_chg"},
    "efficiency":    {"asset_turnover", "du_asset_turnover", "accruals",
                      "cash_conversion", "inventory_turns", "ccc"},
    "sentiment":     {"sent_mean_5d", "sent_mean_30d", "sent_mean_90d",
                      "sent_net_5d", "sent_net_30d", "sent_net_90d",
                      # The recency-weighted twins. Same theme as their plain
                      # counterparts, so dedup picks whichever measured better
                      # rather than letting both count as separate evidence --
                      # they correlate near 1.0 by construction.
                      "sent_decay_5d", "sent_decay_30d", "sent_decay_90d",
                      "sent_delta", "news_z", "news_count_30d",
                      "severity_max"},
    "attention":     {"avg_trade_size", "turnover", "vol_surge", "trade_surge",
                      "trade_size_trend", "range_expansion", "gap_freq",
                      "short_ratio", "short_surge", "extension_pct",
                      "px_vs_rev", "drawdown", "senti_gap", "not_extended"},
}

_THEME_OF = {m: th for th, ms in THEMES.items() for m in ms}


# =========================================================================
# Admission: which metrics earn a place, per horizon
# =========================================================================
def admitted(horizon: int, study_df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Metrics that earned a place at `horizon`, with their measured sign.

    Returns empty if the study has not run -- the caller must then emit nothing.
    """
    import study
    df = study.read() if study_df is None else study_df
    if df is None or df.empty:
        return pd.DataFrame(columns=["metric", "module", "t", "ic", "sign", "theme"])

    s = df[(df["horizon"] == horizon) & (df["size"] == "all")].copy()
    s = s[s["t"].abs() >= MIN_T]
    # Beat the random control, not merely the zero line.
    s = s[s["ic"].abs() > s["ic_random"].abs()]
    s = s[~s["metric"].isin(COMPOSITE)]
    s = s[~s["metric"].isin(EXCLUDED)]
    if s.empty:
        return pd.DataFrame(columns=["metric", "module", "t", "ic", "sign", "theme"])

    s["sign"] = np.sign(s["t"])
    s["theme"] = s["metric"].map(_THEME_OF)
    # A metric with no theme is DROPPED, not defaulted into a bucket: silently
    # bucketing an unknown metric is how a composite drifts without anyone
    # noticing. The selftest turns this into a visible failure.
    s = s[s["theme"].notna()]
    return s[["metric", "module", "t", "ic", "sign", "theme"]].reset_index(drop=True)


def dedup(wide: pd.DataFrame, adm: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Collapse near-duplicate metrics to one representative each.

    Clusters are built on the CURRENT session's cross-section, sign-aligned
    first so that a value metric and a quality metric are not called redundant
    merely for pointing opposite ways. The survivor of a cluster is the metric
    with the largest |t|, ties broken by name so the choice is reproducible.
    """
    cols = [m for m in adm["metric"] if m in wide.columns]
    if len(cols) < 2:
        return adm[adm["metric"].isin(cols)], {}

    sgn = dict(zip(adm["metric"], adm["sign"]))
    al = wide[cols].mul(pd.Series({c: sgn.get(c, 1.0) for c in cols}), axis=1)
    corr = al.corr(method="spearman", min_periods=200).abs()

    tmap = dict(zip(adm["metric"], adm["t"].abs()))
    order = sorted(cols, key=lambda m: (-tmap.get(m, 0.0), m))

    keep: list[str] = []
    dropped: dict = {}
    for m in order:
        rival = next((k for k in keep
                      if pd.notna(corr.loc[m, k]) and corr.loc[m, k] >= DEDUP_RHO),
                     None)
        if rival is None:
            keep.append(m)
        else:
            dropped[m] = (rival, float(corr.loc[m, rival]))
    return adm[adm["metric"].isin(keep)].reset_index(drop=True), dropped


# =========================================================================
# The module
# =========================================================================
class ComboModule:
    name = NAME

    def metrics(self) -> list[str]:
        out = []
        for lab in HORIZONS:
            out += [f"combo_{lab}", f"combo_{lab}_cov", f"combo_{lab}_n"]
        # `sentiment` is a real theme but NOT a declarable one: themes publish
        # at THEME_HORIZON, and no sentiment metric survives 20 days -- all
        # nine of them admit at h=1 and nowhere else. Declaring `th_sentiment`
        # made the audit report a metric permanently missing, which is how a
        # warning list becomes something people scroll past. The absence is the
        # measurement, and it is stated here rather than shown as a gap.
        out += [f"th_{t}" for t in sorted(THEMES)
                if not (t == "sentiment" and THEME_HORIZON != 1)]
        return out

    def compute(self, asof: str, tickers: list[str] | None = None,
                allow_partial: bool = False,
                study_df: pd.DataFrame | None = None) -> pd.DataFrame:
        """`study_df` overrides the fitted evidence, and exists for ONE reason:
        out-of-sample validation.

        Production passes nothing and gets the full-history study, which means
        the score is fitted on the same data it is later graded against. To ask
        whether it generalises, `oos.py` fits on a train period and passes that
        frozen table here while scoring held-out dates. Without this parameter
        the module simply cannot be validated -- it would always read the study
        that already saw the answer.
        """
        empty = pd.DataFrame(columns=["ticker", "metric", "value", "label"])

        import study
        sdf = study.read() if study_df is None else study_df
        if sdf is None or sdf.empty:
            # No measurement means no weights. Equal weights here would be a
            # guess presented with the authority of a measured number.
            return empty

        wide = _sibling_wide(asof, tickers)
        if wide.empty:
            return empty

        rows: list[dict] = []
        for lab, h in HORIZONS.items():
            adm = admitted(h, sdf)
            if adm.empty:
                continue
            adm, _dropped = dedup(wide, adm)
            cols = [m for m in adm["metric"] if m in wide.columns]
            if not cols:
                continue

            sgn = dict(zip(adm["metric"], adm["sign"]))
            th_of = dict(zip(adm["metric"], adm["theme"]))
            tw = adm.assign(at=adm["t"].abs()).groupby("theme")["at"].mean()

            # Rank each metric across the universe, sign-aligned so that 100 is
            # always "good". Ranking rather than z-scoring: XBRL carries genuine
            # unit-error outliers, and a rank cannot be dragged by one 1e12.
            ranked = {}
            for m in cols:
                v = pd.to_numeric(wide[m], errors="coerce")
                r = v.rank(pct=True) * 100.0
                ranked[m] = r if sgn.get(m, 1.0) >= 0 else (100.0 - r)
            R = pd.DataFrame(ranked, index=wide.index)

            cov = R.notna().sum(axis=1) / float(len(cols))

            theme_scores = {}
            for th in sorted(set(th_of.values())):
                mcols = [m for m in cols if th_of[m] == th]
                theme_scores[th] = R[mcols].mean(axis=1, skipna=True)
            T = pd.DataFrame(theme_scores, index=wide.index)

            w = pd.Series({th: float(tw.get(th, 0.0)) for th in T.columns})
            # Weighted mean over the themes a ticker actually has, so a missing
            # theme dilutes nothing -- it just does not vote.
            num = (T * w).sum(axis=1, min_count=1)
            den = T.notna().mul(w).sum(axis=1).replace(0, np.nan)
            score = (num / den).where(cov >= MIN_COVERAGE)

            for t, x in score[score.notna()].items():
                rows.append({"ticker": t, "metric": f"combo_{lab}",
                             "value": float(x), "label": _band(x)})
            for t, x in cov.items():
                if np.isfinite(x):
                    rows.append({"ticker": t, "metric": f"combo_{lab}_cov",
                                 "value": float(x), "label": None})
            rows.append({"ticker": "_ALL", "metric": f"combo_{lab}_n",
                         "value": float(len(cols)), "label": ",".join(sorted(cols))})

            # Themes for one horizon only -- see THEME_HORIZON.
            if h == THEME_HORIZON:
                for th in T.columns:
                    v = T[th]
                    for t, x in v[v.notna()].items():
                        rows.append({"ticker": t, "metric": f"th_{th}",
                                     "value": float(x), "label": None})

        return pd.DataFrame(rows) if rows else empty

    def selftest(self) -> None:
        _selftest()


# ================================================================== helpers
def _sibling_wide(asof: str, tickers: list[str] | None) -> pd.DataFrame:
    """Every other module's latest scores at `asof`, as ticker x metric.

    Same point-in-time discipline as `scores/dip.py:_sibling_scores`: the newest
    session AT OR BEFORE `asof`, never after.
    """
    import scores
    scores.load_all()
    frames = []
    for mod in config.SCORE_MODULES:
        if mod == NAME:
            continue
        try:
            sess = [s for s in scores.sessions_stored(mod) if s <= asof]
            if not sess:
                continue
            df = scores.read(module=mod, start=sess[-1], end=sess[-1],
                             tickers=tickers)
            if df is None or df.empty:
                continue
            frames.append(df.pivot_table(index="ticker", columns="metric",
                                         values="value", aggfunc="last"))
        except Exception:                                        # noqa: BLE001
            continue
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, axis=1)
    return out.loc[:, ~out.columns.duplicated()]


def _band(v: float) -> str:
    if v >= 80:
        return "STRONG"
    if v >= 60:
        return "ABOVE"
    if v >= 40:
        return "MIDDLING"
    if v >= 20:
        return "BELOW"
    return "WEAK"


MODULE = register(ComboModule())


# ================================================================= selftest
def _selftest() -> None:
    declared = set(MODULE.metrics())
    for lab in HORIZONS:
        assert f"combo_{lab}" in declared, lab
        assert f"combo_{lab}_cov" in declared, lab

    # No metric may sit in two themes -- it would then be counted twice and
    # would also inflate its theme's weight.
    seen: dict[str, str] = {}
    for th, ms in THEMES.items():
        for m in ms:
            assert m not in seen, f"{m!r} is in both {seen[m]!r} and {th!r}"
            seen[m] = th

    # Composites must never be admissible, at any horizon.
    assert not (COMPOSITE & set(_THEME_OF)), \
        f"composite(s) given a theme: {sorted(COMPOSITE & set(_THEME_OF))}"

    # Every excluded metric needs a stated reason, so that an exclusion cannot
    # quietly become "we did not like the number".
    for m, why in EXCLUDED.items():
        assert isinstance(why, str) and len(why) > 30, f"{m}: reason too thin"

    import study
    sdf = study.read()
    if sdf is None or sdf.empty:
        print("  [combo] study has not run; module will emit nothing (correct)")
        return

    # THE HORIZON GUARD. Each score's inputs must come from its own horizon's
    # cells. Mixing them is the exact mistake the three-score design exists to
    # prevent, and it would be invisible in the output.
    for lab, h in HORIZONS.items():
        adm = admitted(h, sdf)
        if adm.empty:
            continue
        cells = sdf[(sdf["size"] == "all") & (sdf["horizon"] == h)]
        assert set(adm["metric"]) <= set(cells["metric"]), \
            f"{lab}: admitted a metric with no cell at h={h}"
        assert (adm["t"].abs() >= MIN_T).all(), f"{lab}: admitted |t| < {MIN_T}"

    # Any metric the study finds significant but THEMES does not know about is
    # silently dropped. Report it: that is how a new metric goes missing.
    unthemed = set()
    for h in HORIZONS.values():
        s = sdf[(sdf["horizon"] == h) & (sdf["size"] == "all")]
        s = s[(s["t"].abs() >= MIN_T) & (~s["metric"].isin(COMPOSITE))
              & (~s["metric"].isin(EXCLUDED))]
        unthemed |= set(s["metric"]) - set(_THEME_OF)
    if unthemed:
        print(f"  [combo] WARNING: {len(unthemed)} significant metric(s) have no "
              f"theme and are being dropped: {sorted(unthemed)}")

    # THE THEME-EMISSION GUARD. `THEME_HORIZON` must name a horizon that
    # actually exists, or every `th_*` series stops being written while the
    # stale rows already in the store keep rendering as current. That is
    # precisely what happened when this was a `lab == "medium"` string compare
    # and the labels were renamed -- nothing raised, nothing looked empty, and
    # the pages showed a frozen number for every session after the rename.
    assert THEME_HORIZON in HORIZONS.values(), (
        f"THEME_HORIZON={THEME_HORIZON} is not one of {sorted(HORIZONS.values())}; "
        f"no theme sub-score would ever be emitted")
    themed = {m for m in declared if m.startswith("th_")}
    assert themed, "no th_* metric is declared"

    n = {lab: len(admitted(h, sdf)) for lab, h in HORIZONS.items()}
    print(f"  [combo] {len(declared)} metrics; admitted "
          + " ".join(f"{lab}={n[lab]}" for lab in HORIZONS) + " "
          f"(pre-dedup), {len(EXCLUDED)} excluded with reasons")
    print(f"  [combo] {len(themed)} theme sub-score(s) published at "
          f"h={THEME_HORIZON}")
