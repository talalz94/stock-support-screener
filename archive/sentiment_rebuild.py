"""
One-off: rebuild the sentiment series with the recency metrics, then measure
whether recency weighting actually helps.

    python sentiment_rebuild.py --dry-run
    python sentiment_rebuild.py

WHY A REBUILD AND NOT A CATCHUP: `sent_decay_*`, `sent_age` and `sent_stale` are
new, so the 320 stored sessions carry the old metric set. `catchup` skips
sessions it already has, which would leave a series that is half one definition
and half another -- the same mixed-data problem the overnight chain's staging
exists to prevent.

THE POINT IS THE MEASUREMENT, NOT THE METRIC. `sent_mean_30d` keeps its 320
sessions and its measured t-stat; the decayed twin is added beside it so
`study.py` can say which one predicts. Neither gets promoted onto a dashboard
composite until that answer exists.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

import config

config.safe_console()

import scores                                                    # noqa: E402


def log(m: str) -> None:
    line = f"srb   {datetime.now():%Y-%m-%d %H:%M:%S} | {m}"
    try:
        print(line, flush=True)
    except (ValueError, OSError):
        pass
    try:
        with config.LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Rebuild sentiment with decay.")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    scores.load_all()
    have = scores.sessions_stored("sentiment")
    log(f"sentiment series: {len(have)} session(s), {have[0]} -> {have[-1]}")
    if a.dry_run:
        log("--dry-run: would rebuild every one of those, then re-measure")
        return 0

    # REBUILD THE EXACT STORED SESSIONS, not a resampled grid.
    #
    # `scores.catchup(every=14)` walks its own grid, which covered only 178 of
    # the 320 stored dates -- so `sent_decay_30d` would exist on 178 sessions
    # while `sent_mean_30d` kept all 320, and the study would be comparing two
    # metrics measured over different date sets. That is not a fair comparison,
    # and nothing in the output would have said so.
    import bars
    mod = scores.get("sentiment")
    t0, built, failed = time.time(), 0, []
    for i, sess in enumerate(have, 1):
        try:
            uni = bars.tradeable_universe(sess)
            if not uni:
                continue
            rows = mod.compute(sess, uni, allow_partial=True)
            if rows is None or rows.empty:
                continue
            scores.write(rows, session=sess, module="sentiment")
            built += 1
        except Exception as exc:                                 # noqa: BLE001
            failed.append(f"{sess}({type(exc).__name__})")
        if i % 20 == 0 or i == len(have):
            el = (time.time() - t0) / 60
            eta = el / max(i, 1) * (len(have) - i)
            log(f"  {i}/{len(have)} ({built} written, {el:.1f}m, eta {eta:.0f}m)")
    log(f"rebuilt {built} of {len(have)} session(s) in "
        f"{(time.time() - t0) / 60:.1f} min")
    if failed:
        log(f"  FAILED: {', '.join(failed[:8])}"
            + (f" +{len(failed) - 8} more" if len(failed) > 8 else ""))

    # Every stored session must now carry the new metrics, or the comparison
    # below is between different date sets.
    got = set(scores.read(module="sentiment",
                          metrics=["sent_age"])["session"].astype(str))
    missing = [s for s in have if s not in got]
    if missing:
        log(f"  ! {len(missing)} session(s) still lack the new metrics: "
            f"{missing[:5]} -- the decayed/plain comparison would be unfair")
        return 1

    # Re-measure ONLY sentiment. Its old cells are stale the moment the series
    # changes, so they are dropped rather than left to be read as current.
    import pandas as pd
    import store as _st
    import study
    df = study.read()
    if len(df):
        keep = df[df["module"] != "sentiment"]
        dropped = len(df) - len(keep)
        tmp = study.OUT.with_suffix(".parquet.tmp")
        keep.to_parquet(tmp, compression=config.COMPRESSION,
                        compression_level=config.COMPRESSION_LEVEL, index=False)
        _st.atomic_replace(tmp, study.OUT)
        log(f"dropped {dropped} stale sentiment cell(s); re-measuring")

    rc = study.run(modules=["sentiment"])
    out = study.read()
    s = out[(out["module"] == "sentiment") & (out["size"] == "all")]
    log(f"measured {len(s)} sentiment cell(s) (exit {rc})")

    # The comparison this whole exercise exists to produce.
    log("")
    log("  DECAYED vs PLAIN, per window and horizon:")
    for w in (5, 30, 90):
        for h in (1, 5, 20, 60):
            a_ = s[(s["metric"] == f"sent_mean_{w}d") & (s["horizon"] == h)]
            b_ = s[(s["metric"] == f"sent_decay_{w}d") & (s["horizon"] == h)]
            if len(a_) and len(b_):
                ta, tb = float(a_.iloc[0]["t"]), float(b_.iloc[0]["t"])
                better = "decay" if abs(tb) > abs(ta) else "plain"
                log(f"    {w:>2}d h={h:<3} plain t={ta:+6.2f}  "
                    f"decay t={tb:+6.2f}   -> {better}")
    log("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
