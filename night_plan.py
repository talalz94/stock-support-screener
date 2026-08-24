#!/usr/bin/env python
"""Run the night unattended: finish the SEC fetch, verify it, then score early.

Written to be started detached and left alone. Nobody is watching a terminal, so
everything lands in a log and a one-page summary instead.

THE HARD RULE IS THE DEADLINE. The scheduled run fires at 05:00 and takes the
orchestrator lock. A job still holding that lock at 05:00 does not collide
loudly -- the scheduled run finds the lock held, logs a perfectly normal
message, and silently does nothing for the day. That has already cost this
project half a day once. So a phase is STARTED only if its measured cost fits
in the time left, and every subprocess also carries the deadline as a hard
timeout, because not-starting is a plan and being-killed is the backstop.

Usage:
    python night_plan.py [--wait-pid PID] [--deadline HH:MM] [--dry-run]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable

# Measured, with margin. Used ONLY to decide whether a phase fits before the
# deadline -- never to cut a phase short once it has started.
#
# NIGHTLY_MIN is the whole daily chain: 273 min of measured steps, called 330.
# HYPE_MIN is one forced hype run (median 93, last 154, worst 847) -- generous,
# because the cost of overrunning is the 05:00 run finding the lock held.
#
# There is deliberately NO forced-fundamental phase. Forcing it re-scores the
# whole universe: 2026-08-23 measured 38,020s -- 10.6 HOURS -- which cannot fit
# in a night. Fundamental gets scored the cheap way instead, as part of the
# normal chain once `bars` advances the session.
NIGHTLY_MIN = 330
HYPE_MIN = 200
VERIFY_MIN = 30

_lines: list[str] = []


def log(msg: str) -> None:
    line = f"night  {datetime.now():%H:%M:%S} | {msg}"
    print(line, flush=True)
    _lines.append(line)


def _alive(pid: int) -> bool:
    """True while `pid` is running. tasklist, because os.kill(pid, 0) on Windows
    reports processes we do not own inconsistently."""
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                             capture_output=True, text=True, timeout=60)
        return str(pid) in (out.stdout or "")
    except Exception:                                            # noqa: BLE001
        return False


def wait_for(pid: int, deadline: datetime) -> str:
    if not pid or not _alive(pid):
        return "not running"
    log(f"waiting for pid {pid} to finish (the SEC fetch)")
    while _alive(pid):
        if datetime.now() >= deadline:
            return "deadline reached while waiting"
        time.sleep(60)
    return "finished"


def run(name: str, args: list[str], deadline: datetime) -> dict:
    """Run one command, record how it went. Never raises."""
    left = (deadline - datetime.now()).total_seconds()
    if left <= 0:
        log(f"[{name}] SKIPPED -- past the deadline")
        return {"step": name, "status": "skipped", "reason": "past deadline"}

    log(f"[{name}] start")
    t0 = time.time()
    try:
        p = subprocess.run([PY, "-u", *args], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=left)
        mins = (time.time() - t0) / 60
        tail = [ln for ln in (p.stdout or "").splitlines() if ln.strip()][-4:]
        status = "ok" if p.returncode == 0 else f"exit {p.returncode}"
        log(f"[{name}] {status} in {mins:.0f}m")
        for ln in tail:
            log(f"[{name}]   {ln[:150]}")
        return {"step": name, "status": status, "minutes": round(mins, 1),
                "tail": tail}
    except subprocess.TimeoutExpired:
        mins = (time.time() - t0) / 60
        log(f"[{name}] TIMED OUT at the deadline after {mins:.0f}m")
        return {"step": name, "status": "timeout", "minutes": round(mins, 1)}
    except Exception as exc:                                     # noqa: BLE001
        log(f"[{name}] CRASHED: {repr(exc)[:120]}")
        return {"step": name, "status": "crashed", "error": repr(exc)[:200]}


def summarise(results: list[dict], deadline: datetime, out: Path) -> None:
    ok = [r for r in results if r.get("status") == "ok"]
    bad = [r for r in results if r.get("status") not in ("ok", "skipped")]
    skipped = [r for r in results if r.get("status") == "skipped"]

    body = ["=" * 70,
            f"NIGHT RUN SUMMARY   {datetime.now():%Y-%m-%d %H:%M}",
            f"deadline {deadline:%H:%M} -- the 05:00 run must find the lock free",
            "=" * 70, ""]
    if bad:
        body.append(f"!! {len(bad)} STEP(S) DID NOT SUCCEED -- read these first:")
        for r in bad:
            body.append(f"   {r['step']:26} {r['status']}")
            for ln in r.get("tail", [])[-2:]:
                body.append(f"       {ln[:130]}")
        body.append("")
    else:
        body.append("Every step that ran, succeeded.")
        body.append("")

    body.append(f"{len(ok)} ok, {len(bad)} failed, {len(skipped)} skipped for time")
    body.append("")
    for r in results:
        mins = f"{r['minutes']:.0f}m" if "minutes" in r else "-"
        body.append(f"  {r['step']:26} {r['status']:12} {mins:>6}")
    body += ["", "-" * 70,
             "The verification gate is the part that matters: the regression pins",
             "are 28 values hand-checked against real filings. If they still pass,",
             "the new SEC data did not move anything already verified.",
             "-" * 70, "", "FULL LOG:", ""] + _lines

    out.write_text("\n".join(body), encoding="utf-8")
    print("\n".join(body[:26]), flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wait-pid", type=int, default=0,
                    help="pid of an already-running job to wait for")
    ap.add_argument("--deadline", default="04:30",
                    help="HH:MM local. Nothing new starts after this.")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and the time budget, run nothing")
    a = ap.parse_args()

    hh, mm = (int(x) for x in a.deadline.split(":"))
    now = datetime.now()
    deadline = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if deadline <= now:
        deadline += timedelta(days=1)

    log(f"night plan starting; deadline {deadline:%Y-%m-%d %H:%M} "
        f"({(deadline - now).total_seconds() / 3600:.1f}h from now)")

    if a.dry_run:
        left = (deadline - now).total_seconds() / 60
        after = left - VERIFY_MIN
        print(f"\n  time available        {left:.0f} min")
        print(f"  verification gate     {VERIFY_MIN} min")
        print(f"  full nightly chain    {NIGHTLY_MIN} min   "
              f"{'FITS' if after >= NIGHTLY_MIN else 'DOES NOT FIT'}")
        print(f"  extra hype backfill   {HYPE_MIN} min   "
              f"{'FITS' if after - NIGHTLY_MIN >= HYPE_MIN else 'no room'}")
        return 0

    results: list[dict] = []

    # ---- phase 1: let the running fetch finish, so nothing reads a store that
    # is being rewritten underneath it.
    if a.wait_pid:
        why = wait_for(a.wait_pid, deadline)
        log(f"pid {a.wait_pid}: {why}")
        results.append({"step": f"wait for pid {a.wait_pid}",
                        "status": "ok" if why in ("finished", "not running")
                        else "timeout"})

    # ---- phase 2: the verification gate, BEFORE any scoring. There is no point
    # spending the night writing sessions on top of a store that just broke.
    pins = run("regression pins", ["regression_pins.py"], deadline)
    results.append(pins)
    results.append(run("bounce selftest", ["report.py", "--selftest"], deadline))
    results.append(run("screen audit", ["validate.py", "--only", "screen"], deadline))

    # Named, not results[-3]: an index into a list other phases also append to
    # points at the wrong step the moment a phase is added above it.
    if pins.get("status") != "ok":
        log("PINS DID NOT PASS -- skipping all scoring. Writing more sessions on "
            "top of a store whose verified values moved would multiply the "
            "problem, and the morning needs a clean signal, not more data.")
        results.append({"step": "scoring", "status": "skipped",
                        "reason": "regression pins failed"})
    else:
        # ---- phase 3: run the ordinary nightly, EARLY.
        #
        # Not forced, not a single --step. Today's 05:00 run skipped both
        # fundamental and hype with "already ran for 2026-08-21", because `asof`
        # had not moved past Friday -- the market had not closed when it fired.
        # Running the plain chain now lets `bars` pull the session that has since
        # closed, which advances `asof` and lets every module score normally
        # instead of skipping. That is also the CHEAP path: scoring a NEW session
        # costs fundamental ~100 min, where forcing a re-score of an
        # already-scored one costs ten hours for the same information.
        left = (deadline - datetime.now()).total_seconds() / 60
        if left < NIGHTLY_MIN:
            log(f"{left:.0f} min left, the chain needs {NIGHTLY_MIN} -- skipping "
                f"it rather than risk still holding the lock at 05:00")
            results.append({"step": "nightly chain", "status": "skipped",
                            "reason": f"only {left:.0f} min left"})
        else:
            log(f"{left:.0f} min left -- running the full nightly chain now")
            results.append(run("nightly chain",
                               ["orchestrator.py", "--once",
                                "--wait-for-lock", "45"], deadline))

            # ---- phase 4: one extra hype session if the night still has room.
            # Each module backfills at most one missed session per run, so a
            # second pass is the only way to close a second gap.
            left = (deadline - datetime.now()).total_seconds() / 60
            if left >= HYPE_MIN:
                log(f"{left:.0f} min left -- one more hype session")
                results.append(run("extra hype backfill",
                                   ["orchestrator.py", "--force",
                                    "--wait-for-lock", "20", "--step", "hype"],
                                   deadline))
            else:
                log(f"{left:.0f} min left -- no room for another session, stopping")

    out = ROOT / "data" / f"_night_summary_{datetime.now():%Y%m%d}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    summarise(results, deadline, out)
    log(f"summary written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
