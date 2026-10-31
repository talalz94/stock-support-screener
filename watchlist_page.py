#!/usr/bin/env python
"""Watchlists, measured every night by the orchestrator and drawn on a local page.

A watchlist is a folder under data/watchlists/ holding the list as picked (list.csv)
and a `track.json` that switches tracking on:

    {"entry": "2026-10-23", "title": "Gainers at support"}

Each night the `watchlists` step re-measures every such list with watchlist_track.track
(recomputed from the bar store, never appended, so a missed night loses nothing) and
writes reports/watchlist/index.html plus one page per list.

WHY THIS IS AN ORCHESTRATOR STEP. Until 2026-10-31 the measuring ran only inside a
Claude scheduled task, which fires only while the Claude app is open and its window
listening: 10-29 and 10-30 never fired, and the runs that did fire waited on approvals.
The orchestrator runs from Windows Task Scheduler and retries every 2 hours, so the
numbers are now current whether or not Claude is running. Only the copy to the
claude.ai dashboard still needs a Claude session.

HONESTY RULES (as the other pages): the benchmark is printed beside every list result,
the population behind each rate is named, a missing value is a dash, rows are ordered
by change since entry -- a list's own result, not a score. Research screens, not advice.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import config
import ui
import watchlist_track as W

OUT = config.REPORTS / "watchlist"
STATUS_ORDER = ["per plan", "held", "below support", "failed", "no bar"]
STATUS_CLS = {"per plan": "pos", "held": "", "below support": "warn", "failed": "neg", "no bar": "muted"}

HEAD = """<style>
.wl td.n,.wl th.n{text-align:right;font-variant-numeric:tabular-nums}
.wl .pos{color:var(--pos)} .wl .neg{color:var(--neg)} .wl .warn{color:var(--warn)}
.st{display:inline-block;padding:1px 7px;border-radius:9px;font-size:12px;white-space:nowrap}
.st.pos{background:var(--posbg);color:var(--pos)} .st.neg{background:var(--negbg);color:var(--neg)}
.st.warn{background:var(--warnbg);color:var(--warn)} .st.muted{color:var(--muted)}
</style>"""


def lists() -> list[tuple[str, dict]]:
    """(name, track.json) for every watchlist that has tracking switched on."""
    out = []
    for p in sorted(W.ROOT.glob("*/track.json")):
        cfg = json.loads(p.read_text(encoding="utf-8"))
        entry = pd.Timestamp(cfg["entry"])                       # a date, or fail loudly
        assert (p.parent / "list.csv").exists(), f"{p.parent.name}: no list.csv"
        out.append((p.parent.name, {**cfg, "entry": entry.strftime("%Y-%m-%d")}))
    return out


def _pct(v, signed: bool = True) -> str:
    if v is None or not np.isfinite(v):
        return "&mdash;"
    cls = "pos" if v > 0 else "neg" if v < 0 else ""
    return f'<span class="{cls}">{v:+.2f}%</span>' if signed else f"{v:.1f}%"


def _num(v, nd: int = 2) -> str:
    return "&mdash;" if v is None or not np.isfinite(v) else f"{v:,.{nd}f}"


def render_list(name: str, cfg: dict, S: dict) -> str:
    d = W.ROOT / name
    T = pd.read_csv(d / "tracking.csv")
    L = pd.read_csv(d / "list.csv")
    if "sector" in L.columns:
        T = T.merge(L[["ticker", "sector"]], on="ticker", how="left")
    T = T.sort_values("since_pct", ascending=False, na_position="last")
    title = cfg.get("title") or name
    sub = (f"Picked at the {cfg['entry']} close; measured through {S['latest']} "
           f"({S['sessions']} session(s)). Recomputed every night from the price store.")
    pool_note = (f"the average of all {S['pool_stocks']:,} stocks that met the list's own "
                 f"eligibility on {cfg['entry']} ($5+ close, $20M+ a day), same dates")
    summary = (
        '<div class="scroll"><table class="wl"><thead><tr><th></th>'
        '<th class="n">average change</th><th class="n">median change</th>'
        '<th class="n">share that rose</th></tr></thead><tbody>'
        f'<tr><td>this list ({S["stocks"]} stocks)</td><td class="n">{_pct(S["list_avg_pct"])}</td>'
        f'<td class="n">{_pct(S["list_median_pct"])}</td><td class="n">{_pct(S["list_up_pct"], False)}</td></tr>'
        f'<tr><td>liquid market ({S["pool_stocks"]:,} stocks)</td><td class="n">{_pct(S["pool_avg_pct"])}</td>'
        f'<td class="n">{_pct(S["pool_median_pct"])}</td><td class="n">{_pct(S["pool_up_pct"], False)}</td></tr>'
        f'</tbody></table></div><p class="muted">Liquid market = {pool_note}. '
        'Equal-weight; no costs. A few sessions are mostly noise.</p>')
    counts = T["status"].value_counts()
    chips = "".join(ui.chip(s, int(counts.get(s, 0)), STATUS_CLS.get(s, ""))
                    for s in STATUS_ORDER if s != "no bar" or counts.get(s, 0))
    legend = ('<p class="muted">per plan = latest close above the entry close and at/above the '
              'support level; held = at/above the level, not above entry; below support = under '
              'the level, no low more than 10% under it yet; failed = a low more than 10% under '
              'the level since entry (final).</p>')
    rows = []
    for r in T.itertuples():
        st = str(r.status)
        rows.append(
            f'<tr><td><a href="../stock/{ui.esc(r.ticker)}.html">{ui.esc(r.ticker)}</a></td>'
            f'<td>{ui.esc(getattr(r, "sector", "") if isinstance(getattr(r, "sector", ""), str) else "")}</td>'
            f'<td class="n">{_num(r.entry_close)}</td><td class="n">{_num(r.level)}</td>'
            f'<td class="n">{_num(r.latest_close)}</td><td class="n">{_pct(r.since_pct)}</td>'
            f'<td class="n">{_pct(r.best_close_pct)}</td><td class="n">{_pct(r.worst_low_pct)}</td>'
            f'<td><span class="st {STATUS_CLS.get(st, "")}">{ui.esc(st)}</span></td></tr>')
    table = ('<div class="scroll sticky-x"><table class="wl"><thead><tr><th>ticker</th><th>sector</th>'
             '<th class="n">entry close</th><th class="n">support level</th><th class="n">latest close</th>'
             '<th class="n">since entry</th><th class="n">best close</th><th class="n">worst low</th>'
             '<th>status</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>")
    body = (ui.card("List against the market", summary)
            + ui.card("Status", f'<div class="chips">{chips}</div>{legend}')
            + ui.card(f"Per stock, best to worst since {cfg['entry']}", table)
            + '<p class="muted">Research screens, not investment advice.</p>')
    return ui.page(title, body, subtitle=sub, active="watchlist", depth=1, head=HEAD)


def render_index(rows: list[tuple[str, dict, dict]]) -> str:
    if not rows:
        body = ui.card(None, "<p>No watchlist has tracking switched on (data/watchlists/*/track.json).</p>")
        return ui.page("Watchlists", body, active="watchlist", depth=1, head=HEAD)
    trs = "".join(
        f'<tr><td><a href="{ui.esc(n)}.html">{ui.esc(c.get("title") or n)}</a></td><td>{c["entry"]}</td>'
        f'<td>{S["latest"]}</td><td class="n">{S["sessions"]}</td><td class="n">{S["stocks"]}</td>'
        f'<td class="n">{_pct(S["list_avg_pct"])}</td><td class="n">{_pct(S["pool_avg_pct"])}</td>'
        f'<td class="n">{S["per_plan"]} / {S["held"]} / {S["below_support"]} / {S["failed"]}</td></tr>'
        for n, c, S in rows)
    table = ('<div class="scroll"><table class="wl"><thead><tr><th>list</th><th>entry</th><th>through</th>'
             '<th class="n">sessions</th><th class="n">stocks</th><th class="n">list avg</th>'
             '<th class="n">liquid market avg</th><th class="n">per plan / held / below / failed</th>'
             '</tr></thead><tbody>' + trs + "</tbody></table></div>")
    sub = "Fixed lists, each measured from its own entry close against the liquid market over the same dates."
    return ui.page("Watchlists", ui.card(None, table) + '<p class="muted">Research screens, not investment advice.</p>',
                   subtitle=sub, active="watchlist", depth=1, head=HEAD)


def build(verbose: bool = False) -> tuple[Path, int]:
    """Measure every tracked list and write the pages. Returns (index path, lists)."""
    OUT.mkdir(parents=True, exist_ok=True)
    done = []
    for name, cfg in lists():
        S = W.track(name, cfg["entry"], verbose=verbose)
        (OUT / f"{name}.html").write_text(render_list(name, cfg, S), encoding="utf-8")
        done.append((name, cfg, S))
    p = OUT / "index.html"
    p.write_text(render_index(done), encoding="utf-8")
    return p, len(done)


def selftest() -> int:
    assert _pct(float("nan")) == "&mdash;" and _pct(None) == "&mdash;", "missing is a dash, never 0"
    assert "pos" in _pct(1.0) and "neg" in _pct(-1.0)
    html = render_index([])
    assert "No watchlist" in html and "<html" in html
    print("watchlist_page selftest OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    p, n = build(verbose=True)
    print(f"{n} watchlist(s) -> {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
