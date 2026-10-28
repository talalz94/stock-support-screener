#!/usr/bin/env python
"""Corporate insider trades from SEC Form 4 -- history and nightly, one store, free.

WHY. Officers, directors and 10% owners must report a trade in their own company's
stock within two business days (Form 4). Buying with their own money, at the price
anyone pays, is the signal "insider buying" scanners sell. This module keeps our own
copy of every Form 4 trade, so the screener can find, filter and TEST it
(insider_study.py) instead of trusting a vendor's story about it.

TWO SOURCES, ONE NORMALIZER, ONE SCHEMA
  history  SEC's quarterly "Insider Transactions Data Sets" (2006q1 onward,
           https://www.sec.gov/dera/data/form-345): the flattened XML of every
           Form 3/4/5, one zip per quarter, posted after the quarter ends.
  nightly  EDGAR's daily index lists each new Form 4; its full submission (one
           request, ~6 KB) carries the same XML. Everything filed after the last
           posted quarter comes this way, and the quarter's dataset supersedes it
           once SEC posts it (same accession numbers; the dataset row wins).

WHAT IS KEPT. Forms 4 and 4/A, non-derivative transactions (the stock itself --
options and other derivatives are left out). Personal contact fields the files
carry (filers' addresses, e-mails, phone numbers) are never read into the store.

ONE ROW PER TRADE, ONE PERSON PER FILING. A filing can list several reporting
owners for the same trades (a director and her trust, a fund and its general
partner). Joining every owner to every trade would count one purchase two or three
times, so each filing gets ONE primary owner -- the first listed who is a director
or officer, else the first listed -- with `n_owners` and `owner_ciks` kept beside it.

Store: data/insider/trades/<YYYYqN>.parquet       (from the quarterly datasets)
       data/insider/trades_edgar/<YYYYqN>.parquet (from EDGAR, by filing quarter)
       data/insider/_ledger.parquet               (every EDGAR filing fetched, and how it went)

Run:   python insider.py --backfill [--start 2006q1]   quarterly datasets
       python insider.py --update                      new quarter if posted + EDGAR days
       python insider.py --check                       sanity counts over the store
       python insider.py --selftest
"""
from __future__ import annotations

import argparse
import io
import re
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

import config
import store_lock

ROOT = config.DATA / "insider"
DS_DIR = ROOT / "trades"
ED_DIR = ROOT / "trades_edgar"
RAW = ROOT / "raw"
LEDGER = ROOT / "_ledger.parquet"
DS_URL = "https://www.sec.gov/files/{seg}/data/insider-transactions-data-sets/{q}_form345.zip"
DS_SEGMENTS = ("structureddata", "datastandardsinnovation")   # SEC moved the files in 2026
FIRST_QUARTER = "2006q1"
FORMS = ("4", "4/A")
WORKERS = 4
FAIL_CEILING = 0.25          # share of EDGAR fetches that may fail before the run fails
# ONE WRITER AT A TIME (store_lock.py): the House store lost rows to a nightly step and a
# manual backfill merging at once; trades_edgar/ + _ledger.parquet have the same shape.
WAIT_NIGHTLY = 60            # the nightly step waits this long, then reports SKIPPED
WAIT_CLI = 4 * 3600          # a manual run waits for the nightly instead

COLS = ["accession", "form", "filing_date", "period", "issuer_cik", "issuer_name", "issuer_symbol",
        "owner_cik", "owner_name", "n_owners", "owner_ciks", "is_director", "is_officer", "is_ten_pct",
        "is_other", "officer_title", "role", "is_entity", "aff10b5one", "security_title", "trans_date",
        "trans_code", "acq_disp", "shares", "price", "value", "shares_after", "direct", "price_fn", "src"]

# A reporting owner that is an organisation, not a person. Shown, and by default left
# out of "insider buying": a fund over 10% reports every top-up (Ross's Mexico Fund).
_ENTITY = re.compile(r"\b(L\.?P\.?|L\.?L\.?C\.?|LTD|LIMITED|INC|CORP|CORPORATION|CO|COMPANY|INSURANCE|ASSURANCE|"
                     r"FUND|FUNDS|TRUST|CAPITAL|"
                     r"PARTNERS|PARTNERSHIP|HOLDINGS?|MANAGEMENT|ADVISORS?|ADVISERS?|INVESTMENTS?|GROUP|"
                     r"ASSOCIATES|FOUNDATION|BANK|PLC|S\.?A\.?|AG|N\.?V\.?|B\.?V\.?|GMBH|VENTURES?|"
                     r"OPPORTUNIT(?:Y|IES)|MASTER|OFFSHORE|ONSHORE)\b", re.I)


def log(m: str) -> None:
    print(f"  [insider] {m}", flush=True)


# ============================================================ normalising
def role_of(is_dir, is_off, is_ten, is_oth, title: str | None) -> str:
    """One label per person, most senior first. Title text decides officer roles."""
    t = title.lower() if isinstance(title, str) else ""
    if is_off:
        if re.search(r"\bchief executive|\bceo\b", t):
            return "CEO"
        if re.search(r"\bchief financial|\bcfo\b|principal financial", t):
            return "CFO"
        if re.search(r"\bpresident\b", t):
            return "President"
        if re.search(r"\bchief operating|\bcoo\b", t):
            return "COO"
        if re.search(r"\bchair", t):
            return "Chair"
        return "Officer"
    if is_dir:
        return "Chair" if re.search(r"\bchair", t) else "Director"
    if is_ten:
        return "10% owner"
    return "Other"


def _iso(s) -> str | None:
    """'31-JUL-2026' (datasets) or '2026-07-31' (XML) -> '2026-07-31'."""
    if s is None or (isinstance(s, float) and np.isnan(s)):
        return None
    s = str(s).strip()
    if not s:
        return None
    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:11] if fmt == "%d-%b-%Y" else s[:10], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _iso_s(s: pd.Series) -> pd.Series:
    """`_iso` for a whole column at once: strptime per row was 90% of a quarter's parse."""
    a = pd.to_datetime(s, format="%d-%b-%Y", errors="coerce")
    b = pd.to_datetime(s.where(a.isna()), format="%Y-%m-%d", errors="coerce")
    return a.fillna(b).dt.strftime("%Y-%m-%d").where(a.notna() | b.notna(), None)


def _num_s(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.replace(",", "", regex=False), errors="coerce")


def _num(s) -> float:
    try:
        f = float(str(s).replace(",", ""))
        return f if np.isfinite(f) else np.nan
    except (TypeError, ValueError):
        return np.nan


def _flag(s) -> bool:
    return str(s).strip().lower() in ("1", "true", "t", "y", "yes")


def _finish(df: pd.DataFrame, src: str) -> pd.DataFrame:
    """Derived columns and dtypes shared by both sources."""
    if df.empty:
        return pd.DataFrame(columns=COLS)
    df = df.copy()
    df["shares"] = pd.to_numeric(df["shares"], errors="coerce")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["shares_after"] = pd.to_numeric(df["shares_after"], errors="coerce")
    df["value"] = (df["shares"] * df["price"]).where(df["price"] > 0)
    df["role"] = [role_of(a, b, c, d, t) for a, b, c, d, t in
                  zip(df.is_director, df.is_officer, df.is_ten_pct, df.is_other, df.officer_title)]
    df["is_entity"] = df["owner_name"].fillna("").map(lambda n: bool(_ENTITY.search(n)))
    df["issuer_cik"] = pd.to_numeric(df["issuer_cik"], errors="coerce").astype("Int64")
    df["owner_cik"] = pd.to_numeric(df["owner_cik"], errors="coerce").astype("Int64")
    df["src"] = src
    for c in COLS:
        if c not in df.columns:
            df[c] = np.nan
    return df[COLS].reset_index(drop=True)


# ------------------------------------------------------------ the datasets
def _read_tsv(z: zipfile.ZipFile, name: str, usecols: list[str]) -> pd.DataFrame:
    head = pd.read_csv(z.open(name), sep="\t", dtype=str, nrows=0).columns
    cols = [c for c in usecols if c in head]
    return pd.read_csv(z.open(name), sep="\t", dtype=str, usecols=cols, keep_default_na=False,
                       na_values=[""], quoting=3, on_bad_lines="skip")


def parse_dataset(zbytes: bytes) -> pd.DataFrame:
    """One quarter's zip -> normalised Form 4 / 4/A non-derivative trades."""
    z = zipfile.ZipFile(io.BytesIO(zbytes))
    sub = _read_tsv(z, "SUBMISSION.tsv", ["ACCESSION_NUMBER", "FILING_DATE", "PERIOD_OF_REPORT", "DOCUMENT_TYPE",
                                          "ISSUERCIK", "ISSUERNAME", "ISSUERTRADINGSYMBOL", "AFF10B5ONE"])
    sub = sub[sub["DOCUMENT_TYPE"].isin(FORMS)]
    own = _read_tsv(z, "REPORTINGOWNER.tsv", ["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNERNAME",
                                              "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE", "RPTOWNER_TXT"])
    own = own[own["ACCESSION_NUMBER"].isin(set(sub["ACCESSION_NUMBER"]))].copy()
    rel = own["RPTOWNER_RELATIONSHIP"].fillna("")
    own["is_director"] = rel.str.contains("Director", case=False)
    own["is_officer"] = rel.str.contains("Officer", case=False)
    own["is_ten_pct"] = rel.str.contains("TenPercent", case=False)
    own["is_other"] = rel.str.contains("Other", case=False)
    own["_order"] = range(len(own))
    own["_senior"] = (own.is_director | own.is_officer).astype(int)
    own["_cik"] = pd.to_numeric(own["RPTOWNERCIK"], errors="coerce").astype("Int64").astype(str)
    grp = own.groupby("ACCESSION_NUMBER", sort=False)
    meta = pd.DataFrame({"n_owners": grp.size(), "owner_ciks": grp["_cik"].agg(";".join)})
    prim = (own.sort_values(["ACCESSION_NUMBER", "_senior", "_order"], ascending=[True, False, True])
               .drop_duplicates("ACCESSION_NUMBER").set_index("ACCESSION_NUMBER"))
    tr = _read_tsv(z, "NONDERIV_TRANS.tsv", ["ACCESSION_NUMBER", "SECURITY_TITLE", "TRANS_DATE", "TRANS_CODE",
                                             "TRANS_SHARES", "TRANS_PRICEPERSHARE", "TRANS_PRICEPERSHARE_FN",
                                             "TRANS_ACQUIRED_DISP_CD", "SHRS_OWND_FOLWNG_TRANS",
                                             "DIRECT_INDIRECT_OWNERSHIP"])
    tr = tr[tr["ACCESSION_NUMBER"].isin(set(sub["ACCESSION_NUMBER"]))]
    s = sub.set_index("ACCESSION_NUMBER")
    a = tr["ACCESSION_NUMBER"]
    out = pd.DataFrame({
        "accession": a.values,
        "form": a.map(s["DOCUMENT_TYPE"]).values,
        "filing_date": _iso_s(a.map(s["FILING_DATE"])).values,
        "period": _iso_s(a.map(s["PERIOD_OF_REPORT"])).values,
        "issuer_cik": a.map(s["ISSUERCIK"]).values,
        "issuer_name": a.map(s["ISSUERNAME"]).values,
        "issuer_symbol": a.map(s["ISSUERTRADINGSYMBOL"]).values,
        "owner_cik": a.map(prim["RPTOWNERCIK"]).values,
        "owner_name": a.map(prim["RPTOWNERNAME"]).values,
        "n_owners": a.map(meta["n_owners"]).values,
        "owner_ciks": a.map(meta["owner_ciks"]).values,
        "is_director": a.map(prim["is_director"]).fillna(False).astype(bool).values,
        "is_officer": a.map(prim["is_officer"]).fillna(False).astype(bool).values,
        "is_ten_pct": a.map(prim["is_ten_pct"]).fillna(False).astype(bool).values,
        "is_other": a.map(prim["is_other"]).fillna(False).astype(bool).values,
        "officer_title": a.map(prim["RPTOWNER_TITLE"].fillna(prim["RPTOWNER_TXT"])).values,
        "aff10b5one": (a.map(s["AFF10B5ONE"]).map(lambda v: np.nan if pd.isna(v) else float(_flag(v)))
                       .values if "AFF10B5ONE" in s.columns else np.nan),
        "security_title": tr["SECURITY_TITLE"].values,
        "trans_date": _iso_s(tr["TRANS_DATE"]).values,
        "trans_code": tr["TRANS_CODE"].str.strip().values,
        "acq_disp": tr["TRANS_ACQUIRED_DISP_CD"].str.strip().values,
        "shares": _num_s(tr["TRANS_SHARES"]).values,
        "price": _num_s(tr["TRANS_PRICEPERSHARE"]).values,
        "shares_after": _num_s(tr["SHRS_OWND_FOLWNG_TRANS"]).values,
        "direct": tr["DIRECT_INDIRECT_OWNERSHIP"].str.strip().values,
        "price_fn": tr["TRANS_PRICEPERSHARE_FN"].notna().values if "TRANS_PRICEPERSHARE_FN" in tr else False,
    })
    return _finish(out, "dataset")


# ------------------------------------------------------------ one EDGAR filing
def _x(el, path: str) -> str | None:
    """Text of `path/value` (or `path`) under `el`; Form 4 wraps most values in <value>."""
    n = el.find(path + "/value") if el is not None else None
    if n is None and el is not None:
        n = el.find(path)
    return (n.text or "").strip() if n is not None and n.text is not None else None


def parse_form4_xml(xml: str, accession: str, filing_date: str) -> pd.DataFrame:
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml)
    form = (root.findtext("documentType") or "").strip()
    if form not in FORMS:
        return pd.DataFrame(columns=COLS)
    iss = root.find("issuer")
    owners = []
    for i, o in enumerate(root.findall("reportingOwner")):
        rel = o.find("reportingOwnerRelationship")
        g = (lambda t: _flag(rel.findtext(t)) if rel is not None else False)
        owners.append({"cik": (o.findtext("reportingOwnerId/rptOwnerCik") or "").strip(),
                       "name": (o.findtext("reportingOwnerId/rptOwnerName") or "").strip(),
                       "dir": g("isDirector"), "off": g("isOfficer"), "ten": g("isTenPercentOwner"),
                       "oth": g("isOther"),
                       "title": ((rel.findtext("officerTitle") if rel is not None else None)
                                 or (rel.findtext("otherText") if rel is not None else None)),
                       "order": i})
    if not owners:
        return pd.DataFrame(columns=COLS)
    prim = sorted(owners, key=lambda o: (-(o["dir"] or o["off"]), o["order"]))[0]
    ciks = ";".join(str(int(o["cik"])) for o in owners if o["cik"].isdigit())
    aff = root.findtext("aff10b5One")
    rows = []
    for t in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        price_el = t.find("transactionAmounts/transactionPricePerShare")
        rows.append({
            "accession": accession, "form": form, "filing_date": filing_date,
            "period": _iso(root.findtext("periodOfReport")),
            "issuer_cik": iss.findtext("issuerCik") if iss is not None else None,
            "issuer_name": iss.findtext("issuerName") if iss is not None else None,
            "issuer_symbol": (iss.findtext("issuerTradingSymbol") or "").strip() if iss is not None else None,
            "owner_cik": prim["cik"], "owner_name": prim["name"], "n_owners": len(owners), "owner_ciks": ciks,
            "is_director": prim["dir"], "is_officer": prim["off"], "is_ten_pct": prim["ten"],
            "is_other": prim["oth"], "officer_title": prim["title"],
            "aff10b5one": np.nan if aff is None or not aff.strip() else float(_flag(aff)),
            "security_title": _x(t, "securityTitle"),
            "trans_date": _iso(_x(t, "transactionDate")),
            "trans_code": (t.findtext("transactionCoding/transactionCode") or "").strip(),
            "acq_disp": _x(t, "transactionAmounts/transactionAcquiredDisposedCode"),
            "shares": _num(_x(t, "transactionAmounts/transactionShares")),
            "price": _num(_x(t, "transactionAmounts/transactionPricePerShare")),
            "shares_after": _num(_x(t, "postTransactionAmounts/sharesOwnedFollowingTransaction")),
            "direct": _x(t, "ownershipNature/directOrIndirectOwnership"),
            "price_fn": price_el is not None and price_el.find("footnoteId") is not None,
        })
    return _finish(pd.DataFrame(rows), "edgar")


# ============================================================ fetching
def _get(url: str) -> tuple[int, bytes]:
    import edgar                       # its pacing, user agent and retries
    st, body, _wire = edgar._get(url)
    return st, body


def dataset_quarters(start: str = FIRST_QUARTER, end: date | None = None) -> list[str]:
    end = end or date.today()
    y, q = int(start[:4]), int(start[-1])
    out = []
    while (y, q) <= (end.year, (end.month - 1) // 3 + 1):
        out.append(f"{y}q{q}")
        y, q = (y, q + 1) if q < 4 else (y + 1, 1)
    return out


def fetch_dataset(q: str) -> bytes | None:
    """The quarter's zip, or None if SEC has not posted it (yet)."""
    for seg in DS_SEGMENTS:
        st, body = _get(DS_URL.format(seg=seg, q=q))
        if st == 200 and body[:2] == b"PK":
            return body
    return None


def stored_dataset_quarters() -> list[str]:
    return sorted(p.stem for p in DS_DIR.glob("*.parquet")) if DS_DIR.exists() else []


def _label(what: str) -> str:
    import os
    return f"{os.path.basename(sys.argv[0] or 'python')}: insider {what}"


def backfill(start: str = FIRST_QUARTER, force: bool = False, verbose: bool = True,
             wait_s: float = 0.0) -> dict:
    """`_backfill` under the store's writer lock (store_lock.StoreBusy after `wait_s`)."""
    with store_lock.writer(ROOT, _label(f"backfill from {start}"), wait_s=wait_s, log=log):
        return _backfill(start, force, verbose)


def _backfill(start: str = FIRST_QUARTER, force: bool = False, verbose: bool = True) -> dict:
    DS_DIR.mkdir(parents=True, exist_ok=True)
    have = set(stored_dataset_quarters())
    done, missing, rows = [], [], 0
    t0 = time.time()
    for q in dataset_quarters(start):
        if q in have and not force:
            continue
        z = fetch_dataset(q)
        if z is None:
            missing.append(q)
            continue
        df = parse_dataset(z)
        tmp = DS_DIR / f"{q}.parquet.tmp"
        df.to_parquet(tmp, index=False)
        tmp.replace(DS_DIR / f"{q}.parquet")
        done.append(q)
        rows += len(df)
        if verbose:
            log(f"{q}: {len(df):,} trades ({(df.trans_code == 'P').sum():,} purchases), {time.time() - t0:.0f}s")
    return {"quarters": done, "missing": missing, "rows": rows, "secs": round(time.time() - t0, 1)}


# ------------------------------------------------------------ EDGAR, by day
def _ledger() -> pd.DataFrame:
    if LEDGER.exists():
        return pd.read_parquet(LEDGER)
    return pd.DataFrame(columns=["accession", "filing_date", "status", "rows", "error"])


def _quarter_of(d: str) -> str:
    return f"{d[:4]}q{(int(d[5:7]) - 1) // 3 + 1}"


def _first_uncovered_day() -> date:
    """The day after the last quarter SEC has posted a dataset for."""
    qs = stored_dataset_quarters()
    if not qs:
        return date.today() - timedelta(days=30)
    y, q = int(qs[-1][:4]), int(qs[-1][-1])
    y, q = (y, q + 1) if q < 4 else (y + 1, 1)
    return date(y, 3 * (q - 1) + 1, 1)


def _fetch_one(item: dict) -> tuple[dict, pd.DataFrame | None, str | None]:
    try:
        st, body = _get("https://www.sec.gov/Archives/" + item["path"])
        if st != 200:
            return item, None, f"HTTP {st}"
        t = body.decode("utf-8", "replace")
        m = re.search(r"<XML>\s*(.*?)\s*</XML>", t, re.S)
        if not m:
            return item, pd.DataFrame(columns=COLS), None
        return item, parse_form4_xml(m.group(1), item["accession"], item["filing_date"]), None
    except Exception as exc:                                     # noqa: BLE001
        return item, None, repr(exc)[:200]


def ingest_edgar(since: date | None = None, until: date | None = None, verbose: bool = True,
                 wait_s: float = 0.0) -> dict:
    """`_ingest_edgar` under the store's writer lock."""
    with store_lock.writer(ROOT, _label("EDGAR ingest"), wait_s=wait_s, log=log):
        return _ingest_edgar(since, until, verbose)


# 100 entries a page. `type=4` is a PREFIX match -- 424B2 prospectuses outnumber Form 4s --
# and each Form 4 is listed once per party, so the whole feed is read: it ended by itself at
# 37 pages (3,663 entries, 638 Form 4s, 17s) on 2026-10-28; 25 pages would have missed 23%.
FEED_PAGES = 60


def _feed_form4(max_pages: int = FEED_PAGES) -> list[dict]:
    """Form 4 / 4/A from EDGAR's latest-filings feed: the filings whose daily index is
    not out yet.

    SEC publishes day D's index at ~22:00 ET -- 07:00 here, two hours AFTER the 05:00 run
    -- so reading indexes alone, a Form 4 filed on Tuesday reached the store on Thursday
    (measured 2026-10-28: the 05:00 step found "0 new"; the feed listed 341 filed on 10-27). `edgar.current_feed` solves this for 10-Ks but keeps only those forms. Best
    effort: a page that fails ends the read, and anything missed is in the next index.
    """
    import edgar
    out: dict[str, dict] = {}
    for page in range(max_pages):
        try:
            st, body = _get(edgar.FEED_URL.format(form="4", start=page * 100))
            if st != 200:
                break
            n, items = _parse_form4_feed(body)
            for it in items:
                out.setdefault(it["accession"], it)
            if n < 100:
                break
        except Exception:                                        # noqa: BLE001
            break
    return list(out.values())


def _parse_form4_feed(body: bytes) -> tuple[int, list[dict]]:
    """(entries on the page, Form 4/4-A filings in it). Each filing is listed once per
    party -- issuer and every reporting owner -- and any party's CIK reaches the file."""
    import xml.etree.ElementTree as ET
    atom = "{http://www.w3.org/2005/Atom}"
    entries = list(ET.fromstring(body).iter(atom + "entry"))
    items = []
    for e in entries:
        cat = e.find(atom + "category")
        if cat is None or cat.get("term") not in FORMS:
            continue
        adsh = (e.findtext(atom + "id") or "").rsplit("=", 1)[-1].strip()
        m = re.search(r"Filed:\s*(?:</b>)?\s*(\d{4}-\d{2}-\d{2})", e.findtext(atom + "summary") or "")
        c = re.search(r"\((\d{4,10})\)\s*\((?:Issuer|Reporting|Filer|Subject)\)\s*$",
                      (e.findtext(atom + "title") or "").strip())
        if m and c and re.fullmatch(r"\d{10}-\d{2}-\d{6}", adsh):
            items.append({"accession": adsh, "filing_date": m.group(1),
                          "path": f"edgar/data/{int(c.group(1))}/{adsh}.txt"})
    return len(entries), items


def _ingest_edgar(since: date | None = None, until: date | None = None, verbose: bool = True) -> dict:
    """Form 4 / 4/A filed on [since, until] and not yet in the ledger -> trades_edgar."""
    import edgar
    since = since or _first_uncovered_day()
    until = until or edgar.today_et()
    led = _ledger()
    done = set(led.loc[led.status.isin(["ok", "empty"]), "accession"])
    todo, days, missing_days = [], 0, []
    d = since
    while d <= until:
        if d.weekday() < 5:
            url = edgar.DAILY_URL.format(y=d.year, q=(d.month - 1) // 3 + 1, ymd=d.strftime("%Y%m%d"))
            st, body = _get(url)
            if st == 200:
                days += 1
                for line in body.decode("latin-1", "replace").splitlines():
                    p = line.split("|")
                    if len(p) == 5 and p[2] in FORMS:
                        acc = p[4].rsplit("/", 1)[-1].replace(".txt", "")
                        if acc not in done:
                            todo.append({"accession": acc, "filing_date": d.isoformat(), "path": p[4]})
            elif st != 404:
                missing_days.append(d.isoformat())
        d += timedelta(days=1)
    # ...plus what the latest-filings feed lists that no index has yet (see _feed_form4).
    fed = [f for f in _feed_form4() if f["accession"] not in done and since.isoformat() <= f["filing_date"]]
    n_index = len({t["accession"] for t in todo})
    todo = list({t["accession"]: t for t in fed + todo}.values())   # an index row wins over a feed row
    if verbose:
        log(f"latest-filings feed: {len(fed)} Form 4 filing(s) not yet in the ledger, "
            f"{len(todo) - n_index} of them in no index yet")
    if verbose:
        log(f"EDGAR {since}..{until}: {days} index day(s), {len(todo):,} new Form 4 filing(s) to fetch")
    frames, ledger_rows, failed = [], [], 0
    t0 = time.time()
    with ThreadPoolExecutor(WORKERS) as ex:
        for i, (item, df, err) in enumerate(ex.map(_fetch_one, todo), 1):
            if err:
                failed += 1
                ledger_rows.append({**{k: item[k] for k in ("accession", "filing_date")},
                                    "status": "failed", "rows": 0, "error": err})
            else:
                frames.append(df)
                ledger_rows.append({**{k: item[k] for k in ("accession", "filing_date")},
                                    "status": "ok" if len(df) else "empty", "rows": len(df), "error": None})
            if verbose and i % 1000 == 0:
                log(f"  {i:,}/{len(todo):,} filings, {failed} failed, {time.time() - t0:.0f}s")
    new = pd.concat([f for f in frames if len(f)], ignore_index=True) if frames else pd.DataFrame(columns=COLS)
    ED_DIR.mkdir(parents=True, exist_ok=True)
    if len(new):
        for q, part in new.groupby(new["filing_date"].map(_quarter_of)):
            path = ED_DIR / f"{q}.parquet"
            allq = pd.concat([pd.read_parquet(path), part], ignore_index=True) if path.exists() else part
            allq = allq.drop_duplicates(["accession", "security_title", "trans_date", "trans_code", "shares",
                                         "price", "acq_disp", "shares_after"], keep="last")
            tmp = path.with_suffix(".parquet.tmp")
            allq.to_parquet(tmp, index=False)
            tmp.replace(path)
    if ledger_rows:
        led = pd.concat([led[~led.accession.isin({r["accession"] for r in ledger_rows})],
                         pd.DataFrame(ledger_rows)], ignore_index=True)
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        led.to_parquet(LEDGER, index=False)
    res = {"days": days, "filings": len(todo), "failed": failed, "rows": len(new), "missing_days": missing_days,
           "secs": round(time.time() - t0, 1)}
    if todo and failed / len(todo) > FAIL_CEILING:
        raise RuntimeError(f"EDGAR Form 4 fetch failed for {failed} of {len(todo)} "
                           f"({failed / len(todo):.0%}, ceiling {FAIL_CEILING:.0%})")
    return res


def update(verbose: bool = True, wait_s: float = 0.0) -> dict:
    """Nightly: post any newly published quarter, then EDGAR from the first uncovered day.
    ONE lock for both halves, so nothing writes between them."""
    with store_lock.writer(ROOT, _label("update"), wait_s=wait_s, log=log):
        bf = backfill(start=_quarter_of(_first_uncovered_day().isoformat()), verbose=verbose)
        ed = ingest_edgar(verbose=verbose)
    return {"new_quarters": bf["quarters"], **ed}


# ============================================================ reading
def _ticker_map() -> dict[int, list[str]]:
    import fundamentals as FD
    tm = FD.ticker_map()
    try:
        uni = set(pd.read_parquet(config.UNIVERSE_FILE)["ticker"].astype(str))
    except Exception:                                            # noqa: BLE001
        uni = set()
    out: dict[int, list[str]] = {}
    for t, c in zip(tm["ticker"].astype(str), tm["cik"].astype(int)):
        out.setdefault(c, []).append(t)
    # Universe tickers first, so a CIK with several symbols maps to the one we trade.
    return {c: sorted(v, key=lambda t: (t not in uni, t)) for c, v in out.items()}


_READ_NEEDS = ("accession", "filing_date", "src", "issuer_cik", "issuer_symbol")


def read(start: str | None = None, end: str | None = None, codes: tuple[str, ...] | None = None,
         columns: list[str] | None = None) -> pd.DataFrame:
    """Every stored trade filed in [start, end], one copy per accession, with `ticker`.

    `codes` keeps only those transaction codes ("P", "S"...), applied per quarter as it
    is read: the full store is 7.2 million rows, most of them grants and exercises.
    `columns` reads only those (plus the few this function needs itself) -- a peer-price
    read of every sale since 2016 is 900K rows, and most columns are text."""
    cols = None if columns is None else list(dict.fromkeys(list(columns) + list(_READ_NEEDS)))
    parts = []
    for d, src in ((DS_DIR, "dataset"), (ED_DIR, "edgar")):
        if not d.exists():
            continue
        for p in sorted(d.glob("*.parquet")):
            q = p.stem
            if start and q < _quarter_of(start):
                continue
            if end and q > _quarter_of(end):
                continue
            part = pd.read_parquet(p, columns=cols,
                                   filters=[("trans_code", "in", list(codes))] if codes else None)
            parts.append(part)
    if not parts:
        return pd.DataFrame(columns=(cols or COLS) + ["ticker"])
    df = pd.concat(parts, ignore_index=True)
    if start:
        df = df[df.filing_date >= start]
    if end:
        df = df[df.filing_date <= end]
    # The dataset supersedes EDGAR for the same filing.
    ds_acc = set(df.loc[df.src == "dataset", "accession"])
    df = df[(df.src == "dataset") | ~df.accession.isin(ds_acc)]
    tmap = _ticker_map()
    sym = df["issuer_symbol"].fillna("").str.upper().str.replace(".", "-", regex=False)

    def pick(c, s):
        opts = tmap.get(int(c), []) if pd.notna(c) else []
        return s if s in opts else (opts[0] if opts else None)
    df = df.assign(ticker=[pick(c, s) for c, s in zip(df.issuer_cik, sym)])
    return df.reset_index(drop=True)


def purchases(df: pd.DataFrame, include_entities: bool = False, include_ten_pct_only: bool = False,
              include_planned: bool = True, include_other: bool = False) -> pd.DataFrame:
    """Open-market purchases with the insider's own money: code P, acquired, price > 0.

    Grants and awards (A), option exercises (M), tax withholding (F), gifts (G) and the
    rest are not purchases. By default only DIRECTORS AND OFFICERS count -- the people
    who run the company, which is what "insider buying" means: a holder who is ONLY a
    10% owner, an "Other" relationship and an entity that is neither are left out --
    funds topping up, insurers buying a closed-end fund (found 2026-10-27: an insurance
    company's $2e14 "purchase" of DNP).
    """
    m = (df.trans_code == "P") & (df.acq_disp == "A") & (df.shares > 0) & (df.price > 0)
    if not include_ten_pct_only:
        m &= ~(df.is_ten_pct & ~df.is_director & ~df.is_officer)
    if not include_other:
        m &= df.is_director | df.is_officer | (df.is_ten_pct if include_ten_pct_only else False)
    if not include_entities:
        m &= ~(df.is_entity & ~df.is_director & ~df.is_officer)
    if not include_planned:
        m &= ~(df.aff10b5one == 1)
    return df[m]


PEER_DAYS = 15                    # +- calendar days for the peer price
PEER_BAND = (0.5, 2.0)            # a price this far from its peers is a typing error
MAX_VALUE = 5e8                   # no single filing's purchase passes $500M


def price_ok(rows: pd.DataFrame, peers: pd.DataFrame) -> pd.Series:
    """True where a trade's REPORTED price is believable.

    Not against our bars: those are split-adjusted, so a $50 purchase before a 2-for-1
    split sits beside a $25 bar (measured 2026-10-27: 14% of checked purchases fell
    outside the adjusted day range, 400 of 467 of them by a plain split ratio, the rest
    serial reverse-splitters). Against PEERS instead -- the median reported price of the
    same company's OTHER filings (P and S trades, any insider) within +-15 days, all in the
    same unadjusted terms: 0.5x..2x of it passes. Without 2+ peers, only the caps apply.
    Caps for everything: a filing worth over $500M, or a price over $10,000 (the
    "total typed into the price field" error; the few genuine four-figure-plus prices
    are kept when the filer's peers agree).
    """
    ratio = pd.Series(_peer_ratio(rows, peers), index=rows.index)
    filing_value = rows.groupby("accession").value.transform("sum")
    ok = ratio.isna() | ratio.between(*PEER_BAND)
    ok &= ~(filing_value > MAX_VALUE)
    ok &= ~((rows.price > 1e4) & ratio.isna())
    return ok


def _peer_ratio(rows: pd.DataFrame, peers: pd.DataFrame) -> np.ndarray:
    """Each row's price over the median of its company's OTHER filings' prices within
    +-PEER_DAYS (each filing one median price at its median trade date); NaN with
    fewer than two such filings. Dates are integer days and accessions integer codes,
    so the per-row work is two array slices -- the pandas-per-row version took 162s
    for the 828K purchases since 2006 (2026-10-27)."""
    pr = peers[(peers.price > 0) & peers.trans_date.notna()]
    pr = (pr.assign(d=pd.to_datetime(pr.trans_date, errors="coerce"))
            .groupby(["issuer_cik", "accession"], as_index=False).agg(d=("d", "median"), px=("price", "median"))
            .dropna(subset=["d"]))
    pr["d"] = pr.d.values.astype("datetime64[ns]").astype("int64") / 86_400e9      # days, fractional
    codes = pd.Index(pd.concat([pr.accession, rows.accession]).unique())
    pr["a"] = codes.get_indexer(pr.accession)
    pr = pr.sort_values(["issuer_cik", "d"], kind="mergesort")
    out = np.full(len(rows), np.nan)
    rd = pd.to_datetime(rows.trans_date, errors="coerce").values.astype("datetime64[ns]")
    ok_d = ~np.isnat(rd)
    rdays = np.where(ok_d, rd.astype("int64") / 86_400e9, np.nan)
    ra = codes.get_indexer(rows.accession)
    rpx = rows.price.to_numpy(dtype="float64", na_value=np.nan)
    rc = rows.issuer_cik.to_numpy()
    # Row positions per issuer, once.
    row_groups = pd.Series(np.arange(len(rows))).groupby(rc).indices
    for c, g in pr.groupby("issuer_cik", sort=False):
        idx = row_groups.get(c)
        if idx is None:
            continue
        dd, pp, aa = g.d.to_numpy(), g.px.to_numpy(), g.a.to_numpy()
        x = rdays[idx]
        good = np.isfinite(x) & (rpx[idx] > 0)
        if not good.any():
            continue
        idx, x = idx[good], x[good]
        lo = np.searchsorted(dd, x - PEER_DAYS, side="left")
        hi = np.searchsorted(dd, x + PEER_DAYS, side="right")
        for k in range(len(idx)):
            l, h = lo[k], hi[k]
            if h - l < 2:
                continue
            seg = pp[l:h][aa[l:h] != ra[idx[k]]]
            if len(seg) >= 2:
                out[idx[k]] = rpx[idx[k]] / np.median(seg)
    return out


# ============================================================ checks
def check(df: pd.DataFrame | None = None, bars_check: bool = True) -> dict:
    """Sanity counts. Nothing here is fixed silently: each is a count to read."""
    df = read(codes=("P", "S")) if df is None else df
    p = df[(df.trans_code == "P") & (df.acq_disp == "A")]
    out = {
        "rows": int(len(df)), "purchases": int(len(p)),
        "filed_before_trade": int((df.filing_date < df.trans_date).sum()),
        "late_over_30d": int(((pd.to_datetime(df.filing_date, errors="coerce")
                               - pd.to_datetime(df.trans_date, errors="coerce")).dt.days > 30).sum()),
        "purchase_no_price": int((p.price.isna() | (p.price <= 0)).sum()),
        "purchase_over_1b": int((p.value > 1e9).sum()),
        "unmapped_purchases": int(p.ticker.isna().sum()) if "ticker" in p else None,
        "duplicate_trades": int(df.duplicated(["accession", "security_title", "trans_date", "trans_code",
                                                "shares", "price", "acq_disp", "shares_after"]).sum()),
    }
    if bars_check and len(p):
        ok = price_ok(p, df[df.trans_code.isin(["P", "S"])] if "S" in set(df.trans_code) else p)
        out["price_unbelievable"] = int((~ok).sum())
    return out


# ============================================================ selftest
_SAMPLE = """<?xml version="1.0"?><ownershipDocument><schemaVersion>X0609</schemaVersion>
<documentType>4</documentType><periodOfReport>2026-10-23</periodOfReport>
<issuer><issuerCik>0001003078</issuerCik><issuerName>MSC INDUSTRIAL DIRECT CO INC</issuerName>
<issuerTradingSymbol>MSM</issuerTradingSymbol></issuer>
<reportingOwner><reportingOwnerId><rptOwnerCik>0002000001</rptOwnerCik><rptOwnerName>Example Family Trust</rptOwnerName></reportingOwnerId>
<reportingOwnerRelationship><isDirector>0</isDirector><isOfficer>0</isOfficer><isTenPercentOwner>1</isTenPercentOwner><isOther>0</isOther></reportingOwnerRelationship></reportingOwner>
<reportingOwner><reportingOwnerId><rptOwnerCik>0002000002</rptOwnerCik><rptOwnerName>Doe Jane A</rptOwnerName></reportingOwnerId>
<reportingOwnerRelationship><isDirector>1</isDirector><isOfficer>1</isOfficer><isTenPercentOwner>0</isTenPercentOwner><isOther>0</isOther><officerTitle>Chief Executive Officer</officerTitle></reportingOwnerRelationship></reportingOwner>
<aff10b5One>0</aff10b5One>
<nonDerivativeTable>
<nonDerivativeTransaction><securityTitle><value>Class A Common Stock</value></securityTitle><transactionDate><value>2026-10-23</value></transactionDate>
<transactionCoding><transactionFormType>4</transactionFormType><transactionCode>P</transactionCode><equitySwapInvolved>0</equitySwapInvolved></transactionCoding>
<transactionAmounts><transactionShares><value>370</value></transactionShares><transactionPricePerShare><value>115.645</value><footnoteId id="F1"/></transactionPricePerShare>
<transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
<postTransactionAmounts><sharesOwnedFollowingTransaction><value>1370</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
<ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature></nonDerivativeTransaction>
<nonDerivativeTransaction><securityTitle><value>Class A Common Stock</value></securityTitle><transactionDate><value>2026-10-21</value></transactionDate>
<transactionCoding><transactionFormType>4</transactionFormType><transactionCode>A</transactionCode><equitySwapInvolved>0</equitySwapInvolved></transactionCoding>
<transactionAmounts><transactionShares><value>500</value></transactionShares><transactionPricePerShare><value>0</value></transactionPricePerShare>
<transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
<postTransactionAmounts><sharesOwnedFollowingTransaction><value>1000</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
<ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature></nonDerivativeTransaction>
</nonDerivativeTable></ownershipDocument>"""


def selftest() -> int:
    df = parse_form4_xml(_SAMPLE, "0001003078-26-000112", "2026-10-23")
    assert len(df) == 2, df
    r = df.iloc[0]
    # The director/officer is the primary owner, not the trust listed first.
    assert r.owner_name == "Doe Jane A" and r.role == "CEO" and r.n_owners == 2, r
    assert r.owner_ciks == "2000001;2000002"
    assert r.trans_code == "P" and r.shares == 370 and abs(r.value - 370 * 115.645) < 1e-6 and r.price_fn
    assert r.aff10b5one == 0.0 and not r.is_entity
    assert df.iloc[1].trans_code == "A" and np.isnan(df.iloc[1].value), "a zero-price grant has no value"
    p = purchases(df.assign(ticker="MSM"))
    assert len(p) == 1, "the grant is not a purchase"
    # Latest-filings feed: one filing listed per party -> one item; other forms dropped.
    def _entry(title, form, adsh, filed="2026-10-27"):
        return (f"<entry><title>{title}</title><summary type=\"html\"> &lt;b&gt;Filed:&lt;/b&gt; {filed} "
                f"&lt;b&gt;AccNo:&lt;/b&gt; {adsh}</summary><category scheme=\"https://www.sec.gov/\" "
                f"label=\"form type\" term=\"{form}\"/><id>urn:tag:sec.gov,2008:accession-number={adsh}</id></entry>")
    atom = ('<?xml version="1.0" encoding="ISO-8859-1" ?><feed xmlns="http://www.w3.org/2005/Atom">'
            + _entry("4 - Doe Jane A (0002000001) (Reporting)", "4", "0001003078-26-000112")
            + _entry("4 - Example Corp (0001003078) (Issuer)", "4", "0001003078-26-000112")
            + _entry("4/A - Example Corp (0001003078) (Issuer)", "4/A", "0001003078-26-000113", "2026-10-26")
            + _entry("144 - Example Corp (0001003078) (Subject)", "144", "0001003078-26-000114")
            + _entry("4 - no cik here", "4", "0001003078-26-000115") + "</feed>").encode("latin-1")
    n, items = _parse_form4_feed(atom)
    assert n == 5 and [i["accession"] for i in items] == ["0001003078-26-000112"] * 2 + ["0001003078-26-000113"], items
    assert items[0] == {"accession": "0001003078-26-000112", "filing_date": "2026-10-27",
                        "path": "edgar/data/2000001/0001003078-26-000112.txt"}, items[0]
    assert items[2]["filing_date"] == "2026-10-26" and items[2]["path"].startswith("edgar/data/1003078/")
    # Roles and entities
    assert role_of(False, True, False, False, "SVP & Principal Financial Officer") == "CFO"
    assert role_of(True, False, False, False, "Chairman of the Board") == "Chair"
    assert role_of(False, False, True, False, None) == "10% owner"
    assert _ENTITY.search("Example Group LLC") and _ENTITY.search("XYZ Capital Management LP")
    assert not _ENTITY.search("Roe Richard") and not _ENTITY.search("Doe Jane A")
    # A 10%-owner-only entity's purchase is left out by default, kept on request.
    e = df.iloc[[0]].assign(owner_name="Big Fund LP", is_entity=True, is_director=False, is_officer=False,
                            is_ten_pct=True, ticker="MSM")
    assert len(purchases(e)) == 0 and len(purchases(e, True, True)) == 1
    # Price believability: a typo far from its peers fails, an in-line price passes, a
    # price with no peers passes unless it is the "total in the price field" kind.
    pk = pd.DataFrame({"issuer_cik": [1, 1, 1, 1, 2, 3], "accession": ["a", "b", "c", "d", "e", "f"],
                       "trans_date": ["2026-01-05"] * 6, "price": [10.0, 10.5, 9.8, 4000.0, 25.0, 50000.0],
                       "value": [1e4, 1e4, 1e4, 4e9, 1e4, 1e9]})
    ok = price_ok(pk, pk)
    assert list(ok) == [True, True, True, False, True, False], list(ok)
    assert _iso("31-JUL-2026") == "2026-07-31" and _iso("2026-07-31") == "2026-07-31"
    assert dataset_quarters("2025q3", date(2026, 2, 1)) == ["2025q3", "2025q4", "2026q1"]
    print("insider selftest OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--start", default=FIRST_QUARTER)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--since", default=None, help="EDGAR: first filing day (YYYY-MM-DD)")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    config.dirs()
    if a.selftest:
        return selftest()
    if a.backfill:
        print(backfill(a.start, a.force, wait_s=WAIT_CLI))
    if a.update:
        if a.since:
            print(ingest_edgar(since=date.fromisoformat(a.since), wait_s=WAIT_CLI))
        else:
            print(update(wait_s=WAIT_CLI))
    if a.check:
        print(check())
    return 0


if __name__ == "__main__":
    sys.exit(main())
