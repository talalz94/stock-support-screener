#!/usr/bin/env python
"""The forward record of insider and House trades: what each one did to its stock's price.

Asked for on 2026-11-11: measure how insider trades affect the stock price, and keep tracking it
so correlations can be looked for later. The event study (insider_study.py) answered part of it
for directors' and officers' PURCHASES over 2016-2026. This file keeps a record going FORWARD for
every kind of trade the stock pages show, so that a later search for correlations runs on trades
written down BEFORE their outcome was known, not on a history re-read with hindsight.

WHAT IS RECORDED: one row per filing (kind, owner, ticker, filing date) filed on or after START.
  kind       "buy"        a director's or officer's open-market purchase (the study's population)
             "sell"       a director's or officer's open-market sale (code S)
             "other_buy"  a purchase by a fund, a 10%-only holder or an "Other" relationship
             "house_buy" / "house_sell"   a House member's stock trade ([ST], kind "trade"),
                          from the Periodic Transaction Reports
  features   WRITTEN ONCE, as known then, and never changed: value (House: the amount range),
             role, rare, track record, planned (10b5-1), routine/opportunistic, days from trade
             to filing, the support-zone band from the zone scan of the filing session, and --
             once the entry session exists -- the liquidity fifth and % of the 252-day high
  outcome    entry = the close of the first session after the filing date (event_returns: an
             evening filing cannot be traded at that day's close). `react` is that first
             session's own move (filing-day close to entry close). ret_h is the move from entry
             to h = 1, 5, 20, 60 sessions later; excess_h subtracts the mean move of every
             same-day stock in the same liquidity fifth, as every study here does (needs the
             stock eligible: $2+, $1M+ a day). Filled in as each horizon matures, never before;
             a horizon past the last session with no close is "no price", not a zero
  first_seen the session this file first wrote the row: evidence it was recorded before its
             outcome

NOTHING HERE IS A SIGNAL OR A SCORE. The summary counts what has matured and marks every group
with fewer than MIN_N matured events as too few to judge, so "no data yet" never reads as "no
effect". Excess against the liquidity pool is the baseline: zero means "did what similar stocks
did", whatever the market did.

Run:  python insider_track.py              update the record for the last closed session
      python insider_track.py --show       print the summary
      python insider_track.py --selftest   check it on a scratch ledger (the real one untouched)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import config

config.safe_console()

import event_returns as ER                                       # noqa: E402
import insider as INS                                            # noqa: E402

START = "2026-11-11"            # tracking began the day it was asked for
HORIZONS = (1, 5, 20, 60)       # sessions after entry
MIN_N = 30                      # fewer matured events than this: "too few to judge"
LEDGER = INS.ROOT / "_forward.parquet"
SUMMARY = config.DATA / "_insider_forward_summary.json"
KINDS = ("buy", "sell", "other_buy", "house_buy", "house_sell")
KIND_LABEL = {"buy": "Director and officer purchases", "sell": "Director and officer sales",
              "other_buy": "Fund and 10%-holder purchases", "house_buy": "House members' purchases",
              "house_sell": "House members' sales"}
KEY = ["kind", "owner", "ticker", "filing_date"]
FROZEN = ["trade_date", "role", "value", "amount_lo", "amount_hi", "planned", "rare", "track", "cmp",
          "lag_days", "zone_band", "zone_dist", "fifth", "pct_hi", "sector", "accession"]
OUTCOME = (["entry", "eligible", "suspect_split", "react", "react_ex"] + [f"ret_{h}" for h in HORIZONS]
           + [f"excess_{h}" for h in HORIZONS])
# The price path a trade is judged on runs from 20 sessions before its entry (for the volume
# median) to its longest horizon. An unadjusted split anywhere on it -- the screen's own test,
# pattern.suspect_split: a one-bar |log move| over 0.85 without a 3x volume burst -- makes every
# move on it fiction: ZONE's closes went 0.115 -> 7.14 on its entry day (2026-10-15, a reverse
# split the bar store had not adjusted), alone adding +13.7pp to the mean first-session move of
# 448 October purchases. Such a trade keeps its row, flagged, with no outcome.
SPLIT_PRE = 20
LABELS = [("react", "first session")] + [(str(h), f"{h} session{'s' if h > 1 else ''}") for h in HORIZONS]


def log(m: str) -> None:
    print(f"  [insider_track] {m}", flush=True)


# ============================================================ the events, as filed
def _buys(since: str, asof: str) -> pd.DataFrame:
    """Every purchase filing (directors and officers, and the rest) from the shared derived table."""
    import insider_signals as SIG
    d = SIG.derived(rebuild_if_stale=True)
    d = d[(d.filing_date >= since) & (d.filing_date <= asof) & d.ticker.notna()]
    if d.empty:
        return pd.DataFrame()
    return pd.DataFrame({
        "kind": np.where(d["who"] == "dno", "buy", "other_buy"),
        "owner": d.owner_cik.astype("Int64").astype(str).values,
        "ticker": d.ticker.astype(str).str.upper().values, "filing_date": d.filing_date.astype(str).values,
        "trade_date": d.trans_first.values, "role": d.role.values, "value": d.value.astype(float).values,
        "planned": pd.to_numeric(d.planned, errors="coerce").values,
        "rare": d.rare.astype(float).values, "track": d.track.astype(float).values, "cmp": d.cmp.values,
        "accession": d.accession.values})


def _sales(since: str, asof: str) -> pd.DataFrame:
    """Directors' and officers' open-market sales, one row per (owner, company, filing date),
    exactly as insider_signals.sales filters them for the pages."""
    s = INS.read(start=since, end=asof, codes=("S",),
                 columns=["owner_cik", "role", "trans_date", "trans_code", "acq_disp", "shares", "price",
                          "value", "is_director", "is_officer", "aff10b5one"])
    s = s[(s.acq_disp == "D") & (s.price > 0) & (s.shares > 0) & (s.is_director | s.is_officer) & s.ticker.notna()]
    if s.empty:
        return pd.DataFrame()
    s = s.assign(_td=pd.to_datetime(s.trans_date, errors="coerce"))
    g = (s.sort_values("accession")
          .groupby(["owner_cik", "issuer_cik", "filing_date"], as_index=False)
          .agg(ticker=("ticker", "first"), role=("role", "first"), value=("value", "sum"),
               planned=("aff10b5one", "max"), accession=("accession", "first"), trade_date=("_td", "min")))
    g = g[g.value <= INS.MAX_VALUE]
    return pd.DataFrame({
        "kind": "sell", "owner": pd.to_numeric(g.owner_cik, errors="coerce").astype("Int64").astype(str).values,
        "ticker": g.ticker.astype(str).str.upper().values, "filing_date": g.filing_date.astype(str).values,
        "trade_date": g.trade_date.dt.strftime("%Y-%m-%d").values, "role": g.role.values,
        "value": g.value.astype(float).values, "planned": pd.to_numeric(g.planned, errors="coerce").values,
        "accession": g.accession.values})


def _house(since: str, asof: str) -> pd.DataFrame:
    """House members' stock trades, one row per (member, ticker, kind, filing date); the amount
    is the sum of the reported ranges. Names stay in data/ (the ledger), never on a page."""
    import insider_signals as SIG
    h = SIG.house()
    h = h[(h.asset_type == "ST") & (h.kind == "trade") & h.ticker.notna()
          & h.ttype.isin(["P", "S", "S (partial)"])
          & (h.filing_date >= since) & (h.filing_date <= asof)]
    if h.empty:
        return pd.DataFrame()
    h = h.assign(kind=np.where(h.ttype == "P", "house_buy", "house_sell"),
                 tk=h.ticker.astype(str).str.upper(), _td=pd.to_datetime(h.trans_date, errors="coerce"))
    g = (h.groupby(["kind", "member", "tk", "filing_date"], as_index=False)
          .agg(amount_lo=("amount_lo", "sum"), amount_hi=("amount_hi", "sum"), trade_date=("_td", "min"),
               role=("owner", "first"), accession=("doc_id", "first")))
    return pd.DataFrame({
        "kind": g.kind.values, "owner": g.member.astype(str).values, "ticker": g.tk.values,
        "filing_date": g.filing_date.astype(str).values, "trade_date": g.trade_date.dt.strftime("%Y-%m-%d").values,
        "role": g.role.values, "amount_lo": g.amount_lo.astype(float).values,
        "amount_hi": g.amount_hi.astype(float).values, "accession": g.accession.astype(str).values})


def events(since: str, asof: str) -> pd.DataFrame:
    """Every trade filed in [since, asof], one row per KEY."""
    parts = [p for p in (_buys(since, asof), _sales(since, asof), _house(since, asof)) if len(p)]
    if not parts:
        return pd.DataFrame(columns=KEY)
    ev = pd.concat(parts, ignore_index=True)
    ev = ev[ev.ticker.str.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}")]
    ev = ev.drop_duplicates(KEY, keep="first").reset_index(drop=True)
    td = pd.to_datetime(ev.trade_date, errors="coerce")
    ev["lag_days"] = (pd.to_datetime(ev.filing_date, errors="coerce") - td).dt.days
    return ev


# ============================================================ features known on the filing day
_ZONES: dict[str, pd.DataFrame | None] = {}


def _zones_on(day: str) -> pd.DataFrame | None:
    """The zone scan of the last session on or before `day` (zones.py keeps 90 days of them)."""
    files = sorted(p for p in config.ZONES.glob("*.parquet") if p.stem <= day)
    if not files:
        return None
    f = files[-1]
    if f.stem not in _ZONES:
        try:
            z = pd.read_parquet(f, columns=["ticker", "band", "dist_pct", "no_support"])
            _ZONES[f.stem] = z
        except Exception:                                        # noqa: BLE001
            _ZONES[f.stem] = None
    return _ZONES[f.stem]


def zone_of(ticker: str, day: str) -> tuple[str | None, float]:
    """(band of the nearest support zone, its distance) on `day`: AT / NEAR / APPROACHING, "none"
    when the scan found no zone within 16%, None when the scan did not cover the name."""
    z = _zones_on(day)
    if z is None:
        return None, np.nan
    r = z[z.ticker == ticker]
    if r.empty:
        return None, np.nan
    if bool(r.no_support.fillna(False).any()) and r.dist_pct.isna().all():
        return "none", np.nan
    r = r[r.dist_pct.notna()].sort_values("dist_pct")
    return (str(r.band.iloc[0]), float(r.dist_pct.iloc[0])) if len(r) else ("none", np.nan)


def _sector_map() -> dict:
    try:
        import sectors
        return sectors.groups().drop_duplicates("ticker").set_index("ticker").sector.to_dict()
    except Exception:                                            # noqa: BLE001
        return {}


# ============================================================ outcomes
def measure(ev: pd.DataFrame, panel: ER.Panel) -> pd.DataFrame:
    """Entry, the first session's move and each horizon's move and excess, for whatever has
    happened by the panel's last session. Raw moves need only a close; excess needs the stock
    eligible on its entry day ($2+, $1M+ a day), as in every study."""
    out = pd.DataFrame(index=ev.index, columns=OUTCOME + ["fifth", "pct_hi"], dtype=object)
    if ev.empty:
        return out
    m = panel.measure(ev[["ticker", "filing_date"]].assign(_i=ev.index), date_col="filing_date")
    if m.empty:
        return out
    C = panel.C.values
    n = C.shape[0]
    cols = {t: i for i, t in enumerate(panel.C.columns)}
    ri = np.array([panel.pos[e] for e in m.entry])
    ci = np.array([cols[t] for t in m.ticker])
    bucket = panel.bucket.values
    c0 = C[ri, ci]
    prev = np.where(ri > 0, C[np.maximum(ri - 1, 0), ci], np.nan)
    react = c0 / prev - 1
    # The first session's pool: the mean move that day of the stock's own liquidity fifth,
    # computed once per (session, fifth) -- a month of sales is thousands of events on ~20 days.
    pool0 = np.full(len(m), np.nan)
    seen: dict = {}
    for k, (r, b) in enumerate(zip(ri, m.bucket.values)):
        if r <= 0 or not np.isfinite(b):
            continue
        if (r, b) not in seen:
            day = C[r] / C[r - 1] - 1
            same = (bucket[r] == b) & np.isfinite(day)
            seen[(r, b)] = float(np.nanmean(day[same])) if same.any() else np.nan
        pool0[k] = seen[(r, b)]
    o = pd.DataFrame(index=m._i.values)
    o["entry"] = m.entry.values
    o["eligible"] = m.eligible.astype(bool).values
    o["fifth"] = m.bucket.values
    o["pct_hi"] = m.pct_hi.values
    o["react"] = react
    o["react_ex"] = np.where(o.eligible.values, react - pool0, np.nan)
    for h in HORIZONS:
        j = ri + h
        ok = j < n
        r = np.full(len(m), np.nan)
        r[ok] = C[j[ok], ci[ok]] / c0[ok] - 1
        o[f"ret_{h}"] = r
        o[f"excess_{h}"] = m[f"excess_{h}"].values
    # The split check on each trade's path (see SPLIT_PRE): flagged trades keep no outcome.
    import pattern
    V = panel.V.values if panel.V is not None else None
    sus = np.zeros(len(m), dtype=bool)
    if V is not None:
        last = max(HORIZONS)
        for k, (r, c) in enumerate(zip(ri, ci)):
            a, b = max(0, r - SPLIT_PRE), min(n, r + last + 1)
            sus[k] = pattern.suspect_split(C[a:b, c], V[a:b, c], config)
    o["suspect_split"] = sus
    o.loc[sus, ["react", "react_ex"] + [f"ret_{h}" for h in HORIZONS] + [f"excess_{h}" for h in HORIZONS]] = np.nan
    out.loc[o.index, o.columns] = o.values
    return out


def horizon_state(L: pd.DataFrame, panel: ER.Panel) -> pd.DataFrame:
    """Per row and horizon: 'done' (a number), 'pending' (not reached yet), 'no price' (reached
    with no close: halted, delisted, a gap), 'no entry' (no session after the filing yet, or a
    ticker with no stored bars), 'suspect split' (the path failed the split check)."""
    n = len(panel.dates)
    st = pd.DataFrame(index=L.index)
    pos = L.entry.map(lambda e: panel.pos.get(e) if isinstance(e, str) else None)
    sus = L.get("suspect_split", pd.Series(False, index=L.index)).fillna(False).astype(bool)
    for h in HORIZONS:
        col = L[f"ret_{h}"].astype(float)
        st[str(h)] = np.where(col.notna(), "done",
                              np.where(sus, "suspect split",
                                       np.where(pos.isna(), "no entry",
                                                np.where(pos.fillna(0).astype(int) + h >= n, "pending", "no price"))))
    return st


# ============================================================ the update
def update(asof: str | None = None, ledger: Path = LEDGER, summary: Path | None = SUMMARY,
           since: str = START, verbose: bool = True) -> pd.DataFrame:
    """Add the trades filed since the last run, write each one's features once, and fill in every
    outcome that has matured. Idempotent: a second run on the same day changes nothing."""
    import calendar_us
    t0 = time.time()
    asof = asof or calendar_us.last_closed_session()
    old = pd.read_parquet(ledger) if ledger.exists() else pd.DataFrame(columns=KEY + ["first_seen"])
    ev = events(since, asof)
    new = ev.merge(old[KEY], on=KEY, how="left", indicator=True)
    new = new[new["_merge"] == "left_only"].drop(columns="_merge")
    if len(new):
        sec = _sector_map()
        zz = [zone_of(t, d) for t, d in zip(new.ticker, new.filing_date)]
        new["zone_band"] = [a for a, _b in zz]
        new["zone_dist"] = [b for _a, b in zz]
        new["sector"] = new.ticker.map(sec)
        new["first_seen"] = asof
        new["recorded_at"] = datetime.now().isoformat(timespec="seconds")
    L = pd.concat([old, new], ignore_index=True) if len(old) else new.reset_index(drop=True)
    for c in KEY + FROZEN + OUTCOME + ["first_seen", "recorded_at"]:
        if c not in L.columns:
            L[c] = np.nan
    if len(L):
        panel = ER.Panel(horizons=HORIZONS, since=since, keep_volume=True)
        m = measure(L, panel)
        # Features that need the entry session are written once, when it first exists.
        for c in ("fifth", "pct_hi"):
            L[c] = L[c].where(L[c].notna(), m[c])
        for c in OUTCOME:
            L[c] = m[c]
        for c in ["eligible", "suspect_split"]:
            L[c] = L[c].astype("boolean")
        num = ["react", "react_ex", "fifth", "pct_hi", "value", "amount_lo", "amount_hi", "planned", "rare",
               "track", "zone_dist", "lag_days"] + [f"ret_{h}" for h in HORIZONS] + [f"excess_{h}" for h in HORIZONS]
        for c in num:
            L[c] = pd.to_numeric(L[c], errors="coerce").astype(float)
        for c in ("entry", "trade_date", "role", "cmp", "zone_band", "sector", "accession", "first_seen",
                  "recorded_at", "owner", "ticker", "kind", "filing_date"):
            L[c] = L[c].astype(object).where(L[c].notna(), None)
        states = horizon_state(L, panel)
    else:
        states = pd.DataFrame()
    L = L.sort_values(["filing_date", "kind", "ticker", "owner"]).reset_index(drop=True)
    if len(states):
        states = states.reindex(L.index)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    tmp = ledger.with_suffix(".parquet.tmp")
    L.to_parquet(tmp, index=False, compression=config.COMPRESSION, compression_level=config.COMPRESSION_LEVEL)
    import store
    store.atomic_replace(tmp, ledger)
    if summary is not None:
        s = summarize(L, asof, since)
        tmp2 = summary.with_suffix(".json.tmp")
        tmp2.write_text(json.dumps(s, indent=1), encoding="utf-8")
        store.atomic_replace(tmp2, summary)
    if verbose:
        log(f"{len(new):,} new trade(s) recorded, {len(L):,} in the record since {since}; "
            f"{time.time() - t0:.0f}s")
    return L


# ============================================================ the summary
def _stats(x: pd.Series) -> dict:
    x = pd.to_numeric(x, errors="coerce").dropna()
    if not len(x):
        return {"n": 0}
    return {"n": int(len(x)), "mean_pp": round(float(x.mean() * 100), 2),
            "median_pp": round(float(x.median() * 100), 2),
            "beat_pool_pct": round(float((x > 0).mean() * 100), 1), "judge": bool(len(x) >= MIN_N)}


def summarize(L: pd.DataFrame, asof: str, since: str = START) -> dict:
    """Per kind: recorded, with an entry, and per horizon the matured excess (n, mean, median,
    share beating the pool), each marked judge=False below MIN_N. Buys are also split by the
    study's flags, for the later search for correlations."""
    out = {"start": since, "asof": asof, "written": datetime.now().isoformat(timespec="seconds"),
           "min_n": MIN_N, "recorded": int(len(L)), "kinds": {}, "buy_splits": {}}
    for k in KINDS:
        x = L[L.kind == k] if len(L) else L
        row = {"label": KIND_LABEL[k], "recorded": int(len(x)),
               "with_entry": int(x.entry.notna().sum()) if len(x) else 0,
               "suspect_split": int(x.suspect_split.fillna(False).astype(bool).sum()) if len(x) else 0,
               "horizons": {}}
        for key, _lab in LABELS:
            col = "react_ex" if key == "react" else f"excess_{key}"
            row["horizons"][key] = _stats(x[col]) if len(x) else {"n": 0}
        out["kinds"][k] = row
    b = L[L.kind == "buy"] if len(L) else L
    if len(b):
        splits = {"rare": b.rare == 1, "not rare": b.rare == 0,
                  "small (fifths 1-2)": b.fifth <= 1, "larger (fifths 3-5)": b.fifth >= 2,
                  "at or near support": b.zone_band.isin(["AT", "NEAR"]),
                  "no support within 16%": b.zone_band == "none"}
        for name, mask in splits.items():
            x = b[mask.fillna(False).astype(bool)]
            out["buy_splits"][name] = {"recorded": int(len(x)),
                                       **{key: _stats(x["react_ex" if key == "react" else f"excess_{key}"])
                                          for key, _l in LABELS}}
    return out


def read_summary() -> dict | None:
    try:
        return json.loads(SUMMARY.read_text(encoding="utf-8"))
    except Exception:                                            # noqa: BLE001
        return None


def page_note(kinds: tuple[str, ...]) -> str:
    """A short block for the Insiders and House pages: how much the forward record holds, and for
    each kind the newest horizon with matured events. Says plainly when nothing has matured."""
    import ui
    s = read_summary()
    if not s:
        return ("<div><b>Forward record:</b> not started yet. From the next nightly run every trade "
                "filed is written down with what its stock did after it (insider_track.py).</div>")
    parts = []
    for k in kinds:
        r = (s.get("kinds") or {}).get(k) or {}
        hz = r.get("horizons") or {}
        done = [(key, lab, hz.get(key) or {}) for key, lab in LABELS if (hz.get(key) or {}).get("n")]
        if not r.get("recorded"):
            parts.append(f"{ui.esc(r.get('label', k)).lower()}: none recorded yet")
            continue
        if not done:
            parts.append(f"{ui.esc(r.get('label', k)).lower()}: {r['recorded']:,} recorded, none matured yet")
            continue
        key, lab, st = done[-1]
        verdict = ("" if st.get("judge") else f", too few to judge (under {s.get('min_n', MIN_N)})")
        parts.append(f"{ui.esc(r.get('label', k)).lower()}: {r['recorded']:,} recorded; after {lab} "
                     f"{st['n']:,} matured, mean {st['mean_pp']:+.2f}pp and median {st['median_pp']:+.2f}pp "
                     f"against stocks of the same liquidity, {st['beat_pool_pct']:.0f}% beat them{verdict}")
    return (f"<div><b>Forward record since {ui.esc(s.get('start'))}</b> (insider_track.py): every trade filed "
            f"since is written down the night it appears, with its features as known then and what its stock "
            f"did over the first session and 1, 5, 20 and 60 sessions after it. So far &mdash; "
            + "; ".join(parts) + ".</div>")


# ============================================================ checks
def selftest() -> int:
    """On a SCRATCH ledger from 2026-10-01 (so there is something to measure): keys unique, every
    row filed in the window, features written once (a second run leaves them and first_seen alone),
    no outcome before its horizon is reached, and each move recomputed from the closes."""
    import tempfile

    import calendar_us
    asof = calendar_us.last_closed_session()
    since = "2026-10-01"
    with tempfile.TemporaryDirectory() as td:
        led = Path(td) / "fwd.parquet"
        L1 = update(asof, ledger=led, summary=None, since=since, verbose=False)
        assert len(L1) > 0, "nothing recorded since 2026-10-01"
        assert not L1.duplicated(KEY).any(), "duplicate keys"
        assert (L1.filing_date >= since).all() and (L1.filing_date <= asof).all(), "a row outside the window"
        assert set(L1.kind) <= set(KINDS), set(L1.kind)
        L2 = update(asof, ledger=led, summary=None, since=since, verbose=False)
        assert len(L2) == len(L1), "a second run added rows"
        a, b = L1.set_index(KEY).sort_index(), L2.set_index(KEY).sort_index()
        for c in ["first_seen", "zone_band", "value", "role", "fifth"]:
            assert a[c].astype(str).equals(b[c].astype(str)), f"{c} changed on a second run"
        panel = ER.Panel(horizons=HORIZONS, since=since)
        n = len(panel.dates)
        st = horizon_state(L2, panel)
        for h in HORIZONS:
            early = L2.entry.map(lambda e: panel.pos.get(e) if isinstance(e, str) else None)
            reached = early.notna() & (early.fillna(0).astype(int) + h < n)
            assert not (L2[f"ret_{h}"].notna() & ~reached).any(), f"ret_{h} filled before it was reached"
            assert ((st[str(h)] == "done") == L2[f"ret_{h}"].notna()).all()
        sus = L2.suspect_split.fillna(False).astype(bool)
        assert not L2.loc[sus, ["react", "ret_1", "excess_1"]].notna().any().any(), "a flagged path kept an outcome"
        # The split guard on a known case: ZONE's unadjusted reverse split on 2026-10-15.
        z = L2[(L2.ticker == "ZONE") & (L2.entry == "2026-10-15")]
        assert z.empty or bool(z.suspect_split.all()), "ZONE's unadjusted split passed the check"
        # Spot-check moves against the closes themselves.
        done = L2[L2.ret_5.notna()].head(25)
        for r in done.itertuples(index=False):
            p = panel.pos[r.entry]
            c = panel.C[r.ticker]
            assert abs((c.iloc[p + 5] / c.iloc[p] - 1) - r.ret_5) < 1e-6, (r.ticker, r.entry)
            assert abs((c.iloc[p] / c.iloc[p - 1] - 1) - r.react) < 1e-6, (r.ticker, r.entry)
        s = summarize(L2, asof, since)
        assert s["recorded"] == len(L2) and all(k in s["kinds"] for k in KINDS)
        kinds = L2.kind.value_counts().to_dict()
    print(f"  [insider_track] scratch record {since}..{asof}: {len(L2):,} trades {kinds}; keys unique, "
          f"features written once, no outcome before its horizon, moves match the closes")
    return 0


def show() -> int:
    s = read_summary()
    if not s:
        print("  no summary yet: run python insider_track.py")
        return 1
    print(f"  forward record since {s['start']}, as of {s['asof']}: {s['recorded']:,} trades")
    for k, r in s["kinds"].items():
        cells = []
        for key, lab in LABELS:
            st = r["horizons"].get(key) or {}
            cells.append(f"{lab}: " + (f"n={st['n']} mean {st['mean_pp']:+.2f}pp" if st.get("n") else "-"))
        print(f"  {r['label']:32s} {r['recorded']:5d} recorded | " + " | ".join(cells))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="The forward record of insider and House trades.")
    ap.add_argument("--asof")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.show:
        return show()
    update(a.asof)
    return show()


if __name__ == "__main__":
    sys.exit(main())
