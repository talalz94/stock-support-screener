"""
Report generation: CSV, a self-contained HTML dashboard, and a text digest.

    python report.py                    build for the last closed session
    python report.py --date 2026-08-03

House style, matching the 17 existing reports in the sibling project: script ->
JSON -> HTML with the data inlined as `const D = {...}`, ZERO external references
(no CDN, no fonts, no remote images), inline SVG drawn client-side, CSS-variable
theming.

The chart geometry is computed HERE, in Python, not in the browser. The series is
downsampled and integer-quantised, and every marker's x position is precomputed in
DOWNSAMPLED coordinates -- otherwise the support line drifts off its own touches
once the series is decimated, which looks like a detection bug but is a plotting
bug.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

import numpy as np
import pandas as pd

import calendar_us
import classify
import config
import dataset
import state

# Sparkline budget. Recent history stays full-resolution because that is where the
# bounce is; older history is decimated to an envelope so the parabolic peak and
# the base still read correctly at 2 points per bucket.
TAIL_FULL = 90
HEAD_BUCKET = 8
# Sessions of context to show before the pre-run base, so the chart opens on the
# setup rather than on four years of unrelated history.
CONTEXT_BARS = 60

CSV_COLUMNS = [
    "bucket", "ticker", "name", "close", "price_tier", "size_tier", "market_cap",
    "sector", "age_band", "sessions_since_peak", "peak_date", "peak_high",
    "stage", "ext_atr", "ext_pct", "bars_since_low", "bounce_low",
    "bounce_low_date", "level", "support_grade", "touches_prior",
    "touches_pre_run", "span_days", "dist_low_level", "dist_now_level",
    "run_x", "retrace_of_run", "dd_from_peak", "shape", "liquidity_tier",
    "adv_usd", "bounce_score", "volume_score", "level_Q", "score", "score_band",
    "is_new", "days_on_list", "promoted", "h1_higher_lows", "h1_base_pierce",
    "h1_intraday_reject", "tradingview",
]


def tv_link(ticker: str, exchange: str | None) -> str:
    """TradingView deep link. One click from the list to the chart."""
    pfx = config.EXCHANGE_TV_PREFIX.get(str(exchange or "").upper(), "")
    sym = str(ticker).replace(".", ".")
    return (f"https://www.tradingview.com/chart/?symbol={pfx}:{sym}"
            if pfx else f"https://www.tradingview.com/chart/?symbol={sym}")


# ------------------------------------------------------------------ series prep
def _build_series(high: np.ndarray, low: np.ndarray, close: np.ndarray
                  ) -> tuple[np.ndarray, np.ndarray]:
    """(keep_idx, values) for the sparkline.

    The head is drawn as an envelope -- per bucket, the extreme low and the extreme
    high in their true time order -- so a 3.6x run and the base it started from
    survive decimation. The tail is drawn on closes at full resolution.
    """
    n = len(close)
    if n == 0:
        return np.empty(0, int), np.empty(0, float)
    if n <= TAIL_FULL:
        idx = np.arange(n)
        return idx, close[idx]

    head_end = n - TAIL_FULL
    keep: list[int] = []
    vals: list[float] = []
    for s in range(0, head_end, HEAD_BUCKET):
        e = min(s + HEAD_BUCKET, head_end)
        seg_lo, seg_hi = low[s:e], high[s:e]
        if not np.isfinite(seg_lo).any():
            continue
        i_lo = s + int(np.nanargmin(seg_lo))
        i_hi = s + int(np.nanargmax(seg_hi))
        pair = sorted(((i_lo, float(low[i_lo])), (i_hi, float(high[i_hi]))))
        for i, v in pair:
            if keep and i == keep[-1]:
                continue
            keep.append(i)
            vals.append(v)

    for i in range(head_end, n):
        keep.append(i)
        vals.append(float(close[i]))

    return np.asarray(keep, dtype=int), np.asarray(vals, dtype=float)


def _xmap(keep: np.ndarray, orig_i: int | None) -> int | None:
    """Original bar index -> nearest downsampled position."""
    if orig_i is None or len(keep) == 0:
        return None
    j = int(np.searchsorted(keep, orig_i))
    if j >= len(keep):
        return len(keep) - 1
    if j > 0 and abs(keep[j - 1] - orig_i) <= abs(keep[j] - orig_i):
        return j - 1
    return j


def _focus_window(row: dict, dates: list[str]) -> int:
    """Where the chart should start.

    A full 4-year window compresses the actual setup into a corner: RDW spans
    2.26 to 26.66 over that period, so its base at 7.4 and its bounce to 9.6 end
    up in the bottom third and the thing you are looking for is unreadable.

    Start from the pre-run base with some context, but never later than the
    earliest support touch -- the touch ticks are the evidence for the level, so a
    window that crops them makes the line look unjustified.
    """
    anchors: list[int] = []
    for d in ([row.get("base_date")] + list(_touch_dates(row))):
        if d and str(d) in dates:
            anchors.append(dates.index(str(d)))
    if not anchors:
        return 0
    return max(0, min(anchors) - CONTEXT_BARS)


def _touch_dates(row: dict) -> list[str]:
    """Touch dates, whichever way they arrived.

    This value round-trips through parquet in two different shapes: as a
    stringified list (screen.write_outputs serialises it) or as a numpy array (a
    plain to_parquet of the in-memory frame preserves the list). Never use
    `td or []` on it -- truthiness of an ndarray with more than one element
    raises.
    """
    td = row.get("touch_dates")
    if td is None:
        return []
    if isinstance(td, str):
        s = td.strip()
        if not s or s in ("[]", "nan", "None"):
            return []
        try:
            td = json.loads(s.replace("'", '"'))
        except json.JSONDecodeError:
            return [p.strip(" '\"[]") for p in s.split(",") if p.strip(" '\"[]")]
    if isinstance(td, (list, tuple, set, np.ndarray, pd.Series)):
        return [str(x) for x in list(td) if x is not None and str(x) != "nan"]
    return []


def build_card_series(row: dict, hist: pd.DataFrame) -> dict | None:
    """Quantised series plus every marker's precomputed x position."""
    if hist is None or hist.empty:
        return None
    all_dates = hist["date"].astype(str).tolist()
    w0 = _focus_window(row, all_dates)
    hist = hist.iloc[w0:].reset_index(drop=True)

    h = hist["high"].to_numpy(float)
    l = hist["low"].to_numpy(float)          # noqa: E741
    c = hist["close"].to_numpy(float)
    dates = hist["date"].astype(str).tolist()

    keep, vals = _build_series(h, l, c)
    if len(vals) < 5:
        return None

    # Range comes from the window's TRUE high/low, not from the emitted values.
    # The tail of the series is drawn on closes, so a peak inside the last 90 bars
    # has high > close and its marker would sit above the top of the chart -- which
    # is exactly what happened to RKLB, NVTS, POET, DUOT, MRAM and CPSH. Using the
    # real extremes keeps every marker on canvas, and the peak triangle sitting
    # slightly above the close line is accurate: the peak was an intraday high.
    lo = float(np.nanmin(l))
    hi = float(np.nanmax(h))
    level = row.get("level")
    band_lo, band_hi = row.get("base_lo"), row.get("base_hi")
    for extra in (level, band_lo, band_hi, row.get("bounce_low"),
                  row.get("peak_high"), row.get("close")):
        if extra is not None and extra == extra:
            lo, hi = min(lo, float(extra)), max(hi, float(extra))
    if hi <= lo:
        hi = lo + 1e-6

    q = np.clip(np.round((vals - lo) / (hi - lo) * 1000), 0, 1000).astype(int)

    def date_x(d) -> int | None:
        if not d or str(d) == "nan":
            return None
        try:
            return _xmap(keep, dates.index(str(d)))
        except ValueError:
            return None

    peak_x = date_x(row.get("peak_date"))
    bounce_x = date_x(row.get("bounce_low_date"))
    base_x = date_x(row.get("base_date"))

    touch_xs: list[int] = []
    for d in _touch_dates(row):
        x = date_x(d)
        if x is not None:
            touch_xs.append(x)

    out = {
        "s": q.tolist(), "lo": round(lo, 4), "hi": round(hi, 4),
        "n": len(q),
        "peak_x": peak_x, "peak_v": _r(row.get("peak_high")),
        "bounce_x": bounce_x, "bounce_v": _r(row.get("bounce_low")),
        "base_x": base_x,
        "level": _r(level),
        "band_lo": _r(band_lo), "band_hi": _r(band_hi),
        "touch_x": touch_xs,
        # The support line spans only its evidence, then extends to the right edge
        # so the current test is visibly against the same line.
        "line_x0": min(touch_xs) if touch_xs else (base_x if base_x is not None else 0),
        "line_x1": len(q) - 1,
        "first_date": dates[0], "last_date": dates[-1],
    }
    return out


def base_rate_for(row: dict, br: pd.DataFrame) -> dict | None:
    """Historical outcome for setups sharing this row's characteristics.

    This is what makes the daily list self-evidencing: rather than leaving you to
    guess whether today's names are the good kind, each card carries what
    comparable setups actually did in the backtest. Most specific match wins, and
    anything with fewer than 8 historical instances is not quoted at all.
    """
    if br is None or br.empty:
        return None
    tries = [
        (["bucket", "stage"], f"{row.get('bucket')}|{row.get('stage')}"),
        (["stage", "price_tier"], f"{row.get('stage')}|{row.get('price_tier')}"),
        (["stage"], str(row.get("stage"))),
        (["bucket"], str(row.get("bucket"))),
        (["price_tier"], str(row.get("price_tier"))),
    ]
    for keys, value in tries:
        k = "|".join(keys)
        hit = br[(br["keys"] == k) & (br["value"] == value)]
        if not hit.empty:
            h = hit.iloc[0]
            return {"on": value, "n": int(h["n"]),
                    "mean": _r(h["mean_ret"], 4),
                    "median": _r(h["median_ret"], 4),
                    "win": _r(h["win_rate"], 3),
                    "exit": str(h["exit"])}
    return None


def _r(v, nd: int = 4):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else round(f, nd)


# ================================================== server-side HTML rendering
# The cards are built HERE, in Python, not in the browser.
#
# They used to be assembled by JavaScript from an inlined `const D`. That renders
# a completely blank page in any viewer that does not execute scripts -- a preview
# pane, a static snapshot, an email client, print-to-PDF. The report is the whole
# deliverable, so it must display with JS disabled. JS now only REARRANGES nodes
# that already exist (the group-by control), which is progressive enhancement
# rather than a hard dependency.

def _esc(s) -> str:
    return (str("" if s is None else s)
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _f2(v, nd: int = 2) -> str:
    return "&ndash;" if v is None else f"{float(v):.{nd}f}"


def _pct(v, nd: int = 1) -> str:
    return "&ndash;" if v is None else f"{float(v) * 100:.{nd}f}%"


def _spct(v, nd: int = 1) -> str:
    """Signed percent, for values where direction matters."""
    if v is None:
        return "&ndash;"
    return f"{'+' if float(v) >= 0 else ''}{float(v) * 100:.{nd}f}%"


def _money(v) -> str:
    if v is None:
        return "&ndash;"
    v = float(v)
    a = abs(v)
    for cut, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= cut:
            return f"{v / cut:.2f}{suf}" if cut >= 1e9 else f"{v / cut:.1f}{suf}"
    return f"{v:.0f}"


BUCKET_CLASS = {"EARLY": "early", "PRIME": "prime", "SPEC": "spec",
                "WATCH": "watch", "LATE": "late"}


def svg_html(ch: dict | None) -> str:
    """Inline SVG sparkline. Geometry already resolved in downsampled coords."""
    if not ch or not ch.get("s") or len(ch["s"]) < 2:
        return ""
    W, H, PL, PR, PT, PB = 600, 132, 4, 46, 9, 15
    s = ch["s"]
    n = len(s)
    iw, ih = W - PL - PR, H - PT - PB
    lo, hi = float(ch["lo"]), float(ch["hi"])
    span = (hi - lo) or 1e-9

    def X(i):
        return PL + (i / (n - 1)) * iw

    def Yq(q):
        return PT + (1 - q / 1000) * ih

    def Yp(p):
        return None if p is None else Yq(((float(p) - lo) / span) * 1000)

    out = [f'<svg class="spark" viewBox="0 0 {W} {H}" preserveAspectRatio="none" '
           f'role="img" aria-label="price history with support level">']

    if ch.get("band_lo") is not None and ch.get("band_hi") is not None:
        y1, y2 = Yp(ch["band_hi"]), Yp(ch["band_lo"])
        out.append(f'<rect class="bandr" x="{PL}" y="{y1:.1f}" width="{iw}" '
                   f'height="{max(1.0, y2 - y1):.1f}"></rect>')
    for q in (0, 500, 1000):
        out.append(f'<line class="gl" x1="{PL}" x2="{PL + iw}" '
                   f'y1="{Yq(q):.1f}" y2="{Yq(q):.1f}"></line>')

    pts = " ".join(f"{X(i):.1f},{Yq(q):.1f}" for i, q in enumerate(s))
    out.append(f'<polyline class="sl" points="{pts}"></polyline>')

    if ch.get("level") is not None:
        y = Yp(ch["level"])
        x0, x1 = X(ch.get("line_x0") or 0), X(ch["line_x1"])
        out.append(f'<line class="lvl" x1="{x0:.1f}" x2="{x1:.1f}" '
                   f'y1="{y:.1f}" y2="{y:.1f}"></line>')
        out.append(f'<text class="axl" x="{PL + iw + 4:.1f}" y="{y + 3:.1f}">'
                   f'{_f2(ch["level"])}</text>')
        for tx in (ch.get("touch_x") or []):
            out.append(f'<line class="tick" x1="{X(tx):.1f}" x2="{X(tx):.1f}" '
                       f'y1="{y - 3.5:.1f}" y2="{y + 3.5:.1f}"></line>')

    if ch.get("peak_x") is not None and ch.get("peak_v") is not None:
        x, y = X(ch["peak_x"]), Yp(ch["peak_v"])
        out.append(f'<polygon class="pk" points="{x - 4:.1f},{y - 8:.1f} '
                   f'{x + 4:.1f},{y - 8:.1f} {x:.1f},{y - 1.5:.1f}"></polygon>')
        out.append(f'<text class="axl" x="{PL + iw + 4:.1f}" y="{y - 3:.1f}">'
                   f'{_f2(ch["peak_v"])}</text>')
    if ch.get("bounce_x") is not None and ch.get("bounce_v") is not None:
        out.append(f'<circle class="bn" cx="{X(ch["bounce_x"]):.1f}" '
                   f'cy="{Yp(ch["bounce_v"]):.1f}" r="3.2"></circle>')
    out.append(f'<circle class="cur" cx="{X(n - 1):.1f}" '
               f'cy="{Yq(s[-1]):.1f}" r="2.8"></circle>')
    out.append(f'<text class="axl" x="{PL}" y="{H - 3}">'
               f'{_esc(ch.get("first_date"))}</text>')
    out.append(f'<text class="axl" x="{PL + iw:.1f}" y="{H - 3}" '
               f'text-anchor="end">{_esc(ch.get("last_date"))}</text>')
    out.append("</svg>")
    return "".join(out)


GROUP_KEYS = ("bucket", "price_tier", "age_band", "size_tier", "stage",
              "support_grade", "liquidity_tier", "shape", "sector")


def card_html(c: dict) -> str:
    """One candidate card. Data attributes carry every tag so JS can regroup
    by moving this node instead of rebuilding its markup."""
    bc = BUCKET_CLASS.get(c.get("bucket"), "watch")
    chips = []
    if c.get("is_new"):
        chips.append('<span class="chip new">NEW</span>')
    else:
        chips.append(f'<span class="chip">day {c.get("days_on_list", 1)}</span>')
    if c.get("promoted"):
        chips.append('<span class="chip promo">PROMOTED</span>')
    chips.append(f'<span class="chip b" style="color:var(--{bc})">'
                 f'{_esc(c.get("bucket"))}</span>')
    for t in (c.get("stage"), c.get("price_tier"), c.get("age_band"),
              c.get("size_tier"), c.get("liquidity_tier"),
              f"grade {c.get('support_grade')}", c.get("shape")):
        if t:
            chips.append(f'<span class="chip">{_esc(t)}</span>')
    if c.get("h1_pierce"):
        chips.append('<span class="chip warn">hourly pierce</span>')

    rows = [
        ("score", f'<b>{_f2(c.get("score"), 1)}</b> {_esc(c.get("score_band"))}'),
        ("peak", f'{_f2(c.get("peak_v"))} &middot; '
                 f'{c.get("sessions_since_peak", 0)}d ago'),
        ("run / retrace", f'{_f2(c.get("run_x"))}x &middot; {_f2(c.get("retrace"))}'),
        ("drawdown", _pct(c.get("dd"))),
        ("support", f'{_f2(c.get("level"))} &middot; {c.get("touches_prior", 0)} '
                    f'touches ({c.get("touches_pre_run", 0)} pre-run)'),
        ("span", f'{c.get("span_days", 0)}d'),
        ("low vs level", _spct(c.get("dist_low"))),
        ("bounce low", f'{_f2(c.get("bounce_v"))} &middot; '
                       f'{c.get("bars_since_low", 0)}d ago'),
        ("extension", f'{_f2(c.get("ext_atr"))} ATR &middot; '
                      f'{_spct(c.get("ext_pct"))}'),
        ("B / V / Q", f'{_f2(c.get("B"), 0)} / {_f2(c.get("V"))} / '
                      f'{_f2(c.get("Q"))}'),
        ("mkt cap / ADV", f'{_money(c.get("market_cap"))} / '
                          f'{_money(c.get("adv_usd"))}'),
    ]
    if c.get("h1_higher_lows") is not None:
        extra = (f' &middot; close-off-high {_pct(c.get("h1_reject"), 0)}'
                 if c.get("h1_reject") is not None else "")
        rows.append(("hourly higher lows",
                     f'{_f2(c.get("h1_higher_lows"), 0)}{extra}'))

    br = c.get("base_rate")
    brhtml = ""
    if br:
        col = "muted" if br.get("mean") is None else (
            "up" if br["mean"] > 0 else "down")
        brhtml = (
            f'<div class="br"><div class="brh">historically, '
            f'<b>{_esc(br["on"])}</b> setups (n={br["n"]}, exit '
            f'<code>{_esc(br["exit"])}</code>)</div><div class="brv">'
            f'<span>mean <b style="color:var(--{col})">'
            f'{_spct(br.get("mean"))}</b></span>'
            f'<span>median {_spct(br.get("median"))}</span>'
            f'<span>win {_pct(br.get("win"), 0)}</span></div></div>')

    # Sentiment badge. READ-ONLY: it never touches the score, the bucket or any
    # gate. Until `backtest.py --sentiment` shows the signal is worth something,
    # folding it into the composite would just add an unmeasured term to a number
    # that already carries one weight the data does not support (W_SUPPORT=20 on
    # a grade whose measured edge was +0.04%).
    sn = c.get("sentiment")
    snhtml = ""
    if sn:
        if sn.get("has_news"):
            s = sn.get("sent")
            col = "muted" if s is None else ("up" if s > 0.05 else
                                             ("down" if s < -0.05 else "muted"))
            band = sn.get("band") or ""
            head = f' &middot; {_esc(band)}' if band else ""
            tone = "-" if s is None else f"{s:+.2f}"
            z = sn.get("z")
            burst = "-" if z is None else f"{z:+.1f}"
            hd = (f'<div class="brh">{_esc(sn["hd"])}</div>'
                  if sn.get("hd") else "")
            snhtml = (
                f'<div class="br"><div class="brh">news &middot; '
                f'<b>{_esc(sn.get("event") or "-")}</b>{head}</div>'
                f'<div class="brv">'
                f'<span>tone <b style="color:var(--{col})">{tone}</b></span>'
                f'<span>{int(sn.get("n") or 0)} in 30d</span>'
                f'<span>burst {burst}</span></div>{hd}</div>')
        else:
            # "No news" is a STATE, not a missing value, and it is the normal case
            # for micro-caps -- measured, 13% of flags have zero coverage in 30
            # days. Showing it explicitly stops a silent name reading as neutral.
            snhtml = ('<div class="br"><div class="brh">news &middot; '
                      '<b>NO COVERAGE</b> in 30d</div></div>')

    maxpts = {"support": 20, "bounce": 20, "retrace": 12, "run": 12, "volume": 9,
              "tightness": 7, "stage": 6, "liquidity": 6, "size": 8}
    bars = ""
    if c.get("parts"):
        inner = []
        for k, mx in maxpts.items():
            v = c["parts"].get(k)
            if v is None:
                continue
            w = max(0.0, min(100.0, (float(v) / mx) * 100))
            inner.append(f'<div class="brow"><span class="bl">{k}</span>'
                         f'<span class="bt"><span class="bf" '
                         f'style="width:{w:.0f}%"></span></span>'
                         f'<span class="bv">{float(v):.1f}</span></div>')
        if inner:
            bars = f'<div class="bars">{"".join(inner)}</div>'

    data = " ".join(f'data-{k.replace("_", "-")}="{_esc(c.get(k) or "(none)")}"'
                    for k in GROUP_KEYS)
    trs = "".join(f'<tr><td class="k">{k}</td><td class="v">{v}</td></tr>'
                  for k, v in rows)

    return (
        f'<div class="card" {data} data-score="{c.get("score") or 0}">'
        f'<div><div class="chd">'
        f'<span class="tkr"><a href="{_esc(c.get("tv"))}" target="_blank" '
        f'rel="noopener">{_esc(c.get("ticker"))}</a></span>'
        f'<span class="px num">{_f2(c.get("close"))}</span>'
        f'<span class="cname">{_esc(c.get("name"))}'
        f'{" &middot; " + _esc(c.get("sector")) if c.get("sector") else ""}</span>'
        f'</div><div class="chips">{"".join(chips)}</div>'
        f'{svg_html(c.get("chart"))}</div>'
        f'<div><table class="m">{trs}</table>{snhtml}{brhtml}{bars}</div></div>')


def sections_html(cards: list[dict], key: str = "bucket") -> str:
    """Sections for the default grouping, fully rendered."""
    if not cards:
        return ('<div class="empty">No candidates passed for this session.'
                '<br><br>Not a bug: with high-conviction thresholds on ~2,600 '
                'names, quiet sessions legitimately return nothing. Run '
                '<code>python status.py</code> to confirm the data is current.'
                '</div>')
    order = list(config.BUCKET_ORDER) if key == "bucket" else []
    groups: dict[str, list[dict]] = {}
    for c in cards:
        groups.setdefault(str(c.get(key) or "(none)"), []).append(c)
    for g in groups:
        if g not in order:
            order.append(g)

    help_txt = {
        "PRIME": "Structure complete and the turn has started. Best historical "
                 "mean of any bucket.",
        "SPEC": "Real pattern, thin tape or a weaker level. Size accordingly.",
        "WATCH": "Passed, but weaker on one dimension.",
        "EARLY": "At the level, not turned yet. The only bucket with a NEGATIVE "
                 "historical mean - anticipating the turn lost to waiting for it.",
        "LATE": "Already ran. Shown so you know you are late BEFORE you buy.",
    }
    out = []
    for g in order:
        lst = groups.get(g)
        if not lst:
            continue
        h = (f'<span class="help">{_esc(help_txt[g])}</span>'
             if key == "bucket" and g in help_txt else "")
        colour = BUCKET_CLASS.get(g, "ink") if key == "bucket" else "ink"
        out.append(f'<div class="sect" data-group="{_esc(g)}">'
                   f'<h2 style="color:var(--{colour})">{_esc(g)}</h2>'
                   f'<span class="cnt">{len(lst)}</span>{h}</div>')
        out.extend(card_html(c) for c in lst)
    return "".join(out)


def tallies_html(cards: list[dict], counts: dict) -> str:
    n = len(cards)
    out = [f'<span class="tally"><b>{n}</b> candidate{"" if n == 1 else "s"}</span>']
    for b in config.BUCKET_ORDER:
        if counts.get(b):
            out.append(f'<span class="tally" style="border-color:var(--'
                       f'{BUCKET_CLASS.get(b, "watch")})">{b} '
                       f'<b style="color:var(--{BUCKET_CLASS.get(b, "watch")})">'
                       f'{counts[b]}</b></span>')
    out.append(f'<span class="tally">NEW '
               f'<b>{sum(1 for c in cards if c.get("is_new"))}</b></span>')
    return "".join(out)


def near_miss_html(rows: list[dict]) -> str:
    if not rows:
        return ""
    trs = "".join(
        f'<tr><td><b>{_esc(r.get("ticker"))}</b></td><td>{_f2(r.get("close"))}</td>'
        f'<td>{_money(r.get("adv_usd"))}</td><td class="g">{_esc(r.get("gate"))}</td>'
        f'<td>{_f2(r.get("run_x"))}x</td><td>{_f2(r.get("retrace_of_run"))}</td>'
        f'<td>{r.get("sessions_since_peak") if r.get("sessions_since_peak") is not None else "&ndash;"}</td>'
        f'<td>{r.get("touches_prior") if r.get("touches_prior") is not None else "&ndash;"}</td>'
        f'</tr>' for r in rows)
    return (f'<div class="sect"><h2>Near miss &middot; large &amp; liquid</h2>'
            f'<span class="cnt">{len(rows)}</span><span class="help">Failed exactly '
            f'one gate. On a day when every flag is a micro-cap, the bigger names '
            f'you would actually trade show up here.</span></div>'
            f'<div class="nm"><table><thead><tr><th>ticker</th><th>close</th>'
            f'<th>ADV</th><th>failed gate</th><th>run</th><th>retrace</th>'
            f'<th>since peak</th><th>touches</th></tr></thead>'
            f'<tbody>{trs}</tbody></table></div>')


DROP_TEXT = {
    "support_broken": ("thesis dead - stop watching", "late"),
    "went_extended": ("ran without you", "spec"),
    "bounce_stalled": ("stalled, not broken", "watch"),
    "no_data": ("no data", "watch"), "unknown": ("unknown", "watch"),
}


def dropped_html(rows: list[dict]) -> str:
    if not rows:
        return ""
    trs = []
    for r in rows:
        txt, col = DROP_TEXT.get(r.get("reason"), (r.get("reason"), "watch"))
        fc, lc = r.get("first_close"), r.get("last_close")
        ch = (lc / fc - 1) if (fc and lc) else None
        ccol = "muted" if ch is None else ("up" if ch >= 0 else "down")
        trs.append(
            f'<tr><td><b>{_esc(r.get("ticker"))}</b></td>'
            f'<td style="color:var(--{col})">{_esc(txt)}</td>'
            f'<td>{_esc(r.get("last_seen"))}</td><td>{r.get("days_on_list")}</td>'
            f'<td>{_f2(fc)}</td><td>{_f2(lc)}</td>'
            f'<td style="color:var(--{ccol})">{_spct(ch)}</td></tr>')
    return (f'<div class="sect"><h2>Dropped off</h2>'
            f'<span class="cnt">{len(rows)}</span><span class="help">On a previous '
            f'list, not on this one - and why.</span></div>'
            f'<div class="nm"><table><thead><tr><th>ticker</th><th>reason</th>'
            f'<th>last seen</th><th>days on list</th><th>first close</th>'
            f'<th>last close</th><th>change</th></tr></thead>'
            f'<tbody>{"".join(trs)}</tbody></table></div>')


def sessions_html(asof: str) -> str:
    """Links to every session's report, newest first."""
    files = sorted(config.FLAGS.glob("*.parquet"), reverse=True)
    if not files:
        return ""
    opts = []
    for f in files[:60]:
        d = f.stem
        rep = config.REPORTS_BOUNCE / f"{d}.html"
        if not rep.exists():
            continue
        try:
            n = len(pd.read_parquet(f, columns=["ticker"]))
        except Exception:                                  # noqa: BLE001
            n = "?"
        sel = " selected" if d == asof else ""
        opts.append(f'<option value="{d}.html"{sel}>{d} '
                    f'&nbsp;({n})</option>')
    if not opts:
        return ""
    return (f'<label class="lbl" for="sess">session</label>'
            f'<select id="sess">{"".join(opts)}</select>')


# ------------------------------------------------------------------ near misses
def near_misses(asof: str, max_rows: int = 12) -> list[dict]:
    """Large/liquid names that failed EXACTLY ONE gate.

    Directly serves the stated bias toward bigger names: on a day when every flag
    is a micro-cap, this is where the names actually worth trading show up. Free,
    because `failed_gates` is already recorded for every rejected ticker.
    """
    p = config.REJECTS / f"{asof}.parquet"
    if not p.exists():
        return []
    try:
        r = pd.read_parquet(p)
    except Exception:                                  # noqa: BLE001
        return []
    if r.empty or "failed_gates" not in r:
        return []

    r = r[r["failed_gates"].notna()].copy()
    r["n_failed"] = r["failed_gates"].map(
        lambda s: len([x for x in str(s).split(",") if x]))
    one = r[(r["n_failed"] == 1) & (r.get("adv_usd", 0) >= 20e6)]
    if one.empty:
        return []
    one = one.sort_values("adv_usd", ascending=False).head(max_rows)

    keep = ["ticker", "close", "adv_usd", "failed_gates", "sessions_since_peak",
            "run_x", "retrace_of_run", "level", "touches_prior", "score"]
    rows = []
    for _, x in one.iterrows():
        d = {k: _r(x.get(k)) if k not in ("ticker", "failed_gates")
             else x.get(k) for k in keep if k in one.columns}
        d["ticker"] = str(x["ticker"])
        d["gate"] = str(x["failed_gates"])
        rows.append(d)
    return rows


# ------------------------------------------------------------------ full universe
# Verdict codes travel to the browser as ints because they are also the sort key
# and the filter key: 2 flagged, 1 measured in full, 0 dismissed by the panel pass.
_V_FLAG, _V_PATTERN, _V_PANEL = 2, 1, 0

_REASON_TEXT = {
    "NEAR_HIGHS":    "within reach of its 250d high - no drawdown to bounce from",
    "ILLIQUID":      "20d dollar volume below the floor",
    "SHORT_HISTORY": "not enough bars to measure a run",
    "PENNY":         "price below the floor",
    "FLAT_RANGE":    "250d range too narrow to have a level",
    "STALE_DATA":    "no bar for this session",
    "NO_TRADES":     "too few trades a day",
    "SUSPECT_SPLIT": "unadjusted split suspected - metrics not trusted",
}


def universe_rows(asof: str, flags: pd.DataFrame) -> tuple[list, dict]:
    """Every name the screen looked at, with where it stopped and why.

    The panel tier genuinely has no pattern metrics -- it never reached the pattern
    math -- so those cells go out as null and render as a dash. Sending 0.0 would
    make an unexamined name sort as a measured one holding the worst possible
    score, which is the "not reported is not zero" failure this project keeps
    hitting.
    """
    p = config.REJECTS / f"{asof}.parquet"
    try:
        rej = pd.read_parquet(p) if p.exists() else pd.DataFrame()
    except Exception:                                  # noqa: BLE001
        rej = pd.DataFrame()

    frames = []
    if flags is not None and not flags.empty:
        f = flags.copy()
        f["_verdict"], f["_reason"] = _V_FLAG, ""
        frames.append(f)
    if not rej.empty:
        r = rej.copy()
        if "tier" in r.columns:
            is_panel = r["tier"].astype(str) == "panel"
        else:
            # Files written before the tier column existed hold the pattern tier
            # only; run_x is the marker that a row reached the pattern math.
            is_panel = (r["run_x"].isna() if "run_x" in r.columns
                        else pd.Series(False, index=r.index))
        r["_verdict"] = np.where(is_panel, _V_PANEL, _V_PATTERN)
        r["_reason"] = (r["reject_code"] if "reject_code" in r.columns
                        else pd.Series("", index=r.index)).fillna("")
        frames.append(r)
    if not frames:
        return [], {}

    d = pd.concat(frames, ignore_index=True, sort=False)
    d = d.drop_duplicates(subset=["ticker"], keep="first")

    # `n_bars` means two different things depending on the tier: the panel rows
    # carry the ticker's whole history, while a pattern row carries only the bars
    # loaded into the analysis window (COIN read 896 against 2,531 available). One
    # column cannot hold both meanings, so the whole column is taken from the panel,
    # where it consistently means "sessions of history this ticker has".
    try:
        import bars as _bars
        _ps = _bars.load_panel_stats()
        if not _ps.empty and "n_bars" in _ps.columns:
            hist = dict(zip(_ps["ticker"].astype(str),
                            pd.to_numeric(_ps["n_bars"], errors="coerce")))
            d["n_bars"] = d["ticker"].astype(str).map(hist).fillna(
                pd.to_numeric(d.get("n_bars"), errors="coerce"))
    except Exception:                                  # noqa: BLE001
        pass                                           # keep whatever the rows hold

    def col(name):
        return d[name] if name in d.columns else pd.Series(np.nan, index=d.index)

    def num(v):
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return None
        return None if not np.isfinite(fv) else round(fv, 4)

    # A pattern metric is real only if the math reached the line that assigns it.
    # `screen._blank()` seeds every one of them with 0.0 and the early returns keep
    # that seed, so a name rejected at stage 1 carries run_x 0.0 and score 0.0 that
    # were never computed. Displayed as 0.00 they sort as "measured, and the worst
    # on the page" rather than "never measured" -- the same not-reported-is-not-zero
    # error this project has now hit in five separate places. The stage at which a
    # field is assigned is the only thing that separates the two.
    # dd_from_peak and retrace_of_run are BOTH returned by pattern.retrace_metrics,
    # which runs after the stage-2 gate -- so dd_from_peak is real from 3, not 2.
    # Caught because AAON showed a 2.06x run with a 0.0 drawdown while trading 47%
    # below its high, which cannot both be true.
    VALID_FROM = {"run_x": 2, "dd_from_peak": 3, "retrace_of_run": 3,
                  "touches_prior": 4, "score": 6}
    _stage = pd.to_numeric(col("stage"), errors="coerce")
    _stage = _stage.mask(d["_verdict"] == _V_FLAG, 99)   # a flag cleared every gate

    def metric(name):
        v = pd.to_numeric(col(name), errors="coerce")
        need = VALID_FROM.get(name)
        v = v if need is None else v.where(_stage >= need)
        if name == "touches_prior":
            # No level was selected, so there is nothing for a touch to be counted
            # against. LEVEL_TOO_FEW_TOUCHES is the opposite case -- a level exists
            # and the count is genuinely low -- and must keep its number.
            v = v.where(d["_reason"].astype(str) != "NO_LEVEL_NEAR_LOW")
        return v

    out = []
    for t, verdict, reason, close, adv, hi, nb, sc, rx, dd, rt, tp in zip(
            d["ticker"].astype(str), d["_verdict"], d["_reason"].astype(str),
            col("close"), col("adv_usd"), col("pct_of_250d_high"), col("n_bars"),
            metric("score"), metric("run_x"), metric("dd_from_peak"),
            metric("retrace_of_run"), metric("touches_prior")):
        out.append([t, int(verdict), reason, num(close), num(adv), num(hi),
                    num(nb), num(sc), num(rx), num(dd), num(rt), num(tp)])

    out.sort(key=lambda r: (-r[1], -(r[7] if r[7] is not None else -1), r[0]))
    n_flag = sum(1 for r in out if r[1] == _V_FLAG)
    n_pat = sum(1 for r in out if r[1] == _V_PATTERN)
    counts = {"total": len(out), "flagged": n_flag,
              "measured": n_flag + n_pat,
              "panel": sum(1 for r in out if r[1] == _V_PANEL)}
    by_reason: dict[str, int] = {}
    for r in out:
        if r[2]:
            by_reason[r[2]] = by_reason.get(r[2], 0) + 1
    counts["by_reason"] = dict(sorted(by_reason.items(), key=lambda kv: -kv[1]))
    return out, counts


def universe_html(rows: list, counts: dict) -> str:
    """The whole universe as one sortable table, rendered in the browser.

    This is the ONE place the page inlines its data instead of server-rendering it,
    and the reason is the opposite of the one that removed the cards blob: at 5,400
    rows the JSON is roughly a quarter of the HTML the same table would need, and
    sorting a numeric array beats re-sorting 5,400 DOM nodes. Tickers are the only
    strings in the payload and they are escaped for the script context.
    """
    if not rows:
        return ""
    blob = (json.dumps(rows, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026").replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029"))
    chips = "".join(
        f'<button class="uchip" data-reason="{_esc(k)}" '
        f'title="{_esc(_REASON_TEXT.get(k, ""))}">{_esc(k)}'
        f'<span class="un">{v:,}</span></button>'
        for k, v in counts.get("by_reason", {}).items())
    notes = "".join(f'<div><code>{_esc(k)}</code> &mdash; {_esc(v)}</div>'
                    for k, v in _REASON_TEXT.items()
                    if k in counts.get("by_reason", {}))
    head = (f'<div class="sect"><h2>Every stock screened</h2>'
            f'<span class="cnt">{counts["total"]:,}</span><span class="help">The '
            f'whole panel, not just the flags. <b>{counts["measured"]:,}</b> reached '
            f'the pattern math and were measured in full; <b>{counts["panel"]:,}</b> '
            f'were dismissed in one vectorized pass and carry no pattern metrics, '
            f'which is why those cells are blank rather than zero. A dismissal is a '
            f'stated reason, not a gap in the data.</span></div>')
    controls = (
        f'<div class="uni"><div class="ubar">'
        f'<input id="usearch" type="search" placeholder="filter tickers..." autocomplete="off">'
        f'<button class="uchip on" data-v="all">all<span class="un">{counts["total"]:,}</span></button>'
        f'<button class="uchip" data-v="2">flagged<span class="un">{counts["flagged"]:,}</span></button>'
        f'<button class="uchip" data-v="1">measured, no flag'
        f'<span class="un">{counts["measured"] - counts["flagged"]:,}</span></button>'
        f'<button class="uchip" data-v="0">dismissed by panel'
        f'<span class="un">{counts["panel"]:,}</span></button></div>'
        f'<div class="ubar ubar2"><span class="ulab">stopped at</span>{chips}'
        f'<button class="uchip clr" id="uclear">clear</button></div>')
    table = (
        '<div class="uwrap"><table id="utab"><thead><tr>'
        '<th data-c="0" class="s">ticker</th><th data-c="1" class="s">outcome</th>'
        '<th data-c="2" class="s">stopped at</th><th data-c="3" class="s n">close</th>'
        '<th data-c="4" class="s n">ADV</th><th data-c="5" class="s n">% of 250d high</th>'
        '<th data-c="6" class="s n">bars</th><th data-c="7" class="s n">score</th>'
        '<th data-c="8" class="s n">run</th><th data-c="9" class="s n">drawdown</th>'
        '<th data-c="10" class="s n">retrace</th><th data-c="11" class="s n">touches</th>'
        '</tr></thead><tbody id="ubody"></tbody></table></div>'
        '<div class="ufoot"><span id="ucount"></span>'
        '<button id="umore" class="umore">show all matches</button></div>'
        f'<div class="ukey">{notes}</div></div>')
    return head + controls + table + _UNIVERSE_JS.replace("__DATA__", blob)


# Kept out of the f-string above so the JS braces need no doubling -- doubling them
# is how a working script turns into a syntax error on the next edit.
_UNIVERSE_JS = """<script>
(function(){
const U=__DATA__;
const CAP=300; let cap=CAP, vf='all', rf=new Set(), q='';
let sc=1, sd=-1;   // sort column / direction: outcome, best first
const $=s=>document.getElementById(s);
const money=v=>v==null?'&ndash;':v>=1e9?(v/1e9).toFixed(1)+'B':v>=1e6?(v/1e6).toFixed(0)+'M':v>=1e3?(v/1e3).toFixed(0)+'K':v.toFixed(0);
const f2=(v,n)=>v==null?'&ndash;':v.toFixed(n===undefined?2:n);
const pc=v=>v==null?'&ndash;':(v*100).toFixed(0)+'%';
const VN={2:'<b class="vf">FLAGGED</b>',1:'<span class="vm">measured</span>',0:'<span class="vp">dismissed</span>'};
// Ties break on score, then ticker. Alphabetical alone put ABAT above COIN inside
// the flagged group, which buries the best name on the page under the first one.
function tie(a,b){
  var x=(a[7]==null?-1:a[7]), y=(b[7]==null?-1:b[7]);
  if(x!==y) return y-x;
  return a[0]<b[0]?-1:1;
}
function match(r){
  if(vf!=='all'&&r[1]!==+vf) return false;
  if(rf.size&&!rf.has(r[2])) return false;
  if(q&&r[0].toLowerCase().indexOf(q)<0) return false;
  return true;
}
function render(){
  const m=U.filter(match);
  const s=m.slice().sort(function(a,b){
    var x=a[sc],y=b[sc];
    if(x==null&&y==null) return tie(a,b);
    if(x==null) return 1; if(y==null) return -1;   // blanks last, both directions
    if(x===y) return tie(a,b);
    return (x>y?1:-1)*sd;
  });
  const show=s.slice(0,cap);
  $('ubody').innerHTML=show.map(function(r){return '<tr><td><b>'+r[0]+'</b></td><td>'+VN[r[1]]+'</td>'+
    '<td class="g">'+(r[2]||'&ndash;')+'</td><td>'+f2(r[3])+'</td>'+
    '<td>'+money(r[4])+'</td><td>'+pc(r[5])+'</td><td>'+(r[6]==null?'&ndash;':r[6])+'</td>'+
    '<td>'+f2(r[7])+'</td><td>'+(r[8]==null?'&ndash;':f2(r[8])+'x')+'</td>'+
    '<td>'+pc(r[9])+'</td><td>'+f2(r[10])+'</td><td>'+(r[11]==null?'&ndash;':r[11])+'</td></tr>';}).join('');
  $('ucount').innerHTML=show.length<m.length
    ? 'showing '+show.length.toLocaleString()+' of '+m.length.toLocaleString()+' matches'
    : m.length.toLocaleString()+(m.length===1?' match':' matches');
  $('umore').style.display=show.length<m.length?'':'none';
}
$('usearch').addEventListener('input',function(e){q=e.target.value.trim().toLowerCase();cap=CAP;render();});
$('umore').addEventListener('click',function(){cap=U.length;render();});
$('uclear').addEventListener('click',function(){
  rf.clear();document.querySelectorAll('.uchip[data-reason]').forEach(function(b){b.classList.remove('on');});cap=CAP;render();});
document.querySelectorAll('.uchip[data-v]').forEach(function(b){b.addEventListener('click',function(){
  vf=b.dataset.v;cap=CAP;
  document.querySelectorAll('.uchip[data-v]').forEach(function(o){o.classList.toggle('on',o===b);});render();});});
document.querySelectorAll('.uchip[data-reason]').forEach(function(b){b.addEventListener('click',function(){
  const k=b.dataset.reason;
  if(rf.has(k)){rf.delete(k);b.classList.remove('on');} else {rf.add(k);b.classList.add('on');}
  cap=CAP;render();});});
document.querySelectorAll('#utab th.s').forEach(function(th){th.addEventListener('click',function(){
  const c=+th.dataset.c;
  if(c===sc){sd=-sd;} else {sc=c;sd=(c===0||c===2)?1:-1;}
  document.querySelectorAll('#utab th').forEach(function(o){o.classList.remove('asc','desc');});
  th.classList.add(sd>0?'asc':'desc');
  cap=CAP;render();});});
render();
})();
</script>"""


# ------------------------------------------------------------------ payload
def _sentiment_lookup(asof: str) -> dict[str, dict] | None:
    """{ticker: badge} from the score table, or None if the module is not in use.

    Reads the STORED score rows rather than recomputing: the sentiment screener
    runs on its own interval and its output for `asof` is already a fact. None
    (not {}) when nothing is stored, so card_html can tell "module not installed"
    apart from "installed and this name has no coverage".
    """
    try:
        import scores
    except Exception:                                      # noqa: BLE001
        return None
    try:
        rows = scores.read(module="sentiment", start=asof, end=asof)
    except Exception:                                      # noqa: BLE001
        return None
    if rows.empty:
        return None

    out: dict[str, dict] = {}
    for t, sub in rows.groupby("ticker"):
        num = dict(zip(sub["metric"], sub["value"]))
        lab = {m: l for m, l in zip(sub["metric"], sub["label"]) if l is not None}

        def _n(k):
            v = num.get(k)
            return None if v is None or not np.isfinite(v) else float(v)

        out[str(t)] = {
            "has_news": bool(_n("has_news")),
            "sent": _r(_n("sent_mean_30d"), 3),
            "n": _n("news_count_30d") or 0,
            "z": _r(_n("news_z"), 2),
            "sev": _r(_n("severity_max"), 1),
            "event": lab.get("top_event"),
            "band": lab.get("top_severity_band"),
            "hd": (lab.get("top_headline") or "")[:120] or None,
        }
    return out


def build_payload(flags: pd.DataFrame, asof: str,
                  update_state: bool = True) -> dict:
    """Everything the HTML needs, as one JSON-serialisable dict."""
    tagged = classify.apply(flags) if not flags.empty else flags
    if not (tagged is None or tagged.empty):
        # Reconcile against the registry BEFORE sorting, so is_new / days_on_list /
        # promoted are available as sort and display inputs.
        if update_state:
            try:
                tagged = state.update(tagged, asof)
            except Exception as exc:                   # noqa: BLE001
                print(f"  ! flag-state update failed ({repr(exc)[:90]})")
        tagged = classify.sort_for_report(tagged)
        if len(tagged) > config.MAX_FLAGS_REPORTED:
            truncated = len(tagged) - config.MAX_FLAGS_REPORTED
            tagged = tagged.head(config.MAX_FLAGS_REPORTED)
        else:
            truncated = 0
    else:
        truncated = 0

    try:
        import backtest
        br = backtest.load_base_rates()
    except Exception:                                      # noqa: BLE001
        br = pd.DataFrame()

    # Sentiment badge inputs. Best-effort and never fatal: the bounce report
    # predates this module and must keep working without it.
    sent_by_ticker = _sentiment_lookup(asof)

    cards: list[dict] = []
    if tagged is not None and not tagged.empty:
        start = calendar_us.session_offset(
            calendar_us.all_sessions(), asof, config.IND_WARMUP + config.STRUCT_WIN)
        hist = dataset.panel(tagged["ticker"].astype(str).tolist(), "1d",
                             start=start, end=asof)
        for row in tagged.to_dict("records"):
            t = str(row["ticker"])
            card = {
                "ticker": t,
                "name": str(row.get("name") or row.get("sec_name") or ""),
                "bucket": row.get("bucket"),
                "close": _r(row.get("close"), 2),
                "score": _r(row.get("score"), 1),
                "score_band": row.get("score_band"),
                "stage": row.get("stage"),
                "price_tier": row.get("price_tier"),
                "age_band": row.get("age_band"),
                "size_tier": row.get("size_tier"),
                "liquidity_tier": row.get("liquidity_tier"),
                "support_grade": row.get("support_grade"),
                "shape": row.get("shape"),
                "sector": str(row.get("sector") or ""),
                "market_cap": _r(row.get("market_cap"), 0),
                "adv_usd": _r(row.get("adv_usd"), 0),
                "sessions_since_peak": int(row.get("sessions_since_peak") or 0),
                "bars_since_low": int(row.get("bars_since_low") or 0),
                "run_x": _r(row.get("run_x"), 2),
                "retrace": _r(row.get("retrace_of_run"), 3),
                "dd": _r(row.get("dd_from_peak"), 3),
                "ext_atr": _r(row.get("ext_atr"), 2),
                "ext_pct": _r(row.get("ext_pct"), 3),
                "touches_prior": int(row.get("touches_prior") or 0),
                "touches_pre_run": int(row.get("touches_pre_run") or 0),
                "span_days": int(row.get("span_days") or 0),
                "dist_low": _r(row.get("dist_low_level"), 4),
                "dist_now": _r(row.get("dist_now_level"), 4),
                "B": _r(row.get("bounce_score"), 0),
                "V": _r(row.get("volume_score"), 2),
                "Q": _r(row.get("level_Q"), 3),
                "level": _r(row.get("level"), 2),
                "peak_v": _r(row.get("peak_high"), 2),
                "bounce_v": _r(row.get("bounce_low"), 2),
                "peak_date": str(row.get("peak_date") or ""),
                "bounce_low_date": str(row.get("bounce_low_date") or ""),
                "is_new": bool(row.get("is_new", True)),
                "days_on_list": int(row.get("days_on_list") or 1),
                "promoted": bool(row.get("promoted", False)),
                "h1_higher_lows": _r(row.get("h1_higher_lows"), 0),
                "h1_pierce": bool(row.get("h1_base_pierce"))
                if row.get("h1_base_pierce") is not None else None,
                "h1_reject": _r(row.get("h1_intraday_reject"), 2),
                "tv": tv_link(t, row.get("exchange")),
                "parts": {k[2:]: _r(row.get(k), 1) for k in
                          ("s_support", "s_bounce", "s_retrace", "s_run",
                           "s_volume", "s_tightness", "s_stage", "s_liquidity",
                           "s_size") if row.get(k) is not None},
            }
            rate = base_rate_for(row, br)
            if rate:
                card["base_rate"] = rate
            if sent_by_ticker is not None:
                card["sentiment"] = sent_by_ticker.get(
                    t, {"has_news": False})
            ser = build_card_series(row, hist.get(t))
            if ser:
                card["chart"] = ser
            cards.append(card)

    counts: dict[str, int] = {}
    if tagged is not None and not tagged.empty:
        counts = tagged["bucket"].value_counts().to_dict()

    _uni_rows, _uni_counts = universe_rows(asof, flags)

    return {
        "asof": asof,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "cards": cards,
        "counts": {k: int(v) for k, v in counts.items()},
        "bucket_order": list(config.BUCKET_ORDER),
        "truncated": truncated,
        "near_misses": near_misses(asof),
        "dropped": state.dropped_off(asof),
        "universe": _uni_rows,
        "universe_counts": _uni_counts,
        "config": {
            "min_price": config.MIN_PRICE, "min_adv": config.MIN_DOLLAR_VOL,
            "min_run_x": config.MIN_RUN_X, "min_run_z": config.MIN_RUN_Z,
            "min_touches": config.MIN_PRIOR_TOUCHES,
            "retrace": [config.RETRACE_LO, config.RETRACE_HI],
            "age_bands": [[c, n] for c, n in config.AGE_BANDS],
        },
        "bucket_help": {
            "EARLY": "At the level, not turned yet - the whole move is ahead. "
                     "Highest value if you want to catch the bounce, lowest conviction.",
            "PRIME": "Structure complete and the turn has started. A few sessions in.",
            "SPEC": "Real pattern, thin tape or a weaker level. Size accordingly.",
            "WATCH": "Passed, but weaker on one dimension.",
            "LATE": "Already ran. Shown so you know you are late BEFORE you buy.",
        },
    }


# ------------------------------------------------------------------ writers
def write_csv(flags: pd.DataFrame, asof: str) -> "object":
    tagged = classify.apply(flags) if not flags.empty else flags
    if tagged is not None and not tagged.empty:
        tagged = classify.sort_for_report(tagged)
        tagged = tagged.assign(
            tradingview=[tv_link(t, e) for t, e in
                         zip(tagged["ticker"].astype(str),
                             tagged.get("exchange", pd.Series([""] * len(tagged))))])
    cols = [c for c in CSV_COLUMNS if tagged is not None and c in tagged.columns]
    p = config.REPORTS_BOUNCE / f"{asof}.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    (tagged[cols] if cols else pd.DataFrame(columns=CSV_COLUMNS)).to_csv(
        p, index=False, encoding="utf-8")
    return p


def write_html(payload: dict, asof: str) -> "object":
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    from markupsafe import Markup

    env = Environment(loader=FileSystemLoader(str(config.ROOT)),
                      autoescape=select_autoescape(["html"]))
    tpl = env.get_template("dashboard_template.html")

    cards = payload["cards"]
    # No inlined JSON blob any more: the page is server-rendered, so the data does
    # not need to travel to the browser. That halves the file and removes the
    # </script>-escaping hazard entirely. The machine-readable forms are the CSV
    # and data/flags/<date>.parquet.
    import ui
    html = tpl.render(
        navbar=Markup(ui.nav("bounce", 1)),
        shared_css=Markup(ui.CSS),
        asof=asof, generated=payload["generated"],
        tallies=Markup(tallies_html(cards, payload["counts"])),
        sections=Markup(sections_html(cards, "bucket")),
        near_miss=Markup(near_miss_html(payload.get("near_misses") or [])),
        dropped=Markup(dropped_html(payload.get("dropped") or [])),
        universe=Markup(universe_html(payload.get("universe") or [],
                                      payload.get("universe_counts") or {})),
        sessions=Markup(sessions_html(asof)),
        bucket_order_js=Markup(json.dumps(list(config.BUCKET_ORDER))),
        min_price=config.MIN_PRICE,
        min_adv_m=int(config.MIN_DOLLAR_VOL / 1e6),
        min_run_x=config.MIN_RUN_X,
        min_run_z=config.MIN_RUN_Z,
        min_touches=config.MIN_PRIOR_TOUCHES,
    )

    p = config.REPORTS_BOUNCE / f"{asof}.html"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html, encoding="utf-8")
    (config.REPORTS_BOUNCE / "latest.html").write_text(html, encoding="utf-8")
    write_index()
    return p


def write_index() -> "object":
    """reports/bounce_index.html -- one row per session, newest first.

    The archive was previously unnavigable: dated reports existed on disk but
    nothing linked them, so "check today and previous days" meant knowing the
    filename convention.

    Renamed from index.html on 2026-08-07: `index.html` is now the master hub
    written by dashboard.py, which is what a browser opens by default when
    pointed at reports/. Two writers on one filename is the kind of collision
    that shows up as a page that mysteriously reverts.
    """
    rows = []
    for f in sorted(config.FLAGS.glob("*.parquet"), reverse=True):
        d = f.stem
        rep = config.REPORTS_BOUNCE / f"{d}.html"
        csv = config.REPORTS_BOUNCE / f"{d}.csv"
        try:
            fl = pd.read_parquet(f)
        except Exception:                                  # noqa: BLE001
            continue
        tagged = classify.apply(fl) if not fl.empty else fl
        counts = (tagged["bucket"].value_counts().to_dict()
                  if tagged is not None and not tagged.empty else {})
        top = ""
        if tagged is not None and not tagged.empty and "score" in tagged:
            top = ", ".join(tagged.nlargest(5, "score")["ticker"].astype(str))
        rows.append(
            f'<tr><td><b>{d}</b></td><td>{len(fl)}</td>'
            + "".join(f'<td>{counts.get(b, 0) or ""}</td>'
                      for b in config.BUCKET_ORDER)
            + f'<td class="tk">{_esc(top)}</td>'
            f'<td>{"<a href=" + d + ".html>report</a>" if rep.exists() else ""}'
            f'{" &middot; <a href=" + d + ".csv>csv</a>" if csv.exists() else ""}'
            f'</td></tr>')

    head = "".join(f"<th>{b}</th>" for b in config.BUCKET_ORDER)
    html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Support Bounce Screen &middot; all sessions</title><style>
:root{{--bg:#f7f8fa;--panel:#fff;--ink:#12161c;--muted:#5b6572;--line:#e2e6ec;--accent:#2f6feb}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0e1116;--panel:#161b22;--ink:#e6edf3;--muted:#8b949e;--line:#262c36;--accent:#589bff}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}}
.wrap{{max-width:1080px;margin:0 auto;padding:22px 16px 60px}}
h1{{font-size:19px;margin:0 0 4px}}.sub{{color:var(--muted);font-size:12.5px;margin-bottom:16px}}
table{{width:100%;border-collapse:collapse;font-size:13px;background:var(--panel);
border:1px solid var(--line);border-radius:8px;overflow:hidden}}
th{{text-align:left;color:var(--muted);font-weight:500;font-size:11.5px;
padding:8px 10px;border-bottom:1px solid var(--line)}}
td{{padding:7px 10px;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}}
tr:last-child td{{border-bottom:none}}
td.tk{{color:var(--muted);font-size:12px}}
a{{color:var(--accent);text-decoration:none}}a:hover{{text-decoration:underline}}
.note{{color:var(--muted);font-size:11.5px;margin-top:14px}}
</style></head><body><div class="wrap">
<h1>Support Bounce Screen &middot; all sessions</h1>
<div class="sub">{len(rows)} session(s) recorded &middot;
<a href="latest.html">newest report</a> &middot;
<a href="../index.html">status hub</a></div>
<table><thead><tr><th>session</th><th>flags</th>{head}
<th>top by score</th><th></th></tr></thead><tbody>{"".join(rows)}</tbody></table>
<div class="note">Counts are per bucket. A session with no row was never screened
(the job did not run, or the market was closed).</div>
</div></body></html>"""
    p = config.REPORTS_BOUNCE / "index.html"
    p.write_text(html, encoding="utf-8")
    return p


def write_digest(payload: dict, asof: str) -> str:
    cards = payload["cards"]
    lines = [f"Support-bounce screen  {asof}   ({payload['generated']})",
             f"{len(cards)} candidate(s)"]
    if payload["counts"]:
        lines.append("  " + "  ".join(
            f"{b}={payload['counts'].get(b, 0)}" for b in payload["bucket_order"]
            if payload["counts"].get(b)))
    new = [c["ticker"] for c in cards if c["is_new"]]
    if new:
        lines.append(f"NEW today ({len(new)}): {', '.join(new[:20])}")
    promoted = [c["ticker"] for c in cards if c.get("promoted")]
    if promoted:
        lines.append(f"PROMOTED: {', '.join(promoted)}")
    lines.append("top by score:")
    for c in sorted(cards, key=lambda x: -(x["score"] or 0))[:5]:
        lines.append(f"  {c['ticker']:<6} {c['close']:>8} {c['score']:>5} "
                     f"{c['bucket']:<6} {c['stage']:<13} "
                     f"lvl {c.get('level') or '-':<8} "
                     f"{c['price_tier']}/{c['age_band']}")
    if payload["near_misses"]:
        lines.append("near-miss large caps: " + ", ".join(
            f"{r['ticker']}({r['gate']})" for r in payload["near_misses"][:6]))
    if payload["truncated"]:
        lines.append(f"! {payload['truncated']} row(s) truncated "
                     f"(MAX_FLAGS_REPORTED={config.MAX_FLAGS_REPORTED})")
    text = "\n".join(lines)
    config.DIGEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.DIGEST_FILE.write_text(text + "\n", encoding="utf-8")
    return text


def build(asof: str | None = None, verbose: bool = True) -> dict:
    asof = asof or calendar_us.last_closed_session()
    p = config.FLAGS / f"{asof}.parquet"
    flags = pd.read_parquet(p) if p.exists() else pd.DataFrame()

    payload = build_payload(flags, asof)
    csv_p = write_csv(flags, asof)
    html_p = write_html(payload, asof)
    digest = write_digest(payload, asof)

    if verbose:
        print(f"  {csv_p.name}   ({len(payload['cards'])} rows)")
        print(f"  {html_p.name}  {html_p.stat().st_size / 1024:.0f} KB")
        print(f"  latest.html    (stable path)")
        print("\n" + digest)
    return payload


def universe_invariants(asof: str | None = None) -> tuple[list[str], dict]:
    """Invariants the universe table must hold. Returns (failures, counts).

    These are cross-checks, not restatements of the code that produces the rows --
    a check that only re-derives the same expression cannot catch the expression
    being wrong. The drawdown check is the one that earned its place: it caught
    `dd_from_peak` being cleared at stage 2 when the value is not assigned until
    stage 3, by noticing a name with a 2.06x run and a 0.0 drawdown that was
    trading 47% below its high.

    Shared by `report.py --selftest` and validate's `screen` group so the two
    cannot drift into disagreeing about what a valid row is.
    """
    asof = asof or calendar_us.last_closed_session()
    p = config.FLAGS / f"{asof}.parquet"
    flags = pd.read_parquet(p) if p.exists() else pd.DataFrame()
    rows, counts = universe_rows(asof, flags)
    if not rows:
        return [], {}

    I_V, I_HI, I_SC, I_RX, I_DD, I_RT, I_TP = 1, 5, 7, 8, 9, 10, 11
    fails: list[str] = []

    def bad(name, hits, why):
        if hits:
            fails.append(f"{name}: {len(hits)} row(s) -- {why}\n     e.g. {hits[:3]}")

    # 1. A name the pattern math never saw cannot carry a pattern metric.
    bad("panel rows carry pattern metrics",
        [r[0] for r in rows if r[I_V] == _V_PANEL
         and any(r[i] is not None for i in (I_SC, I_RX, I_DD, I_RT, I_TP))],
        "dismissed by the panel pass, so every pattern cell must be null")

    # 2. `_blank()` seeds score 0.0; a real composite score is never exactly zero.
    bad("fabricated zero scores", [r[0] for r in rows if r[I_SC] == 0.0],
        "score 0.0 is the _blank() seed leaking through an early return")

    # 3. Cross-check: deep below the high and a zero drawdown cannot both be true.
    bad("impossible drawdown",
        [r[0] for r in rows if r[I_DD] == 0.0 and r[I_HI] is not None and r[I_HI] < 0.9],
        "dd_from_peak 0.0 while trading >10% below the 250d high")

    # 4. Scoring happens after the stage-5 gate, so anything scored cleared the
    #    gates that come before it -- a scored row must have its run measured too.
    bad("scored without a run",
        [r[0] for r in rows if r[I_SC] is not None and r[I_RX] is None],
        "score is assigned after the run metrics, so run_x cannot be null")

    # 5. Every name in the panel is accounted for exactly once.
    seen = [r[0] for r in rows]
    if len(seen) != len(set(seen)):
        fails.append(f"duplicate tickers: {len(seen) - len(set(seen))}")
    if counts["flagged"] + (counts["measured"] - counts["flagged"]) + counts["panel"] != counts["total"]:
        fails.append("verdict counts do not sum to the total")

    counts = dict(counts, real_scores=sum(1 for r in rows if r[I_SC] is not None))
    return fails, counts


def selftest(asof: str | None = None) -> int:
    """`python report.py --selftest`"""
    asof = asof or calendar_us.last_closed_session()
    fails, counts = universe_invariants(asof)
    if not counts:
        print(f"report selftest: no rows for {asof} -- nothing to check")
        return 0
    for f in fails:
        print(f"  FAIL {f}")
    if fails:
        print(f"report selftest: {len(fails)} FAILURE(S)")
        return 1
    print(f"report selftest OK  ({counts['total']:,} screened: {counts['flagged']} flagged, "
          f"{counts['measured'] - counts['flagged']:,} measured, {counts['panel']:,} dismissed; "
          f"{counts['real_scores']} real scores, 0 fabricated)")
    return 0


def main() -> int:
    config.safe_console()
    ap = argparse.ArgumentParser(description="Build the daily report.")
    ap.add_argument("--date", default=None)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    config.dirs()
    if a.selftest:
        return selftest(a.date)
    build(a.date)
    return 0


if __name__ == "__main__":
    sys.exit(main())

