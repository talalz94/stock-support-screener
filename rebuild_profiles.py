"""
Rebuild profile pages in bulk.

    python rebuild_profiles.py --dry-run        list what would be rebuilt
    python rebuild_profiles.py --all            every page already on disk
    python rebuild_profiles.py --tickers AAPL,MSFT
    python rebuild_profiles.py --all --limit 20

WHY IT EXISTS: `stock_profile.build` is cheap once the caches are primed and
expensive when they are not. Priming once for a whole batch is the difference
between ~3s and ~23s a page, so rebuilding N pages one-command-at-a-time costs
roughly 8x what this does.

NO ARGUMENTS DOES NOTHING, deliberately. The previous version had a `__main__`
block and no argument parsing, so every invocation -- `--help` included --
started rebuilding every page on disk. Three sibling scripts shared that shape
and are now in `attic/`; see `attic/README.md`.
"""

from __future__ import annotations

import argparse
import sys
import time

import config

config.safe_console()

import stock_profile                                             # noqa: E402


def existing_pages() -> list[str]:
    """Tickers that already have a profile page. `_`-containing stems are the
    dated snapshots, not tickers."""
    d = config.REPORTS / "stock"
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.html")
                  if p.stem != "index" and "_" not in p.stem)


def prime(tickers: list[str]) -> None:
    """Fill both caches once for the whole batch.

    The score history AND the fact store. `history()` is called twice per page
    (annual + quarterly) and re-scans every stored quarter per ticker without
    this -- measured 105 parquet opens and ~20s a page. The orchestrator's
    profiles step primes both; this script used to prime only the first, which
    is why it stayed slow after the other was fixed.
    """
    import calendar_us
    asof = calendar_us.last_closed_session()
    for mod in config.SCORE_MODULES:
        try:
            stock_profile.prime_history(mod, tickers, asof)
        except Exception:                                        # noqa: BLE001
            pass
    try:
        import fundamentals as FD
        FD.prime_history(tickers, 16, "Q")
    except Exception:                                            # noqa: BLE001
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Rebuild profile pages in bulk.")
    ap.add_argument("--all", action="store_true",
                    help="every ticker that already has a page on disk")
    ap.add_argument("--tickers", default="", help="comma-separated")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if a.tickers:
        pages = [t.strip().upper() for t in a.tickers.split(",") if t.strip()]
    elif a.all:
        pages = existing_pages()
    else:
        ap.print_help()
        print("\nNothing to do. Pass --all or --tickers SYM[,SYM].")
        return 2

    if a.limit:
        pages = pages[:a.limit]
    if not pages:
        print("  no pages to rebuild")
        return 0

    print(f"\n  {len(pages)} profile(s): {', '.join(pages[:8])}"
          f"{' ...' if len(pages) > 8 else ''}")
    if a.dry_run:
        print("  --dry-run: nothing written\n")
        return 0

    t0 = time.time()
    prime(pages)
    print(f"  caches primed in {time.time() - t0:.0f}s "
          f"(scores + fact store)", flush=True)

    ok, failed = 0, []
    for i, t in enumerate(pages, 1):
        try:
            stock_profile.build(t, verbose=False)
            ok += 1
        except Exception as exc:                                 # noqa: BLE001
            # Named, not counted. A page that silently never appears is the
            # failure mode the profiles step already had to fix once.
            failed.append(f"{t}({type(exc).__name__})")
        if i % 10 == 0 or i == len(pages):
            print(f"  {i}/{len(pages)} ({ok} ok)", flush=True)

    try:
        stock_profile.write_index(verbose=False)
    except Exception as exc:                                     # noqa: BLE001
        print(f"  ! index not rewritten: {exc!r}"[:120])

    print(f"\n  rebuilt {ok} of {len(pages)} in {(time.time() - t0) / 60:.1f} min")
    if failed:
        print(f"  FAILED: {', '.join(failed[:10])}"
              + (f" +{len(failed) - 10} more" if len(failed) > 10 else ""))
        return 1
    print("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
