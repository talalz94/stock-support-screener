#!/usr/bin/env python
"""Sector rotation: do sector and sub-industry booms persist, follow one another, or repeat by
season -- and does any of it help the shortlist's trade?

Asked 2026-11-12: "is there sector/industry/sub-industry wise money rotation, like we say AI/Tech
stocks boom and then we saw bio/health stocks boom ... so there may be some edge to exploit
certain good/short-listed companies at the right time?"

DATA, all stored, nothing fetched:
  data/_sector_panel_{sector,subind}.parquet (sector_study.py, 2026-11-08, on the corrected map):
    each group on the first session of each month, 2017-09..2026-10, with its trailing excess
    over the equal-weight universe (rs21/63/126/252), breadth200, the median fund score, and
    fwd21 = its NEXT 21 sessions minus the universe -- the monthly outcome (months do not overlap).
  data/_trade_rules_trades.parquet + data/_trade_rules.npz (trade_rules_study.py): 175,937
    at-support name-dates on 217 dates (2018-01..2026-08, 10 sessions apart) with every rule's
    net trade return.

DESIGN, fixed before any result was seen (2026-11-12). Split as everywhere in this project:
TRAIN before 2022-01-01 (halves split at 2020-01-01), TEST after; KEPT = both TRAIN halves and
TEST of the same sign and TEST |t| >= 2.
  R1 PERSISTENCE ("a boom"): the Spearman correlation across groups between each month's excess
     and the excess L months later, L = 1..12; mean over months, t across months. And how long a
     group stays in the top third of its month: consecutive months, against the same groups with
     their months shuffled (1,000 shuffles) -- runs longer than shuffled ones are real booms.
  R2 SEQUENCE ("Tech, then Bio"): for every ordered pair of groups (A, B) and lag k in
     {1, 2, 3, 6} months, the correlation of A's monthly excess with B's excess k months later.
     Pairs with |t| >= 2 on TRAIN, then the same pairs on TEST: the share keeping their sign
     (chance: 50%) and how many reach |t| >= 2 again (chance: about 2.5% in the same sign). Then
     the pairs as a forecast: each group's TEST month predicted from the TRAIN pairs pointing at
     it, its IC across groups against the plain own-momentum forecast (rs252). The user's own
     example pairs reported whatever they show.
  R3 SEASON: not re-tested; season_study.py (2026-10-14) found none at any level (sectors IC
     -0.013, t -0.37; sub-industries -0.027, t -0.95; stocks +0.002, t +0.16, 72 months).
  R4 THE SHORTLIST'S TRADE: each at-support name-date takes its group's state on the newest panel
     month at or before its date (point in time): the tercile among groups that month of rs21,
     rs63, rs252, breadth200 and the sector score (breadth200 + fund ranks, as sector_score.py)
     -- sector level -- and of rs63, rs252, breadth200 at the sub-industry level; plus
     "improving" (bottom half on rs252 and top half on rs21: weak for a year, turning). Per date:
     the mean trade in the top third minus the bottom third, and the top third (or "improving")
     minus every at-support name -- the filter against the plain rule -- for the shortlist's plan
     (a limit at the level +0.5% for 3 sessions, +3% / -8%, 10 sessions) and the default (next
     open, +5% / -5%, 10 sessions). t across dates. 8 states x 2 comparisons x 2 rules + 2 x 2
     = 36 comparisons: about 2 pass |t| >= 2 by chance.
  R5 THE ROTATION CHART'S QUADRANTS (added the same day, after R1-R4's results and before this
     one's, for the page rotation_page.py draws): each group's quadrant on a month's first session
     from its rs252 (across) and rs21 (up), above or below the universe -- leading, weakening,
     lagging, improving. Per month, the mean next-month excess (fwd21) of each quadrant's groups;
     leading minus lagging, improving minus weakening, and each quadrant minus the month's average
     group. t across months. 2 levels x 6 = 12 comparisons, about 0.5 by chance.
LIMITS: about 19 sectors and 45 sub-industries are small cross-sections, so power is low; 110
months; today's map applied to the past; survivorship as everywhere here; a group needs 8 members.

Writes data/_rotation_study.json.  Run: python rotation_study.py
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd

import config

config.safe_console()

SPLIT, HALF = "2022-01-01", "2020-01-01"
LAGS = range(1, 13)
PAIR_LAGS = (1, 2, 3, 6)
SHUFFLES = 1000
OUT = config.DATA / "_rotation_study.json"
PANEL = {lv: config.DATA / f"_sector_panel_{lv}.parquet" for lv in ("sector", "subind")}
PLAN_RULE, DEFAULT_RULE = "limit|f3_8_h10", "open|f5_5_h10"
EXAMPLES = [("Semiconductors", "Biotech"), ("Semiconductors", "Healthcare"),
            ("Technology", "Biotech"), ("Technology", "Healthcare"),
            ("Biotech", "Semiconductors"), ("Healthcare", "Technology")]
RNG = np.random.default_rng(20261112)


def log(m: str) -> None:
    print(f"  {m}", flush=True)


def tstat(a) -> float | None:
    a = np.asarray([x for x in a if x is not None and np.isfinite(x)], dtype=float)
    if len(a) < 5 or a.std(ddof=1) == 0:
        return None
    return round(float(a.mean() / (a.std(ddof=1) / np.sqrt(len(a)))), 2)


def split_stats(s: pd.Series) -> dict:
    """A per-month (or per-date) series: mean and t on TRAIN, its halves and TEST, and KEPT."""
    s = s.dropna()
    tr, te = s[s.index < SPLIT], s[s.index >= SPLIT]
    h1, h2 = tr[tr.index < HALF], tr[tr.index >= HALF]
    out = {"n": int(len(s)), "all": round(float(s.mean()), 4) if len(s) else None, "all_t": tstat(s),
           "train": round(float(tr.mean()), 4) if len(tr) else None, "train_t": tstat(tr),
           "h1": round(float(h1.mean()), 4) if len(h1) else None, "h2": round(float(h2.mean()), 4) if len(h2) else None,
           "test": round(float(te.mean()), 4) if len(te) else None, "test_t": tstat(te), "test_n": int(len(te))}
    sg = [np.sign(out[k] or 0) for k in ("h1", "h2", "test")]
    out["kept"] = bool(sg[0] != 0 and sg[0] == sg[1] == sg[2] and abs(out["test_t"] or 0) >= 2)
    return out


def load_panel(level: str) -> pd.DataFrame:
    p = pd.read_parquet(PANEL[level])
    p["date"] = p["date"].astype(str)
    return p


# ============================================================ R1 persistence
def lag_profile(X: pd.DataFrame, min_groups: int = 8) -> dict:
    """X: month x group monthly excess. Spearman across groups between month m-L and month m."""
    R = X.rank(axis=1)
    out = {}
    for L in LAGS:
        ics = {}
        for i in range(L, len(X)):
            a, b = R.iloc[i - L], R.iloc[i]
            ok = a.notna() & b.notna()
            if ok.sum() < min_groups:
                continue
            ics[X.index[i]] = float(np.corrcoef(a[ok].rank(), b[ok].rank())[0, 1])
        out[str(L)] = split_stats(pd.Series(ics))
    return out


def run_stats(top: pd.DataFrame) -> tuple[float, float]:
    """Mean length of consecutive top-third runs, and P(top next month | top this month)."""
    lengths, stay, n_top = [], 0, 0
    v = top.to_numpy(dtype=float)
    for j in range(v.shape[1]):
        col = v[:, j]
        run = 0
        for i in range(len(col)):
            if col[i] == 1:
                run += 1
            elif run:
                lengths.append(run)
                run = 0
            if i + 1 < len(col) and col[i] == 1 and np.isfinite(col[i + 1]):
                n_top += 1
                stay += col[i + 1] == 1
        if run:
            lengths.append(run)
    return (float(np.mean(lengths)) if lengths else np.nan), (stay / n_top if n_top else np.nan)


def persistence(X: pd.DataFrame) -> dict:
    """Top-third runs observed against the same groups with months shuffled within each group."""
    pct = X.rank(axis=1, pct=True)
    top = (pct > 2 / 3).astype(float).where(X.notna())
    obs_len, obs_stay = run_stats(top)
    sims_len, sims_stay = [], []
    for _ in range(SHUFFLES):
        Xs = X.apply(lambda c: pd.Series(RNG.permutation(c.to_numpy()), index=c.index))
        ps = Xs.rank(axis=1, pct=True)
        ts = (ps > 2 / 3).astype(float).where(Xs.notna())
        a, b = run_stats(ts)
        sims_len.append(a)
        sims_stay.append(b)
    sims_len, sims_stay = np.array(sims_len), np.array(sims_stay)
    return {"mean_run_months": round(obs_len, 2), "shuffled_mean_run": round(float(np.nanmean(sims_len)), 2),
            "p_run": round(float((sims_len >= obs_len).mean()), 4),
            "stay_top_pct": round(obs_stay * 100, 1), "shuffled_stay_top_pct": round(float(np.nanmean(sims_stay)) * 100, 1),
            "p_stay": round(float((sims_stay >= obs_stay).mean()), 4)}


def longest_booms(X: pd.DataFrame, k: int = 8) -> list[dict]:
    """The longest top-third runs: which group, from when, how long, and its excess over the run."""
    pct = X.rank(axis=1, pct=True)
    top = (pct > 2 / 3) & X.notna()
    out = []
    for g in X.columns:
        run_start, run = None, 0
        col = top[g].to_numpy()
        for i, on in enumerate(list(col) + [False]):
            if on:
                run_start = i if run == 0 else run_start
                run += 1
            elif run:
                seg = X[g].iloc[run_start:run_start + run]
                out.append({"group": g, "from": X.index[run_start][:7], "to": X.index[run_start + run - 1][:7],
                            "months": run, "excess_pct": round(float(np.expm1(np.log1p(seg).sum()) * 100), 1)})
                run = 0
    return sorted(out, key=lambda r: (-r["months"], -r["excess_pct"]))[:k]


# ============================================================ R2 sequence
def _corr_t(a: np.ndarray, b: np.ndarray) -> tuple[float, float, int]:
    ok = np.isfinite(a) & np.isfinite(b)
    n = int(ok.sum())
    if n < 12:
        return np.nan, np.nan, n
    r = float(np.corrcoef(a[ok], b[ok])[0, 1])
    t = r * np.sqrt((n - 2) / max(1e-12, 1 - r * r))
    return r, t, n


def pairs(X: pd.DataFrame) -> dict:
    """Every ordered pair and lag on TRAIN and TEST (the OUTCOME month decides the side)."""
    months = np.array(X.index)
    G = list(X.columns)
    V = X.to_numpy(dtype=float)
    res = {}
    for k in PAIR_LAGS:
        lead, follow = V[:-k], V[k:]
        out_month = months[k:]
        tr, te = out_month < SPLIT, out_month >= SPLIT
        rows = []
        for i, a in enumerate(G):
            for j, b in enumerate(G):
                if i == j:
                    continue
                r1, t1, n1 = _corr_t(lead[tr, i], follow[tr, j])
                r2, t2, n2 = _corr_t(lead[te, i], follow[te, j])
                rows.append((a, b, r1, t1, n1, r2, t2, n2))
        df = pd.DataFrame(rows, columns=["lead", "follow", "r_train", "t_train", "n_train", "r_test", "t_test", "n_test"])
        sel = df[df.t_train.abs() >= 2]
        same = (np.sign(sel.r_train) == np.sign(sel.r_test))
        rep = same & (sel.t_test.abs() >= 2)
        res[str(k)] = {"pairs": int(len(df)), "train_t2": int(len(sel)),
                       "expected_train_t2_by_chance": round(0.0455 * len(df), 1),
                       "test_same_sign_pct": round(float(same.mean() * 100), 1) if len(sel) else None,
                       "test_replicated_t2": int(rep.sum()),
                       "expected_replicated_by_chance": round(0.0228 * len(sel), 1),
                       "replicated": sel[rep][["lead", "follow", "r_train", "t_train", "r_test", "t_test"]]
                                     .round(3).to_dict("records")[:12],
                       "_df": df}
    return res


def pair_forecast(X: pd.DataFrame, P: dict, rs252: pd.DataFrame) -> dict:
    """TEST months: each group predicted from the TRAIN-selected pairs pointing at it (sign x the
    leader's excess k months earlier, averaged), IC across groups; against rs252 on the same months."""
    G = list(X.columns)
    out = {}
    Z = (X - X.mean(axis=1).values[:, None]) / X.std(axis=1).values[:, None]
    for k, r in P.items():
        k = int(k)
        sel = r["_df"][r["_df"].t_train.abs() >= 2]
        ics, base = {}, {}
        for i in range(k, len(X)):
            m = X.index[i]
            if m < SPLIT:
                continue
            pred = {}
            for b in G:
                s = sel[sel.follow == b]
                if s.empty:
                    continue
                v = [np.sign(rt) * Z.iloc[i - k][a] for a, rt in zip(s.lead, s.r_train) if np.isfinite(Z.iloc[i - k][a])]
                if v:
                    pred[b] = float(np.mean(v))
            y = X.iloc[i]
            pr = pd.Series(pred)
            ok = pr.index[pr.notna() & y.reindex(pr.index).notna()]
            if len(ok) >= 8:
                ics[m] = float(pr[ok].rank().corr(y[ok].rank()))
            if m in rs252.index:
                rb = rs252.loc[m]
                okb = rb.index[rb.notna() & y.reindex(rb.index).notna()]
                if len(okb) >= 8:
                    base[m] = float(rb[okb].rank().corr(y[okb].rank()))
        a, b = pd.Series(ics), pd.Series(base)
        out[str(k)] = {"test_months": int(len(a)), "pairs_ic": round(float(a.mean()), 3) if len(a) else None,
                       "pairs_ic_t": tstat(a), "rs252_ic": round(float(b.mean()), 3) if len(b) else None,
                       "rs252_ic_t": tstat(b),
                       "pairs_minus_rs252_t": tstat((a - b.reindex(a.index)).dropna())}
    return out


# ============================================================ R5 the chart's quadrants
def quadrants(p: pd.DataFrame) -> dict:
    """Next-month excess by rotation quadrant (rs252 across, rs21 up), per month."""
    q = p.dropna(subset=["rs252", "rs21", "fwd21"]).copy()
    q["quad"] = np.select([(q.rs252 >= 0) & (q.rs21 >= 0), (q.rs252 >= 0) & (q.rs21 < 0),
                           (q.rs252 < 0) & (q.rs21 < 0)], ["leading", "weakening", "lagging"], "improving")
    by = q.groupby(["date", "quad"]).fwd21.mean().unstack()
    avg = q.groupby("date").fwd21.mean()
    out = {"months": int(len(by)), "share_pct": {k: round(float((q.quad == k).mean() * 100), 1)
                                                 for k in ("leading", "weakening", "lagging", "improving")}}
    for a, b in (("leading", "lagging"), ("improving", "weakening")):
        if a in by and b in by:
            out[f"{a}_minus_{b}"] = split_stats((by[a] - by[b]).dropna())
    for k in ("leading", "weakening", "lagging", "improving"):
        if k in by:
            out[f"{k}_minus_avg"] = split_stats((by[k] - avg).dropna())
    return out


# ============================================================ R4 the shortlist's trade
def trade_states(T: pd.DataFrame, panels: dict) -> pd.DataFrame:
    """Per at-support name-date: its sector's and sub-industry's tercile on the newest panel
    month at or before its date, per state."""
    import sectors
    g = sectors.groups().drop_duplicates("ticker").set_index("ticker")
    T = T.assign(subind=T.ticker.map(g["subind"]))
    out = pd.DataFrame(index=T.index)
    for level, key in (("sector", "sector"), ("subind", "subind")):
        p = panels[level].copy()
        comps = ["rs21", "rs63", "rs252", "breadth200"] if level == "sector" else ["rs63", "rs252", "breadth200"]
        for c in comps + ["fund"]:
            p[c + "_pct"] = p.groupby("date")[c].rank(pct=True)
        if level == "sector":
            p["score_pct"] = p[["breadth200_pct", "fund_pct"]].mean(axis=1, skipna=True)
            p["score_pct"] = p.groupby("date")["score_pct"].rank(pct=True)
            comps = comps + ["score"]
            p["improving"] = (p.rs252_pct <= 0.5) & (p.rs21_pct > 0.5)
        months = np.array(sorted(p.date.unique()))
        idx = np.searchsorted(months, T.date.astype(str).to_numpy(), side="right") - 1
        keys = pd.DataFrame({"date": np.where(idx >= 0, months[np.maximum(idx, 0)], None),
                             "group": T[key].to_numpy()}, index=T.index)
        cols = [c + "_pct" for c in comps] + (["improving"] if level == "sector" else [])
        j = keys.reset_index().merge(p[["date", "group"] + cols], on=["date", "group"], how="left").set_index("index")
        j = j.reindex(T.index)
        for c in comps:
            v = j[c + "_pct"].to_numpy(dtype=float)
            out[f"{level}:{c}"] = np.where(np.isnan(v), np.nan, np.where(v > 2 / 3, 3, np.where(v <= 1 / 3, 1, 2)))
        if level == "sector":
            out["sector:improving"] = j["improving"].map({True: 1.0, False: 0.0}).to_numpy(dtype=float)
    return out


def trade_tests(T: pd.DataFrame, ret: np.ndarray, rules: list[str], S: pd.DataFrame) -> dict:
    import trade_rules_study as TR
    ri = {r: k for k, r in enumerate(rules)}
    out = {}
    for rule in (PLAN_RULE, DEFAULT_RULE):
        r = ret[:, ri[rule]]
        base = pd.Series(r).groupby(T.date.to_numpy()).mean()
        res = {"all": TR.summary(base)}
        for col in S.columns:
            st = S[col].to_numpy(dtype=float)
            if col.endswith("improving"):
                m = pd.Series(np.where(st == 1, r, np.nan)).groupby(T.date.to_numpy()).mean()
                res[col] = {"vs_all": TR.summary(m - base), "share_pct": round(float(np.nanmean(st == 1) * 100), 1)}
                continue
            top = pd.Series(np.where(st == 3, r, np.nan)).groupby(T.date.to_numpy()).mean()
            bot = pd.Series(np.where(st == 1, r, np.nan)).groupby(T.date.to_numpy()).mean()
            res[col] = {"top_minus_bottom": TR.summary(top - bot), "top_minus_all": TR.summary(top - base),
                        "top_mean": TR.summary(top), "bottom_mean": TR.summary(bot),
                        "covered_pct": round(float(np.isfinite(st).mean() * 100), 1)}
        out[rule] = res
    return out


# ============================================================ main
def main() -> int:
    t0 = time.time()
    out = {"measured_on": datetime.now().astimezone().isoformat(timespec="seconds"),   # local, with its offset
           "definitions": __doc__.strip(), "levels": {}}
    panels = {lv: load_panel(lv) for lv in ("sector", "subind")}
    for lv, p in panels.items():
        X = p.pivot(index="date", columns="group", values="fwd21").sort_index()
        X = X[X.notna().sum(axis=1) >= 8]
        rs252 = p.pivot(index="date", columns="group", values="rs252").sort_index()
        log(f"{lv}: {X.shape[1]} groups, {X.shape[0]} months {X.index[0]}..{X.index[-1]}")
        lvl = {"groups": int(X.shape[1]), "months": int(X.shape[0]), "first": X.index[0], "last": X.index[-1]}
        lvl["lag_profile"] = lag_profile(X)
        log(f"  lag profile done ({time.time() - t0:.0f}s)")
        lvl["persistence"] = persistence(X)
        lvl["longest_booms"] = longest_booms(X)
        log(f"  persistence done ({time.time() - t0:.0f}s)")
        lvl["quadrants"] = quadrants(p)
        P = pairs(X)
        lvl["pairs"] = {k: {kk: vv for kk, vv in v.items() if kk != "_df"} for k, v in P.items()}
        lvl["pair_forecast"] = pair_forecast(X, P, rs252)
        if lv == "sector":
            ex = []
            for a, b in EXAMPLES:
                for k, v in P.items():
                    r = v["_df"][(v["_df"].lead == a) & (v["_df"].follow == b)]
                    if len(r):
                        r = r.iloc[0]
                        ex.append({"lead": a, "follow": b, "lag_months": int(k),
                                   "r_train": round(float(r.r_train), 3), "t_train": round(float(r.t_train), 2),
                                   "r_test": round(float(r.r_test), 3), "t_test": round(float(r.t_test), 2)})
            lvl["examples"] = ex
        log(f"  pairs done ({time.time() - t0:.0f}s)")
        out["levels"][lv] = lvl
    import trade_rules_study as TR
    T = pd.read_parquet(TR.OUT_TRADES, columns=["date", "ticker", "sector"])
    Z = np.load(TR.OUT_NPZ)
    rules = [str(r) for r in Z["rules"]]
    S = trade_states(T, panels)
    out["trades"] = {"n": int(len(T)), "dates": int(T.date.nunique()), "plan_rule": PLAN_RULE,
                     "default_rule": DEFAULT_RULE,
                     "tests": trade_tests(T, Z["ret"], rules, S)}
    log(f"  trades done ({time.time() - t0:.0f}s)")
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    tmp.replace(OUT)
    log(f"wrote {OUT.name} in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
