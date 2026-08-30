#!/usr/bin/env python
"""Read the panel and answer three questions, with the bar stated first.

    |t| >= 2.0  AND  the sign is stable across horizons

MULTIPLE COMPARISONS ARE COUNTED AND PRINTED. Three sector treatments x four
horizons x ~17 metrics is roughly two hundred tests; at t>=2 about ten cross by
chance. A table of "PASSES" without that number attached is a way to hand
someone ten false discoveries, so the count and the expected false-positive
rate go at the top of the results.

EVERY IC IS PRINTED BESIDE ITS QUANTILE SPREAD. The Quality strategy is why:
it passes on IC (t=+2.99) while its TOP quintile underperforms its bottom
(-1.54pp, t=-3.37). A positive rank correlation that is not tradeable long-only
looks identical to a good one until you print both.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

import numpy as np
import pandas as pd

import calendar_us
import config
import couple_panel as CP

OUT: list[str] = []
N_TESTS = 0


def say(s: str = "") -> None:
    print(s, flush=True)
    OUT.append(s)


def head(t: str) -> None:
    say()
    say("=" * 78)
    say(t)
    say("=" * 78)


def _thin(dates: list[str], h: int) -> set[str]:
    """Dates at least `h` sessions apart, so forward windows do not overlap.

    The first zone study sampled 21 days apart against a 40-bar horizon and
    every t-stat was inflated by autocorrelation until it was redone. Each
    horizon gets its own subset and reports its own date count.
    """
    ses = calendar_us.all_sessions()
    pos = {s: i for i, s in enumerate(ses)}
    keep, last = [], -(10 ** 9)
    for d in sorted(dates):
        if d in pos and pos[d] - last >= h:
            keep.append(d)
            last = pos[d]
    return set(keep)


def ic_t(p: pd.DataFrame, col: str, h: int, neutral: bool = False):
    """Per-date Spearman IC, t across dates, plus the top-bottom quintile spread."""
    global N_TESTS
    fwd = f"fwd{h}"
    if col not in p.columns or fwd not in p.columns:
        return None
    use = p[p["date"].isin(_thin(p["date"].unique().tolist(), h))]
    rs, sp = [], []
    for _, g in use.groupby("date"):
        g = g[[col, fwd, "sector"]].dropna(subset=[col, fwd])
        if len(g) < 50:
            continue
        x = g[col]
        if neutral:
            # rank WITHIN sector, so a quantum name competes only with quantum
            # names and the sector wave cancels
            x = g.groupby("sector")[col].rank(pct=True)
            if x.notna().sum() < 50:
                continue
        rs.append(x.corr(g[fwd], method="spearman"))
        try:
            q = pd.qcut(x, 5, labels=False, duplicates="drop")
            top = g[fwd][q == q.max()].mean()
            bot = g[fwd][q == 0].mean()
            sp.append(top - bot)
        except Exception:                                        # noqa: BLE001
            pass
    a = np.array([v for v in rs if np.isfinite(v)])
    if len(a) < 5:
        return None
    N_TESTS += 1
    t = a.mean() / (a.std(ddof=1) / np.sqrt(len(a)))
    s = np.array([v for v in sp if np.isfinite(v)])
    st = (s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))) if len(s) >= 5 else np.nan
    return dict(ic=a.mean(), t=t, n=len(a), spread=s.mean() if len(s) else np.nan,
                spread_t=st)


ALL_METRICS = (CP.FUND + CP.HYPE + CP.SENTI + CP.DIP
               + ["dist", "brk", "touches", "bmed_atr", "pct_hi", "sec_mom120"])


def phase2(p):
    head("PHASE 2. THREE TREATMENTS, FOUR HORIZONS")
    say("  bar: |t| >= 2.0 AND a stable sign across horizons.")
    say("  'spread' is top-quintile minus bottom-quintile forward return: a")
    say("  positive IC with a negative spread is not tradeable long-only.")
    for label, neutral in (("RAW cross-sectional", False),
                           ("SECTOR-NEUTRAL (ranked within sector)", True)):
        say()
        say(f"  --- {label} ---")
        say(f"  {'metric':16} {'h':>4} {'IC':>9} {'t':>7} {'spread':>9} "
            f"{'sp_t':>6} {'dates':>6}  verdict")
        for m in ALL_METRICS:
            for h in CP.HORIZONS:
                r = ic_t(p, m, h, neutral)
                if r is None:
                    continue
                v = "PASSES" if abs(r["t"]) >= 2.0 else ""
                say(f"  {m:16} {h:>4} {r['ic']:+9.4f} {r['t']:+7.2f} "
                    f"{r['spread']*100:+8.2f}pp {r['spread_t']:+6.2f} "
                    f"{r['n']:>6}  {v}")


def phase2c(p):
    head("PHASE 2c. IS SECTOR STRENGTH ITSELF THE SIGNAL?")
    say("  does the sector ETF's own trailing 120-bar return predict its")
    say("  constituents' forward returns?")
    say(f"  {'h':>4} {'IC':>9} {'t':>7} {'dates':>6}")
    for h in CP.HORIZONS:
        r = ic_t(p, "sec_mom120", h, False)
        if r:
            say(f"  {h:>4} {r['ic']:+9.4f} {r['t']:+7.2f} {r['n']:>6}"
                f"  {'PASSES' if abs(r['t']) >= 2 else ''}")
    say()
    say("  and the sector's OWN forward return, as a ceiling on what sector")
    say("  timing could deliver:")
    for h in CP.HORIZONS:
        c = f"sec_fwd{h}"
        f = f"fwd{h}"
        if c in p and f in p:
            g = p[[c, f]].dropna()
            if len(g) > 500:
                say(f"    h={h:<4} corr(stock fwd, sector fwd) = "
                    f"{g[f].corr(g[c]):+.3f}   n={len(g):,}")


def phase3(p):
    head("PHASE 3. WHAT SEPARATES A BOUNCE FROM A BREAK AT A ZONE?")
    say("  Among names AT or NEAR a level, split the forward return into")
    say("  deciles and compare attributes of the top against the bottom.")
    h = 40
    fwd = f"fwd{h}"
    z = p[p["band"].isin(["AT", "NEAR"]) & p[fwd].notna()].copy()
    if len(z) < 500:
        say("  not enough zone arrivals")
        return None
    z["_d"] = z.groupby("date")[fwd].transform(
        lambda s: pd.qcut(s, 10, labels=False, duplicates="drop"))
    top = z[z["_d"] == z["_d"].max()]
    bot = z[z["_d"] == 0]
    say(f"  {len(z):,} zone arrivals; top decile {len(top):,}, "
        f"bottom {len(bot):,}")
    say()
    say(f"  {'attribute':16} {'bounced':>10} {'did not':>10} {'diff':>9} "
        f"{'t':>7}")
    rank = []
    for m in ALL_METRICS:
        if m not in z.columns:
            continue
        a, b = top[m].dropna(), bot[m].dropna()
        if len(a) < 50 or len(b) < 50:
            continue
        se = np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
        if not np.isfinite(se) or se == 0:
            continue
        t = (a.mean() - b.mean()) / se
        rank.append((abs(t), m, a.mean(), b.mean(), t))
    rank.sort(reverse=True)
    for _, m, am, bm, t in rank:
        say(f"  {m:16} {am:>10.3f} {bm:>10.3f} {am - bm:>+9.3f} {t:>+7.2f}"
            f"  {'<<<' if abs(t) >= 3 else ''}")
    say()
    if rank:
        say(f"  STRONGEST SEPARATOR: {rank[0][1]} (t={rank[0][4]:+.2f})")
    return rank


def phase4(p, rank):
    head("PHASE 4. CASE STUDIES -- bounced vs did not, at a zone")
    h = 40
    fwd = f"fwd{h}"
    z = p[p["band"].isin(["AT", "NEAR"]) & p[fwd].notna()].copy()
    if z.empty:
        say("  none")
        return
    z["_d"] = z.groupby("date")[fwd].transform(
        lambda s: pd.qcut(s, 10, labels=False, duplicates="drop"))
    z["outcome"] = np.where(z["_d"] == z["_d"].max(), "BOUNCED",
                            np.where(z["_d"] == 0, "BROKE", ""))
    cases = z[z["outcome"] != ""].copy()
    keep = (["date", "ticker", "outcome", fwd, "close", "band", "dist", "brk",
             "touches", "bmed_atr", "pct_hi", "sector"]
            + [m for m in CP.FUND + CP.HYPE + CP.SENTI + CP.DIP
               if m in cases.columns] + ["sec_mom120"])
    cases = cases[[c for c in keep if c in cases.columns]]
    out = config.DATA / f"_zone_cases_{datetime.now():%Y%m%d}.csv"
    cases.to_csv(out, index=False, encoding="utf-8")
    say(f"  {len(cases):,} cases -> {out.name}")
    say(f"  BOUNCED {int((cases.outcome == 'BOUNCED').sum()):,}  "
        f"BROKE {int((cases.outcome == 'BROKE').sum()):,}")
    say()
    # sort_values, not nlargest: `date` is an object column and nlargest
    # rejects it outright, which crashed the display after the CSV was already
    # written -- the deliverable survived, the summary did not.
    def _recent(df, n=12):
        return df.sort_values("date", ascending=False).head(n)

    say("  most recent 12 BOUNCED:")
    for _, r in _recent(cases[cases.outcome == "BOUNCED"]).iterrows():
        say(f"    {r['date']}  {str(r['ticker']):6} fwd{h} {r[fwd]*100:+6.1f}%  "
            f"{str(r['band']):4} brk={r['brk']:.2f} touches={r['touches']:.0f}  "
            f"fund={r.get('fund_score', float('nan')):.0f} "
            f"hype={r.get('hype_score', float('nan')):.0f}")
    say()
    say("  most recent 12 BROKE:")
    for _, r in _recent(cases[cases.outcome == "BROKE"]).iterrows():
        say(f"    {r['date']}  {str(r['ticker']):6} fwd{h} {r[fwd]*100:+6.1f}%  "
            f"{str(r['band']):4} brk={r['brk']:.2f} touches={r['touches']:.0f}  "
            f"fund={r.get('fund_score', float('nan')):.0f} "
            f"hype={r.get('hype_score', float('nan')):.0f}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.parse_args()
    if not CP.PANEL.exists():
        print("no panel; run couple_panel.py --rebuild first")
        return 1
    p = pd.read_parquet(CP.PANEL)
    say(f"COUPLING STUDY   {datetime.now():%Y-%m-%d %H:%M}")
    say(f"  panel {len(p):,} obs, {p.ticker.nunique():,} tickers, "
        f"{p.date.nunique()} dates, {p.sector.nunique()} sectors")
    bad = CP.integrity(p)
    say(f"  integrity: {'OK' if not bad else '; '.join(bad)}")

    phase2(p)
    phase2c(p)
    rank = phase3(p)
    phase4(p, rank)

    head("HOW MANY OF THESE ARE LUCK?")
    say(f"  {N_TESTS} tests run at |t| >= 2.0.")
    say(f"  expected false positives by chance alone: ~{N_TESTS * 0.05:.0f}")
    say("  Treat any single result at t ~ 2.0 as unproven. What matters is a")
    say("  metric passing at SEVERAL horizons with the same sign, which chance")
    say("  does not produce as easily as one lucky cell.")

    fp = config.DATA / f"_couple_study_{datetime.now():%Y%m%d_%H%M}.txt"
    fp.write_text("\n".join(OUT), encoding="utf-8")
    print(f"\nsummary -> {fp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
