"""
Recompute the stored score history on the corrected metric definitions.

    python rebuild_history.py              run the chain
    python rebuild_history.py --dry-run    list the stages and their measured cost
    python rebuild_history.py --only dip   one module

WHY THIS EXISTS
-----------------
Two metric families were publishing invented numbers, fixed 2026-08-10:

  `debt`  was `debt_lt.fillna(0) + debt_st.fillna(0)`, so a filer that tagged
          no debt line read as debt-FREE rather than unknown. 42% of tradeable
          names tag no debt line; 849 of them report liabilities above 30% of
          assets, so they plainly owe money. It flowed into net_debt_ebitda,
          into EV for ev_ebitda / ev_sales / fcf_yield, and into
          invested_capital for roic / eva / wacc.

  `ccc`   summed three `.fillna(0)` legs, so a filer reporting none of
          inventory, receivables or payables scored 0 days -- and since lower
          is better, no-data companies outranked disclosed ones 62 to 25.

Only the newest session was recomputed when the fix landed. Every older
session still holds the invented values, which makes the history a mix of two
different metric definitions with nothing in the data to say which is which.

EXACT STORED SESSIONS, NOT AN `every=N` GRID
----------------------------------------------
`scores.catchup(rebuild=True)` walks a sampling grid, so it would recompute the
sessions the grid happens to land on and leave the rest on the old basis --
this project already paid for that once, when a sentiment rebuild covered 178
of 320 stored sessions and the comparison drawn from it was meaningless. Here
the todo list IS `sessions_stored(module)`, and a completeness check at the end
refuses to call the stage done if any session was missed.

ORDER IS LOAD-BEARING
-----------------------
`dip` reads fundamental's stored rows for the same session, and `combo` reads
all four modules, so they must follow. Running them in parallel would build
`combo` from a mix of corrected and uncorrected inputs.

THE STUDY IS DELIBERATELY NOT IN THIS CHAIN. Re-measuring every factor cell is
~4.5 h and it answers "does this predict", which is a different question from
"is the stored data correct". Run `python study.py` separately if that becomes
the goal again.
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

import config

config.safe_console()

import orchestrator                                              # noqa: E402

STATE = config.DATA / "_rebuild_state.json"
LOG = config.DATA / "_rebuild.log"
LOCK_REFRESH_S = 20 * 60

# (module, measured seconds per session). Measured 2026-08-10 on the median
# stored session, not estimated.
# Re-measured 2026-08-12 after the share-count and negative-multiple
# fixes. `hype` is here now because it computes market cap itself, so the
# placeholder share counts distorted its turnover and ps_ratio too.
MODULES = (("fundamental", 65.8), ("hype", 64.3), ("dip", 2.1),
           ("combo", 1.5))

_stop_refresh = threading.Event()


def log(m: str) -> None:
    line = f"rebuild {datetime.now():%H:%M:%S} | {m}"
    try:
        print(line, flush=True)
    except (ValueError, OSError):
        pass
    try:
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


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


def _refresh_lock() -> None:
    while not _stop_refresh.wait(LOCK_REFRESH_S):
        try:
            config.ORCH_LOCK_FILE.write_text(
                json.dumps({"pid": os.getpid(),
                            "started": datetime.now().isoformat(timespec="seconds"),
                            "note": "rebuild_history.py"}), encoding="utf-8")
        except OSError:
            pass


def rebuild_module(module: str, st: dict) -> tuple[int, str]:
    """Recompute every session already stored for `module`. Resumable."""
    import bars
    import scores
    scores.load_all()

    stored = list(scores.sessions_stored(module))
    if not stored:
        return 0, "nothing stored; nothing to rebuild"
    done = set(st.get("done", {}).get(module, []))
    todo = [s for s in stored if s not in done]
    log(f"[{module}] {len(stored)} stored session(s), {len(done)} already "
        f"rebuilt, {len(todo)} to go")

    mod = scores.get(module)
    t0, written, failed = time.time(), 0, []
    for i, d in enumerate(todo, 1):
        try:
            uni = bars.tradeable_universe(d)
            rows = mod.compute(d, uni)
            if rows is None or rows.empty:
                failed.append(d)
            else:
                scores.write(rows, session=d, module=module)
                written += 1
                done.add(d)
        except Exception as exc:                                 # noqa: BLE001
            # Named, never swallowed: a skipped session leaves a hole that is
            # indistinguishable from a quiet market later on.
            failed.append(d)
            log(f"    {d} FAILED {type(exc).__name__}: {exc}"[:150])
        if i % 10 == 0 or i == len(todo):
            st.setdefault("done", {})[module] = sorted(done)
            save_state(st)
            el = (time.time() - t0) / 60
            log(f"    [{module}] {i}/{len(todo)}  {el:.1f}m  "
                f"eta {el / i * (len(todo) - i):.0f}m")

    st.setdefault("done", {})[module] = sorted(done)
    save_state(st)

    # COMPLETENESS. A rebuild that quietly covered 80% of the history would
    # leave the store a blend of two metric definitions -- the exact failure
    # this script exists to end -- so it is checked rather than assumed.
    missing = [s for s in stored if s not in done]
    if missing:
        raise RuntimeError(
            f"{module}: {len(missing)} of {len(stored)} session(s) not rebuilt "
            f"(e.g. {missing[:4]}); the history is still mixed")
    return written, (f"{written} session(s) rewritten, all {len(stored)} "
                     f"verified on the corrected definitions"
                     + (f"; {len(failed)} produced no rows" if failed else ""))


def _facts_refresh() -> tuple[int, str]:
    """Pull the quarters SEC has published but the bulk data sets have not.

    THIS MUST RUN BEFORE THE REBUILD, and getting that order wrong is exactly
    the mistake that cost a 3-hour pass. Rebuilding history against a fact
    store that is two quarters behind produces a correct recomputation of stale
    inputs, which then has to be done again.

    Measured 2026-08-10: the newest bulk data set was 2026q1, so 94% of the
    universe had nothing newer than 2025-12-31 -- while SEC's companyfacts API
    already served Apple's 2026-03-28 and 2026-06-27 quarters. `coverage_gap`
    could not see it because it asks who has NO facts, and these names had
    facts; they had simply stopped updating.
    """
    import fundamentals as FD
    targets = FD.refresh_targets()
    gap = len(FD.coverage_gap())
    if not targets:
        return 0, "nothing missing or stale"
    log(f"  fetching {len(targets):,} companies "
        f"({gap} with no facts, {len(targets) - gap} stale)")
    res = FD.backfill_companyfacts(tickers=targets, verbose=True)
    left = FD.stale_names()
    if not res.get("ok"):
        raise RuntimeError(f"companyfacts failed for "
                           f"{len(res.get('failed', []))} company(ies)")
    return int(res.get("companies", 0) or 0), (
        f"{res.get('companies', 0)} refreshed, {res.get('facts', 0):,} facts; "
        f"{len(left)} name(s) still stale afterwards")


def _pages() -> tuple[int, str]:
    rc = orchestrator.run(only=None, force=False)
    return rc, f"page rebuild (exit {rc})"


def _validate() -> tuple[int, str]:
    import subprocess
    p = subprocess.run([sys.executable, "validate.py"], cwd=str(config.ROOT),
                       capture_output=True, text=True)
    for line in (p.stdout or "").splitlines():
        if line.strip():
            log("  " + line)
    if p.returncode:
        raise RuntimeError(f"validate.py exited {p.returncode}")
    return 1, "integrity audit clean"


def main() -> int:
    ap = argparse.ArgumentParser(description="Rebuild score history on the "
                                             "corrected metric definitions.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", metavar="MODULE")
    ap.add_argument("--fresh", action="store_true",
                    help="ignore saved progress and redo every session")
    a = ap.parse_args()

    mods = [(m, c) for m, c in MODULES if not a.only or m == a.only]
    if a.dry_run:
        import scores
        scores.load_all()
        print("\n  rebuild chain -- stages in order\n")
        total = 0.0
        for m, cost in mods:
            n = len(scores.sessions_stored(m))
            total += n * cost / 60
            print(f"  {m:<14}{n:>4} session(s) x {cost:>5.1f}s = "
                  f"{n * cost / 60:>6.1f} min")
        print(f"  {'pages':<14}{'':>4}              ~  35.0 min")
        print(f"  {'validate':<14}{'':>4}              ~   6.0 min")
        print(f"\n  total ~{total + 41:.0f} min. Resumable: progress is saved "
              f"every 10 sessions.\n")
        return 0

    config.dirs()
    if a.fresh and STATE.exists():
        STATE.unlink()
    st = load_state()
    t0 = time.time()
    log("=" * 60)
    log(f"REBUILD START | {len(mods)} module(s) + pages + audit")

    if not orchestrator.acquire_lock():
        log("another run holds the orchestrator lock; exiting without work")
        # 75, NOT 0. Returning success here let the wrapper mark this
        # chain complete when it had done nothing at all -- the exact
        # "reports success after failing" pattern. A distinct code lets
        # `fix_all` tell 'finished' from 'someone else is already on it'.
        return 75
    threading.Thread(target=_refresh_lock, daemon=True).start()

    failed = 0
    stages = [(m, (lambda m=m: rebuild_module(m, st))) for m, _ in mods]
    if not a.only:
        # facts FIRST -- see _facts_refresh. Rebuilding history against a stale
        # fact store just means rebuilding it twice.
        stages = [("facts_refresh", _facts_refresh)] + stages
        stages += [("pages", _pages), ("validate", _validate)]
    try:
        for name, fn in stages:
            started = datetime.now()
            log(f"[{name}] start")
            s = time.time()
            try:
                rows, detail = fn()
                log(f"[{name}] done in {(time.time() - s) / 60:.1f}m -- {detail}")
                status, err, tb = orchestrator.STATUS_OK, None, None
            except Exception as exc:                             # noqa: BLE001
                failed += 1
                rows, detail = None, None
                status = orchestrator.STATUS_ERROR
                err = f"{type(exc).__name__}: {exc}"[:400]
                tb = traceback.format_exc()[-4000:]
                log(f"[{name}] FAILED after {(time.time() - s) / 60:.1f}m: "
                    f"{repr(exc)[:140]}")
            orchestrator.record({
                "run_id": f"rebuild-{t0:.0f}", "step": f"rebuild/{name}",
                "cadence": "oneoff", "watermark": str(datetime.now().date()),
                "started": started.isoformat(timespec="seconds"),
                "ended": datetime.now().isoformat(timespec="seconds"),
                "duration_s": round(time.time() - s, 1), "status": status,
                "rows": rows if isinstance(rows, int) else None,
                "detail": detail, "error": err, "traceback": tb})
    finally:
        _stop_refresh.set()
        orchestrator.release_lock()

    log(f"REBUILD DONE in {(time.time() - t0) / 60:.0f}m | "
        f"{len(stages) - failed} ok, {failed} failed")
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
