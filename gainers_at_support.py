#!/usr/bin/env python
"""Gainers at support, as of one session: liquid stocks at an attractive level that were
often among the market's biggest daily gainers -- the 2026-10-23 definition, plus the
news-event warnings and the next earnings date.

    python gainers_at_support.py 2026-10-30   -> data/watchlists/gainers_at_support_20261030/

DEFINITION (fixed before any result was seen, as on 10-23):
  universe     close >= $5 and 20-day average dollar volume >= $20M on the session
  gain_days    sessions in the last 252 where the stock was among the market-wide top-20
               daily % gainers (same eligibility, on the prior close)
  attractive   the nearest support zone 0-6% below the close (Zones page, AT/NEAR)
  list         up to 10 per sector, ranked by gain_days, then fund score
Added since: sector score/rank (validated out of sample), combo_h20 label, warning filings
and next earnings (news_events.page_flags, point in time), insider badge.

Writes list.csv (the list) and all_at_support.csv (every liquid name at/near support).
Put a track.json ({"entry": "<session>", "title": "..."}) in the folder and the nightly
`watchlists` step re-measures the list from its entry close. Research screens, not advice.
"""
from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

import config
import news_events as NE
import sectors


def build(asof: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    C, V = sectors.load_wide()
    C, V = C.loc[:asof], V.loc[:asof]
    if C.index[-1] != asof:
        raise SystemExit(f"no bars for {asof} (latest {C.index[-1]})")
    dv20 = (C * V).rolling(20, min_periods=15).mean()
    elig = (C.shift(1) >= 5) & (dv20.shift(1) >= 20e6)
    ret = C.pct_change(fill_method=None).where(elig)
    last = ret.index[-252:]
    top = ret.loc[last].rank(axis=1, ascending=False, method="first") <= 20
    gain_days, elig_days = top.sum(), elig.loc[last].sum()
    liq = (C.loc[asof] >= 5) & (dv20.loc[asof] >= 20e6)
    hi1y = C.loc[last].max()

    z = pd.read_parquet(config.DATA / "zones" / f"{asof}.parquet")
    z = z[(z.dist_pct >= 0) & (z.dist_pct <= 0.06) & z.band.isin(["AT", "NEAR"])]
    z = z.sort_values(["ticker", "dist_pct"]).drop_duplicates("ticker")
    g = sectors.groups().drop_duplicates("ticker").set_index("ticker")
    grp = pd.read_parquet(config.DATA / "sectors" / f"groups_{asof}.parquet")
    sec = grp[grp.level == "sector"].set_index("group")[["score", "rank"]]
    sc = pd.read_parquet(config.DATA / "scores" / f"{asof[:7]}.parquet")
    sc = sc[sc.session == asof]
    piv = sc.pivot_table(index="ticker", columns="metric", values="value", aggfunc="last")
    lab = sc[sc.metric == "combo_h20"].set_index("ticker")["label"]
    flags = NE.page_flags(asof)
    try:
        import insider_signals as SIG
        ins = SIG.badges(asof)
    except Exception:                                            # noqa: BLE001
        ins = {}
    u = pd.read_parquet(config.UNIVERSE_FILE).drop_duplicates("ticker").set_index("ticker")

    def val(t, m):
        if t in piv.index and m in piv.columns and pd.notna(piv.at[t, m]):
            return round(float(piv.at[t, m]), 1)
        return np.nan

    rows = []
    for r in z.itertuples(index=False):
        t = r.ticker
        if t not in C.columns or not bool(liq.get(t, False)):
            continue
        sector = g.at[t, "sector"] if t in g.index and isinstance(g.at[t, "sector"], str) else "Other"
        fl = flags.get(t, {})
        e = fl.get("earn")
        rows.append({
            "ticker": t, "name": u["name"].get(t, ""), "sector": sector,
            "sector_rank": sec["rank"].get(sector), "sector_score": sec["score"].get(sector),
            "subind": g.at[t, "subind"] if t in g.index else "",
            "gain_days": int(gain_days.get(t, 0)), "elig_days": int(elig_days.get(t, 0)),
            "price": round(float(C.at[asof, t]), 2), "level": round(float(r.level), 2),
            "above_level_pct": round(float(r.dist_pct) * 100, 1), "band": r.band, "tests": int(r.touches),
            "level_failed_pct": round(float(r.dd_break_rate) * 100, 1) if pd.notna(r.dd_break_rate) else np.nan,
            "pct_of_1y_high": round(float(C.at[asof, t] / hi1y[t]) * 100, 1),
            "usd_m_day": round(float(dv20.at[asof, t]) / 1e6, 1),
            "fund": val(t, "fund_score"), "combo_h20": val(t, "combo_h20"), "combo_label": lab.get(t, ""),
            "warning": "; ".join(f"{w[1]} {w[2]}" for w in fl.get("warn") or []),
            "earnings": f"{e[0]} {e[1]}".strip() if e else "", "earn_sessions": e[2] if e else np.nan,
            "insider": ins.get(t, ""), "asof": asof,
        })
    D = pd.DataFrame(rows).sort_values(["sector", "gain_days", "fund"], ascending=[True, False, False])
    L = (D.groupby("sector", group_keys=False).head(10)
          .sort_values(["sector_rank", "gain_days"], ascending=[True, False]))
    return L, D


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("asof", help="session, e.g. 2026-10-30")
    a = ap.parse_args()
    L, D = build(a.asof)
    out = config.DATA / "watchlists" / f"gainers_at_support_{a.asof.replace('-', '')}"
    out.mkdir(parents=True, exist_ok=True)
    L.to_csv(out / "list.csv", index=False)
    D.to_csv(out / "all_at_support.csv", index=False)
    print(f"{a.asof}: {len(D):,} liquid names at/near support; list {len(L)} across "
          f"{L.sector.nunique()} sectors -> {out}")
    print(f"warnings in list: {(L.warning != '').sum()}; earnings within {NE.EARN_SOON} sessions: "
          f"{(L.earn_sessions <= NE.EARN_SOON).sum()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
