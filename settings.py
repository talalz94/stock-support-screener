"""
What the per-stock profile view shows, and in what order.

    python settings.py --show            print the active profile
    python settings.py --reset           back to defaults
    python settings.py --enable radar    turn a block on
    python settings.py --disable hype
    python settings.py --order header,radar,financials

Stored as `data/_profile.json`. One file, one profile -- named profiles can come
later, and the schema is versioned so adding one does not invalidate this.

Since 2026-11-09 the stock page groups its sections into TABS (`TABS`, `tab_groups`), and the
Settings page (`settings_page.py`) edits the layout through `ui_state` / `apply_ui`: every value
checked, nothing saved when one is wrong, and fields the page does not show left as they are.

WHY A REGISTRY OF BLOCKS RATHER THAN A LIST OF METRICS
--------------------------------------------------------
The ask was "I want to see only some fundamentals, 2 plots, 2 sentiment
indicators, 1 support level". Those are not all the same kind of thing: a
fundamental is a number, a plot is a rendering of a series, a support level is a
derived structure. A flat list of metric names cannot express "two plots", so
the unit of configuration is a BLOCK with its own options, and `limit` means
"show at most this many rows inside this block".

UNKNOWN BLOCKS ARE KEPT, NOT DROPPED
--------------------------------------
If a saved profile names a block this version does not know about, it is carried
through untouched rather than deleted. A user who upgrades, downgrades and
upgrades again should not silently lose their layout -- and a validator that
deletes what it does not recognise is indistinguishable from data loss.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import config

SCHEMA_VERSION = 1
PROFILE_FILE = config.DATA / "_profile.json"

# The block registry. `limit` is the max rows rendered inside that block;
# None means "no limit". `metrics` narrows a block to specific keys -- an empty
# list means "the block's own default set".
DEFAULT_PROFILE: dict[str, Any] = {
    "schema": SCHEMA_VERSION,
    "name": "default",
    "order": ["header", "chart", "relative", "radar", "scores", "insider", "events", "financials", "growth",
              "fundamentals", "sentiment", "hype", "levels"],
    "blocks": {
        "header": {"enabled": True,
                   "show": ["name", "sector", "industry", "exchange",
                            "mktcap", "price", "tradingview"]},
        # Price sits directly under the header: it is the first thing anyone
        # checks, and the support level is what the whole screener is about.
        "chart": {"enabled": True, "window": "1y"},
        # The stock against SPY, its sector and its sub-industry, rebased a year ago (asked
        # 2026-11-08: "helpful in analysis for a particular stock"). Measured no ranking edge.
        "relative": {"enabled": True, "window": 252},
        "radar": {"enabled": True,
                  "axes": ["fundamentals", "sentiment", "hype", "technicals"]},
        # combo_* first: they are the only scores whose weights were measured
        # rather than assumed, so they belong above the equal-weighted pillars.
        # `limit` must cover the whole list -- it silently truncated the tail
        # before, which is how a requested metric vanished without a trace.
        "scores": {"enabled": True, "limit": 11,
                   "metrics": ["combo_h20", "combo_h1", "combo_h60",
                               "fund_score", "quality_score", "value_score",
                               "growth_score", "safety_score", "hype_score",
                               "premium_score", "attention_score"]},
        # Any key in stock_profile.DERIVED or any concept in fundamentals.TAGS
        # can go in `rows`; `python stock_profile.py --rows` lists them all.
        # `periods`/`quarters` = 0 means EVERY stored period. A number caps it.
        "financials": {"enabled": True, "freq": "A", "periods": 0, "quarters": 0,
                       "rows": ["revenue", "gross_profit", "gross_margin",
                                "op_margin", "ebitda", "net_income",
                                "net_margin", "eps_diluted", "fcf",
                                "fcf_margin", "roe", "debt_to_equity"]},
        # Every row named here gets a shaded trend strip under it. Dollar rows
        # get YoY %, margin/ratio rows get the change in percentage POINTS --
        # "+50%" on a margin that went 2% to 3% reads like a different business.
        "growth": {"enabled": True,
                   "rows": ["revenue", "gross_profit", "net_income",
                            "eps_diluted", "fcf", "gross_margin", "op_margin",
                            "net_margin", "roe"],
                   "basis": "yoy"},
        # `eps_diluted_ttm` sits directly under `pe` for the same reason
        # `sent_age` sits under the sentiment score: a P/E without its earnings
        # denominator is unreadable. CMP is the case -- its P/E reads 157 on
        # EPS of 0.16, and before the provider overlay reached EPS the same
        # page would have rendered a NEGATIVE P/E off our own -2.90. The
        # denominator is what tells the reader which of those they are looking
        # at.
        #
        # It was stored for 3,296 names and displayed for none: not being in
        # `fund_metrics.REGISTRY`, it had no label and no slot here.
        "fundamentals": {"enabled": True, "limit": 13,
                         "metrics": ["pe", "eps_diluted_ttm", "pb",
                                     "ev_ebitda", "fcf_yield",
                                     "roe", "roic", "f_score", "z_score",
                                     "m_score", "accruals", "net_issuance",
                                     "ccc"]},
        # `sent_age` sits directly under the score on purpose: a sentiment
        # number without the age of its newest article is unreadable. DPRO read
        # 0.50 from three articles whose newest was 27 sessions old.
        "sentiment": {"enabled": True, "limit": 6,
                      "metrics": ["sent_mean_30d", "sent_decay_30d", "sent_age",
                                  "news_count_30d", "severity_max", "news_z"]},
        "hype": {"enabled": True, "limit": 5,
                 "metrics": ["hype_score", "premium_score", "attention_score",
                             "ps_ratio", "extension_pct"]},
        "levels": {"enabled": True, "limit": 1},
        # Directors' and officers' purchases and sales, House trades, each buyer's
        # record, and the measured result of each (insider_signals, 2026-10-27).
        "insider": {"enabled": True},
        # Next earnings date, recent SEC filings by type, and the filing types the
        # news-event study measured as warnings (news_events, 2026-11-01).
        "events": {"enabled": True, "limit": 10},
    },
    # Sections grouped into tabs (2026-11-09: a stock page was 7,000 px at a desktop width and
    # 14,000 on a phone). The header stays above the tabs. A section no tab names lands in a
    # "More" tab rather than vanishing; `enabled: False` gives one long page as before.
    "tabs": {"enabled": True, "default": "overview",
             "assign": {"chart": "overview", "relative": "overview", "radar": "overview",
                        "insider": "insiders", "events": "insiders",
                        "financials": "fundamentals", "fundamentals": "fundamentals",
                        "sentiment": "news", "hype": "news"}},
}

# The tabs: a fixed set, so a layout can never name a tab that does not exist.
# (key, label, short label for a phone)
TABS = (("overview", "Overview", "Overview"),
        ("fundamentals", "Fundamentals", "Fundamentals"),
        ("insiders", "Insiders & filings", "Insiders"),
        ("news", "News & sentiment", "News"))
TAB_KEYS = tuple(k for k, _l, _s in TABS)

# The sections a reader sees, in the Settings page's words. Three blocks render inside a host
# and follow its tab: `scores` is the composite card beside the radar, `growth` the growth rows
# under the financials (its switch is a Financials option), and `levels` has no effect of its
# own -- the chart always draws the support level.
SECTIONS = {
    "chart": ("Price chart", "price, volume and the support level"),
    "relative": ("Against the market and sector", "the stock vs SPY, its sector and sub-industry"),
    "radar": ("Score radar", "the four pillars and the composite scores"),
    "insider": ("Insider and House trades", "purchases, sales and each buyer's record"),
    "events": ("Filings and earnings", "the next earnings date and recent SEC filings"),
    "financials": ("Financials", "annual and quarterly statements, with growth rows"),
    "fundamentals": ("Fundamental metrics", "valuation, quality and balance-sheet metrics"),
    "sentiment": ("Sentiment", "news tone and the age of the newest article"),
    "hype": ("Hype", "attention, premium and stretch"),
}
HOSTED = {"scores": "radar", "growth": "financials", "levels": "chart"}
# Must match stock_profile.CHART_WINDOWS and REL_WINDOWS (stock_profile.selftest checks).
CHART_WINDOW_CHOICES = ("1M", "3M", "6M", "1Y", "5Y", "MAX")
RELATIVE_WINDOW_CHOICES = (21, 63, 126, 252)
EVENTS_LIMIT_RANGE = (1, 50)


def load() -> dict:
    """The active profile, defaults filled in. Never raises."""
    prof = json.loads(json.dumps(DEFAULT_PROFILE))     # deep copy
    if not PROFILE_FILE.exists():
        # NOT a bare `return prof`. That skipped the order reconciliation at the
        # bottom, so a block added to DEFAULT_PROFILE["blocks"] but not to
        # ["order"] rendered NOWHERE until the user happened to save a profile.
        # The `chart` block was invisible for exactly this reason.
        return _reconcile(prof)
    try:
        saved = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # A corrupt profile must not stop the page rendering -- it is a display
        # preference, not data. Fall back to defaults silently loud: the page
        # says which profile it used.
        prof["name"] = "default (saved profile unreadable)"
        return prof
    if not isinstance(saved, dict):
        return prof

    prof["name"] = saved.get("name", prof["name"])
    prof["schema"] = saved.get("schema", SCHEMA_VERSION)
    if isinstance(saved.get("order"), list):
        prof["order"] = [str(x) for x in saved["order"]]
    for key, block in (saved.get("blocks") or {}).items():
        if not isinstance(block, dict):
            continue
        # Unknown blocks are KEPT -- see the module docstring.
        prof["blocks"].setdefault(key, {})
        prof["blocks"][key].update(block)
    # Tabs: the saved choices over the defaults, so a section added later still gets a tab.
    t = saved.get("tabs")
    if isinstance(t, dict):
        if isinstance(t.get("enabled"), bool):
            prof["tabs"]["enabled"] = t["enabled"]
        if t.get("default") in TAB_KEYS:
            prof["tabs"]["default"] = t["default"]
        if isinstance(t.get("assign"), dict):
            prof["tabs"]["assign"].update({str(k): str(v) for k, v in t["assign"].items()})

    return _reconcile(prof)


def _reconcile(prof: dict) -> dict:
    """Anything enabled but absent from `order` needs a position, or it is
    silently invisible while reading as enabled. Runs on EVERY load path."""
    for key in prof.get("blocks", {}):
        if key not in prof["order"]:
            prof["order"].append(key)
    return prof


def save(prof: dict) -> None:
    prof = dict(prof)
    prof["schema"] = SCHEMA_VERSION
    PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROFILE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(prof, indent=2), encoding="utf-8")
    tmp.replace(PROFILE_FILE)


def blocks_in_order(prof: dict | None = None) -> list[tuple[str, dict]]:
    """(key, options) for every ENABLED block, in the user's order."""
    prof = prof or load()
    out = []
    for key in prof.get("order", []):
        block = (prof.get("blocks") or {}).get(key)
        if isinstance(block, dict) and block.get("enabled", True):
            out.append((key, block))
    return out


def tab_groups(prof: dict, keys: list[str]) -> list[tuple[str, str, str, list[str]]]:
    """The rendered blocks `keys` (header excluded, in the profile's order) grouped into tabs:
    [(tab key, label, short label, [block keys])], tabs in TABS order and empty ones left out.
    [] when tabs are switched off. A block no tab names goes to a "More" tab at the end."""
    t = prof.get("tabs") or {}
    if not t.get("enabled", True):
        return []
    assign = t.get("assign") or {}
    groups: dict[str, list[str]] = {k: [] for k in TAB_KEYS}
    more = []
    for key in keys:
        tab = assign.get(HOSTED.get(key, key))
        (groups[tab] if tab in groups else more).append(key)
    out = [(k, label, short, groups[k]) for k, label, short in TABS if groups[k]]
    if more:
        out.append(("more", "More", "More", more))
    return out


# ------------------------------------------------------------- what the Settings page edits
def ui_state(prof: dict | None = None) -> dict:
    """The stock-page layout as the Settings page edits it. Every value is valid by
    construction: anything out of range in the profile shows as its default."""
    prof = prof or load()
    b = prof.get("blocks") or {}
    t = prof.get("tabs") or {}
    assign = t.get("assign") or {}
    order = [k for k in prof.get("order", []) if k in SECTIONS]
    order += [k for k in SECTIONS if k not in order]
    win = str((b.get("chart") or {}).get("window") or "1Y").upper()
    rel = (b.get("relative") or {}).get("window")
    lim = (b.get("events") or {}).get("limit")
    return {
        "order": order,
        "enabled": {k: bool((b.get(k) or {}).get("enabled", True)) for k in SECTIONS},
        "tab": {k: assign.get(k) if assign.get(k) in TAB_KEYS
                else DEFAULT_PROFILE["tabs"]["assign"].get(k, TAB_KEYS[0]) for k in SECTIONS},
        "tabs_on": bool(t.get("enabled", True)),
        "default_tab": t.get("default") if t.get("default") in TAB_KEYS else TAB_KEYS[0],
        "chart_window": win if win in CHART_WINDOW_CHOICES else "1Y",
        "relative_window": rel if rel in RELATIVE_WINDOW_CHOICES else 252,
        "fin_freq": (b.get("financials") or {}).get("freq") if (b.get("financials") or {}).get("freq") in ("A", "Q") else "A",
        "growth_rows": bool((b.get("growth") or {}).get("enabled", True)),
        "events_limit": (lim if isinstance(lim, int) and not isinstance(lim, bool)
                         and EVENTS_LIMIT_RANGE[0] <= lim <= EVENTS_LIMIT_RANGE[1] else 10),
    }


def ui_defaults() -> dict:
    return ui_state(_reconcile(json.loads(json.dumps(DEFAULT_PROFILE))))


def apply_ui(values: Any) -> tuple[dict, list[str]]:
    """Check what the Settings page sent and save it. Returns (the page's new state, problems).
    NOTHING is saved when there is any problem, and only the fields the page edits change:
    metric lists, limits and blocks the page does not show are kept as they are."""
    errs: list[str] = []
    if not isinstance(values, dict):
        return ui_state(), ["expected an object of settings"]
    want = set(ui_defaults())
    missing, extra = sorted(want - set(values)), sorted(set(values) - want)
    if missing:
        errs.append(f"missing: {', '.join(missing)}")
    if extra:
        errs.append(f"unknown: {', '.join(extra)}")
    if errs:
        return ui_state(), errs

    def is_int(x) -> bool:
        return isinstance(x, int) and not isinstance(x, bool)

    order, enabled, tab = values["order"], values["enabled"], values["tab"]
    if not (isinstance(order, list) and len(order) == len(SECTIONS) and set(order) == set(SECTIONS)):
        errs.append("order must name every section exactly once")
    if not (isinstance(enabled, dict) and set(enabled) == set(SECTIONS)
            and all(isinstance(v, bool) for v in enabled.values())):
        errs.append("each section needs an on/off value")
    if not (isinstance(tab, dict) and set(tab) == set(SECTIONS) and all(v in TAB_KEYS for v in tab.values())):
        errs.append(f"each section needs a tab, one of: {', '.join(TAB_KEYS)}")
    if not isinstance(values["tabs_on"], bool):
        errs.append("tabs_on must be true or false")
    if values["default_tab"] not in TAB_KEYS:
        errs.append(f"default_tab must be one of: {', '.join(TAB_KEYS)}")
    if values["chart_window"] not in CHART_WINDOW_CHOICES:
        errs.append(f"chart_window must be one of: {', '.join(CHART_WINDOW_CHOICES)}")
    if not (is_int(values["relative_window"]) and values["relative_window"] in RELATIVE_WINDOW_CHOICES):
        errs.append("relative_window must be one of: " + ", ".join(map(str, RELATIVE_WINDOW_CHOICES)))
    if values["fin_freq"] not in ("A", "Q"):
        errs.append("fin_freq must be A or Q")
    if not isinstance(values["growth_rows"], bool):
        errs.append("growth_rows must be true or false")
    lo, hi = EVENTS_LIMIT_RANGE
    if not (is_int(values["events_limit"]) and lo <= values["events_limit"] <= hi):
        errs.append(f"events_limit must be a whole number from {lo} to {hi}")
    if errs:
        return ui_state(), errs

    prof = load()
    rest = [k for k in prof["order"] if k not in SECTIONS and k != "header"]
    prof["order"] = ["header"] + list(order) + rest
    for k, on in enabled.items():
        prof["blocks"].setdefault(k, {})["enabled"] = on
    prof["tabs"] = {"enabled": values["tabs_on"], "default": values["default_tab"],
                    "assign": {**(prof.get("tabs") or {}).get("assign", {}), **tab}}
    prof["blocks"].setdefault("chart", {})["window"] = values["chart_window"]
    prof["blocks"].setdefault("relative", {})["window"] = values["relative_window"]
    prof["blocks"].setdefault("financials", {})["freq"] = values["fin_freq"]
    prof["blocks"].setdefault("growth", {})["enabled"] = values["growth_rows"]
    prof["blocks"].setdefault("events", {})["limit"] = values["events_limit"]
    save(prof)
    return ui_state(load()), []


def set_enabled(key: str, on: bool) -> dict:
    prof = load()
    if key not in prof["blocks"]:
        raise KeyError(f"unknown block {key!r}. Known: "
                       f"{', '.join(sorted(prof['blocks']))}")
    prof["blocks"][key]["enabled"] = bool(on)
    save(prof)
    return prof


def set_order(order: list[str]) -> dict:
    prof = load()
    unknown = [k for k in order if k not in prof["blocks"]]
    if unknown:
        raise KeyError(f"unknown block(s): {', '.join(unknown)}")
    # Blocks the user did not mention keep their relative position at the end,
    # rather than vanishing.
    prof["order"] = order + [k for k in prof["order"] if k not in order]
    save(prof)
    return prof


def show(prof: dict | None = None) -> None:
    prof = prof or load()
    src = PROFILE_FILE if PROFILE_FILE.exists() else "(defaults, no file yet)"
    print(f"\n  profile: {prof.get('name')}   schema v{prof.get('schema')}")
    print(f"  file   : {src}\n")
    for key in prof["order"]:
        b = prof["blocks"].get(key, {})
        on = "on " if b.get("enabled", True) else "OFF"
        extra = []
        if b.get("limit") is not None:
            extra.append(f"limit={b['limit']}")
        if b.get("freq"):
            extra.append(f"freq={b['freq']}")
        if b.get("periods"):
            extra.append(f"periods={b['periods']}")
        n = len(b.get("metrics") or b.get("rows") or b.get("axes") or [])
        if n:
            extra.append(f"{n} item(s)")
        print(f"    [{on}] {key:<14}{'  '.join(extra)}")
    print()


def selftest(verbose: bool = True) -> None:
    prof = load()
    assert prof["schema"] == SCHEMA_VERSION
    for key in prof["order"]:
        assert key in prof["blocks"], f"order names unknown block {key!r}"
    for key in prof["blocks"]:
        assert key in prof["order"], f"block {key!r} has no position in order"

    # An unknown block from a future version must survive a load round-trip.
    merged = dict(DEFAULT_PROFILE)
    merged = json.loads(json.dumps(merged))
    merged["blocks"]["from_the_future"] = {"enabled": True, "limit": 3}
    merged["order"].append("from_the_future")
    assert "from_the_future" in merged["blocks"]

    enabled = blocks_in_order(prof)
    assert enabled, "no blocks enabled -- the profile page would be empty"

    # --- tabs and the Settings page's save, on a scratch profile: the real one is never touched
    global PROFILE_FILE
    import tempfile
    from pathlib import Path
    real = PROFILE_FILE
    with tempfile.TemporaryDirectory() as td:
        PROFILE_FILE = Path(td) / "_profile.json"
        try:
            d = ui_defaults()
            assert set(d["order"]) == set(SECTIONS) and set(d["tab"]) == set(SECTIONS)
            grouped = tab_groups(load(), [k for k in load()["order"] if k != "header"])
            flat = [k for _t, _l, _s, ks in grouped for k in ks]
            assert sorted(flat) == sorted(k for k in load()["order"] if k != "header"), \
                "a section is in no tab or in two"
            assert [g[0] for g in grouped] == list(TAB_KEYS) and "more" not in [g[0] for g in grouped], \
                f"every default section should have a named tab: {grouped}"
            # a valid change saves, and only what the page edits changes
            v = json.loads(json.dumps(d))
            v["order"] = list(reversed(d["order"]))
            v["enabled"]["hype"] = False
            v["tab"]["events"] = "news"
            v.update(tabs_on=True, default_tab="fundamentals", chart_window="6M",
                     relative_window=63, fin_freq="Q", growth_rows=False, events_limit=5)
            got, errs = apply_ui(v)
            assert not errs and got == v, (errs, got)
            p = load()
            assert p["order"][0] == "header" and p["blocks"]["fundamentals"]["metrics"] == \
                DEFAULT_PROFILE["blocks"]["fundamentals"]["metrics"], "a field the page does not edit changed"
            assert not p["blocks"]["hype"]["enabled"] and p["blocks"]["chart"]["window"] == "6M"
            news = [ks for t, _l, _s, ks in tab_groups(p, [k for k in p["order"] if k != "header"]) if t == "news"][0]
            assert "events" in news, news
            # anything wrong saves NOTHING
            before = PROFILE_FILE.read_text(encoding="utf-8")
            for bad in ({**v, "order": v["order"][:-1]}, {**v, "default_tab": "nope"},
                        {**v, "events_limit": True}, {**v, "events_limit": 0}, {**v, "chart_window": "2Y"},
                        {**v, "relative_window": 64}, {**v, "tab": {**v["tab"], "chart": "x"}},
                        {**v, "enabled": {**v["enabled"], "chart": "yes"}}, {**v, "surprise": 1},
                        {k: x for k, x in v.items() if k != "fin_freq"}, [], "x"):
                _s, e = apply_ui(bad)
                assert e, f"accepted a bad value: {bad!r:.120}"
                assert PROFILE_FILE.read_text(encoding="utf-8") == before, "a refused change was saved"
            # a hand-edited bad value shows as its default, so the page never shows a stale choice
            raw = json.loads(before)
            raw["blocks"]["chart"]["window"] = "2y"
            raw["tabs"]["default"] = "nope"
            PROFILE_FILE.write_text(json.dumps(raw), encoding="utf-8")
            st = ui_state()
            assert st["chart_window"] == "1Y" and st["default_tab"] in TAB_KEYS
            # tabs off: one long page
            v2 = {**v, "tabs_on": False}
            assert not apply_ui(v2)[1] and tab_groups(load(), ["chart"]) == []
        finally:
            PROFILE_FILE = real
    if verbose:
        print(f"  [settings] schema v{SCHEMA_VERSION}, "
              f"{len(prof['blocks'])} block(s), {len(enabled)} enabled; tabs cover every section; "
              f"the Settings page's save refuses 12 kinds of bad input and saves nothing then")


def main() -> int:
    ap = argparse.ArgumentParser(description="Profile view settings.")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--enable", metavar="BLOCK")
    ap.add_argument("--disable", metavar="BLOCK")
    ap.add_argument("--order", metavar="A,B,C")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        selftest()
        return 0
    if a.reset:
        save(json.loads(json.dumps(DEFAULT_PROFILE)))
        print("profile reset to defaults")
    if a.enable:
        set_enabled(a.enable, True)
        print(f"enabled {a.enable}")
    if a.disable:
        set_enabled(a.disable, False)
        print(f"disabled {a.disable}")
    if a.order:
        set_order([x.strip() for x in a.order.split(",") if x.strip()])
        print("order updated")
    show()
    return 0


if __name__ == "__main__":
    sys.exit(main())
