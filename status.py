"""
Health report. The one command to run when a report looks wrong.

    python status.py
    python status.py --outcomes     add the forward-performance tables
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

import numpy as np
import pandas as pd

import config                                                   # noqa: E402

config.safe_console()      # before anything prints; guarded for the no-console case

import bars                                                     # noqa: E402
import calendar_us                                              # noqa: E402
import daily_run                                                # noqa: E402
import state                                                    # noqa: E402
import store                                                    # noqa: E402
import universe                                                 # noqa: E402


def bar(frac: float, width: int = 22) -> str:
    n = int(max(0.0, min(1.0, frac)) * width)
    return "[" + "#" * n + "." * (width - n) + "]"


def audit_pages(verbose: bool = True) -> list[tuple[str, str]]:
    """Audit the RENDERED pages, not the code that writes them.

    Every display bug this project has shipped was invisible to a code review
    and obvious in the output: a full green bar for a bad percentile, a filter
    row collapsed to a sliver, a literal `nan` in the sector dropdown, a `.`
    where a middot should be. A guard that greps the generator cannot see any
    of those, so this one reads what the browser would read.

    Checks, in order of how often they have actually bitten:
      1. a literal `nan` / `None` / `undefined` rendered as a value
      2. a mojibake replacement character (an encoding slip)
      3. a relative href pointing at a file that is not there
    """
    import re
    from urllib.parse import unquote

    problems: list[tuple[str, str]] = []
    pages = sorted(config.REPORTS.rglob("*.html"))

    for f in pages:
        raw = f.read_bytes()
        try:
            t = raw.decode("utf-8")
        except UnicodeDecodeError:
            problems.append((f.name, "not valid UTF-8"))
            continue

        # <script> holds legitimate `null`/`NaN` in JSON payloads and JS source;
        # only what a reader SEES is a bug, so strip scripts before checking.
        vis = re.sub(r"<script\b.*?</script>", "", t, flags=re.S | re.I)
        for token in ("nan", "None", "undefined", "NaT"):
            n = len(re.findall(rf">\s*{token}\s*<", vis))
            if n:
                problems.append((f.name, f"{n} cell(s) rendering '{token}'"))
        if "�" in vis:
            problems.append((f.name, f"{vis.count(chr(0xFFFD))} mojibake char(s)"))

        for href in set(re.findall(r'href="([^"#?]+)"', vis)):
            if href.startswith(("http://", "https://", "mailto:", "data:", "/")):
                continue
            if not (f.parent / unquote(href)).exists():
                problems.append((f.name, f"dead link -> {href}"))

    if verbose:
        print(f"\nPAGE AUDIT ({len(pages)} page(s))")
        if not problems:
            print("  clean -- no nan cells, no mojibake, no dead relative links")
        else:
            for name, msg in problems[:25]:
                print(f"  ! {name:34s} {msg}")
            if len(problems) > 25:
                print(f"  ... and {len(problems) - 25} more")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="Screener health report.")
    ap.add_argument("--outcomes", action="store_true")
    ap.add_argument("--pages", action="store_true",
                    help="audit rendered HTML only, then exit")
    a = ap.parse_args()

    if a.pages:
        return 1 if audit_pages() else 0

    st = daily_run.load_state()

    print("=" * 66)
    auto = "PAUSED" if daily_run.paused() else "enabled"
    extra = ""
    if daily_run.paused():
        try:
            extra = "  (" + config.DISABLED_SENTINEL.read_text(
                encoding="utf-8").splitlines()[0] + ")"
        except OSError:
            pass
    print(f"AUTO: {auto}{extra}")
    print("=" * 66)

    # ---------------------------------------------------------------- last run
    print("\nLAST RUN")
    for k, label in (("last_run", "attempted"), ("last_success", "succeeded"),
                     ("last_screened", "session screened")):
        print(f"  {label:20} {st.get(k, '(never)')}")
    if st.get("last_duration_sec") is not None:
        print(f"  {'duration':20} {st['last_duration_sec']}s")
    errs = st.get("last_errors") or []
    print(f"  {'errors':20} {', '.join(errs) if errs else 'none'}")
    if st.get("timings"):
        print("  step timings:")
        for k, v in st["timings"].items():
            print(f"    {k:18} {v:>6}s")

    # ---------------------------------------------------------------- freshness
    print("\nDATA")
    try:
        lcs = calendar_us.last_closed_session()
        print(f"  last closed session  {lcs}")
        ps = bars.load_panel_stats()
        if not ps.empty:
            newest = str(ps["last_date"].max())
            lag = len(calendar_us.sessions_between(newest, lcs)) - 1
            flag = "OK" if lag <= 0 else f"STALE by {lag} session(s)"
            print(f"  newest stored bar    {newest}   {flag}")
            cur = int((ps["last_date"] >= lcs).sum())
            print(f"  tickers current      {cur:,} / {len(ps):,}  "
                  f"{bar(cur / max(len(ps), 1))}")
    except Exception as exc:                                     # noqa: BLE001
        print(f"  ! calendar/panel unavailable ({repr(exc)[:80]})")

    cov = store.coverage("1d")
    if not cov.empty:
        print(f"  1d store             {len(cov)} month file(s) "
              f"{cov['month'].iloc[0]} -> {cov['month'].iloc[-1]}, "
              f"{cov['rows'].sum():,} rows, {cov['bytes'].sum() / 1e6:.0f} MB")
    cov_h = store.coverage("1h")
    if not cov_h.empty:
        print(f"  1h store             {len(cov_h)} month file(s), "
              f"{cov_h['rows'].sum():,} rows, {cov_h['bytes'].sum() / 1e6:.1f} MB")
    print(f"  total on disk        {store.store_bytes() / 1e6:.0f} MB")

    # ---------------------------------------------------------------- universe
    print("\nUNIVERSE")
    u = universe.summary()
    print(f"  {u}")
    ps = bars.load_panel_stats()
    if not ps.empty:
        gates = [
            (f"price >= ${config.MIN_PRICE}", ps["last_close"] >= config.MIN_PRICE),
            (f"$vol >= {config.MIN_DOLLAR_VOL / 1e6:.0f}M",
             ps["dollar_vol_20"] >= config.MIN_DOLLAR_VOL),
            (f"bars >= {config.MIN_BARS}", ps["n_bars"] >= config.MIN_BARS),
            (f"<= {config.MAX_PCT_OF_250D_HIGH:.0%} of 250d high",
             ps["pct_of_250d_high"] <= config.MAX_PCT_OF_250D_HIGH),
            (f"250d range >= {config.MIN_250D_RANGE_X}x",
             ps["range_250_x"] >= config.MIN_250D_RANGE_X),
        ]
        keep = pd.Series(True, index=ps.index)
        print("  panel prefilter:")
        for label, t in gates:
            keep &= t.fillna(False)
            print(f"    {label:28} pass {int(t.fillna(False).sum()):>6,}   "
                  f"cumulative {int(keep.sum()):>5,}")

    # ---------------------------------------------------------------- flags
    print("\nFLAGS")
    files = sorted(config.FLAGS.glob("*.parquet"))
    print(f"  sessions recorded    {len(files)}"
          + (f"   {files[0].stem} -> {files[-1].stem}" if files else ""))
    if files:
        recent = files[-8:]
        counts = []
        for f in recent:
            try:
                counts.append((f.stem, len(pd.read_parquet(f, columns=["ticker"]))))
            except Exception:                                    # noqa: BLE001
                counts.append((f.stem, -1))
        print("  recent sessions:")
        for d, n in counts:
            print(f"    {d}  {n:>4}  {bar(min(n, 60) / 60, 18)}")
        # A threshold regression shows up as a flag count far off the trailing
        # median, which is much easier to notice than a subtly wrong list.
        vals = [n for _d, n in counts if n >= 0]
        if len(vals) >= 4:
            med = float(np.median(vals))
            last = vals[-1]
            if med > 0 and (last < 0.25 * med or last > 4 * med):
                print(f"  ! WARNING: latest count {last} is far from the "
                      f"trailing median {med:.0f} -- check thresholds")
    if st.get("score_p50") is not None:
        print(f"  score p50 / p90      {st['score_p50']} / {st.get('score_p90')}")

    reg = state.load_flag_state()
    if not reg.empty:
        print(f"  tracked setups       {len(reg)}   "
              + "  ".join(f"{k}={v}" for k, v in reg["status"].value_counts().items()))
        act = reg[reg["status"] == "active"]
        if not act.empty:
            print(f"  longest on list      " + ", ".join(
                f"{r['ticker']}({int(r['run_count'])}d)"
                for _, r in act.nlargest(5, "run_count").iterrows()))

    # ---------------------------------------------------------------- outcomes
    o = state.load_outcomes()
    print(f"\nOUTCOMES  ({len(o)} tracked)")
    if o.empty:
        print("  none yet -- populates as flagged setups age")
    else:
        print(f"  median return        {o['ret_pct'].median():+.2%}")
        print(f"  median MFE / MAE     {o['mfe_pct'].median():+.2%} / "
              f"{o['mae_pct'].median():+.2%}")
        print(f"  win rate             {(o['ret_pct'] > 0).mean():.0%}")
        if a.outcomes:
            for by in ("first_bucket", "first_support_grade", "first_stage",
                       "first_age_band", "first_price_tier"):
                t = state.outcome_table(by)
                if not t.empty:
                    print(f"\n  by {by}:")
                    print("    " + t.round(4).to_string().replace("\n", "\n    "))
        print("\n  NOTE: first-close-to-close paths, no costs, slippage or borrow,"
              "\n  on a self-selected sample. Directional feedback for tuning"
              "\n  thresholds -- not a backtest.")

    # ------------------------------------------------------- historical evidence
    # Printed above the live outcomes on purpose: this is the statistically
    # meaningful number, while forward tracking needs months to say anything.
    try:
        import backtest
        br = backtest.load_base_rates()
    except Exception:                                            # noqa: BLE001
        br = pd.DataFrame()

    print("\nHISTORICAL EVIDENCE  (backtest, entry at next open)")
    if br.empty:
        print("  none yet -- run:")
        print("    python backtest.py --start 2024-02 --every 3 --exits --base-rates")
    else:
        exits = br["exit"].dropna().unique()
        print(f"  exit rule: {', '.join(map(str, exits))}")
        for keys in ("bucket", "stage", "price_tier", "age_band", "support_grade"):
            sub = br[br["keys"] == keys]
            if sub.empty:
                continue
            print(f"  by {keys}:")
            for _, r in sub.sort_values("mean_ret", ascending=False).iterrows():
                sign = "+" if r["mean_ret"] >= 0 else ""
                print(f"    {str(r['value']):16} n={int(r['n']):>5}  "
                      f"mean {sign}{r['mean_ret']:.2%}  "
                      f"median {r['median_ret']:+.2%}  "
                      f"win {r['win_rate']:.0%}")
        best = br.loc[br["mean_ret"].idxmax()]
        worst = br.loc[br["mean_ret"].idxmin()]
        print(f"  best slice : {best['value']} ({int(best['n'])}) "
              f"mean {best['mean_ret']:+.2%}")
        print(f"  worst slice: {worst['value']} ({int(worst['n'])}) "
              f"mean {worst['mean_ret']:+.2%}")
        print("  Returns are right-skewed -- most trades lose a little and a few win")
        print("  big -- so MEAN is the decision variable and median understates a")
        print("  viable rule. Survivorship makes all of these optimistic.")

    # ------------------------------------------------------- score modules
    try:
        import news
        import scores as _scores

        print("\nSCORE MODULES")
        _scores.load_all()
        asof = calendar_us.last_closed_session()
        ms = news.months()
        sess = news.stored_sessions()
        print(f"  news store           {len(ms)} month(s), {len(sess):,} session(s), "
              f"{news.store_bytes() / 1e6:.0f} MB"
              + (f"   {ms[0]} .. {ms[-1]}" if ms else ""))

        # Coverage of the window the screener actually scores. This is the
        # number that decides whether a has_news=0 means "quiet" or "not
        # fetched" -- they are indistinguishable downstream, so it is printed
        # rather than left to be inferred.
        win = [s for s in calendar_us.all_sessions()
               if s <= asof][-config.SENTI_WINDOWS[1]:]
        cov = sum(1 for s in win if s in sess) / max(len(win), 1)
        flag = "" if cov >= config.SENTI_MIN_COVERAGE else \
            "   <- below SENTI_MIN_COVERAGE; run `python news.py --backfill`"
        print(f"  scored-window cover  {bar(cov)} {cov:.0%} of last "
              f"{len(win)} session(s){flag}")

        for name in _scores.registered():
            got = _scores.sessions_stored(name)
            print(f"  module '{name}'{'':<10} {len(got)} scored session(s)"
                  + (f"   {got[0]} .. {got[-1]}" if got else "   (none yet)"))

        import events as _events
        ev = _events.load()
        if not ev.empty:
            top = ev.nlargest(4, "abs_ret_atr_med")
            print(f"  severity calibrated  {len(ev)} class(es), "
                  f"{int(ev['n'].sum()):,} (article, session) pairs")
            print("    biggest movers     " + ", ".join(
                f"{r['event_type']} {r['lift']:.2f}x" for _, r in top.iterrows()))
            print("    NOTE the 'dramatic' classes (BANKRUPTCY, MA, FDA, "
                  "SHORT_REPORT) measure BELOW")
            print("    average: by the time the wire writes them, the move "
                  "already happened.")
        else:
            print("  severity             hand priors (run `python events.py --calibrate`)")

        import macro as _macro
        reg, breadth = _macro.regime(asof)
        mrow = _macro.at(asof)
        if mrow:
            bits = [f"regime {reg}"]
            if np.isfinite(breadth):
                bits.append(f"breadth {breadth:.0%}")
            for k, lab in (("gpr", "GPR"), ("epu", "EPU")):
                v = mrow.get(k)
                if v is not None and np.isfinite(v):
                    bits.append(f"{lab} {v:.0f}")
            print(f"  macro                {'  '.join(bits)}")
    except Exception as exc:                                   # noqa: BLE001
        print(f"\nSCORE MODULES\n  unavailable ({repr(exc)[:90]})")

    # ---------------------------------------------------------------- reports
    print("\nREPORTS")
    latest = config.REPORTS / "latest.html"
    if latest.exists():
        print(f"  latest.html          {latest.stat().st_size / 1024:.0f} KB   "
              f"{datetime.fromtimestamp(latest.stat().st_mtime):%Y-%m-%d %H:%M}")
        print(f"  open it              {latest}")
    else:
        print("  (none yet -- run `python daily_run.py`)")
    if config.DIGEST_FILE.exists():
        print("\n" + config.DIGEST_FILE.read_text(encoding="utf-8").rstrip())
    return 0


if __name__ == "__main__":
    sys.exit(main())

