#!/usr/bin/env python
"""The Sectors page: how each sector and sub-industry is doing, and its stocks.

One table of sectors (with the validated sector score), one of sub-industries
(context: same components, no validated score), the coming month's seasonal history
(context: measured NOT predictive), and for each group the stocks currently in a
category (Candidates, Floor retest, Bounce flags, AT a support zone). Click a row to
list them. Every claim printed here comes from sector_study / season_study summaries.
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
import sector_score as SC
import ui
from zones_page import _ESC

OUT_DIR = config.REPORTS / "sectors"


def _category_members(asof: str) -> dict[str, set]:
    cats: dict[str, set] = {}
    try:
        import good_level as G
        z, trusted, df = G.current(asof)
        cats["Candidates"] = set(df[G.rule(df)].ticker.astype(str))
    except Exception:                                            # noqa: BLE001
        cats["Candidates"] = set()
    try:
        import floorretest_study as F
        c = F.current(asof)
        cats["Floor retest"] = set(c[c.pattern.astype(bool)].ticker) if len(c) else set()
    except Exception:                                            # noqa: BLE001
        cats["Floor retest"] = set()
    try:
        f = pd.read_parquet(config.FLAGS / f"{asof}.parquet")
        cats["Bounce flag"] = set(f.ticker.astype(str))
    except Exception:                                            # noqa: BLE001
        cats["Bounce flag"] = set()
    try:
        z = pd.read_parquet(config.ZONES / f"{asof}.parquet")
        z = z[~z.get("suspect_split", False).fillna(False).astype(bool)]
        cats["AT a zone"] = set(z.loc[z.band == "AT", "ticker"].astype(str))
    except Exception:                                            # noqa: BLE001
        cats["AT a zone"] = set()
    return cats


def _evidence() -> str:
    out = []
    try:
        s = json.loads(SC.STUDY.read_text(encoding="utf-8"))
        sec = s["sector"]["components"]
        comp = s["sector"]["composite"].get("h21", {})
        out.append(
            f"<div><b>The sector score is measured.</b> Average rank of the 1-month return vs the market and the "
            f"share of members above their 200-day average. Chosen on {ui.esc(comp.get('train', ['?'])[0])}.."
            f"{ui.esc(comp.get('train', ['?', '?'])[1])}, then tested on {ui.esc(comp.get('test', ['?'])[0])}.."
            f"{ui.esc(comp.get('test', ['?', '?'])[1])} which it had never seen: <b>IC {comp.get('oos_ic', 0):+.3f}, "
            f"t = {comp.get('oos_t', 0):+.2f}</b> over {comp.get('oos_dates')} months. Over the full history the top third "
            f"of sectors by breadth beat the bottom third by {sec['breadth200_h21']['spread'] * 100:+.1f}pp a month "
            f"(t = {sec['breadth200_h21']['spread_t']:+.2f}). Real but modest: it tilts the odds for a month, it "
            f"does not pick stocks.</div>")
        out.append("<div><b>Sub-industries are context.</b> The same readings predict at that level over the full "
                   "history, but did not survive a first-half-only selection, so they carry no score.</div>")
    except Exception:                                            # noqa: BLE001
        out.append("<div><b>Sector study not on file</b> -- treat the score as unmeasured.</div>")
    try:
        s = json.loads(SC.SEASON.read_text(encoding="utf-8"))
        L = s["levels"]
        out.append(f"<div><b>Seasonality: measured, and NOT predictive.</b> A group's average for the same calendar "
                   f"month in earlier years did not predict that month: sectors t = {L['sector']['t']:+.2f}, "
                   f"sub-industries t = {L['subind']['t']:+.2f}, single stocks t = {L['stock']['t']:+.2f} "
                   f"(72 months). The {ui.esc(L['sector'].get('next_month', ''))} history is shown for reading only. "
                   f"That includes energy: the expected winter strength in gas utilities is not in the data.</div>")
    except Exception:                                            # noqa: BLE001
        pass
    out.append("<div>Groups come from SEC SIC codes; &ldquo;Other&rdquo; is the code-less remainder, not a "
               "sector. Returns are equal-weighted over stocks tradeable that day.</div>")
    return "".join(out)


def build(asof: str | None = None) -> tuple[Path, int]:
    asof = asof or calendar_us.last_closed_session()
    g = SC.load_groups(asof)
    per = SC.load(asof)
    if g is None or per is None or g.empty:
        raise RuntimeError(f"no sector data on or before {asof}; run sector_score.py first")
    cats = _category_members(asof)
    rows = []
    for r in g.itertuples(index=False):
        members = per[per[r.level] == r.group].ticker.astype(str)
        inc = {k: sorted(set(members) & v) for k, v in cats.items()}

        def num(v, nd=4):
            try:
                f = float(v)
                return None if not np.isfinite(f) else round(f, nd)
            except (TypeError, ValueError):
                return None
        rows.append({"level": r.level, "group": r.group, "n": int(r.n),
                     "score": num(getattr(r, "score", None), 1), "rank": num(getattr(r, "rank", None), 0),
                     "rs21": num(np.expm1(r.rs21)), "rs63": num(np.expm1(r.rs63)), "rs126": num(np.expm1(r.rs126)),
                     "rs252": num(np.expm1(r.rs252)), "breadth": num(r.breadth200), "hilo": num(r.hilo),
                     "fund": num(r.fund, 1), "season": num(r.season_mean), "season_pos": num(r.season_pos, 2),
                     "season_years": num(r.season_years, 0), "cats": inc})
    month = g["season_month"].dropna().iloc[0] if g["season_month"].notna().any() else ""
    data = json.dumps(rows, separators=(",", ":"), allow_nan=False).translate(_ESC)
    html = _TEMPLATE.replace("@@CSS@@", ui.CSS).replace("@@NAV@@", ui.nav("sectors", 1)) \
        .replace("@@ASOF@@", ui.esc(str(g["session"].iloc[0]))).replace("@@EVIDENCE@@", _evidence()) \
        .replace("@@MONTH@@", ui.esc(month)).replace("@@DATA@@", data)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "latest.html"
    out.write_text(html, encoding="utf-8")
    return out, len(rows)


_TEMPLATE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sectors &middot; @@ASOF@@</title>
<style>@@CSS@@
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,Segoe UI,Roboto,sans-serif}
.scwrap{padding:16px 18px 40px;max-width:1500px;margin:0 auto}
.scwrap h1{font-size:19px;margin:0 0 2px}
.scwrap h2{font-size:15px;margin:22px 0 6px}
.scsub{color:var(--muted);font-size:12.5px;margin-bottom:10px}
.scev{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--warn);border-radius:8px;
  padding:10px 13px;margin:10px 0 14px;font-size:12.5px}
.scev div{margin:3px 0}
.sctw{overflow:auto;border:1px solid var(--grid);border-radius:8px}
table.sct{width:100%;border-collapse:collapse;font-size:12.5px;min-width:1100px}
.sct th{position:sticky;top:0;background:var(--head);text-align:left;color:var(--muted);font-weight:500;font-size:11.5px;
  padding:6px 8px;border-bottom:1px solid var(--line);cursor:pointer;white-space:nowrap}
.sct th.n,.sct td.n{text-align:right;font-variant-numeric:tabular-nums}
.sct td{padding:4px 8px;border-bottom:1px solid var(--grid);white-space:nowrap}
.sct tr.scr{cursor:pointer}
.sct tr.scr:hover,.sct tr.scr.open{background:var(--grid)}
.scdet td{background:var(--panel);white-space:normal;padding:8px 12px;font-size:12.5px}
.scdet a{color:var(--ink)}
.scbar{display:inline-block;height:8px;background:var(--accent);border-radius:2px;vertical-align:middle;margin-right:6px}
.scm{color:var(--muted)}
.scpos{color:var(--pos)} .scneg{color:var(--neg)}
</style></head><body>
@@NAV@@
<div class="scwrap">
<h1>Sectors</h1>
<div class="scsub">session <b>@@ASOF@@</b> &middot; how each sector and sub-industry is doing against the whole market,
and which of its stocks sit in a category today. Click a row for the list.</div>
<div class="scev">@@EVIDENCE@@</div>
<h2>Sectors &mdash; with the measured score</h2>
<div class="sctw"><table class="sct" id="tsec"><thead></thead><tbody></tbody></table></div>
<h2>Sub-industries &mdash; context, no validated score</h2>
<div class="sctw"><table class="sct" id="tsub"><thead></thead><tbody></tbody></table></div>
</div>
<script>
(function(){
var D=@@DATA@@, MONTH='@@MONTH@@';
function eh(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;');}
function pc(v,d){return v==null?'&ndash;':'<span class="'+(v>0?'scpos':v<0?'scneg':'')+'">'+(v>0?'+':'')+(v*100).toFixed(d==null?1:d)+'%</span>';}
function pl(v){return v==null?'&ndash;':(v*100).toFixed(0)+'%';}
function f0(v){return v==null?'&ndash;':v.toFixed(0);}
var COLS=[
 ['group','group','s'],['n','stocks','i'],['score','sector score','score'],['rs21','1 mo vs market','pc'],
 ['rs63','3 mo','pc'],['rs126','6 mo','pc'],['rs252','12 mo','pc'],['breadth','above 200-day','pl'],
 ['hilo','highs minus lows','pl'],['fund','median fund. score','f0'],['season','past '+MONTH+' vs market','pc'],
 ['season_pos',MONTH+' up, share of years','pl'],['cats','in a category today','cats']];
function cell(r,c){
  var v=r[c[0]];
  switch(c[2]){
    case 's': return '<b>'+eh(v)+'</b>';
    case 'i': return String(v);
    case 'score': return v==null?'&ndash;':'<span class="scbar" style="width:'+(v*0.6).toFixed(0)+'px"></span>'+v.toFixed(0);
    case 'pc': return pc(v);
    case 'pl': return pl(v);
    case 'f0': return f0(v);
    case 'cats': var k=Object.keys(v), n=0; k.forEach(function(x){n+=v[x].length;}); return n?String(n):'&ndash;';
  }
  return '';
}
function table(id, level, withScore){
  var cols=COLS.filter(function(c){return withScore||c[0]!=='score';});
  var rows=D.filter(function(r){return r.level===level;});
  var sc=withScore?'score':'rs252', sd=-1, open={};
  var t=document.getElementById(id);
  t.tHead.innerHTML='<tr>'+cols.map(function(c){return '<th data-k="'+c[0]+'" class="'+(c[2]==='s'?'':'n')+'">'+eh(c[1])+'</th>';}).join('')+'</tr>';
  function render(){
    rows.sort(function(a,b){var x=a[sc],y=b[sc]; if(sc==='cats'){x=Object.values(x).flat().length;y=Object.values(y).flat().length;}
      if(x==null&&y==null)return 0; if(x==null)return 1; if(y==null)return -1; return (x>y?1:x<y?-1:0)*sd;});
    var h='';
    rows.forEach(function(r){
      h+='<tr class="scr'+(open[r.group]?' open':'')+'" data-g="'+eh(r.group)+'">'+cols.map(function(c){return '<td class="'+(c[2]==='s'?'':'n')+'">'+cell(r,c)+'</td>';}).join('')+'</tr>';
      if(open[r.group]){
        var parts=Object.keys(r.cats).map(function(k){var L=r.cats[k];
          return '<div><b>'+eh(k)+'</b> ('+L.length+'): '+(L.length?L.map(function(t){return '<a href="../stock/'+encodeURIComponent(t)+'.html">'+eh(t)+'</a>';}).join(', '):'<span class="scm">none</span>')+'</div>';});
        h+='<tr class="scdet"><td colspan="'+cols.length+'">'+parts.join('')+'</td></tr>';
      }
    });
    t.tBodies[0].innerHTML=h;
  }
  t.tHead.addEventListener('click',function(e){var th=e.target.closest('th'); if(!th)return; var k=th.dataset.k; if(k===sc)sd=-sd; else{sc=k;sd=(k==='group')?1:-1;} render();});
  t.tBodies[0].addEventListener('click',function(e){if(e.target.closest('a'))return; var tr=e.target.closest('tr.scr'); if(!tr)return; var g=tr.dataset.g; open[g]=!open[g]; render();});
  render();
}
table('tsec','sector',true);
table('tsub','subind',false);
})();
</script></body></html>
"""


def selftest(asof: str | None = None) -> int:
    out, n = build(asof)
    h = out.read_text(encoding="utf-8")
    assert "Sectors" in h and n > 20, f"only {n} groups"
    import re
    vis = re.sub(r"<script\b.*?</script>", "", h, flags=re.S)
    for tok in ("nan", "None", "undefined", "NaN"):
        assert not re.search(rf">\s*{tok}\s*<", vis), f"page renders {tok}"
    print(f"sectors page selftest OK  ({n} groups)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the Sectors page.")
    ap.add_argument("--asof", default=None)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest(a.asof)
    out, n = build(a.asof)
    print(f"  wrote {out}  ({out.stat().st_size / 1024:.0f} KB, {n} groups)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
