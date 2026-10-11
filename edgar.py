"""
EDGAR per-filing ingest: a new 10-Q/10-K/20-F/40-F reaches the fact store the
night after it is filed, instead of waiting months for SEC's quarterly bulk set.

    python edgar.py                       the nightly pass (what `sec_edgar` runs)
    python edgar.py --since 2026-07-28    backfill every filing since a date
    python edgar.py --tickers KO,AAPL     per-name, via the submissions API
    python edgar.py --diff ADSH [ADSH..]  parse filings the bulk set already has
                                          and report every field that differs
    python edgar.py --dry-run             discover only; fetch no filing
    python edgar.py --stats
    python edgar.py --selftest

THE PROBLEM, MEASURED 2026-10-11
--------------------------------
SEC's companyfacts API -- what the weekly `sec_gap` step refreshes from -- does
not serve some companies' newest filings at all. KO's 10-Q filed 2026-07-29
(0001628280-26-050503) was STILL absent from companyfacts on 10-11; it reached
the store only through the 2026q3 bulk set, published 10-10. 309 of 2,834
scored names (11%) got their newest period only from that set, a median 72 days
after filing. Q3 10-Qs start mid-October, and without this they wait for the
2026q4 bulk set in early January.

THE SOURCE: EDGAR ITSELF, ONE FILING AT A TIME
----------------------------------------------
    discover  the EDGAR daily index (`master.YYYYMMDD.idx`, ~95 KB gzip, every
              filing accepted that day; SEC publishes it ~22:00 ET), plus the
              "latest filings" Atom feed for the days whose index is not out
              yet. Filtered to the bulk set's own form rule (10-K, 10-Q, 20-F,
              40-F and their /A, KT, QT variants) and to CIKs the ticker map
              knows. The submissions API is the per-name path (`--tickers`).
    fetch     per filing: the folder's index.json, FilingSummary.xml, the
              presentation linkbase and the extracted XBRL instance
              (`*_htm.xml`), all gzip on the wire -- KO's 2.9 MB instance is
              123 KB.
    parse     into the bulk SCHEMA through `fundamentals._typed`, matching the
              Financial Statement Data Sets' own semantics (below).
    store     `data/fundamentals_edgar/YYYYqQ.parquet`, partitioned by the
              quarter the (rounded) period ends in, like `fundamentals_cf`.
    dedupe    `fundamentals.read` drops any edgar row whose ACCESSION is in the
              bulk or companyfacts store, so one filing is never counted twice,
              and the quarterly bulk set supersedes this store as it lands.

MATCHING THE BULK SET, AND WHY EACH RULE EXISTS
-----------------------------------------------
Diffed against the bulk rows for the same accession before anything was stored
(see `diff` and NEXT_SESSION.md, 2026-10-11 EDGAR section). Every rule below was
forced by a difference the diff found, or confirmed by its absence:

  ddate   the period end ROUNDED TO THE NEAREST MONTH END. KO's quarter ended
          2026-07-03 and the bulk set says 2026-06-30. Mixing exact and rounded
          dates for one filer would put two "periods" a few days apart into
          every TTM window.
  qtrs    the duration in quarters, rounded to the nearest whole number; 0 for
          an instant. Long durations (5, 10, 46 quarters) are kept, as the bulk
          set keeps them -- and so is a stub under half a quarter (a SPAC's
          28-day inception period), which rounds to 0 there too.
  uom     the unit's NUMERATOR only: EPS is "USD", not "USD/shares", exactly
          as the bulk set stores it.
  value   as filed, at most four decimals, -0 stored as 0. One fact reported
          twice at different precisions keeps the MORE PRECISE copy (MUFG's
          share count: 11,980,601,000 beats 11,981,000,000).
  facts   NON-DIMENSIONAL contexts only (the bulk ingest keeps rows whose
          `segments` and `coreg` are both null; a co-registrant is a
          dimension in XBRL), and ONLY CONCEPTS PRESENTED ON A FACE STATEMENT.
          The data sets are "Financial Statement" data: KO's instance carries
          InterestExpense in a segment note and nowhere on its statements, and
          the bulk set does not have it. Without this filter the parser adds
          rows the bulk set will never have, and they vanish when it lands.
          A dei (cover-page) fact counts only on a date some statement fact
          also uses: BBRY's share count dated on its cover is not in the bulk
          set, SCI's dated on its balance sheet is.
  cik     the entity the instance itself names, not the index line -- a
          combined filing is listed once under every co-registrant's CIK.
  sic     not in the filing; taken from the newest bulk row for that CIK, else
          0 (as companyfacts rows already are).

MEASURED AGREEMENT, 2026-10-11: 261 distinct filings already in the 2026q3 bulk
set (171 10-Q, 19 10-K, 20 20-F, 4 40-F, 2 10-KT, 45 amendments; banks, 52/53-
week filers, IFRS in JPY/CAD), 20,272 bulk rows: every one matched on every
field. ONE KNOWN RESIDUAL: 2 rows the bulk set lacks (FPS's share counts for a
145-day post-IPO period), which SEC's renderer suppresses as a column -- a rule
not reproduced here, because guessing its threshold would be tuning.
"""

from __future__ import annotations

import argparse
import calendar
import gzip
import io
import json
import math
import sys
import threading
import time
import xml.etree.ElementTree as ET
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

import pandas as pd
import requests

import config
import fundamentals
import store

ARCHIVES = "https://www.sec.gov/Archives/edgar"
DAILY_URL = ARCHIVES + "/daily-index/{y}/QTR{q}/master.{ymd}.idx"
FOLDER_URL = ARCHIVES + "/data/{cik}/{nodash}/"
FEED_URL = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent"
            "&type={form}&company=&dateb=&owner=include&start={start}"
            "&count=100&output=atom")

# The bulk ingest's own form rule (`fundamentals.fetch_quarter`), so both
# sources accept exactly the same filings: 10-K, 10-K/A, 10-KT, 10-Q, 10-Q/A,
# 20-F, 20-F/A, 40-F, 40-F/A.
FORMS = ("10-K", "10-Q", "20-F", "40-F")

# SEC's fair-access ceiling is 10 requests/second per client. Six workers share
# ONE pacer, so the aggregate never exceeds RATE however fast the responses
# come back; most of a worker's time is spent waiting on a download.
WORKERS = 6
RATE = 5.0
ATTEMPTS = 3

# How far back the nightly pass looks when it has no record of an earlier one,
# and the most it will ever look back on its own. A laptop shut for a month
# resumes from the newest day it had settled, up to this ceiling; older gaps
# are what the bulk set and `--since` are for.
LOOKBACK_DAYS = 10
MAX_LOOKBACK_DAYS = 120

# Filings fetched between writes. A write is a partition rewrite, so this keeps
# that cost low while making an interrupted run lose at most one chunk.
CHUNK = 120

LEDGER_COLS = ["adsh", "cik", "form", "filed", "status", "rows", "wire_bytes",
               "fetched_at", "error"]
# A filing in one of these states is DONE and is never fetched again. "error"
# is the only state that is retried -- by the step's own retry pass, and then
# by every later night until it clears.
FINAL = frozenset({"ok", "empty", "no_xbrl", "no_statements", "gone"})


def log(m: str) -> None:
    print(m, flush=True)


# ===========================================================================
# Paths and small helpers
# ===========================================================================
def part_path(q: str):
    return config.FUNDAMENTALS_EDGAR / f"{q}.parquet"


def _ledger_path():
    return config.FUNDAMENTALS_EDGAR / "_ledger.parquet"


def _days_path():
    return config.FUNDAMENTALS_EDGAR / "_days.json"


def _quarter(d) -> str:
    d = d if isinstance(d, date) else date.fromisoformat(str(d)[:10])
    return f"{d.year}q{(d.month - 1) // 3 + 1}"


def today_et() -> date:
    """The EDGAR calendar date. Filings are dated in Eastern time, and the
    05:00 PKT run is the previous evening there."""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York")).date()


def month_end(d: date) -> date:
    """The NEAREST month end, which is how the bulk sets date every fact.

    KO's fiscal quarter ended 2026-07-03; the bulk row says 2026-06-30.
    AAPL's ended 2026-06-27; 2026-06-30 again. A tie (the 15th of a 30-day
    month, the 14th of February) goes to the earlier month end -- no measured
    filing has landed on one.
    """
    prev = date(d.year, d.month, 1) - timedelta(days=1)
    cur = date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])
    return cur if (cur - d).days < (d - prev).days else prev


def qtrs_of(start: date, end: date) -> int:
    """Duration in whole quarters, rounded to nearest, as the bulk sets count.

    XBRL durations are inclusive of both ends: 2026-04-04..2026-07-03 is 91
    days, one quarter; a 53-week year (371 days) is still 4.
    """
    days = (end - start).days + 1
    return int(math.floor(days / (365.25 / 4) + 0.5))


# ===========================================================================
# HTTP: one pacer for every worker, and an honest byte count
# ===========================================================================
class _Pacer:
    """At most `rate` requests a second across ALL threads.

    Each caller reserves the next free slot under the lock and sleeps outside
    it, so six workers can never burst past the ceiling together.
    """

    def __init__(self, rate: float):
        self.gap = 1.0 / rate
        self.lock = threading.Lock()
        self.next = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            slot = max(now, self.next)
            self.next = slot + self.gap
        if slot > now:
            time.sleep(slot - now)


_PACER = _Pacer(RATE)
_STATS_LOCK = threading.Lock()
_STATS = {"requests": 0, "wire_bytes": 0}
_LOCAL = threading.local()


def _session() -> requests.Session:
    """One Session per thread: requests does not document Session as
    thread-safe, and `backfill_companyfacts` follows the same rule."""
    s = getattr(_LOCAL, "sess", None)
    if s is None:
        s = _LOCAL.sess = requests.Session()
    return s


def _get(url: str) -> tuple[int, bytes, int]:
    """(status, decoded body, bytes on the wire). 404 is an answer, not an error.

    The body is read RAW and decompressed here, so `wire` is what actually
    crossed the network -- requests' own `.content` would report the inflated
    size and overstate the download ~20x for an XBRL instance.
    """
    hdr = {"User-Agent": config.SEC_UA, "Accept-Encoding": "gzip, deflate"}
    last = None
    for attempt in range(ATTEMPTS):
        _PACER.wait()
        try:
            r = _session().get(url, headers=hdr, timeout=60, stream=True)
            raw = r.raw.read(decode_content=False)
            with _STATS_LOCK:
                _STATS["requests"] += 1
                _STATS["wire_bytes"] += len(raw)
            if r.status_code == 404:
                return 404, b"", len(raw)
            enc = (r.headers.get("Content-Encoding") or "").lower()
            if enc == "gzip":
                body = gzip.decompress(raw)
            elif enc == "deflate":
                try:
                    body = zlib.decompress(raw)
                except zlib.error:
                    body = zlib.decompress(raw, -zlib.MAX_WBITS)
            else:
                body = raw
            if r.status_code == 403 and b"<Code>AccessDenied</Code>" in body:
                # A MISSING FILE, NOT A THROTTLE. The Archives answer a path
                # that does not exist -- Labor Day's daily index, measured --
                # with an S3 AccessDenied document rather than a 404. Treated
                # as throttling it cost 70s of backoff and left the holiday
                # "unsettled", to be re-tried every night.
                return 404, b"", len(raw)
            if r.status_code != 200:
                # 403 is how SEC answers a client it is throttling; 429/5xx are
                # transient by definition. All are retried with backoff.
                raise RuntimeError(f"HTTP {r.status_code}")
            return 200, body, len(raw)
        except Exception as exc:                                 # noqa: BLE001
            last = exc
            slow = "403" in str(exc) or "429" in str(exc)
            time.sleep((10.0 if slow else 1.0) * (2 ** attempt))
    raise RuntimeError(f"{url}: {last!r}")


def _reset_stats() -> None:
    with _STATS_LOCK:
        _STATS["requests"] = 0
        _STATS["wire_bytes"] = 0


# ===========================================================================
# Discover
# ===========================================================================
def _wanted_form(form: str) -> bool:
    return str(form).startswith(FORMS)


def parse_master(txt: str) -> list[dict]:
    """`CIK|Company Name|Form Type|Date Filed|File Name` -> filing dicts."""
    out = []
    for line in txt.splitlines():
        p = line.split("|")
        if len(p) != 5 or not p[0].strip().isdigit():
            continue
        cik, _name, form, filed, fname = (x.strip() for x in p)
        if not _wanted_form(form) or len(filed) != 8:
            continue
        adsh = fname.rsplit("/", 1)[-1]
        if adsh.endswith(".txt"):
            adsh = adsh[:-4]
        out.append({"cik": int(cik), "adsh": adsh, "form": form,
                    "filed": f"{filed[:4]}-{filed[4:6]}-{filed[6:]}"})
    return out


def daily_index(day: date) -> list[dict] | None:
    """Every wanted filing EDGAR accepted on `day`; None if no index exists
    (a weekend, a holiday, or a day SEC has not published yet)."""
    url = DAILY_URL.format(y=day.year, q=(day.month - 1) // 3 + 1,
                           ymd=day.strftime("%Y%m%d"))
    st, body, _ = _get(url)
    if st == 404:
        return None
    return parse_master(body.decode("latin-1"))


_ATOM = "{http://www.w3.org/2005/Atom}"


def parse_feed(xml: bytes) -> list[dict]:
    """The "latest filings" Atom feed -> filing dicts."""
    out = []
    root = ET.fromstring(xml)
    for e in root.iter(_ATOM + "entry"):
        cat = e.find(_ATOM + "category")
        form = cat.get("term") if cat is not None else ""
        title = e.findtext(_ATOM + "title") or ""
        summ = e.findtext(_ATOM + "summary") or ""
        eid = e.findtext(_ATOM + "id") or ""
        try:
            cik = int(title.rsplit("(", 2)[-2].split(")")[0])
        except (IndexError, ValueError):
            continue
        adsh = eid.rsplit("=", 1)[-1].strip()
        i = summ.find("Filed:")
        filed = ""
        if i >= 0:
            tail = summ[i + 6:].replace("</b>", " ").replace("&lt;/b&gt;", " ")
            filed = next((t for t in tail.split() if len(t) == 10
                          and t[4] == "-" and t[7] == "-"), "")
        if not filed or len(adsh) != 20 or not _wanted_form(form):
            continue
        out.append({"cik": cik, "adsh": adsh, "form": form, "filed": filed})
    return out


def current_feed(max_pages: int = 20) -> list[dict]:
    """Filings from the last business day or two, before their daily index
    exists. SEC publishes day D's index at ~22:00 ET, which is 07:00 PKT --
    two hours AFTER the 05:00 run -- so without this a filing waits a full
    extra day. Best effort: anything missed here is in tomorrow's index."""
    out: list[dict] = []
    for form in FORMS:
        for page in range(max_pages):
            st, body, _ = _get(FEED_URL.format(form=form, start=page * 100))
            if st != 200:
                break
            got = parse_feed(body)
            out.extend(got)
            if len(got) < 100:
                break
    return out


def submissions(cik: int, since: str) -> list[dict]:
    """One company's wanted filings since `since`, from the submissions API.

    The per-name path: exact and immediate, but one request per company, so it
    is for named tickers rather than the nightly sweep.
    """
    st, body, _ = _get(config.SEC_SUBMISSIONS_URL.format(cik=int(cik)))
    if st != 200:
        return []
    rec = json.loads(body).get("filings", {}).get("recent", {})
    out = []
    for adsh, form, filed in zip(rec.get("accessionNumber", []),
                                 rec.get("form", []), rec.get("filingDate", [])):
        if _wanted_form(form) and str(filed) >= since:
            out.append({"cik": int(cik), "adsh": str(adsh), "form": str(form),
                        "filed": str(filed)})
    return out


# ===========================================================================
# Parse
# ===========================================================================
_XBRLI = "{http://www.xbrl.org/2003/instance}"
_LINK = "{http://www.xbrl.org/2003/linkbase}"
_XLINK = "{http://www.w3.org/1999/xlink}"
_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"
_DEI = {"EntityCentralIndexKey", "DocumentFiscalYearFocus",
        "DocumentFiscalPeriodFocus"}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def statement_roles(summary: bytes) -> set[str]:
    """The FACE-statement roles: what SEC's renderer files under MenuCategory
    "Statements" (the role definition reads "... - Statement - ...").
    Parenthetical statements count, as they do in the bulk set."""
    roles = set()
    for rp in ET.fromstring(summary).iter():
        if _local(rp.tag) != "Report":
            continue
        kids = {_local(c.tag): (c.text or "").strip() for c in rp}
        role = kids.get("Role", "")
        if role and (kids.get("MenuCategory") == "Statements"
                     or " - statement - " in kids.get("LongName", "").lower()):
            roles.add(role)
    return roles


def statement_concepts(pre: bytes, roles: set[str]) -> set[str]:
    """Local names of every concept the presentation linkbase puts in `roles`."""
    out = set()
    if not roles:
        return out
    for pl in ET.fromstring(pre).iter(_LINK + "presentationLink"):
        if pl.get(_XLINK + "role") not in roles:
            continue
        for loc in pl.iter(_LINK + "loc"):
            frag = (loc.get(_XLINK + "href") or "").rsplit("#", 1)[-1]
            if "_" in frag:
                out.add(frag.split("_", 1)[1])
    return out


def _context(el) -> tuple:
    ent = el.find(_XBRLI + "entity")
    ident = ent.findtext(_XBRLI + "identifier") if ent is not None else None
    seg = ent.find(_XBRLI + "segment") if ent is not None else None
    scen = el.find(_XBRLI + "scenario")
    dims = (seg is not None and len(seg) > 0) or \
        (scen is not None and len(scen) > 0)
    per = el.find(_XBRLI + "period")
    if per is None:
        return (None, True, None, None)
    s = per.findtext(_XBRLI + "startDate")
    e = per.findtext(_XBRLI + "endDate") or per.findtext(_XBRLI + "instant")
    return ((ident or "").strip() or None, dims,
            (s or "").strip()[:10] or None, (e or "").strip()[:10] or None)


def _unit(el) -> str:
    """The NUMERATOR's local name: iso4217:USD -> USD, USD/shares -> USD."""
    div = el.find(_XBRLI + "divide")
    src = div.find(_XBRLI + "unitNumerator") if div is not None else el
    ms = [(m.text or "").strip().split(":")[-1]
          for m in src.iter(_XBRLI + "measure")]
    return "*".join(m for m in ms if m)


def _precision(decimals: str | None, precision: str | None) -> float:
    """How exactly a fact was reported: `decimals="-3"` is thousands, "INF"
    exact. Used only to choose between duplicates of one fact."""
    if decimals is not None:
        d = decimals.strip()
        if d.upper() == "INF":
            return math.inf
        try:
            return float(d)
        except ValueError:
            pass
    # `precision` (significant digits) is not comparable with `decimals`; EFM
    # filings no longer use it, so only its exact form is honoured.
    if precision is not None and precision.strip().upper() == "INF":
        return math.inf
    return -math.inf


def scan_instance(data: bytes, keep: set[str]
                  ) -> tuple[dict, dict, list, list, set]:
    """(contexts, units, facts, dei, statement_contexts), streaming.

    Only direct children of <xbrl> are facts, contexts or units, and each is
    cleared once read, so a 30 MB bank 10-K never sits in memory as a tree.
    `statement_contexts` are the contexts any NON-dei fact uses: a cover-page
    date is used by dei facts alone, so it is not a statement column.
    """
    contexts, units, facts, dei = {}, {}, [], []
    used: set = set()
    depth, root, n = 0, None, 0
    for ev, el in ET.iterparse(io.BytesIO(data), events=("start", "end")):
        if ev == "start":
            if root is None:
                root = el
            depth += 1
            continue
        depth -= 1
        if depth != 1:
            continue
        tag = el.tag
        if tag == _XBRLI + "context":
            contexts[el.get("id")] = _context(el)
        elif tag == _XBRLI + "unit":
            units[el.get("id")] = _unit(el)
        else:
            local = _local(tag)
            is_dei = "/dei/" in tag
            cref = el.get("contextRef")
            if cref and not is_dei:
                used.add(cref)
            if local in keep and el.get("unitRef") and \
                    el.get(_NIL) not in ("true", "1"):
                facts.append((local, cref, el.get("unitRef"),
                              (el.text or "").strip(),
                              _precision(el.get("decimals"), el.get("precision")),
                              is_dei))
            elif local in _DEI and is_dei:
                dei.append((local, cref, (el.text or "").strip()))
        n += 1
        if n % 2000 == 0:
            root.clear()              # every child so far is fully processed
    return contexts, units, facts, dei, used


def parse_filing(instance: bytes, summary: bytes, pre: bytes, *, adsh: str,
                 form: str, filed: str, cik_hint: int, sic: int = 0
                 ) -> tuple[pd.DataFrame, dict]:
    """One filing -> rows in the bulk SCHEMA, plus counters for the ledger."""
    roles = statement_roles(summary)
    keep = statement_concepts(pre, roles) & fundamentals.WANTED
    meta = {"statement_roles": len(roles), "statement_concepts": len(keep),
            "dup_conflicts": 0, "short_durations": 0}
    if not keep:
        return pd.DataFrame(columns=fundamentals.SCHEMA), meta
    contexts, units, facts, dei, used = scan_instance(instance, keep)

    def _dei(name):
        vals = [v for k, c, v in dei if k == name and v
                and c in contexts and not contexts[c][1]]
        return vals[0] if vals else ""

    cik = _dei("EntityCentralIndexKey")
    if not cik.isdigit():
        idents = pd.Series([c[0] for c in contexts.values()
                            if not c[1] and c[0] and c[0].isdigit()])
        cik = idents.mode().iloc[0] if len(idents) else str(cik_hint)
    fy = _dei("DocumentFiscalYearFocus")
    fy = int(fy) if fy.isdigit() and len(fy) == 4 else 0
    fp = _dei("DocumentFiscalPeriodFocus")

    seen: dict = {}
    for local, cref, uref, txt, prec, is_dei in facts:
        ctx = contexts.get(cref)
        if ctx is None or ctx[1] or not ctx[3] or uref not in units:
            continue
        if is_dei and cref not in used:
            # THE COVER-PAGE SHARE COUNT, dated the day the cover was written.
            # BBRY presents dei:EntityCommonStockSharesOutstanding on its
            # parenthetical balance sheet, but its only fact is dated 09-21,
            # a date no statement column has -- and the bulk set omits it.
            # SCI's sits on its 06-30 balance-sheet date, and the bulk set
            # keeps that one. Same concept, decided by the column.
            continue
        try:
            # `+ 0.0` turns a filed "-0.00" into 0.0, as the bulk set stores it.
            value = round(float(txt), 4) + 0.0
            end = date.fromisoformat(ctx[3])
            start = date.fromisoformat(ctx[2]) if ctx[2] else None
        except ValueError:
            continue
        q = qtrs_of(start, end) if start else 0
        if start and q == 0:
            # A duration under half a quarter -- an inception stub (a SPAC's
            # 28 days to its first period end) or a one-day period -- rounds
            # to 0 and so reads like an instant. KEPT, because the bulk set
            # keeps it exactly so (7 of 4,762 diffed rows were these, all
            # bulk-only until this was matched); one filing must give the same
            # rows whichever store serves it. Counted for the ledger.
            meta["short_durations"] += 1
        me = month_end(end)
        key = (local, me.isoformat(), q, units[uref])
        # TWO FACTS, ONE KEY. A filer can report one fact twice at two
        # precisions, and rounding can map two different periods onto one
        # (tag, ddate, qtrs). The bulk set's choice, inferred from every
        # collision the diff met, in this order:
        #   1. the MORE PRECISE copy -- MUFG's share count 11,980,601,000 beats
        #      11,981,000,000 (decimals=-6); LOGC's tax -41.9M (decimals=-5,
        #      02-28..06-30) beats 0 (decimals=-6, the quarter itself);
        #   2. at equal precision, the period that FITS the key best -- LOGC's
        #      capex for 04-01..06-30 (91 days) beats 02-28..06-30 (123 days)
        #      as "one quarter ending June", though it comes second;
        #   3. a true tie keeps the first in document order.
        # Few collisions decide anything (LOGC is a fresh-start successor with
        # two periods ending 06-30), so this is an inference from four cases,
        # not a documented rule -- `diff` is how to re-check it.
        fit = abs((me - end).days) + (
            abs((end - start).days + 1 - q * 365.25 / 4) if start else 0.0)
        old = seen.get(key)
        if old is not None:
            if old[2] != value:
                meta["dup_conflicts"] += 1
            if (-prec, fit) >= (-old[1], old[0]):
                continue
        seen[key] = (fit, prec, value)
    rows = [(int(cik), adsh, t, dd, q, v, filed, form, fy, fp, int(sic or 0), u)
            for (t, dd, q, u), (_f, _p, v) in seen.items()]
    if not rows:
        return pd.DataFrame(columns=fundamentals.SCHEMA), meta
    return fundamentals._typed(pd.DataFrame(rows, columns=fundamentals.SCHEMA)), meta


def _pick(names: list[tuple[str, int]]) -> tuple[str | None, str | None]:
    """(instance, presentation linkbase) file names from a folder listing.

    Inline filings carry an extracted instance named `*_htm.xml`. An older
    plain-XBRL filing's instance is the one .xml that is not a linkbase or
    SEC's own summary; the largest wins if a submission carries several.

    THE LINKBASE MAY LIVE INSIDE THE SCHEMA. MSFT's FY2026 10-K ships no
    `_pre.xml` at all: its presentation links are embedded in the 2.2 MB
    `msft-20260630.xsd`. Without this fallback the parser found no statement
    and stored nothing for Microsoft's annual report.
    """
    lower = [(n, n.lower(), s) for n, s in names]
    pre = next((n for n, l, _ in lower if l.endswith("_pre.xml")), None)
    htm = sorted([(s, n) for n, l, s in lower if l.endswith("_htm.xml")],
                 reverse=True)
    if htm:
        inst = htm[0][1]
    else:
        skip = ("_cal.xml", "_def.xml", "_lab.xml", "_pre.xml",
                "filingsummary.xml")
        rest = sorted([(s, n) for n, l, s in lower
                       if l.endswith(".xml") and not l.endswith(skip)],
                      reverse=True)
        inst = rest[0][1] if rest else None
    if pre is None:
        stem = (inst or "").lower().replace("_htm.xml", "").replace(".xml", "")
        xsd = sorted([(l[:-4] != stem, -s, n) for n, l, s in lower
                      if l.endswith(".xsd")])
        pre = xsd[0][2] if xsd else None
    return inst, pre


def fetch_filing(f: dict, sic: int = 0) -> tuple[pd.DataFrame, dict]:
    """Fetch and parse one filing. Never raises; failures come back as status."""
    t0, wire = time.time(), 0
    rec = {"adsh": f["adsh"], "cik": int(f["cik"]), "form": f["form"],
           "filed": f["filed"], "status": "error", "rows": 0, "error": ""}
    empty = pd.DataFrame(columns=fundamentals.SCHEMA)
    try:
        folder = FOLDER_URL.format(cik=int(f["cik"]),
                                   nodash=f["adsh"].replace("-", ""))
        st, body, w = _get(folder + "index.json")
        wire += w
        if st == 404:
            rec["status"] = "gone"
            return empty, {**rec, "wire_bytes": wire}
        items = json.loads(body).get("directory", {}).get("item", [])
        names = []
        for it in items:
            try:
                size = int(it.get("size") or 0)
            except ValueError:
                size = 0
            names.append((str(it.get("name", "")), size))
        inst, pre = _pick(names)
        if inst is None:
            rec["status"] = "no_xbrl"
            return empty, {**rec, "wire_bytes": wire}
        if pre is None or not any(n == "FilingSummary.xml" for n, _ in names):
            rec["status"] = "no_statements"
            return empty, {**rec, "wire_bytes": wire}
        _, summary, w1 = _get(folder + "FilingSummary.xml")
        _, preb, w2 = _get(folder + pre)
        _, x, w3 = _get(folder + inst)
        wire += w1 + w2 + w3
        df, meta = parse_filing(x, summary, preb, adsh=f["adsh"], form=f["form"],
                                filed=f["filed"], cik_hint=int(f["cik"]), sic=sic)
        # "no_statements": SEC's renderer found no face statement at all (a
        # Part III-only 10-K/A, or a FilingSummary that lists the cover alone).
        # "empty": statements exist but carry no non-dimensional fact we read
        # -- a commodity pool, a royalty trust, or Presidio, whose every
        # statement fact sits under a Successor/Predecessor member and which
        # the bulk set therefore holds no rows for either.
        if not meta["statement_roles"]:
            rec["status"] = "no_statements"
        else:
            rec["status"] = "ok" if len(df) else "empty"
        rec["rows"] = len(df)
        if len(df):
            rec["cik"] = int(df["cik"].iloc[0])
        return df, {**rec, "wire_bytes": wire, "seconds": time.time() - t0, **meta}
    except Exception as exc:                                     # noqa: BLE001
        rec["error"] = repr(exc)[:200]
        return empty, {**rec, "wire_bytes": wire}


# ===========================================================================
# Store
# ===========================================================================
def stored_quarters() -> list[str]:
    d = config.FUNDAMENTALS_EDGAR
    return sorted(p.stem for p in d.glob("*q*.parquet")) if d.exists() else []


def write(df: pd.DataFrame) -> int:
    """Upsert rows by ACCESSION into their period-quarter partitions.

    Replacing every row of an accession that is being written makes a re-run
    idempotent: a filing fetched twice is stored once.
    """
    if df is None or df.empty:
        return 0
    df = fundamentals._typed(df)
    config.FUNDAMENTALS_EDGAR.mkdir(parents=True, exist_ok=True)
    qcol = df["ddate"].map(_quarter)
    for q, grp in df.groupby(qcol):
        path = part_path(str(q))
        if path.exists():
            old = pd.read_parquet(path)
            old = old[~old["adsh"].isin(set(grp["adsh"]))]
            grp = pd.concat([old, grp], ignore_index=True)
        tmp = path.with_suffix(".parquet.tmp")
        fundamentals._typed(grp).to_parquet(
            tmp, compression=config.COMPRESSION,
            compression_level=config.COMPRESSION_LEVEL, index=False)
        store.atomic_replace(tmp, path)
    return len(df)


def read_ledger() -> pd.DataFrame:
    p = _ledger_path()
    if not p.exists():
        return pd.DataFrame(columns=LEDGER_COLS)
    return pd.read_parquet(p)


def _write_ledger(recs: list[dict]) -> None:
    if not recs:
        return
    new = pd.DataFrame([{c: r.get(c) for c in LEDGER_COLS} for r in recs])
    old = read_ledger()
    led = pd.concat([old[~old["adsh"].isin(set(new["adsh"]))], new],
                    ignore_index=True) if len(old) else new
    led["cik"] = pd.to_numeric(led["cik"], errors="coerce").fillna(0).astype("int64")
    led["rows"] = pd.to_numeric(led["rows"], errors="coerce").fillna(0).astype("int64")
    led["wire_bytes"] = pd.to_numeric(led["wire_bytes"],
                                      errors="coerce").fillna(0).astype("int64")
    for c in ("adsh", "form", "filed", "status", "fetched_at", "error"):
        led[c] = led[c].fillna("").astype(str)
    config.FUNDAMENTALS_EDGAR.mkdir(parents=True, exist_ok=True)
    tmp = _ledger_path().with_suffix(".parquet.tmp")
    led.to_parquet(tmp, compression=config.COMPRESSION, index=False)
    store.atomic_replace(tmp, _ledger_path())


def _read_days() -> dict:
    p = _days_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_days(days: dict) -> None:
    config.FUNDAMENTALS_EDGAR.mkdir(parents=True, exist_ok=True)
    tmp = _days_path().with_suffix(".json.tmp")
    tmp.write_text(json.dumps(days, indent=0, sort_keys=True), encoding="utf-8")
    store.atomic_replace(tmp, _days_path())


def _stored_accessions(since: date) -> set[str]:
    """Accessions the bulk and companyfacts stores already hold, so a filing
    they cover is never fetched. Bulk partitions are by FILING quarter, so
    those from `since` on are enough; companyfacts partitions are by PERIOD,
    so look back far enough to reach a late filer's period end."""
    out: set[str] = set()
    q_bulk = _quarter(since)
    q_cf = _quarter(since - timedelta(days=200))
    for q in fundamentals.stored_quarters(include_cf=False):
        if q >= q_bulk:
            out |= set(pd.read_parquet(fundamentals.part_path(q),
                                       columns=["adsh"])["adsh"].unique())
    if config.FUNDAMENTALS_CF.exists():
        for p in config.FUNDAMENTALS_CF.glob("*q*.parquet"):
            if p.stem >= q_cf:
                out |= set(pd.read_parquet(p, columns=["adsh"])["adsh"].unique())
    return out


def _sic_map() -> dict[int, int]:
    """cik -> SIC from the newest bulk partitions that carry one."""
    qs = fundamentals.stored_quarters(include_cf=False)[-8:]
    frames = [pd.read_parquet(fundamentals.part_path(q),
                              columns=["cik", "sic", "filed"]) for q in qs]
    if not frames:
        return {}
    d = pd.concat(frames, ignore_index=True)
    d = d[d["sic"] > 0].sort_values("filed").drop_duplicates("cik", keep="last")
    return dict(zip(d["cik"].astype(int), d["sic"].astype(int)))


def prune() -> int:
    """Drop rows whose accession the BULK store now holds.

    `fundamentals.read` already ignores them, so this is housekeeping, not
    correctness: without it the store would keep a second copy of every filing
    the quarterly sets have since delivered, and every read would page through
    it. Bulk only -- it is the reconciliation backstop; companyfacts can lag.
    """
    qs = stored_quarters()
    if not qs:
        return 0
    # The oldest filing STILL HERE, not the oldest ever ingested: once the bulk
    # set has superseded a quarter's filings this lookback moves forward, so a
    # nightly prune reads one or two bulk partitions, never a growing list.
    oldest = min(pd.read_parquet(part_path(q), columns=["filed"])["filed"].min()
                 for q in qs)
    bulk: set[str] = set()
    for q in fundamentals.stored_quarters(include_cf=False):
        if q >= _quarter(oldest):
            bulk |= set(pd.read_parquet(fundamentals.part_path(q),
                                        columns=["adsh"])["adsh"].unique())
    dropped = 0
    for q in qs:
        path = part_path(q)
        d = pd.read_parquet(path)
        hit = d["adsh"].isin(bulk)
        if not bool(hit.any()):
            continue
        dropped += int(hit.sum())
        d = d[~hit]
        if d.empty:
            path.unlink()
            continue
        tmp = path.with_suffix(".parquet.tmp")
        fundamentals._typed(d).to_parquet(
            tmp, compression=config.COMPRESSION,
            compression_level=config.COMPRESSION_LEVEL, index=False)
        store.atomic_replace(tmp, path)
    return dropped


# ===========================================================================
# Run
# ===========================================================================
def ingest(filings: list[dict], verbose: bool = True,
           sic: dict[int, int] | None = None) -> dict:
    """Fetch, parse and store a list of filings. Counts SUCCESSES.

    Writes every CHUNK filings, rows first and ledger second: a run killed in
    between re-fetches that chunk next time and the accession upsert stores it
    once, so an interruption costs a chunk, never correctness.
    """
    t0 = time.time()
    res = {"attempted": len(filings), "ok": 0, "empty": 0, "no_xbrl": 0,
           "no_statements": 0, "gone": 0, "rows": 0, "dup_conflicts": 0,
           "short_durations": 0, "failed_filings": [], "errors": []}
    if not filings:
        res["seconds"] = 0.0
        return res
    sic = _sic_map() if sic is None else sic
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for start in range(0, len(filings), CHUNK):
            part = filings[start:start + CHUNK]
            frames, recs = [], []
            for f, (df, rec) in zip(part, pool.map(
                    lambda f: fetch_filing(f, sic.get(int(f["cik"]), 0)), part)):
                rec["fetched_at"] = datetime.now().isoformat(timespec="seconds")
                recs.append(rec)
                st = rec["status"]
                if st == "error":
                    res["failed_filings"].append(f)
                    res["errors"].append(f"{f['adsh']}: {rec.get('error', '')[:80]}")
                else:
                    res[st] = res.get(st, 0) + 1
                res["dup_conflicts"] += int(rec.get("dup_conflicts", 0) or 0)
                res["short_durations"] += int(rec.get("short_durations", 0) or 0)
                if len(df):
                    frames.append(df)
            if frames:
                res["rows"] += write(pd.concat(frames, ignore_index=True))
            _write_ledger(recs)
            done += len(part)
            if verbose:
                log(f"  {done}/{len(filings)} filings, {res['ok']} with rows, "
                    f"{res['rows']:,} rows, {len(res['failed_filings'])} failed, "
                    f"{_STATS['wire_bytes'] / 1e6:.1f} MB, {time.time() - t0:.0f}s")
    if res["rows"]:
        # A new filer can change which CIKs report in a foreign currency.
        fundamentals.invalidate_currency_cache()
    res["seconds"] = time.time() - t0
    return res


def discover(since: date | None = None, use_feed: bool = True,
             verbose: bool = True) -> dict:
    """Which universe filings exist that we do not hold. Touches no filing."""
    today = today_et()
    days = _read_days()
    if since is None:
        settled = sorted(d for d, v in days.items() if v.get("status") == "ok")
        start = today - timedelta(days=LOOKBACK_DAYS)
        if settled:
            start = min(start, date.fromisoformat(settled[-1]) + timedelta(days=1))
        since = max(start, today - timedelta(days=MAX_LOOKBACK_DAYS))
    found: list[dict] = []
    tried, failed_days, fresh = 0, [], 0
    d = since
    while d <= today:
        key = d.isoformat()
        prior = days.get(key, {})
        # A published index is final, and a weekend has none. A 404 more than
        # a day old is a holiday; a fresher one may just not be published yet.
        if d.weekday() >= 5 or prior.get("status") == "ok" or (
                prior.get("status") == "none" and d < today - timedelta(days=1)):
            d += timedelta(days=1)
            continue
        tried += 1
        try:
            got = daily_index(d)
        except Exception as exc:                                 # noqa: BLE001
            failed_days.append(f"{key}({type(exc).__name__})")
            d += timedelta(days=1)
            continue
        if got is None:
            days[key] = {"status": "none"}
        else:
            days[key] = {"status": "ok", "n": len(got)}
            found.extend(got)
            fresh += 1
        d += timedelta(days=1)
    _write_days(days)

    feed_n = 0
    if use_feed:
        try:
            feed = current_feed()
            feed_n = len(feed)
            found.extend(feed)
        except Exception as exc:                                 # noqa: BLE001
            # The feed only accelerates; tomorrow's index is the guarantee.
            if verbose:
                log(f"  latest-filings feed failed ({type(exc).__name__}); "
                    f"tomorrow's index covers it")
    return {"since": since.isoformat(), "found": found, "days_tried": tried,
            "days_fresh": fresh, "days_failed": failed_days, "feed": feed_n}


def _select(found: list[dict], since: date, verbose: bool = True) -> dict:
    """Universe filings not already held anywhere, one entry per accession."""
    tm = fundamentals.ticker_map()
    ciks = set(int(c) for c in tm["cik"])
    led = read_ledger()
    final = set(led.loc[led["status"].isin(FINAL), "adsh"]) if len(led) else set()
    # Yesterday's failures are retried even when their day is already settled.
    retry = []
    if len(led):
        err = led[(led["status"] == "error") & (led["filed"] >= since.isoformat())]
        retry = [{"cik": int(r.cik), "adsh": r.adsh, "form": r.form,
                  "filed": r.filed} for r in err.itertuples()]
    held = _stored_accessions(since)
    by_adsh: dict[str, dict] = {}
    listed = 0
    for f in found + retry:
        if int(f["cik"]) not in ciks:
            continue
        if f["adsh"] not in by_adsh:
            listed += 1
            by_adsh[f["adsh"]] = f
    todo = [f for a, f in by_adsh.items() if a not in final and a not in held]
    todo.sort(key=lambda f: (f["filed"], f["adsh"]))
    return {"listed": listed,
            "in_store": sum(a in held for a in by_adsh),
            "in_ledger": sum(a in final and a not in held for a in by_adsh),
            "todo": todo}


def run(since: str | None = None, use_feed: bool = True, verbose: bool = True,
        dry_run: bool = False, tickers: list[str] | None = None) -> dict:
    """The nightly pass: discover, skip what any store holds, fetch the rest."""
    _reset_stats()
    t0 = time.time()
    since_d = date.fromisoformat(since) if since else None
    if tickers:
        tm = fundamentals.ticker_map()
        hit = tm[tm["ticker"].astype(str).isin({t.upper() for t in tickers})]
        since_d = since_d or (today_et() - timedelta(days=400))
        found = []
        for c in sorted(set(int(c) for c in hit["cik"])):
            found.extend(submissions(c, since_d.isoformat()))
        disc = {"since": since_d.isoformat(), "found": found, "days_tried": 0,
                "days_fresh": 0, "days_failed": [], "feed": 0}
    else:
        disc = discover(since_d, use_feed=use_feed, verbose=verbose)
    since_d = date.fromisoformat(disc["since"])
    sel = _select(disc["found"], since_d, verbose=verbose)
    t_disc = time.time() - t0
    if verbose:
        log(f"  edgar: since {disc['since']}, {disc['days_tried']} index day(s) "
            f"fetched ({len(disc['days_failed'])} failed), feed {disc['feed']}; "
            f"{sel['listed']} universe filing(s) listed, {sel['in_store']} already "
            f"in the bulk/companyfacts store, {sel['in_ledger']} already ingested, "
            f"{len(sel['todo'])} to fetch ({t_disc:.0f}s, "
            f"{_STATS['wire_bytes'] / 1e6:.2f} MB so far)")
    res = {"since": disc["since"], "days_tried": disc["days_tried"],
           "days_failed": disc["days_failed"], "feed": disc["feed"],
           "listed": sel["listed"], "in_store": sel["in_store"],
           "in_ledger": sel["in_ledger"], "to_fetch": len(sel["todo"]),
           "discover_seconds": t_disc}
    if dry_run:
        res.update(_STATS, seconds=time.time() - t0, todo=sel["todo"])
        return res
    got = ingest(sel["todo"], verbose=verbose)
    res.update({k: v for k, v in got.items() if k != "seconds"})
    res["pruned"] = prune()
    res.update(_STATS)
    res["seconds"] = time.time() - t0
    if verbose:
        log(f"  edgar: {got['ok']} filing(s) stored ({got['rows']:,} rows), "
            f"{got['empty']} empty, {got['no_xbrl']} without XBRL, "
            f"{got['no_statements']} without statements, "
            f"{len(got['failed_filings'])} failed; {res['pruned']:,} row(s) "
            f"pruned as now in the bulk set; {res['requests']:,} requests, "
            f"{res['wire_bytes'] / 1e6:.1f} MB, {res['seconds']:.0f}s")
    return res


# ===========================================================================
# Diff against the bulk set -- the verification this module was built on
# ===========================================================================
def diff(adshs: list[str], verbose: bool = True) -> pd.DataFrame:
    """Parse filings the BULK store already holds and compare field by field.

    Keyed on (tag, ddate, qtrs, uom), the bulk set's own unique key within an
    accession. Reports rows only one side has, and every metadata or value
    field that differs on rows both have. Stores nothing.
    """
    sic = _sic_map()
    out = []
    for adsh in adshs:
        b = None
        for q in reversed(fundamentals.stored_quarters(include_cf=False)):
            d = pd.read_parquet(fundamentals.part_path(q),
                                filters=[("adsh", "==", adsh)])
            if len(d):
                b = d
                break
        if b is None:
            log(f"  {adsh}: not in the bulk store")
            continue
        f = {"cik": int(b["cik"].iloc[0]), "adsh": adsh,
             "form": str(b["form"].iloc[0]), "filed": str(b["filed"].iloc[0])}
        e, rec = fetch_filing(f, sic.get(f["cik"], 0))
        key = ["tag", "ddate", "qtrs", "uom"]
        bb = b.astype({"tag": str, "form": str, "fp": str})
        ee = e.astype({"tag": str, "form": str, "fp": str})
        m = bb.merge(ee, on=key, how="outer", suffixes=("_b", "_e"),
                     indicator="side")
        both = m[m["side"] == "both"]
        row = {"adsh": adsh, "cik": f["cik"], "form": f["form"],
               "status": rec["status"], "bulk_rows": len(b), "edgar_rows": len(e),
               "matched": len(both),
               "bulk_only": int((m["side"] == "left_only").sum()),
               "edgar_only": int((m["side"] == "right_only").sum()),
               "dup_conflicts": rec.get("dup_conflicts", 0),
               "wire_kb": round(rec.get("wire_bytes", 0) / 1e3, 1),
               "seconds": round(rec.get("seconds", 0), 1)}
        # Numbers compared AS NUMBERS. An outer merge that has any one-sided
        # row upcasts the integer columns to float, and comparing them as text
        # then reports "2080087" != "2080087.0" on every row -- which is how
        # the first sample run showed 119 phantom cik/fy/sic differences.
        for c in ("value", "cik", "fy", "sic"):
            row[f"{c}_diff"] = int((pd.to_numeric(both[f"{c}_b"], errors="coerce")
                                    != pd.to_numeric(both[f"{c}_e"],
                                                     errors="coerce")).sum())
        for c in ("filed", "form", "fp"):
            row[f"{c}_diff"] = int((both[f"{c}_b"].astype(str)
                                    != both[f"{c}_e"].astype(str)).sum())
        out.append(row)
        if verbose:
            log(f"  {adsh} {f['form']:6} cik {f['cik']}: bulk {len(b)}, edgar "
                f"{len(e)}, matched {len(both)}, bulk-only {row['bulk_only']}, "
                f"edgar-only {row['edgar_only']}, value diffs {row['value_diff']}"
                + "".join(f", {c} diffs {row[c + '_diff']}"
                          for c in ("cik", "filed", "form", "fy", "fp", "sic")
                          if row[c + "_diff"]))
            odd = m[m["side"] != "both"]
            for r in odd.head(12).itertuples():
                v = r.value_b if r.side == "left_only" else r.value_e
                log(f"      {r.side:10} {r.tag} {r.ddate} q{r.qtrs} {r.uom} {v}")
            bad = both[both["value_b"] != both["value_e"]]
            for r in bad.head(6).itertuples():
                log(f"      value      {r.tag} {r.ddate} q{r.qtrs} "
                    f"bulk {r.value_b} edgar {r.value_e}")
    return pd.DataFrame(out)


# ===========================================================================
# Selftest -- offline: no network, no real store touched
# ===========================================================================
_T_SUMMARY = b"""<?xml version="1.0"?><FilingSummary><MyReports>
<Report><LongName>0000001 - Document - Cover</LongName><Role>http://x/role/Cover</Role><MenuCategory>Cover</MenuCategory></Report>
<Report><LongName>0000002 - Statement - INCOME</LongName><Role>http://x/role/IS</Role><MenuCategory>Statements</MenuCategory></Report>
<Report><LongName>0000003 - Statement - BALANCE SHEET</LongName><Role>http://x/role/BS</Role><MenuCategory>Statements</MenuCategory></Report>
<Report><LongName>0000009 - Disclosure - SEGMENTS (Details)</LongName><Role>http://x/role/Seg</Role><MenuCategory>Details</MenuCategory></Report>
</MyReports></FilingSummary>"""

_T_PRE = b"""<?xml version="1.0"?>
<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase" xmlns:xlink="http://www.w3.org/1999/xlink">
<link:presentationLink xlink:role="http://x/role/IS">
 <link:loc xlink:href="https://xbrl.fasb.org/us-gaap-2026.xsd#us-gaap_Revenues"/>
 <link:loc xlink:href="https://xbrl.fasb.org/us-gaap-2026.xsd#us-gaap_EarningsPerShareDiluted"/>
 <link:loc xlink:href="https://xbrl.fasb.org/us-gaap-2026.xsd#us-gaap_EarningsPerShareBasic"/>
</link:presentationLink>
<link:presentationLink xlink:role="http://x/role/BS">
 <link:loc xlink:href="https://xbrl.fasb.org/us-gaap-2026.xsd#us-gaap_Assets"/>
 <link:loc xlink:href="https://xbrl.sec.gov/dei-2026.xsd#dei_EntityCommonStockSharesOutstanding"/>
</link:presentationLink>
<link:presentationLink xlink:role="http://x/role/Seg">
 <link:loc xlink:href="https://xbrl.fasb.org/us-gaap-2026.xsd#us-gaap_InterestExpense"/>
</link:presentationLink>
<link:presentationLink xlink:role="http://x/role/Cover">
 <link:loc xlink:href="https://xbrl.sec.gov/dei-2026.xsd#dei_EntityCommonStockSharesOutstanding"/>
</link:presentationLink>
</link:linkbase>"""

_T_INSTANCE = b"""<?xml version="1.0"?>
<xbrl xmlns="http://www.xbrl.org/2003/instance" xmlns:us-gaap="http://fasb.org/us-gaap/2026"
 xmlns:dei="http://xbrl.sec.gov/dei/2026" xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
 xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<context id="q"><entity><identifier scheme="http://www.sec.gov/CIK">0000021344</identifier></entity>
 <period><startDate>2026-04-04</startDate><endDate>2026-07-03</endDate></period></context>
<context id="h"><entity><identifier scheme="http://www.sec.gov/CIK">0000021344</identifier></entity>
 <period><startDate>2026-01-01</startDate><endDate>2026-07-03</endDate></period></context>
<context id="i"><entity><identifier scheme="http://www.sec.gov/CIK">0000021344</identifier></entity>
 <period><instant>2026-07-03</instant></period></context>
<context id="d"><entity><identifier scheme="http://www.sec.gov/CIK">0000021344</identifier>
 <segment><xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis">x:EuropeMember</xbrldi:explicitMember></segment></entity>
 <period><startDate>2026-04-04</startDate><endDate>2026-07-03</endDate></period></context>
<context id="cv"><entity><identifier scheme="http://www.sec.gov/CIK">0000021344</identifier></entity>
 <period><instant>2026-07-24</instant></period></context>
<context id="x"><entity><identifier scheme="http://www.sec.gov/CIK">0000021344</identifier></entity>
 <period><startDate>2026-02-28</startDate><endDate>2026-07-03</endDate></period></context>
<context id="s"><entity><identifier scheme="http://www.sec.gov/CIK">0000021344</identifier></entity>
 <period><startDate>2026-06-01</startDate><endDate>2026-07-03</endDate></period></context>
<unit id="usd"><measure>iso4217:USD</measure></unit>
<unit id="sh"><measure>xbrli:shares</measure></unit>
<unit id="eps"><divide><unitNumerator><measure>iso4217:USD</measure></unitNumerator>
 <unitDenominator><measure>xbrli:shares</measure></unitDenominator></divide></unit>
<dei:EntityCentralIndexKey contextRef="q">0000021344</dei:EntityCentralIndexKey>
<dei:DocumentFiscalYearFocus contextRef="q">2026</dei:DocumentFiscalYearFocus>
<dei:DocumentFiscalPeriodFocus contextRef="q">Q2</dei:DocumentFiscalPeriodFocus>
<dei:EntityCommonStockSharesOutstanding contextRef="cv" unitRef="sh" decimals="0">4302123456</dei:EntityCommonStockSharesOutstanding>
<dei:EntityCommonStockSharesOutstanding contextRef="i" unitRef="sh" decimals="0">4301000000</dei:EntityCommonStockSharesOutstanding>
<us-gaap:Revenues contextRef="x" unitRef="usd" decimals="-6">17000000000</us-gaap:Revenues>
<us-gaap:Revenues contextRef="q" unitRef="usd" decimals="-6">13380000000</us-gaap:Revenues>
<us-gaap:Revenues contextRef="q" unitRef="usd" decimals="-6">13380000000</us-gaap:Revenues>
<us-gaap:Revenues contextRef="h" unitRef="usd" decimals="-6">25852000000</us-gaap:Revenues>
<us-gaap:Revenues contextRef="d" unitRef="usd" decimals="-6">2000000000</us-gaap:Revenues>
<us-gaap:Revenues contextRef="s" unitRef="usd" decimals="-6">4000000000</us-gaap:Revenues>
<us-gaap:EarningsPerShareDiluted contextRef="q" unitRef="eps" decimals="2">1.03</us-gaap:EarningsPerShareDiluted>
<us-gaap:EarningsPerShareDiluted contextRef="h" unitRef="eps" decimals="2">1.941234567</us-gaap:EarningsPerShareDiluted>
<us-gaap:EarningsPerShareBasic contextRef="q" unitRef="eps" decimals="2">-0.00</us-gaap:EarningsPerShareBasic>
<us-gaap:Assets contextRef="i" unitRef="usd" decimals="-9">108000000000</us-gaap:Assets>
<us-gaap:Assets contextRef="i" unitRef="usd" decimals="-6">107922000000</us-gaap:Assets>
<us-gaap:Assets contextRef="i" unitRef="usd" xsi:nil="true"/>
<us-gaap:InterestExpense contextRef="q" unitRef="usd" decimals="-6">369000000</us-gaap:InterestExpense>
</xbrl>"""


def _selftest_rounding() -> None:
    d = date.fromisoformat
    for got, want in ((d("2026-07-03"), "2026-06-30"),   # KO Q2 2026, measured
                      (d("2025-06-27"), "2025-06-30"),   # AAPL-style 52/53 week
                      (d("2025-12-31"), "2025-12-31"),
                      (d("2026-01-02"), "2025-12-31"),
                      (d("2026-02-26"), "2026-02-28")):
        assert month_end(got).isoformat() == want, (got, month_end(got), want)
    for s, e, want in (("2026-04-04", "2026-07-03", 1), ("2026-01-01", "2026-07-03", 2),
                       ("2025-06-29", "2026-06-27", 4),    # 52 weeks
                       ("2024-09-29", "2025-10-04", 4),    # 53 weeks
                       ("2025-09-28", "2026-06-27", 3)):   # 39 weeks
        assert qtrs_of(d(s), d(e)) == want, (s, e, qtrs_of(d(s), d(e)), want)


def _selftest_parse() -> None:
    df, meta = parse_filing(_T_INSTANCE, _T_SUMMARY, _T_PRE, adsh="A-1",
                            form="10-Q", filed="2026-07-29", cik_hint=999, sic=2080)
    got = {(r.tag, r.ddate, int(r.qtrs), r.uom): r.value for r in
           df.astype({"tag": str}).itertuples()}
    want = {("Revenues", "2026-06-30", 1, "USD"): 13_380_000_000.0,
            ("Revenues", "2026-06-30", 2, "USD"): 25_852_000_000.0,
            # The 33-day stub rounds to qtrs=0, as the bulk set stores it.
            ("Revenues", "2026-06-30", 0, "USD"): 4_000_000_000.0,
            ("EarningsPerShareDiluted", "2026-06-30", 1, "USD"): 1.03,
            ("EarningsPerShareDiluted", "2026-06-30", 2, "USD"): 1.9412,
            ("EarningsPerShareBasic", "2026-06-30", 1, "USD"): 0.0,
            ("Assets", "2026-06-30", 0, "USD"): 107_922_000_000.0,
            # The dei share count on the balance-sheet date (SCI's case) stays.
            ("EntityCommonStockSharesOutstanding", "2026-06-30", 0, "shares"):
                4_301_000_000.0}
    # Excluded by design: the dimensional Europe revenue, the nil Assets,
    # InterestExpense (a note, not a statement), the share count dated on the
    # cover page (BBRY's case: no statement column has that date), and the
    # 126-day revenue that rounds onto the quarter's key but fits it worse
    # (LOGC's case) although it comes first in the document.
    # Assets is reported twice; the -6 copy is more precise than the -9 copy
    # that precedes it, and must win (the MUFG case).
    assert got == want, f"parse mismatch:\n got {got}\nwant {want}"
    assert meta["short_durations"] == 1 and meta["dup_conflicts"] == 2, meta
    # A filed "-0.00" is stored as 0.0, not -0.0 (AMFN's EPS, bulk says 0.0).
    eps0 = got[("EarningsPerShareBasic", "2026-06-30", 1, "USD")]
    assert math.copysign(1.0, eps0) == 1.0, "negative zero survived the parse"
    r = df.iloc[0]
    assert int(r["cik"]) == 21344 and str(r["form"]) == "10-Q", r
    assert int(r["fy"]) == 2026 and str(r["fp"]) == "Q2" and int(r["sic"]) == 2080, r
    assert list(df.columns) == fundamentals.SCHEMA


def _selftest_feed_and_index() -> None:
    idx = ("CIK|Company Name|Form Type|Date Filed|File Name\n"
           "-----\n"
           "21344|COCA COLA CO|10-Q|20260729|edgar/data/21344/0001628280-26-050503.txt\n"
           "21344|COCA COLA CO|4|20260729|edgar/data/21344/0000021344-26-000154.txt\n"
           "1000|X CORP|10-12G|20260729|edgar/data/1000/0000001000-26-000001.txt\n"
           "2000|Y CORP|20-F/A|20260729|edgar/data/2000/0000002000-26-000002.txt\n")
    got = parse_master(idx)
    assert [(g["cik"], g["form"], g["adsh"], g["filed"]) for g in got] == [
        (21344, "10-Q", "0001628280-26-050503", "2026-07-29"),
        (2000, "20-F/A", "0000002000-26-000002", "2026-07-29")], got
    feed = (b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry>'
            b'<title>10-Q - Quartzsea Acquisition Corp (0002047455) (Filer)</title>'
            b'<summary type="html"> &lt;b&gt;Filed:&lt;/b&gt; 2026-10-09 '
            b'&lt;b&gt;AccNo:&lt;/b&gt; 0001829126-26-010988</summary>'
            b'<category scheme="https://www.sec.gov/" label="form type" term="10-Q"/>'
            b'<id>urn:tag:sec.gov,2008:accession-number=0001829126-26-010988</id>'
            b'</entry></feed>')
    assert parse_feed(feed) == [{"cik": 2047455, "adsh": "0001829126-26-010988",
                                 "form": "10-Q", "filed": "2026-10-09"}], parse_feed(feed)
    assert _pick([("ko-20260703_htm.xml", 2_921_566), ("ko-20260703_pre.xml", 589_342),
                  ("FilingSummary.xml", 44_017), ("ko-20260703_lab.xml", 805_158)]) == \
        ("ko-20260703_htm.xml", "ko-20260703_pre.xml")
    assert _pick([("abc-20101231.xml", 900_000), ("abc-20101231_pre.xml", 1),
                  ("FilingSummary.xml", 1)]) == ("abc-20101231.xml", "abc-20101231_pre.xml")
    # MSFT's FY2026 10-K: no _pre.xml, linkbases embedded in its own schema.
    assert _pick([("msft-20260630_htm.xml", 10_948_157), ("FilingSummary.xml", 1),
                  ("other.xsd", 9_999_999), ("msft-20260630.xsd", 2_170_276)]) == \
        ("msft-20260630_htm.xml", "msft-20260630.xsd")


def _selftest_read_dedup() -> None:
    """THE invariant: a filing the bulk or companyfacts store holds is never
    read twice, and a filing only EDGAR has is read. Run against a temporary
    store so the real one is never touched."""
    import tempfile
    from pathlib import Path
    saved = (config.FUNDAMENTALS, config.FUNDAMENTALS_CF, config.FUNDAMENTALS_EDGAR)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            config.FUNDAMENTALS = Path(tmp) / "b"
            config.FUNDAMENTALS_CF = Path(tmp) / "c"
            config.FUNDAMENTALS_EDGAR = Path(tmp) / "e"
            for p in (config.FUNDAMENTALS, config.FUNDAMENTALS_CF,
                      config.FUNDAMENTALS_EDGAR):
                p.mkdir()
            fundamentals._READ_CACHE.clear()
            fundamentals.invalidate_currency_cache()

            def rows(adsh, cik, ddate, v, filed):
                return pd.DataFrame([(cik, adsh, "Revenues", ddate, 1, v, filed,
                                      "10-Q", 2026, "Q2", 2080, "USD")],
                                    columns=fundamentals.SCHEMA)
            fundamentals._typed(rows("BULK-1", 1, "2026-06-30", 10.0, "2026-07-29")) \
                .to_parquet(fundamentals.part_path("2026q3"), index=False)
            fundamentals._typed(rows("CF-1", 3, "2026-06-30", 30.0, "2026-08-01")) \
                .to_parquet(fundamentals.cf_part_path("2026q2"), index=False)
            write(pd.concat([rows("BULK-1", 1, "2026-06-30", 10.0, "2026-07-29"),
                             rows("CF-1", 3, "2026-06-30", 30.0, "2026-08-01"),
                             rows("NEW-2", 2, "2026-09-30", 20.0, "2026-10-09")]))
            # A second write of the same accession must replace, not append.
            write(rows("NEW-2", 2, "2026-09-30", 20.0, "2026-10-09"))
            assert len(pd.read_parquet(part_path("2026q3"))) == 1, \
                "re-writing an accession appended instead of replacing it"
            got = fundamentals.read()
            n = got.groupby("adsh").size().to_dict()
            assert n == {"BULK-1": 1, "CF-1": 1, "NEW-2": 1}, (
                f"each accession must be read exactly once, got {n}")
            fp0 = fundamentals._store_fingerprint()
            write(rows("NEW-3", 4, "2026-09-30", 40.0, "2026-10-10"))
            assert fundamentals._store_fingerprint() != fp0, \
                "an edgar write must invalidate the read cache"
            # Prune deletes what the BULK set now holds, and nothing else --
            # not CF-1, which only companyfacts has (read() still skips it).
            assert prune() == 1, "prune must drop exactly the bulk-held filing"
            left = set(pd.concat([pd.read_parquet(p) for p in
                                  config.FUNDAMENTALS_EDGAR.glob("*q*.parquet")])
                       ["adsh"])
            assert left == {"CF-1", "NEW-2", "NEW-3"}, left
            n = fundamentals.read().groupby("adsh").size().to_dict()
            assert n == {"BULK-1": 1, "CF-1": 1, "NEW-2": 1, "NEW-3": 1}, n
        finally:
            (config.FUNDAMENTALS, config.FUNDAMENTALS_CF,
             config.FUNDAMENTALS_EDGAR) = saved
            fundamentals._READ_CACHE.clear()
            fundamentals.invalidate_currency_cache()


def selftest(verbose: bool = True) -> None:
    _selftest_rounding()
    _selftest_parse()
    _selftest_feed_and_index()
    _selftest_read_dedup()
    if verbose:
        print("edgar selftest OK (rounding, parse, index/feed, read dedup)")


def stats() -> None:
    qs = stored_quarters()
    led = read_ledger()
    if not qs and not len(led):
        print("  (empty -- run `python edgar.py`)")
        return
    rows = []
    for q in qs:
        d = pd.read_parquet(part_path(q), columns=["cik", "adsh"])
        rows.append({"quarter": q, "rows": len(d), "filers": d["cik"].nunique(),
                     "filings": d["adsh"].nunique()})
    if rows:
        print(pd.DataFrame(rows).to_string(index=False))
    if len(led):
        print("\n  ledger:", led["status"].value_counts().to_dict(),
              f"newest filed {led['filed'].max()}")
    mb = sum(p.stat().st_size for p in config.FUNDAMENTALS_EDGAR.glob("*.parquet")) / 1e6
    print(f"  {mb:.1f} MB on disk")


def main() -> int:
    config.safe_console()
    ap = argparse.ArgumentParser(description="EDGAR per-filing XBRL ingest.")
    ap.add_argument("--since", default=None, help="YYYY-MM-DD; backfill from here")
    ap.add_argument("--tickers", default=None, help="comma list; submissions API")
    ap.add_argument("--diff", nargs="+", metavar="ADSH", default=None)
    ap.add_argument("--no-feed", dest="feed", action="store_false")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    config.dirs()
    if a.selftest:
        selftest()
    elif a.stats:
        stats()
    elif a.diff:
        _reset_stats()
        d = diff(a.diff)
        if len(d):
            print(d.to_string(index=False))
    else:
        res = run(since=a.since, use_feed=a.feed, dry_run=a.dry_run,
                  tickers=a.tickers.split(",") if a.tickers else None)
        if res.get("failed_filings"):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
