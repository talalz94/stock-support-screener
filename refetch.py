"""
Top the fact store up from SEC's companyfacts API, per company.

    python refetch.py                every tradeable name
    python refetch.py --stale-only   only names whose newest period is old
    python refetch.py --limit 200    a bounded slice, for testing

WHY THIS SCRIPT HAS TO EXIST
-------------------------------
The bulk Financial Statement Data Sets are the backbone and they LAG. Probed
2026-08-13: `2026q2.zip` returns **HTTP 404** -- SEC has not published it. The
newest set that exists is 2026q1, so every filing made since roughly March 2026
is invisible to the bulk route no matter how often it is fetched. This is
structural, not a missed download.

`fundamentals.stale_names()` and `backfill_companyfacts()` were written on
2026-08-10 to cover exactly that gap, one company at a time, from the API that
DOES have the recent quarters. The script that called them, `run_refetch.py`,
was subsequently deleted while the `Screener-Refetch` scheduled task kept
pointing at it -- so the task ran, failed to find the file, and reported
success. Nothing surfaced it, and the universe quietly froze at the last bulk
quarter:

    measured 2026-08-13, 150-ticker random sample vs Yahoo
      median staleness where our figures AGREE with Yahoo    44 days
      median staleness where they DISAGREE                  225 days
      worst seen (VSBC)                                     925 days

The stored numbers were CORRECT FOR THEIR PERIOD the whole time. They were just
old, which on a page is indistinguishable from wrong -- and is why googling a
ticker kept disagreeing with the screener.

RESUMABLE, because this takes ~2 hours and laptops close. Progress is recorded
per batch, so a restart continues from the last completed batch rather than
re-fetching what is already on disk.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime

import config

config.safe_console()

LOG = config.DATA / "_refetch.log"
STATE = config.DATA / "_refetch_state.json"
BATCH = 250


def log(m: str) -> None:
    line = f"refetch {datetime.now():%m-%d %H:%M:%S} | {m}"
    try:
        print(line, flush=True)
    except (ValueError, OSError):
        pass
    try:
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save(done: list[str], total: int) -> None:
    try:
        tmp = STATE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"done": done, "total": total,
                                   "at": datetime.now().isoformat(
                                       timespec="seconds")}),
                       encoding="utf-8")
        tmp.replace(STATE)
    except OSError:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Refresh fundamentals per company.")
    ap.add_argument("--stale-only", action="store_true",
                    help="only names whose newest period is older than the "
                         "freshness threshold (cheaper, but needs a full "
                         "facts_asof pass to work out who they are)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--restart", action="store_true",
                    help="ignore recorded progress and start over")
    a = ap.parse_args()

    import bars
    import fundamentals as FD

    t0 = time.time()
    log("=" * 62)
    log("REFETCH START -- companyfacts top-up (bulk 2026q2 is not published)")

    if a.stale_only:
        log("working out which names are stale (one full facts pass)...")
        targets = FD.refresh_targets()
    else:
        # DEFAULT TO EVERYTHING TRADEABLE. Deciding who is stale costs a full
        # `facts_asof` over the universe, which on this machine ran past ten
        # minutes and is most of the saving it was meant to buy. Re-fetching a
        # current company is cheap and idempotent -- `_flush` replaces that
        # CIK's rows rather than appending -- so the blunt option is both
        # faster overall and less likely to leave someone behind.
        targets = sorted(set(bars.tradeable_universe()))
    if a.limit:
        targets = targets[:a.limit]

    st = {} if a.restart else _state()
    done = [] if a.restart else list(st.get("done", []))
    if done:
        log(f"resuming: {len(done):,} companies already refetched")
    todo = [t for t in targets if t not in set(done)]

    log(f"{len(todo):,} to fetch of {len(targets):,} target(s), "
        f"{FD.CF_WORKERS} workers, ~{len(todo) * 1.98 / 60:.0f} min at the "
        f"measured 1.98 s/company")

    failed_all: list[str] = []
    facts_all = 0
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        s = time.time()
        try:
            r = FD.backfill_companyfacts(chunk, verbose=False)
        except Exception as exc:                                 # noqa: BLE001
            log(f"  batch failed hard ({type(exc).__name__}: {exc}) -- "
                f"stopping so it can be resumed")
            return 1
        facts_all += r.get("facts", 0)
        failed_all += r.get("failed", []) or []
        done += chunk
        _save(done, len(targets))
        el = time.time() - s
        left = (len(todo) - i - len(chunk)) * (el / max(1, len(chunk))) / 60
        log(f"  {len(done):,}/{len(targets):,} companies | "
            f"{r.get('companies', 0)} with data, {r.get('empty', 0)} empty, "
            f"{len(r.get('failed', []) or [])} failed | "
            f"{el / 60:.1f} min this batch | ~{left:.0f} min left")

    mins = (time.time() - t0) / 60
    log(f"REFETCH DONE in {mins:.0f} min | {facts_all:,} facts | "
        f"{len(failed_all)} failure(s)")
    if failed_all:
        log(f"  failures (first 20): {', '.join(failed_all[:20])}")

    # Report the freshness actually achieved rather than assuming it worked.
    try:
        import calendar_us
        import pandas as pd
        asof = calendar_us.last_closed_session()
        f = FD.facts_asof(asof, sorted(set(bars.tradeable_universe())))
        if not f.empty and "last_ddate" in f.columns:
            age = (pd.Timestamp(asof)
                   - pd.to_datetime(f["last_ddate"], errors="coerce")).dt.days
            log(f"  newest-period age now: median {age.median():.0f}d, "
                f"p90 {age.quantile(.9):.0f}d, "
                f"{(age > 200).sum():,} still older than 200d "
                f"({(age > 200).mean() * 100:.1f}%)")
    except Exception as exc:                                     # noqa: BLE001
        log(f"  freshness report unavailable ({type(exc).__name__})")

    return 1 if failed_all and len(failed_all) > len(targets) * 0.05 else 0


if __name__ == "__main__":
    sys.exit(main())
