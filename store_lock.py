#!/usr/bin/env python
"""One writer at a time per store: a lock file holding the writer's pid and start time.

WHY. 2026-10-27 18:49: a scheduled orchestrator retry ran the `congress` step (re-reading
2026's 437 reports, 1,032 s) WHILE a manual `congress.py --backfill` was writing the same
data/congress/trades.parquet and _ledger.parquet. Every write is tmp + replace, so no file
was corrupted -- but each process merged its rows into the copy IT had read, so the last
writer won and the other's rows were silently dropped. A later full --reparse rebuilt the
store; nothing stopped it happening again, and insider.py's trades_edgar/ + _ledger.parquet
have the same shape.

THE RULE. Every function that writes a store (ingest, backfill, reparse, update) runs inside
`writer(store_dir, label)`. The lock is `<store>/.write.lock`: {"pid", "started", "label"}.
  * Created with O_EXCL, so two processes cannot both create it.
  * Re-entrant within one process: `update()` -> `ingest()` takes it once.
  * A holder that is GONE is a stale lock and is taken over (and said so): its pid no
    longer exists, or the pid now belongs to a process started after the lock was written
    (Windows reuses pids after a restart -- the orchestrator lock learned this first).
  * When the wait runs out: `StoreBusy`. The nightly steps wait a minute, then report the
    step SKIPPED (it stays due, so the next retry tries again); a manual CLI run waits
    for the nightly instead.
Readers take no lock: every write is a whole-file replace, so a reader sees old or new.

Run: python store_lock.py --selftest     (spawns a second process to hold a real lock)
"""
from __future__ import annotations

import json
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

LOCK_NAME = ".write.lock"
UNREADABLE_STALE_S = 60      # a lock file still empty/garbled after this long is debris
_HELD: dict[str, int] = {}   # resolved lock path -> depth, for re-entry in this process


class StoreBusy(RuntimeError):
    """Another live process holds the store's writer lock and the wait ran out."""

    def __init__(self, path: Path, info: dict | None):
        self.path, self.info = path, info or {}
        super().__init__(f"{path.parent.name} store is being written by {self.holder()}")

    def holder(self) -> str:
        i = self.info
        return (f"pid {i.get('pid', '?')} ({i.get('label', 'unknown')}, since {i.get('started', '?')})"
                if i else "another process")


def pid_alive(pid, started: str | None = None) -> bool | None:
    """True/False, or None when we cannot tell (moved here from orchestrator._pid_alive).

    Not os.kill(pid, 0): on Windows that TERMINATES the process it asks about. And a pid
    whose process began after the lock was written cannot be the writer -- Windows reuses
    pids after a restart."""
    if not pid:
        return None
    try:
        import psutil
        if not psutil.pid_exists(int(pid)):
            return False
        if started:
            try:
                t_lock = datetime.fromisoformat(started).timestamp()
                t_proc = psutil.Process(int(pid)).create_time()
                if t_proc > t_lock + 60:          # created after the lock: reused id
                    return False
            except (ValueError, psutil.Error):
                pass                              # cannot tell -> trust pid_exists
        return True
    except ImportError:
        return None
    except Exception:                                            # noqa: BLE001
        return None


def lock_path(directory) -> Path:
    return Path(directory) / LOCK_NAME


def holder(directory) -> dict | None:
    """The current lock's contents, {} if the file exists but is unreadable, None if absent."""
    p = lock_path(directory)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {}


def _stale(p: Path, info: dict | None) -> bool:
    if info is None:
        return False                                             # vanished: just retry
    if not info:
        try:
            return time.time() - p.stat().st_mtime > UNREADABLE_STALE_S
        except OSError:
            return False
    return pid_alive(info.get("pid"), info.get("started")) is False


@contextmanager
def writer(directory, label: str, wait_s: float = 0.0, poll_s: float = 5.0, log=None):
    """Hold `<directory>/.write.lock` for the block. Raises StoreBusy after `wait_s`."""
    p = lock_path(directory)
    key = str(p.resolve())
    if _HELD.get(key):                                          # re-entry in this process
        _HELD[key] += 1
        try:
            yield
        finally:
            _HELD[key] -= 1
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    say = log or (lambda m: None)
    deadline = time.monotonic() + max(0.0, wait_s)
    waiting = False
    while True:
        try:
            fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            info = holder(p.parent)
            if _stale(p, info):
                # Take it over only if it is still the SAME stale lock: two waiters must not
                # both delete, or the second would delete the first one's fresh lock.
                if holder(p.parent) == info:
                    try:
                        p.unlink()
                        say(f"stale writer lock on {p.parent.name} taken over: {info or 'unreadable'}")
                    except FileNotFoundError:
                        pass
                    except OSError:
                        time.sleep(0.2)
                continue
            left = deadline - time.monotonic()
            if left <= 0:
                raise StoreBusy(p, info)
            if not waiting:
                say(f"waiting up to {wait_s:.0f}s for the {p.parent.name} writer lock: "
                    f"{StoreBusy(p, info).holder()}")
                waiting = True
            time.sleep(min(poll_s, left))
            continue
        mine = {"pid": os.getpid(), "started": datetime.now().isoformat(timespec="seconds"), "label": label}
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(mine, fh)
        break
    _HELD[key] = 1
    try:
        yield
    finally:
        _HELD.pop(key, None)
        if holder(p.parent) == mine:                            # never delete someone else's
            try:
                p.unlink()
            except OSError:
                pass


# ============================================================ selftest
_CHILD = r"""
import sys, time
sys.path.insert(0, sys.argv[1])
import store_lock
with store_lock.writer(sys.argv[2], "selftest child"):
    print("held", flush=True)
    time.sleep(float(sys.argv[3]))
"""


def selftest() -> int:
    import subprocess
    import tempfile
    here = str(Path(__file__).resolve().parent)
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        # 1. take, re-enter, release
        with writer(d, "outer"):
            assert holder(d)["label"] == "outer"
            with writer(d, "inner"):
                assert holder(d)["label"] == "outer", "re-entry must not rewrite the lock"
            assert lock_path(d).exists(), "inner release must not free the outer lock"
        assert not lock_path(d).exists(), "lock left behind after release"
        # 2. a dead holder is stale and taken over
        lock_path(d).write_text(json.dumps({"pid": 2 ** 22 + 7, "started": "2020-01-01T00:00:00",
                                            "label": "dead"}), encoding="utf-8")
        seen = []
        with writer(d, "after dead", log=seen.append):
            assert holder(d)["label"] == "after dead"
        assert any("stale" in m for m in seen), seen
        # 3. a LIVE holder in another process: busy at once, then acquirable once it exits
        child = subprocess.Popen([sys.executable, "-c", _CHILD, here, str(d), "4"],
                                 stdout=subprocess.PIPE, text=True)
        assert child.stdout.readline().strip() == "held"
        try:
            with writer(d, "too soon", wait_s=0):
                raise AssertionError("took a lock a live process holds")
        except StoreBusy as exc:
            assert "selftest child" in str(exc), exc
        t0 = time.monotonic()
        with writer(d, "patient", wait_s=30, poll_s=0.5):
            assert holder(d)["label"] == "patient"
        assert time.monotonic() - t0 < 30
        child.wait(timeout=10)
        # 4. an empty lock file is held while fresh, debris once old
        lock_path(d).write_text("", encoding="utf-8")
        try:
            with writer(d, "fresh empty", wait_s=0):
                raise AssertionError("took a lock whose writer may be mid-write")
        except StoreBusy:
            pass
        old = time.time() - UNREADABLE_STALE_S - 5
        os.utime(lock_path(d), (old, old))
        with writer(d, "old empty"):
            pass
    print("store_lock selftest OK (re-entry, stale takeover, live holder across processes, debris)")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    print(__doc__)
    sys.exit(2)
