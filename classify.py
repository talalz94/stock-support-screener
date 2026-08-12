"""
Tags and bucket assignment.

PURE: a metrics dict in, tags out. No I/O, no network, no globals beyond config.
That is what makes `python classify.py --selftest` a real unit test, and it means
retuning the taxonomy never requires re-fetching or re-screening anything.

Six independent tags plus one primary bucket. The tags are independent on purpose:
the dashboard re-groups by any of them client-side, so "just the small price
stocks" and "just the old setups" are both one click rather than a config change.

    python classify.py --selftest
"""

from __future__ import annotations

import argparse
import sys

import config


def _num(value):
    """float(value) or None for anything unusable (None, NaN, junk)."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return None if v != v else v          # NaN


def _tier(value, tiers, default: str = "?") -> str:
    """Tier by EXCLUSIVE upper bound -- for continuous amounts.

    (5.00, "MICRO") means "under $5.00", so $5.00 itself is the next tier up.
    Used for price, market cap, and dollar volume.
    """
    v = _num(value)
    if v is None:
        return default
    for cut, name in tiers:
        if v < cut:
            return name
    return tiers[-1][1]


def _tier_incl(value, tiers, default: str = "?") -> str:
    """Tier by INCLUSIVE upper bound -- for integer counts.

    (40, "FRESH") means "25 through 40 sessions", so 40 is still FRESH. Age bands
    are quoted to the user as closed ranges, and an off-by-one here silently
    reclassifies a setup on the boundary.
    """
    v = _num(value)
    if v is None:
        return default
    for cut, name in tiers:
        if v <= cut:
            return name
    return tiers[-1][1]


def price_tier(close: float | None) -> str:
    """MICRO/LOW/MID/HIGH/PREMIUM. 'Small price stocks' = MICRO + LOW."""
    return _tier(close, config.PRICE_TIERS)


def age_band(sessions_since_peak: int | None) -> str:
    """FRESH/RECENT/MATURE/OLD by sessions since the dominant peak.

    The screen already rejects <MIN_DECLINE_BARS and >MAX_DECLINE_BARS, so
    everything reaching here lands in a real band.
    """
    return _tier_incl(sessions_since_peak, config.AGE_BANDS, default="RECENT")


def size_tier(market_cap: float | None) -> str:
    """NANO/MICRO_CAP/SMALL/MID_CAP/LARGE, or UNKNOWN before enrichment."""
    if market_cap is None or market_cap != market_cap or market_cap <= 0:
        return "UNKNOWN"
    return _tier(market_cap, config.SIZE_TIERS)


def liquidity_tier(adv_usd: float | None) -> str:
    return _tier(adv_usd, config.LIQUIDITY_TIERS, default="OK")


def support_grade(touches_prior: int | None, span_days: int | None,
                  band_width: float | None) -> str:
    """A/B/C by touch count, calendar span, and how tight the band is.

    Span matters independently of count: four touches inside one week is one
    event, not four.
    """
    t = int(touches_prior or 0)
    s = int(span_days or 0)
    w = band_width if (band_width is not None and band_width == band_width) else 1.0
    for name, min_t, min_span, max_w in config.SUPPORT_GRADES:
        if t >= min_t and s >= min_span and w <= max_w:
            return name
    return "C" if t >= 2 else "D"


def band_width_of(m: dict) -> float:
    """Cluster width as a fraction of the level price."""
    lo, hi, L = m.get("level_px_lo"), m.get("level_px_hi"), m.get("level")
    if not L or lo is None or hi is None:
        return 1.0
    try:
        return float((hi - lo) / L)
    except (TypeError, ZeroDivisionError):
        return 1.0


def tags(m: dict) -> dict:
    """Every tag for one screened row."""
    bw = band_width_of(m)
    return {
        "price_tier": price_tier(m.get("close")),
        "age_band": age_band(m.get("sessions_since_peak")),
        "size_tier": size_tier(m.get("market_cap")),
        "liquidity_tier": liquidity_tier(m.get("adv_usd")),
        "support_grade": support_grade(m.get("touches_prior"),
                                      m.get("span_days"), bw),
        # Stage comes straight from the ATR-normalised extension; classify does
        # not recompute signal math, it only reads it.
        "stage": m.get("stage") or "STILL_TESTING",
        "shape": m.get("shape") or "SINGLE",
        "band_width": bw,
    }


def bucket(t: dict) -> str:
    """One primary bucket for report sectioning. First match wins.

    Ordering note: the CASCADE below is precedence (most specific first), which is
    NOT the same as the report's section order. The report leads with EARLY --
    names still sitting on the level with the whole move ahead -- because that is
    the actionable end for catching a bounce before it happens. See
    config.BUCKET_ORDER.
    """
    grade = t["support_grade"]
    stage = t["stage"]
    good_support = grade in ("A", "B")
    liquid = t["liquidity_tier"] in ("GOOD", "DEEP")
    # SPEC means "hard to trade or weakly supported" -- deliberately NOT "cheap".
    # A $3 stock with $143M of daily volume and a grade-A level is a good setup
    # that happens to be cheap, and demoting it here would bury it in the bucket
    # least likely to be read. `price_tier` already surfaces nominal price as a
    # tag and a group-by axis, and the size component of the score already ranks
    # bigger names above it.
    thin = t["liquidity_tier"] == "OK" or grade in ("C", "D")

    if stage in ("EXTENDED", "GONE"):
        return "LATE"
    # Buckets discriminate on QUALITY (support strength, tradeable liquidity) and
    # on where the move is (stage). Age is deliberately NOT a condition here.
    # Gating PRIME on age would demote a grade-A, deep-liquidity, confirmed setup
    # to WATCH purely for being an older round trip -- which is exactly what
    # happened to ORCL on 2026-08-03 -- while `age_band` already carries that fact
    # as a tag and a group-by axis. The screen's MAX_DECLINE_BARS already rejects
    # peaks too old to be relevant at all.
    if good_support and stage in ("TURNING", "CONFIRMED") and liquid:
        return "PRIME"
    if stage == "STILL_TESTING" and good_support:
        return "EARLY"
    if thin:
        return "SPEC"
    return "WATCH"


def classify(m: dict) -> dict:
    """tags + bucket for one row."""
    t = tags(m)
    t["bucket"] = bucket(t)
    return t


def apply(df):
    """Add tag columns to a flags DataFrame. Returns a new frame."""
    import pandas as pd

    if df is None or len(df) == 0:
        return df
    rows = [classify(r) for r in df.to_dict("records")]
    tagdf = pd.DataFrame(rows)
    # `stage` and `shape` already exist on the screened frame, and a plain concat
    # would produce duplicate column labels -- after which .to_dict("records")
    # silently drops columns. Tags win, since they are derived from those values.
    base = df.reset_index(drop=True).drop(
        columns=[c for c in tagdf.columns if c in df.columns], errors="ignore")
    out = pd.concat([base, tagdf], axis=1)

    order = {b: i for i, b in enumerate(config.BUCKET_ORDER)}
    out["bucket_order"] = out["bucket"].map(lambda b: order.get(b, 99))
    size_rank = {n: i for i, (_c, n) in enumerate(reversed(config.SIZE_TIERS))}
    out["size_rank"] = out["size_tier"].map(lambda s: size_rank.get(s, 99))
    return out


def sort_for_report(df):
    """Bucket order, then the big-name bias, then score.

    Two buckets sort on a different axis because a different thing is actionable
    in them: EARLY by freshness of the test (the newest test is the one to watch),
    LATE by least-extended first (the only ones still salvageable).
    """
    import pandas as pd

    if df is None or len(df) == 0:
        return df
    parts = []
    for b in config.BUCKET_ORDER:
        chunk = df[df["bucket"] == b]
        if chunk.empty:
            continue
        if b == "EARLY" and "bars_since_low" in chunk:
            chunk = chunk.sort_values(["bars_since_low", "size_rank", "score"],
                                      ascending=[True, True, False])
        elif b == "LATE" and "ext_atr" in chunk:
            chunk = chunk.sort_values(["ext_atr", "size_rank", "score"],
                                      ascending=[True, True, False])
        else:
            chunk = chunk.sort_values(["size_rank", "score"],
                                      ascending=[True, False])
        parts.append(chunk)
    left = df[~df["bucket"].isin(config.BUCKET_ORDER)]
    if not left.empty:
        parts.append(left.sort_values("score", ascending=False))
    return pd.concat(parts, ignore_index=True) if parts else df


# ======================================================================= tests
def _fixture_rdw() -> dict:
    """RDW as of 2026-08-03, from the real screen output.

    The calibration anchor: if a taxonomy change moves this row, that is a
    deliberate decision, not an accident.
    """
    return {
        "ticker": "RDW", "close": 9.64, "sessions_since_peak": 45,
        "market_cap": 1.6e9, "adv_usd": 143.1e6, "touches_prior": 8,
        "span_days": 626, "level": 7.44, "level_px_lo": 7.43,
        "level_px_hi": 7.53, "stage": "CONFIRMED", "shape": "SINGLE",
        "bars_since_low": 3, "ext_atr": 2.01, "score": 73.2,
    }


def _selftest() -> int:
    fails: list[str] = []

    def check(label: str, got, want) -> None:
        if got != want:
            fails.append(f"{label}: got {got!r}, want {want!r}")

    # --- the anchor
    t = classify(_fixture_rdw())
    check("RDW price_tier", t["price_tier"], "LOW")
    check("RDW age_band", t["age_band"], "RECENT")
    check("RDW support_grade", t["support_grade"], "A")
    check("RDW liquidity_tier", t["liquidity_tier"], "DEEP")
    check("RDW size_tier", t["size_tier"], "MICRO_CAP")
    check("RDW stage", t["stage"], "CONFIRMED")
    check("RDW bucket", t["bucket"], "PRIME")

    # --- price tier boundaries ("small price stocks" = MICRO + LOW)
    check("price 1.50", price_tier(1.50), "MICRO")
    check("price 4.99", price_tier(4.99), "MICRO")
    check("price 5.00", price_tier(5.00), "LOW")
    check("price 14.99", price_tier(14.99), "LOW")
    check("price 15.00", price_tier(15.00), "MID")
    check("price 49.99", price_tier(49.99), "MID")
    check("price 50.00", price_tier(50.00), "HIGH")
    check("price 250", price_tier(250.0), "PREMIUM")

    # --- age bands: 'new' vs 'old', the user's explicit request
    check("age 25", age_band(25), "FRESH")
    check("age 40", age_band(40), "FRESH")
    check("age 41", age_band(41), "RECENT")
    check("age 45 (RDW)", age_band(45), "RECENT")
    check("age 90", age_band(90), "RECENT")
    check("age 91", age_band(91), "MATURE")
    check("age 180", age_band(180), "MATURE")
    check("age 181", age_band(181), "OLD")
    check("age 224 (ORCL)", age_band(224), "OLD")
    check("age 300", age_band(300), "OLD")

    # --- support grades: span matters independently of count
    check("grade 4 touches/90d/tight", support_grade(4, 90, 0.05), "A")
    check("grade 4 touches but 5d span", support_grade(4, 5, 0.05), "C")
    check("grade 3/40d", support_grade(3, 40, 0.10), "B")
    check("grade 2", support_grade(2, 200, 0.15), "C")
    check("grade 1", support_grade(1, 200, 0.05), "D")
    check("grade 4 but wide band", support_grade(4, 90, 0.30), "C")

    # --- size + liquidity
    check("size unknown", size_tier(None), "UNKNOWN")
    check("size 250M", size_tier(250e6), "NANO")
    check("size 1.6B", size_tier(1.6e9), "MICRO_CAP")
    check("size 5B", size_tier(5e9), "SMALL")
    check("size 300B", size_tier(300e9), "LARGE")
    check("liq 2M", liquidity_tier(2e6), "OK")
    check("liq 10M", liquidity_tier(10e6), "GOOD")
    check("liq 143M", liquidity_tier(143e6), "DEEP")

    # --- bucket cascade
    base = _fixture_rdw()
    check("LATE wins over PRIME",
          classify({**base, "stage": "EXTENDED"})["bucket"], "LATE")
    check("GONE -> LATE",
          classify({**base, "stage": "GONE"})["bucket"], "LATE")
    check("still testing -> EARLY",
          classify({**base, "stage": "STILL_TESTING"})["bucket"], "EARLY")
    check("thin tape -> SPEC",
          classify({**base, "adv_usd": 2e6})["bucket"], "SPEC")
    # Cheap but liquid and well-supported stays PRIME: SPEC is about tradeability,
    # not nominal price. price_tier carries the "small price stock" information.
    check("micro price but liquid -> PRIME",
          classify({**base, "close": 3.0})["bucket"], "PRIME")
    check("micro price AND thin -> SPEC",
          classify({**base, "close": 3.0, "adv_usd": 2e6})["bucket"], "SPEC")
    check("grade C -> SPEC",
          classify({**base, "touches_prior": 2, "span_days": 300})["bucket"], "SPEC")
    # A weak level that is still merely testing must not be promoted to EARLY.
    check("grade C + testing -> SPEC",
          classify({**base, "touches_prior": 2, "span_days": 300,
                    "stage": "STILL_TESTING"})["bucket"], "SPEC")
    # Age must not decide the bucket. ORCL on 2026-08-03: grade A, DEEP, CONFIRMED,
    # peak 224 sessions back -- a large-cap round trip belongs in PRIME with an
    # OLD tag, not demoted to WATCH.
    orcl = {"ticker": "ORCL", "close": 141.85, "sessions_since_peak": 224,
            "market_cap": 418e9, "adv_usd": 4956e6, "touches_prior": 7,
            "span_days": 400, "level": 118.32, "level_px_lo": 117.5,
            "level_px_hi": 119.0, "stage": "CONFIRMED", "shape": "SINGLE"}
    to = classify(orcl)
    check("ORCL age_band", to["age_band"], "OLD")
    check("ORCL size_tier", to["size_tier"], "LARGE")
    check("ORCL bucket", to["bucket"], "PRIME")

    # --- degenerate input must never raise
    for bad in ({}, {"close": None}, {"close": float("nan")},
                {"sessions_since_peak": None, "adv_usd": None}):
        try:
            classify(bad)
        except Exception as exc:                      # noqa: BLE001
            fails.append(f"raised on {bad!r}: {exc!r}")

    if fails:
        print(f"FAILED ({len(fails)}):")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("classify selftest: all checks passed")
    print(f"  RDW -> {t['price_tier']} / {t['age_band']} / {t['size_tier']} / "
          f"{t['shape']} / {t['stage']} / grade {t['support_grade']} / "
          f"{t['liquidity_tier']} -> bucket {t['bucket']}")
    return 0


def main() -> int:
    config.safe_console()
    ap = argparse.ArgumentParser(description="Tag and bucket assignment.")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return _selftest()
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

