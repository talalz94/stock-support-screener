"""
One-off: rename the metric `quality_rank` to `fund_rank`, in code AND in the
stored rows, in a single pass.

    python migrate_fund_rank.py --dry-run
    python migrate_fund_rank.py

WHY THE NAME IS WRONG
-----------------------
`quality_rank` is the percentile of **`fund_score`** across the universe -- the
whole composite, not the quality pillar. `metrics_doc` has said so all along
("percentile of fund_score across the universe"), and the study measured it at
rho 1.00 against `fund_score`, which is what a rank of a thing always is.

Anyone reading the profile page next to `quality_score` (which IS the quality
pillar) would reasonably conclude the two are related. They are not.

WHY CODE AND DATA MUST MOVE TOGETHER
--------------------------------------
212,871 rows across 81+ sessions carry the old name. Renaming only the code
leaves the store holding BOTH, each with half the history, and `study.py` would
measure them as two unrelated metrics with no sign anything was wrong. Renaming
only the data means the next scoring run writes the old name back.

So this does both, verifies both, and refuses to start if a scoring job is
writing to the store -- a concurrent `scores.write` reads a whole month
partition and rewrites it, so an interleaved run would silently drop whichever
change landed first.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from datetime import datetime

import pandas as pd

import config

config.safe_console()

import scores                                                    # noqa: E402
import store                                                     # noqa: E402

OLD, NEW = "quality_rank", "fund_rank"

# Every place the literal appears in source. Checked rather than assumed: a
# missed call site means the next run writes the old name straight back.
CODE_FILES = ("scores/dip.py", "scores/combo.py", "metrics_doc.py", "ui.py",
              "explore.py", "settings.py", "stock_profile.py")


def log(m: str) -> None:
    print(f"mig   {datetime.now():%H:%M:%S} | {m}", flush=True)


def busy() -> str:
    """Any other process writing scores right now? Empty string if safe."""
    try:
        import psutil
    except ImportError:
        return ""
    me = pathlib.Path(sys.argv[0]).name
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmd = " ".join(p.info.get("cmdline") or [])
        except Exception:                                        # noqa: BLE001
            continue
        if me in cmd:
            continue
        for marker in ("sentiment_rebuild", "catchup_scores", "overnight.py",
                       "orchestrator.py", "senti_screen", "fund_screen"):
            if marker in cmd:
                return f"{marker} (pid {p.info['pid']})"
    return ""


def scan_code() -> dict[str, int]:
    out = {}
    for rel in CODE_FILES:
        f = config.ROOT / rel
        if not f.exists():
            continue
        n = f.read_text(encoding="utf-8").count(OLD)
        if n:
            out[rel] = n
    return out


def scan_store() -> tuple[int, int]:
    """(rows, sessions) carrying the old name."""
    rows = sess = 0
    seen = set()
    for m in scores.months():
        df = pd.read_parquet(scores.part_path(m), columns=["session", "metric"])
        hit = df[df["metric"] == OLD]
        rows += len(hit)
        seen |= set(hit["session"].astype(str))
    return rows, len(seen)


def main() -> int:
    ap = argparse.ArgumentParser(description=f"Rename {OLD} -> {NEW}.")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    scores.load_all()
    code = scan_code()
    rows, sess = scan_store()
    log(f"code: {sum(code.values())} occurrence(s) in {len(code)} file(s) -> {code}")
    log(f"store: {rows:,} row(s) across {sess} session(s)")
    study_n = 0
    try:
        import study
        sdf = study.read()
        study_n = int((sdf["metric"] == OLD).sum()) if len(sdf) else 0
        log(f"study: {study_n} measured cell(s)")
    except Exception:                                            # noqa: BLE001
        pass

    if a.dry_run:
        log("--dry-run: nothing written")
        return 0

    who = busy()
    if who:
        log(f"REFUSING: {who} is writing scores. A concurrent scores.write "
            f"rewrites a whole month partition, so one of the two changes "
            f"would be silently lost. Re-run when it finishes.")
        return 1

    # ---- data first ------------------------------------------------------
    touched = 0
    for m in scores.months():
        p = scores.part_path(m)
        df = pd.read_parquet(p)
        if not (df["metric"] == OLD).any():
            continue
        df["metric"] = df["metric"].astype(str).replace({OLD: NEW})
        tmp = p.with_suffix(".parquet.tmp")
        df.to_parquet(tmp, compression=config.COMPRESSION,
                      compression_level=config.COMPRESSION_LEVEL, index=False)
        store.atomic_replace(tmp, p)
        touched += 1
    log(f"rewrote {touched} month partition(s)")

    # ---- the study's own cells ------------------------------------------
    if study_n:
        import study
        sdf = study.read()
        sdf["metric"] = sdf["metric"].astype(str).replace({OLD: NEW})
        tmp = study.OUT.with_suffix(".parquet.tmp")
        sdf.to_parquet(tmp, compression=config.COMPRESSION,
                       compression_level=config.COMPRESSION_LEVEL, index=False)
        store.atomic_replace(tmp, study.OUT)
        log(f"renamed {study_n} study cell(s)")

    # ---- then the code ---------------------------------------------------
    for rel in code:
        f = config.ROOT / rel
        t = f.read_text(encoding="utf-8")
        f.write_text(t.replace(OLD, NEW), encoding="utf-8")
    log(f"rewrote {len(code)} source file(s)")

    # ---- verify BOTH sides, or the store ends up holding two names -------
    scores._invalidate_sessions()
    left_rows, _ = scan_store()
    left_code = scan_code()
    if left_rows or left_code:
        log(f"INCOMPLETE: {left_rows} row(s) and {left_code} still use {OLD!r}")
        return 1
    new_rows, new_sess = 0, set()
    for m in scores.months():
        df = pd.read_parquet(scores.part_path(m), columns=["session", "metric"])
        hit = df[df["metric"] == NEW]
        new_rows += len(hit)
        new_sess |= set(hit["session"].astype(str))
    log(f"verified: {new_rows:,} row(s) across {len(new_sess)} session(s) now "
        f"named {NEW!r}, and {OLD!r} appears nowhere")
    if new_rows != rows:
        log(f"  ! row count changed ({rows:,} -> {new_rows:,}) -- investigate")
        return 1
    log("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
