# archive/ — retired one-off scripts

Nothing here is scheduled or imported. These were rescue chains written to fix a
specific problem once; they are kept because each records a **measured finding**
worth not re-deriving, and because a deleted script whose scheduled task keeps
firing is exactly how `run_refetch.py` silently did nothing for five days.

**The live daily pipeline is `orchestrator.py` and nothing else.** One scheduled
task (`Screener-Orchestrator`) runs it. If you are looking for "the job", that
is the job.

| script | what it was for | why it is retired |
|---|---|---|
| `fix_all.py` | history rebuild → re-measure, ~18 h | Superseded by `fix_data.py`, then by the orchestrator's own `provider` step. |
| `fix_data.py` | refetch → rescore → pages → verify, 2026-08-13 | Its useful stages are now orchestrator steps. Its `history` stage ran 10 h and rebuilt **one** session — see the warning below. |
| `night2.py` | second unattended chain: mktcap backfill, dip/combo rebuild, study | One-off. The mktcap backfill it performed is done. |
| `overnight.py` | first catch-up chain | One-off, superseded twice over. |
| `remeasure.py` | re-run study/walk-forward/pages after a data fix | Only useful immediately after a full history rebuild. |
| `sentiment_rebuild.py` | rebuild the sentiment series with recency weighting | One-off migration; the weighting is now the default. |
| `migrate_fund_rank.py` | rename `quality_rank` → `fund_rank` everywhere | Migration completed 2026-08-09. `migrate_metric.py` (still at top level) is the general version. |
| `rebuild_profiles.py` | bulk profile page rebuild | The `profiles` orchestrator step does this, and `serve.py` builds any missing page on demand. |

## The one trap worth knowing about

`rebuild_history.py` (still at top level, still the only full-history rebuild)
**resumes from `data/_rebuild_state.json`**, which lists every session a previous
run completed. On 2026-08-13 that made it run for 10 hours and rebuild exactly
one fundamental session, because all 183 were already marked done — by the run
that used the buggy code the rebuild was meant to fix.

It needs `--fresh` to mean anything after a definition change. And at the
~94 min/session measured on the enlarged fact store, measure the real
per-session cost on two or three sessions before committing to a full run.

Its `facts_refresh` stage also duplicates `refetch.py` and spent 500 minutes
re-fetching 519 companies before failing with 119 errors. Prefer `refetch.py`.
