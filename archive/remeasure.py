"""
Re-measure everything against the corrected data, then rebuild and audit.

    python remeasure.py            run the chain
    python remeasure.py --dry-run  stages and their costs

WHY THIS EXISTS
-----------------
On 2026-08-10/11 the fact store was corrected in ways that change what the
metrics ARE, not merely which rows exist:

  * `debt` and `ccc` stopped inventing zeros for filers that report neither
    leg, so `net_debt_ebitda`, `roic`, `eva`, the EV ratios and `ccc` lost the
    30-50% of their values that were fabricated
  * two published quarters that the lagging bulk sets never carried were
    recovered per company, and fiscal Q4 is now derived rather than blank
  * 153 non-USD filers entered the universe on scale-free metrics

The factor study predates all of it: every one of its 1,600 cells was measured
on 2026-08-09. That matters beyond the numbers on the page, because **`combo`
chooses its ingredients from the study** -- so the live composite is assembled
from measurements of data that no longer exists.

ORDER IS LOAD-BEARING
-----------------------
  study    measures the corrected history
  combo    re-scored, because the study just changed which metrics are admitted
  oos      walk-forward on the new weights; its cached train fits are DELETED
           first, since they were fitted on the old data and would otherwise be
           reused silently by `--reuse-train`
  pages    rebuilt so every rendered figure comes from the new tables
  audit    last, so it audits the finished state rather than the previous one

EVERY STAGE IS A SUBPROCESS. The chain runs for hours; running the stages
in-process would pin whatever version of the code was imported at start, which
is how a fix made during the run silently fails to take effect.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime

import config

config.safe_console()

import orchestrator                                              # noqa: E402

LOG = config.DATA / "_remeasure.log"
STATE = config.DATA / "_remeasure_state.json"
LOCK_REFRESH_S = 20 * 60
_stop = threading.Event()


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(st: dict) -> None:
    try:
        tmp = STATE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(st, indent=2), encoding="utf-8")
        tmp.replace(STATE)
    except OSError:
        pass


def log(m: str) -> None:
    line = f"remeas {datetime.now():%m-%d %H:%M:%S} | {m}"
    try:
        print(line, flush=True)
    except (ValueError, OSError):
        pass
    try:
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _refresh_lock() -> None:
    while not _stop.wait(LOCK_REFRESH_S):
        try:
            config.ORCH_LOCK_FILE.write_text(
                json.dumps({"pid": os.getpid(),
                            "started": datetime.now().isoformat(timespec="seconds"),
                            "note": "remeasure.py"}), encoding="utf-8")
        except OSError:
            pass


def _run(args: list[str], tail: int = 25) -> tuple[int, str]:
    """A stage, as its own process. Output is echoed into the chain log."""
    p = subprocess.run([sys.executable, *args], cwd=str(config.ROOT),
                       capture_output=True, text=True)
    for line in (p.stdout or "").splitlines()[-tail:]:
        if line.strip():
            log("  " + line)
    if p.returncode:
        log(f"  stderr: {(p.stderr or '')[-500:]}")
        raise RuntimeError(f"{' '.join(args)} exited {p.returncode}")
    return 1, f"{' '.join(args)} ok"


def _study() -> tuple[int, str]:
    """Re-measure every cell. The old table is ARCHIVED, not deleted.

    `study.py` skips cells it already holds, so the previous table has to move
    aside or the corrected data would never be reflected. Keeping it is what
    makes the before/after comparison possible -- and the comparison is the
    point, since the whole question is whether the fabricated `debt` and `ccc`
    values were holding any of these results up.
    """
    import study
    if study.OUT.exists():
        keep = config.DATA / "_factor_study_pre_datafix.parquet"
        if keep.exists():
            keep.unlink()
        study.OUT.rename(keep)
        log(f"  previous study archived to {keep.name}")
    rc, _ = _run(["study.py"], tail=40)
    import importlib
    importlib.reload(study)
    df = study.read()
    return len(df), f"{len(df):,} cell(s) measured on the corrected data"


def _combo() -> tuple[int, str]:
    """Re-score combo: the study just changed which metrics are admitted."""
    import scores
    scores.load_all()
    n = scores.catchup("combo", every=14, rebuild=True, verbose=True)
    return n, f"{n} session(s) re-scored on the new weights"


def _oos() -> tuple[int, str]:
    """Walk-forward on the new data. Stale train fits are removed first.

    `--reuse-train` would happily reuse fits computed on the fabricated data
    and report them as a fresh result. Deleting the caches is the difference
    between a re-measurement and a replay.
    """
    stale = sorted(config.DATA.glob("_oos_train_*.parquet"))
    for p in stale:
        p.unlink()
    log(f"  removed {len(stale)} cached train fit(s) from the old data")
    return _run(["oos.py", "--walk-forward", "4"], tail=30)


def _pages() -> tuple[int, str]:
    """Rebuild every page, IN-PROCESS unlike the other stages.

    `orchestrator.py` acquires the run lock in its CLI entry point and this
    chain already holds it, so a subprocess would exit with "another run holds
    the lock" and the pages would silently never rebuild. `run()` itself does
    not lock, so calling it directly is the correct call, not a workaround.
    The page modules are imported at this point in the chain -- hours after
    start -- so they still pick up any edit made while it was running.
    """
    rc = orchestrator.run(only=None, force=True)
    return int(rc or 0), f"every page rebuilt (exit {rc})"


STAGES = (
    ("study", _study, "~4.5 h", "re-measure every cell on the corrected data"),
    ("combo", _combo, "~5 min", "re-score combo; the study changed its inputs"),
    ("oos", _oos, "~2.5 h", "4-fold walk-forward, caches cleared first"),
    # IN-PROCESS, unlike the others. `orchestrator.py` acquires the run lock in
    # its CLI entry point, and this chain already holds it -- a subprocess
    # would exit with "another run holds the lock" and the pages would never
    # rebuild. `run()` itself does not lock, so calling it directly is correct
    # rather than a workaround. The page modules are imported at this point in
    # the chain, well after any edit made while it was running.
    ("pages", _pages, "~35 min",
     "rebuild every page so figures come from the new tables"),
    ("audit", lambda: _run(["validate.py"], tail=45), "~6 min",
     "the integrity audit, last, on the finished state"),
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Re-measure on corrected data.")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.dry_run:
        print("\n  remeasure chain\n")
        for n, _f, c, why in STAGES:
            print(f"  {n:<8}{c:>9}   {why}")
        print("\n  ~7.5 h total. Each stage is a separate process.\n")
        return 0

    config.dirs()
    t0 = time.time()
    log("=" * 62)
    log(f"REMEASURE START | {len(STAGES)} stages, ~7.5h expected")
    if not orchestrator.acquire_lock():
        log("another run holds the orchestrator lock; exiting")
        # 75, NOT 0. Returning success here let the wrapper mark this
        # chain complete when it had done nothing at all -- the exact
        # "reports success after failing" pattern. A distinct code lets
        # `fix_all` tell 'finished' from 'someone else is already on it'.
        return 75
    threading.Thread(target=_refresh_lock, daemon=True).start()

    # STAGE-LEVEL RESUME. Without this a reboot eight hours into the chain
    # re-ran the study from the top. `study.py` skips cells it already holds so
    # it recovers on its own, but `combo`, the walk-forward and the page build
    # have no such memory and would each redo in full.
    st = load_state()
    done_stages = set(st.get("done", []))
    if done_stages:
        log(f"resuming: {len(done_stages)} stage(s) already complete "
            f"({', '.join(sorted(done_stages))})")

    failed = 0
    try:
        for name, fn, cost, why in STAGES:
            if name in done_stages:
                log(f"[{name}] skipped -- already done in an earlier run")
                continue
            started = datetime.now()
            log(f"[{name}] start ({cost}) -- {why}")
            s = time.time()
            try:
                rows, detail = fn()
                log(f"[{name}] done in {(time.time() - s) / 60:.1f}m -- {detail}")
                status, err, tb = orchestrator.STATUS_OK, None, None
                done_stages.add(name)
                st["done"] = sorted(done_stages)
                save_state(st)
            except Exception as exc:                             # noqa: BLE001
                failed += 1
                rows, detail = None, None
                status = orchestrator.STATUS_ERROR
                err = f"{type(exc).__name__}: {exc}"[:400]
                tb = traceback.format_exc()[-4000:]
                log(f"[{name}] FAILED after {(time.time() - s) / 60:.1f}m: "
                    f"{repr(exc)[:160]}")
            orchestrator.record({
                "run_id": f"remeasure-{t0:.0f}", "step": f"remeasure/{name}",
                "cadence": "oneoff", "watermark": str(datetime.now().date()),
                "started": started.isoformat(timespec="seconds"),
                "ended": datetime.now().isoformat(timespec="seconds"),
                "duration_s": round(time.time() - s, 1), "status": status,
                "rows": rows if isinstance(rows, int) else None,
                "detail": detail, "error": err, "traceback": tb})
    finally:
        _stop.set()
        orchestrator.release_lock()

    log(f"REMEASURE DONE in {(time.time() - t0) / 3600:.1f}h | "
        f"{len(STAGES) - failed} ok, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as exc:                                 # noqa: BLE001
        try:
            log(f"FATAL {type(exc).__name__}: {exc}")
            orchestrator.release_lock()
        finally:
            sys.exit(2)
