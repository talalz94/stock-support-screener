"""
Tradable US operating-company universe, plus a membership registry.

Two sources:

  Alpaca /v2/assets        ~14,180 active US equities. Authoritative for "can
                           Alpaca route an order", but has NO instrument-type
                           field -- it cannot tell you an ETF from a company.

  nasdaqtraded.txt         Free, unauthenticated, regenerated daily by Nasdaq.
                           Carries an authoritative ETF Y/N flag, Test Issue,
                           Financial Status, and a precise Security Name. Joins
                           Alpaca at 99.95%. A SOFT dependency: if it is
                           unreachable we fall back to the cached copy, then to
                           name heuristics on Alpaca's own `name`, and flag the
                           run as degraded. The scan must never abort because a
                           free text file was down.

The registry (adapted from `Stock Screener/universe.py`) tracks lifecycle so a
delisted ticker stops costing time forever while history already stored stays
valid:

    active   in today's asset list
    removed  was, no longer is -- history remains valid, keep fetching a while
    dead     repeatedly served no bars in HEALTHY runs; skipped

Two adaptations from the S&P version. `HEALTHY_FRACTION` drops 0.80 -> 0.60,
because hundreds of legitimately illiquid names here print zero bars on a given
day (verified: BIO.B, BANXR both return n=0) and 0.80 would be tripped by normal
market quiet. And delisting is now observed DIRECTLY -- absent from /v2/assets
means removed, no fail-count guessing needed -- so fail_count is reserved for
"Alpaca lists it but serves no bars".
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import urllib.request
from datetime import date

import pandas as pd

import alpaca
import config

COLUMNS = [
    "ticker", "name", "exchange", "first_seen", "last_seen", "status", "fail_count",
    "is_etf", "sec_name", "fin_status", "shortable", "etb", "has_options",
    "marginable", "fractionable", "name_source",
]

NASDAQ_EXPECTED = {
    "Nasdaq Traded", "Symbol", "Security Name", "Listing Exchange",
    "Market Category", "ETF", "Round Lot Size", "Test Issue", "Financial Status",
    "CQS Symbol", "NASDAQ Symbol", "NextShares",
}

_KILL = re.compile(config.NAME_KILL_PATTERN, re.I)


def normalize(sym: str) -> str:
    """Canonical internal key = Alpaca's dot form.

    The universe originates from Alpaca, so fetching needs no translation at all
    -- a real simplification over the sibling projects, which carry BRK-B in the
    universe and translate to BRK.B on every request. This exists only to
    sanitise external input (pasted lists, --only args, Yahoo-style tickers).
    """
    return sym.strip().upper().replace("-", ".").replace("/", ".")


# --------------------------------------------------------------- nasdaqtraded
def fetch_nasdaq_traded() -> pd.DataFrame:
    """Nasdaq's symbol directory. Raises on failure; callers fall back."""
    req = urllib.request.Request(
        config.NASDAQ_TRADED_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8", errors="replace")

    df = pd.read_csv(io.StringIO(text), sep="|", dtype=str)

    missing = NASDAQ_EXPECTED - set(df.columns)
    if missing:
        # Refuse to apply a directory whose schema changed rather than silently
        # mis-filtering the whole universe on positional luck.
        raise RuntimeError(f"nasdaqtraded.txt schema changed; missing {sorted(missing)}")

    # The final row is "File Creation Time: 0804202609:47" -- a trailer, not a
    # ticker. Filtering on the Y flag drops it without index arithmetic.
    df = df[df["Nasdaq Traded"] == "Y"].copy()

    df["key"] = df["CQS Symbol"].fillna(
        df["NASDAQ Symbol"].fillna(df["Symbol"])).astype(str)
    df["key"] = df["key"].str.strip().str.upper().str.replace("/", ".", regex=False)
    return df[["key", "Security Name", "ETF", "Test Issue", "Financial Status",
               "Listing Exchange"]].drop_duplicates("key")


def _load_nasdaq_cached() -> tuple[pd.DataFrame, bool]:
    """(directory, degraded). Network -> cache -> empty."""
    try:
        df = fetch_nasdaq_traded()
        tmp = config.NASDAQ_FILE.with_suffix(".parquet.tmp")
        config.NASDAQ_FILE.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(tmp, compression=config.COMPRESSION, index=False)
        tmp.replace(config.NASDAQ_FILE)
        return df, False
    except Exception as exc:
        print(f"  ! nasdaqtraded.txt unavailable ({repr(exc)[:90]})")
        if config.NASDAQ_FILE.exists():
            print("    falling back to the cached directory")
            return pd.read_parquet(config.NASDAQ_FILE), True
        print("    no cache; falling back to Alpaca name heuristics (DEGRADED)")
        return pd.DataFrame(columns=["key", "Security Name", "ETF", "Test Issue",
                                     "Financial Status", "Listing Exchange"]), True


# --------------------------------------------------------------- suffix rules
def _suffix_verdict(sym: str) -> str | None:
    """Reason to drop `sym` on its suffix alone, or None to keep.

    Alpaca has no instrument-type field but encodes it in the symbol: .PRx are
    preferred series (363 of them), .WS/.WSA warrants (67), .U units (49),
    .RT rights (18), while .A/.B/.C/.V are legitimate class shares (24,
    including BRK.A and BRK.B).
    """
    if "." not in sym:
        return None
    suffix = "." + sym.rsplit(".", 1)[1]
    if suffix in config.KEEP_SUFFIXES:
        return None
    if any(suffix.startswith(p) for p in config.DROP_SUFFIX_PREFIXES):
        return "preferred_suffix"
    if suffix in config.DROP_SUFFIXES:
        return "warrant_unit_right_suffix"
    return None


def build(verbose: bool = True) -> tuple[pd.DataFrame, dict, bool]:
    """Run the funnel. Returns (kept_frame, funnel_counts, degraded)."""
    assets = pd.DataFrame(alpaca.fetch_assets())
    funnel = {"active_us_equity": len(assets)}

    a = assets[assets["tradable"] == True].copy()          # noqa: E712
    funnel["tradable"] = len(a)

    a["ticker"] = a["symbol"].map(normalize)
    a = a[a["exchange"].isin(config.KEEP_EXCHANGES)].copy()
    funnel["listed_exchange"] = len(a)

    a["drop_reason"] = a["ticker"].map(_suffix_verdict)
    a = a[a["drop_reason"].isna()].drop(columns="drop_reason")
    funnel["suffix_ok"] = len(a)

    nas, degraded = _load_nasdaq_cached()
    a = a.merge(nas, how="left", left_on="ticker", right_on="key")

    # FAIL OPEN on join misses. Four symbols do not join, one of which is BRK.A
    # -- Berkshire Hathaway A being unmatched is the whole argument. A schema
    # change at Nasdaq must never silently delete real companies.
    a["name_source"] = a["Security Name"].notna().map(
        {True: "nasdaq", False: "alpaca"})
    a["sec_name"] = a["Security Name"].fillna(a["name"])

    a["is_etf"] = a["ETF"].eq("Y")
    before = len(a)
    a = a[~a["is_etf"]].copy()
    funnel["minus_etf_flag"] = len(a)
    funnel["_etf_dropped"] = before - len(a)

    a = a[a["Test Issue"].ne("Y")].copy()
    funnel["minus_test_issue"] = len(a)

    before = len(a)
    a = a[~a["sec_name"].fillna("").str.contains(_KILL)].copy()
    funnel["minus_name_kill"] = len(a)
    funnel["_name_dropped"] = before - len(a)

    out = pd.DataFrame({
        "ticker": a["ticker"].values,
        "name": a["name"].values,
        "exchange": a["exchange"].values,
        "is_etf": False,
        "sec_name": a["sec_name"].values,
        "fin_status": a["Financial Status"].fillna("").values,
        "shortable": a["shortable"].fillna(False).values,
        "etb": a["easy_to_borrow"].fillna(False).values,
        "marginable": a["marginable"].fillna(False).values,
        "fractionable": a["fractionable"].fillna(False).values,
        "has_options": (a["attributes"].map(
            lambda x: bool(x) and "options_enabled" in x) if "attributes" in a
            else False),
        "name_source": a["name_source"].values,
    }).drop_duplicates("ticker").sort_values("ticker").reset_index(drop=True)

    funnel["operating_companies"] = len(out)

    # Audit trail: keeps the raw payload so "why was X excluded?" is answerable
    # after the fact without a refetch.
    try:
        cols = [c for c in ("symbol", "name", "exchange", "tradable", "status",
                            "shortable", "easy_to_borrow", "marginable",
                            "fractionable") if c in assets.columns]
        tmp = config.ASSETS_RAW_FILE.with_suffix(".parquet.tmp")
        assets[cols].to_parquet(tmp, compression=config.COMPRESSION, index=False)
        tmp.replace(config.ASSETS_RAW_FILE)
    except Exception as exc:
        print(f"  ! could not write assets audit snapshot: {repr(exc)[:80]}")

    if verbose:
        print("  funnel:")
        for k, v in funnel.items():
            if not k.startswith("_"):
                print(f"    {k:22} {v:>7,}")
        print(f"    (etf flag dropped {funnel['_etf_dropped']:,}; "
              f"name filter dropped {funnel['_name_dropped']:,})")
        by_x = out["exchange"].value_counts().to_dict()
        print(f"    by exchange: " +
              ", ".join(f"{k} {v:,}" for k, v in sorted(by_x.items())))

    return out, funnel, degraded


# --------------------------------------------------------------- registry
def load() -> pd.DataFrame:
    if config.UNIVERSE_FILE.exists():
        return pd.read_parquet(config.UNIVERSE_FILE)
    return pd.DataFrame(columns=COLUMNS)


def save(df: pd.DataFrame) -> None:
    config.UNIVERSE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.UNIVERSE_FILE.with_suffix(".parquet.tmp")
    df.sort_values("ticker").reset_index(drop=True).to_parquet(
        tmp, compression=config.COMPRESSION,
        compression_level=config.COMPRESSION_LEVEL, index=False)
    tmp.replace(config.UNIVERSE_FILE)


def refresh(verbose: bool = True) -> tuple[pd.DataFrame, list[str], list[str], bool]:
    """Reconcile the registry against today's asset list.

    Returns (registry, added, removed, degraded).
    """
    today = date.today().isoformat()
    fresh, _funnel, degraded = build(verbose=verbose)
    reg = load()

    meta = ["name", "exchange", "is_etf", "sec_name", "fin_status", "shortable",
            "etb", "marginable", "fractionable", "has_options", "name_source"]

    if reg.empty:
        reg = fresh.copy()
        reg["first_seen"] = today
        reg["last_seen"] = today
        reg["status"] = "active"
        reg["fail_count"] = 0
        save(reg[COLUMNS])
        return reg[COLUMNS], sorted(fresh["ticker"]), [], degraded

    current = set(fresh["ticker"])
    known = set(reg["ticker"])
    added = sorted(current - known)
    was_active = set(reg.loc[reg["status"] == "active", "ticker"])
    removed = sorted(was_active - current)

    if added:
        new = fresh[fresh["ticker"].isin(added)].copy()
        new["first_seen"] = today
        new["last_seen"] = today
        new["status"] = "active"
        new["fail_count"] = 0
        reg = pd.concat([reg, new], ignore_index=True)

    # Refresh the metadata for everything still listed (names and flags change).
    reg = reg.set_index("ticker")
    upd = fresh.set_index("ticker")
    common = reg.index.intersection(upd.index)
    for c in meta:
        if c in upd.columns:
            reg.loc[common, c] = upd.loc[common, c]
    reg.loc[common, ["status", "last_seen"]] = ["active", today]
    reg = reg.reset_index()

    # Absent from the asset list = delisted. Observed directly, not inferred.
    reg.loc[reg["ticker"].isin(removed), "status"] = "removed"

    for c in COLUMNS:
        if c not in reg.columns:
            reg[c] = 0 if c == "fail_count" else ""
    reg["fail_count"] = reg["fail_count"].fillna(0).astype("int32")

    save(reg[COLUMNS])
    return reg[COLUMNS], added, removed, degraded


def fetchable(reg: pd.DataFrame | None = None, include_dead: bool = False) -> list[str]:
    """Tickers worth requesting: active + recently removed, minus proven dead."""
    reg = load() if reg is None else reg
    if reg.empty:
        return []
    wanted = ["active", "removed"] + (["dead"] if include_dead else [])
    return sorted(reg.loc[reg["status"].isin(wanted), "ticker"].tolist())


def record_outcome(found: set[str], attempted: list[str], healthy: bool) -> None:
    """Update fail counters. `healthy` guards against an outage killing the universe."""
    if not healthy or not attempted:
        return
    reg = load()
    if reg.empty:
        return

    miss = [t for t in attempted if t not in found]
    hit = reg["ticker"].isin(found)
    bad = reg["ticker"].isin(miss)

    reg.loc[hit, "fail_count"] = 0
    reg.loc[hit & (reg["status"] == "dead"), "status"] = "active"   # revived

    # Cast the whole column first: assigning int64 into an int32 column is
    # deprecated in pandas 2.x and would start raising.
    reg["fail_count"] = reg["fail_count"].fillna(0).astype("int64")
    reg.loc[bad, "fail_count"] = reg.loc[bad, "fail_count"] + 1
    reg.loc[bad & (reg["fail_count"] >= config.MAX_FAILS), "status"] = "dead"
    save(reg[COLUMNS])


def mark_dead(tickers: list[str], reason: str = "invalid_symbol") -> None:
    """Immediately retire symbols the API called invalid (from a 400 body)."""
    if not tickers:
        return
    reg = load()
    if reg.empty:
        return
    hit = reg["ticker"].isin([normalize(t) for t in tickers])
    if hit.any():
        reg.loc[hit, "status"] = "dead"
        reg.loc[hit, "fail_count"] = config.MAX_FAILS
        save(reg[COLUMNS])
        print(f"  marked dead ({reason}): {', '.join(sorted(reg.loc[hit, 'ticker']))}")


def retry_dead() -> int:
    """Clear fail counters on dead tickers so an outage cannot orphan them."""
    reg = load()
    if reg.empty:
        return 0
    dead = reg["status"] == "dead"
    n = int(dead.sum())
    if n:
        reg.loc[dead, "fail_count"] = 0
        save(reg[COLUMNS])
    return n


def is_healthy(found: set[str], attempted: list[str], error_rate: float) -> bool:
    if not attempted:
        return False
    return (len(found) >= config.HEALTHY_FRACTION * len(attempted)
            and error_rate < config.HEALTHY_MAX_ERROR_RATE)


def summary() -> dict:
    reg = load()
    if reg.empty:
        return {"total": 0, "active": 0, "removed": 0, "dead": 0}
    c = reg["status"].value_counts().to_dict()
    return {"total": len(reg), "active": c.get("active", 0),
            "removed": c.get("removed", 0), "dead": c.get("dead", 0)}


def explain(symbols: list[str]) -> None:
    """Per-stage verdict for specific symbols. The tuning/debug entry point."""
    assets = pd.DataFrame(alpaca.fetch_assets())
    assets["ticker"] = assets["symbol"].map(normalize)
    nas, _ = _load_nasdaq_cached()
    nas = nas.set_index("key")

    print("\n  verdicts:")
    for raw in symbols:
        t = normalize(raw)
        row = assets[assets["ticker"] == t]
        if row.empty:
            print(f"    {t:8} ABSENT from /v2/assets")
            continue
        r = row.iloc[0]
        checks: list[tuple[str, bool, str]] = [
            ("tradable", bool(r["tradable"]), str(r["tradable"])),
            ("exchange", r["exchange"] in config.KEEP_EXCHANGES, str(r["exchange"])),
            ("suffix", _suffix_verdict(t) is None, _suffix_verdict(t) or "ok"),
        ]
        if t in nas.index:
            n = nas.loc[t]
            sec = str(n["Security Name"])
            src = "nasdaq"
            checks += [("etf_flag", n["ETF"] != "Y", str(n["ETF"])),
                       ("test_issue", n["Test Issue"] != "Y", str(n["Test Issue"]))]
        else:
            sec = str(r["name"])
            src = "alpaca (join miss -> FAIL OPEN, kept)"
        hit = _KILL.search(sec)
        checks.append(("name_filter", hit is None, hit.group(0) if hit else "clean"))

        kept = all(ok for _, ok, _ in checks)
        print(f"    {t:8} {'KEPT' if kept else 'DROPPED':8} [{src}]  {sec[:52]}")
        for label, ok, detail in checks:
            if not ok:
                print(f"             FAIL {label}: {detail}")
    print()


def main() -> int:
    config.safe_console()
    ap = argparse.ArgumentParser(description="Universe builder and registry.")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--explain", nargs="*", metavar="SYM")
    a = ap.parse_args()

    config.dirs()

    if a.refresh or not config.UNIVERSE_FILE.exists():
        reg, added, removed, degraded = refresh()
        print(f"\n  registry: {summary()}")
        if degraded:
            print("  ! DEGRADED: Nasdaq directory came from cache or heuristics")
        if added:
            print(f"  + added   {len(added)}: {', '.join(added[:15])}"
                  f"{' ...' if len(added) > 15 else ''}")
        if removed:
            print(f"  - removed {len(removed)}: {', '.join(removed[:15])}"
                  f"{' ...' if len(removed) > 15 else ''}  (history retained)")
        n = len(reg[reg["status"] == "active"])
        ok = 5_000 <= n <= 6_000
        print(f"\n  active = {n:,}  (expect 5,300-5,500) -> {'OK' if ok else 'CHECK'}")
    else:
        print(f"  registry: {summary()}  (use --refresh to rebuild)")

    if a.explain:
        explain(a.explain)
    return 0


if __name__ == "__main__":
    sys.exit(main())

