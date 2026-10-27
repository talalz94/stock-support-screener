#!/usr/bin/env python
"""Is there a COMBINED insider score worth showing? A walk-forward test, fixed before the test half is read.

DESIGN (written 2026-10-27, before any test-period number was computed):
  events    data/_insider_events.parquet (insider_study.py): every (ticker, filing day)
            with director/officer buying, its 30-day window features as known that day,
            and its excess returns against the same-day liquidity fifth.
  flags     the four the study measured one by one, each needing $10K+ bought in 30
            days as the study's signals did: SMALL (bottom two liquidity fifths), RARE
            (a buyer's first purchase in this company in 2+ years), CEO/CFO, and TRACK
            (the best buyer's point-in-time record > 0).
  family    score = how many of a subset's flags are present, over every non-empty
            subset of the four (15); a rule is "score >= k", k = 1..size: 32 rules.
  choose    on TRAIN only: entries from 2016-09-01 whose 60 sessions had ENDED by
            2021-07-01 (an embargo, so no train outcome is realised inside the test
            span). The rule with the highest t at 60 sessions among rules with >= 300
            train episodes. Ties: fewer flags, then smaller k.
  test      that ONE rule on entries 2021-07-01 .. today. PASS = test t >= 2 and mean
            excess > 0 at 60 sessions. Nothing else read here decides anything; the
            other rules' test numbers are written for the record, labelled as such.
  machinery the study's own: one episode per ticker per 60 sessions, t across calendar
            quarters (event_returns).
  NOT BLIND, said plainly: phase 2 had already printed each SINGLE flag's two halves
  (small and track looked strongest late), and the four flags were picked because they
  passed on the full sample. What no one had seen is any COMBINATION's test half.
  BASELINE, added 2026-10-27 AFTER the first run (reported, never part of the PASS
  rule): the same split for ANY $10K+ director/officer purchase. The first run showed
  the test half was kind to nearly every rule (28 of 32 at t >= 2), so a PASS alone
  cannot say whether the chosen flags add anything over plain insider buying. The
  file now says so in `vs_baseline`, and every page that shows the score repeats it.
Writes data/_insider_score_wf.json; the `insider` score module emits `insider_score`
(the chosen rule's flag count) ONLY if the file says PASS.

Run: python insider_score_wf.py
"""
from __future__ import annotations

import itertools
import json
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import calendar_us
import config
import event_returns as ER
import insider_study as IS

OUT = config.DATA / "_insider_score_wf.json"
FLAGS = ("small", "rare", "ceo_cfo", "track")
SPLIT = "2021-07-01"
H = 60
MIN_TRAIN_EPISODES = 300
PASS_T = 2.0


class _Sessions:
    """What ER.first_in_window needs from a Panel (session positions), without the
    0.5 GB of prices a Panel carries."""

    def __init__(self):
        self.dates = list(calendar_us.all_sessions())
        self.pos = {d: i for i, d in enumerate(self.dates)}


def flag_frame(ev: pd.DataFrame) -> pd.DataFrame:
    big = ev.value_30 >= 1e4
    return pd.DataFrame({
        "small": (ev.bucket <= 1) & big,
        "rare": ev.rare.astype(bool) & big,
        "ceo_cfo": ev.ceo_cfo.astype(bool) & big,
        "track": (ev.best_track > 0) & big,
    }, index=ev.index)


def rules() -> list[tuple[tuple[str, ...], int]]:
    out = []
    for n in range(1, len(FLAGS) + 1):
        for sub in itertools.combinations(FLAGS, n):
            for k in range(1, n + 1):
                out.append((sub, k))
    return out


def name(sub: tuple[str, ...], k: int) -> str:
    return f"{k}+ of {{{', '.join(sub)}}}" if k < len(sub) else " & ".join(sub)


def run(verbose: bool = True) -> dict:
    ev = pd.read_parquet(IS.OUT_EVENTS)
    fl = flag_frame(ev)
    ses = _Sessions()
    end_of = {d: ses.dates[min(i + H, len(ses.dates) - 1)] for d, i in ses.pos.items()}
    ev["_end"] = ev.entry.map(end_of)
    rows = []
    for sub, k in rules():
        pick = fl[list(sub)].sum(axis=1) >= k
        eps = ER.first_in_window(ev[pick], IS.GAP, ses)
        tr = eps[(eps.entry >= "2016-09-01") & (eps._end < SPLIT)]
        te = eps[eps.entry >= SPLIT]
        rows.append({"rule": name(sub, k), "flags": list(sub), "k": k,
                     "train": ER.summarize(tr, H, name(sub, k)), "test": ER.summarize(te, H, name(sub, k)),
                     "test_20": ER.summarize(te, 20, name(sub, k)), "test_40": ER.summarize(te, 40, name(sub, k))})
    base_eps = ER.first_in_window(ev[ev.value_30 >= 1e4], IS.GAP, ses)
    base = {"rule": "any $10K+ purchase (baseline)",
            "train": ER.summarize(base_eps[(base_eps.entry >= "2016-09-01") & (base_eps._end < SPLIT)], H, "baseline"),
            "test": ER.summarize(base_eps[base_eps.entry >= SPLIT], H, "baseline")}
    elig = [r for r in rows if r["train"]["episodes"] >= MIN_TRAIN_EPISODES and np.isfinite(r["train"]["t"])]
    elig.sort(key=lambda r: (-r["train"]["t"], len(r["flags"]), r["k"]))
    chosen = elig[0] if elig else None
    passed = bool(chosen and np.isfinite(chosen["test"]["t"]) and chosen["test"]["t"] >= PASS_T
                  and chosen["test"]["mean_pp"] > 0)
    out = {"measured_on": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "design": __doc__.split("Writes")[0].strip(), "split": SPLIT, "horizon": H,
           "min_train_episodes": MIN_TRAIN_EPISODES, "rules_tried": len(rows),
           "chosen": None if chosen is None else {k: chosen[k] for k in ("rule", "flags", "k", "train", "test",
                                                                          "test_20", "test_40")},
           "verdict": "PASS" if passed else "FAIL",
           "baseline": base,
           "test_rules_at_t2": int(sum(1 for r in rows if np.isfinite(r["test"]["t"]) and r["test"]["t"] >= PASS_T)),
           "test_median_t": float(np.nanmedian([r["test"]["t"] for r in rows])),
           "vs_baseline": None if chosen is None else {
               "test_mean_pp_diff": round(chosen["test"]["mean_pp"] - base["test"]["mean_pp"], 2),
               "test_t_chosen": chosen["test"]["t"], "test_t_baseline": base["test"]["t"],
               "reading": ("adds to plain insider buying in the test half"
                           if chosen["test"]["t"] > base["test"]["t"] and chosen["test"]["mean_pp"] > base["test"]["mean_pp"]
                           else "NOT shown to add to plain insider buying in the test half")},
           "train_table": [{"rule": r["rule"], **{f"train_{x}": r["train"].get(x) for x in
                                                  ("episodes", "mean_pp", "median_pp", "beat_pool_pct", "t")}}
                           for r in rows],
           "not_used_for_the_decision": [{"rule": r["rule"], "test_episodes": r["test"]["episodes"],
                                          "test_mean_pp": r["test"]["mean_pp"], "test_t": r["test"]["t"]}
                                         for r in rows]}
    OUT.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    if verbose:
        pd.set_option("display.width", 200)
        print(pd.DataFrame(out["train_table"]).sort_values("train_t", ascending=False).to_string(index=False))
        if chosen:
            print(f"\nCHOSEN on train: {chosen['rule']}  train t {chosen['train']['t']}  "
                  f"({chosen['train']['episodes']} episodes)")
            print(f"TEST (2021-07..): {chosen['test']}")
        print(f"BASELINE test: {base['test']}")
        print(f"VERDICT: {out['verdict']}  vs baseline: {out['vs_baseline']}")
    return out


def read() -> dict | None:
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def passed() -> tuple[bool, list[str]]:
    """(PASS?, the chosen rule's flags) from the file; (False, []) if absent."""
    r = read()
    if not r or r.get("verdict") != "PASS" or not r.get("chosen"):
        return False, []
    return True, list(r["chosen"]["flags"])


if __name__ == "__main__":
    run()
    sys.exit(0)
