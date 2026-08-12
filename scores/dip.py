"""
Score module 4: the dip thesis.

    "If fundamentals are strong and historical growth is strong, and price is
     down for a reason that is not the business -- weak sentiment, seasonality,
     a sector drawdown -- then buying that dip should be an opportunity."

THIS IS A CONDITIONAL, NOT A BLEND, AND THAT IS THE WHOLE DESIGN
------------------------------------------------------------------
The obvious implementation is a weighted average of "good fundamentals" and "big
drawdown". That implementation is wrong, and wrong in a way that is expensive:
averaging lets a company with terrible fundamentals and a catastrophic drawdown
score highly, because a huge dip compensates for a weak balance sheet. Those are
exactly the falling knives the thesis is trying to avoid.

So the structure is a GATE followed by a RANK:

    1. gate    fund_score must be in the top DIP_QUALITY_PCT of the universe.
               Below the gate there is NO dip_score at all -- not a low one.
    2. rank    within the survivors, rank by how depressed the price is.

A name that fails the gate emits `dip_gate = 0` and no `dip_score`, so "did not
qualify" and "qualified but ranked low" are different states in the data. That
distinction is the project's standing rule applied to a signal.

WHY EACH DEPRESSION COMPONENT IS THERE
----------------------------------------
`drawdown`      how far below its own 1-year high. The plain reading of "down".
`senti_gap`     inverted 30-day sentiment. The user's "price is down BECAUSE of
                bad sentiment" leg. Sentiment's only overlap-corrected signal is
                at h=1 (IC +0.0158, t=3.29) and it is a REVERSAL at the extremes
                -- the 20-day quantile spread is negative -- which is precisely
                the shape this thesis needs.
`not_extended`  inverted `extension_pct` from the hype module. This is the
                user's own observation that "a good fundamental stock at a
                record high is less exciting", made measurable.

THE USER'S OWN CAVEAT IS THE RIGHT ONE AND IS NOT ASSUMED AWAY
----------------------------------------------------------------
They said this may only work for large caps with long histories. `mktcap` is
emitted alongside so `factor_lab` can slice by size -- if the effect only exists
in the top decile, that is a finding, not a failure, and it must be measured
rather than asserted in either direction.

**NOTHING HERE HAS BEEN MEASURED.** `dip_score` is a hypothesis with a defensible
structure. Until `factor_lab --module dip --leaderboard` reports an IC and a
t-stat against the random control, it earns no weight anywhere and must not be
traded on. The prior is reasonable -- this is a value/quality/short-reversal
interaction, a documented family -- but this project has twice found a
reasonable prior measured backwards.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import config
import store
from scores import register

NAME = "dip"

DEPRESSION = ("drawdown", "senti_gap", "not_extended")


class DipModule:
    name = NAME

    def metrics(self) -> list[str]:
        return ["dip_gate", "dip_score", "dip_cov", "fund_rank",
                "drawdown", "senti_gap", "not_extended",
                "growth_rank", "mktcap", "has_dip"]

    def compute(self, asof: str, tickers: list[str] | None = None,
                allow_partial: bool = False) -> pd.DataFrame:
        empty = pd.DataFrame(columns=["ticker", "metric", "value", "label"])

        prior = _sibling_scores(asof, tickers)
        if prior.empty:
            return empty

        # --- price depression, computed here rather than borrowed -----------
        dd = _drawdown(asof, prior.index.tolist())
        feats = pd.DataFrame(index=prior.index)
        feats["drawdown"] = dd

        # inverted sentiment: LOW sentiment scores HIGH on "depressed"
        if "sent_mean_30d" in prior.columns:
            feats["senti_gap"] = -pd.to_numeric(prior["sent_mean_30d"],
                                                errors="coerce")
        # inverted extension: far BELOW its own usual stretch scores HIGH
        if "extension_pct" in prior.columns:
            feats["not_extended"] = 100.0 - pd.to_numeric(
                prior["extension_pct"], errors="coerce")

        fund = pd.to_numeric(prior.get("fund_score"), errors="coerce")
        growth = pd.to_numeric(prior.get("growth_score"), errors="coerce")
        if fund is None or fund.notna().sum() < 20:
            # Without a fundamental score there is no gate, and without a gate
            # this module is just "buy whatever fell most" -- which is the
            # failure mode it exists to avoid. Emit nothing.
            return empty

        feats["fund_rank"] = fund.rank(pct=True) * 100.0
        if growth is not None:
            feats["growth_rank"] = growth.rank(pct=True) * 100.0
        feats["mktcap"] = pd.to_numeric(prior.get("mktcap"), errors="coerce")

        # --- the gate --------------------------------------------------------
        gate = feats["fund_rank"] >= (100.0 - config.DIP_QUALITY_PCT)
        if config.DIP_REQUIRE_GROWTH and "growth_rank" in feats.columns:
            gate &= feats["growth_rank"] >= (100.0 - config.DIP_GROWTH_PCT)

        # --- rank the survivors on depression --------------------------------
        # Ranked WITHIN the gated set, not across everything. Ranking across the
        # whole universe then filtering would make the score depend on names
        # that were never candidates.
        present = [c for c in DEPRESSION if c in feats.columns]
        sub = feats.loc[gate, present]
        cov = sub.notna().sum(axis=1) / max(len(DEPRESSION), 1)
        ranked = sub.rank(pct=True) * 100.0
        score = ranked.mean(axis=1, skipna=True).where(
            cov >= config.DIP_MIN_COVERAGE)

        rows: list[dict] = []
        for t in feats.index:
            rows.append({"ticker": t, "metric": "dip_gate",
                         "value": 1.0 if bool(gate.get(t, False)) else 0.0,
                         "label": None})
        for col in ("fund_rank", "growth_rank", "mktcap", *present):
            if col not in feats.columns:
                continue
            v = pd.to_numeric(feats[col], errors="coerce")
            for t, x in v[v.notna() & np.isfinite(v)].items():
                rows.append({"ticker": t, "metric": col, "value": float(x),
                             "label": None})
        for t, x in cov.items():
            rows.append({"ticker": t, "metric": "dip_cov", "value": float(x),
                         "label": None})
        for t, x in score[score.notna()].items():
            rows.append({"ticker": t, "metric": "dip_score", "value": float(x),
                         "label": _band(x)})
            rows.append({"ticker": t, "metric": "has_dip", "value": 1.0,
                         "label": None})
        return pd.DataFrame(rows)

    def selftest(self) -> None:
        _selftest()


# ================================================================== helpers
def _sibling_scores(asof: str, tickers: list[str] | None) -> pd.DataFrame:
    """Wide frame of the inputs this module needs, from the tidy table.

    Reads only sessions <= `asof`, so it stays point-in-time. It depends on the
    other modules having run for that session -- which is why the orchestrator
    lists `dip` after them.
    """
    import scores
    scores.load_all()
    want = {"fund_score", "growth_score", "mktcap", "sent_mean_30d",
            "extension_pct"}
    frames = []
    for mod in ("fundamental", "sentiment", "hype"):
        try:
            sess = [s for s in scores.sessions_stored(mod) if s <= asof]
            if not sess:
                continue
            df = scores.read(module=mod, start=sess[-1], end=sess[-1],
                             tickers=tickers)
            if df is None or df.empty:
                continue
            df = df[df["metric"].isin(want)]
            if df.empty:
                continue
            frames.append(df.pivot_table(index="ticker", columns="metric",
                                         values="value", aggfunc="last"))
        except Exception:                                        # noqa: BLE001
            continue
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, axis=1)
    return out.loc[:, ~out.columns.duplicated()]


def _drawdown(asof: str, tickers: list[str]) -> pd.Series:
    """Percent below the trailing 1-year closing high. Positive = further down."""
    start = (pd.Timestamp(asof) - pd.DateOffset(days=400)).strftime("%Y-%m-%d")
    px = store.read("1d", start=start, end=asof, tickers=tickers,
                    columns=["ticker", "date", "close"])
    if px.empty:
        return pd.Series(dtype="float64")
    piv = px.pivot_table(index="date", columns="ticker", values="close")
    hi = piv.max()
    last = piv.iloc[-1]
    return ((hi - last) / hi.replace(0, np.nan)) * 100.0


def _band(v: float) -> str:
    if v >= 85:
        return "DEEP"
    if v >= 60:
        return "MODERATE"
    return "SHALLOW"


MODULE = register(DipModule())


# ================================================================= selftest
def _selftest() -> None:
    declared = set(MODULE.metrics())
    for c in DEPRESSION:
        assert c in declared, f"metrics() omits {c!r}"
    assert {"dip_gate", "dip_score", "dip_cov"} <= declared

    assert 0 < config.DIP_QUALITY_PCT <= 100
    assert _band(90) == "DEEP" and _band(70) == "MODERATE" and _band(10) == "SHALLOW"

    # THE STRUCTURAL ASSERTION. A falling knife -- bottom-decile fundamentals,
    # catastrophic drawdown -- must NOT produce a dip_score. If this ever passes
    # a score through, the gate has been turned back into a blend and the module
    # is recommending exactly what it was built to exclude.
    idx = [f"T{i}" for i in range(100)]
    fund = pd.Series(np.linspace(0, 100, 100), index=idx)
    qrank = fund.rank(pct=True) * 100
    gate = qrank >= (100.0 - config.DIP_QUALITY_PCT)
    assert not gate.iloc[0], "worst-fundamental name must fail the gate"
    assert gate.iloc[-1], "best-fundamental name must pass the gate"
    n_gated = int(gate.sum())
    assert abs(n_gated - config.DIP_QUALITY_PCT) <= 2, (
        f"gate let {n_gated} of 100 through, expected ~{config.DIP_QUALITY_PCT}")

    # Ranking must happen WITHIN the gated set: the best-quality survivor with
    # the deepest drawdown should top the list, not a non-survivor.
    dd = pd.Series(np.r_[np.full(90, 5.0), np.full(10, 60.0)], index=idx)
    sub = pd.DataFrame({"drawdown": dd})[gate]
    r = sub.rank(pct=True) * 100
    assert r["drawdown"].idxmax() in sub.index

    print(f"  [dip] gate=top {config.DIP_QUALITY_PCT}% fundamentals, "
          f"{len(DEPRESSION)} depression components; falling-knife guard OK")
