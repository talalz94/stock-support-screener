#!/usr/bin/env python
"""The Narrative page: the AI's view of every sector and sub-industry, with its reasons and sources.

    python narrative_page.py             build reports/sectors/narrative.html
    python narrative_page.py --selftest  build it from made-up answers in a scratch folder and check it

Asked 2026-11-15 ("how will this narrative look in the UI and gets updated?" ... "yes do both
changes"): until then the pages showed only the scores, while each answer's summaries, themes and
source links sat in data/narrative/. Menu Research > Narrative. The `narrative` step draws it each
time it runs: once a session, and on any pass of the schedule (05:00, then every two hours until
17:00) that finds an answer file not yet checked -- so a run's view is here within two hours.

Only answers `narrative.ingest` accepted are shown. Everything in them came from the web through an
AI: it is escaped here, links must be http(s), and none of it carries weight in any ranking.
Research notes, not investment advice.
"""
from __future__ import annotations

import os
import re
import sys

import numpy as np
import pandas as pd

import config
import narrative
import ui

OUT = config.REPORTS / "sectors" / "narrative.html"
HORIZON_LABELS = {"now": ("now", "now"), "m3": ("3m", "about 3 months"), "m12": ("12m", "about 12 months"),
                  "d2": ("2d", "the next 2 trading days"), "w1": ("1w", "the next 5 trading days"),
                  "m1": ("1m", "the next month, about 21 trading days")}
CSS = """<style>
.nv-head{font-size:12.5px;color:var(--muted);margin:6px 0 10px}
.nv-list{display:flex;flex-direction:column;gap:6px;margin:6px 0 18px}
details.nv-g{border:1px solid var(--line);border-radius:9px;background:var(--panel);min-width:0}
details.nv-g>summary{list-style:none;cursor:pointer;display:flex;flex-wrap:wrap;align-items:center;
gap:6px 10px;padding:9px 12px}
details.nv-g>summary::-webkit-details-marker{display:none}
details.nv-g>summary::before{content:"\\25B8";color:var(--muted);font-size:11px}
details.nv-g[open]>summary::before{content:"\\25BE"}
.nv-name{font-weight:600;flex:1 1 9em;min-width:0;overflow-wrap:anywhere}
.nv-cs{display:inline-flex;gap:5px;flex-wrap:wrap}
.nv-c{font-size:12px;border-radius:12px;padding:1px 8px;white-space:nowrap;border:1px solid;
font-variant-numeric:tabular-nums}
.nv-p{color:var(--pos);background:var(--posbg);border-color:var(--pos)}
.nv-n{color:var(--neg);background:var(--negbg);border-color:var(--neg)}
.nv-z{color:var(--muted);background:var(--grid);border-color:var(--line)}
.nv-meta{font-size:11.5px;color:var(--muted);white-space:nowrap}
.nv-body{padding:0 14px 12px;font-size:13px;line-height:1.5;overflow-wrap:anywhere}
.nv-body p{margin:6px 0}
ul.nv-th{margin:6px 0 4px;padding-left:18px}
ul.nv-th li{margin:5px 0}
.nv-src{font-size:12px;color:var(--muted)}
.nv-d{font-weight:700}
.nv-dp{color:var(--pos)}
.nv-dn{color:var(--neg)}
.nv-subs{margin-top:8px}
.nv-subs details.nv-g{border-style:dashed;margin:6px 0}
.nv-runs td,.nv-runs th{white-space:nowrap}
</style>"""


def _day(d) -> str:
    try:
        return pd.Timestamp(str(d)).strftime("%a %b %d").replace(" 0", " ")
    except (TypeError, ValueError):
        return ui.esc(d)


def _num(v) -> int | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if np.isfinite(f) else None


def _chip(h: str, v) -> str:
    n = _num(v)
    short, long_ = HORIZON_LABELS[h]
    if n is None:
        return f'<span class="nv-c nv-z" title="{ui.esc(long_)}: no reading">{ui.esc(short)} &ndash;</span>'
    cls = "nv-p" if n > 0 else "nv-n" if n < 0 else "nv-z"
    word = {2: "strong tailwind", 1: "tailwind", 0: "mixed or nothing material", -1: "headwind",
            -2: "strong headwind"}.get(n, "")
    return f'<span class="{cls} nv-c" title="{ui.esc(long_)}: {word}">{ui.esc(short)} {n:+d}</span>'


def _link(s: dict) -> str:
    url = str(s.get("url") or "")
    if not re.match(r"^https?://", url):
        return ""
    title = ui.esc(s.get("title") or url)
    date = f' <span class="nv-src">{_day(s.get("date"))}</span>' if s.get("date") else ""
    return f'<a href="{ui.esc(url)}" rel="noopener noreferrer" target="_blank">{title}</a>{date}'


def _themes(themes: list) -> str:
    items = []
    for t in themes or []:
        up = t.get("direction") == "+"
        h = HORIZON_LABELS.get(str(t.get("horizon")), (str(t.get("horizon")), ""))[0]
        links = [x for x in (_link(s) for s in t.get("sources") or []) if x]
        src = f'<div class="nv-src">{" &middot; ".join(links)}</div>' if links else ""
        items.append(f'<li><span class="nv-d {"nv-dp" if up else "nv-dn"}" title="{"tailwind" if up else "headwind"}">'
                     f'{"+" if up else "&minus;"}</span> <span class="muted">{ui.esc(h)}</span> '
                     f'{ui.esc(t.get("theme", ""))}{src}</li>')
    return f'<ul class="nv-th">{"".join(items)}</ul>' if items else ""


def _block(g: dict, hz: tuple, prev: dict, subs: str = "") -> str:
    key = (g.get("group"), g.get("level"))
    chips = "".join(_chip(h, g.get(h)) for h in hz)
    p, n = prev.get(key), _num(g.get(hz[0]))
    if not prev:
        moved = ""
    elif p is None:
        moved = " &middot; new"
    elif n is not None and n != p:
        moved = f" &middot; {'up' if n > p else 'down'} from {p:+d}"
    else:
        moved = ""
    meta = f'<span class="nv-meta">{ui.esc(g.get("confidence", ""))} confidence{moved}</span>'
    body = (f'<p>{ui.esc(g.get("summary", ""))}</p>' if g.get("summary") else "") + _themes(g.get("themes"))
    return (f'<details class="nv-g"><summary><span class="nv-name">{ui.esc(g.get("group", ""))}</span>'
            f'<span class="nv-cs">{chips}</span>{meta}</summary><div class="nv-body">{body}{subs}</div></details>')


def panel(kind: str) -> str:
    """One task's newest checked view: its sectors, best first, each opening to its reasons."""
    hz = narrative.KINDS[kind]["horizons"]
    doc = narrative.answer(kind)
    if not doc:
        when = ("Saturdays" if kind == "weekly" else
                f"12:30 on 15, 17, 19 and 21 Nov (a trial that ends {_day(narrative.SHORT_END)})")
        return (f'<p class="muted">No checked view yet. The task runs {when}; its view appears here within '
                f'two hours of a run, once it has been checked.</p>')
    prev_df = narrative.previous(kind, str(doc.get("date")))
    prev = {(r.group, r.level): _num(getattr(r, hz[0])) for r in prev_df.itertuples()} if len(prev_df) else {}
    parent = narrative.parents_of(str(doc.get("inputs_asof")))
    groups = doc.get("groups") or []
    sectors = [g for g in groups if g.get("level") == "sector"]
    subs = [g for g in groups if g.get("level") == "subind"]
    names = {g.get("group") for g in sectors}
    by_parent: dict[str, list] = {}
    for s in subs:
        by_parent.setdefault(parent.get(s.get("group")) if parent.get(s.get("group")) in names else "", []).append(s)

    def order(gs):
        return sorted(gs, key=lambda g: (-(_num(g.get(hz[0])) if _num(g.get(hz[0])) is not None else -9),
                                         -(_num(g.get(hz[1])) or 0), str(g.get("group"))))

    def sub_html(kids: list) -> str:
        own = [s for s in order(kids) if not s.get("inherited")]
        same = sorted(str(s.get("group")) for s in kids if s.get("inherited"))
        out = "".join(_block(s, hz, prev) for s in own)
        if same:
            out += f'<p class="muted">Same view as the sector: {ui.esc(", ".join(same))}</p>'
        return f'<div class="nv-subs"><b style="font-size:12px">Sub-industries</b>{out}</div>' if out else ""

    blocks = [_block(g, hz, prev, sub_html(by_parent.get(g.get("group"), []))) for g in order(sectors)]
    if by_parent.get(""):
        blocks.append(f'<h3>Other sub-industries</h3>{sub_html(by_parent[""])}')
    since = ""
    if prev:
        since = f' &middot; changes against the view of {_day(prev_df.date.iloc[0])}'
    head = (f'<div class="nv-head">View of <b>{_day(doc.get("date"))}</b>, written from data through '
            f'{_day(doc.get("inputs_asof"))} &middot; {len(sectors)} sectors, {len(subs)} sub-industries '
            f'&middot; {ui.esc(doc.get("searches_used", "?"))} web searches{since}. Tap a sector for its '
            f'reasons and sources.</div>')
    return head + f'<div class="nv-list">{"".join(blocks)}</div>'


def _pct(a, b, d) -> str:
    if a is None or not np.isfinite(a) or b is None or not np.isfinite(b):
        return '<span class="muted">not measured</span>'
    if d is None or not np.isfinite(d):
        return f'{a:.0f}% &rarr; {b:.0f}% <span class="muted">(window reset)</span>'
    return f'{a:.0f}% &rarr; {b:.0f}% (<b>{d:+.0f}</b>)'


def runs_panel() -> str:
    R = narrative.runs()
    note = ui.info(
        "<b>Tokens and cost</b> come from Claude Code's own record in each run's transcript, the helper model "
        "behind web searches and page reads included; the cost is Claude Code's estimate at API list prices "
        "(on a Pro plan nothing is billed per token: the runs count against the plan's limits). "
        "<b>Weekly limit</b> is the plan's meter read by the run itself at its start and its end (from "
        "2026-11-15 evening): whole percents, so a share is good to about one point. The meter counts the whole "
        "account, so a share includes anything else done meanwhile: other Claude Code sessions on this "
        "machine are counted in the last column; chats on claude.ai or another device are not visible here. "
        "<b>Minutes working</b> leave out the time a run stood waiting for someone to approve a tool (the app's "
        "own permission records), shown under it.")
    if not len(R):
        return f'<p class="muted">No run recorded yet. {note}</p>'
    rows = []
    for r in R.sort_values("start", ascending=False).to_dict("records"):
        task = "weekly" if r.get("task") == "narrative-weekly" else "trial"
        fin = bool(r.get("finished"))
        tin = (r.get("main_input", 0) + r.get("main_cache_write", 0) + r.get("main_cache_read", 0)
               + (r.get("helper_input") if fin else 0))
        tout = r.get("main_output", 0) + (r.get("helper_output") if fin else 0)
        cost = f'${r["cost_usd"]:.2f}' if fin and np.isfinite(r.get("cost_usd", np.nan)) else '<span class="muted">no record yet</span>'
        others = r.get("others_sessions", np.nan)
        oth = f"{int(others)}" if others is not None and np.isfinite(others) else '<span class="muted">&ndash;</span>'
        work, wait = r.get("work_min"), r.get("wait_min")
        work = r.get("span_min", 0) if work is None or not np.isfinite(work) else work
        wait = wait if wait is not None and np.isfinite(wait) else 0.0
        mins = f'{work:.0f}' + (f'<br><span class="muted">+{wait:.0f} waiting</span>' if wait >= 1 else '')
        rows.append(
            f'<tr><td>{task} &middot; {_day(str(r.get("start", ""))[:10])}</td>'
            f'<td class="num">{mins}</td>'
            f'<td class="num">{narrative._tok(tin)}{"" if fin else "+"}</td><td class="num">{narrative._tok(tout)}{"" if fin else "+"}</td>'
            f'<td class="num">{cost}</td>'
            f'<td>{_pct(r.get("weekly_pct_start"), r.get("weekly_pct_end"), r.get("weekly_share"))}</td>'
            f'<td>{_pct(r.get("five_pct_start"), r.get("five_pct_end"), r.get("five_share"))}</td>'
            f'<td class="num">{oth}</td>'
            f'<td>{ui.esc(r.get("answer") or "none")}</td></tr>')
    table = ('<div class="scroll"><table class="nv-runs"><thead><tr><th>Run</th><th class="num">Minutes working</th>'
             '<th class="num">Tokens in</th><th class="num">Tokens out</th><th class="num">Cost</th>'
             '<th>Weekly limit</th><th>5-hour limit</th><th class="num">Other sessions</th><th>Answer</th>'
             f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div>')
    measured = R[R["weekly_share"].notna()] if "weekly_share" in R.columns else R.iloc[0:0]
    summary = (f'{len(R)} run(s) recorded, {len(measured)} with the weekly-limit share measured'
               + (f' (mean {measured.weekly_share.mean():.1f} points a run)' if len(measured) else '') + '.')
    return f'<div class="nv-head">{summary} {note} A "+" marks a run still open: its tokens so far.</div>{table}'


def build(verbose: bool = True) -> "os.PathLike":
    about = ui.info(
        "What an AI (Claude, in two scheduled tasks) reads as driving each sector, from &minus;2 (strong headwind) "
        "to +2 (strong tailwind), with confidence, themes and the sources it cited. The weekly task looks at "
        "now, about 3 months and about 12 months; a one-week trial (15-21 Nov 2026) looks at the next 2 trading "
        "days, week and month. A sub-industry keeps its sector's view unless the AI found something of its own. "
        "It carries <b>no</b> weight anywhere: an AI's view of the past knows how it turned out, so it can only "
        "be judged on later prices, over months of these views.")
    body = ui.tabs("nvtabs", [("weekly", "This week", "Week", panel("weekly")),
                              ("short", "Next days (trial)", "Trial", panel("short")),
                              ("runs", "Runs and cost", "Cost", runs_panel())])
    html = ui.page("Narrative", body, subtitle=f"the AI's view of each sector, with its reasons and sources "
                   f"&middot; research notes, not investment advice {about}",
                   active="narrative", depth=1, head=CSS)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_name(f".{OUT.name}.tmp")
    tmp.write_text(html, encoding="utf-8")
    os.replace(tmp, OUT)
    if verbose:
        print(f"  narrative page: {OUT}")
    return OUT


def selftest() -> int:
    """The page from made-up answers in a scratch folder: order, sub-industries, what changed,
    escaping of what the web said, links limited to http(s), and the empty and run states."""
    import json
    import tempfile
    import time
    from pathlib import Path
    global OUT
    saved = (narrative.DIR, narrative.INPUTS, narrative.RUNS, {k: dict(v) for k, v in narrative.KINDS.items()}, OUT)
    bad = 0

    def ok(name: str, cond: bool) -> None:
        nonlocal bad
        bad += not cond
        print(f"  {'ok ' if cond else 'BAD'} {name}")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        try:
            narrative.DIR, narrative.INPUTS, narrative.RUNS = root, root / "inputs_latest.json", root / "_runs.parquet"
            narrative.KINDS["weekly"].update(dir=root, scores=root / "scores.parquet", state=root / "_ingested.json")
            narrative.KINDS["short"].update(dir=root / "short", scores=root / "short" / "scores.parquet",
                                            state=root / "short" / "_ingested.json")
            OUT = root / "page" / "narrative.html"
            (root / "short").mkdir()
            ok("no answers: both tabs say so, and nothing breaks", "No checked view yet" in build(verbose=False).read_text(encoding="utf-8"))
            inp = {"asof": "2026-11-13", "groups": [
                {"group": "Energy", "level": "sector"}, {"group": "Utilities", "level": "sector"},
                {"group": "Refiners", "level": "subind", "parent_sector": "Energy"},
                {"group": "Pipelines", "level": "subind", "parent_sector": "Energy"}]}
            (root / "inputs_2026-11-13.json").write_text(json.dumps(inp), encoding="utf-8")
            src = [{"url": "https://example.org/a", "date": "2026-11-05", "title": "<script>alert(1)</script>"}]

            def g(name, level, now, m3, m12, inherited=False, summary="s", themes=None):
                return {"group": name, "level": level, "now": now, "m3": m3, "m12": m12, "confidence": "medium",
                        "inherited": inherited, "summary": summary,
                        "themes": [] if inherited else (themes or [{"theme": "t", "direction": "+", "horizon": "now",
                                                                    "sources": src}])}
            for date, e_now in (("2026-11-08", -1), ("2026-11-15", 1)):
                doc = {"date": date, "inputs_asof": "2026-11-13", "searches_used": 3, "groups": [
                    g("Energy", "sector", e_now, 0, 0, summary="Energy <b>bold</b> & co"),
                    g("Utilities", "sector", 0, 1, 1, themes=[{"theme": "u", "direction": "-", "horizon": "m3",
                                                               "sources": src}]),
                    g("Refiners", "subind", 2, 1, 0), g("Pipelines", "subind", e_now, 0, 0, inherited=True)]}
                p = root / f"{date}.json"
                p.write_text(json.dumps(doc), encoding="utf-8")
                past = time.time() - 600
                os.utime(p, (past, past))
                narrative.ingest(verbose=False)
            h = build(verbose=False).read_text(encoding="utf-8")
            weekly = h[h.find('id="t-weekly"'):h.find('id="t-short"')]
            ok("sectors best first (Energy +1 before Utilities 0)", weekly.find(">Energy<") < weekly.find(">Utilities<"))
            ok("what changed since the last view", "up from -1" in weekly)
            ok("a sub-industry with its own view sits under its sector", weekly.find(">Refiners<") > weekly.find(">Energy<")
               and weekly.find(">Refiners<") < weekly.find(">Utilities<"))
            ok("an inherited one is named, not repeated", "Same view as the sector: Pipelines" in weekly)
            ok("what the web said is escaped", "<script>alert(1)" not in h and "&lt;script&gt;" in h
               and "Energy &lt;b&gt;bold&lt;/b&gt; &amp; co" in h)
            ok("the answers were accepted, then drawn", len(narrative.latest("2026-11-15")) == 4)
            ok("only http(s) links (the check already demands it; the page refuses others too)",
               _link({"url": "javascript:alert(1)", "title": "x"}) == "" and 'href="https://example.org/a"' in h
               and 'rel="noopener noreferrer"' in h)
            ok("chips carry their sign in words, not colour alone", "now +1" in weekly and "3m +1" in weekly)
            ok("the trial tab says it has no view yet", "No checked view yet" in h[h.find('id="t-short"'):])
            ok("no nan on the page", ">nan<" not in h and "nan%" not in h)
        finally:
            narrative.DIR, narrative.INPUTS, narrative.RUNS = saved[0], saved[1], saved[2]
            for k, v in saved[3].items():
                narrative.KINDS[k].clear()
                narrative.KINDS[k].update(v)
            OUT = saved[4]
    print("selftest:", "PASS" if not bad else f"{bad} FAILED")
    return 1 if bad else 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    build()
    return 0


if __name__ == "__main__":
    sys.exit(main())
