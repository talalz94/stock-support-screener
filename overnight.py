"""
One-off catch-up chain, meant to run unattended with nothing else open.

    python overnight.py             run the whole chain
    python overnight.py --dry-run   list the stages and their measured costs

Launched detached via a one-time Scheduled Task so it survives any terminal or
editor being closed. Expected wall clock ~9h; every stage is resumable, so a
reboot mid-run costs the current stage, not the chain.

WHY A CHAIN AND NOT FIVE TASKS: these stages are ordered by dependency, not by
convenience, and the dependency here is about DATA, not files. Stage 2 adds
~400 sessions to the score store; stage 3 measures that store. Run as separate
tasks they would overlap, and the study would build one results table from two
different datasets -- half its cells against the shallow series, half against
the deep one, with nothing in the output to reveal which.

That is also why stage 1 exists at all: a study was already running when this
chain was written, so the chain waits it out rather than racing it.

IT HOLDS THE ORCHESTRATOR LOCK for its whole duration, refreshed on a timer, so
the scheduled orchestrator sees a live lock and exits cleanly rather than
fighting it for the same files. If this process dies, refreshes stop; psutil is
installed, so the next orchestrator run detects the dead pid and breaks the lock
immediately rather than waiting out ORCH_LOCK_STALE_HOURS.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import traceback
from datetime import datetime

import config                                                    # noqa: E402

config.safe_console()

import orchestrator                                              # noqa: E402

LOCK_REFRESH_S = 20 * 60      # well inside ORCH_LOCK_STALE_HOURS (6h)
_stop_refresh = threading.Event()


def log(msg: str) -> None:
    line = f"night {datetime.now():%Y-%m-%d %H:%M:%S} | {msg}"
    try:
        print(line, flush=True)
    except (ValueError, OSError):
        pass
    try:
        with config.LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _refresh_lock() -> None:
    """Keep the lock warm so a 6h stage does not look abandoned."""
    while not _stop_refresh.wait(LOCK_REFRESH_S):
        try:
            config.ORCH_LOCK_FILE.write_text(
                json.dumps({"pid": os.getpid(),
                            "started": datetime.now().isoformat(timespec="seconds"),
                            "note": "overnight.py chain"}),
                encoding="utf-8")
        except OSError:
            pass


# ------------------------------------------------------------------- stages
def _wait_study() -> tuple[int, str]:
    """Block until any already-running `study.py` has finished.

    THIS STAGE IS THE WHOLE REASON THE CHAIN IS ORDERED. The next stage adds
    ~400 sessions to the score store, and the study reads that store. Letting
    them overlap would mean cells measured early in the study run see a shallow
    series and cells measured late see a deep one -- a single results table
    silently built from two different datasets, with nothing to reveal it.
    """
    import psutil
    waited = 0
    while True:
        alive = []
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                cmd = " ".join(p.info.get("cmdline") or [])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if "study.py" in cmd and p.info["pid"] != os.getpid():
                alive.append(p.info["pid"])
        if not alive:
            return waited, (f"no study running; waited {waited / 60:.0f} min"
                            if waited else "no study was running")
        if waited % 600 == 0:
            log(f"  waiting for study pid {alive} ({waited / 60:.0f} min so far)")
        time.sleep(60)
        waited += 60


def _score_catchup() -> tuple[int, str]:
    """Bring sentiment, hype and dip up to the fundamental series' depth.

    `fundamental` is deliberately NOT in the list: it already has 182 dates, and
    the every=14 grid does not line up with the dates it already holds, so
    including it would add ~180 near-duplicate sessions and hours of compute to
    the one module that needs it least.
    """
    import catchup_scores
    rows = catchup_scores.plan(["sentiment", "hype", "dip"], 14, None, False)
    built = 0
    import scores as _s
    _s.load_all()
    for mod, start, _total, new in rows:
        if not new:
            continue
        log(f"  [{mod}] {new} session(s) from {start}")
        built += _s.catchup(mod, every=14, frm=start, verbose=True)
    return built, f"{built} session(s) written across sentiment/hype/dip"


def _combo_catchup() -> tuple[int, str]:
    """Build the combo series, AFTER its inputs and BEFORE the study.

    Order is load-bearing in both directions. combo reads the other four
    modules, so it cannot run before `score_catchup`. And the study can only
    measure a module that has a stored series, so it cannot run before this --
    otherwise `combo_h20` would be a score nobody had tested, which is the
    one thing this project does not ship.

    A caveat that has to be stated rather than buried: combo's weights come
    from a study measured over the WHOLE history, so measuring combo on that
    same history is IN-SAMPLE. Expect it to look good; that is not yet
    evidence. `validate` reports the split-sample check.
    """
    import scores as _s
    _s.load_all()
    n = _s.catchup("combo", every=14, frm="2022-12-01", verbose=True)
    return n, (f"{n} session(s); floor 2022-12 because combo reads dip, "
               f"which needs the sentiment series")


def _study_fresh() -> tuple[int, str]:
    """Re-measure EVERY cell against the deeper series.

    The study skips cells it has already stored, so the previous table has to be
    moved aside or the deeper data would never be reflected. It is kept, not
    deleted: the before/after comparison is the evidence that the backfill
    changed a verdict, and `dip` at 43 dates is the verdict most likely to move.
    """
    import study
    if study.OUT.exists():
        keep = config.DATA / "_factor_study_pre_backfill.parquet"
        try:
            if keep.exists():
                keep.unlink()
            study.OUT.rename(keep)
            log(f"  previous study archived to {keep.name}")
        except OSError as exc:
            return 0, f"could not archive the old study ({exc!r}); refusing to run"
    rc = study.run()
    df = study.read()
    return len(df), f"{len(df):,} cell(s) measured (exit {rc})"


def _exits() -> tuple[int, str]:
    """The oldest deferred question: is the bounce move capturable at all?

    MFE +9.2% against MAE -11.0% says the favourable excursion exists but is
    given back by holding. `trail_1` was added for this run; `hold_3` and
    `bracket_1x1.5` already existed.
    """
    # A subprocess rather than an import: `backtest.main()` parses sys.argv,
    # which here belongs to the chain, and its --exits path prints a table this
    # stage wants captured into the run log verbatim.
    import subprocess
    p = subprocess.run([sys.executable, "backtest.py", "--exits",
                        "--start", "2024-02", "--every", "3"],
                       cwd=str(config.ROOT), capture_output=True, text=True)
    for line in (p.stdout or "").splitlines()[-45:]:
        log("  " + line)
    if p.returncode:
        log(f"  backtest stderr: {(p.stderr or '')[-400:]}")
        raise RuntimeError(f"backtest --exits exited {p.returncode}")
    return 1, "exit-rule comparison written to the run log"


def _validate() -> tuple[int, str]:
    """The integrity audit, captured into the run log line by line.

    A subprocess so its non-zero exit is visible as a stage failure rather than
    something the chain has to remember to check, and so its whole report lands
    in the log for reading later rather than only a summary count.
    """
    import subprocess
    p = subprocess.run([sys.executable, "validate.py"],
                       cwd=str(config.ROOT), capture_output=True, text=True)
    for line in (p.stdout or "").splitlines():
        if line.strip():
            log("  " + line)
    if p.returncode:
        log(f"  validate stderr: {(p.stderr or '')[-400:]}")
        # Raised, so the chain's own summary records a failure. A dirty audit
        # that exits 0 is exactly the "reports success after failing" pattern.
        raise RuntimeError(f"validate.py exited {p.returncode} -- see the audit above")
    return 1, "integrity audit clean"


def _orch(only, force, label) -> tuple[int, str]:
    rc = orchestrator.run(only=only, force=force)
    return rc, f"{label} (exit {rc})"


STAGES = [
    ("wait_study", _wait_study, "~25 min",
     "let the running study finish -- the next stage changes the data it reads"),
    ("score_catchup", _score_catchup, "~2.6 h",
     # 3.95 bytes/row measured on the existing store x rows-per-session today.
     # An upper bound: 2016-2022 sessions carry fewer names with news.
     "sentiment/hype/dip to 2016 at every=14; ~408 sessions, <=90 MB"),
    ("combo_catchup", _combo_catchup, "~12 min",
     "the three combined scores, back to 2022-12, so the study can test them"),
    ("study_fresh", _study_fresh, "~5 h",
     "re-measure all cells on the deeper series; dip's 43-date verdict may move"),
    ("exits", _exits, "~40 min",
     "hold_3 / trail_1 / bracket_1x1.5 -- is the bounce move capturable?"),
    ("catch_up_all", lambda: _orch(None, False, "full orchestrator pass"),
     "~35 min", "rebuild every page and regenerate the docs on the new data"),
    # LAST, deliberately: an audit that runs before the rebuild would be
    # auditing the previous state and reporting it as the current one.
    ("validate", _validate, "~6 min",
     "the integrity audit -- duplicates, look-ahead, dead feeds, currency"),
]


def dry_run() -> int:
    print("\n  overnight chain -- stages in order\n")
    for name, _fn, cost, why in STAGES:
        print(f"  {name:<18}{cost:>9}   {why}")
    print("\n  total ~9 h. Every stage is resumable, so a reboot costs the stage, not the chain.\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Unattended catch-up chain.")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.dry_run:
        return dry_run()

    config.dirs()
    t0 = time.time()
    log("=" * 62)
    log(f"OVERNIGHT CHAIN START | {len(STAGES)} stage(s), ~9h expected")

    if not orchestrator.acquire_lock():
        log("another run holds the lock; exiting without work")
        return 0

    threading.Thread(target=_refresh_lock, daemon=True).start()

    failed = 0
    try:
        for name, fn, cost, why in STAGES:
            started = datetime.now()
            log(f"[{name}] start ({cost} expected) -- {why}")
            s = time.time()
            try:
                rows, detail = fn()
                dur = time.time() - s
                log(f"[{name}] done in {dur / 60:.1f}m -- {detail}")
                status, err, tb = orchestrator.STATUS_OK, None, None
            except Exception as exc:                             # noqa: BLE001
                dur = time.time() - s
                failed += 1
                rows, detail = None, None
                status = orchestrator.STATUS_ERROR
                err = f"{type(exc).__name__}: {exc}"[:400]
                tb = traceback.format_exc()[-4000:]
                # Same discipline as the orchestrator: record and continue. A
                # failed news backfill must not cost the recalibration behind it.
                log(f"[{name}] FAILED after {dur / 60:.1f}m: {repr(exc)[:140]}")

            orchestrator.record({
                "run_id": f"overnight-{t0:.0f}", "step": f"overnight/{name}",
                "cadence": "overnight", "watermark": str(datetime.now().date()),
                "started": started.isoformat(timespec="seconds"),
                "ended": datetime.now().isoformat(timespec="seconds"),
                "duration_s": round(dur, 1), "status": status,
                "rows": rows if isinstance(rows, int) else None,
                "detail": detail, "error": err, "traceback": tb})
    finally:
        _stop_refresh.set()
        orchestrator.release_lock()
        try:
            import dashboard
            dashboard.build(verbose=False)
        except Exception:                                        # noqa: BLE001
            pass

    log(f"OVERNIGHT CHAIN DONE in {(time.time() - t0) / 3600:.1f}h | "
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
            for ln in traceback.format_exc().splitlines()[-12:]:
                log("  " + ln)
            orchestrator.release_lock()
        finally:
            sys.exit(2)
