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
not running, `reports/system.html` opened over file:// behaves exactly as before.

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
_SERVER = None              # the running ThreadingHTTPServer, for the page's stop button
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


# --------------------------------------------------------- on-demand builds
# A cold profile build costs ~250s (see the priming note in `do_GET`). Doing
# that INSIDE the GET means the browser sits on a blank page with a spinning
# tab for four minutes and usually gives up first -- which is exactly what a
# ticker opened from explore.html did, because those links are plain <a href>
# with no loader of their own. Only the profiles index had a loader, so the
# feedback depended on which page you arrived from.
#
# So the build runs in a thread and the request returns a waiting page AT ONCE.
# Every entry point gets the same feedback, and a client that navigates away
# no longer strands a 250s write against a closed socket.
_BUILDS: dict[str, dict] = {}
_BUILDS_LOCK = threading.Lock()


# THE STAGES OF ONE BUILD, named after what is actually read (2026-11-11: the old list named a
# "bounce scores" module that does not exist and skipped three that do). Their durations are
# MEASURED: every successful build records seconds per stage in BUILD_TIMES, and progress and
# "usually about N min" come from the median of the last 30. Until three builds are recorded the
# defaults below stand in (the old JNJ total of ~160-250s, spread over the stages).
BUILD_TIMES = config.DATA / "_build_times.json"


def _stages() -> list[str]:
    return ["Reading price history",
            f"Loading score history ({len(config.SCORE_MODULES)} modules)",
            "Reading SEC filings",
            "Rendering charts and tables"]


_DEFAULT_SECS = [20.0, 90.0, 60.0, 30.0]


def _expected() -> list[float]:
    """Median seconds per stage over the last 30 recorded builds; defaults until there are 3."""
    try:
        runs = json.loads(BUILD_TIMES.read_text(encoding="utf-8")).get("runs", [])[-30:]
        good = [r["secs"] for r in runs if r.get("stages") == _stages()]
        if len(good) >= 3:
            import statistics
            return [round(statistics.median(s[i] for s in good), 1) for i in range(len(_stages()))]
    except Exception:                                            # noqa: BLE001
        pass
    return list(_DEFAULT_SECS)


def _record_times(tk: str, secs: list[float]) -> None:
    try:
        doc = json.loads(BUILD_TIMES.read_text(encoding="utf-8")) if BUILD_TIMES.exists() else {}
    except Exception:                                            # noqa: BLE001
        doc = {}
    runs = (doc.get("runs") or [])[-59:]
    runs.append({"tk": tk, "at": datetime.now().isoformat(timespec="seconds"), "stages": _stages(),
                 "secs": [round(s, 1) for s in secs]})
    tmp = BUILD_TIMES.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"runs": runs}, indent=1), encoding="utf-8")
    tmp.replace(BUILD_TIMES)


def _stage(tk: str, i: int) -> None:
    with _BUILDS_LOCK:
        st = _BUILDS.get(tk)
        if st is not None:
            st["stage"], st["stage_t"] = i, time.time()


def _build_worker(tk: str) -> None:
    """Prime, then build one profile, reporting each stage as it starts."""
    err, secs = None, []
    t0 = time.time()

    def mark(i: int) -> None:
        nonlocal t0
        now = time.time()
        if i > 0:
            secs.append(now - t0)
        t0 = now
        _stage(tk, i)

    try:
        import stock_profile
        one, asof = [tk], _asof_now()
        # PRIME FIRST, exactly as the nightly step does, or one build pays the full cold cost of
        # re-scanning every score partition per module (measured 252s once).
        mark(0)
        try:
            stock_profile.prime_bars(one, asof)
        except Exception:                                        # noqa: BLE001
            pass
        mark(1)
        try:
            stock_profile.prime_histories(list(config.SCORE_MODULES), one, asof)
        except Exception:                                        # noqa: BLE001
            pass
        mark(2)
        try:
            import fundamentals as _FD
            _FD.prime_history(one, stock_profile.PERIODS_ALL, "A")
        except Exception:                                        # noqa: BLE001
            pass
        mark(3)
        stock_profile.build(tk, verbose=False)
        secs.append(time.time() - t0)
    except Exception as exc:                                     # noqa: BLE001
        err = f"{exc!r}"[:300]
        _log(f"on-demand build FAILED for {tk}: {err}")
    else:
        _log(f"on-demand build done for {tk} ({sum(secs):.0f}s: "
             + ", ".join(f"{s:.0f}" for s in secs) + ")")
        if len(secs) == len(_stages()):
            _record_times(tk, secs)
    with _BUILDS_LOCK:
        _BUILDS[tk].update(done=True, error=err, ended=time.time())


def _start_build(tk: str) -> dict:
    """The build state for `tk`, starting one if none is running. A finished build is started
    again, so the same call both builds a missing page and refreshes an existing one."""
    with _BUILDS_LOCK:
        st = _BUILDS.get(tk)
        if st is not None and not st["done"]:
            return st                       # already building; don't restart
        now = time.time()
        _BUILDS[tk] = {"started": now, "done": False, "error": None, "stage": 0, "stage_t": now}
    _log(f"building profile for {tk} on demand (background)")
    threading.Thread(target=_build_worker, args=(tk,), daemon=True).start()
    return _BUILDS[tk]


_TK_RE = re.compile(r"[A-Z][A-Z0-9.\-]{0,9}")


def _ticker(raw: str) -> str | None:
    """A ticker from a query string, or None: letters, digits, '.' and '-' only."""
    tk = (raw or "").strip().upper()
    return tk if _TK_RE.fullmatch(tk) else None


def _query(path: str, key: str) -> str:
    from urllib.parse import parse_qs, urlsplit
    return (parse_qs(urlsplit(path).query).get(key) or [""])[0]


def _status(tk: str) -> dict:
    """Everything a page needs to show one stock's page and build: is it built and when, and the
    build's stage, elapsed time and measured stage durations. `known` is False when this server
    has no record of a build for `tk` (none was started here, or the server was restarted since),
    so a page following a build can tell "finished" from "lost with the old server"."""
    page = config.REPORTS / "stock" / f"{tk}.html"
    with _BUILDS_LOCK:
        st = dict(_BUILDS.get(tk) or {})
    now = time.time()
    try:
        mtime = page.stat().st_mtime
    except OSError:
        mtime = None
    return {"tk": tk, "built": mtime is not None,
            "built_at": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime else None,
            "built_ts": round(mtime, 1) if mtime else None,
            "known": bool(st), "started": st.get("started"), "ended": st.get("ended"),
            "building": bool(st) and not st.get("done"), "done": bool(st.get("done")),
            "error": st.get("error"), "stage": int(st.get("stage") or 0),
            "elapsed": round((st.get("ended") or now) - st["started"], 1) if st else 0.0,
            "stage_elapsed": round(now - st.get("stage_t", now), 1) if st else 0.0,
            "stages": _stages(), "expected": _expected()}


def _waiting_page(tk: str) -> bytes:
    """Arriving on a stock page that was never built: the build runs in the background and this
    page shows it. Progress only moves forward (2026-11-11: the old bar restarted every ~10 seconds
    inside a long stage, and its countdown jumped with it); the time shown is time elapsed, which
    cannot run backwards, beside the measured usual total. "Keep browsing" hands the build to the
    tray in the corner of every page, which says when it is ready."""
    import ui
    steps = "".join(f'<li data-i="{i}">{ui.esc(n)}</li>' for i, n in enumerate(_stages()))
    css = ("<style>.bw{max-width:720px}.bw-pct{font-size:34px;font-weight:650;letter-spacing:-.02em;"
           "font-variant-numeric:tabular-nums;line-height:1.1}.bw-meta{color:var(--muted);font-size:13px;margin-top:2px}"
           ".bw-track{height:6px;background:var(--grid);border-radius:3px;overflow:hidden;margin:16px 0 18px}"
           ".bw-track i{display:block;height:100%;width:0;background:var(--accent);border-radius:3px;"
           "transition:width .8s cubic-bezier(.4,0,.2,1)}"
           ".bw-steps{list-style:none;margin:0;padding:0;display:grid;gap:6px}"
           ".bw-steps li{color:var(--muted);font-size:13px;padding-left:20px;position:relative}"
           ".bw-steps li:before{content:'';position:absolute;left:3px;top:6px;width:8px;height:8px;border-radius:50%;"
           "background:var(--line)}.bw-steps li.done{color:var(--ink)}.bw-steps li.done:before{background:var(--pos)}"
           ".bw-steps li.now{color:var(--accent);font-weight:600}.bw-steps li.now:before{background:var(--accent)}"
           ".bw-acts{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-top:18px;padding-top:14px;"
           "border-top:1px solid var(--line)}.bw-acts .muted{font-size:12.5px}.bw-err{color:var(--neg)}</style>")
    body = (f'<div class="card bw"><div class="bw-pct" id="bw-pct">0%</div>'
            '<div class="bw-meta" id="bw-meta">starting&hellip;</div>'
            '<div class="bw-track"><i id="bw-fill"></i></div>'
            f'<ol class="bw-steps" id="bw-steps">{steps}</ol>'
            '<div class="bw-acts"><button type="button" class="primary" id="bw-away">Keep browsing</button>'
            '<span class="muted">This page opens by itself when it is ready, and the corner of every page '
            'says so too.</span></div></div>')
    js = ("""
(function(){
  var TK = %s, B = window.scrBuild, lis = document.querySelectorAll('#bw-steps li');
  function show(s){
    // No record here means the server was restarted: asking for the page again starts a new build
    // (or serves the page, if it was written first).
    if (!s.known){ location.reload(); return true; }
    if (s.error){ document.getElementById('bw-meta').innerHTML = '<span class="bw-err">The build failed: '
        + String(s.error).replace(/[&<>]/g, '') + '</span> &middot; <a href="">try again</a>'; return true; }
    if (s.done){ document.getElementById('bw-fill').style.width = '100%%';
      document.getElementById('bw-pct').textContent = '100%%';
      document.getElementById('bw-meta').textContent = 'ready: opening…';
      setTimeout(function(){ location.reload(); }, 400); return true; }
    var p = B.progress(s);
    document.getElementById('bw-fill').style.width = (p * 100).toFixed(1) + '%%';
    document.getElementById('bw-pct').textContent = Math.round(p * 100) + '%%';
    document.getElementById('bw-meta').textContent = B.mmss(s.elapsed) + ' elapsed \\u00b7 usually ' + B.about(s);
    for (var i = 0; i < lis.length; i++) lis[i].className = i < s.stage ? 'done' : (i === s.stage ? 'now' : '');
    return false;
  }
  function poll(){
    B.api('/api/build?tk=' + encodeURIComponent(TK)).then(function(s){
      if (!show(s)) setTimeout(poll, 1500);
    }, function(){ setTimeout(poll, 3000); });
  }
  document.getElementById('bw-away').addEventListener('click', function(){
    B.track(TK);
    if (history.length > 1) history.back(); else location.href = '/index.html';
  });
  if (B) poll();
})();
""" % json.dumps(tk))
    return ui.page(tk, body, subtitle="Building this stock&rsquo;s page now: it was not built yet. Once built "
                   "it opens instantly.", active="profiles", depth=1, head=css, script=js).encode("utf-8")


def _failed_page(tk: str, err: str) -> bytes:
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>{tk} failed</title><style>
 body{{background:#0d1117;color:#c9d1d9;font:15px/1.6 -apple-system,
   'Segoe UI',system-ui,sans-serif;display:flex;align-items:center;
   justify-content:center;height:100vh;margin:0;text-align:center}}
 .box{{max-width:34rem;padding:2rem}}
 h1{{font-size:1.3rem;margin:0 0 .6rem;color:#f85149}}
 pre{{text-align:left;background:#161b22;border:1px solid #30363d;
   border-radius:6px;padding:.8rem;overflow:auto;font-size:.8rem;
   color:#8b949e;white-space:pre-wrap}}
</style></head><body><div class="box">
<h1>Could not build {tk}</h1>
<pre>{err}</pre></div></body></html>""".encode("utf-8")


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
        self._write(body)

    def _html(self, code: int, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self._write(body)

    def _write(self, body: bytes) -> None:
        """Write a response body, tolerating a client that gave up.

        A browser that navigates away mid-response closes the socket, and the
        write then raises ConnectionAbortedError (WinError 10053) or
        BrokenPipeError. That is normal client behaviour, not a server fault,
        so it must not dump a traceback into the log -- it buried the real
        cause of a stuck page under a stack trace that looked like a crash.
        """
        try:
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            self.close_connection = True

    # ------------------------------------------------------------------ GET
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]

        # The watchlist, so the star on the explore page persists to disk
        # instead of living only in one browser. Read is open; writing goes
        # through do_POST and its header guard.
        if path == "/api/watchlist":
            try:
                import watchlist
                return self._json(200, {"tickers": watchlist.load()})
            except Exception as exc:                             # noqa: BLE001
                return self._json(500, {"error": repr(exc)[:200]})

        # The Settings page's current values (settings_page.state). Read is open, like
        # /api/watchlist; a save goes through do_POST and its header guard.
        if path == "/api/settings":
            try:
                import settings_page
                return self._json(200, settings_page.state())
            except Exception as exc:                             # noqa: BLE001
                return self._json(500, {"error": repr(exc)[:200]})

        if path == "/api/steps":
            return self._json(200, {"steps": [
                {"name": s.name, "cadence": s.cadence, "desc": s.desc}
                for s in orchestrator.REGISTRY]})

        # Polled by the waiting page. Reports the stage the worker is in, so
        # the progress bar reflects real work instead of a timer.
        # Pages ask this once to know they are served (and so may offer builds and the stop
        # button); a page opened as a file, or under another server, gets no answer.
        if path == "/api/ping":
            return self._json(200, {"ok": True, "server": "screener", "builds": len(_BUILDS)})

        # One stock's page and build, polled by the build tray on every page and by the waiting
        # page. `built` says whether a page exists; `known` whether this server has a record of
        # a build (see _status).
        if path in ("/api/stock", "/api/build"):
            tk = _ticker(_query(self.path, "tk"))
            if not tk:
                return self._json(400, {"error": "tk must be a ticker: letters, digits, '.' or '-'"})
            return self._json(200, _status(tk))

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
                    # Never block the response on the build -- see _start_build.
                    st = _start_build(tk.upper())
                    if not st["done"]:
                        return self._html(200, _waiting_page(tk.upper()))
                    if st["error"] or not target.exists():
                        return self._html(
                            500, _failed_page(tk.upper(),
                                              st["error"] or "no page written"))
                    # Finished between the exists() check and now -- fall
                    # through and serve it.

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
        self._write(data)

    # ----------------------------------------------------------------- POST
    def do_POST(self) -> None:
        # A cross-site <form> POST cannot set a custom header, and adding one
        # forces a CORS preflight that this server never approves. So requiring
        # the header is what stops a random page driving this endpoint.
        if self.headers.get(GUARD_HEADER) != "1":
            return self._json(403, {"error": f"missing {GUARD_HEADER} header"})

        path = self.path.split("?", 1)[0]

        if path == "/api/watchlist":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                # REPLACE, not merge. The page owns the list and sends the
                # whole thing, so unstarring actually removes -- a merge would
                # make removal impossible and the list would only ever grow.
                import watchlist
                keep = watchlist.save(body.get("tickers") or [])
                return self._json(200, {"tickers": keep})
            except Exception as exc:                             # noqa: BLE001
                return self._json(400, {"error": repr(exc)[:200]})

        # A save from the Settings page. Only two fixed files can change, each through the
        # module that owns it, which checks every value and saves nothing when one is wrong.
        if path == "/api/settings":
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if not 0 < n <= 65536:
                return self._json(413 if n > 65536 else 400, {"ok": False, "errors": ["a body of 1 to 65,536 bytes is required"]})
            try:
                body = json.loads(self.rfile.read(n).decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                return self._json(400, {"ok": False, "errors": [f"not valid JSON: {exc}"[:200]]})
            try:
                import settings_page
                code, payload = settings_page.save(body)
                return self._json(code, payload)
            except Exception as exc:                             # noqa: BLE001
                return self._json(500, {"ok": False, "errors": [repr(exc)[:200]]})

        # Build one stock's page in the background, or rebuild one that exists (the Refresh
        # button and the build offer on any stock link). Returns the build's status at once.
        if path == "/api/build":
            tk = _ticker(_query(self.path, "tk"))
            if not tk:
                return self._json(400, {"ok": False, "error": "tk must be a ticker"})
            _start_build(tk)
            return self._json(200, {"ok": True, **_status(tk)})

        # The stop button. The reply goes out first; the server then stops in another thread,
        # because shutdown() waits for the serving loop, which is busy answering this request.
        # Builds in progress here stop with it (they run in this process); a nightly step
        # started from the Status page runs in its own process and carries on.
        if path == "/api/shutdown":
            with _BUILDS_LOCK:
                running = sorted(t for t, s in _BUILDS.items() if not s.get("done"))
            self._json(200, {"ok": True, "stopped_builds": running})
            _log("stop requested from a page" + (f"; stopping builds of {', '.join(running)}" if running else ""))
            if _SERVER is not None:
                threading.Thread(target=_SERVER.shutdown, daemon=True).start()
            return

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

    # Tickers from a query string: only letters, digits, '.' and '-' ever reach a file name.
    for good in ("AAPL", "brk.b", "BF-B", "A"):
        assert _ticker(good) == good.upper(), good
    for bad in ("", "../x", "A B", "<b>", "AAPL.html/..", "TOOLONGTICK", "1ABC", "A/B"):
        assert _ticker(bad) is None, bad
    assert len(_stages()) == len(_DEFAULT_SECS) == len(_expected()), "stages and durations differ in length"
    assert all(x > 0 for x in _expected()), _expected()
    s = _status("ZZZZQ")
    assert not s["known"] and not s["building"] and not s["done"] and s["started"] is None, s

    # The endpoints, end to end, on a throwaway port: ping, a bad ticker, a POST without the header.
    import urllib.error
    import urllib.request
    global _SERVER
    srv = ThreadingHTTPServer((HOST, 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://{HOST}:{port}"

    def call(path, post=False, header=True):
        req = urllib.request.Request(base + path, data=b"{}" if post else None, method="POST" if post else "GET")
        if post and header:
            req.add_header(GUARD_HEADER, "1")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")
    try:
        code, j = call("/api/ping")
        assert code == 200 and j.get("server") == "screener", (code, j)
        assert call("/api/stock?tk=../../.env")[0] == 400
        assert call("/api/build?tk=", post=True)[0] == 400
        assert call("/api/shutdown", post=True, header=False)[0] == 403, "shutdown without the guard header"
        code, j = call("/api/stock?tk=ZZZZQ")
        assert code == 200 and j["known"] is False and j["built"] is False, j
        # The stop button: replies first, then the server stops.
        prev, _SERVER = _SERVER, srv
        try:
            code, j = call("/api/shutdown", post=True)
            assert code == 200 and j.get("ok"), (code, j)
            for _ in range(50):
                try:
                    call("/api/ping")
                except (urllib.error.URLError, ConnectionError, OSError):
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("the server still answers after /api/shutdown")
        finally:
            _SERVER = prev
    finally:
        srv.shutdown()
        srv.server_close()
    if verbose:
        print(f"  [serve] traversal blocked, unknown steps rejected, bound to {HOST} only; "
              "ticker filter, build status, ping and the guarded stop endpoint checked end to end")


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
    try:
        import settings_page
        import today_page
        today_page.build(verbose=False)     # the landing page, so it is never older than the hub
        settings_page.build(verbose=False)
    except Exception as exc:                                     # noqa: BLE001
        _log(f"could not rebuild Today or Settings ({exc!r}); serving whatever exists")

    url = f"http://{HOST}:{a.port}/index.html"
    global _SERVER
    srv = _SERVER = ThreadingHTTPServer((HOST, a.port), Handler)
    _log(f"serving {config.REPORTS} at {url}")
    _log("buttons on the Status page (system.html) are live while this runs. Ctrl-C to stop.")
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
