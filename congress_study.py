#!/usr/bin/env python
"""Do House members' stock purchases predict returns in our own data? An event study.

DEFINITIONS, fixed before any result was seen (2026-10-27):
  trades     congress.read() (amended reports supersede originals), House only, from the
             PTRs whose text could be read (paper filings are scanned images: counted
             in `coverage`, not used).
  purchase   ttype P on a STOCK ([ST]) whose ticker has been in our universe, and a
             real decision: `kind` == "trade" (automatic dividend reinvestments, gifts
             and donations, option exercises, transfers and rebalancing are out).
  event      (ticker, the day the report was FILED with the Clerk): the first day anyone
             else could act. Entry: the close of the next session (event_returns).
             A second version enters on the TRADE date: what the member got, which no
             reader could -- the gap between the two is what the reporting delay costs.
  episode    one per ticker per 60 sessions.  benchmark: same-day liquidity fifth.
TESTS: 9 signals x 20/40/60 sessions = 27 (+3 trade-date counterfactuals, not
tradeable); about 1.5 would reach |t| >= 2 by chance.

Writes data/_congress_events.parquet and data/_congress_study_summary.json.
Run: python congress_study.py
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import config
import congress as CG
import event_returns as ER

GAP = 60
SPLIT = "2021-07-01"
OUT_EVENTS = config.DATA / "_congress_events.parquet"
OUT_SUMMARY = config.DATA / "_congress_study_summary.json"


def coverage() -> pd.DataFrame:
    led = CG._ledger()
    led["year"] = led.year.astype(int)
    return led.groupby("year").status.value_counts().unstack(fill_value=0)


def _events(tr: pd.DataFrame, date_col: str) -> pd.DataFrame:
    """One row per (ticker, date): members, amounts and kinds that day, plus the
    30-day member count as known that day."""
    g = (tr.groupby(["ticker", date_col], as_index=False)
           .agg(members=("member", "nunique"), amount_lo=("amount_lo", "sum"),
                max_lo=("amount_lo", "max")))
    g = g.rename(columns={date_col: "event_date"}).sort_values(["ticker", "event_date"])
    n30 = []
    for t, x in g.groupby("ticker", sort=False):
        d = pd.to_datetime(x.event_date, errors="coerce").values.astype("datetime64[D]")
        mem = tr[tr.ticker == t][[date_col, "member"]]
        md = pd.to_datetime(mem[date_col], errors="coerce").values.astype("datetime64[D]")
        for di in d:
            w = (md > di - np.timedelta64(30, "D")) & (md <= di)
            n30.append(int(mem.member[w].nunique()))
    g["members_30"] = n30
    return g


def run(verbose: bool = True) -> dict:
    t0 = time.time()
    cov = coverage()
    tr = CG.read()
    uni = set(pd.read_parquet(config.UNIVERSE_FILE)["ticker"].astype(str))
    tr = tr[tr.ticker.isin(uni) & tr.filing_date.notna()]
    stock = tr[tr.asset_type == "ST"]
    buys = stock[(stock.ttype == "P") & (stock.kind == "trade")]
    drip = stock[(stock.ttype == "P") & (stock.kind == "dividend reinvestment")]
    sells = stock[stock.ttype.str.startswith("S") & (stock.kind == "trade")]
    calls = tr[(tr.asset_type == "OP") & (tr.ttype == "P") & (tr.option_kind == "call")]
    if verbose:
        print(f"trades {len(tr):,} in universe tickers; stock purchases {len(buys):,}, sales {len(sells):,}, "
              f"call purchases {len(calls):,}, dividend reinvestments {len(drip):,}; {time.time() - t0:.0f}s")
    panel = ER.Panel()
    sets = {
        "any member stock purchase": (buys, None),
        "purchase >= $15,001": (buys[buys.amount_lo >= 15001], None),
        "purchase >= $50,001": (buys[buys.amount_lo >= 50001], None),
        "purchase >= $250,001": (buys[buys.amount_lo >= 250001], None),
        "call options bought": (calls, None),
        "2+ members bought within 30 days": (buys, "members_30"),
        "sale >= $15,001 (selling)": (sells[sells.amount_lo >= 15001], None),
        "dividend reinvestment (control, expect ~0)": (drip, None),
        "spouse / joint / child purchase": (buys[buys.owner != "self"], None),
    }
    results, all_ev = [], []
    for name, (rows, need) in sets.items():
        ev = panel.measure(_events(rows, "filing_date"))
        if need == "members_30":
            ev = ev[ev.members_30 >= 2]
        ev["test"] = name
        all_ev.append(ev)
        eps = ER.first_in_window(ev, GAP, panel)
        for h in ER.HORIZONS:
            r = ER.summarize(eps, h, name)
            r["first_half"] = ER.summarize(eps[eps.entry < SPLIT], h, name)
            r["second_half"] = ER.summarize(eps[eps.entry >= SPLIT], h, name)
            results.append(r)
    # What the member got, entering on the trade date (not tradeable by anyone else).
    cf = panel.measure(_events(buys.dropna(subset=["trans_date"]), "trans_date"))
    cf_eps = ER.first_in_window(cf, GAP, panel)
    counterfactual = [ER.summarize(cf_eps, h, "entering on the TRADE date (not tradeable)") for h in ER.HORIZONS]
    lag = (pd.to_datetime(buys.filing_date, errors="coerce") - pd.to_datetime(buys.trans_date, errors="coerce")).dt.days
    pd.concat(all_ev, ignore_index=True).to_parquet(OUT_EVENTS, index=False)
    out = {"measured_on": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "tests": len(results), "expected_by_chance": round(len(results) * 0.05, 1),
           "definitions": __doc__.split("TESTS:")[0].strip(),
           "coverage": {str(k): v for k, v in cov.to_dict("index").items()},
           "report_lag_days": {"median": float(lag.median()), "p90": float(lag.quantile(0.9))},
           "results": results, "counterfactual": counterfactual}
    OUT_SUMMARY.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    if verbose:
        pd.set_option("display.width", 220)
        tab = pd.DataFrame([{k: v for k, v in r.items() if k not in ("first_half", "second_half")}
                            | {"t_1st": r["first_half"]["t"], "t_2nd": r["second_half"]["t"]} for r in results])
        print(tab.to_string(index=False))
        print(pd.DataFrame(counterfactual).to_string(index=False))
        print("report lag (trade -> filing), days:", out["report_lag_days"])
        print("coverage by year:"); print(cov.to_string())
        print(f"{len(results)} tests, ~{out['expected_by_chance']} by chance; {time.time() - t0:.0f}s")
    return out


if __name__ == "__main__":
    run()
    sys.exit(0)
