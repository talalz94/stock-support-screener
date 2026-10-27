#!/usr/bin/env python
"""Insider and House buying as SIGNALS: one derivation shared by every page, badge and score.

WHY ONE MODULE. The Insiders page, the House page, the profile block, the "insider
buying at support" badge and the `insider` score module all ask the same questions --
which purchases are recent, which are rare, which buyer has a record, which company is
small. Answered five times, the answers would drift from each other and from the study
that measured them. Everything here reuses insider_study's own definitions (purchases,
believable prices, rarity, track record) and event_returns' liquidity fifths.

THE DERIVED TABLE, data/insider/_derived.parquet: one row per PURCHASE FILING
(owner, issuer, filing date), every one since 2006, with
  who       "dno" = a director or officer (the study's population -- what "insider
            buying" means), "other" = a fund, a 10%-only holder, an "Other" relationship
            (shown only when the page asks for them; never in the measured signals)
  rare      the owner's first open-market purchase in this company in 2+ years
  track     the owner's mean 60-session excess over his/her PREVIOUS measured purchases
            whose 60 sessions had ended before this filing (2+ needed): point in time
  ex60      this filing's own 60-session excess vs its liquidity fifth, once measurable
  ex60_end  the session that measurement ended (so "track as of D" stays point in time)
Rebuilt by `build()` (~2 min); every reader takes `derived()`, cached per process.

ALL FEATURES ARE POINT IN TIME except two small things, stated: the price check
compares a trade with its company's other insider trades within +-15 days (so a typo
filed today can be caught by a trade filed next week), and a quarterly dataset
supersedes the EDGAR copy of the same filing (amendments).

Run:  python insider_signals.py --build      rebuild the derived table
      python insider_signals.py --show T     one ticker's recent buying, as the pages see it
      python insider_signals.py --selftest
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta

import numpy as np
import pandas as pd

import config
import event_returns as ER
import insider as INS
import insider_study as IS

DERIVED = INS.ROOT / "_derived.parquet"
MIN_VALUE = 1e4            # the study's floor for a window to count as buying ($10K)
ROSS_VALUE = 2.5e5         # Ross's screen: 2+ buyers, $250K+, not the largest fifth
HOUSE_BIG = 50001          # the House result: purchases of $50,001 or more
SMALL_FIFTHS = 1           # liquidity fifths 0 and 1 (bottom two) = "small", as in the study
FIFTH_NAMES = {0: "1 (least liquid)", 1: "2", 2: "3", 3: "4", 4: "5 (most liquid)"}

_CACHE: dict = {}


def log(m: str) -> None:
    print(f"  [insider_signals] {m}", flush=True)


def form4_url(issuer_cik, accession: str) -> str | None:
    """SEC's index page for one filing, built from its accession number."""
    try:
        c = int(issuer_cik)
    except (TypeError, ValueError):
        return None
    a = str(accession or "").strip()
    if len(a.replace("-", "")) != 18:
        return None
    return f"https://www.sec.gov/Archives/edgar/data/{c}/{a.replace('-', '')}/{a}-index.htm"


def ptr_url(year, doc_id) -> str | None:
    try:
        return INS_PTR.format(y=int(year), doc=str(doc_id).strip())
    except (TypeError, ValueError):
        return None


INS_PTR = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{y}/{doc}.pdf"


# ============================================================ the derived table
def _features(rows: pd.DataFrame, who: str) -> pd.DataFrame:
    """insider_study.history_features (rare, routine...) plus the columns pages need."""
    f = IS.history_features(rows)
    key = ["owner_cik", "issuer_cik", "filing_date"]
    rows = rows.assign(_td=pd.to_datetime(rows.trans_date, errors="coerce"))
    extra = (rows.sort_values("accession")
                 .groupby(key, as_index=False)
                 .agg(owner_name=("owner_name", "first"), issuer_name=("issuer_name", "first"),
                      accession=("accession", "first"), n_acc=("accession", "nunique"),
                      trans_first=("_td", "min"), trans_last=("_td", "max"),
                      officer_title=("officer_title", "first"), is_entity=("is_entity", "max"),
                      is_ten_pct=("is_ten_pct", "max"), is_director=("is_director", "max"),
                      is_officer=("is_officer", "max")))
    for c in ("trans_first", "trans_last"):
        extra[c] = extra[c].dt.strftime("%Y-%m-%d")
    f = f.merge(extra, on=key, how="left")
    f["price"] = (f.value / f.shares).where(f.shares > 0)
    f["who"] = who
    return f


def build(verbose: bool = True) -> pd.DataFrame:
    """Every purchase filing since 2006 with its point-in-time features. ~2 minutes."""
    t0 = time.time()
    raw = INS.read(codes=("P",))
    raw_s = INS.read(start="2016-06-01", codes=("S",), columns=["trans_date", "price"])
    dno = INS.purchases(raw)
    broad = INS.purchases(raw, include_entities=True, include_ten_pct_only=True, include_other=True)
    cols = ["issuer_cik", "accession", "trans_date", "price"]
    # The study's peers exactly: the directors' and officers' purchases plus every sale.
    peers = pd.concat([dno[cols], raw_s[cols]])
    ok = INS.price_ok(broad, peers)
    dropped = int((~ok).sum())
    broad = broad[ok]
    is_dno = broad.index.isin(dno.index)
    del raw, raw_s, peers
    if verbose:
        log(f"purchases {len(broad):,} ({int(is_dno.sum()):,} by directors/officers), "
            f"{dropped:,} dropped as unbelievable prices; {time.time() - t0:.0f}s")
    # Each population's history is its own, as in the study: a director's rarity is
    # judged on his/her director purchases.
    f = pd.concat([_features(broad[is_dno], "dno"), _features(broad[~is_dno], "other")],
                  ignore_index=True)
    if verbose:
        log(f"{len(f):,} purchase filings featured; {time.time() - t0:.0f}s")
    panel = ER.Panel(horizons=(60,))
    _keep_buckets(panel)
    if verbose:
        log(f"liquidity panel built; {time.time() - t0:.0f}s")
    f["track"] = np.nan
    for who in ("dno", "other"):
        m = f["who"] == who
        f.loc[m, "track"] = IS.track_records(f[m].reset_index(drop=True), panel).values
    # This filing's own 60-session result and when it became known.
    mm = f[f.ticker.notna()][["ticker", "filing_date"]].rename(columns={"filing_date": "event_date"})
    meas = panel.measure(mm.assign(_i=mm.index))
    meas = meas[meas.excess_60.notna()]
    f["ex60"] = np.nan
    f["ex60_end"] = None
    f.loc[meas._i.values, "ex60"] = meas.excess_60.values
    f.loc[meas._i.values, "ex60_end"] = [panel.dates[min(panel.pos[e] + 60, len(panel.dates) - 1)]
                                         for e in meas.entry]
    del panel
    f = f.sort_values(["filing_date", "issuer_cik", "owner_cik"]).reset_index(drop=True)
    for c in ("owner_cik", "issuer_cik"):
        f[c] = pd.to_numeric(f[c], errors="coerce").astype("Int64")
    f["planned"] = pd.to_numeric(f.planned, errors="coerce")
    tmp = DERIVED.with_suffix(".parquet.tmp")
    f.to_parquet(tmp, index=False, compression=config.COMPRESSION, compression_level=config.COMPRESSION_LEVEL)
    import store
    store.atomic_replace(tmp, DERIVED)
    for k in [k for k in _CACHE if k == "derived" or (isinstance(k, tuple) and k[0] in ("per_ticker", "badges", "tracks"))]:
        _CACHE.pop(k, None)
    if verbose:
        log(f"wrote {DERIVED.name}: {len(f):,} purchase filings ({(f['who'] == 'dno').sum():,} directors/officers), "
            f"{f.track.notna().sum():,} with a track record; {time.time() - t0:.0f}s")
    return f


def _store_mtime() -> float:
    ts = [p.stat().st_mtime for d in (INS.DS_DIR, INS.ED_DIR) if d.exists() for p in d.glob("*.parquet")]
    return max(ts) if ts else 0.0


def is_stale() -> bool:
    return (not DERIVED.exists()) or DERIVED.stat().st_mtime < _store_mtime()


def derived(rebuild_if_stale: bool = False) -> pd.DataFrame:
    """The derived table, read once per process. Rebuilt first if the trade store is newer
    and the caller asked for that (the nightly steps do; a page preview does not)."""
    if rebuild_if_stale and is_stale():
        return _remember(build(verbose=False))
    if "derived" not in _CACHE:
        if not DERIVED.exists():
            return _remember(build(verbose=False))
        _CACHE["derived"] = pd.read_parquet(DERIVED)
    return _CACHE["derived"]


def _remember(f: pd.DataFrame) -> pd.DataFrame:
    _CACHE["derived"] = f
    return f


# ============================================================ liquidity fifths
def _keep_buckets(panel: ER.Panel) -> None:
    """Keep only the liquidity fifths (float32) and the last closes from a panel: the
    rest is ~0.5 GB."""
    _CACHE["buckets"] = panel.bucket.astype("float32")
    _CACHE["closes"] = panel.C.tail(30).copy()


def buckets() -> pd.DataFrame:
    """date x ticker liquidity fifth (0 least .. 4 most liquid, NaN = not eligible)."""
    if "buckets" not in _CACHE:
        C, V = __import__("sectors").load_wide()
        dv = (C * V).rolling(20, min_periods=15).mean()
        elig = (C >= ER.MIN_PX) & (dv >= ER.MIN_DV)
        b = (dv.where(elig).rank(axis=1, pct=True) * ER.N_BUCKETS).clip(upper=ER.N_BUCKETS - 1e-9)
        _CACHE["buckets"] = np.floor(b).astype("float32")
        _CACHE["closes"] = C.tail(30).copy()
    return _CACHE["buckets"]


def close_on(asof: str) -> pd.Series:
    """ticker -> close on the last session at or before `asof` (the wide close matrix)."""
    buckets()
    c = _CACHE["closes"]
    idx = [d for d in c.index if str(d) <= asof]
    return c.loc[idx[-1]].dropna() if idx else pd.Series(dtype="float64")


def fifth_on(asof: str) -> pd.Series:
    """ticker -> liquidity fifth on the last session at or before `asof`."""
    b = buckets()
    idx = [d for d in b.index if str(d) <= asof]
    if not idx:
        return pd.Series(dtype="float32")
    return b.loc[idx[-1]].dropna()


# ============================================================ windows
def _day(asof: str, days: int) -> str:
    return (date.fromisoformat(asof) - timedelta(days=days)).isoformat()


def filings(asof: str, days: int, who: tuple[str, ...] = ("dno",), d: pd.DataFrame | None = None) -> pd.DataFrame:
    """Purchase filings with filing date in (asof - days, asof]."""
    d = derived() if d is None else d
    lo = _day(asof, days)
    w = d[(d.filing_date > lo) & (d.filing_date <= asof) & d["who"].isin(who)]
    return w


def owner_tracks(asof: str, owners=None, d: pd.DataFrame | None = None) -> pd.DataFrame:
    """Each owner's record AS OF `asof`: mean 60-session excess of purchases whose 60
    sessions had ended by then, and how many. Same rule as the study's `track`."""
    d = derived() if d is None else d
    m = d[d.ex60.notna() & (d.ex60_end.astype(str) <= asof)]
    if owners is not None:
        m = m[m.owner_cik.isin(set(owners))]
    g = m.groupby("owner_cik").ex60.agg(["mean", "count"]).rename(columns={"mean": "track_now", "count": "track_n"})
    g.loc[g.track_n < 2, "track_now"] = np.nan
    return g


def per_ticker(asof: str, days: int = 30, d: pd.DataFrame | None = None,
               fifth: pd.Series | None = None) -> pd.DataFrame:
    """One row per ticker with director/officer buying in the window, the study's flags set.

    Flags carry the study's own thresholds: each needs $10K+ bought in the window, as the
    measured signals did; `small` is the bottom two liquidity fifths ON `asof`.
    Cached per (asof, days) when reading the shared table: a profile batch asks once per page."""
    key = ("per_ticker", asof, days)
    if d is None and fifth is None and key in _CACHE:
        return _CACHE[key]
    out = _per_ticker(asof, days, d, fifth)
    if d is None and fifth is None:
        _CACHE[key] = out
    return out


def _per_ticker(asof: str, days: int, d: pd.DataFrame | None, fifth: pd.Series | None) -> pd.DataFrame:
    w = filings(asof, days, d=d)
    w = w[w.ticker.notna()]
    if w.empty:
        return pd.DataFrame(columns=["ticker", "buyers", "value", "rare", "ceo_cfo", "best_track", "fifth",
                                     "small", "ross", "last_filed", "planned_value"])
    g = w.groupby("ticker").agg(buyers=("owner_cik", "nunique"), value=("value", "sum"),
                                rare_any=("rare", "max"), best_track=("track", "max"),
                                last_filed=("filing_date", "max"),
                                ceo_cfo_any=("role", lambda s: bool(s.isin(["CEO", "CFO"]).any())),
                                planned_value=("value", lambda s: float(s[w.loc[s.index, "planned"] == 1].sum())))
    fifth = fifth_on(asof) if fifth is None else fifth
    g["fifth"] = fifth.reindex(g.index)
    big = g.value >= MIN_VALUE
    g["rare"] = g.rare_any.astype(bool) & big
    g["ceo_cfo"] = g.ceo_cfo_any.astype(bool) & big
    g["small"] = (g.fifth <= SMALL_FIFTHS) & big
    g["good_track"] = (g.best_track > 0) & big
    g["ross"] = (g.buyers >= 2) & (g.value >= ROSS_VALUE) & (g.fifth <= 3)
    return g.drop(columns=["rare_any", "ceo_cfo_any"]).reset_index()


SALE_YEARS = 5


def sales(asof: str) -> pd.DataFrame:
    """Director/officer open-market SALES (code S, disposed, price > 0) filed in the
    SALE_YEARS before `asof`, one row per (owner, issuer, filing date) -- for display
    (profile markers and trade tables). Not a signal: selling measured not bearish.
    Read once per process: ~12s for five years, so a profile batch primes it once.
    A filing over MAX_VALUE is dropped as a typing error, as purchases are."""
    key = ("sales", asof)
    if key not in _CACHE:
        lo = (date.fromisoformat(asof) - timedelta(days=365 * SALE_YEARS)).isoformat()
        s = INS.read(start=lo, end=asof, codes=("S",),
                     columns=["owner_cik", "owner_name", "is_director", "is_officer", "role", "trans_date",
                              "trans_code", "acq_disp", "shares", "price", "value", "aff10b5one"])
        s = s[(s.acq_disp == "D") & (s.price > 0) & (s.shares > 0) & (s.is_director | s.is_officer) & s.ticker.notna()]
        s = s.assign(_td=pd.to_datetime(s.trans_date, errors="coerce"))
        g = (s.sort_values("accession")
              .groupby(["owner_cik", "issuer_cik", "filing_date"], as_index=False)
              .agg(ticker=("ticker", "first"), owner_name=("owner_name", "first"), role=("role", "first"),
                   value=("value", "sum"), shares=("shares", "sum"), planned=("aff10b5one", "max"),
                   accession=("accession", "first"), trans_first=("_td", "min")))
        g = g[g.value <= INS.MAX_VALUE]
        g["trans_first"] = g.trans_first.dt.strftime("%Y-%m-%d")
        g["price"] = (g.value / g.shares).where(g.shares > 0)
        _CACHE[key] = g
    return _CACHE[key]


def prime(asof: str) -> None:
    """Everything a profile batch reads, once: the derived table, five years of sales,
    the House store, liquidity fifths and every owner's record as of `asof`."""
    d = derived()
    sales(asof)
    house()
    buckets()
    _CACHE[("tracks", asof)] = owner_tracks(asof, d=d)


def tracks(asof: str) -> pd.DataFrame:
    if ("tracks", asof) not in _CACHE:
        _CACHE[("tracks", asof)] = owner_tracks(asof)
    return _CACHE[("tracks", asof)]


# ============================================================ the House
def house(d: pd.DataFrame | None = None) -> pd.DataFrame:
    """Every readable House trade, filing date set, with report lag in days."""
    if "house" not in _CACHE:
        import congress as CG
        h = CG.read()
        h = h[h.filing_date.notna()].copy()
        h["lag_days"] = (pd.to_datetime(h.filing_date, errors="coerce")
                         - pd.to_datetime(h.trans_date, errors="coerce")).dt.days
        _CACHE["house"] = h
    return _CACHE["house"]


def house_buys(asof: str, days: int, big_only: bool = False) -> pd.DataFrame:
    """The study's House purchases: [ST], P, kind 'trade', filed in (asof - days, asof]."""
    h = house()
    m = ((h.asset_type == "ST") & (h.ttype == "P") & (h.kind == "trade")
         & (h.filing_date > _day(asof, days)) & (h.filing_date <= asof) & h.ticker.notna())
    if big_only:
        m &= h.amount_lo >= HOUSE_BIG
    return h[m]


# ============================================================ zones + badges
def zone_status(asof: str) -> pd.DataFrame:
    """ticker -> the NEAREST support level from the Zones file for `asof` (or the newest
    before it): band (AT/NEAR/APPROACHING), level, distance."""
    key = ("zones", asof)
    if key in _CACHE:
        return _CACHE[key]
    zdir = config.DATA / "zones"
    files = sorted(p for p in zdir.glob("*.parquet") if p.stem <= asof) if zdir.exists() else []
    if not files:
        out = pd.DataFrame(columns=["band", "level", "dist_pct", "zones_asof"])
    else:
        z = pd.read_parquet(files[-1], columns=["ticker", "level", "dist_pct", "band", "no_support", "suspect_split"])
        z = z[~z.no_support.astype(bool) & ~z.suspect_split.astype(bool) & z.level.notna()]
        z = z.sort_values(["ticker", "dist_pct"]).drop_duplicates("ticker").set_index("ticker")
        out = z[["band", "level", "dist_pct"]].assign(zones_asof=files[-1].stem)
    _CACHE[key] = out
    return out


BADGE = "insider buying at support"
BADGE_HELP = ("a recent RARE (first in 2+ years) or SMALL-company director/officer purchase of $10K+ "
              "(filed in the last 30 days), or a House member's $50,001+ stock purchase (filed in the last "
              "60 days), on a stock now AT or NEAR a Zones support level. Each part was measured on its own "
              "(see the Insiders page); the combination with support has NOT been measured.")


def badges(asof: str) -> dict[str, str]:
    """ticker -> short reason, for names at/near support with measured-signal buying."""
    key = ("badges", asof)
    if key in _CACHE:
        return _CACHE[key]
    z = zone_status(asof)
    near = set(z.index[z.band.isin(["AT", "NEAR"])])
    out: dict[str, list[str]] = {}
    try:
        pt = per_ticker(asof, 30)
        for r in pt.itertuples(index=False):
            if r.ticker in near and (r.rare or r.small):
                out.setdefault(r.ticker, []).append(
                    " + ".join(x for x, on in (("rare insider buy", r.rare), ("small-company insider buy", r.small)) if on))
    except Exception as exc:                                     # noqa: BLE001
        log(f"insider badges unavailable: {exc!r}"[:160])
    try:
        hb = house_buys(asof, 60, big_only=True)
        for t in sorted(set(hb.ticker) & near):
            out.setdefault(t, []).append("House $50K+ purchase")
    except Exception as exc:                                     # noqa: BLE001
        log(f"House badges unavailable: {exc!r}"[:160])
    res = {t: f"{'; '.join(v)} at {z.at[t, 'band']} support" for t, v in out.items()}
    _CACHE[key] = res
    return res


# ============================================================ reading the evidence
def evidence() -> dict:
    """Both study summaries, keyed by test name and horizon. Never hard-coded."""
    import json
    out = {}
    for name, p in (("insider", IS.OUT_SUMMARY), ("house", config.DATA / "_congress_study_summary.json")):
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            out[name] = None
            continue
        s["by"] = {(r["test"], int(r["h"])): r for r in s.get("results", [])}
        out[name] = s
    return out


# ============================================================ selftest / CLI
def selftest() -> int:
    assert form4_url(320193, "0000320193-26-000001") == \
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/0000320193-26-000001-index.htm"
    assert form4_url(None, "x") is None and form4_url(1, "bad") is None
    assert ptr_url(2026, "20034869").endswith("/ptr-pdfs/2026/20034869.pdf")
    # A synthetic window: two buyers in one small company, one rare.
    d = pd.DataFrame({"who": ["dno", "dno", "dno", "other"], "ticker": ["AAA", "AAA", "BBB", "AAA"],
                      "owner_cik": [1, 2, 3, 4], "issuer_cik": [9, 9, 8, 9],
                      "filing_date": ["2026-10-20", "2026-10-01", "2026-09-01", "2026-10-21"],
                      "value": [8e3, 5e3, 3e5, 1e6], "rare": [True, False, True, True],
                      "track": [np.nan, 0.02, -0.01, 0.5], "role": ["CEO", "Director", "CFO", "10% owner"],
                      "planned": [0.0, 1.0, 0.0, 0.0], "ex60": [np.nan, 0.1, 0.2, np.nan],
                      "ex60_end": [None, "2026-01-02", "2026-12-31", None]})
    fifth = pd.Series({"AAA": 0.0, "BBB": 4.0})
    pt = per_ticker("2026-10-26", 30, d=d, fifth=fifth).set_index("ticker")
    assert list(pt.index) == ["AAA"], pt                     # BBB filed outside 30 days
    a = pt.loc["AAA"]
    assert a.buyers == 2 and a.value == 13e3 and a.rare and a.small and a.ceo_cfo, a
    assert a.planned_value == 5e3 and a.best_track == 0.02, a
    assert not a.ross                                        # under $250K
    pt60 = per_ticker("2026-10-26", 60, d=d, fifth=fifth).set_index("ticker")
    assert bool(pt60.loc["BBB", "rare"]) and not bool(pt60.loc["BBB", "small"])
    tr = owner_tracks("2026-10-26", d=d)
    assert tr.empty or tr.track_now.isna().all()            # one ended measure is not a record
    print("insider_signals selftest OK")
    return 0


def show(ticker: str, asof: str | None = None) -> int:
    import calendar_us
    asof = asof or calendar_us.last_closed_session()
    d = derived()
    t = ticker.upper()
    w = d[(d.ticker == t) & (d.filing_date > _day(asof, 365))]
    pd.set_option("display.width", 200)
    print(w[["filing_date", "who", "role", "value", "price", "rare", "track", "planned", "accession"]].to_string(index=False))
    print(per_ticker(asof, 30).set_index("ticker").reindex([t]).T.to_string())
    print("zone:", zone_status(asof).reindex([t]).to_dict("records"), "badge:", badges(asof).get(t))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--show")
    ap.add_argument("--asof")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.build:
        build()
        return 0
    if a.show:
        return show(a.show, a.asof)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
