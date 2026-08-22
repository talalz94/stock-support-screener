"""Pinned values that were VERIFIED AGAINST FILINGS. Any change that moves one
of these is a regression, whatever else it improves.

    python regression_pins.py            check every pin
    python regression_pins.py --asof 2026-08-19

WHY THIS EXISTS
---------------
Between 2026-08-20 and 2026-08-21 seven data bugs were fixed in `fundamentals`,
and TWO of the fixes broke a name that had already been verified correct:

  * dropping DEF 14A rows fixed LOPE ($8.25M -> $219.9M) and took HZO from a
    web-confirmed 3,986,000 to 35,986,000
  * the roll-forward stub guard fixed HZO and silently removed EPS's only roll
    candidate, leaving it on the summed window

Both were caught only because someone happened to re-check the other name by
hand. That is not a method. Every figure below was confirmed against an SEC
filing or an issuer press release, with the source recorded, so the next fix
gets an automatic answer instead of a lucky one.

TOLERANCES are per-pin and deliberately loose enough to survive a restatement
or a rounding convention, and far too tight to survive a units error, a
double-counted quarter or a swapped definition -- the failures actually seen.

A pin is NOT a claim that the number is eternally right. It is a claim that it
was right on the date recorded, checked against the source named. When a filer
restates, update the pin AND the source in the same commit.
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

import config

config.safe_console()

ASOF = "2026-08-19"

# ticker, field, expected, rel_tol, source
PINS: list[tuple[str, str, float, float, str]] = [
    # --- HZO: the roll-forward hand-computed from the filings, to the dollar
    ("HZO", "cfo_ttm", 219_006_000, 1e-9,
     "FY25 72,806,000 + YTD 157,554,000 - prior 11,354,000 (10-K/10-Q)"),
    ("HZO", "capex_ttm", 40_985_000, 1e-9,
     "FY25 60,864,000 + 27,535,000 - 47,414,000 (10-K/10-Q)"),
    ("HZO", "dna_ttm", 50_823_000, 1e-9,
     "FY25 49,320,000 + 37,888,000 - 36,385,000 (10-K/10-Q)"),
    ("HZO", "revenue_ttm", 2_196_001_000, 1e-9, "matches Yahoo to 8 decimals"),
    ("HZO", "net_income_ttm", 3_986_000, 0.02, "web TTM $4.0M"),
    ("HZO", "eps_diluted_ttm", 0.16, 0.02,
     "roll of reported legs -1.43 + 0.21 - (-1.38); Yahoo 0.16"),

    # --- LOPE: DEF 14A reported THOUSANDS; rescale must hold
    ("LOPE", "net_income_ttm", 224_206_000, 0.03, "web TTM $219.9M"),
    ("LOPE", "eps_diluted_ttm", 8.28, 0.05, "web basic EPS 8.00"),

    # --- EE: proxy carried CONSOLIDATED, statements carry ATTRIBUTABLE
    ("EE", "net_income_ttm", 47_480_000, 0.05,
     "FY25 attributable 39,199,000 + 24,397,000 - 16,116,000 (10-K/10-Q)"),
    ("EE", "eps_diluted_ttm", 1.46, 0.10, "web TTM EPS 1.33"),

    # --- MRVL: the 52/53-week double-count
    ("MRVL", "net_income_ttm", 2_526_700_000, 0.01,
     "FY26 2,670.1 + 34.5 - 177.9; web TTM $2.53B"),
    ("MRVL", "eps_diluted_ttm", 2.91, 0.01, "web TTM EPS to 2026-04-30 = 2.91"),

    # --- STX: exact on all three
    ("STX", "revenue_ttm", 12_195_000_000, 0.01, "FY26 revenue $12.2B"),
    ("STX", "eps_diluted_ttm", 13.90, 0.01, "FY26 GAAP diluted EPS $13.90"),
    ("STX", "net_income_ttm", 3_184_000_000, 0.01, "13.90 x 229M shares"),

    # --- RGTI / BETA: correct despite the internal cross-check flagging them
    ("RGTI", "net_income_ttm", -238_672_000, 1e-6,
     "web TTM net loss $238.672M, to the dollar"),
    ("BETA", "eps_diluted_ttm", -10.01, 0.01,
     "web EPS TTM to 2026-06-30 = -10.02"),

    # --- LASR: three quarterly legs matched the press releases exactly
    ("LASR", "net_income_ttm", -12_477_000, 0.05,
     "Q1-26 +0.6M, Q2-26 -1.3M, Q2-25 -3.6M press releases"),

    # --- large caps: the sanity floor
    ("AAPL", "eps_diluted_ttm", 8.72, 0.02, "web ~8.7"),
    ("KO", "eps_diluted_ttm", 3.18, 0.02, "web ~3.18"),
    ("NVDA", "eps_diluted_ttm", 6.53, 0.03, "internally consistent"),
    ("MU", "eps_diluted_ttm", 44.24, 0.03, "internally consistent"),
    ("CACI", "eps_diluted_ttm", 24.16, 0.03, "internally consistent"),
    ("SCCO", "eps_diluted_ttm", 6.84, 0.03, "internally consistent"),
    ("HOOD", "eps_diluted_ttm", 2.26, 0.03, "internally consistent"),
    ("MRNA", "eps_diluted_ttm", -7.98, 0.03, "internally consistent"),
]

# Share counts that must stay DROPPED -- the filers contradict themselves.
MUST_BE_NULL: list[tuple[str, str, str]] = [
    ("ALMU", "shares_diluted_ttm",
     "10-Q tags 1.735437e10 while CommonStockSharesOutstanding is 1.811355e7"),
    ("TEM", "shares_diluted_ttm",
     "10-Q tags 182,397 shares for a company with ~180 MILLION"),
]


def check(asof: str = ASOF) -> int:
    import fundamentals as FD

    tickers = sorted({t for t, *_ in PINS} | {t for t, *_ in MUST_BE_NULL})
    f = FD.facts_asof(asof, tickers)
    if f.empty:
        print("FAIL: facts_asof returned nothing")
        return 1
    f = f.set_index("ticker")

    fails = []
    for tk, field, want, tol, src in PINS:
        if tk not in f.index or field not in f.columns:
            fails.append((tk, field, "MISSING", want, src))
            continue
        got = pd.to_numeric(pd.Series([f.at[tk, field]]), errors="coerce").iloc[0]
        if pd.isna(got):
            fails.append((tk, field, "NULL", want, src))
            continue
        rel = abs(float(got) - want) / max(abs(want), 1e-9)
        if rel > tol:
            fails.append((tk, field, f"{got:,.4f}", want, src))

    for tk, field, why in MUST_BE_NULL:
        if tk not in f.index:
            continue
        if field in f.columns:
            got = pd.to_numeric(pd.Series([f.at[tk, field]]),
                                errors="coerce").iloc[0]
            if pd.notna(got):
                fails.append((tk, field, f"{got:,.0f} (should be dropped)",
                              float("nan"), why))

    n = len(PINS) + len(MUST_BE_NULL)
    if not fails:
        print(f"regression_pins: {n} pin(s) OK  (asof {asof})")
        return 0

    print(f"regression_pins: {n - len(fails)}/{n} OK -- {len(fails)} REGRESSION(S)\n")
    for tk, field, got, want, src in fails:
        w = "n/a" if pd.isna(want) else f"{want:,.4f}"
        print(f"  {tk:6s} {field:20s} got={got:>22s}  want={w:>18s}")
        print(f"         source: {src}")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Check verified-value pins.")
    ap.add_argument("--asof", default=ASOF)
    a = ap.parse_args()
    return check(a.asof)


if __name__ == "__main__":
    sys.exit(main())
