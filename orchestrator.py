"""
THE scheduled entry point. One shot: catches up whatever is owed, exits.

    python orchestrator.py              the normal run (~3 min daily)
    python orchestrator.py --dry-run    show what is due, touch nothing
    python orchestrator.py --status     read the job table, run nothing
    python orchestrator.py --pause      stop the scheduled task doing work
    python orchestrator.py --resume     start it again
    python orchestrator.py --once       run even if paused
    python orchestrator.py --step NAME  run one step (repeatable), ignoring cadence
    python orchestrator.py --force      run every step, ignoring cadence

This replaces three independent schedules -- `daily_run.py`, `senti_screen.py
--interval` and `fund_screen.py --catchup` -- with one registry. No daemon, no
polling loop, no resident process: Task Scheduler starts it, it exits, and there
is zero footprint until tomorrow.

DESIGN NOTES, each of which is load-bearing:

1. **Dueness is a watermark, never a wall clock.** A step is due when the work it
   represents has not been done for the current session, not when "it is 06:00".
   A laptop closed for a week resumes and reconciles; one closed for a quarter
   falls back to ORCH_MAX_CATCHUP_SESSIONS instead of replaying it all. This is
   the same rule as `daily_run.detect_gap`, generalised to every step.

2. **A step failure never aborts the run.** It records and continues, exactly as
   `daily_run` does. Only declared `depends_on` edges propagate, and they
   propagate as `blocked`, which is a distinct status from `error` -- the project
   rule that "no data" and "no signal" must never look alike applies to job
   status just as much as to metrics.

3. **Timeouts are recorded, not enforced by killing.** There is no safe way to
   kill a step mid-write from in-process Python: `store.atomic_replace` protects
   the *target* partition, but a thread killed between `to_parquet(tmp)` and the
   replace leaks a temp file, and a thread that is merely abandoned keeps writing
   underneath its successor. A step that blows its budget is therefore marked
   `slow` and surfaced on the dashboard, which is the honest failure mode.

4. **The job table is written after every step, not at the end.** A crash in step
   9 must still leave steps 1-8 auditable. It is ~14 rows/day, so rewriting the
   parquet each time costs nothing.

5. **The orchestrator owns the shared fetches.** `news.update()` runs here and
   `senti_screen` is invoked with `do_fetch=False`; `sentiment.build_cache()` is
   its own step rather than a side effect of every screener call; `daily_run` is
   invoked with `do_universe=False, do_bars=False`. Those three were the
   remaining duplicated-work items.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable

import config                                                    # noqa: E402

# Must precede anything that prints -- see daily_run.py for the pythonw case.
config.safe_console()

import calendar_us                                               # noqa: E402
import store                                                     # noqa: E402

STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_SKIPPED = "skipped"       # cadence says not due
STATUS_BLOCKED = "blocked"       # a dependency did not succeed
STATUS_SLOW = "slow"             # succeeded, but over its timeout budget
STATUS_RUNNING = "running"       # written on entry; overwritten on exit

JOB_COLUMNS = ["run_id", "step", "cadence", "watermark", "started", "ended",
               "duration_s", "status", "rows", "detail", "error", "traceback"]


def log(msg: str) -> None:
    """Tee to stdout and the run log. Cannot raise. Mirrors daily_run.log."""
    line = f"orch  {datetime.now():%Y-%m-%d %H:%M:%S} | {msg}"
    try:
        print(line, flush=True)
    except (ValueError, OSError):
        pass
    try:
        config.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with config.LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# ============================================================== the job table
# Explicit and stable, mirroring the `_typed()` convention in store.py/news.py.
# Without it, concatenating a row whose optional columns are all None onto the
# existing table hits pandas' all-NA deprecation and lets dtypes drift between
# writes -- which for a diagnostics table shows up much later as a column that
# silently stopped being readable.
JOB_DTYPES = {"run_id": "string", "step": "string", "cadence": "string",
              "watermark": "string", "started": "string", "ended": "string",
              "duration_s": "float64", "status": "string", "rows": "Int64",
              "detail": "string", "error": "string", "traceback": "string"}


def _typed_jobs(df):
    """Idempotent -- called on both read and write, same as store._typed."""
    for col, dt in JOB_DTYPES.items():
        if col not in df.columns:
            df[col] = None
        try:
            df[col] = df[col].astype(dt)
        except (TypeError, ValueError):
            df[col] = df[col].astype("string")
    return df[JOB_COLUMNS]


def read_jobs():
    """Every recorded step run, oldest first. Empty frame if never run."""
    import pandas as pd
    if not config.JOBS_FILE.exists():
        return _typed_jobs(pd.DataFrame(columns=JOB_COLUMNS))
    try:
        df = pd.read_parquet(config.JOBS_FILE)
    except Exception:                                            # noqa: BLE001
        # A corrupt job table must not stop the pipeline -- it is diagnostics,
        # not data. Rename it aside so the next run starts clean and the old one
        # is still there to look at.
        try:
            config.JOBS_FILE.rename(
                config.JOBS_FILE.with_suffix(f".corrupt-{int(time.time())}.parquet"))
        except OSError:
            pass
        return _typed_jobs(pd.DataFrame(columns=JOB_COLUMNS))
    return _typed_jobs(df)


def write_jobs(df) -> None:
    """Atomic, compressed, and age-pruned. Never raises into the run."""
    import pandas as pd
    try:
        if len(df) and config.ORCH_JOBS_KEEP_DAYS:
            cutoff = (date.today()
                      - timedelta(days=config.ORCH_JOBS_KEEP_DAYS)).isoformat()
            keep = pd.to_datetime(df["started"], errors="coerce")
            df = df[keep.isna() | (keep >= cutoff)]
        tmp = config.JOBS_FILE.with_suffix(".parquet.tmp")
        df.to_parquet(tmp, compression=config.COMPRESSION,
                      compression_level=config.COMPRESSION_LEVEL, index=False)
        store.atomic_replace(tmp, config.JOBS_FILE)
    except Exception as exc:                                     # noqa: BLE001
        log(f"  ! could not write job table ({repr(exc)[:100]})")


def record(row: dict) -> None:
    """Append one step result and flush. Called after every step by design."""
    import pandas as pd
    df = read_jobs()
    new = _typed_jobs(pd.DataFrame([{c: row.get(c) for c in JOB_COLUMNS}]))
    write_jobs(new if not len(df) else pd.concat([df, new], ignore_index=True))


def last_ok(step: str, jobs=None):
    """The most recent successful row for `step`, or None. `slow` counts as ok:
    it finished and wrote its artifacts, it was merely late."""
    jobs = read_jobs() if jobs is None else jobs
    if not len(jobs):
        return None
    m = jobs[(jobs["step"] == step)
             & (jobs["status"].isin([STATUS_OK, STATUS_SLOW]))]
    return None if not len(m) else m.iloc[-1]


# ================================================================== cadences
def _dow_boundary(today: date, dow: int) -> date:
    """Most recent date <= today whose weekday() == dow."""
    return today - timedelta(days=(today.weekday() - dow) % 7)


def is_due(step: "Step", asof: str, jobs=None) -> tuple[bool, str]:
    """(due, why). Watermark-driven; the clock is only ever a tiebreak."""
    prev = last_ok(step.name, jobs)

    if step.due_fn is not None:
        # Data-driven dueness beats any calendar: the step itself inspects the
        # store and says whether the artifact it owns is missing. It receives the
        # last successful row so it can rate-limit its own probing.
        try:
            return step.due_fn(prev)
        except Exception as exc:                                 # noqa: BLE001
            return True, f"dueness check failed ({repr(exc)[:60]}), running"

    if prev is None:
        return True, "never run"

    if step.cadence == config.CADENCE_DAILY:
        # Session-indexed steps are owed one run per SESSION and so compare
        # against the last close; housekeeping steps like `retention` are owed
        # one run per CALENDAR DAY and compare against today. Using asof for
        # both marks retention permanently due on any non-session day.
        ref = asof if step.session_indexed else date.today().isoformat()
        wm = str(prev.get("watermark") or "")
        if wm >= ref:
            return False, f"already ran for {ref}"
        return True, f"watermark {wm or '(none)'} < {ref}"

    if step.cadence == config.CADENCE_WEEKLY:
        started = str(prev.get("started") or "")[:10]
        boundary = _dow_boundary(date.today(), config.WEEKLY_DOW).isoformat()
        if started >= boundary:
            return False, f"ran {started}, this week"
        return True, f"no run since {boundary}"

    return True, "unknown cadence, running"


# ============================================================== step registry
@dataclass
class Step:
    name: str
    fn: Callable[[str], tuple[int, str]]   # (asof) -> (rows, detail)
    cadence: str
    desc: str = ""
    depends_on: tuple[str, ...] = ()
    timeout: int = config.ORCH_DEFAULT_TIMEOUT_S
    session_indexed: bool = True           # watermark is asof, else the run date
    # Receives the last successful job row (or None) so it can rate-limit itself.
    due_fn: Callable[[object], tuple[bool, str]] | None = None


# ------------------------------------------------------------- step bodies
# Each returns (rows, detail). Heavy modules are imported inside so a broken
# optional dependency cannot stop the whole orchestrator from starting.

def _step_universe(asof: str) -> tuple[int, str]:
    import universe
    _reg, added, removed, degraded = universe.refresh(verbose=False)
    s = universe.summary()
    # A dict's repr rendered straight into the dashboard cell as
    # `{'total': 5391, 'active': 5387, ...}` -- five wrapped lines of Python
    # punctuation in a status table. Format it for a reader.
    # ASCII on purpose: this string is printed to a cp1252 console and stored in
    # the job table, so a middot here shows up as a `?` in the log.
    detail = (", ".join(f"{k} {v:,}" if isinstance(v, (int, float)) else f"{k} {v}"
                        for k, v in s.items())
              if isinstance(s, dict) else str(s))
    if degraded:
        detail += "  DEGRADED (cached directory)"
    if added or removed:
        detail += f"  +{len(added)} -{len(removed)}"
    return int(s.get("total", 0) if isinstance(s, dict) else 0), detail


def _step_bars(asof: str) -> tuple[int, str]:
    import bars
    res = bars.update(window=5)
    if not res.get("ok") and not res.get("skipped"):
        raise RuntimeError(f"bars.update reported not-ok: {res}")
    return int(res.get("bars", 0)), (f"{res.get('bars', 0):,} bars, "
                                     f"{res.get('tickers', 0)} tickers, "
                                     f"{res.get('sessions', 0)} new session(s)")


def _step_macro(asof: str) -> tuple[int, str]:
    import macro
    df = macro.build(verbose=False, skip_sectors=True)
    return len(df), f"{len(df):,} macro row(s)"


def _step_news(asof: str) -> tuple[int, str]:
    import news
    res = news.update(verbose=False)
    # `written` maps month -> TOTAL rows in that month file after the merge, not
    # rows added, so summing it would report the whole store as today's delta.
    # `fetched` is the honest count of what this run pulled.
    n = int(res.get("fetched", 0) or 0)
    touched = len(res.get("written") or {})
    unattr = int(res.get("unattributed", 0) or 0)
    detail = f"{n:,} article(s) fetched, {touched} month file(s) touched"
    if unattr:
        detail += f", {unattr} beyond the last session (left unattributed)"
    return n, detail


def _step_senti_cache(asof: str) -> tuple[int, str]:
    """Own step, not a side effect of every screener call (dedup item 5)."""
    import sentiment
    res = sentiment.build_cache(verbose=False)
    n = int(sum(res.values())) if isinstance(res, dict) else int(res or 0)
    return n, f"{n:,} article score(s) cached across {len(res or {})} month(s)"


def _step_sentiment(asof: str) -> tuple[int, str]:
    """do_fetch=False: the orchestrator owns news.update (dedup item 4)."""
    import senti_screen
    res = senti_screen.run_once(asof=asof, do_fetch=False, verbose=False)
    if not res.get("ok"):
        # {"ok": False} means no panel stats -- a real failure that must not be
        # recorded as "0 names scored", which reads identically to a quiet day.
        raise RuntimeError("senti_screen.run_once reported not-ok "
                           "(no panel stats? run bars first)")
    n = int(res.get("n", 0) or 0)
    return n, f"{n:,} name(s) with news scored"


def _step_shortvol(asof: str) -> tuple[int, str]:
    """FINRA Reg SHO daily short volume. Feeds hype's attention pillar.
    Runs BEFORE hype so the same session's short data is available to it."""
    import finra
    res = finra.update(verbose=False)
    n = int(res.get("rows", 0) or 0)
    return n, f"{n:,} row(s) over {res.get('days', 0)} session(s)"


def _step_provider(asof: str) -> tuple[int, str]:
    """Refresh the provider metric cache for the whole tradeable universe.

    REPLACES the standalone `Screener-Refetch` task, which pointed at a script
    (`run_refetch.py`) that had been deleted -- so it ran, found nothing, and
    reported success for five days while the fact store silently went stale.
    A job that lives in the registry cannot rot that way: it is listed by
    `--status`, watermarked, and shows up in the job table.

    Finnhub is the bulk source (see providers.FINNHUB_FIELDS). ~58 min for
    3,480 names at its documented 60/min. It runs BEFORE `fundamental` because
    that step's provider overlay reads this cache.
    """
    import bars
    import providers
    uni = bars.tradeable_universe(asof)
    if not uni:
        raise RuntimeError("no tradeable universe; run bars first")

    # UNITS ARE CHECKED BEFORE ANYTHING IS WRITTEN, not after.
    #
    # Finnhub sends market cap in millions and margins in percent; Yahoo sends
    # absolute and fractions. If a provider changes a unit, or a field is added
    # without a multiplier, the sweep would write 3,480 corrupted rows into the
    # store and the damage would only surface later as a company with a 4.5
    # million dollar market cap. Two fields were already 100x apart when this
    # was written (debt_to_equity, dividend_yield), found by exactly this test.
    #
    # So it runs FIRST and raises. A step that fails writes nothing; a step
    # that writes and then notices has already done the harm.
    providers.selftest()

    got = providers.fetch_finnhub(uni, verbose=True)
    n = len(got)
    if not n:
        raise RuntimeError("provider returned nothing for the whole universe")

    # CROSS-CHECK OUR DERIVED ROWS AGAINST THE PROVIDER, EVERY NIGHT.
    #
    # This is the check that did not exist when COLL's EBITDA read $4M against
    # a real $68M. Ten of the fifteen derived rows on the profile page are ALSO
    # published by a provider we already call, so there was never a reason for
    # them to go unchecked -- and it took a user reading a page to find it.
    #
    # A sample, not the universe: 120 names is minutes, catches a systematic
    # break immediately, and a systematic break is the only kind worth waking
    # up to. Disagreement is LOGGED, never acted on -- the provider is a second
    # opinion, not an authority, and silently overwriting our figure with
    # theirs would just move the unverified problem somewhere else.
    detail = f"{n:,} of {len(uni):,} name(s) refreshed from finnhub"
    try:
        import numpy as np
        rng = np.random.default_rng(int(asof.replace("-", "")))
        sample = sorted(rng.choice(uni, size=min(120, len(uni)),
                                   replace=False).tolist())
        cmp = providers.compare(sample, asof=asof, verbose=False)
        if not cmp.empty:
            live = cmp[~cmp["status"].isin(["no comparison",
                                            "we do not compute"])]
            dis = live[live["status"] == "DISAGREE"]
            if len(live):
                agree = (1 - len(dis) / len(live)) * 100
                log(f"    [provider] cross-check: {agree:.1f}% agree on "
                    f"{len(live)} comparison(s); {len(dis)} disagree")
                worst = (dis.groupby("field").size().sort_values(ascending=False)
                         .head(3))
                for fld, cnt in worst.items():
                    log(f"      {fld}: {cnt} name(s) disagree")
                detail += f"; cross-check {agree:.0f}% agree"
    except Exception as exc:                                     # noqa: BLE001
        log(f"    [provider] cross-check unavailable ({type(exc).__name__})")

    return n, detail


# --------------------------------------------------------------- backfill
# WHAT THIS FIXES: a score step scores exactly the session it is handed. Close
# the laptop for three days and those three sessions have no hype, dip, combo
# or fundamental rows -- permanently, and nothing reported it. `daily_run`'s
# catch-up only reconciles `days_on_list` from local bars, and
# `scores.catchup` builds MONTHLY historical series, so neither covers a short
# gap. Measured on 2026-08-12: the 05:00 run lost its lock and a whole day's
# scores simply never existed.
#
# BOUNDED ON PURPOSE. If a month is missing, doing the most recent
# BACKFILL_MAX_SESSIONS and saying so is better than silently starting a
# multi-hour job at 05:00 that the next trigger then collides with.
#
# 25, not 10. The horizon is how far back a gap is even VISIBLE, and at 10 the
# oldest gaps were invisible forever: on 2026-08-20 `sentiment` was missing
# eleven sessions from 2026-07-21..2026-08-04, all of them 15-21 sessions back,
# so no run would ever have seen them. 25 reaches them. What keeps that safe is
# BACKFILL_BUDGET_S, not the horizon -- widening the search costs nothing
# because the TIME budget still decides how much actually gets done per run.
BACKFILL_MAX_SESSIONS = 25

# ONLY DAILY MODULES. Backfill answers "was a day MISSED", which is only
# meaningful where a row was expected every day.
#
# `fundamental` was excluded here for exactly that reason -- it was WEEKLY, so
# its empty days were empty by design and "filling" them would have
# manufactured rows nobody asked for. That reasoning expired when fundamental
# moved to CADENCE_DAILY alongside the nightly provider sweep: it is now
# expected every session, so a session with no rows is a genuine gap. MEASURED
# 2026-08-20: `fundamental` was missing 2026-08-10 and 2026-08-11 while `dip`
# and `combo` -- which were wrapped -- had both. Unwrapped, that loss was
# permanent.
#
# `sentiment` was never wrapped either, and is missing eleven sessions from
# 2026-07-21..2026-08-04 for the same reason. It costs ~10s/session, so it
# clears its whole backlog in one run.
#
# The COST objection to fundamental (~109 min/session) is still real and is
# answered by BACKFILL_BUDGET_S below, not by exclusion: the budget is checked
# BEFORE each session, so an expensive module backfills exactly one session per
# run and the daily pass is never put at risk.
BACKFILL_MODULES = ("hype", "dip", "combo", "sentiment", "fundamental")

# TIME budget for backfilling, per step, per run.
#
# Measured 2026-08-14: dip and combo backfill a session in ~5 SECONDS, but
# `hype` takes ~70 MINUTES -- it cannot reuse the warm caches the daily path
# builds. A count-based bound (BACKFILL_MAX_SESSIONS) cannot express that
# difference, so four hype sessions blew a 6-hour task limit and today's
# session was never scored.
#
# 25 minutes lets dip and combo clear their whole backlog instantly and lets
# hype make steady progress -- roughly one session per night, caught up within
# a week, without ever putting the daily pass at risk.
BACKFILL_BUDGET_S = 25 * 60


def _missing_sessions(module: str, asof: str, limit: int) -> list[str]:
    """Trading sessions at or before `asof` with no stored rows for `module`."""
    import pandas as pd                     # module-level import is deliberate
    import calendar_us                       # elsewhere too -- see step bodies
    import scores
    try:
        stored = set(scores.sessions_stored(module))
    except Exception:                                            # noqa: BLE001
        return []
    want = calendar_us.sessions_between(
        (pd.Timestamp(asof) - pd.tseries.offsets.BDay(limit * 2)
         ).strftime("%Y-%m-%d"), asof)
    recent = [d for d in want if d <= asof][-limit:]
    return [d for d in recent if d not in stored]


# MODULES WHOSE STEP WRITES A FIXED `latest.html`.
#
# `senti_screen.write_html` and `fund_screen.write_html` write BOTH a
# session-stamped page and an unstamped `latest.html`:
#
#     p = config.REPORTS_SENTIMENT / f"{asof}.html"     # fine
#     (config.REPORTS_SENTIMENT / "latest.html").write  # FIXED PATH
#
# So every backfilled session overwrites `latest.html`, and the LAST session
# processed wins. Gaps are filled newest-first, which means the last one is the
# OLDEST -- `latest.html` would have ended up showing 2026-07-16 while the
# store held 2026-08-19. The page a reader opens would silently be a month old.
#
# `hype`, `dip` and `combo` are unaffected: they write no report of their own,
# their pages come from `explore` and `dashboard`. So this map is exactly the
# two modules wrapped on 2026-08-20, and it must grow if a future score step
# starts writing its own landing page.
LATEST_HTML_DIR = {
    "sentiment": "REPORTS_SENTIMENT",
    "fundamental": "REPORTS_FUNDAMENTAL",
}


def _restore_latest_html(module: str, asof: str) -> None:
    """Point `latest.html` back at `asof` after a backfill moved it.

    A copy, not a re-score: re-running the step for `asof` would cost another
    ~109 minutes for `fundamental` to regenerate a page already written to
    `{asof}.html` earlier in the same step.
    """
    attr = LATEST_HTML_DIR.get(module)
    if not attr:
        return
    d = getattr(config, attr, None)
    if d is None:
        return
    src = d / f"{asof}.html"
    if not src.exists():
        log(f"    [{module}] no {asof}.html to restore latest.html from")
        return
    try:
        (d / "latest.html").write_text(src.read_text(encoding="utf-8"),
                                       encoding="utf-8")
        log(f"    [{module}] latest.html restored to {asof}")
    except OSError as exc:
        log(f"    [{module}] could NOT restore latest.html: {repr(exc)[:80]}")


def _with_backfill(module: str, compute_one) -> Callable[[str], tuple[int, str]]:
    """Score TODAY first, then fill recent gaps within a time budget.

    TWO THINGS HERE ARE THE RESULT OF THIS GOING WRONG ON 2026-08-14.

    **Today is scored FIRST.** The original version backfilled before scoring
    `asof`, which put the least important work in front of the most important.
    `hype` backfill measured ~70 minutes per session -- against the ~3 minutes
    estimated from the daily cost, a 23x miss, because scoring a PAST session
    cannot reuse the warm caches the daily path enjoys. Four backfills consumed
    5.5 hours, Task Scheduler killed the run at its 6-hour limit, and the
    session everyone actually looks at was never scored at all. Today's data
    must never be starved by history.

    **The budget is TIME, not a session count.** `BACKFILL_MAX_SESSIONS` bounds
    how far back to look; it says nothing about how long that takes, and a
    count-based bound is worthless when the per-item cost is unknown. Backfill
    now stops when `BACKFILL_BUDGET_S` is spent and says how many remain --
    they are picked up on subsequent runs, a few per night, without ever
    threatening the daily pass.
    """
    def run(asof: str) -> tuple[int, str]:
        rows, detail = compute_one(asof)          # TODAY FIRST, always

        # NEWEST GAP FIRST. `_missing_sessions` returns chronological order,
        # which spends the budget on the oldest session available -- the exact
        # inversion of this function's own rule that today's data must never be
        # starved by history. With an expensive module doing one session per
        # run, chronological order would have filled July before the 2026-08-10
        # hole a reader would actually notice.
        gaps = sorted(
            (d for d in _missing_sessions(module, asof, BACKFILL_MAX_SESSIONS)
             if d != asof), reverse=True)
        if not gaps:
            return rows, detail

        started, filled = time.time(), 0
        for d in gaps:
            spent = time.time() - started
            if spent > BACKFILL_BUDGET_S:
                log(f"    [{module}] backfill budget spent "
                    f"({spent / 60:.0f}m); {len(gaps) - filled} session(s) "
                    f"left for the next run")
                break
            try:
                compute_one(d)
                filled += 1
                log(f"    [{module}] backfilled missed session {d} "
                    f"({(time.time() - started) / 60:.0f}m spent)")
            except Exception as exc:                             # noqa: BLE001
                log(f"    [{module}] backfill {d} failed: {repr(exc)[:90]}")
        if filled:
            # MUST come after the loop, never inside it: each backfilled
            # session rewrites `latest.html` to itself.
            _restore_latest_html(module, asof)
        if filled or len(gaps) > filled:
            detail = (f"{detail}; backfilled {filled} of {len(gaps)} "
                      f"missed session(s)")
        return rows, detail
    return run


def _step_hype(asof: str) -> tuple[int, str]:
    """Score module 3. DAILY, unlike `fundamental`, and the reason is the
    attention pillar: volume surge and trade-size shrink are short-horizon flow
    measures that are meaningless a week stale. Costs ~250 KB/session
    (~18 metrics x ~2,800 names) = ~63 MB/yr. Move it to weekly if that matters
    more than timeliness -- nothing else depends on the cadence.
    """
    import bars
    import scores
    import scores.hype                                           # noqa: F401
    uni = bars.tradeable_universe(asof)
    if not uni:
        raise RuntimeError("no tradeable universe; run bars first")
    rows = scores.get("hype").compute(asof, uni)
    if rows.empty:
        raise RuntimeError("hype produced no rows")
    scores.write(rows, session=asof, module="hype")
    n = int((rows["metric"] == "hype_score").sum())
    return n, f"{n:,} name(s) scored of {len(uni):,} tradeable"


def _step_dip(asof: str) -> tuple[int, str]:
    """Score module 4. Runs AFTER fundamental/sentiment/hype because it reads
    their stored rows for the same session rather than recomputing them."""
    import bars
    import scores
    import scores.dip                                            # noqa: F401
    uni = bars.tradeable_universe(asof)
    rows = scores.get("dip").compute(asof, uni)
    if rows.empty:
        raise RuntimeError("dip produced no rows -- have the other modules run?")
    scores.write(rows, session=asof, module="dip")
    gated = int(rows[(rows["metric"] == "dip_gate")]["value"].sum())
    n = int((rows["metric"] == "dip_score").sum())
    return n, f"{n:,} scored of {gated:,} that passed the quality gate"


def _step_combo(asof: str) -> tuple[int, str]:
    """Score module 5. Runs LAST of the score modules: it reads all four.

    THIS STEP WAS MISSING. `combo` was built by hand and by the one-off
    overnight chains, so its series looked current -- 176 sessions, newest
    matching every other module -- while nothing in the daily job maintained
    it. The next ordinary 05:00 run would have scored sentiment, fundamental,
    hype and dip for the new session and quietly left combo a day behind, and
    then two, with the pages showing a stale number rather than an empty one.

    A module that is only ever backfilled by hand looks identical to a
    maintained one right up until you stop backfilling it.
    """
    import bars
    import scores
    import scores.combo                                          # noqa: F401
    uni = bars.tradeable_universe(asof)
    rows = scores.get("combo").compute(asof, uni)
    if rows.empty:
        # Legitimate when the study has never run -- combo refuses to invent
        # weights -- so this is a clean zero, not an exception.
        return 0, "no rows; the factor study has not run, so no weights exist"
    scores.write(rows, session=asof, module="combo")
    n = int((rows["metric"] == "combo_h20").sum())
    return n, f"{n:,} name(s) scored across three horizons"


def _step_explore(asof: str) -> tuple[int, str]:
    import explore
    p = explore.build(verbose=False)
    return 1, f"{p.name} rebuilt"


def _step_snapshots(asof: str) -> tuple[int, str]:
    """Dated copies of explore.html for the OFFLINE case only.

    Only MISSING sessions are built, so the steady state is one a day. Surplus
    ones are deleted here rather than left to the `retention` step, whose
    REPORT_KEEP_DAYS=120 window would hold 120 of them: the limit that matters
    for these is a COUNT, not an age.

    The full history is not here. It is in the score store, and serve.py renders
    any of it on demand -- so this step is a convenience, not the archive.
    """
    import calendar_us as cal
    import explore
    want = [s for s in cal.all_sessions() if s <= asof][-config.SNAPSHOT_SESSIONS:]
    built = 0
    for s in want:
        p = config.REPORTS_EXPLORE / f"{s}.html"
        if p.exists():
            continue
        try:
            explore.build(verbose=False, session=s)
            built += 1
        except Exception as exc:                                 # noqa: BLE001
            # Named, not swallowed -- a silently missing snapshot leaves a hole
            # in the picker that looks like the session never happened.
            return built, f"{built} built, FAILED at {s}: {type(exc).__name__}"

    keep, dropped, freed = set(want), 0, 0
    for p in config.REPORTS_EXPLORE.glob("*.html"):
        stem = p.stem
        if len(stem) == 10 and stem[4] == "-" and stem not in keep:
            try:
                freed += p.stat().st_size
                p.unlink()
                dropped += 1
            except OSError:
                pass

    reach = len(explore.stored_sessions())
    tail = f", {dropped} pruned ({freed / 1e6:.1f} MB)" if dropped else ""
    return built, (f"{built} new, {len(want)} offline / {reach} via server"
                   f"{tail}")


def _step_profiles(asof: str) -> tuple[int, str]:
    """Profile pages for today's bounce flags only -- building all 3,472 would
    cost minutes and write ~90 MB of HTML nobody asked for. Any other ticker is
    one `python stock_profile.py TICKER` away."""
    import pandas as pd
    import stock_profile
    f = config.FLAGS / f"{asof}.parquet"
    if not f.exists():
        return 0, "no flags file for this session"
    tk = list(dict.fromkeys(pd.read_parquet(f)["ticker"].astype(str)))
    # ...PLUS every page already on disk.
    #
    # Only today's flags were rebuilt, so a page written on a day its ticker
    # happened to be flagged then sat there ageing with nothing to say so. AMD
    # was served two days stale: it showed neither the recovered 2026 quarters
    # nor the derived fiscal Q4, and looked exactly like a page whose bugs had
    # never been fixed. A stale page is indistinguishable from a current one,
    # which makes it worse than an absent one.
    #
    # This does NOT resurrect the "build all 3,472" cost the docstring rejects
    # -- only pages that already exist are refreshed, 47 of them today, and the
    # history cache below is primed for the whole batch.
    try:
        tk += [p.stem for p in stock_profile.OUT_DIR.glob("*.html")
               if "_" not in p.stem and p.stem not in ("index",)]
        tk = list(dict.fromkeys(tk))
    except Exception:                                            # noqa: BLE001
        pass
    # Prime the history cache ONCE for the whole batch. Without this each
    # profile re-scans every score partition -- measured 22s per ticker per
    # module, i.e. 45 min for 30 flags against a 900s budget.
    for mod in config.SCORE_MODULES:
        try:
            stock_profile.prime_history(mod, tk, asof)
        except Exception:                                    # noqa: BLE001
            pass
    # And the fact store, which history() otherwise re-scans per ticker AND
    # twice per page (annual + quarterly for the toggle). Measured 44.7s for 6
    # tickers cold vs 10.9s prime + 0.16s warm.
    try:
        import fundamentals as _FD
        _FD.prime_history(tk, 16, "Q")
    except Exception:                                        # noqa: BLE001
        pass
    ok, failed = 0, []
    for t in tk:
        try:
            stock_profile.build(t, verbose=False)
            ok += 1
        except Exception as exc:                                 # noqa: BLE001
            # NAMED, not swallowed. The first version counted failures and threw
            # the reason away, so 7 of 30 pages silently never appeared and the
            # step still reported success. A partial result that reads as a
            # whole one is the failure mode this project keeps paying for.
            failed.append(f"{t}({type(exc).__name__})")
    try:
        stock_profile.write_index(verbose=False)
    except Exception:                                        # noqa: BLE001
        pass
    detail = f"{ok} of {len(tk)} profile page(s)"
    if failed:
        detail += f"; FAILED: {', '.join(failed[:8])}"
        if len(failed) > 8:
            detail += f" +{len(failed) - 8} more"
    return ok, detail


def _step_bounce(asof: str) -> tuple[int, str]:
    """The whole bounce pipeline, minus the parts the orchestrator already ran.

    Delegating rather than reimplementing keeps ONE bounce code path. The
    per-stage timings daily_run records in _state.json are lifted into the job
    table as child rows by `_explode_daily_run_timings`, so the dashboard still
    gets stage-level granularity.
    """
    import daily_run
    rc = daily_run.run(window=5, do_confirm=True, do_catchup=True, dry=False,
                       do_sentiment=False, do_universe=False, do_bars=False)
    st = daily_run.load_state()
    n = int(st.get("flag_count", 0) or 0)
    detail = f"{n} flag(s)"
    if st.get("last_errors"):
        detail += f"; stage errors: {', '.join(st['last_errors'])}"
    if rc:
        raise RuntimeError(f"daily_run returned {rc}: {st.get('last_errors')}")
    return n, detail


def _step_fundamental(asof: str) -> tuple[int, str]:
    import fund_screen
    res = fund_screen.run_once(asof=asof, verbose=False)
    if not res.get("ok"):
        raise RuntimeError("fund_screen.run_once reported not-ok "
                           "(no panel stats, or the fact store is empty)")
    n = int(res.get("n", 0) or 0)
    return n, f"{n:,} name(s) scored"


def _step_sec_facts(asof: str) -> tuple[int, str]:
    import fundamentals
    res = fundamentals.backfill(verbose=False)
    n = int(res.get("facts", 0) or 0)
    return n, f"{n:,} fact row(s); {len(fundamentals.stored_quarters())} quarter(s) stored"


def _step_sec_gap(asof: str) -> tuple[int, str]:
    """Close the coverage gap the bulk data sets leave behind.

    MEASURED, in this order, because each step changed the answer:
      588 names had a CIK and no facts. Accepting 20-F/40-F alone did nothing --
      only 16.5% of their tags matched. 19 IFRS aliases took it to 332. Probing
      companyfacts then found the bulk sets simply OMIT many filers, and
      fetching those 332 recovered 141 companies / 65 scoreable names for 0.6 MB.

    The 267 still missing are mostly ETFs and trusts, which have no financial
    statements, plus foreign issuers reporting in CAD or EUR. Those are excluded
    deliberately: putting non-USD values in the same column as dollars would
    corrupt every cross-sectional percentile, and fixing it properly means an FX
    series and a conversion-date convention, not a bigger tag map.
    """
    import fundamentals
    # MISSING **AND** STALE. This used the default target list, which is
    # `coverage_gap()` -- names with no facts at all. A name whose facts simply
    # stopped updating counted as covered, so when the bulk data sets lagged
    # (2026q1 was the newest published set on 2026-08-10) the whole universe
    # froze two quarters back: 94% of names had nothing newer than 2025-12-31
    # while SEC's own API already served the Mar and Jun 2026 quarters. Every
    # health check passed, because they all asked whether data EXISTS.
    targets = fundamentals.refresh_targets(asof)
    gap = len(fundamentals.coverage_gap())
    res = fundamentals.backfill_companyfacts(tickers=targets, verbose=False)
    if not res.get("ok"):
        raise RuntimeError(f"companyfacts failed for {len(res.get('failed', []))} "
                           f"company(ies): {', '.join(res.get('failed', [])[:6])}")
    n = int(res.get("companies", 0) or 0)
    return n, (f"{n} company(ies) refreshed of {len(targets)} targeted "
               f"({gap} with no facts, {len(targets) - gap} stale), "
               f"{res.get('facts', 0):,} row(s)")


def _due_sec_facts(prev) -> tuple[bool, str]:
    """Due iff a quarter in the window is not on disk AND we have not probed for
    it this week.

    The missing-quarter test alone is not sufficient, and the reason is specific:
    `fundamentals.quarters()` always includes the CURRENT quarter, which the SEC
    has not published and will not publish for months. So "a quarter is missing"
    is the permanent steady state, and a purely data-driven check would fetch a
    404 every single day forever. `fetch_quarter` handles that 404 gracefully
    (empty frame, "not yet published"), so it is waste rather than breakage --
    but weekly probing is already ~12x more often than quarterly publication.
    """
    import fundamentals
    want = set(fundamentals.quarters(config.FUNDAMENTALS_YEARS))
    # Bulk only. A quarter present only in the companyfacts fallback is NOT
    # fetched -- treating it as fetched is what lost period 2026-03-31.
    have = set(fundamentals.stored_quarters(include_cf=False))
    missing = sorted(want - have)
    if not missing:
        return False, f"all {len(want)} quarter(s) stored"
    if prev is not None:
        probed = str(prev.get("started") or "")[:10]
        boundary = _dow_boundary(date.today(), config.WEEKLY_DOW).isoformat()
        if probed >= boundary:
            return False, f"{len(missing)} missing, probed {probed}"
    return True, f"{len(missing)} quarter(s) missing, next {missing[0]}"


def _step_events(asof: str) -> tuple[int, str]:
    import events
    df = events.calibrate(verbose=False)
    return len(df), f"{len(df)} event class(es) recalibrated"


# A WALL-CLOCK CEILING THE STEP ENFORCES ON ITSELF.
#
# `Step.timeout` is a label: it is compared against elapsed time AFTER the step
# function returns, so it can report an overrun but never prevent one. On
# 2026-08-16 leaderboard ran 9h31m against its 5,400s budget, held the run lock
# past the task's own 12-hour ExecutionTimeLimit, and would have cost the next
# day's run. A step this long has to bound itself.
#
# Deliberately equal to the step's `timeout`, so "over budget" and "stopped"
# mean the same thing rather than two numbers drifting apart.
LEADERBOARD_BUDGET_S = 5400


def _step_leaderboard(asof: str) -> tuple[int, str]:
    import factor_lab
    out = []
    deadline = time.time() + LEADERBOARD_BUDGET_S
    for module in config.SCORE_MODULES:
        left = deadline - time.time()
        if left <= 0:
            # Reported, never silent: a module dropped for time is a gap in the
            # scoreboard, and a gap that nothing announces reads as "no signal".
            out.append(f"{module}:SKIPPED(budget)")
            continue
        try:
            lb = factor_lab.leaderboard(module, horizon=20, budget_s=left)
            out.append(f"{module}:{len(lb)}")
        except Exception as exc:                                 # noqa: BLE001
            # One module's leaderboard failing must not lose the other's.
            out.append(f"{module}:FAILED({repr(exc)[:40]})")
    return len(out), "  ".join(out)


# How many names the NETWORK-dependent checks sample per run.
#
# The structural checks in `_step_validate` run over the whole store -- they are
# local arithmetic and cost nothing to widen. `verify_metrics` and
# `providers.compare` hit SEC and Finnhub, so they are sampled.
#
# The sample is seeded with the SESSION DATE, not a constant. A fixed seed
# re-checks the same 60 names every night and learns nothing after the first
# run; a session seed accumulates roughly 1,200 distinct names a month and is
# still exactly reproducible -- the seed is readable off the date in the log.
# (`hash()` would NOT be reproducible: Python salts string hashes per process.)
VALIDATE_SAMPLE = 60

# Wall-clock ceiling this step enforces on ITSELF, for the same reason
# LEADERBOARD_BUDGET_S exists: `Step.timeout` is compared against elapsed time
# only AFTER the step returns, so it labels an overrun and never prevents one.
#
# The store-wide `audit` always runs -- it is the check most likely to catch a
# COLL-class bug and its cost does not depend on the sample. The network checks
# are what scale with VALIDATE_SAMPLE and what can stall on a slow SEC or
# Finnhub, so they are the ones skipped when the budget is gone. A skipped
# check is RECORDED, never silent.
VALIDATE_BUDGET_S = 20 * 60


def _step_validate(asof: str) -> tuple[int, str]:
    """Check displayed numbers against sources that are not us.

    NOTHING IN THE PIPELINE DID THIS UNTIL 2026-08-20.

    `verify_metrics`, `providers.compare`, `ttm_invariants` and `audit_metrics`
    all existed, and on 2026-08-16 all passed. But every one of them only ever
    ran because someone typed its name, which means the guarantee expired the
    moment anything downstream changed. A check that runs when it is remembered
    is not a check -- it is a story about one afternoon. The COLL bug (EBITDA
    $4M against a real $68M) survived precisely because the things that would
    have caught it were not wired to anything.

    Four checks, and they are NOT interchangeable -- see audit_metrics for why
    the three classes of number admit different kinds of proof:

      audit         score-store invariants: coverage, constant, fabrication,
                    range, nulls. Whole store. Catches a metric that exists
                    only because a missing input was read as zero.
      ttm windows   the window `_ttm` actually summed spans a year, does not
                    overlap, skips no quarter and holds exactly four ends
      rollforward   every rolled cash-flow TTM re-derived from its three
                    REPORTED legs: FY + YTD_now - YTD_prior.
      sec/provider  independent SEC arithmetic, and our ratios against
                    Finnhub/Yahoo.

    Only `audit` runs store-wide. Everything else runs on the SAMPLE, because
    each of them loads the fact store per call -- see the note on the TTM block.
    Coverage comes from rotation over days, not from doing everything nightly.

    WHAT COUNTS AS A PROBLEM is deliberately narrow: only checks whose failure
    is unambiguously OURS. `audit` HIGH issues, TTM window violations and
    roll-forward identity failures are arithmetic or structural facts about our
    own store. The SEC recompute and the provider cross-check are comparisons
    against third parties that are each wrong often enough to be evidence
    rather than verdicts -- on 2026-08-14 our FCF disagreed with Yahoo on 43%
    of names and SEC proved US right -- so they are recorded and reported, and
    never counted.

    This step NEVER fails the run. A validation failure must not block
    `explore`, `profiles` or `dashboard` -- the pages have to render so the
    problem is visible, and a blocked page is indistinguishable from a quiet
    one. Problems are counted into the detail string, which the dashboard
    shows, and written to data/_validate.csv.
    """
    import numpy as np
    import pandas as pd

    _t_started = time.time()
    parts: list[str] = []
    problems = 0
    rows: list[dict] = []

    def note(check: str, ok: int, bad: int, detail: str = "") -> None:
        rows.append({"asof": asof, "check": check, "ok": ok, "bad": bad,
                     "detail": detail})

    # 1. score-store invariants, whole store
    try:
        import audit_metrics
        issues, _summary = audit_metrics.audit()
        n_high = int((issues["severity"] == "HIGH").sum()) if len(issues) else 0
        problems += n_high
        parts.append(f"audit:{n_high}high/{len(issues)}")
        note("audit", len(issues) - n_high, n_high,
             "; ".join(f"{r.module}.{r.metric}:{r.issue}"
                       for r in issues.head(5).itertuples(index=False))
             if len(issues) else "")
    except Exception as exc:                                     # noqa: BLE001
        parts.append(f"audit:ERR({type(exc).__name__})")
        note("audit", 0, 0, repr(exc)[:120])

    # The rotating sample, drawn BEFORE the checks that use it.
    sample: list[str] = []
    try:
        import bars
        pool = sorted(set(bars.tradeable_universe()))
        if pool:
            rng = np.random.default_rng(int(str(asof).replace("-", "")))
            sample = sorted(rng.choice(
                pool, size=min(VALIDATE_SAMPLE, len(pool)),
                replace=False).tolist())
    except Exception as exc:                                     # noqa: BLE001
        log(f"    [validate] sample unavailable: {repr(exc)[:80]}")

    # 2. The roll-forward identity, ON THE SAMPLE.
    #
    # `ttm_invariants.check()` -- the WINDOW checker -- is deliberately NOT run
    # here. MEASURED 2026-08-20 on unmodified code: 3 of 127 windows pass, a
    # 2.4% pass rate, for large caps and microcaps alike. It is not detecting
    # 97% broken data; its premise does not hold for this source. It wants four
    # consecutive `qtrs==1` quarter-ends, and SEC bulk data does not provide
    # them for most concepts -- 10-Q cash-flow rows are YEAR-TO-DATE, so only
    # Q1 is `qtrs=1` and consecutive ends land a year apart, failing `no_gap`
    # and `spans_year` by construction.
    #
    # Wiring a check with a 2.4% pass rate into a daily step would paint this
    # step permanently red and teach everyone to ignore it. It stays a CLI tool
    # until its premise is fixed; the note below records that it was skipped so
    # the omission is visible rather than forgotten.
    #
    # `check_rollforward` is a different function and genuinely works (27/27
    # measured on the same sample). It re-derives each rolled cash-flow TTM
    # from three REPORTED legs, and reads only from 2024q1, so it is cheap.
    if sample:
        try:
            import ttm_invariants as TI
            # NOW WIRED IN. It was skipped while it re-derived windows from raw
            # `qtrs==1` rows and passed 3 of 127 -- its premise, not the data,
            # was wrong. It now reads the window `_ttm` actually recorded via
            # `fundamentals.ttm_windows`, which restored it to 268/268 on a
            # random 40-name sample, and a negative control confirms it still
            # fails an HD-style 644-day window, an incomplete window and a
            # skipped quarter. A violation here is unambiguously OUR bug, so it
            # COUNTS.
            w = TI.check(sample, asof=asof)
            wbad = int((~w["ok"]).sum()) if len(w) else 0
            problems += wbad
            parts.append(f"ttm:{len(w) - wbad}/{len(w)}")
            note("ttm_windows", len(w) - wbad, wbad, f"{len(sample)} name(s)")
        except Exception as exc:                                 # noqa: BLE001
            parts.append(f"ttm:ERR({type(exc).__name__})")
            note("ttm_windows", 0, 0, repr(exc)[:120])

        try:
            r = TI.check_rollforward(sample)
            rbad = int((~r["ok"]).sum()) if len(r) else 0
            problems += rbad
            parts.append(f"roll:{len(r) - rbad}/{len(r)}")
            note("rollforward", len(r) - rbad, rbad, f"{len(sample)} name(s)")
        except Exception as exc:                                 # noqa: BLE001
            parts.append(f"roll:ERR({type(exc).__name__})")
            note("rollforward", 0, 0, repr(exc)[:120])

    # 4. sampled: independent SEC arithmetic, and provider cross-check
    if sample and (time.time() - _t_started) > VALIDATE_BUDGET_S:
        log(f"    [validate] budget {VALIDATE_BUDGET_S}s spent before the "
            f"network checks; skipping them this run")
        parts.append("sec:skipped(budget)")
        parts.append("prov:skipped(budget)")
        note("sec_recompute", 0, 0, "skipped: validate budget spent")
        note("provider_cross_check", 0, 0, "skipped: validate budget spent")
        sample = []

    if sample:
        try:
            import verify_metrics
            rc = verify_metrics.run(sample, asof)
            # RECORDED, NOT COUNTED -- and this is not leniency, it is the
            # documented limitation. `verify_metrics` cannot follow a
            # roll-forward, so it flags every rolled cash-flow TTM as a
            # mismatch: measured here, 29 of 68 fields, almost all cfo_ttm and
            # capex_ttm. The `rollforward` check above re-derives those same
            # values from three REPORTED legs and passed 27/27, which is
            # positive proof our number is what the filings say.
            #
            # Counting them would put this step permanently in PROBLEM state,
            # and a checker that cries wolf gets ignored -- which is how the
            # COLL bug survived a wall of green checkmarks.
            parts.append("sec:pass" if rc == 0 else "sec:see-csv")
            note("sec_recompute", int(rc == 0), int(rc != 0),
                 f"{len(sample)} name(s); rolled TTMs flag here by design, "
                 f"see rollforward")
        except Exception as exc:                                 # noqa: BLE001
            parts.append(f"sec:ERR({type(exc).__name__})")
            note("sec_recompute", 0, 0, repr(exc)[:120])

        try:
            import providers
            cmp_df = providers.compare(sample, asof, verbose=False)
            if len(cmp_df):
                st = cmp_df["status"]
                agree = int((st == "agree").sum())
                dis = int((st == "DISAGREE").sum())
                rate = agree / max(1, agree + dis)
                # A DISAGREEMENT IS A QUESTION, NOT A VERDICT. On 2026-08-14
                # our FCF disagreed with Yahoo on 43% of names and SEC proved
                # US right. So this is reported, never counted as a problem.
                parts.append(f"prov:{rate:.0%}({agree}/{agree + dis})")
                note("provider_cross_check", agree, dis,
                     "disagreement is a question, not a verdict")
        except Exception as exc:                                 # noqa: BLE001
            parts.append(f"prov:ERR({type(exc).__name__})")
            note("provider_cross_check", 0, 0, repr(exc)[:120])

    try:
        pd.DataFrame(rows).to_csv(config.DATA / "_validate.csv", index=False)
    except (OSError, ValueError) as exc:
        log(f"    [validate] could not write _validate.csv: {repr(exc)[:80]}")

    head = "CLEAN" if problems == 0 else f"{problems} PROBLEM(S)"
    parts.append(f"[{time.time() - _t_started:.0f}s]")
    return problems, f"{head} -- " + "  ".join(parts)


def _step_retention(asof: str) -> tuple[int, str]:
    """Age-based pruning only. Every horizon derives from HISTORY_YEARS, which is
    now 10 -- see the config comment before ever lowering it."""
    dropped = []
    dropped += [f"reject:{d}" for d in
                store.prune_dated(config.REJECTS, config.REJECT_KEEP_DAYS)]
    dropped += [f"1h:{d}" for d in store.prune_1h()]
    # reports/ retention. This call existed before and pruned NOTHING: it went
    # to a `prune_dated` hard-coded to `*.parquet` while reports are .html/.csv,
    # and post-reorganisation the dated files live one level down in their own
    # folders. Both are fixed; `latest.html` and `index.html` survive because
    # `prune_dated` now requires the stem to actually be a date.
    for _d in config.REPORT_DIRS:
        dropped += [f"report:{x}" for x in
                    store.prune_dated(_d, config.REPORT_KEEP_DAYS,
                                      patterns=("*.html", "*.csv"))]
    try:
        import scores
        dropped += [f"score:{d}" for d in scores.prune()]
    except Exception:                                            # noqa: BLE001
        pass
    return len(dropped), (f"pruned {len(dropped)} file(s)" if dropped
                          else "nothing to prune")


def _step_docs(asof: str) -> tuple[int, str]:
    """Regenerate the measured blocks in the handover docs.

    Runs LAST, after every other step has written its job row, so the cost table
    reflects the run that just happened. Every hand-typed cost table in this
    project went stale within a day; these are read from `_jobs.parquet` and the
    stores themselves.
    """
    import docs
    n = docs.apply(verbose=False)
    return n, f"{len(docs.BLOCKS)} generated block(s) in {n} file(s)"


def _step_dashboard(asof: str) -> tuple[int, str]:
    """Last step by design: it renders the job table, so it must run after every
    other step has written its row. Its own row therefore always reads `running`
    on the page it just built -- which is honest, not a bug.
    """
    import dashboard
    p = dashboard.build(verbose=False)
    return len(REGISTRY), f"{p.name} rebuilt"


# --------------------------------------------------------------- the registry
# Order is execution order and it is load-bearing: bars before anything that
# reads them, news before the sentiment cache, sentiment before the bounce
# report (report.py badges cards from stored score rows).
REGISTRY: tuple[Step, ...] = (
    Step("universe", _step_universe, config.CADENCE_DAILY, timeout=120,
         desc="Alpaca asset list -> tradeable registry; how delistings are seen"),
    Step("bars", _step_bars, config.CADENCE_DAILY, depends_on=("universe",),
         timeout=600, desc="daily bar delta + split recheck"),
    Step("macro", _step_macro, config.CADENCE_DAILY, depends_on=("bars",),
         timeout=900, desc="breadth, sector ETFs, FRED, GPR/EPU"),
    Step("news", _step_news, config.CADENCE_DAILY, timeout=600,
         desc="Alpaca news delta (the orchestrator owns this fetch)"),
    Step("senti_cache", _step_senti_cache, config.CADENCE_DAILY,
         depends_on=("news",), timeout=600,
         desc="lexicon scores per article, cached once per day"),
    Step("sentiment", _with_backfill("sentiment", _step_sentiment),
         config.CADENCE_DAILY,
         depends_on=("senti_cache", "bars"), timeout=900,
         desc="sentiment score module + dashboard"),
    Step("shortvol", _step_shortvol, config.CADENCE_DAILY, timeout=600,
         desc="FINRA Reg SHO daily short volume (feeds hype)"),
    Step("hype", _with_backfill("hype", _step_hype), config.CADENCE_DAILY,
         depends_on=("bars", "shortvol"),
         # 7200s, not 1200s. MEASURED: median 2.1 min but last 109.0 min and
         # slowest 116.5 min, so the old budget flagged OVER BUDGET on nearly
         # every real run. A label that always fires is noise, and noise is how
         # a genuine 10-hour overrun went unnoticed.
         timeout=7200,
         desc="attention + narrative-premium score module (daily: attention "
              "is a short-horizon flow measure)"),
    Step("bounce", _step_bounce, config.CADENCE_DAILY, depends_on=("bars",),
         timeout=900, desc="support-bounce screen, confirm, report, outcomes"),
    Step("provider", _step_provider, config.CADENCE_DAILY,
         depends_on=("universe",), timeout=7200,
         desc="finnhub metric cache for the universe (the source for displayed "
              "ratios; ~58 min at 60 req/min)"),
    # DAILY, NOT WEEKLY, NOW THAT THE PROVIDER SWEEP IS DAILY.
    #
    # It was weekly because it is filing-driven -- fundamentals genuinely only
    # change when someone files. That stopped being the whole story when this
    # step became the place the PROVIDER OVERLAY is applied: Finnhub is
    # refreshed every night, and a weekly consumer means fresh provider data
    # sits in the cache for up to six days without ever reaching a page. The
    # sweep would be pointless.
    #
    # Affordable because the step's own median is 39.5s across the last eight
    # runs (the 4,313s outlier was a single run during the fact-store growth),
    # and the provider half is a cache hit measured at 0.07s.
    Step("fundamental", _with_backfill("fundamental", _step_fundamental),
         config.CADENCE_DAILY,
         depends_on=("bars", "provider"), timeout=10800,
         desc="fundamental score module + dashboard (daily: it applies the "
              "provider overlay, which is refreshed nightly)"),
    Step("sec_facts", _step_sec_facts, config.CADENCE_QUARTERLY, timeout=3600,
         session_indexed=False, due_fn=_due_sec_facts,
         desc="SEC Financial Statement Data Sets, one ZIP per quarter"),
    Step("sec_gap", _step_sec_gap, config.CADENCE_WEEKLY, timeout=1200,
         # 332 companies at ~0.85s each was 4.7 min measured; the steady state
         # is far smaller because only NEW gap names are fetched.
         session_indexed=False, depends_on=("sec_facts",),
         desc="companyfacts for filers the bulk data sets omit"),
    Step("events", _step_events, config.CADENCE_WEEKLY, depends_on=("news",),
         timeout=1800, desc="empirical event-severity calibration"),
    # 5400s, not 1800s. MEASURED 3210s once hype and dip had real history
    # (118 and 44 sessions); before that they reported `hype:0 dip:0` and cost
    # nothing. The old budget was sized against a leaderboard that was silently
    # testing two modules instead of four.
    Step("leaderboard", _step_leaderboard, config.CADENCE_WEEKLY,
         depends_on=("sentiment",), timeout=5400,
         desc="factor_lab IC leaderboard per score module"),
    # `dip` reads the other modules' stored rows, so it must follow all three.
    Step("dip", _with_backfill("dip", _step_dip), config.CADENCE_DAILY,
         depends_on=("fundamental", "sentiment", "hype"), timeout=900,
         desc="quality gate + depressed price; the dip thesis (UNMEASURED)"),
    # `combo` reads all four other modules, so it follows dip, and `explore`
    # follows it so the table shows the same session's combined scores.
    Step("combo", _with_backfill("combo", _step_combo), config.CADENCE_DAILY,
         depends_on=("fundamental", "sentiment", "hype", "dip"), timeout=900,
         desc="the three combined scores (DECAYING out of sample -- see docs)"),
    # SESSION-INDEXED, because it RENDERS A SESSION.
    #
    # It was date-indexed, and on 2026-08-14 that meant: the night run built
    # explore at 00:31 from the 08-12 session, then when 08-13's scores landed
    # at 17:33 the step declined to rebuild -- "already ran for 2026-08-14".
    # Profile pages (session-indexed) were current while the all-stock table
    # silently showed a two-day-old session. A step whose OUTPUT depends on the
    # session must have the session as its watermark; only genuinely
    # date-driven work (retention pruning, docs regeneration) may key on today.
    # AFTER the scores are written, BEFORE the pages render. After, because it
    # must validate what today's run actually produced rather than yesterday's.
    # Before, because a number worth flagging is worth flagging on the page the
    # reader opens, not after they have already read it.
    #
    # `depends_on` is deliberately EMPTY of hard requirements beyond combo: a
    # validation step that gets BLOCKED by an upstream failure goes silent at
    # exactly the moment something is most likely wrong.
    Step("validate", _step_validate, config.CADENCE_DAILY,
         depends_on=("combo",), timeout=3600,
         desc="check displayed numbers against SEC, Finnhub and the "
              "store's own invariants"),
    Step("explore", _step_explore, config.CADENCE_DAILY, depends_on=("combo",),
         timeout=300,
         desc="rebuild the sortable/filterable all-stock table"),
    # Session-indexed for the same reason as `explore` above.
    Step("snapshots", _step_snapshots, config.CADENCE_DAILY,
         # Was 2400s for 20 snapshots x ~50s. Indexing `sessions_stored` took
         # the per-snapshot cost from ~48s to ~4s and the count from 20 to 5,
         # so a full cold rebuild is now well under a minute. 300s is generous.
         depends_on=("explore",), timeout=300, session_indexed=False,
         desc="dated explore.html copies so the date picker works offline"),
    Step("profiles", _step_profiles, config.CADENCE_DAILY, depends_on=("bounce",),
         # Was 1800s against a 15.7 min median. Indexing `sessions_stored` and
         # pushing the CIK filter into pyarrow took a 29-page run to 165s, so
         # 900s is now ~5x headroom rather than ~2x.
         timeout=900, desc="per-stock pages for today's bounce flags"),
    Step("retention", _step_retention, config.CADENCE_DAILY, timeout=300,
         session_indexed=False, desc="age-based pruning of rejects, 1h, reports"),
    # Session-indexed too: it summarises the newest session, so a new session
    # must refresh it. It costs 0.5s, so there is no reason to be frugal here.
    Step("dashboard", _step_dashboard, config.CADENCE_DAILY, timeout=120,
         desc="rebuild reports/index.html from the job table"),
    Step("docs", _step_docs, config.CADENCE_DAILY, timeout=300,
         session_indexed=False,
         desc="regenerate measured cost/storage tables in the handover docs"),
)

BY_NAME = {s.name: s for s in REGISTRY}


# =================================================================== the lock
def _lock_info() -> dict | None:
    if not config.ORCH_LOCK_FILE.exists():
        return None
    try:
        return json.loads(config.ORCH_LOCK_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"pid": None, "started": None}


def _pid_alive(pid: int | None) -> bool | None:
    """True/False, or None when we cannot tell.

    Deliberately NOT os.kill(pid, 0): on Windows that maps to TerminateProcess
    for any signal other than CTRL_C/CTRL_BREAK, so the "liveness probe" would
    kill the process it is asking about.
    """
    if not pid:
        return None
    try:
        import psutil
        return psutil.pid_exists(int(pid))
    except ImportError:
        return None
    except Exception:                                            # noqa: BLE001
        return None


def acquire_lock(force: bool = False, wait_min: float = 0.0,
                 poll_s: float = 120.0) -> bool:
    """Take the orchestrator lock. Optionally WAIT for it instead of skipping.

    `wait_min=0` is the historical behaviour: if another run holds the lock,
    log and return False immediately. That is right for interactive runs and
    for the chained jobs, which exit 75 so their caller can decide.

    It is WRONG for the scheduled daily pass. On 2026-08-12 the 05:00 run lost
    the lock to a rebuild chain that had started 94 seconds earlier, exited
    without work, and did not come back for 24 hours -- so a whole day's
    scores, pages and explore snapshot never got built, and the profile pages
    still showed 08-11 data. A 94-second collision cost a full day.

    With `wait_min > 0` the run parks and re-tries instead. Nothing is skipped
    unless the holder is STILL running when the budget runs out, and a holder
    that dies mid-wait is picked up on the next poll by the liveness check.
    """
    deadline = time.monotonic() + wait_min * 60.0
    waited = False
    while True:
        info = _lock_info()
        if info is None:
            break
        started = info.get("started")
        age_h = None
        if started:
            try:
                age_h = (datetime.now()
                         - datetime.fromisoformat(started)).total_seconds() / 3600
            except ValueError:
                pass
        alive = _pid_alive(info.get("pid"))
        stale = (age_h is not None and age_h > config.ORCH_LOCK_STALE_HOURS)
        if alive is False:
            log(f"  breaking lock: pid {info.get('pid')} is gone")
            break
        if stale:
            log(f"  breaking lock: {age_h:.1f}h old "
                f"(> {config.ORCH_LOCK_STALE_HOURS}h), assuming dead")
            break
        if force:
            log(f"  breaking lock by --force (pid {info.get('pid')})")
            break

        left = deadline - time.monotonic()
        if left <= 0:
            verb = (f"Waited {wait_min:g}m, still held. Exiting without work."
                    if waited else "Exiting without work.")
            log(f"ANOTHER RUN HOLDS THE LOCK (pid {info.get('pid')}, "
                f"started {started}). {verb}")
            return False

        if not waited:                      # announce once, not every poll
            log(f"  lock held by pid {info.get('pid')} (started {started}); "
                f"waiting up to {wait_min:g}m for it rather than skipping the "
                f"day")
            waited = True
        time.sleep(min(poll_s, left))

    if waited:
        log("  lock is free now; proceeding")
    config.ORCH_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.ORCH_LOCK_FILE.write_text(
        json.dumps({"pid": os.getpid(),
                    "started": datetime.now().isoformat(timespec="seconds")}),
        encoding="utf-8")
    return True


def release_lock() -> None:
    try:
        config.ORCH_LOCK_FILE.unlink(missing_ok=True)
    except OSError:
        pass


# ------------------------------------------------------------- enable/disable
def paused() -> bool:
    return config.ORCH_DISABLED_SENTINEL.exists()


def pause() -> int:
    config.ORCH_DISABLED_SENTINEL.write_text(
        f"paused {datetime.now():%Y-%m-%d %H:%M:%S}\n"
        "Delete this file (or run `python orchestrator.py --resume`) to re-enable.\n",
        encoding="utf-8")
    print("PAUSED. The scheduled task will still fire and exit immediately.\n"
          f"  sentinel: {config.ORCH_DISABLED_SENTINEL}\n"
          "  resume:   python orchestrator.py --resume")
    return 0


def resume() -> int:
    if config.ORCH_DISABLED_SENTINEL.exists():
        config.ORCH_DISABLED_SENTINEL.unlink()
        print("RESUMED. The next scheduled run will do work.")
    else:
        print("Already enabled.")
    return 0


# ====================================================================== run
def _explode_daily_run_timings(run_id: str, asof: str) -> None:
    """Lift daily_run's per-stage timings into the job table as child rows.

    The bounce pipeline is one registry step so there is only one implementation
    of it, but the dashboard should still show which stage was slow. These rows
    are named `bounce/<stage>` so they never collide with a real registry step.
    """
    import pandas as pd
    try:
        import daily_run
        st = daily_run.load_state()
        timings = st.get("timings") or {}
        if not timings:
            return
        errs = set(st.get("last_errors") or [])
        rows = [{
            "run_id": run_id, "step": f"bounce/{stage}", "cadence": "child",
            "watermark": asof, "started": None, "ended": None,
            "duration_s": float(secs),
            "status": STATUS_ERROR if stage in errs else STATUS_OK,
            "rows": None, "detail": None, "error": None, "traceback": None,
        } for stage, secs in timings.items()]
        cur, new = read_jobs(), _typed_jobs(pd.DataFrame(rows))
        write_jobs(new if not len(cur) else pd.concat([cur, new], ignore_index=True))
    except Exception:                                            # noqa: BLE001
        pass


def run(only: list[str] | None = None, force: bool = False) -> int:
    t0 = time.time()
    run_id = datetime.now().isoformat(timespec="seconds")
    asof = calendar_us.last_closed_session()

    log("=" * 62)
    log(f"ORCH START | run_id={run_id} | asof={asof}")

    jobs = read_jobs()
    results: dict[str, str] = {}
    ran = failed = skipped = 0

    for step in REGISTRY:
        if only and step.name not in only:
            continue

        # dependency gate -- blocked is NOT error, and must not look like one
        bad = [d for d in step.depends_on
               if results.get(d) not in (None, STATUS_OK, STATUS_SLOW,
                                         STATUS_SKIPPED)]
        if bad:
            results[step.name] = STATUS_BLOCKED
            log(f"  [{step.name}] BLOCKED by {', '.join(bad)}")
            record({"run_id": run_id, "step": step.name, "cadence": step.cadence,
                    "watermark": asof if step.session_indexed else str(date.today()),
                    "started": run_id, "ended": None, "duration_s": 0.0,
                    "status": STATUS_BLOCKED, "rows": None,
                    "detail": f"dependency not satisfied: {', '.join(bad)}",
                    "error": None, "traceback": None})
            continue

        if not (force or only):
            due, why = is_due(step, asof, jobs)
            if not due:
                results[step.name] = STATUS_SKIPPED
                skipped += 1
                log(f"  [{step.name}] skipped -- {why}")
                record({"run_id": run_id, "step": step.name,
                        "cadence": step.cadence,
                        "watermark": asof if step.session_indexed
                        else str(date.today()),
                        "started": run_id, "ended": run_id, "duration_s": 0.0,
                        "status": STATUS_SKIPPED, "rows": None, "detail": why,
                        "error": None, "traceback": None})
                continue

        started = datetime.now()
        s = time.time()
        try:
            rows, detail = step.fn(asof)
            dur = time.time() - s
            status = STATUS_SLOW if dur > step.timeout else STATUS_OK
            results[step.name] = status
            ran += 1
            log(f"  [{step.name}] {dur:.1f}s -- {detail}"
                + (f"  ** OVER BUDGET ({step.timeout}s)" if status == STATUS_SLOW
                   else ""))
            record({"run_id": run_id, "step": step.name, "cadence": step.cadence,
                    "watermark": asof if step.session_indexed else str(date.today()),
                    "started": started.isoformat(timespec="seconds"),
                    "ended": datetime.now().isoformat(timespec="seconds"),
                    "duration_s": round(dur, 1), "status": status,
                    "rows": int(rows) if rows is not None else None,
                    "detail": detail, "error": None, "traceback": None})
            if step.name == "bounce":
                _explode_daily_run_timings(run_id, asof)
        except Exception as exc:                                 # noqa: BLE001
            dur = time.time() - s
            results[step.name] = STATUS_ERROR
            failed += 1
            log(f"  [{step.name}] FAILED after {dur:.1f}s: {repr(exc)[:140]}")
            record({"run_id": run_id, "step": step.name, "cadence": step.cadence,
                    "watermark": asof if step.session_indexed else str(date.today()),
                    "started": started.isoformat(timespec="seconds"),
                    "ended": datetime.now().isoformat(timespec="seconds"),
                    "duration_s": round(dur, 1), "status": STATUS_ERROR,
                    "rows": None, "detail": None,
                    "error": f"{type(exc).__name__}: {exc}"[:400],
                    "traceback": traceback.format_exc()[-4000:]})

    mins = (time.time() - t0) / 60
    log(f"ORCH DONE in {mins * 60:.0f}s | {ran} ran, {skipped} skipped, "
        f"{failed} failed")
    return 1 if failed else 0


# =================================================================== reporting
def status() -> int:
    """Read-only view of the job table. Runs nothing -- same contract as the
    dashboard, which must never trigger work on page load."""
    jobs = read_jobs()
    asof = calendar_us.last_closed_session()
    print(f"\n  asof (last closed session): {asof}")
    print(f"  paused: {paused()}   lock: {_lock_info() or 'free'}")
    print(f"  job table: {len(jobs)} row(s)  {config.JOBS_FILE}\n")
    print(f"  {'step':<14}{'cadence':<11}{'last ok':<21}{'dur':>7}  "
          f"{'status':<9}detail")
    print("  " + "-" * 96)
    for step in REGISTRY:
        prev = last_ok(step.name, jobs)
        rows = jobs[jobs["step"] == step.name]
        cur = rows.iloc[-1] if len(rows) else None
        due, why = is_due(step, asof, jobs)
        when = str(prev["ended"])[:19] if prev is not None else "(never)"
        dur = f"{float(prev['duration_s']):.0f}s" if prev is not None else "-"
        st = str(cur["status"]) if cur is not None else "(none)"
        mark = "DUE" if due else "   "
        print(f"  {step.name:<14}{step.cadence:<11}{when:<21}{dur:>7}  "
              f"{st:<9}{mark} {why[:44]}")
        if cur is not None and str(cur["status"]) == STATUS_ERROR:
            print(f"                 ! {str(cur['error'])[:100]}")
    print()
    return 0


def dry_run() -> int:
    jobs = read_jobs()
    asof = calendar_us.last_closed_session()
    print("\n  DRY RUN -- nothing will be written")
    print(f"  asof: {asof}    paused: {paused()}")
    print(f"  store: {store.store_bytes() / 1e6:.0f} MB, "
          f"{len(store.months('1d'))} month file(s)\n")
    total = 0
    for step in REGISTRY:
        due, why = is_due(step, asof, jobs)
        prev = last_ok(step.name, jobs)
        est = float(prev["duration_s"]) if prev is not None else 0.0
        total += est if due else 0
        print(f"  {'WOULD RUN' if due else 'skip     '}  {step.name:<14}"
              f"{('~%.0fs' % est) if est else '   ?':>7}  {why}")
    print(f"\n  estimated wall clock for the due steps: ~{total / 60:.1f} min")
    print("  (estimates are each step's own last measured duration, not a guess)\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Orchestrated daily data + score run.")
    ap.add_argument("--dry-run", action="store_true", help="show what is due")
    ap.add_argument("--status", action="store_true", help="read the job table")
    ap.add_argument("--pause", action="store_true")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--once", action="store_true", help="run even if paused")
    ap.add_argument("--force", action="store_true",
                    help="run every step regardless of cadence")
    ap.add_argument("--step", action="append", metavar="NAME",
                    help="run only this step (repeatable); implies --force for it")
    ap.add_argument("--break-lock", action="store_true",
                    help="take the lock even if another run holds it")
    ap.add_argument("--wait-for-lock", type=float, default=0.0, metavar="MIN",
                    help="if another run holds the lock, wait up to MIN "
                         "minutes for it instead of exiting (default 0). The "
                         "scheduled daily pass uses this so a collision "
                         "delays the run instead of skipping the day.")
    a = ap.parse_args()

    config.dirs()

    if a.pause:
        return pause()
    if a.resume:
        return resume()
    if a.status:
        return status()
    if a.dry_run:
        return dry_run()

    if a.step:
        unknown = [s for s in a.step if s not in BY_NAME]
        if unknown:
            print(f"unknown step(s): {', '.join(unknown)}\n"
                  f"known: {', '.join(BY_NAME)}")
            return 2

    if paused() and not a.once:
        # Exit 0 deliberately: a paused pipeline is not a failed task, and a
        # nonzero code would surface as a Task Scheduler error every night.
        log(f"PAUSED ({config.ORCH_DISABLED_SENTINEL.name} present) -- "
            "exiting without work. Use --resume, or --once to override.")
        return 0

    if not acquire_lock(force=a.break_lock, wait_min=a.wait_for_lock):
        # EXIT 75 IF WE ASKED TO WAIT AND STILL LOST.
        #
        # Returning 0 here says "ran fine" to whatever launched us, which is
        # true enough for the bare scheduled task -- a skipped run is not an
        # error. But a CHAIN that passes --wait-for-lock is saying "this step
        # matters, hold on for it", and a 0 tells the chain to mark the step
        # complete and move on. `fix_data.py` would then rebuild pages and
        # 182 sessions of history on scores that were never recomputed, and
        # nothing would ever say so.
        #
        # 75 is the convention already used by the resumable jobs for exactly
        # this: not a failure, not a success, try again later. Only returned
        # when a wait was requested, so the plain daily task keeps exiting 0.
        return 75 if a.wait_for_lock else 0
    try:
        return run(only=a.step, force=a.force)
    finally:
        release_lock()


if __name__ == "__main__":
    # Last-resort guard, same reasoning as daily_run: a scheduled run that dies
    # outside run() would otherwise leave only "LastTaskResult 1" and no log.
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as exc:                                 # noqa: BLE001
        try:
            log(f"FATAL {type(exc).__name__}: {exc}")
            for ln in traceback.format_exc().splitlines()[-12:]:
                log("  " + ln)
            release_lock()
        finally:
            sys.exit(2)
