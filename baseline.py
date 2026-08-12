"""
The control group: same universe, same dates, same exit rule, random entries.

Without this, "the screener returns +2.16% per trade" is uninterpretable -- if a
coin flip in the same eligible pool returns +2.5% over the same holding period,
the screen is destroying value while looking profitable.

Method, matched to backtest.py as closely as possible:
  - same per-date eligibility (rolling, no look-ahead)
  - same test dates
  - entry at the NEXT session's open
  - same exit rules
  - ATR(14) at entry for stop sizing (the flags use ATR at the bounce low, which
    has no analogue for a random entry -- the one unavoidable asymmetry, noted)

Sampling is repeated over several seeds so the comparison is not one lucky draw.

    python baseline.py --start 2024-02 --every 3 --seeds 5
"""

from __future__ import annotations

import argparse
import sys
import time

import numpy as np
import pandas as pd

import config

config.safe_console()

import backtest                                                 # noqa: E402
import calendar_us                                              # noqa: E402
import dataset                                                  # noqa: E402
import pattern as pt                                            # noqa: E402


def run(dates: list[str], elig: pd.DataFrame, per_date: dict[str, int],
        seed: int, verbose: bool = True) -> pd.DataFrame:
    """Random eligible entries, matched in count to the flags on each date."""
    rng = np.random.default_rng(seed)
    by_date = {d: sorted(g["ticker"].astype(str))
               for d, g in elig[elig["eligible"]].groupby("date", observed=True)
               if d in set(dates)}

    picks: dict[str, list[str]] = {}
    for d in dates:
        pool = by_date.get(d, [])
        k = min(per_date.get(d, 0), len(pool))
        if k:
            picks[d] = list(rng.choice(pool, size=k, replace=False))

    wanted = sorted({t for v in picks.values() for t in v})
    if not wanted:
        return pd.DataFrame()

    start = calendar_us.session_offset(calendar_us.all_sessions(), min(dates), 60)
    frames = dataset.panel(wanted, "1d", start=start,
                           end=calendar_us.last_closed_session())
    pos = {t: {str(dd): i for i, dd in enumerate(g["date"].astype(str))}
           for t, g in frames.items()}

    rows = []
    for d, tickers in picks.items():
        for t in tickers:
            g = frames.get(t)
            if g is None:
                continue
            a = pos[t].get(d)
            if a is None or a + 1 >= len(g):
                continue
            o = g["open"].to_numpy(float)
            h = g["high"].to_numpy(float)
            lw = g["low"].to_numpy(float)
            c = g["close"].to_numpy(float)
            entry = float(o[a + 1])
            if not np.isfinite(entry) or entry <= 0:
                continue

            # ATR(14) at the entry bar, from the pre-entry window only.
            seg = g.iloc[max(a - 60, 0):a + 1]
            atr_series = pt.atr(seg, 14).to_numpy(float)
            atr = float(atr_series[-1]) if np.isfinite(atr_series[-1]) else 0.05 * entry
            atr = float(np.clip(atr, 0.015 * entry, 0.120 * entry))

            end = min(a + 1 + backtest.MAX_HOLD, len(g))
            rows.append({
                "ticker": t, "test_date": d, "entry": entry, "atr_entry": atr,
                "path_h": h[a + 1:end].tolist(),
                "path_l": lw[a + 1:end].tolist(),
                "path_c": c[a + 1:end].tolist(),
                "bucket": "BASELINE", "stage": "BASELINE",
                "price_tier": "BASELINE", "age_band": "BASELINE",
                "support_grade": "BASELINE", "level": None,
            })
    return pd.DataFrame(rows)


def rehydrate_paths(fl: pd.DataFrame) -> pd.DataFrame:
    """Rebuild forward price paths for saved flags.

    backtest.py's --out drops path_h/path_l/path_c to keep the file small, so they
    have to be reconstructed from the store before exits can be applied. Exact,
    not approximate: same bars, keyed on (ticker, entry_date).
    """
    tickers = sorted(fl["ticker"].astype(str).unique())
    frames = dataset.panel(tickers, "1d",
                           start=str(fl["test_date"].min()),
                           end=calendar_us.last_closed_session())
    pos = {t: {str(d): i for i, d in enumerate(g["date"].astype(str))}
           for t, g in frames.items()}

    ph, pl, pc, keep = [], [], [], []
    for r in fl.to_dict("records"):
        g = frames.get(str(r["ticker"]))
        i = pos.get(str(r["ticker"]), {}).get(str(r["entry_date"])) if g is not None else None
        if g is None or i is None:
            ph.append([]), pl.append([]), pc.append([]), keep.append(False)
            continue
        end = min(i + backtest.MAX_HOLD, len(g))
        ph.append(g["high"].to_numpy(float)[i:end].tolist())
        pl.append(g["low"].to_numpy(float)[i:end].tolist())
        pc.append(g["close"].to_numpy(float)[i:end].tolist())
        keep.append(True)

    out = fl.copy()
    out["path_h"], out["path_l"], out["path_c"] = ph, pl, pc
    if "atr_entry" not in out.columns:
        out["atr_entry"] = out.get("atr_at_low",
                                   0.05 * out["entry"]).fillna(0.05 * out["entry"])
    dropped = int((~pd.Series(keep)).sum())
    if dropped:
        print(f"  ! {dropped} flag(s) had no reconstructable path; excluded")
    return out[pd.Series(keep).values]


def main() -> int:
    ap = argparse.ArgumentParser(description="Random-entry control group.")
    ap.add_argument("--start", default="2024-02")
    ap.add_argument("--end", default=None)
    ap.add_argument("--every", type=int, default=3)
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--flags", default="data/backtest_flags.parquet")
    a = ap.parse_args()

    config.dirs()
    t0 = time.time()

    fl = pd.read_parquet(a.flags)
    per_date = fl.groupby("test_date").size().to_dict()
    dates = sorted(per_date)
    print(f"matching {len(fl):,} flag(s) across {len(dates)} date(s)")

    print("  building per-date eligibility...")
    elig = backtest.eligibility()

    all_ex = []
    for s in range(a.seeds):
        b = run(dates, elig, per_date, seed=1000 + s, verbose=False)
        if b.empty:
            continue
        ex = backtest.apply_exits(b)
        ex["seed"] = s
        all_ex.append(ex)
        m = ex[ex["exit"] == "trail_2"]["ret"]
        print(f"    seed {s}: n={len(m):,}  trail_2 mean {m.mean():+.2%}  "
              f"median {m.median():+.2%}  win {(m > 0).mean():.0%}")

    if not all_ex:
        print("  no baseline trades")
        return 1
    base = pd.concat(all_ex, ignore_index=True)

    # The flags' own exits, for the head-to-head.
    print("  rehydrating flag paths...")
    fex = backtest.apply_exits(rehydrate_paths(fl))
    if fex.empty:
        print("  could not rebuild flag exits")
        return 1

    print(f"\n{'exit rule':<16}{'SCREEN mean':>13}{'BASE mean':>12}"
          f"{'EDGE':>10}{'screen win':>12}{'base win':>10}")
    print("-" * 73)
    out = []
    for rule in backtest.EXITS:
        s = fex[fex["exit"] == rule]["ret"]
        b = base[base["exit"] == rule]["ret"]
        if s.empty or b.empty:
            continue
        edge = s.mean() - b.mean()
        out.append({"exit": rule, "screen_mean": s.mean(), "base_mean": b.mean(),
                    "edge": edge, "screen_n": len(s), "base_n": len(b),
                    "screen_win": float((s > 0).mean()),
                    "base_win": float((b > 0).mean())})
        print(f"{rule:<16}{s.mean():>12.2%}{b.mean():>12.2%}"
              f"{edge:>+10.2%}{(s > 0).mean():>12.0%}{(b > 0).mean():>10.0%}")

    df = pd.DataFrame(out).sort_values("edge", ascending=False)
    best = df.iloc[0]
    print(f"\n  best edge: {best['exit']}  {best['edge']:+.2%} per trade "
          f"(screen {best['screen_mean']:+.2%} vs baseline {best['base_mean']:+.2%})")

    # A crude significance check. The distribution is fat-tailed, so a t-test is
    # only indicative -- but it separates "clear" from "noise".
    from scipy import stats
    s = fex[fex["exit"] == best["exit"]]["ret"].to_numpy()
    b = base[base["exit"] == best["exit"]]["ret"].to_numpy()
    t, p = stats.ttest_ind(s, b, equal_var=False)
    print(f"  Welch t-test on {best['exit']}: t={t:.2f}  p={p:.4f}  "
          f"(n_screen={len(s):,}, n_base={len(b):,})")
    if p > 0.05:
        print("  -> NOT statistically distinguishable from random selection.")
    elif t > 0:
        print("  -> screen beats random selection at p<0.05.")
    else:
        print("  -> screen LOSES to random selection at p<0.05.")

    if (df["edge"] <= 0).all():
        print("\n  !! NO exit rule beats random selection. The pattern adds nothing")
        print("     over picking a name from the same eligible pool.")

    print(f"\ndone in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
