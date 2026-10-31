#!/usr/bin/env python
"""Do zones found on a SHORT history fail more often than zones found on a long one?

    python zonelen_study.py            ~10 min, writes data/_zonelen_study.json

Asked 2026-10-31, before touching a threshold. `zones.MIN_BARS` is 310 but the
scan admits only names with 400 stored bars, and the bounce screen needs 400.
Lowering either is only worth it if zones drawn from a year of bars hold about
as well as zones drawn from six.

DESIGN, fixed before any result was seen:

  DATES. Every 126th session from 2019-07 to the last session with 100 sessions
  after it. A test window is at most 60 + 40 = 100 sessions, so no two dates'
  windows overlap, and every per-date rate is an independent draw.

  WHO. Point-in-time liquid names at each date: close >= $5 and a 20-session
  median dollar volume >= $20M, computed from the bars up to that date. Age is
  counted in bars since the name's listing in our store, where a run of 10+
  missing sessions starts a new listing (a reused symbol, as FLY and CCXI are).
  Names whose prices fail `zones.is_suspect` are dropped from every arm.

  ARM A, LENGTH (same stocks, same dates). Names aged >= 600 bars, up to 250 a
  date (fixed seed). Zones from the last L bars for L = 150, 200, 250, 310, 400,
  and from the FULL window the scan uses (6 calendar years). Only the length
  differs, so any change in the failure rate is the length's doing.

  ARM B, YOUNG LISTINGS. Names actually aged 150-399 bars at the date, zones
  from everything they have. This is the population a lower minimum would
  admit, and it can differ from a truncated mature stock (lockups, no price
  memory before the listing).

  BASELINE. Four placebo lines per arm-A name-date, at a uniform 0.5-16% below
  price, kept only if no FULL zone lies within 2% of it. A zone that fails no
  less often than a random line is not a zone.

  OUTCOME = the zones study's own break: from the first bar after the date whose
  low comes within 2.5% above the level (within 60 sessions), the lowest close
  of the next 40 bars at or below 10% under the level is a FAILED test
  (`dd_break_rate`'s rule). A gap straight through the level counts, so a
  crash is a failure and never "untested". A best close >= 15% above the level
  in the same 40 bars is a hit (`bounce_hit`'s rule). Levels not reached in 60
  sessions are counted, not scored.

  TEST. Per date, each arm's failure rate minus the FULL rate on the same date;
  mean and t over dates. Pooled counts are printed beside it.
"""
from __future__ import annotations

import json
import sys
import time

import numpy as np
import pandas as pd

import config
import store
import zones as Z

LENGTHS = (150, 200, 250, 310, 400)
STEP = 126
REACH, HOLD = 60, 40
MIN_CLOSE, MIN_DV = 5.0, 20e6
MATURE, PER_DATE = 600, 250
GAP = 10
OUT = config.DATA / "_zonelen_study.json"


def load():
    """The store as wide float32 arrays (sessions x tickers)."""
    frames = []
    for m in store.months("1d"):
        d = pd.read_parquet(store.part_path(m),
                            columns=["ticker", "date", "high", "low", "close", "volume"])
        d["ticker"] = d["ticker"].astype(str)
        frames.append(d)
    df = pd.concat(frames, ignore_index=True)
    dates = np.array(sorted(df["date"].unique()))
    ticks = np.array(sorted(df["ticker"].unique()))
    di = np.searchsorted(dates, df["date"].to_numpy())
    ti = np.searchsorted(ticks, df["ticker"].to_numpy())
    W = {}
    for c in ("high", "low", "close", "volume"):
        a = np.full((len(dates), len(ticks)), np.nan, dtype=np.float32)
        a[di, ti] = df[c].to_numpy(dtype=np.float32)
        W[c] = a
    return dates, ticks, W


def listing_start(valid: np.ndarray, i: int) -> int:
    """First row of the current listing at row i (a 10-session hole restarts it)."""
    idx = np.flatnonzero(valid[:i + 1])
    if not len(idx):
        return i + 1
    br = np.flatnonzero(np.diff(idx) > GAP)
    return int(idx[br[-1] + 1]) if len(br) else int(idx[0])


def frame(dates, W, j, rows):
    return pd.DataFrame({"date": dates[rows], "high": W["high"][rows, j],
                         "low": W["low"][rows, j], "close": W["close"][rows, j],
                         "volume": W["volume"][rows, j]})


def outcome(W, j, i, level):
    """(reached, failed, hit) for the first test of `level` after row i."""
    lo = W["low"][i + 1:i + 1 + REACH, j]
    k = np.flatnonzero(lo <= level * (1 + config.TOUCH_TOL_UP))
    if not len(k):
        return False, None, None
    s = i + 1 + int(k[0])
    c = W["close"][s:s + HOLD, j]
    c = c[np.isfinite(c)]
    if len(c) < HOLD * 0.9:
        return True, None, None              # window incomplete: not scored
    return True, bool(c.min() <= level * 0.90), bool(c.max() >= level * 1.15)


def band(dist):
    return ("AT" if dist <= Z.BAND_AT else "NEAR" if dist <= Z.BAND_NEAR
            else "APPROACHING")


def run(verbose=True):
    t0 = time.time()
    dates, ticks, W = load()
    if verbose:
        print(f"  loaded {len(dates):,} sessions x {len(ticks):,} tickers "
              f"in {time.time() - t0:.0f}s", flush=True)
    valid = np.isfinite(W["close"])
    dv = W["close"] * W["volume"]
    i0 = int(np.searchsorted(dates, "2019-07-01"))
    rows_d = list(range(i0, len(dates) - (REACH + HOLD), STEP))
    rng = np.random.default_rng(20261031)
    rng_y = np.random.default_rng(20261101)    # own stream: arm A is unchanged by it
    saved_min = Z.MIN_BARS
    Z.MIN_BARS = min(LENGTHS) - 10            # the guard is what is under test
    recs = []
    try:
        for i in rows_d:
            D = str(dates[i])
            six = str((pd.Timestamp(D) - pd.DateOffset(years=6)).date())
            lo6 = int(np.searchsorted(dates, six))
            med = np.nanmedian(dv[max(0, i - 19):i + 1], axis=0)
            ok = valid[i] & (W["close"][i] >= MIN_CLOSE) & (med >= MIN_DV)
            mature, young = [], []
            for j in np.flatnonzero(ok):
                st = listing_start(valid[:, j], i)
                age = int(valid[st:i + 1, j].sum())
                if age >= MATURE:
                    mature.append((j, st))
                elif 150 <= age < 400:
                    young.append((j, st, age))
            if len(mature) > PER_DATE:
                pick = rng.choice(len(mature), PER_DATE, replace=False)
                mature = [mature[k] for k in sorted(pick)]

            def zones_of(j, rows):
                b = frame(dates, W, j, rows)
                if Z.is_suspect(b):
                    return None
                return Z.zones_for(str(ticks[j]), b)

            def score(arm, j, z, price, extra=None):
                if z is None or z.empty:
                    recs.append({"date": D, "arm": arm, "t": str(ticks[j]),
                                 "level": np.nan, **(extra or {})})
                    return
                for L in z["level"].astype(float):
                    reached, failed, hit = outcome(W, j, i, L)
                    recs.append({"date": D, "arm": arm, "t": str(ticks[j]),
                                 "level": L, "band": band(price / L - 1),
                                 "reached": reached, "failed": failed,
                                 "hit": hit, **(extra or {})})

            for j, st in mature:
                rows_full = np.flatnonzero(valid[max(st, lo6):i + 1, j]) + max(st, lo6)
                zf = zones_of(j, rows_full)
                if zf is None:
                    continue                    # untrusted prices: out of every arm
                price = float(W["close"][i, j])
                score("FULL", j, zf, price, {"n_bars": len(rows_full)})
                allrows = np.flatnonzero(valid[st:i + 1, j]) + st
                for L in LENGTHS:
                    zl = zones_of(j, allrows[-L:])
                    score(f"L{L}", j, zl, price)
                full_lv = zf["level"].to_numpy(float) if not zf.empty else np.array([])
                for d in rng.uniform(0.005, Z.BAND_APPROACHING, 4):
                    lv = price * (1 - d)
                    if len(full_lv) and np.min(np.abs(np.log(full_lv / lv))) < 0.02:
                        continue
                    reached, failed, hit = outcome(W, j, i, lv)
                    recs.append({"date": D, "arm": "PLACEBO", "t": str(ticks[j]),
                                 "level": lv, "band": band(d / (1 - d)),
                                 "reached": reached, "failed": failed, "hit": hit})
            for j, st, age in young:
                rows_y = np.flatnonzero(valid[st:i + 1, j]) + st
                zy = zones_of(j, rows_y)
                if zy is None:
                    continue
                arm = "YOUNG_310_399" if age >= 310 else (
                    "YOUNG_250_309" if age >= 250 else "YOUNG_150_249")
                price = float(W["close"][i, j])
                score(arm, j, zy, price, {"age": age})
                # Young listings are more volatile, and a fixed 10% break is
                # easier to hit when they are, so their zones are judged against
                # random lines on THE SAME young names, not on mature ones.
                y_lv = zy["level"].to_numpy(float) if not zy.empty else np.array([])
                for d in rng_y.uniform(0.005, Z.BAND_APPROACHING, 4):
                    lv = price * (1 - d)
                    if len(y_lv) and np.min(np.abs(np.log(y_lv / lv))) < 0.02:
                        continue
                    reached, failed, hit = outcome(W, j, i, lv)
                    recs.append({"date": D, "arm": "PLACEBO_YOUNG", "t": str(ticks[j]),
                                 "level": lv, "band": band(d / (1 - d)),
                                 "reached": reached, "failed": failed, "hit": hit,
                                 "age": age})
            if verbose:
                print(f"  {D}: {len(mature)} mature, {len(young)} young, "
                      f"{time.time() - t0:.0f}s", flush=True)
    finally:
        Z.MIN_BARS = saved_min
    return pd.DataFrame(recs)


def summarise(r: pd.DataFrame) -> dict:
    arms = ["FULL", *[f"L{L}" for L in reversed(LENGTHS)], "YOUNG_310_399",
            "YOUNG_250_309", "YOUNG_150_249", "PLACEBO", "PLACEBO_YOUNG"]
    zr = r[r["level"].notna()]
    sc = zr[zr["failed"].notna()]
    out = {"dates": sorted(r["date"].unique()), "arms": {}}
    full_by_date = sc[sc["arm"] == "FULL"].groupby("date")["failed"].mean()
    for a in arms:
        ra, za, sa = r[r["arm"] == a], zr[zr["arm"] == a], sc[sc["arm"] == a]
        nd = ra.groupby(["date", "t"]).ngroups
        withz = za.groupby(["date", "t"]).ngroups
        per = sa.groupby("date")["failed"].mean()
        diff = (per - full_by_date).dropna()
        tt = (float(diff.mean() / (diff.std(ddof=1) / np.sqrt(len(diff))))
              if len(diff) > 2 and diff.std(ddof=1) > 0 else float("nan"))
        rec = {"name_dates": int(nd) if a != "PLACEBO" else None,
               "with_zone": (round(withz / nd, 3) if nd and a != "PLACEBO" else None),
               "levels": int(len(za)),
               "reached_60": round(float(za["reached"].mean()), 3) if len(za) else None,
               "scored": int(len(sa)),
               "fail_rate": round(float(sa["failed"].mean()), 4) if len(sa) else None,
               "hit_rate": round(float(sa["hit"].mean()), 4) if len(sa) else None,
               "vs_full_pp": round(float(diff.mean()) * 100, 2) if len(diff) else None,
               "vs_full_t": round(tt, 2) if np.isfinite(tt) else None,
               "dates_compared": int(len(diff)),
               "by_band": {b: {"n": int(len(g)), "fail": round(float(g["failed"].mean()), 4)}
                           for b, g in sa.groupby("band")}}
        out["arms"][a] = rec
    # how many short-history zones are also FULL zones (within 2%)
    agree = {}
    full = zr[zr["arm"] == "FULL"].groupby(["date", "t"])["level"].apply(np.array)
    for L in LENGTHS:
        z = zr[zr["arm"] == f"L{L}"]
        hits = []
        for (d, t), g in z.groupby(["date", "t"]):
            f = full.get((d, t))
            for lv in g["level"]:
                hits.append(bool(f is not None and len(f)
                                 and np.min(np.abs(np.log(f / lv))) < 0.02))
        agree[f"L{L}"] = round(float(np.mean(hits)), 3) if hits else None
    out["share_also_full_zone"] = agree
    # young zones against random lines on the same young names, per date
    yz = sc[sc["arm"].str.startswith("YOUNG_")]
    yp = sc[sc["arm"] == "PLACEBO_YOUNG"]
    d = (yz.groupby("date")["failed"].mean() - yp.groupby("date")["failed"].mean()).dropna()
    t = (float(d.mean() / (d.std(ddof=1) / np.sqrt(len(d))))
         if len(d) > 2 and d.std(ddof=1) > 0 else float("nan"))
    out["young_vs_young_placebo"] = {
        "zones_scored": int(len(yz)), "zone_fail": round(float(yz["failed"].mean()), 4),
        "placebo_scored": int(len(yp)), "placebo_fail": round(float(yp["failed"].mean()), 4),
        "diff_pp": round(float(d.mean()) * 100, 2), "t": round(t, 2) if np.isfinite(t) else None,
        "dates": int(len(d)),
        "by_band": {b: {"zone": round(float(yz[yz.band == b]["failed"].mean()), 4),
                        "placebo": round(float(yp[yp.band == b]["failed"].mean()), 4),
                        "n_zone": int((yz.band == b).sum()), "n_placebo": int((yp.band == b).sum())}
                    for b in ("AT", "NEAR", "APPROACHING")}}
    return out


def main() -> int:
    config.safe_console()
    t0 = time.time()
    r = run()
    s = summarise(r)
    s["runtime_s"] = round(time.time() - t0)
    OUT.write_text(json.dumps(s, indent=2), encoding="utf-8")
    r.to_parquet(OUT.with_suffix(".parquet"), index=False)
    print(f"\n  {len(s['dates'])} dates {s['dates'][0]}..{s['dates'][-1]}, "
          f"{s['runtime_s']}s\n")
    print(f"  {'arm':15} {'name-dates':>10} {'w/ zone':>8} {'levels':>7} "
          f"{'reached':>8} {'scored':>7} {'fail':>7} {'hit':>7} "
          f"{'vs FULL':>8} {'t':>6}")
    for a, v in s["arms"].items():
        print(f"  {a:15} {v['name_dates'] or '':>10} {v['with_zone'] or '':>8} "
              f"{v['levels']:>7} {v['reached_60'] or 0:>8.3f} {v['scored']:>7} "
              f"{(v['fail_rate'] or 0):>7.3f} {(v['hit_rate'] or 0):>7.3f} "
              f"{'' if v['vs_full_pp'] is None else format(v['vs_full_pp'], '+.2f'):>8} "
              f"{'' if v['vs_full_t'] is None else format(v['vs_full_t'], '+.2f'):>6}")
    print("\n  fail rate by band:")
    for a, v in s["arms"].items():
        print(f"  {a:15} " + "  ".join(f"{b} {x['fail']:.3f} (n {x['n']})"
                                       for b, x in sorted(v["by_band"].items())))
    print(f"\n  share of short-history zones that are also FULL zones: "
          f"{s['share_also_full_zone']}")
    print(f"\n  written {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
