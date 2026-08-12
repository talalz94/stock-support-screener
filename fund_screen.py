"""
The fundamental screener: daily rank of the tradeable universe.

    python fund_screen.py                  fetch, score, rank, report
    python fund_screen.py --only ORCL --explain    full per-metric trace
    python fund_screen.py --catchup --every 21     build the historical series
    python fund_screen.py --dry-run

Separate from the bounce and sentiment screeners, sharing only the bar store and
the tidy score table. It writes `scores` rows under the module name
"fundamental", which is what makes `factor_lab.py --module fundamental` able to
test every metric without this file being involved at all.

RANKED, NOT SCREENED. There are no pass/fail gates here, deliberately. A gate
throws away the information that a name sat just outside it, and with 29 metrics
across 4 pillars the interesting question is ordering, not membership. Filtering
happens once, on tradeability (price and liquidity), reusing the bounce
screener's own floor so the two lists are drawn from the same pool.

THE COMPOSITE IS NOT A RECOMMENDATION. Pillar weights are equal because nothing
has been measured yet; `factor_lab.py --leaderboard --module fundamental` is what
decides them. Until it has run, read the pillars, not the composite.
"""

from __future__ import annotations

import argparse
import sys
import time
import warnings
from datetime import datetime

import numpy as np
import pandas as pd

import calendar_us
import config
import ui
import fund_metrics as FM
import scores

warnings.filterwarnings("ignore", category=RuntimeWarning)


def log(m: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} | {m}"
    print(line, flush=True)
    try:
        with config.LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write("fund  " + line + "\n")
    except OSError:
        pass


def universe(asof: str) -> list[str]:
    """Delegates to bars.tradeable_universe -- see the note there on
    why this filter must have exactly one definition."""
    import bars
    return bars.tradeable_universe(asof)


def run_once(asof: str | None = None, verbose: bool = True) -> dict:
    t0 = time.time()
    asof = asof or calendar_us.last_closed_session()

    scores.load_all()
    mod = scores.get("fundamental")
    uni = universe(asof)
    if not uni:
        log("  ! no panel stats; run `python bars.py --update` first")
        return {"ok": False}

    rows = mod.compute(asof, uni)
    if rows.empty:
        log("  ! no fundamental rows -- is the fact store backfilled?")
        return {"ok": False}
    scores.write(rows, session=asof, module="fundamental")

    payload = build_payload(asof, rows)
    write_html(payload, asof)
    if verbose:
        log(f"  {payload['n_scored']:,} scored of {len(uni):,} tradeable "
            f"({payload['n_nodata']:,} without enough filings) | {time.time() - t0:.0f}s")
    return {"ok": True, "n": payload["n_scored"]}


def _wide(rows: pd.DataFrame) -> pd.DataFrame:
    num = rows[rows["value"].notna()].pivot_table(
        index="ticker", columns="metric", values="value", aggfunc="last")
    lab = rows[rows["label"].notna()]
    out = num
    if not lab.empty:
        lw = lab.pivot_table(index="ticker", columns="metric", values="label",
                             aggfunc="last")
        lw.columns = [f"{c}_label" for c in lw.columns]
        out = num.join(lw, how="outer")
    return out.reset_index().rename_axis(None, axis=1)


def build_payload(asof: str, rows: pd.DataFrame, top: int = 150) -> dict:
    w = _wide(rows)
    has = w.get("has_fundamentals", pd.Series(1.0, index=w.index)).fillna(0)
    scored = w[(has > 0) & w.get("fund_score", pd.Series(np.nan)).notna()].copy()
    n_nodata = int((has == 0).sum())

    scored = scored.sort_values("fund_score", ascending=False)

    def g(r, k, nd=2):
        v = r.get(k)
        try:
            f = float(v)
            return None if not np.isfinite(f) else round(f, nd)
        except (TypeError, ValueError):
            return None

    cards = []
    for _, r in scored.head(top).iterrows():
        cards.append({
            "ticker": r["ticker"],
            "sector": str(r.get("sector_label") or "?"),
            "score": g(r, "fund_score", 1),
            "quality": g(r, "quality_score", 0), "value": g(r, "value_score", 0),
            "safety": g(r, "safety_score", 0), "growth": g(r, "growth_score", 0),
            "f": g(r, "f_score", 0), "z": g(r, "z_score", 2), "m": g(r, "m_score", 2),
            "roic": g(r, "roic", 3), "spread": g(r, "roic_wacc", 3),
            "ev_ebitda": g(r, "ev_ebitda", 1), "fcfy": g(r, "fcf_yield", 3),
            "accr": g(r, "accruals", 3), "rev_g": g(r, "rev_growth", 3),
            "mktcap": g(r, "mktcap", 0), "stale": g(r, "days_since_filing", 0),
            "filed": str(r.get("last_filed_label") or ""),
        })

    sect = []
    if "sector_label" in scored.columns:
        for s, sub in scored.groupby("sector_label"):
            if len(sub) >= 4:
                sect.append({"sector": str(s), "n": int(len(sub)),
                             "score": round(float(sub["fund_score"].mean()), 1)})
        sect.sort(key=lambda d: -d["score"])

    return {"asof": asof, "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "n_scored": int(len(scored)), "n_nodata": n_nodata,
            "sectors": sect, "cards": cards,
            "flags": _flags(scored)}


def _flags(w: pd.DataFrame) -> dict:
    """Counts against the published academic thresholds, not invented ones."""
    def n(cond):
        try:
            return int(cond.sum())
        except Exception:                                          # noqa: BLE001
            return 0
    return {
        "distress_z_lt_1_81": n(w.get("z_score", pd.Series(dtype=float)) < 1.81),
        "manipulation_m_gt_-1_78": n(w.get("m_score", pd.Series(dtype=float)) > -1.78),
        "strong_f_ge_8": n(w.get("f_score", pd.Series(dtype=float)) >= 8),
        "weak_f_le_2": n(w.get("f_score", pd.Series(dtype=float)) <= 2),
        "moat_spread_gt_0": n(w.get("roic_wacc", pd.Series(dtype=float)) > 0),
    }


def write_html(payload: dict, asof: str) -> str:
    _UI_NAV = ui.nav("fundamental", 1)
    def esc(s):
        return (str(s).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))

    def pill(v, good_hi=True):
        if v is None:
            return '<span class="na">-</span>'
        cls = "hi" if (v >= 66) == good_hi else ("lo" if (v <= 33) == good_hi else "mid")
        return f'<span class="p {cls}">{v:.0f}</span>'

    def money(v):
        if v is None:
            return "-"
        for u, d in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
            if abs(v) >= d:
                return f"${v / d:.1f}{u}"
        return f"${v:,.0f}"

    rows = []
    for c in payload["cards"]:
        z, m, f = c["z"], c["m"], c["f"]
        warn = []
        if z is not None and z < 1.81:
            warn.append('<span class="w red">distress Z</span>')
        if m is not None and m > -1.78:
            warn.append('<span class="w amb">M-flag</span>')
        if c["stale"] is not None and c["stale"] > 200:
            warn.append(f'<span class="w gry">{c["stale"]:.0f}d stale</span>')
        rows.append(f"""<tr data-sector="{esc(c['sector'])}">
<td class="tk">{esc(c['ticker'])}<span class="sec">{esc(c['sector'])}</span></td>
<td class="num b">{c['score']:.1f}</td>
<td>{pill(c['quality'])}{pill(c['value'])}{pill(c['safety'])}{pill(c['growth'])}</td>
<td class="num">{'-' if f is None else f'{f:.0f}'}</td>
<td class="num">{'-' if z is None else f'{z:.2f}'}</td>
<td class="num">{'-' if c['spread'] is None else f"{c['spread']*100:+.1f}%"}</td>
<td class="num">{'-' if c['ev_ebitda'] is None else f"{c['ev_ebitda']:.1f}"}</td>
<td class="num">{'-' if c['fcfy'] is None else f"{c['fcfy']*100:+.1f}%"}</td>
<td class="num">{'-' if c['rev_g'] is None else f"{c['rev_g']*100:+.0f}%"}</td>
<td class="num">{money(c['mktcap'])}</td>
<td class="wr">{''.join(warn)}</td></tr>""")

    secs = "".join(f'<span class="chip">{esc(s["sector"])}<b>{s["score"]:.0f}</b>'
                   f'<i>n={s["n"]}</i></span>' for s in payload["sectors"])
    fl = payload["flags"]

    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fundamentals {esc(asof)}</title><style>{ui.CSS}/* Legacy variable names aliased onto the shared palette (ui.py). These two pages predate ui and use --fg/--mut/--acc/--card; aliasing keeps every existing rule working while the colours come from one place. */:root{{--fg:var(--ink);--mut:var(--muted);--acc:var(--accent);--card:var(--panel);--hi:var(--pos);--lo:var(--neg);--mid:var(--muted);--neu:var(--muted)}}
:root{{--bg:#fff;--fg:#111;--mut:#666;--line:#e3e3e6;--card:#fafafa;
--hi:#0a7d3f;--lo:#b3261e;--mid:#8a8a92;--acc:#1a56db}}
@media(prefers-color-scheme:dark){{:root{{--bg:#111315;--fg:#e8e8ea;--mut:#9a9aa2;
--line:#2a2d31;--card:#191c1f;--hi:#3ddc84;--lo:#ff6b6b;--mid:#8a8a92}}}}
*{{box-sizing:border-box}}body{{margin:0;padding:18px;background:var(--bg);color:var(--fg);
font:14px/1.45 -apple-system,Segoe UI,Roboto,sans-serif}}
h1{{font-size:19px;margin:0 0 2px}}.meta{{color:var(--mut);font-size:12.5px;margin-bottom:12px}}
.meta a{{color:var(--fg)}}
.hdr{{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0 12px}}
.stat{{background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:7px 11px;font-size:12.5px}}.stat b{{display:block;font-size:16px;margin-top:2px}}
.chip{{display:inline-flex;gap:6px;align-items:baseline;background:var(--card);
border:1px solid var(--line);border-radius:999px;padding:4px 10px;font-size:12px;margin:0 5px 5px 0}}
.chip i{{color:var(--mut);font-style:normal;font-size:11px}}
.wrap{{overflow-x:auto;border:1px solid var(--line);border-radius:10px}}
table{{border-collapse:collapse;width:100%;min-width:950px}}
th,td{{padding:6px 9px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}}
th{{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut);
position:sticky;top:0;background:var(--bg)}}
tr:last-child td{{border-bottom:0}}
.tk{{font-weight:640}}.tk .sec{{display:block;font-weight:400;font-size:11px;color:var(--mut)}}
.num{{font-variant-numeric:tabular-nums;text-align:right}}.b{{font-weight:650}}
.p{{display:inline-block;width:30px;text-align:center;padding:1px 0;border-radius:4px;
font-size:11px;margin-right:3px;font-variant-numeric:tabular-nums}}
.p.hi{{background:var(--hi);color:#fff}}.p.lo{{background:var(--lo);color:#fff}}
.p.mid{{background:var(--line);color:var(--mut)}}
.w{{display:inline-block;padding:1px 6px;border-radius:999px;font-size:10px;margin-right:4px}}
.w.red{{background:var(--lo);color:#fff}}.w.amb{{background:#f0b429;color:#3a2c00}}
.w.gry{{background:var(--line);color:var(--mut)}}
.na{{color:var(--mut)}}.note{{margin-top:14px;color:var(--mut);font-size:12px;max-width:780px}}
</style></head><body>{_UI_NAV}
<h1>Fundamental screen &middot; {esc(asof)}</h1>
<div class="meta">generated {esc(payload['generated'])} &middot;
{payload['n_scored']:,} scored &middot; {payload['n_nodata']:,} without enough filings
&middot; ranked by composite</div>
<div class="hdr">
 <div class="stat">Piotroski F &ge; 8<b>{fl['strong_f_ge_8']:,}</b></div>
 <div class="stat">Piotroski F &le; 2<b>{fl['weak_f_le_2']:,}</b></div>
 <div class="stat">Altman Z &lt; 1.81<b>{fl['distress_z_lt_1_81']:,}</b></div>
 <div class="stat">Beneish M &gt; -1.78<b>{fl['manipulation_m_gt_-1_78']:,}</b></div>
 <div class="stat">ROIC &gt; WACC<b>{fl['moat_spread_gt_0']:,}</b></div>
</div>
<div>{secs}</div>
<div class="wrap"><table><thead><tr>
<th>ticker</th>{ui.th("score","fund_score")}
{ui.th("Q&middot;V&middot;S&middot;G","quality_score")}{ui.th("F","f_score")}
{ui.th("Z","z_score")}{ui.th("ROIC&minus;WACC","roic_wacc")}
{ui.th("EV/EBITDA","ev_ebitda")}{ui.th("FCF yld","fcf_yield")}
{ui.th("rev g","rev_growth")}{ui.th("mkt cap","mktcap")}<th>flags</th>
</tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<p class="note"><b>Ranked, not recommended.</b> The four pillar scores are
cross-sectional percentile ranks within sector; the composite weights them
<i>equally</i> because nothing has been measured yet &mdash; run
<code>python factor_lab.py --leaderboard --module fundamental</code> to find out
which of these 29 metrics actually predicts forward returns before trusting the
composite over the pillars. Thresholds shown (F&ge;8, Z&lt;1.81, M&gt;&minus;1.78)
are the published academic ones, not tuned here. Names with too few filings to
build trailing-twelve-month figures are excluded rather than scored as zero.</p>
</body></html>"""
    config.REPORTS.mkdir(parents=True, exist_ok=True)
    p = config.REPORTS_FUNDAMENTAL / f"{asof}.html"
    p.write_text(html, encoding="utf-8")
    (config.REPORTS_FUNDAMENTAL / "latest.html").write_text(html, encoding="utf-8")
    return str(p)


def catchup(every: int = 21, frm: str | None = None, verbose: bool = True) -> int:
    """Build the historical score series so factor_lab has something to test.

    Monthly by default: fundamentals change on a filing cadence, so a daily
    series would be ~95% repeated rows for no extra information.
    """
    asof = calendar_us.last_closed_session()
    sess = [s for s in calendar_us.all_sessions() if s <= asof]
    if frm:
        sess = [s for s in sess if s >= frm]
    todo = sess[::-1][::every][::-1]
    have = set(scores.sessions_stored("fundamental"))
    todo = [s for s in todo if s not in have]
    if not todo:
        log("  fundamental score series already complete")
        return 0

    scores.load_all()
    mod = scores.get("fundamental")
    uni = universe(asof)
    ok, t0 = 0, time.time()
    for i, s in enumerate(todo, 1):
        try:
            rows = mod.compute(s, uni)
            if not rows.empty:
                scores.write(rows, session=s, module="fundamental")
                ok += 1
        except Exception as exc:                                   # noqa: BLE001
            log(f"  ! {s}: {repr(exc)[:100]}")
        if verbose and (i % 5 == 0 or i == len(todo)):
            el = time.time() - t0
            log(f"  catchup {i}/{len(todo)} ({ok} written, {el / 60:.1f}m, "
                f"eta {el / i * (len(todo) - i) / 60:.0f}m)")
    return ok


def explain(ticker: str, asof: str | None = None) -> None:
    asof = asof or calendar_us.last_closed_session()
    scores.load_all()
    rows = scores.get("fundamental").compute(asof, [ticker])
    if rows.empty:
        print(f"  {ticker}: no fundamental rows as of {asof}")
        return
    print(f"\n  {ticker} @ {asof}")
    lab = rows[rows["label"].notna()]
    for _, r in lab.iterrows():
        print(f"    {r['metric']:<22} {r['label']}")
    num = rows[rows["value"].notna()].set_index("metric")["value"]
    for pillar in FM.PILLARS:
        print(f"\n    -- {pillar} ({num.get(f'{pillar}_score', float('nan')):.0f}) --")
        for name, (p, d, desc) in FM.REGISTRY.items():
            if p != pillar or name not in num.index:
                continue
            print(f"    {name:<20} {num[name]:>12.4f}   {'^' if d > 0 else 'v'} {desc}")
    print(f"\n    composite {num.get('fund_score', float('nan')):.1f}  "
          f"(coverage {num.get('fund_cov', float('nan')):.0%})")


def main() -> int:
    config.safe_console()
    ap = argparse.ArgumentParser(description="Fundamental screener.")
    ap.add_argument("--asof", default=None)
    ap.add_argument("--only", metavar="SYM", default=None)
    ap.add_argument("--explain", action="store_true")
    ap.add_argument("--catchup", action="store_true")
    ap.add_argument("--every", type=int, default=21)
    ap.add_argument("--from", dest="frm", default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    config.dirs()

    if a.dry_run:
        import fundamentals as FD
        asof = a.asof or calendar_us.last_closed_session()
        print(f"  asof             {asof}")
        print(f"  tradeable        {len(universe(asof)):,}")
        print(f"  fact quarters    {len(FD.stored_quarters())} "
              f"({FD.store_bytes() / 1e6:.0f} MB)")
        print(f"  scored sessions  {len(scores.sessions_stored('fundamental'))}")
        return 0
    if a.only:
        explain(a.only.upper(), a.asof)
    elif a.catchup:
        catchup(every=a.every, frm=a.frm)
    else:
        run_once(a.asof)
    return 0


if __name__ == "__main__":
    sys.exit(main())
