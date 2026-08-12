"""
The integrity audit. One command that tries to prove the data is wrong.

    python validate.py              run every check
    python validate.py --quick      skip the checks that read whole stores
    python validate.py --only key   run one group

Exit code is 0 only if nothing FAILED. Warnings do not fail the run.

WHY THIS EXISTS, AND WHY IT IS ADVERSARIAL
--------------------------------------------
Every serious bug in this project looked fine from the outside and was found by
accident: a refetch that printed `70/70 DONE` after 51 connection errors; 549
names silently missing a market cap because a caller looked for
`shares_diluted` and the frame held `shares_diluted_ttm`; REXR's revenue 1,703x
understated because an alias sorted before the right tag; a literal `nan` in a
dropdown because `str(float('nan'))` is a truthy string.

None of those raised. So the checks here do not ask "did it run" -- they ask
"is the result self-consistent, in range, unique, point-in-time, and actually
connected to the thing it claims to measure". A check that can only pass is
worth nothing.

THE FOUR CLASSES OF FAILURE IT LOOKS FOR
------------------------------------------
  key       duplicated or undeclared rows -- the tidy table's contract
  time      look-ahead: a fact used before it was filed, a future-dated row
  value     impossible numbers, dead feeds, silent all-NaN metrics
  cross     two stores that disagree, or a fallback leaking past its source
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime

import numpy as np
import pandas as pd

import config

config.safe_console()

FAIL, WARN, OK = "FAIL", "WARN", "ok"
_results: list[tuple[str, str, str, str]] = []      # group, name, status, detail


def record(group: str, name: str, status: str, detail: str = "") -> None:
    _results.append((group, name, status, detail))


def _sessions_upto(asof: str) -> set[str]:
    import calendar_us
    return {s for s in calendar_us.all_sessions() if s <= asof}


# ===========================================================================
# key -- the tidy table's contract
# ===========================================================================
def check_key(asof: str, quick: bool) -> None:
    import scores
    scores.load_all()

    # 1. (session, ticker, module, metric) must be unique. A duplicate means a
    #    write appended instead of replacing, and every downstream pivot then
    #    silently takes 'last' -- which is a coin flip between two values.
    dupes, rows_seen = 0, 0
    months = scores.months()
    look = months[-3:] if quick else months
    worst = ""
    for m in look:
        df = pd.read_parquet(scores.part_path(m),
                             columns=["session", "ticker", "module", "metric"])
        rows_seen += len(df)
        d = int(df.duplicated().sum())
        if d and not worst:
            worst = m
        dupes += d
    record("key", "no duplicate (session,ticker,module,metric)",
           FAIL if dupes else OK,
           f"{dupes:,} duplicate row(s) across {len(look)} month(s), "
           f"{rows_seen:,} checked" + (f"; first in {worst}" if worst else ""))

    # 2. Every stored metric must be declared by its module. An undeclared
    #    metric is invisible to the legend, the study and the dashboards.
    undeclared = {}
    for mod in config.SCORE_MODULES:
        try:
            declared = set(scores.get(mod).metrics())
        except Exception:                                        # noqa: BLE001
            continue
        sess = [s for s in scores.sessions_stored(mod) if s <= asof]
        if not sess:
            continue
        df = scores.read(module=mod, start=sess[-1], end=sess[-1])
        extra = set(df["metric"].astype(str)) - declared
        if extra:
            undeclared[mod] = sorted(extra)
    record("key", "every stored metric is declared in metrics()",
           FAIL if undeclared else OK,
           f"{undeclared}" if undeclared else
           f"{len(config.SCORE_MODULES)} module(s) clean")

    # 3. Declared but never emitted: not a failure, but a metric the docs
    #    promise and the data never supplies reads as "no data" for ever.
    missing = {}
    for mod in config.SCORE_MODULES:
        try:
            declared = set(scores.get(mod).metrics())
        except Exception:                                        # noqa: BLE001
            continue
        sess = [s for s in scores.sessions_stored(mod) if s <= asof]
        if not sess:
            continue
        df = scores.read(module=mod, start=sess[-1], end=sess[-1])
        gap = declared - set(df["metric"].astype(str))
        if gap:
            missing[mod] = sorted(gap)
    record("key", "declared metrics actually emitted",
           WARN if missing else OK,
           f"never emitted: {missing}" if missing else "all declared metrics present")


# ===========================================================================
# time -- look-ahead
# ===========================================================================
def check_time(asof: str, quick: bool) -> None:
    import scores
    import fundamentals as FD
    scores.load_all()

    # THESE TWO CHECKS ASK ABOUT THE MARKET, not about what has been scored.
    # `asof` is the newest FULLY-SCORED session, which lags whenever a weekly
    # module has not run since the last close -- and comparing calendar facts
    # against it flagged 2026-08-10, a perfectly real trading day with
    # perfectly real bars, as both "not in the calendar" and "a bar after the
    # last close". A check whose reference date is the wrong one produces
    # exactly the false alarm that trains people to ignore it.
    import calendar_us as _cal
    market = _cal.last_closed_session()
    real = _sessions_upto(market)
    today = date.today().isoformat()

    # 4. No score session may be a non-trading day or lie in the future.
    bad_sess, future = set(), set()
    for mod in config.SCORE_MODULES:
        for s in scores.sessions_stored(mod):
            if s > today:
                future.add(s)
            elif s not in real:
                bad_sess.add(s)
    record("time", "no future-dated score session", FAIL if future else OK,
           f"{sorted(future)[:5]}" if future else f"newest <= {today}")
    record("time", "every score session is a real trading day",
           FAIL if bad_sess else OK,
           f"{len(bad_sess)} not in the calendar: {sorted(bad_sess)[:5]}"
           if bad_sess else "all sessions on the exchange calendar")

    # 5. A fact must not be FILED BEFORE THE PERIOD IT REPORTS ENDED. This is
    #    the single check that would catch the worst possible bug in this
    #    project: screening on a quarter six weeks before it was public.
    qs = FD.stored_quarters()
    look = qs[-4:] if quick else qs[-16:]
    viol, seen = 0, 0
    for q in look:
        p = FD.part_path(q)
        if not p.exists():
            continue
        d = pd.read_parquet(p, columns=["ddate", "filed"])
        seen += len(d)
        viol += int((d["filed"].astype(str) < d["ddate"].astype(str)).sum())
    frac = viol / max(seen, 1)
    # The one that actually reaches the user. A mis-tagged period date only
    # does harm when it WINS the latest-period race and becomes a company's
    # headline financials -- LEGH was showing period 2033-03-31 on 2026-08-07.
    # So this checks the point-in-time frame the metrics are built from, not
    # the raw store, where an odd row is inert.
    try:
        import bars
        import fundamentals as _FD
        pit = _FD.facts_asof(asof, tickers=bars.tradeable_universe(asof))
        ahead = pit[pd.to_datetime(pit["last_ddate"], errors="coerce")
                    > pd.Timestamp(asof)] if len(pit) else pit
        record("time", "no company's latest period ends after the asof date",
               FAIL if len(ahead) else OK,
               f"{len(ahead)} ticker(s) e.g. "
               f"{list(ahead['ticker'][:4])} report a period that has not ended"
               if len(ahead) else
               f"all {len(pit):,} point-in-time frames use a completed period")
    except Exception as exc:                                     # noqa: BLE001
        record("time", "no company's latest period ends after the asof date",
               WARN, repr(exc)[:110])

    record("time", "no fact filed before its period ended",
           FAIL if frac > 0.02 else (WARN if viol else OK),
           f"{viol:,} of {seen:,} ({frac:.3%}) across {len(look)} quarter(s)")

    # 6. Bars must not run past the last closed session.
    import store
    try:
        b = store.read("1d", start=market, end="2100-01-01",
                       columns=["date", "ticker"])
        ahead = sorted({str(x) for x in b["date"].astype(str)} - {market}) \
            if len(b) else []
    except Exception as exc:                                     # noqa: BLE001
        ahead = []
        record("time", "bar store readable", FAIL, repr(exc)[:120])
    record("time", "no bar dated after the last closed session",
           FAIL if ahead else OK,
           f"{ahead[:5]}" if ahead else f"newest bar == {market}")


# ===========================================================================
# value -- impossible numbers and dead feeds
# ===========================================================================
def check_value(asof: str, quick: bool) -> None:
    import scores
    import metrics_doc
    scores.load_all()

    frames = {}
    for mod in config.SCORE_MODULES:
        sess = [s for s in scores.sessions_stored(mod) if s <= asof]
        if not sess:
            continue
        df = scores.read(module=mod, start=sess[-1], end=sess[-1])
        if not df.empty:
            frames[mod] = df

    # 7. Infinities. A single inf poisons every mean, rank and percentile it
    #    touches, and parquet stores it happily.
    infs = {}
    for mod, df in frames.items():
        v = pd.to_numeric(df["value"], errors="coerce")
        n = int(np.isinf(v).sum())
        if n:
            infs[mod] = n
    record("value", "no infinities in the score store", FAIL if infs else OK,
           f"{infs}" if infs else f"{sum(len(d) for d in frames.values()):,} values finite or NaN")

    # 8. A metric that is CONSTANT across the whole universe is a dead feed
    #    wearing a live one's clothes -- it will never rank anything.
    #     Presence flags (`has_news`, `news_coverage`) are constant BY DESIGN --
    #     they exist to say "this ticker had data", so a column of 1s is the
    #     intended output, not a dead feed.
    dead = []
    for mod, df in frames.items():
        for met, g in df.groupby("metric"):
            if str(met).startswith("has_") or str(met).endswith("_coverage"):
                continue
            v = pd.to_numeric(g["value"], errors="coerce").dropna()
            if len(v) >= 100 and v.nunique() == 1:
                dead.append(f"{mod}.{met}={v.iloc[0]:g}")
    record("value", "no metric is constant across the universe",
           WARN if dead else OK,
           f"{len(dead)}: {dead[:6]}" if dead else "every metric varies")

    # 9. Percentile metrics must sit in [0, 100].
    #
    #    Membership comes from the metric DICTIONARY, not from the name. The
    #    first version of this check used `endswith("_score")` and duly failed
    #    on `z_score` (Altman, -20..40) and `m_score` (Beneish, -15..15) --
    #    neither of which is a percentile. A guard that fires on correct data
    #    trains you to ignore it, which is worse than not having it.
    #    `READ` is the dict that carries ranges -- `DISPLAY` is only labels.
    pct_metrics = set()
    try:
        for m, val in metrics_doc.READ.items():
            rng = val[1] if isinstance(val, (tuple, list)) and len(val) > 1 else ""
            if str(rng).replace(" ", "").startswith("0-100"):
                pct_metrics.add(m)
    except Exception:                                            # noqa: BLE001
        pass
    pct_metrics |= {m for m in
                    (met for df in frames.values() for met in df["metric"].unique())
                    if str(m).startswith("th_") or str(m).endswith("_rank")}

    off = []
    for mod, df in frames.items():
        for met, g in df.groupby("metric"):
            if met not in pct_metrics:
                continue
            v = pd.to_numeric(g["value"], errors="coerce").dropna()
            if len(v) and (v.min() < -0.001 or v.max() > 100.001):
                off.append(f"{mod}.{met} [{v.min():.1f},{v.max():.1f}]")
    record("value", f"percentile metrics within 0-100 ({len(pct_metrics)} checked)",
           FAIL if off else OK, f"{off[:6]}" if off else "all in range")

    # 10. The documented range must match the data. A wrong documented range is
    #     worse than none: `severity_max` claimed 0-3 with a median of 17.
    try:
        bad = metrics_doc.check_ranges()
        record("value", "documented ranges match the data",
               WARN if bad else OK,
               f"{len(bad)} mismatch(es): {bad[:4]}" if bad else "ranges agree")
    except Exception as exc:                                     # noqa: BLE001
        record("value", "documented ranges match the data", WARN, repr(exc)[:110])

    # 11. A declared metric that is 100% NaN today is indistinguishable from a
    #     broken feed. `mktcap` was exactly this: declared, stored, all null.
    allnan = []
    for mod, df in frames.items():
        for met, g in df.groupby("metric"):
            v = pd.to_numeric(g["value"], errors="coerce")
            if len(g) >= 50 and v.notna().sum() == 0 and g["label"].notna().sum() == 0:
                allnan.append(f"{mod}.{met}")
    record("value", "no declared metric is entirely null",
           FAIL if allnan else OK,
           f"{allnan}" if allnan else "every stored metric carries data")


# ===========================================================================
# cross -- two stores that disagree
# ===========================================================================
def check_cross(asof: str, quick: bool) -> None:
    import scores
    import fundamentals as FD
    scores.load_all()

    # 12. The companyfacts fallback must never supply a (cik, quarter) the bulk
    #     store already has: the two round and restate differently, and mixing
    #     them puts two versions of one figure into a single TTM sum.
    #     ONE EXCEPTION, checked here as its own rule rather than waived: for a
    #     filer whose home currency is not USD, the bulk copy is incomplete by
    #     construction (the bulk ingest kept USD rows only for years), so
    #     companyfacts is the authority instead. Either way exactly one source
    #     wins -- what must never happen is a blend.
    #
    #     Tested by ACCESSION provenance, not row counts. A count from a
    #     multi-quarter read compared against one quarter's file proves
    #     nothing; accession numbers say which store a row came from.
    #     Checked as an OUTCOME, on the point-in-time frame the metrics are
    #     actually built from -- not by tracing accessions back to files.
    #     Accession provenance cannot be judged across these two stores: bulk
    #     partitions by filing quarter and companyfacts by period, the two
    #     disagree on the period date (KO Q3 2025 is 2025-09-30 in one and
    #     2025-09-26 in the other), and restatements mean one period
    #     legitimately carries several values. Four attempts at that check all
    #     produced false alarms.
    #
    #     What must be true downstream is simple: one row per ticker, and the
    #     non-USD filers -- whose bulk copy is a few stray USD facts -- come
    #     out carrying their real statements.
    bad = []
    n_fx_ok = 0
    try:
        import bars as _bars
        pit = FD.facts_asof(asof, tickers=_bars.tradeable_universe(asof))
        if len(pit):
            d = pit["ticker"].duplicated().sum()
            if d:
                bad.append(f"{d} ticker(s) appear twice in the point-in-time frame")
            fxset = FD._non_usd_ciks()
            fxrows = pit[pit["cik"].isin(fxset)] if "cik" in pit.columns else pit.iloc[0:0]
            if len(fxrows) and "revenue_ttm" in fxrows.columns:
                n_fx_ok = int(pd.to_numeric(fxrows["revenue_ttm"],
                                            errors="coerce").notna().sum())
                if n_fx_ok < max(1, len(fxrows) // 3):
                    bad.append(f"only {n_fx_ok} of {len(fxrows)} non-USD filers "
                               f"carry revenue -- the authority switch is not "
                               f"taking effect")
    except Exception as exc:                                     # noqa: BLE001
        bad.append(repr(exc)[:90])
    record("cross", "one fact source per filer, resolved in the read",
           FAIL if bad else OK,
           "; ".join(bad[:3]) if bad else
           f"no duplicate tickers; {n_fx_ok} non-USD filer(s) carry real "
           f"statements rather than their stray bulk rows")

    # 13. Currency. Non-USD values in the same column as dollars would corrupt
    #     every cross-sectional percentile, and nothing downstream could tell.
    #
    #     The store is NO LONGER USD-only -- that was the old rule, and it cost
    #     129 filers. The rule now is narrower and stronger: a filer's stored
    #     facts must be in ITS OWN reporting currency, plus unitless things
    #     (shares, ratios, counts). A EUR filer holding a stray USD row is the
    #     hazard, because that row would sit in the same `value` column as its
    #     euros. `facts_asof` filters those out on read; this proves the read
    #     path actually does it, on the frame the metrics are computed from.
    strays = []
    try:
        wide = FD.facts_asof(asof)
        n_fx = 0
        if len(wide) and "currency" in wide.columns:
            n_fx = int((wide["currency"] != "USD").sum())
    except Exception as exc:                                     # noqa: BLE001
        wide, n_fx = None, 0
        strays.append(f"facts_asof failed: {type(exc).__name__}")
    if wide is not None and len(wide):
        home = FD.reporting_currency()
        # Ended quarters only -- see the note at the coverage check. Sampling
        # the lexically-last partitions picks up forward-dated stubs and the
        # check passes having examined nothing.
        cf_q = [p for p in sorted(config.FUNDAMENTALS_CF.glob("*q*.parquet"))
                if p.stem <= FD._current_quarter()][-2:] \
            if config.FUNDAMENTALS_CF.exists() else []
        # A stray in the STORE is harmless -- `facts_asof` filters each filer to
        # its own currency on read, which is the whole design. So the question
        # is not "are there strays on disk" (there always are; SEC filings
        # carry the odd dual-listed figure) but "does the read let one
        # through". Test the filter, on filers known to have strays.
        n_checked = 0
        for p in cf_q:
            d = pd.read_parquet(p, columns=["cik", "uom", "tag"])
            if "uom" not in d.columns:
                continue
            ccy = d["uom"].map(FD.uom_currency)
            off = d[ccy.notna() & (ccy != d["cik"].map(home).fillna("USD"))
                    & d["tag"].astype(str).isin(FD.WANTED)]
            if off.empty:
                continue
            tm2 = FD.ticker_map()
            back = {}
            for t, c in zip(tm2["ticker"], tm2["cik"]):
                back.setdefault(int(c), []).append(str(t))
            probe = [back[c][0] for c in off["cik"].unique()[:10] if c in back]
            if not probe:
                continue
            n_checked += len(probe)
            w = FD.facts_asof(asof, tickers=probe)
            # After the read, each filer's frame must be one currency: its own.
            for _, r in w.iterrows():
                if not isinstance(r.get("currency"), str):
                    strays.append(f"{r['ticker']}: no currency resolved")
    record("cross", "every filer's facts are in its own reporting currency",
           FAIL if strays else OK,
           f"the read let a foreign-currency stray through for {strays[:4]}"
           if strays else
           f"{n_fx} non-USD filer(s) in the point-in-time frame; "
           f"{n_checked} filer(s) with known strays on disk all resolved to a "
           f"single currency after the read")

    # 14. Scored tickers should exist in the universe. Orphans mean a delisting
    #     was handled in one store and not the other.
    try:
        uni = set(pd.read_parquet(config.UNIVERSE_FILE)["ticker"].astype(str))
        orphans = {}
        for mod in config.SCORE_MODULES:
            sess = [s for s in scores.sessions_stored(mod) if s <= asof]
            if not sess:
                continue
            df = scores.read(module=mod, start=sess[-1], end=sess[-1])
            o = set(df["ticker"].astype(str)) - uni - {"_ALL"}
            if o:
                orphans[mod] = len(o)
        record("cross", "scored tickers exist in the universe",
               WARN if orphans else OK,
               f"orphans: {orphans}" if orphans else f"all within {len(uni):,} names")
    except Exception as exc:                                     # noqa: BLE001
        record("cross", "scored tickers exist in the universe", WARN, repr(exc)[:110])

    # 15. Redundancy. Not a failure -- but two metrics at rho >= 0.99 are one
    #     signal, and any composite that weights both counts it twice. This is
    #     the check that would have caught asset_turnover/du_asset_turnover.
    try:
        import itertools
        frames = []
        for mod in config.SCORE_MODULES:
            sess = [s for s in scores.sessions_stored(mod) if s <= asof]
            if not sess:
                continue
            d = scores.read(module=mod, start=sess[-1], end=sess[-1])
            if len(d):
                frames.append(d.pivot_table(index="ticker", columns="metric",
                                            values="value", aggfunc="last"))
        w = pd.concat(frames, axis=1)
        w = w.loc[:, ~w.columns.duplicated()]
        c = w.corr(method="spearman", min_periods=200).abs()
        pairs = [(a, b, c.loc[a, b]) for a, b in itertools.combinations(c.columns, 2)
                 if pd.notna(c.loc[a, b]) and c.loc[a, b] >= 0.99]
        record("cross", "near-duplicate metric pairs (rho >= 0.99)",
               WARN if pairs else OK,
               f"{len(pairs)}: " + ", ".join(f"{a}~{b}" for a, b, _ in pairs[:5])
               if pairs else "no exact duplicates among stored metrics")
    except Exception as exc:                                     # noqa: BLE001
        record("cross", "near-duplicate metric pairs", WARN, repr(exc)[:110])


# ===========================================================================
# combo -- the composite's own contract
# ===========================================================================
def check_combo(asof: str, quick: bool) -> None:
    try:
        import study
        import scores.combo as C
    except Exception as exc:                                     # noqa: BLE001
        record("combo", "module importable", FAIL, repr(exc)[:120])
        return

    sdf = study.read()
    if sdf is None or sdf.empty:
        record("combo", "study available", WARN, "study has not run; combo emits nothing")
        return

    # 16. Horizon purity. Each score's inputs must come from its own horizon.
    mixed = []
    for lab, h in C.HORIZONS.items():
        adm = C.admitted(h, sdf)
        cells = sdf[(sdf["size"] == "all") & (sdf["horizon"] == h)]
        if not set(adm["metric"]) <= set(cells["metric"]):
            mixed.append(lab)
    record("combo", "each score uses only its own horizon's evidence",
           FAIL if mixed else OK,
           f"mixed: {mixed}" if mixed
           else f"{', '.join(C.HORIZONS)} all pure")

    # 17. No composite may feed the composite.
    leaked = []
    for lab, h in C.HORIZONS.items():
        adm = C.admitted(h, sdf)
        bad = set(adm["metric"]) & C.COMPOSITE
        if bad:
            leaked.append(f"{lab}:{sorted(bad)}")
    record("combo", "no composite is admitted into a composite",
           FAIL if leaked else OK,
           f"{leaked}" if leaked else "raw metrics only")

    # 18. Every admitted metric cleared the bar it claims to have cleared.
    weak = []
    for lab, h in C.HORIZONS.items():
        adm = C.admitted(h, sdf)
        if len(adm) and (adm["t"].abs() < C.MIN_T).any():
            weak.append(lab)
    record("combo", f"admitted metrics all reach |t| >= {C.MIN_T}",
           FAIL if weak else OK, f"{weak}" if weak else "threshold honoured")

    # 19. The three scores must not be near-identical -- if they are, carrying
    #     three is a cost with no information behind it.
    try:
        import scores as S
        S.load_all()
        rows = C.MODULE.compute(asof, None)
        w = rows[rows["metric"].isin(["combo_h1", "combo_h20", "combo_h60"])] \
            .pivot_table(index="ticker", columns="metric", values="value")
        if w.shape[1] == 3:
            c = w.corr(method="spearman")
            hi = max(c.where(~np.eye(3, dtype=bool)).max())
            record("combo", "the three horizons rank differently",
                   WARN if hi > 0.95 else OK,
                   f"max pairwise Spearman {hi:.2f} "
                   f"({'too similar to justify three' if hi > 0.95 else 'distinct'})")
    except Exception as exc:                                     # noqa: BLE001
        record("combo", "the three horizons rank differently", WARN, repr(exc)[:110])


# ===========================================================================
# feeds -- is anything silently stale
# ===========================================================================
def check_feeds(asof: str, quick: bool) -> None:
    import scores
    scores.load_all()
    import calendar_us
    sess = [s for s in calendar_us.all_sessions() if s <= asof]
    recent = set(sess[-5:])

    stale = {}
    for mod in config.SCORE_MODULES:
        s = scores.sessions_stored(mod)
        if not s:
            stale[mod] = "never"
        elif not (set(s) & recent):
            stale[mod] = s[-1]
    record("feeds", "every score module ran in the last 5 sessions",
           WARN if stale else OK,
           f"stale: {stale}" if stale else "all current")

    # EVERY SCORE MODULE MUST HAVE A DAILY STEP THAT MAINTAINS IT.
    #
    # "Ran recently" is not the same as "is maintained". `combo` had 176
    # sessions and a newest date matching every other module, so the staleness
    # check above passed -- but nothing in the orchestrator built it. It was
    # current only because it had been backfilled by hand and by one-off
    # overnight chains. The first ordinary day with no one watching, it would
    # have fallen a session behind, then two, showing a stale number rather
    # than an absent one.
    #
    # A module that is only ever backfilled by hand is indistinguishable from a
    # maintained one until the hand stops. This asks the structural question
    # instead of the symptom one.
    try:
        import orchestrator as O
        steps = {s.name for s in O.REGISTRY}
        unmaintained = [m for m in config.SCORE_MODULES if m not in steps]
        record("feeds", "every score module has a daily orchestrator step",
               FAIL if unmaintained else OK,
               f"NOT maintained by any step: {unmaintained}" if unmaintained
               else f"all {len(config.SCORE_MODULES)} module(s) wired into the "
                    f"daily chain")
    except Exception as exc:                                     # noqa: BLE001
        record("feeds", "every score module has a daily orchestrator step",
               WARN, repr(exc)[:110])

    for label, path, pat in (("bars 1d", config.BARS / "1d", "*.parquet"),
                             ("news", config.NEWS, "*.parquet"),
                             ("short volume", config.SHORTVOL, "*.parquet")):
        files = sorted(path.glob(pat)) if path.exists() else []
        if not files:
            record("feeds", f"{label} store present", FAIL, "empty")
            continue
        age = (datetime.now() - datetime.fromtimestamp(
            max(f.stat().st_mtime for f in files))).days
        record("feeds", f"{label} updated recently",
               WARN if age > 5 else OK,
               f"newest partition touched {age}d ago, {len(files)} file(s)")


# ===========================================================================
# pages -- what a reader actually sees
# ===========================================================================
def check_pages(asof: str, quick: bool) -> None:
    try:
        import status
        problems = status.audit_pages(verbose=False)
        record("pages", "no nan cells, mojibake or dead links",
               FAIL if problems else OK,
               f"{len(problems)}: {problems[:4]}" if problems else
               "every rendered page clean")
    except Exception as exc:                                     # noqa: BLE001
        record("pages", "rendered page audit", WARN, repr(exc)[:110])


def check_config(asof: str, quick: bool) -> None:
    """Settings that read as tunable but are wired to nothing.

    A constant nobody references is worse than a missing one: it looks like a
    knob, so someone eventually turns it and nothing happens. Eleven had
    accumulated, including `FUNDAMENTALS_MIN_FILERS` -- a data-integrity guard
    that had been declared for two years and never enforced, so a truncated
    quarter download would have been stored and scored as complete.

    Deliberate deferrals (FinBERT, GDELT) are legitimate; they just have to SAY
    so. Anything unreferenced without a `NOT WIRED` marker is flagged.
    """
    import re
    src = {}
    for pat in ("*.py", "scores/*.py"):
        for f in config.ROOT.glob(pat):
            src[f.name] = f.read_text(encoding="utf-8", errors="replace")
    cfg = src.get("config.py", "")
    names = set(re.findall(r"^([A-Z][A-Z0-9_]{3,})\s*=", cfg, re.M))

    marked, unmarked = [], []
    for n in sorted(names):
        used = sum(t2.count(n) for f, t2 in src.items() if f != "config.py")
        # AND count uses INSIDE config.py, excluding the assignment itself.
        # `SIBLING_ENV` is read two lines below where it is defined, so
        # scanning only the other modules reported a live setting as dead --
        # a guard firing on correct data, which is the failure mode that makes
        # people stop reading guards.
        self_used = sum(
            1 for ln in cfg.splitlines()
            if n in ln and not ln.lstrip().startswith(n + " =")
            and not ln.lstrip().startswith(n + "=")
            and not ln.lstrip().startswith("#"))
        if used or self_used:
            continue
        line = next((ln for ln in cfg.splitlines() if ln.startswith(n + " ")
                     or ln.startswith(n + "=")), "")
        (marked if "NOT WIRED" in line else unmarked).append(n)

    record("config", "no unreferenced setting without a reason",
           WARN if unmarked else OK,
           f"{len(unmarked)} unreferenced and unexplained: {unmarked[:6]}"
           if unmarked else
           f"{len(marked)} deliberate deferral(s), all marked NOT WIRED")


# ===========================================================================
# claims -- do the numbers written into labels still match the tables
# ===========================================================================
def check_claims(asof: str, quick: bool) -> None:
    """Every `t=...` hand-written into a metric label must still be measured.

    The labels are where a measurement becomes a claim the user reads. They
    quote t-stats -- `combo_h60` says "t=+3.19, 69% hit" -- and those numbers
    were typed by hand from a table that gets re-measured. A study rerun that
    moves a t from +4.76 to +2.10 silently turns the label into a lie, and
    nothing anywhere would notice: the page renders, the audit passes, and the
    user reads a stale number as current evidence.

    So: pull every quoted t out of the label text, and require each one to
    match SOME measured cell for that metric -- in the study for in-sample
    claims, in `_oos.parquet` for out-of-sample ones. A label may legitimately
    quote several (in-sample and out-of-sample, two horizons); each is checked
    against the union, because which table a given number came from is not
    recoverable from prose.

    WARN, not FAIL. A drifted label is wrong, but it is documentation -- it
    must not block the audit that checks the data itself.
    """
    import re
    try:
        import metrics_doc
        import study
    except ImportError as exc:
        record("claims", "labels quote measured numbers", WARN, repr(exc)[:110])
        return

    sdf = study.read()
    if sdf is None or sdf.empty:
        record("claims", "labels quote measured numbers", WARN, "no study table")
        return

    # The union of everything that could legitimately be quoted, per metric.
    measured: dict[str, set[float]] = {}
    for _, r in sdf[sdf["size"] == "all"].iterrows():
        if pd.notna(r.get("t")):
            measured.setdefault(str(r["metric"]), set()).add(round(float(r["t"]), 2))
    # Both out-of-sample tables. The walk-forward one matters as much as the
    # single split: the labels quote its PER-FOLD t-stats, and a guard that
    # only knew about `_oos.parquet` flagged those correct numbers as drift.
    n_oos = 0
    for name in ("_oos.parquet", "_oos_walkforward.parquet"):
        p = config.DATA / name
        if not p.exists():
            continue
        odf = pd.read_parquet(p)
        for _, r in odf.iterrows():
            if pd.notna(r.get("t")):
                measured.setdefault(str(r["score"]), set()).add(round(float(r["t"]), 2))
                n_oos += 1

    # IS THE STUDY MEASURING DATA THAT STILL EXISTS?
    #
    # The `claims` check below proves the labels agree with the study. It
    # cannot see the case where BOTH are stale -- and that is what happened on
    # 2026-08-10: history was rewritten to remove fabricated `debt` and `ccc`
    # values and to add two missing quarters, and all 1,600 study cells still
    # dated from before it. `combo` picks its metrics from that study, so the
    # live scores and every "MEASURED" number on the pages described data that
    # no longer exists.
    #
    # A rewrite of a PAST month is the signal. The current month's partition is
    # rewritten every day by normal scoring, so it is excluded.
    try:
        import scores as _sc
        _sc.load_all()
        newest_cell = pd.to_datetime(sdf["measured_at"], errors="coerce").max()
        cur_month = str(asof)[:7]
        rewritten = [m for m in _sc.months()
                     if m < cur_month
                     and pd.Timestamp(_sc.part_path(m).stat().st_mtime, unit="s")
                     > newest_cell]
        record("claims", "the factor study measures the CURRENT data",
               WARN if rewritten else OK,
               f"{len(rewritten)} past month(s) rewritten since the study ran "
               f"({newest_cell:%Y-%m-%d %H:%M}); re-run `python study.py` -- "
               f"combo's weights and every MEASURED label come from it"
               if rewritten else
               f"no past partition changed since {newest_cell:%Y-%m-%d %H:%M}")
    except Exception as exc:                                     # noqa: BLE001
        record("claims", "the factor study measures the CURRENT data",
               WARN, repr(exc)[:110])

    # Per-module unions, for the module blurbs: those are keyed by module name
    # and quote t-stats about the metrics inside, so a quoted number can only
    # be checked against everything that module measured.
    by_module: dict[str, set[float]] = {}
    for _, r in sdf[sdf["size"] == "all"].iterrows():
        if pd.notna(r.get("t")):
            by_module.setdefault(str(r["module"]), set()).add(round(float(r["t"]), 2))
    for name, vals in measured.items():
        if name.startswith("combo_"):
            by_module.setdefault("combo", set()).update(vals)

    quoted = re.compile(r"t\s*=\s*([+-]?\d+\.\d+)")
    drifted, checked, scanned = [], 0, 0

    def _check(key: str, text: str, have: set[float] | None) -> None:
        nonlocal checked
        vals = quoted.findall(text)
        if not vals:
            return
        if not have:
            # Quoting a t for something with no measured cell at all is its own
            # problem -- the claim cannot be checked, which is worse than wrong.
            drifted.append(f"{key}: quotes t but has no measured cell")
            return
        for v in vals:
            checked += 1
            # 0.05 absorbs the 2-decimal rounding in the label; anything larger
            # is the table having moved under the text.
            if not any(abs(float(v) - m) <= 0.05 for m in have):
                near = min(have, key=lambda m: abs(float(v) - m))
                drifted.append(f"{key}: says t={v}, nearest measured {near:+.2f}")

    # The two per-metric label tables, plus the per-module blurbs.
    for container in ("READ", "EXTRA_LABELS"):
        for metric, entry in getattr(metrics_doc, container, {}).items():
            scanned += 1
            body = entry if isinstance(entry, (tuple, list)) else (entry,)
            _check(metric, " ".join(x for x in body if isinstance(x, str)),
                   measured.get(metric))
    for mod, entry in getattr(metrics_doc, "MODULE_DOC", {}).items():
        scanned += 1
        body = entry if isinstance(entry, (tuple, list)) else (entry,)
        _check(f"MODULE_DOC[{mod}]",
               " ".join(x for x in body if isinstance(x, str)),
               by_module.get(mod))

    # A guard that inspected nothing must not report "ok" -- but the thing to
    # verify is that the label tables were FOUND, not that they still contain
    # hard-typed numbers. Zero quoted t-stats is now the goal: measured figures
    # are rendered from the study and the out-of-sample tables at build time,
    # so there is nothing left to drift. Conflating the two would have made
    # this warn forever precisely because the underlying problem was fixed.
    if not scanned:
        record("claims", "hand-written t-stats match the measured tables", WARN,
               "no label table found -- READ/EXTRA_LABELS/MODULE_DOC may have "
               "been renamed, so this check examined nothing")
        return
    if not checked and not drifted:
        record("claims", "hand-written t-stats match the measured tables", OK,
               f"{scanned} label(s) scanned, none quotes a measured figure -- "
               f"all numbers are rendered from the tables at build time")
        return
    record("claims", "hand-written t-stats match the measured tables",
           WARN if drifted else OK,
           f"{len(drifted)} drifted of {checked} checked: {drifted[:4]}"
           if drifted else
           f"{checked} quoted t-stat(s) across {scanned} label(s) all match "
           f"(study + {n_oos} out-of-sample cell(s))")


def check_coverage(asof: str, quick: bool) -> None:
    """Who has no fundamentals, and WHY -- the three reasons kept apart.

    8% of the universe carries no fundamental score, and until this check
    existed that was one undifferentiated number, which is why a real bug hid
    inside it for months: `BRK.B` was indistinguishable from a closed-end fund.
    Both simply had no data. Berkshire, Brown-Forman, Heico, Lennar, Moog,
    Greif, U-Haul and Biglari were all missing because SEC writes `BRK-B` and
    the price feed writes `BRK.B`, and nothing reconciled the separator.

    So the count is split three ways:
      unmapped   -- absent from SEC's company_tickers.json entirely. That file
                    is a convenience list, not a registry, and it genuinely
                    omits filers (AEP among them). Nothing to fix locally.
      no_facts   -- has a CIK but no rows in the fact store: funds and trusts
                    that file N-CSR rather than 10-K, and foreign issuers whose
                    non-USD rows are dropped at ingest by design.
      unexplained-- has a CIK and has facts, but still no score. This is the
                    only bucket that should ever be non-trivial, and it is the
                    one worth waking up for.
    """
    import bars
    import fundamentals as FD
    import scores
    scores.load_all()

    uni = set(bars.tradeable_universe(asof))
    if not uni:
        record("coverage", "fundamental coverage is explained", WARN,
               "no tradeable universe at asof")
        return
    f = scores.read(module="fundamental", start=asof, end=asof)
    have = set(f["ticker"]) if f is not None and len(f) else set()
    miss = sorted(uni - have)

    tm = FD.ticker_map()
    cik_of = dict(zip(tm["ticker"].astype(str).str.upper(), tm["cik"]))
    with_facts = set()
    try:
        # Quarters that have ENDED. Forward-dated disclosures create sparse
        # partitions out to 2028, and those sort last -- taking the lexically
        # final four sampled almost-empty future files.
        qs = [q for q in FD.stored_quarters(include_cf=True)
              if q <= FD._current_quarter()][-4:]
        for q in qs:
            with_facts |= set(pd.read_parquet(FD.part_path(q),
                                              columns=["cik"])["cik"].unique())
    except Exception:                                            # noqa: BLE001
        pass

    # FRESHNESS, not just presence. Every other check here asks whether a
    # company HAS fundamentals; none asked whether they are current, and that
    # is how 94% of the universe sat two quarters stale while the audit stayed
    # green. A US filer reports quarterly with a ~45-day deadline, so a newest
    # period more than ~150 days old means a filing we should already hold.
    try:
        stale = FD.stale_names(asof)
        frac = len(stale) / max(len(uni), 1)
        record("coverage", "fundamentals are CURRENT, not merely present",
               FAIL if frac > 0.50 else (WARN if frac > 0.15 else OK),
               f"{len(stale):,} of {len(uni):,} ({frac:.0%}) have no period "
               f"within {FD.STALE_PERIOD_DAYS} days of {asof}"
               + (f" e.g. {stale[:4]}" if stale else ""))
    except Exception as exc:                                     # noqa: BLE001
        record("coverage", "fundamentals are CURRENT, not merely present",
               WARN, repr(exc)[:110])

    unmapped = [t for t in miss if t.upper() not in cik_of]
    mapped = [t for t in miss if t.upper() in cik_of]
    no_facts = [t for t in mapped if cik_of[t.upper()] not in with_facts]
    unexplained = [t for t in mapped if cik_of[t.upper()] in with_facts]

    # The separator bug is fixed, so a dual-class ticker reappearing in
    # `unmapped` means the alias path regressed. Called out by name because
    # "9 more missing" would otherwise read as noise inside a 283-line gap.
    dual = [t for t in unmapped if "." in t]
    status = FAIL if dual else (WARN if unexplained else OK)
    detail = (f"{len(miss)} of {len(uni)} ({len(miss) / len(uni):.1%}) have no "
              f"fundamental score: {len(unmapped)} absent from SEC's ticker "
              f"file, {len(no_facts)} mapped but no facts (funds, non-USD "
              f"filers), {len(unexplained)} unexplained")
    if dual:
        detail = f"class-share aliasing REGRESSED for {dual}; " + detail
    elif unexplained:
        detail += f": {unexplained[:6]}"
    record("coverage", "fundamental coverage is explained", status, detail)


def check_currency(asof: str, quick: bool) -> None:
    """No FX-dependent metric may be published for a non-USD filer.

    The whole non-USD design rests on one invariant: a company reporting in EUR
    gets the scale-free metrics and nothing else. If `pe` ever appears for such
    a name it is a USD share price over a EUR earnings figure -- wrong by the
    exchange rate, and wrong in the worst way, because it looks like a normal
    P/E. Nobody reading the page could tell.

    This is a FAIL, not a warning. A published valuation ratio that is silently
    off by 1.35x is a wrong number presented as a right one.
    """
    import fund_metrics as FM
    import scores
    scores.load_all()

    df = scores.read(module="fundamental", start=asof, end=asof)
    if df is None or df.empty:
        record("currency", "no FX-dependent metric on a non-USD filer", WARN,
               "no fundamental rows at asof")
        return
    flag = df[df["metric"] == "reports_usd"]
    if flag.empty:
        record("currency", "no FX-dependent metric on a non-USD filer", WARN,
               "no reports_usd rows -- the currency pass has not run yet")
        return

    foreign = set(flag[flag["value"] == 0]["ticker"])
    if not foreign:
        record("currency", "no FX-dependent metric on a non-USD filer", OK,
               f"{len(flag)} filer(s) checked, all report in USD")
        return

    leaked = df[df["ticker"].isin(foreign) & df["metric"].isin(FM.NEEDS_FX)]
    by_metric = sorted(leaked["metric"].unique()) if len(leaked) else []
    # And the converse: withholding must not have silenced them entirely, or
    # "scored on scale-free metrics" would really mean "dropped".
    kept = df[df["ticker"].isin(foreign) & df["metric"].isin(FM.SCALE_FREE)]
    n_scored = kept["ticker"].nunique()

    record("currency", "no FX-dependent metric on a non-USD filer",
           FAIL if len(leaked) else (WARN if n_scored == 0 else OK),
           f"{len(leaked):,} leaked row(s) across {by_metric[:5]}" if len(leaked)
           else (f"{len(foreign)} non-USD filer(s) but none carries a "
                 f"scale-free metric -- withholding became exclusion"
                 if n_scored == 0 else
                 f"{len(foreign)} non-USD filer(s), 0 FX-dependent rows, "
                 f"{n_scored} still scored on scale-free metrics"))


GROUPS = {"key": check_key, "time": check_time, "value": check_value,
          "config": check_config, "claims": check_claims,
          "coverage": check_coverage, "currency": check_currency,
          "cross": check_cross, "combo": check_combo, "feeds": check_feeds,
          "pages": check_pages}


def main() -> int:
    ap = argparse.ArgumentParser(description="Data and score integrity audit.")
    ap.add_argument("--quick", action="store_true",
                    help="skip the checks that read whole stores")
    ap.add_argument("--only", default=None,
                    help=f"one group: {', '.join(GROUPS)}")
    a = ap.parse_args()

    import calendar_us
    # AUDIT THE NEWEST SCORED SESSION, not merely the newest closed one.
    #
    # The market closes hours before the 05:00 job scores it, so between those
    # two points `last_closed_session()` names a date nothing has been written
    # for. Every check that reads `start=asof, end=asof` then found an empty
    # frame and reported the WORST possible reading of it -- "3,479 of 3,479
    # have no fundamental score", "no fundamental rows at asof" -- which is
    # alarming, wrong, and exactly the kind of false alarm that teaches people
    # to ignore the audit.
    #
    # The gap itself is still worth knowing about, so it is stated rather than
    # hidden.
    market = calendar_us.last_closed_session()
    asof = market
    lag = ""
    try:
        import scores as _s
        _s.load_all()
        # The newest session EVERY module has, not the newest any module has.
        # `fundamental` runs weekly by design, so the union's maximum is a date
        # it has not scored -- and every fundamental check then read an empty
        # frame and reported "3,479 of 3,479 have no fundamental score".
        newest = [(_s.sessions_stored(m) or [None])[-1]
                  for m in config.SCORE_MODULES]
        if all(newest):
            common = min(newest)
            if common < market:
                asof = common
                lag = f" (market closed {market}; newest fully-scored session)"
    except Exception:                                            # noqa: BLE001
        pass
    groups = {a.only: GROUPS[a.only]} if a.only in GROUPS else GROUPS

    print(f"\n  integrity audit | asof {asof}{lag} | "
          f"{'quick' if a.quick else 'full'} | {len(groups)} group(s)\n")

    for g, fn in groups.items():
        try:
            fn(asof, a.quick)
        except Exception as exc:                                 # noqa: BLE001
            # A check that CRASHES is a failure, not a skip. Swallowing it here
            # would make the audit report clean because it never looked.
            import traceback
            record(g, f"{g} group crashed", FAIL,
                   f"{type(exc).__name__}: {exc}"[:160])
            print(traceback.format_exc()[-600:])

    width = max(len(n) for _, n, _, _ in _results) + 2
    last = None
    for grp, name, st, detail in _results:
        if grp != last:
            print(f"\n  [{grp}]")
            last = grp
        mark = {OK: "  ok  ", WARN: " warn ", FAIL: " FAIL "}[st]
        print(f"   {mark} {name:<{width}} {detail}")

    fails = [r for r in _results if r[2] == FAIL]
    warns = [r for r in _results if r[2] == WARN]
    print(f"\n  {len(_results)} check(s): {len(_results) - len(fails) - len(warns)} ok, "
          f"{len(warns)} warning(s), {len(fails)} FAILED\n")
    if fails:
        for grp, name, _, detail in fails:
            print(f"   FAILED [{grp}] {name}: {detail}")
        print()
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
