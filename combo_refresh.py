"""One-off: rebuild the combo series and re-measure it, after its admission
rules changed.

`scores/combo.py` gained `sent_decay_*` in the sentiment theme and moved
`sent_age`/`sent_stale` into EXCLUDED, which changed what h=1 admits (24 -> 27,
19 after dedup). The 176 stored sessions were computed under the old rules, so
the series and the code disagree -- and nothing downstream would say so.
"""
from __future__ import annotations
import sys, time
from datetime import datetime
import config
config.safe_console()
import scores, study, store, pandas as pd


def log(m):
    line = f"combo {datetime.now():%H:%M:%S} | {m}"
    print(line, flush=True)
    try:
        with (config.DATA / "_combo_refresh.log").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def main() -> int:
    scores.load_all()
    import catchup_scores as CS
    floor, why = CS.floor_for("combo")
    n_before = len(scores.sessions_stored("combo"))
    t0 = time.time()
    n = scores.catchup("combo", every=14, frm=floor, rebuild=True, verbose=True)
    log(f"rebuilt {n} session(s) (was {n_before}) in {(time.time()-t0)/60:.1f} min")

    # Drop combo's stale cells so the study cannot mix two rule sets.
    df = study.read()
    keep = df[df["module"] != "combo"]
    dropped = len(df) - len(keep)
    tmp = study.OUT.with_suffix(".parquet.tmp")
    keep.to_parquet(tmp, compression=config.COMPRESSION,
                    compression_level=config.COMPRESSION_LEVEL, index=False)
    store.atomic_replace(tmp, study.OUT)
    log(f"dropped {dropped} stale combo cell(s); re-measuring")

    rc = study.run(modules=["combo"])
    out = study.read()
    c = out[(out["module"] == "combo") & (out["size"] == "all")
            & out["metric"].str.startswith("combo_")
            & ~out["metric"].str.contains("_cov|_n")]
    log("COMBO after the rule change:")
    for _, r in c.sort_values(["metric", "horizon"]).iterrows():
        log(f"   {r['metric']:<14} h={int(r['horizon']):<3} t={r['t']:+6.2f} "
            f"hit={r['hit']:.0%} n={int(r['n_dates'])}")
    log(f"DONE (exit {rc})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
