#!/usr/bin/env python
"""US House members' stock trades, from their Periodic Transaction Reports -- free.

WHERE IT COMES FROM. Under the STOCK Act a member reports each trade over $1,000 within
45 days of being notified of it (30 days after notification, 45 after the trade at
the latest). The House Clerk publishes a yearly index of every financial-disclosure
filing (https://disclosures-clerk.house.gov/public_disc/financial-pdfs/<YEAR>FD.zip)
and each Periodic Transaction Report (PTR) as a PDF
(.../public_disc/ptr-pdfs/<YEAR>/<DocID>.pdf).

WHAT IT CANNOT DO, said plainly:
  * THE SENATE IS NOT HERE. efdsearch.senate.gov answers an automated request with
    HTTP 403 (checked 2026-10-27 with an honest, declared user agent); getting past that
    would mean defeating its bot protection, which this project does not do.
  * PAPER FILINGS ARE NOT READ. A PTR filed on paper is a scanned image (DocIDs starting
    8 or 9, ~12% of 2026's); without OCR it has no text. They are counted ("scanned")
    and listed, never guessed at.
  * AMOUNTS ARE RANGES ("$1,001 - $15,000"), as the form reports them; `amount_lo`
    and `amount_hi` keep both ends, `amount_mid` is only a convenience.
USE. 5 U.S.C. 13107(c) forbids using these reports for a commercial purpose (other than
news media), to set a credit rating, or to solicit money. The data stays in data/
(never committed) and is for the owner's own research.

ONE ROW PER TRADE: doc_id, member, state_dst, filing_date (from the Clerk's index: when
the report became public -- the only date anyone else could act on), owner (self, SP
spouse, JT joint, DC dependent child), asset, ticker, asset_type ([ST] stock, [OP]
option, [EF] ETF, [CT] crypto, [GS] government...), ttype (P, S, S (partial), E),
trans_date, notif_date, amount_lo/hi, description (the filer's own words), option_kind
(call/put, from the description), kind (what the description says it was: an
automatic dividend reinvestment, a gift or donation, an option exercise... -- a
purchase only counts as a decision to buy when it was not one of these), status
(New/Amended).

Store: data/congress/index/<YEAR>FD.zip, data/congress/pdf/<YEAR>/<DocID>.pdf (kept, so
a parser fix re-reads without re-downloading), data/congress/trades.parquet,
data/congress/_ledger.parquet.

Run:   python congress.py --backfill [--start 2015]   every PTR since START
       python congress.py --update                    this year's new PTRs
       python congress.py --reparse                   re-read every stored PDF
       python congress.py --selftest
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime

import numpy as np
import pandas as pd

import config
import store_lock

# ONE WRITER AT A TIME (store_lock.py): a scheduled `congress` step and a manual
# --backfill once merged into the same trades.parquet at once, and the last writer won.
WAIT_NIGHTLY = 60            # the nightly step waits this long, then reports SKIPPED
WAIT_CLI = 4 * 3600          # a manual run waits for the nightly instead

ROOT = config.DATA / "congress"
IDX = ROOT / "index"
PDF = ROOT / "pdf"
TRADES = ROOT / "trades.parquet"
LEDGER = ROOT / "_ledger.parquet"
INDEX_URL = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{y}FD.zip"
PTR_URL = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{y}/{doc}.pdf"
GAP = 1.0                       # seconds between request STARTS, across all workers: about 1 a second
WORKERS = 4                     # the Clerk's server takes 4-6 s per PDF (measured), so a few at once
FAIL_CEILING = 0.25
FIRST_YEAR = 2016               # the bar store starts 2016-09; earlier trades could not be measured

COLS = ["doc_id", "year", "member", "last", "state_dst", "filing_date", "owner", "asset", "ticker",
        "asset_type", "ttype", "trans_date", "notif_date", "amount_lo", "amount_hi", "amount_mid",
        "description", "option_kind", "kind", "status"]

_last_req = [0.0]
import threading
_PACE = threading.Lock()


def log(m: str) -> None:
    print(f"  [congress] {m}", flush=True)


def _pace() -> None:
    """At most one request START per GAP seconds, however many workers ask."""
    with _PACE:
        wait = GAP - (time.monotonic() - _last_req[0])
        if wait > 0:
            time.sleep(wait)
        _last_req[0] = time.monotonic()


def _get(url: str) -> tuple[int, bytes]:
    import requests
    for attempt in range(3):
        _pace()
        try:
            r = requests.get(url, headers={"User-Agent": config.SEC_UA}, timeout=60)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            return r.status_code, r.content
        except Exception:                                        # noqa: BLE001
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))
    return 0, b""


# ============================================================ the index
def index(year: int, refresh: bool = False) -> pd.DataFrame:
    """Every PTR (FilingType P) the Clerk lists for `year`."""
    IDX.mkdir(parents=True, exist_ok=True)
    p = IDX / f"{year}FD.zip"
    if refresh or not p.exists():
        st, body = _get(INDEX_URL.format(y=year))
        if st != 200 or body[:2] != b"PK":
            raise RuntimeError(f"{year}FD.zip: HTTP {st}")
        p.write_bytes(body)
    z = zipfile.ZipFile(p)
    name = [n for n in z.namelist() if n.lower().endswith(".xml")][0]
    rows = []
    for m in ET.fromstring(z.read(name)).findall("Member"):
        if (m.findtext("FilingType") or "").strip() != "P":
            continue
        g = lambda t: (m.findtext(t) or "").strip()
        fd = datetime.strptime(g("FilingDate"), "%m/%d/%Y").strftime("%Y-%m-%d") if g("FilingDate") else None
        rows.append({"doc_id": g("DocID"), "year": year, "last": g("Last"),
                     "member": " ".join(x for x in (g("Prefix"), g("First"), g("Last"), g("Suffix")) if x),
                     "state_dst": g("StateDst"), "filing_date": fd})
    return pd.DataFrame(rows)


# ============================================================ one PDF
_HEADER = re.compile(r"^(ID Owner Asset Transaction|Type|Date Notification|Date|Amount Cap\.|Gains >|\$200\?|"
                     r"Filing ID #\d+)\s*$")
_CORE = re.compile(r"(?P<ttype>S \(partial\)|P|S|E)\s+(?P<tdate>\d{2}/\d{2}/\d{4})\s*(?P<ndate>\d{2}/\d{2}/\d{4})"
                   r"\s*(?P<amount>(?:Spouse/DC\s+)?Over\s+\$[\d,]+|\$[\d,]+\s*-\s*(?:\$[\d,]+)?|\$[\d,]+)")
_TICK = re.compile(r"\((?P<ticker>[A-Za-z0-9.\-:/ ]{1,20})\)\s*\[(?P<atype>[A-Z]{2})\]\s*$")
_ATYPE_ONLY = re.compile(r"\[(?P<atype>[A-Z]{2})\]\s*$")
_META = re.compile(r"^(F\s+S\s+:|D\s+:|S\s+O\s+:|C\s+:|L\s+:|I\s+V|I\s+P|\* For the complete|Yes\s+No|I CERTIFY|"
                   r"my knowledge|Digitally Signed)")
_OWNER = re.compile(r"^(?:\d{6,}\s*)?(SP|JT|DC)\s+(.*)$")
_KIND = [("dividend reinvestment", re.compile(r"reinvest", re.I)),
         ("gift or donation", re.compile(r"\b(gift|donat|donor[- ]advised|contribution of)\b", re.I)),
         ("option exercise", re.compile(r"\bexercis", re.I)),
         ("rebalancing", re.compile(r"\brebalanc", re.I)),
         ("transfer", re.compile(r"\btransfer(red)?\b", re.I))]


def _amount(s: str) -> tuple[float, float]:
    nums = [float(x.replace(",", "")) for x in re.findall(r"\$([\d,]+)", s)]
    if "Over" in s and nums:
        return nums[0] + 1, np.nan
    if len(nums) >= 2:
        return nums[0], nums[1]
    if len(nums) == 1:
        return nums[0], nums[0]
    return np.nan, np.nan


def _plausible(iso: str | None) -> str | None:
    """A trade date a filer could mean: 1995 .. next year. Filers mistype years (a
    "3031-04-30" stopped the first study run); an impossible date is blank, not kept."""
    if not isinstance(iso, str):
        return None
    return iso if "1995-01-01" <= iso <= f"{date.today().year + 1}-12-31" else None


def _mdY(s: str) -> str | None:
    try:
        return _plausible(datetime.strptime(s, "%m/%d/%Y").strftime("%Y-%m-%d"))
    except (TypeError, ValueError):
        return None


def parse_ptr_text(text: str) -> list[dict]:
    """The trades in one PTR's extracted text (see the module docstring for the layout)."""
    lines = [l.strip() for l in text.replace("\r", "").split("\n")]
    lines = [l for l in lines if l and not _HEADER.match(l)]
    out, buf, cur, last_meta = [], [], None, None
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _CORE.search(line)
        if m:
            amount = m.group("amount")
            if amount.rstrip().endswith("-") and i + 1 < len(lines) and lines[i + 1].startswith("$"):
                amount += " " + lines[i + 1]                   # the range wrapped onto the next line
                i += 1
            asset_text = " ".join(buf + [line[:m.start()].strip()]).strip()
            # A description that wrapped onto its own line sits in front of the next
            # asset: keep from the owner code if there is one.
            om = None
            for k, piece in enumerate(buf + [line[:m.start()].strip()]):
                o = _OWNER.match(piece)
                if o:
                    om = (k, o)
            owner = "self"
            if om:
                k, o = om
                rest = (buf + [line[:m.start()].strip()])[k:]
                owner = o.group(1)
                asset_text = " ".join([o.group(2)] + rest[1:]).strip()
            asset_text = re.sub(r"^\d{6,}\s*", "", asset_text)
            t = _TICK.search(asset_text)
            a = _ATYPE_ONLY.search(asset_text)
            lo, hi = _amount(amount)
            cur = {"owner": owner, "asset": asset_text,
                   "ticker": (t.group("ticker").split(":")[-1].strip().upper() if t else None),
                   "asset_type": t.group("atype") if t else (a.group("atype") if a else None),
                   "ttype": m.group("ttype"), "trans_date": _mdY(m.group("tdate")),
                   "notif_date": _mdY(m.group("ndate")), "amount_lo": lo, "amount_hi": hi,
                   "description": None, "status": None}
            out.append(cur)
            buf, last_meta = [], None
        elif _META.match(line):
            if cur is not None:
                if line.startswith("F") and ":" in line:
                    cur["status"] = line.split(":", 1)[1].strip() or None
                elif line.startswith("D") and ":" in line:
                    cur["description"] = line.split(":", 1)[1].strip()
                    last_meta = "D"
                    i += 1
                    continue
            last_meta = "M"
            buf = []
        elif last_meta == "D" and cur is not None and not _OWNER.match(line) and not _TICK.search(line) \
                and (i + 1 >= len(lines) or not _CORE.search(lines[i + 1])):
            # The description carried on to this line (it is not the start of an asset:
            # no owner code, no ticker, and the next line is not a trade line).
            cur["description"] = ((cur["description"] or "") + " " + line).strip()
        else:
            buf.append(line)
            last_meta = None if last_meta != "D" else "D"
        i += 1
    for r in out:
        d = r["description"] or ""
        r["option_kind"] = "call" if re.search(r"\bcall", d, re.I) else ("put" if re.search(r"\bput", d, re.I) else None)
        r["kind"] = next((k for k, rx in _KIND if rx.search(d)), "trade")
        r["amount_mid"] = (r["amount_lo"] + r["amount_hi"]) / 2 if np.isfinite(r["amount_hi"]) else r["amount_lo"]
    return out


# ------------------------------------------------------------ the older layout
# Reports filed up to (at least) 2017 extract as one run-on line, with spelled-out
# labels in broken case ("FILINg STATUS:", "SUBHoLDINg oF:", "DESCRIPTIoN:"), no
# asset-type codes, one-digit days ("03/7/2016") and the two dates apart. Record
# boundaries are not marked: a description runs straight into the next asset's name
# ("...Annuity Auto RebalanceAllianceBernstein..."), so the name is recovered from the
# first lower->upper join after the last label. The TICKER, which is what matters, sits
# in parentheses right before the trade type and is never ambiguous.
_OLD_LABEL = re.compile(r"(FILING\s+STATUS|SUBHOLDING\s+OF|DESCRIPTION|LOCATION|COMMENTS?)\s*:\s*", re.I)
_OLD_CORE = re.compile(r"(?P<ttype>S \(partial\)|P|S|E)\s*(?P<tdate>\d{1,2}/\d{1,2}/\d{4})\s+(?P<ndate>\d{1,2}/\d{1,2}/\d{4})"
                       r"\s*(?P<amount>(?:Spouse/DC\s+)?Over\s+\$[\d,]+|\$[\d,]+\s*-\s*\$[\d,]+|\$[\d,]+)")
_OLD_START = re.compile(r"notification\s*Date\s*amount", re.I)
_OLD_END = re.compile(r"aSSet\s+claSS\s+DetailS|initial\s+Public\s+offering|certification\s+anD\s+Signature|I CERTIFY", re.I)
_JOIN = re.compile(r"(?<=[a-z0-9.)\]])(?=[A-Z][a-z0-9&])")
# An owner code glued onto the next asset name ("...McExampleJTAnadarko", "sPNorthwest"):
# the surest record boundary there is, tried before the generic lower->upper join,
# which would otherwise split a surname like "McExample".
_OWNER_JOIN = re.compile(r"(?<=[a-z0-9.)\]])(?=(?:[Ss][Pp]|[Jj][Tt]|[Dd][Cc])[A-Z0-9])")
_OPTION = re.compile(r"\b(call|put)s?\b|\boption", re.I)


def _mdY1(s: str) -> str | None:
    try:
        return _plausible(datetime.strptime(s, "%m/%d/%Y").strftime("%Y-%m-%d"))
    except (TypeError, ValueError):
        return None


def parse_ptr_text_old(text: str) -> list[dict]:
    t = re.sub(r"Filing ID #\d+", " ", text.replace("\n", " "))
    s = _OLD_START.search(t)
    pos = s.end() if s else 0
    e = _OLD_END.search(t, pos)
    end = e.start() if e else len(t)
    cores = [m for m in _OLD_CORE.finditer(t, pos, end)]
    out = []
    for k, m in enumerate(cores):
        seg = t[(cores[k - 1].end() if k else pos):m.start()]
        labels = list(_OLD_LABEL.finditer(seg))
        if labels:
            # The previous record's labelled values, then this record's asset name.
            for a, b in zip(labels, labels[1:] + [None]):
                val = seg[a.end():(b.start() if b else len(seg))]
                if b is None:
                    oj = list(_OWNER_JOIN.finditer(val))
                    j = oj[-1] if oj else _JOIN.search(val)
                    asset = val[j.start():] if j else ""
                    val = val[:j.start()] if j else val
                key = a.group(1).upper().replace(" ", "")
                if out:
                    if key.startswith("FILING"):
                        out[-1]["status"] = (val.strip().split() or [None])[0]
                    elif key == "DESCRIPTION":
                        out[-1]["description"] = val.strip()
        else:
            asset = seg
        asset = asset.strip()
        owner = "self"
        o = re.match(r"^([Ss][Pp]|[Jj][Tt]|[Dd][Cc])(?=[A-Z0-9])", asset)
        if o:
            owner, asset = o.group(1).upper(), asset[2:].strip()
        tk = re.findall(r"\(([A-Z][A-Z0-9.\-]{0,6})\)", asset)
        lo, hi = _amount(m.group("amount"))
        out.append({"owner": owner, "asset": asset, "ticker": tk[-1] if tk else None,
                    "asset_type": None, "ttype": m.group("ttype"),
                    "trans_date": _mdY1(m.group("tdate")), "notif_date": _mdY1(m.group("ndate")),
                    "amount_lo": lo, "amount_hi": hi, "description": None, "status": None})
    tail = t[cores[-1].end():end] if cores else ""
    for a, b in zip(list(_OLD_LABEL.finditer(tail)), list(_OLD_LABEL.finditer(tail))[1:] + [None]):
        val = tail[a.end():(b.start() if b else len(tail))].strip()
        key = a.group(1).upper().replace(" ", "")
        if out and key.startswith("FILING"):
            out[-1]["status"] = (val.split() or [None])[0]
        elif out and key == "DESCRIPTION":
            out[-1]["description"] = val
    for r in out:
        d = r["description"] or ""
        # No asset-type codes in this layout: a ticker'd asset is a stock unless the
        # name or description says option.
        if r["ticker"]:
            r["asset_type"] = "OP" if _OPTION.search(r["asset"]) or _OPTION.search(d) else "ST"
        r["option_kind"] = ("call" if re.search(r"\bcall", r["asset"] + " " + d, re.I)
                            else ("put" if re.search(r"\bput", r["asset"] + " " + d, re.I) else None))
        r["kind"] = next((k for k, rx in _KIND if rx.search(d)), "trade")
        r["amount_mid"] = (r["amount_lo"] + r["amount_hi"]) / 2 if np.isfinite(r["amount_hi"]) else r["amount_lo"]
    return out


def parse_pdf(pdf: bytes) -> tuple[str, list[dict]]:
    """('ok' | 'empty' | 'scanned', trades). The layout is detected per report."""
    import pypdf
    rd = pypdf.PdfReader(io.BytesIO(pdf))
    text = "\n".join((p.extract_text() or "") for p in rd.pages)
    # Some PDFs' fonts extract to lone UTF-16 surrogates, which Parquet cannot store
    # (a 2020 report stopped the first backfill on '\udf4e'): drop them here, once.
    text = text.encode("utf-8", "ignore").decode("utf-8")
    if len(text.strip()) < 50:
        return "scanned", []
    old = bool(re.search(r"FILING\s+STATUS\s*:", text, re.I))
    trades = parse_ptr_text_old(text) if old else parse_ptr_text(text)
    return ("ok" if trades else "empty"), trades


# ============================================================ store
def _ledger() -> pd.DataFrame:
    if LEDGER.exists():
        return pd.read_parquet(LEDGER)
    return pd.DataFrame(columns=["doc_id", "year", "filing_date", "status", "trades", "error"])


def _save(df: pd.DataFrame, path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(path)


def _rows(meta: dict, trades: list[dict]) -> list[dict]:
    return [{**{k: meta[k] for k in ("doc_id", "year", "member", "last", "state_dst", "filing_date")}, **t}
            for t in trades]


def _download(item: tuple[int, str]) -> tuple[str, str | None]:
    """(doc_id, error or None); the PDF lands in data/congress/pdf/<year>/."""
    y, doc = item
    path = PDF / str(y) / f"{doc}.pdf"
    if path.exists():
        return doc, None
    try:
        st, body = _get(PTR_URL.format(y=y, doc=doc))
        if st != 200 or not body.startswith(b"%PDF"):
            return doc, f"HTTP {st}"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".pdf.tmp")
        tmp.write_bytes(body)
        tmp.replace(path)
        return doc, None
    except Exception as exc:                                     # noqa: BLE001
        return doc, repr(exc)[:200]


def _merge(new_rows: list[dict], led_rows: list[dict]) -> None:
    if new_rows:
        old = pd.read_parquet(TRADES) if TRADES.exists() else pd.DataFrame(columns=COLS)
        allt = pd.concat([old, pd.DataFrame(new_rows)], ignore_index=True)
        allt = allt.drop_duplicates(["doc_id", "owner", "asset", "ttype", "trans_date", "amount_lo",
                                     "amount_hi", "description"], keep="last")
        _save(allt[COLS], TRADES)
    if led_rows:
        led = _ledger()
        lr = pd.DataFrame(led_rows)
        led = pd.concat([led[~led.doc_id.astype(str).isin(set(lr.doc_id.astype(str)))], lr], ignore_index=True)
        _save(led, LEDGER)


def _label(what: str) -> str:
    return f"{os.path.basename(sys.argv[0] or 'python')}: congress {what}"


def ingest(years: list[int], refresh_index: bool = True, verbose: bool = True, wait_s: float = 0.0) -> dict:
    """`_ingest` under the store's writer lock (store_lock.StoreBusy after `wait_s`)."""
    with store_lock.writer(ROOT, _label(f"ingest {years[0]}..{years[-1]}" if years else "ingest"),
                           wait_s=wait_s, log=log):
        return _ingest(years, refresh_index, verbose)


def _ingest(years: list[int], refresh_index: bool = True, verbose: bool = True) -> dict:
    """Every PTR the Clerk lists for `years` that the ledger has not read. PDFs are
    fetched a few at a time (the server is slow, not busy), parsed locally, and the store
    is saved after each year, so an interrupted backfill loses at most one year's parse
    and no download."""
    from concurrent.futures import ThreadPoolExecutor
    led = _ledger()
    done = set(led.loc[led.status.isin(["ok", "empty", "scanned"]), "doc_id"].astype(str))
    tot = {"reports": 0, "downloaded": 0, "failed": 0, "trades": 0}
    t0 = time.time()
    for y in years:
        idx = index(y, refresh=refresh_index or y == date.today().year)
        todo = idx[~idx.doc_id.astype(str).isin(done)]
        if verbose:
            log(f"{y}: {len(idx)} PTR(s) listed, {len(todo)} to read")
        if todo.empty:
            continue
        need = [(y, str(d)) for d in todo.doc_id if not (PDF / str(y) / f"{d}.pdf").exists()]
        errs = {}
        with ThreadPoolExecutor(WORKERS) as ex:
            for k, (doc, err) in enumerate(ex.map(_download, need), 1):
                if err:
                    errs[doc] = err
                if verbose and k % 100 == 0:
                    log(f"  {y}: {k}/{len(need)} downloaded, {len(errs)} failed, {time.time() - t0:.0f}s")
        new_rows, led_rows = [], []
        for meta in todo.to_dict("records"):
            base = {k: meta[k] for k in ("doc_id", "year", "filing_date")}
            doc = str(meta["doc_id"])
            if doc in errs:
                led_rows.append({**base, "status": "failed", "trades": 0, "error": errs[doc]})
                continue
            try:
                status, trades = parse_pdf((PDF / str(y) / f"{doc}.pdf").read_bytes())
                new_rows += _rows(meta, trades)
                led_rows.append({**base, "status": status, "trades": len(trades), "error": None})
            except Exception as exc:                             # noqa: BLE001
                led_rows.append({**base, "status": "failed", "trades": 0, "error": repr(exc)[:200]})
        _merge(new_rows, led_rows)
        nf = sum(r["status"] == "failed" for r in led_rows)
        tot["reports"] += len(led_rows); tot["downloaded"] += len(need) - len(errs)
        tot["failed"] += nf; tot["trades"] += len(new_rows)
        if verbose:
            st = pd.Series([r["status"] for r in led_rows]).value_counts().to_dict()
            log(f"{y}: {len(new_rows):,} trades from {len(led_rows)} report(s) {st}, {time.time() - t0:.0f}s")
    tot["secs"] = round(time.time() - t0, 1)
    if tot["reports"] and tot["failed"] / tot["reports"] > FAIL_CEILING:
        raise RuntimeError(f"House PTRs failed for {tot['failed']} of {tot['reports']}")
    return tot


def reparse(verbose: bool = True, wait_s: float = 0.0) -> dict:
    """Re-read every stored PDF (after a parser change) -- no downloads. Under the lock."""
    with store_lock.writer(ROOT, _label("reparse"), wait_s=wait_s, log=log):
        return _reparse(verbose)


def _reparse(verbose: bool = True) -> dict:
    led = _ledger()
    rows, lr = [], []
    for y in sorted(led.year.astype(int).unique()):
        idx = index(y).set_index("doc_id")
        for p in sorted((PDF / str(y)).glob("*.pdf")):
            doc = p.stem
            if doc not in idx.index:
                continue
            meta = {"doc_id": doc, **idx.loc[doc].to_dict()}
            status, trades = parse_pdf(p.read_bytes())
            rows += _rows(meta, trades)
            lr.append({"doc_id": doc, "year": y, "filing_date": meta["filing_date"], "status": status,
                       "trades": len(trades), "error": None})
    df = pd.DataFrame(rows, columns=COLS).drop_duplicates(
        ["doc_id", "owner", "asset", "ttype", "trans_date", "amount_lo", "amount_hi", "description"], keep="last")
    _save(df, TRADES)
    keep = led[~led.doc_id.astype(str).isin({r["doc_id"] for r in lr})]
    _save(pd.concat([keep, pd.DataFrame(lr)], ignore_index=True), LEDGER)
    return {"reports": len(lr), "trades": len(df)}


def update(verbose: bool = True, wait_s: float = 0.0) -> dict:
    """Nightly: this year's index (and last year's in January), new PTRs only."""
    y = date.today().year
    years = [y - 1, y] if date.today().month == 1 else [y]
    with store_lock.writer(ROOT, _label("update"), wait_s=wait_s, log=log):
        return ingest(years, refresh_index=True, verbose=verbose)


def read() -> pd.DataFrame:
    """Every stored trade. Amended reports restate earlier ones: the latest filing of the
    same member, ticker, type, trade date and amount wins."""
    if not TRADES.exists():
        return pd.DataFrame(columns=COLS)
    df = pd.read_parquet(TRADES)
    for c in ("trans_date", "notif_date"):            # rows stored before _plausible existed
        df[c] = df[c].map(_plausible)
    df = (df.sort_values(["filing_date", "doc_id"])
            .drop_duplicates(["member", "owner", "ticker", "asset_type", "ttype", "trans_date", "amount_lo",
                              "amount_hi", "option_kind"], keep="last"))
    return df.reset_index(drop=True)


# ============================================================ selftest
_SAMPLE = """Gains >
$200?
SP Alphabet Inc. - Class A Common
Stock (GOOGL) [ST]
P 01/16/202601/16/2026$500,001 -
$1,000,000
F      S     : New
D          : Exercised 50 call options purchased 1/14/25 (5,000 shares) at a strike price of $150 with an expiration date of
1/16/26.
SP Amazon.com, Inc. - Common Stock
(AMZN) [OP]
P 12/30/202512/30/2025$100,001 -
$250,000
F      S     : New
D          : Purchased 20 call options with a strike price of $120 and an expiration date of 1/15/27.
Filing ID #20033725
ID Owner Asset Transaction
Type
Date Notification
Date
Amount Cap.
Gains >
$200?
Home Depot, Inc. (HD) [ST] P 09/17/202610/05/2026$1,001 - $15,000
F      S     : New
D          : Automatic reinvestment of dividends earned.
The Trade Desk, Inc. - Class A
Common Stock (TTD) [ST]
S (partial) 01/08/202602/01/2026$1,001 - $15,000
F      S     : New
S          O : Moran Wealth IRA
2000152815SP Bitcoin (CRYPTO:BTC) [CT] P 12/18/202512/18/2025 $50,001 -
$100,000
F      S     : Amended
Treasury Bill (3-Month, Matures
10/15/2026) [GS]
P 07/13/202607/13/2026Over $50,000,000
F      S     : New
"""


def selftest() -> int:
    r = parse_ptr_text(_SAMPLE)
    assert len(r) == 6, [x["ticker"] for x in r]
    g = r[0]
    assert (g["owner"], g["ticker"], g["asset_type"], g["ttype"]) == ("SP", "GOOGL", "ST", "P"), g
    assert (g["trans_date"], g["notif_date"], g["amount_lo"], g["amount_hi"]) == ("2026-01-16", "2026-01-16", 500001.0, 1000000.0)
    assert g["kind"] == "option exercise" and g["description"].endswith("1/16/26."), g["description"]
    a = r[1]
    assert a["ticker"] == "AMZN" and a["asset_type"] == "OP" and a["option_kind"] == "call" and a["owner"] == "SP"
    assert a["asset"].startswith("Amazon.com"), a["asset"]
    h = r[2]
    assert h["owner"] == "self" and h["ticker"] == "HD" and h["kind"] == "dividend reinvestment"
    assert h["trans_date"] == "2026-09-17" and h["notif_date"] == "2026-10-05"
    t = r[3]
    assert t["ttype"] == "S (partial)" and t["ticker"] == "TTD" and t["kind"] == "trade"
    b = r[4]
    assert b["ticker"] == "BTC" and b["asset_type"] == "CT" and b["owner"] == "SP" and b["status"] == "Amended"
    tb = r[5]
    assert tb["ticker"] is None and tb["asset_type"] == "GS" and tb["amount_lo"] == 50000001 and np.isnan(tb["amount_hi"])
    assert _amount("$1,001 - $15,000") == (1001.0, 15000.0)
    # The older run-on layout (2016-17), with an owner code glued to the asset after a
    # camel-case account name, a one-digit day, and a dividend-reinvestment description.
    old = ("TrANSAcTIONSIDOwnerAssetTransaction TypeDate NotificationDateAmount JTExample Corp (EXA)P 01/19/2016 "
           "02/1/2016 $1,001 - $15,000 FILINg STATUS: NewSUBHoLDINg oF: Jane Q McExample and John McExampleDCSample "
           "Industries, Inc. (SMPL)S 03/7/2016 03/7/2016 $15,001 - $50,000 FILINg STATUS: NewDESCRIPTIoN: Reinvestment "
           "of dividend distributionsPExample Utility Company(EXU)P 11/15/2016 11/15/2016 $1,001 - $15,000 "
           "FILINg STATUS: Amended initial Public offeringSn mlkj Yes")
    o = parse_ptr_text_old(old)
    assert [(x["owner"], x["ticker"], x["ttype"]) for x in o] == [("JT", "EXA", "P"), ("DC", "SMPL", "S"), ("SP", "EXU", "P")], o
    assert o[1]["trans_date"] == "2016-03-07" and o[1]["kind"] == "dividend reinvestment" and o[0]["asset_type"] == "ST"
    assert o[2]["status"] == "Amended" and o[2]["asset"].startswith("Example Utility"), o[2]
    print("congress selftest OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--start", type=int, default=FIRST_YEAR)
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--reparse", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    config.dirs()
    if a.selftest:
        return selftest()
    if a.backfill:
        print(ingest(list(range(a.start, date.today().year + 1)), wait_s=WAIT_CLI))
    if a.update:
        print(update(wait_s=WAIT_CLI))
    if a.reparse:
        print(reparse(wait_s=WAIT_CLI))
    return 0


if __name__ == "__main__":
    sys.exit(main())
