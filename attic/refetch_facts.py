"""Re-fetch the fact store now that 20-F/40-F are accepted.

`backfill()` skips quarters already on disk, so a filter change needs the
partitions rewritten. Each quarter is fetched, filtered and atomically replaced,
so an interruption costs one quarter rather than the store.
"""
import sys

import config

config.safe_console()

import fundamentals as FD

if __name__ == "__main__":
    qs = FD.quarters(config.FUNDAMENTALS_YEARS)
    print(f"refetching {len(qs)} quarter(s) with 20-F/40-F accepted", flush=True)
    total = 0
    for i, q in enumerate(qs, 1):
        try:
            df = FD.fetch_quarter(q, verbose=False)
            if not df.empty:
                total += FD.write(df, q)
        except Exception as exc:                                 # noqa: BLE001
            print(f"  {q} FAILED {type(exc).__name__}", flush=True)
        if i % 10 == 0 or i == len(qs):
            print(f"  {i}/{len(qs)} quarters, {total:,} facts", flush=True)
    print("DONE", flush=True)
    FD.coverage_report()
    sys.exit(0)
