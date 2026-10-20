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

    # --- SOFI: the first bank-like pin. Its fee line ($610M TTM) used to stand in
    # for revenue; RevenuesNetOfInterestExpense is the total (2026-10-14).
    ("SOFI", "revenue_ttm", 4_305_700_000, 0.002,
     "FY25 3,613.4M (10-K 2026-02-17) + H1-26 2,319.0M - H1-25 1,626.7M "
     "(10-Q 2026-08-06), RevenuesNetOfInterestExpense"),
    ("SOFI", "net_income_ttm", 636_200_000, 0.002,
     "FY25 481.3M + H1-26 323.3M - H1-25 168.4M (same filings)"),

    # --- TRADITIONAL BANKS: revenue = net interest income + noninterest income
    # (fund_metrics.derive). Both halves pinned; legs from the filings (2026-10-20).
    ("FITB", "nii_ttm", 7_199_000_000, 0.002,
     "FY25 5,982M (10-K 2026-02-24) + H1-26 4,149M - H1-25 2,932M"),
    ("FITB", "nonint_income_ttm", 3_545_000_000, 0.002,
     "FY25 3,035M + H1-26 1,954M - H1-25 1,444M"),
    ("MTB", "nonint_income_ttm", 2_877_000_000, 0.002,
     "FY25 2,742M (10-K 2026-02-18) + H1-26 1,429M - H1-25 1,294M"),

    # --- A RESTATEMENT MUST BEAT ITS ORIGINAL (2026-10-14). AD restated 2025 for
    # the wireless sale; with the originals winning its Q4 derived to -$1,691M and
    # revenue_ttm read -$1,538M.
    ("AD", "revenue_ttm", 213_530_000, 0.002,
     "FY25 162.961M (10-K 2026-02-20) + H1-26 106.082M - H1-25 RESTATED 55.513M "
     "(10-Q 2026-08-07; the original H1-25 was 1,807M)"),
    # ...and a 10-Q's stray twelve-month row must not beat the 10-K's year: FIX's
    # Q1-2026 10-Q carries $1,831M for 2025-12-31 beside the 10-K's $9,101.6M.
    ("FIX", "revenue_ttm", 11_228_000_000, 0.002,
     "FY25 9,101.6M (10-K 2026-02-19) + H1-26 6,131.0M - H1-25 4,004.6M "
     "(10-Q 2026-07-23)"),

    # --- large caps: the sanity floor
    ("AAPL", "eps_diluted_ttm", 8.72, 0.02, "web ~8.7"),
    # Was 3.18 ("web ~3.18"): the TTM to Q1, pinned while KO's Q2 10-Q (filed
    # 2026-07-29) was missing from the store -- SEC's companyfacts API never
    # served it and the 2026q3 bulk set only arrived 2026-10-10. Corrected
    # 2026-10-11 from the filings themselves.
    ("KO", "eps_diluted_ttm", 3.33, 0.02,
     "FY25 3.04 (10-K) + H1-26 1.94 - H1-25 1.65 (10-Q filed 2026-07-29); "
     "= 0.86 + 0.53 + 0.91 + 1.03 by quarter"),
    ("NVDA", "eps_diluted_ttm", 6.53, 0.03, "internally consistent"),
    ("MU", "eps_diluted_ttm", 44.24, 0.03, "internally consistent"),
    ("CACI", "eps_diluted_ttm", 24.16, 0.03, "internally consistent"),
    ("SCCO", "eps_diluted_ttm", 6.84, 0.03, "internally consistent"),
    ("HOOD", "eps_diluted_ttm", 2.26, 0.03, "internally consistent"),
    ("MRNA", "eps_diluted_ttm", -7.98, 0.03, "internally consistent"),
    # --- 52/53-week calendars: the roll-forward's YTD and one-year tests used
    # to be "within 20 days" / "350-380 days", which a 16-week Q1 and mid-
    # month period ends fail (2026-10-12; see fundamentals.is_ytd_stub).
    ("KR", "eps_diluted_ttm", 1.71, 0.02,
     "FY25 1.54 (10-K 0001104659-26-037723) + Q1-26 1.46 - Q1-25 1.29 "
     "(10-Q 0001104659-26-078236); Q1 is 16 weeks"),
]

# Pins that only exist at a LATER date than ASOF: (asof, ticker, field,
# expected, rel_tol, source). Same rules as PINS.
DATED_PINS: list[tuple[str, str, str, float, float, str]] = [
    ("2026-10-09", "PEP", "eps_diluted_ttm", 7.95, 0.02,
     "FY25 6.00 (10-K 0000077476-26-000007) + Q3-26 YTD 6.10 - Q3-25 YTD 4.15 "
     "(10-Q 0000077476-26-000048, filed 2026-10-08); by quarter "
     "1.85 + 1.70 + 2.18 + 2.23 = 7.96. Was stuck at 6.37 (the Q1 roll)"),
    ("2026-10-09", "KR", "eps_diluted_ttm", 1.85, 0.02,
     "FY25 1.54 + H1-26 2.51 - H1-25 2.20 (10-Q 0001104659-26-108926, filed "
     "2026-09-18); H1 ends 08-15 and 08-16, rounded to 07-31 and 08-31"),
]

# Share counts that must stay DROPPED -- the filers contradict themselves.
MUST_BE_NULL: list[tuple[str, str, str]] = [
    ("ALMU", "shares_diluted_ttm",
     "10-Q tags 1.735437e10 while CommonStockSharesOutstanding is 1.811355e7"),
    ("TEM", "shares_diluted_ttm",
     "10-Q tags 182,397 shares for a company with ~180 MILLION"),
    # A period under half a quarter is stored qtrs=0; it must never become a
    # LEVEL figure that fund_metrics falls back to (2026-10-12).
    ("BRR", "net_income",
     "24,618,000 is a 28-day inception stub to 2025-06-30 (10-Q filed "
     "2026-08-13), not a year's income"),
    ("BSX", "buybacks",
     "two one-day $2.0B accelerated repurchases (10-Q filed 2026-08-03) are "
     "events, not a year of buybacks"),
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

    for when in sorted({p[0] for p in DATED_PINS}):
        here = [p for p in DATED_PINS if p[0] == when]
        g = FD.facts_asof(when, sorted({p[1] for p in here}))
        g = g.set_index("ticker") if not g.empty else g
        for _w, tk, field, want, tol, src in here:
            if g.empty or tk not in g.index or field not in g.columns:
                fails.append((tk, field, f"MISSING @{when}", want, src))
                continue
            got = pd.to_numeric(pd.Series([g.at[tk, field]]),
                                errors="coerce").iloc[0]
            if pd.isna(got) or abs(float(got) - want) / max(abs(want), 1e-9) > tol:
                fails.append((tk, field, f"{got:,.4f} @{when}", want, src))

    n = len(PINS) + len(MUST_BE_NULL) + len(DATED_PINS)
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
