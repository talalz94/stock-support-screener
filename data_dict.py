"""
The data dictionary: every free source, every field we store, and the gaps.

    python data_dict.py            write docs/DATA_DICTIONARY.md
    python data_dict.py --stdout   print it instead

WHY A GENERATED DICTIONARY AND NOT A HAND-WRITTEN ONE
--------------------------------------------------------
A hand-written inventory is wrong within a week -- this project has proved that
repeatedly. The field lists here are read from the LIVE registries
(`scores.get(m).metrics()`, `fund_metrics.REGISTRY`, `providers.FIELDS`,
`providers.FINNHUB_FIELDS`, `config.FRED_SERIES`), so a metric added tomorrow
appears here tomorrow and a metric deleted stops being documented.

The *gap* section is the point of the exercise. Knowing what we store is
mildly useful; knowing what a source offers that we DO NOT TAKE is where the
next idea comes from. Those entries are necessarily hand-curated (an API's full
surface is not introspectable), so each is dated and attributed to a measured
observation rather than a guess.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

import config

config.safe_console()

OUT = config.ROOT / "docs" / "DATA_DICTIONARY.md"


# ---------------------------------------------------------------- sources
# Access method and free-tier limit per source. LIMITS ARE MEASURED WHERE THE
# MEASUREMENT EXISTS, because published limits and observed behaviour differ:
# yfinance publishes no limit and hung on a 3,480-name sweep, which is the only
# number that actually matters when scheduling a nightly job.
SOURCES = [
    ("Alpaca", "REST + SDK", "200 req/min (keyed)",
     "daily & hourly bars, corporate actions, news, market calendar, "
     "tradeable asset list", "bars.py, news.py, calendar_us.py, universe.py"),
    ("SEC bulk (Financial Statement Data Sets)", "quarterly ZIP", "none (UA required)",
     "every XBRL fact from every filer, POINT-IN-TIME with filed dates -- the "
     "only free source of what was knowable on a past date",
     "fundamentals.fetch_quarter"),
    ("SEC companyfacts", "REST", "none (UA required, ~10 req/s)",
     "per-company XBRL history; used to top up the bulk sets, which lag "
     "(2026q2 probed 2026-08-13: HTTP 404, not yet published)",
     "fundamentals.backfill_companyfacts, refetch.py"),
    ("SEC company_tickers", "JSON", "none",
     "ticker -> CIK map", "fundamentals.ticker_map"),
    ("Finnhub", "REST", "60 req/min (keyed)",
     "133 pre-computed fundamental metrics; PRIMARY source for displayed "
     "ratios. Measured 0.89 s/name -> ~58 min for the universe",
     "providers.fetch_finnhub"),
    ("Yahoo (yfinance)", "unofficial library", "undocumented; throttles",
     "180 fields incl. float, short interest, next earnings date, analyst "
     "targets. Fine for on-demand and small batches; HUNG on a 3,480-name "
     "sweep (53 min for ~20 CPU-seconds) so it is not the bulk source",
     "providers.fetch"),
    ("FINRA Reg SHO", "daily CSV", "none",
     "consolidated short volume per symbol per day", "finra.py"),
    ("FRED", "REST", "free key, generous",
     "macro series (rates, spreads, claims...) and release calendar",
     "macro.py, config.FRED_SERIES"),
    ("GPR index", "XLS", "none", "daily geopolitical risk index", "macro.py"),
    ("EPU index", "CSV", "none", "daily economic policy uncertainty", "macro.py"),
    ("NASDAQ Trader", "text file", "none",
     "symbol directory, used to sanity-check the tradeable universe",
     "universe.py"),
]


# ------------------------------------------------------------------- gaps
# What each source offers that we do NOT currently store. Hand-curated: an
# API's full surface cannot be introspected. Dated so staleness is visible.
GAPS = [
    ("Yahoo", "earningsTimestampEnd", "**next earnings date**",
     "Highest-value gap. A support bounce two days before earnings is a "
     "different trade with different risk, and nothing in the screener "
     "currently knows the date. Would gate or flag entries."),
    ("Yahoo", "floatShares", "free float",
     "Float is not shares outstanding. For small caps the difference drives "
     "squeeze dynamics; `mktcap` alone cannot express it."),
    ("Yahoo", "sharesShort, shortRatio, shortPercentOfFloat, "
     "sharesShortPriorMonth", "short interest and its change",
     "Complements FINRA short VOLUME (a flow) with short INTEREST (a stock). "
     "The month-on-month change is a positioning signal we have no proxy for."),
    ("Yahoo", "targetMeanPrice, targetHigh/Low/Median", "analyst targets",
     "Dispersion is a cheap disagreement measure; distance-to-target is a "
     "crude expectation gap."),
    ("Yahoo", "forwardPE, forwardEps, epsCurrentYear", "forward estimates",
     "Every valuation metric we store is TRAILING. Forward vs trailing is the "
     "expectations gap, which is closer to what a dip thesis is betting on."),
    ("Yahoo", "heldPercentInsiders, heldPercentInstitutions", "ownership mix",
     "Low institutional ownership plus high retail attention is a distinct "
     "regime from the reverse; hype cannot currently distinguish them."),
    ("Finnhub", "~110 of 133 metrics unused", "quarterly/annual series, "
     "per-share items, 52-week bands",
     "We map 22. The rest include 5- and 10-year growth rates and margin "
     "history that would let quality be measured as a TREND, not a level."),
    ("SEC", "8-K item codes", "event type",
     "We calibrate event severity from price reaction (`events.py`) but never "
     "read what the event WAS."),
    ("SEC", "insider transactions (Forms 3/4/5)", "insider buying/selling",
     "Not currently fetched at all. Free, and a classic signal."),
    ("FRED", "series beyond config.FRED_SERIES", "macro breadth",
     "We pull a fixed list; the API has hundreds of thousands of series."),
    ("Alpaca", "corporate action detail, quote/trade ticks", "microstructure",
     "We use bars only. Spread and trade size at entry would sharpen the "
     "confirm step."),
]


def _module_fields() -> list[tuple[str, int, str]]:
    """(module, n_metrics, sample) read from the LIVE registries."""
    import importlib
    import scores
    out = []
    for m in ("sentiment", "hype", "fundamental", "dip", "combo"):
        try:
            importlib.import_module(f"scores.{m}")
            names = list(scores.get(m).metrics())
            out.append((m, len(names), ", ".join(sorted(names)[:6]) + " ..."))
        except Exception as exc:                                 # noqa: BLE001
            out.append((m, 0, f"(unavailable: {type(exc).__name__})"))
    return out


def build() -> str:
    import fund_metrics as FM
    import providers

    L: list[str] = []
    L.append("<!-- GENERATED by data_dict.py -- do not hand-edit -->")
    L.append(f"# Data dictionary\n")
    L.append(f"Generated {date.today().isoformat()} from the live registries. "
             f"Re-run `python data_dict.py` (the `docs` orchestrator step does "
             f"this automatically) rather than editing.\n")

    L.append("## 1. Sources we use\n")
    L.append("| source | access | free limit | what we take | code |")
    L.append("|---|---|---|---|---|")
    for name, how, lim, take, where in SOURCES:
        L.append(f"| **{name}** | {how} | {lim} | {take} | `{where}` |")

    L.append("\n## 2. What we store\n")
    L.append("### Score modules\n")
    L.append("| module | metrics | examples |")
    L.append("|---|---|---|")
    total = 0
    for m, n, sample in _module_fields():
        total += n
        L.append(f"| `{m}` | {n} | {sample} |")
    L.append(f"\n**{total} stored metrics** across the score modules.\n")

    L.append("### Fundamental metric registry\n")
    L.append(f"`fund_metrics.REGISTRY` holds **{len(FM.REGISTRY)} metrics** "
             f"across {len(FM.PILLARS)} pillars ({', '.join(FM.PILLARS)}).\n")
    L.append("| metric | pillar | direction | meaning |")
    L.append("|---|---|---|---|")
    for name, (pillar, direction, desc) in sorted(FM.REGISTRY.items()):
        arrow = "higher better" if direction > 0 else "lower better"
        L.append(f"| `{name}` | {pillar} | {arrow} | {desc} |")

    L.append("\n### Provider fields\n")
    L.append(f"Finnhub supplies **{len(providers.FINNHUB_FIELDS)} normalised "
             f"fields** (primary source for displayed ratios); Yahoo supplies "
             f"**{len(set(providers.FIELDS.values()))}**.\n")
    L.append("> **Units are normalised on ingest.** Finnhub returns market cap "
             "in MILLIONS and ROE/margins as PERCENT; Yahoo returns absolute "
             "and fractions. `providers.FINNHUB_FIELDS` carries an explicit "
             "multiplier per field and `_selftest_units` asserts it against "
             "live AAPL numbers -- an unconverted merge is a 1,000,000x "
             "market-cap error.\n")
    L.append("| our column | finnhub field | multiplier |")
    L.append("|---|---|---|")
    for src, (col, mult) in sorted(providers.FINNHUB_FIELDS.items(),
                                   key=lambda kv: kv[1][0]):
        m = "1" if mult == 1.0 else (f"x{mult:g}" if mult > 1 else f"x{mult}")
        L.append(f"| `{col}` | `{src}` | {m} |")

    L.append("\n## 3. What we do NOT take -- the idea list\n")
    L.append("The point of this document. Each row is available on a source we "
             "already call, and is not currently stored.\n")
    L.append("| source | field(s) | what it is | why it might matter |")
    L.append("|---|---|---|---|")
    for src, field, what, why in GAPS:
        L.append(f"| {src} | `{field}` | {what} | {why} |")

    L.append("\n## 4. Point-in-time status\n")
    L.append("Which sources can answer *what was knowable on a past date* -- "
             "the property the backtest depends on.\n")
    L.append("| source | point-in-time? | consequence |")
    L.append("|---|---|---|")
    L.append("| SEC bulk + companyfacts | **yes** (`filed` dates) | "
             "the only source the factor study may read |")
    L.append("| Alpaca bars | **yes** (dated) | prices are safe historically |")
    L.append("| Finnhub / Yahoo metrics | **no** -- today only | "
             "overlaid onto the LATEST session only; writing them into a "
             "historical row would be look-ahead bias and silently invalidate "
             "every backtest (`scores/fundamental._is_current` enforces this) |")
    L.append("| FINRA / FRED / GPR / EPU | **yes** (dated series) | safe |")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate the data dictionary.")
    ap.add_argument("--stdout", action="store_true")
    a = ap.parse_args()
    text = build()
    if a.stdout:
        print(text)
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"data_dict: wrote {OUT} ({len(text):,} chars, "
          f"{text.count(chr(10)):,} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
