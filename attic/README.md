# attic — retired one-off scripts

Kept, not deleted, because each one documents a job that actually ran and a
decision that was made. Nothing here is on any schedule and nothing imports it.

Every script in here shared one dangerous shape: a `__main__` block that starts
expensive work with **no argument parsing**, so any flag — including `--help` —
was silently ignored and the job ran anyway. That is not hypothetical; probing
`catchup_scores.py --help` during a planning session started a multi-hour score
backfill and wrote eight sessions before it was noticed.

| script | why it is retired | do this instead |
|---|---|---|
| `refetch_facts.py` | Superseded. This is the script that printed `70/70 quarters ... DONE` after **51 consecutive ConnectionErrors** — no rate limit, no retry, and a summary that counted attempts rather than successes. Its replacement rate-limits at 2.5s, retries with backoff, counts successes and exits non-zero. | `python fundamentals.py --backfill --force --newest-first` |
| `run_refetch.py` | A three-line wrapper around `fundamentals.backfill(force=True, newest_first=True)`. Those are now CLI flags, which is where they belonged. | `python fundamentals.py --backfill --force --newest-first` |
| `rebuild_hype.py` | Hardcoded `scores.catchup("hype", every=21, frm="2016-08-01", rebuild=True)` and the same for dip. `catchup_scores.py` now does this for any module, with per-module data floors and a real `--rebuild` flag. | `python catchup_scores.py --modules hype,dip --rebuild` |

`fundamentals.backfill()` keeps the honest-summary behaviour these lacked:
it counts what SUCCEEDED and exits non-zero if any quarter failed.
