"""
Link a ticker's CURRENT CIK to the predecessor CIK that holds its history.

    python cik_succession.py            build/refresh data/_cik_alias.json
    python cik_succession.py --check    report without writing

THE PROBLEM, found 2026-08-16 on XOM
---------------------------------------
A corporate reorganisation gives the surviving company a NEW CIK. SEC's
`company_tickers.json` maps the ticker to that new CIK only, so every filing
made under the predecessor becomes unreachable even though it is sitting in our
own fact store:

    cik 2115436  "Exxon Mobil Corporation"    94 rows   2024-12-31..2026-06-30
    cik   34088  "Exxon Mobil Corporation"  5,208 rows  2006-12-31..2025-12-31

Both are ACTIVE -- 34088 filed through 2026-03-31 -- so the data is genuinely
SPLIT, not merely archived. With only the new CIK, XOM has no annual figure and
no four consecutive quarters, so every TTM is uncomputable and the page renders
blank. Correct behaviour given the inputs, and useless to a reader.

WHY ENTITY NAME IS THE LINK
------------------------------
Both CIKs report `entityName = "Exxon Mobil Corporation"` in companyfacts.
There is no field naming a predecessor CIK, and `company_tickers.json` lists
only current registrants, so the orphan is invisible from the map side. Matching
on the normalised entity name is the available signal.

SCOPE, MEASURED BEFORE BUILDING
----------------------------------
374 tradeable tickers have a CIK with fewer than 8 distinct periods. Of those:

    288  file 20-F / 40-F / 6-K   FOREIGN, IFRS tags, NOT a succession and not
                                  fixable here -- they genuinely file no us-gaap
     86  file 10-K / 10-Q         domestic, and therefore candidates

So this targets 86 names. Foreign filers are excluded explicitly rather than
being swept up and mislabelled, because "thin" has two completely different
causes and conflating them would attach the wrong history to a ticker.

SAFETY
---------
A wrong alias attaches ANOTHER COMPANY'S financials to a ticker, which is worse
than a blank page. So a match requires an exact normalised-name equality, the
candidate must file 10-K/10-Q, and every link is written with both names and
row counts so it can be audited by eye.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime

import pandas as pd

import config

config.safe_console()

OUT = config.DATA / "_cik_alias.json"

# Fewer distinct periods than this and a ticker cannot support a TTM, which is
# what makes it a succession candidate worth investigating.
THIN_PERIODS = 8
# Orphans below this are not worth a network call -- a predecessor holding a
# real history has thousands of rows, not dozens.
MIN_ORPHAN_ROWS = 500

# Total-assets ratio band inside which a new CIK is the SAME company renumbered
# rather than a merged one. Tight on purpose -- see the note in `build`.
ASSET_RATIO_MIN, ASSET_RATIO_MAX = 0.75, 1.35


def _total_assets(cik: int) -> float | None:
    """Newest reported total assets for a CIK, or None."""
    import fundamentals as FD
    try:
        r = FD.read(ciks={int(cik)})
        a = r[r["tag"].isin(FD.TAGS["assets"])].sort_values("ddate")
        return float(a["value"].iloc[-1]) if len(a) else None
    except Exception:                                            # noqa: BLE001
        return None

# ONLY LEGAL-FORM SUFFIXES, and only at the END of the name.
#
# The first version also stripped "holdings", "group", "the" and "new" ANYWHERE
# in the string, which made `Acme Holdings Ltd` and `Acme Group PLC` compare
# EQUAL -- two plainly different registrants -- and `Alpha Group Inc` equal to
# `Alpha Inc`. Caught by a unit test before this ever ran against real data,
# and it matters far more than a missed match: a false link attaches ANOTHER
# COMPANY'S financials to a ticker, which is worse than the blank page it was
# meant to fix.
#
# "incorporated" and "corporation" are listed so `Apple Inc.` still meets
# `Apple Incorporated`.
_SUFFIX = re.compile(
    r"\s+(incorporated|corporation|company|limited|inc|corp|co|ltd|plc|"
    r"lp|llc|sa|nv|ag)$", re.I)


def _norm(name: str) -> str:
    """Normalise an entity name for exact comparison.

    Deliberately conservative: strip punctuation, case and the common corporate
    suffixes, then require EXACT equality. Fuzzy matching would eventually link
    two genuinely different companies with similar names, and attaching the
    wrong financials to a ticker is far worse than leaving a page blank.
    """
    n = re.sub(r"[^a-z0-9 ]", " ", str(name).lower())
    n = re.sub(r"\s+", " ", n).strip()
    # Peel trailing legal forms repeatedly: "acme corp inc" -> "acme".
    for _ in range(3):
        n2 = _SUFFIX.sub("", n).strip()
        if n2 == n or not n2:
            break
        n = n2
    return n


def _entity_name(cik: int) -> str | None:
    import verify_metrics as V
    d = V._get(V.FACTS_URL.format(cik=int(cik)))
    return (d or {}).get("entityName")


def build(check_only: bool = False) -> dict:
    import bars
    import fundamentals as FD

    raw = FD.read(start_q="2024q1")
    tm = FD.ticker_map()
    uni = set(bars.tradeable_universe())
    cur = tm[tm["ticker"].isin(uni)]

    per = raw[raw["cik"].isin(set(cur["cik"]))].groupby("cik")["ddate"].nunique()
    thin = set(per[per < THIN_PERIODS].index)
    sub = raw[raw["cik"].isin(thin)]
    domestic = set(sub[sub["form"].isin(["10-K", "10-Q"])]["cik"])
    print(f"  {len(thin):,} thin CIK(s); {len(domestic):,} domestic "
          f"(candidates), {len(thin - domestic):,} foreign (excluded)")

    counts = raw.groupby("cik").size()
    orphans = counts[~counts.index.isin(set(tm["cik"]))]
    orphans = orphans[orphans >= MIN_ORPHAN_ROWS]
    print(f"  {len(orphans):,} orphan CIK(s) with >={MIN_ORPHAN_ROWS} rows\n")

    print("  fetching entity names for candidates...", flush=True)
    cand = {}
    for i, cik in enumerate(sorted(domestic), 1):
        nm = _entity_name(cik)
        if nm:
            cand[int(cik)] = nm
        if i % 20 == 0:
            print(f"    {i}/{len(domestic)}", flush=True)

    print("  fetching entity names for orphans...", flush=True)
    orph = {}
    for i, cik in enumerate(sorted(orphans.index), 1):
        nm = _entity_name(cik)
        if nm:
            orph[int(cik)] = nm
        if i % 50 == 0:
            print(f"    {i}/{len(orphans)}", flush=True)

    by_name: dict[str, list[int]] = {}
    for cik, nm in orph.items():
        by_name.setdefault(_norm(nm), []).append(cik)

    tick_of = dict(zip(cur["cik"], cur["ticker"]))
    alias, rows, rejected = {}, [], []
    for cik, nm in cand.items():
        hits = by_name.get(_norm(nm), [])
        if not hits:
            continue
        best = max(hits, key=lambda c: int(counts.get(c, 0)))

        # A REORGANISATION PRESERVES THE BALANCE SHEET; A MERGER DOES NOT.
        #
        # Entity name alone cannot tell them apart, and that is what made the
        # first version unsafe: it linked PNFP, whose new CIK is the combined
        # Pinnacle/Synovus entity, to pre-merger Pinnacle -- assets 129.1B
        # against the predecessor's 56.0B, splicing two different companies
        # into one history and dropping verification to 30.3%.
        #
        # Total assets under each CIK is the decisive, data-driven test:
        #
        #     XOM   464.5B / 449.0B = 1.03   same company, renumbered
        #     CLBK   12.2B /  11.0B = 1.10   same company, renumbered
        #     NVRI    1.7B /   2.7B = 0.64   materially changed
        #     PNFP  129.1B /  56.0B = 2.31   merger
        #
        # Only a ratio near 1 is linked. Being wrong here attaches another
        # company's financials to a ticker, so the band is deliberately tight
        # and a candidate with no assets on either side is skipped, not guessed.
        na, oa = _total_assets(cik), _total_assets(best)
        ratio = (na / oa) if (na and oa) else None
        if ratio is None or not (ASSET_RATIO_MIN <= ratio <= ASSET_RATIO_MAX):
            rejected.append({"ticker": tick_of.get(cik, "?"), "cik": cik,
                             "predecessor": best,
                             "ratio": round(ratio, 2) if ratio else None,
                             "why": "merger or materially changed"})
            continue
        alias[str(cik)] = int(best)
        rows.append({"ticker": tick_of.get(cik, "?"), "cik": cik,
                     "name": nm, "predecessor": best,
                     "pred_name": orph[best],
                     "cik_rows": int(counts.get(cik, 0)),
                     "pred_rows": int(counts.get(best, 0))})

    if rows:
        df = pd.DataFrame(rows).sort_values("pred_rows", ascending=False)
        print(f"\n  {len(df)} succession(s) found:\n")
        print(df.to_string(index=False))
    else:
        print("\n  no successions found")

    if not check_only and alias:
        OUT.write_text(json.dumps(alias, indent=2), encoding="utf-8")
        print(f"\n  wrote {OUT}")
    return alias


def main() -> int:
    ap = argparse.ArgumentParser(description="Resolve CIK successions.")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    print(f"cik_succession | {datetime.now():%Y-%m-%d %H:%M}")
    build(check_only=a.check)
    return 0


if __name__ == "__main__":
    sys.exit(main())
