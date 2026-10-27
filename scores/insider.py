"""
Score module 6: insider and House buying, as measured signals per stock.

WHAT IT EMITS. Only the readings phase 2 measured (insider_study.py, congress_study.py),
one number each, point in time at `asof`, for every tradeable stock:

    buyers_30d        directors/officers who bought in the open market (filings, last 30 days)
    value_30d         dollars they bought
    rare_buy_30d      1 = one of them made a first purchase in this company in 2+ years
    small_buy_30d     1 = $10K+ bought and the stock is in the bottom two liquidity fifths
    ceo_cfo_buy_30d   1 = the CEO or CFO bought ($10K+)
    house_buy_60d     House members who bought the stock (reports filed, last 60 days)
    house_big_buy_60d 1 = a member reported a $50,001+ purchase

The flags carry the study's own thresholds (each needs $10K+ in the window) and come
from insider_signals, the derivation every page and badge also reads.

ZERO MEANS "NOBODY BOUGHT", AND ONLY WHERE THAT IS KNOWABLE. A company that files no
Form 4s at all (a foreign private issuer is exempt from Section 16) would read 0 for
"no insider bought" when the truth is "no insider reports here". So the insider
metrics are BLANK for a ticker whose company has filed no Form 4 of any kind in the 3
years before `asof`, and 0 only where filings exist and none was a purchase. The House
metrics are 0 for every stock: any member may buy any of them.

NOT A COMPOSITE. Nothing here is weighted by hand. The factor study (study.py) measures
each metric like any other, and combo admits one only if it clears the same bar as every
other metric (|t| >= 2, beats the random control, >= 100 dates). A combined insider
score exists only if it survived the walk-forward in insider_score_wf.py; see
`insider_score` below.

COMBINED: `insider_score` = how many of the walk-forward's chosen flags hold (chosen on
2016-09..2021-06, tested on 2021-07..2026; insider_score_wf.py). Emitted only while
data/_insider_score_wf.json reads PASS; a FAIL there and the metric simply stops.
The same file's `vs_baseline` says whether it beat plain insider buying over the test
span -- on 2026-10-27 it did NOT (the test years lifted almost every rule); every page
that shows the score says so.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

import config
from scores import register

NAME = "insider"
COVER_YEARS = 3                      # a company "files Form 4s" if it did in the last 3 years
METRICS = ["buyers_30d", "value_30d", "rare_buy_30d", "small_buy_30d", "ceo_cfo_buy_30d",
           "house_buy_60d", "house_big_buy_60d", "insider_score"]
# The walk-forward's flags, by the metric that carries each.
_FLAG_METRIC = {"small": "small_buy_30d", "rare": "rare_buy_30d", "ceo_cfo": "ceo_cfo_buy_30d"}

_COVER: dict = {}


def _filer_quarters() -> pd.DataFrame:
    """(issuer_cik, quarter) for every quarter a company had ANY Form 4 filed (any code)."""
    if "fq" not in _COVER:
        import insider as INS
        parts = []
        for d in (INS.DS_DIR, INS.ED_DIR):
            if not d.exists():
                continue
            for p in sorted(d.glob("*.parquet")):
                c = pd.read_parquet(p, columns=["issuer_cik"])["issuer_cik"]
                parts.append(pd.DataFrame({"cik": pd.to_numeric(c, errors="coerce").dropna().astype("int64").unique(),
                                           "q": p.stem}))
        _COVER["fq"] = (pd.concat(parts, ignore_index=True).drop_duplicates()
                        if parts else pd.DataFrame(columns=["cik", "q"]))
    return _COVER["fq"]


def _ticker_ciks() -> dict[str, set[int]]:
    if "tc" not in _COVER:
        import insider as INS
        out: dict[str, set[int]] = {}
        for c, ts in INS._ticker_map().items():
            for t in ts:
                out.setdefault(t, set()).add(int(c))
        _COVER["tc"] = out
    return _COVER["tc"]


def covered(asof: str, tickers: list[str]) -> set[str]:
    """Tickers whose company had a Form 4 (any kind) filed in the COVER_YEARS before asof."""
    import insider as INS
    fq = _filer_quarters()
    lo = INS._quarter_of((date.fromisoformat(asof) - timedelta(days=365 * COVER_YEARS)).isoformat())
    hi = INS._quarter_of(asof)
    ciks = set(fq.cik[(fq.q >= lo) & (fq.q <= hi)])
    tc = _ticker_ciks()
    return {t for t in tickers if tc.get(t, set()) & ciks}


def _wf() -> tuple[bool, list[str]]:
    if "wf" not in _COVER:
        import insider_score_wf
        _COVER["wf"] = insider_score_wf.passed()
    return _COVER["wf"]


class InsiderModule:
    name = NAME

    def metrics(self) -> list[str]:
        return list(METRICS)

    def compute(self, asof: str, tickers: list[str] | None = None) -> pd.DataFrame:
        import insider_signals as SIG
        empty = pd.DataFrame(columns=["ticker", "metric", "value"])
        tickers = list(dict.fromkeys(tickers or []))
        if not tickers:
            return empty
        out = pd.DataFrame(index=pd.Index(tickers, name="ticker"), columns=METRICS, dtype="float64")

        cov = covered(asof, tickers)
        pt = SIG.per_ticker(asof, 30).set_index("ticker")
        pt = pt[pt.index.isin(tickers)]
        cv = out.index.isin(list(cov))
        out.loc[cv, ["buyers_30d", "value_30d", "rare_buy_30d", "small_buy_30d", "ceo_cfo_buy_30d"]] = 0.0
        # A purchase found for a company the coverage map missed is still a purchase.
        idx = pt.index
        out.loc[idx, "buyers_30d"] = pt.buyers.astype(float)
        out.loc[idx, "value_30d"] = pt.value.astype(float)
        out.loc[idx, "rare_buy_30d"] = pt.rare.astype(float)
        out.loc[idx, "small_buy_30d"] = pt.small.fillna(False).astype(float)
        out.loc[idx, "ceo_cfo_buy_30d"] = pt.ceo_cfo.astype(float)

        hb = SIG.house_buys(asof, 60)
        hb = hb[hb.ticker.isin(tickers)]
        out["house_buy_60d"] = 0.0
        out["house_big_buy_60d"] = 0.0
        if len(hb):
            n = hb.groupby("ticker").member.nunique()
            out.loc[n.index, "house_buy_60d"] = n.astype(float)
            big = hb[hb.amount_lo >= SIG.HOUSE_BIG].ticker.unique()
            out.loc[big, "house_big_buy_60d"] = 1.0

        ok, flags = _wf()
        if ok and all(f in _FLAG_METRIC for f in flags):
            out["insider_score"] = out[[_FLAG_METRIC[f] for f in flags]].sum(axis=1, min_count=len(flags))

        long = (out.reset_index().melt(id_vars="ticker", var_name="metric", value_name="value")
                   .dropna(subset=["value"]))
        return long.reset_index(drop=True)

    def selftest(self) -> None:
        _selftest()


MODULE = register(InsiderModule())


def _selftest() -> None:
    assert len(METRICS) == len(set(METRICS))
    m = InsiderModule()
    assert m.metrics() == METRICS and m.name == "insider"
    # A ticker the coverage map has never seen must come back blank on the insider
    # metrics, never 0: "no reports" is not "no buying".
    _COVER["fq"] = pd.DataFrame({"cik": [7], "q": ["2026q3"]})
    _COVER["tc"] = {"AAA": {7}, "ZZZ": {9}}
    try:
        assert covered("2026-10-26", ["AAA", "ZZZ", "QQQ"]) == {"AAA"}
        assert covered("2031-01-02", ["AAA"]) == set()       # filings too old to count
    finally:
        _COVER.clear()
    print(f"  [insider] {len(METRICS)} metrics; coverage gate keeps 'no reports' blank")
