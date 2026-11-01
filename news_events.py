#!/usr/bin/env python
"""Stock news EVENTS: SEC EDGAR filings by type, press-release wires, FDA, and a
point-in-time earnings calendar -- each with the time it became public.

    python news_events.py --update              nightly: EDGAR since the watermark, wires, calendar
    python news_events.py --backfill            EDGAR history for every universe company (resumable)
    python news_events.py --stats               what is on disk
    python news_events.py --selftest

WHY EVENTS AND NOT MORE HEADLINES (measured 2026-11-01)
---------------------------------------------------------
The Benzinga headline store already holds ~1,580 articles a day, and its sentiment is
weak: IC +0.016 (t 3.29) at one day only, a reversal at the extremes. A TYPED event --
an earnings release, a merger agreement, a tender offer, an activist stake -- with the
second it became public is a different object, and each type can be measured on its
own before anything is scored. The user's other project measured 12 free MACRO
headline sources (FinancialJuice RSS first on 93% of stories, ~46 s after posting) and
found that free feeds arrive WITH or AFTER a surprise move, so speed is not the edge
for a daily screener: the event's type, direction and exact time are.

SOURCES, EACH CHECKED BEFORE USE
---------------------------------
1. SEC EDGAR (authoritative). data.sec.gov/submissions/CIK*.json carries every filing
   with `items` (8-K item codes: "2.02,9.01") and `acceptanceDateTime` to the second --
   Honeywell's earnings 8-K was accepted 2026-10-22 10:02:13Z, before the open. Filings
   ABOUT a company appear in its own file too (WBD: 90 x 425, SC TO-T, SC 14D9,
   DEFM14A, S-4, SCHEDULE 13D), so deal and activist events come with no extra search.
   Full history through the file's extra pages (Honeywell's reach 1994). Nightly: the
   daily index for settled days, the latest-filings feed for today (its index is
   published at 07:00 here, after the 05:00 run), then each company's file for items.
2. PR Newswire subject RSS (M&A; FDA approvals): HTTP 200 to a plain identifying
   User-Agent. 20 items span ~1 day (M&A) and ~1 week (FDA), so the 2-hourly runs see
   every item. Only these feeds' newest items exist: recorded from 2026-11-01 on.
   GlobeNewswire answers 403 to automated requests -- NOT bypassed. Business Wire's
   M&A feed was empty.
3. FDA press releases RSS: ~20 items over ~7 weeks; names drugs, rarely companies.
4. Finnhub earnings calendar (free key): upcoming dates with EPS/revenue estimates
   and bmo/amc. A response is CAPPED AT 1,500 ROWS (a 7-day window came back cut to its
   last days), so it is read one day at a time; it holds NO history (July 2026: 0
   rows), so it is stored as dated snapshots from 2026-11-01 -- a study can then use
   only what was announced by each date. Historical announcement dates come from the
   8-K Item 2.02 acceptances instead.

TIME OF AN EVENT: `public_session` is the session whose CLOSE is the first after the
event was public: accepted before 16:00 ET on a session day -> that day; otherwise the
next session. Entry for a study is that close -- the earliest a daily-close system can
act -- and the reaction day itself is measured separately.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

import calendar_us
import config
import store
import store_lock

ROOT = config.DATA / "news_events"
EDGAR_FILE = ROOT / "edgar.parquet"
WIRE_FILE = ROOT / "wires.parquet"
CAL_DIR = ROOT / "earnings_calendar"
STATE_FILE = ROOT / "_state.json"
BACKFILL_DONE = ROOT / "_backfill_done.json"
FIRST_DAY = "2016-01-01"
WAIT_NIGHTLY = 60
WORKERS = 6                       # share edgar's single pacer: never above its RATE
CAL_DAYS_AHEAD = 28
CAL_DAYS_BACK = 7                 # recent past: the calendar fills in actuals
UA_WIRE = "SupportBounceScreener research (personal, non-commercial)"

# Forms kept. Amendments are kept and flagged: a study drops them, a reader may not.
FORMS = {
    "8-K": "8-K", "8-K/A": "8-K",
    "425": "merger_comm",
    "SC TO-T": "tender_offer", "SC TO-T/A": "tender_offer",
    "SC 14D9": "tender_target", "SC 14D9/A": "tender_target",
    "DEFM14A": "merger_proxy", "PREM14A": "merger_proxy_prelim",
    "S-4": "stock_deal", "S-4/A": "stock_deal",
    "SC 13D": "activist_stake", "SC 13D/A": "activist_stake",
    "SCHEDULE 13D": "activist_stake", "SCHEDULE 13D/A": "activist_stake",
}
# 8-K items (SEC Form 8-K General Instructions B). 9.01 (exhibits) carries no event.
ITEMS = {
    "1.01": "agreement", "1.02": "agreement_end", "1.03": "bankruptcy",
    "1.04": "mine_safety", "1.05": "cyber_incident",
    "2.01": "acquisition_done", "2.02": "earnings", "2.03": "debt",
    "2.04": "debt_trigger", "2.05": "restructuring", "2.06": "impairment",
    "3.01": "delisting_notice", "3.02": "unregistered_equity", "3.03": "holder_rights",
    "4.01": "auditor_change", "4.02": "restatement",
    "5.01": "control_change", "5.02": "exec_change", "5.03": "bylaws",
    "5.07": "shareholder_vote", "5.08": "director_nomination",
    "7.01": "reg_fd", "8.01": "other_event",
}
EDGAR_COLS = ["accession", "cik", "ticker", "form", "amendment", "items", "filing_date",
              "accepted_utc", "public_session", "primary_doc", "desc", "first_seen_utc", "via"]
WIRE_COLS = ["uid", "source", "subject", "title", "url", "pub_utc", "first_seen_utc",
             "tickers", "map_how", "public_session"]

WIRES = {
    "prn_mna": ("PR Newswire", "mergers_acquisitions",
                "https://www.prnewswire.com/rss/financial-services-latest-news/"
                "acquisitions-mergers-and-takeovers-list.rss"),
    "prn_fda": ("PR Newswire", "fda_approval",
                "https://www.prnewswire.com/rss/health-latest-news/fda-approval-list.rss"),
    "fda_press": ("FDA", "fda_press",
                  "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/"
                  "press-releases/rss.xml"),
}
_TICKER_TAG = re.compile(r"\((?:NYSE|NASDAQ|Nasdaq|NYSE American|NYSE MKT|Cboe)[A-Za-z ]{0,8}:\s*"
                         r"([A-Z][A-Z0-9.\-]{0,6})\)")


def log(msg: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} | {msg}", flush=True)


def _label(what: str) -> str:
    import os
    return f"{os.path.basename(sys.argv[0] or 'python')}: news events {what}"


# ================================================================== time
_SESS: list[str] | None = None


def _sessions() -> list[str]:
    global _SESS
    if _SESS is None:
        _SESS = calendar_us.all_sessions()
    return _SESS


def public_session(ts_utc: pd.Series) -> pd.Series:
    """The session whose close is the first after each moment (UTC timestamps)."""
    s = np.asarray(_sessions())
    t = pd.to_datetime(ts_utc, utc=True, errors="coerce")
    et = t.dt.tz_convert("America/New_York")
    day = et.dt.strftime("%Y-%m-%d")
    late = (et.dt.hour * 60 + et.dt.minute) >= 16 * 60
    # first session >= day, or > day when the moment is at/after that day's close
    i = np.searchsorted(s, day.fillna("").to_numpy(), side="left")
    on_session = (i < len(s)) & (s[np.minimum(i, len(s) - 1)] == day.fillna("").to_numpy())
    i = np.where(on_session & late.fillna(False).to_numpy(), i + 1, i)
    out = np.where((i < len(s)) & t.notna().to_numpy(), s[np.minimum(i, len(s) - 1)], None)
    return pd.Series(out, index=ts_utc.index, dtype=object)


# ================================================================== universe
def cik_tickers() -> dict[int, str]:
    """CIK -> the universe ticker (active first). One ticker per company."""
    import fundamentals as FD
    tm = FD.ticker_map()
    uni = pd.read_parquet(config.UNIVERSE_FILE)
    rank = {t: (0 if s == "active" else 1) for t, s in zip(uni.ticker.astype(str), uni.status.astype(str))}
    m = tm[tm.ticker.astype(str).isin(rank)].copy()
    m["r"] = m.ticker.astype(str).map(rank)
    m = m.sort_values(["cik", "r", "ticker"])
    return dict(zip(m.cik.astype(int), m.ticker.astype(str)))


# ================================================================== EDGAR
def parse_submissions(block: dict, cik: int, since: str = FIRST_DAY,
                      accessions: set[str] | None = None) -> pd.DataFrame:
    """Rows of one submissions block (`recent` or an extra page) for the kept forms."""
    n = len(block.get("form", []))
    if not n:
        return pd.DataFrame(columns=EDGAR_COLS)
    df = pd.DataFrame({k: block.get(k, [None] * n) for k in
                       ("accessionNumber", "form", "items", "filingDate", "acceptanceDateTime",
                        "primaryDocument", "primaryDocDescription")})
    df = df[df.form.isin(FORMS) & (df.filingDate >= since)]
    if accessions is not None:
        df = df[df.accessionNumber.isin(accessions)]
    if df.empty:
        return pd.DataFrame(columns=EDGAR_COLS)
    out = pd.DataFrame({
        "accession": df.accessionNumber.astype(str), "cik": int(cik), "ticker": None,
        "form": df.form.astype(str), "amendment": df.form.str.endswith("/A"),
        "items": df["items"].fillna("").astype(str), "filing_date": df.filingDate.astype(str),
        "accepted_utc": pd.to_datetime(df.acceptanceDateTime, utc=True, errors="coerce", format="ISO8601"),
        "public_session": None, "primary_doc": df.primaryDocument.fillna("").astype(str),
        "desc": df.primaryDocDescription.fillna("").astype(str),
        "first_seen_utc": pd.NaT, "via": "submissions"})
    return out[EDGAR_COLS]


def fetch_company(cik: int, since: str = FIRST_DAY, accessions: set[str] | None = None,
                  recent_only: bool = False) -> pd.DataFrame:
    """One company's kept filings since `since`, from its submissions file (+ pages)."""
    import edgar
    st, body, _ = edgar._get(config.SEC_SUBMISSIONS_URL.format(cik=int(cik)))
    if st != 200:
        return pd.DataFrame(columns=EDGAR_COLS)
    js = json.loads(body)
    f = js.get("filings", {})
    parts = [parse_submissions(f.get("recent", {}), cik, since, accessions)]
    if not recent_only:
        for p in f.get("files", []) or []:
            if str(p.get("filingTo", "")) < since:
                continue
            st2, b2, _ = edgar._get("https://data.sec.gov/submissions/" + p["name"])
            if st2 == 200:
                parts.append(parse_submissions(json.loads(b2), cik, since, accessions))
    parts = [p for p in parts if len(p)]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=EDGAR_COLS)


def _read_edgar() -> pd.DataFrame:
    return pd.read_parquet(EDGAR_FILE) if EDGAR_FILE.exists() else pd.DataFrame(columns=EDGAR_COLS)


def _write(df: pd.DataFrame, path, key: list[str]) -> int:
    """Merge into `path` on `key` (newest wins); atomic. Returns rows stored."""
    ROOT.mkdir(parents=True, exist_ok=True)
    old = pd.read_parquet(path) if path.exists() else df.iloc[0:0]
    if len(old) and "first_seen_utc" in df.columns and "first_seen_utc" in old.columns:
        # keep the EARLIEST time we saw an event: a re-read must not move it
        fs = old.set_index(key)["first_seen_utc"]
        idx = pd.MultiIndex.from_frame(df[key]) if len(key) > 1 else df[key[0]]
        prev = fs.reindex(idx).to_numpy()
        df = df.copy()
        df["first_seen_utc"] = np.where(pd.notna(prev), prev, df["first_seen_utc"])
    out = pd.concat([old, df], ignore_index=True).drop_duplicates(key, keep="last")
    tmp = path.with_suffix(".parquet.tmp")
    out.to_parquet(tmp, index=False)
    store.atomic_replace(tmp, path)
    return len(out)


def _finish(df: pd.DataFrame, tickers: dict[int, str]) -> pd.DataFrame:
    df = df.copy()
    df["ticker"] = df.cik.map(tickers)
    df["public_session"] = public_session(df["accepted_utc"])
    df["first_seen_utc"] = pd.to_datetime(df["first_seen_utc"], utc=True, errors="coerce")
    return df[EDGAR_COLS]


def backfill(since: str = FIRST_DAY, limit: int = 0, verbose: bool = True) -> dict:
    """Every universe company's kept filings since `since`. Resumable: companies done
    are listed in BACKFILL_DONE, and the store is written every 300 companies."""
    with store_lock.writer(ROOT, _label("backfill"), log=log):
        tickers = cik_tickers()
        done = set(json.loads(BACKFILL_DONE.read_text())) if BACKFILL_DONE.exists() else set()
        todo = [c for c in sorted(tickers) if c not in done]
        if limit:
            todo = todo[:limit]
        t0, rows, buf, failed = time.time(), 0, [], []
        if verbose:
            log(f"backfill: {len(todo):,} companies to read ({len(done):,} done)")

        def one(c):
            return c, fetch_company(c, since)
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = [ex.submit(one, c) for c in todo]
            for k, fut in enumerate(as_completed(futs), 1):
                try:
                    c, df = fut.result()
                    buf.append(df)
                    done.add(c)
                except Exception as exc:                         # noqa: BLE001
                    failed.append(repr(exc)[:120])
                if k % 300 == 0 or k == len(futs):
                    new = [b for b in buf if len(b)]
                    if new:
                        rows = _write(_finish(pd.concat(new, ignore_index=True), tickers),
                                      EDGAR_FILE, ["accession", "cik"])
                    BACKFILL_DONE.write_text(json.dumps(sorted(done)))
                    buf = []
                    if verbose:
                        log(f"  {k:,}/{len(futs):,} companies, store {rows:,} rows, "
                            f"{len(failed)} failed, {time.time() - t0:.0f}s")
        return {"companies": len(todo), "failed": len(failed), "rows": rows,
                "secs": round(time.time() - t0, 1)}


def _index_day(d: date) -> tuple[int, list[tuple[int, str, str]]]:
    """(status, [(cik, form, accession)]) from one EDGAR daily master index."""
    import edgar
    st, body, _ = edgar._get(edgar.DAILY_URL.format(y=d.year, q=(d.month - 1) // 3 + 1,
                                                    ymd=d.strftime("%Y%m%d")))
    if st != 200:
        return st, []
    out = []
    for line in body.decode("latin-1", "replace").splitlines():
        p = line.split("|")
        if len(p) == 5 and p[2] in FORMS and p[0].isdigit():
            out.append((int(p[0]), p[2], p[4].rsplit("/", 1)[-1].replace(".txt", "")))
    return st, out


def parse_feed_page(body: bytes) -> tuple[int, list[tuple[int, str, str, str]]]:
    """(entries, [(cik, form, accession, role)]) from one latest-filings Atom page."""
    atom = "{http://www.w3.org/2005/Atom}"
    entries = list(ET.fromstring(body).iter(atom + "entry"))
    out = []
    for e in entries:
        cat = e.find(atom + "category")
        form = cat.get("term") if cat is not None else ""
        if form not in FORMS:
            continue
        adsh = (e.findtext(atom + "id") or "").rsplit("=", 1)[-1].strip()
        m = re.search(r"\((\d{4,10})\)\s*\((Issuer|Reporting|Filer|Subject|Filed by)\)\s*$",
                      (e.findtext(atom + "title") or "").strip())
        if m and re.fullmatch(r"\d{10}-\d{2}-\d{6}", adsh):
            out.append((int(m.group(1)), form, adsh, m.group(2)))
    return len(entries), out


def _feed_today(max_pages: int = 60) -> list[tuple[int, str, str]]:
    """Kept filings on EDGAR's latest-filings feed: what today's unpublished index
    will hold. `type=` is a PREFIX match (type=4 returned 424B2s), so each prefix is
    read to its end and filtered here."""
    import edgar
    out = {}
    for pref in ("8-K", "425", "SC TO-T", "SC 14D9", "DEFM14A", "PREM14A", "S-4", "SC 13D",
                 "SCHEDULE 13D"):
        for page in range(max_pages):
            try:
                st, body, _ = edgar._get(edgar.FEED_URL.format(
                    form=pref.replace(" ", "+"), start=page * 100))
                if st != 200:
                    break
                n, items = parse_feed_page(body)
                for cik, form, adsh, _role in items:
                    out[(cik, adsh)] = (cik, form, adsh)
                if n < 100:
                    break
            except Exception:                                    # noqa: BLE001
                break
    return list(out.values())


def _state() -> dict:
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}


def _save_state(**kv) -> None:
    s = _state() | kv
    ROOT.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(s, indent=1, default=str))


def update_edgar(verbose: bool = True, max_days: int = 15) -> dict:
    """EDGAR since the watermark: settled days from their daily index, today from the
    latest-filings feed, then each named company's file for items and acceptance.
    A day is settled only when every kept filing in its index resolved; anything not
    yet in a company's file (seconds after acceptance) is read again next run."""
    import edgar
    tickers = cik_tickers()
    st = _state()
    since = date.fromisoformat(st.get("edgar_through", (edgar.today_et() - timedelta(days=4)).isoformat()))
    d, settled, want, days_read = since + timedelta(days=1), since, {}, []
    today = edgar.today_et()
    while d <= today and len(days_read) < max_days:
        if d.weekday() < 5:
            code, rows = _index_day(d)
            if code == 200:
                days_read.append(d)
                for cik, form, adsh in rows:
                    if cik in tickers:
                        want[(cik, adsh)] = d
            elif code == 404 and d < today - timedelta(days=3):
                days_read.append(d)            # a holiday: nothing filed, nothing to wait for
            else:
                break                          # not published yet: the feed covers it
        d += timedelta(days=1)
    feed = [(c, f, a) for c, f, a in _feed_today() if c in tickers]
    for cik, _form, adsh in feed:
        want.setdefault((cik, adsh), None)
    have = set(zip(_read_edgar().cik.astype(int), _read_edgar().accession)) if EDGAR_FILE.exists() else set()
    todo = {k: v for k, v in want.items() if (k[0], k[1]) not in have}
    by_cik: dict[int, set[str]] = {}
    for cik, adsh in todo:
        by_cik.setdefault(cik, set()).add(adsh)
    got = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(fetch_company, c, FIRST_DAY, a, True): c for c, a in by_cik.items()}
        for fut in as_completed(futs):
            try:
                got.append(fut.result())
            except Exception:                                    # noqa: BLE001
                pass
    new = pd.concat([g for g in got if len(g)], ignore_index=True) if any(len(g) for g in got) \
        else pd.DataFrame(columns=EDGAR_COLS)
    if len(new):
        new = new.assign(first_seen_utc=pd.Timestamp.now(tz="UTC"), via="nightly")
        _write(_finish(new, tickers), EDGAR_FILE, ["accession", "cik"])
    resolved = set(zip(new.cik.astype(int), new.accession)) | have
    pending = {k: v for k, v in todo.items() if k not in resolved}
    for day in days_read:
        late = [k for k, v in pending.items() if v == day]
        if late and day >= today - timedelta(days=3):
            break
        settled = day
    _save_state(edgar_through=settled.isoformat(), edgar_run=datetime.now(timezone.utc).isoformat(
        timespec="seconds"))
    res = {"index_days": len(days_read), "feed_filings": len(feed), "new": int(len(new)),
           "pending": len(pending), "through": settled.isoformat()}
    if verbose:
        log(f"edgar: {res}")
    return res


# ================================================================== wires + FDA
def _rfc822(s: str) -> pd.Timestamp:
    s = (s or "").strip().replace(" EDT", " -0400").replace(" EST", " -0500")
    return pd.to_datetime(s, utc=True, errors="coerce")


def parse_rss(text: str, key: str, names: dict[str, str] | None = None) -> pd.DataFrame:
    """One feed's items. Tickers from '(NYSE: X)' tags, else a unique company-name
    match at the start of the title (`names`: first word -> ticker)."""
    src, subject, _url = WIRES[key]
    rows = []
    for it in re.findall(r"<item>(.*?)</item>", text, re.S):
        def tag(t):
            m = re.search(rf"<{t}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{t}>", it, re.S)
            return html.unescape(m.group(1).strip()) if m else ""
        title, link, desc = tag("title"), tag("link"), tag("description")
        tk = sorted(set(_TICKER_TAG.findall(title + " " + desc)))
        how = "tag" if tk else ""
        # A NAME match only where companies announce their OWN news (FDA approvals):
        # in the M&A feed "Falcon Capital takes a stake..." matched FBYD by first word.
        if not tk and names and subject == "fda_approval":
            w = re.match(r"([A-Za-z][A-Za-z0-9&.\-]{3,})", title)
            if w and w.group(1).upper() in names:
                tk, how = [names[w.group(1).upper()]], "name"
        rows.append({"uid": tag("guid") or link, "source": src, "subject": subject, "title": title,
                     "url": link, "pub_utc": _rfc822(tag("pubDate")), "first_seen_utc": pd.NaT,
                     "tickers": ",".join(tk), "map_how": how, "public_session": None})
    df = pd.DataFrame(rows, columns=WIRE_COLS)
    if len(df):
        df["public_session"] = public_session(df["pub_utc"])
    return df


def _name_index() -> dict[str, str]:
    """First significant word of each universe company name -> ticker, kept only
    where that word is unique (INOVIO -> INO; 'American' would be ambiguous)."""
    u = pd.read_parquet(config.UNIVERSE_FILE)
    u = u[u.status.astype(str) == "active"]
    first = u["name"].astype(str).str.extract(r"^([A-Za-z][A-Za-z0-9&.\-]{3,})")[0].str.upper()
    vc = first.value_counts()
    ok = first.isin(vc[vc == 1].index)
    return dict(zip(first[ok], u.ticker[ok].astype(str)))


def poll_wires(verbose: bool = True) -> dict:
    import requests
    names = _name_index()
    out, res = [], {}
    for key, (_src, _subj, url) in WIRES.items():
        try:
            r = requests.get(url, headers={"User-Agent": UA_WIRE}, timeout=30)
            if r.status_code != 200:
                res[key] = f"HTTP {r.status_code}"
                continue
            df = parse_rss(r.text, key, names)
            df["first_seen_utc"] = pd.Timestamp.now(tz="UTC")
            out.append(df)
            res[key] = len(df)
        except Exception as exc:                                 # noqa: BLE001
            res[key] = repr(exc)[:80]
    if out:
        n = _write(pd.concat(out, ignore_index=True), WIRE_FILE, ["uid"])
        res["stored"] = n
    if verbose:
        log(f"wires: {res}")
    return res


# ================================================================== earnings calendar
def snapshot_calendar(days_ahead: int = CAL_DAYS_AHEAD, days_back: int = CAL_DAYS_BACK,
                      verbose: bool = True) -> dict:
    """Finnhub's calendar, ONE DAY PER CALL (a response caps at 1,500 rows), stored as
    the snapshot of what was known today: CAL_DIR/<fetch date>.parquet."""
    import requests
    import providers
    key = providers._finnhub_key()
    if not key:
        return {"skipped": "no FINNHUB_KEY"}
    today = date.today()
    rows, calls, capped = [], 0, 0
    for k in range(-days_back, days_ahead + 1):
        d = (today + timedelta(days=k)).isoformat()
        for attempt in range(3):
            try:
                r = requests.get("https://finnhub.io/api/v1/calendar/earnings",
                                 params={"from": d, "to": d, "token": key}, timeout=20)
                calls += 1
                time.sleep(1.05)                          # 60 calls/min on the free key
                if r.status_code == 429:
                    time.sleep(20)
                    continue
                got = r.json().get("earningsCalendar", []) if r.ok else []
                capped += len(got) >= 1500
                rows += got
                break
            except Exception:                                    # noqa: BLE001
                time.sleep(2)
    if not rows:
        return {"calls": calls, "rows": 0}
    df = pd.DataFrame(rows)
    df["fetched_utc"] = pd.Timestamp.now(tz="UTC")
    CAL_DIR.mkdir(parents=True, exist_ok=True)
    p = CAL_DIR / f"{today.isoformat()}.parquet"
    tmp = p.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    store.atomic_replace(tmp, p)
    res = {"calls": calls, "rows": len(df), "capped_days": capped,
           "from": df.date.min(), "to": df.date.max()}
    if verbose:
        log(f"calendar: {res}")
    return res


def upcoming_earnings(asof: str | None = None) -> pd.DataFrame:
    """The latest snapshot taken on or before `asof`: what was known then."""
    files = sorted(CAL_DIR.glob("*.parquet")) if CAL_DIR.exists() else []
    if asof:
        files = [f for f in files if f.stem <= asof]
    return pd.read_parquet(files[-1]) if files else pd.DataFrame()


# ================================================================== page flags
# The three filing types the study measured as WARNINGS (2026-11-01, vs similar stocks
# and vs the stock's own non-event days), each shown for as long as its effect was
# measured: 8-K item -> (short tag, sessions shown, study type, horizons to quote).
WARN_ITEMS = {
    "3.02": ("sold unregistered shares", 60, "unregistered_equity", (20, 60)),
    "4.02": ("restated financials", 20, "restatement", (1, 5)),
    "1.02": ("ended a material agreement", 20, "agreement_end", (5, 20)),
}
WARN_SHORT = {"3.02": "share sale", "4.02": "restated", "1.02": "deal ended"}
EARN_SOON = 10                    # sessions: an earnings date this close is highlighted
CAL_STALE_DAYS = 7                # a snapshot older than this says nothing about "next"
STUDY_FILE = config.DATA / "_news_events_study_summary.json"
_FLAG_CACHE: dict = {}


def _study() -> dict:
    try:
        return json.loads(STUDY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def measured(event_type: str, horizons=(20,)) -> str:
    """The study's own numbers for one event type, as one plain sentence."""
    res = [r for r in _study().get("results", []) if r.get("type") == event_type]
    parts = []
    for h in horizons:
        r = next((x for x in res if x.get("h") == h), None)
        if not r or r.get("mean_pp") is None:
            continue
        own = (f"; vs the stock's own days {r['vs_own_pp']:+.2f}pp (t {r['vs_own_t']:+.1f})"
               if r.get("vs_own_pp") is not None else "")
        parts.append(f"{h} session(s): {r['mean_pp']:+.2f}pp vs similar stocks (t {r['t']:+.1f}){own}")
    if not parts:
        return "not measured"
    n = res[0].get("episodes")
    return f"measured since 2016 on {n:,} filings -- " + "; ".join(parts)


def flag_help() -> dict[str, str]:
    """One line per flag for page legends, quoting the measured result."""
    out = {}
    for item, (tag, keep, et, hs) in WARN_ITEMS.items():
        out[item] = f"8-K Item {item}, {tag} (shown {keep} sessions): {measured(et, hs)}"
    beat = next((r for r in _study().get("results", []) if r.get("type") == "earnings_beat"), {})
    miss = next((r for r in _study().get("results", []) if r.get("type") == "earnings_miss"), {})
    out["earnings"] = (
        "next earnings date from the latest calendar snapshot taken before the next session (Finnhub); "
        f"highlighted within {EARN_SOON} sessions. Measured: the move is the reaction day "
        f"(beat {beat.get('reaction_pp', float('nan')):+.2f}pp, miss {miss.get('reaction_pp', float('nan')):+.2f}pp "
        "vs similar stocks) and nothing tradable follows the first close")
    return out


def _sec_url(cik: int, accession: str) -> str:
    return (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{accession.replace('-', '')}/{accession}-index.htm")


def page_flags(asof: str) -> dict[str, dict]:
    """ticker -> {"warn": [[short, tag, session, url, help], ...], "earn": [date, hour, sessions] | None}.

    POINT IN TIME: a warning needs its filing public by `asof`'s close and inside its
    window; the earnings date comes from the newest calendar snapshot taken no later
    than the next session's date, i.e. before it opened (none before 2026-11-01, when
    snapshots began). Any
    failure costs the flags, never the page."""
    if asof in _FLAG_CACHE:
        return _FLAG_CACHE[asof]
    out: dict[str, dict] = {}
    sess = _sessions()
    pos = {s: i for i, s in enumerate(sess)}
    ia = pos.get(asof)
    helps = flag_help()
    try:
        e = _read_edgar()
        e = e[(e.form == "8-K") & e.ticker.notna() & e.public_session.notna()]
        e = e[e.public_session.astype(str) <= asof]
        for item, (tag, keep, _et, _hs) in WARN_ITEMS.items():
            lo = sess[max(0, ia - keep + 1)] if ia is not None else asof
            hit = e[(e.public_session.astype(str) >= lo)
                    & e["items"].astype(str).str.split(",").apply(lambda x, it=item: it in x)]
            for r in hit.sort_values("public_session").itertuples(index=False):
                out.setdefault(r.ticker, {"warn": [], "earn": None})["warn"].append(
                    [WARN_SHORT[item], tag, str(r.public_session), _sec_url(r.cik, r.accession), helps[item]])
    except Exception as exc:                                     # noqa: BLE001
        log(f"warning flags unavailable: {exc!r}"[:160])
    try:
        # known before the NEXT session opened (a Friday page may use the weekend's)
        nxt = (sess[ia + 1] if ia is not None and ia + 1 < len(sess)
               else (pd.Timestamp(asof) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"))
        files = [f for f in sorted(CAL_DIR.glob("*.parquet")) if f.stem <= nxt] if CAL_DIR.exists() else []
        if files and ia is not None and (pd.Timestamp(nxt) - pd.Timestamp(files[-1].stem)).days <= CAL_STALE_DAYS:
            c = pd.read_parquet(files[-1], columns=["symbol", "date", "hour"])
            c = c[(c.date > asof) | ((c.date == asof) & (c.hour == "amc"))].sort_values("date")
            c = c.drop_duplicates("symbol")
            s_arr = np.asarray(sess)
            for sym, d, hr in zip(c.symbol.astype(str), c.date.astype(str), c.hour.fillna("").astype(str)):
                j = int(np.searchsorted(s_arr, d, side="left"))     # the report day's session
                if hr == "amc" and j < len(sess) and sess[j] == d:
                    j += 1                                           # after the close: next session reacts
                if j >= len(sess):
                    continue
                out.setdefault(sym, {"warn": [], "earn": None})["earn"] = [d, hr, j - ia]
    except Exception as exc:                                     # noqa: BLE001
        log(f"earnings dates unavailable: {exc!r}"[:160])
    _FLAG_CACHE[asof] = out
    return out


# ================================================================== nightly
def update(verbose: bool = True, wait_s: float = 0.0) -> dict:
    with store_lock.writer(ROOT, _label("update"), wait_s=wait_s, log=log):
        ed = update_edgar(verbose)
        wi = poll_wires(verbose)
        st = _state()
        cal = {}
        if st.get("calendar_day") != date.today().isoformat():   # once a day is enough
            cal = snapshot_calendar(verbose=verbose)
            if cal.get("rows"):
                _save_state(calendar_day=date.today().isoformat())
    return {"edgar": ed, "wires": wi, "calendar": cal}


def read(start: str | None = None, end: str | None = None, amendments: bool = False) -> pd.DataFrame:
    """EDGAR events with ticker and public_session, one row per filing x company."""
    df = _read_edgar()
    if not amendments:
        df = df[~df.amendment.astype(bool)]
    if start:
        df = df[df.public_session.astype(str) >= start]
    if end:
        df = df[df.public_session.astype(str) <= end]
    df = df.copy()
    df["event_class"] = df.form.map(FORMS)
    return df


def explode_items(df: pd.DataFrame) -> pd.DataFrame:
    """One row per (8-K, item) with the item's event type; other forms keep their class."""
    k = df[df.event_class == "8-K"].copy()
    k["item"] = k["items"].str.split(",")
    k = k.explode("item")
    k["item"] = k["item"].str.strip()
    k = k[k.item.isin(ITEMS)]
    k["event_type"] = k.item.map(ITEMS)
    o = df[df.event_class != "8-K"].copy()
    o["item"] = ""
    o["event_type"] = o.event_class
    return pd.concat([k, o], ignore_index=True)


def stats() -> None:
    df = _read_edgar()
    print(f"edgar: {len(df):,} filings, {df.cik.nunique():,} companies, "
          f"{df.ticker.notna().sum():,} mapped; accepted {df.accepted_utc.min()} .. {df.accepted_utc.max()}")
    if len(df):
        y = pd.to_datetime(df.accepted_utc).dt.year.value_counts().sort_index()
        print("by year:", y.to_dict())
        print("by form:", df.form.value_counts().head(16).to_dict())
    if WIRE_FILE.exists():
        w = pd.read_parquet(WIRE_FILE)
        print(f"wires: {len(w):,} items; mapped {(w.tickers != '').sum():,}; by source "
              f"{w.subject.value_counts().to_dict()}")
    files = sorted(CAL_DIR.glob("*.parquet")) if CAL_DIR.exists() else []
    print(f"calendar snapshots: {len(files)}" + (f", latest {files[-1].stem}" if files else ""))
    print("state:", _state())


# ================================================================== selftest
def selftest() -> int:
    # public_session: pre-market and intraday -> that day; after the close and on a
    # weekend -> the next session (2026-10-22 Thu, 10-23 Fri, 10-26 Mon)
    ts = pd.Series(pd.to_datetime(["2026-10-22 10:02:13Z", "2026-10-22 17:30:00Z",
                                   "2026-10-22 20:05:00Z", "2026-10-24 15:00:00Z", None], utc=True))
    assert list(public_session(ts)) == ["2026-10-22", "2026-10-22", "2026-10-23", "2026-10-26", None], \
        list(public_session(ts))
    blk = {"accessionNumber": ["0000773840-26-000090", "0000773840-26-000091", "0000773840-26-000092"],
           "form": ["8-K", "10-Q", "SCHEDULE 13D/A"], "items": ["2.02,9.01", "", ""],
           "filingDate": ["2026-10-22", "2026-10-22", "2015-01-02"],
           "acceptanceDateTime": ["2026-10-22T10:02:13.000Z"] * 3,
           "primaryDocument": ["a.htm"] * 3, "primaryDocDescription": ["8-K"] * 3}
    p = parse_submissions(blk, 773840)
    assert list(p.accession) == ["0000773840-26-000090"], "10-Q dropped, pre-2016 dropped"
    assert parse_submissions(blk, 773840, accessions={"x"}).empty
    ev = explode_items(p.assign(event_class="8-K"))
    assert list(ev.event_type) == ["earnings"], "9.01 carries no event"
    atom = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>8-K - Example Corp (0001234567) '
            '(Filer)</title><category term="8-K"/><id>urn:tag:sec.gov,2008:accession-number='
            '0001234567-26-000001</id></entry><entry><title>424B2 - Bank (0000000001) (Filer)</title>'
            '<category term="424B2"/><id>x=0000000001-26-000001</id></entry></feed>').encode()
    n, items = parse_feed_page(atom)
    assert n == 2 and items == [(1234567, "8-K", "0001234567-26-000001", "Filer")], items
    rss = ("<rss><channel><item><title>Example Therapeutics Announces FDA Approval</title>"
           "<link>https://x/1</link><guid>g1</guid><pubDate>Fri, 30 Oct 2026 22:01:00 +0000</pubDate>"
           "<description><![CDATA[BOSTON (NASDAQ: EXTX) -- today]]></description></item>"
           "<item><title>Examplo Bio Receives Approval</title><link>https://x/2</link><guid>g2</guid>"
           "<pubDate>Thu, 22 Oct 2026 12:58:19 EDT</pubDate><description>no tag</description></item>"
           "</channel></rss>")
    w = parse_rss(rss, "prn_fda", {"EXAMPLO": "EXBO"})
    assert list(w.tickers) == ["EXTX", "EXBO"] and list(w.map_how) == ["tag", "name"], w
    assert parse_rss(rss, "prn_mna", {"EXAMPLO": "EXBO"}).tickers.tolist() == ["EXTX", ""], \
        "no name guessing in the M&A feed"
    assert list(w.public_session) == ["2026-11-02", "2026-10-22"], list(w.public_session)
    print("news_events selftest OK (session timing, submissions, items, feed, wires)")
    return 0


def main() -> int:
    config.safe_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--since", default=FIRST_DAY)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.backfill:
        print(backfill(a.since, a.limit))
    elif a.update:
        print(update())
    elif a.stats:
        stats()
    else:
        ap.error("pass --update, --backfill, --stats or --selftest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
