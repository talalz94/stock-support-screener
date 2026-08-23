"""
Ranking strategies: one declaration each, one engine, no duplicated logic.

    python strategies.py                 list the registry with coverage
    python strategies.py --key growth    show one strategy's top names
    python strategies.py --selftest

WHY THIS IS A REGISTRY AND NOT A SCORE
--------------------------------------
There is no single right way to rank a stock. "Highest revenue growth", "cheap
and improving", "quality compounders" are different questions, and a screener
that answers only one of them is a screener for somebody else. So a strategy is
DATA -- a name, some inputs, some weights -- and adding one is appending an
entry to `STRATEGIES`. Everything downstream (the page tabs, the columns, the
coverage counts, the checks) is derived from that list, so nothing has to be
edited in five places.

THE CONTRACT, AND IT IS THE WHOLE POINT
---------------------------------------
**A stock missing any input a strategy needs is NOT LISTED for that strategy.**
It is never imputed, never renormalised over the inputs that happen to exist.

That rule costs coverage and buys the only thing that matters here: every row a
strategy shows was ranked on the full set of things that strategy claims to
measure. Renormalising would let a name scored on one of three inputs sit
beside a name scored on three and look comparable, which is exactly the kind of
plausible-looking wrongness this project has paid for repeatedly -- COLL's
EBITDA read $4M against a real $68M because a missing leg was treated as zero.

`coverage()` reports what was dropped so the loss is visible rather than
silent: a strategy that can only rank 1,500 of 3,499 names says so on its tab.

WHY RANKS AND NOT Z-SCORES
--------------------------
Same reason `scores/combo.py:308-310` gives: XBRL carries unit errors -- a
filer tagging shares in thousands, a proxy reporting dollars as thousands --
and one such row would dominate any z-score. A percentile rank cannot be
dragged by an outlier, only ordered behind it.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

import config

config.safe_console()


@dataclass(frozen=True)
class Strategy:
    """One ranking model. `inputs` is (metric, direction, weight).

    `direction` is +1 when higher is better, -1 when lower is. It is stated
    here rather than read from `fund_metrics.REGISTRY` because a strategy may
    deliberately invert a metric -- a deep-value screen wants a LOW P/B, a
    momentum screen may want a high one -- and the registry's direction is an
    accounting convention, not this strategy's opinion.

    `weight` is relative within the strategy; the engine normalises.
    """

    key: str
    title: str
    inputs: tuple[tuple[str, int, float], ...]
    doc: str = ""
    # WHAT THE MEASUREMENT SAYS, shown on the tab. Empty means never measured.
    #
    # A strategy that has not been shown to predict anything is an ORGANISING
    # PRINCIPLE, not a signal, and the page has to say which it is. Four
    # confident-looking tabs implying four working models would be the most
    # expensive kind of wrong this project can produce.
    evidence: str = ""

    @property
    def metrics(self) -> tuple[str, ...]:
        return tuple(m for m, _d, _w in self.inputs)

    @property
    def column(self) -> str:
        """The metric name this strategy produces. `s_` prefixed so it can
        never collide with a real fundamental metric."""
        return f"s_{self.key}"


# ---------------------------------------------------------------------------
# The registry. Adding a strategy is appending an entry -- nothing else.
# ---------------------------------------------------------------------------
STRATEGIES: tuple[Strategy, ...] = (
    Strategy(
        key="growth",
        title="Growth",
        inputs=(("rev_growth", +1, 1.0),
                ("rev_growth_q", +1, 0.5),
                ("eps_growth", +1, 1.0)),
        doc="Revenue and earnings growing, with the sequential rate as a "
            "tiebreak so a name whose growth has already rolled over ranks "
            "behind one still accelerating.",
        evidence="UNMEASURED -- rev_growth_q has no history yet (added "
                 "2026-08-23), so there is nothing to measure over."),
    Strategy(
        key="quality_growth",
        title="Quality growth",
        inputs=(("rev_growth", +1, 1.0),
                ("gross_margin_chg", +1, 1.0),
                ("roic", +1, 1.0),
                ("f_score", +1, 0.5)),
        doc="Growing revenue AND expanding margins AND earning a real return "
            "on capital -- growth that is not being bought with margin.",
        evidence="UNMEASURED -- gross_margin_chg has no history yet (added "
                 "2026-08-23), so there is nothing to measure over."),
    Strategy(
        key="value",
        title="Value",
        inputs=(("pe", -1, 1.0),
                ("ev_ebitda", -1, 1.0),
                ("fcf_yield", +1, 1.0)),
        doc="Cheap on earnings, on enterprise value and on cash. Note this "
            "needs FX-sensitive metrics, so non-USD filers are excluded.",
        evidence="NO EDGE MEASURED. 73 sessions to 2026-08-21, ~871 names: "
                 "IC -0.0006 (t=-0.01) at h=20, IC -0.0097 (t=-0.13) at h=60. "
                 "Indistinguishable from a random ranking of the same names."),
    Strategy(
        key="quality",
        title="Quality",
        inputs=(("roic", +1, 1.0),
                ("gpoa", +1, 1.0),
                ("f_score", +1, 1.0),
                ("interest_cover", +1, 0.5)),
        doc="High return on capital, gross profitability and accounting "
            "quality, with enough interest cover to survive a bad year.",
        evidence="NO EDGE MEASURED. 73 sessions to 2026-08-21, ~949 names: "
                 "IC +0.0186 (t=+0.37) at h=20, hit 61.7%. It beats the "
                 "random control but t=0.37 is far below the |t|>=2 bar, so "
                 "the hit rate is not evidence of anything."),
)


def get(key: str) -> Strategy:
    for s in STRATEGIES:
        if s.key == key:
            return s
    raise KeyError(f"no strategy {key!r}. Known: "
                   f"{', '.join(s.key for s in STRATEGIES)}")


# ---------------------------------------------------------------------------
# The engine. One function, called by explore.py and scores/strategy.py alike.
# ---------------------------------------------------------------------------
def rank(wide: pd.DataFrame, strat: Strategy) -> pd.Series:
    """0-100 for every stock with ALL of `strat`'s inputs; NaN otherwise.

    100 is always the best end, whatever each input's direction, so the column
    is readable without knowing which way its parts point.

    `wide` is ticker-indexed with one column per metric.
    """
    present = [m for m in strat.metrics if m in wide.columns]
    if not present:
        return pd.Series(np.nan, index=wide.index, dtype="float64")

    ranks, weights = {}, {}
    for metric, direction, weight in strat.inputs:
        if metric not in wide.columns:
            # A declared input that the store does not carry means NOBODY can
            # be ranked -- returning a score over the remaining inputs would
            # silently answer a different question than the one declared.
            return pd.Series(np.nan, index=wide.index, dtype="float64")
        v = pd.to_numeric(wide[metric], errors="coerce")
        r = v.rank(pct=True) * 100.0
        ranks[metric] = r if direction >= 0 else 100.0 - r
        weights[metric] = float(weight)

    R = pd.DataFrame(ranks, index=wide.index)
    # ALL-OR-NOTHING. This single line is the contract: one missing input and
    # the stock is not ranked. `min_count=len(R.columns)` would give the same
    # result via a sum, but stating it as a mask makes the intent unmissable.
    complete = R.notna().all(axis=1)

    w = pd.Series(weights)
    total = (R * w).sum(axis=1) / w.sum()
    return total.where(complete)


def coverage(wide: pd.DataFrame, strat: Strategy) -> dict:
    """What this strategy could and could not rank. Shown on its tab."""
    n = int(len(wide))
    scored = int(rank(wide, strat).notna().sum())
    missing = {}
    for metric in strat.metrics:
        if metric not in wide.columns:
            missing[metric] = n
        else:
            missing[metric] = int(pd.to_numeric(
                wide[metric], errors="coerce").isna().sum())
    return {"key": strat.key, "title": strat.title, "universe": n,
            "ranked": scored, "dropped": n - scored, "missing_by_metric": missing}


def add_columns(wide: pd.DataFrame,
                strategies: Iterable[Strategy] | None = None) -> pd.DataFrame:
    """Append one column per strategy. This is why a strategy needs no new
    page: it becomes an ordinary metric, and the table's existing sorting,
    filtering and column chooser work on it unchanged."""
    out = wide.copy()
    for s in (strategies or STRATEGIES):
        out[s.column] = rank(wide, s)
    return out


# ---------------------------------------------------------------------------
def selftest(verbose: bool = True) -> None:
    fails = []

    # 1. every declared input is a real metric somewhere. A typo would
    #    otherwise produce a silently empty column -- the worst outcome,
    #    because an empty ranking looks like "nothing qualified".
    known = set()
    try:
        import fund_metrics as FM
        known |= set(FM.REGISTRY)
    except Exception as exc:                                     # noqa: BLE001
        fails.append(f"cannot import fund_metrics: {exc!r}")
    try:
        import scores
        scores.load_all()
        for m in config.SCORE_MODULES:
            try:
                known |= set(scores.get(m).metrics())
            except Exception:                                    # noqa: BLE001
                pass
    except Exception:                                            # noqa: BLE001
        pass
    for s in STRATEGIES:
        for metric in s.metrics:
            if known and metric not in known:
                fails.append(f"{s.key}: input {metric!r} is not a known metric")

    # 2. keys unique, weights positive
    keys = [s.key for s in STRATEGIES]
    if len(keys) != len(set(keys)):
        fails.append("duplicate strategy keys")
    for s in STRATEGIES:
        if not s.inputs:
            fails.append(f"{s.key}: no inputs")
        for m, d, w in s.inputs:
            if d not in (1, -1):
                fails.append(f"{s.key}: {m} direction must be +1 or -1")
            if w <= 0:
                fails.append(f"{s.key}: {m} weight must be positive")

    # 3. THE CONTRACT. A stock missing one input must not be ranked. This is
    #    the assertion that stops a future refactor quietly reintroducing
    #    renormalisation, which would make partial rows look comparable.
    demo = pd.DataFrame(
        {"a": [1.0, 2.0, 3.0, 4.0], "b": [4.0, 3.0, 2.0, np.nan]},
        index=["W", "X", "Y", "Z"])
    st = Strategy(key="t", title="t", inputs=(("a", +1, 1.0), ("b", +1, 1.0)))
    got = rank(demo, st)
    if pd.notna(got.get("Z")):
        fails.append("a stock missing an input was ranked -- contract broken")
    if int(got.notna().sum()) != 3:
        fails.append(f"expected 3 ranked, got {int(got.notna().sum())}")

    # 4. direction actually inverts
    hi = Strategy(key="h", title="h", inputs=(("a", +1, 1.0),))
    lo = Strategy(key="l", title="l", inputs=(("a", -1, 1.0),))
    if not rank(demo, hi)["Y"] > rank(demo, hi)["W"]:
        fails.append("+1 direction did not rank higher-is-better")
    if not rank(demo, lo)["W"] > rank(demo, lo)["Y"]:
        fails.append("-1 direction did not invert")

    # 5. a missing COLUMN kills the whole strategy rather than scoring on the
    #    rest -- otherwise the column silently answers a different question
    gone = Strategy(key="g", title="g",
                    inputs=(("a", +1, 1.0), ("nope", +1, 1.0)))
    if rank(demo, gone).notna().any():
        fails.append("a strategy with an absent input column still ranked")

    if fails:
        raise AssertionError("strategies selftest FAILED:\n  "
                             + "\n  ".join(fails))
    if verbose:
        print(f"strategies selftest OK ({len(STRATEGIES)} strategies, "
              f"{sum(len(s.inputs) for s in STRATEGIES)} inputs)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Ranking strategies.")
    ap.add_argument("--key")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--asof")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        selftest()
        return 0

    import calendar_us
    import explore
    asof = a.asof or calendar_us.last_closed_session()
    # Every metric any strategy names, so a strategy is never judged on a
    # column the caller happened not to ask for.
    need = sorted({m for st in STRATEGIES for m in st.metrics})
    # `explore.collect` returns a TICKER-INDEXED frame -- its pivot uses
    # index="ticker" and never resets -- so it is used as-is.
    wide, _prov = explore.collect(need, asof)
    if wide.empty:
        print("no scored data")
        return 1

    todo = [get(a.key)] if a.key else list(STRATEGIES)
    for s in todo:
        cov = coverage(wide, s)
        print(f"\n{s.title}  [{s.key}]")
        print(f"  {s.doc}")
        print(f"  ranked {cov['ranked']:,} of {cov['universe']:,} "
              f"({cov['dropped']:,} missing an input)")
        thin = sorted(cov["missing_by_metric"].items(),
                      key=lambda kv: -kv[1])[:3]
        print("  most-missing inputs: "
              + ", ".join(f"{m} ({n:,})" for m, n in thin))
        if a.key:
            r = rank(wide, s).dropna().sort_values(ascending=False)
            print(r.head(a.top).round(1).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
