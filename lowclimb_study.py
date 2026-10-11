#!/usr/bin/env python
"""Does "lower low, now climbing" predict anything?

Asked 2026-10-11 for tech names "at a lower low but climbing"; the list was shown
the same day (data/_tech_levels_20261009.csv) with the warning that the pattern was
UNMEASURED. This measures it.

THE PATTERN, fixed before any result was seen (round numbers, no tuning):
  recent low  = lowest daily LOW of the last 20 sessions
  prior low   = lowest LOW of the 100 sessions before that (21..120 back)
  lower low   : recent low < prior low * 0.99            (a real undercut)
  climbing    : the recent low is >= 3 sessions old (being the minimum, it has
                held since), close >= recent low * 1.03, close > close 5 sessions ago
  still near  : close <= recent low * 1.15

THE TESTS, declared before running (18 in all; ~0.9 false positives expected at 5%):
  primary    pattern vs the same-day pool of eligible stocks, 20 bars forward
  horizons   10 and 40 bars as well                                     (3 tests)
  tech       core-tech picks vs the core-tech pool, same three horizons  (3)
  controls   "lower low, any" and "lower low, NOT climbing", 20 and 40   (4)
             -- does waiting for the climb add anything over the low itself?
  strength   by quartile of % of 1-year high, picks vs THEIR OWN quartile's pool,
             40 bars (4) -- lower-low names are mostly beaten down, and beaten-down
             names were already measured to lag; this separates the two
  support    on the 49 coupling-panel dates (zones computed there), picks vs their
             own band's pool, 40 bars: AT/NEAR, APPROACHING, no support, and no
             support with <= 64% of the 1-year high (the measured avoid profile) (4)

METHOD, as rule_accuracy.py: dates thinned so windows never overlap (spacing >=
horizon), excess = mean pick return - mean pool return per date, t across dates, a
"luck p" from 1,000 random same-size draws out of each date's own pool, hit rates,
tails, and the first half against the second. THE t ACROSS DATES IS THE TEST: the luck
p assumes picks are independent draws, and these picks move together, so with ~100 a
day it reads 0.000 for effects whose t is only -1.8.

ELIGIBILITY AS OF EACH DATE, from the bars: close >= MIN_PRICE, 20-day mean dollar
volume >= MIN_DOLLAR_VOL, at least 121 bars, not suspect (the zones.is_suspect rule:
pattern.suspect_split, then the 5%-of-high price floor). Bars start 2016-08-01.

KNOWN BIAS: survivorship. Delisted names are under-represented in the store, and a
name that delists inside a forward window has no forward return. Both flatter the
weakest stocks -- the ones this pattern selects -- so a measured edge here is an
UPPER bound.

Run: python lowclimb_study.py            (builds the panel once, then analyses)
     python lowclimb_study.py --rebuild  (recompute the panel)
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
import pattern as PT
import zones as Z

REC, PRI = 20, 100
UNDERCUT, OFF_LOW, MAX_OFF, MIN_AGE, MOM = 0.99, 1.03, 1.15, 3, 5
HORIZONS = (10, 20, 40)
PRIMARY = 20
GRID = 10                      # a candidate date every 10 sessions; thinned per horizon
CHUNK = 250
FIRST_BAR = "2016-08-01"
MIN_PICKS = 3
AVOID_PCT_HI = 0.64            # the measured bottom-quartile line (NEXT_SESSION 08-29)
CHECK_DATE = "2026-10-09"      # the date the list was shown; flags must reproduce it
PANEL = config.DATA / "_lowclimb_panel.parquet"
COUPLE = config.DATA / "_couple_panel.parquet"
CHECK_CSV = config.DATA / "_tech_levels_20261009.csv"

rng = np.random.default_rng(11)


def log(m: str) -> None:
    print(f"  {m}", flush=True)


def core_tech(sic) -> bool:
    """Software/internet, computers, comms equipment, chips/components, chip test.
    The SIC map's wider "Technology" label also holds elevators and drug makers."""
    if sic is None or pd.isna(sic):
        return False
    s = int(sic)
    return (3570 <= s <= 3579) or (3661 <= s <= 3679) or s == 3825 or (7370 <= s <= 7379)


def _sessions() -> list[str]:
    last = calendar_us.last_closed_session()
    return [s for s in calendar_us.all_sessions() if FIRST_BAR <= s <= last]


def _flags(lo, hi, cl, vo):
    """The pattern for every bar of one ticker at once.

    Equivalent to the per-date script that produced the 10-09 list: the low being
    >= 3 sessions old is "the first occurrence of the 20-bar minimum lies in bars
    j-19..j-3", i.e. min(j-19..j-3) <= min(j-2..j).
    """
    L = pd.Series(lo)
    rec = L.rolling(REC, min_periods=REC).min().to_numpy()
    pri = L.shift(REC).rolling(PRI, min_periods=PRI).min().to_numpy()
    early = L.shift(MIN_AGE).rolling(REC - MIN_AGE, min_periods=REC - MIN_AGE).min().to_numpy()
    late = L.rolling(MIN_AGE, min_periods=MIN_AGE).min().to_numpy()
    c5 = pd.Series(cl).shift(MOM).to_numpy()
    with np.errstate(invalid="ignore"):
        ll = rec < pri * UNDERCUT
        climb = (early <= late) & (cl >= rec * OFF_LOW) & (cl > c5)
        near = cl <= rec * MAX_OFF
    hi250 = pd.Series(hi).rolling(250, min_periods=1).max().to_numpy()
    dv20 = pd.Series(cl * vo).rolling(20, min_periods=20).mean().to_numpy()
    return ll, climb, near, hi250, dv20, rec


def _suspect_at(j, cl, hi, vo, has_big) -> bool:
    """zones.is_suspect on bars[:j+1], on arrays (slicing a DataFrame per date is slow)."""
    try:
        if has_big and PT.suspect_split(cl[:j + 1], vo[:j + 1], config):
            return True
        if j + 1 >= 250:
            top = float(np.nanmax(hi[j - 249:j + 1]))
            if top > 0 and float(cl[j]) / top < Z.DATA_SUSPECT_PCT_HI:
                return True
    except Exception:                                            # noqa: BLE001
        return False
    return False


def build() -> pd.DataFrame:
    t0 = time.time()
    ses = _sessions()
    grid = set(ses[120::GRID])
    cdates = set(pd.read_parquet(COUPLE, columns=["date"])["date"].astype(str)) if COUPLE.exists() else set()
    want = sorted(grid | cdates | {CHECK_DATE})
    log(f"{len(grid)} grid dates every {GRID} sessions + {len(cdates)} coupling dates + the check date")

    tick = B.load_panel_stats()["ticker"].astype(str).tolist()
    sic = macro.load_sector_map().drop_duplicates("ticker").set_index("ticker")["sic"]
    rows, n_susp = [], 0
    for i in range(0, len(tick), CHUNK):
        part = tick[i:i + CHUNK]
        d = dataset.panel(part, "1d", start=FIRST_BAR, end=ses[-1])
        for t in part:
            b = d.get(t) if isinstance(d, dict) else None
            if b is None or len(b) < REC + PRI + 1:
                continue
            b = b.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
            dt = b["date"].astype(str).str[:10].to_numpy()
            lo, hi, cl, vo = (b[c].to_numpy(float) for c in ("low", "high", "close", "volume"))
            ll, climb, near, hi250, dv20, rec = _flags(lo, hi, cl, vo)
            with np.errstate(divide="ignore", invalid="ignore"):
                big = np.nan_to_num(np.abs(np.diff(np.log(np.where(cl > 0, cl, np.nan)))),
                                    nan=0.0) > config.SUSPECT_SPLIT_RET
            pos = pd.Index(dt).get_indexer(want)
            n = len(cl)
            for D, j in zip(want, pos):
                if j < REC + PRI:
                    continue
                if not (cl[j] >= config.MIN_PRICE and dv20[j] >= config.MIN_DOLLAR_VOL):
                    continue
                if _suspect_at(j, cl, hi, vo, bool(big[:j].any())):
                    n_susp += 1
                    continue
                r = {"date": D, "ticker": t, "close": float(cl[j]),
                     "pct_hi": float(cl[j] / hi250[j]) if hi250[j] > 0 else np.nan,
                     "ll": bool(ll[j]), "climb": bool(climb[j]), "near": bool(near[j]),
                     "off_low": float(cl[j] / rec[j] - 1.0), "dv20": float(dv20[j]),
                     "grid": D in grid}
                r["pattern"] = r["ll"] and r["climb"] and r["near"]
                for h in HORIZONS:
                    r[f"fwd{h}"] = float(cl[j + h] / cl[j] - 1.0) if j + h < n else np.nan
                rows.append(r)
        log(f"{min(i + CHUNK, len(tick)):>5}/{len(tick)}  {len(rows):>8,} rows  {time.time() - t0:5.0f}s")
    p = pd.DataFrame(rows)
    p["sic"] = p["ticker"].map(sic)
    p["tech"] = p["sic"].map(core_tech).astype(bool)
    log(f"panel {len(p):,} rows, {p.ticker.nunique():,} tickers, {n_susp:,} suspect rows excluded, "
        f"{(time.time() - t0) / 60:.1f} min")
    return p


# ------------------------------------------------------------------ checks
def check(p: pd.DataFrame) -> list[str]:
    bad = []
    c = p[p.date == CHECK_DATE]
    if CHECK_CSV.exists() and len(c):
        shown = pd.read_csv(CHECK_CSV)
        shown_ll = set(shown.loc[shown.group.isin(["both", "lower_low_climbing"]), "ticker"])
        mine = c.set_index("ticker")
        # every name the list showed as lower-low-climbing must carry the flag here ...
        miss = sorted(t for t in shown_ll if t in mine.index and not mine.loc[t, "pattern"])
        # ... and no tech-labelled name the list could have shown may carry it here
        # without being on it (the list drew from the tradeable universe)
        smap = macro.load_sector_map().drop_duplicates("ticker").set_index("ticker")["sector"]
        could = set(B.tradeable_universe(CHECK_DATE))
        techlab = {t for t in mine.index if smap.get(t) in ("Technology", "Semiconductors") and t in could}
        extra = sorted(t for t in techlab if mine.loc[t, "pattern"] and t not in shown_ll)
        absent = sorted(t for t in shown_ll if t not in mine.index)
        log(f"check {CHECK_DATE}: list showed {len(shown_ll)} lower-low names; "
            f"{len(shown_ll) - len(miss) - len(absent)} reproduced, {len(miss)} missed, "
            f"{len(absent)} not eligible here (eligibility rule differs at the edges), "
            f"{len(extra)} extra")
        if miss:
            bad.append(f"flags disagree with the list for {miss}")
        if extra:
            bad.append(f"flags extra vs the list: {extra}")
    for h in HORIZONS:
        f = p[f"fwd{h}"]
        if (f < -1.0000001).any():
            bad.append(f"fwd{h} below -100%")
    if p.duplicated(["date", "ticker"]).any():
        bad.append("duplicate (date, ticker)")
    if (p.loc[p.pattern, "off_low"] < OFF_LOW - 1 - 1e-9).any():
        bad.append("a pattern row is less than 3% off its low")
    return bad


# ------------------------------------------------------------------ analysis
SES = calendar_us.all_sessions()
POS = {s: i for i, s in enumerate(SES)}
N_TESTS = 0


def thin(dates, h):
    keep, last = [], -10 ** 9
    for d in sorted(dates):
        if d in POS and POS[d] - last >= h:
            keep.append(d)
            last = POS[d]
    return keep


def tstat(a):
    a = np.asarray([x for x in a if np.isfinite(x)])
    return (a.mean() / (a.std(ddof=1) / np.sqrt(len(a)))) if len(a) >= 5 else np.nan


HEAD = (f"  {'test':38} {'h':>3} {'dates':>5} {'picks':>6} {'/date':>5} | {'pick up':>7} {'pool up':>7} | "
        f"{'pick':>7} {'pool':>7} {'excess':>8} {'t':>6} | {'p(edge)':>7} {'p(worse)':>8}")


def evaluate(q: pd.DataFrame, h: int, pick: pd.Series, label: str, pool_by=None,
             halves: bool = False, tails: bool = False) -> dict | None:
    """q: rows with fwd{h} not null on the analysis dates. pick: bool Series on q.index.
    pool_by: optional column; each pick is compared with the pool rows sharing its value."""
    global N_TESTS
    N_TESTS += 1
    f = f"fwd{h}"
    ex, ph, bh, pm, bm, ks, pools, allp, dates = [], [], [], [], [], [], [], [], []
    groups = q.groupby(["date", pool_by]) if pool_by else q.groupby("date")
    per_date: dict[str, list] = {}
    for key, g in groups:
        pk = g[pick.loc[g.index]]
        if len(pk) < 1:
            continue
        per_date.setdefault(key[0] if pool_by else key, []).append((pk[f].to_numpy(), g[f].to_numpy()))
    for D, parts in sorted(per_date.items()):
        pv = np.concatenate([a for a, _ in parts])
        if len(pv) < MIN_PICKS:
            continue
        # with pool_by, the benchmark is each pick's own stratum, weighted by its picks
        base = sum(len(a) * b.mean() for a, b in parts) / len(pv)
        ex.append(pv.mean() - base)
        pm.append(pv.mean()); bm.append(base)
        ph.append((pv > 0).mean())
        bh.append(sum(len(a) * (b > 0).mean() for a, b in parts) / len(pv))
        ks.append([len(a) for a, _ in parts]); pools.append([b for _, b in parts])
        allp.append(pv); dates.append(D)
    if len(ex) < 5:
        print(f"  {label:38} {h:>3} {len(ex):>5}  too few dates with >= {MIN_PICKS} picks")
        return None
    ex = np.array(ex)
    R = 1000
    sims = np.zeros(R)
    for kk, PP in zip(ks, pools):
        tot = sum(kk)
        s = np.zeros(R)
        for k, P in zip(kk, PP):
            k = min(k, len(P))
            idx = rng.random((R, len(P))).argpartition(k - 1, axis=1)[:, :k]
            s += P[idx].sum(axis=1) - k * P.mean()
        sims += s / tot
    sims /= len(ks)
    p_edge = float((sims >= ex.mean()).mean())
    p_worse = float((sims <= ex.mean()).mean())
    n_p = int(sum(sum(k) for k in ks))
    print(f"  {label:38} {h:>3} {len(ex):>5} {n_p:>6} {n_p / len(ex):>5.1f} | {np.mean(ph) * 100:>6.1f}% "
          f"{np.mean(bh) * 100:>6.1f}% | {np.mean(pm) * 100:>+6.2f}% {np.mean(bm) * 100:>+6.2f}% "
          f"{ex.mean() * 100:>+7.2f}pp {tstat(ex):>+6.2f} | {p_edge:>7.3f} {p_worse:>8.3f}")
    out = dict(label=label, h=h, dates=len(ex), picks=n_p, ex=ex.mean(), t=tstat(ex), p_edge=p_edge,
               p_worse=p_worse, hit=np.mean(ph), pool_hit=np.mean(bh))
    if tails:
        a = np.concatenate(allp)
        pool_all = np.concatenate([np.concatenate(P) for P in pools])
        print(f"  {'':38}     tails: lost >20% picks {np.mean(a <= -0.2) * 100:.1f}% vs pool "
              f"{np.mean(pool_all <= -0.2) * 100:.1f}%;  gained >20% picks {np.mean(a >= 0.2) * 100:.1f}% "
              f"vs pool {np.mean(pool_all >= 0.2) * 100:.1f}%;  median pick {np.median(a) * 100:+.1f}% "
              f"vs pool {np.median(pool_all) * 100:+.1f}%")
    if halves:
        half = len(dates) // 2
        for lab, sl in (("first half", slice(0, half)), ("second half", slice(half, None))):
            e = ex[sl]
            print(f"  {'':38}     {lab:11} {dates[sl][0]}..{dates[sl][-1]}: excess {e.mean() * 100:+.2f}pp "
                  f"t={tstat(e):+.2f} over {len(e)} dates")
    return out


def analyse(p: pd.DataFrame) -> None:
    g = p[p.grid]
    print(f"\npanel: {len(g):,} grid rows, {g.date.nunique()} dates {g.date.min()}..{g.date.max()}, "
          f"{g.ticker.nunique():,} tickers; pattern on {g.pattern.mean() * 100:.2f}% of rows "
          f"({g.groupby('date').pattern.sum().median():.0f} names on a median date); "
          f"tech pool {g.groupby('date').tech.sum().median():.0f} names on a median date")
    try:
        import universe
        u = universe.load()
        dead = set(u.loc[u["status"].astype(str) != "active", "ticker"].astype(str))
        print(f"survivorship: {g.ticker.isin(dead).mean() * 100:.1f}% of rows are names not active today; "
              f"{g.loc[g.pattern, 'ticker'].isin(dead).mean() * 100:.1f}% of PATTERN rows")
    except Exception as exc:                                     # noqa: BLE001
        print("survivorship check unavailable:", repr(exc)[:70])

    print("\nREAD p(edge) / p(worse) WITH CARE: they treat the picks as random draws from the day's"
          "\npool, but this pattern's picks move together (beaten-down names, often the same sectors),"
          "\nso with ~100 picks a day they overstate significance. The t across dates is the test.")
    print("\nA. THE PATTERN vs THE SAME-DAY POOL (all eligible stocks)")
    print(HEAD)
    for h in HORIZONS:
        q = g[g.date.isin(thin(g.date.unique(), h)) & g[f"fwd{h}"].notna()]
        evaluate(q, h, q.pattern, "lower low, now climbing", halves=(h == PRIMARY), tails=(h == 40))

    print("\nB. TECH ONLY (core-tech picks vs the core-tech pool)")
    print(HEAD)
    for h in HORIZONS:
        q = g[g.tech & g.date.isin(thin(g.date.unique(), h)) & g[f"fwd{h}"].notna()]
        evaluate(q, h, q.pattern, "tech: lower low, now climbing", halves=(h == PRIMARY))

    print("\nC. CONTROLS -- does waiting for the climb add anything over the low itself?")
    print(HEAD)
    for h in (20, 40):
        q = g[g.date.isin(thin(g.date.unique(), h)) & g[f"fwd{h}"].notna()]
        evaluate(q, h, q.ll, "lower low, any")
        evaluate(q, h, q.ll & ~q.climb, "lower low, NOT climbing")

    print("\nD. BY STRENGTH -- quartile of % of 1-year high, picks vs their own quartile's pool, 40 bars")
    print(HEAD)
    h = 40
    q = g[g.date.isin(thin(g.date.unique(), h)) & g[f"fwd{h}"].notna()].copy()
    q["hq"] = q.groupby("date")["pct_hi"].transform(lambda s: pd.qcut(s.rank(method="first"), 4, labels=False) + 1)
    share = q.loc[q.pattern, "hq"].value_counts(normalize=True).sort_index()
    print(f"  where the picks sit: " + ", ".join(f"Q{int(k)} {v * 100:.0f}%" for k, v in share.items())
          + "  (Q1 = furthest below the 1-year high)")
    for k in (1, 2, 3, 4):
        s = q[q.hq == k]
        evaluate(s, h, s.pattern, f"quartile Q{k} of % of high")

    print("\nE. BY SUPPORT -- the 49 coupling-panel dates (zones computed there), 40 bars,"
          " picks vs their own band's pool")
    if not COUPLE.exists():
        print("  coupling panel absent; skipped")
        return
    c = pd.read_parquet(COUPLE, columns=["date", "ticker", "band", "pct_hi", "fwd40"])
    c["date"] = c["date"].astype(str)
    # % of high from THIS panel's bars: the coupling panel records pct_hi only when a
    # level exists, so its no-support rows are blank there (that emptied the avoid-
    # profile stratum on the first run). Where both exist they must agree.
    m = c.merge(p[["date", "ticker", "pattern", "pct_hi"]].rename(columns={"pct_hi": "pct_hi_bars"}),
                on=["date", "ticker"], how="inner")
    both = m.dropna(subset=["pct_hi", "pct_hi_bars"])
    print(f"  % of high, coupling panel vs these bars: max abs difference "
          f"{(both.pct_hi - both.pct_hi_bars).abs().max():.4f} over {len(both):,} rows")
    m = m[m.fwd40.notna()]
    m = m[m.date.isin(thin(m.date.unique(), 40))]
    m["sband"] = np.where(m.band.isin(["AT", "NEAR"]), "AT/NEAR",
                          np.where(m.band == "APPROACHING", "APPROACHING", "no support"))
    print(f"  joined {len(m):,} rows on {m.date.nunique()} dates; pattern rows "
          f"{int(m.pattern.sum()):,}: " + ", ".join(f"{k} {v}" for k, v in m.loc[m.pattern, 'sband'].value_counts().items()))
    print(HEAD)
    for lab in ("AT/NEAR", "APPROACHING", "no support"):
        s = m[m.sband == lab]
        evaluate(s, 40, s.pattern, f"support: {lab}")
    s = m[(m.sband == "no support") & (m.pct_hi_bars <= AVOID_PCT_HI)]
    evaluate(s, 40, s.pattern, "no support & <= 64% of high")
    print(f"\n{N_TESTS} tests run; at a 5% threshold about {N_TESTS * 0.05:.1f} would pass by chance alone.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rebuild", action="store_true")
    a = ap.parse_args()
    if PANEL.exists() and not a.rebuild:
        p = pd.read_parquet(PANEL)
        log(f"{PANEL.name}: {len(p):,} rows (pass --rebuild to recompute)")
    else:
        p = build()
        tmp = PANEL.with_suffix(".parquet.tmp")
        p.to_parquet(tmp, compression=config.COMPRESSION, index=False)
        tmp.replace(PANEL)
        log(f"wrote {PANEL.name} ({PANEL.stat().st_size / 1048576:.0f} MB)")
    bad = check(p)
    if bad:
        for x in bad:
            log(f"! {x}")
        log("CHECKS FAILED -- not analysing a panel that disagrees with itself")
        return 1
    log("checks: OK")
    analyse(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
