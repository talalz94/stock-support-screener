#!/usr/bin/env python
"""The zones page: every stock, every support level below it, filterable.

Deliberately ONE table. The request was "filter based on zone strength, like
touches, bounce size history, so the window can list all the stocks in that
zone -- make it intuitive and dont clutter it". So: a search box, four band
chips, three numeric filters, and the table. Nothing else.

EVERY COLUMN CARRIES WHAT IT MEASURED. The touch filter is the reason this
matters: touch count is the intuitive thing to sort by for a bigger bounce, and
it measured the OPPOSITE (-0.0855 rank correlation with rally size, and AT+8
touches scored t=+1.61 against plain AT at t=+2.59). A page that offers that
filter without saying so is inviting the user to rank themselves backwards.
The evidence is printed under the header, not hidden in a tooltip -- the same
choice the strategy rail makes, and for the same reason.

Rendered client-side from one JSON array, like the bounce page's universe
table: ~8,000 rows sort and filter instantly as an array, where the same table
as DOM nodes would be sluggish and roughly four times the bytes.
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

import config
import ui
import zones as Z

_BS = chr(92)
_ESC = str.maketrans({"<": _BS + "u003c", ">": _BS + "u003e",
                      "&": _BS + "u0026",
                      chr(0x2028): _BS + "u2028",
                      chr(0x2029): _BS + "u2029"})

COLS = [
    ("ticker", "ticker", "s"), ("band", "band", "s"),
    ("dist_pct", "dist", "p"), ("level", "level", "f2"),
    ("touches", "touches", "i"), ("bounce_n", "bounces", "i"),
    ("bounce_median", "rally med", "p"), ("bounce_med_atr", "rally/ATR", "f2"),
    ("dd_median", "drawdown", "p"), ("dd_break_rate", "broke >10%", "p"),
    # Beside no_support on purpose: the no-support effect is -5.32pp in the
    # bottom pct_hi quartile and nothing in the top, so the flag is unreadable
    # without it.
    ("pct_hi", "% of 250d high", "p"),
    ("span_days", "span d", "i"), ("last_touch", "last touch", "s"),
]


def _rows(df: pd.DataFrame) -> list[list]:
    def num(v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return None if not np.isfinite(f) else round(f, 5)

    def txt(v):
        """A missing string is empty, not the word "nan".

        `str(v or "")` is not enough: float("nan") is TRUTHY, so `nan or ""`
        returns nan and str() renders it "nan". That is the literal string the
        page audit hunts for, and a no-support row -- which has no last touch by
        definition -- printed it in every one of its 396 rows.
        """
        if v is None:
            return ""
        if isinstance(v, float) and not np.isfinite(v):
            return ""
        s = str(v)
        return "" if s.lower() in ("nan", "nat", "none") else s

    out = []
    for r in df.itertuples(index=False):
        d = r._asdict()
        if bool(d.get("suspect_split")):
            band = "SUSPECT"        # prices not trusted; see zones.is_suspect
        elif bool(d.get("no_support")):
            band = "NO SUPPORT"
        else:
            band = txt(d.get("band"))
        out.append([
            txt(d.get("ticker")), band,
            num(d.get("dist_pct")), num(d.get("level")),
            num(d.get("touches")), num(d.get("bounce_n")),
            num(d.get("bounce_median")), num(d.get("bounce_med_atr")),
            num(d.get("dd_median")), num(d.get("dd_break_rate")),
            num(d.get("pct_hi")),
            num(d.get("span_days")), txt(d.get("last_touch")),
        ])
    # Nearest first, then the strongest-holding level. NOT by bounce size:
    # that ordering is ~5x explained by volatility (see zones.EVIDENCE).
    out.sort(key=lambda x: (x[1] == "NO SUPPORT",
                            x[2] if x[2] is not None else 9,
                            x[0]))
    return out


def build(df: pd.DataFrame | None = None, asof: str | None = None) -> "object":
    import calendar_us
    asof = asof or calendar_us.last_closed_session()
    if df is None:
        p = config.ZONES / f"{asof}.parquet"
        if not p.exists():
            raise FileNotFoundError(f"no zones stored for {asof}")
        df = pd.read_parquet(p)
    if "no_support" not in df.columns:
        df = df.assign(no_support=False)

    rows = _rows(df)
    n_tick = df["ticker"].nunique()
    if "suspect_split" not in df.columns:
        df = df.assign(suspect_split=False)
    n_none = int(df["no_support"].fillna(False).astype(bool).sum())
    n_sus = int(df["suspect_split"].fillna(False).astype(bool).sum())
    n_at = int((df.get("band") == "AT").sum())
    n_near = int((df.get("band") == "NEAR").sum())
    n_appr = int((df.get("band") == "APPROACHING").sum())

    blob = json.dumps(rows, separators=(",", ":")).translate(_ESC)
    heads = "".join(
        f'<th data-c="{i}" class="s{" n" if k != "s" else ""}">{lbl}</th>'
        for i, (_c, lbl, k) in enumerate(COLS))
    kinds = json.dumps([k for _c, _l, k in COLS])

    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Support zones &middot; {ui.esc(asof)}</title>
<style>{ui.CSS}
body{{margin:0;background:var(--bg);color:var(--ink);
  font:14px/1.45 -apple-system,Segoe UI,Roboto,sans-serif}}
.znwrap{{padding:16px 18px 40px;max-width:1500px;margin:0 auto}}
.znhead h1{{font-size:19px;margin:0 0 2px}}
.znsub{{color:var(--muted);font-size:12.5px;margin-bottom:10px}}
.znev{{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--warn);
  border-radius:8px;padding:10px 13px;margin:10px 0 14px;font-size:12.5px;color:var(--ink)}}
.znev b{{color:var(--ink)}}
.znev div{{margin:3px 0}}
.znbar{{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:8px}}
.znbar2{{padding-top:8px;border-top:1px solid var(--grid)}}
.znlab{{color:var(--muted);font-size:11.5px;margin:0 2px 0 6px}}
#znq{{background:var(--bg);color:var(--ink);border:1px solid var(--line);
  border-radius:7px;padding:5px 9px;font:inherit;font-size:12.5px;min-width:150px}}
.znchip{{background:var(--bg);color:var(--ink);border:1px solid var(--line);
  border-radius:999px;padding:4px 11px;font:inherit;font-size:11.5px;cursor:pointer;
  display:inline-flex;align-items:center;gap:6px}}
.znchip:hover{{border-color:var(--accent)}}
.znchip.on{{background:var(--ink);border-color:var(--ink);color:var(--panel)}}
.znchip .n{{opacity:.7;font-variant-numeric:tabular-nums}}
.znsel{{background:var(--bg);color:var(--ink);border:1px solid var(--line);
  border-radius:7px;padding:4px 7px;font:inherit;font-size:11.5px}}
.zntw{{max-height:72vh;overflow:auto;border:1px solid var(--grid);border-radius:8px}}
#zntab{{width:100%;border-collapse:collapse;font-size:12.5px;min-width:1000px}}
#zntab th{{position:sticky;top:0;z-index:1;background:var(--head);text-align:left;
  color:var(--muted);font-weight:500;font-size:11.5px;padding:6px 9px;
  border-bottom:1px solid var(--line);cursor:pointer;white-space:nowrap}}
#zntab th:hover{{color:var(--ink)}}
#zntab th.asc::after{{content:" \\2191"}}
#zntab th.desc::after{{content:" \\2193"}}
#zntab th.n,#zntab td.n{{text-align:right;font-variant-numeric:tabular-nums}}
#zntab td{{padding:4px 9px;border-bottom:1px solid var(--grid);white-space:nowrap}}
#zntab tbody tr:hover{{background:var(--grid)}}
.zb{{font-size:11px;padding:1px 6px;border-radius:999px;border:1px solid var(--line)}}
.zb-AT{{color:var(--pos);border-color:var(--pos)}}
.zb-NEAR{{color:var(--ink)}}
.zb-APPROACHING{{color:var(--muted)}}
.zb-NO{{color:var(--neg);border-color:var(--neg)}}
.zb-SUSPECT{{color:var(--warn);border-color:var(--warn)}}
.znfoot{{display:flex;gap:10px;align-items:center;margin-top:8px;
  color:var(--muted);font-size:11.5px}}
.znmore{{background:var(--panel);color:var(--ink);border:1px solid var(--line);
  border-radius:7px;padding:4px 10px;font:inherit;font-size:11.5px;cursor:pointer}}
.znmore:hover{{border-color:var(--accent)}}
</style></head><body>
{ui.nav("zones", 1)}
<div class="znwrap">
<div class="znhead"><h1>Support zones</h1>
<div class="znsub">session <b>{ui.esc(asof)}</b> &middot; {n_tick:,} stocks &middot;
{len(rows):,} levels &middot; every horizontal level within
{Z.BAND_APPROACHING:.0%} below price, and what price did there before.</div></div>

<div class="znev">
<div><b>What these columns are worth, measured.</b> 325,061 historical episodes and
37,854 point-in-time observations over 12 non-overlapping dates.</div>
<div><b>touches</b> &mdash; {ui.esc(Z.EVIDENCE["touches"])}</div>
<div><b>rally med</b> &mdash; {ui.esc(Z.EVIDENCE["bounce_median"])}</div>
<div><b>drawdown / broke &gt;10%</b> &mdash; {ui.esc(Z.EVIDENCE["dd_median"])}</div>
<div><b>NO SUPPORT</b> &mdash; {ui.esc(Z.EVIDENCE["no_support"])}</div>
<div><b>SUSPECT</b> &mdash; prices not trusted: an unadjusted split, or a fall of
more than {1 - Z.DATA_SUSPECT_PCT_HI:.0%} from the 250d high. Levels drawn from
prices twenty times higher are not information, so these are reported and
excluded rather than counted as having no support.</div>
<div><b>band</b> &mdash; {ui.esc(Z.EVIDENCE["band"])}</div>
</div>

<div class="znbar">
  <input id="znq" type="search" placeholder="filter tickers..." autocomplete="off">
  <button class="znchip on" data-b="all">all<span class="n">{len(rows):,}</span></button>
  <button class="znchip" data-b="AT">AT<span class="n">{n_at:,}</span></button>
  <button class="znchip" data-b="NEAR">NEAR<span class="n">{n_near:,}</span></button>
  <button class="znchip" data-b="APPROACHING">APPROACHING<span class="n">{n_appr:,}</span></button>
  <button class="znchip" data-b="NO SUPPORT">no support<span class="n">{n_none:,}</span></button>
  <button class="znchip" data-b="SUSPECT">prices suspect<span class="n">{n_sus:,}</span></button>
</div>
<div class="znbar znbar2">
  <span class="znlab">touches &ge;</span>
  <select class="znsel" id="znt"><option value="0">any</option><option>3</option>
    <option>5</option><option>8</option><option>12</option></select>
  <span class="znlab">rally/ATR &ge;</span>
  <select class="znsel" id="znr"><option value="0">any</option><option>2</option>
    <option>4</option><option>6</option><option>10</option></select>
  <span class="znlab">broke &gt;10% &le;</span>
  <select class="znsel" id="znd"><option value="101">any</option><option>50</option>
    <option>25</option><option>10</option><option value="0.001">never</option></select>
  <span class="znlab">% of 250d high &le;</span>
  <select class="znsel" id="znh"><option value="101">any</option><option>90</option>
    <option>75</option><option>64</option><option>50</option></select>
  <button class="znchip" id="znclear">clear</button>
</div>

<div class="zntw"><table id="zntab"><thead><tr>{heads}</tr></thead>
<tbody id="znbody"></tbody></table></div>
<div class="znfoot"><span id="zncount"></span>
  <button id="znmore" class="znmore">show all matches</button></div>
</div>
<script>
(function(){{
const D={blob}, K={kinds};
const CAP=400; let cap=CAP, bf='all', q='', mt=0, mr=0, md=101, mh=101;
let sc=2, sd=1;                      // default: nearest level first
const $=s=>document.getElementById(s);
const pc=v=>v==null?'&ndash;':(v*100).toFixed(1)+'%';
const f2=v=>v==null?'&ndash;':v.toFixed(2);
const iv=v=>v==null?'&ndash;':String(Math.round(v));
function cell(v,k){{return k==='p'?pc(v):k==='f2'?f2(v):k==='i'?iv(v):(v||'&ndash;');}}
function match(r){{
  if(bf!=='all'&&r[1]!==bf) return false;
  if(q&&r[0].toLowerCase().indexOf(q)<0) return false;
  if(mt>0&&!(r[4]>=mt)) return false;
  if(mr>0&&!(r[7]>=mr)) return false;
  if(md<=100&&!(r[9]!=null&&r[9]*100<=md)) return false;
  if(mh<=100&&!(r[10]!=null&&r[10]*100<=mh)) return false;
  return true;
}}
function tie(a,b){{return a[0]<b[0]?-1:1;}}
function render(){{
  const m=D.filter(match);
  const s=m.slice().sort(function(a,b){{
    var x=a[sc],y=b[sc];
    if(x==null&&y==null) return tie(a,b);
    if(x==null) return 1; if(y==null) return -1;   // blanks last both ways
    if(x===y) return tie(a,b);
    return (x>y?1:-1)*sd;
  }});
  const show=s.slice(0,cap);
  $('znbody').innerHTML=show.map(function(r){{
    var b=r[1], cls=b==='NO SUPPORT'?'zb-NO':'zb-'+b.replace(/ /g,'');
    var td='<tr><td><b>'+r[0]+'</b></td><td><span class="zb '+cls+'">'+(b||'&ndash;')+'</span></td>';
    for(var i=2;i<K.length;i++) td+='<td class="'+(K[i]==='s'?'':'n')+'">'+cell(r[i],K[i])+'</td>';
    return td+'</tr>';
  }}).join('');
  $('zncount').innerHTML=show.length<m.length
    ? 'showing '+show.length.toLocaleString()+' of '+m.length.toLocaleString()+' matches'
    : m.length.toLocaleString()+(m.length===1?' match':' matches');
  $('znmore').style.display=show.length<m.length?'':'none';
}}
$('znq').addEventListener('input',function(e){{q=e.target.value.trim().toLowerCase();cap=CAP;render();}});
$('znmore').addEventListener('click',function(){{cap=D.length;render();}});
$('znt').addEventListener('change',function(e){{mt=+e.target.value;cap=CAP;render();}});
$('znr').addEventListener('change',function(e){{mr=+e.target.value;cap=CAP;render();}});
$('znd').addEventListener('change',function(e){{md=+e.target.value;cap=CAP;render();}});
$('znh').addEventListener('change',function(e){{mh=+e.target.value;cap=CAP;render();}});
$('znclear').addEventListener('click',function(){{
  bf='all';q='';mt=0;mr=0;md=101;mh=101;cap=CAP;
  $('znq').value='';$('znt').value='0';$('znr').value='0';$('znd').value='101';
  $('znh').value='101';
  document.querySelectorAll('.znchip[data-b]').forEach(function(o){{o.classList.toggle('on',o.dataset.b==='all');}});
  render();}});
document.querySelectorAll('.znchip[data-b]').forEach(function(b){{b.addEventListener('click',function(){{
  bf=b.dataset.b;cap=CAP;
  document.querySelectorAll('.znchip[data-b]').forEach(function(o){{o.classList.toggle('on',o===b);}});
  render();}});}});
document.querySelectorAll('#zntab th.s').forEach(function(th){{th.addEventListener('click',function(){{
  var c=+th.dataset.c;
  if(c===sc){{sd=-sd;}} else {{sc=c;sd=(K[c]==='s')?1:-1;}}
  document.querySelectorAll('#zntab th').forEach(function(o){{o.classList.remove('asc','desc');}});
  th.classList.add(sd>0?'asc':'desc');
  cap=CAP;render();}});}});
render();
}})();
</script></body></html>"""

    config.REPORTS_ZONES.mkdir(parents=True, exist_ok=True)
    p = config.REPORTS_ZONES / f"{asof}.html"
    p.write_text(html, encoding="utf-8")
    (config.REPORTS_ZONES / "latest.html").write_text(html, encoding="utf-8")
    return p


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Render the zones page.")
    ap.add_argument("--asof", default=None)
    a = ap.parse_args()
    p = build(asof=a.asof)
    print(f"  wrote {p}  ({p.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
