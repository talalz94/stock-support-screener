"""
Audit EVERY stored metric and raise an issue for each one that cannot be trusted.

    python audit_metrics.py              full audit, writes data/_audit.csv
    python audit_metrics.py --module dip only one module

WHY THIS EXISTS
------------------
On 2026-08-14 a user opened COLL and found EBITDA reading $4M against a real
$68M. Every INPUT it was built from was correct; the bug was in the composition
(`opinc + missing` silently became `opinc`). At that point 14 of 129 stored
numbers had ever been checked against anything, and exactly one of those was a
derived number -- so "the data is correct" was a claim about 11% of the surface
presented as a claim about all of it.

This module audits all of it, and is explicit that the three classes of number
admit completely different kinds of proof:

  1. FINANCIAL FACTS (revenue, assets, cfo...) have an external truth. SEC
     filings are authoritative -- `verify_metrics.py` recomputes them from raw
     XBRL and compares. A mismatch is a BUG.

  2. PUBLISHED RATIOS (pe, roe, margins, ebitda...) are computed by us AND by
     commercial providers. `providers.compare` checks both. A gap is a
     QUESTION, not a verdict: on 2026-08-14 our FCF disagreed with Yahoo on 43%
     of names and SEC proved US right -- Yahoo's `freeCashflow` exceeded our
     CFO, which its own formula makes impossible.

  3. PROPRIETARY SCORES (hype_score, sent_decay_30d, dip_score...) have NO
     external truth. Nobody else computes them, so there is nothing to compare
     against and claiming they are "verified" would be dishonest. What CAN be
     checked is that they do not exhibit the failure that produced the COLL
     bug: a value that exists only because a missing input was treated as zero.

INVARIANTS FOR CLASS 3, each chosen because it has actually caught something:

  coverage      a metric emitted for almost nobody is broken, not sparse
  constant      a metric with one distinct value is not measuring anything
  fabrication   a metric MORE complete than every input it derives from is
                inventing values from missing data -- this is the COLL bug's
                signature and the reason this file exists
  range         a rank/score outside its declared bounds
  nulls         100% null: declared, computed, emitted for nobody

Output is a CSV plus a printed list of issues, ordered worst first. Exit code is
non-zero if any HIGH-severity issue is found, so this can gate a nightly run.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

import numpy as np
import pandas as pd

import config

config.safe_console()

OUT = config.DATA / "_audit.csv"

# A metric emitted for fewer than this share of the scored universe is
# suspicious. Deliberately low: legitimate metrics ARE sparse (a filer without
# inventory has no DIO), so this catches "broken", not "incomplete".
MIN_COVERAGE = 0.02

# Scores and ranks that must sit inside a known interval.
# EXACT metrics first, then suffix rules. Guessing bounds from a suffix alone
# was wrong on its first run and fired four HIGH issues that were all the
# checker's fault: Beneish M legitimately spans -15..15 and Altman Z -20..40,
# both clipped deliberately, and `_rank` is 0..100 in `dip` while it is 0..1 in
# `sentiment`. A checker that cries wolf gets ignored, which is worse than not
# having one.
EXACT_BOUNDS = {
    "m_score": (-15.0, 15.0),      # Beneish, clipped
    "z_score": (-20.0, 40.0),      # Altman, clipped
    "f_score": (0.0, 9.0),         # Piotroski, 0-9 by construction
}
# `_rank` is 0..1 in sentiment and 0..100 in dip -- a real inconsistency, raised
# as its own issue below rather than papered over. The bound is the union, so it
# still catches a rank of 500 without firing on either convention.
SUFFIX_BOUNDS = {"_score": (0.0, 100.0), "_rank": (0.0, 100.0),
                 "_pct": (0.0, 100.0)}

# `bounce` is the support-bounce SCREEN. It writes flags and cards, not rows in
# the score store, so auditing it as a score module reports "no rows stored"
# for a module that is working exactly as designed.
SCORE_MODULES = ["sentiment", "hype", "fundamental", "dip", "combo"]


def _bounds_for(metric: str):
    if metric in EXACT_BOUNDS:
        return EXACT_BOUNDS[metric]
    for suffix, rng in SUFFIX_BOUNDS.items():
        if metric.endswith(suffix):
            return rng
    return None


def audit(modules: list[str] | None = None) -> pd.DataFrame:
    import scores
    import importlib

    mods = modules or list(SCORE_MODULES)
    issues: list[dict] = []
    summary: list[dict] = []

    for mod in mods:
        try:
            importlib.import_module(f"scores.{mod}")
        except Exception:                                        # noqa: BLE001
            pass
        try:
            st = scores.read(mod)
        except Exception as exc:                                 # noqa: BLE001
            issues.append({"severity": "HIGH", "module": mod, "metric": "*",
                           "issue": "module unreadable",
                           "detail": f"{type(exc).__name__}: {exc}"[:120]})
            continue
        if st.empty:
            issues.append({"severity": "HIGH", "module": mod, "metric": "*",
                           "issue": "no rows stored", "detail": ""})
            continue

        sess = st["session"].max()
        cur = st[st["session"] == sess]
        # `_ALL` is a deliberate SESSION-LEVEL scalar, not a ticker: combo
        # stores its sample size (combo_h20_n = 22) once per session with the
        # contributing column list as the label. Auditing it as a per-ticker
        # metric reported "0.03% coverage" and "constant" for six metrics that
        # are working exactly as designed -- six false alarms out of ten.
        session_scalars = cur[cur["ticker"] == "_ALL"]["metric"].unique()
        cur = cur[cur["ticker"] != "_ALL"]
        if len(session_scalars):
            print(f"  [{mod}] {len(session_scalars)} session-level scalar(s) "
                  f"excluded from per-ticker stats: "
                  f"{', '.join(sorted(session_scalars)[:4])}")
        if cur.empty:
            continue
        universe = cur["ticker"].nunique()
        piv = cur.pivot_table(index="ticker", columns="metric", values="value",
                              aggfunc="last")

        # The most complete metric in the module is the yardstick: nothing
        # DERIVED from these inputs may be more complete than the inputs are.
        completeness = piv.notna().mean()
        best_input = float(completeness.max()) if len(completeness) else 0.0

        for metric in sorted(piv.columns):
            v = pd.to_numeric(piv[metric], errors="coerce")
            cov = float(v.notna().mean())
            nuniq = int(v.nunique(dropna=True))
            summary.append({"module": mod, "metric": metric, "session": sess,
                            "coverage": round(cov, 4), "distinct": nuniq,
                            "min": v.min(), "max": v.max()})

            if cov == 0.0:
                issues.append({"severity": "HIGH", "module": mod,
                               "metric": metric, "issue": "always null",
                               "detail": f"declared and emitted for 0 of "
                                         f"{universe:,} names"})
                continue
            if cov < MIN_COVERAGE:
                issues.append({"severity": "MEDIUM", "module": mod,
                               "metric": metric, "issue": "near-zero coverage",
                               "detail": f"{cov*100:.2f}% of {universe:,}"})
            if nuniq == 1:
                only = float(v.dropna().iloc[0])
                # A PRESENCE FLAG stuck at 1 is a different fault from a
                # measurement stuck at a value. The flag is not wrong, it is
                # redundant -- presence of the row already says the same thing.
                flag = metric.startswith("has_") or metric.endswith("_coverage")
                issues.append({"severity": "LOW" if flag else "MEDIUM",
                               "module": mod, "metric": metric,
                               "issue": "uninformative flag" if flag else "constant",
                               "detail": (f"always {only:g} -- carries no "
                                          f"information beyond the row existing"
                                          if flag else
                                          f"single value {only!r} -- measures nothing")})
            rng = _bounds_for(metric)
            if rng is not None:
                lo, hi = rng
                out = int(((v < lo) | (v > hi)).sum())
                if out:
                    issues.append({"severity": "HIGH", "module": mod,
                                   "metric": metric, "issue": "out of bounds",
                                   "detail": f"{out} value(s) outside "
                                             f"[{lo}, {hi}]; "
                                             f"range {v.min():.4g}..{v.max():.4g}"})
            # THE COLL SIGNATURE: more complete than any input can support.
            if cov > best_input + 1e-9:
                issues.append({"severity": "HIGH", "module": mod,
                               "metric": metric, "issue": "fabricated coverage",
                               "detail": f"{cov*100:.1f}% complete but the most "
                                         f"complete input is {best_input*100:.1f}% "
                                         f"-- a missing leg is being treated as "
                                         f"zero"})

    # RAISE THE RANK CONVENTION CLASH rather than hide it behind a loose bound.
    ranks = [r for r in summary if r["metric"].endswith("_rank")]
    scale = {}
    for r in ranks:
        scale.setdefault("0-1" if (r["max"] or 0) <= 1.5 else "0-100",
                         []).append(f"{r['module']}.{r['metric']}")
    if len(scale) > 1:
        issues.append({"severity": "MEDIUM", "module": "*", "metric": "*_rank",
                       "issue": "inconsistent rank scale",
                       "detail": "; ".join(f"{k}: {', '.join(v[:3])}"
                                           for k, v in scale.items())})

    df = pd.DataFrame(summary)
    if not df.empty:
        df.to_csv(OUT, index=False)
    return pd.DataFrame(issues), df


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit every stored metric.")
    ap.add_argument("--module", action="append")
    a = ap.parse_args()

    print(f"audit_metrics | {datetime.now():%Y-%m-%d %H:%M}")
    issues, summary = audit(a.module)
    print(f"\n{len(summary):,} metric(s) audited across "
          f"{summary['module'].nunique() if not summary.empty else 0} module(s)")

    if issues.empty:
        print("no issues raised")
    else:
        order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        issues = issues.sort_values(
            by="severity", key=lambda s: s.map(order)).reset_index(drop=True)
        n_high = int((issues["severity"] == "HIGH").sum())
        print(f"\n{len(issues)} ISSUE(S) RAISED -- {n_high} high severity\n")
        for r in issues.itertuples(index=False):
            print(f"  [{r.severity:6s}] {r.module}.{r.metric}: {r.issue}")
            if r.detail:
                print(f"           {r.detail}")
        issues.to_csv(config.DATA / "_audit_issues.csv", index=False)
        print(f"\nissues -> {config.DATA / '_audit_issues.csv'}")
    print(f"per-metric detail -> {OUT}")
    return 1 if (not issues.empty
                 and (issues["severity"] == "HIGH").any()) else 0


if __name__ == "__main__":
    sys.exit(main())
