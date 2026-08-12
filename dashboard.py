"""
The master status page: `reports/index.html`.

    python dashboard.py            rebuild the hub
    python dashboard.py --open     rebuild and open it in a browser
    python dashboard.py --print    write nothing, print the status to stdout

What it is: one page that answers "did the job run, what did each step do, and
what broke". It links the three analysis dashboards (bounce, sentiment,
fundamental) and is what a browser lands on when pointed at `reports/`.

THREE CONSTRAINTS, each deliberate:

1. **It reads `data/_jobs.parquet` and file sizes. Nothing else.** No step is
   invoked, no store is scanned, no network call is made. Opening this page must
   never cost anything or change anything -- a status page that triggers work is
   a status page you become afraid to refresh.

2. **"Run now" is a command to copy, not a button that runs.** A static file
   cannot spawn a process, and the alternative -- a resident local server -- would
   throw away the no-daemon design that makes this project cost nothing when idle.
   So each step renders `python orchestrator.py --step <name>` with a copy button.
   The button copies; it does not execute.

3. **No framework, no external request.** Inline CSS, ~20 lines of vanilla JS for
   copy-to-clipboard and filtering. The page opens over `file://`, where
   `navigator.clipboard` is not available (not a secure context), so the copy
   path falls back to `document.execCommand`. Everything degrades to a plain
   selectable `<code>` if JS is off entirely.

Row budget: the step table is one row per registry step (12). Run history is
capped at HISTORY_RUNS most recent runs, so the page cannot grow without bound
as the job table accumulates a year of rows.
"""

from __future__ import annotations

import argparse
import html as _html
import os
import sys
import webbrowser
from datetime import date, datetime
from pathlib import Path

import config                                                    # noqa: E402

config.safe_console()

import calendar_us                                               # noqa: E402
import orchestrator                                              # noqa: E402
import ui                                                        # noqa: E402

HISTORY_RUNS = 30          # most recent runs shown; keeps the page bounded
HUB_FILE = config.REPORTS / "index.html"

# The three analysis dashboards this hub links. `label, file, what it is`.
DASHBOARDS = [
    ("Explore", "explore/latest.html",
     "Every stock, every score &mdash; sort and filter the whole universe",
     None, None),
    ("Support Bounce", "bounce/latest.html",
     "Parabolic run &rarr; full retrace &rarr; base holds &rarr; bounce",
     "bounce/index.html", "all sessions"),
    ("Sentiment", "sentiment/latest.html",
     "News-driven score per name, with the macro regime", None, None),
    ("Fundamental", "fundamental/latest.html",
     "47 metrics over four pillars, from the SEC fact store", None, None),
    ("Stock profiles", "stock/index.html",
     "Per-stock sheet: score radar, financials, QoQ/YoY trends", None, None),
]

# Stores summarised on the page. `label, path, glob`.
STORES = [
    ("bars 1d", config.BARS / "1d", "*.parquet"),
    ("bars 1h", config.BARS / "1h", "*.parquet"),
    ("bars ETF", config.BARS_ETF, "*.parquet"),
    ("news", config.NEWS, "*.parquet"),
    ("sentiment cache", config.SENTI, "*.parquet"),
    ("scores", config.SCORES, "*.parquet"),
    ("fundamentals", config.FUNDAMENTALS, "*.parquet"),
    ("flags", config.FLAGS, "*.parquet"),
    ("rejects", config.REJECTS, "*.parquet"),
]

STATUS_HELP = {
    orchestrator.STATUS_OK: "completed and wrote its artifacts",
    orchestrator.STATUS_ERROR: "raised; see the traceback",
    orchestrator.STATUS_BLOCKED: "did not run because a dependency failed "
                                 "-- this is NOT the same as failing",
    orchestrator.STATUS_SKIPPED: "cadence says it is not due yet",
    orchestrator.STATUS_SLOW: "completed, but took longer than its budget",
}


def _na(v):
    """pandas missing -> None, once, at the boundary.

    The job table uses nullable dtypes, so an absent cell is `pd.NA`, and
    `bool(pd.NA)` RAISES rather than being falsy -- the same family of trap as
    "a float NaN is truthy" already recorded in SCORE_MODULES.md. Normalising
    here means every `if s["error"]:` downstream is safe.
    """
    if v is None:
        return None
    try:
        import pandas as pd
        if v is pd.NA or (not isinstance(v, (list, tuple, dict)) and pd.isna(v)):
            return None
    except (TypeError, ValueError):
        pass
    return v


def _esc(v) -> str:
    return _html.escape("" if v is None else str(v), quote=True)


def _fmt_dur(s) -> str:
    try:
        s = float(s)
    except (TypeError, ValueError):
        return "-"
    if s < 1:
        return "<1s"
    if s < 90:
        return f"{s:.0f}s"
    return f"{s / 60:.1f}m"


def _fmt_ago(iso) -> str:
    """Human gap. The point of the page is 'is this current', so an absolute
    timestamp alone makes the reader do arithmetic."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso))
    except ValueError:
        return ""
    secs = (datetime.now() - dt).total_seconds()
    if secs < 0:
        return "just now"
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= n:
            return f"{secs / n:.0f}{unit} ago"
    return "just now"


def _mb(paths) -> float:
    total = 0
    for p in paths:
        try:
            total += os.path.getsize(p)
        except OSError:
            pass
    return total / 1e6


# ============================================================ data collection
def collect() -> dict:
    """Everything the page needs, gathered once. Read-only by construction."""
    jobs = orchestrator.read_jobs()
    asof = calendar_us.last_closed_session()

    steps = []
    for st in orchestrator.REGISTRY:
        mine = jobs[jobs["step"] == st.name]
        cur = mine.iloc[-1] if len(mine) else None
        ok = orchestrator.last_ok(st.name, jobs)
        try:
            due, why = orchestrator.is_due(st, asof, jobs)
        except Exception as exc:                                 # noqa: BLE001
            due, why = True, f"dueness check failed: {exc!r}"[:120]

        # Child rows from the bounce pipeline, nested rather than listed as
        # siblings -- they are stages of one step, not steps.
        kids = []
        prefix = f"{st.name}/"
        if len(jobs):
            kid_rows = jobs[jobs["step"].astype(str).str.startswith(prefix)]
            if len(kid_rows):
                last_run = str(kid_rows.iloc[-1]["run_id"])
                for _, k in kid_rows[kid_rows["run_id"] == last_run].iterrows():
                    kids.append({"name": str(k["step"])[len(prefix):],
                                 "duration_s": _na(k["duration_s"]),
                                 "status": str(k["status"])})

        steps.append({
            "name": st.name, "cadence": st.cadence, "desc": st.desc,
            "depends_on": st.depends_on, "timeout": st.timeout,
            "status": (str(cur["status"]) if cur is not None else "never"),
            "last_ok": (_na(ok["ended"]) if ok is not None else None),
            "duration_s": (_na(ok["duration_s"]) if ok is not None else None),
            "rows": (_na(cur["rows"]) if cur is not None else None),
            "detail": (_na(cur["detail"]) if cur is not None else None),
            "error": (_na(cur["error"]) if cur is not None else None),
            "traceback": (_na(cur["traceback"]) if cur is not None else None),
            "due": due, "why": why, "children": kids,
        })

    # Recent runs, newest first, excluding child rows.
    runs = []
    if len(jobs):
        real = jobs[jobs["cadence"] != "child"]
        for rid in list(dict.fromkeys(real["run_id"].dropna().tolist()))[::-1][:HISTORY_RUNS]:
            g = real[real["run_id"] == rid]
            counts = g["status"].value_counts().to_dict()
            runs.append({
                "run_id": str(rid),
                "n": len(g),
                "secs": float(g["duration_s"].fillna(0).sum()),
                "ok": int(counts.get(orchestrator.STATUS_OK, 0)),
                "error": int(counts.get(orchestrator.STATUS_ERROR, 0)),
                "blocked": int(counts.get(orchestrator.STATUS_BLOCKED, 0)),
                "skipped": int(counts.get(orchestrator.STATUS_SKIPPED, 0)),
                "slow": int(counts.get(orchestrator.STATUS_SLOW, 0)),
            })

    stores = []
    total_mb = 0.0
    for label, path, pat in STORES:
        files = sorted(Path(path).glob(pat)) if Path(path).exists() else []
        mb = _mb(files)
        total_mb += mb
        span = ""
        if files:
            span = f"{files[0].stem} &rarr; {files[-1].stem}"
        stores.append({"label": label, "files": len(files), "mb": mb,
                       "span": span})
    # Loose top-level parquet/json in data/ (macro, universe, panel stats, ...)
    loose = [p for p in Path(config.DATA).glob("*.parquet")]
    loose_mb = _mb(loose)
    total_mb += loose_mb
    stores.append({"label": "loose (macro, universe, …)", "files": len(loose),
                   "mb": loose_mb, "span": ""})

    return {"jobs": jobs, "asof": asof, "steps": steps, "runs": runs,
            "stores": stores, "total_mb": total_mb,
            "paused": orchestrator.paused(),
            "lock": orchestrator._lock_info(),
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


# ==================================================================== render
CSS = """
/* Page-specific only. Everything generic comes from ui.CSS -- see the collision
   guard in ui.selftest, which fails the build if a class is defined in both. */
.step{font-weight:600}
.desc{color:var(--muted);font-size:11.5px;margin-top:2px;max-width:52ch}
/* MEASURED in a 735px viewport: without a floor the eight columns squeeze until
   every cell wraps, and rows went to 174px each -- 3,500px of scrolling for a
   20-row status table. A min-width makes the wrapper's overflow-x do its job
   and hands back 70px rows, the same as at full width. */
.steps{min-width:1080px}
.due{color:var(--warn);font-weight:600}
.kids{margin-top:6px;font-size:11.5px;color:var(--muted)}
.kids span{display:inline-block;margin-right:10px}
.kids b{font-weight:600;color:var(--ink)}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;margin:10px 0 0;
font-size:11.5px;color:var(--muted)}
.legend .li{display:inline-flex;align-items:baseline;gap:6px}
.card h3{margin:0 0 4px;font-size:14px;color:var(--accent)}
.card p{margin:0;font-size:12px;color:var(--muted)}
.card .alt{margin-top:8px;font-size:11.5px}
td.num,th.num{text-align:right}
code{font:12px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace;
background:var(--skipbg);padding:2px 6px;border-radius:5px;
border:1px solid var(--line);white-space:nowrap}
button.copy{font:11px/1 inherit;padding:4px 8px;margin-left:6px;
color:var(--muted)}
button.run{font:11px/1 inherit;padding:4px 9px;margin-right:8px;
border-color:var(--accent);background:var(--accent);color:#fff;font-weight:600}
button.run:hover{opacity:.88;color:#fff}
button.run:disabled{opacity:.55;cursor:default}
details{margin-top:6px}
summary{cursor:pointer;color:var(--neg);font-size:12px;font-weight:600}
pre{background:var(--skipbg);border:1px solid var(--line);border-radius:6px;
padding:10px;overflow-x:auto;font-size:11.5px;margin:8px 0 0;max-height:340px}
"""

JS = """
// Copy-to-clipboard. The page is normally opened over file://, which is NOT a
// secure context, so navigator.clipboard is undefined there -- hence the
// execCommand fallback. If both fail the <code> is still selectable by hand.
function copyCmd(btn, text){
  var done = function(){ btn.textContent='copied'; setTimeout(function(){
    btn.textContent='copy'; }, 1200); };
  if (navigator.clipboard && window.isSecureContext){
    navigator.clipboard.writeText(text).then(done, function(){ fallback(); });
  } else { fallback(); }
  function fallback(){
    var ta=document.createElement('textarea');
    ta.value=text; ta.style.position='fixed'; ta.style.opacity='0';
    document.body.appendChild(ta); ta.select();
    try{ document.execCommand('copy'); done(); }
    catch(e){ btn.textContent='select it'; }
    document.body.removeChild(ta);
  }
}
document.addEventListener('click', function(e){
  var b = e.target.closest('button.copy');
  if (b) copyCmd(b, b.getAttribute('data-cmd'));
});

// --- live "run" buttons, ONLY when served by serve.py ----------------------
// Progressive enhancement on purpose. Opened as a plain file:// page this
// probe fails, the buttons stay hidden, and the page behaves exactly as it did
// before serve.py existed -- the copy-able commands remain the fallback.
(function(){
  if (location.protocol === 'file:') return;
  fetch('/api/steps').then(function(r){ return r.ok ? r.json() : null; })
    .then(function(d){
      if (!d) return;
      document.querySelectorAll('button.run').forEach(function(b){
        b.hidden = false;
      });
      var banner = document.getElementById('live');
      if (banner) banner.hidden = false;
    }).catch(function(){ /* static mode; nothing to do */ });

  document.addEventListener('click', function(e){
    var b = e.target.closest('button.run');
    if (!b) return;
    var step = b.getAttribute('data-step');
    b.disabled = true; b.textContent = 'starting...';
    fetch('/api/run/' + encodeURIComponent(step), {
      method: 'POST',
      headers: {'X-Screener-Control': '1'}
    }).then(function(r){ return r.json(); }).then(function(res){
      if (res.ok){
        b.textContent = 'running (pid ' + res.pid + ')';
        // The job table only updates when the step finishes and the dashboard
        // step rewrites this page, so tell the user to reload rather than
        // faking progress we cannot see.
        setTimeout(function(){ b.textContent = 'reload to see result';
                               b.disabled = false; }, 4000);
      } else {
        b.textContent = res.error || 'failed';
        setTimeout(function(){ b.textContent = '\\u25b6 run';
                               b.disabled = false; }, 4000);
      }
    }).catch(function(err){
      b.textContent = 'error'; b.disabled = false;
    });
  });
})();
"""


def _pill(status: str) -> str:
    cls = status if status in ("ok", "error", "slow", "blocked", "skipped") \
        else "never"
    return f'<span class="pill {cls}">{_esc(status)}</span>'


def render(d: dict) -> str:
    asof, paused = d["asof"], d["paused"]

    n_err = sum(1 for s in d["steps"] if s["status"] == orchestrator.STATUS_ERROR)
    n_blk = sum(1 for s in d["steps"] if s["status"] == orchestrator.STATUS_BLOCKED)
    n_due = sum(1 for s in d["steps"] if s["due"])
    # The `dashboard` step is excluded from the never-run count on purpose: it is
    # the step that BUILDS this page, so at render time its own row for the
    # current run has not been written yet. Counting it would make every healthy
    # run end with "1 step has never run", which is noise that trains you to
    # ignore the banner.
    n_never = sum(1 for s in d["steps"]
                  if s["status"] == "never" and s["name"] != "dashboard")

    if paused:
        banner = ('<div class="banner warn"><b>PAUSED.</b> '
                  f'<code>{_esc(config.ORCH_DISABLED_SENTINEL.name)}</code> is present, '
                  'so the scheduled task fires and exits without doing work. '
                  'Resume with <code>python orchestrator.py --resume</code>.</div>')
    elif n_err:
        extra = (f' {n_blk} more were blocked behind them.' if n_blk else '')
        banner = (f'<div class="banner err"><b>{n_err} step(s) failed.</b> '
                  f'Expand the traceback on the failing row.{extra}</div>')
    elif n_blk:
        # Blocked with nothing failing means the blocker is stale -- typically a
        # dependency that failed on an EARLIER run and has since recovered, so
        # the last recorded row for the dependent is still `blocked`. Amber, not
        # red: nothing is broken right now.
        banner = (f'<div class="banner warn"><b>{n_blk} step(s) blocked.</b> '
                  'They did not run and did not fail &mdash; a dependency was '
                  'unsatisfied at the time. If nothing is failing now, the next '
                  'run clears it.</div>')
    elif n_never:
        banner = (f'<div class="banner warn">{n_never} step(s) have never run. '
                  'Run <code>python orchestrator.py</code> once to populate them.'
                  '</div>')
    else:
        banner = ('<div class="banner ok"><b>All steps healthy.</b> '
                  + (f'{n_due} due on the next run.' if n_due
                     else 'Nothing is due.') + '</div>')

    # ---------------------------------------------------------- dashboards
    # The card is a <div>, NOT an <a>. Wrapping the whole card in an anchor and
    # then nesting the "all sessions" link inside it is invalid HTML -- anchors
    # cannot nest, and the browser silently hoists the inner one out of the card,
    # which renders as a stray link floating in its own empty box.
    cards = []
    for label, fn, blurb, alt_fn, alt_label in DASHBOARDS:
        alt = ""
        if alt_fn and (config.REPORTS / alt_fn).exists():
            alt = f'<div class="alt"><a href="{alt_fn}">{alt_label}</a></div>'
        if (config.REPORTS / fn).exists():
            cards.append(f'<div class="card"><h3><a href="{fn}">{label}</a></h3>'
                         f'<p>{blurb}</p>{alt}</div>')
        else:
            cards.append(f'<div class="card"><h3 class="muted">{label}</h3>'
                         f'<p>{blurb}</p>'
                         f'<p class="alt muted">not generated yet</p></div>')

    # --------------------------------------------------------------- steps
    rows = []
    for s in d["steps"]:
        cmd = f"python orchestrator.py --step {s['name']}"
        dep = (f'<div class="desc">after: {", ".join(s["depends_on"])}</div>'
               if s["depends_on"] else "")
        kids = ""
        if s["children"]:
            parts = "".join(
                f'<span><b>{_esc(k["name"])}</b> {_fmt_dur(k["duration_s"])}'
                + (' &#9888;' if k["status"] == orchestrator.STATUS_ERROR else '')
                + '</span>' for k in s["children"])
            kids = f'<div class="kids">stages: {parts}</div>'
        err = ""
        if s["error"]:
            tb = _esc(s["traceback"]) if s["traceback"] else "(no traceback recorded)"
            err = (f'<details><summary>{_esc(str(s["error"])[:160])}</summary>'
                   f'<pre>{tb}</pre></details>')
        # For a skipped step the orchestrator records the cadence reason as the
        # detail, so rendering both prints the same sentence twice.
        same = str(s["detail"] or "").strip() == str(s["why"] or "").strip()
        detail = ("" if not s["detail"] or same
                  else f'<div class="desc">{_esc(s["detail"])}</div>')
        due = '<span class="due">DUE</span> ' if s["due"] else ""

        # pandas Int64 renders missing as the string "<NA>", which would escape
        # into a literal &lt;NA&gt; in the cell.
        n_rows = s["rows"]
        rows_cell = "" if n_rows is None or str(n_rows) == "<NA>" \
            else f"{int(n_rows):,}"
        # "2026-08-07T02:05:43" wraps mid-value in a narrow column; the T is
        # machine punctuation anyway.
        when = _esc(str(s["last_ok"] or "")[:19].replace("T", " ")) \
            or '<span class="muted">never</span>'

        rows.append(
            "<tr>"
            f'<td><div class="step">{_esc(s["name"])}</div>'
            f'<div class="desc">{s["desc"]}</div>{dep}</td>'
            f'<td>{_esc(s["cadence"])}</td>'
            f'<td>{_pill(s["status"])}</td>'
            f'<td class="when">{when}'
            f'<div class="desc">{_fmt_ago(s["last_ok"])}</div></td>'
            f'<td class="num">{_fmt_dur(s["duration_s"])}</td>'
            f'<td class="num">{rows_cell}</td>'
            f'<td>{due}<span class="muted">{_esc(s["why"])}</span>'
            f'{detail}{kids}{err}</td>'
            f'<td><button class="run" data-step="{_esc(s["name"])}" '
            f'hidden>&#9654; run</button>'
            f'<code>{_esc(cmd)}</code>'
            f'<button class="copy" data-cmd="{_esc(cmd)}">copy</button></td>'
            "</tr>")

    legend = "".join(
        f'<span class="li">{_pill(k)}<span>{v}</span></span>'
        for k, v in STATUS_HELP.items())

    # --------------------------------------------------------------- stores
    srows = "".join(
        f'<tr><td>{_esc(s["label"])}</td>'
        f'<td class="num">{s["files"]:,}</td>'
        f'<td class="num">{s["mb"]:,.1f}</td>'
        f'<td class="muted">{s["span"]}</td></tr>'
        for s in d["stores"])

    # ----------------------------------------------------------- run history
    hrows = "".join(
        f'<tr><td>{_esc(r["run_id"][:19].replace("T", " "))}'
        f'<div class="desc">{_fmt_ago(r["run_id"])}</div></td>'
        f'<td class="num">{r["n"]}</td>'
        f'<td class="num">{_fmt_dur(r["secs"])}</td>'
        f'<td class="num">{r["ok"] or ""}</td>'
        f'<td class="num">{r["error"] or ""}</td>'
        f'<td class="num">{r["blocked"] or ""}</td>'
        f'<td class="num">{r["slow"] or ""}</td>'
        f'<td class="num muted">{r["skipped"] or ""}</td></tr>'
        for r in d["runs"]) or '<tr><td colspan="8" class="muted">no runs recorded</td></tr>'

    lock = d["lock"]
    if not lock:
        lock_txt = "free"
    elif lock.get("pid") == os.getpid():
        # Rendering from inside the orchestrator's own `dashboard` step. Saying
        # "held by pid N" here reads as contention when it is just us.
        lock_txt = "held by this run"
    else:
        lock_txt = (f'held by pid {lock.get("pid")} since '
                    f'{str(lock.get("started") or "")[:19].replace("T", " ")}')

    NAVBAR = ui.nav("hub", 0)
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Screener &middot; status</title><style>{ui.CSS}{CSS}</style></head>
<body>{NAVBAR}<div class="wrap">

<h1>Screener &middot; status</h1>
<div class="sub">last closed session <b>{_esc(asof)}</b>
&nbsp;&middot;&nbsp; page built {_esc(d["generated"])}
&nbsp;&middot;&nbsp; store {d["total_mb"]:,.0f} MB
&nbsp;&middot;&nbsp; lock {_esc(lock_txt)}</div>

{banner}

<h2>Dashboards</h2>
<div class="cards">{"".join(cards)}</div>

<h2>Job steps</h2>
<div class="scroll"><table class="steps"><thead><tr>
<th>step</th><th>cadence</th><th>status</th><th>last success</th>
<th class="num">took</th><th class="num">rows</th><th>state / detail</th>
<th>run it yourself</th>
</tr></thead><tbody>{"".join(rows)}</tbody></table></div>
<div class="legend">{legend}</div>
<div class="banner ok" id="live" hidden><b>Live mode.</b> This page is being
served by <code>serve.py</code>, so the <b>run</b> buttons execute the step on
this machine. Reload after a step finishes to see its result.</div>
<div class="note"><b>Opened as a file, this page never runs anything.</b> It
reads <code>data/_jobs.parquet</code> and file sizes, nothing else, and the
commands above are for you to paste into a terminal &mdash; copying one does not
execute it. To get buttons that <i>do</i> run a step, start the opt-in local
server and open the page through it:
<code>python serve.py --open</code>. It binds 127.0.0.1 only and refuses any
step name not in the registry. Run everything that is owed with
<code>python orchestrator.py</code>.</div>

<h2>Stores</h2>
<div class="scroll"><table><thead><tr>
<th>store</th><th class="num">files</th><th class="num">MB</th><th>span</th>
</tr></thead><tbody>{srows}</tbody></table></div>

<h2>Recent runs</h2>
<div class="scroll"><table><thead><tr>
<th>run</th><th class="num">steps</th><th class="num">wall</th>
<th class="num">ok</th><th class="num">error</th><th class="num">blocked</th>
<th class="num">slow</th><th class="num">skipped</th>
</tr></thead><tbody>{hrows}</tbody></table></div>
<div class="note">Capped at the {HISTORY_RUNS} most recent runs so the page stays
a fixed size as the job table grows. Full history is in
<code>data/_jobs.parquet</code>.</div>

</div><script>{JS}</script></body></html>"""


def build(verbose: bool = True) -> Path:
    config.REPORTS.mkdir(parents=True, exist_ok=True)
    d = collect()
    HUB_FILE.write_text(render(d), encoding="utf-8")
    if verbose:
        n_err = sum(1 for s in d["steps"]
                    if s["status"] == orchestrator.STATUS_ERROR)
        print(f"  hub: {HUB_FILE}  ({len(d['steps'])} step(s), "
              f"{n_err} failing, {len(d['runs'])} run(s) shown)")
    return HUB_FILE


def to_stdout() -> int:
    """Same information, no file written. For a headless check."""
    d = collect()
    print(f"\n  asof {d['asof']}   paused {d['paused']}   "
          f"store {d['total_mb']:,.0f} MB")
    print(f"  {'step':<14}{'cadence':<11}{'status':<10}{'last ok':<21}{'took':>7}")
    print("  " + "-" * 74)
    for s in d["steps"]:
        print(f"  {s['name']:<14}{s['cadence']:<11}{s['status']:<10}"
              f"{str(s['last_ok'] or '(never)')[:19]:<21}"
              f"{_fmt_dur(s['duration_s']):>7}"
              f"{'  DUE' if s['due'] else ''}")
        if s["error"]:
            print(f"                 ! {str(s['error'])[:96]}")
    print()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the master status hub.")
    ap.add_argument("--open", action="store_true",
                    help="open the page in the default browser afterwards")
    ap.add_argument("--print", dest="to_print", action="store_true",
                    help="print status to stdout, write no file")
    a = ap.parse_args()

    if a.to_print:
        return to_stdout()

    p = build()
    if a.open:
        webbrowser.open(p.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
