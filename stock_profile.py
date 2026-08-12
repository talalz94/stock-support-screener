"""
One page per stock: `reports/stock/<TICKER>.html`.

    python profile.py AAPL                 build one
    python profile.py AAPL MSFT PLTR       build several
    python profile.py --flags              build for today's bounce flags
    python profile.py AAPL --open

Everything on the page is laid out by `settings.py`, so which blocks appear, in
what order, and how many rows each shows is the user's choice rather than this
file's. `python settings.py --show` prints the active profile.

WHAT THIS IS MODELLED ON
--------------------------
The Wisesheets-style stock sheet: years across the top, line items down the
side, in-cell sparklines, and growth rows shaded red/green so a trend is legible
without reading a single number. Plus a radar over the four score modules, which
answers "what KIND of stock is this" before any individual figure is read.

TWO HONEST LIMITS, STATED ON THE PAGE ITSELF
----------------------------------------------
1. **The financial history is NOT point-in-time.** `fundamentals.history()`
   deliberately lets the latest restatement win, because a history table is a
   claim about what the numbers *were*, not about what was visible on a date.
   That makes it right for reading and **wrong for backtesting**. Nothing here
   may ever feed `factor_lab`; `facts_asof` is the point-in-time door.
2. **Monthly and half-yearly views do not exist.** The brief asked for monthly /
   quarterly / half-yearly / annual. The SEC fact store is quarterly, so annual
   and quarterly are real and the other two would have to be interpolated. An
   interpolated fundamental is a made-up number wearing a real one's clothes, so
   the toggle offers only what the data supports.
"""

from __future__ import annotations

import argparse
import html as _html
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import config

config.safe_console()

import calendar_us                                               # noqa: E402
import settings as SETTINGS                                      # noqa: E402
import ui                                                        # noqa: E402

OUT_DIR = config.REPORTS / "stock"

# "Every period the store has." `fundamentals.history` takes a count, so this is
# a ceiling high enough that no real filer reaches it (the deepest today is 75).
PERIODS_ALL = 400

# Derived rows the fact store does not carry directly. Each is (label, fn).
# Rows the fact store does not carry directly. ANY of these can be put in
# `financials.rows` or `growth.rows` via settings.py, so a trend line and a YoY
# strip are available for every ratio here, not only for revenue.
DERIVED = {
    "gross_margin": lambda d: _safe_div(d.get("gross_profit"), d.get("revenue")),
    "op_margin": lambda d: _safe_div(d.get("opinc"), d.get("revenue")),
    "net_margin": lambda d: _safe_div(d.get("net_income"), d.get("revenue")),
    "fcf_margin": lambda d: _safe_div(_sub(d.get("cfo"), d.get("capex")),
                                      d.get("revenue")),
    "ebitda": lambda d: _add(d.get("opinc"), d.get("dna")),
    "ebitda_margin": lambda d: _safe_div(_add(d.get("opinc"), d.get("dna")),
                                         d.get("revenue")),
    "fcf": lambda d: _sub(d.get("cfo"), d.get("capex")),
    "roe": lambda d: _safe_div(d.get("net_income"), d.get("equity")),
    "roa": lambda d: _safe_div(d.get("net_income"), d.get("assets")),
    "current_ratio": lambda d: _safe_div(d.get("assets_current"),
                                         d.get("liabilities_current")),
    "debt_to_equity": lambda d: _safe_div(_add(d.get("debt_lt"), d.get("debt_st")),
                                          d.get("equity")),
    "asset_turnover": lambda d: _safe_div(d.get("revenue"), d.get("assets")),
    "rnd_intensity": lambda d: _safe_div(d.get("rnd"), d.get("revenue")),
    "buyback_yield": lambda d: _safe_div(d.get("buybacks"), d.get("equity")),
}

ROW_LABELS = {
    "revenue": "Revenue", "gross_profit": "Gross Profit",
    "gross_margin": "Gross Margin", "op_margin": "Operating Margin",
    "ebitda": "EBITDA", "ebitda_margin": "EBITDA Margin",
    "net_income": "Net Income", "net_margin": "Net Margin",
    "eps_diluted": "EPS (diluted)", "eps_basic": "EPS (basic)",
    "fcf": "Free Cash Flow", "fcf_margin": "FCF Margin",
    "cfo": "Operating Cash Flow", "capex": "CapEx", "assets": "Total Assets",
    "equity": "Total Equity", "opinc": "Operating Income",
    "roe": "Return on Equity", "roa": "Return on Assets",
    "current_ratio": "Current Ratio", "debt_to_equity": "Debt / Equity",
    "asset_turnover": "Asset Turnover", "rnd_intensity": "R&D / Revenue",
    "buyback_yield": "Buybacks / Equity", "rnd": "R&D Expense",
    "sga": "SG&A", "inventory": "Inventory", "cash": "Cash",
    "dividends": "Dividends Paid", "buybacks": "Buybacks",
}
# Rendered as percentages. A ratio in this set is shown x100 with a % sign.
PCT_ROWS = {"gross_margin", "op_margin", "net_margin", "fcf_margin",
            "ebitda_margin", "roe", "roa", "rnd_intensity", "buyback_yield"}
# Rendered as a plain multiple (x.xx), not scaled and not a percentage.
RATIO_ROWS = {"current_ratio", "debt_to_equity", "asset_turnover"}
UNIT_ROWS = {"eps_diluted", "eps_basic"}   # per-share: shown as-is, not scaled

# PER-SHARE ROWS ARE NOT SPLIT-ADJUSTED ACROSS THE WHOLE HISTORY, and the effect
# is large enough to read as a business event if it is not flagged. AAPL's FY2017
# EPS is $9.21 and FY2018 is $2.98 -- a -68% "collapse" in a year when net income
# rose 23%. That is the 4:1 split, not earnings.
#
# The cause is structural: each 10-K restates only the comparatives IT carries,
# so a period last reported before a split keeps its pre-split denominator
# forever. Fixing it would need a split-adjustment factor per period, which the
# fact store does not carry -- bars.py has split data but joining it here would
# be a second, silent source of truth for the same number.
#
# So it is FLAGGED rather than silently wrong. The check compares each per-share
# row's YoY against the same year's net-income YoY; a large divergence is a split.
SPLIT_SUSPECT_GAP = 0.35     # fractional YoY divergence that trips the flag


def _safe_div(a, b):
    if a is None or b is None:
        return None
    return pd.to_numeric(a, errors="coerce") / pd.to_numeric(
        b, errors="coerce").replace(0, np.nan)


def _add(a, b):
    if a is None:
        return None
    return pd.to_numeric(a, errors="coerce").add(
        pd.to_numeric(b, errors="coerce") if b is not None else 0, fill_value=0)


def _sub(a, b):
    if a is None:
        return None
    return pd.to_numeric(a, errors="coerce").sub(
        pd.to_numeric(b, errors="coerce") if b is not None else 0, fill_value=0)


def _esc(v) -> str:
    return ui.esc(v)



# ====================================================== comparison context
_PEER_CACHE: dict = {}
_SESSION_CACHE: dict = {}


def peer_percentiles(module: str, session: str, sector: str | None) -> dict:
    """{metric: {ticker: percentile}} within `sector` for one session.

    Cached on (module, session, sector), AND the underlying session read is
    cached on (module, session). Without the second cache, building N profiles
    re-read the whole cross-section 4xN times -- the same shape as the
    `self_percentiles` problem, and it dominated the profiles step once that one
    was fixed.
    """
    if not sector:
        return {}
    ck = (module, session, str(sector))
    if ck in _PEER_CACHE:
        return _PEER_CACHE[ck]
    try:
        import macro
        import scores
        smap = macro.load_sector_map()
        peers = smap[smap["sector"].astype(str) == str(sector)]["ticker"]
        peers = set(peers.astype(str))
        if len(peers) < 5:
            return {}          # too thin for a percentile to mean anything
        sk = (module, session)
        if sk not in _SESSION_CACHE:
            _SESSION_CACHE[sk] = scores.read(module=module, start=session,
                                             end=session)
        df = _SESSION_CACHE[sk]
        if df is None or df.empty:
            _PEER_CACHE[ck] = {}
            return {}
        df = df[df["ticker"].astype(str).isin(peers)]
        if df.empty:
            _PEER_CACHE[ck] = {}
            return {}
        wide = df.pivot_table(index="ticker", columns="metric", values="value",
                              aggfunc="last")
        out = {c: (wide[c].rank(pct=True) * 100).to_dict() for c in wide.columns}
        _PEER_CACHE[ck] = out
        return out
    except Exception:                                            # noqa: BLE001
        _PEER_CACHE[ck] = {}
        return {}


# One cached read per (module, asof) instead of one per (module, TICKER).
# MEASURED: the per-ticker version took 22.1s because `scores.read` opens every
# month partition to find one name, so a 30-flag `profiles` step would have cost
# 45 minutes against a 900s budget. Reading once for the whole batch turns that
# into one scan per module.
_HIST_CACHE: dict = {}


def prime_history(module: str, tickers: list[str], asof: str) -> None:
    """Read one module's history for a BATCH of tickers, once."""
    key = (module, asof)
    if key in _HIST_CACHE:
        return
    try:
        import scores
        start = (pd.Timestamp(asof) - pd.DateOffset(years=6)).strftime("%Y-%m-%d")
        df = scores.read(module=module, start=start, end=asof, tickers=tickers)
        _HIST_CACHE[key] = df if df is not None else pd.DataFrame()
    except Exception:                                            # noqa: BLE001
        _HIST_CACHE[key] = pd.DataFrame()


def self_percentiles(module: str, ticker: str, asof: str) -> dict:
    """{metric: percentile of TODAY within this ticker's own stored history}.

    Answers "is this unusual for this company" rather than "unusual for the
    market", which is the question a vs-history column is asked.
    """
    key = (module, asof)
    if key not in _HIST_CACHE:
        prime_history(module, [ticker], asof)
    df = _HIST_CACHE.get(key)
    if df is None or df.empty:
        return {}
    try:
        d = df[df["ticker"].astype(str) == ticker]
        if d.empty:
            return {}
        wide = d.pivot_table(index="session", columns="metric", values="value",
                             aggfunc="last").sort_index()
        if len(wide) < 8:
            # Too short to rank against. Absent, NOT 50 -- a made-up midpoint
            # would read as "perfectly average" when the truth is "unknown".
            return {}
        out = {}
        for c in wide.columns:
            s = pd.to_numeric(wide[c], errors="coerce").dropna()
            if len(s) < 8:
                continue
            out[c] = float((s < s.iloc[-1]).sum()) / len(s) * 100.0
        return out
    except Exception:                                            # noqa: BLE001
        return {}


# ============================================================ data collection
def meta(ticker: str) -> dict:
    """Name, exchange, sector, industry -- everything the header shows."""
    out = {"ticker": ticker, "name": ticker, "exchange": None,
           "sector": None, "sic": None, "is_etf": False}
    try:
        u = pd.read_parquet(config.UNIVERSE_FILE)
        r = u[u["ticker"].astype(str) == ticker]
        if not r.empty:
            r = r.iloc[0]
            out["name"] = str(r.get("name") or ticker)
            out["exchange"] = r.get("exchange")
            out["is_etf"] = bool(r.get("is_etf", False))
    except Exception:                                            # noqa: BLE001
        pass
    try:
        import macro
        m = macro.load_sector_map()
        r = m[m["ticker"].astype(str) == ticker]
        if not r.empty:
            out["sector"] = r.iloc[0].get("sector")
            out["sic"] = r.iloc[0].get("sic")
            out["sector_etf"] = r.iloc[0].get("sector_etf")
    except Exception:                                            # noqa: BLE001
        pass
    return out


def latest_scores(ticker: str, asof: str) -> dict[str, dict]:
    """{module: {metric: value}} from the tidy score table, most recent session
    at or before `asof` per module."""
    out: dict[str, dict] = {}
    try:
        import scores
        scores.load_all()
    except Exception:                                            # noqa: BLE001
        return out
    for mod in config.SCORE_MODULES:
        try:
            sess = [s for s in scores.sessions_stored(mod) if s <= asof]
            if not sess:
                continue
            df = scores.read(module=mod, start=sess[-1], end=sess[-1],
                             tickers=[ticker])
            if df is None or df.empty:
                continue
            vals = {}
            for _, r in df.iterrows():
                v = r.get("value")
                vals[str(r["metric"])] = (r.get("label")
                                          if pd.isna(v) else float(v))
            out[mod] = {"_session": sess[-1], **vals}
        except Exception:                                        # noqa: BLE001
            continue
    return out


def price_series(ticker: str, asof: str, days: int = 400) -> pd.Series:
    try:
        import store
        start = (pd.Timestamp(asof) - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
        px = store.read("1d", start=start, end=asof, tickers=[ticker],
                        columns=["ticker", "date", "close"])
        if px.empty:
            return pd.Series(dtype="float64")
        return px.sort_values("date").set_index("date")["close"]
    except Exception:                                            # noqa: BLE001
        return pd.Series(dtype="float64")


# ==================================================================== render
# Styling, nav and the shared components now come from ui.py. This file had
# its own 48-line stylesheet, which is how six pages ended up with two
# different colour vocabularies.


def _fmt(v, row: str) -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)) or pd.isna(v):
        return '<span style="color:var(--muted)">&mdash;</span>'
    v = float(v)
    if row in PCT_ROWS:
        return f"{v * 100:,.1f}%"
    if row in RATIO_ROWS:
        return f"{v:,.2f}x"
    if row in UNIT_ROWS:
        return f"{v:,.2f}"
    a = abs(v)
    if a >= 1e9:
        return f"{v / 1e9:,.1f}B"
    if a >= 1e6:
        return f"{v / 1e6:,.0f}M"
    return f"{v:,.0f}"


def _growth_cell(g, as_points: bool = False) -> str:
    """A shaded growth cell. Intensity is capped: without the cap a single
    +900% recovery year saturates the scale and every other year reads flat.

    `as_points` renders a percentage-POINT delta (for margin rows) on a tighter
    scale, since 6pp of margin change is a big move where 6% of revenue growth
    is not.
    """
    if g is None or pd.isna(g) or not np.isfinite(g):
        return '<td><span style="color:var(--muted)">&mdash;</span></td>'
    pct = float(g) * 100
    strength = min(abs(pct) / (8.0 if as_points else 60.0), 1.0)
    alpha = round(0.10 + 0.55 * strength, 3)
    colour = "26,127,55" if pct >= 0 else "207,34,46"
    txt = f"{pct:+,.1f}pp" if as_points else f"{pct:+,.0f}%"
    return f'<td style="background:rgba({colour},{alpha})">{txt}</td>'


def _sparkline(vals, w: int = 78, h: int = 20) -> str:
    return ui.sparkline(vals, w, h)


def _radar(axes: dict, size: int = 250) -> str:
    """Delegates to ui.radar, which fixed the viewBox that clipped
    "sentiment" to "sen" and "technicals" to "cas"."""
    return ui.radar(axes)


# ================================================================ the blocks
def _block_header(t: str, m: dict, sc: dict, px: pd.Series, opt: dict) -> str:
    show = set(opt.get("show") or [])
    chips = []
    # `if m.get("sic")` is NOT enough: a float NaN is TRUTHY, so a missing SIC
    # passed the guard and `int(nan)` raised. That killed 7 of 30 profile builds
    # (ASM, DPRO, DXYZ, EXK, POET, SGML, VLN) -- the exact gotcha already
    # recorded in SCORE_MODULES.md, hit again in new code.
    sec = m.get("sector")
    if "sector" in show and isinstance(sec, str) and sec:
        chips.append(f'<span class="chip">sector <b>{_esc(sec)}</b></span>')
    sic = m.get("sic")
    if sic is not None and pd.notna(sic):
        chips.append(f'<span class="chip">SIC <b>{_esc(int(sic))}</b></span>')
    if "exchange" in show and m.get("exchange"):
        chips.append(f'<span class="chip">{_esc(m["exchange"])}</span>')
    # THE REPORTING CURRENCY, and only when it is not USD.
    #
    # A non-USD filer has no P/E, P/B, EV/EBITDA or Altman Z -- those are
    # withheld rather than converted, because a USD share price over a foreign
    # book is wrong by the exchange rate and looks entirely normal. Without
    # this chip the reader sees those rows simply absent and cannot tell a
    # deliberate withholding from missing data, which defeats the point of
    # withholding them. A "USD" chip on the other 3,195 names would be noise,
    # so only the exception is shown.
    ccy = (sc.get("fundamental") or {}).get("currency")
    if isinstance(ccy, str) and ccy and ccy != "USD":
        chips.append(f'<span class="chip note" title="Files in {_esc(ccy)}. '
                     f'Ratios (margins, ROE, F-score, leverage) are currency-'
                     f'free and shown; price-relative metrics (P/E, P/B, '
                     f'EV/EBITDA, Altman Z) are withheld rather than converted '
                     f'at a guessed rate.">reports in <b>{_esc(ccy)}</b> '
                     f'&middot; no price ratios</span>')
    if "price" in show and len(px):
        chips.append(f'<span class="chip">last <b>${px.iloc[-1]:,.2f}</b></span>')
    if "mktcap" in show:
        mc = (sc.get("fundamental") or {}).get("mktcap")
        if isinstance(mc, float) and np.isfinite(mc):
            chips.append(f'<span class="chip">mkt cap <b>${mc / 1e9:,.1f}B</b></span>')
    etf = m.get("sector_etf")
    if isinstance(etf, str) and etf:
        chips.append(f'<span class="chip">peer ETF <b>{_esc(etf)}</b></span>')
    # TradingView is the only OUTBOUND link here, so it goes last and to the
    # right: `.chips .right` takes `margin-left:auto`. Sitting mid-row among the
    # descriptive chips, it read as another fact about the company rather than
    # as a way out of the page.
    if "tradingview" in show:
        import report
        chips.append(f'<a class="chip cta right" '
                     f'href="{report.tv_link(t, m.get("exchange"))}" '
                     f'target="_blank" rel="noopener">TradingView &nearr;</a>')
    return f'<div class="chips">{"".join(chips)}</div>'


def _axis_values(sc: dict, px: pd.Series) -> dict[str, float | None]:
    """The four radar axes, each 0-100. `technicals` has no score module yet, so
    it is derived here from 12-1 momentum percentile -- stated on the page rather
    than passed off as a peer of the other three."""
    fund = (sc.get("fundamental") or {}).get("fund_score")
    hype = (sc.get("hype") or {}).get("hype_score")
    senti = (sc.get("sentiment") or {}).get("sent_mean_30d")
    # sentiment is roughly -1..+1; map to 0-100 for the radar
    senti = None if senti is None or pd.isna(senti) else \
        max(0.0, min(100.0, (float(senti) + 1) * 50))
    tech = None
    if len(px) > 252:
        r = float(px.iloc[-21] / px.iloc[-252] - 1.0)
        tech = max(0.0, min(100.0, 50 + r * 100))
    return {"fundamentals": fund, "sentiment": senti, "hype": hype,
            "technicals": tech}


# (label, sessions back, bar size). Bar size is chosen so every window renders
# roughly 100-250 points: the old 5y pane drew 1,260 daily closes into 920px of
# plot, which is sub-pixel detail nobody can see and was 57% of the page weight.
# SIX windows, which is what was asked for -- 3M was missing, leaving a
# four-fold jump from 1M straight to 6M with nothing in between, exactly the
# range you look at after an earnings move.
CHART_WINDOWS = (
    ("1M",    21, "D"),
    ("3M",    63, "D"),
    ("6M",   126, "D"),
    ("1Y",   252, "D"),
    ("5Y",  1260, "W"),
    ("MAX",      0, "M"),      # 0 = everything stored
)
CHART_DEFAULT = "1Y"


def _resample(bars, how: str):
    """Daily bars -> weekly or monthly OHLC-ish points. `close` is the period's
    last close and `volume` its sum, which is what a price line and a volume bar
    should show; anything else would misstate one of them."""
    if how == "D" or bars.empty:
        return bars
    b = bars.copy()
    b["_d"] = pd.to_datetime(b["date"], errors="coerce")
    b = b[b["_d"].notna()].set_index("_d")
    rule = "W" if how == "W" else "MS"
    out = b.resample(rule).agg({"close": "last", "volume": "sum"}).dropna(
        subset=["close"]).reset_index()
    out["date"] = out["_d"].dt.strftime("%Y-%m-%d")
    return out[["date", "close", "volume"]]


def _support_level(ticker: str, asof: str) -> tuple[float | None, tuple | None]:
    """The screener's own support level and base band, or (None, None).

    Taken from `screen.screen_one` rather than recomputed, so the chart cannot
    draw a level the gate never saw. Uses the quiet path -- `explain_one` prints
    a full gate trace, which would land in the middle of a build log.
    """
    try:
        import calendar_us
        import dataset
        import screen
        import universe
        tk = universe.normalize(ticker)
        start = calendar_us.session_offset(
            calendar_us.all_sessions(), asof,
            config.IND_WARMUP + config.STRUCT_WIN + 5)
        df = dataset.history(tk, "1d", start=start, end=asof)
        if df.empty:
            return None, None
        mm = screen.screen_one(df, None, None, config)
        lv = mm.get("level")
        lo, hi = mm.get("base_lo"), mm.get("base_hi")
        lv = float(lv) if lv is not None and np.isfinite(float(lv)) else None
        band = ((float(lo), float(hi))
                if lo is not None and hi is not None
                and np.isfinite(float(lo)) and np.isfinite(float(hi)) else None)
        return lv, band
    except Exception:                                            # noqa: BLE001
        # A chart without a level is still useful; a build that dies because the
        # gate could not run is not.
        return None, None


def _block_chart(ticker: str, asof: str, opt: dict) -> str:
    """Price + volume + support, one pane per window, toggled client-side.

    Three server-rendered SVGs rather than one drawn in JS: it reuses the exact
    toggle pattern the financials table already uses, works with scripting off,
    and the 5y pane is only ~15 KB of polyline.
    """
    import store
    start = (pd.Timestamp(asof) - pd.DateOffset(days=int(1260 * 1.5))) \
        .strftime("%Y-%m-%d")
    try:
        bars = store.read("1d", start=start, end=asof, tickers=[ticker],
                          columns=["ticker", "date", "close", "volume"])
    except Exception:                                            # noqa: BLE001
        bars = pd.DataFrame()
    if bars.empty:
        return '<div class="note">no bars stored for this name</div>'
    bars = bars.sort_values("date")

    level, band = _support_level(ticker, asof)
    default = str(opt.get("window") or CHART_DEFAULT).upper()
    if default not in {n for n, _s, _b in CHART_WINDOWS}:
        default = CHART_DEFAULT
    panes, btns = "", ""
    for name, ndays, how in CHART_WINDOWS:
        sub = bars if not ndays else bars.tail(ndays)
        sub = _resample(sub, how)
        hide = "" if name == default else ' style="display:none"'
        panes += (f'<div class="ch-pane" data-win="{name}"{hide}>'
                  + ui.price_chart(sub["date"].tolist(), sub["close"].tolist(),
                                   sub["volume"].tolist(), level=level,
                                   base=band, label=f"{ticker} {name}")
                  + "</div>")
        on = "on" if name == default else ""
        unit = {"D": "daily", "W": "weekly", "M": "monthly"}[how]
        btns += (f'<button data-win="{name}" class="{on}" '
                 f'title="{unit} bars">{name}</button>')

    lvl_txt = (f'support <b>{level:,.2f}</b>' if level is not None
               else '<span class="muted">no support level found by the gate</span>')
    return ('<div class="pcbar"><div class="toggle" id="winToggle">'
            f'{btns}</div><span class="note" style="margin:0">{lvl_txt} '
            '&mdash; the level and base band come from the bounce gate itself, '
            'not from a separate calculation</span></div>' + panes)


def _block_radar(t, m, sc, px, opt) -> str:
    axes = _axis_values(sc, px)
    wanted = opt.get("axes") or list(axes)
    return ('<div class="card">'
            + _radar({k: axes.get(k) for k in wanted})
            + '<div class="note"><b>technicals</b> is 12-1 momentum mapped to '
              '0-100, not a score module &mdash; there is no technical module '
              'yet. The other three come from their modules.</div></div>')


def _metric_card(title: str, vals: dict, keys: list[str], limit: int | None,
                 peer: dict | None = None, hist: dict | None = None,
                 ticker: str = "") -> str:
    """Labelled rows with vs-industry and vs-history bars.

    Replaces a 2-column dump of raw `snake_case` keys. Names come from
    `ui.METRIC_LABELS`, which is built from `fund_metrics.REGISTRY` so the
    labels cannot drift from the definitions they describe.
    """
    # A REQUESTED METRIC WITH NO VALUE IS STILL SHOWN.
    #
    # This used to be `keys = [k for k in keys if k in vals]`, which dropped the
    # row entirely -- and a missing row is the least visible state a page has.
    # ABAT's `safety_score` disappeared that way: three of its four inputs are
    # absent for a pre-revenue company, so the card listed Quality, Value and
    # Growth and gave no hint that a fourth pillar existed at all. The reader
    # cannot ask about a number they cannot see.
    #
    # `metric_row` and `pct_bar` already render an explicit empty state, so the
    # honest version costs nothing but not filtering.
    missing = [k for k in keys if k not in vals or vals.get(k) is None]
    if limit:
        keys = keys[:limit]
    if not keys:
        return ('<div class="card"><div class="note">no stored values for '
                'this name</div></div>')
    peer = peer or {}
    hist = hist or {}
    import metrics_doc
    rows = "".join(
        ui.metric_row(k, vals.get(k),
                      (peer.get(k) or {}).get(ticker),
                      hist.get(k),
                      direction=metrics_doc.direction(k))
        for k in keys)
    head = f"<h3>{ui.esc(title)}</h3>" if title else ""
    note = ""
    shown_missing = [k for k in missing if k in keys]
    if shown_missing:
        names = ", ".join(ui.label(k) for k in shown_missing[:4])
        more = f" +{len(shown_missing) - 4} more" if len(shown_missing) > 4 else ""
        note = (f'<div class="note"><b>No data for {len(shown_missing)} of '
                f'{len(keys)}:</b> {ui.esc(names)}{more}. Shown as &mdash; '
                f'rather than hidden: a missing input is not a low score.</div>')
    return f'<div class="card">{head}{ui.metric_head()}{rows}{note}</div>'


def _block_financials(t, hist: pd.DataFrame, opt: dict,
                      freq: str = "A") -> str:
    if hist.empty:
        return ('<div class="warn">No filings in the fact store for this '
                'ticker &mdash; ADR, trust, or it has never filed XBRL. '
                'This is "no data", not "no growth".</div>')
    d = {c: hist[c] for c in hist.columns}
    for name, fn in DERIVED.items():
        try:
            s = fn(d)
            if s is not None:
                d[name] = s
        except Exception:                                        # noqa: BLE001
            pass

    rows = [r for r in (opt.get("rows") or []) if r in d]
    if not rows:
        return '<div class="warn">No requested rows are available.</div>'
    periods = list(hist.index)

    # <thead> IS LOAD-BEARING, not decoration: `.scroll.sticky-x thead th` is
    # what pins the header row, and without the wrapper the selector matched
    # nothing and the header scrolled away like any other row.
    head = ('<thead><tr><th class="lab">Line item</th><th class="spark"></th>'
            + "".join(f"<th>{_esc(p)}</th>" for p in periods)
            + "</tr></thead>")
    body = ""
    growth_rows = set(opt.get("_growth_rows") or [])
    ni = pd.to_numeric(pd.Series(d.get("net_income", pd.Series(dtype=float)))
                       .reindex(periods), errors="coerce")
    ni_g = ni.pct_change(fill_method=None)
    split_years: list[str] = []

    for r in rows:
        s = pd.to_numeric(pd.Series(d[r]).reindex(periods), errors="coerce")
        g = s.pct_change(fill_method=None)
        mark = ""
        if r in UNIT_ROWS:
            # A per-share row moving very differently from net income is a split
            # artifact, not a business event -- see SPLIT_SUSPECT_GAP.
            div = (g - ni_g).abs()
            hits = [p for p, x in zip(periods, div)
                    if pd.notna(x) and x > SPLIT_SUSPECT_GAP]
            if hits:
                split_years += hits
                mark = ' <span title="not split-adjusted" ' \
                       'style="color:var(--neg)">&#9888;</span>'
        body += (f'<tr><td class="lab">{_esc(ROW_LABELS.get(r, r))}{mark}</td>'
                 f'<td class="spark">{_sparkline(s.tolist())}</td>'
                 + "".join(f"<td>{_fmt(v, r)}</td>" for v in s) + "</tr>")
        if r in growth_rows:
            # On a QUARTERLY table the 1-period change is quarter-over-quarter,
            # not year-over-year, and labelling it "YoY" would be simply wrong.
            # Both are shown there because QoQ alone is misleading for any
            # seasonal business -- a retailer's Q1 is always below its Q4.
            # NOT `periods` -- that name holds the period LABELS from the top
            # of this function, and rebinding it here collapsed every row after
            # the first to a single cell. Revenue rendered fine because it is
            # first; everything below it showed one em-dash.
            lags = [(1, "QoQ"), (4, "YoY")] if freq.upper() == "Q" \
                else [(1, "YoY")]
            for lag, tag in lags:
                if r in PCT_ROWS:
                    # The percent change OF a percentage is close to
                    # meaningless -- a margin going 2% -> 3% is "+50%", which
                    # reads like a transformed business. The change in
                    # percentage POINTS is what an analyst wants.
                    d_ = s.diff(lag)
                    body += (f'<tr class="growth">'
                             f'<td class="lab">{tag} &Delta;pp</td><td class="spark"></td>'
                             + "".join(_growth_cell(v, as_points=True)
                                       for v in d_) + "</tr>")
                else:
                    d_ = s.pct_change(lag, fill_method=None)
                    body += (f'<tr class="growth">'
                             f'<td class="lab">{tag} %</td><td class="spark"></td>'
                             + "".join(_growth_cell(v) for v in d_) + "</tr>")

    warn = ""
    if split_years:
        yrs = ", ".join(sorted(set(split_years)))
        warn = (f'<div class="warn"><b>Per-share figures are not split-adjusted '
                f'across this whole history.</b> {yrs} show a per-share change '
                f'far larger than the change in net income, which is the '
                f'signature of a stock split rather than a business event. Each '
                f'10-K restates only the comparative years it carries, so a '
                f'period last reported before a split keeps its old denominator. '
                f'Read the dollar rows, not the per-share rows, across a split.'
                f'</div>')
    # `start-end`: open on the NEWEST period, not the oldest. This table is 75
    # columns going back to 2006, and the column anyone opens it for is the
    # most recent quarter. It also removes a real defect -- the browser settled
    # 11px short of the true end, which clipped the last column's value mid
    # character ("143.8B" rendering as "143.8E").
    return (f'{warn}<div class="scroll sticky-x start-end">'
            f'<table>{head}<tbody>{body}</tbody></table></div>')


# ==================================================================== build
def build(ticker: str, verbose: bool = True,
          session: str | None = None) -> Path:
    ticker = str(ticker).upper().strip()
    asof = session or calendar_us.last_closed_session()
    prof = SETTINGS.load()
    blocks = SETTINGS.blocks_in_order(prof)
    keys = {k for k, _ in blocks}

    m = meta(ticker)
    sc = latest_scores(ticker, asof)
    px = price_series(ticker, asof)

    # Comparison context, gathered once and shared by every metric card.
    peer = {mod: peer_percentiles(mod, (sc.get(mod) or {}).get("_session", asof),
                                  m.get("sector")) for mod in sc}
    hist_pct = {mod: self_percentiles(mod, ticker, asof) for mod in sc}

    fin_opt = dict((prof["blocks"].get("financials") or {}))
    growth_opt = prof["blocks"].get("growth") or {}
    if growth_opt.get("enabled", True):
        fin_opt["_growth_rows"] = growth_opt.get("rows") or []

    # BOTH frequencies are built and toggled client-side. The fact store is
    # quarterly, so annual and quarterly are the only honest options -- monthly
    # and half-yearly would be interpolation, i.e. invented numbers.
    tables, hist = {}, pd.DataFrame()
    if "financials" in keys:
        import fundamentals as FD
        # ALL stored periods, first to most recent -- not a 16/12 window.
        # AAPL has 73 quarters in the fact store and the page was showing 16 of
        # them, which reads as "the data stops in 2022" rather than "the table
        # was capped". A 73-column table is only usable because the first two
        # columns and the header are frozen and the scrollbar is on top, so
        # this deliberately lands after those.
        for freq, periods in (("A", int(fin_opt.get("periods") or 0) or PERIODS_ALL),
                              ("Q", int(fin_opt.get("quarters") or 0) or PERIODS_ALL)):
            try:
                h = FD.history(ticker, freq, periods)
            except Exception:                                    # noqa: BLE001
                h = pd.DataFrame()
            tables[freq] = h
            if freq == fin_opt.get("freq", "A"):
                hist = h

    parts = []
    for key, opt in blocks:
        if key == "header":
            parts.append(_block_header(ticker, m, sc, px, opt))
        elif key == "chart":
            parts.append("<h2>Price</h2>"
                         + _block_chart(ticker, asof, opt))
        elif key == "radar":
            parts.append('<h2>Score radar</h2><div class="grid2">'
                         + _block_radar(ticker, m, sc, px, opt)
                         + _metric_card(
                             "Composite scores",
                             {**(sc.get("fundamental") or {}),
                              **(sc.get("hype") or {}),
                              **(sc.get("dip") or {}),
                              **(sc.get("combo") or {})},
                             (prof["blocks"].get("scores") or {}).get("metrics", []),
                             (prof["blocks"].get("scores") or {}).get("limit"),
                             peer.get("fundamental"), hist_pct.get("fundamental"),
                             ticker)
                         + "</div>")
        elif key == "financials":
            default = fin_opt.get("freq", "A")
            panes = ""
            for freq in ("A", "Q"):
                h = tables.get(freq, pd.DataFrame())
                hide = "" if freq == default else ' style="display:none"'
                panes += (f'<div class="fin-pane" data-freq="{freq}"{hide}>'
                          + _block_financials(ticker, h, fin_opt, freq)
                          + "</div>")
            on_a = "on" if default == "A" else ""
            on_q = "on" if default == "Q" else ""
            toggle = ('<div class="toggle" id="freqToggle">'
                      f'<button data-freq="A" class="{on_a}">Annual</button>'
                      f'<button data-freq="Q" class="{on_q}">Quarterly</button>'
                      '</div>')
            parts.append('<h2>Financials</h2>'
                         f'<div class="finbar">{toggle}'
                         '<span class="note" style="margin:0">annual and '
                         'quarterly only &mdash; the fact store is quarterly, '
                         'anything finer would be interpolation</span></div>'
                         + panes)
        elif key == "fundamentals":
            parts.append("<h2>Fundamental metrics</h2>" + _metric_card(
                "", sc.get("fundamental") or {}, opt.get("metrics", []),
                opt.get("limit"), peer.get("fundamental"),
                hist_pct.get("fundamental"), ticker))
        elif key == "sentiment":
            parts.append("<h2>Sentiment</h2>" + _metric_card(
                "", sc.get("sentiment") or {}, opt.get("metrics", []),
                opt.get("limit"), peer.get("sentiment"),
                hist_pct.get("sentiment"), ticker))
        elif key == "hype":
            parts.append("<h2>Hype</h2>" + _metric_card(
                "", sc.get("hype") or {}, opt.get("metrics", []),
                opt.get("limit"), peer.get("hype"), hist_pct.get("hype"),
                ticker))
        # `scores` renders inside the radar block; `levels` and `growth` are
        # handled by their hosts. Unknown blocks are skipped without error so a
        # profile saved by a future version still renders.

    src = ", ".join(f"{k} @ {v.get('_session')}" for k, v in sc.items()) or "none"
    body = "".join(parts) + (
        '<div class="note"><b>The financial history above is not '
        'point-in-time.</b> It lets the latest restatement win, which is '
        'correct for reading and wrong for backtesting &mdash; nothing on this '
        'page may feed factor_lab. <code>facts_asof</code> is the '
        'point-in-time door.<br><b>Vs industry</b> is the percentile among '
        'peers in the same sector for this session; <b>vs history</b> is where '
        "today sits inside this company's own stored history. A hatched bar "
        'means no data, which is not the same as ranking last.<br>Built '
        f'{datetime.now():%Y-%m-%d %H:%M:%S}.</div>')

    # BUILD TIME IS PART OF THE HEADER, not a footnote.
    #
    # These pages are static files. Only the tickers flagged on a given day
    # were rebuilt, so a page could sit on disk for days showing figures from
    # before a data fix -- AMD was served two days stale, missing both the
    # recovered 2026 quarters and the derived fiscal Q4, and looked for all the
    # world like a bug that had never been fixed. Nothing on the page said
    # when it had been rendered, so there was no way to tell.
    _built = datetime.now()
    sub = (f'session <b>{ui.esc(asof)}</b> &middot; profile '
           f'&ldquo;{ui.esc(prof.get("name"))}&rdquo; &middot; '
           f'scores from {ui.esc(src)} &middot; '
           f'page built <b>{_built:%Y-%m-%d %H:%M}</b>')

    extra_css = (
        "<style>"
        ".grid2{display:grid;grid-template-columns:minmax(300px,380px) 1fr;"
        "gap:18px;align-items:start}"
        "@media(max-width:900px){.grid2{grid-template-columns:1fr}}"
        ".finbar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;"
        "margin-bottom:9px}"
        "td.spark{padding:2px 6px;width:86px}"
        "tr.growth td{font-size:11.5px}"
        "tr.growth td.lab{font-weight:400;color:var(--muted);padding-left:18px}"
        "th.lab,td.lab{position:sticky;left:0;background:var(--panel);z-index:1;"
        "white-space:nowrap;font-weight:600}"
        ".warn{background:var(--warnbg);border:1px solid var(--line);"
        "border-radius:8px;padding:9px 12px;font-size:12px;margin:12px 0}"
        "</style>")

    js = (
        "var ft=document.getElementById('freqToggle');\n"
        "if(ft){ft.addEventListener('click',function(e){\n"
        "  var b=e.target.closest('button[data-freq]'); if(!b) return;\n"
        "  var f=b.getAttribute('data-freq');\n"
        "  ft.querySelectorAll('button').forEach(function(x){\n"
        "    x.classList.toggle('on', x===b); });\n"
        "  document.querySelectorAll('.fin-pane').forEach(function(p){\n"
        "    p.style.display=(p.getAttribute('data-freq')===f)?'':'none'; });\n"
        "});}\n"
        # THE CHART WINDOW TOGGLE. This block went missing for a whole round:
        # a patch script printed "wired" without asserting its replace matched,
        # so the 6m/1y/5y buttons rendered and did nothing. `selftest` now greps
        # the RENDERED html inside <script> for it, not this source.
        "var wt=document.getElementById('winToggle');\n"
        "if(wt){wt.addEventListener('click',function(e){\n"
        "  var b=e.target.closest('button[data-win]'); if(!b) return;\n"
        "  var v=b.getAttribute('data-win');\n"
        "  wt.querySelectorAll('button').forEach(function(x){\n"
        "    x.classList.toggle('on', x===b); });\n"
        "  document.querySelectorAll('.ch-pane').forEach(function(p){\n"
        "    p.style.display=(p.getAttribute('data-win')===v)?'':'none'; });\n"
        "});}\n")

    # A session picker, which this page had no way to offer before -- the only
    # route to a previous session was editing the filename by hand. Dated
    # profiles are written as `<TICKER>_<session>.html`, so the pattern carries
    # the ticker; sessions with no saved file are marked and explain themselves
    # rather than 404ing (see ui.session_picker).
    try:
        import scores as _s
        _s.load_all()
        sess = sorted({d for mod in config.SCORE_MODULES
                       for d in _s.sessions_stored(mod) if d <= asof},
                      reverse=True)[:120]
        static = {p.stem.split("_", 1)[1] for p in OUT_DIR.glob(f"{ticker}_*.html")
                  if "_" in p.stem}
        static.add(asof)
        picker = ui.session_picker(asof, sess, ticker + "_{d}.html",
                                   static=static, latest=f"{ticker}.html")
    except Exception:                                            # noqa: BLE001
        picker = ""

    html = ui.page(f"{ticker} \u00b7 {m.get('name')}", body, subtitle=sub,
                   active="profiles", depth=1, head=extra_css, script=js,
                   nav_extra=picker)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{ticker}.html" if session is None else f"{ticker}_{session}.html"
    out = OUT_DIR / name
    out.write_text(html, encoding="utf-8")
    if verbose:
        print(f"  {ticker}: {out}  ({len(hist)} period(s), "
              f"{len(sc)} module(s) with scores)")
    return out


def write_index(verbose: bool = True) -> Path:
    """`reports/stock/index.html` -- every built profile, searchable.

    The hub used to print a dead "+18 more" with no way to reach those pages.
    """
    import report
    import scores
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pages = sorted(p.stem for p in OUT_DIR.glob("*.html")
                   if p.stem != "index" and "_" not in p.stem)

    meta_rows = {}
    try:
        u = pd.read_parquet(config.UNIVERSE_FILE)[["ticker", "name", "exchange"]]
        meta_rows = u.set_index("ticker").to_dict("index")
    except Exception:                                            # noqa: BLE001
        pass
    sector = {}
    try:
        import macro
        sector = macro.load_sector_map().set_index("ticker")["sector"].to_dict()
    except Exception:                                            # noqa: BLE001
        pass

    asof = calendar_us.last_closed_session()
    wide = {}
    try:
        scores.load_all()
        frames = []
        for mod in config.SCORE_MODULES:
            sess = [s for s in scores.sessions_stored(mod) if s <= asof]
            if not sess:
                continue
            df = scores.read(module=mod, start=sess[-1], end=sess[-1],
                             tickers=pages)
            if df is None or df.empty:
                continue
            keep = df[df["metric"].isin(
                ["fund_score", "hype_score", "sent_mean_30d", "dip_score"])]
            if not keep.empty:
                frames.append(keep.pivot_table(index="ticker", columns="metric",
                                               values="value", aggfunc="last"))
        if frames:
            w = pd.concat(frames, axis=1)
            wide = w.loc[:, ~w.columns.duplicated()].to_dict("index")
    except Exception:                                            # noqa: BLE001
        pass

    cols = ["fund_score", "hype_score", "dip_score", "sent_mean_30d"]
    rows = ""
    for t_ in pages:
        mrow = meta_rows.get(t_, {})
        vals = wide.get(t_, {})
        cells = "".join(f"<td>{ui.fmt_value(vals.get(c), c)}</td>" for c in cols)
        key = (t_ + " " + str(mrow.get("name") or "")).upper()
        rows += (f'<tr data-k="{ui.esc(key)}">'
                 f'<td class="lab"><a href="{t_}.html">{ui.esc(t_)}</a></td>'
                 f'<td class="lab muted">{ui.esc(mrow.get("name") or "")}</td>'
                 f'<td class="lab muted">{ui.esc(sector.get(t_) or "")}</td>'
                 f'{cells}'
                 f'<td class="lab"><a href="'
                 f'{report.tv_link(t_, mrow.get("exchange"))}" target="_blank" '
                 f'rel="noopener">chart &nearr;</a></td></tr>')

    head = "".join(f"<th>{ui.esc(ui.label(c))}</th>" for c in cols)
    # EVERY tradeable name is searchable, not only the ones already rendered.
    # 37 of 3,480 had pages, and nothing told you the other 3,443 were
    # reachable at all.
    universe = []
    try:
        import bars
        uni = bars.tradeable_universe(asof)
        built = set(pages)
        universe = [[t_, str((meta_rows.get(t_) or {}).get("name") or "")]
                    for t_ in uni if t_ not in built]
    except Exception:                                            # noqa: BLE001
        pass

    import json as _json
    body = (
        '<div class="chips">'
        '<input id="q" placeholder="search ANY ticker or name..." '
        'autocomplete="off" style="min-width:300px">'
        '<span class="muted" id="count"></span></div>'
        '<div id="hint"></div>'
        f'<script>window.UNIVERSE={_json.dumps(universe, separators=(",", ":"))};'
        f'window.BUILT={_json.dumps(sorted(pages))};</script>'
        '<div class="scroll"><table><thead><tr>'
        '<th class="lab">Ticker</th><th class="lab">Name</th>'
        f'<th class="lab">Sector</th>{head}<th class="lab"></th>'
        f'</tr></thead><tbody id="tb">{rows}</tbody></table></div>'
        '<div class="note"><b>Search any of the '
        + f'{len(universe) + len(pages):,}' +
        ' tradeable names above</b>, not just the ones already built. '
        'Pages are pre-built for each session\'s bounce flags; anything else '
        'renders on demand when <code>serve.py</code> is running '
        '(<code>python serve.py --open</code>). MEASURED: the FIRST build after '
        'starting the server takes a couple of minutes -- it loads the modules '
        'and scans the stores cold -- and every one after that is about 20 '
        'seconds. Once built the page is a static file and opens instantly. '
        'From a terminal instead: <code>python stock_profile.py TICKER</code>.'
        '</div>')

    js = """
var q=document.getElementById('q'), tb=document.getElementById('tb'),
    c=document.getElementById('count'), hint=document.getElementById('hint'),
    rows=[].slice.call(tb.rows);
var LIVE = (location.protocol !== 'file:');

function apply(){
  var v=q.value.trim().toUpperCase(), n=0;
  rows.forEach(function(r){
    var s=r.getAttribute('data-k').indexOf(v)>=0;
    r.style.display=s?'':'none'; if(s)n++;});
  c.textContent = n+' of '+rows.length+' built';

  // Nothing built matches -- is it a real ticker we simply have not rendered?
  hint.innerHTML='';
  if(v.length>=1 && n===0){
    var hits=(window.UNIVERSE||[]).filter(function(u){
      return u[0].indexOf(v)>=0 || u[1].toUpperCase().indexOf(v)>=0;
    }).slice(0,8);
    if(hits.length){
      hint.innerHTML='<div class="card" style="margin-top:10px">'
        +'<div class="muted" style="font-size:12px;margin-bottom:6px">'
        +(LIVE
            ? 'Not built yet &mdash; click Build to render it now.'
            : 'Not built yet. This page was opened from a FILE, so it cannot '
              +'build anything &mdash; the browser has no server to ask. '
              +'Start one and the Build button appears here.')+'</div>'
        + hits.map(function(u){
            return LIVE
              ? '<div class="cmdrow"><button class="copy mk" data-t="'+u[0]
                +'">Build '+u[0]+'</button> <b>'+u[0]+'</b> '
                +'<span class="muted">'+u[1]+'</span></div>'
              : '<div class="cmdrow"><b>'+u[0]+'</b> '
                +'<span class="muted">'+u[1]+'</span>'
                +'<code>python stock_profile.py '+u[0]+'</code>'
                +'<button class="copy cp" data-c="python stock_profile.py '
                +u[0]+'">copy</button></div>';
          }).join('')
        + (LIVE ? '' :
            '<div class="cmdrow" style="margin-top:10px">'
            +'<b>Better:</b><code>python serve.py --open</code>'
            +'<button class="copy cp" data-c="python serve.py --open">copy'
            +'</button><span class="muted">then every ticker is one click</span>'
            +'</div>')
        +'</div>';
    } else {
      hint.innerHTML='<div class="note">No ticker matches &ldquo;'+v+'&rdquo;.'
        +'</div>';
    }
  }
}
q.addEventListener('input', apply);

// Build on demand, with a status line rather than a 20-second blank stare.
document.addEventListener('click', function(e){
  // `.mk`, not `a.mk`: this is a <button> now, and the old anchor
  // selector matched nothing, so the button did nothing at all.
  var a=e.target.closest('.mk'); if(!a) return;
  e.preventDefault();
  var tk=a.getAttribute('data-t');
  // MEASURED, not guessed: ~20s once the server is warm, ~200s for the first
  // build after it starts (cold module load + first store scan). The bar is
  // driven off whichever budget applies, and the elapsed clock keeps running
  // past it -- a bar that sticks at 100% still says "working", where a bare
  // spinner after two minutes is indistinguishable from a hang.
  var warm = sessionStorage.getItem('builtOne') ? 20 : 200;
  hint.innerHTML='<div class="loader"><span class="spin"></span>'
    +'<div><b>Building '+tk+'</b>'
    +'<div class="st" id="st">reading bars and fact store...</div>'
    +'<div class="pbar"><i id="pb"></i></div>'
    +'<div class="el" id="el">0s elapsed &middot; usually about '+warm+'s</div>'
    +'</div></div>';
  var msgs=['reading bars and fact store...','scoring against peers...',
            'comparing with its own history...','rendering the sheet...'];
  var i=0, t0=Date.now();
  var tick=setInterval(function(){
    var sec=(Date.now()-t0)/1000;
    var st=document.getElementById('st'), pb=document.getElementById('pb'),
        el=document.getElementById('el');
    if(st && i<msgs.length-1 && sec > (i+1)*warm/4) st.textContent=msgs[++i];
    if(pb) pb.style.width=Math.min(99, sec/warm*100).toFixed(0)+'%';
    if(el) el.textContent=sec.toFixed(0)+'s elapsed · '
      +(sec>warm*1.5 ? 'longer than usual, still working'
                     : 'usually about '+warm+'s');
  }, 500);
  // The server BUILDS the page on this request and returns it when ready.
  fetch(tk+'.html', {cache:'no-store'}).then(function(r){
    clearInterval(tick);
    if(r.ok){ sessionStorage.setItem('builtOne','1'); location.href = tk+'.html'; }
    else { hint.innerHTML='<div class="banner err">Could not build '+tk
             +'. It may have no stored data.</div>'; }
  }).catch(function(){
    clearInterval(tick);
    hint.innerHTML='<div class="banner warn">Build failed &mdash; is '
      +'<code>serve.py</code> running?</div>';
  });
});

// Copy buttons for the file:// path, where the browser cannot build.
document.addEventListener('click', function(e){
  var b=e.target.closest('button.cp'); if(!b) return;
  var txt=b.getAttribute('data-c'), old=b.textContent;
  function done(){ b.textContent='copied'; setTimeout(function(){
      b.textContent=old; }, 1200); }
  if(navigator.clipboard && navigator.clipboard.writeText){
    navigator.clipboard.writeText(txt).then(done, done);
  } else {
    var ta=document.createElement('textarea'); ta.value=txt;
    document.body.appendChild(ta); ta.select();
    try{ document.execCommand('copy'); }catch(err){}
    document.body.removeChild(ta); done();
  }
});

// Say it BEFORE the user searches, not after they have waited.
if(!LIVE){
  var b=document.createElement('div');
  b.className='banner warn';
  b.innerHTML='<b>Opened as a file, so nothing can be built on demand.</b> '
    +'Search works across all names, but rendering a new one needs a server. '
    +'Run <code>python serve.py --open</code> and every ticker becomes one '
    +'click.';
  var h=document.getElementById('hint');
  h.parentNode.insertBefore(b, h);
}

apply();
"""

    html = ui.page("Stock profiles", body,
                   subtitle=f"{len(pages)} built &middot; "
                            f"{len(universe) + len(pages):,} searchable "
                            f"&middot; session <b>{ui.esc(asof)}</b>",
                   active="profiles", depth=1, script=js)
    out = OUT_DIR / "index.html"
    out.write_text(html, encoding="utf-8")
    if verbose:
        print(f"  profiles index: {out}  ({len(pages)} profile(s))")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Per-stock profile pages.")
    ap.add_argument("tickers", nargs="*")
    ap.add_argument("--flags", action="store_true",
                    help="build for today's bounce flags")
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--session", metavar="YYYY-MM-DD",
                    help="build a dated snapshot for a past session")
    a = ap.parse_args()

    tk = [t.upper() for t in a.tickers]
    if a.flags:
        asof = calendar_us.last_closed_session()
        f = config.FLAGS / f"{asof}.parquet"
        if f.exists():
            tk += pd.read_parquet(f)["ticker"].astype(str).tolist()
    if not tk:
        print("give at least one ticker, or --flags")
        return 2

    last = None
    for t in dict.fromkeys(tk):
        try:
            last = build(t, session=a.session)
        except Exception as exc:                                 # noqa: BLE001
            print(f"  {t}: FAILED {exc!r}")
    write_index()
    if a.open and last:
        webbrowser.open(last.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())


def selftest(verbose: bool = True) -> None:
    """Guards for the two ways this file has broken a rendered table.

    THE RAGGED-ROW GUARD is the important one. A `periods` rebinding inside the
    growth-row loop collapsed every line item after the first to a single cell:
    Revenue showed 14 cells, Gross Profit showed 3. Nothing raised, no value was
    wrong -- the table was simply ragged, which is invisible in code and
    unmissable on screen. Only counting cells catches it.
    """
    import re

    import fundamentals as FD

    prof = SETTINGS.load()
    opt = dict(prof["blocks"]["financials"])
    opt["_growth_rows"] = prof["blocks"]["growth"]["rows"]

    # A REQUESTED METRIC WITH NO VALUE MUST STILL RENDER A ROW.
    # ABAT's `safety_score` vanished because `_metric_card` filtered the key
    # out, so the card showed three pillars of four and gave no sign a fourth
    # existed. A missing row is the least visible state a page has.
    card = _metric_card("t", {"quality_score": 40.0},
                        ["quality_score", "safety_score"], None)
    assert "Safety pillar" in card,         "a requested metric with no value was dropped instead of shown as no-data"
    assert "No data for 1 of 2" in card,         "the no-data count is not stated on the card"

    # THE HANDLER MUST REACH THE RENDERED SCRIPT, not merely exist in source.
    # The chart's window buttons shipped dead for a round because a patch script
    # claimed success without checking. Grepping the built HTML is the only
    # version of this check that could have caught it.
    import calendar_us as _cal
    _probe = OUT_DIR / "AAPL.html"
    if _probe.exists():
        _html = _probe.read_text(encoding="utf-8")
        _js = re.search(r"<script>(.*?)</script>", _html, re.S)
        assert _js and "winToggle" in _js.group(1), (
            "the chart window handler is NOT in the rendered <script>; the "
            "buttons will render and do nothing")
        assert 'data-win="MAX"' in _html, "chart windows missing from the page"

    # <thead> is what `.scroll.sticky-x thead th` pins. Without the wrapper the
    # selector matched nothing and the header scrolled away -- which looked
    # correct in the stylesheet for weeks.
    hh = FD.history("AAPL", "Q", 8)
    if not hh.empty:
        html_q = _block_financials("AAPL", hh, opt, "Q")
        assert "<thead>" in html_q and "<tbody>" in html_q,             "financials table lost its <thead>/<tbody>; the header freeze needs them"
        assert 'class="spark"' in html_q,             "the second column is not tagged, so it cannot be frozen"

    checked = 0
    for freq, periods_n in (("A", 12), ("Q", 16)):
        hist = FD.history("AAPL", freq, periods_n)
        if hist.empty:
            continue
        html = _block_financials("AAPL", hist, opt, freq)
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)
        widths = {len(re.findall("<td", r)) for r in rows}
        widths.discard(0)                      # the <th> header row
        assert len(widths) == 1, (
            f"{freq} financials table is RAGGED: cell counts {sorted(widths)}. "
            f"Every row must span the same periods as the header.")
        assert widths.pop() == len(hist.index) + 2, (
            f"{freq} rows do not span all {len(hist.index)} periods (+label,"
            f" +sparkline)")
        # Quarterly must label the 1-period delta QoQ, never YoY.
        if freq == "Q":
            assert "QoQ" in html, "quarterly table must show QoQ, not only YoY"
        else:
            assert "QoQ" not in html, "annual table must not claim QoQ"
        checked += 1

    if verbose:
        print(f"  [stock_profile] {checked} financial table(s) aligned, "
              f"QoQ/YoY labelled per frequency")
