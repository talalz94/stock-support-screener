"""
Score modules: the pluggable layer that attaches per-ticker scores to the universe.

The eventual shape of this project is a screener where each stock carries several
independently-updated scores. Sentiment is module 1. This package exists so
module 2 costs a file rather than a refactor.

THE DESIGN DECISION, and the reason this file exists before there is anything to
put in it: scores live in ONE TIDY TABLE, so a new module adds ROWS, NOT COLUMNS.

    data/scores/YYYY-MM.parquet
        session | ticker | module | metric | value | label

A wide table (one column per metric) looks friendlier and is a trap: every new
module migrates the schema of every stored partition, modules cannot be built or
backfilled independently, and a module that emits a different metric set per date
cannot be represented at all. With the tidy form, backtesting one module is a
filter, comparing two is a groupby, and dropping one is a delete.

`value` is the float channel and `label` the categorical one, because real
modules emit both -- `sent_mean_30d` is a number, `event_types` is a set of
strings, and forcing the second through the first means inventing an encoding
that nothing else can read.

WHAT DOES NOT GO IN HERE
------------------------
Rolling aggregates are NOT stored. They are recomputed from a window ending at
`asof` every time they are needed. That is deliberate and it is the same
slice-once discipline that makes `screen_one` causal: a stored aggregate is a
number whose provenance you cannot check later, and if the window that built it
ever reached one bar too far, nothing errors -- the backtest just quietly
improves. Recomputing costs milliseconds and makes leak test #2 possible at all.

What IS stored is the point-in-time snapshot a module computed for a session,
which is a fact about that session, not a rolling view of history.
"""

from __future__ import annotations

import re
from typing import Iterable, Protocol, runtime_checkable

import pandas as pd

import config
import store

SCHEMA = ["session", "ticker", "module", "metric", "value", "label"]

_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")

_REGISTRY: dict[str, "Module"] = {}


# ---------------------------------------------------------------- interface
@runtime_checkable
class Module(Protocol):
    """What a score module must provide.

    A Protocol rather than an ABC so a module is a plain object with three
    methods and does not have to import this package to be one -- which keeps the
    dependency arrow pointing one way (scores -> module, never back) and lets a
    module be unit-tested with no registry present.
    """

    name: str

    def metrics(self) -> list[str]:
        """Every metric key this module can emit. Used to validate writes and to
        build the report legend without running the module."""

    def compute(self, asof: str, tickers: list[str]) -> pd.DataFrame:
        """Point-in-time scores for `asof`. MUST NOT look past `asof`.

        Returns a frame with at least [ticker, metric, value]; `label` optional.
        `session` and `module` are stamped by `write()` so a module cannot
        mislabel its own rows.
        """

    def selftest(self) -> None:
        """Raise on any internal inconsistency. Called by `selftest_all()`."""


def register(module: "Module") -> "Module":
    """Add a module to the registry. Idempotent per name."""
    if not getattr(module, "name", ""):
        raise ValueError("a score module needs a non-empty .name")
    dup = _REGISTRY.get(module.name)
    if dup is not None and dup is not module:
        raise ValueError(f"score module {module.name!r} is already registered")
    _REGISTRY[module.name] = module
    return module


def get(name: str) -> "Module":
    if name not in _REGISTRY:
        raise KeyError(
            f"no score module {name!r}. Registered: {sorted(_REGISTRY) or '(none)'}. "
            "Modules register on import -- is it listed in config.SCORE_MODULES?"
        )
    return _REGISTRY[name]


def registered() -> list[str]:
    return sorted(_REGISTRY)


def load_all() -> list[str]:
    """Import every module named in config.SCORE_MODULES so it registers.

    Import failure is raised, not swallowed: a module that silently fails to load
    produces an empty score table, and an empty score table looks exactly like
    "there was no news today".
    """
    import importlib

    for name in config.SCORE_MODULES:
        importlib.import_module(f"{__name__}.{name}")
    return registered()


# ---------------------------------------------------------------- storage
def part_path(month: str):
    return config.SCORES / f"{month}.parquet"


def months() -> list[str]:
    if not config.SCORES.exists():
        return []
    return sorted(p.stem for p in config.SCORES.glob("*.parquet")
                  if _MONTH_RE.match(p.stem))


def _typed(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in SCHEMA:
        if c not in df.columns:
            df[c] = None
    df["session"] = df["session"].astype(str)
    for c in ("ticker", "module", "metric"):
        df[c] = df[c].astype(str)
    df["value"] = pd.to_numeric(df["value"], errors="coerce").astype("float32")
    df["label"] = df["label"].astype("object").where(df["label"].notna(), None)
    return (df[SCHEMA]
            .sort_values(["session", "module", "ticker", "metric"])
            .reset_index(drop=True))


def write(df: pd.DataFrame, session: str, module: str,
          verbose: bool = False) -> dict[str, int]:
    """Merge one module's scores for one session into the month partition.

    `session` and `module` are stamped here rather than trusted from the frame,
    so a module cannot write rows attributed to another module or another date --
    which would be undetectable later and would corrupt every backtest that
    filtered on them.

    Replaces (session, module) wholesale rather than appending, so re-running a
    session is idempotent instead of doubling every row.
    """
    if df is None or df.empty:
        return {}

    known = set(get(module).metrics()) if module in _REGISTRY else None
    if known:
        unknown = set(df["metric"].astype(str)) - known
        if unknown:
            raise ValueError(
                f"module {module!r} emitted metrics not in .metrics(): "
                f"{sorted(unknown)}. Declare them, or the report legend and the "
                "backtest will silently ignore them."
            )

    df = df.copy()
    df["session"] = str(session)
    df["module"] = str(module)
    df = _typed(df)

    month = str(session)[:7]
    path = part_path(month)
    config.SCORES.mkdir(parents=True, exist_ok=True)

    if path.exists():
        old = pd.read_parquet(path)
        old = old[~((old["session"] == str(session)) & (old["module"] == str(module)))]
        merged = pd.concat([old, df], ignore_index=True)
    else:
        merged = df
    merged = _typed(merged)

    tmp = path.with_suffix(".parquet.tmp")
    merged.to_parquet(tmp, compression=config.COMPRESSION,
                      compression_level=config.COMPRESSION_LEVEL, index=False)
    store.atomic_replace(tmp, path)
    _invalidate_sessions()   # retries Windows sharing violation
    if verbose:
        print(f"    scores[{module}] {session}: {len(df)} row(s) -> {month}.parquet")
    return {month: len(merged)}


def read(module: str | None = None, metrics: Iterable[str] | None = None,
         start: str | None = None, end: str | None = None,
         tickers: Iterable[str] | None = None) -> pd.DataFrame:
    """Read the tidy table, opening only the months inside [start, end]."""
    want = months()
    if start:
        want = [m for m in want if m >= start[:7]]
    if end:
        want = [m for m in want if m <= end[:7]]
    if not want:
        return pd.DataFrame(columns=SCHEMA)

    mset = set(metrics) if metrics else None
    tset = set(tickers) if tickers else None
    frames = []
    for m in want:
        df = pd.read_parquet(part_path(m))
        if module:
            df = df[df["module"] == module]
        if mset:
            df = df[df["metric"].isin(mset)]
        if tset:
            df = df[df["ticker"].isin(tset)]
        if start:
            df = df[df["session"] >= start]
        if end:
            df = df[df["session"] <= end]
        if not df.empty:
            frames.append(df)

    if not frames:
        return pd.DataFrame(columns=SCHEMA)
    return pd.concat(frames, ignore_index=True).reset_index(drop=True)


def wide(module: str, session: str, tickers: Iterable[str] | None = None) -> pd.DataFrame:
    """One session pivoted to ticker x metric. The shape a report wants.

    Categorical metrics come back suffixed `_label` so a numeric column and its
    string counterpart never collide in the same name.
    """
    df = read(module=module, start=session, end=session, tickers=tickers)
    if df.empty:
        return pd.DataFrame(columns=["ticker"])

    num = df.pivot_table(index="ticker", columns="metric", values="value",
                         aggfunc="last")
    lab = df[df["label"].notna()]
    out = num
    if not lab.empty:
        lw = lab.pivot_table(index="ticker", columns="metric", values="label",
                             aggfunc="last")
        lw.columns = [f"{c}_label" for c in lw.columns]
        out = num.join(lw, how="outer")
    return out.reset_index().rename_axis(None, axis=1)


# (module -> sessions), invalidated by write(). Rebuilding it means opening
# EVERY score partition, which is cheap once and ruinous in a loop.
#
# MEASURED: this call was 51 of the 58 seconds of a single profile build --
# `latest_scores()` asks once per module, and each ask read 511 parquet files.
# Nothing was wrong with the answer; it was simply recomputed for every ticker.
_SESSIONS_CACHE: dict[str | None, list[str]] = {}

# ...and the in-memory cache only helps WITHIN one process. Measured cold:
# 36.7s to scan 120 partitions, paid again by every `python <anything>.py`,
# by each orchestrator step and by every serve.py request that renders an old
# session. So the answer is also kept ON DISK, per month, fingerprinted.
#
# The fingerprint is the partition's (size, mtime_ns). A month whose
# fingerprint still matches is not reopened; anything else -- new month,
# rewritten month, index absent -- is rescanned and folded back in. That makes
# the index self-healing: there is no way to hand back a stale answer, because
# a stale entry cannot match the file it describes. Corrupt or unreadable index
# simply means a full scan, never a wrong list.
#
# Derived from config.SCORES on every call, NOT frozen at import. The selftest
# redirects config.SCORES to a temp dir; a module-level constant would have had
# the fake store's index overwrite the real one -- the same "two call sites
# disagree about a cache key" bug this project has already paid for twice.


def _index_path():
    return config.SCORES.parent / f"_{config.SCORES.name}_index.parquet"


def _fingerprint(month: str) -> str:
    st = part_path(month).stat()
    return f"{st.st_size}:{st.st_mtime_ns}"


def _read_index() -> pd.DataFrame:
    if not _index_path().exists():
        return pd.DataFrame(columns=["month", "sig", "module", "session"])
    try:
        df = pd.read_parquet(_index_path())
        if not {"month", "sig", "module", "session"} <= set(df.columns):
            return pd.DataFrame(columns=["month", "sig", "module", "session"])
        return df
    except Exception:                                            # noqa: BLE001
        return pd.DataFrame(columns=["month", "sig", "module", "session"])


def _scan_month(month: str) -> pd.DataFrame:
    df = pd.read_parquet(part_path(month), columns=["session", "module"])
    out = (df.astype({"session": str, "module": str})
             .drop_duplicates()
             .reset_index(drop=True))
    out["month"] = month
    out["sig"] = _fingerprint(month)
    return out[["month", "sig", "module", "session"]]


def _invalidate_sessions() -> None:
    """Drop the in-process cache. The on-disk index needs no invalidation --
    `write()` changes the partition, which changes its fingerprint."""
    _SESSIONS_CACHE.clear()


def sessions_stored(module: str | None = None, refresh: bool = False
                    ) -> list[str]:
    """Every session already scored. The watermark for catch-up."""
    if not refresh and module in _SESSIONS_CACHE:
        return _SESSIONS_CACHE[module]

    have = _read_index() if not refresh else _read_index().iloc[0:0]
    fresh = {m: _fingerprint(m) for m in months()}
    good = have[have.apply(lambda r: fresh.get(r["month"]) == r["sig"], axis=1)] \
        if len(have) else have

    stale = [m for m in fresh if m not in set(good["month"])]
    parts = [good] if len(good) else []
    for m in stale:
        try:
            parts.append(_scan_month(m))
        except Exception:                                        # noqa: BLE001
            continue                                             # skip, do not lie

    idx = (pd.concat(parts, ignore_index=True) if parts
           else pd.DataFrame(columns=["month", "sig", "module", "session"]))

    if stale and len(idx):
        try:
            dest = _index_path()
            tmp = dest.with_suffix(".tmp")
            idx.to_parquet(tmp, compression="zstd", compression_level=9,
                           index=False)
            store.atomic_replace(tmp, dest)
        except Exception:                                        # noqa: BLE001
            pass                    # a missing index costs time, never accuracy

    for mod, grp in idx.groupby("module", observed=True):
        _SESSIONS_CACHE[str(mod)] = sorted(set(grp["session"].astype(str)))
    _SESSIONS_CACHE[None] = sorted(set(idx["session"].astype(str))) if len(idx) else []
    return _SESSIONS_CACHE.get(module, [])


def store_bytes() -> int:
    if not config.SCORES.exists():
        return 0
    return sum(p.stat().st_size for p in config.SCORES.glob("*.parquet"))


def prune(years: float | None = None) -> list[str]:
    """Age-based retention, matching store.prune. Never size-capped."""
    from datetime import date, timedelta

    years = config.SCORE_KEEP_YEARS if years is None else years
    cutoff = (date.today() - timedelta(days=int(years * 365.25) + 40)).strftime("%Y-%m")
    dropped = []
    for m in months():
        if m < cutoff:
            part_path(m).unlink()
            dropped.append(m)
    return dropped


# ---------------------------------------------------------------- selftest
def selftest_all(verbose: bool = True) -> None:
    load_all()
    for name in registered():
        get(name).selftest()
        if verbose:
            print(f"  [{name}] selftest OK ({len(get(name).metrics())} metrics)")


def _selftest() -> None:
    """Round-trip the store without touching the real one."""
    import tempfile
    from pathlib import Path

    real = config.SCORES
    try:
        config.SCORES = Path(tempfile.mkdtemp()) / "scores"

        class _Fake:
            name = "_fake"

            def metrics(self):
                return ["a", "b"]

            def compute(self, asof, tickers):
                return pd.DataFrame({"ticker": tickers, "metric": "a",
                                     "value": range(len(tickers))})

            def selftest(self):
                pass

        fake = register(_Fake())
        rows = fake.compute("2026-08-05", ["AAA", "BBB"])
        rows = pd.concat([rows, pd.DataFrame(
            [{"ticker": "AAA", "metric": "b", "value": 1.0, "label": "X"}])],
            ignore_index=True)

        write(rows, session="2026-08-05", module="_fake")
        # idempotent: rewriting the same session must not double the rows
        write(rows, session="2026-08-05", module="_fake")
        back = read(module="_fake")
        assert len(back) == 3, f"expected 3 rows after rewrite, got {len(back)}"
        assert set(back["session"]) == {"2026-08-05"}
        assert set(back["module"]) == {"_fake"}

        w = wide("_fake", "2026-08-05")
        assert "a" in w.columns and "b_label" in w.columns, list(w.columns)
        assert w.loc[w["ticker"] == "AAA", "b_label"].iloc[0] == "X"
        assert sessions_stored("_fake") == ["2026-08-05"]

        # The on-disk index must live beside the store it describes, or the
        # selftest would clobber the real one. Then: a rewritten partition has
        # a new fingerprint, so a second session MUST appear without a refresh.
        assert _index_path().parent == config.SCORES.parent, _index_path()
        assert not (config.DATA / "_score_index.parquet").exists() or             config.SCORES.parent == config.DATA, "selftest wrote the real index"
        write(rows.assign(session="2026-08-06"), session="2026-08-06",
              module="_fake")
        assert sessions_stored("_fake") == ["2026-08-05", "2026-08-06"],             f"stale index: {sessions_stored('_fake')}"

        # an undeclared metric must be refused, not silently stored
        try:
            write(pd.DataFrame([{"ticker": "AAA", "metric": "zzz", "value": 1.0}]),
                  session="2026-08-05", module="_fake")
        except ValueError:
            pass
        else:
            raise AssertionError("undeclared metric was accepted")

        print("scores selftest OK")
    finally:
        config.SCORES = real
        _REGISTRY.pop("_fake", None)


def main() -> int:
    import argparse

    config.safe_console()
    ap = argparse.ArgumentParser(prog="python -m scores",
                                 description="Score module registry.")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        _selftest()
        selftest_all()
    elif a.list:
        load_all()
        for n in registered():
            m = get(n)
            print(f"  {n:14} {len(m.metrics())} metrics: {', '.join(m.metrics())}")
    else:
        print(f"registered   : {load_all() or '(none)'}")
        print(f"stored months: {months()}")
        print(f"store bytes  : {store_bytes():,}")
    return 0


def catchup(module: str, every: int = 21, frm: str | None = None,
            to: str | None = None, tickers=None, verbose: bool = True,
            rebuild: bool = False) -> int:
    """Build a module's historical score series so factor_lab can test it.

    THE THIRD COPY OF THIS LOOP, hoisted. `fund_screen.catchup` and
    `senti_screen.catchup` each grew their own; a module without a bespoke
    screener (hype, dip) had none at all and was therefore UNMEASURABLE --
    `factor_lab` needs >= 10 dates and they had exactly one, so the leaderboard
    reported `hype:0 dip:0` while both modules looked healthy on the dashboard.

    Monthly spacing by default: `every=21` sessions. A daily series is ~95%
    repeated rows for filing-driven metrics and costs 21x the storage.

    Sessions already stored are skipped, so this is resumable.
    """
    import time

    import bars
    import calendar_us

    mod = get(module)
    asof = to or calendar_us.last_closed_session()
    sess = [s for s in calendar_us.all_sessions() if s <= asof]
    if frm:
        sess = [s for s in sess if s >= frm]
    # Anchor the sampling at the RECENT end so the newest date is always
    # included -- sampling from the old end leaves the latest session out
    # whenever the span is not an exact multiple of `every`.
    todo = sess[::-1][::every][::-1]
    # `rebuild` recomputes sessions that already exist -- needed when a
    # module's INPUTS change (e.g. hype gained short volume), because the
    # stored rows are then a mix of two different metric sets.
    have = set() if rebuild else set(sessions_stored(module))
    todo = [d for d in todo if d not in have]
    if not todo:
        if verbose:
            print(f"  [{module}] nothing to catch up ({len(have)} stored)")
        return 0

    t0, written = time.time(), 0
    for i, d in enumerate(todo, 1):
        try:
            uni = tickers or bars.tradeable_universe(d)
            rows = mod.compute(d, uni)
            if rows is None or rows.empty:
                continue
            write(rows, session=d, module=module)
            written += 1
        except Exception as exc:                                 # noqa: BLE001
            # Named, not swallowed: a silently missing date is a hole in the
            # series that factor_lab cannot distinguish from a quiet market.
            print(f"    {d} FAILED {type(exc).__name__}: {exc}"[:160])
        if verbose and (i % 5 == 0 or i == len(todo)):
            el = (time.time() - t0) / 60
            eta = el / i * (len(todo) - i)
            print(f"    [{module}] {i}/{len(todo)} ({written} written, "
                  f"{el:.1f}m, eta {eta:.0f}m)", flush=True)
    if verbose:
        print(f"  [{module}] {written} session(s) written, "
              f"{(time.time() - t0) / 60:.1f}m")
    return written
