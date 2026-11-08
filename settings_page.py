#!/usr/bin/env python
"""`reports/settings.html` -- Settings: every choice a reader makes, on one page.

    python settings_page.py          build it (the `dashboard` step does, every run)

Asked 2026-11-09: "a config page that is professional that can be used to tweak settings/configs,
please be neat and elegant and consistent and bug free".

TWO KINDS OF SETTING, and every section says which it is:
- **This browser** -- the theme and Today's cards. Kept in the browser (`scr.prefs` in
  localStorage, read by the menu script on every page) and applied at once. Browsers keep pages
  opened as files apart from pages opened through the local server, so each has its own.
- **Project file** -- the stock page's sections and tabs (`data/_profile.json`) and the
  shortlist's ranking, size and filters (`shortlist_recipes.json`): what the nightly steps build
  from. Saving needs the local server (`python serve.py --open`): a page opened as a file cannot
  write files, so there these sections show their values read-only and say how to change them.

Every save is checked by the module that owns the file (`settings.apply_ui`,
`shortlist.apply_ui`): the same rules whichever page or script is the source, and nothing is
saved when any value is wrong. The page reuses the server's existing step runner to rebuild.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import config
import ui

OUT = config.REPORTS / "settings.html"

# Today's cards: the keys are today_page's `data-card` names.
TODAY_CARDS = (
    ("market", "Market", "SPY against its averages, VIX, breadth, the strongest and weakest sectors"),
    ("shortlist", "Shortlist", "the top names with their buy limit, target and stop"),
    ("week", "This week", "earnings and warning filings for the listed names"),
    ("watchlists", "Watchlists", "each saved list against the market"),
    ("narrative", "Narrative", "the weekly AI view of each sector"),
)
WIN_LABELS = {"1M": "1 month", "3M": "3 months", "6M": "6 months", "1Y": "1 year", "5Y": "5 years",
              "MAX": "All history"}
REL_LABELS = {21: "1 month", 63: "3 months", 126: "6 months", 252: "1 year"}


# ------------------------------------------------------------------------------ the data
def state() -> dict:
    """Everything the page draws, fresh from the files (also served as GET /api/settings)."""
    import settings as S
    import shortlist as SL
    return {
        "stock": S.ui_state(),
        "stock_defaults": S.ui_defaults(),
        "sections": [{"key": k, "label": label, "hint": hint} for k, (label, hint) in S.SECTIONS.items()],
        "tabs": [{"key": k, "label": label} for k, label, _s in S.TABS],
        "chart_windows": [{"value": w, "label": WIN_LABELS.get(w, w)} for w in S.CHART_WINDOW_CHOICES],
        "relative_windows": [{"value": w, "label": REL_LABELS.get(w, str(w))} for w in S.RELATIVE_WINDOW_CHOICES],
        "events_range": list(S.EVENTS_LIMIT_RANGE),
        "shortlist": SL.ui_state(),
        "shortlist_defaults": dict(SL.SETTINGS_DEFAULTS),
        "shortlist_meta": SL.ui_meta(),
        "today_cards": [{"key": k, "label": label, "hint": hint} for k, label, hint in TODAY_CARDS],
        "stock_pages": len(ui.built_profiles()),
    }


def _redraw_shortlist() -> str:
    """The new ranking and size reach the Shortlist page and Today at once (seconds). The filters
    decide which names qualify, which only a rebuild applies."""
    import calendar_us
    import shortlist as SL
    import today_page
    try:
        asof = today_page._latest_dir(SL.DIR, calendar_us.last_closed_session())
        if asof:
            SL.render(asof)
        today_page.build(verbose=False)
        return ("The Shortlist page and Today were redrawn. A change to the filters applies from the "
                "next build: tonight, or now with Rebuild.")
    except Exception as exc:                                     # noqa: BLE001
        return f"The pages could not be redrawn now ({type(exc).__name__}); tonight's run will."


def save(body) -> tuple[int, dict]:
    """A save from the page: {"section": "stock" | "shortlist", "values": {...}}."""
    if not isinstance(body, dict) or body.get("section") not in ("stock", "shortlist"):
        return 400, {"ok": False, "errors": ["section must be stock or shortlist"]}
    if body["section"] == "stock":
        import settings as S
        _st, errs = S.apply_ui(body.get("values"))
        note = ("Saved. Stock pages take this up when they are next built: tonight, or now with "
                "Rebuild.")
    else:
        import shortlist as SL
        _st, errs = SL.apply_ui(body.get("values"))
        note = ""
    if errs:
        return 400, {"ok": False, "errors": errs}
    if body["section"] == "shortlist":
        note = "Saved. " + _redraw_shortlist()
    try:
        build(verbose=False)            # this page's own copy, for when it is opened as a file
    except Exception:                                            # noqa: BLE001
        pass
    return 200, {"ok": True, "note": note, "state": state()}


# ------------------------------------------------------------------------------ the page
CSS = """
.set-intro{color:var(--muted);font-size:13px;margin:-6px 0 16px;max-width:90ch}
.set-layout{display:grid;grid-template-columns:190px minmax(0,1fr);gap:28px;align-items:start}
.set-toc{position:sticky;top:calc(var(--navh,44px) + 18px);display:flex;flex-direction:column;gap:2px}
.set-toc a{padding:7px 11px;border-radius:7px;color:var(--muted);font-weight:500;font-size:13px}
.set-toc a:hover{background:var(--grid);color:var(--ink);text-decoration:none}
.set-toc a.on{background:var(--grid);color:var(--accent);font-weight:600}
.set-main{display:flex;flex-direction:column;gap:18px;min-width:0;max-width:880px}
.set-sec{padding:18px 20px;scroll-margin-top:calc(var(--navh,44px) + 14px)}
.set-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.set-head h3{margin:0;font-size:15px}
.set-scope{font-size:11px;font-weight:600;padding:2px 9px;border-radius:20px;border:1px solid var(--line);
color:var(--muted);white-space:nowrap}
.set-scope.project{color:var(--accent);border-color:var(--accent)}
.set-desc{color:var(--muted);font-size:12.5px;margin:6px 0 10px;max-width:78ch;line-height:1.5}
.set-desc code{font-size:11.5px}
.set-row{display:grid;grid-template-columns:minmax(0,250px) minmax(0,1fr);gap:18px;align-items:center;
padding:12px 0;border-top:1px solid var(--line)}
.set-lbl b{display:block;font-weight:600;font-size:13px}
.set-lbl span{display:block;color:var(--muted);font-size:12px;margin-top:1px;line-height:1.4}
.set-ctl{display:flex;flex-wrap:wrap;gap:8px;align-items:center;min-width:0}
.set-ctl select,.set-ctl input[type=number]{font-size:13px;padding:6px 9px;max-width:100%}
.set-ctl input[type=number]{width:96px}
.set-ctl .toggle button{font-size:12.5px;padding:6px 14px}
.set-check{display:inline-flex;align-items:center;gap:9px;cursor:pointer;min-width:0}
.set-check input{width:16px;height:16px;margin:0;flex:none;accent-color:var(--accent);cursor:pointer}
.set-check b{font-weight:600}
.set-check .hint{display:block;color:var(--muted);font-size:12px;font-weight:400;line-height:1.35}
.set-evidence{flex-basis:100%;color:var(--muted);font-size:12px;line-height:1.5;max-width:68ch;margin:0}
.set-list{list-style:none;margin:2px 0 0;padding:0}
.set-list li{display:flex;align-items:center;flex-wrap:wrap;gap:8px 14px;padding:10px 0;border-top:1px solid var(--line)}
.set-list li>.set-check{flex:1 1 260px}
.set-list li.off .set-check b{color:var(--muted);font-weight:500}
.set-list select{font-size:12.5px;padding:5px 8px}
.set-move{display:inline-flex;gap:4px;flex:none}
.set-move button{padding:3px 9px;font-size:13px;line-height:1.3}
.set-move button:disabled{opacity:.3;cursor:default;border-color:var(--line);color:inherit}
.set-foot{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin-top:14px;padding-top:14px;
border-top:1px solid var(--line)}
.set-foot .grow{flex:1 1 200px;min-width:0}
.set-foot button{font-size:12.5px;padding:6px 13px}
.set-foot button:disabled{opacity:.45;cursor:default}
.set-status{font-size:12.5px;color:var(--muted)}
.set-status.ok{color:var(--pos)}
.set-status.err{color:var(--neg)}
.set-status.busy{color:var(--ink)}
.set-status.dirty{color:var(--warn);font-weight:600}
fieldset.set-fs{border:0;margin:0;padding:0;min-width:0}
fieldset.set-fs:disabled{opacity:.62}
.set-ro{display:none}
.set-ro.on{display:block;margin-top:12px}
#set-file{display:none}
#set-file.on{display:block}
@media (max-width:900px){.set-layout{grid-template-columns:minmax(0,1fr)}.set-toc{display:none}}
@media (max-width:640px){.set-row{grid-template-columns:minmax(0,1fr);gap:8px}.set-sec{padding:16px 14px}
  .set-list li>.set-check{flex-basis:190px}}
"""


def _took(step: str, pages: bool = False) -> str:
    """' (the last rebuild took N minutes for M pages)', MEASURED from the job table -- the page
    count is the one that run recorded, not today's. Empty when there is no record."""
    try:
        import orchestrator
        j = orchestrator.read_jobs()
        last = j[(j["step"] == step) & (j["status"].isin(["ok", "slow"]))].iloc[-1]
        mins = max(1, round(float(last["duration_s"]) / 60))
        what = f" for {int(last['rows'])} pages" if pages and last["rows"] == last["rows"] else ""
        return f" (the last rebuild took {mins} minute{'s' if mins > 1 else ''}{what})"
    except Exception:                                            # noqa: BLE001
        return ""


def _row(label: str, hint: str, control: str) -> str:
    return (f'<div class="set-row"><div class="set-lbl"><b>{label}</b><span>{hint}</span></div>'
            f'<div class="set-ctl">{control}</div></div>')


def _move(label: str) -> str:
    lab = ui.esc(label)
    return (f'<span class="set-move"><button type="button" data-move="-1" aria-label="Move {lab} up">&uarr;</button>'
            f'<button type="button" data-move="1" aria-label="Move {lab} down">&darr;</button></span>')


def _foot(sec: str, buttons: str) -> str:
    return (f'<div class="set-foot"><span class="set-status grow" id="st-{sec}" role="status" '
            f'aria-live="polite"></span>{buttons}</div>')


def _scope(project: bool) -> str:
    return ('<span class="set-scope project">Project file</span>' if project
            else '<span class="set-scope">This browser</span>')


def body(st: dict) -> str:
    tabs_opts = "".join(f'<option value="{t["key"]}">{ui.esc(t["label"])}</option>' for t in st["tabs"])
    sec_items = "".join(
        f'<li data-key="{s["key"]}"><label class="set-check"><input type="checkbox" data-f="on">'
        f'<span><b>{ui.esc(s["label"])}</b><span class="hint">{ui.esc(s["hint"])}</span></span></label>'
        f'<select data-f="tab" aria-label="Tab for {ui.esc(s["label"])}">{tabs_opts}</select>'
        f'{_move(s["label"])}</li>' for s in st["sections"])
    today_items = "".join(
        f'<li data-key="{c["key"]}"><label class="set-check"><input type="checkbox">'
        f'<span><b>{ui.esc(c["label"])}</b><span class="hint">{ui.esc(c["hint"])}</span></span></label>'
        f'{_move(c["label"])}</li>' for c in st["today_cards"])
    win_opts = "".join(f'<option value="{w["value"]}">{ui.esc(w["label"])}</option>' for w in st["chart_windows"])
    rel_opts = "".join(f'<option value="{w["value"]}">{ui.esc(w["label"])}</option>' for w in st["relative_windows"])
    rec_opts = "".join(f'<option value="{r["name"]}">{ui.esc(r["label"])}</option>'
                       for r in st["shortlist_meta"]["recipes"])
    lo, hi = st["events_range"]
    alo, ahi = st["shortlist_meta"]["above_range"]
    max_n = st["shortlist_meta"]["max_n"]
    pages = st["stock_pages"]
    ro = ('<div class="set-ro">Read-only here: this page was opened as a file. Start the local server '
          'to change it.</div>')

    appearance = (
        '<section class="card set-sec" id="appearance" aria-labelledby="h-appearance">'
        f'<div class="set-head"><h3 id="h-appearance">Appearance</h3>{_scope(False)}</div>'
        '<p class="set-desc">How every page looks in this browser.</p>'
        + _row("Theme", "Auto follows the light or dark setting of your system",
               '<div class="toggle" id="themeToggle" role="group" aria-label="Theme">'
               '<button type="button" data-v="auto">Auto</button><button type="button" data-v="light">Light</button>'
               '<button type="button" data-v="dark">Dark</button></div>')
        + _foot("appearance", "") + '</section>')

    today = (
        '<section class="card set-sec" id="today" aria-labelledby="h-today">'
        f'<div class="set-head"><h3 id="h-today">Today page</h3>{_scope(False)}</div>'
        '<p class="set-desc">The cards on the home page, in the order you want them. Hidden cards are '
        'still built every night; they are only kept out of sight.</p>'
        f'<ol class="set-list" id="todayList">{today_items}</ol>'
        + _foot("today", '<button type="button" id="todayReset">Restore defaults</button>') + '</section>')

    stock = (
        '<section class="card set-sec" id="stock" aria-labelledby="h-stock">'
        f'<div class="set-head"><h3 id="h-stock">Stock pages</h3>{_scope(True)}</div>'
        '<p class="set-desc">Which sections a stock page shows, in which tab and order. The name, price '
        'and sector always sit at the top. Saved to <code>data/_profile.json</code>; pages take it up when '
        f'they are next built: tonight, or now with Rebuild{_took("profiles", pages=True)}.</p>'
        '<fieldset class="set-fs" id="fs-stock">'
        + _row("Group sections into tabs", "Off gives one long page, every section in order",
               '<label class="set-check"><input type="checkbox" id="tabsOn"><span>Use tabs</span></label>')
        + _row("Open on", "The tab a stock page shows first", f'<select id="defaultTab">{tabs_opts}</select>')
        + '<div class="set-row" style="align-items:start"><div class="set-lbl"><b>Sections</b>'
          '<span>Tick to show; choose its tab; arrows set the order</span></div>'
          f'<div class="set-ctl" style="display:block"><ol class="set-list" id="secList">{sec_items}</ol></div></div>'
        + _row("Price chart opens at", "The other windows stay one click away", f'<select id="chartWin">{win_opts}</select>')
        + _row("Against the market and sector", "The window the comparison chart opens at",
               f'<select id="relWin">{rel_opts}</select>')
        + _row("Financials open on", "Both are on the page; this picks the one shown first",
               '<div class="toggle" id="finFreq" role="group" aria-label="Financials open on">'
               '<button type="button" data-v="A">Annual</button><button type="button" data-v="Q">Quarterly</button></div>')
        + _row("Growth rows", "A shaded year-on-year strip under each financial line",
               '<label class="set-check"><input type="checkbox" id="growthRows"><span>Show</span></label>')
        + _row("Filings listed", f"Recent SEC filings on the Insiders tab, {lo} to {hi}",
               f'<input type="number" id="eventsLimit" min="{lo}" max="{hi}" step="1" inputmode="numeric">')
        + '</fieldset>' + ro
        + _foot("stock", '<button type="button" id="stockDefaults">Restore defaults</button>'
                         '<button type="button" id="stockSave" class="primary">Save</button>'
                         '<button type="button" id="stockRebuild">Rebuild stock pages</button>')
        + '</section>')

    shortlist = (
        '<section class="card set-sec" id="shortlist" aria-labelledby="h-shortlist">'
        f'<div class="set-head"><h3 id="h-shortlist">Shortlist</h3>{_scope(True)}</div>'
        '<p class="set-desc">The ranking the Shortlist and Today show first, how many names, and which names '
        'qualify. Every ranking is still built and tracked each night. Saved to '
        '<code>shortlist_recipes.json</code>; the ranking and size apply at once, the filters from the next '
        'build. Research screens, not investment advice.</p>'
        '<fieldset class="set-fs" id="fs-shortlist">'
        + _row("Ranking shown first", "The evidence for each is under the choice",
               f'<select id="slDefault">{rec_opts}</select><p class="set-evidence" id="slEvidence"></p>')
        + _row("Names shown", f"1 to {max_n}; the forward record scores this many from each ranking",
               f'<input type="number" id="slN" min="1" max="{max_n}" step="1" inputmode="numeric">')
        + _row("Skip names with warning filings", "Unregistered share sales, restatements, ended agreements",
               '<label class="set-check"><input type="checkbox" id="slWarn"><span>Skip them</span></label>')
        + _row("At most this far above the support level",
               f"In percent, {alo:g} to {ahi:g}: every candidate is within {ahi:g}% of its level",
               f'<input type="number" id="slAbove" min="{alo:g}" max="{ahi:g}" step="0.5" inputmode="decimal">'
               '<span class="muted">%</span>')
        + '</fieldset>' + ro
        + _foot("shortlist", '<button type="button" id="slDefaults">Restore defaults</button>'
                             '<button type="button" id="slSave" class="primary">Save</button>'
                             '<button type="button" id="slRebuild">Rebuild the shortlist</button>')
        + '</section>')

    backup = (
        '<section class="card set-sec" id="backup" aria-labelledby="h-backup">'
        '<div class="set-head"><h3 id="h-backup">Backup</h3></div>'
        '<p class="set-desc">Save every setting on this page to a file, or bring this browser&rsquo;s choices '
        'back from one. Project settings in a file are loaded into their sections for you to review and '
        'save.</p>'
        + _foot("backup", '<button type="button" id="bkExport">Export settings</button>'
                          '<button type="button" id="bkImport">Import&hellip;</button>'
                          '<input type="file" id="bkFile" accept="application/json,.json" hidden>'
                          '<button type="button" id="bkReset">Reset this browser</button>')
        + '</section>')

    toc = ('<nav class="set-toc" aria-label="Settings sections">'
           '<a href="#appearance">Appearance</a><a href="#today">Today page</a><a href="#stock">Stock pages</a>'
           '<a href="#shortlist">Shortlist</a><a href="#backup">Backup</a></nav>')
    file_note = ('<div class="banner warn" id="set-file"><b>Opened as a file, so the project settings are '
                 'read-only.</b> Appearance and Today work here. To change the stock pages and the shortlist, '
                 'start the local server and open Settings from there:'
                 '<div class="cmdrow"><code>python serve.py --open</code>'
                 '<button type="button" class="copy" data-c="python serve.py --open">copy</button></div></div>')
    data = json.dumps(st, separators=(",", ":")).replace("<", "\\u003c")
    return (f'<p class="set-intro">Choices for how the pages look and what they show. Each section says '
            f'where it is kept.</p>{file_note}'
            f'<div class="set-layout">{toc}<div class="set-main">{appearance}{today}{stock}{shortlist}{backup}'
            f'</div></div><script type="application/json" id="set-state">{data}</script>')


JS = r"""
(function(){
  var S = JSON.parse(document.getElementById('set-state').textContent);
  var P = window.scrPrefs || {get: function(){ return {}; }, set: function(){ return false; }, applyTheme: function(){}};
  var LIVE = false, GUARD = {'X-Screener-Control': '1', 'Content-Type': 'application/json'};
  function $(s, r){ return (r || document).querySelector(s); }
  function $$(s, r){ return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
  function status(sec, text, cls){ var el = $('#st-' + sec); if (!el) return; el.textContent = text || ''; el.className = 'set-status grow' + (cls ? ' ' + cls : ''); }
  // Key order must not count as a change: {a,b} and {b,a} are the same settings.
  function canon(x){
    if (Array.isArray(x)) return '[' + x.map(canon).join(',') + ']';
    if (x && typeof x === 'object') return '{' + Object.keys(x).sort().map(function(k){ return JSON.stringify(k) + ':' + canon(x[k]); }).join(',') + '}';
    return JSON.stringify(x);
  }
  function hhmm(){ var d = new Date(); return ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2); }

  // ---------------------------------------------------------------- ordered lists
  function refreshMoves(list){
    var items = $$(':scope > li', list);
    items.forEach(function(li, i){
      li.querySelector('[data-move="-1"]').disabled = i === 0;
      li.querySelector('[data-move="1"]').disabled = i === items.length - 1;
      var cb = li.querySelector('input[type=checkbox]'); li.classList.toggle('off', !!cb && !cb.checked);
    });
  }
  // `keys` first, in their order; anything they do not name follows in the CANONICAL order --
  // not the order on screen, or "Restore defaults" would keep a reordered list as it was.
  function orderList(list, keys, canonical){
    var items = $$(':scope > li', list);
    function rank(li){
      var k = li.getAttribute('data-key'), i = keys.indexOf(k);
      return i >= 0 ? i : 1e3 + canonical.indexOf(k);
    }
    items.sort(function(a, b){ return rank(a) - rank(b); });
    items.forEach(function(li){ list.appendChild(li); });
  }
  function onMove(list, after){
    list.addEventListener('click', function(e){
      var b = e.target.closest('[data-move]'); if (!b || b.disabled) return;
      var li = b.closest('li'), d = +b.getAttribute('data-move');
      var sib = d < 0 ? li.previousElementSibling : li.nextElementSibling; if (!sib) return;
      if (d < 0) list.insertBefore(li, sib); else list.insertBefore(sib, li);
      refreshMoves(list);
      (b.disabled ? li.querySelector('[data-move="' + (-d) + '"]') : b).focus();
      after();
    });
  }
  function seg(el, v){ $$('button[data-v]', el).forEach(function(b){ var on = b.getAttribute('data-v') === String(v); b.classList.toggle('on', on); b.setAttribute('aria-pressed', on ? 'true' : 'false'); }); }
  function segVal(el){ var b = $('button.on[data-v]', el); return b ? b.getAttribute('data-v') : null; }

  // ---------------------------------------------------------------- Appearance (this browser)
  var themeEl = $('#themeToggle');
  function paintTheme(){ seg(themeEl, P.get().theme || 'auto'); }
  themeEl.addEventListener('click', function(e){
    var b = e.target.closest('button[data-v]'); if (!b) return;
    var p = P.get(), v = b.getAttribute('data-v');
    if (v === 'auto') delete p.theme; else p.theme = v;
    var ok = P.set(p); P.applyTheme(p.theme); paintTheme();
    status('appearance', ok ? 'Applied to every page in this browser.' : 'This browser does not keep preferences, so it applies to this page only.', ok ? 'ok' : 'err');
  });

  // ---------------------------------------------------------------- Today page (this browser)
  var todayList = $('#todayList'), CARD_KEYS = S.today_cards.map(function(c){ return c.key; });
  function paintToday(){
    var t = P.get().today || {}, order = Array.isArray(t.order) ? t.order : [], hidden = Array.isArray(t.hidden) ? t.hidden : [];
    orderList(todayList, order, CARD_KEYS);
    $$(':scope > li', todayList).forEach(function(li){ li.querySelector('input').checked = hidden.indexOf(li.getAttribute('data-key')) < 0; });
    refreshMoves(todayList);
  }
  function saveToday(){
    var items = $$(':scope > li', todayList);
    var shown = items.filter(function(li){ return li.querySelector('input').checked; });
    if (!shown.length) { paintToday(); status('today', 'Today needs at least one card.', 'err'); return; }
    var p = P.get();
    p.today = {order: items.map(function(li){ return li.getAttribute('data-key'); }),
               hidden: items.filter(function(li){ return !li.querySelector('input').checked; }).map(function(li){ return li.getAttribute('data-key'); })};
    var ok = P.set(p); refreshMoves(todayList);
    status('today', ok ? 'Saved. Today shows this the next time it opens.' : 'This browser does not keep preferences.', ok ? 'ok' : 'err');
  }
  todayList.addEventListener('change', saveToday);
  onMove(todayList, saveToday);
  $('#todayReset').addEventListener('click', function(){
    var p = P.get(); delete p.today; P.set(p); paintToday(); status('today', 'Restored: every card, in the original order.', 'ok');
  });

  // ---------------------------------------------------------------- Stock pages (project file)
  var secList = $('#secList'), saved = {stock: S.stock, shortlist: S.shortlist};
  function readStock(){
    var v = {order: [], enabled: {}, tab: {}};
    $$(':scope > li', secList).forEach(function(li){
      var k = li.getAttribute('data-key');
      v.order.push(k); v.enabled[k] = li.querySelector('[data-f=on]').checked; v.tab[k] = li.querySelector('[data-f=tab]').value;
    });
    v.tabs_on = $('#tabsOn').checked; v.default_tab = $('#defaultTab').value;
    v.chart_window = $('#chartWin').value; v.relative_window = parseInt($('#relWin').value, 10);
    v.fin_freq = segVal($('#finFreq')); v.growth_rows = $('#growthRows').checked;
    v.events_limit = Number($('#eventsLimit').value);
    return v;
  }
  var SECTION_KEYS = S.sections.map(function(s){ return s.key; });
  function paintStock(v){
    orderList(secList, Array.isArray(v.order) ? v.order : [], SECTION_KEYS);
    $$(':scope > li', secList).forEach(function(li){
      var k = li.getAttribute('data-key');
      if (v.enabled && k in v.enabled) li.querySelector('[data-f=on]').checked = !!v.enabled[k];
      if (v.tab && v.tab[k]) li.querySelector('[data-f=tab]').value = v.tab[k];
    });
    $('#tabsOn').checked = !!v.tabs_on; $('#defaultTab').value = v.default_tab;
    $('#chartWin').value = v.chart_window; $('#relWin').value = String(v.relative_window);
    seg($('#finFreq'), v.fin_freq); $('#growthRows').checked = !!v.growth_rows;
    $('#eventsLimit').value = v.events_limit;
    refreshMoves(secList); syncTabControls();
  }
  function syncTabControls(){
    var on = $('#tabsOn').checked;
    $('#defaultTab').disabled = !on || !LIVE;
    $$('[data-f=tab]', secList).forEach(function(s){ s.disabled = !on || !LIVE; });
  }
  function stockProblem(v){
    var lo = S.events_range[0], hi = S.events_range[1];
    if (!Number.isInteger(v.events_limit) || v.events_limit < lo || v.events_limit > hi) return 'Filings listed must be a whole number from ' + lo + ' to ' + hi + '.';
    var any = Object.keys(v.enabled).some(function(k){ return v.enabled[k]; });
    if (!any) return 'Tick at least one section.';
    return '';
  }

  // ---------------------------------------------------------------- Shortlist (project file)
  function readShortlist(){
    return {"default": $('#slDefault').value, n: Number($('#slN').value),
            exclude_warnings: $('#slWarn').checked, max_above_level_pct: Number($('#slAbove').value)};
  }
  function paintShortlist(v){
    $('#slDefault').value = v["default"]; $('#slN').value = v.n; $('#slWarn').checked = !!v.exclude_warnings;
    $('#slAbove').value = v.max_above_level_pct; paintEvidence();
  }
  function paintEvidence(){
    var r = S.shortlist_meta.recipes.filter(function(x){ return x.name === $('#slDefault').value; })[0];
    $('#slEvidence').textContent = r ? r.evidence : '';
  }
  function shortlistProblem(v){
    var m = S.shortlist_meta, lo = m.above_range[0], hi = m.above_range[1];
    if (!Number.isInteger(v.n) || v.n < 1 || v.n > m.max_n) return 'Names shown must be a whole number from 1 to ' + m.max_n + '.';
    if (!isFinite(v.max_above_level_pct) || v.max_above_level_pct < lo || v.max_above_level_pct > hi) return 'The distance above the level must be from ' + lo + ' to ' + hi + '%.';
    return '';
  }

  // ---------------------------------------------------------------- saving and rebuilding
  var SEC = {
    stock: {read: readStock, paint: paintStock, problem: stockProblem, save: '#stockSave', rebuild: '#stockRebuild', step: 'profiles', what: 'stock pages'},
    shortlist: {read: readShortlist, paint: paintShortlist, problem: shortlistProblem, save: '#slSave', rebuild: '#slRebuild', step: 'shortlist', what: 'the shortlist'}
  };
  var busy = {stock: false, shortlist: false};
  function dirty(sec){ return canon(SEC[sec].read()) !== canon(saved[sec]); }
  function refresh(sec){
    var d = dirty(sec), prob = SEC[sec].problem(SEC[sec].read());
    $(SEC[sec].save).disabled = !LIVE || busy[sec] || !d || !!prob;
    $(SEC[sec].rebuild).disabled = !LIVE || busy[sec];
    $(SEC[sec].rebuild).textContent = d ? 'Save and rebuild' : (sec === 'stock' ? 'Rebuild stock pages' : 'Rebuild the shortlist');
    if (busy[sec]) return;
    if (prob && d) status(sec, prob, 'err');
    else if (d) status(sec, 'Unsaved changes.', 'dirty');
    else if ($('#st-' + sec).classList.contains('dirty') || $('#st-' + sec).classList.contains('err')) status(sec, '');
  }
  function post(url, body){
    return fetch(url, {method: 'POST', headers: GUARD, body: body ? JSON.stringify(body) : '{}'})
      .then(function(r){ return r.json().then(function(j){ return {ok: r.ok, j: j}; }, function(){ return {ok: false, j: {errors: ['the server answered ' + r.status]}}; }); });
  }
  function save(sec, then){
    var v = SEC[sec].read(), prob = SEC[sec].problem(v);
    if (prob) { status(sec, prob, 'err'); return; }
    busy[sec] = true; refresh(sec); status(sec, 'Saving…', 'busy');
    post('/api/settings', {section: sec, values: v}).then(function(res){
      busy[sec] = false;
      if (!res.ok || !res.j.ok) { status(sec, 'Not saved: ' + ((res.j.errors || [res.j.error || 'unknown error']).join('; ')), 'err'); refresh(sec); return; }
      S = res.j.state; saved = {stock: S.stock, shortlist: S.shortlist};
      SEC[sec].paint(saved[sec]); status(sec, res.j.note + ' (' + hhmm() + ')', 'ok'); refresh(sec);
      if (then) then();
    }, function(){ busy[sec] = false; status(sec, 'The local server did not answer. Is it still running?', 'err'); refresh(sec); });
  }
  function rebuild(sec){
    var step = SEC[sec].step;
    busy[sec] = true; refresh(sec); status(sec, 'Starting…', 'busy');
    post('/api/run/' + step).then(function(res){
      if (!res.ok || !res.j.ok) { busy[sec] = false; refresh(sec); status(sec, 'Not started: ' + (res.j.error || 'the server refused'), 'err'); return; }
      watch(sec, step, Date.now());
    }, function(){ busy[sec] = false; refresh(sec); status(sec, 'The local server did not answer. Is it still running?', 'err'); });
  }
  function watch(sec, step, t0){
    var misses = 0;
    var timer = setInterval(function(){
      fetch('/api/status', {cache: 'no-store'}).then(function(r){ return r.json(); }).then(function(d){
        misses = 0;
        var mins = Math.floor((Date.now() - t0) / 60000), secs = Math.floor((Date.now() - t0) / 1000) % 60;
        if (d.running && d.running.step === step) { status(sec, 'Rebuilding ' + SEC[sec].what + '… ' + mins + ':' + ('0' + secs).slice(-2), 'busy'); return; }
        clearInterval(timer);
        var rows = (d.jobs || []).filter(function(j){ return j.step === step; }), last = rows[rows.length - 1];
        var good = last && (last.status === 'ok' || last.status === 'slow');
        busy[sec] = false; refresh(sec);
        status(sec, good ? 'Rebuilt (' + hhmm() + '): ' + (last.detail || 'done') + '.'
                         : 'The rebuild ended ' + (last ? last.status + ': ' + (last.error || last.detail || '') : 'without a record') + '. The Status page has the details.', good ? 'ok' : 'err');
        if (good && sec === 'shortlist') post('/api/run/dashboard');     // Today shows the shortlist's top names
      }, function(){ if (++misses >= 5) { clearInterval(timer); busy[sec] = false; refresh(sec); status(sec, 'Lost touch with the local server; the rebuild may still be running. The Status page shows it.', 'err'); } });
    }, 3000);
  }
  Object.keys(SEC).forEach(function(sec){
    var root = $('#' + sec);
    root.addEventListener('change', function(){ refresh(sec); });
    root.addEventListener('input', function(){ refresh(sec); });
    $(SEC[sec].save).addEventListener('click', function(){ save(sec); });
    $(SEC[sec].rebuild).addEventListener('click', function(){ if (dirty(sec)) save(sec, function(){ rebuild(sec); }); else rebuild(sec); });
  });
  onMove(secList, function(){ refresh('stock'); });
  secList.addEventListener('change', function(){ refreshMoves(secList); });
  $('#tabsOn').addEventListener('change', syncTabControls);
  $('#finFreq').addEventListener('click', function(e){ var b = e.target.closest('button[data-v]'); if (!b || !LIVE) return; seg($('#finFreq'), b.getAttribute('data-v')); refresh('stock'); });
  $('#slDefault').addEventListener('change', paintEvidence);
  $('#stockDefaults').addEventListener('click', function(){ paintStock(S.stock_defaults); refresh('stock'); status('stock', dirty('stock') ? 'Defaults restored in the form: Save to keep them.' : 'These are the defaults.', dirty('stock') ? 'dirty' : ''); });
  $('#slDefaults').addEventListener('click', function(){ paintShortlist(S.shortlist_defaults); refresh('shortlist'); status('shortlist', dirty('shortlist') ? 'Defaults restored in the form: Save to keep them.' : 'These are the defaults.', dirty('shortlist') ? 'dirty' : ''); });
  window.addEventListener('beforeunload', function(e){ if (LIVE && (dirty('stock') || dirty('shortlist'))) { e.preventDefault(); e.returnValue = ''; } });

  // ---------------------------------------------------------------- Backup
  function sanitizePrefs(p){
    var out = {};
    if (p && (p.theme === 'light' || p.theme === 'dark')) out.theme = p.theme;
    if (p && p.today && typeof p.today === 'object') {
      var keep = function(a){ return Array.isArray(a) ? a.filter(function(k){ return CARD_KEYS.indexOf(k) >= 0; }) : []; };
      var t = {order: keep(p.today.order), hidden: keep(p.today.hidden)};
      if (t.hidden.length >= CARD_KEYS.length) t.hidden = [];
      out.today = t;
    }
    return out;
  }
  $('#bkExport').addEventListener('click', function(){
    var doc = {exported: new Date().toISOString(), browser: sanitizePrefs(P.get()), stock: saved.stock, shortlist: saved.shortlist};
    var a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([JSON.stringify(doc, null, 1)], {type: 'application/json'}));
    a.download = 'screener-settings-' + doc.exported.slice(0, 10) + '.json';
    document.body.appendChild(a); a.click(); setTimeout(function(){ URL.revokeObjectURL(a.href); a.remove(); }, 0);
    status('backup', 'Exported ' + a.download + '.', 'ok');
  });
  $('#bkImport').addEventListener('click', function(){ $('#bkFile').value = ''; $('#bkFile').click(); });
  $('#bkFile').addEventListener('change', function(){
    var f = this.files && this.files[0]; if (!f) return;
    var rd = new FileReader();
    rd.onload = function(){
      var doc; try { doc = JSON.parse(rd.result); } catch (e) { status('backup', 'Not a settings file: it is not valid JSON.', 'err'); return; }
      if (!doc || typeof doc !== 'object' || !('browser' in doc || 'stock' in doc || 'shortlist' in doc)) { status('backup', 'Not a settings file from this page.', 'err'); return; }
      var done = [];
      if (doc.browser) { P.set(sanitizePrefs(doc.browser)); P.applyTheme(P.get().theme); paintTheme(); paintToday(); done.push('this browser’s choices applied'); }
      if (LIVE && doc.stock && typeof doc.stock === 'object') { paintStock(Object.assign({}, saved.stock, doc.stock)); refresh('stock'); done.push('stock pages loaded: review and Save'); }
      if (LIVE && doc.shortlist && typeof doc.shortlist === 'object') { paintShortlist(Object.assign({}, saved.shortlist, doc.shortlist)); refresh('shortlist'); done.push('shortlist loaded: review and Save'); }
      if (!LIVE && (doc.stock || doc.shortlist)) done.push('project settings need the local server');
      status('backup', done.length ? 'Imported: ' + done.join('; ') + '.' : 'Nothing to import.', done.length ? 'ok' : 'err');
    };
    rd.onerror = function(){ status('backup', 'The file could not be read.', 'err'); };
    rd.readAsText(f);
  });
  $('#bkReset').addEventListener('click', function(){
    P.set({}); P.applyTheme(null); paintTheme(); paintToday();
    status('backup', 'This browser is back to the defaults: Auto theme, every Today card.', 'ok');
  });
  $$('button.copy[data-c]').forEach(function(b){ b.addEventListener('click', function(){
    var t = b.getAttribute('data-c'), done = function(){ b.textContent = 'copied'; setTimeout(function(){ b.textContent = 'copy'; }, 1400); };
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(t).then(done, function(){});
    else { var ta = document.createElement('textarea'); ta.value = t; document.body.appendChild(ta); ta.select(); try { document.execCommand('copy'); done(); } catch (e) {} ta.remove(); }
  }); });

  // ---------------------------------------------------------------- the section in view
  var toc = $$('.set-toc a');
  function mark(id){ toc.forEach(function(a){ a.classList.toggle('on', a.getAttribute('href') === '#' + id); }); }
  if ('IntersectionObserver' in window && toc.length) {
    var io = new IntersectionObserver(function(es){
      if (window.scrollY < 40) { mark('appearance'); return; }        // at the top, the first section
      es.forEach(function(en){ if (en.isIntersecting) mark(en.target.id); });
    }, {rootMargin: '-30% 0px -60% 0px'});
    $$('.set-sec').forEach(function(s){ io.observe(s); });
    window.addEventListener('scroll', function(){ if (window.scrollY < 40) mark('appearance'); }, {passive: true});
    mark('appearance');
  }

  // ---------------------------------------------------------------- start: file or live
  // `quiet`: controls locked while the server is asked, but no read-only notes flashing up first.
  function setLive(on, quiet){
    LIVE = on;
    $('#fs-stock').disabled = !on; $('#fs-shortlist').disabled = !on;
    $$('.set-ro').forEach(function(x){ x.classList.toggle('on', !on && !quiet); });
    $('#set-file').classList.toggle('on', !on && !quiet);
    syncTabControls(); refresh('stock'); refresh('shortlist');
  }
  paintTheme(); paintToday(); paintStock(saved.stock); paintShortlist(saved.shortlist); setLive(false, true);
  // Opened as a file there is no server to ask, and asking would only log an error.
  if (location.protocol === 'file:') { setLive(false); return; }
  fetch('/api/settings', {cache: 'no-store'}).then(function(r){ if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(function(d){
      if (!d || !d.stock || !d.shortlist) throw new Error('no settings');
      S = d; saved = {stock: d.stock, shortlist: d.shortlist};
      paintStock(saved.stock); paintShortlist(saved.shortlist); setLive(true);
      // A rebuild already running (started before a reload, or from another tab): show it.
      return fetch('/api/status', {cache: 'no-store'}).then(function(r){ return r.json(); }).then(function(st){
        var cur = st && st.running && st.running.step;
        Object.keys(SEC).forEach(function(sec){
          if (cur && cur === SEC[sec].step) {
            busy[sec] = true; refresh(sec); status(sec, 'Rebuilding ' + SEC[sec].what + '…', 'busy');
            watch(sec, cur, st.running.started ? Date.parse(st.running.started) || Date.now() : Date.now());
          }
        });
      }, function(){});
    }).catch(function(){ setLive(false); });
})();
"""


def build(verbose: bool = True) -> Path:
    st = state()
    html = ui.page("Settings", body(st), active="settings", depth=0,
                   head=f"<style>{CSS}</style>", script=JS)
    OUT.write_text(html, encoding="utf-8")
    if verbose:
        print(f"  settings: {OUT}")
    return OUT


def selftest() -> int:
    """Structure and refusals only: every save tried here is invalid, so no file is written."""
    import re

    import today_page
    st = state()
    keys = [c["key"] for c in st["today_cards"]]
    src = Path(today_page.__file__).read_text(encoding="utf-8")
    order = re.search(r'for k in \("market", "shortlist", "week", "watchlists", "narrative"\)', src)
    assert order and keys == ["market", "shortlist", "week", "watchlists", "narrative"], \
        "Settings' Today cards no longer match today_page's cards"
    assert today_page.HALF <= set(keys)
    for bad in ("x", None, [], {"section": "nope"}, {"section": "stock"}, {"section": "stock", "values": {}},
                {"section": "shortlist", "values": {"default": "nope"}},
                {"section": "shortlist", "values": {**st["shortlist"], "n": 0}},
                {"section": "stock", "values": {**st["stock"], "default_tab": "x"}}):
        code, p = save(bad)
        assert code == 400 and not p["ok"] and p["errors"], (bad, code, p)
    html = body(st)
    blob = re.search(r'<script type="application/json" id="set-state">(.*?)</script>', html, re.S).group(1)
    assert json.loads(blob)["stock"] == st["stock"], "the page's embedded state does not parse back"
    for el in ("themeToggle", "todayList", "secList", "tabsOn", "defaultTab", "chartWin", "relWin", "finFreq",
               "growthRows", "eventsLimit", "slDefault", "slN", "slWarn", "slAbove", "stockSave", "slSave",
               "stockRebuild", "slRebuild", "bkExport", "bkImport", "bkFile", "bkReset", "set-file"):
        assert f'id="{el}"' in html, f"the page lost #{el}, which its script needs"
    print(f"  [settings_page] {len(st['sections'])} sections, {len(st['tabs'])} tabs, "
          f"{len(st['shortlist_meta']['recipes'])} rankings; 9 malformed saves refused, nothing written")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    build()
    return 0


if __name__ == "__main__":
    sys.exit(main())
