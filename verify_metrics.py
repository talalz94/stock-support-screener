"""
Verify the numbers the APP DISPLAYS against independent SEC ground truth.

WHY THIS EXISTS, AND WHY THE EXISTING CHECK WAS NOT ENOUGH
------------------------------------------------------------
`fundamentals.py --reconcile` compares RAW REPORTED QUARTERS -- revenue and
net income for 2026Q1, as filed -- against SEC's XBRL `companyconcept` API.
On 2026-08-12 it passed 15/15 at 0.000% and that result was reported as if the
data were verified end to end.

It was not. Nothing on a page is a raw quarterly figure. Every valuation metric
consumes the TRAILING-TWELVE-MONTH AGGREGATION built on top of those quarters,
and on 2026-08-13 two bugs were found living exactly there:

  * `_ttm()` short-circuited on any visible annual figure, so 94% of filers
    carried a TTM a median 181 days stale. AAPL: $112.0B instead of $128.9B.
  * weighted-average share counts were summed and Q4-subtracted like flows,
    producing -30,150,480,000 diluted shares for AAPL.

Both were invisible to a check that only ever looked at the inputs. The quarters
were right the whole time; what we DID with them was wrong.

So this module checks the OUTPUT side, independently:

    SEC XBRL API  ->  our own TTM arithmetic, written here, deliberately NOT
                      importing fundamentals._ttm  ->  compare to facts_asof

Reimplementing the aggregation is the entire point. A check that calls the code
under test can only confirm the code agrees with itself -- which is precisely
how the last two bugs survived. The arithmetic here is intentionally naive and
slow: sum four calendar quarters, take the latest instant, never derive.

    python verify_metrics.py                    15 default names
    python verify_metrics.py --tickers AAPL,KO  specific names
    python verify_metrics.py --n 40             a wider random sample

Exit code is 0 only if every checked field is within tolerance.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

import pandas as pd

import config
import fundamentals as FD

# Large, liquid, well-known filers across sectors. Chosen so a human can eyeball
# the output against any quote site, which is the check behind the check.
DEFAULT = ["AAPL", "MSFT", "NVDA", "KO", "JNJ", "WMT", "XOM", "PG",
           "HD", "MCD", "CSCO", "ORCL", "PFE", "INTC", "ADBE"]

# The concepts worth checking are the ones a metric divides by. A tolerance of
# 1% absorbs restatement timing and alias choice; anything larger is a bug.
TOL = 0.01

# Two period-ends this close are the same fiscal quarter labelled twice by a
# 52/53-week filer. Mirrors `fundamentals.NEAR_PERIOD_DAYS` so both sides of
# the comparison agree on what one period is.
NEAR_PERIOD_DAYS = 10

UA = getattr(config, "SEC_UA", None) or "support-bounce-screener contact@example.com"
# ONE request per company, not one per concept.
#
# `companyconcept/CIK.../us-gaap/{tag}.json` looked like the precise tool but
# returns `units: {"USD": []}` -- an empty array, HTTP 200, no error -- for
# concepts a company demonstrably files. KO came back empty on every revenue
# and balance-sheet tag that way, and the run still printed a 91.7% pass rate
# because "no data" was counted as neutral rather than as a failed check.
#
# `companyfacts` returns every tag the filer has ever used in a single payload,
# so a concept is missing only if it is genuinely absent. It is also ~10x fewer
# requests, which matters against SEC's rate limit.
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# (our concept, SEC tag candidates, kind)
#   flow    -> sum the four most recent non-overlapping quarters
#   instant -> most recent instantaneous value
#   avg     -> most recent duration value, NEVER summed
#
# The TAG LIST comes from `fundamentals.TAGS`, deliberately. Hand-writing a
# short list here looked more independent but was simply wrong: KO tags nothing
# under `StockholdersEquity` or the three revenue names guessed first, so every
# KO field came back "no data" and the run still printed a 91.7% pass rate. A
# checker that silently skips what it cannot find measures its own vocabulary,
# not the data.
#
# Sharing the tag->concept MAPPING is not sharing the code under test. The bugs
# this module exists to catch live in the AGGREGATION -- which window, summed or
# not, derived or not -- and that arithmetic is written from scratch below.
# `dei:` is tried alongside us-gaap for cover-page share counts.
_KIND = {"revenue": "flow", "net_income": "flow", "eps_diluted": "flow",
         "shares_diluted": "avg", "shares_out": "instant",
         "equity": "instant", "assets": "instant",
         # legs behind the derived rows
         "opinc": "flow", "dna": "flow", "deprec": "flow", "amort": "flow",
         "cfo": "flow", "capex": "flow"}

CHECKS = [
    (f"{c}_ttm" if _KIND[c] in ("flow", "avg") else c,
     list(FD.TAGS.get(c, [])), _KIND[c])
    for c in ("revenue", "net_income", "eps_diluted", "shares_diluted",
              "shares_out", "equity", "assets",
              # ADDED 2026-08-14 after COLL. These are the legs the DERIVED
              # rows are built from, and none of them were checked -- which is
              # exactly why EBITDA could read $4M against a real $68M and this
              # module still reported 100%.
              "opinc", "dna", "deprec", "amort", "cfo", "capex")
]


# SEC asks for fewer than 10 requests/second and answers 403 above it. Sleeping
# only between TICKERS was not enough: each ticker fires seven concept requests
# back to back, so the third ticker onward got throttled and every field came
# back "no data" -- which reads exactly like a missing concept and would have
# been mistaken for a coverage gap instead of a checker fault. Throttle every
# request, and count real failures separately from genuine absences.
_MIN_GAP = 0.15
_last_req = [0.0]
FETCH_ERRORS: dict[str, int] = {}


def _get(url: str, tries: int = 4) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Encoding": "gzip, deflate"})
    for i in range(tries):
        gap = time.monotonic() - _last_req[0]
        if gap < _MIN_GAP:
            time.sleep(_MIN_GAP - gap)
        _last_req[0] = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    import gzip
                    raw = gzip.decompress(raw)
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None                    # genuinely not tagged: not an error
            FETCH_ERRORS[str(e.code)] = FETCH_ERRORS.get(str(e.code), 0) + 1
            if i == tries - 1:
                return None
            time.sleep(1.0 + 2.0 * i)          # back off on 403/429/5xx
        except Exception as exc:                             # noqa: BLE001
            FETCH_ERRORS[type(exc).__name__] = \
                FETCH_ERRORS.get(type(exc).__name__, 0) + 1
            if i == tries - 1:
                return None
            time.sleep(1.0 + 2.0 * i)
    return None


def _units(payload: dict) -> list[dict]:
    if not payload:
        return []
    for _u, rows in (payload.get("units") or {}).items():
        if rows:
            return rows
    return []


def _better(best, val, end):
    """Keep whichever candidate covers the LATER period."""
    if best[0] is None or (end or "") > (best[1] or ""):
        return (val, end)
    return best


def sec_value(facts: dict, tags: list[str], kind: str, asof: str):
    """Ground truth for one concept, computed here from SEC's own filings.

    Deliberately independent of `fundamentals`: this is the second opinion.

    EVERY candidate tag is evaluated and the one whose period ENDS LATEST
    wins. Taking the first tag that returned anything picked MSFT's `Revenues`
    -- which Microsoft stopped using around 2018 -- and compared a 2026 figure
    against a 2010 one, reporting a 397% "mismatch" that was entirely the
    checker's fault. A verifier that cries wolf is worse than none.
    """
    best = (None, None)
    for tag in tags:
        t = tag.split(":")[-1]
        rows = []
        for ns in ("us-gaap", "dei"):          # cover-page counts live in dei
            node = (facts.get(ns) or {}).get(t)
            if node:
                rows = _units(node)
                if rows:
                    break
        if not rows:
            continue
        df = pd.DataFrame(rows)
        if "end" not in df.columns:
            continue
        # POINT IN TIME: only what was filed on or before `asof`.
        if "filed" in df.columns:
            df = df[df["filed"] <= asof]
        df = df[df["end"] <= asof]
        if df.empty:
            continue

        if kind == "instant":
            # SEC omits "start" for instantaneous facts. dei facts sometimes
            # carry one anyway, so fall back to latest-by-end rather than
            # returning nothing.
            inst = df[df["start"].isna()] if "start" in df.columns else df
            use = inst if not inst.empty else df
            use = use.sort_values(["end", "filed"])
            best = _better(best, float(use.iloc[-1]["val"]),
                           str(use.iloc[-1]["end"]))
            continue

        if "start" not in df.columns:
            continue
        df = df[df["start"].notna()].copy()
        if df.empty:
            continue
        df["days"] = (pd.to_datetime(df["end"]) - pd.to_datetime(df["start"])).dt.days
        quarters = (df[(df["days"] >= 80) & (df["days"] <= 100)]
                    .sort_values(["end", "filed"])
                    .drop_duplicates(subset=["start", "end"], keep="last"))

        # COLLAPSE NEAR-DUPLICATE PERIOD ENDS BEFORE THE NON-OVERLAP SCAN.
        #
        # A 52/53-week filer reports the same quarter under two dates as its
        # fiscal calendar drifts against the month end. HNI files Q3 FY2025 as
        # BOTH `2025-09-27` and `2025-09-30`, same value. The non-overlap scan
        # then treats them as two distinct quarters, consumes two of its four
        # slots on one real quarter, fails the twelve-month span test, and
        # falls back to the annual -- which for HNI is the PRE-Steelcase-
        # acquisition year: 2,839M against a true TTM of ~4,171M. Reported as
        # a 47% "mismatch" on seven fields, all of which our pipeline had
        # right (`_merge_near_periods` already handles this).
        #
        # Same NEAR_PERIOD_DAYS window production uses, so the two sides agree
        # on what counts as one period.
        if len(quarters) > 1:
            q = quarters.sort_values("end").copy()
            keep, last_end = [], None
            for r in q.itertuples(index=False):
                e = pd.Timestamp(r.end)
                if last_end is not None and (e - last_end).days <= NEAR_PERIOD_DAYS:
                    continue                    # same quarter, second labelling
                keep.append(r)
                last_end = e
            quarters = pd.DataFrame(keep)
        annuals = (df[(df["days"] >= 350) & (df["days"] <= 380)]
                   .sort_values(["end", "filed"])
                   .drop_duplicates(subset=["start", "end"], keep="last"))

        if kind == "avg":
            # Latest QUARTERLY duration; never a sum, never an annual average,
            # and never derived -- an average does not decompose.
            if quarters.empty:
                continue
            best = _better(best, float(quarters.iloc[-1]["val"]),
                           str(quarters.iloc[-1]["end"]))
            continue

        # FLOW. Four most recent non-overlapping quarters -- but NO 10-Q ever
        # covers fiscal Q4, so that window silently spans fifteen months
        # (AAPL: 455 days) unless the missing quarter is reconstructed. Derive
        # it the only way it is defined: FY minus the three quarters inside it.
        # This is arithmetic on SEC's own numbers, not a call into the code
        # under test -- the point of this module is a second opinion.
        # A 10-K's own annual figure IS twelve months, and right after it is
        # filed no four-quarter window reaches as far forward. MSFT (June year
        # end) filed FY2026 before this check ran, so the quarterly path could
        # only reach 2026-03-31 and the checker fell back to a 2018 window,
        # reporting a 220% "mismatch" against a perfectly correct number.
        # Emit the annual as a candidate and let the latest-ending one win --
        # that is the definition of TTM, arrived at independently here.
        if not annuals.empty:
            ar = annuals.iloc[-1]
            best = _better(best, float(ar["val"]), str(ar["end"]))

        q = quarters.copy()
        derived = []
        for ar in annuals.itertuples(index=False):
            inside = q[(q["start"] >= ar.start) & (q["end"] <= ar.end)]
            if len(inside) != 3:
                continue                       # partial: refuse to invent one
            if ((q["end"] == ar.end) & (q["start"] > ar.start)).any():
                continue                       # a real Q4 row already exists
            # START IS THE DAY AFTER the third quarter ends, not the same day.
            # XBRL periods are inclusive on both sides, so reusing that date
            # made the derived Q4 overlap the quarter before it by one day --
            # enough for the non-overlap scan to discard a real quarter, reach
            # back an extra one, and produce a 457-day "twelve months" that the
            # span guard then rejected. KO fell through to its FY2025 annual
            # and was reported as a 2.8% mismatch against a correct figure.
            nxt = (pd.to_datetime(inside["end"].max())
                   + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            derived.append({"start": nxt, "end": ar.end,
                            "val": float(ar.val) - float(inside["val"].sum()),
                            "filed": ar.filed})
        if derived:
            q = pd.concat([q[["start", "end", "val", "filed"]],
                           pd.DataFrame(derived)], ignore_index=True)
        q = q.sort_values("end", ascending=False)

        picked, cursor = [], None
        for r in q.itertuples(index=False):
            if cursor is not None and r.end >= cursor:
                continue                       # overlaps one already taken
            picked.append(r)
            cursor = r.start
            if len(picked) == 4:
                break
        if len(picked) < 4:
            continue
        span = (pd.to_datetime(picked[0].end)
                - pd.to_datetime(picked[-1].start)).days
        if not (350 <= span <= 380):
            continue                           # not actually twelve months
        # NO ROLL-FORWARD HERE, DELIBERATELY -- and this is a KNOWN LIMITATION.
        #
        # `fundamentals._ttm` rolls a year-to-date filing forward
        # (FY + YTD_now - YTD_a_year_ago) because that is the only correct TTM
        # for a cumulative cash flow statement. Hand-verified against the
        # filings, production is RIGHT:
        #
        #     KO    7,408.0 + 2,021.0 - (-5,202.0) = 14,631.0
        #     HD   16,325.0 + 6,032.0 -   4,325.0  = 18,032.0
        #     UPS   8,450.0 + 3,083.0 -   2,666.0  =  8,867.0
        #     AAPL   111.48B + 117.00B -   81.75B  =  146.73B
        #
        # Two attempts to teach this checker the same trick both made it WORSE:
        # applied eagerly it hijacked income-statement concepts where the
        # four-quarter path was already correct (AAPL revenue 431.5B against a
        # true 466.8B); applied as a last resort it silently failed to fire and
        # answered with the annual anyway. Overall pass rate went 97.6% -> 92.1%
        # -> 72.6% across those attempts.
        #
        # So this side reports the ANNUAL for cumulative concepts, and
        # `cfo_ttm`, `capex_ttm`, `sbc_ttm` and friends will show as mismatches
        # for filers whose stub has moved past the last 10-K. THOSE ARE
        # EXPECTED AND ARE NOT DATA BUGS. Check them by hand against the
        # filings, the way the four cases above were, before believing this
        # module over production.
        best = _better(best, float(sum(p.val for p in picked)),
                       str(picked[0].end))
    return best


def run(tickers: list[str], asof: str | None = None) -> int:
    asof = asof or datetime.now().strftime("%Y-%m-%d")
    tm = FD.ticker_map()
    cik_of = dict(zip(tm["ticker"], tm["cik"])) if "ticker" in tm.columns else {}

    print(f"verify_metrics | asof {asof} | {len(tickers)} ticker(s) | tol {TOL:.1%}")
    print("independent SEC arithmetic vs facts_asof -- checks the TTM layer,\n"
          "which the raw-quarter reconciliation never touched.\n")

    facts = FD.facts_asof(asof, tickers)
    if facts.empty:
        print("!! facts_asof returned nothing")
        return 1
    f = facts.set_index("ticker")

    bad = checked = missing = unreachable = stale = 0
    _facts_cache: dict = {}          # reused by the derived-row pass below
    # A TTM for an actively filing company must end within about a
    # year of `asof`; older than that and the two sides are not
    # describing the same period.
    stale_before = (pd.Timestamp(asof) - pd.Timedelta(days=400)
                    ).strftime('%Y-%m-%d')
    rows = []
    for tk in tickers:
        cik = cik_of.get(tk)
        if cik is None or tk not in f.index:
            print(f"  {tk:6s} no cik / not in facts -- skipped")
            continue
        facts_json = (_get(FACTS_URL.format(cik=int(cik))) or {}).get("facts") or {}
        _facts_cache[tk] = facts_json
        if not facts_json:
            print(f"  {tk:6s} SEC companyfacts unavailable -- cannot verify")
            unreachable += 1
            continue
        for concept, tags, kind in CHECKS:
            truth, end = sec_value(facts_json, tags, kind, asof)
            ours = pd.to_numeric(pd.Series([f.at[tk, concept]]),
                                 errors="coerce").iloc[0] \
                if concept in f.columns else None
            if truth is None or ours is None or pd.isna(ours):
                missing += 1
                rows.append((tk, concept, ours, truth, None, "no data"))
                continue
            # A GROUND TRUTH THAT IS YEARS OLD IS NOT A GROUND TRUTH.
            #
            # The companyfacts API and the bulk Financial Statement Data Sets
            # we ingest do not always carry the same tags: CSCO's
            # `EarningsPerShareDiluted` stops at 2010 in the API but is current
            # in the bulk files. Comparing our 2026 TTM against a 2010 figure
            # produced a 126% "mismatch" that said nothing about our data.
            # Refuse the comparison instead of failing it -- and count it, so
            # a shrinking comparable set cannot masquerade as a clean run.
            if end and end < stale_before:
                stale += 1
                rows.append((tk, concept, ours, truth, None,
                             f"sec data stale ({end})"))
                continue
            checked += 1
            diff = abs(ours - truth) / abs(truth) if truth else (
                0.0 if ours == 0 else 1.0)
            ok = diff <= TOL
            bad += (not ok)
            rows.append((tk, concept, ours, truth, diff, "OK" if ok else "MISMATCH"))
            if not ok:
                print(f"  {tk:6s} {concept:20s} ours={ours:>18,.2f} "
                      f"sec={truth:>18,.2f}  {diff*100:6.2f}%  <- MISMATCH "
                      f"(period {end})")

    # ------------------------------------------------------------- DERIVED
    # THE ROWS THE USER ACTUALLY READS, checked end to end.
    #
    # Everything above verifies an INPUT. COLL's EBITDA read $4M against a real
    # $68M while every input it is built from was correct, because the bug was
    # in the composition -- a missing D&A silently treated as zero. Checking
    # legs and never the thing built from them is how this module reported
    # 100% on a page that was wrong by 17x.
    #
    # EBITDA = operating income + D&A, where D&A is the reported TOTAL or, for
    # filers that report the halves separately, depreciation + intangible
    # amortisation. Computed here from SEC values, then compared to what our
    # pipeline produces for the same ticker.
    for tk in tickers:
        if tk not in f.index:
            continue
        g = lambda c: pd.to_numeric(pd.Series([f.at[tk, c]]), errors="coerce"
                                    ).iloc[0] if c in f.columns else None
        opinc = g("opinc_ttm")
        tot, dep, amo = g("dna_ttm"), g("deprec_ttm"), g("amort_ttm")
        comp = None
        if dep is not None and not pd.isna(dep) or amo is not None and not pd.isna(amo):
            comp = (0 if dep is None or pd.isna(dep) else dep) + \
                   (0 if amo is None or pd.isna(amo) else amo)
        dna = tot if (tot is not None and not pd.isna(tot)) else comp
        ours_eb = (opinc + dna) if (opinc is not None and not pd.isna(opinc)
                                    and dna is not None) else None

        # Independent side: same arithmetic, SEC's numbers.
        cik = cik_of.get(tk)
        sec_eb = None
        if cik is not None:
            fj = _facts_cache.get(tk)
            if fj:
                # BOTH LEGS MUST COVER THE SAME TWELVE MONTHS.
                #
                # Composing opinc from one window and D&A from another is not a
                # ground truth, it is a third wrong answer. COLL's first run of
                # this check compared our TTM ending 2026-06-30 against an
                # opinc ending 2026-06-30 plus an amortisation ending
                # 2025-12-31, and reported a 147% "mismatch" that was entirely
                # the checker's. Windows must agree or the comparison is
                # declined -- an honest "cannot say" beats a false alarm, which
                # is the same rule the stale-data guard already applies.
                so, so_end = sec_value(fj, FD.TAGS.get("opinc", []), "flow", asof)
                st, st_end = sec_value(fj, FD.TAGS.get("dna", []), "flow", asof)
                if st is None:
                    sd, sd_end = sec_value(fj, FD.TAGS.get("deprec", []), "flow", asof)
                    sa, sa_end = sec_value(fj, FD.TAGS.get("amort", []), "flow", asof)
                    if sd is None and sa is None:
                        st, st_end = None, None
                    elif sd is not None and sa is not None and sd_end != sa_end:
                        st, st_end = None, None      # halves disagree: decline
                    else:
                        st = (sd or 0) + (sa or 0)
                        st_end = sa_end or sd_end
                if so is not None and st is not None and so_end == st_end:
                    sec_eb = so + st
        if ours_eb is None or sec_eb is None or not sec_eb:
            rows.append((tk, "EBITDA(derived)", ours_eb, sec_eb, None,
                         "no comparison"))
            continue
        checked += 1
        diff = abs(ours_eb - sec_eb) / abs(sec_eb)
        ok = diff <= 0.05          # looser: composition of two TTM windows
        bad += (not ok)
        rows.append((tk, "EBITDA(derived)", ours_eb, sec_eb, diff,
                     "OK" if ok else "MISMATCH"))
        if not ok:
            print(f"  {tk:6s} {'EBITDA(derived)':20s} ours={ours_eb:>18,.0f} "
                  f"sec={sec_eb:>18,.0f}  {diff*100:6.2f}%  <- MISMATCH")

    out = pd.DataFrame(rows, columns=["ticker", "concept", "ours", "sec",
                                      "rel_diff", "status"])
    dest = config.DATA / "_verify_metrics.csv"
    out.to_csv(dest, index=False)

    print(f"\n{checked} field(s) compared, {bad} mismatch(es), "
          f"{missing} with no SEC data, {stale} where SEC's own copy is stale")
    print(f"detail -> {dest}")
    if checked:
        print(f"pass rate: {(checked - bad) / checked * 100:.1f}%")

    # A run that could not reach SEC is NOT a passing run. Say so, loudly, and
    # fail -- otherwise a throttled check reports "0 mismatches" and gets read
    # as a clean bill of health, which is the exact failure this whole module
    # exists to stop.
    if unreachable:
        print(f"!! {unreachable} ticker(s) could not be fetched from SEC at all")
        bad += unreachable
    if FETCH_ERRORS:
        print(f"!! {sum(FETCH_ERRORS.values())} SEC request failure(s): "
              f"{FETCH_ERRORS} -- results above are INCOMPLETE, not clean")
        bad += 1
    if checked and missing > checked:
        print(f"!! more fields missing ({missing}) than compared ({checked}) "
              f"-- treat this run as inconclusive")
        bad += 1
    # A negative share count or a nonpositive denominator is a bug regardless
    # of what SEC says, so assert it independently of the comparison.
    for col in ("shares_diluted_ttm", "shares_basic_ttm", "shares_out"):
        if col in f.columns:
            n = (pd.to_numeric(f[col], errors="coerce") < 0).sum()
            if n:
                print(f"!! {n} NEGATIVE value(s) in {col}")
                bad += n
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tickers", help="comma separated; default a 15-name set")
    ap.add_argument("--n", type=int, default=0,
                    help="instead, sample N tickers at random from the store")
    ap.add_argument("--asof", help="point-in-time date (default today)")
    ap.add_argument("--tradeable", action="store_true",
                    help="sample the TRADEABLE universe (what the screener "
                         "actually serves) rather than every CIK in the SEC "
                         "ticker map")
    a = ap.parse_args()

    if a.tickers:
        tickers = [t.strip().upper() for t in a.tickers.split(",") if t.strip()]
    elif a.n:
        import numpy as np
        # POPULATION MATTERS MORE THAN SAMPLE SIZE.
        #
        # Sampling the SEC ticker map draws from ~8,000 CIKs, thousands of
        # which the pipeline deliberately does not maintain -- names outside
        # `bars.tradeable_universe()` are never refetched, so they sit 226-591
        # days stale BY DESIGN. Measured 2026-08-15: a 60-name map sample
        # scored 65.1%, and every one of the worst offenders (MTEX, BRGX,
        # SPHL, BCTX, OABI, UWHR) was outside the tradeable universe.
        #
        # That number is real but it answers the wrong question. The question
        # is whether a name the SCREENER SHOWS can be trusted.
        if a.tradeable:
            import bars
            pool = sorted(set(bars.tradeable_universe()))
        else:
            tm = FD.ticker_map()
            pool = sorted(set(tm["ticker"].astype(str)))
        rng = np.random.default_rng(20260813)
        tickers = sorted(rng.choice(pool, size=min(a.n, len(pool)),
                                    replace=False).tolist())
    else:
        tickers = DEFAULT
    return run(tickers, a.asof)


if __name__ == "__main__":
    sys.exit(main())
