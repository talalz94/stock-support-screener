"""Stocks sitting at a level that the measured evidence says tends to hold.

MEASURED 2026-10-08 (see NEXT_SESSION.md): this rule gives a LOWER-VARIANCE basket and NO extra
return; `python rule_accuracy.py` reproduces the evidence. It is a list of names that fit an
idea, not a list of buys.

THE RULE is the profile the 2026-08-30 study found separating zone arrivals that
bounced from those that broke, written as plain round-number thresholds BEFORE
being scored here (no tuning):

    AT or NEAR a level (<= 6% above it)
    near its 1-year high         pct_hi        >= 0.80   (strongest separator, t=+14.8)
    sound fundamentals           fund_score    >= 55     (t=+5.2)
    little attention             hype_score    <= 45     (hype ran the WRONG way: t=-4.9)

A name missing any input is EXCLUDED, never filled in: not reported is not zero.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import pandas as pd

import bars as B, calendar_us, config, macro

BAND_MAX, PCT_HI_MIN, FUND_MIN, HYPE_MAX = 0.06, 0.80, 55.0, 45.0


def current(asof):
    z = pd.read_parquet(config.ZONES / f"{asof}.parquet")
    z["suspect_split"] = z["suspect_split"].fillna(False).astype(bool)
    z["no_support"] = z["no_support"].fillna(False).astype(bool)
    trusted = z[~z["suspect_split"]]
    lv = trusted[~trusted["no_support"]].sort_values("dist_pct").groupby("ticker", as_index=False).head(1)

    s = pd.read_parquet(config.SCORES / f"{asof[:7]}.parquet",
                        columns=["session", "module", "metric", "ticker", "value"])
    s = s[s.session == asof]
    want = {("fundamental", m): m for m in ("fund_score", "roic", "f_score", "interest_cover",
                                            "pe", "fcf_yield", "rev_growth", "mktcap")}
    want.update({("hype", m): m for m in ("hype_score", "premium_score", "attention_score")})
    s = s[[(a, b) in want for a, b in zip(s.module, s.metric)]]
    w = s.pivot_table(index="ticker", columns="metric", values="value", aggfunc="first").reset_index()

    ps = B.load_panel_stats()[["ticker", "last_close", "dollar_vol_20"]]
    ps["ticker"] = ps["ticker"].astype(str)
    sm = macro.load_sector_map()[["ticker", "sector"]].drop_duplicates("ticker")

    df = (lv.merge(w, on="ticker", how="left").merge(ps, on="ticker", how="left")
            .merge(sm, on="ticker", how="left"))
    return z, trusted, df


def rule(df):
    return ((df["dist_pct"] <= BAND_MAX) & (df["pct_hi"] >= PCT_HI_MIN)
            & (df["fund_score"] >= FUND_MIN) & (df["hype_score"] <= HYPE_MAX))


if __name__ == "__main__":
    asof = calendar_us.last_closed_session()
    z, trusted, df = current(asof)
    print(f"session {asof}")
    n_all = trusted["ticker"].nunique()
    print(f"\nFUNNEL (each stage keeps only names that pass AND have the data)")
    print(f"  names with trusted prices                       {n_all:>5,}")
    print(f"  ... with a support level within 16% below       {len(df):>5,}")
    st1 = df["dist_pct"] <= BAND_MAX
    st2 = st1 & (df["pct_hi"] >= PCT_HI_MIN)
    st3 = st2 & (df["fund_score"] >= FUND_MIN)
    st4 = st3 & (df["hype_score"] <= HYPE_MAX)
    print(f"  ... AT or NEAR it (<= 6% above)                 {int(st1.sum()):>5,}")
    print(f"  ... AND within 20% of the 1-year high           {int(st2.sum()):>5,}")
    print(f"  ... AND fundamentals >= 55 (has a score)        {int(st3.sum()):>5,}")
    print(f"  ... AND hype <= 45                              {int(st4.sum()):>5,}   <- the list")
    print(f"  (names missing a fund_score among stage 2: {int((st2 & df['fund_score'].isna()).sum())}, "
          f"missing hype: {int((st3 & df['hype_score'].isna()).sum())} -- excluded, not guessed)")

    out = df[st4].copy()
    try:
        fl = pd.read_parquet(config.FLAGS / f"{asof}.parquet")
        out["bounce_flag"] = out["ticker"].isin(set(fl["ticker"].astype(str)))
    except Exception:
        out["bounce_flag"] = False
    out = out.sort_values(["dist_pct", "ticker"]).reset_index(drop=True)
    keep = ["ticker", "band", "dist_pct", "level", "last_close", "touches", "dd_break_rate",
            "pct_hi", "fund_score", "interest_cover", "f_score", "roic", "pe", "rev_growth",
            "hype_score", "premium_score", "mktcap", "dollar_vol_20", "sector", "bounce_flag"]
    out = out[[c for c in keep if c in out.columns]]
    p = config.DATA / f"_good_level_{asof.replace('-', '')}.csv"
    out.to_csv(p, index=False)
    print(f"\n{len(out)} names -> {p.name}")
    print(f"  AT a level (<=2.5%): {int((out.band=='AT').sum())}   NEAR (2.5-6%): {int((out.band=='NEAR').sum())}"
          f"   also flagged by the bounce screen: {int(out.bounce_flag.sum())}")
    print(f"  median market cap ${out.mktcap.median()/1e9:.1f}B   median dollar volume ${out.dollar_vol_20.median()/1e6:.1f}M/day")
    print("  sectors:", out.sector.value_counts().head(6).to_dict())
