"""
The sentiment screener. Separate from the bounce screener, on its own interval.

    python senti_screen.py                  one pass: fetch, score, report
    python senti_screen.py --interval       run continuously at SENTI_INTERVAL_MIN
    python senti_screen.py --only RDW --explain     full per-signal trace
    python senti_screen.py --catchup 30     backfill the score series
    python senti_screen.py --dry-run
    python senti_screen.py --pause / --resume

Deliberately NOT coupled to the bounce screener. It writes its own report and
its own score rows; report.py reads those rows to put a badge on each bounce
card, but nothing here changes a gate or a score weight over there. Until
`backtest.py --sentiment` says the signal is worth something, wiring it into a
composite would just be adding an unmeasured term to a number that already has
one weight the data does not support (W_SUPPORT).

WATERMARK, NOT WALL CLOCK
-------------------------
Every pass advances a watermark and catches up whatever it missed, the same way
daily_run.py handles a closed laptop. An interval runner that assumes it fired
on schedule leaves silent holes in the series, and a hole in a sentiment series
is indistinguishable from a quiet news day -- which is the same confusion the
news-coverage guard exists to prevent.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd

import calendar_us
import config
import ui
import macro
import news
import scores
import sentiment as senti


def log(msg: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} | {msg}"
    try:
        print(line, flush=True)
    except (ValueError, OSError):
        pass
    try:
        with config.LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write("senti " + line + "\n")
    except OSError:
        pass


def paused() -> bool:
    return config.SENTI_DISABLED_SENTINEL.exists()


# ===========================================================================
# Universe
# ===========================================================================
def screen_universe(asof: str) -> list[str]:
    """Delegates to bars.tradeable_universe -- see the note there on
    why this filter must have exactly one definition."""
    import bars
    return bars.tradeable_universe(asof)


def run_once(asof: str | None = None, do_fetch: bool = True,
             allow_partial: bool = False, verbose: bool = True) -> dict:
    t0 = time.time()
    asof = asof or calendar_us.last_closed_session()

    if do_fetch:
        try:
            res = news.update(verbose=False)
            if verbose:
                log(f"  news +{res['fetched']} article(s)")
        except Exception as exc:                                   # noqa: BLE001
            log(f"  ! news fetch failed ({repr(exc)[:110]}); scoring what is stored")

    try:
        senti.build_cache(verbose=False)
    except Exception as exc:                                       # noqa: BLE001
        log(f"  ! score cache failed ({repr(exc)[:90]})")

    scores.load_all()
    mod = scores.get("sentiment")
    uni = screen_universe(asof)
    if not uni:
        log("  ! no panel stats; run `python bars.py --update` first")
        return {"ok": False}

    rows = mod.compute(asof, uni, allow_partial=allow_partial)
    if rows.empty:
        log("  no sentiment rows produced")
        return {"ok": True, "n": 0}

    scores.write(rows, session=asof, module="sentiment")

    payload = build_payload(asof, rows)
    write_html(payload, asof)

    if verbose:
        log(f"  {payload['n_with_news']} name(s) with news of {len(uni):,} tradeable "
            f"| regime {payload['regime']} | {time.time() - t0:.0f}s")
    return {"ok": True, "n": payload["n_with_news"]}


def build_payload(asof: str, rows: pd.DataFrame) -> dict:
    w = _wide(rows)
    smap = macro.load_sector_map()
    if not smap.empty:
        w = w.merge(smap[["ticker", "sector", "sector_etf"]], on="ticker", how="left")

    w = w[w.get("has_news", 0) > 0].copy() if "has_news" in w.columns else w
    for c in ("sent_mean_30d", "severity_max", "news_z", "news_count_30d",
              "sent_delta", "sent_mean_5d", "sent_age", "sent_stale",
              "sent_decay_30d"):
        if c not in w.columns:
            w[c] = np.nan

    # Ranked by SEVERITY, not by polarity. "What happened" is the question a
    # research queue answers; "is it good news" is a judgement the reader makes
    # with the headline in front of them.
    w = w.sort_values("severity_max", ascending=False, na_position="last")

    reg, breadth = macro.regime(asof)
    mrow = macro.at(asof)
    sect = _sector_rollup(w)

    cards = []
    for _, r in w.head(config.MAX_FLAGS_REPORTED if hasattr(
            config, "MAX_FLAGS_REPORTED") else 120).iterrows():
        cards.append({
            "ticker": r["ticker"],
            # NOT `r.get("sector") or "?"`: a float NaN is TRUTHY, so that idiom
            # passes NaN straight through and renders the literal string "nan"
            # under every ticker whose sector has not been mapped yet.
            "sector": "?" if pd.isna(r.get("sector")) else str(r.get("sector")),
            "sent": _f(r.get("sent_mean_30d")),
            "sent5": _f(r.get("sent_mean_5d")),
            "delta": _f(r.get("sent_delta")),
            "sev": _f(r.get("severity_max")),
            "band": str(r.get("top_severity_band_label") or ""),
            "event": str(r.get("top_event_label") or ""),
            "events": str(r.get("event_types_label") or ""),
            "n": _f(r.get("news_count_30d")),
            "z": _f(r.get("news_z")),
            "age": _f(r.get("sent_age")),
            "stale": _f(r.get("sent_stale")),
            "headline": str(r.get("top_headline_label") or ""),
        })

    return {
        "asof": asof,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "regime": reg,
        "breadth": None if not np.isfinite(breadth) else round(float(breadth), 3),
        "gpr": _f(mrow.get("gpr")),
        "epu": _f(mrow.get("epu")),
        "macro_shock": _f(mrow.get("macro_shock")),
        "releases": str(mrow.get("releases") or ""),
        "coverage": _f(rows.loc[rows["metric"] == "news_coverage", "value"].max()),
        "n_universe": int(w.shape[0]),
        "n_with_news": int(w.shape[0]),
        "sectors": sect,
        "cards": cards,
    }


def _wide(rows: pd.DataFrame) -> pd.DataFrame:
    num = rows[rows["value"].notna()].pivot_table(
        index="ticker", columns="metric", values="value", aggfunc="last")
    lab = rows[rows["label"].notna()]
    out = num
    if not lab.empty:
        lw = lab.pivot_table(index="ticker", columns="metric", values="label",
                             aggfunc="last")
        lw.columns = [f"{c}_label" for c in lw.columns]
        out = num.join(lw, how="outer")
    return out.reset_index().rename_axis(None, axis=1)


def _sector_rollup(w: pd.DataFrame) -> list[dict]:
    """Sector-level sentiment -- the backstop for the names with no news at all.

    Measured: the median flag has 3 articles in 30 days and 13% have none. For
    most of the list this rollup is the only sentiment reading that exists, which
    is why it is computed even when the per-ticker view looks complete.
    """
    if "sector" not in w.columns or w.empty:
        return []
    g = w.groupby("sector")
    out = []
    for s, sub in g:
        if len(sub) < 3:
            continue
        out.append({"sector": str(s), "n": int(len(sub)),
                    "sent": _f(sub["sent_mean_30d"].mean()),
                    "sev": _f(sub["severity_max"].mean())})
    return sorted(out, key=lambda d: (d["sent"] is None, -(d["sent"] or 0)))


def _f(v, nd: int = 3):
    try:
        f = float(v)
        return None if not np.isfinite(f) else round(f, nd)
    except (TypeError, ValueError):
        return None


# ===========================================================================
# Report
# ===========================================================================
def write_html(payload: dict, asof: str) -> str:
    _UI_NAV = ui.nav("sentiment", 1)
    def esc(s):
        return (str(s).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))

    def bar(v, lo=-1.0, hi=1.0):
        if v is None:
            return '<span class="na">no data</span>'
        pct = max(0.0, min(1.0, (v - lo) / (hi - lo)))
        cls = "pos" if v > 0.05 else ("neg" if v < -0.05 else "neu")
        return (f'<span class="bar"><i class="{cls}" style="left:50%;'
                f'width:{abs(pct - 0.5) * 100:.1f}%;'
                f'{"margin-left:0" if v >= 0 else f"margin-left:-{abs(pct - 0.5) * 100:.1f}%"}">'
                f'</i></span><span class="v {cls}">{v:+.2f}</span>')

    rows = []
    for c in payload["cards"]:
        band = c["band"] or "NOISE"
        rows.append(f"""<tr data-band="{esc(band)}" data-sector="{esc(c['sector'])}">
  <td class="tk">{esc(c['ticker'])}<span class="sec">{esc(c['sector'])}</span></td>
  <td class="sev"><span class="pill b-{esc(band)}">{esc(band)}</span>
      <span class="num">{c['sev'] if c['sev'] is not None else '-'}</span></td>
  <td class="ev">{esc(c['event'])}</td>
  <td>{bar(c['sent'])}</td>
  <td class="num">{c['n'] if c['n'] is not None else '-'}</td>
  <td class="num{' stale' if c['stale'] else ''}"
      title="{'the newest article is ' + str(int(c['age'])) + ' sessions old -- this score is not news' if c['stale'] else 'sessions since the newest article'}"
      >{f"{int(c['age'])}" if c['age'] is not None else '-'}</td>
  <td class="num">{f"{c['z']:+.1f}" if c['z'] is not None else '-'}</td>
  <td class="hl">{esc(c['headline'][:150])}</td>
</tr>""")

    secs = "".join(
        f'<span class="chip">{esc(s["sector"])}'
        f'<b class="{"pos" if (s["sent"] or 0) > 0.05 else ("neg" if (s["sent"] or 0) < -0.05 else "neu")}">'
        f'{s["sent"]:+.2f}</b><i>n={s["n"]}</i></span>'
        for s in payload["sectors"])

    cov = payload.get("coverage")
    cov_warn = ""
    if cov is not None and cov < config.SENTI_MIN_COVERAGE:
        cov_warn = (f'<div class="warn">News store covers only {cov:.0%} of the '
                    f'scored window. Names may read as silent because they were '
                    f'never fetched, not because they had no news. '
                    f'Run <code>python news.py --backfill</code>.</div>')

    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sentiment {esc(asof)}</title><style>{ui.CSS}/* Legacy variable names aliased onto the shared palette (ui.py). These two pages predate ui and use --fg/--mut/--acc/--card; aliasing keeps every existing rule working while the colours come from one place. */:root{{--fg:var(--ink);--mut:var(--muted);--acc:var(--accent);--card:var(--panel);--hi:var(--pos);--lo:var(--neg);--mid:var(--muted);--neu:var(--muted)}}
:root{{--bg:#fff;--fg:#111;--mut:#666;--line:#e3e3e6;--card:#fafafa;
--pos:#0a7d3f;--neg:#b3261e;--neu:#777;--acc:#1a56db}}
@media(prefers-color-scheme:dark){{:root{{--bg:#111315;--fg:#e8e8ea;--mut:#9a9aa2;
--line:#2a2d31;--card:#191c1f;--pos:#3ddc84;--neg:#ff6b6b;--neu:#8a8a92;--acc:#7aa2f7}}}}
*{{box-sizing:border-box}}body{{margin:0;padding:18px;background:var(--bg);color:var(--fg);
font:14px/1.45 -apple-system,Segoe UI,Roboto,sans-serif}}
h1{{font-size:19px;margin:0 0 2px}}
.meta{{color:var(--mut);font-size:12.5px;margin-bottom:12px}}
.meta a{{color:var(--fg)}}
.hdr{{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0 14px}}
.stat{{background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:7px 11px;font-size:12.5px}}.stat b{{display:block;font-size:16px;margin-top:2px}}
.warn{{background:#fff4e5;border:1px solid #f0b429;color:#7a4b00;padding:9px 12px;
border-radius:8px;margin:10px 0;font-size:13px}}
@media(prefers-color-scheme:dark){{.warn{{background:#3a2c10;border-color:#8a6a20;color:#f5d597}}}}
.chip{{display:inline-flex;gap:6px;align-items:baseline;background:var(--card);
border:1px solid var(--line);border-radius:999px;padding:4px 10px;font-size:12px;margin:0 5px 5px 0}}
.chip i{{color:var(--mut);font-style:normal;font-size:11px}}
.wrap{{overflow-x:auto;border:1px solid var(--line);border-radius:10px}}
table{{border-collapse:collapse;width:100%;min-width:900px}}
th,td{{padding:7px 9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{font-size:11.5px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut);
position:sticky;top:0;background:var(--bg)}}
tr:last-child td{{border-bottom:0}}
.tk{{font-weight:640;white-space:nowrap}}.tk .sec{{display:block;font-weight:400;
font-size:11px;color:var(--mut)}}
.num{{font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap}}
.ev{{font-size:12px;color:var(--mut);white-space:nowrap}}
/* A stale score is still shown -- greyed and flagged, never hidden. The
   number is real; it is just not news any more. */
.stale{{color:var(--warn);font-weight:600}}
.hl{{font-size:12.5px;color:var(--mut);max-width:430px}}
.pill{{display:inline-block;padding:1px 7px;border-radius:999px;font-size:10.5px;
font-weight:650;letter-spacing:.03em}}
.b-CRITICAL{{background:#b3261e;color:#fff}}.b-HIGH{{background:#e8710a;color:#fff}}
.b-MEDIUM{{background:#f0b429;color:#3a2c00}}.b-LOW{{background:var(--line);color:var(--mut)}}
.b-NOISE{{background:transparent;color:var(--mut);border:1px solid var(--line)}}
.bar{{position:relative;display:inline-block;width:88px;height:7px;background:var(--line);
border-radius:4px;vertical-align:middle;overflow:hidden}}
.bar i{{position:absolute;top:0;height:7px}}
.bar i.pos{{background:var(--pos)}}.bar i.neg{{background:var(--neg)}}.bar i.neu{{background:var(--neu)}}
.v{{margin-left:7px;font-variant-numeric:tabular-nums;font-size:12px}}
.pos{{color:var(--pos)}}.neg{{color:var(--neg)}}.neu{{color:var(--mut)}}
.na{{color:var(--mut);font-size:11.5px;font-style:italic}}
.note{{margin-top:14px;color:var(--mut);font-size:12px;max-width:760px}}
</style></head><body>{_UI_NAV}
<h1>Sentiment screen &middot; {esc(asof)}</h1>
<div class="meta">generated {esc(payload['generated'])} &middot;
{payload['n_with_news']} name(s) with company news &middot; ranked by severity
&middot; <a href="../index.html">status hub</a></div>
{cov_warn}
<div class="hdr">
  <div class="stat">market regime<b>{esc(payload['regime'])}</b></div>
  <div class="stat">breadth &gt;50DMA<b>{f"{payload['breadth']:.0%}" if payload['breadth'] is not None else '-'}</b></div>
  <div class="stat">geopolitical risk<b>{payload['gpr'] if payload['gpr'] is not None else '-'}</b></div>
  <div class="stat">policy uncertainty<b>{payload['epu'] if payload['epu'] is not None else '-'}</b></div>
  <div class="stat">macro shock (ATR)<b>{payload['macro_shock'] if payload['macro_shock'] is not None else '-'}</b></div>
  {f'<div class="stat">scheduled today<b>{esc(payload["releases"])}</b></div>' if payload['releases'] else ''}
</div>
<div>{secs}</div>
<div class="wrap"><table>
<thead><tr><th>ticker</th>{ui.th("severity","severity_max")}
{ui.th("top event","top_event")}{ui.th("sentiment 30d","sent_mean_30d")}
{ui.th("n","news_count_30d")}{ui.th("age","sent_age")}
{ui.th("burst z","news_z")}
<th>most severe headline</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
<p class="note"><b>ONE SOURCE: every article here is Benzinga.</b> A story that
ran on Reuters, GlobeNewswire or a company wire and not on Benzinga does not
exist as far as these scores are concerned, so a low article count means thin
COVERAGE, not a quiet company. The <b>age</b> column is how old the newest
article is, in sessions &mdash; a 30-session mean built from three articles that
are all six weeks old is a fossil, and only that column will tell you.</p>
<p class="note"><b>Read this as a research queue, not a buy list.</b> Severity is
the measured median |return| / ATR for that event class over this store, not a
judgement of importance; direction lives in the sentiment column, deliberately
separate. Names absent from this table had fewer than
{config.SENTI_MIN_ARTICLES} company articles in the window &mdash; that is a
distinct state from neutral sentiment, and it is the normal case for micro-caps.</p>
</body></html>"""

    config.REPORTS.mkdir(parents=True, exist_ok=True)
    p = config.REPORTS_SENTIMENT / f"{asof}.html"
    p.write_text(html, encoding="utf-8")
    (config.REPORTS_SENTIMENT / "latest.html").write_text(html, encoding="utf-8")
    return str(p)


# ===========================================================================
# Interval runner
# ===========================================================================
def in_hours(now: pd.Timestamp | None = None) -> bool:
    now = now or pd.Timestamp.now(tz="America/New_York")
    lo, hi = (("04:00", "20:00") if config.SENTI_EXTENDED else config.SENTI_HOURS)
    return lo <= f"{now:%H:%M}" <= hi and now.weekday() < 5


def run_interval(minutes: int | None = None, max_passes: int = 0) -> int:
    every = (minutes or config.SENTI_INTERVAL_MIN) * 60
    log(f"interval runner: every {every // 60} min, hours="
        f"{'04:00-20:00' if config.SENTI_EXTENDED else '-'.join(config.SENTI_HOURS)} ET")
    n = 0
    while True:
        if paused():
            log("PAUSED (sentiment.disabled present)")
        elif not in_hours():
            log("outside market hours; skipping")
        else:
            try:
                run_once()
            except Exception as exc:                               # noqa: BLE001
                log(f"! pass failed: {repr(exc)[:150]}")
        n += 1
        if max_passes and n >= max_passes:
            return 0
        time.sleep(every)


def _eligible_by_date(dates: list[str]) -> dict[str, list[str]]:
    """Causal per-date eligibility, reused from the bounce backtest.

    The right universe for a historical catchup. Passing None scores every
    ticker that has news (~4,000 names, ~90s a date); passing TODAY's panel
    stats would be fast but conditions the sample on having survived and stayed
    liquid until now -- the exact look-ahead that made the first bounce replay's
    absolute numbers uninterpretable. eligibility() is computed from rolling
    windows per date, so it is both correct and ~10x smaller.
    """
    import backtest

    elig = backtest.eligibility()
    elig = elig[elig["eligible"]]
    want = set(dates)
    return {d: sorted(g["ticker"].astype(str))
            for d, g in elig.groupby("date", observed=True) if d in want}


def catchup(days: int = 0, frm: str | None = None, to: str | None = None,
            every: int = 1, eligible: bool = False, verbose: bool = True) -> int:
    """Fill the score series, so the backtest has something to test.

    The compute universe is deliberately left as None -- i.e. every ticker that
    HAS news in the window -- rather than today's tradeable list. Prefiltering a
    HISTORICAL date with TODAY's panel stats conditions the sample on having
    survived and stayed liquid until now, which is the exact look-ahead that made
    the first bounce replay's absolute numbers uninterpretable. senti_backtest
    intersects with backtest.eligibility() per date anyway, so a superset here
    costs a little time and removes a bias.
    """
    asof = to or calendar_us.last_closed_session()
    sess = [s for s in calendar_us.all_sessions() if s <= asof]
    if frm:
        sess = [s for s in sess if s >= frm]
    elif days:
        sess = sess[-days:]
    sess = sess[::every]

    have = set(scores.sessions_stored("sentiment"))
    todo = [s for s in sess if s not in have]
    if not todo:
        log("  score series already complete for that window")
        return 0

    scores.load_all()
    mod = scores.get("sentiment")
    by_date = _eligible_by_date(todo) if eligible else {}
    if eligible and verbose:
        n = [len(v) for v in by_date.values()]
        log(f"  causal eligibility: {len(by_date)} date(s), "
            f"{int(np.mean(n)) if n else 0} names/date")
    ok = 0
    t0 = time.time()
    for i, s in enumerate(todo, 1):
        try:
            uni = by_date.get(s) if eligible else None
            if eligible and not uni:
                continue          # no eligible names that date; nothing to score
            rows = mod.compute(s, uni, allow_partial=True)
            if not rows.empty:
                scores.write(rows, session=s, module="sentiment")
                ok += 1
        except Exception as exc:                                   # noqa: BLE001
            log(f"  ! {s}: {repr(exc)[:90]}")
        if verbose and (i % 10 == 0 or i == len(todo)):
            el = time.time() - t0
            log(f"  catchup {i}/{len(todo)} ({ok} written, {el / 60:.1f}m, "
                f"eta {el / i * (len(todo) - i) / 60:.0f}m)")
    return ok


def explain(ticker: str, asof: str | None = None) -> None:
    asof = asof or calendar_us.last_closed_session()
    scores.load_all()
    mod = scores.get("sentiment")
    rows = mod.compute(asof, [ticker], allow_partial=True)
    print(f"\n  {ticker} @ {asof}")
    if rows.empty:
        print("    (no rows -- ticker not in the news store for this window)")
        return
    for _, r in rows.sort_values("metric").iterrows():
        v = r["label"] if pd.isna(r["value"]) else f"{r['value']:.4f}"
        print(f"    {r['metric']:<20} {v}")

    print("\n  contributing articles:")
    sessions = [s for s in calendar_us.all_sessions() if s <= asof][-config.SENTI_WINDOWS[1]:]
    art = news.read(start=sessions[0], end=asof, tickers=[ticker])
    if art.empty:
        print("    (none)")
        return
    sc = senti.score_frame(art)
    for (_, a), (_, s) in zip(art.iterrows(), sc.iterrows()):
        print(f"    {a['session']}  {s['event_type']:<18} lm={s['lm_score']:+.2f} "
              f"sev={s['severity']:5.1f} {s['severity_band']:<8} {a['headline'][:66]}")


def main() -> int:
    config.safe_console()
    ap = argparse.ArgumentParser(description="Sentiment screener.")
    ap.add_argument("--interval", action="store_true")
    ap.add_argument("--minutes", type=int, default=None)
    ap.add_argument("--passes", type=int, default=0)
    ap.add_argument("--asof", default=None)
    ap.add_argument("--catchup", type=int, default=0, metavar="N")
    ap.add_argument("--from", dest="frm", default=None, metavar="DATE")
    ap.add_argument("--to", default=None, metavar="DATE")
    ap.add_argument("--every", type=int, default=1)
    ap.add_argument("--eligible", action="store_true",
                    help="score only the causally-eligible universe per date "
                         "(what the backtest needs; ~10x faster)")
    ap.add_argument("--only", metavar="SYM", default=None)
    ap.add_argument("--explain", action="store_true")
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--allow-partial", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--pause", action="store_true")
    ap.add_argument("--resume", action="store_true")
    a = ap.parse_args()

    config.dirs()

    if a.pause:
        config.SENTI_DISABLED_SENTINEL.write_text(
            f"paused {datetime.now():%Y-%m-%d %H:%M}\n", encoding="utf-8")
        print("PAUSED. `--resume` to re-enable.")
        return 0
    if a.resume:
        if config.SENTI_DISABLED_SENTINEL.exists():
            config.SENTI_DISABLED_SENTINEL.unlink()
        print("RESUMED.")
        return 0

    if a.dry_run:
        asof = a.asof or calendar_us.last_closed_session()
        uni = screen_universe(asof)
        print(f"  asof            {asof}")
        print(f"  tradeable names {len(uni):,}")
        print(f"  news months     {len(news.months())}, {len(news.stored_sessions())} sessions")
        print(f"  score sessions  {len(scores.sessions_stored('sentiment'))}")
        print(f"  interval        {config.SENTI_INTERVAL_MIN} min, "
              f"in-hours now: {in_hours()}")
        print(f"  paused          {paused()}")
        return 0

    if a.only:
        explain(a.only.upper(), a.asof)
    elif a.catchup or a.frm:
        catchup(a.catchup, frm=a.frm, to=a.to, every=a.every,
                eligible=a.eligible)
    elif a.interval:
        return run_interval(a.minutes, a.passes)
    else:
        run_once(a.asof, do_fetch=not a.no_fetch, allow_partial=a.allow_partial)
    return 0


if __name__ == "__main__":
    sys.exit(main())
