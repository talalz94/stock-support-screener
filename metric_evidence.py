#!/usr/bin/env python
"""Which of the screener's metrics carry measured evidence? (plan item 3, step 1)

    python metric_evidence.py      -> data/_metric_evidence.json and data/_metric_evidence.csv
                                      (~10 min: one pass over the scores store)

The filtering pipeline the user asked for on 2026-11-08 ("both" a ranked daily shortlist and
an interactive filter page; criteria "Price & levels, Quality scores, Momentum & strength,
Risk & calendar", plus the AI narrative later) should label every criterion with what it
measurably did, and rank the shortlist only on what held up. This is that catalogue.
Research screens, not investment advice.

DESIGN, fixed 2026-11-08 before any result was seen:
  WHO      the step-10 events (big_movers_study.py --step 10: 217 dates 2018-01..2026-08, liquid
           names) and their at-support bounce trades (trade_rules_study.py --build).
  METRICS  every metric in the scores store with history from mid-2018, as of each date: each
           module's latest stored session on or before the date, at most 31 days old (the
           backfill scored 2-3 sessions a month); bookkeeping metrics (coverage counts, dates,
           labels) left out. Plus the studies' own: gain_days, loss_days, rally_count,
           recovery_rate, vol63, atr_pct, dist (to the level), and item 2's rs_sec_63,
           rs_sec_252, rs_mkt_63, above_126. Flags (0/1) compare on minus off.
  TESTS    per date, t across dates; TRAIN before 2022 / TEST from 2022; TRAIN halves at 2020:
    T1 at support, the default bounce trade (open, +5% / -5%, 10 sessions): top third minus
       bottom third of the metric.
    T2 the same with item 1's chosen rule (limit at the level + 0.5%, 3 x ATR target and stop,
       10 sessions), the one picked on TRAIN dates.
    T3 every liquid name: Spearman IC with the 20-session excess return (liquidity-fifth
       pool); 20-session horizons on dates 10 apart overlap, so Newey-West t, one lag.
  KEPT     both TRAIN halves and TEST agree in sign and TEST |t| >= 2 (the house rule). With
           about 3 x 120 comparisons some 18 pass |t| >= 2 by chance, so a metric is called
           STRONG only when kept with TEST |t| >= 3.
"""
from __future__ import annotations

import glob
import json
import sys
import time
import warnings
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import config

EVENTS = config.DATA / "_big_movers_events_s10.parquet"
OUT = config.DATA / "_metric_evidence.json"
OUT_CSV = config.DATA / "_metric_evidence.csv"
SPLIT, HALF = "2022-01-01", "2020-01-01"
MAX_AGE_DAYS = 31
SKIP = {"sector", "currency", "top_event", "top_headline", "event_types", "top_severity_band",
        "last_filed", "reports_usd", "bars_used", "has_fundamentals", "sent_age", "sent_stale",
        "days_since_filing"}
STUDY = ["gain_days", "loss_days", "rally_count", "recovery_rate", "vol63", "atr_pct", "dist",
         "rs_sec_63", "rs_sec_252", "rs_mkt_63", "above_126"]


def _skip(m: str) -> bool:
    return m in SKIP or m.endswith("_cov") or m.endswith("_n")


def scores_asof(dates: list[str], names: dict[str, set], verbose: bool = True) -> pd.DataFrame:
    """Per date, a ticker x metric frame of each module's latest session <= date (<= 31 days
    old), for that date's liquid names only -- pivoted per date, so memory stays small."""
    idx = pd.read_parquet(config.DATA / "_scores_index.parquet")
    idx["session"] = idx.session.astype(str).str[:10]
    sess = idx[["month", "module", "session"]].drop_duplicates().sort_values("session")
    cache: dict[str, pd.DataFrame] = {}
    out = []
    t0 = time.time()
    for n, D in enumerate(dates):
        lo = str((pd.Timestamp(D) - pd.Timedelta(days=MAX_AGE_DAYS)).date())
        pick = sess[(sess.session <= D) & (sess.session >= lo)].groupby("module").tail(1)
        frames = []
        for month, s in pick.groupby("month").session:
            if month not in cache:
                d = pd.read_parquet(config.DATA / "scores" / f"{month}.parquet",
                                    columns=["ticker", "session", "metric", "value"])
                d["session"] = d.session.astype(str).str[:10]
                cache[month] = d
            d = cache[month]
            frames.append(d[d.session.isin(set(s))])
        for m in [m for m in cache if m < lo[:7]]:
            del cache[m]
        if not frames:
            continue
        f = pd.concat(frames, ignore_index=True)
        f = f[~f.metric.map(_skip)]
        f = f[f.ticker.isin(names[D])]
        f = f.sort_values("session").drop_duplicates(["ticker", "metric"], keep="last")
        w = f.pivot(index="ticker", columns="metric", values="value").astype("float32")
        w.insert(0, "date", D)
        out.append(w.reset_index())
        if verbose and n % 25 == 0:
            print(f"  scores as of {D}: {w.shape[1] - 2} metrics, {len(w):,} names; "
                  f"{time.time() - t0:.0f}s", flush=True)
    return pd.concat(out, ignore_index=True)


def _t(x) -> float | None:
    x = pd.Series(x, dtype=float).dropna()
    if len(x) < 3 or x.std(ddof=1) == 0:
        return None
    return round(float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))), 2)


def _nw_t(x) -> float | None:
    x = pd.Series(x, dtype=float).dropna().to_numpy()
    if len(x) < 5:
        return None
    u = x - x.mean()
    v = u @ u / len(x) + 2 * 0.5 * (u[1:] @ u[:-1]) / len(x)
    return round(float(x.mean() / np.sqrt(v / len(x))), 2) if v > 0 else None


def summary(x: pd.Series, nw: bool = False, scale: float = 100) -> dict:
    x = x.dropna()
    if x.empty:                     # e.g. a 0/1 flag has no rank IC: nothing to report
        return {"dates": 0, "train": None, "train_t": None, "h1": None, "h2": None, "test": None,
                "test_t": None, "kept": False, "strong": False}
    x.index = x.index.astype(str)
    tf = _nw_t if nw else _t
    tr, te = x[x.index < SPLIT], x[x.index >= SPLIT]
    h1, h2 = tr[tr.index < HALF], tr[tr.index >= HALF]
    r = lambda v: round(float(v) * scale, 3) if len(x) and np.isfinite(v) else None
    s = {"dates": int(len(x)), "train": r(tr.mean()), "train_t": tf(tr), "h1": r(h1.mean()),
         "h2": r(h2.mean()), "test": r(te.mean()), "test_t": tf(te)}
    sg = [np.sign(s[k] or 0) for k in ("h1", "h2", "test")]
    s["kept"] = bool(sg[0] != 0 and sg[0] == sg[1] == sg[2] and abs(s["test_t"] or 0) >= 2)
    s["strong"] = bool(s["kept"] and abs(s["test_t"] or 0) >= 3)
    return s


def _spread(df: pd.DataFrame, col: str, y: str, binary: bool) -> pd.Series:
    out = {}
    for d, g in df[df[col].notna() & df[y].notna()].groupby("date"):
        x = g[col]
        if binary:
            top, bot = x == 1.0, x == 0.0
        else:
            lo, hi = x.quantile(1 / 3), x.quantile(2 / 3)
            top, bot = x > hi, x <= lo
            if top.sum() < 10:
                top = x >= hi
        if top.sum() < 10 or bot.sum() < 10 or bool((top & bot).any()):
            continue
        out[d] = g.loc[top, y].mean() - g.loc[bot, y].mean()
    return pd.Series(out, dtype=float).sort_index()


def run(verbose: bool = True) -> dict:
    warnings.filterwarnings("ignore")
    import trade_rules_study as TR
    t0 = time.time()
    ev = pd.read_parquet(EVENTS)
    ev["loss_days"] = ev["top20_dn_w252"]
    dates = sorted(ev.date.unique())
    names = ev.groupby("date").ticker.agg(set).to_dict()
    W = scores_asof(dates, names, verbose).set_index(["date", "ticker"])
    metrics = [m for m in W.columns if W[m].notna().mean() > 0.05]
    W = W[metrics]
    if verbose:
        print(f"pivoted {len(W):,} name-dates x {len(metrics)} metrics; {time.time() - t0:.0f}s", flush=True)
    rs = pd.read_parquet(config.DATA / "_rs_metrics.parquet")
    A = (ev[["date", "ticker", "at_support", "excess_20", "gain_days", "loss_days", "rally_count",
             "recovery_rate", "dips", "vol63", "dist"]]
         .merge(W.reset_index(), on=["date", "ticker"], how="left")
         .merge(rs[["date", "ticker", "rs_sec_63", "rs_sec_252", "rs_mkt_63", "above_126"]],
                on=["date", "ticker"], how="left"))
    A.loc[A.dips.fillna(0) < 2, "recovery_rate"] = np.nan
    T = pd.read_parquet(TR.OUT_TRADES, columns=["date", "ticker", "atr_pct"])
    Z = np.load(TR.OUT_NPZ)
    R = list(Z["rules"])
    q1 = json.loads(TR.OUT.read_text(encoding="utf-8"))["q1"]["chosen"]
    T["y_default"] = Z["ret"][:, R.index(TR.DEFAULT)]
    T["y_q1"] = Z["ret"][:, R.index(q1)]
    AT = T.merge(A, on=["date", "ticker"], how="left")
    cols = metrics + [c for c in STUDY if c in AT.columns and c not in metrics]
    res = {}
    for n, c in enumerate(cols):
        vals = AT[c].dropna()
        binary = bool(len(vals) and vals.isin([0.0, 1.0]).all())
        r = {"binary": binary, "coverage_at_support": round(float(AT[c].notna().mean()), 3)}
        r["t1_default"] = summary(_spread(AT, c, "y_default", binary))
        r["t2_q1_rule"] = summary(_spread(AT, c, "y_q1", binary))
        if c in A.columns:
            ic = {}
            for d, g in A[A[c].notna() & A.excess_20.notna()].groupby("date"):
                if len(g) >= 100 and g[c].nunique() > 2:
                    ic[d] = g[c].rank().corr(g.excess_20.rank())
            r["t3_ic"] = summary(pd.Series(ic, dtype=float).sort_index(), nw=True, scale=1)
        res[c] = r
        if verbose and n % 20 == 0:
            print(f"  tested {n + 1}/{len(cols)} metrics; {time.time() - t0:.0f}s", flush=True)
    n_tests = sum(sum(1 for k in ("t1_default", "t2_q1_rule", "t3_ic") if k in r and r[k].get("test_t") is not None)
                  for r in res.values())
    out = {"measured_on": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "definitions": __doc__.strip(), "q1_rule": q1, "metrics": len(cols),
           "tests_with_t": n_tests, "expected_abs_t_ge_2_by_chance": round(n_tests * 0.05, 1),
           "expected_abs_t_ge_3_by_chance": round(n_tests * 0.0027, 1), "results": res}
    rows = []
    for c, r in res.items():
        row = {"metric": c, "binary": r["binary"], "coverage": r["coverage_at_support"]}
        for k in ("t1_default", "t2_q1_rule", "t3_ic"):
            if k in r:
                for f in ("train", "test", "test_t", "kept", "strong"):
                    row[f"{k}_{f}"] = r[k].get(f)
        rows.append(row)
    pd.DataFrame(rows).to_csv(OUT_CSV, index=False)
    return out


def main() -> int:
    s = run()
    OUT.write_text(json.dumps(s, indent=1, default=str), encoding="utf-8")
    kept = {c: [k for k in ("t1_default", "t2_q1_rule", "t3_ic") if k in r and r[k]["kept"]]
            for c, r in s["results"].items()}
    kept = {c: v for c, v in kept.items() if v}
    print(f"wrote {OUT.name}: {s['metrics']} metrics, {s['tests_with_t']} tests (~{s['expected_abs_t_ge_2_by_chance']} "
          f"pass |t| >= 2 and ~{s['expected_abs_t_ge_3_by_chance']} pass |t| >= 3 by chance); kept: {kept}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
