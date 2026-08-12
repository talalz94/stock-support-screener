"""
Does a sentiment-ranked pick beat a random pick from the same eligible pool?

    python senti_backtest.py --start 2024-02 --every 3 --seeds 10

That question, and only that question. The bounce screener's own backtest already
demonstrated why: it produced +2.16% per trade on a 2-ATR trailing stop and
looked convincing, until a random pick from the SAME pool returned +1.60% and the
edge collapsed to +0.56% against a +/-0.96% standard error. Without the control,
"+2.16%" would have been read as an edge. It was mostly the pool.

So this module reuses, deliberately and without modification:

  * backtest.eligibility()  -- per-(ticker, date) gates from rolling windows, so
                              the pool on 2024-03-15 is what was eligible THEN
  * backtest.EXITS          -- the same 12 exit rules
  * the same next-open entry -- the signal bar's close is not tradeable

...so the sentiment result is directly comparable to the bounce result, and any
difference is the signal rather than the harness.

WHAT "RANKED BY SENTIMENT" MEANS HERE
-------------------------------------
On each test date, every eligible ticker that has a sentiment reading is ranked
by the chosen metric, and the top `--top` names are the picks. The control draws
the same number of names, uniformly, from the SAME eligible-and-covered pool --
not from the whole universe. That matters: names with news coverage are larger
and more liquid than names without, so a control drawn from the full universe
would hand the strategy a size premium and call it sentiment.
"""

from __future__ import annotations

import argparse
import sys
import time

import numpy as np
import pandas as pd

import backtest
import calendar_us
import config
import dataset
import scores


def _paths(tickers: list[str], dates: list[str], hold: int = 21) -> dict:
    """Entry at the NEXT open, plus the forward path each exit rule walks."""
    lookback = 30
    start = calendar_us.session_offset(calendar_us.all_sessions(),
                                       min(dates), lookback)
    frames = dataset.panel(sorted(set(tickers)), "1d", start=start,
                           end=calendar_us.last_closed_session())
    out = {}
    for t, g in frames.items():
        g = g.reset_index(drop=True)
        idx = {str(d): i for i, d in enumerate(g["date"].astype(str))}
        o = g["open"].to_numpy("float64")
        h = g["high"].to_numpy("float64")
        lo = g["low"].to_numpy("float64")
        c = g["close"].to_numpy("float64")
        prev = np.concatenate([[np.nan], c[:-1]])
        tr = np.maximum.reduce([h - lo, np.abs(h - prev), np.abs(lo - prev)])
        atr = pd.Series(tr).rolling(14, min_periods=7).mean().to_numpy()
        out[t] = {"idx": idx, "o": o, "h": h, "l": lo, "c": c, "atr": atr,
                  "n": len(g)}
    return out


def _trade(pk: dict, i: int, hold: int = 21) -> dict | None:
    """One trade: entry at bar i+1's open, path over the next `hold` bars."""
    e = i + 1
    if e >= pk["n"] or e + 1 >= pk["n"]:
        return None
    entry = pk["o"][e]
    if not np.isfinite(entry) or entry <= 0:
        return None
    atr = pk["atr"][i]
    if not np.isfinite(atr) or atr <= 0:
        return None
    j = min(e + hold, pk["n"])
    return {"entry": float(entry), "atr_entry": float(atr),
            "path_h": pk["h"][e:j].tolist(), "path_l": pk["l"][e:j].tolist(),
            "path_c": pk["c"][e:j].tolist(), "level": None}


def _apply(rows: list[dict]) -> pd.DataFrame:
    recs = []
    for r in rows:
        for name, fn in backtest.EXITS.items():
            if name == "below_level":
                continue                     # needs a support level; not defined here
            try:
                px, held = fn(r)
            except Exception:                                  # noqa: BLE001
                continue
            if not np.isfinite(px) or held == 0:
                continue
            recs.append({"exit": name, "ticker": r["ticker"],
                         "test_date": r["test_date"], "arm": r["arm"],
                         "ret": px / r["entry"] - 1.0, "held": held})
    return pd.DataFrame(recs)


def run(start: str = "2024-02", every: int = 3, top: int = 20,
        metric: str = "sent_mean_30d", seeds: int = 10,
        hold: int = 21, verbose: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    t0 = time.time()

    have = sorted(scores.sessions_stored("sentiment"))
    if not have:
        raise RuntimeError(
            "no stored sentiment scores. Run "
            "`python senti_screen.py --catchup 400` first.")

    sess = [s for s in calendar_us.all_sessions() if s >= start]
    dates = [d for d in sess[::every] if d in set(have)]
    if len(dates) < 5:
        raise RuntimeError(
            f"only {len(dates)} test date(s) have stored scores "
            f"({have[0]} .. {have[-1]}). Widen --start or run a longer --catchup.")

    if verbose:
        print(f"  {len(dates)} test date(s) {dates[0]} .. {dates[-1]}, "
              f"top={top}, metric={metric}, seeds={seeds}")

    elig = backtest.eligibility()
    elig = elig[elig["eligible"]]

    # ELIGIBILITY HAS A HARD START DATE, and it is not obvious. It needs
    # MIN_BARS=400 bars per ticker plus 250-session rolling windows, so with the
    # store beginning 2022-07-25 the first date with ANY eligible ticker is
    # 2024-02-26. That is precisely why the bounce backtest starts at 2024-02.
    #
    # Scoring dates before that produces an empty pool on every date and the run
    # dies with "no trades produced", which says nothing about why. Caught during
    # the build after a catchup that filled 2022-12..2024-02 -- a window in which
    # no backtest can ever run.
    first_elig = str(elig["date"].min()) if not elig.empty else None
    usable = [d for d in dates if first_elig and d >= first_elig]
    # Fewer than 5 usable dates is the same failure as none, and must produce the
    # same actionable message rather than a downstream "no trades produced".
    if len(usable) < 5:
        raise RuntimeError(
            f"only {len(usable)} of {len(dates)} scored date(s) reach the "
            f"eligibility window. backtest.eligibility() first has eligible names on "
            f"{first_elig} (it needs MIN_BARS={config.MIN_BARS} plus 250-session "
            f"rolling windows, and the bar store starts 2022-07-25). Your scored "
            f"range is {dates[0]} .. {dates[-1]}.\n"
            f"  Fix: finish `python news.py --backfill`, then\n"
            f"       `python senti_screen.py --from {first_elig} --every 3`")
    if len(usable) < len(dates):
        print(f"  dropping {len(dates) - len(usable)} date(s) before the "
              f"eligibility window opens ({first_elig})")
    dates = usable

    by_date = {d: set(g["ticker"].astype(str))
               for d, g in elig.groupby("date", observed=True)
               if d in set(dates)}

    sc = scores.read(module="sentiment", metrics=[metric, "has_news"],
                     start=dates[0], end=dates[-1])
    if sc.empty:
        raise RuntimeError(f"no stored rows for metric {metric!r}")

    rng = np.random.default_rng(17)
    picks: list[tuple[str, str, str]] = []       # (date, ticker, arm)

    for d in dates:
        pool = by_date.get(d, set())
        if not pool:
            continue
        day = sc[sc["session"] == d]
        cov = set(day.loc[day["metric"] == "has_news", "ticker"][
            day.loc[day["metric"] == "has_news", "value"] > 0])
        # THE POOL FOR BOTH ARMS: eligible AND carrying a sentiment reading.
        # Drawing the control from the full eligible set instead would give the
        # strategy the size/liquidity premium that comes with being covered at
        # all, and report it as a sentiment edge.
        both = sorted(pool & cov)
        if len(both) < top * 2:
            continue

        m = day[day["metric"] == metric].set_index("ticker")["value"]
        m = m.reindex(both).dropna()
        if len(m) < top * 2:
            continue

        for t in m.sort_values(ascending=False).head(top).index:
            picks.append((d, str(t), "screen"))
        for s in range(seeds):
            for t in rng.choice(list(m.index), size=top, replace=False):
                picks.append((d, str(t), f"random{s}"))

    if not picks:
        raise RuntimeError("no test date had a large enough covered pool")

    pk = _paths([p[1] for p in picks], dates, hold)
    rows = []
    for d, t, arm in picks:
        g = pk.get(t)
        if not g or d not in g["idx"]:
            continue
        tr = _trade(g, g["idx"][d], hold)
        if tr:
            tr.update({"ticker": t, "test_date": d, "arm": arm})
            rows.append(tr)

    ex = _apply(rows)
    if ex.empty:
        raise RuntimeError("no trades produced")

    summ = _summarise(ex, seeds)
    if verbose:
        print(f"  {len(rows):,} trade(s) in {time.time() - t0:.0f}s "
              f"({(ex['arm'] == 'screen').sum():,} screen rows)")
    return ex, summ


def _summarise(ex: pd.DataFrame, seeds: int) -> pd.DataFrame:
    out = []
    for rule, g in ex.groupby("exit", observed=True):
        s = g[g["arm"] == "screen"]["ret"]
        r = g[g["arm"] != "screen"]["ret"]
        if len(s) < 20 or len(r) < 20:
            continue
        # Per-seed means, so the baseline's own spread is visible. The bounce
        # backtest found single-seed baselines ranging +0.17% to +4.44% -- one
        # draw would have been worthless.
        per_seed = (g[g["arm"] != "screen"].groupby("arm")["ret"].mean()
                    if seeds > 1 else pd.Series(dtype=float))
        se = float(np.sqrt(s.var(ddof=1) / len(s) + r.var(ddof=1) / len(r)))
        t = (s.mean() - r.mean()) / se if se > 0 else np.nan
        out.append({
            "exit": rule, "n": len(s),
            "screen": s.mean(), "random": r.mean(),
            "edge": s.mean() - r.mean(), "se": se, "t": t,
            "win_s": float((s > 0).mean()), "win_r": float((r > 0).mean()),
            "seed_lo": per_seed.min() if len(per_seed) else np.nan,
            "seed_hi": per_seed.max() if len(per_seed) else np.nan,
            "held": g[g["arm"] == "screen"]["held"].median(),
        })
    return pd.DataFrame(out).sort_values("edge", ascending=False)


def report(summ: pd.DataFrame, metric: str) -> None:
    if summ.empty:
        print("  (nothing to report)")
        return
    print(f"\n  metric = {metric}\n")
    print(f"  {'exit':<15} {'n':>6} {'screen':>8} {'random':>8} {'edge':>8} "
          f"{'t':>6} {'win s/r':>12} {'seed range':>16}")
    for _, r in summ.iterrows():
        sr = (f"{r['seed_lo']:+.2%}..{r['seed_hi']:+.2%}"
              if np.isfinite(r["seed_lo"]) else "-")
        print(f"  {r['exit']:<15} {int(r['n']):>6,} {r['screen']:>+8.2%} "
              f"{r['random']:>+8.2%} {r['edge']:>+8.2%} {r['t']:>6.2f} "
              f"{r['win_s']:>5.0%}/{r['win_r']:<6.0%} {sr:>16}")

    fav = int((summ["edge"] > 0).sum())
    n = len(summ)
    # Sign test across rules. The rules are highly correlated so this OVERSTATES
    # the evidence -- the bounce backtest recorded exactly that caveat at 11/12,
    # and it applies identically here.
    from math import comb
    p = sum(comb(n, k) for k in range(fav, n + 1)) / (2 ** n)
    print(f"\n  {fav}/{n} exit rules favour the screen (sign test p={p:.4f}; "
          "rules are correlated, so this overstates the evidence)")

    best = summ.iloc[0]
    print(f"  best edge: {best['exit']} {best['edge']:+.2%} "
          f"(t={best['t']:.2f}, se={best['se']:.2%})")
    if abs(best["t"]) < 2:
        print("  |t| < 2: NOT distinguishable from random selection at this sample size.")


def main() -> int:
    config.safe_console()
    ap = argparse.ArgumentParser(
        description="Does sentiment beat a random pick from the same pool?")
    ap.add_argument("--start", default="2024-02")
    ap.add_argument("--every", type=int, default=3)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--metric", default="sent_mean_30d")
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--hold", type=int, default=21)
    ap.add_argument("--all-metrics", action="store_true",
                    help="sweep the ranking metrics (multiple-comparison warning applies)")
    a = ap.parse_args()

    config.dirs()
    scores.load_all()

    mets = ([a.metric] if not a.all_metrics else
            ["sent_mean_30d", "sent_mean_5d", "sent_delta", "severity_max",
             "news_z", "sent_net_30d"])
    for m in mets:
        try:
            _ex, summ = run(start=a.start, every=a.every, top=a.top,
                            metric=m, seeds=a.seeds, hold=a.hold)
            report(summ, m)
        except Exception as exc:                                # noqa: BLE001
            print(f"  {m}: {exc}")

    if a.all_metrics:
        print(f"\n  NOTE: {len(mets)} metrics x {len(backtest.EXITS) - 1} rules were "
              "compared. The bounce backtest found ZERO of 17 subgroup\n"
              "  comparisons survived Bonferroni. Treat any single winner here as a "
              "hypothesis for out-of-sample confirmation, not a filter.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
