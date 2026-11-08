#!/usr/bin/env python
"""The daily shortlist (plan item 3): which ranking of at-support stocks works best?

    python shortlist.py --backtest     every ranking's top-N on the step-10 dates, judged like
                                       items 1-2 -> data/_shortlist_backtest.json (~4 min)

Asked 2026-11-08: a ranked daily shortlist plus a filter page; for the ranking, "try all and give
the best option and others too"; size "lets start with 20 but it should be dynamic and
compatible". Research screens, not investment advice.

BACKTEST DESIGN, fixed 2026-11-08 before any result was seen:
  WHO      the at-support name-dates of trade_rules_study.py (217 dates, 2018-01..2026-08),
           with their trades under the default rule (open, +5% / -5%, 10 sessions) and item 1's
           TRAIN-chosen rule (limit at the level + 0.5% for 3 sessions, 3 x ATR target and
           stop, 10 sessions).
  RANKINGS (RANKINGS below) each date's top N = 20 by the metric (ties by ticker), as of the
           date (scores: each module's latest stored session <= the date, <= 31 days old):
           limit_ready (distance above the level, largest first), combo_h20, combo_h60,
           fund_score, growth_score, gain_days (the 10-23 list's rule), rally_count,
           recovery_rate (>= 2 dips), insider_score, sentiment (sent_mean_30d), low_vol
           (63-day volatility, smallest first).
  MEASURE  per date, the top-N's mean trade return minus every at-support name's mean (the
           plain rule), t across dates; TRAIN before 2022 / TEST after; TRAIN halves at 2020;
           KEPT = both halves and TEST agree in sign and TEST |t| >= 2. The BEST ranking is
           the one with the highest TRAIN t under the chosen rule; the others are reported
           beside it. A top-20 is noisy, so the same is measured for 200 random 20s per date
           (the spread chance alone gives).
"""
from __future__ import annotations

import json
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import config

N_DEFAULT = 20
RANKINGS = {
    "limit_ready": ("dist", False),
    "combo_h20": ("combo_h20", False),
    "combo_h60": ("combo_h60", False),
    "fund_score": ("fund_score", False),
    "growth_score": ("growth_score", False),
    "gain_days": ("gain_days", False),
    "rally_count": ("rally_count", False),
    "recovery_rate": ("recovery_rate", False),
    "insider_score": ("insider_score", False),
    "sentiment": ("sent_mean_30d", False),
    "low_vol": ("vol63", True),
}
SCORE_METRICS = ["combo_h20", "combo_h60", "fund_score", "growth_score", "insider_score", "sent_mean_30d"]
SPLIT, HALF = "2022-01-01", "2020-01-01"
OUT_BT = config.DATA / "_shortlist_backtest.json"
RANDOM_DRAWS = 200


def _summary(x: pd.Series) -> dict:
    import trade_rules_study as TR
    return TR.summary(x)


def backtest(n: int = N_DEFAULT, verbose: bool = True) -> dict:
    warnings.filterwarnings("ignore")
    import metric_evidence as ME
    import trade_rules_study as TR
    t0 = time.time()
    T = pd.read_parquet(TR.OUT_TRADES)
    Z = np.load(TR.OUT_NPZ)
    R = list(Z["rules"])
    q1 = json.loads(TR.OUT.read_text(encoding="utf-8"))["q1"]["chosen"]
    T["y_default"] = Z["ret"][:, R.index(TR.DEFAULT)]
    T["y_q1"] = Z["ret"][:, R.index(q1)]
    T.loc[T.dips.fillna(0) < 2, "recovery_rate"] = np.nan
    dates = sorted(T.date.unique())
    names = T.groupby("date").ticker.agg(set).to_dict()
    S = ME.scores_asof(dates, names, verbose=False)
    S = S[["date", "ticker"] + [m for m in SCORE_METRICS if m in S.columns]]
    T = T.merge(S, on=["date", "ticker"], how="left")
    if verbose:
        print(f"loaded {len(T):,} at-support name-dates with scores; {time.time() - t0:.0f}s", flush=True)
    base = {y: T.groupby("date")[y].mean() for y in ("y_default", "y_q1")}
    out = {"measured_on": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "definitions": __doc__.strip(), "n": n, "q1_rule": q1, "rankings": {}}
    for name, (col, asc) in RANKINGS.items():
        top = (T[T[col].notna()].sort_values(["date", col, "ticker"], ascending=[True, asc, True])
               .groupby("date").head(n))
        r = {"metric": col, "ascending": asc,
             "names_per_date": int(top.groupby("date").size().median()) if len(top) else 0}
        for y in ("y_default", "y_q1"):
            m = top.groupby("date")[y].mean()
            r[y] = {"top_n": _summary(m), "top_minus_all": _summary(m - base[y]),
                    "fill_rate": round(float(top[y].notna().mean()), 3)}
        out["rankings"][name] = r
    rng = np.random.default_rng(20261108)
    rnd = {y: [] for y in ("y_default", "y_q1")}
    for D, g in T.groupby("date"):
        idx = g.index.to_numpy()
        if len(idx) < n:
            continue
        for y in rnd:
            v = g[y].to_numpy()
            draws = [np.nanmean(v[rng.choice(len(idx), n, replace=False)]) for _ in range(RANDOM_DRAWS)]
            rnd[y].append((D, float(np.nanstd(draws)), float(np.nanmean(v))))
    out["random_top_n_spread_pp"] = {y: round(float(np.mean([s for _, s, _ in v])) * 100, 3) for y, v in rnd.items()}
    trn = {k: v["y_q1"]["top_minus_all"].get("train_t") for k, v in out["rankings"].items()}
    best = max((k for k, v in trn.items() if v is not None), key=lambda k: trn[k], default=None)
    out["best_on_train"] = best
    out["train_t_by_ranking"] = trn
    return out


# ------------------------------------------------------------------------- the daily build
RECIPES_FILE = config.ROOT / "shortlist_recipes.json"
DIR = config.DATA / "shortlist"
LEDGER = DIR / "ledger.parquet"
OUTCOMES = DIR / "outcomes.parquet"
PAGE = config.REPORTS / "shortlist"
LIMIT_UP, LIMIT_DAYS, HOLD = 0.005, 3, 10
# Item 1's rule chosen on TRAIN dates once the 30-minute bars were in (2026-11-08): a buy limit at
# the level + 0.5% for 3 sessions, target +3% and stop -8% from the fill, out after 10 sessions.
# 2022-26 with fills that must trade 0.2% through the limit: +0.36% a trade (t 2.42), positive in
# 8 of 9 years. +5% / -5% is the symmetric alternative (+0.38%, t 2.37).
TGT, STP = 0.03, 0.08
TGT_ALT, STP_ALT = 0.05, 0.05
COST = 0.001


def recipes() -> dict:
    cfg = json.loads(RECIPES_FILE.read_text(encoding="utf-8"))
    names = [r["name"] for r in cfg["recipes"]]
    assert cfg["default"] in names, f"default {cfg['default']!r} is not a recipe"
    assert 1 <= int(cfg["n"]) <= int(cfg["max_n"]), "n must be between 1 and max_n"
    return cfg


def _rs_for(C: pd.DataFrame, V: pd.DataFrame, asof: str, tickers: list[str]) -> pd.DataFrame:
    """rs_sec_63 / rs_sec_252 / rs_mkt_63 / rs_sub_63 on `asof`, as rs_study.py (the stock left
    out of its own group), from the last 300 sessions only."""
    import glob
    import rs_study as RSS
    import sectors
    Cs, Vs = C.iloc[-300:], V.iloc[-300:]
    E = sectors.eligible(Cs, Vs)
    R = sectors.daily_returns(Cs)
    g = sectors.groups().drop_duplicates("ticker").set_index("ticker")
    ls = np.log1p(R)
    rel_sec = ls - np.log1p(RSS._loo_group(R, E, g["sector"].reindex(Cs.columns), sectors.MIN_MEMBERS))
    rel_sub = ls - np.log1p(RSS._loo_group(R, E, g["subind"].reindex(Cs.columns), sectors.MIN_MEMBERS))
    e = pd.concat(pd.read_parquet(p, columns=["ticker", "date", "close"])
                  for p in sorted(glob.glob(str(config.BARS_ETF / "*.parquet"))))
    e["date"] = e.date.astype(str).str[:10]
    spy = e[e.ticker == "SPY"].drop_duplicates("date").set_index("date").close.sort_index()
    rel_mkt = ls.sub(np.log1p(spy.pct_change(fill_method=None).reindex(Cs.index)), axis=0)

    def win(A: pd.DataFrame, w: int) -> pd.Series:
        x = A.iloc[-w:]
        return x.sum(min_count=int(0.8 * w)).reindex(tickers)
    return pd.DataFrame({"rs_sec_63": win(rel_sec, 63), "rs_sec_252": win(rel_sec, 252),
                         "rs_mkt_63": win(rel_mkt, 63), "rs_sub_63": win(rel_sub, 63)})


def candidates(asof: str) -> pd.DataFrame:
    """Every liquid name at or near support on `asof` (the Gainers-at-support universe), with
    every column the shortlist and its page use, and the trade plan of item 1's chosen rule."""
    import big_movers_study as BM
    import gainers_at_support as GA
    import sectors
    import store
    _, D = GA.build(asof)
    D = D.reset_index(drop=True)
    tk = D.ticker.tolist()
    sc = pd.read_parquet(config.DATA / "scores" / f"{asof[:7]}.parquet")
    sc = sc[sc.session.astype(str).str[:10] == asof]
    piv = sc.pivot_table(index="ticker", columns="metric", values="value", aggfunc="last")
    for m in ("combo_h60", "growth_score", "insider_score", "sent_mean_30d", "mktcap"):
        D[m] = D.ticker.map(piv[m]) if m in piv.columns else np.nan
    C, V = sectors.load_wide()
    C, V = C.loc[:asof], V.loc[:asof]
    r63 = C.iloc[-64:][tk].pct_change(fill_method=None).iloc[1:]
    D["vol63"] = r63.std(ddof=1).reindex(tk).to_numpy() * np.where(r63.notna().sum().reindex(tk) >= 50, 1, np.nan)
    rc, dips, rec = [], [], []
    for t in tk:
        c = C[t].to_numpy(dtype=float) if t in C.columns else np.array([])
        i = len(c) - 1
        ra = np.asarray(BM.rallies_of(c.tolist()), dtype=np.int64) if len(c) >= BM.YEAR else np.array([], dtype=np.int64)
        rc.append(float(((ra > i - BM.YEAR) & (ra <= i)).sum()) if len(c) >= BM.YEAR else np.nan)
        if len(c) >= BM.YEAR:
            dr, ok = BM.dips_of(c)
            m = (dr > i - BM.YEAR) & (dr <= i - BM.DIP_BACK)
            dips.append(float(m.sum()))
            rec.append(float(ok[m].mean()) if m.sum() >= 2 else np.nan)
        else:
            dips.append(np.nan)
            rec.append(np.nan)
    D["rally_count"], D["dips"], D["recovery_rate"] = rc, dips, rec
    lo = str((pd.Timestamp(asof) - pd.Timedelta(days=45)).date())
    b = store.read(interval="1d", start=lo, end=asof, tickers=tk, columns=["ticker", "date", "high", "low", "close"])
    b["date"] = b.date.astype(str).str[:10]
    b = b.sort_values(["ticker", "date"])
    b["pc"] = b.groupby("ticker").close.shift(1)
    b["tr"] = np.fmax(b.high, b.pc) - np.fmin(b.low, b.pc)
    atr = b.groupby("ticker").tail(20).groupby("ticker").agg(atr=("tr", "mean"), n=("tr", "count"))
    D["atr_pct"] = (D.ticker.map(atr.atr.where(atr.n >= 15)) / D.price)
    D = D.join(_rs_for(C, V, asof, tk), on="ticker")
    D["limit"] = D.level * (1 + LIMIT_UP)
    D["target"] = D.limit * (1 + TGT)
    D["stop"] = D.limit * (1 - STP)
    D["target_alt"] = D.limit * (1 + TGT_ALT)
    D["stop_alt"] = D.limit * (1 - STP_ALT)
    return D


def build(asof: str, verbose: bool = True) -> dict:
    cfg = recipes()
    D = candidates(asof)
    f = cfg.get("filters", {})
    pool = D
    if f.get("exclude_warnings"):
        pool = pool[pool.warning.fillna("") == ""]
    if "max_above_level_pct" in f:
        pool = pool[pool.above_level_pct <= float(f["max_above_level_pct"])]
    lists = {}
    for r in cfg["recipes"]:
        col = r["rank_by"]
        x = pool[pool[col].notna()].sort_values([col, "ticker"], ascending=[bool(r["ascending"]), True])
        lists[r["name"]] = x.head(int(cfg["max_n"])).assign(rank=lambda d: np.arange(1, len(d) + 1))
    out = DIR / asof
    out.mkdir(parents=True, exist_ok=True)
    D.to_parquet(out / "candidates.parquet", index=False)
    for name, x in lists.items():
        x.to_csv(out / f"{name}.csv", index=False)
    led = pd.concat([x.assign(date=asof, recipe=name)[["date", "recipe", "rank", "ticker", "price", "level",
                                                        "limit", "atr_pct"]]
                     for name, x in lists.items()], ignore_index=True)
    old = pd.read_parquet(LEDGER) if LEDGER.exists() else pd.DataFrame(columns=led.columns)
    old = old[old.date != asof]
    pd.concat([old, led], ignore_index=True).to_parquet(LEDGER, index=False)
    res = {"asof": asof, "candidates": int(len(D)), "pool": int(len(pool)),
           "lists": {k: int(len(v)) for k, v in lists.items()}}
    if verbose:
        print(f"shortlist {asof}: {len(D):,} liquid names at/near support, {len(pool):,} after filters; "
              f"{len(lists)} recipes -> {out}", flush=True)
    return res


def evaluate(verbose: bool = True) -> pd.DataFrame:
    """Score every ledger entry whose 3 limit days + 10 hold days have passed, under item 1's
    chosen rule and the default (open, +5% / -5%), with trade_rules_study's own engine."""
    import store
    import trade_rules_study as TR
    if not LEDGER.exists():
        return pd.DataFrame()
    L = pd.read_parquet(LEDGER)
    done = pd.read_parquet(OUTCOMES) if OUTCOMES.exists() else pd.DataFrame(columns=["date", "recipe", "ticker"])
    have = set(zip(done.date, done.recipe, done.ticker))
    todo = L[[(d, r, t) not in have for d, r, t in zip(L.date, L.recipe, L.ticker)]]
    if todo.empty:
        return done
    axis = TR._axis()
    pos = {d: i for i, d in enumerate(axis)}
    need = LIMIT_DAYS + HOLD + 2
    rows = []
    for D, g in todo.groupby("date"):
        i = pos.get(D)
        if i is None or i + need >= len(axis):
            continue                                             # its window has not passed yet
        u = g.drop_duplicates("ticker")
        sess = axis[i + 1:i + 1 + need]
        b = store.read(interval="1d", start=sess[0], end=sess[-1], tickers=u.ticker.tolist(),
                       columns=["ticker", "date", "open", "high", "low", "close"])
        b["date"] = b.date.astype(str).str[:10]
        P = {c: b.pivot_table(index="date", columns="ticker", values=c).reindex(index=sess, columns=u.ticker)
             .to_numpy(dtype=float) for c in ("open", "high", "low", "close")}
        E = TR.entries(P, u.level.to_numpy(dtype=float))
        a = u.atr_pct.to_numpy(dtype=float)
        out = {}
        e_px, e_row, start, gap_from, intra, ok0 = E["limit"]
        for tag, tg, sp in (("limit", TGT, STP), ("limit55", TGT_ALT, STP_ALT)):
            px, kk, hh = TR.simulate(P, start, gap_from, intra, e_row, e_px * (1 + tg), e_px * (1 - sp),
                                     e_row + HOLD - 1, ok0)
            out[f"{tag}_ret"] = np.where(ok0 & np.isfinite(px), px / e_px - 1 - COST, np.nan)
            out[f"{tag}_kind"] = np.where(ok0, kk, 0)
        out["limit_filled"] = ok0
        e_px, e_row, start, gap_from, intra, ok0 = E["open"]
        px, kk, hh = TR.simulate(P, start, gap_from, intra, e_row, e_px * 1.05, e_px * 0.95, e_row + HOLD - 1, ok0)
        out["open_ret"] = np.where(ok0 & np.isfinite(px), px / e_px - 1 - COST, np.nan)
        out["open_kind"] = np.where(ok0, kk, 0)
        res = pd.DataFrame({"ticker": u.ticker.to_numpy(), **out})
        rows.append(g[["date", "recipe", "rank", "ticker"]].merge(res, on="ticker", how="left"))
    if rows:
        done = pd.concat([done] + rows, ignore_index=True)
        done.to_parquet(OUTCOMES, index=False)
    if verbose:
        print(f"shortlist outcomes: {len(done):,} scored entries", flush=True)
    return done


# ------------------------------------------------------------------------- the page
COLS = [  # (key, header, kind) -- kind: t text, p price, pc percent (x100), n number, i int
    ("rank", "#", "i"), ("ticker", "Ticker", "t"), ("name", "Name", "t"), ("sector", "Sector", "t"),
    ("subind", "Sub-industry", "t"), ("price", "Price", "p"), ("level", "Level", "p"),
    ("above_level_pct", "Above level %", "n"), ("limit", "Buy limit", "p"), ("target", "Target +3%", "p"),
    ("stop", "Stop -8%", "p"), ("target_alt", "Alt target +5%", "p"), ("stop_alt", "Alt stop -5%", "p"), ("atr_pct", "ATR %", "pc"), ("fund", "Fund", "n"), ("combo_h60", "Combo 3m", "n"),
    ("combo_h20", "Combo 1m", "n"), ("gain_days", "Gainer days", "i"), ("rally_count", "Rallies", "i"),
    ("recovery_rate", "Dips won back", "pc"), ("rs_sec_63", "vs sector 3m", "pc"),
    ("rs_sub_63", "vs sub-ind 3m", "pc"), ("rs_mkt_63", "vs market 3m", "pc"), ("earn_sessions", "Earnings in (sessions)", "i"),
    ("warning", "Warning", "t"), ("insider", "Insider", "t"), ("narrative", "Narrative (group)", "t"),
]

PAGE_JS = r"""
const D = JSON.parse(document.getElementById('sl-data').textContent);
const sel = document.getElementById('sl-recipe'), num = document.getElementById('sl-n'),
      hideE = document.getElementById('sl-earn'), tb = document.getElementById('sl-body'),
      ev = document.getElementById('sl-evidence'), cnt = document.getElementById('sl-count');
D.recipes.forEach(r => { const o = document.createElement('option'); o.value = r.name;
  o.textContent = r.label + (r.name === D.default ? ' (best tested)' : ''); sel.appendChild(o); });
sel.value = D.default; num.value = D.n; num.max = D.max_n;
function fmt(v, k) { if (v === null || v === undefined || v === '' || (typeof v === 'number' && isNaN(v))) return '—';
  if (k === 'p') return Number(v).toFixed(2); if (k === 'pc') return (Number(v) * 100).toFixed(1) + '%';
  if (k === 'n') return Number(v).toFixed(1); if (k === 'i') return String(Math.round(Number(v))); return String(v); }
function draw() {
  const r = D.recipes.find(x => x.name === sel.value);
  ev.textContent = r.evidence;
  let rows = r.rows;
  if (hideE.checked) rows = rows.filter(x => !(x.earn_sessions !== null && x.earn_sessions <= D.earn_soon));
  const n = Math.max(1, Math.min(D.max_n, parseInt(num.value || D.n, 10)));
  rows = rows.slice(0, n);
  cnt.textContent = rows.length + ' of ' + r.rows.length + ' shown';
  tb.textContent = '';
  rows.forEach((x, i) => { const tr = document.createElement('tr');
    D.cols.forEach(([k, h, kind]) => { const td = document.createElement('td');
      if (kind !== 't') td.className = 'n';
      if (k === 'ticker') { const a = document.createElement('a'); a.href = '../stock/' + encodeURIComponent(x.ticker) + '.html';
        a.textContent = x.ticker; td.appendChild(a); }
      else if (k === 'rank') td.textContent = String(i + 1);
      else td.textContent = fmt(x[k], kind);
      tr.appendChild(td); });
    tb.appendChild(tr); });
}
sel.onchange = draw; num.oninput = draw; hideE.onchange = draw; draw();
"""


def _regime(asof: str) -> list[str]:
    import trade_rules_study as TR
    out = []
    try:
        r = TR.regimes([asof]).iloc[0]
        m = pd.read_parquet(config.DATA / "_macro.parquet")
        m["session"] = m.session.astype(str).str[:10]
        vix = m[m.session <= asof].vix.dropna()
        out.append(f"S&amp;P {'above' if r.m2 == 1 else 'below'} its 50-day, {'above' if r.m1 == 1 else 'below'} its 200-day")
        if len(vix):
            out.append(f"VIX {vix.iloc[-1]:.1f} ({'20 or higher' if vix.iloc[-1] >= 20 else 'under 20'})")
    except Exception as exc:                                     # noqa: BLE001
        out.append(f"market regime unavailable ({type(exc).__name__})")
    return out


def _forward_table(cfg: dict) -> str:
    import ui
    if not OUTCOMES.exists():
        return "<p class='muted'>No entry scored yet: each needs its 3 limit days and 10 hold days to pass.</p>"
    o = pd.read_parquet(OUTCOMES)
    o = o[o["rank"] <= int(cfg["n"])]
    if o.empty:
        return "<p class='muted'>No entry scored yet.</p>"
    rows = []
    for r in cfg["recipes"]:
        x = o[o.recipe == r["name"]]
        if x.empty:
            continue
        lim = x.limit_ret.dropna()
        rows.append(f"<tr><td>{ui.esc(r['label'])}</td><td class='n'>{x.date.nunique()}</td><td class='n'>{len(x)}</td>"
                    f"<td class='n'>{x.limit_filled.mean() * 100:.0f}%</td>"
                    f"<td class='n'>{lim.mean() * 100:+.2f}%</td>"
                    f"<td class='n'>{(x.limit_kind == 1).sum() / max(len(lim), 1) * 100:.0f}% / {(x.limit_kind == -1).sum() / max(len(lim), 1) * 100:.0f}%</td>"
                    f"<td class='n'>{x.limit55_ret.dropna().mean() * 100:+.2f}%</td>"
                    f"<td class='n'>{x.open_ret.dropna().mean() * 100:+.2f}%</td></tr>")
    return ("<table class='sl'><thead><tr><th>Ranking</th><th class='n'>Days</th><th class='n'>Entries</th>"
            "<th class='n'>Limit filled</th><th class='n'>Limit +3/-8, mean a trade</th><th class='n'>Target / stop hit</th>"
            "<th class='n'>Limit +5/-5</th><th class='n'>Open +5/-5</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
            "<p class='muted'>Net of 0.10% a round trip. Top "
            f"{int(cfg['n'])} of each ranking per day; a day counts once its 13-session window has passed. Scored on "
            "daily bars (the fill day pessimistic: its stop counts, its target does not), so a little below the "
            "30-minute backtest.</p>")


def render(asof: str) -> Path:
    import news_events as NE
    import ui
    cfg = recipes()
    d = DIR / asof
    D = pd.read_parquet(d / "candidates.parquet")
    narr = {}
    try:
        import narrative
        nl = narrative.latest(asof)
        narr = {(r.level, r.group): f"{r.now:+d} now / {r.m3:+d} 3m" for r in nl.itertuples()}
    except Exception:                                            # noqa: BLE001
        pass
    keys = [k for k, _, _ in COLS]
    recs = []
    for r in cfg["recipes"]:
        p = d / f"{r['name']}.csv"
        x = pd.read_csv(p) if p.exists() else pd.DataFrame(columns=keys)
        x["narrative"] = [narr.get(("subind", s)) or narr.get(("sector", c)) for s, c in zip(x.get("subind", []), x.get("sector", []))]
        rows = [{k: (None if (isinstance(v, float) and not np.isfinite(v)) else v) for k, v in zip(keys, vals)}
                for vals in x.reindex(columns=keys).itertuples(index=False, name=None)]
        recs.append({"name": r["name"], "label": r["label"], "evidence": r["evidence"], "rows": rows})
    data = {"default": cfg["default"], "n": int(cfg["n"]), "max_n": int(cfg["max_n"]), "earn_soon": NE.EARN_SOON,
            "cols": COLS, "recipes": recs}
    blob = json.dumps(data, default=str).replace("</", "<\\/")
    head = ("<style>.sl td.n,.sl th.n{text-align:right;font-variant-numeric:tabular-nums}"
            ".sl{font-size:13px}.ctl{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin:4px 0 10px}"
            ".ctl input[type=number]{width:64px}</style>")
    th = "".join(f"<th{' class=n' if kind != 't' else ''}>{ui.esc(h)}</th>" for _, h, kind in COLS)
    market = " &middot; ".join(_regime(asof))
    body = (ui.card("Market today", f"<p>{market}</p><p class='muted'>Measured 2018-2026: trading only when the "
                    "S&amp;P is above its averages did NOT help these trades; a VIX of 20 or more helped in both "
                    "periods but not reliably. Context, not a filter.</p>")
            + ui.card("Shortlist", "<div class='ctl'><label>Ranking <select id='sl-recipe'></select></label>"
                      "<label>Show <input id='sl-n' type='number' min='1'></label>"
                      f"<label><input id='sl-earn' type='checkbox'> hide earnings within {NE.EARN_SOON} sessions</label>"
                      "<span id='sl-count' class='muted'></span></div>"
                      "<p id='sl-evidence' class='muted'></p>"
                      f"<div class='scroll'><table class='sl'><thead><tr>{th}</tr></thead><tbody id='sl-body'></tbody></table></div>")
            + ui.card("How each ranking has done since tracking began", _forward_table(cfg))
            + ui.card("How to read this",
                      "<p><b>Trade plan</b> (item 1's rule, chosen on 2018-21 dates with 30-minute bars): a buy limit at "
                      "the level + 0.5%, live for 3 sessions; target +3% and stop -8% from the fill; out after 10 "
                      "sessions. Measured on 2022-26 with fills that must trade 0.2% through the limit and the fill day "
                      "in 30-minute order: +0.36% a trade net of costs (t 2.42), positive in 8 of 9 years; about 54% of "
                      "orders fill. The stop is wider than the target: it wins often and loses more when it loses. "
                      "The alternative +5% / -5% did about the same (+0.38%, t 2.37). Buying at the next open earned "
                      "about nothing after costs; buying an hour after the open made no difference.</p>"
                      "<p><b>Rankings</b>: the default is the one that tested best; every ranking is built and tracked "
                      "forward each night (data/shortlist/), so the others keep competing. Change the default, size and "
                      "filters in shortlist_recipes.json.</p>"
                      "<p class='muted'>Research screens, not investment advice. Narrative: the weekly AI view of the "
                      "stock's sub-industry (else its sector), shown once the first weekly run has been checked.</p>"))
    html = ui.page("Shortlist", body, subtitle=f"as of {asof} &middot; {len(D):,} liquid names at or near support",
                   active="shortlist", depth=1, head=head,
                   script="") .replace("</body>", f"<script type='application/json' id='sl-data'>{blob}</script>"
                                                  f"<script>{PAGE_JS}</script></body>")
    PAGE.mkdir(parents=True, exist_ok=True)
    p = PAGE / "latest.html"
    p.write_text(html, encoding="utf-8")
    return p


def main() -> int:
    if "--build" in sys.argv:
        i = sys.argv.index("--build")
        asof = sys.argv[i + 1] if len(sys.argv) > i + 1 and not sys.argv[i + 1].startswith("-") else None
        if asof is None:
            import calendar_us
            asof = calendar_us.last_closed_session()
        build(asof)
        evaluate()
        print(f"page: {render(asof)}")
        return 0
    if "--backtest" in sys.argv:
        s = backtest()
        OUT_BT.write_text(json.dumps(s, indent=1, default=str), encoding="utf-8")
        print(f"wrote {OUT_BT.name}; best on TRAIN: {s['best_on_train']}")
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
