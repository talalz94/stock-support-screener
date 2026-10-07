#!/usr/bin/env python
"""Keep the machine awake while a job runs -- and record any sleep that happens anyway.

WHY THIS EXISTS. The nightly pipeline is a ~2 hour job on a laptop whose sleep
timer is NOT under the user's control: it was 5 hours on 2026-08-24 and 1 hour
on 2026-10-07, changed by something other than us. Asking the user to set it to
"never" does not hold, so the job has to protect itself.

Measured, 2026-09-04 .. 2026-10-07: 26 full sleeps, 3 shutdowns, 4 unclean
reboots; six runs took 7-12 hours of wall clock for ~2 hours of work (one hit
11.8h of the 12h kill limit); and a run's first step logged 28,495s for what
normally takes 6s.

TWO THINGS, and neither touches a system setting:

  1. A power REQUEST. The same mechanism a video player uses. While it is held
     Windows will not idle-sleep the machine. It is released when the job ends
     or the process dies, so it cannot leave the machine awake afterwards.
       - PowerRequestSystemRequired    stops the idle -> sleep transition
       - PowerRequestExecutionRequired stops background-process throttling if the
         machine enters Modern Standby anyway (this laptop does: events 506/507)
     SetThreadExecutionState is also set from the heartbeat thread as a fallback.

  2. A SLEEP-GAP DETECTOR. A heartbeat thread ticks every 15s. If two ticks are
     more than 90s apart the whole process was frozen, and the gap is logged and
     appended to data/_sleep_gaps.csv. This is the part that tells us whether (1)
     works: before this, the 2026-08-29 `validate` step logged 37,658s against 245s
     of measured work and the missing 9.4 hours stayed unexplained, because
     nothing recorded the machine being asleep.

WHAT IT CANNOT DO. It does not stop a forced restart (Windows Update restarted
the machine twice at 05:38 and 05:41 on 2026-10-07, killing the run), a lid
close with a "sleep" action, or the user choosing Sleep. Those need the job to be
RESUMABLE, which it already is: every step skips if it already ran for the
session, so re-running after an interruption just continues.
"""
from __future__ import annotations

import argparse
import ctypes
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

TICK_S = 15.0           # heartbeat period
GAP_S = 90.0            # two ticks further apart than this == the process was frozen

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
_REQ_SYSTEM, _REQ_EXECUTION = 1, 3
_IS_WIN = sys.platform == "win32"


class GapWatcher:
    """Pure logic, no clock of its own, so the maths can be tested exactly.

    Fed successive wall-clock readings, it returns the number of seconds the
    process was frozen when a reading arrives later than a heartbeat should.
    """

    def __init__(self, now: float, tick: float = TICK_S, gap: float = GAP_S):
        self.last, self.tick, self.gap = now, tick, gap

    def step(self, now: float) -> float | None:
        dt = now - self.last
        self.last = now
        return dt - self.tick if dt > self.gap else None


if _IS_WIN:
    class _Detailed(ctypes.Structure):
        _fields_ = [("LocalizedReasonModule", ctypes.c_void_p),
                    ("LocalizedReasonId", ctypes.c_ulong),
                    ("ReasonStringCount", ctypes.c_ulong),
                    ("ReasonStrings", ctypes.c_void_p)]

    class _Reason(ctypes.Union):
        _fields_ = [("Detailed", _Detailed), ("Simple", ctypes.c_wchar_p)]

    class _ReasonContext(ctypes.Structure):
        _fields_ = [("Version", ctypes.c_ulong), ("Flags", ctypes.c_ulong),
                    ("Reason", _Reason)]

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.PowerCreateRequest.restype = ctypes.c_void_p
    _k32.PowerCreateRequest.argtypes = [ctypes.POINTER(_ReasonContext)]
    _k32.PowerSetRequest.restype = ctypes.c_int
    _k32.PowerSetRequest.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _k32.PowerClearRequest.restype = ctypes.c_int
    _k32.PowerClearRequest.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _k32.CloseHandle.argtypes = [ctypes.c_void_p]
    _k32.SetThreadExecutionState.restype = ctypes.c_ulong
    _k32.SetThreadExecutionState.argtypes = [ctypes.c_ulong]


class Hold:
    """`with Hold(log=log, label="orchestrator"): ...`"""

    def __init__(self, log=print, label: str = "job", path: Path | None = None,
                 tick: float = TICK_S, gap: float = GAP_S, clock=time.time):
        self.log, self.label, self.path = log, label, path
        self.tick, self.gap, self.clock = tick, gap, clock
        self.status = "not started"
        self.gaps: list[tuple[str, str, float]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._handle = None
        self._ctx = None
        self._ready = threading.Event()

    # -- recording --------------------------------------------------------
    def _on_gap(self, start: float, end: float, frozen: float) -> None:
        s = datetime.fromtimestamp(start).isoformat(timespec="seconds")
        e = datetime.fromtimestamp(end).isoformat(timespec="seconds")
        self.gaps.append((s, e, frozen))
        self.log(f"    [keepawake] process was FROZEN for {frozen / 60:.1f} min "
                 f"({s} -> {e}): the machine slept or was suspended"
                 + (" despite the keep-awake request" if self._handle else ""))
        if self.path:
            try:
                new = not Path(self.path).exists()
                with open(self.path, "a", encoding="utf-8") as f:
                    if new:
                        f.write("start,end,frozen_s,label,request_held\n")
                    f.write(f"{s},{e},{frozen:.0f},{self.label},{bool(self._handle)}\n")
            except OSError:
                pass            # losing a diagnostic row must never stop the job

    # -- the request ------------------------------------------------------
    def _acquire(self) -> None:
        if not _IS_WIN:
            self.status = "non-Windows: no-op"
            return
        try:
            ctx = _ReasonContext()
            ctx.Version, ctx.Flags = 0, 1          # SIMPLE_STRING
            ctx.Reason.Simple = f"Screener {self.label}: scoring run in progress"
            h = _k32.PowerCreateRequest(ctypes.byref(ctx))
            if h in (None, 0, ctypes.c_void_p(-1).value):
                raise OSError(ctypes.get_last_error(), "PowerCreateRequest failed")
            got = [t for t in (_REQ_SYSTEM, _REQ_EXECUTION)
                   if _k32.PowerSetRequest(h, t)]
            if not got:
                _k32.CloseHandle(h)
                raise OSError(ctypes.get_last_error(), "PowerSetRequest failed")
            self._ctx, self._handle = ctx, h
            self.status = ("power request held: "
                           + "+".join({_REQ_SYSTEM: "system", _REQ_EXECUTION: "execution"}[t]
                                      for t in got))
        except Exception as exc:                                  # noqa: BLE001
            self.status = f"power request UNAVAILABLE ({repr(exc)[:60]}); fallback only"

    def _release(self) -> None:
        if self._handle:
            for t in (_REQ_SYSTEM, _REQ_EXECUTION):
                try:
                    _k32.PowerClearRequest(self._handle, t)
                except Exception:                                 # noqa: BLE001
                    pass
            try:
                _k32.CloseHandle(self._handle)
            except Exception:                                     # noqa: BLE001
                pass
            self._handle = None

    # -- the heartbeat ----------------------------------------------------
    def _run(self) -> None:
        # SetThreadExecutionState is PER THREAD and lapses when the thread ends,
        # so it is set here, on the thread that stays alive for the whole run.
        if _IS_WIN:
            try:
                _k32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
            except Exception:                                     # noqa: BLE001
                pass
        self._ready.set()
        w = GapWatcher(self.clock(), self.tick, self.gap)
        prev = w.last
        while not self._stop.wait(self.tick):
            now = self.clock()
            frozen = w.step(now)
            if frozen:
                self._on_gap(prev, now, frozen)
            prev = now
        if _IS_WIN:
            try:
                _k32.SetThreadExecutionState(ES_CONTINUOUS)
            except Exception:                                     # noqa: BLE001
                pass

    def __enter__(self) -> "Hold":
        self._acquire()
        self._thread = threading.Thread(target=self._run, name="keepawake",
                                        daemon=True)
        self._thread.start()
        self._ready.wait(2.0)
        self.log(f"    [keepawake] {self.status}")
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._release()
        total = sum(g[2] for g in self.gaps)
        self.log(f"    [keepawake] released; {len(self.gaps)} sleep gap(s) this run"
                 + (f", {total / 60:.0f} min frozen in total" if self.gaps else ""))


def selftest() -> int:
    # --- the gap arithmetic, exactly ---
    w = GapWatcher(1000.0, tick=15, gap=90)
    assert w.step(1015.0) is None, "a normal tick is not a gap"
    assert w.step(1030.5) is None, "jitter is not a gap"
    assert w.step(1030.5 + 60) is None, "a 60s stall is under the 90s threshold"
    g = w.step(1030.5 + 60 + 3600)
    assert g is not None and abs(g - 3585) < 1e-6, f"1h freeze reported as {g}"
    assert w.step(1030.5 + 60 + 3600 + 15) is None, "recovers cleanly after a gap"

    # --- a real heartbeat thread, with a clock that jumps an hour ---
    tmp = Path(tempfile.mkdtemp()) / "gaps.csv"
    seq = {"n": 0}

    def fake_clock() -> float:
        seq["n"] += 1
        base = 1_800_000_000.0 + seq["n"] * 0.05
        return base + (3600.0 if seq["n"] >= 5 else 0.0)

    lines: list[str] = []
    with Hold(log=lines.append, label="selftest", path=tmp, tick=0.02, gap=0.5,
              clock=fake_clock) as h:
        time.sleep(0.6)
    assert h.gaps, "the heartbeat thread missed a 1 hour clock jump"
    assert 3000 < h.gaps[0][2] < 3700, f"frozen seconds {h.gaps[0][2]}"
    rows = tmp.read_text(encoding="utf-8").splitlines()
    assert rows[0].startswith("start,end,frozen_s") and len(rows) == 2, rows
    assert any("FROZEN" in x for x in lines), "gap was not logged"
    assert not h._thread.is_alive(), "heartbeat thread left running"
    assert h._handle is None, "power request not released"

    # --- the request really registers, and really releases (Windows) ---
    if _IS_WIN:
        with Hold(log=lambda *_: None, label="selftest") as h2:
            assert h2._handle, f"no power request: {h2.status}"
            assert "system" in h2.status, h2.status
        assert h2._handle is None
    print("keepawake selftest OK (gap maths, heartbeat thread, log file, "
          "request acquire and release)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--hold", type=float, default=0, metavar="SECONDS",
                    help="hold the request for N seconds (to inspect it)")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.hold:
        with Hold(label="manual") as h:
            print(f"holding for {a.hold:.0f}s -- {h.status}", flush=True)
            time.sleep(a.hold)
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
