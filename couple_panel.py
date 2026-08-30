#!/usr/bin/env python
"""One point-in-time panel: zones, fundamentals, hype, sentiment, sector.

Built once and read by every later analysis, because the expensive part is the
per-date zone computation and there is no reason to pay it three times.

WHAT THE FIRST ATTEMPT GOT WRONG, and why each guard below exists:

  THE JOIN. It sampled arbitrary trading days. Fundamental scores exist only on
  the ~205 sessions the pipeline actually scored, so the exact-date overlap was
  ZERO rows and every metric printed "skipped". This panel is built ON the
  fundamental sessions, so the join is exact and both sides are point-in-time
  rather than an as-of fudge that quietly shifts what was knowable when.

  ONE HORIZON. It measured 40 bars only. Every fundamental metric's IC RISES
  with horizon -- roic +0.026 at h=20 against +0.037 at h=60 -- so a single
  short window understates a thesis that is explicitly long-term. Forward
  returns are computed at 20/40/120/250 and left NaN where the window does not
  exist, rather than dropping the date; each horizon then uses whatever it can
  and reports its own date count.

  NO SECTOR, NO SENTIMENT. A market-wide cross-sectional rank partly measures
  "was your sector hot". `factor_lab`'s by="sector" only emits a reporting
  slice; it does not neutralise. Sector, sector ETF and the hype/sentiment
  modules are carried here so the analysis can neutralise or test them.

  SURVIVORSHIP. Eligibility is recomputed per date FROM THE BARS. Using today's
  panel stats would silently drop everything delisted since, biasing in the
  flattering direction.

  BAD PRICES. `zones.is_suspect` excludes unadjusted splits, which otherwise
  present as a 99% decline and land in the strongest signal on the page.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd

import bars as B
import calendar_us
import config
import dataset
import factor_lab as FL
import zones as Z

HORIZONS = (20, 40, 120, 250)
GAP = 42                       # >= h=40 window, the densest horizon reported
CHUNK = 250

FUND = ["fund_score", "roic", "f_score", "pe", "fcf_yield", "gpoa",
        "interest_cover", "rev_growth", "ev_ebitda"]
HYPE = ["hype_score", "attention_score", "premium_score", "stretch_score"]
SENTI = ["sent_rank", "news_z", "news_count_rank"]
DIP = ["dip_score"]
MODULES = {"fundamental": FUND, "hype": HYPE, "sentiment": SENTI, "dip": DIP}

PANEL = config.DATA / "_couple_panel.parquet"


def log(m: str) -> None:
    print(f"  {m}", flush=True)


def panel_dates() -> list[str]:
    """Fundamental sessions, spaced so the h=40 windows do not overlap.

    Spacing is set by GAP, not by the longest horizon: dropping every date that
    lacks a full 250-bar future would throw away the last year entirely. Each
    horizon thins this list further at ANALYSIS time and reports its own count.
    """
    m = FL.load_metric("fundamental", "roic")
    fs = sorted(m["date"].astype(str).unique())
    ses = calendar_us.all_sessions()
    pos = {s: i for i, s in enumerate(ses)}
    out, last = [], -(10 ** 9)
    for s in fs:
        if s not in pos or pos[s] + min(HORIZONS) >= len(ses):
            continue
        if pos[s] - last >= GAP:
            out.append(s)
            last = pos[s]
    return out


def _sector_frames(dates: list[str]):
    """(ticker -> sector, sector_etf) and the ETF's own forward returns.

    The ETF's return is the sector's return. Averaging constituents would make
    the sector control partly a function of the very names being ranked.
    """
    import macro
    smap = macro.load_sector_map()
    tick_sec = smap.set_index("ticker")["sector"] if len(smap) else pd.Series(dtype=object)
    tick_etf = smap.set_index("ticker")["sector_etf"] if len(smap) else pd.Series(dtype=object)

    etf_fwd = {}
    try:
        e = macro.read_etfs(end=dates[-1])
        for t, g in e.groupby("ticker"):
            g = g.sort_values("date").reset_index(drop=True)
            dt = g["date"].astype(str).to_numpy()
            c = g["close"].to_numpy(float)
            idx = {s: j for j, s in enumerate(dt)}
            for D in dates:
                j = idx.get(D)
                if j is None:
                    continue
                row = {}
                for h in HORIZONS:
                    row[f"sec_fwd{h}"] = (float(c[j + h] / c[j] - 1.0)
                                          if j + h < len(c) else np.nan)
                # trailing 120-bar strength: "is this sector hot right now"
                row["sec_mom120"] = (float(c[j] / c[j - 120] - 1.0)
                                     if j >= 120 else np.nan)
                etf_fwd[(D, str(t))] = row
    except Exception as exc:                                     # noqa: BLE001
        log(f"! sector ETF returns unavailable: {repr(exc)[:70]}")
    return tick_sec, tick_etf, etf_fwd


def build(verbose: bool = True) -> pd.DataFrame:
    t0 = time.time()
    dates = panel_dates()
    log(f"{len(dates)} sessions, {dates[0]} .. {dates[-1]}, gap >= {GAP} bars")

    metrics = {}
    for mod, names in MODULES.items():
        for m in names:
            try:
                d = FL.load_metric(mod, m)
                d["date"] = d["date"].astype(str)
                metrics[m] = d.set_index(["date", "ticker"])["value"]
                if verbose:
                    log(f"{mod}.{m:16} {len(d):>8,} rows")
            except Exception as exc:                             # noqa: BLE001
                log(f"{mod}.{m:16} FAILED {repr(exc)[:50]}")

    tick_sec, tick_etf, etf_fwd = _sector_frames(dates)

    ps = B.load_panel_stats()
    cand = ps[ps["n_bars"] >= 400]["ticker"].astype(str).tolist()
    log(f"{len(cand):,} candidate tickers")

    dset = set(dates)
    rows, suspect, t1 = [], 0, time.time()
    for i in range(0, len(cand), CHUNK):
        part = cand[i:i + CHUNK]
        try:
            d = dataset.panel(part, "1d", start="2010-01-01", end=dates[-1])
        except Exception:                                        # noqa: BLE001
            continue
        for t in part:
            b = d.get(t) if isinstance(d, dict) else None
            if b is None or len(b) < 500:
                continue
            b = b.sort_values("date").reset_index(drop=True)
            dt = b["date"].astype(str).to_numpy()
            c = b["close"].to_numpy(float)
            v = (b["volume"].to_numpy(float) if "volume" in b
                 else np.zeros(len(c)))
            hit = {s: j for j, s in enumerate(dt) if s in dset}
            if not hit:
                continue
            for D, j in hit.items():
                if j < 400:
                    continue
                # ELIGIBILITY AS OF D, from the bars themselves
                dv = float(np.nanmean(c[j - 19:j + 1] * v[j - 19:j + 1]))
                if c[j] < config.MIN_PRICE or dv < config.MIN_DOLLAR_VOL:
                    continue
                sl = b.iloc[:j + 1]
                if Z.is_suspect(sl):
                    suspect += 1
                    continue
                zz = Z.zones_for(t, sl, D)
                row = {"date": D, "ticker": t, "close": float(c[j]),
                       "band": "NONE", "dist": np.nan, "brk": np.nan,
                       "touches": np.nan, "bmed_atr": np.nan, "pct_hi": np.nan}
                if not zz.empty:
                    best = zz.sort_values("dist_pct").iloc[0]
                    row.update({"band": str(best["band"]),
                                "dist": float(best["dist_pct"]),
                                "brk": float(best["dd_break_rate"]),
                                "touches": float(best["touches"]),
                                "bmed_atr": float(best["bounce_med_atr"]),
                                "pct_hi": float(best["pct_hi"])})
                for h in HORIZONS:
                    row[f"fwd{h}"] = (float(c[j + h] / c[j] - 1.0)
                                      if j + h < len(c) else np.nan)
                rows.append(row)
        if verbose:
            log(f"{min(i + CHUNK, len(cand)):>5}/{len(cand)}  "
                f"{len(rows):>7,} obs  {time.time() - t1:5.0f}s")

    p = pd.DataFrame(rows)
    if p.empty:
        raise RuntimeError("panel is empty")

    # ---- joins. EXACT on (date, ticker); the first attempt joined 0 rows.
    mi = pd.MultiIndex.from_arrays([p["date"], p["ticker"]])
    for name, s in metrics.items():
        p[name] = mi.map(s)
    p["sector"] = p["ticker"].map(tick_sec).fillna("?")
    p["sector_etf"] = p["ticker"].map(tick_etf)
    for k in [f"sec_fwd{h}" for h in HORIZONS] + ["sec_mom120"]:
        p[k] = [etf_fwd.get((d, e), {}).get(k, np.nan)
                for d, e in zip(p["date"], p["sector_etf"])]

    p.attrs["suspect_excluded"] = suspect
    log(f"\n  panel {len(p):,} obs, {p.ticker.nunique():,} tickers, "
        f"{p.date.nunique()} dates, {suspect:,} suspect-price rows excluded")
    log(f"  built in {(time.time() - t0) / 60:.1f} min")
    return p


def integrity(p: pd.DataFrame) -> list[str]:
    """Assertions the plan named. Returns the list of failures."""
    bad = []
    if "pct_hi" in p:
        n = int((p["pct_hi"] < Z.DATA_SUSPECT_PCT_HI).sum())
        if n:
            bad.append(f"{n} row(s) below the price-range floor survived")
    # the join must actually join
    jr = float(p["fund_score"].notna().mean()) if "fund_score" in p else 0.0
    if jr < 0.50:
        bad.append(f"fundamental join rate {jr:.1%} -- the join is broken")
    per = p.groupby("date")["fund_score"].apply(lambda s: s.notna().mean())
    weak = per[per < 0.30]
    if len(weak):
        bad.append(f"{len(weak)} date(s) join under 30%: "
                   f"{list(weak.index[:3])}")
    # spacing
    ses = calendar_us.all_sessions()
    pos = {s: i for i, s in enumerate(ses)}
    ds = sorted(p["date"].unique())
    gaps = [pos[b] - pos[a] for a, b in zip(ds, ds[1:]) if a in pos and b in pos]
    if gaps and min(gaps) < GAP:
        bad.append(f"date spacing {min(gaps)} < GAP {GAP}")
    # no forward value may exist without its window
    for h in HORIZONS:
        col = f"fwd{h}"
        if col in p and p[col].notna().sum() == 0:
            bad.append(f"{col} is entirely null")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rebuild", action="store_true")
    a = ap.parse_args()
    config.dirs()
    if PANEL.exists() and not a.rebuild:
        log(f"{PANEL.name} exists; pass --rebuild to redo")
        return 0
    p = build()
    bad = integrity(p)
    log("")
    if bad:
        log("INTEGRITY FAILURES:")
        for x in bad:
            log(f"  ! {x}")
    else:
        log("integrity: OK")
    tmp = PANEL.with_suffix(".parquet.tmp")
    p.to_parquet(tmp, compression=config.COMPRESSION, index=False)
    tmp.replace(PANEL)
    log(f"wrote {PANEL.name}  ({PANEL.stat().st_size / 1048576:.0f} MB)")
    for h in HORIZONS:
        log(f"  fwd{h:<4} non-null {int(p[f'fwd{h}'].notna().sum()):>8,}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
