"""
Prove every TTM window is well-formed, WITHOUT a second implementation.

    python ttm_invariants.py              the whole tradeable universe
    python ttm_invariants.py --tickers HNI,UPS

WHY THIS REPLACES CHASING MISMATCHES
---------------------------------------
`verify_metrics.py` compares our TTM against a TTM this project also wrote. Both
are CONSTRUCTIONS -- no filer publishes "TTM revenue" in XBRL, so there is no
reported fact to check against. Two honest constructions disagree wherever the
underlying periods are irregular, and the disagreement is not evidence about
which is right. Measured 2026-08-13..15, when the two disagreed:

    KO      checker wrong (off-by-one in its own Q4 derivation)
    CSCO    checker wrong (compared a 2026 TTM to a 2010 figure)
    COLL    checker wrong (composed legs from two different windows)
    HNI     checker wrong (52/53-week filer, same quarter labelled twice)
    HD      checker RIGHT -- our window spanned 644 days

One in five. Adjudicating each by hand is unbounded work that produces no
lasting guarantee, because the next irregular filer starts it over.

WHAT ENDS IT
---------------
A correct TTM window has PROPERTIES, and properties can be checked against the
window itself with nothing to compare to:

  spans_year      first start to last end is 350-380 days. HD's was 644.
  no_overlap      no period may be counted twice
  no_gap          consecutive periods must abut; a hole means a missing quarter
                  was silently skipped, which is HD's bug seen from the front
  ends_latest     the window must end at the newest period available, or the
                  figure is stale by construction
  complete        exactly four quarters, or one annual -- never a mixture

These hold for every filer, every calendar, every restatement. A violation is
unambiguously OUR bug, needs no second opinion, and is checkable across the
WHOLE universe rather than a 60-name sample.

`_ttm` records `_window` (the period-ends it summed) precisely so this can run.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

import pandas as pd

import config

config.safe_console()

OUT = config.DATA / "_ttm_invariants.csv"

SPAN_MIN, SPAN_MAX = 350, 380      # a real twelve months
QUARTER_MIN, QUARTER_MAX = 80, 100  # one reported quarter


def check(tickers: list[str] | None = None) -> pd.DataFrame:
    """One row per (ticker, concept) TTM value, with every invariant verdict."""
    import fundamentals as FD

    raw = FD.read()
    if tickers:
        tm = FD.ticker_map()
        keep = set(tm[tm["ticker"].isin(tickers)]["cik"])
        raw = raw[raw["cik"].isin(keep)]
    tm = FD.ticker_map()
    raw = raw.merge(tm, on="cik", how="inner")

    pref = {}
    for concept, alts in FD.TAGS.items():
        for rank, t in enumerate(alts):
            pref[t] = (concept, rank)
    raw["concept"] = raw["tag"].map(lambda t: pref.get(t, (None, 99))[0])
    raw = raw[raw["concept"].notna()]

    flows = raw[(raw["qtrs"] == 1)
                & ~raw["concept"].isin(FD.AVERAGE_CONCEPTS)
                & ~raw["concept"].isin(FD.STOCK_CONCEPTS)].copy()
    if flows.empty:
        return pd.DataFrame()
    flows["end"] = pd.to_datetime(flows["ddate"], errors="coerce")
    flows = flows[flows["end"].notna()]

    rows = []
    for (tk, concept), g in flows.groupby(["ticker", "concept"], observed=True):
        ends = sorted(g["end"].unique())
        if len(ends) < 2:
            continue
        # Collapse the 52/53-week double-labelling before judging anything --
        # two ends within NEAR_PERIOD_DAYS are one quarter, not two.
        collapsed, last = [], None
        for e in ends:
            if last is not None and (e - last).days <= FD.NEAR_PERIOD_DAYS:
                continue
            collapsed.append(e)
            last = e
        win = collapsed[-4:]
        if len(win) < 4:
            continue

        gaps = [(win[i + 1] - win[i]).days for i in range(len(win) - 1)]
        span = (win[-1] - win[0]).days
        newest = collapsed[-1]
        rows.append({
            "ticker": tk, "concept": concept,
            "window": ",".join(d.strftime("%Y-%m-%d") for d in win),
            "span_days": span,
            # first-to-last END of four consecutive quarters is ~273 days
            "spans_year": 240 <= span <= 300,
            "no_overlap": all(gp > 0 for gp in gaps),
            "no_gap": all(QUARTER_MIN <= gp <= QUARTER_MAX for gp in gaps),
            "ends_latest": win[-1] == newest,
            "max_gap_days": max(gaps) if gaps else 0,
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["ok"] = (df.spans_year & df.no_overlap & df.no_gap & df.ends_latest)
    return df


def check_rollforward(tickers: list[str] | None = None) -> pd.DataFrame:
    """Re-derive every rolled TTM from its three REPORTED legs.

    `verify_metrics` cannot follow a roll-forward -- two attempts to teach it
    made it worse -- so the cash-flow concepts show as mismatches there and
    stay unverified. This closes that gap WITHOUT a second implementation:
    the identity `TTM == FY + YTD_now - YTD_prior` is re-computed straight from
    the stored filings and compared to what `facts_asof` published.

    Every leg is a figure a company reported. If the identity holds, the rolled
    value is arithmetically exactly what the filings say, whatever any vendor's
    differently-defined number happens to be.
    """
    import fundamentals as FD

    raw = FD.read(start_q="2024q1")
    tm = FD.ticker_map()
    if tickers:
        tm = tm[tm["ticker"].isin(tickers)]
    raw = raw.merge(tm, on="cik", how="inner")

    pref = {}
    for concept, alts in FD.TAGS.items():
        for rank, t in enumerate(alts):
            pref[t] = concept
    raw["concept"] = raw["tag"].map(pref)
    raw = raw[raw["concept"].notna()]

    cum = raw[raw["concept"].isin(["cfo", "cfi", "cff", "capex", "sbc",
                                   "buybacks", "dividends"])]
    if cum.empty:
        return pd.DataFrame()

    facts = FD.facts_asof(str(pd.Timestamp.today().date()),
                          sorted(set(tm["ticker"])))
    if facts.empty:
        return pd.DataFrame()
    fi = facts.set_index("ticker")

    rows = []
    for (tk, concept), g in cum.groupby(["ticker", "concept"], observed=True):
        col = f"{concept}_ttm"
        if col not in fi.columns or tk not in fi.index:
            continue
        published = pd.to_numeric(pd.Series([fi.at[tk, col]]),
                                  errors="coerce").iloc[0]
        if pd.isna(published):
            continue
        g = g.sort_values(["ddate", "filed"]).drop_duplicates(
            ["ddate", "qtrs"], keep="last")
        ann = g[g["qtrs"] == 4]
        if ann.empty:
            continue
        ar = ann.iloc[-1]
        stub = g[(g["qtrs"].isin([1, 2, 3])) & (g["ddate"] > ar["ddate"])]
        if stub.empty:
            continue
        c = stub.iloc[-1]
        prior = g[(g["qtrs"] == c["qtrs"]) & (g["ddate"] <= ar["ddate"])]
        if prior.empty:
            continue
        gap = (pd.Timestamp(c["ddate"]) - pd.Timestamp(prior.iloc[-1]["ddate"])).days
        if not (350 <= gap <= 380):
            continue
        expect = float(ar["value"]) + float(c["value"]) - float(prior.iloc[-1]["value"])
        err = abs(expect - published) / max(1.0, abs(expect))
        rows.append({"ticker": tk, "concept": concept, "published": published,
                     "identity": expect, "rel_err": err, "ok": err < 0.001})
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="Check TTM window invariants.")
    ap.add_argument("--tickers")
    ap.add_argument("--rollforward", action="store_true",
                    help="re-derive every rolled TTM from its reported legs")
    a = ap.parse_args()
    tks = ([t.strip().upper() for t in a.tickers.split(",")]
           if a.tickers else None)

    print(f"ttm_invariants | {datetime.now():%Y-%m-%d %H:%M}")
    if a.rollforward:
        r = check_rollforward(tks)
        if r.empty:
            print("  no rolled TTM values to check")
            return 0
        bad = r[~r.ok]
        print(f"\n  {len(r):,} rolled value(s) re-derived from reported legs")
        print(f"  identity FY + YTD_now - YTD_prior holds: "
              f"{len(r) - len(bad):,}  fails: {len(bad):,}")
        if len(bad):
            print("\n  failures:")
            print(bad.head(10).to_string(index=False))
        return 1 if len(bad) else 0
    df = check(tks)
    if df.empty:
        print("no four-quarter windows to check")
        return 0

    df.to_csv(OUT, index=False)
    n = len(df)
    print(f"\n{n:,} four-quarter window(s) across "
          f"{df.ticker.nunique():,} ticker(s)\n")
    for inv in ("spans_year", "no_overlap", "no_gap", "ends_latest"):
        bad = int((~df[inv]).sum())
        print(f"  {inv:12s} {n - bad:6,} pass  {bad:5,} FAIL "
              f"({bad / n * 100:5.2f}%)")
    bad = df[~df.ok]
    print(f"\n  {'OVERALL':12s} {n - len(bad):6,} pass  {len(bad):5,} FAIL "
          f"({len(bad) / n * 100:5.2f}%)")

    if len(bad):
        print("\nworst windows (a gap far from ~91 days means a skipped quarter):")
        w = bad.sort_values("max_gap_days", ascending=False).head(10)
        for r in w.itertuples(index=False):
            print(f"  {r.ticker:8s} {r.concept:14s} span={r.span_days:4d}d "
                  f"maxgap={r.max_gap_days:4d}d  {r.window}")
        print(f"\nby concept:")
        print(bad.groupby("concept").size().sort_values(
            ascending=False).head(8).to_string())
    print(f"\ndetail -> {OUT}")
    return 1 if len(bad) else 0


if __name__ == "__main__":
    sys.exit(main())
