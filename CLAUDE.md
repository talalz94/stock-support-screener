# Working on this project with Claude Code

Claude Code reads this file at the start of every session. It carries the working agreements that
were built up over the project, so they hold on any machine.

## Start here

1. `NEXT_SESSION.md`: the current state and, since 2026-08-13, the dated log ("State at <date>"
   sections, newest first). Its numbers were measured: treat them as fact rather than re-deriving them.
2. `SCORE_MODULES.md`: the map of every module, step and page.
3. `README.md`: operations (the orchestrator, the pages, moving to a new machine).
4. `PROJECT_LOG.md`: dated findings up to 2026-08-08.

When a piece of work is done, add a dated "State at <date>" section to `NEXT_SESSION.md` (newest
first) and update `README.md` / `SCORE_MODULES.md` wherever they describe what changed. That upkeep
is a standing request, not an optional courtesy.

## Hard rules

- `.env` and `data/` are never committed (both are gitignored). Never print or commit a key value.
- **The GitHub repository is public.** Before every commit, scan the added lines for key-shaped
  strings and for personal information (the owner's name, email address, home folder paths).
- Commit with `git commit --only <paths>`. Push only when the user asks.
- No real people's names in committed code. Congressional (House) trade data stays in `data/`.
- Lists and scores are research screens, not investment advice, and the pages say so.
- Never bypass bot protection or a site's terms of use, and never work around a refused permission.
- Never change power or sleep settings, and never ask the user to: the sleep timer changes on its
  own. Resilience belongs in the code (keep-awake during runs, resumable watermarks, the repeating
  05:00 task, the retry cap).
- Do not create scheduled reminders for pending items. At every update, end with the short list of
  what is still pending (kept in `NEXT_SESSION.md`) and ask about the next step.

## How the work is done here

- **Measured, not estimated.** Measure the artifact (rows, file sizes, wall clock). If something
  cannot be measured, say so and give the method and the error band. "No data" must never look
  like "no signal" or like zero, on a page or in a job status.
- **Check what the page shows,** end to end, and name the population behind every pass rate.
- **A result needs a baseline:** compare a new rule with a plain rule on the same split (TRAIN
  before 2022-01-01, TEST after; "kept" means both TRAIN halves and TEST agree in sign and TEST
  |t| >= 2). A pass in a kind test span is not an edge.
- **Provider values first** for the ratios a page displays today; our own arithmetic only where
  no provider publishes. Never write a provider's current value into a historical session.
- **Fundamentals:** several XBRL tags map to one concept with different numbers. Dump
  `(ddate, qtrs, value, tag, rank)` before suspecting a formula, and run `regression_pins.py` after
  any change. A growth metric needs two reports: the period moved AND the value changed (count
  exact zeros).
- **Thorough and bug-free:** the user asked for work with no margin for error. Ask when a decision
  is genuinely theirs; otherwise pick the sensible default and say so.
- **Pages:** uncluttered and consistent, explanations behind an (i), every page usable at phone
  width with no sideways scroll, in light and dark. Prefer the shared pieces in `ui.py` (`page`,
  `nav`, `tabs`, `viewbar`, `info`, `stock_href`).

## Things that bite

- The scheduled orchestrator runs from this working folder, uncommitted edits included. Keep every
  module importable at every point (`python -W error -c "import <module>"`) and commit coherent
  states promptly.
- Do not run heavy tests while the pipeline runs (`data/_orchestrator.lock` exists while it does).
  A starved step and a hung one look the same: measure CPU over an interval before concluding.
- Check a name before creating a module or an orchestrator step (an existing `events.py` was once
  overwritten).
- Claude-app scheduled tasks run only while the app is open. Data work belongs in the orchestrator;
  app tasks are only for what needs Claude itself (`docs/claude_app_tasks.md`).
- Pages are static files: a menu change reaches pages already written through `python ui.py
  --renav` (the nightly `dashboard` step runs it too).

## Checks

```
python status.py --pages          # every page: dead links, nan cells, mojibake
python ui.py --selftest           # the shared UI, contrast, menu refresh
python settings.py --selftest     # and: shortlist.py / settings_page.py / explore.py --selftest
python orchestrator.py --status   # the last run of every step
```
