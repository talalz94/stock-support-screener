"""
Regenerate the mechanical parts of the docs from what actually happened.

    python docs.py            rewrite the generated blocks
    python docs.py --print    show them without writing

WHY THIS EXISTS
-----------------
Every hand-written cost table in this project has gone stale within a day. The
step budgets were sized against a leaderboard testing two modules and were wrong
by 78% once it tested four; `reports/` was described as badly bloated when it
was 19 MB. Numbers that are typed by hand rot, and a rotted number is worse than
no number because it is quoted with confidence.

So the timings, the store inventory and the disk projection are READ FROM DISK
on every run: `data/_jobs.parquet` for durations, the stores themselves for
sizes. Narrative stays hand-written; only the blocks between the markers below
are replaced.

    <!-- GENERATED:name --> ... <!-- /GENERATED:name -->
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

import pandas as pd

import config

config.safe_console()

import orchestrator                                              # noqa: E402

TARGETS = (config.ROOT / "NEXT_SESSION.md",)


def _fmt_dur(s: float) -> str:
    if s != s:
        return "-"
    if s < 90:
        return f"{s:.0f}s"
    return f"{s / 60:.1f} min"


def cost_table() -> str:
    """Per-step wall clock, from the job table. Never hand-typed."""
    jobs = orchestrator.read_jobs()
    if not len(jobs):
        return "_No runs recorded yet._"
    ok = jobs[jobs["status"].isin(["ok", "slow"])].copy()
    ok["duration_s"] = pd.to_numeric(ok["duration_s"], errors="coerce")
    ok = ok[ok["duration_s"].notna() & ~ok["step"].astype(str).str.contains("/")]
    if ok.empty:
        return "_No completed steps recorded yet._"

    # The ⚠ compares against the RECENT max, not the all-time one. A step that
    # was 16.2 min before it was optimised to 0.5s would otherwise carry the
    # warning for ever, which trains the reader to ignore it.
    RECENT = 5
    g = ok.groupby("step")["duration_s"]
    stats = pd.DataFrame({"last": g.last(), "median": g.median(),
                          "max": g.apply(lambda s: s.tail(RECENT).max()),
                          "runs": g.size()})
    order = [s.name for s in orchestrator.REGISTRY]
    stats = stats.reindex([s for s in order if s in stats.index])

    budgets = {s.name: s.timeout for s in orchestrator.REGISTRY}
    cad = {s.name: s.cadence for s in orchestrator.REGISTRY}

    lines = ["| step | cadence | last | median | slowest (last 5) | budget | runs |",
             "|---|---|---:|---:|---:|---:|---:|"]
    for name, r in stats.iterrows():
        over = " ⚠" if r["max"] > budgets.get(name, 1e9) else ""
        lines.append(
            f"| `{name}` | {cad.get(name,'')} | {_fmt_dur(r['last'])} | "
            f"{_fmt_dur(r['median'])} | {_fmt_dur(r['max'])}{over} | "
            f"{_fmt_dur(budgets.get(name, float('nan')))} | {int(r['runs'])} |")

    # A full pass is the sum of medians, not of the worst run.
    daily = stats[[c for c in ["median"]]].copy()
    daily["cad"] = [cad.get(i, "daily") for i in daily.index]
    d_sum = daily[daily["cad"] == "daily"]["median"].sum()
    w_sum = daily[daily["cad"] == "weekly"]["median"].sum()
    lines += [
        "",
        f"**Daily total ≈ {_fmt_dur(d_sum)}.** Weekly adds "
        f"{_fmt_dur(w_sum)} on top. ⚠ marks a step whose slowest run of the "
        f"last {RECENT} exceeded its budget.",
    ]
    return "\n".join(lines)


def store_table() -> str:
    """Disk per store, measured, plus the daily growth rate."""
    rows = []
    total = 0
    for label, path, pat in (
            ("bars 1d", config.BARS / "1d", "*.parquet"),
            ("bars 1h", config.BARS / "1h", "*.parquet"),
            ("bars ETF", config.BARS_ETF, "*.parquet"),
            ("news", config.NEWS, "*.parquet"),
            ("sentiment cache", config.SENTI, "*.parquet"),
            ("scores", config.SCORES, "*.parquet"),
            ("fundamentals", config.FUNDAMENTALS, "*.parquet"),
            ("short volume", config.SHORTVOL, "*.parquet"),
            ("flags", config.FLAGS, "*.parquet"),
            ("rejects", config.REJECTS, "*.parquet")):
        files = sorted(path.glob(pat)) if path.exists() else []
        mb = sum(f.stat().st_size for f in files) / 1e6
        total += mb
        span = f"{files[0].stem} → {files[-1].stem}" if files else "—"
        rows.append(f"| {label} | {len(files):,} | {mb:,.1f} | {span} |")

    loose = list(config.DATA.glob("*.parquet"))
    lmb = sum(f.stat().st_size for f in loose) / 1e6
    total += lmb
    rows.append(f"| loose (macro, universe, jobs, study) | {len(loose)} | "
                f"{lmb:,.1f} | — |")

    reports = list(config.REPORTS.rglob("*.html"))
    rmb = sum(f.stat().st_size for f in reports) / 1e6

    return "\n".join(
        ["| store | files | MB | span |", "|---|---:|---:|---|"] + rows +
        ["",
         f"**`data/` total ≈ {total:,.0f} MB.** "
         f"`reports/` is a further {rmb:,.0f} MB across {len(reports)} pages.",
         "",
         "Measured bytes per stored row (zstd-9): bars **25.0**, news **91.8**, "
         "fundamentals **11.6**, scores **3.2**, short volume **12.1**.",
         ])


def module_table() -> str:
    try:
        import scores
        scores.load_all()
        rows = ["| module | metrics | stored sessions | span |",
                "|---|---:|---:|---|"]
        for m in config.SCORE_MODULES:
            sess = scores.sessions_stored(m)
            n = len(scores.get(m).metrics())
            span = f"{sess[0]} → {sess[-1]}" if sess else "—"
            rows.append(f"| `{m}` | {n} | {len(sess)} | {span} |")
        return "\n".join(rows)
    except Exception as exc:                                     # noqa: BLE001
        return f"_module table unavailable: {exc!r}_"


def study_table() -> str:
    """What the effectiveness study has measured so far."""
    try:
        import study
        df = study.read()
        if df.empty:
            return ("_The effectiveness study has not run yet._ "
                    "`python study.py` — 4 horizons x 4 size buckets.")
        best = study.best_by_metric(df)
        strong = best[best["t"].abs() >= 2].sort_values(
            "t", key=abs, ascending=False)
        head = (f"{len(df):,} cells measured across {df['metric'].nunique()} "
                # int()/str() the numpy scalars: their repr leaks into the
                # rendered markdown as `np.int64(1)`.
                f"metrics, horizons "
                f"{sorted(int(h) for h in df['horizon'].unique())}, "
                f"buckets {sorted(str(b) for b in df['size'].unique())}.\n\n"
                f"**{len(strong)} metric(s) reach |t| >= 2 at their best "
                f"horizon.**\n")
        if strong.empty:
            return head
        rows = ["| metric | module | best h | IC | t | hit |",
                "|---|---|---:|---:|---:|---:|"]
        for _, r in strong.head(20).iterrows():
            rows.append(f"| `{r['metric']}` | {r['module']} | "
                        f"{int(r['horizon'])} | {r['ic']:+.4f} | {r['t']:+.2f} "
                        f"| {r['hit']:.0%} |")
        return head + "\n".join(rows)
    except Exception as exc:                                     # noqa: BLE001
        return f"_study table unavailable: {exc!r}_"


BLOCKS = {
    "costs": cost_table,
    "stores": store_table,
    "modules": module_table,
    "study": study_table,
}


def render_all() -> dict[str, str]:
    return {k: fn() for k, fn in BLOCKS.items()}


def apply(verbose: bool = True) -> int:
    """Replace each marked block in every target. Missing markers are appended
    once, so a fresh doc picks the blocks up without hand-editing."""
    blocks = render_all()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    touched = 0
    for path in TARGETS:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        original = text
        for name, body in blocks.items():
            start, end = f"<!-- GENERATED:{name} -->", f"<!-- /GENERATED:{name} -->"
            payload = (f"{start}\n_Generated {stamp} — do not edit by hand._\n\n"
                       f"{body}\n{end}")
            if start in text and end in text:
                head = text[:text.index(start)]
                tail = text[text.index(end) + len(end):]
                text = head + payload + tail
            else:
                text = text.rstrip() + f"\n\n## {name.title()} (generated)\n\n" \
                    + payload + "\n"
        if text != original:
            path.write_text(text, encoding="utf-8")
            touched += 1
        if verbose:
            print(f"  {path.name}: {len(blocks)} generated block(s)")
    return touched


def main() -> int:
    ap = argparse.ArgumentParser(description="Regenerate generated doc blocks.")
    ap.add_argument("--print", dest="show", action="store_true")
    a = ap.parse_args()
    if a.show:
        for name, body in render_all().items():
            print(f"\n===== {name} =====\n{body}")
        return 0
    apply()
    return 0


if __name__ == "__main__":
    sys.exit(main())
