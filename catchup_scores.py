"""
Build a module's historical score series, so the study can measure it.

    python catchup_scores.py --dry-run              what would be built
    python catchup_scores.py --all                  every module, to its floor
    python catchup_scores.py --modules hype,dip
    python catchup_scores.py --modules hype --from 2016-08-01 --every 14
    python catchup_scores.py --all --rebuild        recompute stored sessions

RUNNING THIS WITH NO ARGUMENTS DOES NOTHING, ON PURPOSE
---------------------------------------------------------
The previous version had a `__main__` block and no argument parsing, so *every*
invocation started a multi-hour backfill -- including `--help`, which silently
ignored the flag and began writing score rows. A command whose help text starts
the job is a trap, and this one sprang: it wrote eight hype sessions before it
was noticed.

So: no arguments prints usage and exits non-zero. The work needs `--all` or an
explicit `--modules`.

WHY THE FLOORS DIFFER PER MODULE
----------------------------------
A module cannot be scored before its inputs exist, and scoring it anyway does
not fail -- it quietly produces rows computed from a subset of its components,
which is indistinguishable from a real reading later. `dip` is the sharp case:
its `senti_gap` leg comes from the sentiment module, so running it before the
news floor would score two of three depression components and look fine.

DEFAULT SPACING IS 14 SESSIONS, MATCHING `fundamental`
--------------------------------------------------------
`fundamental` has 182 dates over 2016-08 → 2026-08, i.e. one per ~14 sessions.
Matching it is what makes cross-module t-stats comparable: `dip` reading t=0.24
on 43 dates against `fundamental`'s t=4.03 on 179 is not a fair comparison, and
equalising the date count is the cheapest way to make it one.
"""

from __future__ import annotations

import argparse
import sys
import time

import config

config.safe_console()

import scores                                                    # noqa: E402

# The earliest session each module can be scored HONESTLY, and why. Measured
# from the stores: bars start 2016-07, the news store starts 2016-08, and the
# sentiment score series starts at the news floor.
FLOORS = {
    "fundamental": ("2016-08-01", "SEC fact store + bar floor"),
    "sentiment":   ("2016-08-01", "news store floor"),
    "hype":        ("2016-08-01", "bar floor (volume, trades, short volume)"),
}

# Modules whose floor is NOT a constant: it is wherever their input actually
# starts today. `dip` was pinned to "2022-12-01 -- its senti_gap leg needs the
# sentiment series" and stayed pinned after that series was extended back to
# 2016-09. The stale constant capped dip at 110 dates and combo at 66, which is
# why the only validated composite was measured on 61 dates instead of ~290.
#
# A floor that describes a dependency must be READ from that dependency, or it
# silently becomes a cap.
DERIVED_FLOORS = {
    "dip":   (("sentiment",), "its senti_gap leg reads the sentiment series"),
    "combo": (("sentiment", "hype", "dip", "fundamental"),
              "it reads every other module, so it starts where the last one does"),
}


def floor_for(module: str) -> tuple[str, str]:
    """(earliest honest session, why). Derived floors are looked up live."""
    if module in DERIVED_FLOORS:
        deps, why = DERIVED_FLOORS[module]
        starts = []
        for d in deps:
            try:
                s = scores.sessions_stored(d)
            except Exception:                                    # noqa: BLE001
                s = []
            if s:
                starts.append(s[0])
        if not starts:
            return "2022-12-01", f"{why} (no dependency series stored yet)"
        # The LATEST of the dependency starts: a module cannot be scored before
        # every input it reads exists, and scoring it anyway silently produces a
        # number from a subset of its components.
        return max(starts), f"{why}; latest dependency starts there"
    return FLOORS.get(module, ("2016-08-01", ""))

DEFAULT_EVERY = 14


def plan(modules: list[str], every: int, frm: str | None,
         rebuild: bool) -> list[tuple[str, str, int, int]]:
    """What each module would build. Read-only -- this is what --dry-run shows."""
    import calendar_us
    asof = calendar_us.last_closed_session()
    allsess = [s for s in calendar_us.all_sessions() if s <= asof]

    out = []
    for m in modules:
        floor, _why = floor_for(m)
        start = max(floor, frm) if frm else floor
        sess = [s for s in allsess if s >= start]
        # Mirror scores.catchup's own sampling exactly, anchored at the recent
        # end, or the preview would disagree with what actually runs.
        todo = sess[::-1][::every][::-1]
        have = set() if rebuild else set(scores.sessions_stored(m))
        new = [d for d in todo if d not in have]
        out.append((m, start, len(todo), len(new)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Build historical score series for the study.")
    ap.add_argument("--modules", default="",
                    help="comma-separated, e.g. hype,dip")
    ap.add_argument("--all", action="store_true",
                    help="every module in config.SCORE_MODULES")
    ap.add_argument("--every", type=int, default=DEFAULT_EVERY,
                    help=f"session spacing (default {DEFAULT_EVERY}, "
                         "matching the fundamental series)")
    ap.add_argument("--from", dest="frm", default=None,
                    help="earliest session; clamped to the module's own floor")
    ap.add_argument("--rebuild", action="store_true",
                    help="recompute sessions that are already stored -- needed "
                         "when a module's INPUTS changed")
    ap.add_argument("--dry-run", action="store_true",
                    help="show the work and write nothing")
    a = ap.parse_args()

    if a.all:
        modules = list(config.SCORE_MODULES)
    elif a.modules:
        modules = [m.strip() for m in a.modules.split(",") if m.strip()]
    else:
        # The whole point of this rewrite: no arguments must never mean
        # "start the expensive thing".
        ap.print_help()
        print("\nNothing to do. Pass --all or --modules NAME[,NAME].")
        return 2

    scores.load_all()
    known = set(scores.registered())
    unknown = [m for m in modules if m not in known]
    if unknown:
        print(f"unknown module(s): {unknown}. Registered: {sorted(known)}")
        return 2

    rows = plan(modules, a.every, a.frm, a.rebuild)
    print(f"\n  spacing every {a.every} session(s)"
          f"{'  REBUILD (recomputes stored sessions)' if a.rebuild else ''}\n")
    print(f"  {'module':<14}{'from':<13}{'target':>8}{'to build':>10}   floor reason")
    for m, start, total, new in rows:
        print(f"  {m:<14}{start:<13}{total:>8}{new:>10}   {floor_for(m)[1]}")
    total_new = sum(n for _, _, _, n in rows)
    print(f"\n  {total_new} session(s) to build in total")

    if a.dry_run:
        print("  --dry-run: nothing written")
        return 0
    if not total_new:
        return 0

    t0 = time.time()
    built = 0
    failed = []
    for m, start, _total, new in rows:
        if not new:
            print(f"\n=== {m}: already complete ===", flush=True)
            continue
        print(f"\n=== {m}: {new} session(s) from {start} ===", flush=True)
        try:
            built += scores.catchup(m, every=a.every, frm=start,
                                    rebuild=a.rebuild)
        except Exception as exc:                                 # noqa: BLE001
            # NAMED, not swallowed. A module that dies here would otherwise
            # leave a short series that looks like a finished one.
            failed.append(f"{m}({type(exc).__name__}: {exc!r}[:80])")
            print(f"  ! {m} FAILED: {exc!r}"[:160], flush=True)

    # Count what was BUILT, not what was attempted, and exit non-zero on any
    # failure. This project has shipped "DONE" after 51 errors once already.
    print(f"\n  built {built} session(s) in {(time.time() - t0) / 60:.1f} min")
    if failed:
        print(f"  FAILED: {', '.join(failed)}")
        return 1
    print("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
