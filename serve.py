"""
Opt-in local control server, so the dashboard's buttons actually run things.

    python serve.py                 http://127.0.0.1:8787
    python serve.py --port 9000
    python serve.py --open

WHY THIS IS A SEPARATE, HAND-STARTED PROGRAM
----------------------------------------------
A static HTML file cannot spawn a process, so "click a button to run the job"
needs a server. But a resident server is exactly what this project has avoided
everywhere else -- the whole design is one-shot scripts that exit, costing
nothing when idle. Making it a separate opt-in program keeps both properties:
the hub still works as a plain file with copy-able commands, and when you WANT
buttons you start this and get them. Nothing depends on it running.

That is progressive enhancement, and it is load-bearing here: if this server is
not running, `reports/index.html` opened over file:// behaves exactly as before.

SECURITY, BECAUSE THIS EXECUTES CODE ON REQUEST
-------------------------------------------------
This endpoint runs subprocesses, so it is written as if hostile input will reach
it -- a browser tab on any site can POST to 127.0.0.1.

  1. Binds 127.0.0.1 ONLY. Never 0.0.0.0, which would expose it to the network.
  2. The step name is validated against `orchestrator.BY_NAME` and the process
     is spawned with an ARGUMENT LIST, never a shell string. There is no path by
     which request text reaches a shell -- an unknown step is a 400, not an
     escaped command.
  3. Requires a same-origin-ish custom header, which a simple cross-site form
     POST cannot set without triggering a CORS preflight this server refuses.
  4. One job at a time, gated on the orchestrator's own lock file.
  5. Serves ONLY `reports/`, with the path resolved and confirmed to still be
     inside it, so `../../.env` cannot be fetched.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import config

config.safe_console()

import orchestrator                                              # noqa: E402

HOST = "127.0.0.1"          # never 0.0.0.0 -- see the module docstring
DEFAULT_PORT = 8787
GUARD_HEADER = "X-Screener-Control"     # must be present on any POST

_run_lock = threading.Lock()
_last_request = [time.time()]   # list so the watchdog thread can mutate it
_current: dict = {"step": None, "started": None, "pid": None}


def already_serving(port: int, timeout: float = 1.0) -> bool:
    """Is an instance of this server already answering on `port`?

    Checked BEFORE binding. `ThreadingHTTPServer` on a taken port raises
    OSError 10048 and prints a traceback, which for someone who simply
    double-clicked the shortcut twice looks like the app is broken -- when in
    fact the first one is running perfectly and all they need is the browser
    pointed at it.
    """
    import socket
    try:
        with socket.create_connection((HOST, port), timeout=timeout):
            return True
    except OSError:
        return False


def _is_victim(cmd: str, pid: int, me: int) -> bool:
    """Should `--stop` terminate this process? Pure, so it can be tested
    without actually killing anything."""
    if pid == me or "serve.py" not in cmd:
        return False
    return "--stop" not in cmd


def stop_running(port: int = DEFAULT_PORT) -> tuple[int, str]:
    """Stop every serve.py except this process. Returns (count, detail).

    Terminated politely first -- the handler thread gets to finish whatever
    page it is mid-build on -- then killed only if it will not go. A hard kill
    during a profile build can leave a half-written HTML file behind, and a
    truncated page renders as a broken one rather than an absent one.
    """
    try:
        import psutil
    except ImportError:
        return 0, ("psutil not installed; close the server window, or "
                   "Ctrl-C in it")
    me = os.getpid()
    victims = []
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmd = " ".join(p.info.get("cmdline") or [])
        except Exception:                                        # noqa: BLE001
            continue
        # NEVER a process that is itself stopping. `python serve.py --stop`
        # matches "serve.py" too, and excluding only our own pid was not
        # enough -- under a shell wrapper the invocation can appear as more
        # than one process, so `--stop` terminated itself and exited 15
        # without stopping the actual server.
        if not _is_victim(cmd, p.info["pid"], me):
            continue
        victims.append(p)
    if not victims:
        return 0, "no server was running"
    for p in victims:
        try:
            p.terminate()
        except Exception:                                        # noqa: BLE001
            pass
    gone, alive = psutil.wait_procs(victims, timeout=8)
    for p in alive:
        try:
            p.kill()
        except Exception:                                        # noqa: BLE001
            pass
    return len(victims), (f"stopped {len(victims)} server process(es)"
                          + (f", {len(alive)} needed a hard kill" if alive else ""))


def _asof_now() -> str:
    """Last closed session, for priming the caches an on-demand build reads."""
    import calendar_us
    return calendar_us.last_closed_session()


def _log(msg: str) -> None:
    line = f"serve {datetime.now():%H:%M:%S} | {msg}"
    try:
        print(line, flush=True)
    except (ValueError, OSError):
        pass


def _safe_path(url_path: str) -> Path | None:
    """Resolve inside reports/ or return None. Blocks traversal."""
    rel = url_path.lstrip("/") or "index.html"
    root = config.REPORTS.resolve()
    try:
        p = (root / rel).resolve()
    except (OSError, ValueError):
        return None
    if p == root or root in p.parents:
        return p if p.is_file() else None
    return None


def _spawn(step: str) -> dict:
    """Run one orchestrator step in a detached child. Argument list, no shell."""
    if step not in orchestrator.BY_NAME:
        # Unknown names never reach a process at all.
        return {"ok": False, "error": f"unknown step {step!r}"}

    lock = orchestrator._lock_info()
    if lock:
        return {"ok": False,
                "error": f"a run already holds the lock (pid {lock.get('pid')})"}

    if not _run_lock.acquire(blocking=False):
        return {"ok": False, "error": "this server is already running a step"}
    try:
        exe = sys.executable or "python"
        proc = subprocess.Popen(
            [exe, "orchestrator.py", "--step", step],
            cwd=str(config.ROOT),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        _current.update({"step": step, "pid": proc.pid,
                         "started": datetime.now().isoformat(timespec="seconds")})
        _log(f"started {step} (pid {proc.pid})")

        def _wait():
            try:
                proc.wait()
                _log(f"{step} finished (exit {proc.returncode})")
            finally:
                _current.update({"step": None, "pid": None, "started": None})
                _run_lock.release()

        threading.Thread(target=_wait, daemon=True).start()
        return {"ok": True, "step": step, "pid": proc.pid}
    except Exception as exc:                                     # noqa: BLE001
        _run_lock.release()
        return {"ok": False, "error": repr(exc)[:200]}


class Handler(BaseHTTPRequestHandler):
    server_version = "ScreenerControl/1"

    def log_message(self, fmt, *args):        # quieter than the default
        pass

    def handle_one_request(self):
        _last_request[0] = time.time()
        return super().handle_one_request()

    # ------------------------------------------------------------- helpers
    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------------ GET
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]

        if path == "/api/steps":
            return self._json(200, {"steps": [
                {"name": s.name, "cadence": s.cadence, "desc": s.desc}
                for s in orchestrator.REGISTRY]})

        if path == "/api/status":
            try:
                jobs = orchestrator.read_jobs().tail(200)
                rows = json.loads(jobs.to_json(orient="records"))
            except Exception as exc:                             # noqa: BLE001
                return self._json(500, {"error": repr(exc)[:200]})
            return self._json(200, {"running": _current, "jobs": rows})

        # Profiles are built for flagged names and on request, never for all
        # 3,464 tickers (that is ~90 MB of HTML nobody asked for). In live mode
        # a miss is built on demand, so every row in explore.html is clickable.
        if path.startswith("/stock/") and path.endswith(".html"):
            tk = path[len("/stock/"):-len(".html")]
            if tk.isalnum() or (tk.replace(".", "").replace("-", "").isalnum()):
                target = config.REPORTS / "stock" / f"{tk.upper()}.html"
                if not target.exists():
                    try:
                        import stock_profile
                        _log(f"building profile for {tk.upper()} on demand")
                        # PRIME FIRST, exactly as the batch path does.
                        # `build()` alone re-scans every score partition per
                        # module and the fact store twice (annual + quarterly
                        # for the toggle). The nightly step primes once for the
                        # whole batch and gets ~20s a page; this path primed
                        # nothing, so a single on-demand build paid the full
                        # cold cost -- measured at 252s, against the "about 20
                        # seconds" the index page promises the user.
                        one = [tk.upper()]
                        for _m in config.SCORE_MODULES:
                            try:
                                stock_profile.prime_history(_m, one, _asof_now())
                            except Exception:                    # noqa: BLE001
                                pass
                        try:
                            import fundamentals as _FD
                            _FD.prime_history(one, 16, "Q")
                        except Exception:                        # noqa: BLE001
                            pass
                        stock_profile.build(tk.upper(), verbose=False)
                    except Exception as exc:                     # noqa: BLE001
                        return self._json(404, {
                            "error": f"could not build profile for {tk}: "
                                     f"{exc!r}"[:200]})

        # Same idea for previous sessions. The score store holds 154 of them
        # but only the last few are kept as static files, because each is
        # ~0.8 MB of HTML. Rendering one costs ~4s now that `sessions_stored`
        # is indexed, so live mode reaches the whole history for no disk at all.
        m = re.fullmatch(r"/explore/(\d{4}-\d{2}-\d{2})\.html", path)
        if m and not (config.REPORTS_EXPLORE / f"{m.group(1)}.html").exists():
            sess = m.group(1)
            try:
                import explore
                if sess not in set(explore.stored_sessions()):
                    return self._json(404, {
                        "error": f"no scores stored for {sess}"})
                _log(f"building explore snapshot for {sess} on demand")
                explore.build(verbose=False, session=sess)
            except Exception as exc:                             # noqa: BLE001
                return self._json(404, {
                    "error": f"could not build {sess}: {exc!r}"[:200]})

        p = _safe_path(path)
        if p is None:
            return self._json(404, {"error": "not found"})
        try:
            data = p.read_bytes()
        except OSError:
            return self._json(404, {"error": "not found"})
        ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ----------------------------------------------------------------- POST
    def do_POST(self) -> None:
        # A cross-site <form> POST cannot set a custom header, and adding one
        # forces a CORS preflight that this server never approves. So requiring
        # the header is what stops a random page driving this endpoint.
        if self.headers.get(GUARD_HEADER) != "1":
            return self._json(403, {"error": f"missing {GUARD_HEADER} header"})

        path = self.path.split("?", 1)[0]
        if not path.startswith("/api/run/"):
            return self._json(404, {"error": "not found"})

        step = path[len("/api/run/"):].strip("/")
        res = _spawn(step)
        return self._json(200 if res.get("ok") else 409, res)

    def do_OPTIONS(self) -> None:
        # Refuse the preflight outright: no cross-origin caller is wanted.
        self.send_response(403)
        self.send_header("Content-Length", "0")
        self.end_headers()


def selftest(verbose: bool = True) -> None:
    root = config.REPORTS.resolve()
    # traversal must not escape reports/
    for bad in ("/../.env", "/../../config.py", "/..%2f.env", "//etc/passwd"):
        p = _safe_path(bad)
        assert p is None or root in p.parents, f"traversal escaped: {bad} -> {p}"
    # unknown steps never spawn
    r = _spawn("definitely-not-a-step")
    assert not r["ok"] and "unknown step" in r["error"], r
    assert HOST == "127.0.0.1", "must never bind a public interface"
    # `--stop` must never pick itself. It did, exiting 15 and leaving the real
    # server running -- excluding our own pid was not enough under a shell
    # wrapper, which can present the invocation as more than one process.
    assert not _is_victim("python serve.py --stop", 111, 999),         "--stop would terminate another stopping process"
    assert not _is_victim("python serve.py --port 8787", 999, 999),         "--stop would terminate itself by pid"
    assert _is_victim("python serve.py --port 8787", 111, 999),         "--stop would miss a real running server"
    assert not _is_victim("python study.py", 111, 999),         "--stop would terminate an unrelated process"
    if verbose:
        print(f"  [serve] traversal blocked, unknown steps rejected, "
              f"bound to {HOST} only")


def main() -> int:
    ap = argparse.ArgumentParser(description="Local control server for the hub.")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    # DEFAULT 240, not 0. At 0 a forgotten window sits resident indefinitely,
    # and every extra double-click of the shortcut used to add another. Four
    # hours is long past any real session and still bounded.
    ap.add_argument("--idle-exit", type=int, default=240, metavar="MIN",
                    help="shut down after MIN minutes with no requests (0=never)")
    ap.add_argument("--stop", action="store_true",
                    help="stop any running server and exit")
    a = ap.parse_args()

    if a.selftest:
        selftest()
        return 0

    if a.stop:
        n, detail = stop_running(a.port)
        print(f"  {detail}")
        return 0

    # ONE SERVER, not one per double-click. Starting a second on a taken port
    # raised OSError 10048 and printed a traceback -- which reads as "the app
    # is broken" when the truth is "it is already running". Point the browser
    # at the live one instead.
    if already_serving(a.port):
        url = f"http://{HOST}:{a.port}/index.html"
        print(f"  a server is already running at {url}")
        print(f"  opening it. To stop it:  python serve.py --stop")
        if a.open:
            webbrowser.open(url)
        return 0

    config.dirs()
    try:
        import dashboard
        dashboard.build(verbose=False)      # make sure there is a page to serve
    except Exception as exc:                                     # noqa: BLE001
        _log(f"could not rebuild the hub ({exc!r}); serving whatever exists")

    url = f"http://{HOST}:{a.port}/index.html"
    srv = ThreadingHTTPServer((HOST, a.port), Handler)
    _log(f"serving {config.REPORTS} at {url}")
    _log("buttons on the hub are live while this runs. Ctrl-C to stop.")
    if a.idle_exit:
        # NOT a daemon. It lives while it is being used and exits on its own,
        # which is the honest way to have working buttons without a process
        # sitting resident forever. A browser tab left open does not keep it
        # alive -- only actual requests do.
        def _watchdog():
            while True:
                time.sleep(30)
                idle = (time.time() - _last_request[0]) / 60
                if idle >= a.idle_exit:
                    _log(f"idle {idle:.0f}m >= {a.idle_exit}m, shutting down")
                    srv.shutdown()
                    return
        threading.Thread(target=_watchdog, daemon=True).start()
        _log(f"will exit after {a.idle_exit} idle minute(s)")

    if a.open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        _log("stopped")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
