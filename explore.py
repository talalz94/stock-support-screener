"""
`reports/explore.html` -- every stock, every score, sortable and filterable.

    python explore.py                     build it
    python explore.py --metrics pe,roe    pick the columns
    python explore.py --open

One row per ticker, one column per metric, pulled from the tidy score table
across ALL modules. Click a header to sort. Type in the filter boxes to narrow.
Click a ticker to open its profile page; click the chart glyph for TradingView.

WHY THE FILTERING IS CLIENT-SIDE
----------------------------------
The alternative is regenerating the page per query, which needs a server, and
the server is opt-in here (`serve.py`) precisely so the reports keep working as
plain files. ~3,400 rows x ~20 columns of numbers is about 1.5 MB of JSON --
small enough that the browser sorts it instantly and the page stays a file you
can email to yourself. Above roughly 20,000 rows this choice would flip.

**Rows are rendered on demand, not all at once.** Building 3,400 rows x 20 cells
of DOM up front costs seconds and a lot of memory; the table paints only the
visible slice and re-paints on sort/filter/scroll. That is why sorting stays
instant on the full universe.

NO DATA IS NOT ZERO
---------------------
A missing metric renders as an em-dash and sorts to the END regardless of
direction, never as 0. Sorting NaN as zero would put every unscored company at
the top of an ascending P/E screen, which is the most dangerous possible
presentation of "we don't know".
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import config

config.safe_console()

import calendar_us                                               # noqa: E402
import ui                                                        # noqa: E402

OUT = config.REPORTS_EXPLORE / "latest.html"

# Sensible default columns, spanning all three modules. Anything in the score
# store can be requested with --metrics.
DEFAULT_METRICS = [
    # The three combined scores, first. They were in the store all along but not
    # in this list, so the one table that shows every stock never showed them
    # (added 2026-10-12). Each tooltip carries its out-of-sample verdict.
    "combo_h1", "combo_h20", "combo_h60",
    # The stock's SECTOR score (sector_score.py) -- measured out of sample; joined
    # in `collect` because it lives outside the score store.
    "sector_score",
    # The bounce trade at support and the plan items of 2026-11-08 (shortlist.py via
    # explore_columns): where the level is, the measured trade plan, how often the name runs,
    # and how it is doing against its sector and the market. Each tooltip carries what was
    # measured -- most of them nothing.
    "at_support", "above_level_pct", "buy_limit", "target", "stop", "atr_pct",
    "gain_days", "loss_days", "rally_count", "recovery_rate",
    "rs_sec_63", "rs_sec_252", "rs_sub_63", "rs_mkt_63", "narrative_now",
    "fund_score", "quality_score", "value_score", "growth_score", "safety_score",
    "pe", "pb", "ev_ebitda", "fcf_yield", "roe", "roic", "f_score", "z_score",
    # The growth pillar went from 5 metrics to 11 on 2026-08-23. The sequential
    # and margin-direction ones are here because "revenue growing AND margins
    # expanding" is the thesis this table exists to express, and it was
    # previously unsayable.
    "rev_growth", "rev_growth_q", "eps_growth", "eps_growth_q",
    "gross_margin_chg", "op_margin_chg",
    "net_issuance", "mktcap",
    "hype_score", "premium_score", "attention_score", "ps_ratio",
    # `sent_age` travels WITH the sentiment score, never without it. A 30-day
    # mean says nothing about when its inputs arrived: DPRO read 0.50 from three
    # articles whose newest was 27 sessions old. The decayed twin sits beside
    # the plain one so the study's verdict is readable on the page.
    "sent_mean_30d", "sent_decay_30d", "sent_age", "news_count_30d",
    "dip_score", "dip_gate", "drawdown",
    # Insider and House buying (scores/insider.py, 2026-10-27): the walk-forward
    # score and the two filters measured to matter, then the plain readings.
    "insider_score", "small_buy_30d", "rare_buy_30d", "buyers_30d", "value_30d",
    "house_big_buy_60d",
]

# COLUMN VIEWS (2026-11-09: 61 columns at once was "too cluttered"). Each view names its columns
# by key, shown in the table's own order; a key this build does not have is skipped. "Custom" is
# the reader's own set from the column chooser. (key, label, short label for a phone, what it is,
# columns -- None = every column.)
VIEWS = (
    ("essentials", "Essentials", "Essentials",
     "the measured scores, the plan at support, quality, strength, news and size",
     ["combo_h20", "combo_h60", "sector_score", "at_support", "above_level_pct", "buy_limit",
      "fund_score", "pe", "rs_sec_63", "sent_mean_30d", "insider_score", "mktcap"]),
    ("plan", "Trade plan", "Plan",
     "where the support level is, the measured bounce trade, and how often the name runs",
     ["at_support", "above_level_pct", "buy_limit", "target", "stop", "atr_pct", "gain_days",
      "loss_days", "rally_count", "recovery_rate", "sector_score", "fund_score"]),
    ("quality", "Quality & value", "Quality",
     "the fundamental pillars, valuation and returns on capital",
     ["fund_score", "quality_score", "value_score", "safety_score", "pe", "pb", "ev_ebitda",
      "fcf_yield", "roe", "roic", "f_score", "z_score", "net_issuance", "mktcap"]),
    ("growth", "Growth", "Growth",
     "revenue and earnings growth, and whether margins are widening",
     ["growth_score", "rev_growth", "rev_growth_q", "eps_growth", "eps_growth_q", "gross_margin_chg",
      "op_margin_chg", "fund_score", "ps_ratio", "mktcap"]),
    ("momentum", "Momentum", "Momentum",
     "strength against the market and sector, rallies, dips and hype",
     ["rs_mkt_63", "rs_sec_63", "rs_sec_252", "rs_sub_63", "gain_days", "rally_count",
      "recovery_rate", "drawdown", "dip_score", "dip_gate", "hype_score", "attention_score",
      "premium_score"]),
    ("news", "News & insiders", "News",
     "news tone and its age, the sector narrative, insider and House buying",
     ["sent_mean_30d", "sent_decay_30d", "sent_age", "news_count_30d", "narrative_now",
      "insider_score", "small_buy_30d", "rare_buy_30d", "buyers_30d", "value_30d",
      "house_big_buy_60d"]),
    ("all", "All columns", "All", "every column", None),
)
DEFAULT_VIEW = "essentials"

# Metrics where LOW is conventionally better -- used only to colour the cell,
# never to reorder anything. Sorting is always literal.
LOWER_BETTER = {"pe", "pb", "ev_ebitda", "ps_ratio", "net_issuance",
                "m_score", "days_since_filing"}
INTEGERISH = {"news_count_30d", "f_score", "bars_used", "sent_age"}
NCOL = 4            # each row: ticker, name, sector, exchange, then the metrics (see _payload)


def static_sessions() -> set[str]:
    """Sessions that exist as a plain dated file, i.e. reachable over file://."""
    import os
    out = set()
    for f in config.REPORTS_EXPLORE.glob("*.html"):
        stem = f.stem
        if len(stem) == 10 and stem[4] == "-":
            out.add(stem)
    return out


def stored_sessions(limit: int = 400) -> list[str]:
    """Every session the SCORE STORE can render, newest first.

    History used to mean "sessions we happened to keep an HTML copy of" -- 20 of
    them, 15 MB. It now means "sessions we have data for", which is 154 and
    costs nothing, because `scores.sessions_stored` is indexed and a snapshot
    renders in ~4s on demand. Nothing is duplicated to disk to make this work:
    the explore table is a pivot of the score store, so a second copy would be
    the same numbers stored twice and able to disagree.
    """
    import scores
    scores.load_all()
    out: set[str] = set()
    for mod in config.SCORE_MODULES:
        try:
            out |= set(scores.sessions_stored(mod))
        except Exception:                                        # noqa: BLE001
            continue
    return sorted(out, reverse=True)[:limit]


def available_sessions(limit: int = 400) -> list[str]:
    """Everything the picker may offer: stored sessions, static files, today."""
    out = set(stored_sessions(limit)) | static_sessions()
    try:
        out.add(calendar_us.last_closed_session())
    except Exception:                                            # noqa: BLE001
        pass
    return sorted(out, reverse=True)[:limit]


def collect(metrics: list[str], asof: str) -> tuple[pd.DataFrame, dict]:
    """Wide (ticker x metric) frame from the tidy score table, newest session
    per module. Also returns per-module provenance."""
    import scores
    scores.load_all()

    frames, prov = [], {}
    for mod in config.SCORE_MODULES:
        sess = [s for s in scores.sessions_stored(mod) if s <= asof]
        if not sess:
            prov[mod] = None
            continue
        prov[mod] = sess[-1]
        df = scores.read(module=mod, start=sess[-1], end=sess[-1])
        if df is None or df.empty:
            continue
        keep = df[df["metric"].isin(metrics)]
        if keep.empty:
            continue
        frames.append(keep.pivot_table(index="ticker", columns="metric",
                                       values="value", aggfunc="last"))
    if not frames:
        return pd.DataFrame(), prov

    wide = pd.concat(frames, axis=1)
    wide = wide.loc[:, ~wide.columns.duplicated()]

    # Metadata every row wants: name, exchange (for the TradingView link) and
    # sector (the single most-used filter).
    try:
        u = pd.read_parquet(config.UNIVERSE_FILE)[["ticker", "name", "exchange"]]
        wide = wide.join(u.set_index("ticker"), how="left")
    except Exception:                                            # noqa: BLE001
        wide["name"] = None
        wide["exchange"] = None
    try:
        import macro
        m = macro.load_sector_map()[["ticker", "sector"]]
        wide = wide.join(m.set_index("ticker"), how="left")
    except Exception:                                            # noqa: BLE001
        wide["sector"] = None

    # THE SECTOR SCORE is not a score module (sector_score.py writes its own file),
    # so it is joined here from the newest file on or before asof. Missing file ->
    # the column is blank, never zero.
    if "sector_score" in metrics:
        try:
            import sector_score as SC
            sx = SC.load(asof)
            if sx is not None and len(sx):
                wide = wide.join(sx.drop_duplicates("ticker").set_index("ticker")[["sector_score"]], how="left")
                prov["sector"] = str(sx["session"].iloc[0])
        except Exception:                                        # noqa: BLE001
            pass

    # THE SHORTLIST / STUDY COLUMNS (2026-11-08) live outside the score store too: joined
    # from the newest shortlist build on or before asof. Missing -> blank, never zero.
    try:
        import shortlist as SL
        want = [m for m in metrics if m in SL.EXPLORE_COLS]
        if want:
            j = SL.explore_columns(asof)
            if len(j):
                wide = wide.join(j[want], how="left")
                prov["shortlist"] = j.attrs.get("asof")
    except Exception:                                            # noqa: BLE001
        pass

    return wide.sort_index(), prov


_EVIDENCE: dict | None = None


def evidence_note(metric: str) -> str:
    """What metric_evidence.py measured for this column: the bounce trade at support (top third
    minus bottom third, 2022-26, after choosing nothing) and the 20-day IC over every liquid
    name. Empty when the metric was not in the catalogue."""
    global _EVIDENCE
    if _EVIDENCE is None:
        try:
            _EVIDENCE = json.loads((config.DATA / "_metric_evidence.json").read_text(encoding="utf-8"))["results"]
        except Exception:                                        # noqa: BLE001
            _EVIDENCE = {}
    r = _EVIDENCE.get(metric)
    if not r:
        return ""
    bits = []
    for k, lab in (("t1_default", "at support, bought at the open"), ("t2_q1_rule", "at support, limit order"),
                   ("t3_ic", "all liquid names, 20-day IC")):
        x = r.get(k) or {}
        if x.get("test_t") is None:
            continue
        unit = "" if k == "t3_ic" else "pp a trade, top third minus bottom"
        verdict = "STRONG" if x.get("strong") else ("held in every period" if x.get("kept") else "not proven")
        bits.append(f"{lab}: {x['test']:+.3f}{unit and ' ' + unit} on 2022-26 (t {x['test_t']:+.2f}) -- {verdict}")
    return ("MEASURED (metric_evidence.py): " + "; ".join(bits)) if bits else ""


def _s(v) -> str:
    """A string cell that can never read 'nan' or 'None'."""
    if v is None or (isinstance(v, float) and not np.isfinite(v)) or pd.isna(v):
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("nan", "none", "<na>") else s


def _payload(wide: pd.DataFrame, metrics: list[str]) -> tuple[list, list, list]:
    """(cols, rows, dropped). `dropped` is REPORTED, never silently discarded.

    A requested metric with no stored column used to vanish here with no trace,
    which is the same class of bug as the profile card dropping `safety_score`:
    the reader asks for a column, does not get it, and nothing says why. An
    all-empty column across 3,400 rows would be worse, so the column is still
    omitted -- but the page now names what went missing.
    """
    cols = [m for m in metrics if m in wide.columns]
    dropped = [m for m in metrics if m not in wide.columns]
    rows = []
    for t, r in wide.iterrows():
        vals = []
        for c in cols:
            v = r.get(c)
            if v is None or (isinstance(v, float) and not np.isfinite(v)) \
                    or pd.isna(v):
                vals.append(None)
            else:
                vals.append(round(float(v), 4))
        # NOT `str(x or "")`: a float NaN is TRUTHY, so that idiom renders the
        # literal string "nan" -- which then became a real "nan" entry in the
        # sector dropdown. Normalise through pd.isna at the boundary.
        rows.append([str(t), _s(r.get("name")), _s(r.get("sector")),
                     _s(r.get("exchange"))] + vals)
    return cols, rows, dropped


CSS = """
/* ONLY what is specific to this page. Everything generic -- body, links,
   headings, .wrap, .sub, .note, base table styling, form controls -- comes from
   ui.CSS. Redefining a shared class here is what collapsed the filter bar: this
   file's `.bar` overrode `display` but not the `height:7px;overflow:hidden` that
   ui.CSS sets for percentile bars, so the whole control row became a sliver.
   `ui.selftest` now asserts no class is defined in both sheets. */
.filters{display:flex;flex-wrap:wrap;gap:8px;align-items:center;
margin:0 0 10px;padding:10px 12px;background:var(--panel);
border:1px solid var(--line);border-radius:9px;position:sticky;top:46px;z-index:20}
.filters input.num{width:82px}
.tk .star{cursor:pointer;margin-right:5px;color:var(--muted);
  user-select:none;font-size:13px}
.tk .star:hover{color:var(--warn)}
.tk .star.on{color:var(--warn)}
.insb{margin-left:5px;padding:0 5px;border-radius:9px;font-size:10px;font-weight:600;
  background:var(--blkbg);color:var(--blk)}
.evw{margin-left:5px;padding:0 5px;border-radius:9px;font-size:10px;font-weight:600;
  background:var(--negbg);color:var(--neg);text-decoration:none;white-space:nowrap}
.eve{margin-left:5px;padding:0 5px;border-radius:9px;font-size:10px;font-weight:600;
  background:var(--warnbg);color:var(--warn);white-space:nowrap}
.eve.later{background:transparent;color:var(--muted);border:1px solid var(--line);font-weight:500}
.watchtog{display:inline-flex;align-items:center;gap:4px;font-size:12px;
  color:var(--muted);cursor:pointer}
.watchn{color:var(--warn)}
.filters .rule{display:inline-flex;gap:4px;align-items:center;
  padding:2px 4px;border:1px solid var(--line);border-radius:6px}
.filters .rule button{padding:0 6px;line-height:1.4}
/* Strategy rail. Sits to the RIGHT of the table so the eye reads the data
   first and the model second -- the model is a lens, not the subject. */
.layout{display:flex;gap:14px;align-items:flex-start}
.layout>.main{flex:1;min-width:0}
.rail{width:190px;flex:none;position:sticky;top:10px}
.rail h3{margin:0 0 6px;font-size:12px;color:var(--muted);
  text-transform:uppercase;letter-spacing:.05em}
.rail button{display:block;width:100%;text-align:left;margin-bottom:6px;
  padding:7px 9px;border:1px solid var(--line);border-radius:7px;
  background:var(--panel);cursor:pointer;font-size:12px;color:inherit}
.rail button.on{border-color:var(--accent);background:var(--posbg)}
.rail button b{display:block;font-size:13px;margin-bottom:2px}
.rail button span{display:block;color:var(--muted);font-size:11px}
.rail button .warnflag{color:var(--warn);margin-top:2px}
.rail .note{font-size:11px;color:var(--muted);margin-top:8px;line-height:1.4}
.filters .count{color:var(--muted);font-size:12px;margin-left:auto;
font-variant-numeric:tabular-nums}
.tblwrap{overflow:auto;max-height:72vh;border:1px solid var(--line);
border-radius:9px;background:var(--panel)}
.tblwrap table{border-collapse:separate;border-spacing:0;border:0;
border-radius:0;font-size:12px}
.tblwrap th{position:sticky;top:0;z-index:2;background:var(--head);
cursor:pointer;padding:8px}
.tblwrap th:hover{color:var(--accent)}
.tblwrap th.tk,.tblwrap td.tk{text-align:left;position:sticky;left:0;
background:var(--panel);z-index:1}
.tblwrap th.tk{z-index:3;background:var(--head)}
.tblwrap td{padding:5px 8px;white-space:nowrap}
.tblwrap td.tk{font-weight:600}
.tblwrap td.sec{text-align:left;color:var(--muted);font-size:11.5px}
.na{color:var(--muted)}
.sorted{color:var(--accent)}
.tv{font-size:11px;color:var(--muted);margin-left:5px}
/* Column views (2026-11-09): tab-like, the same look as a stock page's tabs. */
.viewbar{display:flex;align-items:center;gap:2px;margin:0 0 10px;border-bottom:1px solid var(--line)}
.viewbar .vb{display:flex;gap:2px;overflow-x:auto;scrollbar-width:none;min-width:0}
.viewbar .vb::-webkit-scrollbar{display:none}
.viewbar button[data-view]{border:0;border-radius:0;background:none;padding:9px 13px;font-size:13px;
font-weight:600;color:var(--muted);border-bottom:2px solid transparent;margin-bottom:-1px;white-space:nowrap}
.viewbar button[data-view]:hover{color:var(--ink);border-bottom-color:transparent}
.viewbar button[data-view].on{color:var(--accent);border-bottom-color:var(--accent)}
.viewbar .vtools{margin-left:auto;display:flex;align-items:center;gap:10px;padding:4px 0 4px 12px;flex:none}
#colN{color:var(--muted);font-size:12px;white-space:nowrap}
#viewSel{display:none;font-size:13px;padding:6px 9px;margin:4px 0}
th.off{display:none}
th.pinned{box-shadow:inset 0 -2px 0 var(--warn)}
/* Phones (2026-11-09). Measured at 375px: the 190px rail beside the table left the TABLE 139px
   wide, a filter rule (metric, test, value, remove) ran 369px and scrolled the page sideways, and
   the sticky filter bar held 200px of an 812px screen. */
@media (max-width:700px){
  .layout{flex-direction:column;align-items:stretch}
  .rail{width:auto;position:static;display:grid;grid-template-columns:1fr 1fr;gap:6px}
  .rail h3,.rail .note{grid-column:1/-1}
  .rail button{margin-bottom:0}
  .filters{position:static}
  .filters .rule{flex-wrap:wrap;max-width:100%}
  .filters .rule select{max-width:160px}
  .viewbar{border-bottom:0;flex-wrap:wrap;position:relative}
  .viewbar .vb{display:none}
  #viewSel{display:inline-block}
  /* the count can carry "(1 shown because sorted or filtered)": it wraps rather than
     pushing the page sideways (measured 402px at 375 before) */
  .viewbar .vtools{padding-left:4px;flex:1 1 auto;min-width:0;flex-wrap:wrap;justify-content:space-between}
  #colN{white-space:normal}
  /* the column list opens the full width of the bar, never past the screen's edge */
  .viewbar .chooser{position:static}
  .viewbar .chooser .pop{left:0;right:0;top:100%;width:auto}
}
"""


# The views and the column chooser (2026-11-09), a plain string spliced into render()'s script
# after the sort and filter code it reads. It replaces a hide/show that walked every cell of every
# drawn row after each draw: only the visible columns are drawn now.
VIEWS_JS = r"""
// ---- column views -------------------------------------------------------
// A view is a named set of columns; "Custom" is the reader's own, ticked in the column
// chooser. The choice lives in this browser (scr.prefs.explore, shared with the Settings page)
// and survives the nightly rebuild. A column you SORT or FILTER by is always shown, marked as
// such: a ranking by a value you cannot see is no use.
const VIEWS = __VIEWS__, VIEWKEYS = VIEWS.map(function(v){ return v.key; }).concat(['custom']);
const PREFS = window.scrPrefs || {get: function(){ return {}; }, set: function(){ return false; }};
const TH = Array.prototype.slice.call(document.querySelectorAll('#head th[data-m]'));
function exPrefs(){
  const e = PREFS.get().explore;
  return (e && typeof e === 'object' && !Array.isArray(e)) ? e : {};
}
function saveEx(e){ const p = PREFS.get(); p.explore = e; PREFS.set(p); }
// The chooser before views kept its hidden columns under its own key: carried over once, as Custom.
(function(){
  let old = null;
  try { old = JSON.parse(localStorage.getItem('explore.cols.v1') || 'null'); } catch (e) {}
  if (old && typeof old === 'object' && !PREFS.get().explore
      && Object.keys(old).some(function(k){ return old[k] === true; })) {
    saveEx({view: 'custom', custom: COLS.filter(function(c){ return !old[c]; })});
  }
  try { localStorage.removeItem('explore.cols.v1'); } catch (e) {}
})();
function base(){
  const e = exPrefs(), v = VIEWKEYS.indexOf(e.view) >= 0 ? e.view : '__DEFAULT__';
  if (v === 'custom') {
    const c = Array.isArray(e.custom) ? e.custom : COLS;
    return {view: v, set: new Set(c.filter(function(k){ return COLS.indexOf(k) >= 0; }))};
  }
  const d = VIEWS.filter(function(x){ return x.key === v; })[0];
  return {view: v, set: new Set(d.cols.map(function(i){ return COLS[i]; }))};
}
function computeVis(){
  const b = base(), extra = new Set();
  if (sortI !== null && sortI >= NCOL) extra.add(COLS[sortI - NCOL]);
  readRules().forEach(function(r){
    if ((!isNaN(r.lo) || !isNaN(r.hi)) && r.ci >= NCOL) extra.add(COLS[r.ci - NCOL]);
  });
  VIS = [];
  COLS.forEach(function(c, i){ if (b.set.has(c) || extra.has(c)) VIS.push(i); });
  let pinned = 0;
  TH.forEach(function(th){
    const c = COLS[+th.getAttribute('data-i') - NCOL], own = b.set.has(c), shown = own || extra.has(c);
    th.classList.toggle('off', !shown); th.classList.toggle('pinned', shown && !own);
    if (shown && !own) pinned++;
  });
  document.querySelectorAll('#viewbar button[data-view]').forEach(function(x){
    const on = x.getAttribute('data-view') === b.view;
    x.classList.toggle('on', on); x.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
  document.getElementById('viewSel').value = b.view;
  document.querySelectorAll('#colList input[data-c]').forEach(function(x){ x.checked = b.set.has(x.getAttribute('data-c')); });
  document.getElementById('colN').textContent = VIS.length + ' of ' + COLS.length + ' columns'
    + (pinned ? ' (' + pinned + ' shown because sorted or filtered)' : '');
}
function setView(v){ const e = exPrefs(); e.view = v; saveEx(e); computeVis(); paint(true); }
document.getElementById('viewbar').addEventListener('click', function(ev){
  const x = ev.target.closest('button[data-view]'); if (x) setView(x.getAttribute('data-view'));
});
document.getElementById('viewSel').addEventListener('change', function(){ setView(this.value); });

// The column chooser: a tick or untick makes the view Custom, starting from what was shown.
const colList = document.getElementById('colList');
colList.innerHTML = TH.map(function(th){
  const m = th.getAttribute('data-m');
  return '<label><input type="checkbox" data-c="' + m + '"> ' + th.textContent
    + (th.getAttribute('data-v') === '1' ? ' <span class="pill ok" style="font-size:9px">validated</span>' : '')
    + '</label>';
}).join('');
function setCustom(set){
  saveEx({view: 'custom', custom: COLS.filter(function(k){ return set.has(k); })});
  computeVis(); paint(true);
}
colList.addEventListener('change', function(ev){
  const c = ev.target.getAttribute('data-c'); if (!c) return;
  const s = new Set(base().set);
  if (ev.target.checked) s.add(c); else s.delete(c);
  setCustom(s);
});
document.querySelector('#chooser .acts').addEventListener('click', function(ev){
  const act = ev.target.getAttribute('data-act'); if (!act) return;
  if (act === 'all') { setView('all'); return; }
  setCustom(new Set(act === 'none' ? [] : TH.filter(function(th){ return th.getAttribute('data-v') === '1'; })
                                           .map(function(th){ return th.getAttribute('data-m'); })));
});
document.getElementById('colsBtn').addEventListener('click', function(ev){
  ev.stopPropagation(); document.getElementById('chooser').classList.toggle('open');
});
document.addEventListener('click', function(ev){
  if (!ev.target.closest('#chooser')) document.getElementById('chooser').classList.remove('open');
});
// The Settings page (or another tab) changed the view: follow it.
window.addEventListener('storage', function(ev){ if (ev.key === 'scr.prefs') { computeVis(); paint(true); } });
"""


def _dropped_note(dropped) -> str:
    """Name the requested columns that had no data, rather than dropping them
    silently. An empty column across 3,400 rows would be worse than omitting
    it -- but so is omitting it with no explanation."""
    if not dropped:
        return ""
    names = ", ".join(f"<code>{ui.esc(d)}</code>" for d in sorted(dropped)[:10])
    more = f" +{len(dropped) - 10} more" if len(dropped) > 10 else ""
    return (f'<div class="note"><b>{len(dropped)} requested column(s) are not '
            f'shown</b> because no module stored them for this session: {names}'
            f'{more}.</div>')


def _rail_html(cols: list[str], strat_cov: dict | None) -> str:
    """The strategy rail. Each button states its own coverage, because a
    strategy that can only rank half the universe must say so BEFORE anyone
    reads its top ten -- that is the whole contract of this page."""
    if not strat_cov:
        return ""
    out = ['<div class="rail"><h3>Strategy</h3>',
           '<button data-si="-1" class="on"><b>None</b>'
           '<span>ticker order</span></button>']
    for key, cov in strat_cov.items():
        if cov["column"] not in cols:
            continue
        ev = cov.get("evidence") or ""
        # The verdict rides ON the button, not in a tooltip. A strategy that
        # has not been shown to predict anything must say so where the choice
        # is made -- hiding it behind a hover is how four unproven models come
        # to look like four working ones.
        mark = ("&#9888; unmeasured" if ev.startswith("UNMEASURED")
                else "&#9888; no edge measured" if ev.startswith("NO EDGE")
                else "")
        # data-si is the ROW index the sort reads: 4 leading fields (ticker, name, sector,
        # exchange) come before the metrics. It was the metric's own index until 2026-11-09, so
        # every strategy sorted by those leading fields -- "Growth" put the table in Z-A ticker
        # order -- and the Ticker header lit up as the sorted column.
        out.append(
            f'<button data-si="{NCOL + cols.index(cov["column"])}" '
            f'title="{ui.esc(cov["doc"])}{chr(10)}{chr(10)}{ui.esc(ev)}">'
            f'<b>{ui.esc(cov["title"])}</b>'
            f'<span>{cov["ranked"]:,} of {cov["universe"]:,} ranked</span>'
            + (f'<span class="warnflag">{mark}</span>' if mark else "")
            + '</button>')
    out.append('<div class="note">A strategy orders stocks by the things it '
               'names. None of these has been shown to predict returns &mdash; '
               'hover a tab for what was measured.</div>')
    out.append("</div>")
    return "".join(out)


def render(cols: list[str], rows: list, prov: dict, asof: str,
           sessions: list[str] | None = None,
           dropped: list[str] | None = None,
           strat_cov: dict | None = None,
           badges: dict | None = None,
           flags: dict | None = None) -> str:
    navbar = ui.nav("explore", 1,
                    ui.session_picker(asof, sessions or [],
                                      "{d}.html",
                                      static=static_sessions()))
    data = json.dumps({"cols": cols, "rows": rows}, separators=(",", ":"))
    sd = config.REPORTS / "stock"
    built = json.dumps(sorted(f.stem for f in sd.glob("*.html"))
                       if sd.is_dir() else [])
    lower = json.dumps(sorted(LOWER_BETTER))
    integerish = json.dumps(sorted(INTEGERISH))
    badge_js = json.dumps(badges or {}, separators=(",", ":")).replace("<", "\\u003c")
    # Warning filings and next earnings (news_events.page_flags), COMPACT: 3,000+ rows,
    # so the measured text goes in the legend once, not into every row.
    flag_js = json.dumps({t: {"w": [[w[0], w[2], w[3]] for w in (v.get("warn") or [])], "e": v.get("earn")}
                          for t, v in (flags or {}).items()},
                         separators=(",", ":")).replace("<", "\\u003c")
    try:
        import news_events as _NE
        _fh = _NE.flag_help()
        warn_help = " | ".join(v for k, v in _fh.items() if k != "earnings")
        earn_help, earn_soon = _fh.get("earnings", ""), int(_NE.EARN_SOON)
    except Exception:                                            # noqa: BLE001
        warn_help, earn_help, earn_soon = "news-event data unavailable", "", 10
    try:
        import insider_signals as _SIG
        badge_help = _SIG.BADGE_HELP
    except Exception:                                            # noqa: BLE001
        badge_help = ""
    src = " &middot; ".join(f"{k} @ {v}" for k, v in prov.items() if v) or "none"
    # The views, as column INDEXES into `cols`; a view's key this build lacks is skipped.
    views = [{"key": k, "label": label, "short": short, "hint": hint,
              "cols": [i for i, c in enumerate(cols) if want is None or c in want]}
             for k, label, short, hint, want in VIEWS]
    views_js = (VIEWS_JS.replace("__VIEWS__", json.dumps(views, separators=(",", ":")))
                .replace("__DEFAULT__", DEFAULT_VIEW))
    view_btns = "".join(
        f'<button type="button" data-view="{v["key"]}" aria-pressed="false" '
        f'title="{ui.esc(v["hint"])} ({len(v["cols"])} columns)">{ui.esc(v["label"])}</button>'
        for v in views) + ('<button type="button" data-view="custom" aria-pressed="false" '
                           'title="your own set: tick columns under Columns">Custom</button>')
    view_opts = "".join(f'<option value="{v["key"]}">{ui.esc(v["label"])}</option>' for v in views) \
        + '<option value="custom">Custom</option>'
    sectors = sorted({s for s in (_s(r[2]) for r in rows) if s})
    sec_opts = "".join(f'<option value="{s}">{s}</option>' for s in sectors)

    # Header text and tooltip both come from the metric dictionary, so a
    # column can never show a name the reference page does not explain.
    import metrics_doc
    valid = metrics_doc.validated()
    head = ('<th class="tk" data-i="0">Ticker</th>'
            '<th class="sec" data-i="2" style="text-align:left">Sector</th>'
            + "".join(
                f'<th data-i="{i + 4}" data-m="{ui.esc(c)}" '
                f'data-v="{1 if c in valid else 0}" '
                f'title="{ui.esc(metrics_doc.tooltip(c))}{chr(10) + chr(10) + ui.esc(evidence_note(c)) if evidence_note(c) else ""}">'
                f'{ui.esc(ui.label(c))}</th>'
                for i, c in enumerate(cols)))

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">{ui.EARLY_JS}
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Explore &middot; all stocks</title><style>{ui.CSS}{CSS}</style></head>
<body>{navbar}<div class="wrap">
<h1>Explore</h1>
<div class="sub">{len(rows):,} tickers &middot; session <b>{asof}</b> &middot;
scores from {src} </div>

<div class="viewbar" id="viewbar">
  <div class="vb" role="group" aria-label="Columns shown">{view_btns}</div>
  <select id="viewSel" aria-label="Columns shown">{view_opts}</select>
  <div class="vtools"><span id="colN"></span>
  <span class="chooser" id="chooser">
    <button type="button" id="colsBtn" aria-label="Choose columns">Columns &#9662;</button>
    <div class="pop">
      <div class="acts">
        <button type="button" data-act="all">all</button>
        <button type="button" data-act="valid" title="only metrics the study found significant at some horizon">validated</button>
        <button type="button" data-act="none">none</button>
      </div>
      <div id="colList"></div>
    </div>
  </span></div>
</div>

<div class="filters">
  <input id="q" placeholder="ticker or name..." autocomplete="off">
  <select id="sector"><option value="">all sectors</option>{sec_opts}</select>
  <span id="rules"></span>
  <button id="addRule" title="add another condition, ANDed with the rest">+ filter</button>
  <label class="watchtog" title="show only starred names">
    <input type="checkbox" id="watchOnly"> &#9733; only
    <span id="watchN" class="watchn"></span></label>
  <button id="reset">reset</button>
  <span class="count" id="count"></span>
</div>

<div class="layout">
<div class="main">
<div class="tblwrap" id="scroller">
  <table><thead><tr id="head">{head}</tr></thead>
  <tbody id="body"></tbody></table>
</div>
</div>
{_rail_html(cols, strat_cov)}
</div>

{_dropped_note(dropped)}
<div class="note"><b>Hover any column header</b> for what it measures, how to
read it and whether it has been measured &mdash; or open the
<a href="../metrics.html">metric dictionary</a> for the full reference.
<br><b>A missing metric is an em-dash and always sorts last</b>,
in both directions &mdash; never 0. Sorting absent values as zero would put
every unscored company at the top of an ascending P/E screen, which is the most
misleading way to show "we don't know". Colour marks direction only for metrics
where low is conventionally better; sorting itself is always literal.
<br><b>insider</b> after a ticker = {ui.esc(badge_help)}
<br><b>&#9888; tags</b> = a recent SEC filing measured as a WARNING (click for the filing): {ui.esc(warn_help)}
<br><b>earnings</b> tag (mm-dd &middot; sessions away) = {ui.esc(earn_help)}. A grey tag is further out.
<br>Rows render on demand, so sorting stays instant across the full universe.
</div>
</div>
<script>
const DATA = {data};
const LOWER = new Set({lower});
const INTEGERISH = new Set({integerish});
const BADGE = {badge_js};
const FLAGS = {flag_js}, SOON = {earn_soon}, HR = {{bmo: 'before the open', amc: 'after the close'}};
function evtags(tk){{
  const f = FLAGS[tk]; if (!f) return '';
  let h = '';
  for (const w of (f.w || [])) h += '<a class="evw" href="' + w[2] + '" target="_blank" rel="noopener"'
      + ' title="8-K public ' + w[1] + ' (see the legend for what was measured)">&#9888; ' + w[0] + '</a>';
  if (f.e) h += '<span class="eve' + (f.e[2] <= SOON ? '' : ' later') + '" title="next earnings ' + f.e[0]
      + (HR[f.e[1]] ? ', ' + HR[f.e[1]] : ', time not given') + '; reacts in ' + f.e[2] + ' session(s)">'
      + f.e[0].slice(5) + ' &middot; ' + f.e[2] + '</span>';
  return h;
}}
const COLS = DATA.cols, ROWS = DATA.rows;
const NCOL = 4;                      // ticker, name, sector, exchange
let view = ROWS.slice(), sortI = null, sortAsc = false, painted = 0;
let VIS = COLS.map(function(_, i){{ return i; }});    // the columns drawn: see computeVis
// Plain labels, the headers' own words, for the filter menu (it showed raw keys before).
const LABEL = {{}};
document.querySelectorAll('#head th[data-m]').forEach(function(th){{
  LABEL[th.getAttribute('data-m')] = th.textContent;
}});
const PAGE = 120;

const body = document.getElementById('body');
const scroller = document.getElementById('scroller');
// ---- watchlist ----------------------------------------------------------
// Persisted to data/_watchlist.json through /api/watchlist when a server is
// running, and mirrored to localStorage so the star still works when this page
// is opened as a plain file. The FILE is the source of truth, because that is
// what `validate` reads -- starred names are checked against filings every
// night instead of waiting weeks for the rotating sample to reach them.
const WKEY = 'explore.watch.v1';
let WATCH = new Set();
try {{ WATCH = new Set(JSON.parse(localStorage.getItem(WKEY) || '[]')); }}
catch (e) {{}}

function saveWatch(){{
  const list = Array.from(WATCH).sort();
  try {{ localStorage.setItem(WKEY, JSON.stringify(list)); }} catch (e) {{}}
  // Fire-and-forget: a page opened from disk has no server and must keep
  // working, so a failed POST is not an error the reader needs to see.
  try {{
    fetch('/api/watchlist', {{method: 'POST', cache: 'no-store',
      headers: {{'Content-Type': 'application/json', 'X-Screener': '1'}},
      body: JSON.stringify({{tickers: list}})}}).catch(function(){{}});
  }} catch (e) {{}}
  const el = document.getElementById('watchN');
  if (el) el.textContent = list.length ? list.length + ' starred' : '';
}}

function toggleWatch(tk, span){{
  if (WATCH.has(tk)) {{ WATCH.delete(tk); span.textContent = '☆';
                       span.classList.remove('on'); }}
  else {{ WATCH.add(tk); span.textContent = '★';
         span.classList.add('on'); }}
  saveWatch();
  if (document.getElementById('watchOnly').checked) apply();
}}

document.addEventListener('click', function(ev){{
  const sp = ev.target.closest ? ev.target.closest('.star') : null;
  if (sp) {{ ev.preventDefault(); toggleWatch(sp.getAttribute('data-tk'), sp); }}
}});

// Adopt the server's copy when there is one, so a list starred on another
// browser -- or added with `python watchlist.py --add` -- shows up here.
try {{
  fetch('/api/watchlist', {{cache: 'no-store'}})
    .then(function(r){{ return r.ok ? r.json() : null; }})
    .then(function(j){{
      if (!j || !j.tickers) return;
      WATCH = new Set(j.tickers);
      try {{ localStorage.setItem(WKEY, JSON.stringify(j.tickers)); }} catch (e) {{}}
      const el = document.getElementById('watchN');
      if (el) el.textContent = j.tickers.length ? j.tickers.length + ' starred' : '';
      paint(true);
    }}).catch(function(){{}});
}} catch (e) {{}}

// ---- filter rules -------------------------------------------------------
// Each rule is {{ci, lo, hi}} against one metric column. They are ANDed in
// apply(). Persisted, because the page is rebuilt nightly and a screen you
// set up should survive that -- same reasoning as the column chooser.
const RULEKEY = 'explore.filters.v1';
const rulesBox = document.getElementById('rules');

function metricSelect(sel){{
  const s = document.createElement('select');
  COLS.forEach(function(c, i){{
    const o = document.createElement('option');
    o.value = i + NCOL; o.textContent = LABEL[c] || c;
    if ((i + NCOL) === sel) o.selected = true;
    s.appendChild(o);
  }});
  return s;
}}

function addRule(ci, lo, hi){{
  const row = document.createElement('span');
  row.className = 'rule';
  const sel = metricSelect(ci === undefined ? NCOL : ci);
  const a = document.createElement('input');
  a.className = 'num'; a.placeholder = 'min'; a.autocomplete = 'off';
  if (lo !== undefined && lo !== null && lo === lo) a.value = lo;
  const b = document.createElement('input');
  b.className = 'num'; b.placeholder = 'max'; b.autocomplete = 'off';
  if (hi !== undefined && hi !== null && hi === hi) b.value = hi;
  const x = document.createElement('button');
  x.textContent = '×'; x.title = 'remove this condition';
  x.addEventListener('click', function(){{ row.remove(); saveRules(); apply(); }});
  [sel, a, b].forEach(function(el){{
    el.addEventListener(el.tagName === 'SELECT' ? 'change' : 'input',
                        function(){{ saveRules(); apply(); }});
  }});
  row.appendChild(sel); row.appendChild(a); row.appendChild(b); row.appendChild(x);
  rulesBox.appendChild(row);
}}

function readRules(){{
  return Array.prototype.map.call(rulesBox.children, function(row){{
    const el = row.querySelectorAll('select,input');
    return {{ci: parseInt(el[0].value, 10),
            lo: parseFloat(el[1].value), hi: parseFloat(el[2].value)}};
  }});
}}

function saveRules(){{
  try {{
    localStorage.setItem(RULEKEY, JSON.stringify(readRules().map(function(r){{
      return [r.ci, isNaN(r.lo) ? null : r.lo, isNaN(r.hi) ? null : r.hi];
    }})));
  }} catch (e) {{}}
}}

function loadRules(){{
  let saved = null;
  try {{ saved = JSON.parse(localStorage.getItem(RULEKEY) || 'null'); }} catch (e) {{}}
  if (saved && saved.length){{
    // A saved rule points at a column INDEX. If the column set changed since
    // it was saved, that index means a different metric -- so drop anything
    // out of range rather than silently filtering on the wrong thing.
    saved.forEach(function(r){{
      if (r[0] >= NCOL && r[0] < NCOL + COLS.length) addRule(r[0], r[1], r[2]);
    }});
  }}
  if (!rulesBox.children.length) addRule();
}}
document.getElementById('addRule').addEventListener('click', function(){{
  addRule(); apply();
}});

function fmt(v, name){{
  if (v === null || v === undefined) return '<span class="na">&mdash;</span>';
  const a = Math.abs(v);
  if (name === 'mktcap') return (v/1e9).toFixed(2) + 'B';
  if (INTEGERISH.has(name)) return v.toFixed(0);
  if (a >= 1e9) return (v/1e9).toFixed(1) + 'B';
  if (a >= 1e6) return (v/1e6).toFixed(1) + 'M';
  if (a >= 1000) return v.toLocaleString(undefined,{{maximumFractionDigits:0}});
  return v.toFixed(2);
}}

function tvLink(tk, ex){{
  const p = {{NYSE:'NYSE', NASDAQ:'NASDAQ', AMEX:'AMEX'}}[ex] || '';
  return 'https://www.tradingview.com/chart/?symbol=' + (p ? p + ':' + tk : tk);
}}

// Only link to a profile that EXISTS. Linking all 3,464 rows would 404 on
// almost every click, because profiles are built for flagged names and on
// request, not for the whole universe (that is ~90 MB of HTML nobody asked
// for). Under serve.py the server builds them on demand, so everything links.
const BUILT = new Set({built});
const LIVE = (location.protocol !== 'file:');

function rowHTML(r){{
  const tk = r[0];
  const cell = (LIVE || BUILT.has(tk))
    ? '<a href="../stock/' + tk + '.html">' + tk + '</a>'
    : '<span title="no profile page built yet — python stock_profile.py ' + tk
      + '">' + tk + '</span>';
  let h = '<tr><td class="tk">'
        + '<span class="star' + (WATCH.has(tk) ? ' on' : '') + '"'
        + ' data-tk="' + tk + '" role="button" tabindex="0"'
        + ' title="watchlist -- these names are verified against filings every'
        + ' night, not sampled">' + (WATCH.has(tk) ? '★' : '☆')
        + '</span>' + cell
        + (BADGE[tk] ? '<span class="insb" title="' + BADGE[tk] + '">insider</span>' : '')
        + evtags(tk)
        + '<a class="tv" href="' + tvLink(tk, r[3]) + '" target="_blank"'
        + ' rel="noopener" title="TradingView">&#9741;</a></td>'
        + '<td class="sec">' + (r[2] || '') + '</td>';
  for (let k = 0; k < VIS.length; k++){{
    const i = VIS[k];
    h += '<td>' + fmt(r[i + NCOL], COLS[i]) + '</td>';
  }}
  return h + '</tr>';
}}

function paint(reset){{
  if (reset){{ body.innerHTML = ''; painted = 0; scroller.scrollTop = 0; }}
  const slice = view.slice(painted, painted + PAGE);
  if (!slice.length) return;
  body.insertAdjacentHTML('beforeend', slice.map(rowHTML).join(''));
  painted += slice.length;
}}

scroller.addEventListener('scroll', function(){{
  if (scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 300)
    paint(false);
}});

function apply(){{
  computeVis();                      // a filter can bring its column into view
  const q = document.getElementById('q').value.trim().toUpperCase();
  const sec = document.getElementById('sector').value;
  const rules = readRules();
  const only = document.getElementById('watchOnly').checked;
  view = ROWS.filter(function(r){{
    if (only && !WATCH.has(r[0])) return false;
    if (q && r[0].indexOf(q) < 0 && (r[1]||'').toUpperCase().indexOf(q) < 0)
      return false;
    if (sec && r[2] !== sec) return false;
    // EVERY rule must pass -- conditions are ANDed, which is what makes this
    // a screener rather than a sorted table. One rule at a time could not
    // express "growing AND cheap AND not distressed".
    for (var k = 0; k < rules.length; k++){{
      const ru = rules[k];
      if (isNaN(ru.lo) && isNaN(ru.hi)) continue;
      const v = r[ru.ci];
      // A row with NO value is excluded by a numeric filter rather than
      // treated as 0 -- same rule as the sort.
      if (v === null || v === undefined) return false;
      if (!isNaN(ru.lo) && v < ru.lo) return false;
      if (!isNaN(ru.hi) && v > ru.hi) return false;
    }}
    return true;
  }});
  if (sortI !== null) doSort(sortI, sortAsc, false);
  const nf = rules.filter(function(x){{ return !isNaN(x.lo) || !isNaN(x.hi); }}).length;
  document.getElementById('count').textContent =
    view.length.toLocaleString() + ' of ' + ROWS.length.toLocaleString()
    + (nf ? '  (' + nf + ' filter' + (nf > 1 ? 's' : '') + ')' : '');
  paint(true);
}}

function doSort(i, asc, repaint){{
  view.sort(function(a, b){{
    const x = a[i], y = b[i];
    const xn = (x === null || x === undefined), yn = (y === null || y === undefined);
    // NULLS ALWAYS LAST, in both directions. This is the whole point.
    if (xn && yn) return 0;
    if (xn) return 1;
    if (yn) return -1;
    if (typeof x === 'string') return asc ? x.localeCompare(y) : y.localeCompare(x);
    return asc ? x - y : y - x;
  }});
  if (repaint) paint(true);
}}

document.getElementById('head').addEventListener('click', function(e){{
  const th = e.target.closest('th'); if (!th) return;
  const i = parseInt(th.getAttribute('data-i'), 10);
  sortAsc = (sortI === i) ? !sortAsc : false;
  sortI = i;
  markSort();
  computeVis();
  doSort(i, sortAsc, true);
}});

// The sorted header, and the strategy button that matches it (or None).
function markSort(){{
  document.querySelectorAll('#head th').forEach(function(x){{
    x.classList.toggle('sorted', sortI !== null && parseInt(x.getAttribute('data-i'), 10) === sortI);
  }});
  document.querySelectorAll('.rail button').forEach(function(b){{
    const si = parseInt(b.getAttribute('data-si'), 10);
    b.classList.toggle('on', sortI === null ? si < 0 : si === sortI);
  }});
}}

document.getElementById('watchOnly').addEventListener('change', apply);
['q','sector'].forEach(function(id){{
  const el = document.getElementById(id);
  el.addEventListener(el.tagName === 'SELECT' ? 'change' : 'input', apply);
}});
document.getElementById('reset').addEventListener('click', function(){{
  document.getElementById('q').value = '';
  document.getElementById('sector').value = '';
  rulesBox.innerHTML = ''; addRule(); saveRules();
  sortI = null; markSort(); apply();
}});


{views_js}

// ---- strategy rail ------------------------------------------------------
// A strategy is just a column, so "select a strategy" is "sort by that
// column, best first". No separate view, no second code path. data-si is the
// row index the sort reads; sortI is SET, so a later search or filter keeps
// the strategy's order (it reverted to unsorted before 2026-11-09).
Array.prototype.forEach.call(document.querySelectorAll('.rail button'),
  function(b){{
    b.addEventListener('click', function(){{
      const si = parseInt(b.getAttribute('data-si'), 10);
      sortI = si < 0 ? null : si;
      sortAsc = false;               // a strategy score is best-first
      markSort();
      apply();                       // applies the sort and shows its column
    }});
  }});

loadRules();
markSort();
apply();
</script>
</body></html>"""


def build(metrics: list[str] | None = None, verbose: bool = True,
          session: str | None = None) -> Path:
    asof = session or calendar_us.last_closed_session()
    metrics = metrics or DEFAULT_METRICS

    # EVERY metric a strategy names must be COLLECTED, even when it is not a
    # displayed column. `collect` only reads the metrics it is asked for, and a
    # strategy whose input is absent ranks nobody -- by design, since scoring on
    # a subset would silently answer a different question. That rule turned a
    # missing fetch into three strategies reading "0 of 3,506" rather than into
    # three plausible-looking but wrong rankings.
    try:
        import strategies as STRAT
        _need = sorted({m for st in STRAT.STRATEGIES for m in st.metrics})
    except Exception:                                            # noqa: BLE001
        _need = []
    wide, prov = collect(sorted(set(metrics) | set(_need)), asof)
    if wide.empty:
        raise RuntimeError("no score rows found -- run the score modules first")

    # STRATEGY COLUMNS. A strategy is a ranking model over metrics already in
    # this frame, so it becomes an ordinary column and every existing feature
    # -- sorting, filtering, the column chooser, the session picker -- works on
    # it without a line of new table code. That is the whole reason this is a
    # column and not a second page.
    #
    # Computed here rather than stored, because a strategy is a re-rank of data
    # already on disk: it costs milliseconds and cannot drift from what the
    # page is showing.
    strat_cov = {}
    try:
        import strategies as STRAT
        # `collect` returns a TICKER-INDEXED frame -- the pivot uses
        # index="ticker" and it is never reset -- so the engine reads it
        # directly and the assignment aligns on that index.
        for _s in STRAT.STRATEGIES:
            wide[_s.column] = STRAT.rank(wide, _s)
            _c = STRAT.coverage(wide, _s)
            _c.update(column=_s.column, doc=_s.doc, evidence=_s.evidence)
            strat_cov[_s.key] = _c
            if verbose:
                print(f"    strategy {_s.key:16s} ranked "
                      f"{_c['ranked']:,} of {_c['universe']:,}")
        metrics = [s.column for s in STRAT.STRATEGIES] + list(metrics)
    except Exception as exc:                                     # noqa: BLE001
        # A strategy failing must not cost the whole table. Say so rather than
        # rendering a page that silently has no strategy columns.
        print(f"    [explore] strategies unavailable: {exc!r}")

    cols, rows, dropped = _payload(wide, metrics)
    try:
        import insider_signals as SIG
        badges = SIG.badges(asof)
    except Exception:                                            # noqa: BLE001
        badges = {}
    try:
        import news_events as NE
        flags = NE.page_flags(asof)
    except Exception:                                            # noqa: BLE001
        flags = {}
    config.REPORTS_EXPLORE.mkdir(parents=True, exist_ok=True)
    out = OUT if session is None else \
        config.REPORTS_EXPLORE / f"{session}.html"
    out.write_text(render(cols, rows, prov, asof, available_sessions(),
                          dropped=dropped, strat_cov=strat_cov, badges=badges, flags=flags),
                   encoding="utf-8")
    if verbose:
        print(f"  explore: {out}  ({len(rows):,} tickers x {len(cols)} metrics, "
              f"{out.stat().st_size / 1e6:.1f} MB)")
    return out


def selftest() -> int:
    """The views' definitions, and the built page's view bar and strategy rail."""
    import re
    keys = [k for k, *_r in VIEWS]
    assert len(set(keys)) == len(keys) and "custom" not in keys and DEFAULT_VIEW in keys, keys
    try:
        import strategies as STRAT
        known = set(DEFAULT_METRICS) | {s.column for s in STRAT.STRATEGIES}
    except Exception:                                            # noqa: BLE001
        known = set(DEFAULT_METRICS)
    for k, _label, _short, _hint, cols in VIEWS:
        # a typo here would make a column silently absent from its view
        unknown = sorted(set(cols or []) - known)
        assert not unknown, f"view {k!r} names column(s) the table never has: {unknown}"
        assert cols is None or len(cols) == len(set(cols)), f"view {k!r} repeats a column"
    if OUT.exists():
        html = OUT.read_text(encoding="utf-8")
        assert html.count('data-view="') == len(VIEWS) + 1, "the view bar lost a button"
        si = [int(x) for x in re.findall(r'<button data-si="(-?\d+)"', html)]
        assert si and all(x == -1 or x >= NCOL for x in si), \
            f"a strategy button points below the metric columns: {si}"
    print(f"  [explore] {len(VIEWS)} views + Custom, every column real; "
          f"strategy buttons sort by their own columns")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    ap = argparse.ArgumentParser(description="Sortable/filterable stock table.")
    ap.add_argument("--metrics", help="comma-separated metric list")
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--session", metavar="YYYY-MM-DD")
    a = ap.parse_args()
    m = [x.strip() for x in a.metrics.split(",")] if a.metrics else None
    p = build(m, session=a.session)
    if a.open:
        webbrowser.open(p.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
