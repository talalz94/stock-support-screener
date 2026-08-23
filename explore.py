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
]

# Metrics where LOW is conventionally better -- used only to colour the cell,
# never to reorder anything. Sorting is always literal.
LOWER_BETTER = {"pe", "pb", "ev_ebitda", "ps_ratio", "net_issuance",
                "m_score", "days_since_filing"}
INTEGERISH = {"news_count_30d", "f_score", "bars_used", "sent_age"}


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

    return wide.sort_index(), prov


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
.rail button span{color:var(--muted);font-size:11px}
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
        out.append(
            f'<button data-si="{cols.index(cov["column"])}" '
            f'title="{ui.esc(cov["doc"])}"><b>{ui.esc(cov["title"])}</b>'
            f'<span>{cov["ranked"]:,} of {cov["universe"]:,} ranked</span>'
            f'</button>')
    out.append("</div>")
    return "".join(out)


def render(cols: list[str], rows: list, prov: dict, asof: str,
           sessions: list[str] | None = None,
           dropped: list[str] | None = None,
           strat_cov: dict | None = None) -> str:
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
    src = " &middot; ".join(f"{k} @ {v}" for k, v in prov.items() if v) or "none"
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
                f'title="{ui.esc(metrics_doc.tooltip(c))}">'
                f'{ui.esc(ui.label(c))}</th>'
                for i, c in enumerate(cols)))

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Explore &middot; all stocks</title><style>{ui.CSS}{CSS}</style></head>
<body>{navbar}<div class="wrap">
<h1>Explore</h1>
<div class="sub">{len(rows):,} tickers &middot; session <b>{asof}</b> &middot;
scores from {src} </div>

<div class="filters">
  <input id="q" placeholder="ticker or name..." autocomplete="off">
  <select id="sector"><option value="">all sectors</option>{sec_opts}</select>
  <span id="rules"></span>
  <button id="addRule" title="add another condition, ANDed with the rest">+ filter</button>
  <label class="watchtog" title="show only starred names">
    <input type="checkbox" id="watchOnly"> &#9733; only
    <span id="watchN" class="watchn"></span></label>
  <button id="reset">reset</button>
  <span class="chooser" id="chooser">
    <button id="colsBtn">columns &#9662;</button>
    <div class="pop">
      <div class="acts">
        <button data-act="all">all</button>
        <button data-act="valid" title="only metrics the study found significant at some horizon">validated</button>
        <button data-act="none">none</button>
      </div>
      <div id="colList"></div>
    </div>
  </span>
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
<br>Rows render on demand, so sorting stays instant across the full universe.
</div>
</div>
<script>
const DATA = {data};
const LOWER = new Set({lower});
const INTEGERISH = new Set({integerish});
const COLS = DATA.cols, ROWS = DATA.rows;
const NCOL = 4;                      // ticker, name, sector, exchange
let view = ROWS.slice(), sortI = null, sortAsc = false, painted = 0;
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
    o.value = i + NCOL; o.textContent = c;
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
        + '<a class="tv" href="' + tvLink(tk, r[3]) + '" target="_blank"'
        + ' rel="noopener" title="TradingView">&#9741;</a></td>'
        + '<td class="sec">' + (r[2] || '') + '</td>';
  for (let i = 0; i < COLS.length; i++){{
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
  document.querySelectorAll('th').forEach(function(x){{ x.classList.remove('sorted'); }});
  th.classList.add('sorted');
  doSort(i, sortAsc, true);
}});

document.getElementById('watchOnly').addEventListener('change', apply);
['q','sector'].forEach(function(id){{
  const el = document.getElementById(id);
  el.addEventListener(el.tagName === 'SELECT' ? 'change' : 'input', apply);
}});
document.getElementById('reset').addEventListener('click', function(){{
  document.getElementById('q').value = '';
  document.getElementById('sector').value = '';
  rulesBox.innerHTML = ''; addRule(); saveRules();
  sortI = null; apply();
}});


// ---- column chooser -------------------------------------------------------
// Choice persists in localStorage, so a nightly rebuild does not silently undo
// what you picked. "validated" uses the study, not a hand-kept list.
var TH = [].slice.call(document.querySelectorAll('th[data-m]'));
var KEY = 'explore.cols.v1';
var hidden = {{}};
try {{ hidden = JSON.parse(localStorage.getItem(KEY) || '{{}}'); }} catch(e){{}}

function applyCols(){{
  TH.forEach(function(th){{
    var m = th.getAttribute('data-m');
    var off = !!hidden[m];
    var i = [].indexOf.call(th.parentNode.children, th);
    th.style.display = off ? 'none' : '';
    [].forEach.call(document.querySelectorAll('#body tr'), function(r){{
      if (r.cells[i]) r.cells[i].style.display = off ? 'none' : '';
    }});
  }});
  try {{ localStorage.setItem(KEY, JSON.stringify(hidden)); }} catch(e){{}}
}}

var list = document.getElementById('colList');
list.innerHTML = TH.map(function(th){{
  var m = th.getAttribute('data-m');
  return '<label><input type="checkbox" data-c="' + m + '"'
    + (hidden[m] ? '' : ' checked') + '> ' + th.textContent
    + (th.getAttribute('data-v') === '1'
        ? ' <span class="pill ok" style="font-size:9px">validated</span>' : '')
    + '</label>';
}}).join('');

document.getElementById('colsBtn').addEventListener('click', function(e){{
  e.stopPropagation();
  document.getElementById('chooser').classList.toggle('open');
}});
document.addEventListener('click', function(e){{
  if (!e.target.closest('#chooser'))
    document.getElementById('chooser').classList.remove('open');
}});
list.addEventListener('change', function(e){{
  var c = e.target.getAttribute('data-c'); if (!c) return;
  hidden[c] = !e.target.checked; applyCols();
}});
document.querySelector('#chooser .acts').addEventListener('click', function(e){{
  var act = e.target.getAttribute('data-act'); if (!act) return;
  TH.forEach(function(th){{
    var m = th.getAttribute('data-m');
    hidden[m] = (act === 'none') ? true
              : (act === 'valid') ? (th.getAttribute('data-v') !== '1')
              : false;
  }});
  [].forEach.call(list.querySelectorAll('input'), function(i){{
    i.checked = !hidden[i.getAttribute('data-c')];
  }});
  applyCols();
}});

// Re-apply after every repaint, since rows render on demand.
var _paint = paint;
paint = function(reset){{ _paint(reset); applyCols(); }};

// ---- strategy rail ------------------------------------------------------
// A strategy is just a column, so "select a strategy" is "sort by that
// column, best first". No separate view, no second code path.
Array.prototype.forEach.call(document.querySelectorAll('.rail button'),
  function(b){{
    b.addEventListener('click', function(){{
      Array.prototype.forEach.call(document.querySelectorAll('.rail button'),
        function(o){{ o.classList.remove('on'); }});
      b.classList.add('on');
      const si = parseInt(b.getAttribute('data-si'), 10);
      if (si < 0){{ sortI = null; apply(); return; }}
      Array.prototype.forEach.call(document.querySelectorAll('#head th'),
        function(th){{ th.classList.remove('sorted'); }});
      const th = document.querySelectorAll('#head th')[si];
      if (th) th.classList.add('sorted');
      sortAsc = false;               // a strategy score is best-first
      doSort(si, false, false);
      paint(true);
    }});
  }});

loadRules();
apply();
applyCols();
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
            _c.update(column=_s.column, doc=_s.doc)
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
    config.REPORTS_EXPLORE.mkdir(parents=True, exist_ok=True)
    out = OUT if session is None else \
        config.REPORTS_EXPLORE / f"{session}.html"
    out.write_text(render(cols, rows, prov, asof, available_sessions(),
                          dropped=dropped, strat_cov=strat_cov),
                   encoding="utf-8")
    if verbose:
        print(f"  explore: {out}  ({len(rows):,} tickers x {len(cols)} metrics, "
              f"{out.stat().st_size / 1e6:.1f} MB)")
    return out


def main() -> int:
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
