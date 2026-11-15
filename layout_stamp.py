#!/usr/bin/env python
"""Which code and which saved layout drew a stock page -- so a page left over from before a change
is found and rebuilt instead of sitting there in the old format.

Asked 2026-11-15: "schedule a job that quietly rebuilds stock profiles acc to the new updates, or
an auto feature that rebuilds or prompts ... if i open a stock profile page if theres been an
update". The nightly `profiles` step rebuilds every page once a session, so a change made after it
(new code, or a new layout saved on the Settings page) showed the old format until the next night,
and a local server left running kept writing pages with the code it had started with.

THE STAMP is 12 hex characters over the files that draw a stock page (stock_profile.py, ui.py,
settings.py) and the saved layout (data/_profile.json). Each page carries the stamp of the code and
layout that built it (<meta name="scr-format">); a page whose stamp differs from the files on disk
now is stale. Whole files are hashed on purpose: a stamp that missed a change would leave a stale
page looking current, while one that over-reacts costs only a rebuild.

USED BY: stock_profile.build (writes the stamp), the `profiles_layout` step (rebuilds stale pages
on the schedule's next pass), serve.py (reloads the page code when it changed on disk, and tells an
open page whether it is stale, so the page rebuilds itself in the background).

Light on purpose (hashlib and settings only): the orchestrator asks it on every pass.

Run: python layout_stamp.py            the current stamp and every stale page
     python layout_stamp.py --selftest
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import config
import settings

SOURCES = ("stock_profile.py", "ui.py", "settings.py")
STOCK_DIR = config.REPORTS / "stock"
_META_RE = re.compile(rb'<meta name="scr-format" content="([0-9a-f]{12})">')
HEAD_BYTES = 8192           # the stamp sits at the top of <head> (ui.page `meta`)


def code_stamp(root: Path | None = None) -> str:
    """Over the files that draw a stock page, as they are on disk now."""
    root = root or config.ROOT
    h = hashlib.sha1()
    for name in SOURCES:
        h.update(name.encode() + b"\0")
        try:
            h.update((root / name).read_bytes())
        except OSError:
            h.update(b"(missing)")
    return h.hexdigest()[:12]


def stamp(code: str | None = None) -> str:
    """The stamp a page built now would carry: `code` (default: the files on disk now) and the
    saved layout as it is on disk now."""
    h = hashlib.sha1((code or code_stamp()).encode() + b"\0")
    try:
        h.update(settings.PROFILE_FILE.read_bytes())
    except OSError:
        h.update(b"(default layout)")
    return h.hexdigest()[:12]


def meta(code: str | None = None) -> str:
    return f'<meta name="scr-format" content="{stamp(code)}">'


def page_stamp(path) -> str | None:
    """The stamp a written page carries; None when it has none (built before stamps) or is unreadable."""
    try:
        with open(path, "rb") as f:
            m = _META_RE.search(f.read(HEAD_BYTES))
        return m.group(1).decode() if m else None
    except OSError:
        return None


def current_pages() -> list[Path]:
    """TICKER.html pages; dated snapshots (TICKER_<session>.html) are records of their session."""
    if not STOCK_DIR.exists():
        return []
    return sorted(p for p in STOCK_DIR.glob("*.html") if p.stem != "index" and "_" not in p.stem)


def stale_pages(current: str | None = None) -> list[str]:
    """Tickers whose page was drawn by other code or another layout than the files on disk now."""
    cur = current or stamp()
    return [p.stem for p in current_pages() if page_stamp(p) != cur]


def selftest() -> int:
    import tempfile
    root = Path(tempfile.mkdtemp())
    for name in SOURCES:
        (root / name).write_text(f"# {name}\n", encoding="utf-8")
    a = code_stamp(root)
    assert a == code_stamp(root) and len(a) == 12, "the stamp must be stable"
    (root / "ui.py").write_text("# ui.py changed\n", encoding="utf-8")
    assert code_stamp(root) != a, "a change to a source must change the stamp"
    real = settings.PROFILE_FILE
    try:
        settings.PROFILE_FILE = root / "_profile.json"
        s0 = stamp("x" * 12)
        settings.PROFILE_FILE.write_text('{"name": "a"}', encoding="utf-8")
        s1 = stamp("x" * 12)
        settings.PROFILE_FILE.write_text('{"name": "b"}', encoding="utf-8")
        assert len({s0, s1, stamp("x" * 12)}) == 3, "the saved layout must be part of the stamp"
    finally:
        settings.PROFILE_FILE = real
    page = root / "AAA.html"
    page.write_text(f'<!DOCTYPE html><html><head><meta charset="utf-8">{meta("y" * 12)}</head></html>', encoding="utf-8")
    assert page_stamp(page) == stamp("y" * 12)
    page.write_text("<html><head></head></html>", encoding="utf-8")
    assert page_stamp(page) is None, "a page without a stamp has none"
    print(f"  [layout_stamp] stable, moved by any source and by the saved layout; current {stamp()}, "
          f"{len(stale_pages())} of {len(current_pages())} page(s) stale")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    cur = stamp()
    stale = stale_pages(cur)
    print(f"  current stamp {cur} (code {code_stamp()}); {len(stale)} of {len(current_pages())} page(s) stale"
          + (f": {', '.join(stale[:20])}" + (" ..." if len(stale) > 20 else "") if stale else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
