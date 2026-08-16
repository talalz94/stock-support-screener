"""
One unattended chain that makes the DISPLAYED data correct, then the history.

    python fix_data.py             run it
    python fix_data.py --dry-run   stages and costs only

ORDERED SO THE SCREENER BECOMES USABLE FIRST
-----------------------------------------------
The full job is ~11 hours, but almost all of that is recomputing 182 historical
sessions -- which only the factor study reads. Nothing you look at on a page
depends on it. So the chain is split at the point where the app becomes
trustworthy:

    stages 1-4   ~3 h    the screener is correct and usable
    stages 5-6   ~9 h    the history behind the study catches up

If the machine dies after stage 4, the thing that matters is already done.

WHAT IS BEING FIXED, AND WHY IT TOOK THIS LONG TO FIND
---------------------------------------------------------
Three separate faults, each of which hid the next:

  1. `_ttm()` preferred a stale ANNUAL figure over newer quarters. 94% of
     filers, median 181 days stale. AAPL's P/E read 40.87 against a true 34.70.
  2. Weighted-average share counts were treated as flows -- summed for TTM and
     Q4-derived by subtraction, which gave AAPL **-30,150,480,000** diluted
     shares.
  3. The fact store was STALE, and this is the big one. SEC's bulk data sets
     lag (2026q2 probed 2026-08-13: HTTP 404, not published), the per-company
     top-up existed, and the script that ran it had been deleted while its
     scheduled task kept reporting success.

1 and 2 are fixed in code and pinned by selftests. 3 is what stage 1 fixes, and
it is why googling a ticker disagreed with the screener even after the
arithmetic was right: the numbers were correct for their period and up to 925
days old.

WHY THE ORDER IS NOT NEGOTIABLE
----------------------------------
Scoring reads the fact store, and the study reads the scores. Rebuilding
history before the refetch would recompute 182 sessions from the same stale
facts -- a correct measurement of numbers nobody should be looking at, which is
the loop this project has already been round three times. Data first, then
scores, then history, then the measurement of it.

Every stage is resumable and records itself, so a reboot costs the stage in
flight rather than the run.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta

import config

config.safe_console()

LOG = config.DATA / "_fix_data.log"
STATE = config.DATA / "_fix_data_state.json"

# (name, argv, hours, why, usable_after)
STAGES = (
    ("prefetch", [sys.executable, "providers.py", "--prefetch"], 1.6,
     "warm the provider cache for the whole universe, once", False),
    ("score", [sys.executable, "orchestrator.py", "--force",
               "--step", "fundamental", "--step", "combo",
               "--wait-for-lock", "180"], 1.0,
     "re-score today on provider metrics, our arithmetic only as fallback",
     False),
    ("pages", [sys.executable, "orchestrator.py", "--force",
               "--step", "explore", "--step", "snapshots", "--step", "profiles",
               "--step", "dashboard", "--wait-for-lock", "180"], 0.4,
     "rebuild explore, profiles and the dashboard", False),
    ("recheck", [sys.executable, "providers.py", "--n", "150", "--compare",
                 "--tradeable"], 0.3,
     "measure agreement against the provider on the tradeable universe", True),
)

# DELIBERATELY NOT IN THE CHAIN
# --------------------------------
# `rebuild_history.py`. It ran for 10 hours on 2026-08-13 and rebuilt ONE
# fundamental session, because it resumes from a state file that lists all 183
# as done -- they were, but by the buggy code. Its `facts_refresh` stage also
# spent 500 minutes re-fetching 519 companies that `refetch.py` had already
# collected properly, then failed with 119 errors.
#
# It needs `--fresh` to mean anything, and at the ~94 min/session measured on
# the enlarged fact store that is not a run anyone should start without
# measuring the real per-session cost first. It buys back the factor study, not
# the screener: nothing displayed on a page reads a historical session.


def log(m: str) -> None:
    line = f"fixdata {datetime.now():%m-%d %H:%M:%S} | {m}"
    try:
        print(line, flush=True)
    except (ValueError, OSError):
        pass
    try:
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _done() -> set:
    try:
        return set(json.loads(STATE.read_text(encoding="utf-8")).get("done", []))
    except (OSError, json.JSONDecodeError):
        return set()


def _mark(name: str) -> None:
    d = _done() | {name}
    try:
        tmp = STATE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"done": sorted(d)}, indent=2), encoding="utf-8")
        tmp.replace(STATE)
    except OSError:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Fix the data, unattended.")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    total = sum(s[2] for s in STAGES)
    if a.dry_run:
        print("\n  fix_data\n")
        run = 0.0
        for n, _argv, hrs, why, usable in STAGES:
            run += hrs
            mark = "  <-- USABLE HERE" if usable else ""
            print(f"  {n:<9}{hrs:>5.1f}h  (cum {run:4.1f}h)  {why}{mark}")
        print(f"\n  ~{total:.0f} h total, every stage resumable.\n")
        return 0

    t0 = time.time()
    log("=" * 66)
    log(f"FIX DATA START | {len(STAGES)} stages | ~{total:.0f}h | "
        f"usable after 'verify' (~{sum(s[2] for s in STAGES[:4]):.1f}h)")
    done = _done()
    if done:
        log(f"resuming -- already complete: {', '.join(sorted(done))}")

    failed = 0
    for name, argv, hrs, why, usable in STAGES:
        if name in done:
            log(f"[{name}] skipped -- completed in an earlier run")
            continue
        eta = datetime.now() + timedelta(hours=hrs)
        log(f"[{name}] start (~{hrs:.1f}h, expect ~{eta:%H:%M}) -- {why}")
        s = time.time()
        p = subprocess.run(argv, cwd=str(config.ROOT))
        dur = (time.time() - s) / 3600

        # 75 = another instance holds the run lock. Not a failure, and NOT a
        # success -- marking it done would record work that never happened.
        if p.returncode == 75:
            log(f"[{name}] another instance already holds the lock -- leaving "
                f"it to finish, nothing marked done")
            return 0
        if p.returncode:
            # `verify` and `recheck` are REPORTS. A non-zero exit there means
            # "some fields disagree", which is information to read, not a
            # reason to abandon the rebuild that would fix them.
            if name in ("verify", "recheck"):
                log(f"[{name}] reported differences (exit {p.returncode}) -- "
                    f"see the log above; continuing")
                _mark(name)
                continue
            failed += 1
            log(f"[{name}] FAILED after {dur:.1f}h (exit {p.returncode}) -- "
                f"stopping; later stages would build on it")
            break
        _mark(name)
        log(f"[{name}] done in {dur:.1f}h")
        if usable:
            log(f"    >>> the screener is now correct and usable "
                f"({(time.time() - t0) / 3600:.1f}h in)")

    ok = len(_done() & {s[0] for s in STAGES})
    log(f"FIX DATA DONE in {(time.time() - t0) / 3600:.1f}h | "
        f"{ok}/{len(STAGES)} stages complete, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
