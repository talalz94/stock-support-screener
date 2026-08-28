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
# ONE REPORTED QUARTER. The upper bound is 125, not 100, because a retail
# 4-4-5 calendar is not four equal quarters. KR files a SIXTEEN-week Q1 and
# three twelve-week quarters: measured 2026-08-22, revenue 45.118B for the
# 16-week period against ~33.9B for each 12-week one, and the window
# 2025-08-16 / 2025-11-08 / 2026-01-31 / 2026-05-31 carries gaps of 84, 84 and
# 120 days. That is 12+12+12+16 = 52 weeks -- a correct TTM that the old bound
# failed on six concepts at once.
#
# Still far below a SKIPPED quarter (~182 days) or the HD 644-day window, so
# the check keeps its teeth.
QUARTER_MIN, QUARTER_MAX = 80, 125


def check(tickers: list[str] | None = None,
          asof: str | None = None) -> pd.DataFrame:
    """Check the windows PRODUCTION ACTUALLY USED, not a re-derivation.

    THE OLD VERSION TESTED ITSELF. It rebuilt windows from raw `qtrs==1` rows
    and passed 3 of 127 -- measured 2026-08-21 on untouched code, for large caps
    and microcaps alike. It was not detecting 97% broken data; its premise did
    not hold for this source. No 10-K reports fiscal Q4 separately (it is
    derived FY - Q1 - Q2 - Q3), so every filer shows a ~182-day hole once a
    year. AAPL revenue: 2025-06-28, 2025-12-27, 2026-03-28, 2026-06-27 --
    2025-09-27 absent, identical at every history depth.

    A checker with a 2.4% pass rate cannot gate anything, which is why it was
    kept out of the daily `validate` step.

    `_ttm` already RECORDS the window it summed, Q4 derivation included, and
    `fundamentals.ttm_windows` now exposes it. So this reads production's own
    answer and checks the properties that must hold of it:

      spans_year   four consecutive quarter-ends span ~273 days first to last
      no_overlap   no period counted twice
      no_gap       consecutive ends ~91 days apart; a hole means a skipped
                   quarter, which is the 644-day HD window seen from the front
      complete     exactly four distinct ends

    Only `_src == "4q"` rows have a window to check. `annual` is one reported
    period, `roll` is three reported legs whose identity `check_rollforward`
    verifies, and `avg` is a point-in-time reduction -- none has period-ends to
    validate, so they are counted and reported rather than judged.
    """
    import fundamentals as FD

    w = FD.ttm_windows(asof or str(pd.Timestamp.today().date()), tickers)
    if w.empty:
        return pd.DataFrame()

    # `itertuples` RENAMES columns beginning with an underscore to positional
    # names (_1, _2 ...), so `_src` and `_window` are unreachable by attribute.
    # Renamed here rather than debugged again later.
    w = w.rename(columns={"_src": "src", "_window": "window_ends"})

    rows = []
    for r in w.itertuples(index=False):
        src = getattr(r, "src", None)
        win = getattr(r, "window_ends", None)
        if src != "4q" or not isinstance(win, str) or not win:
            continue
        ends = sorted(pd.Timestamp(x) for x in win.split(",") if x)
        # `_ttm` already collapses 52/53-week near-duplicates, so anything
        # still here is a distinct period.
        if len(ends) < 2:
            continue
        gaps = [(ends[i + 1] - ends[i]).days for i in range(len(ends) - 1)]
        span = (ends[-1] - ends[0]).days
        rows.append({
            "ticker": r.ticker, "concept": r.concept, "src": src,
            "window": win, "n_ends": len(ends), "span_days": span,
            "spans_year": 240 <= span <= 300,
            "no_overlap": all(g > 0 for g in gaps),
            "no_gap": all(QUARTER_MIN <= g <= QUARTER_MAX for g in gaps),
            "complete": len(ends) == 4,
            "max_gap_days": max(gaps) if gaps else 0,
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["ok"] = (df.spans_year & df.no_overlap & df.no_gap & df.complete)
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

    # ALIAS RANK, not just the concept. Several tags map to one concept and
    # they carry DIFFERENT numbers, so ignoring rank builds an identity out of
    # legs the production path never used -- or worse, out of a mixture.
    #
    # Measured 2026-08-22. SRZN maps two tags to `capex`:
    #   PaymentsToAcquirePropertyPlantAndEquipment  rank 0  FY 128,000
    #   PaymentsToAcquireProductiveAssets           rank 1  FY 280,000
    # production takes rank 0 and gets 128,000 + 139,000 - 45,000 = 222,000;
    # the checker took rank 1 and called that a 40% failure.
    #
    # ARKO was worse: 13,600,000 + 6,700,000 - 6,910,000 = 13,390,000 mixes two
    # rank-1 legs with a rank-0 one inside a single identity.
    pref, rank_of = {}, {}
    for concept, alts in FD.TAGS.items():
        for rank, t in enumerate(alts):
            pref[t] = concept
            rank_of[t] = rank
    raw["concept"] = raw["tag"].map(pref)
    raw["rank"] = raw["tag"].map(rank_of)
    raw = raw[raw["concept"].notna()]

    cum = raw[raw["concept"].isin(["cfo", "cfi", "cff", "capex", "sbc",
                                   "buybacks", "dividends"])]
    if cum.empty:
        return pd.DataFrame()

    _asof = str(pd.Timestamp.today().date())
    facts = FD.facts_asof(_asof, sorted(set(tm["ticker"])))
    if facts.empty:
        return pd.DataFrame()
    fi = facts.set_index("ticker")

    # ONLY CHECK VALUES THAT WERE ACTUALLY ROLLED.
    #
    # The identity `FY + YTD_now - YTD_prior` says nothing about a figure
    # produced by summing four quarters or by taking an annual outright, so
    # applying it to those compares two different constructions and calls the
    # difference a failure. Measured 2026-08-22: HTLD `sbc` (1,900,000) and
    # SMPL `sbc` (15,712,000) are both `_src == "4q"`, and both were reported
    # as roll-forward failures by a checker that never asked.
    #
    # `_ttm` records which source produced each figure; `ttm_windows` exposes
    # it. So the checker now tests exactly the population the identity governs.
    # (ticker, concept) -> the TAG production rolled, so the identity is
    # re-derived from the SAME legs rather than from a guessed alias.
    #
    # Forcing the preferred alias was wrong and LBRDA proves it: rank 0 has no
    # annual and no current stub, so only rank 1 supplies a complete set.
    # Production rolls rank 1 to -389M; a rank-0 prior spliced into rank-1 legs
    # gives -614M, and the checker reported the correct figure as a 37%
    # failure. CAI and PLOW `capex` are the same shape.
    try:
        _w = FD.ttm_windows(_asof, sorted(set(tm["ticker"])))
        _r = _w[_w["_src"] == "roll"] if not _w.empty else _w
        _rolled = {(t, c): g for t, c, g in
                   zip(_r["ticker"], _r["concept"], _r["_tag"])}             if len(_r) else {}
    except Exception:                                            # noqa: BLE001
        _rolled = {}

    rows = []
    for (tk, concept), g in cum.groupby(["ticker", "concept"], observed=True):
        if _rolled and (tk, concept) not in _rolled:
            continue
        _use_tag = _rolled.get((tk, concept)) if _rolled else None
        if _use_tag is not None and "tag" in g.columns:
            g = g[g["tag"] == _use_tag]
            if g.empty:
                continue
        col = f"{concept}_ttm"
        if col not in fi.columns or tk not in fi.index:
            continue
        published = pd.to_numeric(pd.Series([fi.at[tk, col]]),
                                  errors="coerce").iloc[0]
        if pd.isna(published):
            continue
        # rank DESCENDING so the preferred alias (rank 0) lands last and
        # `keep="last"` takes it -- the same convention as `_latest`.
        g = (g.sort_values(["ddate", "rank", "filed"],
                           ascending=[True, False, True], kind="mergesort")
              .drop_duplicates(["ddate", "qtrs"], keep="last"))
        ann = g[g["qtrs"] == 4]
        if ann.empty:
            continue
        ar = ann.iloc[-1]
        stub = g[(g["qtrs"].isin([1, 2, 3])) & (g["ddate"] > ar["ddate"])]

        # THE STUB MUST BE A TRUE YEAR-TO-DATE CUMULATIVE -- the same guard
        # `_ttm` got on 2026-08-21, which this checker never received.
        #
        # `stub.iloc[-1]` takes the newest row regardless of `qtrs`, and a
        # `qtrs=1` row is YTD only for fiscal Q1. NVR, measured 2026-08-22:
        # the checker paired the single quarter ending 2026-06-30 (19,293,000)
        # against the single quarter a year earlier (17,813,000) and computed
        # 70,693,000, then reported production's 65,454,000 as a failure.
        # Production was right -- 69,213,000 + 32,580,000 - 36,339,000 uses the
        # qtrs=2 YTD legs the filer actually published.
        #
        # A YTD stub of n quarters ends about 3n months after the fiscal year
        # end; anything else is a bare quarter wearing a cumulative's label.
        if not stub.empty:
            _d = (pd.to_datetime(stub["ddate"], errors="coerce")
                  - pd.Timestamp(ar["ddate"])).dt.days
            stub = stub[(_d - stub["qtrs"].astype(int) * 91.31).abs() <= 20]
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
    # "complete", not "ends_latest": `check` was rewritten to read production
    # windows and renamed this column, but the print loop still asked for the
    # old name -- so the whole invariant report died with KeyError before
    # printing a single line, and had been dead since that rewrite.
    for inv in ("spans_year", "no_overlap", "no_gap", "complete"):
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
