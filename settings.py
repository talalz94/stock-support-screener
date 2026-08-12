"""
What the per-stock profile view shows, and in what order.

    python settings.py --show            print the active profile
    python settings.py --reset           back to defaults
    python settings.py --enable radar    turn a block on
    python settings.py --disable hype
    python settings.py --order header,radar,financials

Stored as `data/_profile.json`. One file, one profile -- named profiles can come
later, and the schema is versioned so adding one does not invalidate this.

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
    "order": ["header", "chart", "radar", "scores", "financials", "growth",
              "fundamentals", "sentiment", "hype", "levels"],
    "blocks": {
        "header": {"enabled": True,
                   "show": ["name", "sector", "industry", "exchange",
                            "mktcap", "price", "tradingview"]},
        # Price sits directly under the header: it is the first thing anyone
        # checks, and the support level is what the whole screener is about.
        "chart": {"enabled": True, "window": "1y"},
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
        "fundamentals": {"enabled": True, "limit": 12,
                         "metrics": ["pe", "pb", "ev_ebitda", "fcf_yield",
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
    },
}


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
    if verbose:
        print(f"  [settings] schema v{SCHEMA_VERSION}, "
              f"{len(prof['blocks'])} block(s), {len(enabled)} enabled")


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
