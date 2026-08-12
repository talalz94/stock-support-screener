"""
The second unattended chain: deepen the composites and remove the last
known look-ahead.

    python night2.py             run the whole chain
    python night2.py --dry-run   list the stages and their measured costs

Launched as a one-shot Scheduled Task so it survives Claude Code, a terminal, or
an editor being closed. Expected wall clock ~7.5h. Every stage is resumable, so
a reboot costs the current stage rather than the chain.

WHY THESE STAGES, IN THIS ORDER
---------------------------------
The dependencies are about DATA, not files, and getting them wrong produces a
results table built from two different datasets with nothing to reveal it:

  * The sentiment study is running when this starts. Stage 2 onward changes the
    score store it is reading, so stage 1 waits it out.
  * `mktcap` must land BEFORE `dip` is rebuilt: dip passes it through, and the
    study's size buckets stay a snapshot until >= 24 sessions carry it.
  * `dip` before `combo`: combo reads dip, and its floor is derived from
    whatever dip actually holds.
  * The `fund_rank` migration runs where nothing else is writing -- a concurrent
    `scores.write` rewrites a whole month partition, so an interleaved run would
    silently drop one of the two changes.
  * `study_fresh` last of the data stages, so every cell is measured against the
    finished store.

WHAT TO READ WHEN IT FINISHES
-------------------------------
    tail -80 data/_night2.log

Two results matter. **Plain vs decayed sentiment** (stage 2) -- both stay on the
page either way; one in-sample measurement does not promote a metric. And
**combo_h60 on ~290 dates instead of 61** (stage 7): it currently reads t=+2.91
at h=60, and that number is the whole reason for the chain. Report it whichever
way it moves; `dip_score` already faced this test and failed it.
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

LOG = config.DATA / "_night2.log"
# WHICH STAGES ARE ALREADY DONE. Without this a reboot restarts the chain from
# stage 1 and redoes hours of finished work -- and worse, `study_fresh` would
# archive the study a second time, overwriting the pre-night2 backup with the
# half-built one it had just made.
STATE = config.DATA / "_night2_state.json"
LOCK_REFRESH_S = 20 * 60
_stop_refresh = threading.Event()


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:                                            # noqa: BLE001
        return {"done": [], "started": datetime.now().isoformat(timespec="seconds")}


def save_state(st: dict) -> None:
    try:
        tmp = STATE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
        tmp.replace(STATE)
    except OSError as exc:
        log(f"  ! could not save state ({exc!r}) -- a restart will redo this stage")


def log(msg: str) -> None:
    line = f"night2 {datetime.now():%Y-%m-%d %H:%M:%S} | {msg}"
    try:
        print(line, flush=True)
    except (ValueError, OSError):
        pass
    for path in (LOG, config.LOG_FILE):
        try:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass


def _refresh_lock() -> None:
    while not _stop_refresh.wait(LOCK_REFRESH_S):
        try:
            config.ORCH_LOCK_FILE.write_text(
                json.dumps({"pid": os.getpid(),
                            "started": datetime.now().isoformat(timespec="seconds"),
                            "note": "night2.py chain"}),
                encoding="utf-8")
        except OSError:
            pass


# ------------------------------------------------------------------- stages
def _wait_study() -> tuple[int, str]:
    """Block until any running study or rebuild has finished."""
    import psutil
    waited = 0
    markers = ("study.py", "sentiment_rebuild")
    while True:
        alive = []
        for p in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmd = " ".join(p.info.get("cmdline") or [])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if p.info["pid"] == os.getpid():
                continue
            if any(m in cmd for m in markers) and "night2" not in cmd:
                alive.append(p.info["pid"])
        if not alive:
            return waited, (f"clear after {waited / 60:.0f} min" if waited
                            else "nothing was running")
        if waited % 600 == 0:
            log(f"  waiting on pid {alive} ({waited / 60:.0f} min so far)")
        time.sleep(60)
        waited += 60


def _report_decay() -> tuple[int, str]:
    """Did recency weighting beat the flat mean? Printed, never acted on here."""
    import study
    df = study.read()
    s = df[(df["module"] == "sentiment") & (df["size"] == "all")]
    if s.empty:
        return 0, "no sentiment cells measured"
    n = 0
    log("  window  horizon    plain       decayed     better")
    for w in (5, 30, 90):
        for h in (1, 5, 20, 60):
            a = s[(s["metric"] == f"sent_mean_{w}d") & (s["horizon"] == h)]
            b = s[(s["metric"] == f"sent_decay_{w}d") & (s["horizon"] == h)]
            if not len(a) or not len(b):
                continue
            ta, tb = float(a.iloc[0]["t"]), float(b.iloc[0]["t"])
            log(f"    {w:>3}d  h={h:<4} t={ta:+6.2f}    t={tb:+6.2f}    "
                f"{'decay' if abs(tb) > abs(ta) else 'plain'}")
            n += 1
    # SAY SO IF THE COMPARISON IS PARTIAL. If the machine rebooted mid-study
    # this reports on whatever cells happened to be written, and a table built
    # from a fraction of the runs looks identical to a complete one. 12 pairs
    # (3 windows x 4 horizons) is the full set.
    if n < 12:
        log(f"  ! PARTIAL: only {n} of 12 pairs measured -- the sentiment study "
            f"did not finish. `study_fresh` later in this chain re-measures "
            f"everything, so treat THAT as the answer, not this.")
    log("  NEITHER is promoted on one in-sample run -- both stay on the page.")
    return n, (f"{n} of 12 plain/decay pair(s) compared"
               + ("  [PARTIAL]" if n < 12 else ""))


def _mktcap_backfill() -> tuple[int, str]:
    """Populate `mktcap` on every stored fundamental session.

    ONLY mktcap, not the whole 46-metric compute. Measured: `facts_asof` +
    `_price_inputs` is 36s a session against 69s for the full module, so this is
    ~1.8h instead of 3.5h for the identical outcome.

    This is what turns the study's size terciles from a snapshot applied to ten
    years -- where "large" really means "became large" -- into point-in-time
    buckets. `study.size_bucket_frame` switches over on its own at >= 24
    bucketed sessions, so there is no code change waiting behind this.
    """
    import bars
    import pandas as pd
    import scores
    import fundamentals as FD
    import scores.fundamental as SF

    scores.load_all()
    all_sess = scores.sessions_stored("fundamental")
    # RESUMABLE: sessions that already carry mktcap are skipped, so a reboot
    # two hours in costs one session rather than two hours.
    have_mk = set(scores.read(module="fundamental",
                              metrics=["mktcap"])["session"].astype(str))
    sess = [s for s in all_sess if s not in have_mk]
    log(f"  {len(have_mk)} session(s) already carry mktcap; {len(sess)} to do")
    done, failed, t0 = 0, [], time.time()
    for i, s in enumerate(sess, 1):
        try:
            uni = bars.tradeable_universe(s)
            if not uni:
                continue
            cur = FD.facts_asof(s, uni)
            if cur.empty:
                continue
            px = SF._price_inputs(s, cur["ticker"].tolist(), cur)
            mk = pd.to_numeric(px.get("mktcap"), errors="coerce")
            rows = [{"ticker": t, "metric": "mktcap", "value": float(v),
                     "label": None}
                    for t, v in zip(px["ticker"], mk)
                    if pd.notna(v) and v > 0]
            if not rows:
                continue
            # MERGE, not replace: `scores.write` swaps out (session, module)
            # wholesale, so writing mktcap alone would delete the other 45
            # metrics for that session. Read what is there and add to it.
            have = scores.read(module="fundamental", start=s, end=s)
            have = have[have["metric"] != "mktcap"]
            merged = pd.concat(
                [have[["ticker", "metric", "value", "label"]],
                 pd.DataFrame(rows)], ignore_index=True)
            scores.write(merged, session=s, module="fundamental")
            done += 1
        except Exception as exc:                                 # noqa: BLE001
            failed.append(f"{s}({type(exc).__name__})")
        if i % 20 == 0 or i == len(sess):
            el = (time.time() - t0) / 60
            log(f"  {i}/{len(sess)} ({done} written, {el:.1f}m, "
                f"eta {el / max(i, 1) * (len(sess) - i):.0f}m)")
    if failed:
        log(f"  FAILED: {', '.join(failed[:8])}")
    return done, (f"{done} of {len(sess)} remaining session(s) now carry mktcap"
                  + (f"; {len(failed)} failed" if failed else ""))


def _rebuild(module: str) -> tuple[int, str]:
    """Rebuild one module's whole series to its DERIVED floor."""
    import catchup_scores as CS
    import scores
    scores.load_all()
    floor, why = CS.floor_for(module)
    n = scores.catchup(module, every=14, frm=floor, rebuild=True, verbose=True)
    return n, f"{n} session(s) from {floor} ({why})"


def _fund_rank() -> tuple[int, str]:
    import migrate_fund_rank as M
    rc = M.main_unattended() if hasattr(M, "main_unattended") else None
    if rc is None:
        import subprocess
        p = subprocess.run([sys.executable, "migrate_fund_rank.py"],
                           cwd=str(config.ROOT), capture_output=True, text=True)
        for line in (p.stdout or "").splitlines():
            log("  " + line)
        if p.returncode:
            raise RuntimeError(f"migration exited {p.returncode}")
        return 1, "quality_rank -> fund_rank, code and store"
    if rc:
        raise RuntimeError(f"migration exited {rc}")
    return 1, "quality_rank -> fund_rank, code and store"


def _study_fresh() -> tuple[int, str]:
    """Re-measure every cell against the finished store."""
    import study
    keep = config.DATA / "_factor_study_pre_night2.parquet"
    # Archive ONLY on the first attempt. A restart that re-archived would
    # overwrite the genuine pre-night2 table with the partial one this stage
    # had already started building -- destroying the before/after comparison
    # the whole chain exists to produce.
    if study.OUT.exists() and not keep.exists():
        try:
            study.OUT.rename(keep)
            log(f"  previous study archived to {keep.name}")
        except OSError as exc:
            return 0, f"could not archive the old study ({exc!r}); refusing"
    rc = study.run()
    df = study.read()

    # The headline the chain exists to produce.
    c = df[(df["size"] == "all") & df["metric"].str.startswith("combo_")
           & ~df["metric"].str.contains("_cov|_n")]
    if len(c):
        log("  COMBO, re-measured:")
        for _, r in c.sort_values(["metric", "horizon"]).iterrows():
            log(f"    {r['metric']:<14} h={int(r['horizon']):<3} "
                f"t={r['t']:+6.2f}  hit={r['hit']:.0%}  "
                f"n_dates={int(r['n_dates'])}")
    return len(df), f"{len(df):,} cell(s) measured (exit {rc})"


def _orch() -> tuple[int, str]:
    rc = orchestrator.run(only=None, force=False)
    return rc, f"full orchestrator pass (exit {rc})"


def _validate() -> tuple[int, str]:
    import subprocess
    p = subprocess.run([sys.executable, "validate.py"],
                       cwd=str(config.ROOT), capture_output=True, text=True)
    for line in (p.stdout or "").splitlines():
        if line.strip():
            log("  " + line)
    if p.returncode:
        raise RuntimeError(f"validate.py exited {p.returncode}")
    return 1, "integrity audit clean"


STAGES = [
    ("wait_study", _wait_study, "~50 min",
     "the sentiment re-measure is running; every later stage changes its store"),
    ("report_decay", _report_decay, "s",
     "plain vs decayed sentiment -- reported, deliberately not acted on"),
    ("mktcap_backfill", _mktcap_backfill, "~1.8 h",
     "182 sessions; turns the study's size buckets point-in-time"),
    ("dip_rebuild", lambda: _rebuild("dip"), "~23 min",
     "to 2016 -- the floor now derives from the sentiment series"),
    ("combo_rebuild", lambda: _rebuild("combo"), "~17 min",
     "to 2016 -- its floor follows dip"),
    ("fund_rank", _fund_rank, "s",
     "rename quality_rank in code AND store, where nothing else is writing"),
    ("study_fresh", _study_fresh, "~4.3 h",
     "every cell re-measured; combo_h60 moves from 61 dates to ~290"),
    ("rebuild_pages", _orch, "~35 min",
     "rebuild every page and regenerate the docs on the new data"),
    ("validate", _validate, "~6 min",
     "the 26-check audit, last, so it audits the finished state"),
]


def dry_run() -> int:
    print("\n  night2 chain -- stages in order\n")
    for name, _fn, cost, why in STAGES:
        print(f"  {name:<18}{cost:>9}   {why}")
    print("\n  total ~7.5 h. Every stage is resumable.\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Deepen the composites, unattended.")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.dry_run:
        return dry_run()

    config.dirs()
    if (config.DATA / "_night2_complete").exists():
        # The task repeats so a reboot resumes the chain; without this the same
        # repetition would re-run a finished chain every 15 minutes for ever.
        print("night2 already completed; nothing to do "
              "(delete data/_night2_complete to force a re-run)")
        return 0
    t0 = time.time()
    log("=" * 62)
    log(f"NIGHT2 CHAIN START | {len(STAGES)} stage(s), ~7.5h expected")

    if not orchestrator.acquire_lock():
        log("another run holds the lock; exiting without work")
        return 0
    threading.Thread(target=_refresh_lock, daemon=True).start()

    st = load_state()
    done_already = set(st.get("done", []))
    if done_already:
        log(f"RESUMING -- {len(done_already)} stage(s) already complete: "
            f"{sorted(done_already)}")

    failed = 0
    try:
        for name, fn, cost, why in STAGES:
            if name in done_already:
                log(f"[{name}] skipped -- already completed on an earlier run")
                continue
            log(f"[{name}] start ({cost} expected) -- {why}")
            s = time.time()
            try:
                rows, detail = fn()
                log(f"[{name}] done in {(time.time() - s) / 60:.1f}m -- {detail}")
                # Recorded only on SUCCESS, so a failed stage is retried rather
                # than silently skipped by the next run.
                st.setdefault("done", []).append(name)
                st[f"{name}_detail"] = str(detail)[:200]
                save_state(st)
            except Exception as exc:                             # noqa: BLE001
                failed += 1
                log(f"[{name}] FAILED after {(time.time() - s) / 60:.1f}m "
                    f"-- {type(exc).__name__}: {exc}"[:300])
                log(traceback.format_exc()[-900:])
    finally:
        _stop_refresh.set()
        try:
            orchestrator.release_lock()
        except Exception:                                        # noqa: BLE001
            pass

    log(f"NIGHT2 CHAIN DONE in {(time.time() - t0) / 3600:.1f}h | "
        f"{len(STAGES) - failed} ok, {failed} failed")
    if not failed:
        # Finished cleanly: retire the checkpoint AND the repeating trigger's
        # reason to exist, so the task does not start the chain again later.
        try:
            STATE.unlink(missing_ok=True)
            (config.DATA / "_night2_complete").write_text(
                datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
            log("  checkpoint cleared; wrote data/_night2_complete")
        except OSError:
            pass
    # Non-zero on any failure: a chain that reports success after a failed stage
    # is the pattern this project keeps paying for.
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
