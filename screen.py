"""
The screening funnel.

    python screen.py                      screen the last closed session
    python screen.py --only RDW --explain per-gate trace for one name
    python screen.py --gates-only         panel prefilter only, no pattern math
    python screen.py --date 2026-07-30    screen as of a past session

Two structural commitments:

1. `screen_one` SLICES ONCE at the top and rebases `asof` to a local index.
   Nothing below that line may reference the parent frame. This is what makes the
   same function safe to replay historically -- see replay.py.

2. EVERY ticker emits a row, always, with `reject_code` (the first gate that
   failed) and `failed_gates` (all of them). Recording all failures rather than
   just the first is what turns "how many more would pass if MIN_RUN_X were
   2.0?" into a groupby instead of twelve re-runs.

Gate order is cheapest-first, so the expensive level clustering only runs on names
that already look like a round trip.
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

import bars
import calendar_us
import config
import dataset
import levels as lv
import pattern as pt
import store
import universe

# (code, stage, predicate). Mirrors sweep.py's FILT lambda-registry so gates stay
# composable and individually ablatable.
GATES: list[tuple[str, int, object]] = [
    ("SHORT_HISTORY",         0, lambda m: m["n_bars"] >= config.MIN_BARS),
    ("PENNY",                 0, lambda m: m["close"] >= config.MIN_PRICE),
    ("ILLIQUID",              0, lambda m: m["adv_usd"] >= config.MIN_DOLLAR_VOL),
    ("NO_TRADES",             0, lambda m: m["trades_20"] >= config.MIN_TRADES_20D),
    ("SUSPECT_SPLIT",         0, lambda m: not m["suspect_split"]),
    ("NEAR_HIGHS",            1, lambda m: m["pct_of_250d_high"] <= config.MAX_PCT_OF_250D_HIGH),
    ("NO_MAJOR_PEAK",         1, lambda m: m["peak_i"] is not None),
    ("PEAK_TOO_RECENT",       1, lambda m: m["sessions_since_peak"] >= config.MIN_DECLINE_BARS),
    ("PEAK_TOO_OLD",          1, lambda m: m["sessions_since_peak"] <= config.MAX_DECLINE_BARS),
    ("PEAK_NEAR_LISTING",     1, lambda m: m["bars_before_peak"] >= config.IPO_QUARANTINE),
    ("RUN_STRUCTURE_UNCLEAR", 2, lambda m: m["b0"] is not None),
    ("RUN_TOO_SHORT",         2, lambda m: m["run_bars"] >= config.MIN_RUN_BARS),
    ("RUN_TOO_LONG",          2, lambda m: m["run_bars"] <= config.MAX_RUN_BARS),
    ("RUN_TOO_SMALL",         2, lambda m: m["run_x"] >= config.MIN_RUN_X),
    ("RUN_NOT_PARABOLIC",     2, lambda m: m["run_z"] >= config.MIN_RUN_Z),
    ("DD_TOO_SHALLOW",        3, lambda m: m["dd_from_peak"] >= config.DD_MIN),
    ("RETRACE_INCOMPLETE",    3, lambda m: m["retrace_of_run"] >= config.RETRACE_LO),
    ("RETRACE_OVERSHOT",      3, lambda m: m["retrace_of_run"] <= config.RETRACE_HI),
    ("BASE_UNDERCUT",         3, lambda m: (m["undercut"] <= config.UNDERCUT_LOW_MAX
                                            and m["undercut_close"] <= config.UNDERCUT_CLOSE_MAX)),
    ("NO_LEVEL_NEAR_LOW",     4, lambda m: m["level"] is not None),
    ("LEVEL_TOO_FEW_TOUCHES", 4, lambda m: m["touches_prior"] >= config.MIN_PRIOR_TOUCHES),
    ("LEVEL_NOT_PRE_RUN",     4, lambda m: (m["touches_pre_run"] >= 1
                                            or not config.REQUIRE_PRE_RUN_TOUCH)),
    ("LEVEL_OFF_BASE",        4, lambda m: m["level_off_base"] <= config.BASE_PROX),
    ("LEVEL_STALE",           4, lambda m: m["gap_bars"] <= config.STALE_BARS),
    ("LEVEL_BROKEN",          4, lambda m: not m["level_broken"]),
    ("LOW_OFF_LEVEL",         5, lambda m: (config.LOW_TO_LEVEL[0] <= m["dist_low_level"]
                                            <= config.LOW_TO_LEVEL[1])),
    ("LOW_NOT_LIVE",          5, lambda m: m["bars_since_low"] <= config.MAX_BARS_SINCE_LOW_TESTING),
    ("NOT_BOUNCING",          6, lambda m: m["ext_pct"] >= config.MIN_BOUNCE_PCT),
    ("WEAK_CONFIRM",          6, lambda m: m["bounce_score"] >= config.MIN_BOUNCE_SCORE),
    ("TOO_EXTENDED",          6, lambda m: (m["ext_atr"] <= config.EXT_EXTENDED
                                            and m["ext_pct"] <= config.EXT_PCT_HARD_CAP)),
    ("LOW_SCORE",             7, lambda m: m["score"] >= config.MIN_SCORE),
]

_GATE_STAGE = {code: stage for code, stage, _ in GATES}


def _eval_gates(m: dict, up_to_stage: int) -> list[str]:
    """Every gate up to `up_to_stage` that fails, in declaration order."""
    failed = []
    for code, stage, pred in GATES:
        if stage > up_to_stage:
            continue
        try:
            if not pred(m):
                failed.append(code)
        except (TypeError, KeyError, ValueError):
            failed.append(code)          # a metric that could not be computed
    return failed


def _blank(ticker: str) -> dict:
    return {
        "ticker": ticker, "peak_i": None, "b0": None, "b_lo": None, "level": None,
        "n_bars": 0, "close": np.nan, "adv_usd": 0.0, "trades_20": 0.0,
        "suspect_split": False, "pct_of_250d_high": 1.0,
        "sessions_since_peak": 0, "bars_before_peak": 10 ** 6,
        "run_bars": 0, "run_x": 0.0, "run_z": 0.0, "run_dd_max": np.nan,
        "dd_from_peak": 0.0, "retrace_of_run": 0.0, "undercut": 1.0,
        "undercut_close": 1.0, "touches_prior": 0, "touches_pre_run": 0,
        "touches_total": 0, "level_off_base": 9.99, "gap_bars": 10 ** 6,
        "level_broken": True, "dist_low_level": 9.99, "bars_since_low": 10 ** 6,
        "ext_pct": 0.0, "ext_atr": 0.0, "bounce_score": 0.0, "score": 0.0,
    }


def screen_one(df: pd.DataFrame, asof: int | None = None,
               market_cap: float | None = None, cfg=config) -> dict:
    """Evaluate one ticker. Returns a metrics dict; never raises on data shape.

    `asof` is a positional index into `df`. The window is sliced ONCE here and
    `asof` rebased to a local index, so this function's output depends only on
    bars at or before `asof`. That property is what replay.py's truncation and
    future-scramble assertions verify.
    """
    ticker = str(df["ticker"].iloc[0]) if len(df) else "?"
    asof = len(df) - 1 if asof is None else int(asof)
    if asof < 0 or asof >= len(df):
        m = _blank(ticker)
        m["reject_code"] = "SHORT_HISTORY"
        m["failed_gates"] = ["SHORT_HISTORY"]
        return m

    n_total = asof + 1
    lo = max(0, n_total - (cfg.IND_WARMUP + cfg.STRUCT_WIN))
    w = df.iloc[lo:asof + 1].reset_index(drop=True)
    a = len(w) - 1                        # local asof; `df` is not touched again

    m = _blank(ticker)
    m["asof_date"] = str(w["date"].iloc[a])
    m["n_bars"] = n_total

    o = w["open"].to_numpy(float)
    h = w["high"].to_numpy(float)
    l = w["low"].to_numpy(float)          # noqa: E741
    c = w["close"].to_numpy(float)
    v = w["volume"].to_numpy(float)
    tn = (w["trades"].to_numpy(float) if "trades" in w else np.zeros(len(w)))
    dates = w["date"].to_numpy(dtype=object)

    m["close"] = float(c[a])
    m["adv_usd"] = float(np.nanmedian((c * v)[max(a - 19, 0):a + 1]))
    m["trades_20"] = float(np.nanmedian(tn[max(a - 19, 0):a + 1]))
    m["volume"] = float(v[a])
    m["suspect_split"] = pt.suspect_split(c, v, cfg)

    seg = slice(max(a - 249, 0), a + 1)
    hi250 = float(np.nanmax(h[seg]))
    lo250 = float(np.nanmin(l[seg]))
    m["hi250"], m["lo250"] = hi250, lo250
    m["pct_of_250d_high"] = float(c[a] / hi250) if hi250 > 0 else 1.0
    m["range_250_x"] = float(hi250 / lo250) if lo250 > 0 else 0.0

    failed = _eval_gates(m, 0)
    if failed:
        m["reject_code"], m["failed_gates"] = failed[0], failed
        return m

    # ---------------------------------------------------------------- indicators
    ind = pt.indicators_for(w)
    atr_pct = lv.atr_pct_of(ind["atr14"], c)
    m["atr_pct"] = atr_pct

    piv = lv.find_pivots(h, l, atr_pct, right_edge=a, cfg=cfg)
    m["prom_major"], m["prom_minor"] = piv.prom_major, piv.prom_minor

    p = pt.dominant_peak(h, piv.maj_pk, a, cfg)
    m["peak_i"] = p
    if p is not None:
        m["peak_high"] = float(h[p])
        m["peak_date"] = str(dates[p])
        m["sessions_since_peak"] = int(a - p)
        # An IPO pop is not a run off a base, because there is no base. RDW is
        # itself a de-SPAC (first bar 2021-01-14), so this must not be too strict.
        m["bars_before_peak"] = int(lo + p)

    failed = _eval_gates(m, 1)
    if failed:
        m["reject_code"], m["failed_gates"] = failed[0], failed
        return m

    # ---------------------------------------------------------------- run + base
    b0, b_lo, dd_max = pt.find_run_start(h, l, p, piv.min_tr, cfg)
    m["b0"], m["b_lo"] = b0, b_lo
    if b0 is not None:
        base = pt.base_zone(l, c, ind["atr14"], b0, b_lo, p, cfg)
        run = pt.run_stats(h, base, p, dd_max)
        m.update({
            "base_lo": base.base_lo, "base_md": base.base_md,
            "base_hi": base.base_hi, "base_center": base.base_center,
            "base_width": base.base_width, "base_date": str(dates[base.b_lo]),
            "run_x": run.run_x, "run_z": run.run_z, "run_bars": run.run_bars,
            "run_dd_max": run.run_dd_max, "base_atr_pct": base.atr_pct_base,
        })
        m.update(lv.topology(h, piv.maj_pk, p, b0, cfg))

    failed = _eval_gates(m, 2)
    if failed:
        m["reject_code"], m["failed_gates"] = failed[0], failed
        return m

    # ---------------------------------------------------------------- retrace
    low_i, bounce_low = pt.bounce_low_since(l, p, a)
    m["low_i"], m["bounce_low"] = low_i, bounce_low
    m["bounce_low_date"] = str(dates[low_i])
    m.update(pt.retrace_metrics(h, c, p, base, low_i, bounce_low, a))

    failed = _eval_gates(m, 3)
    if failed:
        m["reject_code"], m["failed_gates"] = failed[0], failed
        return m

    # ---------------------------------------------------------------- levels
    # Candidate pivots: minor-trough lows AND minor-peak highs. Old resistance
    # genuinely becomes support, and on this pattern the pre-run base's UPPER edge
    # is often the line that actually holds.
    px = np.concatenate([l[piv.min_tr], h[piv.min_pk]])
    idx = np.concatenate([piv.min_tr, piv.min_pk])
    wt = np.concatenate([np.ones(len(piv.min_tr)),
                         np.full(len(piv.min_pk), cfg.PEAK_PIVOT_WEIGHT)])

    tol = lv.level_tolerance(bounce_low, atr_pct, cfg)
    cand = lv.cluster_levels(px, idx, wt, tol, cfg.MIN_PIVOTS_PER_LEVEL)
    m["n_levels_found"] = len(cand)
    m["level_tol"] = tol

    best = None
    if not cand.empty:
        # Wide selection band, then PREFER candidates that also satisfy the tight
        # stage-5 distance gate. Selecting on the tight band alone misses shelves
        # whose cluster median lands just outside it; selecting on the wide band
        # alone can pick a high-Q level 11% away and fail stage 5 while a nearer
        # usable level existed. Two tiers gets both right.
        near = cand[(cand["level"] <= bounce_low * (1 + cfg.LEVEL_SELECT_BAND))
                    & (cand["level"] >= bounce_low * (1 - cfg.LEVEL_SELECT_BAND))]
        m["n_levels_near_low"] = len(near)

        scored = []
        for _, row in near.iterrows():
            L = float(row["level"])
            # ORDERING IS MANDATORY: touch stats use the STRUCTURAL b_lo, never a
            # b_lo re-derived from this candidate level. Choosing, per level, the
            # b_lo that maximises that level's own quality would be a per-ticker
            # in-sample fit under which every ticker produces a beautiful level.
            ts = lv.count_touches(l, c, v, dates, L, a, low_i, base.b_lo, p, cfg)
            Q, qparts = lv.score_level(ts, len(w), cfg)
            dist = bounce_low / L - 1.0
            tight = cfg.LOW_TO_LEVEL[0] <= dist <= cfg.LOW_TO_LEVEL[1]
            scored.append((tight, Q, L, ts, qparts, row))

        if scored:
            tight_only = [s for s in scored if s[0]]
            pool = tight_only or scored
            pick = max(pool, key=lambda s: s[1])
            best = (pick[2], pick[1], pick[3], pick[4], pick[5])
            m["n_levels_tight"] = len(tight_only)

    if best is not None:
        L, Q, ts, qparts, row = best
        m.update({
            "level": L, "level_Q": Q,
            "level_px_lo": float(row["px_lo"]), "level_px_hi": float(row["px_hi"]),
            "level_n_pivots": int(row["n_pivots"]),
            "touches_prior": ts.touches_prior,
            "touches_pre_run": ts.touches_pre_run,
            "touches_pre_peak": ts.touches_pre_peak,
            "touches_total": ts.touches_total,
            "span_days": ts.span_days, "gap_bars": ts.gap_bars,
            "vol_at_level_ratio": ts.vol_ratio,
            # How far OUTSIDE the base zone the level sits; 0 if inside it.
            #
            # Deliberately not distance-from-base-centre. The base is a zone
            # (RDW: 7.43-9.65) and a support level legitimately sits at its FLOOR,
            # so a centre-distance metric penalises the correct answer -- RDW's
            # real level, 7.44, is base_lo to the cent yet scored 0.121 against a
            # 0.12 threshold. Zone membership is what "the level is part of the
            # pre-run base" actually means, and the low's own tightness to the
            # level is already gated separately by LOW_OFF_LEVEL.
            "level_off_base": float(
                base.base_lo / L - 1.0 if L < base.base_lo
                else L / base.base_hi - 1.0 if L > base.base_hi
                else 0.0),
            "level_off_center": float(abs(L / max(base.base_center, 1e-9) - 1.0)),
            "level_broken": lv.support_broken(c, L, p, a, cfg),
            "dist_low_level": float(bounce_low / L - 1.0),
            "dist_now_level": float(c[a] / L - 1.0),
            "prior_visits": [[int(s) + lo, int(e) + lo] for s, e in ts.prior_visits],
            "touch_dates": [str(dates[int(e)]) for _s, e in ts.prior_visits],
        })
        m.update({f"level_{k}": vv for k, vv in qparts.items()})

    failed = _eval_gates(m, 5)
    if failed:
        m["reject_code"], m["failed_gates"] = failed[0], failed
        return m

    # ---------------------------------------------------------------- bounce
    B, bparts = pt.bounce_score(o, h, l, c, ind, piv.min_tr, low_i, bounce_low, a, cfg)
    V, vparts = pt.volume_signature(v, low_i, a)
    ext = pt.extension(c, ind["atr14"], low_i, bounce_low, a, cfg)
    m["bounce_score"] = B
    m["volume_score"] = V
    m.update(ext)
    m.update({f"b_{k}": vv for k, vv in bparts.items()})
    m.update({f"v_{k}": vv for k, vv in vparts.items()})

    score, sparts = pt.composite(
        m["level_Q"], m["retrace_of_run"], run, B, V, m["dist_low_level"],
        base.base_width, ext["stage_fit"], m["adv_usd"], market_cap, cfg)
    m["score"] = score
    m["score_band"] = pt.score_band(score, cfg)
    m["market_cap"] = market_cap
    m.update({f"s_{k}": vv for k, vv in sparts.items()})

    failed = _eval_gates(m, 7)
    m["failed_gates"] = failed
    m["reject_code"] = failed[0] if failed else None
    m["passed"] = not failed
    return m


# ------------------------------------------------------------------ panel stage
def prefilter(ps: pd.DataFrame, asof_date: str, cfg=config) -> tuple[list[str], pd.DataFrame]:
    """Vectorized panel filter. Returns (survivors, rejects_frame).

    Kills ~90% of the universe in a handful of column comparisons, so the
    per-ticker Python loop only ever sees a few hundred names. Reads
    _panel_stats.parquet rather than the full store: the difference is ~2s versus
    ~20s of zstd decompression, and it is the single biggest saving in the daily
    run.

    `close <= MAX_PCT_OF_250D_HIGH * 250d_high` is the cheapest, highest-yield
    clause in the whole design -- the pattern requires price to be deep below its
    high, and that is one comparison.
    """
    if ps.empty:
        return [], pd.DataFrame()

    ps = ps.copy()
    ps["ticker"] = ps["ticker"].astype(str)

    tests = {
        "STALE_DATA": ps["last_date"] >= asof_date,
        "SHORT_HISTORY": ps["n_bars"] >= cfg.MIN_BARS,
        "PENNY": ps["last_close"] >= cfg.MIN_PRICE,
        "ILLIQUID": ps["dollar_vol_20"] >= cfg.MIN_DOLLAR_VOL,
        "NO_TRADES": ps["trades_20"] >= cfg.MIN_TRADES_20D,
        "NEAR_HIGHS": ps["pct_of_250d_high"] <= cfg.MAX_PCT_OF_250D_HIGH,
        "FLAT_RANGE": ps["range_250_x"] >= cfg.MIN_250D_RANGE_X,
    }
    keep = pd.Series(True, index=ps.index)
    for t in tests.values():
        keep &= t.fillna(False)

    rej = ps.loc[~keep, ["ticker"]].copy()
    first = pd.Series("", index=ps.index)
    for name, t in tests.items():
        bad = ~t.fillna(False) & (first == "")
        first[bad] = name
    rej["reason"] = first.loc[~keep].values
    rej["stage"] = 0

    return ps.loc[keep, "ticker"].tolist(), rej


def _fundamentals() -> dict[str, float]:
    if not config.FUNDAMENTALS_FILE.exists():
        return {}
    f = pd.read_parquet(config.FUNDAMENTALS_FILE)
    if f.empty or "market_cap" not in f:
        return {}
    return dict(zip(f["ticker"].astype(str), f["market_cap"].astype(float)))


_PANEL_COLS = {"last_close": "close", "dollar_vol_20": "adv_usd",
               "trades_20": "trades_20", "n_bars": "n_bars",
               "pct_of_250d_high": "pct_of_250d_high"}


def _panel_reject_rows(rej, ps, asof_date: str):
    """Give panel rejects the column names the pattern-math rows already use.

    They carry no pattern metrics -- they never reached the pattern math, and
    inventing zeros for `run_x` or `retrace_of_run` would make an unexamined name
    look like a measured one. Price, liquidity, history depth and distance below
    the 250d high ARE computed for them, and those four are exactly the numbers
    that explain every panel rejection.
    """
    if rej is None or rej.empty:
        return rej if rej is not None else pd.DataFrame()
    cols = ["ticker"] + [c for c in _PANEL_COLS if c in ps.columns]
    out = (rej.merge(ps[cols], on="ticker", how="left")
              .rename(columns={**_PANEL_COLS, "reason": "reject_code"}))
    return out.assign(asof_date=asof_date, passed=False, tier="panel")


def screen_universe(asof_date: str | None = None, only: list[str] | None = None,
                    gates_only: bool = False, workers: int | None = None,
                    cfg=config, verbose: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run the whole funnel. Returns (flags, rejects)."""
    asof_date = asof_date or calendar_us.last_closed_session()
    ps = bars.load_panel_stats()

    if only:
        survivors = [universe.normalize(t) for t in only]
        panel_rej = pd.DataFrame()
        if verbose:
            print(f"  --only: {', '.join(survivors)}")
    else:
        survivors, panel_rej = prefilter(ps, asof_date, cfg)
        if verbose:
            print(f"  panel prefilter: {len(ps):,} -> {len(survivors):,} survivors")
            if not panel_rej.empty:
                vc = panel_rej["reason"].value_counts()
                for r, n in vc.items():
                    print(f"    {r:18} {n:>6,}")

    if gates_only or not survivors:
        return pd.DataFrame(), _panel_reject_rows(panel_rej, ps, asof_date)

    t0 = time.time()
    # Only the survivors' history is read -- a few hundred tickers, not 5,374.
    start = calendar_us.session_offset(
        calendar_us.all_sessions(), asof_date, cfg.IND_WARMUP + cfg.STRUCT_WIN + 5)
    frames = dataset.panel(survivors, "1d", start=start, end=asof_date)
    if verbose:
        print(f"  loaded {len(frames):,} ticker histories in {time.time() - t0:.1f}s")

    mcap = _fundamentals()
    rows: list[dict] = []
    t1 = time.time()

    def run(item):
        t, g = item
        try:
            return screen_one(g, None, mcap.get(t), cfg)
        except Exception as exc:              # never let one ticker kill the scan
            r = _blank(t)
            r["reject_code"] = "ERROR"
            r["failed_gates"] = ["ERROR"]
            r["error"] = repr(exc)[:200]
            return r

    workers = workers or cfg.SCREEN_WORKERS
    if workers <= 1:
        rows = [run(it) for it in frames.items()]
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(run, it) for it in frames.items()]
            for fut in as_completed(futs):
                rows.append(fut.result())

    if verbose:
        print(f"  pattern math on {len(rows):,} tickers in {time.time() - t1:.1f}s")

    allrows = pd.DataFrame(rows)
    if allrows.empty:
        return pd.DataFrame(), _panel_reject_rows(panel_rej, ps, asof_date)

    passed = allrows[allrows.get("passed", False) == True].copy()   # noqa: E712
    rejected = allrows[allrows.get("passed", False) != True].copy()  # noqa: E712

    if verbose:
        print("\n  funnel by stage:")
        counts = rejected["reject_code"].value_counts()
        by_stage: dict[int, int] = {}
        for code, n in counts.items():
            by_stage[_GATE_STAGE.get(code, 9)] = by_stage.get(_GATE_STAGE.get(code, 9), 0) + n
        remaining = len(allrows)
        for stage in sorted(by_stage):
            remaining -= by_stage[stage]
            print(f"    stage {stage}: -{by_stage[stage]:>5,}  -> {remaining:>5,} remain")
        print(f"\n  top reject reasons:")
        for code, n in counts.head(12).items():
            print(f"    {code:24} {n:>6,}")
        print(f"\n  PASSED: {len(passed):,}")

    if not passed.empty:
        passed = passed.sort_values("score", ascending=False).reset_index(drop=True)

    # Two-tier rejects: cheap panel gates get the panel columns, names that reached
    # the pattern math keep their full metric row plus every gate they failed. BOTH
    # tiers are returned. Returning only the second made the stored record claim the
    # screen looked at 679 names on a day it looked at every one of 5,439 -- the
    # 4,760 it dismissed in one vectorized pass are examined names with a stated
    # reason, not names that went unconsidered.
    rej_out = rejected.copy()
    if not rej_out.empty:
        rej_out["failed_gates"] = rej_out["failed_gates"].map(
            lambda x: ",".join(x) if isinstance(x, list) else str(x))
        rej_out["stage"] = rej_out["reject_code"].map(
            lambda c: _GATE_STAGE.get(c, 9))
        # SHORT_HISTORY/PENNY/ILLIQUID/NO_TRADES are declared at stage 0 AND run in
        # the panel prefilter, so the code alone cannot say which tier rejected a
        # name. `tier` is what separates "dismissed in the vectorized pass" from
        # "measured per-ticker and failed the same test on fresher data".
        rej_out["tier"] = "pattern"

    if not panel_rej.empty:
        panel_rej = _panel_reject_rows(panel_rej, ps, asof_date)
        rej_out = (panel_rej if rej_out.empty
                   else pd.concat([rej_out, panel_rej], ignore_index=True))

    return passed, rej_out


def explain_one(ticker: str, asof_date: str | None = None, cfg=config) -> dict | None:
    """Full per-gate trace for one ticker. The tuning entry point."""
    t = universe.normalize(ticker)
    asof_date = asof_date or calendar_us.last_closed_session()
    start = calendar_us.session_offset(
        calendar_us.all_sessions(), asof_date, cfg.IND_WARMUP + cfg.STRUCT_WIN + 5)
    df = dataset.history(t, "1d", start=start, end=asof_date)
    if df.empty:
        print(f"  {t}: no bars in the store")
        return None

    m = screen_one(df, None, _fundamentals().get(t), cfg)
    failed = set(m.get("failed_gates") or [])

    print(f"\n  {t}  as of {m.get('asof_date')}  close {m.get('close'):.2f}")
    print(f"  {'VERDICT':<24} "
          f"{'PASS' if m.get('passed') else 'REJECT -> ' + str(m.get('reject_code'))}")

    def show(label: str, key: str, fmt: str = "{}") -> None:
        """Print m[key] formatted. `key` is ALWAYS a dict key, never a value."""
        v = m.get(key)
        try:
            s = fmt.format(v)
        except (TypeError, ValueError):
            s = str(v)
        print(f"    {label:<26} {s}")

    def line(label: str, text: str) -> None:
        """Print an already-composed string (for combined fields)."""
        print(f"    {label:<26} {text}")

    def g(key: str, default=float("nan")):
        v = m.get(key)
        return default if v is None else v

    print("\n  structure")
    show("peak", "peak_high", "{:.2f}")
    show("peak date", "peak_date")
    show("sessions since peak", "sessions_since_peak")
    line("base low / center / high",
         f"{g('base_lo'):.2f} / {g('base_center'):.2f} / {g('base_hi'):.2f}")
    show("base date", "base_date")
    line("run_x / run_z / bars",
         f"{g('run_x', 0):.2f}x / {g('run_z', 0):.2f} / {g('run_bars', 0)}"
         f"   (need >={cfg.MIN_RUN_X}x, z>={cfg.MIN_RUN_Z})")
    line("base ATR%", f"{g('base_atr_pct', float('nan')):.3f}")
    show("run max drawdown", "run_dd_max", "{:.1%}")
    show("shape", "shape")

    print("\n  retrace")
    show("bounce low", "bounce_low", "{:.2f}")
    show("bounce low date", "bounce_low_date")
    show("bars since low", "bars_since_low")
    show("drawdown from peak", "dd_from_peak", "{:.1%}")
    show("retrace of run", "retrace_of_run", "{:.3f}")
    line("undercut low / close",
         f"{g('undercut', 0):+.1%} / {g('undercut_close', 0):+.1%}")

    print("\n  level")
    show("level", "level", "{:.2f}")
    show("Q", "level_Q", "{:.3f}")
    line("touches prior / pre-run / total",
         f"{g('touches_prior', 0)} / {g('touches_pre_run', 0)}"
         f" / {g('touches_total', 0)}   (need >={cfg.MIN_PRIOR_TOUCHES} prior, >=1 pre-run)")
    show("touch dates", "touch_dates")
    line("span days / gap bars",
         f"{g('span_days', 0)} / {g('gap_bars', 0)}")
    show("level off base", "level_off_base", "{:.3f}")
    show("low vs level", "dist_low_level", "{:+.2%}")
    show("now vs level", "dist_now_level", "{:+.2%}")
    show("broken", "level_broken")
    line("candidates found / near low",
         f"{g('n_levels_found', 0)} / {g('n_levels_near_low', 0)}"
         f"   (tol +/-{g('level_tol', 0):.3f})")

    print("\n  bounce")
    show("B (>= %d)" % cfg.MIN_BOUNCE_SCORE, "bounce_score", "{:.0f}")
    show("V", "volume_score", "{:.2f}")
    line("ext_atr / ext_pct",
         f"{g('ext_atr', 0):.2f} / {g('ext_pct', 0):+.1%}")
    show("ATR at low", "atr_at_low", "{:.3f}")
    show("stage", "stage")
    on = [k[2:] for k in m if k.startswith("b_") and isinstance(m[k], float) and m[k] > 0]
    print(f"    {'B components firing':<26} {', '.join(sorted(on)) if on else '(none)'}")

    print("\n  score")
    show("composite", "score", "{:.1f}")
    show("band", "score_band")
    for k in ("support", "bounce", "retrace", "run", "volume", "tightness",
              "stage", "liquidity", "size"):
        v = m.get(f"s_{k}")
        if v is not None:
            print(f"    {'  ' + k:<26} {v:5.1f}")

    if failed:
        print(f"\n  FAILED GATES: {', '.join(sorted(failed))}")
    print()
    return m


LIST_COLUMNS = ("failed_gates", "prior_visits", "touch_dates")


def serialise_lists(df: pd.DataFrame) -> pd.DataFrame:
    """Stringify list-valued columns before parquet.

    Must be applied by EVERY writer of the flags file, not just this one. A plain
    to_parquet preserves a Python list and reads it back as a numpy array, so a
    downstream `if value or []` then raises on ndarray truthiness -- which is
    exactly how confirm.py's write-back broke report.py.
    """
    out = df.copy()
    for col in LIST_COLUMNS:
        if col in out:
            out[col] = out[col].map(
                lambda x: str(list(x)) if isinstance(x, (list, tuple, np.ndarray))
                else x)
    return out


def write_outputs(flags: pd.DataFrame, rejects: pd.DataFrame,
                  asof_date: str) -> None:
    config.dirs()
    if not flags.empty:
        f = serialise_lists(flags)
        p = config.FLAGS / f"{asof_date}.parquet"
        tmp = p.with_suffix(".parquet.tmp")
        f.to_parquet(tmp, compression=config.COMPRESSION, index=False)
        tmp.replace(p)
        print(f"  wrote {p.name}  ({len(f)} flags)")

    if not rejects.empty:
        r = serialise_lists(rejects)
        p = config.REJECTS / f"{asof_date}.parquet"
        tmp = p.with_suffix(".parquet.tmp")
        r.to_parquet(tmp, compression=config.COMPRESSION, index=False)
        tmp.replace(p)
        print(f"  wrote {p.name}  ({len(r)} rejects)")
    store.prune_dated(config.REJECTS, config.REJECT_KEEP_DAYS)


def main() -> int:
    config.safe_console()
    ap = argparse.ArgumentParser(description="Support-bounce screener.")
    ap.add_argument("--date", default=None, help="as-of session (default: last closed)")
    ap.add_argument("--only", nargs="*", metavar="SYM")
    ap.add_argument("--explain", action="store_true")
    ap.add_argument("--gates-only", action="store_true")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()

    config.dirs()
    asof = a.date or calendar_us.last_closed_session()
    t0 = time.time()
    print(f"screening as of {asof}")

    if a.explain and a.only:
        for t in a.only:
            explain_one(t, asof)
        return 0

    flags, rejects = screen_universe(asof, a.only, a.gates_only, a.workers)

    if not flags.empty:
        cols = [c for c in ("ticker", "close", "score", "score_band", "stage",
                            "level", "touches_prior", "sessions_since_peak",
                            "run_x", "retrace_of_run", "ext_atr", "adv_usd")
                if c in flags]
        show = flags[cols].head(25).copy()
        for c in ("close", "level", "run_x", "retrace_of_run", "ext_atr", "score"):
            if c in show:
                show[c] = show[c].map(lambda x: f"{x:.2f}")
        if "adv_usd" in show:
            show["adv_usd"] = show["adv_usd"].map(lambda x: f"{x / 1e6:.1f}M")
        print("\n" + show.to_string(index=False))

    if not a.no_write and not a.gates_only:
        write_outputs(flags, rejects, asof)

    print(f"\ndone in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

