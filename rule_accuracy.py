"""How accurate is the 'good level' rule? Three independent ways, each with its caveat.

Run: python rule_accuracy.py   (~3 min, idle machine)

  A. HISTORY   the 50-date point-in-time panel (2016-2026), 20/40/120/250 bars forward
  B. LUCK      the same number of picks drawn at random from the same-day pool
  C. LIVE      days AFTER the study was finished (2026-08-30), scored with the rule
               exactly as it stands, outcomes the study never saw.

"Accuracy" here is: how often a pick finishes up, how often it beats the same-day
market of eligible stocks, and by how much -- never a promise about one stock.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import pandas as pd

import calendar_us, config, dataset
import good_level as G

rng = np.random.default_rng(7)
SES = calendar_us.all_sessions()
POS = {s: i for i, s in enumerate(SES)}


def thin(dates, h):
    keep, last = [], -10**9
    for d in sorted(dates):
        if d in POS and POS[d] - last >= h:
            keep.append(d); last = POS[d]
    return keep


def tstat(a):
    a = np.asarray([x for x in a if np.isfinite(x)])
    return (a.mean() / (a.std(ddof=1) / np.sqrt(len(a)))) if len(a) >= 5 else np.nan


# ------------------------------------------------------------------ A + B
def history():
    p = pd.read_parquet(config.DATA / "_couple_panel.parquet")
    mask = (p["band"].isin(["AT", "NEAR"]) & (p["pct_hi"] >= G.PCT_HI_MIN)
            & (p["fund_score"] >= G.FUND_MIN) & (p["hype_score"] <= G.HYPE_MAX))
    print(f"panel {len(p):,} obs, {p.date.nunique()} dates {p.date.min()}..{p.date.max()}; "
          f"rule picks {int(mask.sum()):,} ({mask.sum()/len(p)*100:.2f}% of eligible)")
    try:
        import universe
        u = universe.load()
        col = "active" if "active" in u.columns else None
        if col:
            dead = set(u.loc[~u[col].astype(bool), "ticker"].astype(str))
            print(f"survivorship check: {p.ticker.isin(dead).mean()*100:.1f}% of panel rows are names "
                  f"no longer active today (delisted names present in the history)")
    except Exception as exc:
        print("survivorship check unavailable:", repr(exc)[:60])

    print(f"\n{'horizon':>8} {'dates':>5} {'picks':>6} | {'pick hit':>8} {'pool hit':>8} | "
          f"{'beat pool':>9} | {'pick mean':>9} {'pool mean':>9} {'excess':>8} {'t':>6} | {'luck p':>7}")
    res = {}
    for h in (20, 40, 120, 250):
        f = f"fwd{h}"
        ds = set(thin(p.date.unique().tolist(), h))
        q = p[p.date.isin(ds) & p[f].notna()]
        ex, hit_p, hit_b, beat, pm, bm, ks, pools = [], [], [], [], [], [], [], []
        allpick = []
        for d, g in q.groupby("date"):
            m = mask.loc[g.index]
            pk = g[m]
            if len(pk) < 3:
                continue
            ex.append(pk[f].mean() - g[f].mean()); pm.append(pk[f].mean()); bm.append(g[f].mean())
            allpick.append(pk[f].values)
            beat.append((pk[f] > g[f].median()).mean())
            hit_p.append((pk[f] > 0).mean()); hit_b.append((g[f] > 0).mean())
            ks.append(len(pk)); pools.append(g[f].values)
        if len(ex) < 5:
            print(f"{h:>8} too few dates"); continue
        ex = np.array(ex)
        # B. luck: random same-size draws from each date's own pool
        R = 1000
        sims = np.zeros(R)
        for k, P in zip(ks, pools):
            idx = rng.random((R, len(P))).argpartition(k - 1, axis=1)[:, :k]
            sims += (P[idx].mean(axis=1) - P.mean())
        sims /= len(ks)
        pval = float((sims >= ex.mean()).mean())
        res[h] = dict(ex=ex.mean(), t=tstat(ex))
        print(f"{h:>8} {len(ex):>5} {sum(ks):>6} | {np.mean(hit_p)*100:>7.1f}% {np.mean(hit_b)*100:>7.1f}% | "
              f"{np.mean(beat)*100:>8.1f}% | {np.mean(pm)*100:>+8.1f}% {np.mean(bm)*100:>+8.1f}% "
              f"{ex.mean()*100:>+7.2f}pp {tstat(ex):>+6.2f} | {pval:>7.3f}")
        if h == 40:
            allp = np.concatenate(allpick)
            allq = np.concatenate(pools)
            print(f"{'':>8} 40-bar spread: pick median {np.median(allp)*100:+.1f}% vs pool {np.median(allq)*100:+.1f}%;  "
                  f"lost >20%: picks {np.mean(allp<=-0.20)*100:.1f}% vs pool {np.mean(allq<=-0.20)*100:.1f}%;  "
                  f"gained >20%: picks {np.mean(allp>=0.20)*100:.1f}% vs pool {np.mean(allq>=0.20)*100:.1f}%")
            ds_sorted = sorted(ds)
            half = len(ds_sorted) // 2
            for lab, sub in (("first half", ds_sorted[:half]), ("second half", ds_sorted[half:])):
                e2 = []
                for d, g in q[q.date.isin(sub)].groupby("date"):
                    pk = g[mask.loc[g.index]]
                    if len(pk) >= 3:
                        e2.append(pk[f].mean() - g[f].mean())
                print(f"{'':>8} 40-bar {lab:11} ({sub[0]}..{sub[-1]}): excess {np.mean(e2)*100:+.2f}pp  "
                      f"t={tstat(e2):+.2f}  over {len(e2)} dates")
    return res


# ------------------------------------------------------------------ C
def live():
    print("\n" + "=" * 78)
    print("C. LIVE FORWARD TEST -- days after the study was finished (2026-08-30)")
    print("=" * 78)
    zdates = sorted(f.stem for f in config.ZONES.glob("*.parquet") if f.stem >= "2026-08-31")
    last_bar = SES[-1] if SES[-1] <= "2026-10-06" else "2026-10-06"
    last_bar = "2026-10-06"
    tickers = set()
    per = {}
    for D in zdates:
        try:
            z, trusted, df = G.current(D)
        except Exception as exc:
            print(f"  {D}: skipped ({repr(exc)[:50]})"); continue
        per[D] = (set(trusted.ticker.astype(str)), df)
        tickers |= per[D][0]
    print(f"{len(zdates)} stored zone sessions, {len(tickers):,} tickers; loading bars")
    closes = {}
    tl = sorted(tickers)
    for i in range(0, len(tl), 250):
        part = tl[i:i + 250]
        d = dataset.panel(part, "1d", start="2026-08-25", end=last_bar)
        for t in part:
            b = d.get(t) if isinstance(d, dict) else None
            if b is not None and len(b):
                closes[t] = b.set_index(b["date"].astype(str))["close"].astype(float)
    C = pd.DataFrame(closes)
    for h, dmax in ((10, "2026-09-22"), (20, "2026-09-08")):
        rows = []
        for D in zdates:
            if D > dmax or D not in per or D not in C.index:
                continue
            E = SES[POS[D] + h]
            if E not in C.index:
                continue
            pool_t, df = per[D]
            r = (C.loc[E] / C.loc[D] - 1.0).dropna()
            pool = r[r.index.isin(pool_t)]
            pk_t = set(df.loc[G.rule(df), "ticker"].astype(str))
            pk = r[r.index.isin(pk_t)]
            if len(pk) < 5:
                continue
            rows.append(dict(D=D, n=len(pk), pick=pk.mean(), pool=pool.mean(),
                             hit=(pk > 0).mean(), bhit=(pool > 0).mean(),
                             beat=(pk > pool.median()).mean(),
                             se=pk.std(ddof=1) / np.sqrt(len(pk))))
        if not rows:
            print(f"h={h}: no usable dates"); continue
        t = pd.DataFrame(rows)
        print(f"\nh={h} bars forward  ({len(t)} start days {t.D.iloc[0]}..{t.D.iloc[-1]}, windows OVERLAP heavily)")
        print(f"  {'start':10} {'picks':>5} {'pick':>8} {'pool':>8} {'excess':>8} {'pick up':>8} {'pool up':>8} {'beat pool':>9}")
        for _, r in t.iterrows():
            print(f"  {r.D:10} {int(r.n):>5} {r.pick*100:>+7.1f}% {r.pool*100:>+7.1f}% {(r.pick-r.pool)*100:>+7.2f}pp "
                  f"{r.hit*100:>7.0f}% {r.bhit*100:>7.0f}% {r.beat*100:>8.0f}%")
        print(f"  AVERAGE  picks {t.n.mean():.0f}/day   pick {t.pick.mean()*100:+.1f}%  pool {t.pool.mean()*100:+.1f}%  "
              f"excess {(t.pick-t.pool).mean()*100:+.2f}pp   pick up {t.hit.mean()*100:.0f}% vs pool up {t.bhit.mean()*100:.0f}%   "
              f"beat pool median {t.beat.mean()*100:.0f}%")
        print(f"  one start day's own cross-sectional noise: +/- {t.se.mean()*100:.1f}pp (1 s.e. of the pick average)")


if __name__ == "__main__":
    print("A+B. HISTORY (50 point-in-time dates, 2016-2026) and LUCK")
    print("=" * 78)
    history()
    live()
