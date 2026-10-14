#!/usr/bin/env python
"""Sector and sub-industry groups, and their point-in-time return series.

Shared by sector_study.py, season_study.py and the Sectors page, so a group means the
same thing everywhere.

GROUPS
  sector        the SIC map's sector label (macro.load_sector_map), ~21 labels
  sub-industry  hand-named SIC ranges, energy split the way the user asked for it
                (producers, drilling & services, refiners, gas pipelines, gas
                utilities, electric utilities, coal, oil & gas equipment) -- see
                SUBIND. A ticker outside every named range gets "Other <sector>".

RETURNS are EQUAL-WEIGHTED over members that were ELIGIBLE ON THAT DAY (close >=
MIN_PRICE, 20-day mean dollar volume >= MIN_DOLLAR_VOL), so membership is point in
time and a name that later vanished still counts while it traded. A group needs
>= MIN_MEMBERS eligible names on a date to have a value; below that it is blank, not
zero. Daily returns are clipped at +/-50% so one bad print cannot swing a group.

The wide close/volume matrices are cached in data/_wide_1d.parquet (rebuilt with
--rebuild or when older than the newest bar).
"""
from __future__ import annotations

import argparse
import sys
import time

import numpy as np
import pandas as pd

import bars as B
import calendar_us
import config
import dataset
import macro

FIRST_BAR = "2016-08-01"
MIN_MEMBERS = 8
CLIP = 0.5
WIDE = config.DATA / "_wide_1d.parquet"

# (name, [(sic_lo, sic_hi), ...]) -- first match wins, so narrow ranges come first.
SUBIND: list[tuple[str, list[tuple[int, int]]]] = [
    ("Oil & gas producers", [(1311, 1311)]),
    ("Oil & gas drilling & services", [(1381, 1389)]),
    ("Oil & gas equipment", [(3533, 3533)]),
    ("Refiners", [(2911, 2911)]),
    ("Gas pipelines", [(4922, 4923)]),
    ("Gas utilities", [(4924, 4925), (4932, 4932)]),
    ("Electric utilities", [(4911, 4911), (4931, 4931), (4991, 4991)]),
    ("Water utilities", [(4941, 4941)]),
    ("Coal", [(1220, 1229)]),
    ("Petroleum marketing", [(5171, 5172)]),
    ("Banks", [(6021, 6029), (6035, 6036)]),
    ("Insurance", [(6311, 6411)]),
    ("REITs", [(6798, 6798)]),
    ("Asset managers & brokers", [(6199, 6289)]),
    ("Semiconductors", [(3674, 3674)]),
    ("Software & internet", [(7370, 7379)]),
    ("Computer hardware", [(3570, 3579)]),
    ("Communications equipment", [(3661, 3669)]),
    ("Electronic components", [(3670, 3673), (3675, 3679)]),
    ("Pharma & biotech", [(2833, 2836)]),
    ("Medical devices", [(3841, 3845), (3851, 3851)]),
    ("Healthcare services", [(8000, 8099)]),
    ("Aerospace & defense", [(3720, 3729), (3760, 3769), (3812, 3812)]),
    ("Autos & parts", [(3711, 3716)]),
    ("Homebuilders", [(1520, 1531)]),
    ("Construction & engineering", [(1540, 1799)]),
    ("Chemicals", [(2800, 2829), (2840, 2899)]),
    ("Metals & mining", [(1000, 1099), (1400, 1499), (3310, 3399)]),
    ("Food & beverage", [(2000, 2099)]),
    ("Restaurants", [(5812, 5812)]),
    ("Retail", [(5200, 5811), (5813, 5999)]),
    ("Telecom", [(4810, 4829), (4890, 4899)]),
    ("Media & entertainment", [(2710, 2789), (4830, 4841), (7810, 7829)]),
    ("Transport", [(4000, 4799)]),
    ("Machinery", [(3500, 3569), (3580, 3599)]),
    ("Electrical equipment", [(3600, 3660), (3690, 3699)]),
]
ENERGY = ["Oil & gas producers", "Oil & gas drilling & services", "Oil & gas equipment",
          "Refiners", "Gas pipelines", "Gas utilities", "Electric utilities", "Coal",
          "Petroleum marketing"]


def subindustry(sic, sector: str | None = None) -> str:
    try:
        s = int(sic)
    except (TypeError, ValueError):
        return f"Other {sector}" if sector else "Other"
    for name, ranges in SUBIND:
        if any(lo <= s <= hi for lo, hi in ranges):
            return name
    return f"Other {sector}" if sector else "Other"


def groups() -> pd.DataFrame:
    """ticker -> sector, subind (one row per ticker)."""
    m = macro.load_sector_map().drop_duplicates("ticker")[["ticker", "sic", "sector"]].copy()
    m["sector"] = m["sector"].where(m["sector"].astype(str).str.len() > 0, "Other").fillna("Other")
    m["subind"] = [subindustry(s, sec) for s, sec in zip(m["sic"], m["sector"])]
    return m[["ticker", "sector", "subind"]].reset_index(drop=True)


def build_wide(verbose: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Date x ticker closes and volumes for every stored ticker (float32)."""
    t0 = time.time()
    last = calendar_us.last_closed_session()
    tick = B.load_panel_stats()["ticker"].astype(str).tolist()
    cl, vo = {}, {}
    for i in range(0, len(tick), 250):
        part = tick[i:i + 250]
        d = dataset.panel(part, "1d", start=FIRST_BAR, end=last)
        for t in part:
            b = d.get(t) if isinstance(d, dict) else None
            if b is None or len(b) < 30:
                continue
            b = b.drop_duplicates("date", keep="last")
            idx = b["date"].astype(str).str[:10].values
            cl[t] = pd.Series(b["close"].astype("float32").values, index=idx)
            vo[t] = pd.Series(b["volume"].astype("float32").values, index=idx)
        if verbose:
            print(f"  wide {min(i + 250, len(tick)):>5}/{len(tick)}  {time.time() - t0:5.0f}s", flush=True)
    C = pd.DataFrame(cl).sort_index()
    V = pd.DataFrame(vo).reindex(index=C.index, columns=C.columns)
    out = pd.concat({"close": C, "volume": V}, axis=1)
    out.columns = [f"{a}|{b}" for a, b in out.columns]
    tmp = WIDE.with_suffix(".parquet.tmp")
    out.to_parquet(tmp, compression=config.COMPRESSION)
    tmp.replace(WIDE)
    if verbose:
        print(f"  wrote {WIDE.name}: {C.shape[0]} dates x {C.shape[1]} tickers, {time.time() - t0:.0f}s")
    return C, V


def load_wide(rebuild: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not rebuild and WIDE.exists():
        w = pd.read_parquet(WIDE)
        if str(w.index.max()) >= calendar_us.last_closed_session():
            c = [x for x in w.columns if x.startswith("close|")]
            v = [x for x in w.columns if x.startswith("volume|")]
            C = w[c]; C.columns = [x.split("|", 1)[1] for x in c]
            V = w[v]; V.columns = [x.split("|", 1)[1] for x in v]
            return C, V
    return build_wide()


def eligible(C: pd.DataFrame, V: pd.DataFrame) -> pd.DataFrame:
    dv20 = (C * V).rolling(20, min_periods=20).mean()
    return (C >= config.MIN_PRICE) & (dv20 >= config.MIN_DOLLAR_VOL)


def daily_returns(C: pd.DataFrame) -> pd.DataFrame:
    r = C.pct_change(fill_method=None)
    return r.clip(-CLIP, CLIP)


def group_returns(R: pd.DataFrame, E: pd.DataFrame, col: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Equal-weight daily return per group over names eligible the PRIOR day, and the
    member count. Blank where fewer than MIN_MEMBERS."""
    g = groups().set_index("ticker")[col]
    tick = [t for t in R.columns if t in g.index]
    lab = g.reindex(tick)
    elig = E[tick].shift(1).fillna(False).astype(bool)
    Rm = R[tick].where(elig)
    out, cnt = {}, {}
    for name in sorted(lab.dropna().unique()):
        cols = lab.index[lab == name]
        x = Rm[cols]
        n = x.notna().sum(axis=1)
        out[name] = x.mean(axis=1).where(n >= MIN_MEMBERS)
        cnt[name] = n
    return pd.DataFrame(out), pd.DataFrame(cnt)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rebuild", action="store_true")
    a = ap.parse_args()
    C, V = load_wide(rebuild=a.rebuild)
    g = groups()
    print(g.subind.value_counts().head(40).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
