#!/usr/bin/env python
"""`reports/index.html` -- Today: what matters this morning, on one screen.

    python today_page.py            build it (the `dashboard` step does, every run)
    python today_page.py --open

Asked 2026-11-08: the pages were "too cluttered with so much info"; the home page was the job
table (6,100 px of steps, stores and runs). Today is the new home: the market's mood, the top
of the shortlist with its trade plan, the tracked lists, this week's earnings and warning
filings for those names, the weekly AI narrative, and one line saying whether the data is
current. The system detail moved to `reports/system.html` (dashboard.py). Explanations sit
behind a small (i), not in paragraphs. Every stock link goes to its page when one is built,
else to the stock search -- never to a page that does not exist.

READ-ONLY: it reads what the nightly steps wrote and computes nothing that feeds anything.
Each card fails on its own: a missing file costs that card a "not available" line, never the
page. Research screens, not investment advice.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import config
import ui

OUT = config.REPORTS / "index.html"
TOP_N = 5
SOON = 5                      # earnings within this many sessions count as "this week"
HOUR = {"bmo": "before the open", "amc": "after the close", "dmh": "during the day"}

# Page-specific classes only; ui.selftest fails the build if one of these is also in ui.CSS.
CSS = """
.today-status{font-size:12.5px;color:var(--muted);margin:-8px 0 14px}
.today-status .ok{color:var(--pos);font-weight:600}
.today-status .bad{color:var(--neg);font-weight:600}
.today .card{margin-bottom:16px}
.today .card>h3{display:flex;align-items:center;gap:2px}
.today .card>h3 .more{margin-left:auto;font-size:12px;font-weight:500}
.today .tile .v.up{color:var(--pos)}
.today .tile .v.dn{color:var(--neg)}
.today td.nm{color:var(--muted);max-width:230px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.today td.up{color:var(--pos)}
.today td.dn{color:var(--neg)}
.today-list{margin:6px 0 0;padding-left:18px}
.today-list li{margin:4px 0}
.today-sect{margin:10px 0 0;font-size:12.5px}
.today-foot{font-size:11.5px;color:var(--muted)}
.today-earn{font-size:11px;font-weight:600;color:var(--warn);white-space:nowrap}
@media (max-width:700px){.today .hs{display:none}}
"""


def _num(v, fmt: str = "{:,.2f}") -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "&mdash;"
    return fmt.format(f) if np.isfinite(f) else "&mdash;"


def _txt(v) -> str:
    try:
        if v is None or pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    return ui.esc(v)


def _stock_link(t: str, built: set[str]) -> str:
    return f'<a href="{ui.stock_href(t, 0, built)}">{ui.esc(t)}</a>'


def _day(d: str) -> str:
    try:
        return pd.Timestamp(d).strftime("%a %b %d").replace(" 0", " ")
    except (TypeError, ValueError):
        return ui.esc(d)


def _card(key: str, title: str, note: str, body: str, more: str = "") -> str:
    """`data-card` names the card for the customisation panel (hide / reorder) to come."""
    return f'<div class="card" data-card="{key}"><h3>{title}{note}{more}</h3>{body}</div>'


def _unavailable(key: str, title: str, why: str) -> str:
    return _card(key, title, "", f'<p class="muted" style="margin:0">{why}</p>')


def _latest_dir(root: Path, asof: str | None) -> str | None:
    days = sorted(p.name for p in root.glob("????-??-??") if p.is_dir() and (asof is None or p.name <= asof))
    return days[-1] if days else None


# ------------------------------------------------------------------------------ the cards
def system_state() -> dict:
    """Which steps are failing now (latest row error or blocked) and when anything last ran:
    the same reading as the Status page, from the job table."""
    import orchestrator as O
    jobs = O.read_jobs()
    bad = []
    for st in O.REGISTRY:
        mine = jobs[jobs["step"] == st.name]
        if len(mine) and str(mine.iloc[-1]["status"]) in (O.STATUS_ERROR, O.STATUS_BLOCKED):
            bad.append(st.name)
    last = str(jobs["started"].dropna().max())[:16].replace("T", " ") if len(jobs) else ""
    return {"bad": bad, "last": last, "steps": len(O.REGISTRY)}


def spy_trend(asof: str) -> dict | None:
    """SPY's latest close on or before `asof` against its 50- and 200-session averages, from
    the ETF bars, with the session it is from (the card says so when that is not `asof`)."""
    files = sorted(Path(config.BARS_ETF).glob("*.parquet"))
    files = [f for f in files if f.stem <= asof[:7]][-13:]
    if not files:
        return None
    e = pd.concat(pd.read_parquet(f, columns=["ticker", "date", "close"], filters=[("ticker", "==", "SPY")])
                  for f in files)
    e["date"] = e["date"].astype(str).str[:10]
    s = e.drop_duplicates("date").set_index("date")["close"].sort_index()
    s = s[s.index <= asof]
    if len(s) < 200:
        return None
    c = float(s.iloc[-1])
    return {"close": c, "vs50": c / float(s.tail(50).mean()) - 1, "vs200": c / float(s.tail(200).mean()) - 1,
            "session": str(s.index[-1])}


def vix_reading(asof: str) -> tuple[float, str] | None:
    """The latest VIX on or before `asof`, and the session it was first seen on. FRED posts
    the VIX a day late and the macro table carries the last value forward, so an unchanged
    value is reported with the date it was actually read, not as today's."""
    m = pd.read_parquet(config.DATA / "_macro.parquet", columns=["session", "vix"])
    m["session"] = m["session"].astype(str).str[:10]
    v = m[m.session <= asof].dropna(subset=["vix"]).sort_values("session")
    if v.empty:
        return None
    val = float(v.vix.iloc[-1])
    i = len(v) - 1
    while i > 0 and float(v.vix.iloc[i - 1]) == val:
        i -= 1
    return val, str(v.session.iloc[i])


def _market(asof: str) -> str:
    tiles = []
    try:
        t = spy_trend(asof)
        if t:
            when = "" if t["session"] == asof else f" &middot; close of {_day(t['session'])}"
            tiles.append(("S&amp;P 500 vs 50-day", f"{t['vs50'] * 100:+.1f}%", "up" if t["vs50"] > 0 else "dn",
                          f"vs 200-day: {t['vs200'] * 100:+.1f}%{when}"))
    except Exception:                                            # noqa: BLE001
        pass
    try:
        r = vix_reading(asof)
        if r:
            val, seen = r
            when = "" if seen == asof else f" &middot; reading of {_day(seen)}"
            tiles.append(("VIX (fear gauge)", f"{val:.1f}", "",
                          ("20 or more: a nervous market" if val >= 20 else "under 20: a calm market") + when))
    except Exception:                                            # noqa: BLE001
        pass
    try:
        m = pd.read_parquet(config.DATA / "_macro.parquet", columns=["session", "pct_above_ma"])
        m["session"] = m["session"].astype(str).str[:10]
        b = m[m.session <= asof].dropna(subset=["pct_above_ma"]).sort_values("session")
        if len(b):
            seen = str(b.session.iloc[-1])
            when = "" if seen == asof else f" &middot; reading of {_day(seen)}"
            tiles.append(("Breadth", f"{float(b.pct_above_ma.iloc[-1]) * 100:.0f}%", "",
                          f"of stocks above their {config.BREADTH_MA}-day average{when}"))
    except Exception:                                            # noqa: BLE001
        pass
    sect = ""
    try:
        g = pd.read_parquet(config.DATA / "sectors" / f"groups_{asof}.parquet")
        g = g[(g.level == "sector") & (g.group != "Other")].dropna(subset=["score"])
        g = g.sort_values(["score", "group"], ascending=[False, True])
        if len(g) >= 6:
            fmt = lambda x: ", ".join(f"{ui.esc(r.group)} <span class='muted'>{r.score:.0f}</span>"  # noqa: E731
                                      for r in x.itertuples())
            sect = (f'<p class="today-sect">Strongest sectors: <b>{fmt(g.head(3))}</b> &middot; weakest: '
                    f'{fmt(g.tail(3).iloc[::-1])} &middot; <a href="sectors/latest.html">all sectors</a></p>')
    except Exception:                                            # noqa: BLE001
        pass
    if not tiles and not sect:
        return _unavailable("market", "Market", f"No market reading for {ui.esc(asof)}.")
    body = '<div class="tiles">' + "".join(
        f'<div class="tile"><div class="k">{k}</div><div class="v {c}">{v}</div><div class="s">{s}</div></div>'
        for k, v, c, s in tiles) + "</div>" + sect
    note = ui.info("Context, not a rule. Measured on 2018-2026 bounce trades: trading only when the S&amp;P 500 "
                   "was above its averages did <b>not</b> help; a VIX of 20 or more helped in both test periods "
                   "but not reliably enough to keep. The sector score (0-100) ranks breadth and business quality.")
    return _card("market", "Market", note, body)


def _shortlist(asof: str, built: set[str]) -> tuple[str, list[str]]:
    import shortlist as SL
    cfg = SL.recipes()
    f = SL.DIR / asof / f"{cfg['default']}.csv"
    if not f.exists():
        return _unavailable("shortlist", "Shortlist", f"No shortlist was built for {ui.esc(asof)}."), []
    x = pd.read_csv(f)
    if "rank" in x.columns:
        x = x.sort_values("rank", kind="stable")
    label = next((r["label"] for r in cfg["recipes"] if r["name"] == cfg["default"]), cfg["default"])
    rows = []
    for r in x.head(TOP_N).itertuples(index=False):
        # Earnings within the week sit under the ticker, not in a column of their own: a
        # column wrapped "earnings in 2 sessions" into a 4-line cell at phone width.
        es, ed = getattr(r, "earn_sessions", np.nan), str(getattr(r, "earnings", "") or "")
        earn = ""
        try:
            if pd.notna(es) and float(es) <= SOON and ed[:10] != "nan":
                d, _sp, h = ed.partition(" ")
                earn = (f"<div class='today-earn' title='reports {_day(d)}{' ' + HOUR[h] if h in HOUR else ''}"
                        f"; the market reacts in {int(es)} session{'s' if int(es) != 1 else ''}'>"
                        f"earnings {pd.Timestamp(d).strftime('%a')}</div>")
        except (TypeError, ValueError):
            earn = ""
        rows.append(f"<tr><td class='lab'><b>{_stock_link(r.ticker, built)}</b>{earn}</td>"
                    f"<td class='lab nm hs' title='{_txt(r.name)}'>{_txt(r.name)}</td>"
                    f"<td class='lab hs'>{_txt(r.sector)}</td><td>{_num(r.price)}</td>"
                    f"<td><b>{_num(r.limit)}</b></td><td class='up'>{_num(r.target)}</td>"
                    f"<td class='dn'>{_num(r.stop)}</td>"
                    f"<td class='hs'>{_num(getattr(r, 'fund', np.nan), '{:.0f}')}</td></tr>")
    note = ui.info(f"Ranked by {ui.esc(label.lower())}, the ranking that tested best (2018-2026). The plan for each "
                   "name: a buy limit at its support level + 0.5%, good for 3 sessions; then a target of +3% and a "
                   "stop of -8% from the fill, out after 10 sessions. On 2022-2026, counting only fills that traded "
                   "through the limit: +0.36% a trade after costs (t 2.42); about half the orders fill. "
                   "Research, not advice.")
    more = (f'<a class="more" href="shortlist/latest.html">all {int(cfg["n"])} &middot; '
            f'{len(cfg["recipes"]) - 1} other rankings &rarr;</a>')
    table = ("<div class='scroll'><table><thead><tr><th class='lab'>Ticker</th><th class='lab hs'>Name</th>"
             "<th class='lab hs'>Sector</th><th>Price</th><th>Buy limit</th><th>Target</th><th>Stop</th>"
             "<th class='hs'>Fund</th></tr></thead><tbody>" + "".join(rows)
             + "</tbody></table></div>")
    return (_card("shortlist", f"Shortlist &middot; top {TOP_N}", note, table, more),
            x.head(int(cfg["n"])).ticker.astype(str).tolist())


def _watchlists() -> tuple[str, list[str]]:
    import watchlist_page as WP
    note = ui.info("Each saved list measured nightly from its entry-day close, against every stock that met the "
                   "list's own rules that day (the market column). Status counts: per plan (above the entry) / "
                   "held (above the support level) / below support / failed (a low under 90% of the level).")
    try:
        lists = WP.lists()
    except Exception:                                            # noqa: BLE001
        lists = []
    rows, names = [], []
    for name, cfg in lists:
        d = WP.W.ROOT / name
        try:
            S = json.loads((d / "summary.json").read_text(encoding="utf-8"))[0]
            diff = float(S["list_avg_pct"]) - float(S["pool_avg_pct"])
        except Exception:                                        # noqa: BLE001
            continue
        try:
            names += pd.read_csv(d / "list.csv", usecols=["ticker"]).ticker.astype(str).tolist()
        except Exception:                                        # noqa: BLE001
            pass
        rows.append(f'<tr><td class="lab"><a href="watchlist/{ui.esc(name)}.html">{ui.esc(cfg.get("title") or name)}</a>'
                    f"<div class='muted' style='font-size:11px'>{int(S['stocks'])} stocks &middot; "
                    f"{int(S['sessions'])} sessions since {_day(cfg['entry'])}</div></td>"
                    f"<td>{float(S['list_avg_pct']):+.2f}%</td><td>{float(S['pool_avg_pct']):+.2f}%</td>"
                    f"<td class='{'up' if diff > 0 else 'dn'}'><b>{diff:+.2f}</b></td>"
                    f"<td class='hs'>{S['per_plan']} / {S['held']} / {S['below_support']} / {S['failed']}</td></tr>")
    if not rows:
        return _card("watchlists", "Watchlists", note, '<p class="muted" style="margin:0">No list is tracked.</p>'), names
    table = ("<div class='scroll'><table><thead><tr><th class='lab'>List</th><th>List avg</th><th>Market avg</th>"
             "<th>Gap (pp)</th><th class='hs'>Plan / held / below / failed</th></tr></thead><tbody>"
             + "".join(rows) + "</tbody></table></div>")
    more = '<a class="more" href="watchlist/index.html">all lists &rarr;</a>'
    return _card("watchlists", "Watchlists", note, table, more), names


def _this_week(asof: str, tickers: list[str], built: set[str], short: set[str]) -> str:
    import news_events as NE
    try:
        flags = NE.page_flags(asof)
    except Exception:                                            # noqa: BLE001
        flags = {}
    earn, warn = [], []
    for t in dict.fromkeys(tickers):
        f = flags.get(t) or {}
        e = f.get("earn")
        if e and e[2] is not None and int(e[2]) <= SOON:
            earn.append((int(e[2]), str(e[0]), str(e[1] or ""), t))
        for w in f.get("warn") or []:
            warn.append((str(w[2]), t, str(w[0]), str(w[3])))
    warn.sort(reverse=True)
    # One line per report slot (day + before/after the close), in the order the market reacts:
    # sessions-until-reaction first, so Friday-after-the-close sits with Monday-before-the-open.
    slots: dict[tuple[int, str, str], list[str]] = {}
    for n, d, h, t in sorted(earn):
        slots.setdefault((n, d, h), []).append(t)
    link = lambda t: f"<b>{_stock_link(t, built)}</b>" if t in short else _stock_link(t, built)  # noqa: E731
    e_html = "".join(f"<li><span class='muted'>{_day(d)}{' ' + HOUR[h] if h in HOUR else ''}:</span> "
                     + ", ".join(link(t) for t in sorted(ts)) + "</li>"
                     for (_n, d, h), ts in sorted(slots.items()))         or "<li class='muted'>none in the next 5 sessions</li>"
    w_html = "".join(f'<li>{_stock_link(t, built)} &middot; <a href="{ui.esc(u)}" rel="noopener" target="_blank">'
                     f"{ui.esc(s)}</a> <span class='muted'>{_day(d)}</span></li>"
                     for d, t, s, u in warn[:8]) or "<li class='muted'>none</li>"
    note = ui.info("For the shortlist (in bold) and the tracked lists only. Earnings inside a 10-session trade did not change "
                   "the average result but meant more stop-outs (41% against 34%). The warning filings (unregistered "
                   "share sales, restatements, ended agreements and the like) measured as negative for the weeks "
                   "after.")
    body = (f'<div class="cols2"><div><b style="font-size:12px">Earnings</b><ul class="today-list">{e_html}</ul></div>'
            f'<div><b style="font-size:12px">Recent warning filings</b><ul class="today-list">{w_html}</ul></div></div>')
    return _card("week", "This week", note, body)


def _narrative(asof: str) -> str:
    note = ui.info("The weekly AI view of what is driving each sector, from -2 (strong headwind) to +2 (strong "
                   "tailwind), with its sources. It carries <b>no</b> weight anywhere until months of these views "
                   "have been checked against later prices.")
    try:
        import narrative
        nl = narrative.latest(asof)
    except Exception:                                            # noqa: BLE001
        nl = pd.DataFrame()
    if nl is None or nl.empty:
        return _card("narrative", "Narrative", note, '<p class="muted" style="margin:0">The first weekly run is '
                     'due Saturday 2026-11-14; its view appears here once it has been checked.</p>')
    s = nl[nl.level == "sector"].copy()
    s["now"] = pd.to_numeric(s["now"], errors="coerce")
    up = s[s.now > 0].sort_values(["now", "group"], ascending=[False, True])
    dn = s[s.now < 0].sort_values(["now", "group"])
    fmt = lambda x: ", ".join(f"{ui.esc(r.group)} ({int(r.now):+d})" for r in x.head(4).itertuples()) or "none"  # noqa: E731
    body = (f'<p style="margin:0">Tailwinds: <b>{fmt(up)}</b></p><p style="margin:4px 0 0">Headwinds: {fmt(dn)}</p>'
            f'<p class="muted" style="margin:6px 0 0;font-size:11.5px">View of {_day(str(nl.date.iloc[0]))}</p>')
    return _card("narrative", "Narrative", note, body)


# ------------------------------------------------------------------------------ the page
def build(asof: str | None = None, verbose: bool = True) -> Path:
    import calendar_us
    import shortlist as SL
    last_closed = calendar_us.last_closed_session()
    asof = asof or _latest_dir(SL.DIR, last_closed) or last_closed
    built = ui.built_profiles()
    cards: dict[str, str] = {}
    got: dict[str, list[str]] = {}
    for key, fn in (("shortlist", lambda: _shortlist(asof, built)), ("watchlists", _watchlists)):
        try:
            cards[key], got[key] = fn()
        except Exception as exc:                                 # noqa: BLE001
            cards[key], got[key] = _unavailable(key, key.title(), f"Not available ({ui.esc(type(exc).__name__)})."), []
    names = got["shortlist"] + got["watchlists"]
    short = set(got["shortlist"])
    for key, fn in (("market", lambda: _market(asof)), ("week", lambda: _this_week(asof, names, built, short)),
                    ("narrative", lambda: _narrative(asof))):
        try:
            cards[key] = fn()
        except Exception as exc:                                 # noqa: BLE001
            cards[key] = _unavailable(key, key.title(), f"Not available ({ui.esc(type(exc).__name__)}).")
    current = asof >= last_closed
    status = (f'<span class="{"ok" if current else "bad"}">{"Data current" if current else "Data behind"}</span>'
              f' through {_day(asof)}')
    try:
        st = system_state()
        status += (f' &middot; last run {ui.esc(st["last"])} &middot; '
                   + (f'<span class="bad">{len(st["bad"])} step(s) failing: {ui.esc(", ".join(st["bad"][:4]))}</span>'
                      if st["bad"] else f'all {st["steps"]} steps fine'))
    except Exception:                                            # noqa: BLE001
        pass
    status += ' &middot; <a href="system.html">Status</a>'
    body = (f'<div class="today"><div class="today-status">{status}</div>'
            + cards["market"] + cards["shortlist"] + cards["week"]
            + f'<div class="cols2">{cards["watchlists"]}{cards["narrative"]}</div>'
            + '<p class="today-foot">Research screens, not investment advice.</p></div>')
    html = ui.page("Today", body, active="today", depth=0, head=f"<style>{CSS}</style>")
    OUT.write_text(html, encoding="utf-8")
    if verbose:
        print(f"  today: {OUT}  (data through {asof})")
    return OUT


def main() -> int:
    p = build()
    if "--open" in sys.argv:
        import webbrowser
        webbrowser.open(p.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
