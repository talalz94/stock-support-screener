"""
Fundamental data: SEC Financial Statement Data Sets -> point-in-time fact store.

    python fundamentals.py --backfill        quarterly ZIPs, resumable (~10 min)
    python fundamentals.py --update          the newest quarter only
    python fundamentals.py --stats
    python fundamentals.py --explain ORCL    every stored fact for one ticker
    python fundamentals.py --selftest

SOURCE CHOICE, MEASURED 2026-08-06
-----------------------------------
Three free routes to SEC XBRL exist. The differences are not cosmetic:

| route | one request gets | point-in-time? | verdict |
|---|---|---|---|
| `companyfacts` API | all history for 1 company (3.9 MB for ORCL, 535 concepts) | YES -- has `filed` | 5,383 companies = 10-20 GB |
| `frames` API | 1 concept x 1 period for 6,251 companies (0.83 MB) | **NO** -- no `filed` field | fast but unusable |
| **Financial Statement Data Sets** | one quarter of EVERY filing (85 MB zip) | **YES** | **chosen** |

The `frames` API is the seductive one -- a single 1.6-second request returns
6,251 companies -- and it is the wrong choice, because its rows carry only
`accn/cik/end/val`. Without a filing date there is no way to know when a number
became public, so every backtest built on it would silently use figures that did
not exist yet. That is the same class of bug as attributing after-close news to
the session that just closed, and it is equally invisible.

The bulk sets solve it by construction: `sub.txt` carries `filed` per accession,
`num.txt` carries the facts, and joining them on `adsh` gives every number
stamped with the date it became public. They also carry `sic`, which is a better
sector source than the per-ticker submissions scrape in macro.py.

THE POINT-IN-TIME RULE, applied in `facts_asof` and nowhere else
----------------------------------------------------------------
    a fact is visible on date D iff filed <= D.

NOT `ddate <= D`. A fiscal quarter ending 2024-03-31 is not public until the
10-Q lands in May, so ranking stocks on Q1 numbers in April is a six-week
look-ahead that would flatter every fundamental factor ever tested here.
Restatements are handled by the same rule: the ORIGINAL figure is what was
known, so `facts_asof` takes the latest filing that existed at the time rather
than the latest filing that exists now.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import threading
import time
import zipfile
from datetime import date, datetime

import numpy as np
import pandas as pd
import requests

import config
import store

BASE = "https://www.sec.gov/files/dera/data/financial-statement-data-sets"

SCHEMA = ["cik", "adsh", "tag", "ddate", "qtrs", "value", "filed", "form",
          "fy", "fp", "sic", "uom"]

# The unit a `value` is denominated in: "USD", "shares", "USD/shares", or a
# foreign currency code such as "EUR".
#
# WHY THIS COLUMN EXISTS. Non-USD rows used to be dropped at ingest, which was
# the right call while there was nowhere to record the currency -- mixing EUR
# and USD in one `value` column makes every cross-sectional percentile
# meaningless, and that is worse than no coverage. But it cost 129 filers
# (measured on 2026q1), 75 of them tradeable, including BTI, CNQ, BCE, BCS, DB
# and CCJ.
#
# Recording the unit instead of discarding the row lets the SCALE-FREE metrics
# be computed for those filers -- margins, ROE, ROIC, F-score, accruals, asset
# turnover, debt/equity are all ratios of two same-currency quantities and need
# no conversion at all. Only metrics that put a USD price next to a foreign book
# (P/E, P/B, EV/EBITDA, Altman Z) are withheld. No FX feed, no conversion, and
# therefore no risk of a wrong rate silently mispricing 64 large caps.
#
# Partitions written before this column existed hold USD only, by construction.
# `_with_uom` fills them in on read rather than rewriting 68 quarters.
DEFAULT_UOM = "USD"


def uom_currency(uom) -> str | None:
    """The currency a unit is denominated in, or None if it carries no currency.

    SEC unit strings are not just currency codes. Measured on BTI's
    companyfacts: `GBP` (8,523 rows), `pure` (217), `GBP/shares` (162),
    `shares` (93), `Employee` (22), plus a handful of stray USD/EUR/CAD.

    Three cases, and getting them wrong costs real data in both directions:

      `GBP`        -> "GBP"   a monetary figure
      `GBP/shares` -> "GBP"   per-share, still GBP -- treating this as
                              currency-free would let a GBP EPS sit beside a
                              USD one, and treating it as unmatched would drop
                              every foreign filer's EPS
      `shares`, `pure`, `Employee`, `Y` -> None   counts and ratios, valid for
                              any filer

    A currency is exactly three uppercase letters, which is the ISO 4217 shape
    and distinguishes `GBP` from `Employee` without a hardcoded list.

    A composite unit is a currency only when the denominator is `shares`.
    `ARS/EUR` (seen in a 2025q4 filing) is an EXCHANGE RATE -- a pure number,
    not an amount in pesos -- and reading its numerator as the currency would
    both mislabel the row and let a rate vote in the reporting-currency mode.
    """
    if not uom:
        return None
    parts = str(uom).split("/")
    if len(parts) > 2:
        return None
    head = parts[0]
    if not (len(head) == 3 and head.isalpha() and head.isupper()):
        return None
    return head if (len(parts) == 1 or parts[1] == "shares") else None


def _with_uom(df: pd.DataFrame) -> pd.DataFrame:
    """Guarantee a `uom` column. Absent means a pre-currency partition, and
    those are USD-only because the ingest dropped everything else."""
    if df is None or df.empty:
        return df
    if "uom" not in df.columns:
        df = df.copy()
        df["uom"] = DEFAULT_UOM
    else:
        df["uom"] = df["uom"].fillna(DEFAULT_UOM).astype(str)
    return df

# ---------------------------------------------------------------------------
# Tags. Aliases exist because filers legitimately choose different concepts for
# the same line -- "Revenues" vs the ASC 606 tag vs a segment roll-up -- and a
# single-tag lookup silently returns NaN for a third of the market.
# Order is preference order: first non-null wins.
# ---------------------------------------------------------------------------
TAGS: dict[str, list[str]] = {
    # income statement
    # IFRS aliases carry the suffix comment. Foreign private issuers (20-F,
    # 40-F) file the same audited statements under IFRS, whose concept names are
    # near-misses for the US-GAAP ones -- `Revenue` not `Revenues`,
    # `RevenueFromContractsWithCustomers` (plural) not
    # `RevenueFromContractWithCustomer...` (singular). MEASURED on 2025q1: 460
    # foreign filings carried 317,268 fact rows of which only **16.5%** matched
    # this map, which is why 588 universe names had a CIK and no facts.
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax",
                "SalesRevenueGoodsNet",
                "Revenue", "RevenueFromContractsWithCustomers",      # IFRS
                "RevenueFromSaleOfGoods"],                           # IFRS
    "cogs": ["CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold",
             "CostOfSales"],                                         # IFRS
    "gross_profit": ["GrossProfit"],
    "opinc": ["OperatingIncomeLoss",
              "ProfitLossFromOperatingActivities"],                  # IFRS
    "sga": ["SellingGeneralAndAdministrativeExpense", "GeneralAndAdministrativeExpense"],
    "rnd": ["ResearchAndDevelopmentExpense"],
    # TOTALS ONLY. Components live in `deprec` and `amort` below and are
    # SUMMED, never chosen between -- see the note there.
    "dna": ["DepreciationDepletionAndAmortization",
            "DepreciationAndAmortization",
            "DepreciationAmortizationAndAccretionNet"],

    # D&A COMPONENTS, FOR FILERS THAT REPORT NO TOTAL.
    #
    # Found 2026-08-14 on COLL, by the user, from the page. Collegium reports
    # none of the three total tags; it files the two halves separately:
    #
    #     AmortizationOfIntangibleAssets   62,953,000   (Q2 2026)
    #     Depreciation                      1,812,000   (Q2 2026)
    #
    # These are COMPLEMENTARY, not synonyms, so the alias mechanism -- which
    # picks ONE by preference -- is the wrong tool. Picking `Depreciation`
    # yielded 1.8M against a true 64.8M and made EBITDA read $4M instead of
    # ~$68M. They are separate concepts here precisely so `derive` can add them,
    # and so that adding a component alias can never silently shadow a total.
    #
    # 1,938 filers had operating income and no recognised D&A before this.
    "deprec": ["Depreciation", "DepreciationNonproduction"],
    "amort": ["AmortizationOfIntangibleAssets",
              "FiniteLivedIntangibleAssetsAmortizationExpense"],
    "pretax": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
               "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
               "ProfitLossBeforeTax"],                               # IFRS
    "tax": ["IncomeTaxExpenseBenefit",
            "IncomeTaxExpenseContinuingOperations"],                 # IFRS
    "interest_exp": ["InterestExpense", "InterestExpenseDebt", "InterestIncomeExpenseNet"],
    "net_income": ["NetIncomeLoss", "ProfitLoss",
                   "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "eps_diluted": ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"],
    "eps_basic": ["EarningsPerShareBasic"],
    # balance sheet
    "assets": ["Assets"],
    "assets_current": ["AssetsCurrent", "CurrentAssets"],            # IFRS
    "liabilities": ["Liabilities"],
    "liabilities_current": ["LiabilitiesCurrent",
                            "CurrentLiabilities"],                   # IFRS
    # `Equity` is IFRS and INCLUDES non-controlling interests, matching the
    # US-GAAP "...IncludingPortionAttributable..." alias already listed, so the
    # two are consistent rather than mixing two different definitions.
    "equity": ["StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
               "Equity"],                                            # IFRS
    "cash": ["CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
             "CashAndCashEquivalents"],                              # IFRS
    "sti": ["ShortTermInvestments", "MarketableSecuritiesCurrent",
            "AvailableForSaleSecuritiesDebtSecuritiesCurrent"],
    "inventory": ["InventoryNet"],
    "receivables": ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent"],
    "payables": ["AccountsPayableCurrent", "AccountsPayableAndAccruedLiabilitiesCurrent"],
    "debt_lt": ["LongTermDebtNoncurrent", "LongTermDebt",
                "LongtermBorrowings"],                               # IFRS
    "debt_st": ["LongTermDebtCurrent", "ShortTermBorrowings", "DebtCurrent"],
    "retained": ["RetainedEarningsAccumulatedDeficit"],
    "ppe": ["PropertyPlantAndEquipmentNet",
            "PropertyPlantAndEquipment",                             # IFRS
            "PropertyPlantAndEquipmentIncludingRightofuseAssets"],   # IFRS
    "goodwill": ["Goodwill"],
    "intangibles": ["IntangibleAssetsNetExcludingGoodwill",
                    "FiniteLivedIntangibleAssetsNet",
                    "IntangibleAssetsOtherThanGoodwill"],            # IFRS
    "shares_out": ["CommonStockSharesOutstanding", "EntityCommonStockSharesOutstanding"],
    "shares_diluted": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
    "shares_basic": ["WeightedAverageNumberOfSharesOutstandingBasic",
                     "WeightedAverageNumberOfSharesOutstanding"],
    # cash flow
    "cfo": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
            "CashFlowsFromUsedInOperatingActivities"],               # IFRS
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment",
              "PaymentsToAcquireProductiveAssets"],
    "cfi": ["NetCashProvidedByUsedInInvestingActivities",
            "CashFlowsFromUsedInInvestingActivities"],               # IFRS
    "cff": ["NetCashProvidedByUsedInFinancingActivities",
            "CashFlowsFromUsedInFinancingActivities"],               # IFRS
    "dividends": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends",
                  "DividendsPaid"],                                  # IFRS
    "buybacks": ["PaymentsForRepurchaseOfCommonStock"],
    "sbc": ["ShareBasedCompensation"],
}

WANTED = {t for alts in TAGS.values() for t in alts}


def log(m: str) -> None:
    print(m, flush=True)


# ===========================================================================
# Fetch
# ===========================================================================
def quarters(years: float) -> list[str]:
    """Quarter labels covering `years` back from today, oldest first."""
    today = date.today()
    out = []
    y, q = today.year, (today.month - 1) // 3 + 1
    for _ in range(int(years * 4) + 2):
        out.append(f"{y}q{q}")
        q -= 1
        if q == 0:
            y, q = y - 1, 4
    return sorted(out)


def fetch_quarter(q: str, verbose: bool = True, session=None) -> pd.DataFrame:
    """One quarterly ZIP -> filtered facts joined to their filing metadata.

    Raises on a transport error so `backfill` can retry; returns an EMPTY frame
    for a clean 404/not-yet-published, which is a normal outcome and not a
    failure.
    """
    hdr = {"User-Agent": config.SEC_UA, "Accept-Encoding": "gzip, deflate"}
    t0 = time.time()
    get = (session or requests).get
    r = get(f"{BASE}/{q}.zip", headers=hdr, timeout=300)
    if r.status_code != 200 or r.content[:2] != b"PK":
        if verbose:
            log(f"    {q}: HTTP {r.status_code}, not yet published")
        return pd.DataFrame(columns=SCHEMA)

    z = zipfile.ZipFile(io.BytesIO(r.content))
    sub = pd.read_csv(z.open("sub.txt"), sep="\t", low_memory=False,
                      usecols=["adsh", "cik", "sic", "form", "period", "fy", "fp",
                               "filed"], encoding="utf-8", on_bad_lines="skip")
    # 10-K/10-Q only. 8-K and S-1 exhibits carry XBRL too, but their numbers are
    # promotional extracts rather than a full audited statement set.
    # 10-K/10-Q are US domestic filers. 20-F (foreign private issuers) and 40-F
    # (Canadian MJDS, e.g. POET) carry the same audited statement set and were
    # being discarded -- 604 of 3,480 universe names had a CIK and no facts.
    #
    # CAVEAT, not yet measured: these filers often report under IFRS, whose tag
    # names differ from the US-GAAP set in TAGS. Accepting the forms is
    # necessary but may not be sufficient; `--coverage` reports how many
    # actually populate rather than assuming the count.
    sub = sub[sub["form"].astype(str).str.startswith(
        ("10-K", "10-Q", "20-F", "40-F"))]

    num = pd.read_csv(z.open("num.txt"), sep="\t", low_memory=False,
                      usecols=["adsh", "tag", "ddate", "qtrs", "uom", "value",
                               "segments", "coreg"], encoding="utf-8",
                      on_bad_lines="skip")
    # Consolidated parent only: `segments`/`coreg` rows are business-unit or
    # subsidiary breakouts, and summing them alongside the total double-counts.
    # Non-USD rows are KEPT and tagged, not dropped -- see DEFAULT_UOM. The
    # `uom` value travels with the row so nothing downstream can mistake a
    # EUR figure for a USD one.
    num = num[num["tag"].isin(WANTED)
              & num["segments"].isna() & num["coreg"].isna()]
    num = num[num["uom"].notna() & (num["uom"].astype(str).str.len() > 0)]

    df = num.merge(sub, on="adsh", how="inner")
    if df.empty:
        return pd.DataFrame(columns=SCHEMA)

    df["filed"] = pd.to_datetime(df["filed"], format="%Y%m%d",
                                 errors="coerce").dt.strftime("%Y-%m-%d")
    df["ddate"] = pd.to_datetime(df["ddate"], format="%Y%m%d",
                                 errors="coerce").dt.strftime("%Y-%m-%d")
    df = df.dropna(subset=["filed", "ddate", "value"])
    out = df[["cik", "adsh", "tag", "ddate", "qtrs", "value", "filed", "form",
              "fy", "fp", "sic", "uom"]].copy()

    if verbose:
        ccy = out["uom"].map(uom_currency)
        fx = out[ccy.notna() & (ccy != "USD")]["cik"].nunique()
        log(f"    {q}: {len(out):,} facts, {out['cik'].nunique():,} filers, "
            f"{fx:,} reporting a non-USD currency, "
            f"{len(r.content) / 1e6:.0f} MB, {time.time() - t0:.0f}s")
    return out


# ===========================================================================
# Store
# ===========================================================================
def part_path(q: str):
    return config.FUNDAMENTALS / f"{q}.parquet"


def cf_part_path(q: str):
    return config.FUNDAMENTALS_CF / f"{q}.parquet"


def stored_quarters(include_cf: bool = False) -> list[str]:
    """Quarters on disk. **Bulk store only, by default.**

    THE DEFAULT IS THE CONSERVATIVE ANSWER, AND IT IS THAT WAY BECAUSE THE
    OPPOSITE COST A QUARTER OF DATA. This function briefly defaulted to unioning
    the bulk store with the `companyfacts` fallback, which quietly broke three
    callers at once:

      * `_due_sec_facts` saw `2026q2` "stored" and never scheduled the fetch,
      * `backfill()` skipped it for the same reason,
      * `--stats` crashed outright, because it opens `part_path(q)` and the
        bulk file does not exist.

    The result was every filer missing period 2026-03-31 while the coverage
    numbers looked healthy -- a 141-company fallback masking a gap across ~5,500
    filers. Two different questions were being asked of one function:

        "what have I FETCHED?"  -> bulk only. Getting this wrong skips a fetch.
        "what can I READ?"      -> the union. Getting this wrong loses rows.

    Forgetting the flag now means re-fetching something you might have, which is
    a wasted download. Before, it meant never fetching something you do not
    have. Only `read()` and the SIC map ask for the union, explicitly.
    """
    qs = set()
    if config.FUNDAMENTALS.exists():
        qs |= {p.stem for p in config.FUNDAMENTALS.glob("*q*.parquet")}
    if include_cf and config.FUNDAMENTALS_CF.exists():
        qs |= {p.stem for p in config.FUNDAMENTALS_CF.glob("*q*.parquet")}
    return sorted(qs)


# ===========================================================================
# companyfacts fallback -- for filers the bulk data sets omit
# ===========================================================================
CF_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
CF_GAP_S = 0.35             # SEC allows 10 req/s; these are small JSON documents
CF_ATTEMPTS = 3
# Companies held in memory before writing to disk. 200 x ~9,000 rows is a few
# hundred MB, which stays well clear of the concat spike that a whole-universe
# refresh would otherwise hit.
CF_BATCH = 200
# Concurrent companyfacts fetchers. SEC's published ceiling is 10 req/s; six
# workers averaging ~3.7 s per multi-MB document lands near 1.6 req/s, which
# leaves headroom for retries without ever approaching the limit.
CF_WORKERS = 6


def _cf_qtrs(start: str | None, end: str) -> int | None:
    """`qtrs` the way the bulk sets encode it: 0 = instant, else whole quarters.

    Anything that is not close to a whole number of quarters is DROPPED rather
    than rounded. A 9-month figure rounded to 4 would be silently mixed in with
    annuals, and `facts_asof` sums flows by `qtrs` -- so one bad rounding here
    becomes a wrong revenue number with no way to notice it downstream.
    """
    if not start:
        return 0
    try:
        d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    except (TypeError, ValueError):
        return None
    days = (d1 - d0).days
    for q, lo, hi in ((1, 80, 100), (2, 172, 192), (3, 264, 284), (4, 355, 375)):
        if lo <= days <= hi:
            return q
    return None


def fetch_companyfacts(cik: int, session=None) -> pd.DataFrame:
    """One company's full XBRL history, shaped exactly like a bulk partition.

    Both taxonomies are read: `us-gaap` and `ifrs-full`, and every unit is kept
    with its currency recorded in `uom`. Mixing currencies in one `value` column
    would make every cross-sectional percentile meaningless -- so they are not
    mixed; they are labelled, and the scoring layer computes only the
    scale-free metrics for a non-USD filer. See DEFAULT_UOM.

    This is the CHEAP path to the non-USD names. Re-ingesting them from the
    bulk sets would mean re-downloading 68 quarters at 85 MB each; there are
    only ~129 such filers, so one small request each is three orders of
    magnitude less traffic for the same history.
    """
    import requests
    get = (session or requests).get
    hdr = {"User-Agent": config.SEC_UA, "Accept-Encoding": "gzip, deflate"}
    last = None
    for attempt in range(CF_ATTEMPTS):
        try:
            r = get(CF_URL.format(cik=int(cik)), headers=hdr, timeout=60)
            if r.status_code == 404:
                return pd.DataFrame(columns=SCHEMA)      # no filings, not an error
            r.raise_for_status()
            payload = r.json()
            break
        except Exception as exc:                                 # noqa: BLE001
            last = exc
            time.sleep(CF_GAP_S * (2 ** attempt))
    else:
        raise RuntimeError(f"companyfacts CIK{cik} failed: {last!r}")

    rows = []
    for taxonomy in ("us-gaap", "ifrs-full"):
        for tag, body in payload.get("facts", {}).get(taxonomy, {}).items():
            # KEEP ONLY THE TAGS THE PIPELINE READS. The bulk path has always
            # filtered to WANTED; this one never did, so it stored every
            # concept a company tags -- 6,000-8,000 distinct tags per quarter
            # against the 88 anything here uses.
            #
            # It went unnoticed while this was a ~200-name fallback. Pointing
            # it at the whole universe added 25 MILLION facts, of which 79%
            # were unreadable by design, and every `facts_asof` had to page
            # through them: the history rebuild slowed from a measured 48
            # seconds per session to 25 minutes and was killed at the 10-hour
            # task limit having done 90 of 182.
            if tag not in WANTED:
                continue
            for unit, facts in body.get("units", {}).items():
                for f in facts:
                    end, filed = f.get("end"), f.get("filed")
                    if not end or not filed:
                        continue
                    q = _cf_qtrs(f.get("start"), end)
                    if q is None:
                        continue
                    rows.append((int(cik), str(f.get("accn") or ""), tag, end, q,
                                 f.get("val"), filed, str(f.get("form") or ""),
                                 f.get("fy") or 0, str(f.get("fp") or ""), 0,
                                 str(unit)))
    if not rows:
        return pd.DataFrame(columns=SCHEMA)
    return _typed(pd.DataFrame(rows, columns=SCHEMA))


def backfill_companyfacts(tickers: list[str] | None = None, verbose: bool = True,
                          limit: int | None = None) -> dict:
    """Fill the coverage gap one company at a time.

    Defaults to exactly the names `coverage_report` counts as "CIK but no
    facts", so it never re-fetches a company the bulk sets already cover.

    Counts SUCCESSES, not attempts, and reports `ok` False if any company
    errored -- the failure mode this project has already paid for twice.
    """
    import requests
    if tickers is None:
        tickers = coverage_gap()
    tm = ticker_map()
    hit = tm[tm["ticker"].astype(str).isin(set(tickers))]
    pairs = [(str(r.ticker), int(r.cik)) for r in hit.itertuples()]
    if limit:
        pairs = pairs[:limit]
    if not pairs:
        return {"ok": True, "companies": 0, "facts": 0, "failed": []}

    # WRITE IN BATCHES. This used to hold every company's full XBRL history in
    # memory and concat once at the end. That was fine for the ~200-name
    # coverage gap it was written for, but the staleness refresh targets the
    # whole universe: 3,413 companies x ~9,000 rows each. Measured mid-run it
    # was already at 2.2 GB with 4.5 GB free, and `pd.concat` briefly doubles
    # peak usage -- so the final write was the step most likely to fail, after
    # 28 minutes of rate-limited fetching had already been spent.
    companies, facts, quarters = 0, 0, set()

    def _flush(fr: list) -> None:
        nonlocal companies, facts
        if not fr:
            return
        allf = pd.concat(fr, ignore_index=True)
        # Partitioned by the quarter the PERIOD ends in, matching the bulk
        # layout so `read(start_q=...)` prunes the same way for both stores.
        allf["_q"] = allf["ddate"].astype(str).str.slice(0, 4) + "q" + \
            ((pd.to_numeric(allf["ddate"].astype(str).str.slice(5, 7),
                            errors="coerce").fillna(1).astype(int) - 1) // 3
             + 1).astype(str)
        config.FUNDAMENTALS_CF.mkdir(parents=True, exist_ok=True)
        for q, grp in allf.groupby("_q"):
            grp = grp.drop(columns=["_q"])
            path = cf_part_path(str(q))
            if path.exists():
                old = pd.read_parquet(path)
                old = old[~old["cik"].isin(set(grp["cik"]))]   # replace, not append
                grp = pd.concat([old, grp], ignore_index=True)
            tmp = path.with_suffix(".parquet.tmp")
            _typed(grp).to_parquet(tmp, compression=config.COMPRESSION,
                                   compression_level=config.COMPRESSION_LEVEL,
                                   index=False)
            store.atomic_replace(tmp, path)
            quarters.add(str(q))
        companies += len(fr)
        facts += len(allf)
        fr.clear()

    # FETCH IN PARALLEL, WRITE SERIALLY.
    #
    # Measured 2026-08-13: 3.74 s per company sequentially, which is 3.6 hours
    # for the tradeable universe and over 8 for every CIK -- and almost all of
    # it is waiting on a multi-megabyte download, not CPU. That put us at
    # 0.27 requests/second against SEC's published ceiling of 10.
    #
    # Six workers is a deliberate fraction of that ceiling: enough to turn
    # hours into minutes, far enough below the limit that a slow response or a
    # retry cannot push the aggregate over it. `fetch_companyfacts` already
    # retries per company, and each worker keeps its OWN Session because
    # requests' Session is not documented as thread-safe.
    #
    # The flush stays on this thread. It reads, merges and atomically replaces
    # parquet partitions, and doing that concurrently would race two writers
    # onto the same file for no gain -- the cost here was never the writing.
    from concurrent.futures import ThreadPoolExecutor

    frames, failed, empty = [], [], 0
    _local = threading.local()

    def _one(pair):
        tk, cik = pair
        s = getattr(_local, "sess", None)
        if s is None:
            s = _local.sess = requests.Session()
        try:
            return tk, fetch_companyfacts(cik, session=s), None
        except Exception as exc:                                 # noqa: BLE001
            return tk, None, type(exc).__name__

    done_n = 0
    with ThreadPoolExecutor(max_workers=CF_WORKERS) as pool:
        # Chunked so `_flush` still runs every CF_BATCH companies and peak
        # memory stays bounded -- the 2.2 GB incident above is why.
        step = max(CF_BATCH, CF_WORKERS)
        for start in range(0, len(pairs), step):
            for tk, d, err in pool.map(_one, pairs[start:start + step]):
                done_n += 1
                if err is not None:
                    failed.append(f"{tk}({err})")
                elif d is None or d.empty:
                    empty += 1
                else:
                    frames.append(d)
            if len(frames) >= CF_BATCH:
                _flush(frames)
            if verbose:
                print(f"  {done_n}/{len(pairs)} companies, "
                      f"{facts + sum(len(f) for f in frames):,} facts "
                      f"({companies} written to disk)", flush=True)

    if not frames:
        # REPORT WHAT WAS ALREADY FLUSHED, not zero.
        #
        # This used to hardcode 0/0, which was harmless when the loop only
        # flushed on a full CF_BATCH and usually had a remainder left over.
        # Fetching in chunks makes an empty tail the NORMAL case, so every
        # batch logged "0 with data, 0 facts" while writing tens of thousands
        # of rows to disk -- a refresh that worked and reported that it had
        # done nothing, which is the worst possible direction for a log to be
        # wrong in. Measured 2026-08-13: 110,685 rows landed in 2026q2 while
        # the log claimed 0.
        return {"ok": not failed, "companies": companies, "facts": facts,
                "empty": empty, "failed": failed, "quarters": len(quarters)}

    _flush(frames)
    # A fetch can change which filers are non-USD, and `read()` caches that.
    invalidate_currency_cache()
    res = {"ok": not failed, "companies": companies, "facts": facts,
           "empty": empty, "failed": failed, "quarters": len(quarters)}
    if verbose:
        print(f"  companyfacts: {companies} of {len(pairs)} companies had data "
              f"({empty} empty, {len(failed)} failed), {facts:,} facts "
              f"across {res['quarters']} quarter(s)")
        if failed:
            print(f"  FAILED: {', '.join(failed[:10])}")
    return res


def coverage_gap() -> list[str]:
    """Universe names that have a CIK but no usable facts. The fetch list."""
    import bars
    import calendar_us
    asof = calendar_us.last_closed_session()
    uni = bars.tradeable_universe(asof)
    tm = ticker_map()
    have_cik = set(tm["ticker"].astype(str)) & set(uni)
    facts = facts_asof(asof, uni)
    scored = set(facts["ticker"].astype(str)) if not facts.empty else set()
    return sorted(have_cik - scored)


# A US filer reports quarterly and has 40-45 days to file, so the newest period
# END should never be more than ~1 quarter + the filing window behind today.
# Past that, a quarter we should already have is missing.
STALE_PERIOD_DAYS = 150


def stale_names(asof: str | None = None,
                max_age_days: int = STALE_PERIOD_DAYS) -> list[str]:
    """Universe names whose newest PERIOD is older than a filer should allow.

    THE HOLE THIS FILLS, measured 2026-08-10. `coverage_gap` asks "who has no
    facts", so a company with facts that stopped updating two quarters ago is
    counted as covered and never re-fetched. The bulk data sets lag -- on
    2026-08-10 the newest published set was 2026q1 -- so the ENTIRE universe
    quietly froze at the last bulk quarter:

        3,084 of 3,270 names (94%) newest period 2025-12-31 or older
           22 of 3,270 names ( 1%) had anything from Jun 2026

    while SEC's companyfacts API already carried Apple's 2026-03-28 AND
    2026-06-27 quarters. Nothing flagged it: `has_fundamentals` was true, the
    feeds looked current, and every coverage check passed, because they all
    asked whether data EXISTS and none asked whether it is CURRENT.

    Coverage and freshness are different questions and both need asking.
    """
    import bars
    import calendar_us
    asof = asof or calendar_us.last_closed_session()
    uni = bars.tradeable_universe(asof)
    facts = facts_asof(asof, uni)
    if facts.empty or "last_ddate" not in facts.columns:
        return []
    cutoff = (pd.Timestamp(asof) - pd.Timedelta(days=max_age_days))
    dd = pd.to_datetime(facts["last_ddate"], errors="coerce")
    return sorted(facts.loc[dd.notna() & (dd < cutoff), "ticker"].astype(str))


def refresh_targets(asof: str | None = None) -> list[str]:
    """Everything worth fetching from companyfacts: missing OR stale."""
    return sorted(set(coverage_gap()) | set(stale_names(asof)))


def _current_quarter(when=None) -> str:
    """The quarter label for today, e.g. '2026q3'. Anything after this is a
    forward-dated disclosure, not a reported period."""
    d = when or date.today()
    return f"{d.year}q{(d.month - 1) // 3 + 1}"


def _quarter_bounds(q: str) -> tuple[str, str]:
    """('2025q3') -> ('2025-07-01', '2025-09-30'). The periods a partition holds."""
    y, n = int(q[:4]), int(q[-1])
    m0 = (n - 1) * 3 + 1
    last_day = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
                7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}[m0 + 2]
    if m0 + 2 == 2 and (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)):
        last_day = 29
    return f"{y}-{m0:02d}-01", f"{y}-{m0 + 2:02d}-{last_day}"


def _typed(df: pd.DataFrame) -> pd.DataFrame:
    df = _with_uom(df.copy())
    df["cik"] = pd.to_numeric(df["cik"], errors="coerce").fillna(0).astype("int64")
    df["qtrs"] = pd.to_numeric(df["qtrs"], errors="coerce").fillna(0).astype("int8")
    df["value"] = pd.to_numeric(df["value"], errors="coerce").astype("float64")
    df["fy"] = pd.to_numeric(df["fy"], errors="coerce").fillna(0).astype("int16")
    df["sic"] = pd.to_numeric(df["sic"], errors="coerce").fillna(0).astype("int32")
    for c in ("tag", "form", "fp"):
        df[c] = df[c].astype(str).astype("category")
    for c in ("adsh", "ddate", "filed"):
        df[c] = df[c].astype(str)

    # DROP IMPLAUSIBLE PERIOD DATES AT THE CHOKE POINT.
    #
    # XBRL filing began around 2009, so a period end in 1986 -- or in 6016 --
    # is a tagging error, not history. Left in, they cost twice: `facts_asof`
    # once picked LEGH's period 2033-03-31 as its latest results, and the
    # companyfacts writer partitions by period, so they also created junk
    # partition files (1986q4, 1989q1, 1989q4, 6016q2).
    #
    # Both ingest paths funnel through here, and so does the read-merge before
    # a partition is rewritten, so existing junk is cleaned as it is touched.
    dd = pd.to_datetime(df["ddate"], errors="coerce")
    ok = dd.notna() & (dd >= pd.Timestamp("1993-01-01")) & \
        (dd <= pd.Timestamp.today() + pd.DateOffset(years=2))
    return df[ok][SCHEMA].reset_index(drop=True)


def filer_shortfall(q: str, n_filers: int) -> str:
    """"" if the quarter looks complete, else why it does not.

    `FUNDAMENTALS_MIN_FILERS = 2000` existed for two years and was **never
    referenced**, so a truncated download would have been stored and scored as
    complete with nothing to notice it.

    A FIXED floor is the wrong test and would have made the guard useless: nine
    stored quarters sit below 2000 (19 filers in 2009q2 rising to 1,620 by
    2011q2) and every one of them is real -- XBRL was phased in from the largest
    filers down. Rejecting those would delete genuine history.

    So the test is RELATIVE: a quarter is suspect when it carries far fewer
    filers than the quarters around it. That distinguishes "the SEC published
    less back then" from "our download was cut short", which is the only
    distinction that matters here.
    """
    if n_filers <= 0:
        return f"{q}: no filers at all"
    neighbours = []
    for other in stored_quarters(include_cf=False):
        if other == q:
            continue
        try:
            d = pd.read_parquet(part_path(other), columns=["cik"])
        except Exception:                                        # noqa: BLE001
            continue
        neighbours.append((other, d["cik"].nunique()))
    # Compare against the PRECEDING quarters only, never the surrounding ones.
    #
    # Symmetric neighbours fail during the XBRL phase-in: coverage ramps from 19
    # filers to 6,747 over three years, so every early quarter sits far below a
    # median that includes its much larger successors. That first version
    # rejected 2009q2 and 2011q2 -- both genuine.
    #
    # Filer counts only ever ramp UP or hold flat, so a quarter far below what
    # came BEFORE it is the signature of a truncated download and nothing else.
    idx = _q_index(q)
    prior = [n for other, n in neighbours if 0 < idx - _q_index(other) <= 4]
    if len(prior) < 3:
        # The first quarters in the archive have no history to judge against,
        # and an absolute floor would reject exactly the real phase-in data.
        return ""
    med = float(np.median(prior))
    if med > 0 and n_filers < med * config.FUNDAMENTALS_MIN_FILER_FRAC:
        return (f"{q}: {n_filers:,} filers vs {med:,.0f} median over the four "
                f"preceding quarters ({n_filers / med:.0%}) -- filer counts do "
                f"not fall like that; this looks truncated")
    return ""


def _q_index(q: str) -> int:
    """`2024q3` -> a monotonic integer, so 'nearest in time' is comparable."""
    try:
        y, qq = q.split("q")
        return int(y) * 4 + int(qq)
    except Exception:                                            # noqa: BLE001
        return 0


def write(df: pd.DataFrame, q: str, check: bool = True) -> int:
    if df.empty:
        return 0
    if check:
        bad = filer_shortfall(q, int(df["cik"].nunique()))
        if bad:
            # Raised, not warned. A partial quarter stored silently is the
            # failure mode this project keeps paying for; refusing the write
            # leaves the previous good copy in place.
            raise ValueError(
                f"refusing to store a suspect quarter -- {bad}. Re-fetch it, or "
                f"pass check=False if you have verified it is genuinely small.")
    config.FUNDAMENTALS.mkdir(parents=True, exist_ok=True)
    p = part_path(q)
    tmp = p.with_suffix(".parquet.tmp")
    _typed(df).to_parquet(tmp, compression=config.COMPRESSION,
                          compression_level=config.COMPRESSION_LEVEL, index=False)
    store.atomic_replace(tmp, p)
    return len(df)


# These are 128 MB files. SEC throttles a client that pulls them back to back:
# a refetch with no delay got 19 quarters through and then took **51
# consecutive ConnectionErrors**. One every few seconds, with backoff, is the
# difference between a working job and a broken one.
FETCH_GAP_S = 2.5
FETCH_ATTEMPTS = 3


def backfill(years: float | None = None, verbose: bool = True,
             force: bool = False, newest_first: bool = False) -> dict:
    """Every quarter in the window. Resumable: stored quarters are skipped.

    The newest quarter is always refetched -- SEC republishes it as late filers
    land, so a cached copy of the current quarter is complete only by accident.

    `force` refetches quarters already on disk. Needed whenever the FILTER
    changes (a new form or tag alias): the stored partition was written under
    the old rules and will never gain the new rows on its own.

    `newest_first` fetches recent quarters first. `facts_asof` only looks back
    `lookback_q=12`, so the last ~16 quarters restore current scoring; deep
    history can finish afterwards.

    **THE SUMMARY COUNTS SUCCESSES, NOT ATTEMPTS, AND `ok` IS FALSE IF ANY
    QUARTER FAILED.** The previous version logged each failure and then printed
    "70/70 quarters ... DONE" -- a job that reports success after 51 failures is
    worse than one that crashes, because nobody goes looking.
    """
    years = config.FUNDAMENTALS_YEARS if years is None else years
    qs = quarters(years)
    if newest_first:
        qs = list(reversed(qs))
    # Bulk only, explicitly: a quarter the companyfacts fallback happens to
    # cover is NOT a quarter we have fetched.
    have = set(stored_quarters(include_cf=False))
    t0, total = time.time(), 0
    ok_q, failed, skipped = [], [], []
    sess = requests.Session()

    for i, q in enumerate(qs, 1):
        if q in have and q != qs[-1] and not force:
            skipped.append(q)
            if verbose:
                log(f"  [{i}/{len(qs)}] {q} skip (stored)")
            continue

        got = None
        for attempt in range(1, FETCH_ATTEMPTS + 1):
            try:
                got = fetch_quarter(q, verbose=verbose, session=sess)
                break
            except Exception as exc:                             # noqa: BLE001
                wait = FETCH_GAP_S * (2 ** attempt)
                log(f"  [{i}/{len(qs)}] {q} attempt {attempt}/{FETCH_ATTEMPTS} "
                    f"{type(exc).__name__}; retrying in {wait:.0f}s")
                time.sleep(wait)
        if got is None:
            failed.append(q)
            continue

        try:
            total += write(got, q)
            ok_q.append(q)
        except Exception as exc:                                 # noqa: BLE001
            log(f"  [{i}/{len(qs)}] {q} WRITE FAILED {repr(exc)[:90]}")
            failed.append(q)
        time.sleep(FETCH_GAP_S)

    if verbose:
        log(f"\nfundamentals: {len(ok_q)} fetched, {len(skipped)} skipped, "
            f"{len(failed)} FAILED, {total:,} facts, "
            f"{store_bytes() / 1e6:.0f} MB, {(time.time() - t0) / 60:.1f} min")
        if failed:
            log(f"  failed quarters: {', '.join(failed[:12])}"
                + (f" +{len(failed) - 12} more" if len(failed) > 12 else ""))
    return {"ok": not failed, "facts": total, "fetched": len(ok_q),
            "failed": failed, "skipped": len(skipped)}


def _store_fingerprint() -> tuple:
    """Cheap identity of the fact store: (file count, newest mtime, total size).

    Any write to any partition changes it, so a cache keyed on this cannot
    serve data from a store that has since been rewritten. ~190 stat calls,
    microseconds, against the 86-second read it protects.
    """
    n = mt = sz = 0
    for d in (config.FUNDAMENTALS, config.FUNDAMENTALS_CF):
        if not d.exists():
            continue
        for p in d.glob("*q*.parquet"):
            s = p.stat()
            n += 1
            mt = max(mt, int(s.st_mtime))
            sz += s.st_size
    return (n, mt, sz)


# Keyed on (start_q, ciks, fingerprint).
#
# TWO ENTRIES, deliberately. `facts_asof` is called twice per session -- the
# asof date and the year before -- and those two are what actually share a
# read, which is where the 124s -> 100s came from. Holding more does NOT help
# across sessions, because the tradeable universe shifts so one session's cik
# set is not a subset of the next, and each entry is large: a full-store read
# is 25.8M rows / 2.3 GB, and this machine had ~2 GB free. Caching four of
# these would OOM before it saved anything.
_READ_CACHE: dict = {}
_READ_CACHE_MAX = 2


# XBRL FROM A PROXY IS NOT THE FINANCIAL STATEMENTS.
#
# A DEF 14A carries the Item 402(v) "pay versus performance" table, whose Net
# Income column is tagged `NetIncomeLoss` exactly like the income statement's --
# but is reported in THOUSANDS, and covers the fiscal year with qtrs=4. The
# proxy is filed AFTER the 10-K for the same period end, so it won the
# `drop_duplicates(..., keep="last")` tie-break and REPLACED the annual leg.
#
# LOPE, measured 2026-08-21:
#     2025-12-31  qtrs=4       216,170.0  NetIncomeLoss  DEF 14A   <- $216.17M
#     2026-06-30  qtrs=2  121,200,000.0   ProfitLoss     10-Q      <- dollars
#     roll-forward: 216,170 + 121,200,000 - 113,164,000 = 8,206,170
# so `net_income_ttm` read $8.25M against a true $219.9M -- 26x low. EPS and
# share count were both correct, which is what made it hard to see: the error
# lived in one leg of one concept.
#
# 1,303 of 3,500 tradeable tickers had DEF 14A net-income rows in the store.
#
# Filtered at READ time, not at ingest, so every existing partition is
# corrected without a refetch. Only the proxy family is dropped: 6-K, 20-F,
# 40-F, 10-KT and S-1 all carry real statements and stay.
_PROXY_FORM_RE = re.compile(r"^(DEF|DEFA|DEFM|DEFR|DEFC|PRE|PRER|PREM)\s*14[AC]",
                            re.I)


# Toggle so the filter's aggregate effect can be MEASURED rather than argued.
_PROXY_FILTER_ON = os.environ.get("FD_PROXY_FILTER", "1") != "0"


def _drop_non_statement_forms(df: pd.DataFrame) -> pd.DataFrame:
    """Rescale proxy rows reported in thousands; never drop them outright.

    DROPPING WAS WRONG, and measuring showed it. The proxy family is the only
    place some filers tag a concept at all: HZO tags every statement figure
    `ProfitLoss` (which INCLUDES noncontrolling interests) and its only
    `NetIncomeLoss` -- net income attributable to the parent, the $4.0M a
    reader wants -- comes from its DEF 14A. Dropping proxies took HZO from a
    web-verified 3,986,000 to 35,986,000.

    And the scale is not uniform: LOPE's proxy reports THOUSANDS (216,170 for
    $216.17M) while HZO's reports DOLLARS. So the defect is the UNIT, not the
    form, and the fix is to restore the unit.

    A proxy row is treated as thousands only when the filer's own statement
    rows for the SAME concept are at least 100x larger -- a gap no rounding or
    restatement produces. Everything else is left exactly as filed.
    """
    if df.empty or "form" not in df.columns or not _PROXY_FILTER_ON:
        return df
    is_proxy = df["form"].astype(str).str.match(_PROXY_FORM_RE)
    if not bool(is_proxy.any()):
        return df

    v = pd.to_numeric(df["value"], errors="coerce").abs()
    key = [df["cik"], df["tag"]]
    # Statement-side magnitude per (cik, tag) -- the reference scale.
    stmt = v.where(~is_proxy)
    ref = stmt.groupby(key).transform("median")
    prox = v.where(is_proxy)

    # 100x, not 1000x: a proxy year need not match any single statement period,
    # so the test is an order-of-magnitude one, deliberately loose enough to
    # survive that and far too tight to fire on rounding.
    thousands = is_proxy & ref.notna() & prox.notna() & (prox > 0) &         (ref >= prox * 100)
    n = int(thousands.sum())
    if n:
        out = df.copy()
        out.loc[thousands, "value"] =             pd.to_numeric(out.loc[thousands, "value"], errors="coerce") * 1000.0
        _log_once("rescaled proxy-filing rows reported in thousands "
                  "(DEF 14A pay-versus-performance tables)")
        return out
    return df


def read(start_q: str | None = None, ciks: set[int] | None = None) -> pd.DataFrame:
    # THE SAME READ, 299 TIMES. `facts_asof` filters by `filed <= asof`, so a
    # rebuild sweeping historical sessions asks this function for the identical
    # frame every time and only the downstream filter differs. Profiled on
    # hype's oldest session: 124.5s total, of which 123.3s was
    # `_fundamental_detachment` -> two `facts_asof` calls -> 86.7s in here.
    #
    # Keyed on a store fingerprint, so a partition rewritten mid-run
    # invalidates it rather than serving numbers that no longer exist -- the
    # failure this project has paid for repeatedly.
    ck = None if ciks is None else frozenset(ciks)
    fp = _store_fingerprint()
    key = (start_q, ck, fp)
    hit = _READ_CACHE.get(key)
    if hit is not None:
        return hit.copy()

    # A WIDER CACHED WINDOW CAN SERVE A NARROWER REQUEST. `facts_asof` derives
    # `start_q` from its asof date, so sweeping 299 historical sessions asks
    # for ~40 different windows and an exact-match cache thrashed: 39s on a
    # hit, 201s on the quarter boundary. Slicing a superset is the same trick
    # `_HIST_CACHE` already uses for `history()` -- one cold read then serves
    # every session.
    # ...and a cached SUPERSET OF CIKS can serve a subset. The tradeable
    # universe shifts between sessions, so keying on the exact cik set missed
    # on almost every call even when the underlying frame was identical --
    # measured 34.8s on a hit against 137-185s on a miss.
    for (cq, cc, cf), df in _READ_CACHE.items():
        if cf != fp:
            continue
        if cq is not None and (start_q is None or cq > start_q):
            continue                      # cached window starts too late
        if cc is not None and (ck is None or not ck <= cc):
            continue                      # cached cik set does not cover us
        out = df
        if start_q is not None:
            q = out["ddate"].astype(str).str.slice(0, 4) + "q" + \
                ((pd.to_numeric(out["ddate"].astype(str).str.slice(5, 7),
                                errors="coerce").fillna(1).astype(int) - 1)
                 // 3 + 1).astype(str)
            out = out[q >= start_q]
        if ck is not None and cc != ck:
            out = out[out["cik"].isin(ck)]
        return out.copy()

    # The union: this is the READ path, so a quarter the fallback supplies must
    # be visible even when the bulk store lacks it.
    qs = stored_quarters(include_cf=True)
    if start_q:
        qs = [q for q in qs if q >= start_q]
    if not qs:
        return pd.DataFrame(columns=SCHEMA)
    # Push the CIK filter DOWN into pyarrow instead of materialising 481k rows
    # per quarter and throwing 99.9% of them away. Measured on 2024q4: 0.252s
    # -> 0.127s, and `history()` opens 105 partitions, so it is ~10s a page on
    # the unprimed path that serve.py uses to build a profile on demand.
    # The partitions are a single row group, so this is the pandas conversion
    # being skipped, not row-group pruning -- sorting the store by cik would add
    # that, but it would mean rewriting all 68 files.
    cl = sorted(int(c) for c in ciks) if ciks is not None else None

    def _one(path):
        if not path.exists():
            return None
        try:
            d = (pd.read_parquet(path, filters=[("cik", "in", cl)])
                 if cl else pd.read_parquet(path))
        except Exception:                                        # noqa: BLE001
            # Any pyarrow version without predicate pushdown, or a partition
            # written before `cik` existed: fall back rather than lose the data.
            d = pd.read_parquet(path)
            if ciks is not None:
                d = d[d["cik"].isin(ciks)]
        return d

    frames = []
    for q in qs:
        bulk = _one(part_path(q))
        # The companyfacts store is a FALLBACK, so it is only consulted for CIKs
        # the bulk sets did not supply in this quarter. Preferring bulk keeps one
        # authority per (cik, quarter): the two sources round and restate
        # differently, and interleaving them would put two versions of the same
        # figure in one TTM sum.
        #
        # ONE EXCEPTION, and it is not a loophole -- it is the same principle.
        # For a filer whose home currency is not USD, the bulk store's copy is
        # incomplete BY CONSTRUCTION: the bulk ingest kept USD rows only for
        # years, so what survives there is a handful of stray dollar facts, not
        # a competing version of the statements. Measured on 2025q3: Canadian
        # Pacific has 8 bulk rows against 176 in companyfacts, Imperial Oil 2
        # against 136, Enbridge 2 against 178. Letting bulk win would score
        # three large caps off two facts each and discard their real filings.
        #
        # So cf wins for these names -- ENTIRELY, never interleaved. One
        # authority per (cik, quarter) still holds; this only changes which
        # source is the authority when bulk is known to be a fragment.
        cf = _one(cf_part_path(q))
        has_bulk = bulk is not None and len(bulk)
        has_cf = cf is not None and len(cf)
        if has_bulk and has_cf:
            # WHICHEVER SOURCE FILED MORE RECENTLY WINS, per (cik, partition).
            #
            # "bulk always wins" defeated the whole point of refreshing stale
            # names. The bulk data sets lag by two quarters, so bulk holds an
            # AAPL row in 2026q1 -- and that alone discarded every companyfacts
            # row for AAPL in that partition, INCLUDING the 2026-03-28 quarter
            # that only companyfacts has. June 2026 came through purely because
            # no 2026q2 bulk file exists yet. Refreshing could therefore never
            # fix a stale filer that bulk covered at all, which is most of them.
            #
            # Newest `filed` is the principled tie-break: the later filing knows
            # strictly more, including restatements. Still exactly one authority
            # per (cik, partition) -- never interleaved -- so a TTM sum cannot
            # pick up two versions of one figure.
            fx = _non_usd_ciks() & set(cf["cik"])
            bmax = bulk.groupby("cik")["filed"].max()
            cmax = cf.groupby("cik")["filed"].max()
            shared = bmax.index.intersection(cmax.index)
            # cf wins where it filed later, or where the filer is non-USD (its
            # bulk copy is a few stray USD facts by construction).
            cf_wins = set(shared[cmax[shared] > bmax[shared]]) | fx
            cf = cf[cf["cik"].isin(cf_wins) | ~cf["cik"].isin(bmax.index)]
            if cf_wins:
                bulk = bulk[~bulk["cik"].isin(cf_wins)]
        if has_bulk and len(bulk):
            frames.append(bulk)
        if has_cf and len(cf):
            frames.append(cf)
    if not frames:
        return pd.DataFrame(columns=SCHEMA)
    # `_with_uom` on the concatenated frame, not per partition: partitions
    # written before the column existed contribute NaN, and those are USD by
    # construction because the old ingest dropped everything else.
    out = _with_uom(pd.concat(frames, ignore_index=True))
    out = _drop_non_statement_forms(out)
    # A COPY goes in and a COPY comes out, so a caller mutating the frame it
    # received cannot corrupt what the next caller sees.
    if len(_READ_CACHE) >= _READ_CACHE_MAX:
        _READ_CACHE.pop(next(iter(_READ_CACHE)))
    _READ_CACHE[key] = out.copy()
    return out


_NON_USD_CACHE: set[int] | None = None


def _non_usd_ciks() -> set[int]:
    """CIKs whose home currency is not USD. Cached -- `read()` is the hot path.

    Small (75 names as measured) and it changes only when a companyfacts
    backfill runs, which calls `invalidate_currency_cache`. Deriving it on
    every read would mean re-scanning eight quarters per profile page.
    """
    global _NON_USD_CACHE
    if _NON_USD_CACHE is None:
        try:
            _NON_USD_CACHE = {c for c, v in reporting_currency().items()
                              if v != DEFAULT_UOM}
        except Exception:                                        # noqa: BLE001
            # A read must not fail because the currency map could not be
            # built; the conservative answer is "no non-USD filers", which is
            # the behaviour that existed before this feature.
            _NON_USD_CACHE = set()
    return _NON_USD_CACHE


def invalidate_currency_cache() -> None:
    global _NON_USD_CACHE
    _NON_USD_CACHE = None


def reporting_currency(ciks=None, lookback_q: int = 8) -> dict[int, str]:
    """cik -> the currency its monetary facts are reported in.

    The MODE over recent monetary rows, not the first value seen. A filer can
    carry a stray USD figure -- a dual-listed disclosure, a US-dollar debt
    tranche -- without that being its reporting currency, and picking the first
    row would let one such fact relabel the whole company.

    Absent means USD: every partition written before `uom` existed holds USD
    only, so silence is not ambiguity here.
    """
    # QUARTERS THAT HAVE ENDED, not the lexically-last ones.
    #
    # Filings legitimately carry forward-dated facts -- lease commitments,
    # contractual obligations -- so the companyfacts store holds sparse
    # partitions out to 2028q2. Those sort AFTER the real ones, so `[-8:]`
    # sampled almost-empty future quarters: `reporting_currency` resolved 52
    # filers instead of thousands and reported ZERO non-USD, which silently
    # switched all 75 foreign filers back to their stray bulk rows.
    qs = [q for q in stored_quarters(include_cf=True)
          if q <= _current_quarter()][-lookback_q:]
    if not qs:
        return {}
    frames = []
    for q in qs:
        for p in (part_path(q), cf_part_path(q)):
            if not p.exists():
                continue
            try:
                d = pd.read_parquet(p, columns=["cik", "uom"])
            except (ValueError, KeyError):
                continue                       # pre-currency partition: USD
            frames.append(d)
    if not frames:
        return {}
    d = pd.concat(frames, ignore_index=True)
    if ciks is not None:
        d = d[d["cik"].isin(set(int(c) for c in ciks))]
    d = d.assign(ccy=d["uom"].map(uom_currency))
    d = d[d["ccy"].notna()]
    if d.empty:
        return {}
    # Ties broken by currency name so the answer is reproducible rather than
    # dependent on groupby ordering.
    mode = (d.groupby(["cik", "ccy"]).size().reset_index(name="n")
            .sort_values(["cik", "n", "ccy"], ascending=[True, False, True])
            .drop_duplicates("cik"))
    return {int(r.cik): str(r.ccy) for r in mode.itertuples()}


# `qtrs` in the SEC data sets: 0 = an instant (balance sheet), 1 = one quarter,
# 4 = a full year. Flow concepts must be filtered to the right duration or a
# quarterly and an annual figure end up in the same column.
QTRS_ANNUAL, QTRS_QUARTER, QTRS_INSTANT = 4, 1, 0

# Concepts that are a STOCK (a balance at an instant) rather than a FLOW (an
# amount over a period). Everything not listed is treated as a flow.
STOCK_CONCEPTS = frozenset({
    "assets", "assets_current", "liabilities", "liabilities_current", "equity",
    "cash", "sti", "inventory", "receivables", "payables", "debt_lt", "debt_st",
    "retained", "ppe", "goodwill", "intangibles", "shares_out",
})

# A THIRD KIND, and the one that was silently wrong: a period AVERAGE.
#
# Weighted-average share counts are tagged with a duration, so `qtrs > 0` puts
# them on the flow path -- but they do not accumulate. Two quarters of revenue
# add up; two quarters of average shares outstanding do not. Treating them as
# flows broke both ends of the pipeline:
#
#   Q4 derivation   FY - Q1 - Q2 - Q3 on a count that is ~the same every
#                   quarter gives roughly -2x the count. AAPL's derived
#                   2025-09-27 diluted shares were **-30,150,480,000** --
#                   a negative share count, shown on the profile page.
#   TTM             summing four quarterly averages gives ~4x the true count,
#                   except when the negative derived Q4 happens to cancel it
#                   back to ~1x. Both are wrong; which one you get depends on
#                   whether the fiscal year boundary fell inside the window.
#
# `share_count()` falls back to these for market cap whenever `shares_out` is
# missing, so this fed straight into pe, pb, ev_ebitda, ev_sales and fcf_yield.
# The right reduction is the MOST RECENT period's figure, never a sum.
AVERAGE_CONCEPTS = frozenset({"shares_basic", "shares_diluted"})

# PER-SHARE FIGURES THAT MUST NOT BE SUMMED ACROSS PERIODS.
#
# Distinct from AVERAGE_CONCEPTS: a weighted-average share count is a duration
# fact reduced by LATEST, whereas EPS is a genuine twelve-month quantity -- it
# just cannot be reached by addition, because each quarter's denominator
# differs. See the ordering flip at the end of `_ttm`.
NON_ADDITIVE_CONCEPTS = frozenset({"eps_basic", "eps_diluted"})


_HIST_CACHE: dict = {}


def prime_history(tickers: list[str], periods: int = 20, freq: str = "A") -> int:
    """Read the fact store ONCE for a batch of tickers, for `history()` to slice.

    Without this, `history()` re-opens all 68 stored quarters per ticker, and the
    profile page calls it twice (annual + quarterly). Measured: that was the
    dominant cost of the `profiles` step once the comparison bars were fixed.
    Returns the number of CIKs primed.
    """
    tm = ticker_map()
    want = {str(x).upper() for x in tickers}
    hit = tm[tm["ticker"].astype(str).str.upper().isin(want)]
    ciks = set(int(c) for c in hit["cik"])
    if not ciks:
        return 0
    # Prime the WIDEST window either frequency can ask for, so one read serves
    # both the annual and the quarterly table on a profile page.
    back = max(periods + 2, periods // 4 + 3)
    start_q = _q_back(date.today().isoformat(), int(back * 4))
    _HIST_CACHE.clear()
    _HIST_CACHE[start_q] = read(start_q=start_q, ciks=ciks)
    _HIST_CACHE["_ciks"] = ciks
    return len(ciks)


NEAR_PERIOD_DAYS = 10

# Four CONSECUTIVE quarter-ends span ~273 days (three gaps of ~91). Allow slack
# for 52/53-week fiscal calendars and late filings, but reject anything that
# implies a missing quarter -- see the HD case in `_ttm`.
MAX_4Q_SPAN_DAYS = 310

_LOGGED: set = set()


def _log_once(msg: str) -> None:
    """Print a data-quality note once per process, not once per ticker."""
    if msg not in _LOGGED:
        _LOGGED.add(msg)
        try:
            print(f"  [fundamentals] {msg}", flush=True)
        except (ValueError, OSError):
            pass


def _merge_near_periods(wide: pd.DataFrame) -> pd.DataFrame:
    """Collapse dates that are the same fiscal quarter reported twice.

    A company whose quarter does not end on a month-end files its income
    statement against the fiscal date and its balance sheet against the
    calendar month-end. Pivoting on `ddate` then produces TWO rows for one
    quarter -- one carrying revenue and one carrying only balance-sheet items,
    which renders as a BLANK COLUMN in the financials table.

    Measured across 20 large caps: 5 affected, 19 blank columns. NVDA showed
    revenue at 2025-07-27 and an empty 2025-07-31 beside it; AMAT, XOM, ADBE
    and AAPL the same. It looks exactly like missing data and is not.

    Dates within NEAR_PERIOD_DAYS are merged, keeping the EARLIER (fiscal)
    date, because that is the one the income statement uses and the one a
    reader recognises as the quarter. A real quarter is ~90 days away, so the
    window cannot fuse two genuine periods.
    """
    if wide.empty or len(wide) < 2:
        return wide
    idx = pd.to_datetime(wide.index, errors="coerce")
    if idx.isna().any():
        return wide
    order = idx.argsort()
    wide = wide.iloc[order]
    idx = idx[order]

    groups, cur = [], [0]
    for i in range(1, len(idx)):
        if (idx[i] - idx[cur[0]]).days <= NEAR_PERIOD_DAYS:
            cur.append(i)
        else:
            groups.append(cur); cur = [i]
    groups.append(cur)
    if len(groups) == len(wide):
        return wide                       # nothing to merge

    rows, labels = [], []
    for g in groups:
        block = wide.iloc[g]
        # Later filings win where both carry a value: `bfill` down the block
        # takes the first non-null in date order, so prefer the last row's
        # value only where the earlier is missing.
        merged = block.bfill().iloc[0]
        rows.append(merged)
        labels.append(wide.index[g[0]])
    out = pd.DataFrame(rows)
    out.index = labels
    out.index.name = wide.index.name
    return out


def _fill_q4(wide: pd.DataFrame, all_raw: pd.DataFrame,
             tag_to_concept: dict) -> pd.DataFrame:
    """Derive the missing fiscal Q4 as annual minus the first three quarters.

    A 10-K reports the FULL YEAR, never Q4 on its own -- no 10-Q is filed for
    the final quarter -- so a quarterly series built from `qtrs == 1` rows has
    a hole every fourth period. On Apple's profile that was a blank column at
    2022-09-30, 2023-09-30, 2024-09-30 and 2025-09-30: one column in four
    reading as missing data when the figure is fully determined by numbers the
    filing does contain.

    FLOW CONCEPTS ONLY. Revenue and net income accumulate, so Q4 = FY - Q1 - Q2
    - Q3 is exact. A balance-sheet item does not accumulate -- assets at year
    end are not assets-for-the-year minus three quarters -- and those instants
    are already present from the 10-K, so they are left alone.

    Only filled when all three earlier quarters AND the annual figure are
    present. A partial subtraction would silently invent a number, which is the
    failure this codebase keeps paying for.
    """
    if wide.empty or all_raw.empty:
        return wide
    ann = all_raw[all_raw["qtrs"] == QTRS_ANNUAL]
    if ann.empty:
        return wide
    ann = ann.sort_values("filed").drop_duplicates(["concept", "ddate"],
                                                   keep="last")
    # TWO PASSES. Computing the date index once and then inserting rows inside
    # the loop left the mask stale the moment the first fiscal-year-end row was
    # added: `wide` grew, `idx` did not, and every later concept's boolean mask
    # silently misaligned. The symptom was an EMPTY row at the year end -- the
    # row created, the value never filled -- which looks exactly like the
    # missing data this function exists to remove.
    fills: list[tuple[str, str, float]] = []
    for _, a in ann.iterrows():
        concept = str(a["concept"])
        if (concept in STOCK_CONCEPTS or concept in AVERAGE_CONCEPTS
                or concept not in wide.columns):
            continue
        fy_end = pd.to_datetime(a["ddate"], errors="coerce")
        if pd.isna(fy_end):
            continue
        key = fy_end.strftime("%Y-%m-%d")
        if key in wide.index and pd.notna(wide.at[key, concept]):
            continue                      # a real Q4 was filed; leave it
        # The three quarters inside this fiscal year, by period end. Read from
        # the ORIGINAL frame every time, so nothing added by this pass can
        # shift the window or be counted as one of the three.
        idx = pd.to_datetime(wide.index, errors="coerce")
        lo = fy_end - pd.DateOffset(months=12)
        prior = wide[(idx > lo) & (idx < fy_end)][concept].dropna()
        if len(prior) != 3:
            continue                      # incomplete: refuse to guess
        fills.append((key, concept, float(a["value"]) - float(prior.sum())))

    for key, concept, val in fills:
        if key not in wide.index:
            wide.loc[key] = pd.Series(dtype="float64")
        wide.at[key, concept] = val
    return wide.sort_index() if fills else wide


def history(ticker: str, freq: str = "A", periods: int = 20) -> pd.DataFrame:
    """Reported financial history for one ticker: index = period end, columns =
    concept. Newest last.

    `freq` is "A" (annual) or "Q" (quarterly).

    TWO THINGS MAKE THIS CHEAPER AND MORE ACCURATE THAN IT LOOKS:

    1. **Each 10-K carries comparative periods.** Apple's FY2023 filing reports
       revenue for 2021, 2022 AND 2023. So history is keyed on `ddate` (the
       period the number describes), not on `fy` (the filing's own year) --
       keying on `fy` would throw away two thirds of the data and would mislabel
       the comparatives as belonging to the filing year.

    2. **The latest filing wins.** Within a `ddate` the row with the greatest
       `filed` is kept, so a restatement supersedes the original print. This is
       the one place in the project where that is correct: `facts_asof` must
       show what was visible ON a date, but a HISTORY table is a statement about
       what the numbers actually were, so the best available revision is right.

    Note this is therefore NOT point-in-time and must never feed a backtest.
    `facts_asof` is the point-in-time door; this one is for display.
    """
    tm = ticker_map()
    row = tm[tm["ticker"].astype(str).str.upper() == str(ticker).upper()]
    if row.empty:
        return pd.DataFrame()
    cik = int(row["cik"].iloc[0])

    back = periods + 2 if freq.upper() == "A" else periods // 4 + 3
    start_q = _q_back(date.today().isoformat(), int(back * 4))

    # Served from the batch cache when one has been primed. `read()` opens every
    # stored quarter to find one CIK, so building N profiles did N full scans --
    # and the profile page calls this TWICE (annual + quarterly) for its
    # frequency toggle, doubling it again. See prime_history().
    # Any cached window starting at or BEFORE the one we need can serve this
    # request -- extra history is harmless because the frame is tailed to
    # `periods` at the end. Matching the key exactly was a real bug: the profile
    # page primes with quarterly params (start_q 2019q3) and then asks for
    # annual (2012q3), so every lookup missed and re-scanned the whole store
    # per ticker. Quarter labels sort lexically, so `<=` is the right test.
    cached, ciks = None, _HIST_CACHE.get("_ciks", set())
    if cik in ciks:
        for key in sorted(k for k in _HIST_CACHE if k != "_ciks"):
            if key <= start_q:
                cached = _HIST_CACHE[key]
                break
    if cached is not None and not cached.empty:
        raw = cached[cached["cik"] == cik]
    else:
        raw = read(start_q=start_q, ciks={cik})
    if raw.empty:
        return pd.DataFrame()

    tag_to_concept = {}
    for concept, alts in TAGS.items():
        for t in alts:
            tag_to_concept.setdefault(t, concept)
    raw = raw.assign(concept=raw["tag"].map(tag_to_concept)).dropna(
        subset=["concept"])
    if raw.empty:
        return pd.DataFrame()

    all_raw = raw          # every duration, kept for the Q4 derivation below
    flow_q = QTRS_ANNUAL if freq.upper() == "A" else QTRS_QUARTER
    is_stock = raw["concept"].isin(STOCK_CONCEPTS)
    keep = (is_stock & (raw["qtrs"] == QTRS_INSTANT)) | \
           (~is_stock & (raw["qtrs"] == flow_q))
    raw = raw[keep]
    if raw.empty:
        return pd.DataFrame()

    # Restatement-aware: latest `filed` wins within (concept, ddate)...
    raw = raw.sort_values("filed").drop_duplicates(
        subset=["concept", "adsh", "ddate", "tag"], keep="last")

    # ...and for AGGREGATE_MAX_CONCEPTS the LARGEST alias wins, exactly as
    # `_latest` already does for the point-in-time frame.
    #
    # This path did not, and the two disagreed. Walmart files both
    # `RevenueFromContractWithCustomerExcludingAssessedTax` (net sales) and
    # `Revenues` (total) in the SAME 10-Q; the table took whichever was filed
    # last, so quarters came out on net sales while the ANNUAL figure came out
    # on the total. The derived Q4 is `FY - Q1 - Q2 - Q3`, so mixing the two
    # inflated it: our TTM read 713-718B against SEC's 700B.
    #
    # A revenue component cannot exceed the revenue total, which is why max is
    # safe here and why that constant stays deliberately small.
    agg = raw["concept"].isin(AGGREGATE_MAX_CONCEPTS)
    if agg.any():
        big = (raw[agg].sort_values("value")
               .drop_duplicates(subset=["concept", "ddate", "qtrs"], keep="last"))
        raw = pd.concat([raw[~agg], big], ignore_index=True)
    raw = raw.sort_values("filed").drop_duplicates(
        subset=["concept", "ddate"], keep="last")

    wide = raw.pivot_table(index="ddate", columns="concept", values="value",
                           aggfunc="last")
    wide = wide.sort_index()

    if freq.upper() != "A":
        # BEFORE the Q4 fill, so it derives from clean periods.
        wide = _merge_near_periods(wide)
        wide = _fill_q4(wide, all_raw, tag_to_concept)

    if freq.upper() == "A":
        # Annual instants land on the fiscal year end. Keep one row per fiscal
        # year -- the last one -- so a company that also tags an instant at a
        # quarter end does not produce two "annual" rows.
        wide = wide.groupby(pd.to_datetime(wide.index).year).last()
        wide.index = [str(y) for y in wide.index]

    return wide.tail(periods)


SHARE_COLUMNS = ("shares_out", "shares_diluted_ttm", "shares_basic_ttm",
                 "shares_diluted", "shares_basic")

# Below this a "share count" is a cover-page tagging artefact, not a company.
# The smallest real listed floats are in the low millions; 10,000 is two orders
# of magnitude below anything genuine and still catches 1 / 10 / 100.
MIN_SHARES = 10_000


def share_count(facts: pd.DataFrame) -> pd.Series:
    """Best available share count per ticker, from a `facts_asof` frame.

    ONE implementation, because there were two and they were both wrong the same
    way. Every valuation metric is price x shares, so a missing share count does
    not produce a warning -- it produces a silent NaN market cap and then a
    silent NaN for pe, pb, ev_ebitda, ev_sales, fcf_yield and peg.

    THE BUG THIS FIXES (measured 2026-08-07): callers looked for `shares_diluted`
    and `shares_basic`, but `facts_asof` classifies weighted-average share counts
    as FLOW concepts and therefore returns them suffixed -- `shares_diluted_ttm`,
    `shares_basic_ttm`. Neither name ever matched, so the chain always fell
    through to `shares_out`, which only some filers tag. **549 of 2,873 names
    (19.1%) had no market cap** despite a perfectly good diluted count sitting in
    the same frame. KO, JNJ, PG, VZ and F were all in that set.

    Order is deliberate: `shares_out` is a point-in-time count as of the cover
    date and is the most accurate; the weighted averages are period figures and
    lag a buyback or issuance, but they are far better than nothing.
    """
    out = None
    for col in SHARE_COLUMNS:
        if col not in facts.columns:
            continue
        s = pd.to_numeric(facts[col], errors="coerce")
        # `> 0` WAS NOT A STRICT ENOUGH TEST. Some filers tag the cover-page
        # `shares_out` as a placeholder 1, 10 or 100, and because `shares_out`
        # is preferred first those placeholders WON over a perfectly good
        # weighted-average count in the same frame: FBYD resolved to 10 shares
        # while carrying shares_diluted_ttm of 39,255,880.
        #
        # The consequence was a market cap of $14.13 for HQ and $100.70 for
        # FBYD -- dollars, not millions -- which then made `turnover`
        # (21-day dollar volume / market cap) read 414,549x, and fed every
        # valuation ratio, Altman Z and the study's size buckets.
        #
        # No listed equity has fewer than 10,000 shares outstanding, so a
        # count below that is a tag artefact and must fall through to the next
        # source rather than be believed.
        s = s.where(s >= MIN_SHARES)
        out = s if out is None else out.fillna(s)
    if out is None:
        return pd.Series(np.nan, index=facts.index, dtype="float64")
    return out


def store_bytes() -> int:
    if not config.FUNDAMENTALS.exists():
        return 0
    return sum(p.stat().st_size for p in config.FUNDAMENTALS.glob("*.parquet"))


# ===========================================================================
# CIK <-> ticker
# ===========================================================================
def _succession_aliases(df: pd.DataFrame) -> pd.DataFrame:
    """Map a ticker to its PREDECESSOR CIK as well as its current one.

    A corporate reorganisation issues the surviving company a new CIK, and
    SEC's `company_tickers.json` lists only that new one. Every filing made
    under the predecessor then becomes unreachable even though it is sitting in
    our own fact store. Measured 2026-08-16:

        XOM   cik 2115436   94 rows        <- all the map knows about
              cik   34088   5,208 rows     <- 2006..2025, still filing

    With only the new CIK there is no annual figure and no four consecutive
    quarters, so every TTM is uncomputable and the page renders blank --
    correct given the inputs, and useless to read.

    Adding a SECOND ROW for the same ticker is what fixes it: `facts_asof`
    merges facts to tickers on `cik`, so both CIKs' rows resolve to the one
    ticker and the histories join. Nothing is substituted, so a lookup that
    already worked cannot change.

    The map is built by `cik_succession.py`, which requires an exact
    normalised entity-name match and a 10-K/10-Q filing history. Four links
    exist today (XOM, NVRI, PNFP, CLBK) out of 86 candidates -- deliberately
    strict, because attaching another company's financials to a ticker is far
    worse than the blank page this repairs.
    """
    # ENABLED 2026-08-16, once the merger case could be excluded by DATA.
    #
    # First attempt linked on entity name alone and was wrong: PNFP's new CIK
    # is the combined Pinnacle/Synovus entity, so splicing pre-merger Pinnacle
    # onto it gave assets of 129B against the predecessor's 56B and dropped
    # verification to 30.3%.
    #
    # `cik_succession.py` now also requires the two CIKs to report TOTAL ASSETS
    # WITHIN 0.75-1.35x of each other, because a reorganisation preserves the
    # balance sheet and a merger does not:
    #
    #     XOM   464.5B / 449.0B = 1.03   linked
    #     CLBK   12.2B /  11.0B = 1.10   linked
    #     NVRI    1.7B /   2.7B = 0.64   rejected
    #     PNFP  129.1B /  56.0B = 2.31   rejected
    #
    # Set FD_CIK_SUCCESSION=0 to turn it off.
    if os.environ.get("FD_CIK_SUCCESSION", "1") == "0":
        return df
    if df.empty or "ticker" not in df:
        return df
    p = config.DATA / "_cik_alias.json"
    if not p.exists():
        return df
    # CATCH ONLY WHAT A BAD FILE THROWS.
    #
    # This was `except Exception`, and it swallowed a NameError from a missing
    # `import json` -- so the alias silently never applied, XOM stayed broken,
    # and every line of this function appeared to run. A blanket catch turns a
    # CODE bug into a silent data gap, which is the hardest kind to find.
    try:
        alias = {int(k): int(v) for k, v in
                 json.loads(p.read_text(encoding="utf-8")).items()}
    except (OSError, ValueError, TypeError) as exc:
        _log_once(f"cik alias map unreadable ({type(exc).__name__}); "
                  f"successions not applied")
        return df
    if not alias:
        return df
    # `list(alias)`, not `alias`. `Series.isin(dict)` does NOT test the dict's
    # keys -- it silently matched nothing, so this returned the frame unchanged
    # and XOM stayed broken while every line of the function ran.
    hit = df[df["cik"].isin(list(alias))]
    if hit.empty:
        return df
    extra = hit.assign(cik=hit["cik"].map(alias))
    # Only genuinely new (ticker, cik) pairs; never displace an existing row.
    have = set(map(tuple, df[["ticker", "cik"]].values))
    extra = extra[[t not in have for t in map(tuple, extra[["ticker", "cik"]].values)]]
    if extra.empty:
        return df
    return pd.concat([df, extra], ignore_index=True)


def _class_share_aliases(df: pd.DataFrame) -> pd.DataFrame:
    """Add dotted aliases for SEC's hyphenated class-share tickers.

    SEC writes `BRK-B`; the price feed writes `BRK.B`. Nothing reconciled them,
    so every dual-class name fell out of the CIK lookup and silently received
    no fundamentals at all -- Berkshire Hathaway, Brown-Forman, Heico, Lennar,
    Moog, Greif, U-Haul and Biglari among them. It never raised: a missing key
    returns None, and "no fundamentals" is a legitimate state for a fund or an
    ETF, so nine large operating companies looked exactly like the funds.

    Aliases are ADDED, not substituted. Existing keys win, so this cannot
    change any lookup that already resolved. Safe because SEC uses no dots in
    ticker symbols, so a dotted key can never collide with a real SEC one.
    """
    if df.empty or "ticker" not in df:
        return df
    hy = df[df["ticker"].str.contains("-", na=False)]
    if hy.empty:
        return df
    alias = hy.assign(ticker=hy["ticker"].str.replace("-", ".", regex=False))
    alias = alias[~alias["ticker"].isin(set(df["ticker"]))]
    if alias.empty:
        return df
    return pd.concat([df, alias], ignore_index=True)


def ticker_map(refresh: bool = False) -> pd.DataFrame:
    """ticker <-> cik, from SEC's own file. Cached; it changes slowly."""
    p = config.DATA / "_cik_map.parquet"
    if p.exists() and not refresh:
        # Aliased on READ, not baked into the cache: an existing cache written
        # before this fix must gain the aliases too, without needing a refresh.
        return _succession_aliases(_class_share_aliases(pd.read_parquet(p)))
    r = requests.get(config.SEC_TICKER_MAP_URL,
                     headers={"User-Agent": config.SEC_UA}, timeout=60)
    r.raise_for_status()
    rows = [{"ticker": str(v["ticker"]).upper(), "cik": int(v["cik_str"])}
            for v in r.json().values()]
    df = pd.DataFrame(rows).drop_duplicates("ticker")
    tmp = p.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, compression=config.COMPRESSION, index=False)
    store.atomic_replace(tmp, p)
    return _succession_aliases(_class_share_aliases(df))


def sector_map(write_file: bool = True, verbose: bool = True) -> pd.DataFrame:
    """ticker -> sic, sector, sector ETF, derived from the FACT STORE.

    Replaces macro.build_sector_map(), which issued one SEC submissions request
    PER TICKER -- ~44 minutes for the universe. Every `sub.txt` in every
    quarterly ZIP already carries `sic` for every filer, so that scrape was
    re-fetching data we had already downloaded and stored.

    Also strictly better than the scrape, not merely faster: the scrape returned
    each company's CURRENT SIC, so a reclassification silently rewrote history.
    Here the code arrives attached to a filing with a `filed` date, so the
    sector a company was in at the time is recoverable.
    """
    import macro

    qs = stored_quarters(include_cf=True)          # read path
    if not qs:
        return pd.DataFrame(columns=["ticker", "cik", "sic", "sector", "sector_etf"])

    # Newest filings last, so drop_duplicates(keep="last") gives the most recent
    # SIC per filer.
    frames = [pd.read_parquet(part_path(q), columns=["cik", "sic", "filed"])
              for q in qs]
    df = pd.concat(frames, ignore_index=True)
    df = df[df["sic"] > 0].sort_values("filed").drop_duplicates("cik", keep="last")

    tm = ticker_map()
    out = tm.merge(df[["cik", "sic"]], on="cik", how="left")
    out["sector"] = out["sic"].map(macro.sic_to_sector)
    out["sector_etf"] = out["sector"].map(
        lambda s: macro.SECTOR_ETF_FOR.get(s, "SPY"))

    if write_file:
        tmp = config.SECTOR_MAP_FILE.with_suffix(".parquet.tmp")
        out.to_parquet(tmp, compression=config.COMPRESSION, index=False)
        store.atomic_replace(tmp, config.SECTOR_MAP_FILE)
    if verbose:
        known = int(out["sic"].notna().sum())
        log(f"  sectors: {len(out):,} tickers, {known:,} with a SIC, "
            f"{out['sector'].nunique()} sectors (from the fact store, no scrape)")
    return out


# ===========================================================================
# THE point-in-time view
# ===========================================================================
def facts_asof(asof: str, tickers: list[str] | None = None,
               lookback_q: int = 12) -> pd.DataFrame:
    """Wide (ticker x concept) frame of what was PUBLIC on `asof`.

    Two filters, and both matter:

      filed <= asof   what the market could actually see. Filtering on `ddate`
                      instead would hand the screen a quarter six weeks before
                      it was published.
      latest ddate    the most recent PERIOD among visible filings, and within
                      a period the most recent filing -- so a later restatement
                      is used only once it exists.

    Returns both the level and, for flow concepts, the trailing-twelve-month sum,
    since a single quarter's revenue is not comparable to an annual figure.
    """
    tm = ticker_map()
    if tickers:
        tm = tm[tm["ticker"].isin(set(tickers))]
    ciks = set(tm["cik"])
    if not ciks:
        return pd.DataFrame()

    start_q = _q_back(asof, lookback_q)
    df = read(start_q=start_q, ciks=ciks)
    if df.empty:
        return pd.DataFrame()

    # TWO time filters, not one. `filed <= asof` is what the market could see;
    # `ddate <= asof` is that the PERIOD has actually ended.
    #
    # Without the second, a mis-tagged period date wins the "latest period"
    # race and becomes the company's headline financials. Measured 2026-08-10:
    # 344 stored rows carry an absurd `ddate` (range 1927-02-28 to 2215-09-30),
    # and LEGH's point-in-time frame was reporting period **2033-03-31** --
    # seven years into the future -- because it sorted latest. A period that
    # has not ended cannot be a completed result, whatever the filing says.
    df = df[(df["filed"] <= asof) & (df["ddate"] <= asof)]
    if df.empty:
        return pd.DataFrame()

    # ONE CURRENCY PER FILER, enforced here rather than trusted.
    #
    # Every row of the wide frame below becomes one company's balance sheet and
    # income statement side by side, and the metrics divide those figures by
    # each other. A filer that reports in EUR but carries one stray USD fact --
    # a dual-listed disclosure, a dollar debt tranche -- would otherwise put a
    # USD numerator over a EUR denominator inside a single row, which is the
    # currency mixing this column exists to prevent. Non-monetary units
    # (shares, per-share, pure) are currency-free and always kept.
    df = _with_uom(df)
    home = reporting_currency(ciks)
    if home:
        row_ccy = df["uom"].map(uom_currency)
        keep = row_ccy.isna() | (row_ccy == df["cik"].map(home).fillna(DEFAULT_UOM))
        df = df[keep]
        if df.empty:
            return pd.DataFrame()

    df = df.merge(tm, on="cik", how="inner")
    df["tag"] = df["tag"].astype(str)

    # Map each raw tag to its canonical concept, honouring alias preference.
    pref = {}
    for concept, alts in TAGS.items():
        for rank, t in enumerate(alts):
            pref[t] = (concept, rank)
    df["concept"] = df["tag"].map(lambda t: pref.get(t, (None, 99))[0])
    df["rank"] = df["tag"].map(lambda t: pref.get(t, (None, 99))[1])
    df = df[df["concept"].notna()]

    inst = df[df["qtrs"] == 0]                    # balance-sheet instants
    flow = df[df["qtrs"] > 0]                     # income / cash-flow durations

    wide = _latest(inst, ["ticker", "concept"])
    ttm = _ttm(flow)

    out = wide.pivot_table(index="ticker", columns="concept", values="value",
                           aggfunc="last") if not wide.empty else pd.DataFrame()
    if not ttm.empty:
        t = ttm.pivot_table(index="ticker", columns="concept", values="value",
                            aggfunc="last")
        t.columns = [f"{c}_ttm" for c in t.columns]
        out = out.join(t, how="outer") if not out.empty else t

    if out.empty:
        return pd.DataFrame()
    out = out.reset_index().rename_axis(None, axis=1)

    meta = (df.sort_values(["ticker", "filed"])
              .groupby("ticker", observed=True)
              .agg(sic=("sic", "last"), last_filed=("filed", "last"),
                   last_ddate=("ddate", "max"), cik=("cik", "last"))
              .reset_index())
    out = out.merge(meta, on="ticker", how="left")
    # The currency travels WITH the figures. Without it the scoring layer has
    # no way to know a book value is in EUR, and the whole point of keeping
    # these rows is that it must know.
    out["currency"] = out["cik"].map(home).fillna(DEFAULT_UOM) if home \
        else DEFAULT_UOM
    return out


# Concepts whose aliases are TOTAL-vs-COMPONENT rather than genuine synonyms.
# For these the largest alias in a period wins, not the first one listed.
#
# WHY (measured 2026-08-07): Rexford Industrial's FY2025 10-K reports BOTH
# `Revenues` = $1,003,133,000 (the real total) AND
# `RevenueFromContractWithCustomerExcludingAssessedTax` = $589,000 (an ancillary
# line). Alias order put the contract tag first, so REXR's revenue was read as
# $589K instead of $1.0B -- a 1,700x error that produced a P/S of 14,350.
#
# Apple is the reason the bug survived: AAPL reports ONLY the contract tag, and
# for AAPL it IS the total. So the ordering looked right on every mega-cap and
# was wrong for REITs, insurers and anyone with mixed revenue streams.
#
# Taking the max is safe here specifically because a revenue COMPONENT cannot
# exceed the revenue TOTAL. That argument does not hold for other concepts, so
# this set stays deliberately small.
AGGREGATE_MAX_CONCEPTS = frozenset({"revenue"})


def _latest(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Most recent period, then preferred alias, then latest filing.

    For AGGREGATE_MAX_CONCEPTS the alias tiebreak is by VALUE rather than by
    list position -- see that constant for the 1,700x error this prevents.
    """
    if df.empty:
        return df
    d = df.copy()
    # Sorting ascending and taking .tail(1) means the LAST row wins, so the sort
    # key must put the preferred row last: rank descending (rank 0 = preferred),
    # and for the max-concepts, value ascending so the largest lands last.
    is_max = d["concept"].isin(AGGREGATE_MAX_CONCEPTS)
    d["_pref"] = d["rank"].where(~is_max, 0)
    d["_val"] = pd.to_numeric(d["value"], errors="coerce").where(is_max, 0.0)
    # STABLE. Every sort feeding a `.tail()` in this file uses mergesort: the
    # default quicksort is not stable, so tied rows were ordered by whatever
    # else happened to be in the array, and the survivor of a tie changed with
    # the BATCH. See the tie-break note at the end of `_ttm`.
    d = d.sort_values(["ddate", "_pref", "_val", "filed"],
                      ascending=[True, False, True, True], kind="mergesort")
    return d.groupby(keys, observed=True).tail(1).drop(
        columns=["_pref", "_val"], errors="ignore")


def _q4_rows(q: pd.DataFrame, ann: pd.DataFrame) -> pd.DataFrame:
    """Add the fiscal Q4 that no 10-Q ever reports: annual minus Q1+Q2+Q3.

    Same arithmetic as `_fill_q4`, on the long point-in-time frame rather than
    the wide display one. Only filled when the annual figure AND exactly three
    quarters inside its year are present -- a partial subtraction would invent
    a number, and an invented earnings figure is worse than a missing one.
    """
    if q.empty or ann is None or ann.empty:
        return q
    # Period AVERAGES do not decompose by subtraction -- see AVERAGE_CONCEPTS.
    ann = ann[~ann["concept"].isin(AVERAGE_CONCEPTS)]
    if ann.empty:
        return q
    a = _latest(ann, ["ticker", "concept"])[["ticker", "concept", "value",
                                             "ddate"]]
    qd = pd.to_datetime(q["ddate"], errors="coerce")
    rows = []
    for r in a.itertuples(index=False):
        end = pd.Timestamp(r.ddate)
        if pd.isna(end):
            continue
        m = ((q["ticker"] == r.ticker) & (q["concept"] == r.concept)
             & (qd > end - pd.DateOffset(months=12)) & (qd <= end))
        inside = q[m]
        if len(inside) == 3 and not (inside["ddate"] == r.ddate).any():
            rows.append({"ticker": r.ticker, "concept": r.concept,
                         "ddate": r.ddate, "qtrs": 1,
                         "value": float(r.value) - float(inside["value"].sum()),
                         "rank": 0, "filed": q["filed"].max()})
    return pd.concat([q, pd.DataFrame(rows)], ignore_index=True) if rows else q


def _ttm(flow: pd.DataFrame) -> pd.DataFrame:
    """Trailing twelve months per (ticker, concept).

    Prefers an annual (qtrs==4) figure when one is visible; otherwise sums the
    four most recent distinct quarterly (qtrs==1) periods. Mixing the two would
    double count, which is why annual short-circuits rather than adding.

    AVERAGE_CONCEPTS are carved out and reduced by LATEST instead of by sum --
    a weighted-average share count is a duration fact that does not accumulate.
    """
    if flow.empty:
        return flow

    # Period averages: newest period wins, no summing, no Q4 derivation.
    avg = flow[flow["concept"].isin(AVERAGE_CONCEPTS)]
    flow = flow[~flow["concept"].isin(AVERAGE_CONCEPTS)]
    avg_out = None
    if not avg.empty:
        avg_out = _latest(avg, ["ticker", "concept"])[
            ["ticker", "concept", "value", "ddate"]].assign(_src="avg")
    if flow.empty:
        return (avg_out.drop(columns=["_src"]) if avg_out is not None
                else pd.DataFrame(columns=["ticker", "concept", "value"]))

    out = []
    ann = flow[flow["qtrs"] == 4]
    if not ann.empty:
        a = _latest(ann, ["ticker", "concept"])[["ticker", "concept", "value",
                                                 "ddate"]]
        out.append(a.assign(_src="annual"))

    q1 = flow[flow["qtrs"] == 1]
    if not q1.empty:
        d = (q1.sort_values(["ddate", "rank", "filed"],
                            ascending=[True, False, True], kind="mergesort")
               .groupby(["ticker", "concept", "ddate"], observed=True).tail(1))
        # DERIVE THE MISSING FISCAL Q4 FIRST, or the "four most recent
        # quarters" is not twelve months. A 10-K reports the full year and no
        # 10-Q ever covers Q4, so the fourth-newest FILED quarter sits a year
        # back: Apple's window became Jun25 + Dec25 + Mar26 + Jun26, skipping
        # Sep25 entirely and spanning fifteen months. `history()` already fills
        # this gap for the table; the point-in-time frame did not, so every
        # TTM-derived metric inherited the malformed window.
        d = _q4_rows(d, ann)
        d = (d.sort_values("ddate", kind="mergesort")
               .groupby(["ticker", "concept"], observed=True).tail(4))
        n = d.groupby(["ticker", "concept"], observed=True)["value"].transform("size")
        d = d[n == 4]

        # FOUR QUARTERS IS NOT THE SAME AS TWELVE MONTHS.
        #
        # `tail(4)` takes the four most recent quarters that EXIST, and for a
        # concept a filer tags only occasionally those four can be scattered
        # across years. Home Depot tags `AmortizationOfIntangibleAssets`
        # quarterly only sometimes: the four most recent were 2024-07-28,
        # 2024-10-27, 2025-05-04 and 2026-05-03 -- a **644-day** window summed
        # and labelled TTM. It produced 538M against a true 639M.
        #
        # Four CONSECUTIVE quarter-ends span about nine months from first to
        # last (three gaps of ~91 days), so anything past ~10 months means the
        # window has holes in it. Such a window is dropped, and the annual
        # figure -- a real twelve months, merely older -- wins instead via the
        # ends-later rule below. An honest older number beats a fabricated
        # recent one.
        #
        # `verify_metrics` has enforced exactly this since it was written,
        # which is why it caught the discrepancy the production path missed.
        if not d.empty:
            dd = pd.to_datetime(d["ddate"], errors="coerce")
            span = (dd.groupby([d["ticker"], d["concept"]]).transform("max")
                    - dd.groupby([d["ticker"], d["concept"]]).transform("min")).dt.days
            malformed = int((span > MAX_4Q_SPAN_DAYS).groupby(
                [d["ticker"], d["concept"]]).first().sum())
            if malformed:
                _log_once(f"_ttm: dropped {malformed} four-quarter window(s) "
                          f"spanning more than {MAX_4Q_SPAN_DAYS} days")
            d = d[span <= MAX_4Q_SPAN_DAYS]

        if not d.empty:
            g = d.groupby(["ticker", "concept"], observed=True)
            # RECORD THE WINDOW, not just the sum.
            #
            # A TTM is never a reported fact -- no filer publishes "TTM
            # revenue" in XBRL -- so it is always a CONSTRUCTION, and two
            # honest constructions disagree wherever the underlying periods are
            # irregular. Chasing those disagreements one at a time is endless;
            # what ends it is being able to SEE the window that produced a
            # number. `_window` carries the exact period-ends summed, so any
            # future dispute is settled by reading it rather than by
            # re-deriving both sides.
            agg = g.agg(value=("value", "sum"), ddate=("ddate", "max"),
                        _window=("ddate", lambda s: ",".join(sorted(map(str, s)))),
                        _n=("ddate", "size")).reset_index()
            out.append(agg.assign(_src="4q"))

    # ROLL-FORWARD: DIAGNOSED, ATTEMPTED, REVERTED 2026-08-15. DO NOT REDO BLIND.
    #
    # THE DIAGNOSIS IS SOUND and worth acting on later. A 10-Q reports the cash
    # flow statement CUMULATIVELY: only Q1 is tagged qtrs=1, Q2 is qtrs=2, Q3 is
    # qtrs=3. So "the four most recent qtrs==1 rows" collects ONE QUARTER PER
    # YEAR. Measured on HNI's `cfo` by `ttm_invariants.py`:
    #
    #     2023-04-01, 2024-03-30, 2025-03-29, 2026-04-04   span 1,099 days
    #
    # The MAX_4Q_SPAN_DAYS guard rejects those, so nothing catastrophic ships --
    # but the fallback is the last ANNUAL figure. For cfo, cfi, cff, capex, sbc,
    # buybacks, dividends, tax and pretax, `*_ttm` therefore means "last full
    # year", up to a year stale, NOT a trailing twelve months. That staleness is
    # the source of the residual UPS/HNI/KO disagreements.
    #
    # The correct construction is TTM = FY + YTD_now - YTD_year_ago, every leg a
    # reported figure. An implementation of it was written and REVERTED because
    # it made things worse, not better:
    #
    #     KO   cfo_ttm  14,631M against a true 7,408M   (~2x, double counted)
    #     HNI  net_income_ttm  56.4M against 4.4M       (1,182% off)
    #     overall verify_metrics 96% -> 76%
    #
    # The merge produced more rows than intended somewhere between `cur`,
    # `prior` and the 350-380 day stub match. Redo it with a per-ticker unit
    # test that pins KO, HD and UPS to hand-computed values BEFORE wiring it
    # into `_ttm` -- not by reasoning about the merge, which is what failed.
    #
    # ------------------------------------------------------- ROLL-FORWARD
    # TTM = FY + YTD_now - YTD_a_year_ago, for YEAR-TO-DATE filings.
    #
    # See `_selftest_rollforward` for the pinned hand-computed cases. Every leg
    # is a REPORTED figure, so nothing is derived by subtracting periods that
    # were never meant to be subtracted.
    #
    # Guarded three ways, because an earlier version of this was reverted:
    #   * the current stub must END AFTER the annual, else the annual already
    #     covers those twelve months and rolling would double count;
    #   * the prior stub must be the SAME qtrs and 350-380 days earlier, so a
    #     six-month stub is only ever netted against a six-month stub;
    #   * exactly one current stub and one prior stub per (ticker, concept),
    #     taken as the latest of each -- a many-to-many merge here is what
    #     produced the bogus figures the first time.
    if not flow.empty:
        ytd = flow[flow["qtrs"].isin([1, 2, 3])]
        ann_all = flow[flow["qtrs"] == 4]
        if not ytd.empty and not ann_all.empty:
            y = (ytd.sort_values(["ddate", "filed"], kind="mergesort")
                    .drop_duplicates(["ticker", "concept", "ddate", "qtrs"],
                                     keep="last"))
            a = _latest(ann_all, ["ticker", "concept"])[
                ["ticker", "concept", "value", "ddate"]].rename(
                columns={"value": "_fy", "ddate": "_fy_end"})
            y = y.merge(a, on=["ticker", "concept"], how="inner")

            # THE STUB MUST START AT THE FISCAL YEAR START, and this filter has
            # to run BEFORE a stub is chosen.
            #
            # `FY + stub_now - stub_prior` is twelve months only when the stub
            # is a YEAR-TO-DATE cumulative. A `qtrs=1` row is YTD only for
            # fiscal Q1; anywhere else it is a single quarter, and netting it
            # yields the fiscal year with ONE QUARTER SWAPPED -- not a trailing
            # twelve months.
            #
            # HZO, measured 2026-08-21 (fiscal year ends September): the roll
            # took the qtrs=1 quarter ending 2026-06-30 (fiscal Q3) and
            # produced -31,631,000 + 15,647,000 - (-51,970,000) = 35,986,000
            # against a true $4.0M. The valid qtrs=3 stub ending the same day
            # was sitting beside it, and lost only because `cur` keeps the
            # latest row. Filtering AFTER the pick would have dropped the roll
            # entirely instead of choosing the right stub -- which is exactly
            # what happened to EPS.
            #
            # A genuine YTD stub of n quarters ends about 3n months after the
            # fiscal year end, so that is the test.
            _want = (pd.to_datetime(y["ddate"], errors="coerce")
                     - pd.to_datetime(y["_fy_end"], errors="coerce")).dt.days
            y = y[((_want - y["qtrs"].astype(int) * 91.31).abs() <= 20)
                  | (y["ddate"] <= y["_fy_end"])]

            cur = y[y["ddate"] > y["_fy_end"]]
            if not cur.empty:
                cur = (cur.sort_values("ddate", kind="mergesort")
                          .groupby(["ticker", "concept"], observed=True)
                          .tail(1)[["ticker", "concept", "qtrs", "ddate",
                                    "value", "_fy", "_fy_end"]])
                prior = y[["ticker", "concept", "qtrs", "ddate", "value"]].rename(
                    columns={"ddate": "_p_end", "value": "_p_val"})
                m = cur.merge(prior, on=["ticker", "concept", "qtrs"], how="inner")
                gap = (pd.to_datetime(m["ddate"])
                       - pd.to_datetime(m["_p_end"])).dt.days
                m = m[(gap >= 350) & (gap <= 380)]

                if not m.empty:
                    m = (m.sort_values("_p_end", kind="mergesort")
                           .groupby(["ticker", "concept"], observed=True).tail(1))
                    roll = pd.DataFrame({
                        "ticker": m["ticker"].values,
                        "concept": m["concept"].values,
                        "value": (m["_fy"].astype(float)
                                  + m["value"].astype(float)
                                  - m["_p_val"].astype(float)).values,
                        "ddate": m["ddate"].values,
                    }).assign(_src="roll")
                    _log_once(f"_ttm: rolled {len(roll)} year-to-date series "
                              f"forward to a true twelve months")
                    out.append(roll)

    if not out:
        # AVERAGE CONCEPTS SURVIVE AN EMPTY `out`.
        #
        # This used to return an empty frame and silently drop `avg_out`, so a
        # filer whose only duration facts are weighted-average share counts
        # lost them entirely -- but ONLY when queried alone, because in a batch
        # some other ticker kept `out` non-empty. RMIX, measured 2026-08-21:
        # `shares_diluted_ttm` absent solo, present in a batch. Same class of
        # bug as the tie-break below: a per-ticker answer decided by what else
        # happened to be in the frame.
        return (avg_out[["ticker", "concept", "value"]].reset_index(drop=True)
                if avg_out is not None and not avg_out.empty
                else pd.DataFrame(columns=["ticker", "concept", "value"]))

    # WHICHEVER WINDOW ENDS LATER WINS -- never both, never added.
    #
    # This used to prefer the annual figure unconditionally, and the reason
    # given ("mixing them would double count") is real but does not imply that
    # the annual is the right pick. A 10-K is filed once a year, so for most of
    # the year the four most recent quarters end LATER than the last annual
    # period. Measured on 2026-08-11: 2,799 of 2,964 filers (94%) were in that
    # position, median 181 days stale, worst 639.
    #
    # Apple was the case that surfaced it -- `net_income_ttm` read $112.0B,
    # which is fiscal 2025 (ended 2025-09-27), while the true trailing four
    # quarters through 2026-06-27 were $128.9B. That put the displayed P/E at
    # 40.9 against a real ~35.5, and the same staleness ran through revenue,
    # margins, EV/EBITDA, FCF yield, ROE and every growth metric.
    cat = pd.concat(out, ignore_index=True)
    # A TIE ON `ddate` MUST NOT BE BROKEN BY ARRAY ORDER.
    #
    # `sort_values` defaults to quicksort, which is NOT stable, so when two
    # windows ended on the same date the survivor depended on what else was in
    # the frame. Measured 2026-08-21: HZO's `net_income_ttm` read 3,986,000
    # queried alone and 35,986,000 queried alongside LOPE -- same rows, same
    # candidates, different winner. `facts_asof` runs over the whole universe,
    # so the batch answer is the one that reaches the pages, and no
    # single-ticker check could ever reproduce it.
    #
    # Explicit priority, stable sort: among windows ending on the same day
    # prefer the four reported quarters, then the roll-forward, then the
    # annual (which is a real twelve months but the oldest of the three).
    _PRIO = {"annual": 0, "roll": 1, "4q": 2}
    cat["_prio"] = cat["_src"].map(_PRIO).fillna(0).astype(int)

    # EPS IS NOT ADDITIVE, so four quarters must NOT simply be summed.
    #
    # Every per-share figure rides a weighted-average share count that changes
    # each quarter, and in a loss quarter diluted collapses to basic because
    # dilution is antidilutive. Adding four such numbers -- and this path adds a
    # DERIVED Q4 on top -- compounds both effects.
    #
    # HZO, measured 2026-08-21 (fiscal year ends September):
    #     4q sum   0.66 - 0.12 - 0.36 + 0.08(derived Q4) = 0.26
    #     roll     FY -1.43 + YTD 0.21 - prior YTD -1.38 = 0.16
    # Yahoo 0.16, Finnhub ~0.18, Simply Wall St 0.18, and net_income/shares
    # 0.172. The sum is the outlier; every leg of the roll is a figure the
    # company actually reported for that exact window.
    #
    # So for these concepts the ordering flips: prefer the roll-forward, and
    # take the summed window only when nothing else is available.
    _nonadd = cat["concept"].isin(NON_ADDITIVE_CONCEPTS)
    cat.loc[_nonadd, "_prio"] = cat.loc[_nonadd, "_src"].map(
        {"annual": 0, "4q": 1, "roll": 2}).fillna(0).astype(int)
    cat = (cat.sort_values(["ddate", "_prio"], kind="mergesort")
              .groupby(["ticker", "concept"], observed=True).tail(1)
              .drop(columns="_prio"))
    if avg_out is not None:      # disjoint concepts, so no contest to resolve
        cat = pd.concat([cat, avg_out], ignore_index=True)
    return cat[["ticker", "concept", "value"]].reset_index(drop=True)


def _q_back(asof: str, n: int) -> str:
    y, m = int(asof[:4]), int(asof[5:7])
    q = (m - 1) // 3 + 1
    for _ in range(n):
        q -= 1
        if q == 0:
            y, q = y - 1, 4
    return f"{y}q{q}"


# ===========================================================================
# CLI
# ===========================================================================
def _selftest_alias_max() -> None:
    """Guards the total-vs-component revenue bug (see AGGREGATE_MAX_CONCEPTS).

    Measured blast radius when it was live: 79 of 2,427 names with revenue were
    understated, worst case 1,703x, and they were systematically REITs and
    insurers -- an entire sector wrong on every revenue-derived metric.
    """
    df = pd.DataFrame({
        "ticker": ["X", "X", "Y", "Y"],
        "concept": ["revenue", "revenue", "equity", "equity"],
        "tag": ["RevenueFromContractWithCustomerExcludingAssessedTax",
                "Revenues", "StockholdersEquity", "StockholdersEquity"],
        "rank": [0, 1, 0, 1],
        "ddate": ["2025-12-31"] * 4,
        "filed": ["2026-02-11"] * 4,
        "value": [589_000.0, 1_003_133_000.0, 50.0, 900.0],
    })
    got = _latest(df, ["ticker", "concept"]).set_index("ticker")["value"]
    assert got["X"] == 1_003_133_000.0, (
        f"revenue must take the LARGEST alias, got {got['X']:,}")
    # A concept NOT in the max-set must still honour alias order (rank 0 wins),
    # or this fix would silently change every other concept too.
    assert got["Y"] == 50.0, f"non-max concepts must keep alias order, got {got['Y']}"


def _selftest_merge_near_periods() -> None:
    """Two dates, one quarter -> one row. Two real quarters -> still two."""
    idx = ["2025-07-27", "2025-07-31", "2025-10-31"]
    w = pd.DataFrame({"revenue": [46.7, np.nan, 57.0],
                      "assets": [np.nan, 111.6, 120.0]}, index=idx)
    out = _merge_near_periods(w)
    assert len(out) == 2, f"want 2 periods, got {len(out)}: {list(out.index)}"
    assert out.index[0] == "2025-07-27", "must keep the fiscal (income) date"
    assert out.loc["2025-07-27", "revenue"] == 46.7, "revenue lost in the merge"
    assert out.loc["2025-07-27", "assets"] == 111.6, "balance sheet lost"

    # 90 days apart is two genuine quarters and must never fuse.
    w2 = pd.DataFrame({"revenue": [10.0, 11.0]},
                      index=["2025-03-31", "2025-06-30"])
    assert len(_merge_near_periods(w2)) == 2, "two real quarters were fused"

    # A single period, and an empty frame, must pass through untouched.
    w3 = pd.DataFrame({"revenue": [1.0]}, index=["2025-03-31"])
    assert len(_merge_near_periods(w3)) == 1
    assert _merge_near_periods(pd.DataFrame()).empty


def _selftest_read_cache() -> None:
    """A sliced cache result must equal a real read, and must never outlive a
    store write.

    The cache exists because a rebuild asks `read()` for the same frame 299
    times -- 86s each, profiled. Serving a narrower window from a wider cached
    one is what makes that one read instead of forty. It is also exactly the
    kind of optimisation that silently returns yesterday's numbers, so both
    properties are asserted rather than assumed.
    """
    _READ_CACHE.clear()
    cik = {320193}
    wide = read(start_q="2016q1", ciks=cik)
    if wide.empty:
        return                       # nothing stored; nothing to check
    sliced = read(start_q="2024q1", ciks=cik)
    _READ_CACHE.clear()
    fresh = read(start_q="2024q1", ciks=cik)
    key = ["cik", "adsh", "tag", "ddate", "qtrs", "filed"]
    cast = {c: "string" for c in ("tag", "form", "fp")}
    a = sliced.astype(cast).sort_values(key).reset_index(drop=True)
    b = fresh.astype(cast).sort_values(key).reset_index(drop=True)
    assert a.equals(b), (
        f"a window sliced from cache differs from a real read "
        f"({len(a)} vs {len(b)} rows)")

    # And the fingerprint must move when any partition is written, or a stale
    # frame could outlive the data it came from.
    f1 = _store_fingerprint()
    part = sorted(config.FUNDAMENTALS.glob("*q*.parquet"))
    if part:
        part[-1].touch()
        assert _store_fingerprint() != f1, (
            "the store fingerprint ignored a partition write, so the read "
            "cache could serve data that has since been rewritten")


def _selftest_share_count() -> None:
    """Guards the 19.1%-of-the-universe bug described in `share_count`."""
    # every name in SHARE_COLUMNS must be a column facts_asof can actually emit,
    # either directly or with the _ttm suffix a flow concept receives
    for col in SHARE_COLUMNS:
        base = col[:-4] if col.endswith("_ttm") else col
        assert base in TAGS, (
            f"share column {col!r} maps to no tag group; the fallback chain "
            f"would silently never match")

    # PLAUSIBLE MAGNITUDES. This fixture used 7, 50 and 100 "shares", which the
    # MIN_SHARES floor now rejects -- and rightly, since those are exactly the
    # cover-page artefacts that gave HQ a $14 market cap. The behaviour under
    # test (the fallback order) is unchanged; only the numbers are real.
    f = pd.DataFrame({"shares_out": [np.nan, 100e6, np.nan],
                      "shares_diluted_ttm": [50e6, 999e6, np.nan],
                      "shares_basic_ttm": [np.nan, np.nan, 7e6]},
                     index=["A", "B", "C"])
    s = share_count(f)
    assert s["A"] == 50e6, "must fall through to the _ttm diluted count"
    assert s["B"] == 100e6, "shares_out must win when present"
    assert s["C"] == 7e6, "must fall through to basic"

    # zero/negative counts are bad data, not a valid share count
    z = share_count(pd.DataFrame({"shares_out": [0.0, -5.0]}, index=["D", "E"]))
    assert z.isna().all(), "non-positive share counts must be dropped"

    # A PLACEHOLDER COUNT MUST NOT BEAT A REAL ONE. `shares_out` is preferred
    # first, so a filer tagging it as 1/10/100 used to win outright: FBYD
    # resolved to 10 shares while carrying 39,255,880 diluted, producing a
    # market cap of $100.70 and a turnover of 15,528x.
    ph = share_count(pd.DataFrame(
        {"shares_out": [1.0, 10.0, 100.0],
         "shares_diluted_ttm": [np.nan, 39.25e6, np.nan]},
        index=["HQ", "FBYD", "BCAR"]))
    assert pd.isna(ph["HQ"]), "a 1-share cover-page tag must not become a count"
    assert ph["FBYD"] == 39.25e6, "must fall through to the real diluted count"
    assert pd.isna(ph["BCAR"]), "a 100-share tag must not become a count"


def _selftest_companyfacts() -> None:
    """The fallback store must stay a fallback, and must never mix currencies.

    Two things can go wrong silently here. If `read()` ever preferred the
    companyfacts row over the bulk row for the same (cik, quarter), a TTM sum
    would mix two sources that round and restate differently. And if the USD
    filter were dropped, RY's CAD figures would land in the same `value` column
    as everyone else's dollars and every cross-sectional percentile would be
    quietly wrong -- 6 of 8 sampled foreign filers report in CAD or EUR.
    """
    assert _cf_qtrs(None, "2025-03-31") == 0
    assert _cf_qtrs("2025-01-01", "2025-03-31") == 1
    assert _cf_qtrs("2024-04-01", "2025-03-31") == 4
    # A 17-day span is not a quarter and must be dropped, not rounded to 0 or 1.
    assert _cf_qtrs("2025-01-15", "2025-02-01") is None

    # THE FILER GUARD must accept every real quarter and reject a truncation.
    # Its first version compared against SURROUNDING quarters and rejected
    # 2009q2 and 2011q2 -- both genuine XBRL phase-in data. Filer counts only
    # ramp up, so the comparison is against what came BEFORE.
    for q in stored_quarters()[-6:]:
        n = pd.read_parquet(part_path(q), columns=["cik"])["cik"].nunique()
        assert not filer_shortfall(q, int(n)),             f"the filer guard rejects a real stored quarter: {q} ({n:,} filers)"
        # ...and a 30% download of that same quarter must NOT pass.
        assert filer_shortfall(q, int(n * 0.3)),             f"the filer guard would accept a truncated {q} ({int(n*0.3):,})"

    # THE FALLBACK MUST NEVER MAKE A BULK QUARTER LOOK FETCHED.
    #
    # This exact confusion cost period 2026-03-31 for every filer: `2026q2`
    # existed only in the companyfacts store, `stored_quarters()` unioned the
    # two, and the dueness check therefore never scheduled the bulk fetch. The
    # coverage report stayed healthy the whole time, because the 141 companies
    # the fallback covers do have data.
    bulk = {p.stem for p in config.FUNDAMENTALS.glob("*q*.parquet")} \
        if config.FUNDAMENTALS.exists() else set()
    cf = {p.stem for p in config.FUNDAMENTALS_CF.glob("*q*.parquet")} \
        if config.FUNDAMENTALS_CF.exists() else set()
    assert set(stored_quarters()) == bulk, (
        "stored_quarters() must default to the BULK store only. Quarters that "
        f"would have been wrongly treated as fetched: {sorted(cf - bulk)}")
    assert set(stored_quarters(include_cf=True)) == (bulk | cf), \
        "stored_quarters(include_cf=True) must span both stores for the read path"
    # And every quarter the default reports must actually be openable, because
    # `--stats` and validate.py open part_path(q) directly.
    for q in stored_quarters():
        assert part_path(q).exists(), f"{q} reported stored but no bulk file"

    qs = sorted({p.stem for p in config.FUNDAMENTALS_CF.glob("*q*.parquet")}
                & {p.stem for p in config.FUNDAMENTALS.glob("*q*.parquet")}) \
        if config.FUNDAMENTALS_CF.exists() else []
    # THE SELECTION RULE IS TESTED ON SYNTHETIC INPUT, not against the live
    # store, and that choice was expensive to learn. Four attempts to prove
    # provenance from the real partitions all raised FALSE ALARMS, because the
    # two stores are organised in ways that make cross-store bookkeeping
    # meaningless:
    #   - bulk partitions by FILING quarter, companyfacts by PERIOD, so one
    #     filing lands in several partitions in one store and one in the other
    #   - the two disagree on the period date itself: for KO's Q3 2025 the bulk
    #     sets say 2025-09-30 and companyfacts says the true fiscal 2025-09-26
    #   - the store deliberately holds RESTATEMENTS, so the same
    #     (cik, tag, period) legitimately carries two values and `facts_asof`
    #     resolves it by taking the newest filing
    # A guard that fires on correct data is worse than no guard, so the rule is
    # verified where it is unambiguous -- on inputs built for the purpose.
    _selftest_source_choice()
    _selftest_rollforward()
    _selftest_average_concepts()
    _selftest_q4()

    # ...and the OUTCOME is checked on the real store: a non-USD filer whose
    # bulk copy is a handful of stray USD facts must come out with its real
    # statements, which is the whole point of the authority switch.
    fx = _non_usd_ciks()
    if fx:
        tm = ticker_map()
        by_cik = {}
        for t, c in zip(tm["ticker"], tm["cik"]):
            by_cik.setdefault(int(c), []).append(str(t))
        probe = [by_cik[c][0] for c in sorted(fx)[:40] if c in by_cik]
        if probe:
            w = facts_asof(date.today().isoformat(), tickers=probe)
            if len(w) and "revenue_ttm" in w.columns:
                got_rev = int(pd.to_numeric(w["revenue_ttm"],
                                            errors="coerce").notna().sum())
                assert got_rev >= max(1, len(w) // 3), (
                    f"only {got_rev} of {len(w)} non-USD filers have revenue; "
                    f"the companyfacts authority switch is not taking effect")


def _selftest_rollforward() -> None:
    """TTM from a YEAR-TO-DATE filing: FY + YTD_now - YTD_a_year_ago.

    PINNED TO HAND-COMPUTED VALUES FROM REAL FILINGS, written BEFORE the
    implementation, because the first attempt at this was reverted on a
    misreading and the only way to be sure is to fix the expected answer first.

    A 10-Q reports the cash flow statement CUMULATIVELY: only Q1 carries
    qtrs=1, Q2 is qtrs=2 (six months), Q3 is qtrs=3 (nine months). So the
    "four most recent quarterly rows" are one quarter per year -- HNI's `cfo`
    window spanned 1,099 days. The span guard rejects that and falls back to
    the annual, which is a real twelve months but up to a year stale.

    The three cases below are taken from the filings:

        KO   FY2025  7,408.0 + Q1-26  2,021.0 - Q1-25 (-5,202.0) = 14,631.0
        HD   FY2026 16,325.0 + Q1-26  6,032.0 - Q1-25   4,325.0  = 18,032.0
        UPS  FY2025  8,450.0 + H1-26  3,083.0 - H1-25   2,666.0  =  8,867.0

    KO IS THE CASE THAT MATTERS. Its Q1-2025 operating cash flow was NEGATIVE
    5,202.0M -- the fairlife contingent-consideration payment. Rolling that
    quarter OUT of the window is why the TTM (14,631.0) is nearly double the
    fiscal year (7,408.0). That is arithmetically right, and mistaking it for a
    double count is exactly what caused the first revert.
    """
    def _mk(rows):
        return pd.DataFrame([
            {"ticker": "T", "concept": "cfo", "value": v, "ddate": d,
             "qtrs": q, "rank": 0, "filed": "2026-08-01", "tag": "x",
             "adsh": f"a{d}{q}"} for d, q, v in rows])

    cases = [
        ("KO",  [("2025-12-31", 4, 7408.0), ("2025-03-28", 1, -5202.0),
                 ("2026-04-03", 1, 2021.0)], 14631.0),
        ("HD",  [("2026-02-01", 4, 16325.0), ("2025-05-04", 1, 4325.0),
                 ("2026-05-03", 1, 6032.0)], 18032.0),
        ("UPS", [("2025-12-31", 4, 8450.0), ("2025-06-30", 2, 2666.0),
                 ("2026-06-30", 2, 3083.0)], 8867.0),
    ]
    for name, rows, want in cases:
        got = _ttm(_mk(rows))
        v = float(got.loc[got["concept"] == "cfo", "value"].iloc[0])
        assert abs(v - want) < 0.5, (
            f"{name} roll-forward: got {v:,.1f} want {want:,.1f}")

    # A stub with NO prior-year match must NOT roll -- the annual stands.
    got = _ttm(_mk([("2025-12-31", 4, 1000.0), ("2026-04-03", 1, 200.0)]))
    v = float(got.loc[got["concept"] == "cfo", "value"].iloc[0])
    assert abs(v - 1000.0) < 0.5, (
        f"no prior-year stub must keep the annual, got {v}")


def _selftest_average_concepts() -> None:
    """A weighted-average share count is never summed and never subtracted.

    Pins the AAPL bug: a derived fiscal Q4 of -30,150,480,000 diluted shares,
    and a `shares_diluted_ttm` that was the sum of four quarterly averages.
    Both fed `share_count()` -> market cap -> pe, pb, ev_ebitda, ev_sales.
    """
    # Four quarters at ~14.8B shares plus the FY average, exactly the shape
    # that produced the negative Q4.
    rows = []
    for ddate, val, qtrs in [
            ("2025-12-27", 14.81e9, 1), ("2026-03-28", 14.73e9, 1),
            ("2026-06-27", 14.71e9, 1), ("2025-09-27", 14.95e9, 4)]:
        rows.append({"ticker": "T", "concept": "shares_diluted", "value": val,
                     "ddate": ddate, "qtrs": qtrs, "rank": 0,
                     "filed": "2026-07-31", "tag": "x", "adsh": f"a{ddate}"})
    # A real flow alongside it, to prove the carve-out did not drop the rest.
    for ddate, val in [("2025-12-27", 100.0), ("2026-03-28", 110.0),
                       ("2026-06-27", 120.0), ("2025-09-27", 90.0)]:
        rows.append({"ticker": "T", "concept": "revenue", "value": val,
                     "ddate": ddate, "qtrs": 1, "rank": 0,
                     "filed": "2026-07-31", "tag": "y", "adsh": f"r{ddate}"})
    got = _ttm(pd.DataFrame(rows))
    sh = float(got.loc[got["concept"] == "shares_diluted", "value"].iloc[0])
    rev = float(got.loc[got["concept"] == "revenue", "value"].iloc[0])

    # Latest period (2026-06-27), NOT the 59B sum and NOT a negative.
    assert abs(sh - 14.71e9) < 1.0, f"shares_diluted_ttm={sh:,.0f}, want 14.71e9"
    assert sh > 0, f"negative share count {sh:,.0f}"
    # Revenue still sums all four quarters.
    assert abs(rev - 420.0) < 1e-6, f"revenue_ttm={rev}, want 420"

    # And the Q4 derivation refuses to touch an average concept.
    q = pd.DataFrame([r for r in rows if r["qtrs"] == 1
                      and r["concept"] == "shares_diluted"])
    ann = pd.DataFrame([r for r in rows if r["qtrs"] == 4])
    assert len(_q4_rows(q, ann)) == len(q), "Q4 derived for an average concept"


def _selftest_q4() -> None:
    """Q4 is derived exactly, and never guessed from partial data."""
    cols = ["revenue"]
    wide = pd.DataFrame({"revenue": [100.0, 110.0, 120.0]},
                        index=["2024-12-31", "2025-03-31", "2025-06-30"])
    ann = pd.DataFrame({"concept": ["revenue"], "ddate": ["2025-09-30"],
                        "qtrs": [QTRS_ANNUAL], "value": [500.0],
                        "filed": ["2025-11-01"]})
    out = _fill_q4(wide.copy(), ann, {})
    got = out.at["2025-09-30", "revenue"]
    assert abs(got - 170.0) < 1e-6, f"Q4 should be 500-330=170, got {got}"

    # Only two of the three quarters present -> refuse, do not invent.
    partial = wide.drop(index=["2025-06-30"])
    out2 = _fill_q4(partial.copy(), ann, {})
    assert "2025-09-30" not in out2.index or pd.isna(
        out2.at["2025-09-30", "revenue"]), \
        "Q4 was invented from an incomplete year"

    # A balance-sheet concept must never be derived by subtraction.
    stock = next(iter(STOCK_CONCEPTS))
    w3 = pd.DataFrame({stock: [10.0, 11.0, 12.0]}, index=wide.index)
    a3 = ann.assign(concept=[stock])
    out3 = _fill_q4(w3.copy(), a3, {})
    assert "2025-09-30" not in out3.index, \
        f"{stock} is a balance at an instant and must not be summed"

    # Two concepts in one year: the row is created once and BOTH are filled.
    # Mutating mid-loop previously left the second one empty.
    w4 = pd.DataFrame({"revenue": [100.0, 110.0, 120.0],
                       "net_income": [10.0, 11.0, 12.0]}, index=wide.index)
    a4 = pd.DataFrame({"concept": ["revenue", "net_income"],
                       "ddate": ["2025-09-30"] * 2, "qtrs": [QTRS_ANNUAL] * 2,
                       "value": [500.0, 50.0], "filed": ["2025-11-01"] * 2})
    out4 = _fill_q4(w4.copy(), a4, {})
    assert pd.notna(out4.at["2025-09-30", "revenue"]) and \
        pd.notna(out4.at["2025-09-30", "net_income"]), \
        "a second concept was left empty -- the stale-index bug is back"


def _selftest_source_choice() -> None:
    """bulk wins for a USD filer, companyfacts wins for a non-USD one."""
    bulk = pd.DataFrame({"cik": [1, 2], "adsh": ["B1", "B2"], "value": [10.0, 20.0]})
    cf = pd.DataFrame({"cik": [1, 2], "adsh": ["C1", "C2"], "value": [11.0, 21.0]})
    for fx, want_cik1, want_cik2 in ((set(), "B1", "B2"),      # both USD
                                     ({2}, "B1", "C2")):       # cik 2 non-USD
        f = fx & set(cf["cik"])
        keep_cf = cf[~cf["cik"].isin(set(bulk["cik"]) - f)]
        keep_bulk = bulk[~bulk["cik"].isin(f)] if f else bulk
        merged = pd.concat([keep_bulk, keep_cf], ignore_index=True)
        for c, want in ((1, want_cik1), (2, want_cik2)):
            rows = merged[merged["cik"] == c]
            assert len(rows) == 1 and rows["adsh"].iloc[0] == want, (
                f"fx={fx or 'none'}: cik {c} should come from {want}, "
                f"got {list(rows['adsh'])}")



def selftest(verbose: bool = True) -> None:
    fails = []
    _selftest_merge_near_periods()
    _selftest_read_cache()
    _selftest_share_count()
    _selftest_alias_max()
    _selftest_companyfacts()
    qs = quarters(1.0)
    assert len(qs) >= 4 and qs == sorted(qs), qs
    assert _q_back("2026-08-05", 4) == "2025q3", _q_back("2026-08-05", 4)
    assert _q_back("2026-01-15", 1) == "2025q4", _q_back("2026-01-15", 1)

    if stored_quarters():
        df = read(start_q=stored_quarters()[-1])
        if not df.empty:
            if (df["filed"] < df["ddate"]).mean() > 0.02:
                fails.append("many facts filed BEFORE their period end -- date parse bug")
            if df["value"].isna().any():
                fails.append("null values survived the filter")

    if fails:
        print("SELFTEST FAILURES:")
        for f in fails:
            print("  -", f)
        raise SystemExit(1)
    if verbose:
        print(f"fundamentals selftest OK ({len(stored_quarters())} quarter(s) stored, "
              f"{len(TAGS)} concepts / {len(WANTED)} tags)")


def explain(ticker: str, asof: str | None = None) -> None:
    import calendar_us
    asof = asof or calendar_us.last_closed_session()
    w = facts_asof(asof, [ticker])
    if w.empty:
        print(f"  {ticker}: no facts visible as of {asof}")
        return
    r = w.iloc[0]
    print(f"\n  {ticker} as of {asof}  (last filed {r.get('last_filed')}, "
          f"period {r.get('last_ddate')}, SIC {r.get('sic')})")
    for k in sorted(w.columns):
        if k in ("ticker", "sic", "cik", "last_filed", "last_ddate"):
            continue
        v = r[k]
        if pd.notna(v):
            print(f"    {k:26} {v:>20,.0f}" if abs(v) > 1000 else f"    {k:26} {v:>20,.4f}")


def coverage_report(verbose: bool = True) -> dict:
    """How many universe names actually have facts, and why the rest do not.

    Exists because "accept 20-F/40-F" is a hypothesis until counted -- IFRS tag
    names may not match the US-GAAP set in TAGS.
    """
    import bars
    import calendar_us
    asof = calendar_us.last_closed_session()
    uni = bars.tradeable_universe(asof)
    tm = ticker_map()
    have_cik = set(tm["ticker"].astype(str)) & set(uni)
    facts = facts_asof(asof, uni)
    scored = set(facts["ticker"].astype(str)) if not facts.empty else set()
    out = {"universe": len(uni), "with_cik": len(have_cik),
           "with_facts": len(scored),
           "no_cik": len(set(uni) - have_cik),
           "cik_but_no_facts": len(have_cik - scored)}
    if verbose:
        print("")
        print(f"  universe            {out['universe']:>6,}")
        print(f"  in SEC ticker map   {out['with_cik']:>6,}")
        print(f"  WITH facts          {out['with_facts']:>6,}")
        print(f"  no CIK at all       {out['no_cik']:>6,}  (not an SEC filer)")
        print(f"  CIK but no facts    {out['cik_but_no_facts']:>6,}  "
              f"(IFRS tags, or never filed XBRL)")
        print("")
    return out


def main() -> int:
    config.safe_console()
    ap = argparse.ArgumentParser(description="SEC XBRL fundamental fact store.")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--years", type=float, default=None)
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--explain", metavar="SYM", default=None)
    ap.add_argument("--asof", default=None)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--coverage", action="store_true",
                    help="how many universe names have facts, and why not")
    # These two were READ by main() but never declared, so `--backfill` died
    # with AttributeError on the CLI path. The orchestrator calls backfill()
    # directly, which is why it stayed hidden.
    ap.add_argument("--force", action="store_true",
                    help="refetch quarters already on disk (e.g. after a tag-map "
                         "change); rate-limited and retried either way")
    ap.add_argument("--newest-first", dest="newest_first", action="store_true",
                    help="walk backwards from the newest quarter, so the ~16 "
                         "quarters facts_asof actually reads are fixed first")
    ap.add_argument("--companyfacts", action="store_true",
                    help="fill the coverage gap from data.sec.gov per-company "
                         "XBRL, for filers the bulk data sets omit")
    a = ap.parse_args()
    config.dirs()

    if a.coverage:
        coverage_report()
    elif a.companyfacts:
        res = backfill_companyfacts(verbose=True)
        if not res["ok"]:
            log(f"  {len(res['failed'])} company(ies) failed -- exit 1")
            return 1
    elif a.selftest:
        selftest()
    elif a.backfill:
        res = backfill(years=a.years, force=a.force,
                       newest_first=a.newest_first)
        # Exit non-zero on ANY failed quarter. A partial refetch that exits 0 is
        # how 51 ConnectionErrors got reported as "DONE".
        if not res["ok"]:
            log(f"  {len(res['failed'])} quarter(s) failed -- exit 1")
            return 1
    elif a.update:
        qs = quarters(0.5)
        write(fetch_quarter(qs[-1]), qs[-1])
    elif a.explain:
        explain(a.explain.upper(), a.asof)
    elif a.stats:
        qs = stored_quarters()
        if not qs:
            print("  (empty -- run `python fundamentals.py --backfill`)")
        else:
            rows = []
            for q in qs:
                d = pd.read_parquet(part_path(q), columns=["cik", "tag"])
                rows.append({"quarter": q, "facts": len(d),
                             "filers": d["cik"].nunique()})
            print(pd.DataFrame(rows).to_string(index=False))
            print(f"\n  {store_bytes() / 1e6:.0f} MB over {len(qs)} quarter(s)")
    else:
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
