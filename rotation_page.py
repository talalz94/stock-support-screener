#!/usr/bin/env python
"""Sector rotation, drawn: which sectors and sub-industries lead the market now and how they got
there, month by month since 2017, with what rotation_study.py and season_study.py measured.

Asked 2026-11-12: "is there sector/industry/sub-industry wise money rotation, like we say AI/Tech
stocks boom and then we saw bio/health stocks boom ... is there a trend or seasonality or chart
that shows this?". Built by the `sectors` step: reports/sectors/rotation.html.

THE NUMBERS are those of every sector reading here (sectors.py): a group's equal-weight daily
return over members eligible the day before ($1.50+, $1M+ a day; blank under 8 members), against
the equal-weight return of every eligible name (the universe).
  LEADERSHIP NOW: x = the group's excess over the last 252 sessions (its trend), y = over the
    last 21 (its month); the tail runs back through where it stood 5, 10, 15 and 20 sessions
    ago, so the direction of travel shows. The quadrants of a relative-rotation chart: leading
    (both above the market), weakening (trend above, month below), lagging (both below),
    improving (trend below, month above). A reading needs the group on 80% of the window's days.
  MONTH BY MONTH: each calendar month's excess (15+ sessions; the newest month runs to the last
    session and says so).
CONTEXT, NOT A SIGNAL: what was measured about rotation is printed beside it from the study files,
and nothing here enters a score or a ranking.

Run: python rotation_page.py [--asof D] [--selftest]
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import numpy as np
import pandas as pd

import calendar_us
import config

config.safe_console()

import sectors as S                                              # noqa: E402
import ui                                                        # noqa: E402

OUT = config.REPORTS / "sectors" / "rotation.html"
STUDY = config.DATA / "_rotation_study.json"
SEASON = config.DATA / "_season_study_summary.json"
TREND, MONTH = 252, 21
TAIL = (20, 15, 10, 5, 0)
MIN_COVER = 0.8
MIN_MONTH_SESSIONS = 15
RECENT_MONTHS = 24
CAP = 0.10                     # a cell's colour saturates at +-10% against the market
POS_RGB, NEG_RGB = "26,127,55", "207,34,46"
QUAD = {"leading": ("Leading", "var(--pos)", "trend and month above the market"),
        "weakening": ("Weakening", "var(--warn)", "trend above, this month below"),
        "lagging": ("Lagging", "var(--neg)", "trend and month below the market"),
        "improving": ("Improving", "var(--accent)", "trend below, this month above")}


def log(m: str) -> None:
    print(f"  [rotation_page] {m}", flush=True)


# ============================================================ numbers
def returns(asof: str) -> tuple[dict, pd.Series]:
    """{level: date x group daily return}, and the universe's daily return, through `asof`."""
    C, V = S.load_wide()
    C, V = C.loc[:asof], V.loc[:asof]
    E = S.eligible(C, V)
    R = S.daily_returns(C)
    U = R.where(E.shift(1, fill_value=False).astype(bool)).mean(axis=1)
    out = {}
    for level in ("sector", "subind"):
        GR, _N = S.group_returns(R, E, level)
        out[level] = GR.drop(columns=[c for c in GR.columns if str(c) in ("Other", "Other Other")], errors="ignore")
    return out, U


def window_excess(lg: pd.DataFrame, lu: pd.Series, end: int, n: int) -> pd.Series:
    """Each group's simple return minus the universe's over the n sessions ending at row `end`;
    blank where the group has a return on fewer than MIN_COVER of those sessions."""
    a = end - n + 1
    if a < 1:
        return pd.Series(np.nan, index=lg.columns)
    g, u = lg.iloc[a:end + 1], lu.iloc[a:end + 1]
    ex = np.expm1(g.sum()) - float(np.expm1(u.sum()))
    return ex.where(g.notna().mean() >= MIN_COVER)


def leadership(GR: pd.DataFrame, U: pd.Series) -> pd.DataFrame:
    """Per group: x (252-session excess), y (21-session excess), the tail and the quadrant."""
    lg, lu = np.log1p(GR), np.log1p(U)
    n = len(lg) - 1
    df = pd.DataFrame(index=GR.columns)
    for off in TAIL:
        df[f"x{off}"] = window_excess(lg, lu, n - off, TREND)
        df[f"y{off}"] = window_excess(lg, lu, n - off, MONTH)
    df = df.rename(columns={"x0": "x", "y0": "y"}).dropna(subset=["x", "y"])
    df["quad"] = np.select([(df.x >= 0) & (df.y >= 0), (df.x >= 0) & (df.y < 0), (df.x < 0) & (df.y < 0)],
                           ["leading", "weakening", "lagging"], "improving")
    return df.sort_values("x", ascending=False)


def monthly(GR: pd.DataFrame, U: pd.Series) -> tuple[pd.DataFrame, str]:
    """month x group excess (15+ sessions; the newest month to date), and that month's last date."""
    lg, lu = np.log1p(GR), np.log1p(U)
    m = np.asarray(pd.Index(GR.index).astype(str).str[:7])
    sg, su = lg.groupby(m).sum(min_count=1), lu.groupby(m).sum(min_count=1)
    cnt = lg.notna().groupby(m).sum()
    ex = np.expm1(sg).sub(np.expm1(su), axis=0)
    ok = cnt >= MIN_MONTH_SESSIONS
    last = ex.index[-1]
    ok.loc[last] = cnt.loc[last] >= 3
    return ex.where(ok), str(GR.index[-1])


# ============================================================ drawing
def _pc(v: float, nd: int = 1) -> str:
    return "&ndash;" if v is None or not np.isfinite(v) else f"{v * 100:+.{nd}f}%"


def _limit(vals: np.ndarray, floor: float) -> float:
    """An axis half-range for the bulk of the points: one group far out (Mining's year) would
    otherwise squeeze every other into the middle. Points beyond it are drawn at the edge."""
    a = np.abs(vals[np.isfinite(vals)])
    if not len(a):
        return floor
    return max(min(float(a.max()), float(np.quantile(a, 0.85)) * 1.6), floor) * 1.12


def scatter(df: pd.DataFrame, label: bool = True, w: int = 640, h: int = 470, cid: str = "rrg") -> str:
    """The relative-rotation chart: trend across, month up; tails back 20 sessions. A point past
    the axis range sits on the edge as an arrow, its label giving the true value; a tail running
    past it is cut at the frame (`cid` names the clip, unique per chart on a page)."""
    if df.empty:
        return '<div class="note">no group has the history this needs yet</div>'
    pad_l, pad_r, pad_t, pad_b = 46, 16, 14, 34
    xs = df[[c for c in df.columns if c.startswith("x")]].to_numpy(dtype=float).ravel()
    ys = df[[c for c in df.columns if c.startswith("y")]].to_numpy(dtype=float).ravel()
    xm, ym = _limit(xs, 0.05), _limit(ys, 0.02)
    xl, yl = xm * 0.97, ym * 0.97
    X = lambda v: pad_l + (float(np.clip(v, -xl, xl)) + xm) / (2 * xm) * (w - pad_l - pad_r)   # noqa: E731
    Y = lambda v: pad_t + (ym - float(np.clip(v, -yl, yl))) / (2 * ym) * (h - pad_t - pad_b)   # noqa: E731
    Xu = lambda v: pad_l + (float(v) + xm) / (2 * xm) * (w - pad_l - pad_r)                    # noqa: E731
    Yu = lambda v: pad_t + (ym - float(v)) / (2 * ym) * (h - pad_t - pad_b)                    # noqa: E731
    x0, y0 = X(0), Y(0)
    out = [f'<svg viewBox="0 0 {w} {h}" class="rrg" role="img" aria-label="relative rotation of groups">',
           f'<defs><clipPath id="{cid}"><rect x="{pad_l}" y="{pad_t}" width="{w - pad_l - pad_r}" '
           f'height="{h - pad_t - pad_b}"/></clipPath></defs>']
    for q, (x_a, y_a, x_b, y_b) in {"leading": (x0, pad_t, w - pad_r, y0), "weakening": (x0, y0, w - pad_r, h - pad_b),
                                    "lagging": (pad_l, y0, x0, h - pad_b), "improving": (pad_l, pad_t, x0, y0)}.items():
        out.append(f'<rect x="{x_a:.1f}" y="{y_a:.1f}" width="{x_b - x_a:.1f}" height="{y_b - y_a:.1f}" '
                   f'style="fill:{QUAD[q][1]}" opacity=".06"/>')
    # Quadrant names in the middle of each quadrant's outer edge: the corners are where a group
    # past both axes is drawn (Semiconductors on 2026-11-10 sat on the old corner label).
    mid_r, mid_l = (x0 + w - pad_r) / 2, (pad_l + x0) / 2
    corners = {"leading": (mid_r, pad_t + 15), "weakening": (mid_r, h - pad_b - 7),
               "lagging": (mid_l, h - pad_b - 7), "improving": (mid_l, pad_t + 15)}
    qboxes = []
    for q, (cx, cy) in corners.items():
        out.append(f'<text x="{cx:.1f}" y="{cy:.1f}" class="rq" text-anchor="middle" '
                   f'style="fill:{QUAD[q][1]}">{QUAD[q][0]}</text>')
        tw = 8.6 * len(QUAD[q][0])
        qboxes.append((cx - tw / 2, cy - 12, cx + tw / 2, cy + 3))
    out.append(f'<line x1="{pad_l}" x2="{w - pad_r}" y1="{y0:.1f}" y2="{y0:.1f}" class="rax"/>'
               f'<line x1="{x0:.1f}" x2="{x0:.1f}" y1="{pad_t}" y2="{h - pad_b}" class="rax"/>')
    for v in (-xm / 1.12, xm / 1.12):
        out.append(f'<text x="{X(v):.1f}" y="{h - pad_b + 14}" class="pax" text-anchor="middle">{v * 100:+.0f}%</text>')
    for v in (-ym / 1.12, ym / 1.12):
        out.append(f'<text x="{pad_l - 6}" y="{Y(v) + 4:.1f}" class="pax" text-anchor="end">{v * 100:+.0f}%</text>')
    out.append(f'<text x="{(pad_l + w - pad_r) / 2:.1f}" y="{h - 4}" class="pax" text-anchor="middle">'
               f'past 12 months against the market &rarr;</text>')
    out.append(f'<text x="12" y="{(pad_t + h - pad_b) / 2:.1f}" class="pax" text-anchor="middle" '
               f'transform="rotate(-90 12 {(pad_t + h - pad_b) / 2:.1f})">past month &rarr;</text>')
    pts = [(X(r.x), Y(r.y)) for r in df.itertuples()]
    # Labels go where they cover no other label and no point: eight directions at two distances,
    # else the spot overlapping least. The points are placed first, so no label hides one.
    boxes: list[tuple[float, float, float, float]] = [(px - 6, py - 6, px + 6, py + 6) for px, py in pts] + qboxes

    def overlap(b: tuple, others: list) -> float:
        return sum(max(0.0, min(b[2], o[2]) - max(b[0], o[0])) * max(0.0, min(b[3], o[3]) - max(b[1], o[1]))
                   for o in others)
    for k, ((name, r), (px, py)) in enumerate(zip(df.iterrows(), pts)):
        col = QUAD[r.quad][1]
        tail = [(Xu(r[f"x{o}"]), Yu(r[f"y{o}"])) for o in TAIL[:-1] if np.isfinite(r[f"x{o}"]) and np.isfinite(r[f"y{o}"])]
        tip = (f"{name}: past 12 months {_pc(r.x)} and past month {_pc(r.y)} against the market; "
               f"4 weeks ago {_pc(r.x20)} and {_pc(r.y20)}").replace("&ndash;", "n/a")
        g = [f'<g><title>{ui.esc(tip)}</title>']
        if tail:
            g.append(f'<g clip-path="url(#{cid})"><polyline points="'
                     + " ".join(f"{a:.1f},{b:.1f}" for a, b in tail + [(Xu(r.x), Yu(r.y))])
                     + f'" fill="none" style="stroke:{col}" stroke-width="1.6" opacity=".45"/>')
            ta, tb = tail[0]
            g.append(f'<circle cx="{ta:.1f}" cy="{tb:.1f}" r="2" style="fill:{col}" opacity=".45"/></g>')
        off_x = abs(r.x) > xl
        off_y = abs(r.y) > yl
        if off_x or off_y:            # past the axis: an arrow on the edge, pointing the way out
            dx = (1 if r.x > 0 else -1) if off_x else 0
            dy = (-1 if r.y > 0 else 1) if off_y else 0
            n = max(1e-9, (dx * dx + dy * dy) ** 0.5)
            ux, uy = dx / n, dy / n
            tip_x, tip_y = px + ux * 7, py + uy * 7
            lx, ly = px - uy * 5 - ux * 4, py + ux * 5 - uy * 4
            rx_, ry_ = px + uy * 5 - ux * 4, py - ux * 5 - uy * 4
            g.append(f'<polygon points="{tip_x:.1f},{tip_y:.1f} {lx:.1f},{ly:.1f} {rx_:.1f},{ry_:.1f}" '
                     f'style="fill:{col}" class="rpt"/>')
        else:
            g.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="5" style="fill:{col}" class="rpt"/>')
        if label:
            text = str(name) + (f" {_pc(r.x, 0)}" if off_x else "")
            tw = 7.0 * len(text) + 6           # measured in the browser: 6.4 a character was short
            # Candidate spots around the point, nearest first: right/left at seven heights, then
            # further out, then above and below.
            cands = []
            for dx in (8, 20, 34):
                for dy in (4, -8, 16, -20, 28, -32, -44):
                    cands += [(px + dx, py + dy, "start"), (px - dx, py + dy, "end")]
            cands += [(px, py - 11, "middle"), (px, py + 19, "middle"), (px, py - 24, "middle"), (px, py + 32, "middle")]
            cands.sort(key=lambda c: abs(c[0] - px) + abs(c[1] - 4 - py))
            others = [b for j, b in enumerate(boxes) if j != k]
            best, best_ov = None, None
            for cx, cy, anc in cands:
                bx = cx if anc == "start" else (cx - tw if anc == "end" else cx - tw / 2)
                box = (bx, cy - 11, bx + tw, cy + 3)
                if box[0] < pad_l - 40 or box[2] > w - 2 or box[1] < 2 or box[3] > h - pad_b + 2:
                    continue
                ov = overlap(box, others)
                if best_ov is None or ov < best_ov:
                    best, best_ov = (cx, cy, anc, box), ov
                if ov == 0:
                    break
            if best is None:
                best = (px + 8, py + 4, "start", (px + 8, py - 7, px + 8 + tw, py + 7))
            cx, cy, anc, box = best
            boxes.append(box)
            g.append(f'<text x="{cx:.1f}" y="{cy:.1f}" class="rlab" text-anchor="{anc}">{ui.esc(text)}</text>')
        g.append("</g>")
        out.append("".join(g))
    out.append("</svg>")
    return "".join(out)


def heatmap(M: pd.DataFrame, order: list[str], last_date: str, hid: str) -> str:
    """Rows in `order`, months across; the newest RECENT_MONTHS shown, the rest behind a switch.
    The numbers travel as one JSON block that the page script draws (HM_JS): 7,564 cells written
    as HTML, each with its own hover text, made the page 1.2 MB (2026-11-12)."""
    cols = [m for m in M.index]
    rows = [g for g in order if g in M.columns]
    data = {"months": cols, "last": last_date, "recent": RECENT_MONTHS, "cap": CAP, "pos": POS_RGB, "neg": NEG_RGB,
            "rows": [{"g": g, "v": [None if not np.isfinite(x) else round(float(x) * 100, 1) for x in M[g].to_numpy()]}
                     for g in rows]}
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    return (f'<div class="hm-bar"><div class="toggle" id="{hid}-span">'
            f'<button type="button" data-span="recent" class="on">Last {RECENT_MONTHS} months</button>'
            f'<button type="button" data-span="all">Since {cols[0][:4]}</button></div>'
            f'<span class="hm-key"><i style="background:rgba({NEG_RGB},.8)"></i>&minus;10% '
            f'<i style="background:rgba({NEG_RGB},.2)"></i><i style="background:rgba({POS_RGB},.2)"></i>'
            f'<i style="background:rgba({POS_RGB},.8)"></i>+10% against the market</span></div>'
            f'<div class="scroll hm-wrap"><table class="hm recent" id="{hid}" data-hm="{hid}-data"></table></div>'
            f'<script type="application/json" id="{hid}-data">{blob}</script>')


def quad_table(df: pd.DataFrame) -> str:
    """Sub-industries by quadrant: the numbers behind each point, with the 4-week change."""
    rows = []
    for q in ("leading", "improving", "weakening", "lagging"):
        part = df[df.quad == q]
        for name, r in part.iterrows():
            rows.append(f'<tr><td class="lab">{ui.esc(name)}</td>'
                        f'<td class="lab"><span class="qchip" style="color:{QUAD[q][1]};border-color:{QUAD[q][1]}">'
                        f'{QUAD[q][0]}</span></td><td>{_pc(r.x)}</td><td>{_pc(r.y)}</td>'
                        f'<td>{_pc(r.x - r.x20) if np.isfinite(r.x20) else "&ndash;"}</td></tr>')
    return ('<div class="scroll"><table><thead><tr><th class="lab">Sub-industry</th><th class="lab">Now</th>'
            '<th>12 months</th><th>Past month</th><th>12-month change in 4 weeks</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


# ============================================================ what was measured
def _get(d, *keys, default=None):
    for k in keys:
        if not isinstance(d, dict) or k not in d:
            return default
        d = d[k]
    return d


def evidence() -> str:
    """The findings, read from the study files: nothing typed by hand that a re-run could change."""
    s = None
    try:
        s = json.loads(STUDY.read_text(encoding="utf-8"))
    except Exception:                                            # noqa: BLE001
        pass
    if not s:
        return '<div class="note">Not measured yet: run <code>python rotation_study.py</code>.</div>'
    sec, sub = _get(s, "levels", "sector") or {}, _get(s, "levels", "subind") or {}
    l1s, l1u = _get(sec, "lag_profile", "1") or {}, _get(sub, "lag_profile", "1") or {}
    ps, pu = sec.get("persistence") or {}, sub.get("persistence") or {}
    items = []
    items.append(
        "<li><b>A month's leaders tend to lead the next month too, and booms are short.</b> The rank correlation "
        f"between one month's excess and the next is {l1s.get('all', 0):+.2f} across {sec.get('groups')} sectors "
        f"(t {l1s.get('all_t')}) and {l1u.get('all', 0):+.2f} across {sub.get('groups')} sub-industries "
        f"(t {l1u.get('all_t')}), {sec.get('months')} months from {str(sec.get('first'))[:7]}; on 2022-26 alone "
        f"{l1s.get('test', 0):+.2f} and {l1u.get('test', 0):+.2f}. Two to five months later it is gone, with a faint "
        f"echo six months on ({(_get(sec, 'lag_profile', '6') or {}).get('all', 0):+.2f} for sectors, t "
        f"{(_get(sec, 'lag_profile', '6') or {}).get('all_t')}). A sector "
        f"in the top third stays there the next month {ps.get('stay_top_pct')}% of the time "
        f"(the same sectors with their months shuffled: {ps.get('shuffled_stay_top_pct')}%), and a run in the top "
        f"third lasts {ps.get('mean_run_months')} months on average ({ps.get('shuffled_mean_run')} shuffled). "
        "The long booms are the exceptions: "
        + ", ".join(f"{ui.esc(b['group'])} {b['from']} to {b['to']} ({b['months']} months, {b['excess_pct']:+.0f}%)"
                    for b in (sec.get("longest_booms") or [])[:3]) + ".</li>")
    qs, qu = sec.get("quadrants") or {}, sub.get("quadrants") or {}
    lls, llu = qs.get("leading_minus_lagging") or {}, qu.get("leading_minus_lagging") or {}
    iws, iwu = qs.get("improving_minus_weakening") or {}, qu.get("improving_minus_weakening") or {}
    if lls and llu:
        items.append(
            "<li><b>This chart's quadrants, one month on:</b> sub-industries in <i>Leading</i> beat those in "
            f"<i>Lagging</i> by {llu.get('all', 0) * 100:+.2f}pp the next month (t {llu.get('all_t')}; on 2022-26 "
            f"{llu.get('test', 0) * 100:+.2f}pp, t {llu.get('test_t')}{', kept' if llu.get('kept') else ''}), sectors "
            f"by {lls.get('all', 0) * 100:+.2f}pp (t {lls.get('all_t')}; 2022-26 {lls.get('test', 0) * 100:+.2f}pp, "
            f"t {lls.get('test_t')}{', kept' if lls.get('kept') else ', not kept'}). That is the momentum above, read "
            "off the chart. The <i>Improving</i> and <i>Weakening</i> quadrants, the turns rotation charts are "
            f"usually read for, said nothing: improving minus weakening {iws.get('all', 0) * 100:+.2f}pp for sectors "
            f"and {iwu.get('all', 0) * 100:+.2f}pp for sub-industries. (Added after the first results; the house "
            "rule decides KEPT.)</li>")
    p1, f1 = _get(sec, "pairs", "1") or {}, _get(sec, "pair_forecast", "1") or {}
    other = [(_get(sec, "pairs", k) or {}).get("test_same_sign_pct") for k in ("2", "3", "6")]
    ex = sec.get("examples") or []
    ex_t = max((abs(e.get("t_train") or 0) for e in ex), default=float("nan"))
    items.append(
        "<li><b>No &ldquo;one boom leads to another&rdquo; sequence.</b> Of the "
        f"{p1.get('pairs')} ordered sector pairs, those linked one month apart on 2017-21 ({p1.get('train_t2')}; "
        f"chance alone gives {p1.get('expected_train_t2_by_chance')}) kept their sign on 2022-26 "
        f"{p1.get('test_same_sign_pct')}% of the time, and two, three and six months apart "
        + "/".join(f"{v}%" for v in other if v is not None) + " (chance: 50%). Some one-month links carry over, but "
        f"a forecast built from them scored an IC of {f1.get('pairs_ic', 0):+.3f} against "
        f"{f1.get('rs252_ic', 0):+.3f} for each group's own 12-month strength: they add nothing a group's own "
        "trend does not already say. "
        f"Semiconductors or Technology leading Biotech or Healthcare by one to six months: no link on 2017-21 "
        f"(largest |t| {ex_t:.2f}).</li>")
    season = None
    try:
        season = json.loads(SEASON.read_text(encoding="utf-8"))
    except Exception:                                            # noqa: BLE001
        pass
    lv = (season or {}).get("levels") or {}
    if lv:
        parts = []
        for k, name in (("sector", "sectors"), ("subind", "sub-industries"), ("stock", "stocks")):
            r = lv.get(k) or {}
            if r:
                parts.append(f"{name} IC {r.get('ic', float('nan')):+.3f} (t {r.get('t', float('nan')):+.2f})")
        items.append("<li><b>No seasonality.</b> A group's average excess in the same calendar month of earlier "
                     f"years against that month this year (season_study.py, measured "
                     f"{ui.esc(str(season.get('measured_on'))[:10])}): " + "; ".join(parts) + ".</li>")
    tr = _get(s, "trades", "tests", "limit|f3_8_h10") or {}
    b = _get(tr, "sector:breadth200", "top_minus_all") or {}
    mom = [abs(_get(tr, f"sector:{c}", "top_minus_bottom", "test_t") or 0) for c in ("rs21", "rs63", "rs252")]
    items.append(
        "<li><b>For the shortlist's trade</b> (the at-support plan, "
        f"{_get(s, 'trades', 'n'):,} name-dates on {_get(s, 'trades', 'dates')} dates): buying where the sector "
        f"has momentum did nothing (best 2022-26 |t| {max(mom):.2f}). Names in the third of sectors with the most "
        f"stocks above their 200-day average did {b.get('test_pp', 0):+.2f}pp a trade better than all at-support "
        f"names on 2022-26 (t {b.get('test_t')}), the same sign in both earlier halves &mdash; but that is one of 36 "
        "comparisons, about the chance level, so it is a lead to watch, not an edge.</li>")
    items.append("<li><b>What does carry over:</b> sector strength one month ahead is what the sector score on the "
                 "<a href=\"latest.html\">Sectors</a> page uses (breadth and fundamentals, validated on 2022-26 months "
                 "it had never seen). Research screens, not investment advice.</li>")
    return (f'<p class="note" style="margin-top:0">rotation_study.py, measured '
            f'{ui.esc(str(s.get("measured_on"))[:10])}. TRAIN before 2022, TEST after; t across months or dates.</p>'
            f'<ul class="rot-ev">{"".join(items)}</ul>')


# ============================================================ the page
CSS = """<style>
.rot-grid{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(0,1fr);gap:16px;align-items:start}
@media(max-width:900px){.rot-grid{grid-template-columns:1fr}}
.rot-grid>*{min-width:0}
.rrg{width:100%;height:auto;display:block;background:var(--panel);border:1px solid var(--line);border-radius:9px}
.rrg .rax{stroke:var(--line);stroke-width:1.2}
.rrg .rq{font-size:12px;font-weight:700;letter-spacing:.04em;text-transform:uppercase}
.rrg .rlab{font-size:11.5px;fill:var(--ink)}
.rrg .rpt{stroke:var(--panel);stroke-width:1.5}
.rot-quads{display:grid;gap:8px}
.rot-quads .qbox{border:1px solid var(--line);border-left-width:3px;border-radius:8px;padding:8px 11px;background:var(--panel)}
.rot-quads .qbox b{font-size:12.5px}
.rot-quads .qbox .qm{color:var(--muted);font-size:11.5px;margin-left:6px}
.rot-quads .qbox .qn{font-size:12.5px;margin-top:3px;line-height:1.55}
.hm-bar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:4px 0 9px}
.hm-key{color:var(--muted);font-size:11.5px;display:inline-flex;align-items:center;gap:3px}
.hm-key i{display:inline-block;width:14px;height:10px;border-radius:2px}
table.hm{width:auto;border-collapse:separate;border-spacing:2px;background:transparent;border:0}
table.hm th,table.hm td{border:0;padding:0}
table.hm td{width:20px;min-width:20px;height:18px;border-radius:3px}
table.hm.all td{width:9px;min-width:9px}
table.hm thead th{font-size:10px;font-weight:600;color:var(--muted);text-align:center;line-height:1.1;padding-bottom:3px;
vertical-align:bottom}
table.hm th.lab{position:sticky;left:0;z-index:1;background:var(--bg);text-align:left;font-size:12px;
font-weight:500;color:var(--ink);padding-right:8px;white-space:nowrap}
table.hm.recent .old{display:none}
table.hm td.hm-na{background:repeating-linear-gradient(45deg,var(--grid),var(--grid) 3px,transparent 3px,transparent 6px)}
.qchip{border:1px solid;border-radius:10px;padding:0 7px;font-size:11px;font-weight:600}
ul.rot-ev{margin:0;padding-left:18px;max-width:100ch}
ul.rot-ev li{margin:0 0 9px;line-height:1.55}
</style>"""

JS = """
// The month-by-month tables, drawn from their JSON block (see heatmap()).
document.querySelectorAll('table[data-hm]').forEach(function(t){
  var D; try { D = JSON.parse(document.getElementById(t.getAttribute('data-hm')).textContent); } catch (e) { return; }
  var M = D.months, n = M.length, first = n - D.recent, MON = 'JFMAMJJASOND';
  function esc(s){ return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'); }
  var h = '<thead><tr><th class="lab"></th>';
  for (var k = 0; k < n; k++) {
    var m = M[k], mm = +m.slice(5, 7), yr = (mm === 1 || k === 0 || k === first) ? '<br>' + m.slice(2, 4) : '';
    h += '<th' + (k < first ? ' class="old"' : '') + ' title="' + m + (k === n - 1 ? ' (to ' + D.last + ')' : '') + '">'
       + MON[mm - 1] + yr + '</th>';
  }
  h += '</tr></thead><tbody>';
  D.rows.forEach(function(r){
    h += '<tr><th class="lab" scope="row">' + esc(r.g) + '</th>';
    for (var k = 0; k < n; k++) {
      var v = r.v[k], cls = k < first ? 'old' : '', when = M[k] + (k === n - 1 ? ' to ' + D.last : '');
      if (v === null) { h += '<td class="hm-na ' + cls + '" title="' + esc(r.g) + ' \\u00b7 ' + when + ': no reading"></td>'; continue; }
      var a = (0.08 + 0.72 * Math.min(Math.abs(v) / (D.cap * 100), 1)).toFixed(3);
      h += '<td class="' + cls + '" style="background:rgba(' + (v >= 0 ? D.pos : D.neg) + ',' + a + ')" title="' + esc(r.g)
         + ' \\u00b7 ' + when + ': ' + (v >= 0 ? '+' : '\\u2212') + Math.abs(v).toFixed(1) + '% against the market"></td>';
    }
    h += '</tr>';
  });
  t.innerHTML = h + '</tbody>';
});
document.querySelectorAll('[id$="-span"]').forEach(function(bar){
  var t = document.getElementById(bar.id.replace(/-span$/, ''));
  bar.addEventListener('click', function(e){
    var b = e.target.closest('button[data-span]'); if (!b || !t) return;
    var all = b.getAttribute('data-span') === 'all';
    bar.querySelectorAll('button').forEach(function(x){ x.classList.toggle('on', x === b); });
    t.classList.toggle('recent', !all); t.classList.toggle('all', all);
    var w = t.closest('.scroll'); if (w && all) w.scrollLeft = w.scrollWidth;
  });
});
"""


def quad_boxes(df: pd.DataFrame) -> str:
    out = []
    for q in ("leading", "improving", "weakening", "lagging"):
        names = [n for n in df[df.quad == q].index]
        out.append(f'<div class="qbox" style="border-left-color:{QUAD[q][1]}"><b style="color:{QUAD[q][1]}">'
                   f'{QUAD[q][0]}</b><span class="qm">{QUAD[q][2]}</span>'
                   f'<div class="qn">{ui.esc(", ".join(names)) if names else "<span class=muted>none</span>"}</div></div>')
    return f'<div class="rot-quads">{"".join(out)}</div>'


def build(asof: str | None = None, verbose: bool = True) -> tuple:
    t0 = time.time()
    asof = asof or calendar_us.last_closed_session()
    GRs, U = returns(asof)
    last = str(U.index[-1])
    lead = {lv: leadership(GR, U) for lv, GR in GRs.items()}
    mon = {}
    for lv, GR in GRs.items():
        M, _ld = monthly(GR, U)
        mon[lv] = M
    about = ui.info(
        "Each group is the equal-weight average of its members that can be traded ($1.50+, $1M+ a day), against the "
        "equal-weight average of every such stock. <b>Leadership now</b> plots the past 12 months across and the "
        "past month up; the faint tail is the path over the last 4 weeks, so a group moving up and to the right is "
        "gaining. <b>Month by month</b> colours each calendar month green above the market and red below "
        "(full colour at &plusmn;10%). Hover a point or a cell for its numbers.")
    sec_df, sub_df = lead["sector"], lead["subind"]
    sec_tab = (f'<div class="rot-grid"><div>{scatter(sec_df, cid="rrg-sec")}</div>{quad_boxes(sec_df)}</div>'
               f'<h2>Month by month</h2>' + heatmap(mon["sector"], list(sec_df.index), last, "hm-sec"))
    sub_order = list(sub_df.index) + [g for g in mon["subind"].columns if g not in sub_df.index]
    sub_tab = (f'<div class="rot-grid"><div>{scatter(sub_df, label=False, cid="rrg-sub")}'
               '<p class="note">Hover a point for its name and numbers; the table lists them all.</p></div>'
               f'<div>{quad_table(sub_df)}</div></div>'
               f'<h2>Month by month</h2>' + heatmap(mon["subind"], sub_order, last, "hm-sub"))
    body = ui.tabs("rottabs", [("sectors", "Sectors", "Sectors", sec_tab),
                               ("subind", "Sub-industries", "Sub-ind.", sub_tab),
                               ("measured", "What the history says", "Measured", evidence())])
    html = ui.page("Sector rotation", body,
                   subtitle=(f"sessions through <b>{ui.esc(last)}</b> &middot; which sectors and sub-industries "
                             f"lead the market now, how they got there, and month by month since "
                             f"{ui.esc(str(mon['sector'].index[0]))} {about}"),
                   active="rotation", depth=1, head=CSS, script=JS)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_name(f".{OUT.name}.tmp")
    tmp.write_text(html, encoding="utf-8")
    import os
    os.replace(tmp, OUT)
    if verbose:
        log(f"{OUT} ({len(sec_df)} sectors, {len(sub_df)} sub-industries, {mon['sector'].shape[0]} months; "
            f"{time.time() - t0:.0f}s)")
    return OUT, len(sec_df), len(sub_df)


def selftest() -> int:
    """The window arithmetic on a hand-made series, and the built page's parts."""
    idx = [f"2026-01-{d:02d}" for d in range(1, 31)]
    GR = pd.DataFrame({"A": [0.01] * 30, "B": [0.0] * 30, "C": [np.nan] * 20 + [0.02] * 10}, index=idx)
    U = pd.Series([0.0] * 30, index=idx)
    lg, lu = np.log1p(GR), np.log1p(U)
    ex = window_excess(lg, lu, 29, 10)
    assert abs(ex["A"] - (1.01 ** 10 - 1)) < 1e-12 and ex["B"] == 0, ex
    assert abs(ex["C"] - (1.02 ** 10 - 1)) < 1e-12, "a full window of the group is a reading"
    assert np.isnan(window_excess(lg, lu, 29, 20)["C"]), "half a window must not be a reading"
    M, last = monthly(GR, U)
    assert last == "2026-01-30" and abs(M.at["2026-01", "A"] - (1.01 ** 30 - 1)) < 1e-12
    out, ns, nu = build(verbose=False)
    h = out.read_text(encoding="utf-8")
    assert ns >= 15 and nu >= 30, (ns, nu)
    for needle in ('class="rrg"', 'id="hm-sec"', 'id="hm-sub"', "What the history says", "rotation_study.py"):
        assert needle in h, needle
    assert "nan%" not in h and ">nan<" not in h, "a blank shown as nan"
    print(f"  [rotation_page] window arithmetic exact; page built with {ns} sectors and {nu} sub-industries")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="The sector rotation page.")
    ap.add_argument("--asof")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    build(a.asof)
    return 0


if __name__ == "__main__":
    sys.exit(main())
