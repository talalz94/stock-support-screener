"""Refetch the fact store with IFRS aliases live. Newest quarters first."""
import sys

import config

config.safe_console()

import fundamentals as FD

if __name__ == "__main__":
    print("refetch: force=True, newest-first, rate-limited, retrying",
          flush=True)
    res = FD.backfill(force=True, newest_first=True, verbose=True)
    print(f"RESULT ok={res['ok']} fetched={res['fetched']} "
          f"failed={len(res['failed'])}", flush=True)
    FD.coverage_report()
    sys.exit(0 if res["ok"] else 1)
