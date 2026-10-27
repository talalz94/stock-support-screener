#!/usr/bin/env python
"""The candidates page: the "good level" rule, drawn.

`good_level.py` answers WHICH names fit the rule; this page lets you SEE them: a
small price chart per stock with its support level as a dashed line and the
"at the level" band shaded, beside the fundamentals, hype and sentiment, with a
sector filter. Click a row for a larger chart.

THIS PAGE IS A VIEWER FOR A LIST WITH NO MEASURED EDGE, and says so at the top
(`rule_accuracy.py`: hit rate 54-55% against 50-52% for any stock, +0.14pp excess
at 20 bars with t=+0.3, fewer big losers AND fewer big winners). It is built to
make the list inspectable, not to make it look like a recommendation: rows are
ordered by distance to the level, never by a score.

HONESTY RULES, each from an earlier mistake in this project:
  * A missing value is a dash, never a zero. 49 of the 93 names on 2026-10-07 had
    no recent news, so their sentiment is blank.
  * A chart whose last point disagrees with the stock's close is DROPPED rather
    than drawn -- a level drawn on misaligned prices is a fiction.
  * Names that report in a non-USD currency are marked (dagger): they are scored on
    fewer metrics and their market cap can be wrong (FMX showed $665B).
  * Thin volume (< $10M a day) is marked (asterisk): the level is real, the fill
    may not be.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import calendar_us
import config
import dataset
import good_level as G
import ui
import zones as Z
from zones_page import _ESC

SPARK_N = 180                  # sessions drawn (~8.5 months)
THIN_USD = 10e6

# index -> (label, tooltip, format kind). The row array is built in this order.
COLS = [
    ("ticker", "ticker", "* = under $10M/day traded.  dagger = reports in a non-USD currency: scored on fewer metrics, market cap may be wrong", "s"),
    ("sector", "sector", "sector from the SEC SIC map", "s"),
    ("price", "price", "close on the session shown", "f2"),
    ("dist", "% above level", "how far the close is above the support level", "p1"),
    ("level", "level", "the support level (the dashed line on the chart)", "f2"),
    ("band", "band", "AT = within 2.5% of the level, NEAR = within 6%", "s"),
    ("touches", "touches", "times price has tested this level", "i"),
    ("broke", "level failed", "share of past tests after which the stock later fell more than 10%. Lower = the level has held better", "p0"),
    ("pct_hi", "% of 1y high", "close divided by the 250-day high", "p0"),
    ("fund", ui.label("fund_score"), ui.tooltip("fund_score"), "f0"),
    ("intcov", ui.label("interest_cover"), ui.tooltip("interest_cover"), "f1"),
    ("fscore", ui.label("f_score"), ui.tooltip("f_score"), "f0"),
    ("roic", ui.label("roic"), ui.tooltip("roic"), "p0"),
    ("pe", ui.label("pe"), ui.tooltip("pe"), "f1"),
    ("hype", ui.label("hype_score"), ui.tooltip("hype_score") + "  On a support test, MORE attention was measured as a warning, not a tailwind.", "f0"),
    ("sent", ui.label("sent_rank"), "news sentiment percentile, 0-100. Blank = no news in 30 days (about half the list). No reliable signal measured.", "f0"),
    ("news", ui.label("news_count_30d"), ui.tooltip("news_count_30d"), "i"),
    ("cap", "mkt cap $B", "market cap in billions", "cap"),
    ("vol", "$M / day", "20-day average dollar volume, millions", "vol"),
]
IDX = {c[0]: i for i, c in enumerate(COLS)}
N_FLAG = len(COLS)             # extra field after the columns: 1 = non-USD reporter
N_BADGE = N_FLAG + 1           # and after it: the "insider buying at support" reason, "" when none


def _badges(asof: str) -> dict[str, str]:
    """ticker -> reason (insider_signals.badges). A failure costs the badge, never the page."""
    try:
        import insider_signals as SIG
        return SIG.badges(asof)
    except Exception:                                            # noqa: BLE001
        return {}


def _num(v, nd: int = 5):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if not np.isfinite(f) else round(f, nd)


def _txt(v) -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return ""
    s = str(v)
    return "" if s.lower() in ("nan", "nat", "none") else s


class NoCandidates(RuntimeError):
    """The rule selected nobody. A legitimate outcome, not a malfunction: the
    nightly step reports it and leaves the previous page alone instead of
    publishing an empty table or counting an error."""


def payload(asof: str) -> dict:
    """Everything the page draws, plus the problems found while assembling it.

    PRICE AND VOLUME COME FROM THE BARS AS OF `asof`, never from the panel-stats
    cache. That cache holds the LATEST bar, so for any session but the newest it
    describes a different day: asked for 2026-10-07 on 10-09 it returned the 10-08
    close, and a stock that had since dipped below its level made a level look as
    if it sat above price. The invariant that caught it is kept, and strengthened
    by a second one: price / level - 1 must equal the distance the zones file
    recorded, which cannot hold if the price is from the wrong day.
    """
    z, trusted, df = G.current(asof)
    missing = [c for c in ("fund_score", "hype_score") if c not in df.columns]
    if missing:
        # The page needs scored fundamentals and hype for THIS session. Building it
        # before they are written would publish an empty or wrong list, so refuse.
        raise RuntimeError(f"{asof} has no stored {', '.join(missing)} yet; build after fundamental and hype")
    sel = (df[G.rule(df)].sort_values(["dist_pct", "ticker"]).reset_index(drop=True))
    if sel.empty:
        raise NoCandidates(f"no names pass the rule on {asof}")

    # ---- the bars: one shared date axis, closes and 20-day dollar volume AS OF asof
    ses = [s for s in calendar_us.all_sessions() if s <= asof][-SPARK_N:]
    tick = sel["ticker"].astype(str).tolist()
    series, price, adv, dropped = {}, {}, {}, []
    for i in range(0, len(tick), 250):
        part = tick[i:i + 250]
        d = dataset.panel(part, "1d", start=ses[0], end=asof)
        for t in part:
            b = d.get(t) if isinstance(d, dict) else None
            if b is None or not len(b):
                dropped.append((t, "no bars"))
                continue
            idx = b["date"].astype(str).values
            cl = pd.Series(b["close"].astype(float).values, index=idx)
            vo = pd.Series(b["volume"].astype(float).values, index=idx)
            cl = cl[~cl.index.duplicated(keep="last")].reindex(ses)
            vo = vo[~vo.index.duplicated(keep="last")].reindex(ses)
            if not np.isfinite(cl.iloc[-1]):
                dropped.append((t, f"no bar on {asof}"))
                continue
            series[t] = [None if not np.isfinite(x) else round(float(x), 2) for x in cl.tolist()]
            price[t] = float(cl.iloc[-1])
            adv[t] = float((cl * vo).iloc[-20:].mean())
    gone = {t for t, _ in dropped}
    sel = sel[~sel["ticker"].isin(gone)].reset_index(drop=True)
    sel["last_close"] = sel["ticker"].map(price)
    sel["dollar_vol_20"] = sel["ticker"].map(adv)
    if sel.empty:
        raise RuntimeError(f"no names left with bars on {asof}")

    # ---- hard invariants: a failure means the LIST is wrong, so refuse to publish
    hard = []
    if (sel["dist_pct"] > G.BAND_MAX + 1e-9).any():
        hard.append("a row is further than 6% from its level")
    if (sel["level"] > sel["last_close"] * 1.0001).any():
        hard.append("a level sits above price")
    drift = (sel["last_close"] / sel["level"] - 1.0 - sel["dist_pct"]).abs()
    if (drift > 0.002).any():
        bad = sel.loc[drift > 0.002, "ticker"].tolist()[:4]
        hard.append(f"price and recorded distance disagree for {bad}: prices are from the wrong day")
    if (sel["pct_hi"] < G.PCT_HI_MIN).any() or (sel["fund_score"] < G.FUND_MIN).any()             or (sel["hype_score"] > G.HYPE_MAX).any():
        hard.append("a row breaks the rule it was selected by")
    if sel["ticker"].duplicated().any():
        hard.append("duplicate tickers")
    if hard:
        raise RuntimeError("; ".join(hard))

    rows = []
    badges = _badges(asof)
    for r in sel.itertuples(index=False):
        d = r._asdict()
        sent = _num(d.get("sent_rank"))
        rows.append([
            _txt(d["ticker"]), _txt(d.get("sector")) or "Other",
            _num(d.get("last_close"), 2), _num(d.get("dist_pct")), _num(d.get("level"), 2),
            _txt(d.get("band")), _num(d.get("touches"), 0), _num(d.get("dd_break_rate")),
            _num(d.get("pct_hi")), _num(d.get("fund_score"), 1), _num(d.get("interest_cover"), 2),
            _num(d.get("f_score"), 0), _num(d.get("roic")), _num(d.get("pe"), 2),
            _num(d.get("hype_score"), 1),
            None if sent is None else round(sent * 100, 1),     # 0-1 -> 0-100, blank stays blank
            _num(d.get("news_count_30d"), 0),
            _num(d.get("mktcap"), 0), _num(d.get("dollar_vol_20"), 0),
            1 if _num(d.get("reports_usd")) == 0 else 0,
            badges.get(_txt(d["ticker"]), ""),
        ])
    return {"asof": asof, "rows": rows, "dates": ses, "series": series,
            "dropped": dropped, "n_universe": int(trusted["ticker"].nunique())}


_TEMPLATE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Good-level candidates &middot; @@ASOF@@</title>
<style>@@CSS@@
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,Segoe UI,Roboto,sans-serif}
.cdwrap{padding:16px 18px 40px;max-width:1700px;margin:0 auto}
.cdwrap h1{font-size:19px;margin:0 0 2px}
.cdsub{color:var(--muted);font-size:12.5px;margin-bottom:10px}
.cdev{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--warn);border-radius:8px;
  padding:10px 13px;margin:10px 0 14px;font-size:12.5px}
.cdev div{margin:3px 0}
.cdbar{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:8px}
.cdlab{color:var(--muted);font-size:11.5px;margin:0 2px 0 6px}
#cdq,.cdsel{background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:7px;
  padding:5px 9px;font:inherit;font-size:12.5px}
#cdq{min-width:140px}
.cdchip{background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:999px;padding:4px 11px;
  font:inherit;font-size:11.5px;cursor:pointer;display:inline-flex;gap:6px;align-items:center}
.cdchip:hover{border-color:var(--accent)}
.cdchip.on{background:var(--ink);border-color:var(--ink);color:var(--panel)}
.cdchip .n{opacity:.7;font-variant-numeric:tabular-nums}
.cdtw{max-height:78vh;overflow:auto;border:1px solid var(--grid);border-radius:8px}
#cdtab{width:100%;border-collapse:collapse;font-size:12.5px;min-width:1500px}
#cdtab th{position:sticky;top:0;z-index:1;background:var(--head);text-align:left;color:var(--muted);font-weight:500;
  font-size:11.5px;padding:6px 8px;border-bottom:1px solid var(--line);cursor:pointer;white-space:nowrap}
#cdtab th.nos{cursor:default}
#cdtab th:hover:not(.nos){color:var(--ink)}
#cdtab th.asc::after{content:" \2191"}
#cdtab th.desc::after{content:" \2193"}
#cdtab td{padding:3px 8px;border-bottom:1px solid var(--grid);white-space:nowrap;vertical-align:middle}
#cdtab td.n,#cdtab th.n{text-align:right;font-variant-numeric:tabular-nums}
#cdtab tbody tr.cdr{cursor:pointer}
#cdtab tbody tr.cdr:hover{background:var(--grid)}
#cdtab tr.cdr.open{background:var(--grid)}
.cdb{font-size:11px;padding:1px 6px;border-radius:999px;border:1px solid var(--line)}
.cdb-AT{color:var(--pos);color:color-mix(in srgb,var(--pos) 80%,var(--ink));border-color:var(--pos)}  /* 4.48:1 on a hovered row without the mix */
.cdm{color:var(--muted)}
.cdins{margin-left:5px;padding:0 6px;border-radius:9px;font-size:10.5px;font-weight:600;background:var(--blkbg);color:var(--blk)}
.cddet td{background:var(--panel);padding:10px 14px;white-space:normal}
.cddet .cdfacts{margin:6px 0 0;font-size:12.5px;max-width:860px}
.cddet a{color:var(--ink);text-decoration:underline}
.cdsvg{display:block}
.cdline{fill:none;stroke:var(--ink);stroke-width:1.4;stroke-linejoin:round}
.cdlevel{stroke:var(--accent);stroke-width:1.5;stroke-dasharray:4 3}
.cdband{fill:var(--accent);opacity:.16}
.cddot{fill:var(--ink)}
.cdax{fill:var(--muted);font:10px -apple-system,Segoe UI,Roboto,sans-serif}
.cdaxl{fill:var(--ink);font:600 10px -apple-system,Segoe UI,Roboto,sans-serif}
.cdno{color:var(--muted);font-size:11px}
.cdfoot{display:flex;gap:10px;align-items:center;margin-top:8px;color:var(--muted);font-size:11.5px}
</style></head><body>
@@NAV@@
<div class="cdwrap">
<h1>Good-level candidates</h1>
<div class="cdsub">session <b>@@ASOF@@</b> &middot; @@N@@ of @@UNIVERSE@@ stocks pass the rule &middot;
at or near a support level, within 20% of the 1-year high, fundamental score &ge; 55, hype &le; 45.
Ordered by distance to the level, <b>not</b> by any score.</div>

<div class="cdev">
<div><b>This is a watchlist, not a list of buys.</b> Measured over 49 historical dates and on live days after the study:
these names finish up 54&ndash;55% of the time against 50&ndash;52% for any stock, but the extra return is
<b>+0.14pp over 20 bars (t = +0.3), indistinguishable from luck</b>. The one measurable difference is the tails:
they lose more than 20% in 40 days 3.9% of the time against 9.9% for the pool &mdash; and gain more than 20% 6.6%
against 11.7%. Fewer disasters <i>and</i> fewer jackpots.</div>
<div><b>Hype is a warning, sentiment is blank for about half.</b> Names that broke their level had <i>more</i>
attention, not less, and news sentiment showed no reliable signal. Both are shown for context only.</div>
<div><b>insider</b> after a ticker = @@BADGE_HELP@@</div>
<div><b>History is flattered:</b> only 0.9% of it belongs to stocks that later vanished. I cannot see earnings
dates or news here &mdash; check both before acting.</div>
</div>

<div class="cdbar">
  <input id="cdq" type="search" placeholder="filter tickers..." autocomplete="off">
  <span class="cdlab">sector</span><select class="cdsel" id="cds"></select>
  <button class="cdchip on" data-b="all">all<span class="n" id="cdn_all"></span></button>
  <button class="cdchip" data-b="AT">AT a level<span class="n" id="cdn_AT"></span></button>
  <button class="cdchip" data-b="NEAR">NEAR<span class="n" id="cdn_NEAR"></span></button>
  <button class="cdchip" id="cdliq">liquid only (&ge; $10M/day)</button>
  <button class="cdchip" id="cdins" title="@@BADGE_HELP@@">insider buying at support<span class="n" id="cdn_ins"></span></button>
  <button class="cdchip" id="cdclr">clear</button>
</div>

<div class="cdtw"><table id="cdtab"><thead><tr>@@HEADS@@</tr></thead><tbody id="cdbody"></tbody></table></div>
<div class="cdfoot"><span id="cdcount"></span><span>click a row for a larger chart</span></div>
</div>
<script>
(function(){
var R=@@ROWS@@, DATES=@@DATES@@, S=@@SERIES@@, K=@@KINDS@@, BAND_AT=@@BAND_AT@@, FLAG=@@FLAG@@;
var $=function(s){return document.getElementById(s)};
var sec='all', band='all', liq=false, ins=false, q='', sc=3, sd=1, open={}, BADGE=FLAG+1;
function fmt(v,k){
  if(v==null) return '&ndash;';
  switch(k){
    case 'p1': return (v*100).toFixed(1)+'%';  case 'p0': return (v*100).toFixed(0)+'%';
    case 'f0': return v.toFixed(0);  case 'f1': return v.toFixed(1);  case 'f2': return v.toFixed(2);
    case 'i': return String(Math.round(v));  case 'cap': return (v/1e9).toFixed(1);
    case 'vol': return (v/1e6).toFixed(0);  default: return v;
  }
}
function chart(t,w,h,big,lv){
  var c=S[t]; if(!c) return '<span class="cdno">no chart</span>';
  var n=c.length,i,mn=Infinity,mx=-Infinity;
  for(i=0;i<n;i++){ if(c[i]!=null){ if(c[i]<mn)mn=c[i]; if(c[i]>mx)mx=c[i]; } }
  var top=lv*(1+BAND_AT);
  if(lv<mn)mn=lv; if(top>mx)mx=top;
  var pad=(mx-mn)*0.07||1; mn-=pad; mx+=pad;
  var L=big?50:2, Rr=big?10:2, T=big?10:3, B=big?22:3;
  var X=function(j){return L+(w-L-Rr)*j/(n-1)}, Y=function(v){return T+(h-T-B)*(1-(v-mn)/(mx-mn))};
  var d='',on=false;
  for(i=0;i<n;i++){ if(c[i]==null){on=false;continue;} d+=(on?'L':'M')+X(i).toFixed(1)+' '+Y(c[i]).toFixed(1); on=true; }
  var yl=Y(lv), yb=Y(top);
  var s='<svg class="cdsvg" width="'+w+'" height="'+h+'" viewBox="0 0 '+w+' '+h+'" role="img" aria-label="'+t+' price and support level">';
  s+='<rect class="cdband" x="'+L+'" y="'+yb.toFixed(1)+'" width="'+(w-L-Rr)+'" height="'+Math.max(1,yl-yb).toFixed(1)+'"/>';
  s+='<line class="cdlevel" x1="'+L+'" x2="'+(w-Rr)+'" y1="'+yl.toFixed(1)+'" y2="'+yl.toFixed(1)+'"/>';
  s+='<path class="cdline" d="'+d+'"/>';
  var e=n-1; while(e>0&&c[e]==null)e--;
  s+='<circle class="cddot" cx="'+X(e).toFixed(1)+'" cy="'+Y(c[e]).toFixed(1)+'" r="'+(big?3.4:2.3)+'"/>';
  if(big){
    s+='<text class="cdaxl" x="'+(L-4)+'" y="'+(yl+3).toFixed(1)+'" text-anchor="end">'+lv.toFixed(2)+'</text>';
    s+='<text class="cdax" x="'+(L-4)+'" y="'+(Y(mx-pad)+3).toFixed(1)+'" text-anchor="end">'+(mx-pad).toFixed(2)+'</text>';
    s+='<text class="cdax" x="'+(L-4)+'" y="'+(Y(mn+pad)+3).toFixed(1)+'" text-anchor="end">'+(mn+pad).toFixed(2)+'</text>';
    var xs=[0,Math.floor((n-1)/2),n-1];
    for(i=0;i<3;i++) s+='<text class="cdax" x="'+X(xs[i]).toFixed(1)+'" y="'+(h-6)+'" text-anchor="'+(i==0?'start':i==2?'end':'middle')+'">'+DATES[xs[i]]+'</text>';
  }
  return s+'</svg>';
}
function ok(r){
  if(sec!=='all'&&r[1]!==sec) return false;
  if(band!=='all'&&r[5]!==band) return false;
  if(liq&&!(r[18]>=1e7)) return false;
  if(ins&&!r[BADGE]) return false;
  if(q&&r[0].toLowerCase().indexOf(q)<0) return false;
  return true;
}
function det(r){
  var t=r[0];
  var f='The support level is <b>'+fmt(r[4],'f2')+'</b>; the close is <b>'+fmt(r[3],'p1')+'</b> above it ('+r[5]+'). '
    +'It has been tested <b>'+fmt(r[6],'i')+'</b> times, and after '+(r[7]==null?'&ndash;':'<b>'+fmt(r[7],'p0')+'</b>')
    +' of those tests the stock later fell more than 10%. '
    +'The stock is at <b>'+fmt(r[8],'p0')+'</b> of its 1-year high. '
    +'Fundamental composite <b>'+fmt(r[9],'f0')+'</b>, interest cover '+fmt(r[10],'f1')+', Piotroski '+fmt(r[11],'f0')
    +', ROIC '+fmt(r[12],'p0')+', P/E '+fmt(r[13],'f1')+'. Hype <b>'+fmt(r[14],'f0')+'</b>'
    +(r[15]==null?'; no news in the last 30 days.':'; news sentiment percentile '+fmt(r[15],'f0')+' from '+fmt(r[16],'i')+' articles.');
  return '<tr class="cddet"><td colspan="'+(K.length+1)+'">'+chart(t,820,240,true,r[4])
    +'<div class="cdfacts">'+f+' <a href="../stock/'+t+'.html">open the full profile</a></div></td></tr>';
}
function render(){
  var m=R.filter(ok).sort(function(a,b){
    var x=a[sc],y=b[sc];
    if(x==null&&y==null) return a[0]<b[0]?-1:1;
    if(x==null) return 1; if(y==null) return -1;
    if(x===y) return a[0]<b[0]?-1:1;
    return (x>y?1:-1)*sd;
  });
  var h='';
  for(var i=0;i<m.length;i++){
    var r=m[i], t=r[0], tick='<b>'+t+'</b>'+(r[FLAG]===1?'<span class="cdm" title="reports in a non-USD currency"> &dagger;</span>':'')+(r[18]!=null&&r[18]<1e7?'<span class="cdm" title="under $10M/day traded"> *</span>':'')+(r[BADGE]?'<span class="cdins" title="'+r[BADGE]+'">insider</span>':'');
    h+='<tr class="cdr'+(open[t]?' open':'')+'" data-t="'+t+'"><td>'+chart(t,150,40,false,r[4])+'</td><td>'+tick+'</td><td>'+(r[1]||'&ndash;')+'</td>';
    for(var j=2;j<K.length;j++){
      var b=(j==5)?'<span class="cdb cdb-'+r[5]+'">'+r[5]+'</span>':fmt(r[j],K[j]);
      h+='<td class="'+(K[j]=='s'?'':'n')+'">'+b+'</td>';
    }
    h+='</tr>'; if(open[t]) h+=det(r);
  }
  $('cdbody').innerHTML=h;
  $('cdcount').textContent=m.length+(m.length===1?' stock':' stocks')+' shown of '+R.length;
}
// sector options with counts
(function(){
  var c={},k; R.forEach(function(r){c[r[1]]=(c[r[1]]||0)+1;});
  var o='<option value="all">all sectors ('+R.length+')</option>';
  Object.keys(c).sort(function(a,b){return c[b]-c[a]||(a<b?-1:1)}).forEach(function(s){o+='<option value="'+s+'">'+s+' ('+c[s]+')</option>';});
  $('cds').innerHTML=o;
  $('cdn_all').textContent=R.length;
  $('cdn_AT').textContent=R.filter(function(r){return r[5]==='AT'}).length;
  $('cdn_NEAR').textContent=R.filter(function(r){return r[5]==='NEAR'}).length;
  $('cdn_ins').textContent=R.filter(function(r){return r[BADGE]}).length;
})();
$('cdq').addEventListener('input',function(e){q=e.target.value.trim().toLowerCase();render();});
$('cds').addEventListener('change',function(e){sec=e.target.value;render();});
document.querySelectorAll('.cdchip[data-b]').forEach(function(b){b.addEventListener('click',function(){
  band=b.dataset.b; document.querySelectorAll('.cdchip[data-b]').forEach(function(o){o.classList.toggle('on',o===b);}); render();});});
$('cdliq').addEventListener('click',function(){liq=!liq;this.classList.toggle('on',liq);render();});
$('cdins').addEventListener('click',function(){ins=!ins;this.classList.toggle('on',ins);render();});
$('cdclr').addEventListener('click',function(){
  sec='all';band='all';liq=false;ins=false;q='';$('cdq').value='';$('cds').value='all';$('cdliq').classList.remove('on');$('cdins').classList.remove('on');
  document.querySelectorAll('.cdchip[data-b]').forEach(function(o){o.classList.toggle('on',o.dataset.b==='all');});render();});
$('cdbody').addEventListener('click',function(e){
  if(e.target.closest('a')) return;
  var tr=e.target.closest('tr.cdr'); if(!tr) return;
  var t=tr.dataset.t; open[t]=!open[t]; if(!open[t]) delete open[t]; render();});
document.querySelectorAll('#cdtab th[data-c]').forEach(function(th){th.addEventListener('click',function(){
  var c=+th.dataset.c; if(c===sc){sd=-sd;}else{sc=c;sd=(K[c]==='s')?1:-1;}
  document.querySelectorAll('#cdtab th').forEach(function(o){o.classList.remove('asc','desc');});
  th.classList.add(sd>0?'asc':'desc'); render();});});
render();
})();
</script></body></html>
"""


def _badge_help() -> str:
    try:
        import insider_signals as SIG
        return SIG.BADGE_HELP
    except Exception:                                            # noqa: BLE001
        return "insider data unavailable"


def build(asof: str | None = None) -> tuple[Path, int]:
    asof = asof or calendar_us.last_closed_session()
    p = payload(asof)
    rows, dates, series = p["rows"], p["dates"], p["series"]
    kinds = [c[3] for c in COLS]

    heads = '<th class="nos" title="price over the last ~8 months; the dashed line is the support level">chart</th>' + "".join(
        f'<th data-c="{i}" class="{"" if k == "s" else "n"}" title="{ui.esc(tip)}">{ui.esc(lab)}</th>'
        for i, (_key, lab, tip, k) in enumerate(COLS))

    def js(obj) -> str:
        # allow_nan=False: a stray NaN must crash HERE, not render as the word "NaN" in a cell
        return json.dumps(obj, separators=(",", ":"), allow_nan=False).translate(_ESC)

    html = (_TEMPLATE
            .replace("@@CSS@@", ui.CSS).replace("@@NAV@@", ui.nav("candidates", 1))
            .replace("@@ASOF@@", ui.esc(asof)).replace("@@N@@", str(len(rows)))
            .replace("@@UNIVERSE@@", f"{p['n_universe']:,}").replace("@@HEADS@@", heads)
            .replace("@@ROWS@@", js(rows)).replace("@@DATES@@", js(dates))
            .replace("@@SERIES@@", js(series)).replace("@@KINDS@@", js(kinds))
            .replace("@@BAND_AT@@", str(Z.BAND_AT)).replace("@@FLAG@@", str(N_FLAG))
            .replace("@@BADGE_HELP@@", ui.esc(_badge_help())))
    config.REPORTS_ZONES.mkdir(parents=True, exist_ok=True)
    out = config.REPORTS_ZONES / "candidates.html"
    out.write_text(html, encoding="utf-8")
    return out, len(rows)


def selftest(asof: str | None = None) -> int:
    """`python candidates_page.py --selftest` -- builds from stored data, writes nothing."""
    asof = asof or calendar_us.last_closed_session()
    p = payload(asof)
    rows, dates, series = p["rows"], p["dates"], p["series"]
    assert rows, "no rows"
    # The page script addresses fields by literal position (r[1], r[4], r[5], r[18]...).
    # If a column is added or moved this fails here instead of showing the wrong field.
    assert [IDX[k] for k in ("sector", "dist", "level", "band", "touches", "broke", "pct_hi", "fund",
                             "intcov", "fscore", "roic", "pe", "hype", "sent", "news", "vol")] \
        == [1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 18], "page script indexes are stale"
    assert all(len(r) == N_BADGE + 1 for r in rows), "row width disagrees with the column list"
    assert all(isinstance(r[N_BADGE], str) for r in rows), "badge field is not text"
    assert len(dates) == SPARK_N and dates == sorted(dates), "date axis"
    assert all(len(v) == len(dates) for v in series.values()), "a series is not aligned to the date axis"
    for r in rows:
        assert r[IDX["level"]] <= r[IDX["price"]] * 1.0001, f"{r[0]}: level above price"
        assert r[IDX["dist"]] <= G.BAND_MAX + 1e-9, f"{r[0]}: too far from its level"
        assert r[IDX["fund"]] >= G.FUND_MIN and r[IDX["hype"]] <= G.HYPE_MAX, f"{r[0]}: breaks the rule"
        sent = r[IDX["sent"]]
        assert sent is None or 0 <= sent <= 100, f"{r[0]}: sentiment {sent} not on a 0-100 scale"
    json.dumps(rows, allow_nan=False)                     # raises on any NaN
    no_sent = sum(r[IDX["sent"]] is None for r in rows)
    print(f"candidates selftest OK  ({len(rows)} rows on {asof}; {len(series)} charts, "
          f"{len(p['dropped'])} dropped; {no_sent} with no news shown blank, not zero)")
    for t, why in p["dropped"]:
        print(f"   dropped chart {t}: {why}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the good-level candidates page.")
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
