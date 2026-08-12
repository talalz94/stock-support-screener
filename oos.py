"""
Out-of-sample validation for the combo scores.

    python oos.py --split 2021-09-01
    python oos.py --split 2021-09-01 --horizons 20,60
    python oos.py --dry-run

THE PROBLEM THIS EXISTS TO ANSWER
-----------------------------------
`combo_h60` reads t=+4.75 at h=20 over ~174 dates. That number is **in-sample**:
`study.py` measured every metric over the whole history, `scores/combo.py`
admitted the ones that scored well, and then the composite was graded on the
same history that chose its ingredients. A procedure that picks the best 20 of
80 metrics and reports how well those 20 did will produce an impressive number
from pure noise.

So: fit on a TRAIN period, freeze, and grade only on dates the fit never saw.

WHAT IS AND IS NOT HELD OUT
-----------------------------
Held out: which metrics are admitted, their signs, and the theme weights -- all
derived from train-period study cells only.

NOT held out, and this must be stated rather than glossed: the metric
DEFINITIONS, the theme assignments, the dedup rule and the exclusion list were
authored by a human who had already seen the full-sample results. That is
researcher degrees of freedom no split can remove. This test answers "do the
fitted weights generalise", not "is the whole design free of hindsight".

WHY A SINGLE SPLIT AND NOT WALK-FORWARD
-----------------------------------------
Walk-forward is better and is the obvious next step. A single split is what
fits in one run: refitting the study at every step multiplies a ~40-minute
measurement by the number of folds. If the single split fails, walk-forward is
not worth building.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd

import config

config.safe_console()

import factor_lab                                               # noqa: E402
import scores                                                   # noqa: E402

OUT = config.DATA / "_oos.parquet"
WF_OUT = config.DATA / "_oos_walkforward.parquet"
HORIZONS = (1, 20, 60)


def train_path(split: str):
    """One cache per split. Walk-forward refits at every fold, and a single
    shared file would mean each fold silently overwriting the last."""
    return config.DATA / f"_oos_train_{split}.parquet"


def fit(split: str, horizons=HORIZONS, reuse: bool = True) -> pd.DataFrame:
    """The train-only study for `split`, measured once and cached.

    The fit is the expensive half (~22 min) and is deterministic given the
    split, so it is cached. Without this, inspecting WHICH metrics the honest
    fit chose costs a full re-measure -- which is how the first run shipped a
    headline t-stat with no record of the ingredients behind it.
    """
    p = train_path(split)
    if reuse and p.exists():
        try:
            df = pd.read_parquet(p)
            log(f"reusing cached train fit: {len(df)} cell(s) from {p.name}")
            return df
        except Exception:                                        # noqa: BLE001
            log(f"  cached fit {p.name} unreadable; re-measuring")
    df = train_study(split, horizons=horizons)
    if df.empty:
        return df
    import store
    tmp = p.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, compression=config.COMPRESSION,
                  compression_level=config.COMPRESSION_LEVEL, index=False)
    store.atomic_replace(tmp, p)
    log(f"cached the train fit to {p.name}")
    return df


def log(m: str) -> None:
    line = f"oos   {datetime.now():%H:%M:%S} | {m}"
    print(line, flush=True)
    try:
        with (config.DATA / "_oos.log").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def train_study(split: str, horizons=HORIZONS, min_dates: int = 10
                ) -> pd.DataFrame:
    """Measure every candidate metric using ONLY sessions before `split`.

    The same computation `study.py` does, restricted in time and to the `all`
    size bucket -- combo admits on `size == "all"`, so the per-size cells would
    be measured and never read.
    """
    import scores.combo as C
    scores.load_all()
    rows = []
    todo = []
    for mod in config.SCORE_MODULES:
        if mod == C.NAME:
            continue                      # combo cannot be an input to itself
        for metric in scores.get(mod).metrics():
            if not factor_lab.is_signal(metric):
                continue
            if metric in C.COMPOSITE or metric in C.EXCLUDED:
                continue
            todo.append((mod, metric))

    log(f"measuring {len(todo)} candidate metric(s) on sessions < {split}")
    t0 = time.time()
    for i, (mod, metric) in enumerate(todo, 1):
        try:
            mr = factor_lab.load_metric(mod, metric, end=split)
        except Exception:                                        # noqa: BLE001
            continue
        if mr is None or mr.empty or mr["date"].nunique() < min_dates:
            continue
        for h in horizons:
            rec = {"module": mod, "metric": metric, "horizon": h, "size": "all",
                   "ic": np.nan, "t": np.nan, "hit": np.nan,
                   "ic_random": np.nan, "spread": np.nan, "n_dates": 0,
                   "measured_at": datetime.now().isoformat(timespec="seconds")}
            try:
                res = factor_lab.evaluate(mr, horizons=(h,), by=None)
                ic = res.get("ic")
                if ic is not None and not ic.empty:
                    r = ic.iloc[0]
                    rec.update({"ic": float(r["ic"]), "t": float(r["t"]),
                                "hit": float(r["hit"]),
                                "ic_random": float(r["ic_random"]),
                                "n_dates": int(r.get("n_dates", 0) or 0)})
            except Exception:                                    # noqa: BLE001
                pass
            rows.append(rec)
        if i % 20 == 0 or i == len(todo):
            el = (time.time() - t0) / 60
            log(f"  {i}/{len(todo)} metric(s), {el:.1f}m, "
                f"eta {el / max(i, 1) * (len(todo) - i):.0f}m")
    return pd.DataFrame(rows)


def score_test(sessions, train_sdf) -> pd.DataFrame:
    """Combo values on held-out dates, using ONLY the train-fitted evidence."""
    import bars
    import scores.combo as C
    scores.load_all()
    out = []
    t0 = time.time()
    for i, s in enumerate(sessions, 1):
        try:
            uni = bars.tradeable_universe(s)
            if not uni:
                continue
            rows = C.MODULE.compute(s, uni, study_df=train_sdf)
            if rows is None or rows.empty:
                continue
            keep = rows[rows["metric"].isin(
                [f"combo_{lab}" for lab in C.HORIZONS])]
            keep = keep.assign(date=s)
            out.append(keep[["date", "ticker", "metric", "value"]])
        except Exception as exc:                                 # noqa: BLE001
            log(f"  ! {s}: {type(exc).__name__}")
        if i % 20 == 0 or i == len(sessions):
            el = (time.time() - t0) / 60
            log(f"  scored {i}/{len(sessions)} session(s), {el:.1f}m")
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def walk_forward(sess, folds: int, horizons, reuse: bool) -> int:
    """Expanding-window walk-forward: refit at every fold, never look ahead.

    The single split answers "does a 2016-2021 fit work on 2021-2026". It
    cannot distinguish a signal that is genuinely stable from one that happened
    to survive one arbitrary cut -- and the cut was chosen by me, at the median,
    which is one more researcher degree of freedom.

    Here the fit is redone from scratch at each fold on everything before that
    fold's test block, so a metric that only worked in one era shows up as a
    fold that fails rather than being averaged into a single reassuring number.
    Per-fold results are reported SEPARATELY for that reason; the pooled row at
    the end is a summary, not the finding.

    WHY EXPANDING AND NOT ROLLING: a rolling window would hold the training
    size fixed, which is cleaner statistically, but this series is 176 sessions
    total. A fixed window small enough to leave four folds would admit almost
    nothing at |t| >= 2, and every fold would report "no score" -- a test that
    can only be inconclusive.
    """
    import scores.combo as C
    n = len(sess)
    # Leave the first ~40% as the initial training block: below ~70 sessions
    # the |t| >= 2 bar admits so little that the fold measures the bar, not the
    # signal. That is a judgement, and it is the one knob here worth stating.
    start = max(60, int(n * 0.40))
    block = max(15, (n - start) // folds)
    bounds = [(start + i * block, min(start + (i + 1) * block, n))
              for i in range(folds)]
    bounds = [(a_, b_) for a_, b_ in bounds if b_ - a_ >= 10]
    if not bounds:
        log(f"{n} session(s) cannot be split into {folds} usable fold(s)")
        return 1

    log(f"WALK-FORWARD: {len(bounds)} expanding fold(s) over {n} sessions")
    for i, (a_, b_) in enumerate(bounds, 1):
        log(f"  fold {i}: train {sess[0]}..{sess[a_ - 1]} ({a_}), "
            f"test {sess[a_]}..{sess[b_ - 1]} ({b_ - a_})")

    recs = []
    for i, (a_, b_) in enumerate(bounds, 1):
        split, test = sess[a_], sess[a_:b_]
        log("")
        log(f"--- fold {i}/{len(bounds)}: refit on {a_} session(s) < {split}")
        tsdf = fit(split, horizons=horizons, reuse=reuse)
        if tsdf.empty:
            log(f"  fold {i}: no train cells; skipped")
            continue
        adm_n = {lab: len(C.admitted(h, tsdf)) for lab, h in C.HORIZONS.items()}
        log(f"  admitted: " + "  ".join(f"{k}={v}" for k, v in adm_n.items()))
        vals = score_test(test, tsdf)
        if vals.empty:
            log(f"  fold {i}: no scores produced; skipped")
            continue
        for lab in C.HORIZONS:
            name = f"combo_{lab}"
            mr = vals[vals["metric"] == name][["date", "ticker", "value"]]
            if mr.empty:
                continue
            for h in horizons:
                try:
                    res = factor_lab.evaluate(mr, horizons=(h,), by=None)
                    ic = res.get("ic")
                    if ic is None or ic.empty:
                        continue
                    r = ic.iloc[0]
                    recs.append({"fold": i, "split": split, "score": name,
                                 "horizon": h, "ic": float(r["ic"]),
                                 "t": float(r["t"]), "hit": float(r["hit"]),
                                 "n_dates": int(r.get("n_dates", 0) or 0),
                                 "n_admitted": adm_n.get(lab, 0)})
                except Exception as exc:                         # noqa: BLE001
                    log(f"  {name} h={h}: {type(exc).__name__}")

    if not recs:
        log("no fold produced a gradeable result")
        return 1

    df = pd.DataFrame(recs)
    import store
    tmp = WF_OUT.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, compression=config.COMPRESSION,
                  compression_level=config.COMPRESSION_LEVEL, index=False)
    store.atomic_replace(tmp, WF_OUT)

    log("")
    log("WALK-FORWARD RESULT -- every fold shown, because an average hides "
        "the fold that failed:")
    log(f"  {'score':<12}{'h':>4}" +
        "".join(f"{'f' + str(i):>9}" for i in sorted(df['fold'].unique())) +
        f"{'folds>0':>9}{'mean t':>9}")
    for name in [f"combo_{lab}" for lab in C.HORIZONS]:
        for h in horizons:
            s = df[(df["score"] == name) & (df["horizon"] == h)]
            if s.empty:
                continue
            cells = "".join(
                f"{float(s[s['fold'] == f]['t'].iloc[0]):>+9.2f}"
                if len(s[s["fold"] == f]) else f"{'--':>9}"
                for f in sorted(df["fold"].unique()))
            log(f"  {name:<12}{h:>4}{cells}"
                f"{int((s['t'] > 0).sum())}/{len(s):>8}"
                f"{s['t'].mean():>+9.2f}")

    log("")
    # THE VERDICT MUST TEST DECAY, NOT JUST SIGN.
    #
    # The first version of this asked only "is every fold positive?" and
    # printed STABLE ACROSS FOLDS for a series reading +3.60, +2.28, +0.94,
    # +0.42 -- a signal falling monotonically to nothing, described as stable
    # because none of the four numbers happened to be negative. That is a test
    # that cannot fail on a dying edge, which is the same worthless-guard
    # pattern this project keeps paying for.
    #
    # So three questions, in order of how much they should worry you:
    #   1. does the LAST fold still work?   the recent period is the only one
    #                                       that says anything about tomorrow
    #   2. is the trend DOWNWARD?           strong early and weak late is decay
    #                                       or regime, not a durable edge
    #   3. is every fold positive?          the weakest claim, checked last
    best = df.groupby(["score", "horizon"])["t"].agg(["mean", "min", "count"])
    top = best.sort_values("mean", ascending=False).index[0]
    row = best.loc[top]
    s = df[(df["score"] == top[0]) & (df["horizon"] == top[1])].sort_values("fold")
    ts = list(s["t"])
    last, first = ts[-1], ts[0]
    # Spearman of t against fold index: -1 is a perfectly monotonic decline.
    trend = (s["t"].rank().corr(s["fold"].rank())
             if len(ts) > 2 else float("nan"))
    log(f"  best average: {top[0]} at h={top[1]}, mean t={row['mean']:+.2f} "
        f"across {int(row['count'])} fold(s)")
    log(f"    by fold: {', '.join(f'{t:+.2f}' for t in ts)}"
        f"   (trend rho={trend:+.2f})")
    if last >= 2:
        log(f"  HOLDS IN THE MOST RECENT FOLD: t={last:+.2f}. The edge is "
            f"present in data closest to today, which is the only fold that "
            f"speaks to whether it still works.")
    elif trend <= -0.8:
        log(f"  DECAYING: t falls monotonically {first:+.2f} -> {last:+.2f} "
            f"(rho={trend:+.2f}). Whatever the mean says, the most recent "
            f"fold reads {last:+.2f}. An edge that worked early and not late "
            f"is decay, arbitrage or regime -- NOT something to trade on the "
            f"strength of its average.")
    else:
        log(f"  WEAK: the most recent fold reads t={last:+.2f}, below the "
            f"|t|>=2 bar. The mean of {row['mean']:+.2f} is carried by older "
            f"folds.")
    log(f"  Per-fold n is small ({int(s['n_dates'].min())}-"
        f"{int(s['n_dates'].max())} dates, n_eff roughly two-thirds of that at "
        f"h=20), so ONE weak fold would be noise. A monotonic slide across "
        f"every score and horizon is not.")
    log(f"  wrote {WF_OUT.name}")
    log("DONE")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Out-of-sample test for combo.")
    ap.add_argument("--split", default=None,
                    help="first TEST session; default is the median combo date")
    ap.add_argument("--horizons", default="1,20,60")
    ap.add_argument("--reuse-train", action="store_true",
                    help="reuse a cached train fit for the same split")
    ap.add_argument("--walk-forward", type=int, default=0, metavar="N",
                    help="N expanding folds instead of one split")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    horizons = tuple(int(x) for x in a.horizons.split(","))

    scores.load_all()
    sess = scores.sessions_stored("combo")
    if len(sess) < 40:
        log(f"only {len(sess)} combo session(s); too few to split")
        return 1

    if a.walk_forward:
        log(f"combo series {len(sess)} sessions {sess[0]} -> {sess[-1]}")
        if a.dry_run:
            n = len(sess)
            start = max(60, int(n * 0.40))
            block = max(15, (n - start) // a.walk_forward)
            for i in range(a.walk_forward):
                a_, b_ = start + i * block, min(start + (i + 1) * block, n)
                if b_ - a_ < 10:
                    continue
                log(f"  fold {i + 1}: train {a_} session(s) < {sess[a_]}, "
                    f"test {sess[a_]}..{sess[b_ - 1]} ({b_ - a_})")
            log("--dry-run: nothing measured")
            return 0
        return walk_forward(sess, a.walk_forward, horizons, a.reuse_train)

    split = a.split or sess[len(sess) // 2]
    train = [s for s in sess if s < split]
    test = [s for s in sess if s >= split]
    log(f"combo series {len(sess)} sessions {sess[0]} -> {sess[-1]}")
    log(f"  split at {split}: {len(train)} train, {len(test)} test")
    if a.dry_run:
        log("--dry-run: nothing measured")
        return 0
    if len(test) < 20:
        log(f"only {len(test)} test session(s); the t-stat would be meaningless")
        return 1

    # ---- 1. fit on train only -------------------------------------------
    tsdf = fit(split, horizons=horizons, reuse=a.reuse_train)
    if tsdf.empty:
        log("no train cells measured")
        return 1

    import scores.combo as C
    log("")
    log("ADMITTED ON TRAIN DATA ONLY:")
    for lab, h in C.HORIZONS.items():
        if h not in horizons:
            continue
        adm = C.admitted(h, tsdf)
        full = C.admitted(h)                       # what the full sample picks
        log(f"  combo_{lab}: {len(adm)} admitted on train "
            f"(full sample picks {len(full)})")
        # Named, not counted. "5 metrics survived" is not a reviewable claim;
        # "these 5, with these signs and themes" is.
        for _, r in adm.sort_values("t", key=lambda s: s.abs(),
                                    ascending=False).iterrows():
            log(f"      {r['metric']:<22}{r['theme']:<14}"
                f"t={r['t']:+6.2f}  ic={r['ic']:+.4f}")
        only_full = sorted(set(full['metric']) - set(adm['metric']))
        if only_full:
            log(f"    full-sample-only, i.e. NOT available to the honest fit: "
                f"{len(only_full)} metric(s), {only_full[:8]}")

    # ---- 2. score the held-out dates with the frozen fit -----------------
    log("")
    log(f"scoring {len(test)} held-out session(s) with the train-fitted weights")
    vals = score_test(test, tsdf)
    if vals.empty:
        log("no out-of-sample scores produced")
        return 1

    # ---- 3. grade -------------------------------------------------------
    log("")
    log("OUT-OF-SAMPLE RESULT (test dates only, train-fitted weights):")
    log(f"  {'score':<14}{'h':>4}{'IC':>9}{'t':>8}{'hit':>7}{'dates':>7}")
    recs = []
    for lab in C.HORIZONS:
        name = f"combo_{lab}"
        mr = vals[vals["metric"] == name][["date", "ticker", "value"]]
        if mr.empty:
            continue
        for h in horizons:
            try:
                res = factor_lab.evaluate(mr, horizons=(h,), by=None)
                ic = res.get("ic")
                if ic is None or ic.empty:
                    continue
                r = ic.iloc[0]
                recs.append({"score": name, "horizon": h, "ic": float(r["ic"]),
                             "t": float(r["t"]), "hit": float(r["hit"]),
                             "ic_random": float(r["ic_random"]),
                             "n_dates": int(r.get("n_dates", 0) or 0),
                             "split": split})
                log(f"  {name:<14}{h:>4}{r['ic']:>9.4f}{r['t']:>8.2f}"
                    f"{r['hit']:>7.0%}{int(r.get('n_dates', 0)):>7}")
            except Exception as exc:                             # noqa: BLE001
                log(f"  {name} h={h}: {type(exc).__name__}")

    if recs:
        df = pd.DataFrame(recs)
        import store
        tmp = OUT.with_suffix(".parquet.tmp")
        df.to_parquet(tmp, compression=config.COMPRESSION,
                      compression_level=config.COMPRESSION_LEVEL, index=False)
        store.atomic_replace(tmp, OUT)
        log(f"\n  wrote {OUT.name}")

        best = df.loc[df["t"].abs().idxmax()]
        log("")
        if abs(best["t"]) >= 2:
            log(f"  HOLDS OUT OF SAMPLE: {best['score']} at h={int(best['horizon'])} "
                f"reads t={best['t']:+.2f} on {int(best['n_dates'])} unseen dates.")
        else:
            log(f"  DOES NOT HOLD: the strongest out-of-sample reading is "
                f"{best['score']} h={int(best['horizon'])} at t={best['t']:+.2f}, "
                f"below the |t|>=2 bar. The in-sample number was selection, not "
                f"signal.")
        log("  Either way: the metric definitions and theme assignments were "
            "authored with full-sample knowledge, so this bounds the "
            "optimism -- it does not eliminate it.")
    log("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
