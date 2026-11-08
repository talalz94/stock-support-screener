"""
The shared visual system for every report page.

    python ui.py --selftest
    python ui.py --renav        bring every page already in reports/ to the current menu

WHY THIS EXISTS
-----------------
Six report generators grew independently and each hand-rolled its own stylesheet,
its own header, and its own idea of what a metric row looks like. Two different
variable vocabularies were in use -- `--ink/--muted/--accent/--panel` in
dashboard/explore/stock_profile, `--fg/--mut/--acc/--card` in
senti_screen/fund_screen -- so pages that were individually fine read as
unrelated products side by side.

Everything visual now comes from here. A generator builds its own body content
and calls `page()`; it does not write `<head>`, CSS, or nav.

CONSTRAINTS INHERITED FROM THE REST OF THE PROJECT
----------------------------------------------------
- **No framework, no external request.** Every page must open from `file://`
  with no network. That rules out CDN fonts, icon packs and chart libraries;
  charts here are hand-built inline SVG.
- **"No data" must never look like "scored zero".** `pct_bar()` renders an
  explicit empty state, because a grey 0%-width bar reads as a bad score, and
  that is the single most misleading thing a metric row can do.
"""

from __future__ import annotations

import html as _html
import math
import re
import sys

# The menu's own rules, kept apart so `refresh_nav()` can bring an already-written page's
# menu up to date: the markers let it find and replace exactly this block.
NAV_CSS = """/* nav:start */
/* ---- nav: the same on every page ---- */
.nav{position:sticky;top:0;z-index:50;background:var(--panel);
border-bottom:1px solid var(--line);margin-bottom:18px}
.nav .inner{max-width:1400px;margin:0 auto;padding:0 16px;display:flex;
gap:2px;align-items:center;flex-wrap:wrap}
.nav a{padding:11px 13px;font-size:13px;color:var(--muted);font-weight:500;
border-bottom:2px solid transparent;white-space:nowrap}
.nav a:hover{color:var(--ink);text-decoration:none}
.nav a.on{color:var(--accent);border-bottom-color:var(--accent);font-weight:600}
.nav .brand{font-weight:700;color:var(--ink);padding:11px 14px 11px 0;
font-size:13px;letter-spacing:.02em}
.nav .spacer{margin-left:auto}
.nav select{margin:0 0 0 8px}
/* grouped menu (2026-11-08): five groups, native <details> so it works by keyboard,
   by touch and with scripting off; one line on a phone instead of four */
.nav details.dd{position:relative;margin:0}
/* a page's own bare `summary{...}` rule must not restyle the menu */
.nav details.dd>summary{list-style:none;cursor:pointer;padding:11px 12px;font-size:13px;
color:var(--muted);font-weight:500;border-bottom:2px solid transparent;white-space:nowrap;
user-select:none}
.nav details.dd>summary::-webkit-details-marker{display:none}
.nav details.dd>summary::after{content:"";display:inline-block;margin-left:5px;
border:4px solid transparent;border-top-color:currentColor;transform:translateY(2px)}
.nav details.dd>summary:hover,.nav details.dd[open]>summary{color:var(--ink)}
.nav details.dd.on>summary{color:var(--accent);border-bottom-color:var(--accent);font-weight:600}
.nav details.dd .menu{position:absolute;top:100%;left:0;min-width:190px;background:var(--panel);
border:1px solid var(--line);border-radius:9px;box-shadow:0 8px 24px rgba(0,0,0,.12);
padding:6px;z-index:60}
.nav details.dd .menu a{display:block;padding:7px 10px;border-radius:6px;border-bottom:0;color:var(--ink)}
.nav details.dd .menu a:hover{background:var(--grid)}
.nav details.dd .menu a.on{color:var(--accent);background:var(--grid)}
.nav details.dd .menu .hint{display:block;font-size:11px;color:var(--muted);font-weight:400}
.nav form.find{margin:0 0 0 6px;display:flex}
.nav form.find input{width:110px;padding:5px 8px;font-size:12.5px;border:1px solid var(--line);
border-radius:7px;background:var(--bg);color:var(--ink)}
.nav form.find.in-menu{margin:2px 2px 6px}
.nav form.find.in-menu input{width:100%;font-size:14px;padding:7px 9px}
@media (max-width:700px){
  .nav .brand,.nav form.find.inline,.nav .spacer{display:none}
  .nav .inner{padding:0 6px;gap:0}
  .nav a.top,.nav details.dd>summary{padding:11px 7px}
  /* full width, just under the bar however tall it is: a page's session picker takes a
     second row on a phone, and a menu pinned at 44px opened over it */
  .nav details.dd{position:static}
  .nav details.dd .menu{position:absolute;left:8px;right:8px;top:calc(100% + 4px);min-width:0}
  .nav select{margin:0 0 7px 7px;max-width:calc(100% - 14px)}
}
/* nav:end */
"""

# ===========================================================================
# Colour + type. ONE vocabulary.
# ===========================================================================
CSS = """
:root{
  --bg:#f7f8fa; --panel:#fff; --ink:#12161c; --muted:#5b6572; --line:#e2e6ec;
  --accent:#2c68dc; --grid:#eef1f5; --head:#f0f2f5;
  --pos:#1a7f37; --posbg:#dafbe1; --neg:#cf222e; --negbg:#ffebe9;
  --warn:#9a6700; --warnbg:#fff8c5; --blk:#8250df; --blkbg:#fbefff;
  --skip:#676f79; --skipbg:#f0f2f5;
}
@media (prefers-color-scheme:dark){:root{
  --bg:#0e1116; --panel:#161b22; --ink:#e6edf3; --muted:#8b949e; --line:#262c36;
  --accent:#589bff; --grid:#1c222b; --head:#1b2027;
  --pos:#3fb950; --posbg:#0f2a17; --neg:#f85149; --negbg:#2d1214;
  --warn:#d29922; --warnbg:#2b2413; --blk:#a371f7; --blkbg:#221733;
  --skip:#8b949e; --skipbg:#1b2027;
}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:13.5px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
h1{font-size:21px;margin:0 0 2px}
h2{font-size:12px;margin:26px 0 9px;font-weight:700;letter-spacing:.06em;
text-transform:uppercase;color:var(--muted)}
h3{font-size:14px;margin:0 0 4px}
.wrap{max-width:1400px;margin:0 auto;padding:0 16px 70px}
.sub{color:var(--muted);font-size:12.5px;margin-bottom:14px}
.muted{color:var(--muted)}
.note{color:var(--muted);font-size:11.5px;margin-top:10px;max-width:96ch}

""" + NAV_CSS + """
/* ---- info popover (2026-11-08): one small (i) instead of a paragraph of notes ---- */
.info{display:inline-block;position:relative;vertical-align:middle;margin-left:4px}
.info>summary{list-style:none;cursor:pointer;width:16px;height:16px;border-radius:50%;
border:1px solid var(--line);font-size:10px;font-weight:700;text-align:center;line-height:14px;
color:var(--muted);text-transform:none;letter-spacing:0}
.info>summary::-webkit-details-marker{display:none}
.info>summary:hover,.info[open]>summary{color:var(--accent);border-color:var(--accent)}
.info .tip{position:absolute;z-index:45;top:21px;left:-8px;width:min(330px,82vw);
background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:10px 12px;
box-shadow:0 8px 24px rgba(0,0,0,.14);font-size:12px;line-height:1.5;color:var(--ink);
font-weight:400;text-transform:none;letter-spacing:0;white-space:normal}
/* ---- dashboard tiles and grids (Today page) ---- */
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}
.tile{background:var(--bg);border:1px solid var(--line);border-radius:9px;padding:10px 12px;min-width:0}
.tile .k{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.tile .v{font-size:19px;font-weight:650;margin-top:2px;white-space:nowrap}
.tile .s{font-size:11.5px;color:var(--muted)}
.cols2{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr));gap:16px;align-items:start}
.cols2>*{min-width:0}

/* ---- controls ---- */
input,select,button{font:12px inherit;padding:5px 9px;border:1px solid var(--line);
border-radius:6px;background:var(--panel);color:var(--ink)}
button{cursor:pointer}
button:hover{border-color:var(--accent);color:var(--accent)}
button.primary{background:var(--accent);color:#fff;border-color:var(--accent);
font-weight:600}
button.primary:hover{opacity:.9;color:#fff}
.toggle{display:inline-flex;border:1px solid var(--line);border-radius:7px;
overflow:hidden}
.toggle button{border:0;border-radius:0;padding:5px 13px;background:var(--panel)}
.toggle button.on{background:var(--accent);color:#fff;font-weight:600}

/* ---- surfaces ---- */
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:14px 16px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));
gap:12px}
.chips{display:flex;flex-wrap:wrap;gap:7px;margin:10px 0 4px;align-items:center}
.chip{background:var(--panel);border:1px solid var(--line);border-radius:20px;
padding:3px 11px;font-size:12px;white-space:nowrap}
.chip b{font-weight:600}
/* The price chip is the one people scan for, so it is bigger and
   outlined rather than sitting flat among the descriptive chips. */
.chip.px{font-size:15px;padding:5px 13px;border-color:var(--accent)}
.chip.px b{font-size:17px}
.chip.cta{background:var(--accent);color:#fff;border-color:var(--accent);
font-weight:600}
.chip.cta:hover{text-decoration:none;opacity:.9}
/* A chip that qualifies the numbers rather than describing the company --
   currently the non-USD reporting-currency notice. Tinted, not coloured like
   an error: the data is sound, it is the comparability that is limited. */
.chip.note{background:var(--warnbg,var(--panel));border-color:var(--warn,var(--line));
color:var(--warn,inherit);font-weight:600}
/* Pushed to the far end of a chip row: for the one outbound link, so it does
   not read as another descriptive fact about the company. */
.chips .right{margin-left:auto}
.banner{padding:10px 14px;border-radius:8px;margin:0 0 16px;font-size:13px;
border:1px solid var(--line)}
.banner.ok{background:var(--posbg);border-color:var(--pos);color:var(--pos)}
.banner.err{background:var(--negbg);border-color:var(--neg);color:var(--neg)}
.banner.warn{background:var(--warnbg);border-color:var(--warn);color:var(--warn)}

/* ---- tables ---- */
/* The scrollbar is PINNED to the bottom of the viewport, not the bottom of the
   table. A 30-row financials table put its horizontal scrollbar below the fold,
   so reaching a later quarter meant scrolling all the way down first, dragging
   right, then scrolling back up. */
.scroll{overflow-x:auto}
.scroll.sticky-x{max-height:78vh;overflow:auto}
/* `position:sticky` on a TD/TH is ignored when the table is
   `border-collapse:collapse` -- the cell has no box of its own to pin. The base
   `table` rule sets collapse, so a sticky table must opt back out. explore.py
   already did this locally; the shared rule did not, which is why the frozen
   columns rendered as sticky in the CSS and scrolled away on the page. */
.scroll.sticky-x table{border-collapse:separate;border-spacing:0;
/* AND `overflow:visible`. The base `table` rule sets `overflow:hidden` to clip
   its own border-radius, and an ancestor with `overflow:hidden` re-parents a
   sticky descendant to THAT box -- which scrolls with the content, so the cell
   pins to something already moving and appears not to stick at all. Computed
   style still reads `position: sticky`, which is why this looked correct in the
   stylesheet and failed on the page. */
overflow:visible;border-radius:0}
.scroll.sticky-x th,.scroll.sticky-x td{border-bottom:1px solid var(--line)}
/* TWO problems, both reported from the rendered page:
   1. There were TWO horizontal scrollbars -- this container's own, plus the
      mirrored `.xbar` above the table. Hide only the horizontal one; the
      vertical is still needed.
   2. The vertical scrollbar SAT ON the last column. Measured: the last cell's
      right edge was 386px against a content edge of 374px, so 12px of the most
      recent quarter was underneath it. `scrollbar-gutter:stable` reserves the
      track instead of overlaying it. */
.scroll.sticky-x{scrollbar-gutter:stable}
/* AND breathing room after the last column. `scrollbar-gutter:stable` reserves
   the scrollbar's width correctly, but with no padding the final column ends
   ONE PIXEL before it -- measured 1237 against a content edge of 1238. That is
   not technically covered, and it reads as covered to anyone looking at it,
   which amounts to the same complaint. */
.scroll.sticky-x th:last-child,.scroll.sticky-x td:last-child{padding-right:20px}
.scroll.sticky-x::-webkit-scrollbar:horizontal{height:0}
.scroll.sticky-x::-webkit-scrollbar{width:11px}
.scroll.sticky-x::-webkit-scrollbar-thumb{background:var(--line);
border-radius:6px}
.scroll.sticky-x::-webkit-scrollbar-thumb:hover{background:var(--muted)}
.scroll.sticky-x thead th{position:sticky;top:0;z-index:3;background:var(--head)}

/* FROZEN FIRST TWO COLUMNS. Reaching the newest quarter of a 16-column table
   used to mean losing the row labels entirely -- you could see the numbers or
   see what they were, never both. Widths are fixed because `left` for the
   second column has to be a constant, and `sticky` needs a resolved offset. */
.scroll.sticky-x th.lab,.scroll.sticky-x td.lab{position:sticky;left:0;z-index:2;
background:var(--panel);width:196px;min-width:196px;max-width:196px}
.scroll.sticky-x th.spark,.scroll.sticky-x td.spark{position:sticky;left:196px;
z-index:2;background:var(--panel);width:88px;min-width:88px;max-width:88px}
/* The header cells of those columns cross BOTH sticky axes, so they need the
   higher z-index and the header colour or the body cells scroll over them. */
.scroll.sticky-x thead th.lab,.scroll.sticky-x thead th.spark{z-index:4;
background:var(--head)}
/* A frozen column needs a visible edge, otherwise the numbers appear to slide
   underneath the labels with nothing separating them. */
.scroll.sticky-x th.spark,.scroll.sticky-x td.spark{
box-shadow:1px 0 0 var(--line)}

/* Horizontal scrollbar ABOVE the table, mirrored from the container below.
   A 30-row financials table put its only scrollbar past the fold, so getting to
   the latest quarter meant scroll down, drag right, scroll back up. */
.xbar{overflow-x:auto;overflow-y:hidden;height:13px;margin:0 0 5px}
.xbar>div{height:1px}
.xbar::-webkit-scrollbar{height:11px}
.xbar::-webkit-scrollbar-thumb{background:var(--line);border-radius:6px}
.xbar::-webkit-scrollbar-thumb:hover{background:var(--muted)}

/* ---- price chart (hand-built SVG; no library can be loaded offline) ---- */
.pchart{width:100%;height:auto;display:block;background:var(--panel);
border:1px solid var(--line);border-radius:9px}
.pgrid{stroke:var(--grid);stroke-width:1}
.pax{fill:var(--muted);font-size:10px;font-variant-numeric:tabular-nums}
.pline{fill:none;stroke-width:1.6;stroke-linejoin:round}
.pline.up{stroke:var(--pos)}
.pline.down{stroke:var(--neg)}
.pvol{fill:var(--muted);opacity:.28}
/* The support level is the one line a reader should notice, so it is the accent
   colour and dashed -- a solid line reads as data rather than as a threshold. */
.plevel{stroke:var(--accent);stroke-width:1.4;stroke-dasharray:5 4}
.plevtxt{fill:var(--accent);font-size:10.5px;font-weight:600}
.pbase{fill:var(--accent);opacity:.09}
.pcbar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:9px}
/* Trade markers (stock_profile's insider block): who bought or sold, on the line. */
.pmk-buy{fill:var(--pos)}
.pmk-sell{fill:var(--neg)}
.pmk-house{fill:var(--blk)}
table{width:100%;border-collapse:collapse;font-size:12.5px;
background:var(--panel);border:1px solid var(--line);border-radius:8px;
overflow:hidden}
th{text-align:right;color:var(--muted);font-weight:600;font-size:11px;
padding:7px 9px;border-bottom:1px solid var(--line);white-space:nowrap}
th.lab,td.lab{text-align:left}
td{padding:6px 9px;border-bottom:1px solid var(--line);text-align:right;
font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:none}
td.when{white-space:nowrap}

/* ---- status pills ---- */
.pill{display:inline-block;padding:1px 8px;border-radius:20px;font-size:11px;
font-weight:600;white-space:nowrap}
.pill.ok{background:var(--posbg);color:var(--pos)}
.pill.error{background:var(--negbg);color:var(--neg)}
.pill.slow{background:var(--warnbg);color:var(--warn)}
.pill.blocked{background:var(--blkbg);color:var(--blk)}
.pill.skipped,.pill.never{background:var(--skipbg);color:var(--skip)}

/* ---- metric rows with comparison bars ---- */
.mrow{display:grid;grid-template-columns:1fr 84px 74px 74px;gap:10px;
align-items:center;padding:5px 0;border-bottom:1px solid var(--line)}
.mrow:last-child{border-bottom:none}
.mrow .name{font-size:12.5px}
.mrow .name small{color:var(--muted);display:block;font-size:10.5px;
line-height:1.3}
.mrow .val{text-align:right;font-weight:600;font-variant-numeric:tabular-nums}
.mhead{display:grid;grid-template-columns:1fr 84px 74px 74px;gap:10px;
font-size:10px;text-transform:uppercase;letter-spacing:.05em;
color:var(--muted);padding-bottom:5px;border-bottom:1px solid var(--line);
font-weight:700}
.mhead span:nth-child(2){text-align:right}
.pctbar{height:7px;border-radius:4px;background:var(--grid);position:relative;
overflow:hidden}
.pctbar i{position:absolute;left:0;top:0;bottom:0;border-radius:4px;display:block}
.pctbar.na{background:repeating-linear-gradient(45deg,var(--grid),var(--grid) 3px,
transparent 3px,transparent 6px)}
.pctbar-wrap{display:flex;flex-direction:column;gap:2px}
.pctbar-wrap em{font-style:normal;font-size:9.5px;color:var(--muted);
text-align:right;line-height:1}

/* ---- loading state ---- */
.loader{display:flex;gap:10px;align-items:center;padding:14px 16px;
background:var(--panel);border:1px solid var(--line);border-radius:10px;
font-size:13px}
.spin{width:15px;height:15px;border:2px solid var(--line);
border-top-color:var(--accent);border-radius:50%;animation:sp .8s linear infinite;
flex:0 0 auto}
@keyframes sp{to{transform:rotate(360deg)}}
.loader .st{color:var(--muted);font-size:12px}
/* A determinate bar and an elapsed clock. A bare spinner says only "something
   is happening", which after 60 seconds is indistinguishable from "hung" --
   the build genuinely takes ~20s warm and ~3min cold, so the wait has to be
   shown against an expectation rather than left to the user to guess. */
.pbar{height:6px;border-radius:3px;background:var(--line);overflow:hidden;
margin-top:8px;width:260px}
.pbar>i{display:block;height:100%;width:0;background:var(--accent);
border-radius:3px;transition:width .4s linear}
.loader .el{font-variant-numeric:tabular-nums;font-size:12px;color:var(--muted)}
.cmdrow{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:8px}
.cmdrow code{background:var(--panel);border:1px solid var(--line);
border-radius:6px;padding:4px 9px;font-size:12px}
button.copy{cursor:pointer;border:1px solid var(--line);border-radius:6px;
background:var(--panel);padding:4px 10px;font-size:12px;color:inherit}
button.copy:hover{border-color:var(--accent);color:var(--accent)}
@media (prefers-reduced-motion:reduce){.spin{animation:none}}

/* ---- column chooser ---- */
.chooser{position:relative;display:inline-block}
.chooser .pop{display:none;position:absolute;right:0;top:32px;z-index:60;
background:var(--panel);border:1px solid var(--line);border-radius:9px;
padding:10px;width:290px;max-height:60vh;overflow:auto;
box-shadow:0 8px 24px rgba(0,0,0,.14)}
.chooser.open .pop{display:block}
.chooser .pop label{display:flex;gap:7px;align-items:center;padding:3px 2px;
font-size:12px;cursor:pointer}
.chooser .pop .grp{font-size:10px;text-transform:uppercase;letter-spacing:.05em;
color:var(--muted);margin:8px 0 3px;font-weight:700}
.chooser .pop .acts{display:flex;gap:6px;margin-bottom:6px}
"""

# One folder per report type, `latest.html` inside each. Every page except the
# hub and the dictionary now sits one directory deep, so they all pass depth=1
# and `nav()` prefixes `../`.
NAV = [
    ("today", "Today", "index.html"),
    ("hub", "Status", "system.html"),
    ("explore", "Explore", "explore/latest.html"),
    ("bounce", "Bounce", "bounce/latest.html"),
    ("zones", "Zones", "zones/latest.html"),
    ("candidates", "Candidates", "zones/candidates.html"),
    ("shortlist", "Shortlist", "shortlist/latest.html"),
    ("floor", "Floor retest", "zones/floor.html"),
    ("insider", "Insiders", "insider/latest.html"),
    ("house", "House trades", "insider/house.html"),
    ("sectors", "Sectors", "sectors/latest.html"),
    ("watchlist", "Watchlists", "watchlist/index.html"),
    ("sentiment", "Sentiment", "sentiment/latest.html"),
    ("fundamental", "Fundamental", "fundamental/latest.html"),
    ("profiles", "Profiles", "stock/index.html"),
    ("metrics", "Metrics", "metrics.html"),
]


def esc(v) -> str:
    return _html.escape("" if v is None else str(v), quote=True)


# ===========================================================================
# Labels
# ===========================================================================
def _fund_labels() -> dict[str, tuple[str, str]]:
    """(short label, tooltip) per fundamental metric.

    REUSES `fund_metrics.REGISTRY`, which already carries
    `(pillar, direction, description)` -- e.g.
    `f_score -> ('quality', 1, 'Piotroski F-Score, 0-9')`. Re-authoring 29
    labels by hand would immediately drift from the definitions they describe.
    """
    out: dict[str, tuple[str, str]] = {}
    try:
        import fund_metrics as FM
        for key, spec in FM.REGISTRY.items():
            desc = spec[2] if len(spec) > 2 else key
            short = str(desc).split(",")[0].strip()
            out[key] = (short[:34], str(desc))
    except Exception:                                            # noqa: BLE001
        pass
    return out


# Modules with no REGISTRY of their own.
_EXTRA_LABELS = {
    # STORED FOR 3,296 NAMES AND RENDERED FOR NONE until 2026-08-23.
    #
    # `eps_diluted_ttm` became a declared metric when the provider overlay was
    # extended to cover it, but it is not in `fund_metrics.REGISTRY`, so
    # `_fund_labels()` never gave it a name and `metric_row` skipped it. The
    # figure the overlay exists to correct -- CMP reads 0.1628 here against the
    # -2.90 our own arithmetic produced -- was invisible on the page.
    #
    # This file's own rule: a missing row is the least visible state a page
    # has, and the reader cannot ask about a number they cannot see.
    "eps_diluted_ttm": ("EPS (diluted, TTM)",
                        "trailing twelve-month diluted earnings per share; "
                        "provider-sourced where published"),
    "fund_score": ("Fundamental composite", "equal-weight mean of the four pillars"),
    "quality_score": ("Quality pillar", "rank of the quality metrics"),
    "value_score": ("Value pillar", "rank of the valuation metrics"),
    "safety_score": ("Safety pillar", "rank of the balance-sheet metrics"),
    "growth_score": ("Growth pillar", "rank of the growth metrics"),
    "hype_score": ("Hype composite", "mean of attention, premium and stretch"),
    "attention_score": ("Attention", "flow: is it traded unusually hard now"),
    "premium_score": ("Narrative premium", "level: paid per dollar of business"),
    "stretch_score": ("Stretch", "delta: price vs its own past and the business"),
    "ps_ratio": ("Price / Sales", "market cap / TTM revenue"),
    "vol_surge": ("Volume surge", "log 21d / 252d average volume"),
    "trade_surge": ("Trade-count surge", "log 21d / 252d print count"),
    "trade_shrink": ("Print-size shrink", "smaller average prints = retail attention"),
    "turnover": ("Turnover", "21d dollar volume / market cap"),
    "range_expansion": ("Range expansion", "log 21d / 252d ATR%"),
    "gap_freq": ("Gap frequency", "share of 63d sessions gapping >2%"),
    "extension_pct": ("Extension percentile", "today vs its OWN 200DMA history"),
    "above_200dma": ("Above 200DMA", "percent above the 200-day average"),
    "px_vs_rev": ("Price vs revenue", "1y price return minus revenue growth"),
    "short_ratio": ("Short volume ratio", "FINRA short / total volume, 21d"),
    "short_surge": ("Short pressure surge", "log 21d / 252d short ratio"),
    "avg_trade_size": ("Average print size", "shares per trade, 21d"),
    "dip_score": ("Dip score", "depressed price WITHIN the quality gate"),
    "dip_gate": ("Dip gate", "1 = passed the quality+growth gate"),
    "drawdown": ("Drawdown", "percent below its own 1-year high"),
    "senti_gap": ("Sentiment gap", "inverted 30d sentiment"),
    "not_extended": ("Not extended", "inverted extension percentile"),
    "fund_rank": ("Quality rank", "percentile of fund_score"),
    "growth_rank": ("Growth rank", "percentile of growth_score"),
    "sent_mean_30d": ("Sentiment 30d", "mean article score over 30 days"),
    "sent_net_30d": ("Net sentiment 30d", "positive minus negative"),
    "news_count_30d": ("Article count 30d", "deduped articles in 30 days"),
    "news_z": ("News volume z", "article count vs its own baseline"),
    "severity_max": ("Peak severity", "highest measured event severity"),
    "mktcap": ("Market cap", "price x point-in-time share count"),
    "beta": ("Beta", "weekly regression vs SPY"),
    "wacc": ("WACC", "weighted average cost of capital"),
    "days_since_filing": ("Filing age", "days since the last 10-K/10-Q"),
    "hype_cov": ("Hype coverage", "fraction of components present"),
    "fund_cov": ("Fundamental coverage", "fraction of pillar inputs present"),
    "dip_cov": ("Dip coverage", "fraction of depression components present"),
}

METRIC_LABELS: dict[str, tuple[str, str]] = {**_fund_labels(), **_EXTRA_LABELS}


def label(metric: str) -> str:
    """Display name, from whichever dictionary documents the metric.

    `metrics_doc.DISPLAY` wins -- deriving the title from the description made
    `pe` print "price / earnings" twice. **`EXTRA_LABELS` is then checked**,
    because that is where a module's own metrics are documented and
    `metrics_doc.selftest` treats an entry there as documented. Without this
    branch the two disagreed: the selftest passed for `combo_h20` while the
    profile page rendered the raw key as "combo medium".
    """
    try:
        import metrics_doc
        if metric in metrics_doc.DISPLAY:
            return metrics_doc.DISPLAY[metric]
        extra = getattr(metrics_doc, "EXTRA_LABELS", {})
        if metric in extra:
            v = extra[metric]
            return v[0] if isinstance(v, (tuple, list)) and v else str(v)
    except Exception:                                            # noqa: BLE001
        pass
    return METRIC_LABELS.get(metric, (metric.replace("_", " "), ""))[0]


def tooltip(metric: str) -> str:
    """Rich tooltip from the dictionary when available, else the bare label."""
    try:
        import metrics_doc
        return metrics_doc.tooltip(metric)
    except Exception:                                            # noqa: BLE001
        return METRIC_LABELS.get(metric, ("", ""))[1]


def pillar_of(metric: str) -> str | None:
    try:
        import fund_metrics as FM
        spec = FM.REGISTRY.get(metric)
        return spec[0] if spec else None
    except Exception:                                            # noqa: BLE001
        return None


# ===========================================================================
# Components
# ===========================================================================
def chip(text: str, value=None, cls: str = "") -> str:
    inner = f"{esc(text)} <b>{esc(value)}</b>" if value is not None else esc(text)
    return f'<span class="chip {cls}">{inner}</span>'


def info(text: str) -> str:
    """A small (i) that opens a short explanation: the declutter rule of 2026-11-08 -- notes
    live behind this, not as paragraphs on the page. `text` is HTML the caller escaped."""
    return ('<details class="info"><summary title="what is this?">i</summary>'
            f'<div class="tip">{text}</div></details>')


def card(title: str | None, body: str) -> str:
    h = f"<h3>{esc(title)}</h3>" if title else ""
    return f'<div class="card">{h}{body}</div>'


# Metrics that are COUNTS, not measurements: rendering them with decimals
# ("3.00 articles", "27.00 sessions") reads as false precision.
COUNT_METRICS = frozenset({
    "news_count_5d", "news_count_30d", "news_count_90d", "f_score",
    "bars_used", "sent_age", "days_since_filing", "n_levels_found",
    "combo_h1_n", "combo_h20_n", "combo_h60_n",
})


def fmt_value(v, metric: str = "") -> str:
    """Human number. Absent renders as an em-dash, never as 0."""
    import numpy as np
    import pandas as pd
    if v is None or (isinstance(v, float) and not np.isfinite(v)) or pd.isna(v):
        return '<span class="muted">&mdash;</span>'
    if isinstance(v, str):
        return esc(v)
    v = float(v)
    if metric == "mktcap":
        return f"${v / 1e9:,.2f}B"
    # Counts are integers and reading "27.00 sessions" invites the question of
    # what the hundredths mean. The unit is spelled out because "27" alone was
    # ambiguous between days and sessions on the one metric whose whole purpose
    # is telling you how old the news is.
    if metric == "sent_age":
        return f"{v:,.0f} <span class='muted'>sess</span>"
    if metric in COUNT_METRICS:
        return f"{v:,.0f}"
    a = abs(v)
    if a >= 1e9:
        return f"{v / 1e9:,.2f}B"
    if a >= 1e6:
        return f"{v / 1e6:,.1f}M"
    if a >= 1000:
        return f"{v:,.0f}"
    return f"{v:,.2f}"


def pct_bar(pct, caption: str = "", direction: int = 0,
            title: str = "") -> str:
    """A 0-100 percentile bar, coloured by whether high is GOOD for this metric.

    THE BUG THIS FIXES. Colour used to be "green above 50" for everything, so
    Apple's P/E sitting at the 93rd percentile of its own history -- close to the
    most expensive it has ever been -- rendered as a full green bar reading
    "excellent". `fund_metrics.REGISTRY` has carried `direction=-1` for P/E all
    along and nothing consulted it.

    LENGTH always means the percentile; only COLOUR carries the judgement. If
    both moved, a short bar could mean either "low value" or "bad", and the
    reader could not tell which.

      direction +1  high is good   -> green high, red low
      direction -1  high is bad    -> RED high, green low
      direction  0  unknown        -> NEUTRAL grey, no claim either way

    Zero is the honest default. Every hype metric is direction 0 because that
    module deliberately refuses to assume a sign, and colouring it would invent
    a claim the code explicitly declines to make.

    A MISSING percentile is a hatched track labelled "n/a", never a zero-width
    bar -- a grey empty bar is indistinguishable from "ranked worst".
    """
    import numpy as np
    import pandas as pd
    tip = f' title="{esc(title)}"' if title else ""
    if pct is None or (isinstance(pct, float) and not np.isfinite(pct)) \
            or pd.isna(pct):
        return (f'<div class="pctbar-wrap"{tip}><div class="pctbar na"></div>'
                '<em>n/a</em></div>')
    p = max(0.0, min(100.0, float(pct)))
    if direction > 0:
        colour = "var(--pos)" if p >= 50 else "var(--neg)"
    elif direction < 0:
        colour = "var(--neg)" if p >= 50 else "var(--pos)"
    else:
        colour = "var(--muted)"
    cap = esc(caption) if caption else f"{p:.0f}"
    return (f'<div class="pctbar-wrap"{tip}><div class="pctbar">'
            f'<i style="width:{p:.1f}%;background:{colour}"></i></div>'
            f'<em>{cap}</em></div>')


def metric_row(metric: str, value, vs_industry=None, vs_history=None,
               direction: int = 0, value_txt: str = "") -> str:
    """One metric: label, value, and the two percentile bars.

    `direction` decides bar COLOUR only -- see pct_bar. The two tooltips spell
    the comparison out in words, because "93" on a bar told the reader nothing
    about what it was 93 of.
    """
    # The inline subtitle shows the ACTIONABLE half only. Showing measures+how
    # inline printed near-duplicates -- "deduped articles in 30 days - deduped
    # articles in 30 days. 27.6% of raw pairs..." -- which reads as a stutter.
    # The full text still arrives on hover.
    tip = tooltip(metric)
    try:
        import metrics_doc
        e = metrics_doc.entry(metric)
        short = e["how"] or e["measures"]
        if e["range"]:
            short = f"{short}  ({e['range']})"
    except Exception:                                            # noqa: BLE001
        short = tip
    sub = f'<small title="{esc(tip)}">{esc(short)}</small>' if short else ""
    shown = value_txt or fmt_value(value, metric)
    sense = ("lower is better for this metric" if direction < 0
             else "higher is better for this metric" if direction > 0
             else "direction not measured -- no colour is implied")
    ind_t = (f"{label(metric)} is higher than {vs_industry:.0f}% of same-sector "
             f"peers this session. {sense}."
             if vs_industry is not None and vs_industry == vs_industry else
             f"{label(metric)}: no sector comparison (needs 5+ peers)")
    hist_t = (f"{label(metric)} is higher than {vs_history:.0f}% of this "
              f"company's OWN history. {sense}."
              if vs_history is not None and vs_history == vs_history else
              f"{label(metric)}: no history comparison (needs 8+ observations)")
    return (f'<div class="mrow">'
            f'<div class="name">{esc(label(metric))}{sub}</div>'
            f'<div class="val">{shown}</div>'
            f'{pct_bar(vs_industry, direction=direction, title=ind_t)}'
            f'{pct_bar(vs_history, direction=direction, title=hist_t)}</div>')


def metric_head() -> str:
    return ('<div class="mhead"><span>Metric</span><span>Value</span>'
            '<span>Vs industry</span><span>Vs history</span></div>')


def sparkline(vals, w: int = 78, h: int = 20) -> str:
    import numpy as np
    import pandas as pd
    s = [float(v) for v in vals
         if v is not None and pd.notna(v) and np.isfinite(v)]
    if len(s) < 2:
        return ""
    lo, hi = min(s), max(s)
    rng = (hi - lo) or 1.0
    n = len(s)
    pts = " ".join(
        f"{i / (n - 1) * (w - 2) + 1:.1f},{h - 1 - (v - lo) / rng * (h - 2):.1f}"
        for i, v in enumerate(s))
    col = "var(--pos)" if s[-1] >= s[0] else "var(--neg)"
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'preserveAspectRatio="none" aria-hidden="true">'
            f'<polyline points="{pts}" fill="none" stroke="{col}" '
            f'stroke-width="1.4" stroke-linejoin="round"/></svg>')


def nice_step(span: float, target: int = 5) -> float:
    """A round axis step: 1, 2, 2.5 or 5 times a power of ten.

    Dividing the range into N equal parts gives ticks like 1.48 / 4.08 / 6.68 --
    technically correct and unreadable, because nobody eyeballs a value against
    a 2.6-unit grid. Rounding the STEP instead gives 2 / 4 / 6.
    """
    if not (span > 0) or not math.isfinite(span):
        return 1.0
    raw = span / max(target, 1)
    mag = 10.0 ** math.floor(math.log10(raw))
    for m in (1.0, 2.0, 2.5, 5.0):
        if raw <= m * mag:
            return m * mag
    return 10.0 * mag


def nice_ticks(lo: float, hi: float, target: int = 5,
               lo_n: int = 4, hi_n: int = 8) -> list[float]:
    """Round tick values covering [lo, hi], and ENOUGH of them.

    Rounding the step alone is not sufficient: `nice_step` snaps up the ladder,
    and one snap too far leaves a chart with two gridlines. AAPL's 1Y range
    produced exactly `250, 300`. So candidate steps are generated and the one
    whose tick COUNT lands closest to `target` wins, with a floor of `lo_n`.
    """
    span = hi - lo
    if not (span > 0) or not math.isfinite(span):
        return [lo]

    def ticks_for(step):
        if step <= 0:
            return []
        start = math.ceil(lo / step) * step
        out, v = [], start
        while v <= hi + step * 1e-9 and len(out) < 40:
            out.append(round(v, 10))
            v += step
        return out

    mag = 10.0 ** math.floor(math.log10(span / max(target, 1)))
    cands = [m * mag * f for f in (0.1, 1.0, 10.0)
             for m in (1.0, 2.0, 2.5, 5.0)]
    best, best_key = [], None
    for step in sorted(set(cands)):
        ts = ticks_for(step)
        if not ts:
            continue
        # Prefer counts inside [lo_n, hi_n]; among those, closest to target.
        inside = 0 if lo_n <= len(ts) <= hi_n else 1
        key = (inside, abs(len(ts) - target))
        if best_key is None or key < best_key:
            best, best_key = ts, key
    return best or ticks_for(nice_step(span, target))


def price_chart(dates, closes, volumes=None, level=None, base=None,
                w: int = 980, h: int = 270, label: str = "",
                markers=None) -> str:
    """Close line + volume + the support level, as one inline SVG.

    HAND-BUILT because every page here must open from `file://` with no network,
    which rules out every charting library. Same constraint that made `radar()`
    and `sparkline()` raw SVG.

    THE SUPPORT LEVEL IS THE POINT. A price line on its own is decoration; the
    screener's whole thesis is "parabolic run, full retrace, base holds, bounce",
    and `level` plus the `base` band are the two numbers that describe it. Drawn
    from `screen.screen_one`, so the chart cannot disagree with the gate that
    used them.

    `preserveAspectRatio` is left at its default and the SVG is width:100% with
    height:auto, so it scales down to a phone without the labels colliding --
    unlike `sparkline`, which stretches on purpose.

    `markers`: optional (date, kind, title) with kind "buy", "sell" or "house" --
    insider purchases (green, under the line), sales (red, over it) and House
    purchases (purple). Each lands on the first bar ON OR AFTER its date (a weekly
    or monthly bar holds the dates inside it); several on one bar become one mark
    whose tooltip lists them. Outside the window: not drawn.
    """
    import numpy as np
    import pandas as pd

    pts = [(str(d), float(c)) for d, c in zip(dates, closes)
           if c is not None and pd.notna(c) and np.isfinite(float(c))]
    if len(pts) < 3:
        return ('<div class="note">no price history stored for this name</div>')
    ds = [p[0] for p in pts]
    cs = [p[1] for p in pts]
    n = len(cs)

    padl, padr, padt = 52, 8, 10
    pb = int(h * 0.70)                 # price band bottom
    vb_top = pb + 16                   # volume band top
    plot_w = w - padl - padr

    lo, hi = min(cs), max(cs)
    # The level must be INSIDE the y-range or the line is drawn off-canvas and
    # silently invisible -- which would read as "no support found".
    cands = [lo, hi] + ([float(level)] if level and np.isfinite(float(level)) else [])
    if base and all(b is not None and np.isfinite(float(b)) for b in base):
        cands += [float(base[0]), float(base[1])]
    lo, hi = min(cands), max(cands)
    rng = (hi - lo) or 1.0
    lo -= rng * 0.06
    hi += rng * 0.06
    rng = hi - lo

    def X(i):
        return padl + (i / (n - 1)) * plot_w

    def Y(v):
        return padt + (1 - (v - lo) / rng) * (pb - padt)

    out = [f'<svg viewBox="0 0 {w} {h}" class="pchart" role="img" '
           f'aria-label="{esc(label or "price history")}">']

    # gridlines + price axis, on ROUND values
    dec = 0 if (hi - lo) >= 50 else (1 if (hi - lo) >= 5 else 2)
    for v in nice_ticks(lo, hi, 5):
        y = Y(v)
        out.append(f'<line x1="{padl}" y1="{y:.1f}" x2="{w - padr}" y2="{y:.1f}" '
                   f'class="pgrid"/>')
        out.append(f'<text x="{padl - 6}" y="{y + 3.5:.1f}" class="pax" '
                   f'text-anchor="end">{v:,.{dec}f}</text>')

    # the base band, then the level on top of it
    if base and all(b is not None and np.isfinite(float(b)) for b in base):
        b0, b1 = sorted((float(base[0]), float(base[1])))
        y0, y1 = Y(b1), Y(b0)
        out.append(f'<rect x="{padl}" y="{y0:.1f}" width="{plot_w}" '
                   f'height="{max(y1 - y0, 1):.1f}" class="pbase"/>')
    if level and np.isfinite(float(level)):
        y = Y(float(level))
        out.append(f'<line x1="{padl}" y1="{y:.1f}" x2="{w - padr}" y2="{y:.1f}" '
                   f'class="plevel"/>')
        out.append(f'<text x="{w - padr - 2}" y="{y - 5:.1f}" class="plevtxt" '
                   f'text-anchor="end">support {float(level):,.2f}</text>')

    # volume
    if volumes is not None:
        vs = [float(v) if v is not None and pd.notna(v) and np.isfinite(float(v))
              else 0.0 for v in volumes][:n]
        vs += [0.0] * (n - len(vs))
        vmax = max(vs) or 1.0
        bw = max(plot_w / n * 0.8, 0.6)
        for i, v in enumerate(vs):
            bh = (v / vmax) * (h - vb_top - 4)
            if bh <= 0:
                continue
            out.append(f'<rect x="{X(i) - bw / 2:.1f}" y="{h - 4 - bh:.1f}" '
                       f'width="{bw:.1f}" height="{bh:.1f}" class="pvol"/>')

    # the price line, coloured by the direction of the whole window
    poly = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(cs))
    cls = "pline up" if cs[-1] >= cs[0] else "pline down"
    out.append(f'<polyline points="{poly}" class="{cls}"/>')

    if markers:
        import bisect
        groups: dict[tuple[int, str], list[str]] = {}
        for d, kind, title in markers:
            d = str(d)[:10]
            if d < ds[0][:10] or d > ds[-1][:10]:
                continue
            i = min(bisect.bisect_left([x[:10] for x in ds], d), n - 1)
            groups.setdefault((i, kind), []).append(str(title))
        for (i, kind), titles in sorted(groups.items()):
            x, y = X(i), Y(cs[i])
            if kind == "sell":
                pts_ = f"{x - 4.5:.1f},{y - 12:.1f} {x + 4.5:.1f},{y - 12:.1f} {x:.1f},{y - 4:.1f}"
            elif kind == "house":
                pts_ = f"{x:.1f},{y + 5:.1f} {x + 4.5:.1f},{y + 10:.1f} {x:.1f},{y + 15:.1f} {x - 4.5:.1f},{y + 10:.1f}"
            else:
                pts_ = f"{x:.1f},{y + 4:.1f} {x + 4.5:.1f},{y + 12:.1f} {x - 4.5:.1f},{y + 12:.1f}"
            tip = esc("; ".join(titles[:6]) + (f"; +{len(titles) - 6} more" if len(titles) > 6 else ""))
            out.append(f'<polygon points="{pts_}" class="pmk-{esc(kind)}"><title>{tip}</title></polygon>')

    # date axis. Three labels six months apart was unreadable; this puts one
    # roughly every 160px and drops the format down to the useful part -- a
    # one-month window does not need the year on every tick.
    n_lab = max(2, min(8, int(plot_w // 150)))
    span_days = 0
    try:
        span_days = (pd.Timestamp(ds[-1]) - pd.Timestamp(ds[0])).days
    except Exception:                                            # noqa: BLE001
        span_days = 0
    if span_days > 1500:
        fmt = lambda s: s[:4]                                    # noqa: E731
    elif span_days > 200:
        fmt = lambda s: s[:7]                                    # noqa: E731
    else:
        fmt = lambda s: s[5:10]                                  # noqa: E731
    seen = set()
    for k in range(n_lab + 1):
        i = min(int(round(k * (n - 1) / n_lab)), n - 1)
        lab_txt = fmt(ds[i])
        if lab_txt in seen:
            continue
        seen.add(lab_txt)
        anc = "start" if k == 0 else ("end" if k == n_lab else "middle")
        out.append(f'<text x="{X(i):.1f}" y="{h - 0.5}" class="pax" '
                   f'text-anchor="{anc}">{esc(lab_txt)}</text>')

    out.append("</svg>")
    return "".join(out)


# Geometry for radar(). The viewBox is WIDER THAN TALL on purpose: axis labels
# sit outside the polygon at radius*1.19 and the left/right ones overflow a
# square box. The first version used `0 0 250 250` and clipped "sentiment" to
# "sen" and "technicals" to "cas" -- invisible in code, obvious on screen.
RADAR_SIZE = 250
RADAR_VB = (-62, -6, 374, 266)          # min-x, min-y, width, height


def radar(axes: dict, size: int = RADAR_SIZE) -> str:
    """Score radar, 0-100 per axis. A missing axis is a GAP in the polygon,
    never a point at zero."""
    import pandas as pd
    names = list(axes)
    n = len(names)
    if n < 3:
        return '<p class="note">radar needs at least 3 axes</p>'
    cx = cy = size / 2
    r = size / 2 - 34

    def pt(i, frac):
        ang = -math.pi / 2 + 2 * math.pi * i / n
        return cx + r * frac * math.cos(ang), cy + r * frac * math.sin(ang)

    rings = "".join(
        '<polygon points="{}" fill="none" stroke="var(--grid)" '
        'stroke-width="1"/>'.format(
            " ".join(f"{x:.1f},{y:.1f}"
                     for x, y in (pt(i, f) for i in range(n))))
        for f in (0.25, 0.5, 0.75, 1.0))
    spokes = "".join(
        '<line x1="{:.1f}" y1="{:.1f}" x2="{:.1f}" y2="{:.1f}" '
        'stroke="var(--grid)" stroke-width="1"/>'.format(cx, cy, *pt(i, 1.0))
        for i in range(n))

    have = [(i, axes[k]) for i, k in enumerate(names)
            if axes[k] is not None and pd.notna(axes[k])]
    poly = dots = ""
    if len(have) >= 3:
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in
                       (pt(i, max(0.0, min(1.0, v / 100.0))) for i, v in have))
        poly = (f'<polygon points="{pts}" fill="rgba(47,111,235,0.22)" '
                f'stroke="var(--accent)" stroke-width="2"/>')
    dots = "".join(
        '<circle cx="{:.1f}" cy="{:.1f}" r="3.2" fill="var(--accent)"/>'.format(
            *pt(i, max(0.0, min(1.0, v / 100.0)))) for i, v in have)

    labels = ""
    for i, k in enumerate(names):
        x, y = pt(i, 1.19)
        v = axes[k]
        txt = "n/a" if v is None or pd.isna(v) else f"{float(v):.0f}"
        anchor = "middle" if abs(x - cx) < 6 else ("start" if x > cx else "end")
        labels += (f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" '
                   f'font-size="11" fill="var(--muted)">{esc(k)}</text>'
                   f'<text x="{x:.1f}" y="{y + 13:.1f}" text-anchor="{anchor}" '
                   f'font-size="13" font-weight="700" fill="var(--ink)">'
                   f'{txt}</text>')

    missing = [k for k in names if axes[k] is None or pd.isna(axes[k])]
    note = (f'<div class="note">no data for: {esc(", ".join(missing))}</div>'
            if missing else "")
    vb = " ".join(str(x) for x in RADAR_VB)
    return (f'<svg viewBox="{vb}" width="100%" '
            f'style="max-width:340px;display:block;margin:0 auto">'
            f'{rings}{spokes}{poly}{dots}{labels}</svg>{note}')


def session_picker(current: str, available: list[str], pattern: str,
                   static: set[str] | None = None,
                   latest: str = "latest.html") -> str:
    """A <select> that navigates to a dated snapshot on change.

    `pattern` contains `{d}` -- e.g. `explore_{d}.html`. The newest entry points
    at the undated "latest" alias so the default page is never a dead end.

    `static` is the subset that exists as a plain file. Everything else is
    stored data that the server renders on demand, and picking one over
    `file://` would hit a missing file -- a broken link, which this project has
    already been called out for. So those options are MARKED, and on file:// the
    picker says what to run instead of navigating into a 404.
    """
    if not available:
        return ""
    have = set(static) if static is not None else set(available)
    opts = []
    for i, d in enumerate(sorted(set(available), reverse=True)):
        # The newest entry points at the undated alias, which is passed in
        # rather than derived. Deriving it as `pattern.split("_")[0] + ".html"`
        # worked only while every pattern happened to contain an underscore --
        # the moment explore's became `{d}.html` it produced `{d}.html.html`.
        href = pattern.replace("{d}", d) if i else latest
        sel = " selected" if d == current else ""
        live = "" if (d in have or i == 0) else ' data-live="1"'
        # Built AFTER esc(), and pure ASCII in source: a raw U+00B7 here was
        # written through cp1252 and rendered as a replacement character.
        tag = esc(d) + (" (latest)" if i == 0
                        else ("" if d in have else " &middot; live"))
        opts.append(f'<option value="{esc(href)}"{sel}{live}>{tag}</option>')
    return (
        '<select title="jump to a previous session" onchange="'
        "var o=this.options[this.selectedIndex];"
        "if(!o.value)return;"
        "if(o.dataset.live&&location.protocol==='file:'){"
        "alert('That session is stored but has no saved page.\\n\\n"
        "Run Screener.bat (or: python serve.py --idle-exit 30) and it will be "
        "built on demand.');this.value='';return;}"
        'location.href=o.value">' + "".join(opts) + "</select>")


def th(text: str, metric: str | None = None, cls: str = "") -> str:
    """A table header whose tooltip comes from the metric dictionary.

    Every dashboard should build headers with this rather than a bare `<th>`,
    so a column can never display a name the reference page cannot explain.
    `metric` may differ from the visible text -- "Z" is `z_score`.
    """
    tip = ""
    if metric:
        try:
            import metrics_doc
            tip = metrics_doc.tooltip(metric)
        except Exception:                                        # noqa: BLE001
            tip = ""
    c = f' class="{cls}"' if cls else ""
    a = f' title="{esc(tip)}"' if tip else ""
    return f"<th{c}{a}>{text}</th>"


# The menu, grouped (2026-11-08: 15 flat items took a third of a phone screen). "today" is
# the top-level home link; every other NAV key sits in exactly one group (ui.selftest).
NAV_GROUPS = [
    ("Lists", [("shortlist", "the best-tested daily list"), ("watchlist", "saved lists, tracked nightly"),
               ("candidates", "names at a level, with scores"), ("floor", "retests after a big run"),
               ("bounce", "the original bounce screen"), ("zones", "every support zone near price")]),
    ("Research", [("explore", "every stock, every score"), ("sectors", "sector and sub-industry readings"),
                  ("insider", "directors' and officers' buying"), ("house", "House members' trades"),
                  ("sentiment", "news tone"), ("fundamental", "business quality")]),
    ("Stocks", [("profiles", "every stock page")]),
    ("System", [("hub", "jobs, data and runs"), ("metrics", "what every number means")]),
]

NAV_JS = r"""<script>
(function(){
  if (window.__scrNav) return; window.__scrNav = 1;
  window.scrFind = function(f, up){
    var t = (f.t.value || '').trim().toUpperCase().replace(/[^A-Z0-9.\-]/g, '');
    if (t) location.href = up + 'stock/index.html?t=' + encodeURIComponent(t);
    return false;
  };
  // Menus and (i) notes: one open at a time, closed by a click elsewhere or Escape.
  var OPEN = '.nav details.dd[open], details.info[open]';
  function closeAll(except){
    document.querySelectorAll(OPEN).forEach(function(d){ if (d !== except) d.removeAttribute('open'); });
  }
  // An (i) near the right edge would push its note past the screen (measured: up to 65px
  // at 375px wide) and the page would scroll sideways. Shift it back inside, 8px clear.
  function fitTip(d){
    var tip = d.querySelector('.tip'); if (!tip) return;
    tip.style.left = '';
    var r = tip.getBoundingClientRect(), vw = document.documentElement.clientWidth, pad = 8, shift = 0;
    if (r.right > vw - pad) shift = (vw - pad) - r.right;
    if (r.left + shift < pad) shift = pad - r.left;
    if (shift) tip.style.left = ((parseFloat(getComputedStyle(tip).left) || 0) + shift) + 'px';
  }
  document.addEventListener('click', function(e){
    document.querySelectorAll(OPEN).forEach(function(d){ if (!d.contains(e.target)) d.removeAttribute('open'); });
  });
  document.addEventListener('keydown', function(e){ if (e.key === 'Escape') closeAll(null); });
  // Tables drawn by script link every row to stock/<T>.html, but only ~120 of ~3,500 names
  // have a page. The local server builds a missing one on request; opened as a FILE the link
  // is dead. So over file:// a stock link goes through the stock list, which opens the page
  // when it exists and otherwise offers it to build. Rewritten at click time (left, middle,
  // ctrl-click alike), so a page that exists costs one quick hop.
  function viaList(e){
    if (location.protocol !== 'file:' || !e.target || !e.target.closest) return;
    var a = e.target.closest('a[href]'); if (!a) return;
    var m = (a.getAttribute('href') || '').match(/^((?:\.\.\/)*)stock\/([A-Za-z0-9.\-]+)\.html$/);
    if (m && m[2] !== 'index') a.setAttribute('href', m[1] + 'stock/index.html?t=' + encodeURIComponent(m[2]));
  }
  document.addEventListener('click', viaList, true);
  document.addEventListener('auxclick', viaList, true);
  document.addEventListener('toggle', function(e){
    var d = e.target; if (!d.matches || !d.open) return;
    if (d.matches('.nav details.dd') || d.matches('details.info')) closeAll(d);
    if (d.matches('details.info')) fitTip(d);
  }, true);
})();
</script>"""


def nav(active: str = "", depth: int = 0, extra: str = "") -> str:
    """The header. `depth` is how many directories deep the page sits, so
    `reports/stock/AAPL.html` passes depth=1 and gets `../index.html`.
    Today, four groups (native <details> dropdowns) and a ticker box."""
    up = "../" * depth
    href = {k: h for k, _t, h in NAV}
    text = {k: t for k, t, _h in NAV}
    parts = [f'<a class="brand" href="{up}index.html">SCREENER</a>',
             f'<a class="top{" on" if active == "today" else ""}" href="{up}index.html">Today</a>']
    def find(cls: str) -> str:
        return (f'<form class="{cls}" onsubmit="return scrFind(this,\'' + up + '\')">'
                '<input name="t" placeholder="Ticker" aria-label="Open a stock page" '
                'autocomplete="off" spellcheck="false"></form>')
    for group, items in NAV_GROUPS:
        on = any(k == active for k, _h in items)
        links = "".join(f'<a class="{"on" if k == active else ""}" href="{up}{href[k]}">{text[k]}'
                        f'<span class="hint">{esc(hint)}</span></a>' for k, hint in items)
        # The Stocks menu carries the ticker box too: on a phone the inline box is hidden so
        # the whole menu stays on one line (measured: it wrapped to 70px at 375px wide).
        if group == "Stocks":
            links = find("find in-menu") + links
        parts.append(f'<details class="dd{" on" if on else ""}"><summary>{group}</summary>'
                     f'<div class="menu">{links}</div></details>')
    parts.append(find("find inline"))
    tail = f'<span class="spacer"></span>{EXTRA_START}{extra}{EXTRA_END}' if extra else ""
    return (f'{NAV_START}<div class="nav"><div class="inner">{"".join(parts)}{tail}</div></div>'
            f'{NAV_JS}{NAV_END}')


# ---------------------------------------------------------------- menus on pages already written
# A page is a static file: a menu change reaches only the pages built after it. 412 pages were
# on disk on 2026-11-08, most of them dated snapshots that are never rebuilt, and their
# "Status" link pointed at index.html -- which became Today that day. `refresh_nav()` swaps in
# the current menu (and its styles) on every page, keeping each page's own session picker and
# highlighted entry. The markers make it exact; pages from before the markers are recognised by
# the old menu's one fixed shape. Unchanged pages are not rewritten, so running it again is cheap.
NAV_START, NAV_END = "<!--nav:start-->", "<!--nav:end-->"
EXTRA_START, EXTRA_END = "<!--nav:extra-->", "<!--nav:/extra-->"
_CSS_START, _CSS_END = "/* nav:start */", "/* nav:end */"
_OLD_NAV = re.compile(r'<div class="nav"><div class="inner">(.*?)</div></div>', re.S)
_ON = re.compile(r'<a class="(?:[^"]*\s)?on(?:\s[^"]*)?" href="([^"]*)"')
# Links in page BODIES that meant the job table, the home page until 2026-11-08.
_HUB_LINKS = (('href="../index.html">status hub</a>', 'href="../system.html">Status</a>'),
              ('href="index.html">status hub</a>', 'href="system.html">Status</a>'))


def built_profiles() -> set[str]:
    """Tickers with a stock page on disk now. Read fresh: the profiles step adds pages mid-run."""
    import config
    d = config.REPORTS / "stock"
    return {p.stem for p in d.glob("*.html") if p.stem != "index"} if d.exists() else set()


def stock_href(ticker: str, depth: int = 0, built: set[str] | None = None) -> str:
    """Where a ticker link should point: its page when one is built, else the stock list with the
    ticker filled in (which opens the page if it appears later, and offers to build it). Only
    ~120 of ~3,500 names have a page, and on 2026-11-08 336 links on the watchlist pages led
    to files that did not exist."""
    t = str(ticker)
    built = built_profiles() if built is None else built
    up = "../" * depth
    return f"{up}stock/{esc(t)}.html" if t in built else f"{up}stock/index.html?t={esc(t)}"


def _menu_of(html: str) -> tuple[int, int, str, str] | None:
    """(start, end, highlighted href, session picker) of a page's menu; None if it has none."""
    i = html.find(NAV_START)
    if i >= 0:
        j = html.find(NAV_END, i)
        if j < 0:
            return None
        end = j + len(NAV_END)
        block = html[i:end]
        a, b = block.find(EXTRA_START), block.find(EXTRA_END)
        extra = block[a + len(EXTRA_START):b] if 0 <= a < b else ""
    else:
        m = _OLD_NAV.search(html)
        if not m or "<div" in m.group(1):          # not the old menu's shape: leave the page alone
            return None
        i, end, block = m.start(), m.end(), m.group(1)
        k = block.find('<span class="spacer"></span>')
        extra = block[k + len('<span class="spacer"></span>'):] if k >= 0 else ""
    on = _ON.search(html, i, end)
    return i, end, (on.group(1) if on else ""), extra


def _active_key(href: str, depth: int) -> str:
    up = "../" * depth
    rel = href[len(up):] if up and href.startswith(up) else href
    return next((k for k, _t, h in NAV if h == rel), "")


def refresh_nav(root=None, verbose: bool = True) -> dict:
    """Bring every page under `root` (default reports/) to the current menu. Returns counts."""
    from pathlib import Path
    if root is None:
        import config
        root = config.REPORTS
    root = Path(root)
    import os
    out = {"pages": 0, "updated": 0, "no_menu": [], "busy": []}
    for f in sorted(root.rglob("*.html")):
        out["pages"] += 1
        try:
            seen = f.stat().st_mtime_ns
            old = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            out["busy"].append(str(f.relative_to(root)))
            continue
        new = old
        depth = len(f.relative_to(root).parts) - 1
        got = _menu_of(new)
        if got:
            i, end, on, extra = got
            new = new[:i] + nav(_active_key(on, depth), depth, extra) + new[end:]
            a, b = new.find(_CSS_START), new.find(_CSS_END)
            if 0 <= a < b:
                new = new[:a] + NAV_CSS.rstrip("\n") + new[b + len(_CSS_END):]
            elif "</head>" in new:
                k = new.index("</head>")
                new = new[:k] + f"<style>{NAV_CSS}</style>" + new[k:]
        else:
            out["no_menu"].append(str(f.relative_to(root)))
        for a, b in _HUB_LINKS:
            new = new.replace(a, b)
        if new != old:
            # Atomic, and only if nothing rewrote the page meanwhile: the local server builds
            # stock pages on request, and a fresh page must never be overwritten by its old copy.
            tmp = f.with_name(f".{f.name}.renav")
            tmp.write_text(new, encoding="utf-8")
            try:
                if f.stat().st_mtime_ns != seen:
                    out["busy"].append(str(f.relative_to(root)))
                    continue
                os.replace(tmp, f)
                out["updated"] += 1
            finally:
                if tmp.exists():
                    tmp.unlink()
    if verbose:
        print(f"  menus: {out['updated']} of {out['pages']} page(s) updated"
              + (f"; no menu on {len(out['no_menu'])}: {', '.join(out['no_menu'][:5])}" if out["no_menu"] else "")
              + (f"; skipped {len(out['busy'])} being rewritten" if out["busy"] else ""))
    return out


# Injected into every page. Gives each `.scroll.sticky-x` container a mirrored
# scrollbar above it, so a wide table is reachable from the top of the page.
# The MutationObserver matters: the profile's annual/quarterly toggle REPLACES
# the table, and without it the bar would keep the old table's width and scroll
# to nowhere.
SCROLL_SYNC_JS = """
(function(){
  function attach(box){
    if(box.dataset.xbar) return; box.dataset.xbar='1';
    var bar=document.createElement('div'); bar.className='xbar';
    var inner=document.createElement('div'); bar.appendChild(inner);
    box.parentNode.insertBefore(bar,box);
    function sync(){
      var t=box.querySelector('table');
      // Size the proxy bar from the BOX's scroll width, not the table's. They
      // differ by the box's own padding/borders, and a 12px difference means
      // the two elements have different maximum scrollLeft -- so scrolling the
      // box to its end synced the bar to ITS smaller end, which echoed back
      // and left the box 12px short. That shortfall clipped the last column's
      // value mid-character.
      var w=box.scrollWidth||(t?t.scrollWidth:0);
      inner.style.width=w+'px';
      bar.style.display=(w>box.clientWidth+1)?'block':'none';
    }
    var lock=false;
    bar.addEventListener('scroll',function(){
      if(lock)return; lock=true; box.scrollLeft=bar.scrollLeft; lock=false;});
    box.addEventListener('scroll',function(){
      if(lock)return; lock=true; bar.scrollLeft=box.scrollLeft; lock=false;});
    window.addEventListener('resize',sync);
    try{
      new MutationObserver(sync).observe(box,{childList:true,subtree:true});
      // The annual/quarterly and 6m/1y/5y toggles flip an inline `style` on an
      // ANCESTOR pane. Measured while hidden the table is 0 wide, so the bar
      // hid itself and never reappeared. Watch the pane's attributes too.
      var pane=box.closest('[data-freq],[data-win]')||box.parentNode;
      if(pane) new MutationObserver(sync).observe(
          pane,{attributes:true,attributeFilter:['style','class']});
    }catch(e){}
    // `start-end` opens the box on its RIGHTMOST column. For a financials
    // table that is the newest period, which is the one the reader came for --
    // and it lands on the true end, where letting the browser choose settled
    // 11px short and clipped the last value mid-character.
    function toEnd(){
      if(!box.classList.contains('start-end')) return;
      if(box.scrollWidth<=box.clientWidth) return;
      // Hold the lock across BOTH writes. Without it the bar's scroll event
      // fires mid-pin and copies its own clamped value back over the box.
      lock=true;
      box.scrollLeft=box.scrollWidth;
      bar.scrollLeft=box.scrollLeft;
      lock=false;
    }
    function syncEnd(){ sync(); toEnd(); }
    sync(); toEnd();
    // One deferred pass: on first paint a hidden pane still measures 0, so the
    // pin has nothing to pin to. Re-run once layout is real, and again when a
    // hidden pane is revealed by the annual/quarterly toggle.
    setTimeout(syncEnd,0);
    try{
      var pane2=box.closest('[data-freq],[data-win]');
      if(pane2) new MutationObserver(function(){setTimeout(syncEnd,0);})
          .observe(pane2,{attributes:true,attributeFilter:['style','class']});
    }catch(e){}
  }
  function all(){ document.querySelectorAll('.scroll.sticky-x').forEach(attach); }
  if(document.readyState!=='loading') all();
  else document.addEventListener('DOMContentLoaded',all);
})();
"""


def page(title: str, body: str, subtitle: str = "", active: str = "",
         depth: int = 0, nav_extra: str = "", head: str = "",
         script: str = "") -> str:
    """The whole document. Generators supply body content only."""
    heading = f"<h1>{title}</h1>" if title else ""
    sub = f'<div class="sub">{subtitle}</div>' if subtitle else ""
    # SCROLL_SYNC_JS goes on every page rather than being opted into: it is a
    # no-op without a `.scroll.sticky-x` container, and the one page that
    # forgets to opt in is the one with the unreachable table.
    combined = SCROLL_SYNC_JS + (script or "")
    js = f"<script>{combined}</script>"
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title) or "Screener"}</title><style>{CSS}</style>{head}</head>
<body>{nav(active, depth, nav_extra)}<div class="wrap">
{heading}{sub}{body}
</div>{js}</body></html>"""


# ===========================================================================
def _class_names(css: str) -> set[str]:
    """Bare `.class` selectors in a stylesheet. Used by the collision guard.

    Comments are stripped FIRST: a comment saying "do not redefine .wrap" would
    otherwise be read as a definition of `.wrap` and fail the guard it explains.
    """
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out = set()
    for sel in re.findall(r"([^{}]+)\{", css):
        for part in sel.split(","):
            part = part.strip()
            m = re.fullmatch(r"\.([A-Za-z][\w-]*)", part)
            if m:
                out.add(m.group(1))
    return out


def _palettes() -> dict[str, dict[str, str]]:
    """The two colour palettes, parsed out of CSS rather than duplicated here.

    Duplicating them is how a stylesheet and its own accessibility test drift
    apart: the test keeps passing against colours the page stopped using.
    """
    out = {}
    for name, marker in (("light", ":root{"),
                         ("dark", "@media (prefers-color-scheme:dark){:root{")):
        i = CSS.index(marker)
        block = CSS[i:CSS.index("}", i)]
        pal = {}
        for k, v in re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{3,6})", block):
            pal[k] = ("#" + "".join(c * 2 for c in v[1:])) if len(v) == 4 else v
        out[name] = pal
    return out


def _luminance(hexc: str) -> float:
    ch = [int(hexc[k:k + 2], 16) / 255 for k in (1, 3, 5)]
    ch = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in ch]
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]


def contrast(fg: str, bg: str) -> float:
    """WCAG 2.1 contrast ratio, 1.0 to 21.0."""
    hi, lo = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# Every foreground/background pair the stylesheet actually renders. All of this
# text is under 18px, so the AA floor is 4.5:1 with no large-text exemption.
CONTRAST_PAIRS = (
    ("ink", "bg"), ("ink", "panel"), ("ink", "head"),
    ("muted", "bg"), ("muted", "panel"), ("muted", "head"), ("muted", "skipbg"),
    ("accent", "bg"), ("accent", "panel"),
    ("pos", "panel"), ("neg", "panel"), ("warn", "panel"),
    ("pos", "posbg"), ("neg", "negbg"), ("warn", "warnbg"),
    ("blk", "blkbg"), ("skip", "skipbg"),
)
AA = 4.5


def check_contrast() -> list[str]:
    """MEASURED, not eyeballed. The dark theme was the one assumed to be
    unreviewed; measuring found it clean at 5.06:1 worst case and caught two
    LIGHT failures instead -- links at 4.30:1 and skipped-pills at 4.05:1."""
    bad = []
    for theme, pal in _palettes().items():
        for fg, bg in CONTRAST_PAIRS:
            a, b = pal.get(f"--{fg}"), pal.get(f"--{bg}")
            if not a or not b:
                continue
            r = contrast(a, b)
            if r < AA:
                bad.append(f"{theme}: --{fg} on --{bg} is {r:.2f}:1 (need {AA})")
    return bad


def selftest(verbose: bool = True) -> None:
    # --- the radar clipping regression -------------------------------------
    # Every label must fall INSIDE the viewBox. The original square box clipped
    # "sentiment" (right edge x=280 vs box 250) and "technicals" (left x=-30).
    # Approximated with a 6.2px-per-char advance at font-size 11.
    minx, miny, w, h = RADAR_VB
    maxx, maxy = minx + w, miny + h
    names = ["fundamentals", "sentiment", "hype", "technicals"]
    n, size = len(names), RADAR_SIZE
    cx = cy = size / 2
    r = size / 2 - 34
    for i, k in enumerate(names):
        ang = -math.pi / 2 + 2 * math.pi * i / n
        x = cx + r * 1.19 * math.cos(ang)
        y = cy + r * 1.19 * math.sin(ang)
        wid = len(k) * 6.2
        left, right = (x - wid / 2, x + wid / 2)
        if abs(x - cx) >= 6:
            left, right = (x, x + wid) if x > cx else (x - wid, x)
        assert left >= minx, f"{k!r} clipped left: {left:.0f} < {minx}"
        assert right <= maxx, f"{k!r} clipped right: {right:.0f} > {maxx}"
        assert miny <= y + 13 <= maxy, f"{k!r} clipped vertically"

    # --- DIRECTION GUARD ----------------------------------------------------
    # High P/E must read RED, not green. This is the bug that made a
    # near-record-expensive Apple look excellent.
    assert "--neg" in pct_bar(93, direction=-1), "high percentile on a "\
        "lower-is-better metric must be RED"
    assert "--pos" in pct_bar(10, direction=-1)
    assert "--pos" in pct_bar(93, direction=+1)
    assert "--neg" in pct_bar(10, direction=+1)
    # Unknown direction must claim nothing.
    grey = pct_bar(93, direction=0)
    assert "--muted" in grey and "--pos" not in grey and "--neg" not in grey, \
        "an unmeasured direction must not be coloured green or red"

    # --- no-data must not read as zero -------------------------------------
    assert "pctbar na" in pct_bar(None) and "n/a" in pct_bar(None)
    assert "width:0.0%" not in pct_bar(None), "absent must not render a 0% bar"
    assert "width:0.0%" in pct_bar(0), "an actual zero SHOULD render a 0% bar"
    assert "&mdash;" in fmt_value(None)

    # --- nav reaches every page, and depth rewrites correctly --------------
    top, deep = nav("hub", 0), nav("profiles", 1)
    for _key, _text, href in NAV:
        assert f'href="{href}"' in top, f"nav missing {href}"
        assert f'href="../{href}"' in deep, f"depth-1 nav missing ../{href}"
    assert top.count('class="on"') == 1

    # --- labels come from fund_metrics, not hand-copied ---------------------
    assert label("f_score").startswith("Piotroski"), label("f_score")
    assert label("hype_score") == "Hype composite"
    assert label("totally_unknown_metric") == "totally unknown metric"
    assert pillar_of("f_score") == "quality"

    # --- session picker: newest entry points at the undated alias ----------
    sp = session_picker("2026-08-06", ["2026-08-06", "2026-08-05"],
                        "explore_{d}.html")
    assert 'value="latest.html"' in sp, "newest option must be the latest alias"
    # And the alias must never be built by string surgery on the pattern.
    sp3 = session_picker("2026-08-06", ["2026-08-06", "2026-08-05"], "{d}.html")
    assert "{d}" not in sp3, f"pattern leaked into an href: {sp3[:160]}"
    assert 'value="explore_2026-08-05.html"' in sp
    assert session_picker("x", [], "explore_{d}.html") == ""

    # A session with no saved page must be MARKED, not silently linked into a
    # 404. The newest entry is exempt: it is the undated "latest" alias.
    sp2 = session_picker("2026-08-06",
                         ["2026-08-06", "2026-08-05", "2026-08-04"],
                         "explore_{d}.html", static={"2026-08-05"})
    # Anything metrics_doc calls documented must also be NAMEABLE. These two
    # drifted once: the doc selftest passed for combo_h20 while the page
    # rendered "combo medium".
    try:
        import metrics_doc as _md
        documented = set(_md.DISPLAY) | set(getattr(_md, "EXTRA_LABELS", {}))             | set(getattr(_md, "READ", {}))
        unnamed = sorted(k for k in documented
                         if label(k) == k.replace("_", " ")
                         and k.replace("_", " ") != k)
        assert not unnamed, (
            f"{len(unnamed)} documented metric(s) fall back to the raw key in "
            f"ui.label(): {unnamed[:6]}")
    except ImportError:
        pass

    bad = check_contrast()
    assert not bad, "contrast below WCAG AA:\n  " + "\n  ".join(bad)

    assert 'data-live="1"' in sp2, "server-only session was not marked"
    assert sp2.count('data-live="1"') == 1, sp2
    assert "2026-08-04 &middot; live" in sp2 or "2026-08-04 · live" in sp2, sp2
    assert "file:" in sp2, "no file:// fallback in the picker"

    # --- MENU REFRESH on pages already written ------------------------------
    import tempfile
    from pathlib import Path as _P
    with tempfile.TemporaryDirectory() as td:
        root = _P(td)
        (root / "stock").mkdir()
        picker = '<select id="sess"><option value="X.html">2026-11-06</option></select>'
        old_nav = ('<div class="nav"><div class="inner"><span class="brand">SCREENER</span>'
                   '<a class="" href="../index.html">Status</a><a class="on" href="../stock/index.html">'
                   'Profiles</a><span class="spacer"></span>' + picker + '</div></div>')
        (root / "stock" / "X.html").write_text(
            '<html><head><style>.nav{}</style></head><body>' + old_nav + '<div class="wrap">'
            '<a href="../index.html">status hub</a></div></body></html>', encoding="utf-8")
        (root / "m.html").write_text(page("M", "<p>x</p>", active="metrics"), encoding="utf-8")
        (root / "plain.html").write_text("<html><body>no menu</body></html>", encoding="utf-8")
        r1 = refresh_nav(root, verbose=False)
        x = (root / "stock" / "X.html").read_text(encoding="utf-8")
        assert r1["updated"] == 1 and r1["no_menu"] == ["plain.html"], r1      # m.html already current
        assert x.count(NAV_START) == 1 and x.count(_CSS_START) == 1 and "Profiles" in x, x[:300]
        assert EXTRA_START + picker + EXTRA_END in x, "the session picker was lost"
        assert 'class="dd on"><summary>Stocks' in x, "the highlighted entry was lost"
        assert 'href="../system.html">Status</a>' in x and "status hub" not in x
        assert refresh_nav(root, verbose=False)["updated"] == 0, "a second run rewrote pages"

    # --- CSS COLLISION GUARD ------------------------------------------------
    # `.bar` was defined here for percentile bars AND in explore.py for the
    # filter row. explore's rule set `display` but not `height`, so the shared
    # `height:7px;overflow:hidden` collapsed the filter bar to a 7px sliver.
    # Nothing errored; the controls simply vanished. Any generic class name
    # added to ui.CSS will do this again, so it is asserted rather than trusted.
    shared = _class_names(CSS)
    for mod_name in ("explore", "dashboard", "stock_profile", "today_page"):
        try:
            mod = __import__(mod_name)
        except Exception:                                        # noqa: BLE001
            continue
        local = _class_names(getattr(mod, "CSS", "") or "")
        clash = sorted(shared & local)
        assert not clash, (
            f"{mod_name}.CSS redefines class(es) also in ui.CSS: {clash}. "
            f"Rename one -- a partial override silently breaks layout.")

    if verbose:
        worst = min(contrast(pal[f"--{a}"], pal[f"--{b}"])
                    for pal in _palettes().values()
                    for a, b in CONTRAST_PAIRS
                    if f"--{a}" in pal and f"--{b}" in pal)
        print(f"  [ui] radar labels inside viewBox, no-data != zero, "
              f"{len(NAV)} nav entries, {len(METRIC_LABELS)} labels, "
              f"no CSS class collisions, "
              f"both themes >= {worst:.2f}:1 (AA {AA})")


if __name__ == "__main__":
    if "--renav" in sys.argv:          # bring every page in reports/ to the current menu
        refresh_nav()
    else:
        selftest()
    sys.exit(0)
