#!/usr/bin/env python
"""The Floor-retest page: stocks in the SOFI pattern, drawn.

The pattern (defined and measured in floorretest_study.py, which this page imports so
the page and the study cannot drift apart): a big run (>= 2x within 2 years), a deep
pullback (35-70% below that high, still >= 30% above where the run started), a floor
tested >= 3 times over >= 3 months in the last 9 months, and the close back within 6%
of it. Asked for on 2026-10-14 because neither existing screen names it: the bounce
screen wants a parabolic run and a fall to the run's own base, the good-level rule
wants a stock within 20% of its 1-year high.

THE MEASURED RESULT IS PRINTED AT THE TOP, whatever it is, from
data/_floor_study_summary.json. A category shipped without its accuracy would be the
one thing this project has repeatedly promised not to do.

HONESTY RULES (as candidates_page.py): a missing value is a dash, never zero; rows are
ordered by distance to the floor, never by a score; the page refuses to publish if a
row breaks the definition it was selected by.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import calendar_us
import config
import dataset
import floorretest_study as F
import macro
import ui
from zones_page import _ESC

SPARK_N = 300            # ~14 months: shows the run's tail, the fall and the floor
SUMMARY = F.SUMMARY

COLS = [
    ("ticker", "ticker", "click a row for the larger chart and the story", "s"),
    ("sector", "sector", "sector from the SEC SIC map", "s"),
    ("price", "price", "close on the session shown", "f2"),
    ("floor", "floor", "lowest low of the last 9 months -- the dashed line", "f2"),
    ("above", "% above floor", "how far the close is above the floor (pattern requires <= 6%)", "p1"),
    ("tests", "floor tests", "confirmed swing lows within 3% of the floor, >= 10 sessions apart, last 9 months", "i"),
    ("months", "months tested", "first to last floor test", "f1"),
    ("run_x", "run", "the 2-year high divided by the lowest low before it", "x"),
    ("off_hi", "below the high", "how far the close is below that high (pattern: 35-70%)", "p0"),
    ("quiet", "quieting", "yes = 20-day range AND 20-day volume both below their longer averages", "s"),
    ("tier", "tier", "match = the measured pattern; near miss = one condition short (named), within a fixed slack", "s"),
    ("fund", ui.label("fund_score"), ui.tooltip("fund_score"), "f0"),
    ("hype", ui.label("hype_score"), ui.tooltip("hype_score"), "f0"),
    ("sent", ui.label("sent_rank"), "news sentiment percentile 0-100; blank = no news in 30 days", "f0"),
    ("vol", "$M / day", "20-day average dollar volume, millions (pattern requires >= $10M)", "vol"),
]
IX = {c[0]: i for i, c in enumerate(COLS)}
EXTRA = ["peak", "peak_date", "start", "start_date", "badge"]


def _num(v, nd: int = 5):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if not np.isfinite(f) else round(f, nd)


def _scores(asof: str) -> pd.DataFrame:
    s = pd.read_parquet(config.SCORES / f"{asof[:7]}.parquet",
                        columns=["session", "module", "metric", "ticker", "value"])
    s = s[(s.session == asof) & (((s.module == "fundamental") & (s.metric == "fund_score"))
                                 | ((s.module == "hype") & (s.metric == "hype_score"))
                                 | ((s.module == "sentiment") & (s.metric == "sent_rank")))]
    return s.pivot_table(index="ticker", columns="metric", values="value", aggfunc="first")


def payload(asof: str) -> dict:
    c = F.current(asof)
    if c.empty:
        return {"asof": asof, "rows": [], "dates": [], "series": {}}
    # HARD INVARIANTS on the FULL matches: a row that breaks the definition means the
    # list is wrong. Near misses are checked against their own (looser) slack below.
    bad = []
    nm = c[~c.pattern.astype(bool)]
    if (nm.close > nm.floor * F.NEAR["at_floor"] + 1e-9).any() or not nm.near.astype(bool).all():
        bad.append("a near miss is outside its slack")
    c_all, c = c, c[c.pattern.astype(bool)]
    if (c.close > c.floor * F.AT_FLOOR + 1e-9).any():
        bad.append("a row is more than 6% above its floor")
    if (c.n_tests < F.MIN_TESTS).any() or (c.span < F.MIN_SPAN).any():
        bad.append("a floor has too few tests or too short a span")
    if (c.run_x < F.RUN_X).any() or (c.dv20 < F.LIQUID).any():
        bad.append("a row has too small a run or too little volume")
    if ((c.off_hi < F.OFF_HI_MIN) | (c.off_hi > F.OFF_HI_MAX)).any():
        bad.append("a row is outside the 35-70% pullback band")
    if bad:
        raise RuntimeError("; ".join(bad))
    c = c_all

    sc = _scores(asof)
    sec = macro.load_sector_map().drop_duplicates("ticker").set_index("ticker")["sector"]
    ses = [s for s in calendar_us.all_sessions() if s <= asof][-SPARK_N:]
    tick = c.ticker.tolist()
    d = dataset.panel(tick, "1d", start=ses[0], end=asof)
    series = {}
    for t in tick:
        b = d.get(t) if isinstance(d, dict) else None
        if b is None or not len(b):
            continue
        cl = pd.Series(b["close"].astype(float).values, index=b["date"].astype(str).str[:10].values)
        cl = cl[~cl.index.duplicated(keep="last")].reindex(ses)
        series[t] = [None if not np.isfinite(x) else round(float(x), 2) for x in cl.tolist()]

    c["above"] = c.close / c.floor - 1.0
    c = c.sort_values(["above", "ticker"]).reset_index(drop=True)
    try:
        import insider_signals as SIG
        badges = SIG.badges(asof)
    except Exception:                                            # noqa: BLE001
        badges = {}
    rows = []
    for r in c.itertuples(index=False):
        sent = _num(sc.at[r.ticker, "sent_rank"]) if (r.ticker in sc.index and "sent_rank" in sc) else None
        row = [None] * len(COLS)
        row[IX["ticker"]] = r.ticker
        s_ = sec.get(r.ticker)
        row[IX["sector"]] = s_ if isinstance(s_, str) and s_ else "Other"
        row[IX["price"]] = _num(r.close, 2)
        row[IX["floor"]] = _num(r.floor, 2)
        row[IX["above"]] = _num(r.above)
        row[IX["tests"]] = int(r.n_tests)
        row[IX["months"]] = round(float(r.span) / 21.0, 1)
        row[IX["run_x"]] = _num(r.run_x, 2)
        row[IX["off_hi"]] = _num(r.off_hi)
        row[IX["quiet"]] = "yes" if bool(r.quiet) else "no"
        row[IX["tier"]] = "match" if bool(r.pattern) else f"near miss: {r.missing}"
        row[IX["fund"]] = _num(sc.at[r.ticker, "fund_score"], 1) if (r.ticker in sc.index and "fund_score" in sc) else None
        row[IX["hype"]] = _num(sc.at[r.ticker, "hype_score"], 1) if (r.ticker in sc.index and "hype_score" in sc) else None
        row[IX["sent"]] = None if sent is None else round(sent * 100, 1)
        row[IX["vol"]] = _num(r.dv20, 0)
        row += [_num(r.peak, 2), r.peak_date, _num(r.start, 2), r.start_date, badges.get(r.ticker, "")]
        rows.append(row)
    return {"asof": asof, "rows": rows, "dates": ses, "series": series}


def _evidence() -> str:
    if not SUMMARY.exists():
        return ("<div><b>Not measured yet.</b> This category has no measured accuracy on file; "
                "treat every row as unverified.</div>")
    s = json.loads(SUMMARY.read_text(encoding="utf-8"))
    ev = s.get("events", {})
    pat, qt = ev.get("pattern", {}), ev.get("pattern + quieting", {})
    nf, nr = ev.get("control: pullback, no floor", {}), ev.get("control: floor, no prior run", {})
    h = f"h{F.PRIMARY}"

    def pp(x):
        return f"{x * 100:+.1f}pp"
    out = [f"<div><b>Measured on {s.get('dates')} dates, {ui.esc(s.get('first'))} to {ui.esc(s.get('last'))}</b> "
           f"(study {ui.esc(s.get('measured_on'))}; {s.get('tests')} tests, so about one would pass by chance). "
           f"The pattern is RARE -- usually none on a given day -- so it is measured as {pat.get('events', 0)} "
           f"separate episodes, each counted once, against all liquid stocks on the same day.</div>"]
    if h in pat:
        a = pat[h]
        out.append(f"<div><b>40 trading days later:</b> up {a['hit'] * 100:.0f}% of the time against "
                   f"{a['pool_hit'] * 100:.0f}% for liquid stocks; average excess <b>{pp(a['ex'])}</b> "
                   f"(median {pp(a['ex_median'])}), <b>t = {a['t_quarters']:+.2f}</b> across {a['quarters']} quarters. "
                   f"<b>Positive but not proven</b>: |t| below ~2 is indistinguishable from luck, and the average "
                   f"sits well above the median because the wins, when they come, are large.</div>")
    if pat.get("broke40") is not None:
        out.append(f"<div><b>The floor broke</b> (a low more than 10% under it within 40 days) in "
                   f"<b>{pat['broke40'] * 100:.0f}%</b> of episodes, against {nf.get('broke40', 0) * 100:.0f}% for a "
                   f"pullback with no floor and {nr.get('broke40', 0) * 100:.0f}% for a floor with no prior run. "
                   f"A high-variance setup: a stop just under the floor is part of reading it.</div>")
    nmiss = ev.get("near miss (unmeasured until now)", {})
    if h in nmiss:
        a = nmiss[h]
        out.append(f"<div><b>Near misses did WORSE than average</b> -- shown only on request, as a caution "
                   f"list, never as a watchlist (one condition short, {nmiss.get('events')} episodes): "
                   f"{pp(a['ex'])} at 40 days (median {pp(a['ex_median'])}), t = {a['t_quarters']:+.2f}, "
                   f"up {a['hit'] * 100:.0f}% vs {a['pool_hit'] * 100:.0f}%"
                   + (f"; the floor broke {nmiss['broke40'] * 100:.0f}% of the time" if nmiss.get('broke40') is not None else "")
                   + ".</div>")
    if h in qt:
        a = qt[h]
        out.append(f"<div><b>With quieting</b> ({qt.get('events')} episodes): {pp(a['ex'])} at 40 days, "
                   f"t = {a['t_quarters']:+.2f} -- no clear gain over the plain pattern.</div>")
    if h in nf and h in nr:
        out.append(f"<div><b>Controls:</b> a pullback with no floor {pp(nf[h]['ex'])}; a floor with no prior run "
                   f"{pp(nr[h]['ex'])} (t = {nr[h]['t_quarters']:+.2f}).</div>")
    try:
        import insider_signals as SIG
        out.append(f"<div><b>insider</b> after a ticker = {ui.esc(SIG.BADGE_HELP)}</div>")
    except Exception:                                            # noqa: BLE001
        pass
    out.append("<div><b>History is flattered:</b> stocks that later vanished are under-represented, and "
               "they are exactly the beaten-down kind this pattern selects. Not a buy list; not advice.</div>")
    return "".join(out)


_TEMPLATE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Floor retest &middot; @@ASOF@@</title>
<style>@@CSS@@
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,Segoe UI,Roboto,sans-serif}
.flwrap{padding:16px 18px 40px;max-width:1700px;margin:0 auto}
.flwrap h1{font-size:19px;margin:0 0 2px}
.flsub{color:var(--muted);font-size:12.5px;margin-bottom:10px}
.flev{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--warn);border-radius:8px;
  padding:10px 13px;margin:10px 0 14px;font-size:12.5px}
.flev div{margin:3px 0}
.flbar{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:8px}
.fllab{color:var(--muted);font-size:11.5px;margin:0 2px 0 6px}
#flq,.flsel{background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:5px 9px;font:inherit;font-size:12.5px}
.flchip{background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:999px;padding:4px 11px;
  font:inherit;font-size:11.5px;cursor:pointer}
.flchip:hover{border-color:var(--accent)}
.flchip.on{background:var(--ink);border-color:var(--ink);color:var(--panel)}
.fltw{max-height:78vh;overflow:auto;border:1px solid var(--grid);border-radius:8px}
#fltab{width:100%;border-collapse:collapse;font-size:12.5px;min-width:1400px}
#fltab th{position:sticky;top:0;z-index:1;background:var(--head);text-align:left;color:var(--muted);font-weight:500;
  font-size:11.5px;padding:6px 8px;border-bottom:1px solid var(--line);cursor:pointer;white-space:nowrap}
#fltab th.nos{cursor:default}
#fltab th.asc::after{content:" \2191"}
#fltab th.desc::after{content:" \2193"}
#fltab td{padding:3px 8px;border-bottom:1px solid var(--grid);white-space:nowrap;vertical-align:middle}
#fltab td.n,#fltab th.n{text-align:right;font-variant-numeric:tabular-nums}
#fltab tbody tr.flr{cursor:pointer}
#fltab tbody tr.flr:hover,#fltab tr.flr.open{background:var(--grid)}
.fldet td{background:var(--panel);padding:10px 14px;white-space:normal}
.fldet .flfacts{margin:6px 0 0;font-size:12.5px;max-width:860px}
.fldet a{color:var(--ink);text-decoration:underline}
.flsvg{display:block}
.flline{fill:none;stroke:var(--ink);stroke-width:1.4;stroke-linejoin:round}
.fllevel{stroke:var(--accent);stroke-width:1.5;stroke-dasharray:4 3}
.flband{fill:var(--accent);opacity:.16}
.fldot{fill:var(--ink)}
.flax{fill:var(--muted);font:10px -apple-system,Segoe UI,Roboto,sans-serif}
.flaxl{fill:var(--ink);font:600 10px -apple-system,Segoe UI,Roboto,sans-serif}
.flno{color:var(--muted);font-size:11px}
.flins{margin-left:5px;padding:0 6px;border-radius:9px;font-size:10.5px;font-weight:600;background:var(--blkbg);color:var(--blk)}
.flfoot{display:flex;gap:10px;align-items:center;margin-top:8px;color:var(--muted);font-size:11.5px}
</style></head><body>
@@NAV@@
<div class="flwrap">
<h1>Floor retest after a big run</h1>
<div class="flsub">session <b>@@ASOF@@</b> &middot; <b>@@NM@@ full match(es)</b> and @@NN@@ near miss(es) &middot; a run of at least 2&times; within two years,
a pullback of 35&ndash;70% from the high, a floor tested at least 3 times over at least 3 months, and the close back
within 6% of it. Ordered by distance to the floor, <b>not</b> by any score.</div>
<div class="flev">@@EVIDENCE@@</div>
<div class="flbar">
  <input id="flq" type="search" placeholder="filter tickers..." autocomplete="off">
  <span class="fllab">sector</span><select class="flsel" id="fls"></select>
  <span class="fllab">tier</span><select class="flsel" id="flt"><option value="match" selected>full matches</option><option value="near">near misses (measured WORSE)</option><option value="all">all</option></select>
  <button class="flchip" id="flquiet">quieting only</button>
  <button class="flchip" id="flclr">clear</button>
</div>
<div class="fltw"><table id="fltab"><thead><tr>@@HEADS@@</tr></thead><tbody id="flbody"></tbody></table></div>
<div class="flfoot"><span id="flcount"></span><span>click a row for a larger chart</span></div>
</div>
<script>
(function(){
var R=@@ROWS@@, DATES=@@DATES@@, S=@@SERIES@@, K=@@KINDS@@, IX=@@IX@@, NC=@@NC@@, AT=@@AT@@;
var $=function(s){return document.getElementById(s)};
var sec='all', quiet=false, tier='match', q='', sc=IX.above, sd=1, open={};
function eh(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;');}
function fmt(v,k){
  if(v==null) return '&ndash;';
  switch(k){
    case 'p1': return (v*100).toFixed(1)+'%';  case 'p0': return (v*100).toFixed(0)+'%';
    case 'f0': return v.toFixed(0);  case 'f1': return v.toFixed(1);  case 'f2': return v.toFixed(2);
    case 'i': return String(Math.round(v));  case 'x': return v.toFixed(1)+'&times;';
    case 'vol': return (v/1e6).toFixed(0);  default: return eh(v);
  }
}
function chart(t,w,h,big,lv){
  var c=S[t]; if(!c) return '<span class="flno">no chart</span>';
  var n=c.length,i,mn=Infinity,mx=-Infinity;
  for(i=0;i<n;i++){ if(c[i]!=null){ if(c[i]<mn)mn=c[i]; if(c[i]>mx)mx=c[i]; } }
  var top=lv*AT; if(lv<mn)mn=lv; if(top>mx)mx=top;
  var pad=(mx-mn)*0.07||1; mn-=pad; mx+=pad;
  var L=big?50:2, Rr=big?10:2, T=big?10:3, B=big?22:3;
  var X=function(j){return L+(w-L-Rr)*j/(n-1)}, Y=function(v){return T+(h-T-B)*(1-(v-mn)/(mx-mn))};
  var d='',on=false;
  for(i=0;i<n;i++){ if(c[i]==null){on=false;continue;} d+=(on?'L':'M')+X(i).toFixed(1)+' '+Y(c[i]).toFixed(1); on=true; }
  var yl=Y(lv), yb=Y(top);
  var s='<svg class="flsvg" width="'+w+'" height="'+h+'" viewBox="0 0 '+w+' '+h+'" role="img" aria-label="'+eh(t)+' price and floor">';
  s+='<rect class="flband" x="'+L+'" y="'+yb.toFixed(1)+'" width="'+(w-L-Rr)+'" height="'+Math.max(1,yl-yb).toFixed(1)+'"/>';
  s+='<line class="fllevel" x1="'+L+'" x2="'+(w-Rr)+'" y1="'+yl.toFixed(1)+'" y2="'+yl.toFixed(1)+'"/>';
  s+='<path class="flline" d="'+d+'"/>';
  var e=n-1; while(e>0&&c[e]==null)e--;
  s+='<circle class="fldot" cx="'+X(e).toFixed(1)+'" cy="'+Y(c[e]).toFixed(1)+'" r="'+(big?3.4:2.3)+'"/>';
  if(big){
    s+='<text class="flaxl" x="'+(L-4)+'" y="'+(yl+3).toFixed(1)+'" text-anchor="end">'+lv.toFixed(2)+'</text>';
    s+='<text class="flax" x="'+(L-4)+'" y="'+(Y(mx-pad)+3).toFixed(1)+'" text-anchor="end">'+(mx-pad).toFixed(2)+'</text>';
    var xs=[0,Math.floor((n-1)/2),n-1];
    for(i=0;i<3;i++) s+='<text class="flax" x="'+X(xs[i]).toFixed(1)+'" y="'+(h-6)+'" text-anchor="'+(i==0?'start':i==2?'end':'middle')+'">'+DATES[xs[i]]+'</text>';
  }
  return s+'</svg>';
}
function okr(r){
  if(sec!=='all'&&r[IX.sector]!==sec) return false;
  if(quiet&&r[IX.quiet]!=='yes') return false;
  if(tier==='match'&&r[IX.tier]!=='match') return false;
  if(tier==='near'&&r[IX.tier]==='match') return false;
  if(q&&r[IX.ticker].toLowerCase().indexOf(q)<0) return false;
  return true;
}
function det(r){
  var t=r[IX.ticker], x=NC;
  var f='Ran from <b>'+fmt(r[x+2],'f2')+'</b> ('+eh(r[x+3])+') to <b>'+fmt(r[x],'f2')+'</b> ('+eh(r[x+1])+'), '
    +fmt(r[IX.run_x],'x')+'; now <b>'+fmt(r[IX.off_hi],'p0')+'</b> below that high. '
    +'Floor <b>'+fmt(r[IX.floor],'f2')+'</b>, tested <b>'+fmt(r[IX.tests],'i')+'</b> times over '+fmt(r[IX.months],'f1')
    +' months; the close is '+fmt(r[IX.above],'p1')+' above it. Quieting: '+eh(r[IX.quiet])+'. '
    +'Fundamental composite '+fmt(r[IX.fund],'f0')+', hype '+fmt(r[IX.hype],'f0')
    +(r[IX.sent]==null?', no news in 30 days.':', news sentiment percentile '+fmt(r[IX.sent],'f0')+'.');
  return '<tr class="fldet"><td colspan="'+(K.length+1)+'">'+chart(t,820,240,true,r[IX.floor])
    +'<div class="flfacts">'+f+' <a href="../stock/'+encodeURIComponent(t)+'.html">open the profile</a></div></td></tr>';
}
function render(){
  var m=R.filter(okr).sort(function(a,b){
    var x=a[sc],y=b[sc];
    if(x==null&&y==null) return a[0]<b[0]?-1:1;
    if(x==null) return 1; if(y==null) return -1;
    if(x===y) return a[0]<b[0]?-1:1;
    return (x>y?1:-1)*sd;
  });
  var h='';
  for(var i=0;i<m.length;i++){
    var r=m[i], t=r[IX.ticker];
    h+='<tr class="flr'+(open[t]?' open':'')+'" data-t="'+eh(t)+'"><td>'+chart(t,150,40,false,r[IX.floor])+'</td><td><b>'+eh(t)+'</b>'+(r[NC+4]?'<span class="flins" title="'+eh(r[NC+4])+'">insider</span>':'')+'</td>';
    for(var j=1;j<K.length;j++) h+='<td class="'+(K[j]=='s'?'':'n')+'">'+fmt(r[j],K[j])+'</td>';
    h+='</tr>'; if(open[t]) h+=det(r);
  }
  $('flbody').innerHTML=h;
  $('flcount').textContent=m.length+(m.length===1?' stock':' stocks')+' shown of '+R.length;
}
(function(){
  var c={}; R.forEach(function(r){c[r[IX.sector]]=(c[r[IX.sector]]||0)+1;});
  var o='<option value="all">all sectors ('+R.length+')</option>';
  Object.keys(c).sort(function(a,b){return c[b]-c[a]||(a<b?-1:1)}).forEach(function(s){o+='<option value="'+eh(s)+'">'+eh(s)+' ('+c[s]+')</option>';});
  $('fls').innerHTML=o;
})();
$('flq').addEventListener('input',function(e){q=e.target.value.trim().toLowerCase();render();});
$('fls').addEventListener('change',function(e){sec=e.target.value;render();});
$('flt').addEventListener('change',function(e){tier=e.target.value;render();});
$('flquiet').addEventListener('click',function(){quiet=!quiet;this.classList.toggle('on',quiet);render();});
$('flclr').addEventListener('click',function(){sec='all';quiet=false;tier='match';q='';$('flq').value='';$('fls').value='all';$('flt').value='match';$('flquiet').classList.remove('on');render();});
$('flbody').addEventListener('click',function(e){
  if(e.target.closest('a')) return;
  var tr=e.target.closest('tr.flr'); if(!tr) return;
  var t=tr.dataset.t; open[t]=!open[t]; if(!open[t]) delete open[t]; render();});
document.querySelectorAll('#fltab th[data-c]').forEach(function(th){th.addEventListener('click',function(){
  var c=+th.dataset.c; if(c===sc){sd=-sd;}else{sc=c;sd=(K[c]==='s')?1:-1;}
  document.querySelectorAll('#fltab th').forEach(function(o){o.classList.remove('asc','desc');});
  th.classList.add(sd>0?'asc':'desc'); render();});});
render();
})();
</script></body></html>
"""


def build(asof: str | None = None) -> tuple[Path, int]:
    asof = asof or calendar_us.last_closed_session()
    p = payload(asof)
    heads = ('<th class="nos" title="about 14 months of closes; the dashed line is the floor, the band the 6% above it">chart</th>'
             + "".join(f'<th data-c="{i}" class="{"" if k == "s" else "n"}" title="{ui.esc(tip)}">{ui.esc(lab)}</th>'
                       for i, (_k, lab, tip, k) in enumerate(COLS)))

    def js(o) -> str:
        return json.dumps(o, separators=(",", ":"), allow_nan=False).translate(_ESC)

    html = (_TEMPLATE.replace("@@CSS@@", ui.CSS).replace("@@NAV@@", ui.nav("floor", 1))
            .replace("@@ASOF@@", ui.esc(asof))
            .replace("@@NM@@", str(sum(r[IX["tier"]] == "match" for r in p["rows"])))
            .replace("@@NN@@", str(sum(r[IX["tier"]] != "match" for r in p["rows"])))
            .replace("@@EVIDENCE@@", _evidence()).replace("@@HEADS@@", heads)
            .replace("@@ROWS@@", js(p["rows"])).replace("@@DATES@@", js(p["dates"]))
            .replace("@@SERIES@@", js(p["series"])).replace("@@KINDS@@", js([c[3] for c in COLS]))
            .replace("@@IX@@", js(IX)).replace("@@NC@@", str(len(COLS))).replace("@@AT@@", str(F.AT_FLOOR)))
    config.REPORTS_ZONES.mkdir(parents=True, exist_ok=True)
    out = config.REPORTS_ZONES / "floor.html"
    out.write_text(html, encoding="utf-8")
    return out, len(p["rows"])


def selftest(asof: str | None = None) -> int:
    asof = asof or calendar_us.last_closed_session()
    p = payload(asof)
    rows = p["rows"]
    assert all(len(r) == len(COLS) + len(EXTRA) for r in rows), "row width disagrees with COLS + EXTRA"
    for r in rows:
        if r[IX["tier"]] == "match":
            assert r[IX["price"]] <= r[IX["floor"]] * F.AT_FLOOR + 0.011, f"{r[0]}: too far above its floor"
            assert r[IX["tests"]] >= F.MIN_TESTS and r[IX["run_x"]] >= F.RUN_X, f"{r[0]}: breaks the definition"
        else:
            assert r[IX["tier"]].startswith("near miss: "), f"{r[0]}: unknown tier {r[IX['tier']]}"
            assert r[IX["price"]] <= r[IX["floor"]] * F.NEAR["at_floor"] + 0.011, f"{r[0]}: near miss outside slack"
        assert r[IX["sent"]] is None or 0 <= r[IX["sent"]] <= 100, f"{r[0]}: sentiment off scale"
    json.dumps(rows, allow_nan=False)
    print(f"floor page selftest OK  ({len(rows)} rows on {asof}; {len(p['series'])} charts; "
          f"{sum(r[IX['quiet']] == 'yes' for r in rows)} quieting)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the Floor-retest page.")
    ap.add_argument("--asof", default=None)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest(a.asof)
    out, n = build(a.asof)
    print(f"  wrote {out}  ({out.stat().st_size / 1024:.0f} KB, {n} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
