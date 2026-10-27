#!/usr/bin/env python
"""The Insiders page (reports/insider/latest.html) and the House trades page (house.html).

WHAT THE USER ASKED FOR: a free, self-maintained version of a paid insider-buying
scanner -- recent purchases by company insiders and by politicians, filtered by cluster
size, dollars, role, sector, market cap, rarity and each buyer's track record.

WHAT THIS PAGE ADDS THAT THE PAID ONE DOES NOT: the measured result of every filter,
printed beside the filter, read from our own event studies (insider_study.py,
congress_study.py, insider_score_wf.py) at build time -- never typed. Two of the filters
were measured to matter (SMALL companies, RARE buyers); most were not, Ross's own screen
included, and the page says so where the filter is chosen, not in a footnote.

HONESTY RULES (as the other screens): a missing value is a dash, never zero; rows are
ordered by filing date unless the reader sorts; no row is called a buy. Aggregation is
client-side from one array of FILINGS, so every filter (window, role, funds, 10b5-1)
changes the counts the way the reader expects instead of filtering a pre-made total.

THE HOUSE PAGE IS LOCAL. 5 U.S.C. 13107(c) bars commercial use of these reports, and
reports/ is never committed or published.

Run:  python insider_page.py              both pages for the last closed session
      python insider_page.py --selftest
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

import calendar_us
import config
import insider_signals as SIG
import ui
from zones_page import _ESC

OUT_DIR = config.REPORTS / "insider"
WINDOW_MAX = 90              # days of filings the Insiders page carries (its window menu tops out here)
HOUSE_DAYS = 365             # days of House filings the House page carries
PX_FAR = (0.4, 2.5)          # reported price / our close outside this = another currency, class or split?
H = 60                       # the horizon every verdict is read at
HALF_T = 1.0                 # "measured to matter" also needs t >= 1 in EACH half: same sign, some strength

# Each scanner filter -> the study test that measured it (insider_study.SIGNALS names).
FILTER_TESTS = {
    "any": "any purchase >= $10K",
    "ross": "Ross: 2+ buyers, $250K+, not largest",
    "b2": "2+ buyers in 30 days",
    "b3": "3+ buyers in 30 days",
    "v250": ">= $250K bought in 30 days",
    "v1m": ">= $1M bought in 30 days",
    "small": "small (bottom 2 liquidity fifths)",
    "large": "large (top liquidity fifth)",
    "rare": "first buy in 2+ years (rare)",
    "ceo": "CEO or CFO bought",
    "dirs": "only directors bought",
    "track": "buyer with a good track record",
    "unplanned": "unplanned purchases, 2023+",
    "planned": "planned (10b5-1) purchases, 2023+",
}
FILTER_LABEL = {"any": "any purchase", "ross": "Ross's screen", "b2": "2+ insiders", "b3": "3+ insiders",
                "v250": "$250K+", "v1m": "$1M+", "small": "small companies", "large": "the largest companies",
                "rare": "rare buyers", "ceo": "CEO/CFO buying", "dirs": "directors only",
                "track": "buyers with a good record", "unplanned": "unplanned (non-10b5-1) buying",
                "planned": "planned 10b5-1 buying"}
HOUSE_TESTS = ["any member stock purchase", "purchase >= $15,001", "purchase >= $50,001",
               "purchase >= $250,001", "2+ members bought within 30 days", "spouse / joint / child purchase",
               "call options bought", "sale >= $15,001 (selling)", "dividend reinvestment (control, expect ~0)"]


def _num(v, nd: int = 4):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if not np.isfinite(f) else round(f, nd)


def _txt(v) -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("nan", "nat", "none", "<na>") else s


def _js(o) -> str:
    # allow_nan=False: a stray NaN must fail the build, not render as "NaN" in a cell
    return json.dumps(o, separators=(",", ":"), allow_nan=False).translate(_ESC)


# ============================================================ verdicts, from the files
def verdict(r: dict | None) -> tuple[str, str]:
    """(css class, words) for one study result at H sessions. The rule is fixed here so
    every label on both pages reads the same way."""
    if not r or r.get("t") is None or not np.isfinite(r.get("t", np.nan)):
        return "na", "not measured"
    t = float(r["t"])
    h1 = (r.get("first_half") or {}).get("t")
    h2 = (r.get("second_half") or {}).get("t")
    halves = [x for x in (h1, h2) if x is not None and np.isfinite(x)]
    if t >= 2 and len(halves) == 2 and min(halves) >= HALF_T:
        return "ok", f"measured to matter: t {t:.1f}, and t {min(halves):.1f}+ in each half"
    if t >= 2 and len(halves) < 2:
        return "half", f"t {t:.1f}, but measurable in one half of the history only"
    if t >= 2:
        return "half", f"t {t:.1f} overall, but only {min(halves):+.1f} in one half"
    if t <= -2:
        return "neg", f"measured WORSE than peers: t {t:.1f}"
    return "no", f"not significant: t {t:.1f}"


def _res(ev: dict, name: str, key: str) -> dict | None:
    s = ev.get(name)
    return None if not s else s["by"].get((key, H))


def evidence_table(ev: dict, tests: list[str], which: str) -> str:
    rows = []
    for k in tests:
        r = _res(ev, which, k)
        if not r:
            continue
        cls, words = verdict(r)
        h1 = (r.get("first_half") or {}).get("t")
        h2 = (r.get("second_half") or {}).get("t")

        def f(x, fmt):
            return "&ndash;" if x is None or not np.isfinite(x) else format(x, fmt)
        rows.append(f'<tr class="ev-{cls}"><td class="lab">{ui.esc(k)}</td><td>{r["episodes"]:,}</td>'
                    f'<td>{f(r.get("mean_pp"), "+.2f")}</td><td>{f(r.get("median_pp"), "+.2f")}</td>'
                    f'<td>{f(r.get("beat_pool_pct"), ".1f")}%</td><td><b>{f(r.get("t"), "+.2f")}</b></td>'
                    f'<td>{f(h1, "+.2f")} / {f(h2, "+.2f")}</td><td class="lab">{ui.esc(words)}</td></tr>')
    return ('<table class="evt"><thead><tr><th class="lab">test (60 sessions after the filing)</th><th>episodes</th>'
            '<th>mean pp vs peers</th><th>median pp</th><th>beat peers</th><th>t</th><th>t 1st / 2nd half</th>'
            '<th class="lab">reading</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table>")


def _chip_ev(ev: dict, key: str) -> str:
    r = _res(ev, "insider", FILTER_TESTS[key])
    cls, words = verdict(r)
    t = "&ndash;" if not r or r.get("t") is None else f"t {r['t']:+.1f}"
    return f'<span class="ev ev-{cls}" title="{ui.esc(words)}">{t}</span>'


def insider_evidence(ev: dict) -> str:
    s = ev.get("insider")
    if not s:
        return "<div><b>Not measured yet.</b> No study file; treat every filter as unverified.</div>"
    wf = None
    try:
        import insider_score_wf
        wf = insider_score_wf.read()
    except Exception:                                            # noqa: BLE001
        pass
    any_r = _res(ev, "insider", FILTER_TESTS["any"]) or {}
    ross = _res(ev, "insider", FILTER_TESTS["ross"]) or {}
    any_word = ("is not significant over the whole history" if (any_r.get("median_pp") or 0) < 0 and abs(any_r.get("t") or 0) < 2
                else "reads t {:+.2f} here".format(any_r.get("t") or float("nan")))
    med = any_r.get("median_pp")
    med_txt = ("trailed" if (med or 0) < 0 else "beat") + f" its peers by {abs(med or 0):.2f}pp"
    _cls, ross_words = verdict(ross)
    matter = [FILTER_LABEL[k] for k, t in FILTER_TESTS.items() if verdict(_res(ev, "insider", t))[0] == "ok"]
    weak = [FILTER_LABEL[k] for k, t in FILTER_TESTS.items() if verdict(_res(ev, "insider", t))[0] == "half"]
    out = [f"<div><b>What our own history says</b> (insider_study.py, measured {ui.esc(str(s.get('measured_on'))[:10])}; "
           f"events {ui.esc(s.get('events_from'))} to {ui.esc(s.get('events_to'))}; {s.get('tests')} tests, so about "
           f"{s.get('expected_by_chance')} would reach |t| &ge; 2 by chance). Each event is a stock on the day a "
           f"purchase was FILED, entered at the next close, and compared over 60 sessions with stocks of the same "
           f"liquidity that day; t is across calendar quarters.</div>",
           f"<div><b>Insider buying in general {any_word}:</b> the median stock after any $10K+ purchase "
           f"{med_txt} and {any_r.get('beat_pool_pct', 0):.0f}% beat them"
           + ("; the positive mean comes from a minority of big winners -- a fat-tail bet, not a typical-stock edge"
              if (med or 0) < 0 < (any_r.get("mean_pp") or 0) else "")
           + f". <b>Ross's own screen</b> (2+ insiders, $250K+, not the largest): {ui.esc(ross_words)}.</div>",
           f"<div><b>Measured to matter</b> (t &ge; 2 overall and &ge; {HALF_T:.0f} in each half): "
           f"<b>{ui.esc(', '.join(matter) or 'none')}</b>"
           + (f"; significant overall but weak in one half: {ui.esc(', '.join(weak))}" if weak else "")
           + ". The coloured t beside each filter below is that filter's own test.</div>"]
    if wf and wf.get("chosen"):
        c = wf["chosen"]
        out.append(f"<div><b>Combined insider score</b> &mdash; chosen on 2016&ndash;mid-2021 only, then tested on "
                   f"{ui.esc(wf.get('split'))} onward ({wf.get('rules_tried')} rules tried on the first half): "
                   f"<b>{ui.esc(c['rule'])}</b>. Train t {c['train']['t']:+.2f}; <b>test t {c['test']['t']:+.2f}</b> over "
                   f"{c['test']['episodes']:,} episodes, mean {c['test']['mean_pp']:+.2f}pp, median "
                   f"{c['test']['median_pp']:+.2f}pp &mdash; <b>{ui.esc(wf.get('verdict'))}</b>. Still a fat tail: the "
                   f"median is {'negative' if (c['test']['median_pp'] or 0) < 0 else 'positive'}.")
        b, vb = (wf.get("baseline") or {}).get("test") or {}, wf.get("vs_baseline") or {}
        if b:
            out.append(f" <b>But the test half was kind to insider buying in general</b>: "
                       f"{wf.get('test_rules_at_t2')} of {wf.get('rules_tried')} rules reached t &ge; 2 there, and ANY "
                       f"$10K+ purchase made {b.get('mean_pp', float('nan')):+.2f}pp (t {b.get('t', float('nan')):+.2f}) "
                       f"over the same span. The score's extra {vb.get('test_mean_pp_diff', float('nan')):+.2f}pp is "
                       f"{ui.esc(vb.get('reading', ''))}.")
        out.append("</div>")
    import insider_study as IS
    sell = _res(ev, "insider", IS.SELL_SIGNAL)
    if sell and sell.get("t") is not None and np.isfinite(sell["t"]):
        out.append(f"<div><b>Selling is not the mirror image:</b> clustered unplanned selling (3+ sellers, $1M+ "
                   f"in 30 days) reads t {sell['t']:+.2f} &mdash; "
                   + ("those stocks did no worse than their peers, so this page shows buying only."
                      if sell["t"] > -2 else "measured bearish.") + "</div>")
    out.append('<details><summary>every test, with both halves</summary>'
               + evidence_table(ev, list(FILTER_TESTS.values()) + [IS.SELL_SIGNAL], "insider") + "</details>")
    out.append("<div><b>Read with:</b> only stocks still in our bar store are measured, and half of all purchases "
               "are in companies since delisted (often failures), so these results are FLATTERED. Small-company "
               "spreads and costs are not modelled. A research screen, not advice.</div>")
    return "".join(out)


# ============================================================ per-ticker context
def _fund(asof: str) -> pd.DataFrame:
    import scores
    try:
        ss = [s for s in scores.sessions_stored("fundamental") if s <= asof]
        if not ss:
            return pd.DataFrame()
        df = scores.read(module="fundamental", start=ss[-1], end=ss[-1], metrics=["fund_score", "mktcap"])
        return df.pivot_table(index="ticker", columns="metric", values="value", aggfunc="first")
    except Exception:                                            # noqa: BLE001
        return pd.DataFrame()


def _insider_scores(asof: str, tickers: list[str]) -> dict[str, float]:
    try:
        import scores
        scores.load_all()
        r = scores.get("insider").compute(asof, tickers)
        r = r[r.metric == "insider_score"]
        return dict(zip(r.ticker, r.value))
    except Exception:                                            # noqa: BLE001
        return {}


def ticker_context(asof: str, tickers: list[str]) -> dict[str, list]:
    """ticker -> [company, sector, mktcap, fifth 1-5, band, dist, level, fund, close, profile, insider_score, badge]."""
    import macro
    sec = macro.load_sector_map().drop_duplicates("ticker").set_index("ticker")["sector"]
    fu = _fund(asof)
    z = SIG.zone_status(asof)
    fifth = SIG.fifth_on(asof)
    close = SIG.close_on(asof)
    prof = {p.stem for p in config.REPORTS_STOCK.glob("*.html")} if config.REPORTS_STOCK.exists() else set()
    isc = _insider_scores(asof, tickers)
    bd = SIG.badges(asof)
    out = {}
    for t in tickers:
        s = sec.get(t)
        out[t] = [
            "", s if isinstance(s, str) and s else "Other",
            _num(fu.at[t, "mktcap"], 0) if (t in fu.index and "mktcap" in fu) else None,
            int(fifth[t]) + 1 if t in fifth.index and np.isfinite(fifth[t]) else None,
            _txt(z.at[t, "band"]) if t in z.index else "",
            _num(z.at[t, "dist_pct"]) if t in z.index else None,
            _num(z.at[t, "level"], 2) if t in z.index else None,
            _num(fu.at[t, "fund_score"], 1) if (t in fu.index and "fund_score" in fu) else None,
            _num(close.get(t), 2),
            1 if t in prof else 0,
            _num(isc.get(t), 0),
            bd.get(t, ""),
        ]
    return out


# ============================================================ the Insiders page
def insider_payload(asof: str) -> dict:
    d = SIG.derived()
    lo = (date.fromisoformat(asof) - timedelta(days=WINDOW_MAX)).isoformat()
    w = d[(d.filing_date > lo) & (d.filing_date <= asof) & d.ticker.notna()].copy()
    tr = SIG.owner_tracks(asof, w.owner_cik.unique(), d=d)
    w = w.join(tr, on="owner_cik")
    tick = sorted(w.ticker.unique())
    ctx = ticker_context(asof, tick)
    names = w.groupby("ticker").issuer_name.last()
    for t in tick:
        ctx[t][0] = _txt(names.get(t))
    rows = []
    for r in w.sort_values(["filing_date", "ticker"], ascending=[False, True]).itertuples(index=False):
        close = ctx[r.ticker][8]
        far = 0
        if close and r.price and np.isfinite(r.price) and r.price > 0:
            far = int(not (PX_FAR[0] <= r.price / close <= PX_FAR[1]))
        kind = ""
        if r.who != "dno":
            kind = "fund/entity" if bool(r.is_entity) else ("10% owner" if bool(r.is_ten_pct) else "other")
        rows.append([
            r.ticker, r.filing_date, _txt(r.trans_first), _txt(r.owner_name), _txt(r.role),
            0 if r.who == "dno" else 1, kind, _num(r.value, 0), _num(r.shares, 0), _num(r.price, 4),
            None if pd.isna(r.planned) else int(r.planned == 1), int(bool(r.rare)),
            None if pd.isna(r.track_now) else round(float(r.track_now) * 100, 2),
            0 if pd.isna(r.track_n) else int(r.track_n),
            SIG.form4_url(r.issuer_cik, r.accession) or "", int(r.owner_cik) if pd.notna(r.owner_cik) else 0, far,
        ])
    return {"asof": asof, "rows": rows, "ctx": ctx}


FI = {k: i for i, k in enumerate(["tk", "filed", "traded", "owner", "role", "other", "kind", "value", "shares",
                                   "price", "planned", "rare", "track", "track_n", "url", "ocik", "far"])}
CI = {k: i for i, k in enumerate(["company", "sector", "cap", "fifth", "band", "dist", "level", "fund", "close",
                                   "profile", "iscore", "badge"])}

_INS_TEMPLATE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Insiders &middot; @@ASOF@@</title>
<style>@@CSS@@
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,Segoe UI,Roboto,sans-serif}
.inwrap{padding:16px 18px 40px;max-width:1700px;margin:0 auto}
.inwrap h1{font-size:19px;margin:0 0 2px}
.insub{color:var(--muted);font-size:12.5px;margin-bottom:10px}
.inev{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--warn);border-radius:8px;
  padding:10px 13px;margin:10px 0 14px;font-size:12.5px}
.inev div{margin:3px 0}
.inev details{margin:6px 0}
.inev summary{cursor:pointer;color:var(--accent)}
.evt{margin-top:6px;font-size:12px}
.evt td,.evt th{padding:4px 8px}
tr.ev-ok td{background:var(--posbg)}
tr.ev-neg td{background:var(--negbg)}
.ev{display:inline-block;border-radius:9px;padding:0 6px;font-size:10.5px;font-weight:600;margin-left:3px}
.ev-ok{background:var(--posbg);color:var(--pos)}
.ev-half{background:var(--warnbg);color:var(--warn)}
.ev-no,.ev-na{background:var(--skipbg);color:var(--skip)}
.ev-neg{background:var(--negbg);color:var(--neg)}
.inbar{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:6px}
.inlab{color:var(--muted);font-size:11.5px;margin:0 2px 0 6px}
.insel,#inq,.innum{background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:4px 8px;font:inherit;font-size:12.5px}
.innum{width:70px}
.inchip{background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:999px;padding:3px 10px;
  font:inherit;font-size:11.5px;cursor:pointer}
.inchip:hover{border-color:var(--accent)}
.inchip.on{background:var(--ink);border-color:var(--ink);color:var(--panel)}
.inchip.on .ev{filter:saturate(.6)}
.intw{max-height:76vh;overflow:auto;border:1px solid var(--grid);border-radius:8px}
#intab{width:100%;border-collapse:collapse;font-size:12.5px;min-width:1350px}
#intab th{position:sticky;top:0;z-index:1;background:var(--head);text-align:left;color:var(--muted);font-weight:500;
  font-size:11.5px;padding:6px 8px;border-bottom:1px solid var(--line);cursor:pointer;white-space:nowrap}
#intab th.asc::after{content:" \2191"}
#intab th.desc::after{content:" \2193"}
#intab td{padding:4px 8px;border-bottom:1px solid var(--grid);white-space:nowrap;vertical-align:middle;text-align:left}
#intab td.n,#intab th.n{text-align:right;font-variant-numeric:tabular-nums}
#intab tr.inr{cursor:pointer}
#intab tr.inr:hover,#intab tr.inr.open{background:var(--grid)}
.indet td{background:var(--panel);padding:8px 14px;white-space:normal}
.indet table{font-size:12px;margin:4px 0}
.indet td,.indet th{padding:3px 7px;text-align:right}
.indet td.lab,.indet th.lab{text-align:left}
.badge{display:inline-block;margin-left:5px;padding:0 6px;border-radius:9px;font-size:10.5px;font-weight:600;
  background:var(--blkbg);color:var(--blk)}
.bnd{display:inline-block;padding:0 6px;border-radius:9px;font-size:10.5px;font-weight:600;background:var(--skipbg);color:var(--skip)}
.bnd-AT{background:var(--posbg);color:var(--pos)}
.bnd-NEAR{background:var(--warnbg);color:var(--warn)}
.mut{color:var(--muted)}
.infoot{display:flex;gap:10px;align-items:center;margin-top:8px;color:var(--muted);font-size:11.5px}
</style></head><body>
@@NAV@@
<div class="inwrap">
<h1>Insider buying</h1>
<div class="insub">filings to <b>@@ASOF@@</b> &middot; open-market purchases (SEC Form 4, code P) by the company's own
directors and officers, from our own copy of every Form 4 &middot; <b>@@NF@@ purchase filings</b> in the last @@WMAX@@ days
&middot; ordered by the latest filing unless you sort &middot; a research screen, not a buy list</div>
<div class="inev">@@EVIDENCE@@</div>
<div class="inbar">
  <span class="inlab">window</span><select class="insel" id="inw"><option value="7">7 days</option><option value="14">14 days</option><option value="30" selected>30 days (as measured)</option><option value="60">60 days</option><option value="90">90 days</option></select>
  <span class="inlab">min insiders</span><select class="insel" id="inb"><option value="1" selected>1+</option><option value="2">2+</option><option value="3">3+</option></select>@@EV_B2@@
  <span class="inlab">min $</span><select class="insel" id="inv"><option value="0">any</option><option value="10000" selected>$10K+</option><option value="100000">$100K+</option><option value="250000">$250K+</option><option value="1000000">$1M+</option></select>@@EV_V250@@
  <span class="inlab">role</span><select class="insel" id="inr"><option value="any">any director/officer</option><option value="ceo">CEO or CFO</option><option value="off">officers</option><option value="dir">directors only</option></select>@@EV_CEO@@
  <span class="inlab">10b5-1</span><select class="insel" id="inp"><option value="all">planned + unplanned</option><option value="un">unplanned only</option><option value="pl">planned only</option></select>@@EV_UN@@
</div>
<div class="inbar">
  <span class="inlab">sector</span><select class="insel" id="ins"></select>
  <span class="inlab">market cap</span><select class="insel" id="inc"><option value="all">any</option><option value="micro">under $300M</option><option value="small">$300M&ndash;$2B</option><option value="mid">$2B&ndash;$10B</option><option value="large">$10B+</option></select>
  <button class="inchip" id="cf-small">small companies (bottom 2 liquidity fifths)@@EV_SMALL@@</button>
  <button class="inchip" id="cf-rare">rare buyer (first in 2+ years)@@EV_RARE@@</button>
  <button class="inchip" id="cf-track">buyer with a good record@@EV_TRACK@@</button>
  <button class="inchip" id="cf-ross">Ross's screen@@EV_ROSS@@</button>
  <button class="inchip" id="cf-sup">at/near support</button>
  <button class="inchip" id="cf-funds" title="funds, trusts, companies and holders who are ONLY 10% owners -- left out of every measured test">include funds &amp; 10% owners</button>
  <input id="inq" type="search" placeholder="ticker or company..." autocomplete="off">
  <button class="inchip" id="inclr">clear</button>
</div>
<div class="intw"><table id="intab"><thead><tr>@@HEADS@@</tr></thead><tbody id="inbody"></tbody></table></div>
<div class="infoot"><span id="incount"></span><span>click a row for each filing, with its SEC link and the buyer's record</span></div>
<div class="note">Liquidity fifth = 20-day dollar volume rank among stocks at $2+ trading $1M+/day (1 = least liquid), the
study's size measure. A buyer's record = mean 60-session excess of his or her earlier purchases whose 60 sessions have
ended (2+ needed). &ldquo;Rare&rdquo; = this person's first open-market purchase in this company in 2+ years. A
<span class="mut">?</span> after a price: the reported price is far from our latest close &mdash; another currency (some
foreign filers report in their own), share class, or a split since. Market cap is blank for non-USD filers.</div>
</div>
<script>
(function(){
var F=@@ROWS@@, C=@@CTX@@, FI=@@FI@@, CI=@@CI@@, ASOF='@@ASOF@@';
var $=function(s){return document.getElementById(s)};
var st={w:30,b:1,v:10000,r:'any',p:'all',s:'all',c:'all',small:false,rare:false,track:false,ross:false,sup:false,funds:false,q:''};
var sc='last', sd=-1, open={};
var COLS=[['tk','ticker','s'],['company','company','s'],['sector','sector','s'],['n','insiders','i'],['v','$ bought','usd'],
 ['roles','roles','s'],['last','last filed','s'],['rare','rare','yn'],['track','best record','pp'],['fifth','liquidity fifth','i'],
 ['cap','mkt cap','cap'],['zone','support','zone'],['fund','fundamental','f0'],['iscore','insider score','i']];
function eh(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');}
function cut(days){var d=new Date(ASOF+'T00:00:00Z'); d.setUTCDate(d.getUTCDate()-days); return d.toISOString().slice(0,10);}
function usd(v){if(v==null)return '&ndash;'; var a=Math.abs(v); return a>=1e9?'$'+(v/1e9).toFixed(2)+'B':a>=1e6?'$'+(v/1e6).toFixed(2)+'M':a>=1e3?'$'+(v/1e3).toFixed(0)+'K':'$'+v.toFixed(0);}
function fmt(v,k){
  if(v==null||v==='') return '&ndash;';
  switch(k){case 'i':return String(Math.round(v)); case 'usd':return usd(v); case 'f0':return v.toFixed(0);
    case 'pp':return (v>0?'+':'')+v.toFixed(1)+'pp'; case 'cap':return usd(v); case 'yn':return v?'yes':'&ndash;';
    default:return eh(v);}
}
function roleOk(f){
  var r=f[FI.role];
  if(st.r==='ceo') return r==='CEO'||r==='CFO';
  if(st.r==='off') return ['CEO','CFO','President','COO','Officer'].indexOf(r)>=0;
  if(st.r==='dir') return r==='Director'||r==='Chair';
  return true;
}
function capOk(v){
  if(st.c==='all') return true; if(v==null) return false;
  return st.c==='micro'?v<3e8:st.c==='small'?(v>=3e8&&v<2e9):st.c==='mid'?(v>=2e9&&v<1e10):v>=1e10;
}
function agg(){
  var lo=cut(st.w), by={}, i, f, a;
  for(i=0;i<F.length;i++){
    f=F[i];
    if(f[FI.filed]<=lo) continue;
    if(f[FI.other]&&!st.funds) continue;
    if(!f[FI.other]&&!roleOk(f)) continue;
    if(st.p==='un'&&f[FI.planned]===1) continue;
    if(st.p==='pl'&&f[FI.planned]!==1) continue;
    a=by[f[FI.tk]]||(by[f[FI.tk]]={tk:f[FI.tk],own:{},v:0,roles:{},last:'',rare:0,track:null,fs:[]});
    a.own[f[FI.ocik]]=1; a.v+=f[FI.value]||0; a.roles[f[FI.kind]||f[FI.role]]=1;
    if(f[FI.filed]>a.last) a.last=f[FI.filed];
    if(f[FI.rare]&&!f[FI.other]) a.rare=1;
    if(f[FI.track]!=null&&(a.track==null||f[FI.track]>a.track)) a.track=f[FI.track];
    a.fs.push(f);
  }
  var out=[];
  for(var t in by){
    a=by[t]; var c=C[t]||[]; a.n=Object.keys(a.own).length;
    a.rare=(a.rare&&a.v>=1e4)?1:0;
    a.company=c[CI.company]; a.sector=c[CI.sector]; a.cap=c[CI.cap]; a.fifth=c[CI.fifth]; a.fund=c[CI.fund];
    a.iscore=c[CI.iscore]; a.zone=c[CI.band]?(c[CI.band]==='AT'?0:c[CI.band]==='NEAR'?1:2):null;
    a.roles=Object.keys(a.roles).join(', ');
    if(a.n<st.b||a.v<st.v) continue;
    if(st.s!=='all'&&a.sector!==st.s) continue;
    if(!capOk(a.cap)) continue;
    if(st.small&&!(a.fifth!=null&&a.fifth<=2&&a.v>=1e4)) continue;
    if(st.rare&&!a.rare) continue;
    if(st.track&&!(a.track!=null&&a.track>0)) continue;
    if(st.ross&&!(a.n>=2&&a.v>=2.5e5&&a.fifth!=null&&a.fifth<=4)) continue;
    if(st.sup&&!(a.zone===0||a.zone===1)) continue;
    if(st.q&&(t.toLowerCase().indexOf(st.q)<0&&String(a.company||'').toLowerCase().indexOf(st.q)<0)) continue;
    out.push(a);
  }
  return out;
}
function det(a){
  var c=C[a.tk]||[], h='<tr class="indet"><td colspan="'+COLS.length+'"><table><thead><tr><th class="lab">filed</th><th class="lab">traded</th><th class="lab">who</th><th class="lab">role</th><th>$ value</th><th>shares</th><th>price</th><th class="lab">10b5-1</th><th class="lab">rare</th><th>buyer record</th><th class="lab">Form 4</th></tr></thead><tbody>';
  a.fs.sort(function(x,y){return x[FI.filed]<y[FI.filed]?1:-1;});
  a.fs.forEach(function(f){
    h+='<tr><td class="lab">'+f[FI.filed]+'</td><td class="lab">'+(f[FI.traded]||'&ndash;')+'</td><td class="lab">'+eh(f[FI.owner])+'</td><td class="lab">'+eh(f[FI.kind]||f[FI.role])+'</td>'
      +'<td>'+usd(f[FI.value])+'</td><td>'+(f[FI.shares]==null?'&ndash;':Math.round(f[FI.shares]).toLocaleString())+'</td>'
      +'<td>'+(f[FI.price]==null?'&ndash;':f[FI.price].toFixed(2))+(f[FI.far]?' <span class="mut" title="far from our latest close: another currency, class or a split?">?</span>':'')+'</td>'
      +'<td class="lab">'+(f[FI.planned]===1?'planned':f[FI.planned]===0?'no':'&ndash;')+'</td><td class="lab">'+(f[FI.rare]?'yes':'&ndash;')+'</td>'
      +'<td>'+(f[FI.track]==null?'&ndash;':fmt(f[FI.track],'pp')+' <span class="mut">('+f[FI.track_n]+')</span>')+'</td>'
      +'<td class="lab">'+(f[FI.url]?'<a href="'+eh(f[FI.url])+'" target="_blank" rel="noopener">SEC</a>':'&ndash;')+'</td></tr>';
  });
  h+='</tbody></table><div class="mut">'
    +(c[CI.band]?'Nearest Zones support '+c[CI.level].toFixed(2)+' ('+c[CI.band]+', '+(c[CI.dist]*100).toFixed(1)+'% below the close). ':'No Zones support level below the price. ')
    +(c[CI.badge]?'<b>'+eh(c[CI.badge])+'.</b> ':'')
    +(c[CI.profile]?'<a href="../stock/'+encodeURIComponent(a.tk)+'.html">open the profile</a>':'no profile page built for this ticker')+'</div></td></tr>';
  return h;
}
function render(){
  var m=agg();
  m.sort(function(a,b){var x=a[sc],y=b[sc];
    if(x==null&&y==null) return a.tk<b.tk?-1:1; if(x==null) return 1; if(y==null) return -1;
    if(x===y) return a.tk<b.tk?-1:1; return (x>y?1:-1)*sd;});
  var h='';
  m.forEach(function(a){
    var c=C[a.tk]||[];
    h+='<tr class="inr'+(open[a.tk]?' open':'')+'" data-t="'+eh(a.tk)+'">';
    COLS.forEach(function(col){
      var v=a[col[0]], cell;
      if(col[0]==='tk') cell='<b>'+eh(a.tk)+'</b>'+(c[CI.badge]?'<span class="badge" title="'+eh(c[CI.badge])+'">at support</span>':'');
      else if(col[0]==='zone') cell=c[CI.band]?'<span class="bnd bnd-'+c[CI.band]+'">'+c[CI.band]+'</span> <span class="mut">'+(c[CI.dist]*100).toFixed(1)+'%</span>':'&ndash;';
      else if(col[0]==='company') cell=eh(String(a.company||'').slice(0,34));
      else cell=fmt(v,col[2]);
      h+='<td class="'+(['s','zone','yn'].indexOf(col[2])>=0?'':'n')+'">'+cell+'</td>';
    });
    h+='</tr>'; if(open[a.tk]) h+=det(a);
  });
  $('inbody').innerHTML=h;
  $('incount').textContent=m.length+(m.length===1?' company':' companies')+' with buying in the window';
}
(function(){
  var cnt={}; for(var t in C){var s=C[t][CI.sector]; cnt[s]=(cnt[s]||0)+1;}
  var o='<option value="all">all sectors</option>';
  Object.keys(cnt).sort().forEach(function(s){o+='<option value="'+eh(s)+'">'+eh(s)+'</option>';});
  $('ins').innerHTML=o;
  var TIP={iscore:@@ISCORE_TIP@@, fifth:'20-day dollar-volume rank among stocks at $2+ trading $1M+/day: 1 = least liquid; 1-2 = the measured SMALL group', track:'best buyer record in the window: mean 60-session excess of earlier purchases whose 60 sessions have ended', rare:'a buyer made a first open-market purchase in this company in 2+ years ($10K+ bought)'};
  var hh=''; COLS.forEach(function(c){hh+='<th data-c="'+c[0]+'" class="'+(['s','zone','yn'].indexOf(c[2])>=0?'':'n')+(c[0]===sc?(sd>0?' asc':' desc'):'')+'"'+(TIP[c[0]]?' title="'+eh(TIP[c[0]])+'"':'')+'>'+c[1]+'</th>';});
  document.querySelector('#intab thead tr').innerHTML=hh;
})();
function sel(id,key,num){$(id).addEventListener('change',function(e){st[key]=num?+e.target.value:e.target.value;render();});}
sel('inw','w',1); sel('inb','b',1); sel('inv','v',1); sel('inr','r'); sel('inp','p'); sel('ins','s'); sel('inc','c');
['small','rare','track','ross','sup','funds'].forEach(function(k){$('cf-'+k).addEventListener('click',function(){st[k]=!st[k];this.classList.toggle('on',st[k]);render();});});
$('inq').addEventListener('input',function(e){st.q=e.target.value.trim().toLowerCase();render();});
$('inclr').addEventListener('click',function(){
  st={w:30,b:1,v:10000,r:'any',p:'all',s:'all',c:'all',small:false,rare:false,track:false,ross:false,sup:false,funds:false,q:''};
  $('inw').value='30';$('inb').value='1';$('inv').value='10000';$('inr').value='any';$('inp').value='all';$('ins').value='all';$('inc').value='all';$('inq').value='';
  document.querySelectorAll('.inchip[id^=cf-]').forEach(function(b){b.classList.remove('on');}); render();});
$('inbody').addEventListener('click',function(e){
  if(e.target.closest('a')) return; var tr=e.target.closest('tr.inr'); if(!tr) return;
  var t=tr.dataset.t; open[t]=!open[t]; if(!open[t]) delete open[t]; render();});
document.querySelector('#intab thead').addEventListener('click',function(e){
  var th=e.target.closest('th[data-c]'); if(!th) return; var c=th.dataset.c;
  if(c===sc) sd=-sd; else {sc=c; var k=COLS.filter(function(x){return x[0]===c;})[0][2]; sd=(k==='s'||k==='zone')?1:-1;}
  document.querySelectorAll('#intab th').forEach(function(o){o.classList.remove('asc','desc');});
  th.classList.add(sd>0?'asc':'desc'); render();});
render();
})();
</script></body></html>
"""


def _iscore_tip() -> str:
    """The insider score column's tooltip, from the walk-forward file."""
    try:
        import insider_score_wf
        wf = insider_score_wf.read() or {}
    except Exception:                                            # noqa: BLE001
        wf = {}
    c, vb = wf.get("chosen") or {}, wf.get("vs_baseline") or {}
    if not c:
        return "no combined score: the walk-forward has not run"
    return (f"how many of {{{', '.join(c.get('flags', []))}}} hold over 30 days (0-2). Walk-forward "
            f"{wf.get('verdict')}: test t {(c.get('test') or {}).get('t')}; "
            f"{vb.get('reading', '')} (any $10K+ purchase: t {vb.get('test_t_baseline')}).")


def build_insiders(asof: str | None = None) -> tuple[Path, int]:
    asof = asof or calendar_us.last_closed_session()
    p = insider_payload(asof)
    ev = SIG.evidence()
    html = (_INS_TEMPLATE.replace("@@CSS@@", ui.CSS).replace("@@NAV@@", ui.nav("insider", 1))
            .replace("@@ASOF@@", ui.esc(asof)).replace("@@NF@@", f"{len(p['rows']):,}")
            .replace("@@WMAX@@", str(WINDOW_MAX)).replace("@@EVIDENCE@@", insider_evidence(ev))
            .replace("@@HEADS@@", "")
            .replace("@@EV_B2@@", _chip_ev(ev, "b2")).replace("@@EV_V250@@", _chip_ev(ev, "v250"))
            .replace("@@EV_CEO@@", _chip_ev(ev, "ceo")).replace("@@EV_UN@@", _chip_ev(ev, "unplanned"))
            .replace("@@EV_SMALL@@", _chip_ev(ev, "small")).replace("@@EV_RARE@@", _chip_ev(ev, "rare"))
            .replace("@@EV_TRACK@@", _chip_ev(ev, "track")).replace("@@EV_ROSS@@", _chip_ev(ev, "ross"))
            .replace("@@ROWS@@", _js(p["rows"])).replace("@@CTX@@", _js(p["ctx"]))
            .replace("@@FI@@", _js(FI)).replace("@@CI@@", _js(CI))
            .replace("@@ISCORE_TIP@@", _js(_iscore_tip())))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "latest.html"
    out.write_text(html, encoding="utf-8")
    return out, len(p["rows"])


# ============================================================ the House page
HI = {k: i for i, k in enumerate(["filed", "traded", "lag", "member", "dist", "owner", "tk", "asset", "atype", "ttype",
                                   "lo", "hi", "kind", "opt", "url", "band", "profile", "big"])}


def house_payload(asof: str) -> dict:
    h = SIG.house()
    lo = (date.fromisoformat(asof) - timedelta(days=HOUSE_DAYS)).isoformat()
    w = h[(h.filing_date > lo) & (h.filing_date <= asof)].sort_values(["filing_date", "member"], ascending=[False, True])
    z = SIG.zone_status(asof)
    prof = {p.stem for p in config.REPORTS_STOCK.glob("*.html")} if config.REPORTS_STOCK.exists() else set()
    rows = []
    for r in w.itertuples(index=False):
        tk = _txt(r.ticker).upper()
        big = int(r.asset_type == "ST" and r.ttype == "P" and r.kind == "trade" and (r.amount_lo or 0) >= SIG.HOUSE_BIG)
        rows.append([r.filing_date, _txt(r.trans_date), None if pd.isna(r.lag_days) else int(r.lag_days),
                     _txt(r.member), _txt(r.state_dst), _txt(r.owner), tk, _txt(r.asset)[:70], _txt(r.asset_type),
                     _txt(r.ttype), _num(r.amount_lo, 0), _num(r.amount_hi, 0), _txt(r.kind), _txt(r.option_kind),
                     SIG.ptr_url(r.year, r.doc_id) or "", _txt(z.at[tk, "band"]) if tk in z.index else "",
                     1 if tk in prof else 0, big])
    led = None
    try:
        import congress as CG
        led = CG._ledger()
        led["year"] = led.year.astype(int)
        led = led.groupby("year").status.value_counts().unstack(fill_value=0)
    except Exception:                                            # noqa: BLE001
        pass
    return {"asof": asof, "rows": rows, "ledger": led}


def house_evidence(ev: dict, led: pd.DataFrame | None) -> str:
    s = ev.get("house")
    if not s:
        return "<div><b>Not measured yet.</b> No study file.</div>"
    big = _res(ev, "house", "purchase >= $50,001") or {}
    anyp = _res(ev, "house", "any member stock purchase") or {}
    cf = {int(r["h"]): r for r in s.get("counterfactual", [])}.get(H, {})
    lag = s.get("report_lag_days") or {}
    sale = _res(ev, "house", "sale >= $15,001 (selling)") or {}
    any_head = ("The average member purchase predicts nothing" if abs(anyp.get("t") or 0) < 2
                else f"The average member purchase reads t {anyp.get('t', float('nan')):+.2f}")
    big_cls, big_words = verdict(big)
    big_head = ("Purchases of $50,001 or more are the exception" if big_cls == "ok"
                else f"Purchases of $50,001 or more: {big_words}")
    sale_txt = ("Selling was not bearish" if (sale.get("t") or 0) > -2
                else "Selling measured bearish") + f" (t {sale.get('t', float('nan')):+.2f})"
    out = [f"<div><b>What our own history says</b> (congress_study.py, measured {ui.esc(str(s.get('measured_on'))[:10])}; "
           f"{s.get('tests')} tests, about {s.get('expected_by_chance')} would pass by chance). Event = the day the "
           f"report was FILED with the Clerk, entered at the next close, against same-liquidity stocks for 60 sessions.</div>",
           f"<div><b>{any_head}</b>: t {anyp.get('t', float('nan')):+.2f}, median "
           f"{anyp.get('median_pp', float('nan')):+.2f}pp &mdash; not even entered on the TRADE date, which no outsider "
           f"can do (t {cf.get('t', float('nan')):+.2f}), so the reporting delay is not what hides an edge.</div>",
           f"<div><b>{ui.esc(big_head)}</b> &mdash; highlighted below: {big.get('episodes', 0):,} "
           f"episodes, mean {big.get('mean_pp', float('nan')):+.2f}pp, <b>median {big.get('median_pp', float('nan')):+.2f}pp</b>, "
           f"{big.get('beat_pool_pct', float('nan')):.0f}% beat their peers, <b>t {big.get('t', float('nan')):+.2f}</b> "
           f"(halves {(big.get('first_half') or {}).get('t', float('nan')):+.2f} / "
           f"{(big.get('second_half') or {}).get('t', float('nan')):+.2f})"
           + ("; the typical (median) event beat its peers." if (big.get("median_pp") or 0) > 0 else ".")
           + "</div>",
           f"<div><b>Reports arrive late:</b> trade to filing median {lag.get('median', float('nan')):.0f} days, 10% over "
           f"{lag.get('p90', float('nan')):.0f}. {sale_txt}.</div>",
           '<details><summary>every House test</summary>' + evidence_table(ev, HOUSE_TESTS, "house") + "</details>"]
    cov = ""
    if led is not None and len(led):
        yrs = sorted(led.index)[-3:]
        cov = "; ".join(f"{y}: {int(led.loc[y].get('ok', 0)):,} read, {int(led.loc[y].get('scanned', 0)):,} paper "
                        f"(scanned images, not read), {int(led.loc[y].get('empty', 0)):,} with no trades found"
                        for y in yrs)
    out.append("<div><b>What is missing:</b> THE SENATE &mdash; its disclosure site refuses automated access, and "
               "getting past that would mean defeating its bot protection. PAPER filings are scanned images, counted "
               f"but not read{(' (' + cov + ')') if cov else ''}. Amounts are the form's ranges.</div>")
    out.append("<div><b>Use:</b> 5 U.S.C. 13107(c) bars commercial use of these reports. This page is built for the "
               "owner's own research, stays on this machine, and is never published. Not advice.</div>")
    return "".join(out)


_HOUSE_TEMPLATE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>House trades &middot; @@ASOF@@</title>
<style>@@CSS@@
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,Segoe UI,Roboto,sans-serif}
.hwrap{padding:16px 18px 40px;max-width:1700px;margin:0 auto}
.hwrap h1{font-size:19px;margin:0 0 2px}
.hsub{color:var(--muted);font-size:12.5px;margin-bottom:10px}
.hev{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--warn);border-radius:8px;
  padding:10px 13px;margin:10px 0 14px;font-size:12.5px}
.hev div{margin:3px 0}
.hev summary{cursor:pointer;color:var(--accent)}
.evt{margin-top:6px;font-size:12px}
.evt td,.evt th{padding:4px 8px}
tr.ev-ok td{background:var(--posbg)}
tr.ev-neg td{background:var(--negbg)}
.hbar{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:8px}
.hlab{color:var(--muted);font-size:11.5px;margin:0 2px 0 6px}
.hsel,#hq{background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:4px 8px;font:inherit;font-size:12.5px}
.hchip{background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:999px;padding:3px 10px;font:inherit;font-size:11.5px;cursor:pointer}
.hchip.on{background:var(--ink);border-color:var(--ink);color:var(--panel)}
.htw{max-height:76vh;overflow:auto;border:1px solid var(--grid);border-radius:8px}
#htab{width:100%;border-collapse:collapse;font-size:12.5px;min-width:1250px}
#htab th{position:sticky;top:0;z-index:1;background:var(--head);text-align:left;color:var(--muted);font-weight:500;font-size:11.5px;padding:6px 8px;border-bottom:1px solid var(--line);cursor:pointer;white-space:nowrap}
#htab th.asc::after{content:" \2191"}
#htab th.desc::after{content:" \2193"}
#htab td{padding:4px 8px;border-bottom:1px solid var(--grid);white-space:nowrap;text-align:left}
#htab td.n,#htab th.n{text-align:right;font-variant-numeric:tabular-nums}
#htab tr.big td{background:var(--posbg)}
.bnd{display:inline-block;padding:0 6px;border-radius:9px;font-size:10.5px;font-weight:600;background:var(--skipbg);color:var(--skip)}
.bnd-AT{background:var(--posbg);color:var(--pos)}
.bnd-NEAR{background:var(--warnbg);color:var(--warn)}
.mut{color:var(--muted)}
.hfoot{margin-top:8px;color:var(--muted);font-size:11.5px}
</style></head><body>
@@NAV@@
<div class="hwrap">
<h1>House members' trades</h1>
<div class="hsub">reports filed to <b>@@ASOF@@</b> &middot; Periodic Transaction Reports from the House Clerk, read by
our own parser &middot; <b>@@N@@ trades</b> filed in the last @@DAYS@@ days &middot; <span style="background:var(--posbg);padding:0 5px;border-radius:4px">green</span>
= a stock purchase of $50,001 or more, the measured exception &middot; newest filing first</div>
<div class="hev">@@EVIDENCE@@</div>
<div class="hbar">
  <span class="hlab">filed in</span><select class="hsel" id="hw"><option value="30">30 days</option><option value="60">60 days</option><option value="90" selected>90 days</option><option value="180">180 days</option><option value="365">365 days</option></select>
  <span class="hlab">side</span><select class="hsel" id="hs"><option value="P" selected>purchases</option><option value="S">sales</option><option value="all">all</option></select>
  <span class="hlab">asset</span><select class="hsel" id="ha"><option value="ST" selected>stocks</option><option value="OP">options</option><option value="all">all</option></select>
  <span class="hlab">amount</span><select class="hsel" id="hm"><option value="0" selected>any</option><option value="15001">$15,001+</option><option value="50001">$50,001+ (measured)</option><option value="250001">$250,001+</option></select>
  <span class="hlab">owner</span><select class="hsel" id="ho"><option value="all">all</option><option value="self">member</option><option value="SP">spouse</option><option value="JT">joint</option><option value="DC">dependent child</option></select>
  <button class="hchip" id="hsup">at/near support</button>
  <button class="hchip" id="hdec" title="dividend reinvestments, gifts, exercises and transfers are not decisions to buy and are left out of every measured test">include non-trades</button>
  <input id="hq" type="search" placeholder="member or ticker..." autocomplete="off">
</div>
<div class="htw"><table id="htab"><thead><tr></tr></thead><tbody id="hbody"></tbody></table></div>
<div class="hfoot"><span id="hcount"></span> &middot; lag = days from the trade to the filing &middot; PTR = the Clerk's PDF of the report</div>
</div>
<script>
(function(){
var R=@@ROWS@@, HI=@@HI@@, ASOF='@@ASOF@@';
var $=function(s){return document.getElementById(s)};
var st={w:90,s:'P',a:'ST',m:0,o:'all',sup:false,dec:false,q:''}, sc='filed', sd=-1;
var COLS=[['filed','filed','s'],['traded','traded','s'],['lag','lag d','i'],['member','member','s'],['dist','district','s'],
 ['owner','owner','s'],['tk','ticker','tk'],['asset','asset','s'],['atype','type','s'],['ttype','side','s'],['lo','amount','amt'],
 ['kind','kind','s'],['band','support','band'],['url','report','url']];
function eh(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');}
function cut(days){var d=new Date(ASOF+'T00:00:00Z'); d.setUTCDate(d.getUTCDate()-days); return d.toISOString().slice(0,10);}
function amt(r){var lo=r[HI.lo],hi=r[HI.hi]; if(lo==null) return '&ndash;'; var f=function(v){return '$'+Math.round(v).toLocaleString();}; return hi==null?f(lo)+'+':f(lo)+' &ndash; '+f(hi);}
function ok(r,lo){
  if(r[HI.filed]<=lo) return false;
  if(st.s==='P'&&r[HI.ttype]!=='P') return false;
  if(st.s==='S'&&r[HI.ttype].charAt(0)!=='S') return false;
  if(st.a!=='all'&&r[HI.atype]!==st.a) return false;
  if(st.m&&!(r[HI.lo]>=st.m)) return false;
  if(st.o!=='all'&&r[HI.owner]!==st.o) return false;
  if(!st.dec&&r[HI.kind]!=='trade') return false;
  if(st.sup&&!(r[HI.band]==='AT'||r[HI.band]==='NEAR')) return false;
  if(st.q&&r[HI.member].toLowerCase().indexOf(st.q)<0&&r[HI.tk].toLowerCase().indexOf(st.q)<0) return false;
  return true;
}
function render(){
  var lo=cut(st.w), m=R.filter(function(r){return ok(r,lo);}), ix=HI[sc];
  m.sort(function(a,b){var x=a[ix],y=b[ix]; if(x==null&&y==null) return 0; if(x==null) return 1; if(y==null) return -1;
    if(x===y) return a[HI.member]<b[HI.member]?-1:1; return (x>y?1:-1)*sd;});
  var h='';
  m.forEach(function(r){
    h+='<tr'+(r[HI.big]?' class="big" title="a stock purchase of $50,001 or more: the measured exception"':'')+'>';
    COLS.forEach(function(c){
      var v=r[HI[c[0]]], cell;
      if(c[2]==='tk') cell=v?(r[HI.profile]?'<a href="../stock/'+encodeURIComponent(v)+'.html"><b>'+eh(v)+'</b></a>':'<b>'+eh(v)+'</b>'):'&ndash;';
      else if(c[2]==='amt') cell=amt(r);
      else if(c[2]==='band') cell=v?'<span class="bnd bnd-'+v+'">'+v+'</span>':'&ndash;';
      else if(c[2]==='url') cell=v?'<a href="'+eh(v)+'" target="_blank" rel="noopener">PTR</a>':'&ndash;';
      else if(c[0]==='owner') cell={self:'member',SP:'spouse',JT:'joint',DC:'child'}[v]||eh(v);
      else if(c[0]==='kind') cell=v==='trade'?'':eh(v)+(r[HI.opt]?' ('+eh(r[HI.opt])+')':'');
      else cell=(v==null||v==='')?'&ndash;':eh(v);
      if(c[0]==='atype'&&r[HI.opt]) cell+=' '+eh(r[HI.opt]);
      h+='<td class="'+(c[2]==='i'||c[2]==='amt'?'n':'')+'">'+cell+'</td>';
    });
    h+='</tr>';
  });
  $('hbody').innerHTML=h;
  var nb=m.filter(function(r){return r[HI.big];}).length;
  $('hcount').textContent=m.length+' trade(s) shown, '+nb+' of them $50,001+ stock purchases';
}
var hh=''; COLS.forEach(function(c){hh+='<th data-c="'+c[0]+'" class="'+(c[2]==='i'||c[2]==='amt'?'n':'')+(c[0]===sc?' desc':'')+'">'+c[1]+'</th>';});
document.querySelector('#htab thead tr').innerHTML=hh;
function sel(id,key,num){$(id).addEventListener('change',function(e){st[key]=num?+e.target.value:e.target.value;render();});}
sel('hw','w',1); sel('hs','s'); sel('ha','a'); sel('hm','m',1); sel('ho','o');
$('hsup').addEventListener('click',function(){st.sup=!st.sup;this.classList.toggle('on',st.sup);render();});
$('hdec').addEventListener('click',function(){st.dec=!st.dec;this.classList.toggle('on',st.dec);render();});
$('hq').addEventListener('input',function(e){st.q=e.target.value.trim().toLowerCase();render();});
document.querySelector('#htab thead').addEventListener('click',function(e){
  var th=e.target.closest('th[data-c]'); if(!th) return; var c=th.dataset.c;
  if(c==='url') return;
  if(c===sc) sd=-sd; else {sc=c; sd=(c==='lo'||c==='lag')?-1:1;}
  document.querySelectorAll('#htab th').forEach(function(o){o.classList.remove('asc','desc');});
  th.classList.add(sd>0?'asc':'desc'); render();});
render();
})();
</script></body></html>
"""


def build_house(asof: str | None = None) -> tuple[Path, int]:
    asof = asof or calendar_us.last_closed_session()
    p = house_payload(asof)
    ev = SIG.evidence()
    html = (_HOUSE_TEMPLATE.replace("@@CSS@@", ui.CSS).replace("@@NAV@@", ui.nav("house", 1))
            .replace("@@ASOF@@", ui.esc(asof)).replace("@@N@@", f"{len(p['rows']):,}")
            .replace("@@DAYS@@", str(HOUSE_DAYS)).replace("@@EVIDENCE@@", house_evidence(ev, p["ledger"]))
            .replace("@@ROWS@@", _js(p["rows"])).replace("@@HI@@", _js(HI)))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "house.html"
    out.write_text(html, encoding="utf-8")
    return out, len(p["rows"])


def build(asof: str | None = None) -> tuple[int, int]:
    asof = asof or calendar_us.last_closed_session()
    _p, n = build_insiders(asof)
    _q, m = build_house(asof)
    return n, m


# ============================================================ selftest / CLI
def selftest(asof: str | None = None) -> int:
    asof = asof or calendar_us.last_closed_session()
    # The verdict rule, on fixed inputs: it decides every label on both pages.
    assert verdict({"t": 3.69, "first_half": {"t": 1.67}, "second_half": {"t": 3.58}})[0] == "ok"
    assert verdict({"t": 2.6, "first_half": {"t": -0.5}, "second_half": {"t": 3.2}})[0] == "half"
    assert verdict({"t": 2.6, "first_half": {"t": 0.57}, "second_half": {"t": 3.2}})[0] == "half"
    assert verdict({"t": 2.8, "first_half": {"t": float("nan")}, "second_half": {"t": 2.8}})[0] == "half"
    assert verdict({"t": 1.49, "first_half": {"t": 0.76}, "second_half": {"t": 1.43}})[0] == "no"
    assert verdict({"t": -2.5})[0] == "neg" and verdict(None)[0] == "na"
    ev = SIG.evidence()
    assert ev.get("insider") and ev.get("house"), "a study summary is missing"
    missing = [k for k, t in FILTER_TESTS.items() if (t, H) not in ev["insider"]["by"]]
    assert not missing, f"filters whose study test is not in the summary: {missing}"
    hm = [t for t in HOUSE_TESTS if (t, H) not in ev["house"]["by"]]
    assert not hm, f"House tests not in the summary: {hm}"
    p = insider_payload(asof)
    rows, ctx = p["rows"], p["ctx"]
    assert rows, "no insider purchase filings in the window"
    assert all(len(r) == len(FI) for r in rows), "filing row width disagrees with FI"
    assert all(len(v) == len(CI) for v in ctx.values()), "context width disagrees with CI"
    lo = (date.fromisoformat(asof) - timedelta(days=WINDOW_MAX)).isoformat()
    assert all(lo < r[FI["filed"]] <= asof for r in rows), "a filing outside the window"
    assert all(r[FI["tk"]] in ctx for r in rows), "a filing whose ticker has no context"
    assert all(r[FI["value"]] is None or r[FI["value"]] > 0 for r in rows), "a non-positive purchase"
    assert all(c[CI["fifth"]] is None or 1 <= c[CI["fifth"]] <= 5 for c in ctx.values())
    assert all(c[CI["band"]] in ("", "AT", "NEAR", "APPROACHING") for c in ctx.values())
    assert all(not r[FI["url"]] or r[FI["url"]].startswith("https://www.sec.gov/Archives/edgar/data/") for r in rows)
    json.dumps([rows, ctx], allow_nan=False)
    hp = house_payload(asof)
    assert all(len(r) == len(HI) for r in hp["rows"]), "House row width disagrees with HI"
    assert all(r[HI["big"]] == 0 or (r[HI["ttype"]] == "P" and r[HI["lo"]] >= SIG.HOUSE_BIG) for r in hp["rows"])
    json.dumps(hp["rows"], allow_nan=False)
    n_dno = sum(1 for r in rows if not r[FI["other"]])
    print(f"insider page selftest OK  ({len(rows)} filings on {asof}, {n_dno} by directors/officers, "
          f"{len(ctx)} tickers, {sum(1 for c in ctx.values() if c[CI['badge']])} with the support badge; "
          f"House {len(hp['rows'])} trades, {sum(r[HI['big']] for r in hp['rows'])} $50K+ purchases)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the Insiders and House trades pages.")
    ap.add_argument("--asof", default=None)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest(a.asof)
    n, m = build(a.asof)
    print(f"  wrote {OUT_DIR / 'latest.html'} ({n} filings) and {OUT_DIR / 'house.html'} ({m} trades)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
