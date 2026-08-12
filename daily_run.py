"""
THE daily entrypoint. One shot: runs, writes the report, exits.

    python daily_run.py             the normal run (~60-90s)
    python daily_run.py --dry-run   show what it would do, touch nothing
    python daily_run.py --pause     stop the scheduled task doing work
    python daily_run.py --resume    start it again
    python daily_run.py --once      run now even if paused
    python daily_run.py --no-confirm    skip the hourly/market-cap stage

No daemon, no polling loop, no resident process -- Task Scheduler starts it, it
exits, and there is zero footprint until tomorrow.

Step order is fixed and load-bearing:

    bars -> screen -> confirm -> report(+state) -> outcomes -> _state.json

State advances only after the artifacts it describes exist on disk, so a crash
always leaves a consistent store with a stale-but-truthful state file, never the
reverse. `_state.json` is written at BOTH ends of the run so a hard crash still
records that a run was attempted.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import date, datetime

import config                                                   # noqa: E402

# Must come before anything that prints. Guarded inside, because under Task
# Scheduler with pythonw.exe there is no console handle and a bare
# sys.stdout.reconfigure() raises before the first log line -- which is exactly
# how this task failed its first scheduled run (LastTaskResult 1, empty log).
config.safe_console()

import bars                                                     # noqa: E402
import calendar_us                                              # noqa: E402
import screen                                                   # noqa: E402
import store                                                    # noqa: E402
import universe                                                 # noqa: E402


def log(msg: str) -> None:
    """Tee to stdout and the run log. Cannot raise.

    The log file is the ONLY record of a scheduled run, so a failure to write it
    must never abort the run -- but equally, a run that dies with an empty log is
    almost impossible to diagnose, so both sinks are attempted independently.
    """
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} | {msg}"
    try:
        print(line, flush=True)
    except (ValueError, OSError):
        pass
    try:
        config.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with config.LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def load_state() -> dict:
    if config.STATE_FILE.exists():
        try:
            return json.loads(config.STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_state(st: dict) -> None:
    config.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(st, indent=2, default=str), encoding="utf-8")
    tmp.replace(config.STATE_FILE)


# --------------------------------------------------------------- enable/disable
def paused() -> bool:
    return config.DISABLED_SENTINEL.exists()


def pause() -> int:
    config.DISABLED_SENTINEL.write_text(
        f"paused {datetime.now():%Y-%m-%d %H:%M:%S}\n"
        "Delete this file (or run `python daily_run.py --resume`) to re-enable.\n",
        encoding="utf-8")
    print(f"PAUSED. The scheduled task will still fire and exit immediately.\n"
          f"  sentinel: {config.DISABLED_SENTINEL}\n"
          f"  resume:   python daily_run.py --resume")
    return 0


def resume() -> int:
    if config.DISABLED_SENTINEL.exists():
        config.DISABLED_SENTINEL.unlink()
        print("RESUMED. The next scheduled run will do work.")
    else:
        print("Already enabled.")
    return 0


# ------------------------------------------------------------------ gap catch-up
def detect_gap(asof: str) -> list[str]:
    """Sessions with no flags file that should have one.

    Bounded by the last successful run rather than by a fixed window, so a laptop
    closed for a week is fully reconciled and one closed for a month falls back to
    the cap instead of replaying a quarter.
    """
    st = load_state()
    last = st.get("last_screened")
    sessions = calendar_us.all_sessions()
    if not last:
        return []
    window = calendar_us.sessions_between(last, asof)[1:]
    if not window:
        return []
    if len(window) > config.MAX_CATCHUP_SESSIONS:
        window = window[-config.MAX_CATCHUP_SESSIONS:]
    return [d for d in window
            if d != asof and not (config.FLAGS / f"{d}.parquet").exists()]


# ------------------------------------------------------------------------- run
def run(window: int = 5, do_confirm: bool = True, do_catchup: bool = True,
        dry: bool = False, do_sentiment: bool = True,
        do_universe: bool = True, do_bars: bool = True) -> int:
    """
    `do_universe` / `do_bars` default True so the standalone path is unchanged.
    orchestrator.py passes False for both because it owns those as their own
    registry steps -- without the opt-out it would refresh the universe and
    re-run the split-recheck a second time (7s + 32s) for no new data, which is
    exactly the duplicated-work class this project is trying to remove.
    `do_sentiment` is likewise False under the orchestrator, which runs the
    sentiment module as its own step BEFORE this one so the cards still badge.
    """
    t0 = time.time()
    timings: dict[str, float] = {}

    def step(name: str, fn, *a, **kw):
        """Time one pipeline step. Callers pass KEYWORD args deliberately: these
        cross module boundaries, and a positional call would bind silently to the
        wrong parameter if any signature ever grew an argument."""
        s = time.time()
        out = fn(*a, **kw)
        timings[name] = round(time.time() - s, 1)
        log(f"  [{name}] {timings[name]}s")
        return out

    log("=" * 62)
    asof = calendar_us.last_closed_session()
    log(f"RUN START | asof={asof} | window={window}d")

    if dry:
        st = load_state()
        gap = detect_gap(asof)
        print(f"\n  DRY RUN -- nothing will be written")
        print(f"  last closed session : {asof}")
        print(f"  last screened       : {st.get('last_screened', '(never)')}")
        print(f"  last successful run : {st.get('last_success', '(never)')}")
        print(f"  paused              : {paused()}")
        print(f"  store               : {store.store_bytes() / 1e6:.0f} MB, "
              f"{len(store.months('1d'))} month file(s)")
        print(f"  universe            : {universe.summary()}")
        print(f"  sessions to catch up: {gap or '(none)'}")
        print(f"  would run           : bars.update -> screen -> "
              f"{'confirm -> ' if do_confirm else ''}"
              f"{'sentiment -> ' if do_sentiment else ''}report -> outcomes")
        try:
            import news
            import scores
            print(f"  news store          : {len(news.months())} month(s), "
                  f"{len(news.stored_sessions())} session(s), "
                  f"{news.store_bytes() / 1e6:.0f} MB")
            print(f"  score sessions      : "
                  f"{len(scores.sessions_stored('sentiment'))}")
        except Exception:                                        # noqa: BLE001
            pass
        return 0

    st = load_state()
    st["last_run"] = datetime.now().isoformat(timespec="seconds")
    save_state(st)               # written FIRST so a crash still records the attempt

    errors: list[str] = []

    # 1. universe (cheap, and it is how delistings are observed)
    if do_universe:
        try:
            _reg, added, removed, degraded = step("universe", universe.refresh, verbose=False)
            log(f"    {universe.summary()}"
                f"{'  DEGRADED (nasdaq directory from cache)' if degraded else ''}")
            if added:
                log(f"    + {len(added)} added")
            if removed:
                log(f"    - {len(removed)} removed (history retained)")
        except Exception as exc:                                # noqa: BLE001
            log(f"    ! universe refresh failed ({repr(exc)[:110]}); using stored registry")
            errors.append("universe")

    # weekly revival sweep for names marked dead
    try:
        lastr = st.get("last_dead_retry")
        due = (not lastr or (datetime.now()
               - datetime.fromisoformat(lastr)).days >= config.DEAD_RETRY_DAYS)
        if due:
            n = universe.retry_dead()
            if n:
                log(f"    weekly sweep: {n} dead ticker(s) get another chance")
            st["last_dead_retry"] = datetime.now().isoformat(timespec="seconds")
    except Exception:                                            # noqa: BLE001
        pass

    # 2. bars
    if do_bars:
        try:
            res = step("bars", bars.update, window=window)
            if not res.get("ok") and not res.get("skipped"):
                errors.append("bars")
        except Exception as exc:                                 # noqa: BLE001
            log(f"    ! bars update failed: {repr(exc)[:140]}")
            log(traceback.format_exc(limit=2))
            errors.append("bars")

    # 3. catch-up: reconcile sessions missed while the machine was off, using the
    #    already-local bars, purely so days_on_list is honest after a gap.
    if do_catchup:
        gap = detect_gap(asof)
        if gap:
            log(f"  gap detected: {len(gap)} missed session(s) "
                f"{gap[0]} -> {gap[-1]}; reconciling from local bars")
            try:
                import replay
                step("catchup", replay.catchup, dates=gap, verbose=False)
            except Exception as exc:                             # noqa: BLE001
                log(f"    ! catch-up failed ({repr(exc)[:110]})")
                errors.append("catchup")

    # 4. screen
    flags = None
    try:
        flags, rejects = step("screen", screen.screen_universe,
                              asof_date=asof, verbose=False)
        log(f"    {len(flags)} flag(s), {len(rejects)} reject(s)")
        screen.write_outputs(flags, rejects, asof)
        st["last_screened"] = asof
    except Exception as exc:                                     # noqa: BLE001
        log(f"    ! screen failed: {repr(exc)[:140]}")
        log(traceback.format_exc(limit=2))
        errors.append("screen")

    # 5. confirm (optional, never fatal, never changes pass/fail)
    if do_confirm and flags is not None and not flags.empty:
        try:
            import confirm
            import pandas as pd
            enriched = step("confirm", confirm.apply, flags,
                            do_hourly=True, do_fundamentals=True, verbose=False)
            p = config.FLAGS / f"{asof}.parquet"
            tmp = p.with_suffix(".parquet.tmp")
            screen.serialise_lists(enriched).to_parquet(
                tmp, compression=config.COMPRESSION, index=False)
            tmp.replace(p)
        except Exception as exc:                                 # noqa: BLE001
            log(f"    ! confirm failed ({repr(exc)[:110]}); report will omit "
                "hourly and market-cap annotations")

    # 5b. sentiment scores. BEFORE the report, because report.py reads the
    #     stored score rows to put a news badge on each card -- running it after
    #     would badge today's cards with yesterday's news. Never fatal: the
    #     bounce report predates this module and must work without it.
    if do_sentiment:
        try:
            import senti_screen
            step("sentiment", senti_screen.run_once, asof=asof, verbose=False)
        except Exception as exc:                                 # noqa: BLE001
            log(f"    ! sentiment failed ({repr(exc)[:110]}); "
                "cards will show no news badge")

    # 6. report (also reconciles flag state)
    try:
        import report
        payload = step("report", report.build, asof=asof, verbose=False)
        log(f"    {len(payload['cards'])} card(s); "
            f"{'  '.join(f'{k}={v}' for k, v in payload['counts'].items())}")
        st["flag_count"] = len(payload["cards"])
        scores = [c["score"] for c in payload["cards"] if c.get("score")]
        if scores:
            import numpy as np
            st["score_p50"] = round(float(np.percentile(scores, 50)), 1)
            st["score_p90"] = round(float(np.percentile(scores, 90)), 1)
    except Exception as exc:                                     # noqa: BLE001
        log(f"    ! report failed: {repr(exc)[:140]}")
        log(traceback.format_exc(limit=2))
        errors.append("report")

    # 7. outcomes
    try:
        import state
        step("outcomes", state.track_outcomes, asof=asof, verbose=False)
    except Exception as exc:                                     # noqa: BLE001
        log(f"    ! outcome tracking failed ({repr(exc)[:110]})")
        errors.append("outcomes")

    # 8. retention
    try:
        dropped = store.prune_dated(config.REJECTS, config.REJECT_KEEP_DAYS)
        if dropped:
            log(f"  pruned {len(dropped)} old reject dump(s)")
        store.prune_1h()
    except Exception:                                            # noqa: BLE001
        pass

    mins = (time.time() - t0) / 60
    st.update({
        "last_duration_sec": round(time.time() - t0, 1),
        "last_errors": errors,
        "store_bytes": store.store_bytes(),
        "timings": timings,
        "asof": asof,
    })
    if not errors:
        st["last_success"] = datetime.now().isoformat(timespec="seconds")
    save_state(st)

    log(f"RUN DONE in {mins * 60:.0f}s | "
        f"{'OK' if not errors else 'ERRORS: ' + ', '.join(errors)}")
    log(f"  report: {config.REPORTS / 'latest.html'}")
    return 1 if errors else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Daily support-bounce screener run.")
    ap.add_argument("--window", type=int, default=5,
                    help="session look-back to keep gap-free (default 5)")
    ap.add_argument("--no-confirm", action="store_true",
                    help="skip hourly + market-cap enrichment")
    ap.add_argument("--no-catchup", action="store_true")
    ap.add_argument("--no-sentiment", action="store_true",
                    help="skip the news/sentiment score module")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--pause", action="store_true")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--once", action="store_true",
                    help="run even if paused")
    a = ap.parse_args()

    config.dirs()

    if a.pause:
        return pause()
    if a.resume:
        return resume()

    if paused() and not a.once and not a.dry_run:
        # Deliberately exit 0: a paused screener is not a failed task, and a
        # nonzero code would show up as a Task Scheduler error every night.
        log(f"PAUSED ({config.DISABLED_SENTINEL.name} present) -- exiting without work. "
            "Use --resume to re-enable, or --once to override.")
        return 0

    return run(a.window, not a.no_confirm, not a.no_catchup, a.dry_run,
               not a.no_sentiment)


if __name__ == "__main__":
    # Last-resort guard. A scheduled run that dies before or outside `run()` would
    # otherwise leave only "LastTaskResult 1" and an empty log, which is the least
    # diagnosable failure mode available. Anything that escapes gets a traceback in
    # data/_run.log where status.py will surface it.
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as exc:                                 # noqa: BLE001
        try:
            log(f"FATAL {type(exc).__name__}: {exc}")
            for ln in traceback.format_exc().splitlines()[-12:]:
                log("  " + ln)
        finally:
            sys.exit(2)

