#!/usr/bin/env python
"""Correct the SIC sector map with Finnhub's industry labels (asked 2026-11-08).

    python industry_map.py --fetch     Finnhub's free company profile (finnhubIndustry) for every
                                       tradeable name -> data/_finnhub_industry.parquet (~1 h at
                                       60/min; resumable, saved every 100 names)
    python industry_map.py --compare   SIC sector vs Finnhub industry, the disagreements listed
    python industry_map.py --apply     the corrected sectors into the sector map (backed up first)

WHY: macro.py maps SIC 3827-3851 to Healthcare, so 3827 optical instruments (KLAC, NVMI, CAMT,
COHR) and 3829 measuring devices (ONTO, MIR, GEOS) sit in Healthcare; SIC 3559 "special
machinery" puts AMAT and LRCX in Industrials. SIC codes cannot tell a chip-equipment maker from
a packaging-machine maker; a company's own industry label can. Found 2026-11-08 when KLA's news
showed up among Healthcare headlines in the narrative inputs.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request

import pandas as pd

import config

OUT = config.DATA / "_finnhub_industry.parquet"
URL = "https://finnhub.io/api/v1/stock/profile2?symbol={sym}"
GAP = 1.05                      # 60 calls/min on the free key; providers.py uses the same
SAVE_EVERY = 100


def fetch(verbose: bool = True) -> pd.DataFrame:
    import bars
    import providers
    key = providers._finnhub_key()
    if not key:
        raise SystemExit("no FINNHUB_KEY in the environment or .env")
    names = sorted(set(bars.tradeable_universe()))
    have = pd.read_parquet(OUT) if OUT.exists() else pd.DataFrame(columns=["ticker"])
    todo = [t for t in names if t not in set(have.ticker)]
    if verbose:
        print(f"finnhub profiles: {len(names):,} tradeable names, {len(have):,} cached, {len(todo):,} to "
              f"fetch (~{len(todo) * GAP / 60:.0f} min)", flush=True)
    rows, last, t0 = [], 0.0, time.time()

    def save():
        nonlocal have, rows
        if rows:
            have = pd.concat([have, pd.DataFrame(rows)], ignore_index=True).drop_duplicates("ticker", keep="last")
            have.to_parquet(OUT, index=False)
            rows = []

    for n, t in enumerate(todo, 1):
        wait = GAP - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
        last = time.monotonic()
        rec = {"ticker": t, "fetched": pd.Timestamp.now(), "industry": None, "name": None,
               "country": None, "exchange": None, "mcap_musd": None, "ok": False}
        for attempt in range(3):
            try:
                url = URL.format(sym=urllib.parse.quote(t)) + f"&token={key}"
                with urllib.request.urlopen(url, timeout=30) as r:
                    p = json.loads(r.read()) or {}
                rec.update(industry=p.get("finnhubIndustry") or None, name=p.get("name"),
                           country=p.get("country"), exchange=p.get("exchange"),
                           mcap_musd=p.get("marketCapitalization"), ok=bool(p))
                break
            except Exception as exc:                             # noqa: BLE001
                if "429" in str(exc):
                    time.sleep(20 * (attempt + 1))               # over the limit: back off, retry
                    last = time.monotonic()
                    continue
                break                                            # unknown symbol etc.: recorded as not ok
        rows.append(rec)
        if n % SAVE_EVERY == 0:
            save()
            if verbose:
                print(f"  {n:,}/{len(todo):,}; {time.time() - t0:.0f}s", flush=True)
    save()
    if verbose:
        print(f"done: {int(have.ok.sum()):,} of {len(have):,} with a profile, "
              f"{int(have.industry.notna().sum()):,} with an industry", flush=True)
    return have


# ------------------------------------------------------------------------- the correction
OVERRIDES = config.DATA / "_sector_overrides.parquet"

# Finnhub industry -> our sector names. Decided 2026-11-08 from the full disagreement table
# (1,025 of 3,263 labelled names): most moves follow the GICS-style label (REITs to Real Estate,
# chip equipment to Semiconductors, life-science tools to Healthcare, apparel out of Materials,
# pipelines and tankers to Energy, payment processors to Financials). Labels too mixed to trust
# are handled in `_target` below or left alone.
FH2SEC = {
    "Banking": "Financials", "Financial Services": "Financials", "Insurance": "Financials",
    "Real Estate": "Real Estate", "Biotechnology": "Biotech", "Pharmaceuticals": "Biotech",
    "Health Care": "Healthcare", "Life Sciences Tools & Services": "Healthcare",
    "Technology": "Technology", "Semiconductors": "Semiconductors",
    "Communications": "Technology",                  # network equipment (CSCO, ANET, CIEN), not media
    "Machinery": "Industrials", "Aerospace & Defense": "Aerospace", "Professional Services": "Industrials",
    "Commercial Services & Supplies": "Industrials", "Trading Companies & Distributors": "Industrials",
    "Industrial Conglomerates": "Industrials", "Construction": "Construction", "Building": "Construction",
    "Road & Rail": "Transport", "Marine": "Transport", "Airlines": "Transport",
    "Logistics & Transportation": "Transport", "Transportation Infrastructure": "Transport",
    "Energy": "Energy", "Metals & Mining": "Mining", "Chemicals": "Chemicals", "Packaging": "Materials",
    "Paper & Forest": "Materials", "Retail": "Consumer Discretionary",
    "Hotels, Restaurants & Leisure": "Consumer Discretionary",
    "Textiles, Apparel & Luxury Goods": "Consumer Discretionary", "Auto Components": "Consumer Discretionary",
    "Automobiles": "Consumer Discretionary", "Diversified Consumer Services": "Consumer Discretionary",
    "Leisure Products": "Consumer Discretionary", "Distributors": "Consumer Discretionary",
    "Food Products": "Consumer Staples", "Beverages": "Consumer Staples", "Tobacco": "Consumer Staples",
    "Media": "Communication", "Telecommunication": "Communication", "Utilities": "Utilities",
}
AMBIGUOUS = {"Consumer products", "N/A"}           # never override on these
# "Electrical Equipment" mixes true industrials (AME, AYI) with electronics makers (APH, CLS,
# BHE: GICS IT). It overrides only a sector that plainly does not fit.
ELECTRICAL_MISFITS = {"Healthcare", "Other", "Consumer Discretionary", "Utilities", "Financials"}

# Our named sub-industries and the sector each belongs to: a moved name keeps its SIC
# sub-industry only when it fits the new sector.
SUB_SECTOR = {
    "Oil & gas producers": "Energy", "Oil & gas drilling & services": "Energy", "Oil & gas equipment": "Energy",
    "Refiners": "Energy", "Gas pipelines": "Energy", "Petroleum marketing": "Energy", "Coal": "Coal",
    "Gas utilities": "Utilities", "Electric utilities": "Utilities", "Water utilities": "Utilities",
    "Banks": "Financials", "Insurance": "Financials", "Asset managers & brokers": "Financials",
    "REITs": "Real Estate", "Semiconductors": "Semiconductors", "Software & internet": "Technology",
    "Computer hardware": "Technology", "Communications equipment": "Technology",
    "Electronic components": "Technology", "Pharma & biotech": "Biotech", "Medical devices": "Healthcare",
    "Healthcare services": "Healthcare", "Aerospace & defense": "Aerospace",
    "Autos & parts": "Consumer Discretionary", "Restaurants": "Consumer Discretionary",
    "Retail": "Consumer Discretionary", "Homebuilders": "Construction",
    "Construction & engineering": "Construction", "Chemicals": "Chemicals", "Metals & mining": "Mining",
    "Food & beverage": "Consumer Staples", "Telecom": "Communication",
    "Media & entertainment": "Communication", "Transport": "Transport", "Machinery": "Industrials",
    "Electrical equipment": "Industrials",
}
FH2SUB = {
    "Semiconductors": "Semiconductors", "Biotechnology": "Pharma & biotech", "Pharmaceuticals": "Pharma & biotech",
    "Banking": "Banks", "Insurance": "Insurance", "Real Estate": "REITs", "Machinery": "Machinery",
    "Electrical Equipment": "Electrical equipment", "Aerospace & Defense": "Aerospace & defense",
    "Metals & Mining": "Metals & mining", "Chemicals": "Chemicals", "Retail": "Retail",
    "Media": "Media & entertainment", "Telecommunication": "Telecom", "Communications": "Communications equipment",
    "Food Products": "Food & beverage", "Beverages": "Food & beverage", "Automobiles": "Autos & parts",
    "Auto Components": "Autos & parts", "Construction": "Construction & engineering",
    "Road & Rail": "Transport", "Marine": "Transport", "Airlines": "Transport",
    "Logistics & Transportation": "Transport", "Transportation Infrastructure": "Transport",
}


def _target(industry: str | None, sic, old: str | None) -> str | None:
    """The corrected sector, or None to leave the SIC sector alone."""
    if not industry or industry in AMBIGUOUS:
        return None
    try:
        s = int(sic)
    except (TypeError, ValueError):
        s = -1
    if industry == "Electrical Equipment":
        return "Industrials" if old in ELECTRICAL_MISFITS else None
    if industry == "Retail" and (5140 <= s <= 5149 or 5400 <= s <= 5499):
        return "Consumer Staples"                     # food distributors and grocers (SYY, KR)
    return FH2SEC.get(industry)


def proposed() -> pd.DataFrame:
    """Every name whose sector (or sub-industry) the Finnhub label changes."""
    import macro
    import sectors
    f = pd.read_parquet(OUT)
    m = macro.load_sector_map(raw=True).drop_duplicates("ticker")
    x = f.merge(m[["ticker", "sic", "sector"]], on="ticker", how="inner")
    x["old_subind"] = [sectors.subindustry(s, c) for s, c in zip(x.sic, x.sector)]
    x["new_sector"] = [_target(i, s, o) for i, s, o in zip(x.industry, x.sic, x.sector)]
    x = x[x.new_sector.notna() & (x.new_sector != x.sector)].copy()

    def sub(r) -> str:
        if SUB_SECTOR.get(r.old_subind) == r.new_sector:
            return r.old_subind
        cand = FH2SUB.get(r.industry)            # only when it belongs to the new sector (KR: not "Retail")
        return cand if cand and SUB_SECTOR.get(cand) == r.new_sector else f"Other {r.new_sector}"
    x["new_subind"] = [sub(r) for r in x.itertuples(index=False)]
    return x.rename(columns={"sector": "old_sector"})[
        ["ticker", "name", "industry", "sic", "old_sector", "new_sector", "old_subind", "new_subind"]]


def compare() -> pd.DataFrame:
    x = proposed()
    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 200)
    t = (x.groupby(["old_sector", "new_sector"]).ticker
         .agg(n="size", eg=lambda s: ", ".join(sorted(s)[:8])).reset_index().sort_values("n", ascending=False))
    print(f"{len(x):,} names would move sector")
    print(t.to_string(index=False))
    return x


def apply(verbose: bool = True) -> pd.DataFrame:
    x = proposed()
    o = x.rename(columns={"new_sector": "sector", "new_subind": "subind"})[["ticker", "sector", "subind", "industry"]]
    o["source"] = "finnhub profile2 finnhubIndustry"
    o["applied_on"] = pd.Timestamp.now().strftime("%Y-%m-%d")
    o.to_parquet(OVERRIDES, index=False)
    if verbose:
        print(f"wrote {len(o):,} sector overrides -> {OVERRIDES}")
    return o


def overrides() -> pd.DataFrame:
    """ticker -> corrected sector and sub-industry; empty when none were applied."""
    if not OVERRIDES.exists():
        return pd.DataFrame(columns=["ticker", "sector", "subind"])
    return pd.read_parquet(OVERRIDES, columns=["ticker", "sector", "subind"]).drop_duplicates("ticker")


def main() -> int:
    if "--fetch" in sys.argv:
        fetch()
        return 0
    if "--compare" in sys.argv:
        compare()
        return 0
    if "--apply" in sys.argv:
        apply()
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
