"""
Rename a metric everywhere at once -- source, score store, and study cells.

    python migrate_metric.py --map old=new[,old=new...] --dry-run
    python migrate_metric.py --map quality_rank=fund_rank

Generalised from `migrate_fund_rank.py`, which was the first of these. A metric
name lives in four places and they have to move together:

  1. the modules that emit and consume it
  2. `data/scores/*.parquet`          -- hundreds of thousands of rows
  3. `data/_factor_study.parquet`     -- its measured cells
  4. the docs / labels that name it

Renaming only the code leaves the store holding BOTH names, each with half the
history, and `study.py` measures them as two unrelated metrics with nothing in
the output to say so. Renaming only the data means the next scoring run writes
the old name straight back.

It REFUSES to start while anything else is writing scores: `scores.write` reads
a whole month partition and rewrites it, so an interleaved run silently drops
whichever change landed first.
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

# Everything that can name a metric. Checked rather than assumed -- a missed
# call site means the next run writes the old name back.
CODE_GLOBS = ("*.py", "scores/*.py")

WRITERS = ("sentiment_rebuild", "catchup_scores", "overnight.py", "night2.py",
           "orchestrator.py", "senti_screen", "fund_screen", "combo_refresh")


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
        if me in cmd or p.info["pid"] == __import__("os").getpid():
            continue
        for marker in WRITERS:
            if marker in cmd:
                return f"{marker} (pid {p.info['pid']})"
    return ""


def code_files() -> list[pathlib.Path]:
    out = []
    for g in CODE_GLOBS:
        out += [f for f in config.ROOT.glob(g) if f.name != "migrate_metric.py"]
    return sorted(set(out))


def scan_code(names) -> dict[str, int]:
    hits = {}
    for f in code_files():
        t = f.read_text(encoding="utf-8", errors="replace")
        n = sum(t.count(x) for x in names)
        if n:
            hits[f.relative_to(config.ROOT).as_posix()] = n
    return hits


def scan_store(names) -> tuple[int, int]:
    rows, sess = 0, set()
    for m in scores.months():
        df = pd.read_parquet(scores.part_path(m), columns=["session", "metric"])
        hit = df[df["metric"].isin(names)]
        rows += len(hit)
        sess |= set(hit["session"].astype(str))
    return rows, len(sess)


def main() -> int:
    ap = argparse.ArgumentParser(description="Rename metrics everywhere.")
    ap.add_argument("--map", dest="mapping", required=True,
                    help="old=new[,old=new...]")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    pairs = []
    for part in a.mapping.split(","):
        if "=" not in part:
            print(f"bad mapping entry {part!r}; expected old=new")
            return 2
        old, new = (x.strip() for x in part.split("=", 1))
        if not old or not new:
            print(f"bad mapping entry {part!r}")
            return 2
        pairs.append((old, new))

    # LONGEST FIRST. Renaming `combo_short` before `combo_short_cov` would leave
    # `combo_h1_cov` half-renamed if the shorter key were a prefix.
    pairs.sort(key=lambda kv: -len(kv[0]))
    olds = [o for o, _ in pairs]

    scores.load_all()
    code = scan_code(olds)
    rows, sess = scan_store(olds)
    log(f"mapping: {', '.join(f'{o} -> {n}' for o, n in pairs)}")
    log(f"code : {sum(code.values())} occurrence(s) in {len(code)} file(s)")
    for k, v in sorted(code.items()):
        log(f"        {v:4d}  {k}")
    log(f"store: {rows:,} row(s) across {sess} session(s)")

    study_n = 0
    try:
        import study
        sdf = study.read()
        study_n = int(sdf["metric"].isin(olds).sum()) if len(sdf) else 0
        log(f"study: {study_n} measured cell(s)")
    except Exception:                                            # noqa: BLE001
        pass

    if a.dry_run:
        log("--dry-run: nothing written")
        return 0

    who = busy()
    if who:
        log(f"REFUSING: {who} is writing scores. Re-run when it finishes.")
        return 1

    # ---- data ------------------------------------------------------------
    touched = 0
    for m in scores.months():
        p = scores.part_path(m)
        df = pd.read_parquet(p)
        if not df["metric"].isin(olds).any():
            continue
        df["metric"] = df["metric"].astype(str).replace(dict(pairs))
        tmp = p.with_suffix(".parquet.tmp")
        df.to_parquet(tmp, compression=config.COMPRESSION,
                      compression_level=config.COMPRESSION_LEVEL, index=False)
        store.atomic_replace(tmp, p)
        touched += 1
    log(f"rewrote {touched} month partition(s)")

    if study_n:
        import study
        sdf = study.read()
        sdf["metric"] = sdf["metric"].astype(str).replace(dict(pairs))
        tmp = study.OUT.with_suffix(".parquet.tmp")
        sdf.to_parquet(tmp, compression=config.COMPRESSION,
                       compression_level=config.COMPRESSION_LEVEL, index=False)
        store.atomic_replace(tmp, study.OUT)
        log(f"renamed {study_n} study cell(s)")

    # ---- code ------------------------------------------------------------
    for rel in code:
        f = config.ROOT / rel
        t = f.read_text(encoding="utf-8")
        for old, new in pairs:                    # longest first, see above
            t = t.replace(old, new)
        f.write_text(t, encoding="utf-8")
    log(f"rewrote {len(code)} source file(s)")

    # ---- verify BOTH sides ----------------------------------------------
    scores._invalidate_sessions()
    left_rows, _ = scan_store(olds)
    left_code = scan_code(olds)
    if left_rows or left_code:
        log(f"INCOMPLETE: {left_rows} row(s) and {left_code} still use the old name(s)")
        return 1
    new_rows, new_sess = 0, set()
    news = [n for _, n in pairs]
    for m in scores.months():
        df = pd.read_parquet(scores.part_path(m), columns=["session", "metric"])
        hit = df[df["metric"].isin(news)]
        new_rows += len(hit)
        new_sess |= set(hit["session"].astype(str))
    log(f"verified: {new_rows:,} row(s) across {len(new_sess)} session(s) "
        f"renamed, old name(s) appear nowhere")

    # AND: warn about any stored metric that merely CONTAINS an old name.
    #
    # The rename is exact-match, which is correct -- a substring replace would
    # have turned the hype metrics `short_ratio`/`short_surge` into
    # `h1_ratio`/`h1_surge`. But exact-match means `combo_short_cov` survives a
    # `combo_short` rename, and the verification above could not see it because
    # it used the same exact-match list. Renaming `combo_short` and leaving
    # `combo_short_cov` behind is exactly the half-migration this tool exists to
    # prevent, so near-misses are reported for a human to judge.
    stored = set()
    for m in scores.months():
        stored |= set(pd.read_parquet(scores.part_path(m),
                                      columns=["metric"])["metric"].astype(str))
    near = sorted(x for x in stored
                  if any(o in x for o in olds) and x not in news)
    if near:
        log(f"  ! {len(near)} stored metric(s) CONTAIN an old name but were not "
            f"renamed (exact-match, by design): {near[:8]}")
        log(f"    If these are variants of the same metric, re-run with them "
            f"in --map. If they are unrelated (short_ratio), ignore this.")
    if new_rows != rows:
        log(f"  ! row count changed ({rows:,} -> {new_rows:,}) -- investigate")
        return 1
    log("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
