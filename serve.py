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


# Measured stage costs for a cold single-ticker build, from the JNJ build on
# 2026-08-13 (160s total under load). These drive a REAL progress bar: the
# worker reports which stage it is in and the page interpolates within it, so
# the ETA reflects work actually done rather than a spinner that means nothing.
# Weights are fractions of total elapsed time, in execution order.
_STAGES: list[tuple[str, float]] = [
    ("Reading price history",        0.10),
    ("Loading bounce scores",        0.14),
    ("Loading hype scores",          0.14),
    ("Loading dip scores",           0.14),
    ("Loading combo scores",         0.12),
    ("Reading SEC filings",          0.24),
    ("Rendering charts and tables",  0.12),
]


def _stage(tk: str, i: int) -> None:
    with _BUILDS_LOCK:
        st = _BUILDS.get(tk)
        if st is not None:
            st["stage"] = i


def _build_worker(tk: str) -> None:
    """Prime, then build one profile, reporting stage progress as it goes."""
    err = None
    try:
        import stock_profile
        one = [tk]
        # PRIME FIRST, exactly as the batch path does. `build()` alone
        # re-scans every score partition per module and the fact store twice
        # (annual + quarterly for the toggle). The nightly step primes once
        # for the whole batch and gets ~20s a page; unprimed, a single build
        # pays the full cold cost -- measured at 252s.
        _stage(tk, 0)
        for i, _m in enumerate(config.SCORE_MODULES):
            _stage(tk, min(1 + i, len(_STAGES) - 3))
            try:
                stock_profile.prime_history(_m, one, _asof_now())
            except Exception:                                    # noqa: BLE001
                pass
        _stage(tk, len(_STAGES) - 2)
        try:
            import fundamentals as _FD
            _FD.prime_history(one, 16, "Q")
        except Exception:                                        # noqa: BLE001
            pass
        _stage(tk, len(_STAGES) - 1)
        stock_profile.build(tk, verbose=False)
    except Exception as exc:                                     # noqa: BLE001
        err = f"{exc!r}"[:300]
        _log(f"on-demand build FAILED for {tk}: {err}")
    else:
        _log(f"on-demand build done for {tk} "
             f"({time.time() - _BUILDS[tk]['started']:.0f}s)")
    with _BUILDS_LOCK:
        _BUILDS[tk].update(done=True, error=err)


def _start_build(tk: str) -> dict:
    """Return the build state for `tk`, starting one if none is running."""
    with _BUILDS_LOCK:
        st = _BUILDS.get(tk)
        if st is not None and not st["done"]:
            return st                       # already building; don't restart
        _BUILDS[tk] = {"started": time.time(), "done": False, "error": None,
                       "stage": 0}
    _log(f"building profile for {tk} on demand (background)")
    threading.Thread(target=_build_worker, args=(tk,), daemon=True).start()
    return _BUILDS[tk]


def _waiting_page(tk: str) -> bytes:
    """The profile page's own shell, with real staged progress and an ETA.

    Deliberately NOT a bare spinner on a blank page. Arriving here means the
    user clicked a ticker and expects that ticker's profile, so the page shows
    the profile's header and skeleton immediately and fills the content area
    with progress. Stages come from the build worker over /api/build, so the
    bar tracks work actually completed; the ETA is the measured total (160s
    warm, up to 250s cold) minus elapsed, and it degrades to "any moment now"
    rather than counting into negative numbers when a build runs long.
    """
    steps = "".join(
        f'<li data-i="{i}"><span class="dot"></span>{name}</li>'
        for i, (name, _w) in enumerate(_STAGES))
    weights = ",".join(str(w) for _n, w in _STAGES)
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{tk} &middot; building</title>
<style>
 :root{{--bg:#0d1117;--panel:#161b22;--line:#30363d;--ink:#e6edf3;
   --dim:#8b949e;--accent:#2f81f7;--ok:#3fb950}}
 *{{box-sizing:border-box}}
 body{{background:var(--bg);color:var(--ink);margin:0;
   font:15px/1.6 -apple-system,'Segoe UI',system-ui,sans-serif}}
 .wrap{{max-width:70rem;margin:0 auto;padding:1.6rem 1.4rem 3rem}}
 header{{border-bottom:1px solid var(--line);padding-bottom:1rem;
   margin-bottom:1.8rem}}
 h1{{font-size:1.7rem;margin:0 0 .3rem;letter-spacing:-.02em}}
 .sub{{color:var(--dim);font-size:.88rem}}
 .card{{background:var(--panel);border:1px solid var(--line);
   border-radius:10px;padding:1.5rem 1.6rem}}
 .pct{{font-size:2.4rem;font-weight:600;letter-spacing:-.03em;
   font-variant-numeric:tabular-nums;line-height:1.1}}
 .eta{{color:var(--dim);font-size:.9rem;margin-top:.15rem}}
 .track{{height:6px;background:#21262d;border-radius:3px;overflow:hidden;
   margin:1.2rem 0 1.4rem}}
 .fill{{height:100%;width:0;background:linear-gradient(90deg,var(--accent),
   #58a6ff);border-radius:3px;transition:width .6s cubic-bezier(.4,0,.2,1)}}
 ul{{list-style:none;margin:0;padding:0;
   display:grid;grid-template-columns:repeat(auto-fit,minmax(15rem,1fr));
   gap:.35rem .9rem}}
 li{{color:var(--dim);font-size:.87rem;display:flex;align-items:center;
   gap:.55rem;padding:.12rem 0;transition:color .3s}}
 li.done{{color:var(--ink)}}
 li.now{{color:var(--accent)}}
 .dot{{width:7px;height:7px;border-radius:50%;background:#30363d;
   flex:0 0 auto;transition:background .3s,box-shadow .3s}}
 li.done .dot{{background:var(--ok)}}
 li.now .dot{{background:var(--accent);
   box-shadow:0 0 0 0 rgba(47,129,247,.6);animation:p 1.4s infinite}}
 @keyframes p{{70%{{box-shadow:0 0 0 7px rgba(47,129,247,0)}}
   100%{{box-shadow:0 0 0 0 rgba(47,129,247,0)}}}}
 .note{{margin-top:1.5rem;color:var(--dim);font-size:.82rem;
   border-top:1px solid var(--line);padding-top:1rem}}
 .skel{{margin-top:1.4rem;display:grid;gap:.55rem}}
 .skel div{{height:11px;border-radius:5px;
   background:linear-gradient(90deg,#1b2129 25%,#242c36 50%,#1b2129 75%);
   background-size:200% 100%;animation:sh 1.5s infinite}}
 @keyframes sh{{0%{{background-position:200% 0}}100%{{background-position:-200% 0}}}}
 .err{{color:#f85149}}
</style></head><body><div class="wrap">
<header>
  <h1>{tk}</h1>
  <div class="sub">Building this profile now &mdash; it was not pre-rendered.
    Once built it opens instantly.</div>
</header>
<div class="card">
  <div class="pct" id="pct">0%</div>
  <div class="eta" id="eta">estimating&hellip;</div>
  <div class="track"><div class="fill" id="fill"></div></div>
  <ul id="steps">{steps}</ul>
  <div class="skel"><div style="width:88%"></div><div style="width:64%"></div>
    <div style="width:75%"></div></div>
  <div class="note" id="note">Price history, four score modules and the SEC
    filing history are being read for {tk}.</div>
</div></div>
<script>
var W=[{weights}],TK="{tk}",T0=Date.now(),EXP=170000;
function fmt(ms){{var s=Math.max(0,Math.round(ms/1000));
  return s>=60?(Math.floor(s/60)+"m "+(s%60)+"s"):(s+"s");}}
function paint(stage,frac){{
  var base=0;for(var i=0;i<stage&&i<W.length;i++)base+=W[i];
  var p=Math.min(.99,base+(W[stage]||0)*frac);
  document.getElementById("fill").style.width=(p*100).toFixed(1)+"%";
  document.getElementById("pct").textContent=Math.round(p*100)+"%";
  var el=Date.now()-T0, left=p>0.04?el*(1-p)/p:EXP-el;
  document.getElementById("eta").textContent=
    left>1500?("about "+fmt(left)+" remaining"):"any moment now";
  var li=document.querySelectorAll("#steps li");
  for(var j=0;j<li.length;j++){{li[j].className=
    j<stage?"done":(j===stage?"now":"");}}
}}
var stage=0,tick=0;
setInterval(function(){{tick++;paint(stage,Math.min(1,tick%14/14));}},700);
function poll(){{
  fetch("/api/build?tk="+TK,{{cache:"no-store"}}).then(function(r){{
    return r.json();}}).then(function(j){{
      if(j.error){{document.getElementById("note").innerHTML=
        '<span class="err">Build failed: '+j.error+'</span>';return;}}
      if(j.done){{document.getElementById("fill").style.width="100%";
        document.getElementById("pct").textContent="100%";
        document.getElementById("eta").textContent="opening\\u2026";
        setTimeout(function(){{location.reload();}},500);return;}}
      if(typeof j.stage==="number"){{stage=j.stage;tick=0;}}
      setTimeout(poll,1200);
    }}).catch(function(){{setTimeout(poll,2500);}});
}}
poll();
</script></body></html>""".encode("utf-8")


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

        if path == "/api/steps":
            return self._json(200, {"steps": [
                {"name": s.name, "cadence": s.cadence, "desc": s.desc}
                for s in orchestrator.REGISTRY]})

        # Polled by the waiting page. Reports the stage the worker is in, so
        # the progress bar reflects real work instead of a timer.
        if path == "/api/build":
            q = self.path.split("?", 1)[1] if "?" in self.path else ""
            tk = ""
            for part in q.split("&"):
                if part.startswith("tk="):
                    tk = part[3:].upper()
            with _BUILDS_LOCK:
                st = dict(_BUILDS.get(tk) or {})
            if not st:
                # No record: either already built, or this server restarted
                # mid-build. Either way the page should reload and find out.
                done = (config.REPORTS / "stock" / f"{tk}.html").exists()
                return self._json(200, {"done": done, "stage": 0,
                                        "error": None, "unknown": True})
            return self._json(200, {
                "done": bool(st.get("done")), "error": st.get("error"),
                "stage": int(st.get("stage") or 0),
                "elapsed": round(time.time() - st.get("started", time.time()), 1),
                "stages": [n for n, _w in _STAGES]})

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
