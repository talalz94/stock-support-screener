"""
The metric dictionary: what every number on every page means.

    python metrics_doc.py --build       write reports/metrics.html
    python metrics_doc.py --show pe     one metric, in the terminal
    python metrics_doc.py --selftest    the drift guard

ONE REGISTRY, THREE RENDERINGS
--------------------------------
Every explanation the UI shows comes from `ENTRIES` here: the reference page,
the `title=` tooltip on a column header, and the click-to-open panel. Writing
the same explanation into three templates is how they drift apart, and a stale
explanation is worse than none because it is believed.

GENERATED FROM THE CODE THAT DEFINES THE METRICS
--------------------------------------------------
`fund_metrics.REGISTRY` already carries `(pillar, direction, description)` for
29 fundamentals and `ui.METRIC_LABELS` merges that with the hype/dip/sentiment
labels. This module does NOT re-type any of that -- it reads both and adds only
what they lack: how to read the number, its typical range, and whether it has
been measured.

**THE DRIFT GUARD IS THE POINT.** `selftest()` asserts every metric emitted by
every registered module has an entry, and that every entry names a live metric.
Add a metric and the test fails until it is documented; delete one and the test
fails until the entry goes. That is what keeps this current when metrics change,
which is the thing a hand-written reference can never promise.

MEASURED RESULTS ARE PART OF THE DEFINITION
---------------------------------------------
`hype_score` and `dip_score` are structure with no measured evidence behind
them, and that fact lived only in PROJECT_LOG.md where no reader of the
dashboard would ever see it. Each entry carries its leaderboard number, so the
page itself says whether a score has been validated.

NO MEASURED NUMBER IS TYPED INTO A LABEL
------------------------------------------
The prose says what a metric IS and how to read it; every t-stat and IC comes
from `study.py` and the out-of-sample tables at build time. Hand-typed figures
went stale the moment the data was corrected -- one label spent months quoting
another metric's t-stat -- and a stale number that reads as measured is worse
than no number. `validate.py` fails the build if a quoted figure drifts from
its table, and warns if the study predates the data it claims to describe.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

import config

config.safe_console()

import ui                                                        # noqa: E402

# Measured 2026-08-08, h=20, against the random control. `None` = not tested.
# Keep in step with PROJECT_LOG.md; `docs.py` regenerates the log table from
# the same numbers.
MEASURED: dict[str, tuple[float, float]] = {
    "fund_score": (+0.0350, 4.61), "z_score": (-0.0344, -3.37),
    "net_issuance": (-0.0423, -3.20), "quality_score": (+0.0373, 2.93),
    "safety_score": (+0.0071, 1.14), "sent_mean_30d": (+0.0158, 3.29),
    "ps_ratio": (-0.0285, -2.26), "premium_score": (-0.0285, -2.26),
    "avg_trade_size": (+0.0197, 3.06), "turnover": (+0.0180, 2.02),
    "hype_score": (-0.0132, -1.36), "attention_score": (+0.0005, 0.07),
    "stretch_score": (+0.0028, 0.28), "extension_pct": (-0.0058, -0.67),
    "short_ratio": (-0.0046, -0.77), "short_surge": (-0.0012, -0.28),
    "dip_score": (+0.0049, 0.24), "dip_gate": (+0.0181, 2.95),
    "drawdown": (-0.0141, -0.53), "fund_rank": (+0.0216, 2.23),
    "growth_rank": (+0.0137, 1.50), "senti_gap": (-0.0041, -0.31),
    # trade_size_trend carried avg_trade_size's pair verbatim -- the same
    # transcription error as its label. It measures -0.0039 / -0.52 at h=20.
    "not_extended": (+0.0047, 0.39), "trade_size_trend": (-0.0039, -0.52),
}

def _load_study() -> dict[str, tuple[float, float]]:
    """Overlay MEASURED with the study file the moment it exists.

    The 24 hand-typed numbers above were transcribed from two terminal runs,
    which is why 73 of 97 metrics read "not tested" when 87 were testable. Once
    `study.py` finishes, its results supersede them AUTOMATICALLY -- no edit,
    no redeploy, nothing for anyone to remember.

    A metric is judged at the horizon where it is strongest, not at one
    arbitrary horizon: sentiment works at h=1 and reads dead at h=20, so fixing
    the horizon would manufacture false negatives.
    """
    try:
        import study
        best = study.best_by_metric()
        if best is None or best.empty:
            return {}
        return {str(r["metric"]): (float(r["ic"]), float(r["t"]))
                for _, r in best.iterrows()
                if r["t"] == r["t"]}
    except Exception:                                            # noqa: BLE001
        return {}


MODULE_DOC = {
    "fundamental": (
        "What the business is worth and how well it is run. 29 metrics from the "
        "SEC filings themselves — profitability, valuation, balance-sheet "
        "safety and growth — rolled into four pillar ranks and one composite. "
        "Point-in-time: a fact counts only once it was actually filed, so a "
        "quarter ending in March is invisible until the 10-Q lands in May."),
    "sentiment": (
        "What the news is saying. Every article is scored for tone and sorted "
        "into an event class whose severity was MEASURED from how prices "
        "actually moved, not guessed. Its signal is genuinely fast — it shows "
        "up at h=1 and is gone by h=20 — so read it for today, not for next "
        "month."),
    "combo": (
        "Three scores instead of one, because the evidence comes in three "
        "speeds. Sentiment only predicts tomorrow; most fundamentals predict a "
        "month out; balance-sheet safety predicts a quarter out. Blending those "
        "into one number would average away all three, so `combo_h1` (1 "
        "day), `combo_h20` (1 month) and `combo_h60` (1 quarter) are kept "
        "apart. Nothing is hand-weighted: a metric enters a horizon only if it "
        "was MEASURED to work at that horizon and to beat a random pick. "
        "TESTED OUT OF SAMPLE. The fitted numbers are shown in the "
        "out-of-sample block on this page, refreshed from the result files "
        "every build rather than typed here. Read them fold by fold, not as "
        "an average: a score that works early and fades late is a regime, not "
        "an edge. Two limits that no split removes -- each fold carries only "
        "~26 test dates, so ONE weak fold is noise while a slide across every "
        "score is not; and the metric definitions and theme assignments were "
        "authored with the full history already seen."),
    "hype": (
        "How much of the price is attention rather than business. Three "
        "pillars: ATTENTION (is it being traded unusually hard — volume, print "
        "counts, short volume), PREMIUM (what you pay per dollar of sales), and "
        "STRETCH (how far price has run from its own past). Built for names "
        "like PLTR where the story is doing work the accounts do not explain. "
        "It is a MAGNITUDE, not a recommendation: high hype is neither buy nor "
        "sell until measured."),
    "dip": (
        "Strong business, depressed price. A GATE, not a blend: a name must be "
        "top-30% on fundamentals AND top-50% on growth even to qualify, and "
        "only then is it ranked by how beaten-down it is. Averaging quality "
        "with drawdown would let a collapsing company score well because the "
        "fall was large — the falling knife this exists to avoid."),
}

# Metrics that are PROVENANCE, not signal. Mirrors factor_lab.NON_SIGNAL.
PROVENANCE = {
    "hype_cov", "fund_cov", "dip_cov", "news_coverage", "has_hype",
    "has_fundamentals", "has_news", "has_dip", "bars_used", "last_filed",
    "sector",
}

# `how to read` + `typical range` per metric. The label and the one-line
# description come from ui.METRIC_LABELS / fund_metrics.REGISTRY and are NOT
# repeated here.
READ: dict[str, tuple[str, str]] = {
    # --- composites and pillars -------------------------------------------
    "fund_score": ("higher = stronger business on the four pillars combined",
                   "0-100 percentile"),
    "quality_score": ("higher = better returns on capital and margins", "0-100"),
    "value_score": ("higher = cheaper on the valuation metrics", "0-100"),
    "safety_score": ("higher = stronger balance sheet", "0-100"),
    "growth_score": ("higher = faster growth", "0-100"),
    "hype_score": ("magnitude, NOT a direction. High means attention and "
                   "narrative premium, which may be good or bad",
                   "0-100"),
    "attention_score": ("higher = traded unusually hard versus its own baseline",
                        "0-100"),
    "premium_score": ("higher = paying more per dollar of sales", "0-100"),
    "stretch_score": ("higher = price has run further from its own past",
                      "0-100"),
    "dip_score": ("only exists for names that PASSED the quality gate; higher "
                  "= more depressed inside that set", "0-100 or absent"),
    "dip_gate": ("1 = top 30% fundamentals AND top 50% growth. 0 = did not "
                 "qualify, so dip_score is deliberately absent", "0 or 1"),
    # --- valuation ---------------------------------------------------------
    "pe": ("lower is cheaper. Negative means loss-making, not cheap",
           "5-40 typical; clipped at +-500"),
    "pb": ("lower is cheaper. Below 1 = priced under book value", "0.5-10"),
    "ev_ebitda": ("lower is cheaper; capital-structure neutral", "5-20"),
    "ps_ratio": ("market cap / TTM revenue. MEASURED NEGATIVE: expensive on "
                 "sales predicted LOWER returns", "0.5-15; >30 is extreme"),
    "fcf_yield": ("higher = more cash per dollar of enterprise value",
                  "0.02-0.10"),
    "peg": ("P/E divided by growth. Below 1 is the classic cheap-growth mark",
            "0.5-3"),
    "mktcap": ("market cap = price x point-in-time share count", "USD"),
    # --- quality / returns -------------------------------------------------
    "roe": ("net income / equity. Very high can mean thin equity, not skill",
            "universe median 0.08; 0.15+ is strong"),
    "roic": ("return on invested capital, the cleanest profitability read",
             "0.05-0.25"),
    "roic_wacc": ("ROIC minus cost of capital. Above 0 = creating value",
                  "-0.10 to +0.20"),
    "f_score": ("Piotroski, 9 binary tests. 8-9 strong, 0-2 weak", "0-9"),
    "z_score": ("Altman. Below 1.81 = distress. MEASURED INVERTED here: "
                "higher Z predicted LOWER returns (distress premium)",
                "<1.81 distress, >2.99 safe"),
    "m_score": ("Beneish. Above -1.78 flags possible earnings manipulation",
                "-3 to -1"),
    "accruals": ("Sloan. High = profit is accounting, not cash", "-0.1 to 0.1"),
    "ccc": ("cash conversion cycle in days. Negative is excellent",
            "-100 to 200 days"),
    "net_issuance": ("share count change. Positive = dilution, which measured "
                     "NEGATIVE for returns", "-0.05 to 0.10"),
    # --- hype --------------------------------------------------------------
    "vol_surge": ("log(21d / 252d volume). 0 = normal, 0.7 = double", "-1 to 2"),
    "trade_surge": ("log(21d / 252d print count)", "-1 to 2"),
    # The LEVEL works and the TREND does not, and for two years this label had
    # them swapped: `avg_trade_size`'s t was transcribed onto `trade_size_trend`
    # and an "institutional reading" story written on top of it. Big prints
    # predict; big prints GROWING does not.
    "trade_size_trend": ("log(21d / 252d average print size). MEASURED FLAT: "
                         "negative and not significant at any horizon. The "
                         "LEVEL works, the change does not -- see "
                         "avg_trade_size. Current numbers in the strength "
                         "column; they are read from the study, not typed here",
                         "-1 to 1"),
    "avg_trade_size": ("shares per print, 21d average. MEASURED POSITIVE at "
                       "the longer horizons: bigger prints predicted better, "
                       "the institutional reading, not the retail one. Do not "
                       "confuse it with trade_size_trend, whose t was once "
                       "copied onto it by mistake", "50-500 shares"),
    "turnover": ("21d dollar volume / market cap. How fast the float turns",
                 "0.001-0.10"),
    "range_expansion": ("log(21d / 252d ATR%). Above 0 = wider swings", "-1 to 1"),
    "gap_freq": ("share of the last 63 sessions gapping more than 2% overnight",
                 "0-0.5"),
    "above_200dma": ("percent above the 200-day average", "-0.5 to 1.0"),
    "extension_pct": ("where TODAY sits in this name's OWN history of that "
                      "same distance. 95 = more stretched than 95% of its past",
                      "0-100"),
    "px_vs_rev": ("1y price return minus 1y revenue growth. Positive = price "
                  "outran the business", "-1 to 2"),
    "short_ratio": ("FINRA short volume / total volume, 21d. Short VOLUME, not "
                    "short interest -- days-to-cover is no longer free",
                    "0.2-0.6"),
    "short_surge": ("log(21d / 252d short ratio). Is the pressure unusual FOR "
                    "THIS NAME", "-0.5 to 0.5"),
    # --- dip ---------------------------------------------------------------
    "drawdown": ("percent below its own 1-year closing high. MEASURED "
                 "NEGATIVE: deeper drawdown predicted LOWER returns",
                 "0-90%"),
    "senti_gap": ("inverted 30d sentiment: high = unusually negative news",
                  "-1 to 1"),
    "not_extended": ("100 minus extension_pct: high = far below its own norm",
                     "0-100"),
    "fund_rank": ("percentile of fund_score across the universe", "0-100"),
    "growth_rank": ("percentile of growth_score", "0-100"),
    # --- sentiment ---------------------------------------------------------
    "sent_mean_30d": ("mean article tone over 30 days. The ONLY horizon that "
                      "survived overlap correction, and at h=1 not h=20",
                      "-0.9 to +0.9, median +0.23"),
    "news_count_30d": ("deduped articles in 30 days. 27.6% of raw pairs are "
                       "republications", "2-99, median 6"),
    "news_z": ("article count versus this name's own baseline",
               "-1.2 to 3.5, median 0.1"),
    "severity_max": ("largest measured event severity. Calibrated from actual "
                     "moves, not hand priors", "2-45, median ~17"),
    "sent_delta": ("recent tone minus the longer baseline", "-1 to 1"),
    # --- DuPont ------------------------------------------------------------
    "du_tax_burden": ("net income / pretax. Lower = heavier tax", "0.6-0.9"),
    "du_interest_burden": ("pretax / EBIT. Lower = heavier interest", "0.7-1.0"),
    "du_op_margin": ("EBIT / revenue", "0.05-0.35"),
    "du_asset_turnover": ("revenue / assets", "0.3-2.0"),
    "du_leverage": ("assets / equity. Higher = more levered", "1.5-4.0"),
}

# Labels for the 28 keys ui.METRIC_LABELS does not cover.
EXTRA_LABELS: dict[str, tuple[str, str]] = {
    # --- recency and staleness. See the DPRO case in scores/sentiment.py.
    "sent_age": ("Article age",
                 "trading sessions since the NEWEST article. The number that "
                 "tells you whether a sentiment score is current or a fossil "
                 "-- a 30-session mean says nothing about when its inputs "
                 "arrived. A DIAGNOSTIC, never a signal: it describes the news "
                 "feed, not the company, so it is excluded from the composite "
                 "for the same reason days_since_filing is."),
    "sent_stale": ("Stale",
                   "1 when the newest article is more than 10 sessions old. The "
                   "score is still shown; it is just no longer news."),
    "sent_decay_5d": ("Sentiment 5d, recency-weighted",
                      "as sent_mean_5d but each article is weighted by "
                      "0.5^(age/5 sessions)"),
    "sent_decay_30d": ("Sentiment 30d, recency-weighted",
                       "as sent_mean_30d but weighted by 0.5^(age/5 sessions), "
                       "so a six-week-old story cannot dominate. NOTE: when "
                       "every article in the window is the same age this equals "
                       "the flat mean -- staleness is what sent_age reports."),
    "sent_decay_90d": ("Sentiment 90d, recency-weighted",
                       "as sent_mean_90d, weighted by 0.5^(age/5 sessions)"),

    # --- module 5: combo. Ranks, so 0-100 with 50 as the universe median.
    # The suffix is the EVIDENCE horizon, not a claim about where the score
    # works. `combo_h60` is assembled from h=60-significant metrics and peaks at
    # h=20 -- which is precisely why the old `combo_long` name was replaced.
    # Where each one measures best is stated here and nowhere encoded in a name.
    "combo_h1": ("Combined · 1d evidence",
                 "0-100. Built from metrics that measured significant at a "
                 "ONE-DAY horizon, sign-aligned so higher is better. The only "
                 "score sentiment reaches. FAILED OUT OF SAMPLE: in-sample it "
                 "sat right on the |t|>=2 bar in sample and collapsed out of "
                 "it -- the clearest case here of selection rather than "
                 "signal. Current figures in the out-of-sample block."),
    "combo_h20": ("Combined · 1mo evidence",
                  "0-100. Built from h=20 evidence: mostly profitability, "
                  "valuation and safety. Sentiment does not appear at all, "
                  "because it does not survive 20 days. In-sample it peaks at "
                  "h=20. Out of sample it is MARGINAL and FADES across the "
                  "walk-forward folds -- the best fold-average of the three, "
                  "which says more about the early folds than about today. "
                  "Fold-by-fold figures in the out-of-sample block."),
    "combo_h60": ("Combined · 1q evidence",
                  "0-100. 21 metrics of h=60 evidence -- valuation (7), then "
                  "profitability and balance-sheet safety (5 each), topped by "
                  "z_score and ev_sales. STRONGEST IN SAMPLE, and the one to "
                  "read the out-of-sample block for before using: a single "
                  "2021 split looked convincing, while the walk-forward shows "
                  "it fading as the test window approaches today. Watch "
                  "whether the fold with the MOST training data is still the "
                  "worst -- that pattern is what overfitting or a dead edge "
                  "looks like."),
    "combo_h1_cov": ("Coverage · 1d",
                     "fraction of the admitted metrics this name actually "
                     "has. Below 0.5 there is no score, not a low one."),
    "combo_h20_cov": ("Coverage · 1mo", "as above, for the h=20 score"),
    "combo_h60_cov": ("Coverage · 1q", "as above, for the h=60 score"),
    "combo_h1_n": ("Inputs · 1d",
                   "how many metrics survived admission and de-duplication"),
    "combo_h20_n": ("Inputs · 1mo", "as above, for the h=20 score"),
    "combo_h60_n": ("Inputs · 1q", "as above, for the h=60 score"),
    "th_profitability": ("Theme: profitability",
                         "0-100 within the score: returns on capital and margins"),
    "th_valuation": ("Theme: valuation",
                     "0-100 within the score: what you pay per unit of business"),
    "th_safety": ("Theme: safety",
                  "0-100 within the score: distress risk, leverage, dilution"),
    "th_growth": ("Theme: growth",
                  "0-100 within the score: revenue, earnings, cash-flow trend"),
    "th_efficiency": ("Theme: efficiency",
                      "0-100 within the score: asset turnover and accrual quality"),
    # No `th_sentiment`. Theme sub-scores publish at h=20 and no sentiment
    # metric survives 20 days -- all nine admit at h=1 and nowhere else -- so
    # the series can never exist. It was documented here for a while anyway,
    # which is a reference describing something that does not exist.
    "currency": ("Reporting currency",
                 "the currency this company files in. Non-USD filers are "
                 "scored on the scale-free metrics only -- margins, ROE, "
                 "ROIC, F-score, accruals, turnover, leverage -- because "
                 "those are ratios of two same-currency figures and need no "
                 "conversion. Anything that puts a USD share price next to a "
                 "foreign book (P/E, P/B, EV/EBITDA, Altman Z) is WITHHELD "
                 "rather than converted: a wrong exchange rate produces a "
                 "plausible number, which is worse than none."),
    "reports_usd": ("Reports in USD",
                    "1 if the filings are in US dollars. 0 means the value "
                    "pillar is absent by construction, not missing by "
                    "accident -- see currency. Such a name can never exceed "
                    "fund_cov 0.75, so read its fund_score knowing it rests "
                    "on 3 pillars where a USD filer's rests on 4."),
    "th_attention": ("Theme: attention",
                     "0-100 within the score: volume, trade size, stretch"),

    "has_news": ("Has news", "1 if any article covered this name in the window"),
    "news_coverage": ("News coverage", "fraction of the window the store covers"),
    "sent_delta": ("Sentiment change", "recent tone minus baseline"),
    # THE `_rank` SUFFIX DOES NOT MEAN ONE SCALE, and that is a trap worth
    # stating rather than hiding. Audited 2026-08-14: sentiment's ranks are
    # 0-1 while `dip.fund_rank` and `dip.growth_rank` are 0-100, both under the
    # same suffix. Rescaling the sentiment series would be a monotonic change
    # -- harmless to any IC or ordering -- but it would silently invalidate the
    # stored history against the measured ICs recorded above, so the scale is
    # DOCUMENTED here instead of quietly rewritten.
    "severity_rank": ("Severity rank", "percentile of severity_max (0-1)"),
    "news_count_rank": ("Article count rank",
                        "percentile of article count (0-1)"),
    "sent_rank": ("Sentiment rank", "percentile of sent_mean_30d (0-1; note "
                  "dip's *_rank metrics are 0-100)"),
    "top_event": ("Top event", "highest-severity event class seen"),
    "event_types": ("Event types", "every event class in the window"),
    "top_severity_band": ("Severity band", "band of the top event"),
    "top_headline": ("Top headline", "headline behind the top event"),
    "sent_mean_5d": ("Sentiment 5d", "mean tone over 5 sessions"),
    "sent_net_5d": ("Net sentiment 5d", "positive minus negative, 5d"),
    "news_count_5d": ("Articles 5d", "deduped article count, 5d"),
    "sent_mean_90d": ("Sentiment 90d", "mean tone over 90 sessions"),
    "sent_net_90d": ("Net sentiment 90d", "positive minus negative, 90d"),
    "news_count_90d": ("Articles 90d", "deduped article count, 90d"),
    "has_fundamentals": ("Has fundamentals", "1 if TTM could be computed"),
    "sector": ("Sector", "SIC-derived, from the filing itself"),
    "last_filed": ("Last filing", "date of the most recent 10-K/10-Q"),
    "du_tax_burden": ("DuPont: tax burden", "net income / pretax"),
    "du_interest_burden": ("DuPont: interest burden", "pretax / EBIT"),
    "du_op_margin": ("DuPont: operating margin", "EBIT / revenue"),
    "du_asset_turnover": ("DuPont: asset turnover", "revenue / assets"),
    "du_leverage": ("DuPont: leverage", "assets / equity"),
    "trade_size_trend": ("Print-size trend", "log 21d/252d average print size"),
    "has_hype": ("Has hype score", "1 if enough components were present"),
    "bars_used": ("Bars used", "sessions of history behind the score"),
    "has_dip": ("Has dip score", "1 if it passed the gate and was scored"),
}

# How the two comparison columns are computed. This is #14: they were unexplained
# anywhere in the UI.
COMPARISON_HELP = {
    "vs industry": (
        "Percentile among names in the SAME SECTOR, for this session only. "
        "Needs at least 5 peers or it shows n/a. Sector is SIC-derived from the "
        "company's own filing, not a vendor classification."),
    "vs history": (
        "Percentile of today's value within THIS TICKER'S OWN stored series. "
        "Needs at least 8 stored observations or it shows n/a -- a midpoint "
        "would read as 'perfectly average' when the truth is 'not enough "
        "history'. It answers 'unusual for this company', not 'unusual for the "
        "market'."),
    "n/a": (
        "A hatched bar means NO DATA, which is not the same as ranking last. "
        "A zero-width bar would be indistinguishable from 'worst in sector'."),
}


# Proper display names. Without these the label is derived from the
# description, so `pe` rendered "price / earnings" as BOTH its title and its
# explanation -- the row said the same thing twice and explained nothing.
DISPLAY = {
    "pe": "P/E ratio", "pb": "P/B ratio", "ev_ebitda": "EV / EBITDA",
    "ev_sales": "EV / Sales", "fcf_yield": "FCF yield", "peg": "PEG ratio",
    "roe": "Return on equity", "roa": "Return on assets",
    "roic": "Return on invested capital", "roic_wacc": "ROIC - WACC spread",
    "gpoa": "Gross profit / assets", "eva": "Economic value added",
    "f_score": "Piotroski F-Score", "z_score": "Altman Z-Score",
    "m_score": "Beneish M-Score", "accruals": "Sloan accrual ratio",
    "ccc": "Cash conversion cycle", "current_ratio": "Current ratio",
    "interest_cover": "Interest coverage", "net_debt_ebitda": "Net debt / EBITDA",
    "asset_turnover": "Asset turnover", "op_margin": "Operating margin",
    "rev_growth": "Revenue growth", "eps_growth": "EPS growth",
    "fcf_growth": "FCF growth", "asset_growth": "Asset growth",
    "net_issuance": "Net share issuance", "shareholder_yield": "Shareholder yield",
    "sue": "Earnings surprise (SUE)", "mom_12_1": "12-1 momentum",
    "beta": "Beta vs SPY", "wacc": "Cost of capital (WACC)",
    "mktcap": "Market cap",
}


def direction(metric: str) -> int:
    """+1 high is good, -1 high is bad, 0 unknown.

    Three sources, in order of authority:
      1. `fund_metrics.REGISTRY` -- an accounting convention, not an opinion.
      2. A MEASURED result with |t| >= 2 -- evidence beats convention's absence.
      3. Otherwise 0. Every hype metric lands here, because that module
         deliberately refuses to assume a sign and colouring it would invent a
         claim the code declines to make.
    """
    try:
        import fund_metrics as FM
        spec = FM.REGISTRY.get(metric)
        if spec:
            return int(spec[1])
    except Exception:                                            # noqa: BLE001
        pass
    m = MEASURED.get(metric)
    if m and abs(m[1]) >= 2.0:
        return 1 if m[0] > 0 else -1
    return 0


# Applied at import: the study wins wherever it has an answer.
MEASURED.update(_load_study())


def horizon_curve(metric: str) -> list[tuple[int, float, float]]:
    """[(horizon, ic, t)] on the full universe, ascending. Empty if unstudied."""
    try:
        import study
        df = study.read()
        if df.empty:
            return []
        d = df[(df["metric"] == metric) & (df["size"] == "all")
               & df["t"].notna()].sort_values("horizon")
        return [(int(r["horizon"]), float(r["ic"]), float(r["t"]))
                for _, r in d.iterrows()]
    except Exception:                                            # noqa: BLE001
        return []


def best_horizon(metric: str) -> int | None:
    c = horizon_curve(metric)
    return max(c, key=lambda x: abs(x[2]))[0] if c else None


def size_split(metric: str) -> list[tuple[str, float, float]]:
    """[(bucket, ic, t)] at the metric's best horizon. Answers 'does this only
    work for large caps', which was an explicit open question about the dip
    thesis."""
    h = best_horizon(metric)
    if h is None:
        return []
    try:
        import study
        df = study.read()
        d = df[(df["metric"] == metric) & (df["horizon"] == h)
               & (df["size"] != "all") & df["t"].notna()]
        order = {"large": 0, "mid": 1, "small": 2}
        rows = [(str(r["size"]), float(r["ic"]), float(r["t"]))
                for _, r in d.iterrows()]
        return sorted(rows, key=lambda x: order.get(x[0], 9))
    except Exception:                                            # noqa: BLE001
        return []


def validated(threshold: float = 2.0) -> set[str]:
    """Metrics clearing |t| >= threshold at ANY horizon, from the study.

    Computed, never hand-listed -- the whole reason the earlier 24 numbers went
    stale is that somebody (me) typed them.
    """
    out = set()
    try:
        import study
        df = study.read()
        if df.empty:
            return out
        a = df[(df["size"] == "all") & df["t"].notna()]
        for m, g in a.groupby("metric"):
            if g["t"].abs().max() >= threshold:
                out.add(str(m))
    except Exception:                                            # noqa: BLE001
        pass
    return out


def module_of(metric: str) -> str:
    try:
        import scores
        scores.load_all()
        for m in config.SCORE_MODULES:
            if metric in scores.get(m).metrics():
                return m
    except Exception:                                            # noqa: BLE001
        pass
    return "?"


def entry(metric: str) -> dict:
    """Everything known about one metric, assembled from the existing sources."""
    label, desc = ui.METRIC_LABELS.get(metric, EXTRA_LABELS.get(metric, (None, "")))
    if label is None:
        label = metric.replace("_", " ")
    # A proper title always wins over one derived from the description.
    label = DISPLAY.get(metric, label)
    how, rng = READ.get(metric, ("", ""))
    ic_t = MEASURED.get(metric)
    return {
        "key": metric, "label": label, "measures": desc,
        "how": how, "range": rng,
        "pillar": ui.pillar_of(metric),
        "direction": direction(metric),
        "provenance": metric in PROVENANCE,
        "ic": None if ic_t is None else ic_t[0],
        "t": None if ic_t is None else ic_t[1],
    }


def all_entries() -> dict[str, dict]:
    import scores
    scores.load_all()
    out = {}
    for mod in config.SCORE_MODULES:
        for m in scores.get(mod).metrics():
            e = entry(m)
            e["module"] = mod
            out.setdefault(m, e)
    return out


def tooltip(metric: str) -> str:
    """One-line `title=` text for a column header."""
    e = entry(metric)
    bits = [e["measures"] or e["label"]]
    if e["how"]:
        bits.append(e["how"])
    if e["range"]:
        bits.append(f"typical: {e['range']}")
    if e["t"] is not None:
        bits.append(f"measured t={e['t']:+.2f}"
                    + ("  (NOT significant)" if abs(e["t"]) < 2 else ""))
    elif e["provenance"]:
        bits.append("provenance, not a signal")
    return " — ".join(bits)


# ==================================================================== render
def _horizon_cell(metric: str) -> str:
    """IC across horizons as a tiny bar chart. One glance says whether a signal
    is fast, slow, or absent -- which a single number never could."""
    curve = horizon_curve(metric)
    if not curve:
        return '<span class="muted" style="font-size:11px">&mdash;</span>'
    peak = max(abs(ic) for _h, ic, _t in curve) or 1e-9
    # EVERY cell is the same fixed width and left-aligned, and the bar row and
    # the label row use the SAME 12px slot. The previous version gave each bar
    # `margin-right:3px` including the last one, so the bar row measured 3px
    # wider than the label row; in a right-aligned cell the two rows sat
    # visibly out of step, and the column drifted from row to row because each
    # cell sized to its own content.
    slot, n = 12, len(curve)
    bars = ""
    for i, (h, ic, tv) in enumerate(curve):
        frac = abs(ic) / peak
        col = ("var(--pos)" if tv >= 2 else "var(--neg)" if tv <= -2
               else "var(--muted)")
        hgt = max(3, round(frac * 20))
        up = ic >= 0
        gap = 0 if i == n - 1 else slot - 9      # no trailing gap
        bars += (f'<span title="{h}-day horizon: IC {ic:+.4f}, t {tv:+.2f}" '
                 f'style="display:inline-block;width:9px;height:22px;'
                 f'position:relative;vertical-align:top;margin-right:{gap}px">'
                 f'<i style="position:absolute;{"bottom" if up else "top"}:11px;'
                 f'left:0;width:9px;height:{hgt}px;background:{col};'
                 f'border-radius:2px;display:block"></i></span>')
    labels = "".join(f'<span style="display:inline-block;width:{slot}px;'
                     f'font-size:8px;color:var(--muted);text-align:left">{h}</span>'
                     for h, _i, _t in curve)
    w = slot * n
    return (f'<div style="width:{w}px;white-space:nowrap;'
            f'border-bottom:1px solid var(--line)">{bars}</div>'
            f'<div style="width:{w}px;white-space:nowrap;margin-top:2px">'
            f'{labels}</div>')


def _oos_block() -> str:
    """The out-of-sample results, read from the parquet the runs wrote.

    Rendered rather than typed, for the reason in the module docstring: these
    numbers change whenever the data is corrected or the study re-run, and a
    label quoting last week's figure reads exactly like a measurement.

    Shows every FOLD, never an average. A score that works in 2020 and not in
    2025 has a mean that looks respectable and a present value of nothing --
    which is the specific way this project has already been misled once.
    """
    import pandas as pd
    wf = config.DATA / "_oos_walkforward.parquet"
    single = config.DATA / "_oos.parquet"
    if not wf.exists() and not single.exists():
        return ('<div class="card"><b>Out of sample:</b> not yet run. '
                '<code>python oos.py --walk-forward 4</code></div>')
    out = ['<h2>Out of sample</h2>',
           '<div class="card modblurb">Fitted on earlier dates only, then '
           'graded on dates the fit never saw. Every fold is shown: an average '
           'hides the fold that failed, and a score strong early and weak late '
           'is a regime, not an edge. t is overlap-corrected.</div>']
    if wf.exists():
        try:
            d = pd.read_parquet(wf)
            folds = sorted(d["fold"].unique())
            head = "".join(f"<th>fold {f}<div class='hzsub'>from "
                           f"{str(d[d['fold'] == f]['split'].iloc[0])}</div></th>"
                           for f in folds)
            rows = ""
            for (sc, h), g in d.groupby(["score", "horizon"]):
                cells = ""
                for f in folds:
                    r = g[g["fold"] == f]
                    if r.empty:
                        cells += "<td>&mdash;</td>"; continue
                    t = float(r["t"].iloc[0]); hit = float(r["hit"].iloc[0])
                    col = ("var(--pos)" if t >= 2 else "var(--neg)"
                           if t <= -2 else "var(--muted)")
                    cells += (f'<td><b style="color:{col}">{t:+.2f}</b>'
                              f'<div class="hzsub">{hit:.0%} hit</div></td>')
                rows += (f'<tr><td class="lab"><code>{ui.esc(sc)}</code></td>'
                         f'<td>h={int(h)}</td>{cells}</tr>')
            out.append('<div class="scroll"><table><thead><tr>'
                       '<th class="lab">score</th><th>horizon</th>'
                       f'{head}</tr></thead><tbody>{rows}</tbody></table></div>')
            ts = datetime.fromtimestamp(wf.stat().st_mtime)
            out.append(f'<div class="note">Walk-forward measured '
                       f'{ts:%Y-%m-%d %H:%M}.</div>')
        except Exception as exc:                                 # noqa: BLE001
            out.append(f'<div class="note">walk-forward unreadable: '
                       f'{ui.esc(type(exc).__name__)}</div>')
    return "".join(out)


def _verdict(e: dict) -> str:
    if e["provenance"]:
        return ('<span class="pill skipped" title="data availability, '
                'not a prediction">provenance</span>')
    if e["t"] is None:
        return '<span class="pill never">not tested</span>'
    t = e["t"]
    if abs(t) >= 3:
        cls, txt = "ok", "strong"
    elif abs(t) >= 2:
        cls, txt = "slow", "suggestive"
    else:
        cls, txt = "error", "not significant"
    return (f'<span class="pill {cls}">{txt} t={t:+.2f}</span>')


def build(verbose: bool = True):
    ents = all_entries()
    by_mod: dict[str, list] = {}
    for e in ents.values():
        by_mod.setdefault(e["module"], []).append(e)

    body = ('<div class="banner warn"><b>Read the verdict column first.</b> '
            'A metric marked <i>not tested</i> or <i>not significant</i> has no '
            'evidence behind it and should not drive a decision. '
            '<i>provenance</i> means the column describes data availability, '
            'not the company &mdash; those were excluded from the leaderboard '
            'after ranking above every real metric.</div>')

    body += '<h2>How the comparison columns work</h2><div class="card">'
    for k, v in COMPARISON_HELP.items():
        body += (f'<div class="mrow" style="grid-template-columns:120px 1fr">'
                 f'<div class="name"><b>{ui.esc(k)}</b></div>'
                 f'<div style="text-align:left">{ui.esc(v)}</div></div>')
    body += "</div>"

    for mod in config.SCORE_MODULES:
        rows = sorted(by_mod.get(mod, []), key=lambda e: (e["provenance"],
                                                          e["label"]))
        if not rows:
            continue
        trs = ""
        for e in rows:
            trs += (f'<tr id="m-{ui.esc(e["key"])}">'
                    f'<td class="lab"><b>{ui.esc(e["label"])}</b>'
                    f'<div class="desc"><code>{ui.esc(e["key"])}</code>'
                    + (f' &middot; {ui.esc(e["pillar"])} pillar'
                       if e["pillar"] else "")
                    + f'</div></td>'
                    f'<td class="lab">{ui.esc(e["measures"])}</td>'
                    f'<td class="lab">{ui.esc(e["how"])}</td>'
                    f'<td class="lab muted">{ui.esc(e["range"])}</td>'
                    f'<td class="hz">{_horizon_cell(e["key"])}</td>'
                    f'<td>{_verdict(e)}</td></tr>')
        legend = ('<div class="hzkey">'
                  '<span><i style="background:var(--pos)"></i>higher values did better (significant)</span>'
                  '<span><i style="background:var(--neg)"></i>higher values did worse (significant)</span>'
                  '<span><i style="background:var(--muted)"></i>not distinguishable from chance</span>'
                  '<span>bar height = strength &middot; above the line = positive</span></div>')
        blurb = MODULE_DOC.get(mod, "")
        body += (f'<h2>{ui.esc(mod)} &middot; {len(rows)} metrics</h2>'
                 + (f'<div class="card modblurb">{ui.esc(blurb)}</div>'
                    if blurb else "")
                 + legend
                 + f'<div class="scroll"><table><thead><tr>'
                 f'<th class="lab">Metric</th><th class="lab">What it measures</th>'
                 f'<th class="lab">How to read it</th>'
                 f'<th class="lab">Typical range</th>'
                 f'<th class="hz" title="How well this metric ranked stocks, '
                 f'measured at four holding periods. Bar up = higher values '
                 f'did better; down = higher values did worse. Taller = '
                 f'stronger. Green or red = statistically significant '
                 f'(|t| >= 2); grey = indistinguishable from chance.">'
                 f'Predictive strength'
                 f'<div class="hzsub">by holding period &middot; '
                 f'1 / 5 / 20 / 60 days</div></th><th>Verdict</th>'
                 f'</tr></thead><tbody>{trs}</tbody></table></div>')

    body += _oos_block()
    body += ('<div class="note">Every explanation here is generated from the '
             'same registry the tooltips use, and a selftest fails if a module '
             'emits a metric with no entry &mdash; so this page cannot drift '
             'from the code. Measured figures are h=20 against a random '
             'control; see PROJECT_LOG.md for the full tables.<br>'
             f'Built {datetime.now():%Y-%m-%d %H:%M:%S}.</div>')

    # The strength column is a chart, so it is LEFT-aligned and fixed-width
    # like the text columns. Right-aligning a chart makes every row start at a
    # different x and the column reads as noise.
    hz_css = ("<style>"
              "th.hz,td.hz{text-align:left;white-space:nowrap;"
              "width:1%;padding-right:18px}"
              ".hzsub{font-weight:400;font-size:9px;color:var(--muted);"
              "margin-top:2px;letter-spacing:.02em}"
              ".hzkey{display:flex;gap:16px;flex-wrap:wrap;align-items:center;"
              "font-size:11px;color:var(--muted);margin:2px 0 14px}"
              ".hzkey i{display:inline-block;width:9px;height:11px;"
              "border-radius:2px;margin-right:5px;vertical-align:-1px}"
              "</style>")
    html = ui.page("Metric dictionary", body,
                   subtitle=f"{len(ents)} metrics across "
                            f"{len(config.SCORE_MODULES)} modules",
                   active="metrics", depth=0, head=hz_css)
    config.REPORTS.mkdir(parents=True, exist_ok=True)
    p = config.REPORTS / "metrics.html"
    p.write_text(html, encoding="utf-8")
    if verbose:
        tested = sum(1 for e in ents.values() if e["t"] is not None)
        print(f"  metrics: {p}  ({len(ents)} entries, {tested} with a "
              f"measured result)")
    return p


def check_ranges(verbose: bool = True) -> list[str]:
    """Compare each documented range against the LIVE distribution.

    `severity_max` was documented as "0-3" when its median is 16.85 -- a range
    that is wrong is worse than no range, because the reader trusts it to decide
    whether a value is extreme. This reads the actual stored values and reports
    anything whose median sits outside its own stated range.
    """
    import re

    import pandas as pd
    import scores
    scores.load_all()
    bad = []
    for mod in config.SCORE_MODULES:
        sess = scores.sessions_stored(mod)
        if not sess:
            continue
        df = scores.read(module=mod, start=sess[-1], end=sess[-1])
        for metric, (_how, rng) in READ.items():
            # "0-100" must parse as a RANGE, not as [0, -100]. A bare hyphen
            # between two digits is a separator; a hyphen before a digit at the
            # start of a token is a minus. Getting this wrong made the checker
            # flag eight correct ranges as broken on its first run.
            # Entries that state THRESHOLDS ("<1.81 distress, >2.99 safe") are
            # not ranges and cannot be checked this way.
            if "<" in rng or ">" in rng or "median" in rng:
                continue
            norm = re.sub(r"(?<=\d)\s*-\s*(?=\d)", " to ", rng)
            nums = [float(x) for x in re.findall(r"-?\d+\.?\d*", norm)]
            if len(nums) < 2 or metric not in set(df["metric"]):
                continue
            v = pd.to_numeric(df[df["metric"] == metric]["value"],
                              errors="coerce").dropna()
            if v.empty:
                continue
            lo, hi = min(nums[:2]), max(nums[:2])
            med = float(v.median())
            if not (lo <= med <= hi):
                bad.append(f"{metric}: documented {rng}, actual median {med:.2f}")
    if verbose:
        print(f"  [ranges] {len(bad)} documented range(s) contradict the data"
              + ("" if not bad else ":"))
        for b in bad:
            print(f"      {b}")
    return bad


def selftest(verbose: bool = True) -> None:
    """THE DRIFT GUARD. Add or remove a metric and this fails until documented."""
    import scores
    scores.load_all()

    live = set()
    for mod in config.SCORE_MODULES:
        live |= set(scores.get(mod).metrics())

    documented = set(ui.METRIC_LABELS) | set(EXTRA_LABELS)
    missing = sorted(live - documented)
    assert not missing, (
        f"{len(missing)} metric(s) emitted with no dictionary entry: "
        f"{missing[:8]}. Add them to metrics_doc.EXTRA_LABELS (or "
        f"ui.METRIC_LABELS) -- an undocumented column on a dashboard is a "
        f"number nobody can act on.")

    stale = sorted(k for k in EXTRA_LABELS if k not in live)
    assert not stale, (
        f"EXTRA_LABELS documents metric(s) no module emits: {stale}. "
        f"Remove them, or the reference describes something that does not "
        f"exist.")

    stale_read = sorted(k for k in READ if k not in live)
    assert not stale_read, f"READ documents dead metric(s): {stale_read}"
    stale_meas = sorted(k for k in MEASURED if k not in live)
    assert not stale_meas, f"MEASURED references dead metric(s): {stale_meas}"

    # PROVENANCE here must agree with factor_lab's exclusion list, or the page
    # and the leaderboard would disagree about what counts as a signal.
    import factor_lab
    assert PROVENANCE == set(factor_lab.NON_SIGNAL), (
        f"PROVENANCE and factor_lab.NON_SIGNAL disagree: "
        f"{PROVENANCE ^ set(factor_lab.NON_SIGNAL)}")

    # The two claims the UI must never lose: hype is a magnitude, and an
    # unvalidated score says so on its own tooltip.
    assert "not a direction" in tooltip("hype_score").lower()
    assert "not significant" in tooltip("dip_score").lower(), tooltip("dip_score")

    bad_ranges = check_ranges(verbose=False)
    assert not bad_ranges, (
        f"documented range(s) contradict the stored data: {bad_ranges}. "
        f"A wrong range is worse than none -- the reader trusts it to judge "
        f"whether a value is extreme.")

    if verbose:
        covered = len(live)
        with_read = len([m for m in live if m in READ])
        print(f"  [metrics_doc] {covered} metrics documented, {with_read} with "
              f"how-to-read, {len(MEASURED)} with a measured result")


def main() -> int:
    ap = argparse.ArgumentParser(description="Metric dictionary.")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--show", metavar="METRIC")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
    elif a.show:
        e = entry(a.show)
        print(f"\n  {e['label']}  ({e['key']}, {module_of(a.show)})")
        for k in ("measures", "how", "range"):
            if e[k]:
                print(f"    {k:9s} {e[k]}")
        if e["t"] is not None:
            print(f"    measured  IC {e['ic']:+.4f}, t {e['t']:+.2f}")
        print()
    else:
        build()
    return 0


if __name__ == "__main__":
    sys.exit(main())
