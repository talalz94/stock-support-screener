"""
Cross-run state: setup identity, days-on-list, and forward outcomes.

THE IDENTITY KEY IS (ticker, peak_date), NOT ticker.

If a name retraces, bounces, runs to a NEW peak and retraces again, that is a new
setup and must read "NEW today", not "day 47 on the list". Keying on ticker alone
either suppresses a genuinely new signal or claims a two-month-old flag is fresh --
and the day counter is precisely how you tell a fresh setup from one that has been
sitting. When the detected peak drifts by more than PEAK_DRIFT_TOLERANCE sessions
the old row is retired and a new one opens.

Suppression is a DISPLAY decision, never a data one: every row is always written,
and the report decides what to show. Dropping data to avoid a duplicate alert
loses the history that makes the outcome log possible.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

import numpy as np
import pandas as pd

import calendar_us
import config
import dataset

FLAG_COLUMNS = [
    "ticker", "peak_date", "first_flagged", "last_flagged", "run_count",
    "consecutive", "first_close", "first_bucket", "first_stage",
    "first_support_grade", "first_age_band", "first_price_tier", "first_score",
    "last_close", "last_bucket", "last_stage", "last_score",
    "best_bucket", "best_score", "status", "retired_on", "drop_reason",
]

OUTCOME_COLUMNS = [
    "ticker", "peak_date", "first_flagged", "first_close", "first_bucket",
    "first_stage", "first_support_grade", "first_age_band", "first_price_tier",
    "first_size_tier", "first_liquidity_tier", "first_shape", "first_score",
    "asof", "sessions_held", "close", "ret_pct", "max_close_since", "mfe_pct",
    "min_close_since", "mae_pct", "ret_5d", "ret_10d", "ret_20d",
    "still_flagging", "alive",
]

_BUCKET_RANK = {b: i for i, b in enumerate(
    ["LATE", "WATCH", "SPEC", "EARLY", "PRIME"])}


def _load(path, columns) -> pd.DataFrame:
    if path.exists():
        try:
            return pd.read_parquet(path)
        except Exception:                              # noqa: BLE001
            pass
    return pd.DataFrame(columns=columns)


def _save(df: pd.DataFrame, path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    df.reset_index(drop=True).to_parquet(
        tmp, compression=config.COMPRESSION,
        compression_level=config.COMPRESSION_LEVEL, index=False)
    tmp.replace(path)


def load_flag_state() -> pd.DataFrame:
    return _load(config.FLAG_STATE_FILE, FLAG_COLUMNS)


def load_outcomes() -> pd.DataFrame:
    return _load(config.OUTCOMES_FILE, OUTCOME_COLUMNS)


def _sessions_between(d0: str, d1: str) -> int:
    try:
        return max(len(calendar_us.sessions_between(d0, d1)) - 1, 0)
    except Exception:                                  # noqa: BLE001
        return 0


def update(tagged: pd.DataFrame, asof: str) -> pd.DataFrame:
    """Reconcile today's flags against the registry. Returns `tagged` annotated.

    Adds is_new / days_on_list / promoted, which is what turns a daily list into
    something you can actually track day to day.
    """
    reg = load_flag_state()
    today_rows = []

    if tagged is None or tagged.empty:
        cur = pd.DataFrame(columns=["ticker", "peak_date"])
    else:
        cur = tagged.copy()
        cur["ticker"] = cur["ticker"].astype(str)
        cur["peak_date"] = cur["peak_date"].astype(str)

    if not reg.empty:
        reg["ticker"] = reg["ticker"].astype(str)
        reg["peak_date"] = reg["peak_date"].astype(str)

    seen_keys: set[tuple[str, str]] = set()

    for row in cur.to_dict("records"):
        t, pk = str(row["ticker"]), str(row["peak_date"])

        # Same ticker, a peak that has MOVED more than the tolerance = a new setup.
        # `reg` always carries FLAG_COLUMNS even when empty (load_flag_state
        # guarantees it), so this filters correctly on the very first run -- a bare
        # pd.DataFrame() here has no columns and raises on the next line.
        prior = reg[(reg["ticker"] == t) & (reg["status"] != "retired")]
        match = prior[prior["peak_date"] == pk]

        if match.empty and not prior.empty:
            drift = prior["peak_date"].map(lambda d: abs(_sessions_between(
                min(d, pk), max(d, pk))))
            close_enough = prior[drift <= config.PEAK_DRIFT_TOLERANCE]
            if not close_enough.empty:
                # Same setup, the detector merely re-resolved the peak bar.
                old_pk = str(close_enough.iloc[0]["peak_date"])
                match = close_enough.head(1)
                reg.loc[(reg["ticker"] == t) & (reg["peak_date"] == old_pk),
                        "peak_date"] = pk
            else:
                reg.loc[(reg["ticker"] == t) & (reg["status"] != "retired"),
                        ["status", "retired_on", "drop_reason"]] = \
                    ["retired", asof, "new_peak"]

        key = (t, pk)
        seen_keys.add(key)

        if match.empty:
            today_rows.append({
                "ticker": t, "peak_date": pk,
                "first_flagged": asof, "last_flagged": asof,
                "run_count": 1, "consecutive": 1,
                "first_close": row.get("close"),
                "first_bucket": row.get("bucket"),
                "first_stage": row.get("stage"),
                "first_support_grade": row.get("support_grade"),
                "first_age_band": row.get("age_band"),
                "first_price_tier": row.get("price_tier"),
                "first_score": row.get("score"),
                "last_close": row.get("close"),
                "last_bucket": row.get("bucket"),
                "last_stage": row.get("stage"),
                "last_score": row.get("score"),
                "best_bucket": row.get("bucket"),
                "best_score": row.get("score"),
                "status": "active", "retired_on": "", "drop_reason": "",
            })
        else:
            m = reg["ticker"].eq(t) & reg["peak_date"].eq(pk)
            prev = reg.loc[m].iloc[0]
            same_session = str(prev["last_flagged"]) == asof
            gap = _sessions_between(str(prev["last_flagged"]), asof)
            reg.loc[m, "last_flagged"] = asof
            # Count SESSIONS on the list, not invocations. Re-running the same
            # session (the at-logon trigger, or a manual re-run) must be
            # idempotent -- otherwise "day 4" silently means "I ran it 4 times".
            if not same_session:
                reg.loc[m, "run_count"] = int(prev["run_count"] or 0) + 1
                reg.loc[m, "consecutive"] = (int(prev["consecutive"] or 0) + 1
                                             if gap <= 1 else 1)
            reg.loc[m, "last_close"] = row.get("close")
            reg.loc[m, "last_bucket"] = row.get("bucket")
            reg.loc[m, "last_stage"] = row.get("stage")
            reg.loc[m, "last_score"] = row.get("score")
            reg.loc[m, "status"] = "active"
            if _BUCKET_RANK.get(str(row.get("bucket")), -1) > \
                    _BUCKET_RANK.get(str(prev["best_bucket"]), -1):
                reg.loc[m, "best_bucket"] = row.get("bucket")
            if (row.get("score") or 0) > (prev["best_score"] or 0):
                reg.loc[m, "best_score"] = row.get("score")

    if today_rows:
        new = pd.DataFrame(today_rows)
        # An all-empty `reg` (first ever run) must not take part in the concat:
        # pandas 2.x deprecates inferring dtypes across empty frames and would
        # otherwise coerce the new rows' types.
        reg = new if reg.empty else pd.concat([reg, new], ignore_index=True)

    # Anything active but absent today has dropped off. Cooled first, then retired,
    # so a one-day absence does not lose the setup's history.
    if not reg.empty:
        absent = reg["status"].eq("active") & ~reg.apply(
            lambda r: (str(r["ticker"]), str(r["peak_date"])) in seen_keys, axis=1)
        for i in reg.index[absent]:
            gap = _sessions_between(str(reg.at[i, "last_flagged"]), asof)
            if gap > config.RETIRED_AFTER_RUNS:
                reg.at[i, "status"] = "retired"
                reg.at[i, "retired_on"] = asof
                if not str(reg.at[i, "drop_reason"] or ""):
                    reg.at[i, "drop_reason"] = _drop_reason(
                        str(reg.at[i, "ticker"]), reg.loc[i], asof)
            elif gap >= config.COOLED_AFTER_RUNS:
                reg.at[i, "status"] = "cooled"
                reg.at[i, "consecutive"] = 0
                if not str(reg.at[i, "drop_reason"] or ""):
                    reg.at[i, "drop_reason"] = _drop_reason(
                        str(reg.at[i, "ticker"]), reg.loc[i], asof)

    for c in FLAG_COLUMNS:
        if c not in reg.columns:
            reg[c] = ""
    _save(reg[FLAG_COLUMNS], config.FLAG_STATE_FILE)

    # Annotate today's frame from the reconciled registry.
    #
    # Plain dict lookups rather than a MultiIndex: `df.at[(a, b), "col"]` does not
    # accept a tuple key positionally and raises a confusing TypeError about a
    # missing `col` argument. Dicts are also faster here and cannot silently
    # partial-match.
    if tagged is None or tagged.empty:
        return tagged

    by_key = {(str(r["ticker"]), str(r["peak_date"])): r
              for _, r in reg.iterrows()}
    out = tagged.copy()
    keys = list(zip(out["ticker"].astype(str), out["peak_date"].astype(str)))

    def rank(v) -> int:
        return _BUCKET_RANK.get(str(v), -1)

    out["is_new"] = [
        (str(by_key[k]["first_flagged"]) == asof) if k in by_key else True
        for k in keys]
    out["days_on_list"] = [
        int(by_key[k]["run_count"] or 1) if k in by_key else 1 for k in keys]
    out["promoted"] = [
        (rank(by_key[k]["last_bucket"]) > rank(by_key[k]["first_bucket"]))
        if k in by_key else False for k in keys]
    return out


def _drop_reason(ticker: str, row, asof: str) -> str:
    """Why a name left the list: broken / extended / stalled.

    Without this a name you were tracking just silently vanishes and you never
    learn which of the three happened -- and they mean completely different things.
    """
    try:
        hist = dataset.history(ticker, "1d",
                              start=str(row["last_flagged"]), end=asof)
        if hist.empty:
            return "no_data"
        c = hist["close"].to_numpy(float)
        # Re-derive the level from the flag snapshot on the day it last appeared.
        p = config.FLAGS / f"{row['last_flagged']}.parquet"
        level = np.nan
        if p.exists():
            f = pd.read_parquet(p, columns=["ticker", "level", "bounce_low"])
            hit = f[f["ticker"].astype(str) == ticker]
            if not hit.empty:
                level = float(hit.iloc[0]["level"])
        if np.isfinite(level) and c.min() < level * (1 - config.BREAK_HARD):
            return "support_broken"
        first = float(row["last_close"]) if row["last_close"] else c[0]
        if c[-1] / max(first, 1e-9) - 1 > 0.20:
            return "went_extended"
        return "bounce_stalled"
    except Exception:                                  # noqa: BLE001
        return "unknown"


def dropped_off(asof: str, max_rows: int = 20) -> list[dict]:
    """Names that left the list at `asof`, with the reason."""
    reg = load_flag_state()
    if reg.empty:
        return []
    gone = reg[(reg["status"].isin(["cooled", "retired"]))
               & (reg["retired_on"].astype(str).eq(asof)
                  | reg["last_flagged"].astype(str).ne(asof))]
    gone = gone[gone["drop_reason"].astype(str) != ""]
    if gone.empty:
        return []
    gone = gone.sort_values("last_flagged", ascending=False).head(max_rows)
    return [{"ticker": str(r["ticker"]), "reason": str(r["drop_reason"]),
             "last_seen": str(r["last_flagged"]),
             "days_on_list": int(r["run_count"] or 0),
             "first_close": _f(r["first_close"]), "last_close": _f(r["last_close"])}
            for _, r in gone.iterrows()]


def _f(v):
    try:
        f = float(v)
        return None if f != f else round(f, 2)
    except (TypeError, ValueError):
        return None


def track_outcomes(asof: str, verbose: bool = True) -> pd.DataFrame:
    """Forward performance for every setup flagged in the tracking window.

    Returns are recomputed from FRESH bars each run rather than from a stored
    scalar. That is deliberate: split adjustment is retroactive, so a split landing
    between the flag and the check silently corrupts any return computed once and
    cached.
    """
    reg = load_flag_state()
    if reg.empty:
        return pd.DataFrame(columns=OUTCOME_COLUMNS)

    sessions = calendar_us.all_sessions()
    cutoff = calendar_us.session_offset(sessions, asof, config.OUTCOME_TRACK_DAYS)
    live = reg[reg["first_flagged"].astype(str) >= cutoff].copy()
    if live.empty:
        return pd.DataFrame(columns=OUTCOME_COLUMNS)

    tickers = sorted(live["ticker"].astype(str).unique())
    frames = dataset.panel(tickers, "1d", start=cutoff, end=asof)

    today_flags: set[str] = set()
    p = config.FLAGS / f"{asof}.parquet"
    if p.exists():
        today_flags = set(pd.read_parquet(p, columns=["ticker"])["ticker"].astype(str))

    rows = []
    for _, r in live.iterrows():
        t = str(r["ticker"])
        g = frames.get(t)
        if g is None or g.empty:
            continue
        seg = g[g["date"].astype(str) >= str(r["first_flagged"])]
        if seg.empty:
            continue
        c = seg["close"].to_numpy(float)
        base = float(r["first_close"]) if r["first_close"] else float(c[0])
        if not np.isfinite(base) or base <= 0:
            base = float(c[0])

        def at(n: int):
            return (float(c[n]) / base - 1.0) if len(c) > n else np.nan

        rows.append({
            "ticker": t, "peak_date": str(r["peak_date"]),
            "first_flagged": str(r["first_flagged"]),
            "first_close": base,
            "first_bucket": r.get("first_bucket"),
            "first_stage": r.get("first_stage"),
            "first_support_grade": r.get("first_support_grade"),
            "first_age_band": r.get("first_age_band"),
            "first_price_tier": r.get("first_price_tier"),
            "first_size_tier": "", "first_liquidity_tier": "", "first_shape": "",
            "first_score": r.get("first_score"),
            "asof": asof,
            "sessions_held": int(len(c) - 1),
            "close": float(c[-1]),
            "ret_pct": float(c[-1] / base - 1.0),
            "max_close_since": float(np.nanmax(c)),
            "mfe_pct": float(np.nanmax(c) / base - 1.0),
            "min_close_since": float(np.nanmin(c)),
            # MAE alongside return on purpose: +8% median that required sitting
            # through -20% is a different strategy from one that never drew down,
            # and only MAE separates them.
            "mae_pct": float(np.nanmin(c) / base - 1.0),
            "ret_5d": at(5), "ret_10d": at(10), "ret_20d": at(20),
            "still_flagging": t in today_flags,
            "alive": str(r["status"]) == "active",
        })

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    for c in OUTCOME_COLUMNS:
        if c not in out.columns:
            out[c] = np.nan
    _save(out[OUTCOME_COLUMNS], config.OUTCOMES_FILE)
    if verbose:
        print(f"  outcomes: tracking {len(out)} setup(s) "
              f"flagged since {cutoff}")
    return out


def outcome_table(by: str = "first_bucket") -> pd.DataFrame:
    """Median forward return / MFE / MAE grouped by any first-seen tag."""
    o = load_outcomes()
    if o.empty or by not in o.columns:
        return pd.DataFrame()
    g = o.groupby(by, observed=True)
    t = pd.DataFrame({
        "n": g.size(),
        "med_ret": g["ret_pct"].median(),
        "med_mfe": g["mfe_pct"].median(),
        "med_mae": g["mae_pct"].median(),
        "med_ret_10d": g["ret_10d"].median(),
        "win_rate": g["ret_pct"].apply(lambda s: float((s > 0).mean())),
        "med_held": g["sessions_held"].median(),
    })
    return t.sort_values("med_ret", ascending=False)


def main() -> int:
    config.safe_console()
    ap = argparse.ArgumentParser(description="Flag state and outcome tracking.")
    ap.add_argument("--date", default=None)
    ap.add_argument("--outcomes", action="store_true")
    a = ap.parse_args()
    config.dirs()
    asof = a.date or calendar_us.last_closed_session()

    reg = load_flag_state()
    print(f"flag state: {len(reg)} setup(s)")
    if not reg.empty:
        print("  " + "  ".join(f"{k}={v}" for k, v in
                               reg["status"].value_counts().items()))
        newest = reg.sort_values("last_flagged", ascending=False).head(10)
        cols = ["ticker", "peak_date", "first_flagged", "run_count",
                "consecutive", "first_bucket", "last_bucket", "status"]
        print("\n" + newest[[c for c in cols if c in newest]].to_string(index=False))

    if a.outcomes:
        track_outcomes(asof)
        for by in ("first_bucket", "first_support_grade", "first_stage",
                   "first_age_band", "first_price_tier"):
            t = outcome_table(by)
            if not t.empty:
                print(f"\nby {by}:")
                print(t.round(4).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())

