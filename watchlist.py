"""
The watchlist: tickers you are actually tracking.

    python watchlist.py                    show it
    python watchlist.py --add NVDA,HZO     add
    python watchlist.py --remove HZO       remove
    python watchlist.py --clear
    python watchlist.py --selftest

WHY THIS IS A FILE AND NOT localStorage
---------------------------------------
A browser-local list is invisible to everything else. Stored on disk, the
watchlist can do the thing that actually matters: **drive verification.**

`validate` samples 60 rotating names a night out of ~3,500, so any individual
name waits weeks for its turn. Names on this list are checked EVERY night,
because they are the ones a wrong number would actually cost something on.
That turns "Claude spot-checked some tickers once" into "the names I trade are
under continuous test", which is the only version of trust worth having here.

`explore.py` still keeps a localStorage mirror so the star works when the page
is opened as a file with no server running -- but the file is the source of
truth, and `/api/watchlist` syncs the two whenever the server is up.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
No notes, no target prices, no position sizes, no tags. Those are a portfolio
tracker, which is a different program with a different failure mode. This is a
set of tickers, and its whole value is that everything else can read it.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

import config

config.safe_console()

FILE = config.DATA / "_watchlist.json"

# A ticker the universe has never heard of is almost always a typo, and a typo
# that silently joins the list would quietly consume a verification slot every
# night while checking nothing.
MAX_ENTRIES = 500


def _clean(tickers) -> list[str]:
    out = []
    for t in tickers or []:
        t = str(t).strip().upper()
        if t and t not in out:
            out.append(t)
    return out


def load() -> list[str]:
    """The list, or empty. Never raises -- a corrupt file must not take down
    the page that reads it."""
    try:
        raw = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(raw, dict):                      # {"tickers": [...]} form
        raw = raw.get("tickers", [])
    return _clean(raw) if isinstance(raw, list) else []


def save(tickers) -> list[str]:
    """Write and return what was written. Sorted, so a diff of this file is
    readable and two clients adding the same name converge."""
    keep = sorted(_clean(tickers))[:MAX_ENTRIES]
    config.DATA.mkdir(parents=True, exist_ok=True)
    payload = {"updated": datetime.now().isoformat(timespec="seconds"),
               "tickers": keep}
    tmp = FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        import store
        store.atomic_replace(tmp, FILE)            # same durability as the stores
    except Exception:                                            # noqa: BLE001
        tmp.replace(FILE)
    return keep


def add(tickers) -> list[str]:
    return save(load() + _clean(tickers))


def remove(tickers) -> list[str]:
    drop = set(_clean(tickers))
    return save([t for t in load() if t not in drop])


def unknown(tickers=None) -> list[str]:
    """Names not in the tradeable universe. REPORTED, not silently dropped --
    a delisted ticker you are still watching is a legitimate thing to have on
    the list, and this project's rule is that the caller is told rather than
    quietly corrected."""
    try:
        import bars
        uni = set(bars.tradeable_universe())
    except Exception:                                            # noqa: BLE001
        return []
    return [t for t in (tickers if tickers is not None else load())
            if t not in uni]


def selftest(verbose: bool = True) -> None:
    """Exercises the round-trip on a temporary path, never the real file."""
    global FILE
    real, fails = FILE, []
    FILE = config.DATA / "_watchlist_selftest.json"
    try:
        save([])
        if load() != []:
            fails.append("empty round-trip failed")

        add(["nvda", " hzo ", "NVDA"])             # case, whitespace, duplicate
        got = load()
        if got != ["HZO", "NVDA"]:
            fails.append(f"normalise/dedupe failed: {got}")

        remove(["HZO"])
        if load() != ["NVDA"]:
            fails.append(f"remove failed: {load()}")

        # A corrupt file must read as empty rather than raising, because the
        # page that reads it must still render.
        FILE.write_text("{ not json", encoding="utf-8")
        if load() != []:
            fails.append("corrupt file did not read as empty")

        save(["AAA"] * 3 + ["BBB"])
        if load() != ["AAA", "BBB"]:
            fails.append("duplicate collapse failed")
    finally:
        try:
            FILE.unlink(missing_ok=True)
        except OSError:
            pass
        FILE = real

    if fails:
        raise AssertionError("watchlist selftest FAILED:\n  " + "\n  ".join(fails))
    if verbose:
        print(f"watchlist selftest OK ({len(load())} on the real list)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Tickers you are tracking.")
    ap.add_argument("--add")
    ap.add_argument("--remove")
    ap.add_argument("--clear", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        selftest()
        return 0
    if a.clear:
        save([])
    if a.add:
        add(a.add.split(","))
    if a.remove:
        remove(a.remove.split(","))

    tk = load()
    if not tk:
        print("watchlist is empty  (python watchlist.py --add NVDA,HZO)")
        return 0
    print(f"{len(tk)} ticker(s):  {' '.join(tk)}")
    bad = unknown(tk)
    if bad:
        print(f"  not in the tradeable universe: {' '.join(bad)}")
        print("  kept anyway -- delisted names are a legitimate thing to watch")
    return 0


if __name__ == "__main__":
    sys.exit(main())
