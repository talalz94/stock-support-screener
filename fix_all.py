"""
One command that finishes everything outstanding, unattended.

    python fix_all.py            run it
    python fix_all.py --dry-run  costs only

Two chains, in the only order that works:

  1. rebuild_history.py  recompute every stored session on the corrected metric
                         definitions -- fundamental, hype, dip, combo
  2. remeasure.py        re-measure the factor study on that corrected history,
                         re-score combo on the new admissions, redo the
                         walk-forward, rebuild the pages, audit

WHY BOTH, AND WHY IN THIS ORDER
---------------------------------
The study measures the stored series. Running it against history that still
holds the old definitions produces a correct measurement of numbers that no
longer exist -- which is exactly the loop this project has already been round
twice. History first, always.

WHAT CHANGED THE DEFINITIONS (2026-08-11/12)
----------------------------------------------
  `pe` / `pb` / `ev_ebitda`  no longer emit a value on a negative denominator.
      They rank lower-is-better, so a negative multiple sorted as the cheapest
      thing in the market: 932 of 2,918 filers (32%) carried a negative P/E and
      the loss-makers earned a mean value rank of 0.84 against 0.34 for
      profitable companies. Redwire, unprofitable every year since 2021, scored
      0.85 on "cheapness".

  `share_count`  now rejects counts below MIN_SHARES. Some filers tag the
      cover-page `shares_out` as a placeholder 1, 10 or 100, and since
      `shares_out` is preferred first those beat a real weighted-average count
      in the same frame -- FBYD resolved to 10 shares while carrying 39,255,880
      diluted. HQ's market cap read $14.13, which made its turnover 414,549x
      and fed every valuation ratio, Altman Z and the study's size buckets.

Both chains are resumable and each stage records itself, so a reboot costs the
stage in flight rather than the run.
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import time
from datetime import datetime

import config

config.safe_console()

LOG = config.DATA / "_fix_all.log"
STATE = config.DATA / "_fix_all_state.json"


def _done() -> set:
    """Chains already finished, so a restart continues rather than repeats.

    Both inner chains resume on their own, but without this the wrapper would
    still re-enter a finished one -- harmless but slow, and on a laptop that
    reboots twice it turns a long job into an endless one.
    """
    import json
    try:
        return set(json.loads(STATE.read_text(encoding="utf-8")).get("done", []))
    except (OSError, json.JSONDecodeError):
        return set()


def _mark(name: str) -> None:
    import json
    d = _done() | {name}
    try:
        tmp = STATE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"done": sorted(d)}, indent=2), encoding="utf-8")
        tmp.replace(STATE)
    except OSError:
        pass

CHAINS = (
    ("history", "rebuild_history.py", "~9.0 h",
     "recompute every stored session on the corrected definitions"),
    ("remeasure", "remeasure.py", "~9.0 h",
     "study, combo, walk-forward, pages, audit -- on that history"),
)


def log(m: str) -> None:
    line = f"fixall {datetime.now():%m-%d %H:%M:%S} | {m}"
    try:
        print(line, flush=True)
    except (ValueError, OSError):
        pass
    try:
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Rebuild history, then re-measure.")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.dry_run:
        print("\n  fix_all\n")
        for n, script, cost, why in CHAINS:
            print(f"  {n:<11}{cost:>8}   {script:<20} {why}")
        print("\n  ~18 h total. Both chains resumable.\n")
        return 0

    t0 = time.time()
    log("=" * 62)
    log("FIX ALL START | history rebuild, then re-measure | ~18h")
    done = _done()
    if done:
        log(f"resuming: {', '.join(sorted(done))} already complete")
    failed = 0
    for name, script, cost, why in CHAINS:
        if name in done:
            log(f"[{name}] skipped -- completed in an earlier run")
            continue
        log(f"[{name}] start ({cost}) -- {why}")
        s = time.time()
        # A SUBPROCESS PER CHAIN: each picks up the current code rather than
        # whatever was imported when this started, and a crash in one is
        # contained instead of taking the other down with it.
        p = subprocess.run([sys.executable, script], cwd=str(config.ROOT))
        dur = (time.time() - s) / 3600
        # 75 = "another instance already holds the run lock". NOT a failure and
        # emphatically NOT a success: marking the chain done here would record
        # work that never happened, which is how a resume trigger firing during
        # a live run could quietly skip the entire rebuild.
        if p.returncode == 75:
            log(f"[{name}] another instance is already running this -- "
                f"leaving it to finish, nothing marked done")
            return 0
        if p.returncode:
            failed += 1
            log(f"[{name}] FAILED after {dur:.1f}h (exit {p.returncode}) -- "
                f"stopping; the next chain would build on it")
            break
        _mark(name)
        log(f"[{name}] done in {dur:.1f}h")

    # REMOVE THE RESUME HOOK once there is nothing left to resume. It lives in
    # the Startup folder so a reboot picks the job back up; leaving it there
    # afterwards would launch a pointless python process at every login for
    # ever. Only removed when BOTH chains are recorded complete.
    if not failed and _done() >= {c[0] for c in CHAINS}:
        try:
            import os
            hook = pathlib.Path(os.environ.get("APPDATA", "")) / (
                r"Microsoft\Windows\Start Menu\Programs\Startup"
                r"\Screener-Resume.bat")
            if hook.exists():
                hook.unlink()
                log(f"removed the startup resume hook -- nothing left to resume")
        except Exception as exc:                                 # noqa: BLE001
            log(f"could not remove the resume hook ({exc!r}); harmless, "
                f"delete it by hand from shell:startup")

    log(f"FIX ALL DONE in {(time.time() - t0) / 3600:.1f}h | "
        f"{len(CHAINS) - failed} ok, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
