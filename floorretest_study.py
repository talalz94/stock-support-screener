#!/usr/bin/env python
"""Does a "floor retest after a big run" predict anything?

Asked 2026-10-14, from SOFI: a 4.3x run ($7.57 -> $32.73, Oct 2024 - Nov 2025), a 53%
pullback (70% of the run given back), then seven months of a floor -- monthly lows of
$14.94, $14.93, $14.92, $14.88 with rebounds to $18-20 -- and now back at that floor
on smaller ranges and lighter volume. Neither existing screen names it: the bounce
screen wants a PARABOLIC run and a fall to the run's own base; the good-level rule
wants a stock within 20% of its 1-year high.

THE PATTERN, fixed before any result was seen (round numbers; SOFI is the example the
definition must catch, not a target it was tuned to):
  a. big run      within the last 2 years, a high >= 2x the lowest low of the 2 years
                  before it, and that high is OLDER than the 9-month floor window
  b. pullback     close 35-70% below that high, and >= 30% above where the run started
  c. floor        floor = lowest low of the last 189 sessions (9 months); >= 3 confirmed
                  swing lows (lowest of +/-5 bars) within 3% above it, >= 10 sessions
                  apart, first-to-last >= 60 sessions
                  ONE CHANGE, made before any forward return was computed: the window was
                  declared as 6 months (126 sessions) and that missed SOFI's own floor --
                  its March and April tests fall before the window, leaving 2 of 3. SOFI's
                  floor spans March-October, so the window became 9 months.
  d. at the floor close <= floor * 1.06
  e. quieting     20-day ATR below 60-day ATR AND 20-day volume below 120-day volume
                  -- measured WITH and WITHOUT, to see whether it adds anything
  f. liquid       20-day mean dollar volume >= $10M

THE TESTS, declared before running (11; ~0.55 false positives expected at 5%):
  pattern (a-d,f) vs the same-day LIQUID pool        h = 20, 40 (primary), 60   (3)
  pattern + quieting (a-f)                           h = 20, 40, 60             (3)
  control: pullback, no floor (a,b,f, not c)         h = 40                     (1)
  control: floor + at it, no prior run (c,d,f, a<1.5x) h = 40                   (1)
  floor break (low > 10% under the floor within 40 sessions): pattern vs both
  controls, and pattern+quieting vs pattern                                     (3)
Sector is reported descriptively only.

METHOD as lowclimb_study.py (whose evaluate() is reused): dates thinned so windows
never overlap, excess per date vs the pool, t across dates (the luck p overstates
significance when picks move together -- the t is the test), tails, halves. Pool =
eligible names with >= $10M/day, because the pattern requires that.

KNOWN BIAS: survivorship flatters beaten-down stocks, which this pattern selects.

Run: python floorretest_study.py [--rebuild]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time

import numpy as np
import pandas as pd

import bars as B
import calendar_us
import config
import dataset
import macro
import lowclimb_study as LC

RUN_WIN, FLOOR_WIN, CONF = 500, 189, 5
RUN_X, RUN_X_CONTROL = 2.0, 1.5
OFF_HI_MIN, OFF_HI_MAX = 0.35, 0.70           # close / high between 0.30 and 0.65
ABOVE_START = 1.30
BAND, MIN_TESTS, MIN_GAP, MIN_SPAN = 0.03, 3, 10, 60
AT_FLOOR = 1.06
LIQUID = 10e6
BREAK = 0.90
HORIZONS = (20, 40, 60)
PRIMARY = 40
GRID = 10
CHUNK = 250
FIRST_BAR = "2016-08-01"
CHECK = ("SOFI", "2026-10-13")
PANEL = config.DATA / "_floor_panel.parquet"
SUMMARY = config.DATA / "_floor_study_summary.json"


def log(m: str) -> None:
    print(f"  {m}", flush=True)


def tests_on_floor(lo: np.ndarray, swing: np.ndarray, j: int, floor: float):
    """Confirmed swing lows within BAND above `floor` in the floor window, greedily
    spaced >= MIN_GAP apart. Returns (count, span_in_sessions)."""
    a, b = max(0, j - FLOOR_WIN + 1), j - CONF
    if b <= a:
        return 0, 0
    idx = np.nonzero(swing[a:b + 1] & (lo[a:b + 1] <= floor * (1 + BAND)))[0] + a
    kept = []
    for i in idx:
        if not kept or i - kept[-1] >= MIN_GAP:
            kept.append(i)
    return len(kept), (kept[-1] - kept[0]) if len(kept) >= 2 else 0


def features_at(j, lo, hi, cl, vo, swing, atr20, atr60, v20, v120, dv20):
    """The pattern's inputs at bar j, or None when history is too short."""
    if j < 250:
        return None
    a = max(0, j - RUN_WIN + 1)
    p = a + int(np.nanargmax(hi[a:j + 1]))
    peak = float(hi[p])
    a0 = max(0, p - RUN_WIN + 1)
    start = float(np.nanmin(lo[a0:p + 1]))
    f0 = max(0, j - FLOOR_WIN + 1)
    floor = float(np.nanmin(lo[f0:j + 1]))
    n_tests, span = tests_on_floor(lo, swing, j, floor)
    c = float(cl[j])
    return dict(
        peak_old=bool(p <= j - FLOOR_WIN), run_x=peak / start if start > 0 else np.nan,
        off_hi=1 - c / peak if peak > 0 else np.nan, above_start=c / start if start > 0 else np.nan,
        floor=floor, n_tests=n_tests, span=span, at_floor=c <= floor * AT_FLOOR,
        above_floor=c / floor - 1.0 if floor > 0 else np.nan,
        quiet=bool(atr20[j] < atr60[j] and v20[j] < v120[j]), dv20=float(dv20[j]),
    )


# NEAR MISS, fixed 2026-10-14 before it was measured: liquid, the high older than the
# floor window, and exactly 3 of the 4 core conditions -- the 4th missed only within
# this slack. The full pattern is on 0.04% of liquid rows (usually none on a day), so
# the page also lists setups one step from it, labelled with what they lack.
NEAR = {"run_x": 1.7, "off_lo": 0.30, "off_hi": 0.75, "above_start": 1.2,
        "tests": 2, "span": 40, "at_floor": 1.10}


def classify(f: dict) -> dict:
    run = f["peak_old"] and f["run_x"] >= RUN_X
    pull = OFF_HI_MIN <= f["off_hi"] <= OFF_HI_MAX and f["above_start"] >= ABOVE_START
    floor = f["n_tests"] >= MIN_TESTS and f["span"] >= MIN_SPAN
    liq = f["dv20"] >= LIQUID
    pat = run and pull and floor and f["at_floor"] and liq
    close_to_floor = f.get("above_floor", 0.0) <= NEAR["at_floor"] - 1 + 1e-12
    soft = {"run": f["peak_old"] and f["run_x"] >= NEAR["run_x"],
            "pullback": NEAR["off_lo"] <= f["off_hi"] <= NEAR["off_hi"] and f["above_start"] >= NEAR["above_start"],
            "floor": f["n_tests"] >= NEAR["tests"] and f["span"] >= NEAR["span"],
            "at floor": close_to_floor}
    hard = {"run": run, "pullback": pull, "floor": floor, "at floor": f["at_floor"]}
    failing = [k for k, v in hard.items() if not v]
    near = (liq and f["peak_old"] and not pat and len(failing) == 1 and soft[failing[0]])
    return dict(pattern=pat, pattern_quiet=pat and f["quiet"], near=near,
                missing=failing[0] if near else "",
                ctl_pull_nofloor=run and pull and liq and not floor,
                ctl_floor_norun=floor and f["at_floor"] and liq and f["run_x"] < RUN_X_CONTROL,
                liquid=liq)


def _series(lo, hi, cl, vo):
    L = pd.Series(lo)
    swing = (L == L.rolling(2 * CONF + 1, center=True, min_periods=2 * CONF + 1).min()).to_numpy()
    prev = np.roll(cl, 1); prev[0] = np.nan
    tr = np.nanmax(np.vstack([hi - lo, np.abs(hi - prev), np.abs(lo - prev)]), axis=0)
    T = pd.Series(tr)
    V = pd.Series(vo)
    return (swing, T.rolling(20).mean().to_numpy(), T.rolling(60).mean().to_numpy(),
            V.rolling(20).mean().to_numpy(), V.rolling(120).mean().to_numpy(),
            pd.Series(cl * vo).rolling(20, min_periods=20).mean().to_numpy())


def build() -> pd.DataFrame:
    t0 = time.time()
    last = calendar_us.last_closed_session()
    ses = [s for s in calendar_us.all_sessions() if FIRST_BAR <= s <= last]
    grid = set(ses[250::GRID])
    want = sorted(grid | {CHECK[1]})
    tick = B.load_panel_stats()["ticker"].astype(str).tolist()
    sec = macro.load_sector_map().drop_duplicates("ticker").set_index("ticker")["sector"]
    rows, n_susp = [], 0
    for i in range(0, len(tick), CHUNK):
        part = tick[i:i + CHUNK]
        d = dataset.panel(part, "1d", start=FIRST_BAR, end=last)
        for t in part:
            b = d.get(t) if isinstance(d, dict) else None
            if b is None or len(b) < 260:
                continue
            b = b.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
            dt = b["date"].astype(str).str[:10].to_numpy()
            lo, hi, cl, vo = (b[c].to_numpy(float) for c in ("low", "high", "close", "volume"))
            swing, atr20, atr60, v20, v120, dv20 = _series(lo, hi, cl, vo)
            with np.errstate(divide="ignore", invalid="ignore"):
                big = np.nan_to_num(np.abs(np.diff(np.log(np.where(cl > 0, cl, np.nan)))),
                                    nan=0.0) > config.SUSPECT_SPLIT_RET
            n = len(cl)
            for D, j in zip(want, pd.Index(dt).get_indexer(want)):
                if j < 250 or not (cl[j] >= config.MIN_PRICE and dv20[j] >= config.MIN_DOLLAR_VOL):
                    continue
                if LC._suspect_at(j, cl, hi, vo, bool(big[:j].any())):
                    n_susp += 1
                    continue
                f = features_at(j, lo, hi, cl, vo, swing, atr20, atr60, v20, v120, dv20)
                if f is None:
                    continue
                r = {"date": D, "ticker": t, "close": float(cl[j]), "grid": D in grid, **f, **classify(f)}
                for h in HORIZONS:
                    r[f"fwd{h}"] = float(cl[j + h] / cl[j] - 1.0) if j + h < n else np.nan
                k = min(n, j + 41)
                r["broke40"] = (bool(np.nanmin(lo[j + 1:k]) < f["floor"] * BREAK)
                                if j + 40 < n else np.nan)
                rows.append(r)
        log(f"{min(i + CHUNK, len(tick)):>5}/{len(tick)}  {len(rows):>8,} rows  {time.time() - t0:5.0f}s")
    p = pd.DataFrame(rows)
    p["sector"] = p["ticker"].map(sec).fillna("?")
    log(f"panel {len(p):,} rows, {p.ticker.nunique():,} tickers, {n_susp:,} suspect rows excluded, "
        f"{(time.time() - t0) / 60:.1f} min")
    return p


def check(p: pd.DataFrame) -> list[str]:
    bad = []
    r = p[(p.ticker == CHECK[0]) & (p.date == CHECK[1])]
    if r.empty:
        bad.append(f"{CHECK[0]} missing on {CHECK[1]}")
    else:
        r = r.iloc[0]
        log(f"check {CHECK[0]} {CHECK[1]}: run {r.run_x:.2f}x (peak older than floor window: "
            f"{r.peak_old}), {r.off_hi:.0%} below the high, {r.above_start:.2f}x the run start, "
            f"floor {r.floor:.2f} with {int(r.n_tests)} tests over {int(r.span)} sessions, "
            f"at floor {r.at_floor}, quiet {r.quiet} -> pattern {r.pattern}, +quiet {r.pattern_quiet}")
        if not r.pattern:
            bad.append(f"the definition does not catch {CHECK[0]} on {CHECK[1]}")
    if p.duplicated(["date", "ticker"]).any():
        bad.append("duplicate (date, ticker)")
    return bad


def analyse(p: pd.DataFrame) -> None:
    g = p[p.grid & p.liquid]
    per = g.groupby("date")
    print(f"\npanel: {len(g):,} liquid grid rows, {g.date.nunique()} dates {g.date.min()}..{g.date.max()}, "
          f"{g.ticker.nunique():,} tickers. Pattern on {g.pattern.mean() * 100:.2f}% of rows "
          f"(median {per.pattern.sum().median():.0f} names/date, max {per.pattern.sum().max():.0f}); "
          f"+quiet {per.pattern_quiet.sum().median():.0f}/date")
    print("\nREAD p(edge)/p(worse) WITH CARE: they assume independent picks. The t across dates is the test.")
    LC.N_TESTS = 0

    def q_for(h):
        return g[g.date.isin(LC.thin(g.date.unique(), h)) & g[f"fwd{h}"].notna()]

    summary = {"measured_on": dt.date.today().isoformat(), "dates": int(g.date.nunique()),
               "first": str(g.date.min()), "last": str(g.date.max()), "results": {}, "breaks": {}}

    def keep(key, r):
        if r:
            summary["results"][key] = {k: float(v) for k, v in r.items() if k != "label"}

    print("\nA. THE PATTERN vs THE SAME-DAY LIQUID POOL")
    print(LC.HEAD)
    for h in HORIZONS:
        q = q_for(h)
        keep(f"pattern_h{h}", LC.evaluate(q, h, q.pattern, "floor retest after a run",
                                          halves=(h == PRIMARY), tails=(h == PRIMARY)))
    print("\nB. WITH QUIETING (smaller ranges, lighter volume)")
    print(LC.HEAD)
    for h in HORIZONS:
        q = q_for(h)
        keep(f"quiet_h{h}", LC.evaluate(q, h, q.pattern_quiet, "floor retest + quieting",
                                        halves=(h == PRIMARY)))
    print("\nC. CONTROLS at the primary horizon")
    print(LC.HEAD)
    q = q_for(PRIMARY)
    keep("ctl_pull_nofloor", LC.evaluate(q, PRIMARY, q.ctl_pull_nofloor, "control: pullback, no floor"))
    keep("ctl_floor_norun", LC.evaluate(q, PRIMARY, q.ctl_floor_norun, "control: floor, no prior run"))

    print("\nD. HOW OFTEN THE FLOOR BROKE (low > 10% under it within 40 sessions)")
    q = g[g.date.isin(LC.thin(g.date.unique(), 40)) & g.broke40.notna()]
    for lab, m in (("pattern", q.pattern), ("pattern + quieting", q.pattern_quiet),
                   ("control: pullback, no floor", q.ctl_pull_nofloor),
                   ("control: floor, no prior run", q.ctl_floor_norun)):
        s = q[m]
        print(f"  {lab:32} {s.broke40.mean() * 100:5.1f}% of {len(s):,} cases on {s.date.nunique()} dates")
        summary["breaks"][lab] = {"rate": float(s.broke40.mean()), "cases": int(len(s))}
    LC.N_TESTS += 3

    print("\nE. BY SECTOR (descriptive only, primary horizon)")
    q = q_for(PRIMARY)
    pool = q.groupby("date")[f"fwd{PRIMARY}"].transform("mean")
    s = q[q.pattern].assign(ex=lambda x: x[f"fwd{PRIMARY}"] - pool[x.index])
    t = s.groupby("sector").agg(cases=("ex", "size"), excess_pp=("ex", lambda v: v.mean() * 100),
                                up=(f"fwd{PRIMARY}", lambda v: (v > 0).mean() * 100))
    print(t[t.cases >= 15].sort_values("cases", ascending=False).round(1).to_string())
    # F. EVENT STUDY. Added 2026-10-14 after A-C came back "too few dates": the full
    # pattern is on 0.04% of liquid rows (median 0 names a date, max 10), so a per-
    # date average with >= 3 picks leaves 4-10 dates. The standard tool for a rare
    # pattern: each EPISODE counted once (a ticker's first grid date in a run of
    # consecutive pattern dates), its forward return minus that day's liquid-pool
    # mean, and the t computed across CALENDAR QUARTERS so events bunched in one
    # selloff are not counted as independent evidence. The definition is unchanged.
    print("\nF. EVENT STUDY -- each episode once, excess vs the same-day liquid pool, t across quarters")
    gg = p[p.grid & p.liquid].sort_values(["ticker", "date"]).copy()
    gdates = sorted(gg.date.unique())
    gpos = {d: i for i, d in enumerate(gdates)}
    gg["gi"] = gg.date.map(gpos)
    summary["events"] = {}
    if "near" not in gg.columns:
        # panels built before the near-miss tier existed: derive it from stored features
        if "above_floor" not in gg.columns:
            gg["above_floor"] = gg["close"] / gg["floor"] - 1.0
        cl = gg.apply(lambda r: classify(r.to_dict()), axis=1, result_type="expand")
        gg["near"] = cl["near"]
    for lab, col in (("pattern", "pattern"), ("pattern + quieting", "pattern_quiet"),
                     ("near miss (unmeasured until now)", "near"),
                     ("control: pullback, no floor", "ctl_pull_nofloor"),
                     ("control: floor, no prior run", "ctl_floor_norun")):
        on = gg[gg[col]]
        prev = on.groupby("ticker").gi.shift(1)
        ev = on[prev.isna() | (on.gi - prev > 1)]
        res = {"events": int(len(ev)), "tickers": int(ev.ticker.nunique())}
        line = f"  {lab:32} {len(ev):>5} events, {ev.ticker.nunique():>4} tickers"
        for h in HORIZONS:
            f = f"fwd{h}"
            e = ev[ev[f].notna()]
            if len(e) < 10:
                line += f" | h{h}: too few"
                continue
            pool = gg[gg[f].notna()].groupby("date")[f]
            pm, ph = pool.mean(), pool.apply(lambda v: (v > 0).mean())
            ex = e[f] - e.date.map(pm)
            qx = ex.groupby(e.date.str[:4] + "Q" + ((e.date.str[5:7].astype(int) - 1) // 3 + 1).astype(str)).mean()
            t = LC.tstat(qx.values) if len(qx) >= 5 else float("nan")
            res[f"h{h}"] = {"n": int(len(e)), "hit": float((e[f] > 0).mean()),
                            "pool_hit": float(e.date.map(ph).mean()), "ex": float(ex.mean()),
                            "ex_median": float(ex.median()), "t_quarters": float(t), "quarters": int(len(qx))}
            line += (f" | h{h}: n {len(e)}, up {(e[f] > 0).mean() * 100:.0f}% vs {e.date.map(ph).mean() * 100:.0f}%, "
                     f"excess {ex.mean() * 100:+.2f}pp (median {ex.median() * 100:+.2f}), t {t:+.2f} over {len(qx)} qtrs")
        b = ev[ev.broke40.notna()]
        res["broke40"] = float(b.broke40.mean()) if len(b) else None
        line += f" | floor broke {b.broke40.mean() * 100:.0f}%" if len(b) else ""
        print(line)
        summary["events"][lab] = res
        LC.N_TESTS += 3 if col in ("pattern", "pattern_quiet") else 1
    print(f"\n{LC.N_TESTS} tests run; at 5% about {LC.N_TESTS * 0.05:.1f} would pass by chance alone.")
    summary["tests"] = LC.N_TESTS
    SUMMARY.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    log(f"wrote {SUMMARY.name}")


def current(asof: str | None = None) -> pd.DataFrame:
    """Names matching the pattern on `asof` (for the page). Same code path as the study."""
    asof = asof or calendar_us.last_closed_session()
    tick = B.tradeable_universe(asof)
    start = [s for s in calendar_us.all_sessions() if s <= asof][-(RUN_WIN + 260)]
    out = []
    for i in range(0, len(tick), CHUNK):
        part = tick[i:i + CHUNK]
        d = dataset.panel(part, "1d", start=start, end=asof)
        for t in part:
            b = d.get(t) if isinstance(d, dict) else None
            if b is None or len(b) < 260:
                continue
            b = b.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
            if str(b["date"].iloc[-1])[:10] != asof:
                continue
            lo, hi, cl, vo = (b[c].to_numpy(float) for c in ("low", "high", "close", "volume"))
            swing, atr20, atr60, v20, v120, dv20 = _series(lo, hi, cl, vo)
            j = len(cl) - 1
            with np.errstate(divide="ignore", invalid="ignore"):
                big = np.nan_to_num(np.abs(np.diff(np.log(np.where(cl > 0, cl, np.nan)))), nan=0.0) \
                    > config.SUSPECT_SPLIT_RET
            if LC._suspect_at(j, cl, hi, vo, bool(big.any())):
                continue
            f = features_at(j, lo, hi, cl, vo, swing, atr20, atr60, v20, v120, dv20)
            if f is None:
                continue
            c = classify(f)
            if c["pattern"] or c["near"]:
                a = max(0, j - RUN_WIN + 1)
                p = a + int(np.nanargmax(hi[a:j + 1]))
                a0 = max(0, p - RUN_WIN + 1)
                s0 = a0 + int(np.nanargmin(lo[a0:p + 1]))
                out.append({"ticker": t, "close": float(cl[j]), **f, **c,
                            "peak": float(hi[p]), "peak_date": str(b["date"].iloc[p])[:10],
                            "start": float(lo[s0]), "start_date": str(b["date"].iloc[s0])[:10]})
    return pd.DataFrame(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--current", action="store_true", help="list today's matches and exit")
    a = ap.parse_args()
    if a.current:
        c = current()
        print(c.sort_values("off_hi").to_string(index=False) if len(c) else "no matches")
        return 0
    if PANEL.exists() and not a.rebuild:
        p = pd.read_parquet(PANEL)
        log(f"{PANEL.name}: {len(p):,} rows (pass --rebuild to recompute)")
    else:
        p = build()
        tmp = PANEL.with_suffix(".parquet.tmp")
        p.to_parquet(tmp, compression=config.COMPRESSION, index=False)
        tmp.replace(PANEL)
        log(f"wrote {PANEL.name}")
    bad = check(p)
    if bad:
        for x in bad:
            log(f"! {x}")
        log("CHECK FAILED -- the definition must catch the example it was written from")
        return 1
    analyse(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
