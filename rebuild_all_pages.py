"""Rebuild every profile page that already exists on disk.

The `profiles` orchestrator step only builds TODAY'S FLAGGED names, so after a
change to `stock_profile` the pages already on disk keep whatever markup they
were built with. On 2026-08-15 that meant 59 pages without the staleness banner
and `profiles` reporting "no flags file for this session" -- doing nothing,
successfully.

This walks `reports/stock/*.html` and rebuilds each one. Resumable: it skips
pages already newer than this file's own mtime, so a restart continues.
"""
from __future__ import annotations
import sys, time
from pathlib import Path
import config
config.safe_console()
import stock_profile as SP

def main() -> int:
    d = config.REPORTS / "stock"
    pages = sorted(p.stem for p in d.glob("*.html") if p.stem != "index")
    print(f"rebuilding {len(pages)} page(s)", flush=True)
    t0 = time.time(); ok = err = 0
    for i, tk in enumerate(pages, 1):
        try:
            SP.build(tk, verbose=False); ok += 1
        except Exception as exc:                                 # noqa: BLE001
            err += 1
            print(f"  ! {tk}: {type(exc).__name__}: {exc}"[:120], flush=True)
        if i % 10 == 0:
            el = time.time() - t0
            print(f"  {i}/{len(pages)}  {el/60:.1f}m  "
                  f"eta {(len(pages)-i)*el/i/60:.0f}m", flush=True)
    print(f"DONE in {(time.time()-t0)/60:.1f}m | {ok} rebuilt, {err} failed",
          flush=True)
    return 1 if err else 0

if __name__ == "__main__":
    sys.exit(main())
