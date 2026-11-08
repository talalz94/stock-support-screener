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


def main() -> int:
    if "--fetch" in sys.argv:
        fetch()
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
