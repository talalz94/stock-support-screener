# Next session — START HERE

`SCORE_MODULES.md` for architecture, `PROJECT_LOG.md` for dated findings.
Blocks marked GENERATED are rewritten by `docs.py` — **do not hand-edit those**.

### 2026-08-29 — THE FIRST FULL RUN WITH ZONES, AND A 10.5-HOUR VALIDATE

Saturday 05:00: **25 ran, 0 skipped, 0 failed**, 47,868s wall (13h18m).

WHAT WORKED. `zones` ran in the chain for the first time -- 81.6s at position 8,
straight after `bounce` -- 7,978 zones over 3,262 names. `fundamental` came in
at **877s (14.6 min)** against 2h20m before the `_q4_rows` rewrite, holding the
11x at full scale. Every backfill gap stayed closed.

WHAT DID NOT. `validate` logged **37,658s -- 10.5 HOURS** against 298s the day
before, and it is what made the run 13h18m.

THE HONEST STATE OF THAT: I do not know where the time went. Measured on the
same data afterwards, every check inside it totals **245s**: audit 213s, ttm
7s, roll 24s, and the network checks were skipped by budget. Windows power
events show **1.1 hours** of modern standby inside validate's window -- real,
but a tenth of the gap. The machine was awake for the other 9.4 hours and the
code cannot account for them.

Rather than invent an explanation, the step now TIMES ITSELF per check and logs
a line like `timing: audit 180s, ttm 5s, roll 20s, screen 3s`, plus a warning
for any single check over 300s. A step that reports only its total cannot say
what it spent. Re-run afterwards: **373.9s**, network checks completed
(sec:see-csv, prov:82%), so nothing is structurally slow.

STILL UNRUN: `powercfg /change standby-timeout-ac 0`. Modern standby is
throttling background work, and 1.1 hours of it landed inside one step.

### AND A GAP WORTH MORE THAN THE MYSTERY

`_step_validate` never ran `validate.py`'s GROUPS. Every group check --
including the zone invariants written the day before specifically to stop
fabricated statistics -- ran ONLY when someone typed `python validate.py` by
hand. They were protecting nothing on a normal night.

The `screen` group (bounce funnel + zone invariants) is now part of the nightly
step and reports `screen:pass`. It measures **3s**, which is why it is the one
group pulled in rather than all twelve -- `--quick` across every group is ~11
minutes and belongs on demand, not in the chain.


## State at 2026-08-29 — A ZONES PAGE, AND THE STUDY THAT CHANGED ITS DESIGN

Reported with three charts (AAON, AMSC, DXYZ) sitting on obvious multi-year
shelves the bounce screen never mentions, and a request: catch the early ones
reaching a major zone, count how many times price bounced there, and rank by how
hard it bounced.

**The ranking that was asked for is the one thing the data refused.** Building it
as specified would have ranked backwards.

### WHAT THE SCREEN WAS ALREADY DOING, AND BINNING

From the stored 2026-08-27 run: AMSC had a NINE-TOUCH level with Q=0.836
discarded at stage 4 for LEVEL_OFF_BASE. DXYZ's machine-found level was 20.93
against a hand-drawn 20.64 -- 1.4% apart. AAON had nothing, having stopped at
stage 2 on RUN_TOO_SMALL before the level code ran. The level work was being
done and thrown away, so `zones.py` reuses `levels.py` untouched rather than
reimplementing anything.

### PHASE 1 -- 325,061 EPISODES, 3,277 TICKERS

    prior touches      n      med r40   hit >=15%
    0             77,071       17.2%      54.8%
    8+             6,129       12.8%      43.7%     Spearman -0.0855

**More touches predicts a SMALLER bounce, monotonically.** It survives inside
every volatility quartile, so it is not an artifact -- but volatility is what
actually drives bounce size (10.8/15.0/18.7/24.7% by quartile, a ~14pp spread
against touch count's ~3pp). **Ranking on raw bounce size is an ATR ranking in
disguise**, which is why `bounce_med_atr` exists and is the sortable one.

The same data says touch count IS predictive -- of drawdown:

    prior touches   med dd40   share breaking >10%
    0                 -9.0%          46.6%
    8+                -6.0%          35.6%

**A well-tested level is a better STOP, not a bigger target.**

### PHASE 2 -- 37,854 POINT-IN-TIME OBSERVATIONS, 12 NON-OVERLAPPING DATES

Point-in-time, eligibility recomputed per date from the bars (not today's panel,
which would be survivorship bias pointing the flattering way), paired per-date
against every eligible name so the market move cancels.

    any zone within 16%    2,712/date   +0.42pp   t=+4.20
    AT (<=2.5%)            1,229/date   +0.46pp   t=+2.59
    AT + 8+ touches          887/date   +0.50pp   t=+1.61   <- the filter HURTS
    NO zone within 16%       442/date   -2.57pp   t=-4.17   <- strongest effect

**The strongest effect is absence, not presence**, six times the size of the
positive signal. Hence `no_support`.

A DESIGN ERROR CAUGHT IN MY OWN STUDY: the first run sampled dates 21 sessions
apart against a 40-bar horizon, so adjacent observations shared ~19 bars of
forward return and every t-stat was inflated by autocorrelation. Re-run on
non-overlapping dates the results held (they strengthened), but the first
version was not entitled to its confidence.

CAVEAT KEPT IN VIEW: "no support within 16%" structurally means the stock has
run far above its last consolidation, so this may be re-deriving "extended
stocks fall back" by an expensive route. Untested, and the first thing to test.

### WHAT SHIPPED

`zones.py` -- levels for every name, no run required, 69s for 3,270 tickers.
`zones_page.py` -- one table, search + 4 band chips + 3 numeric filters, ~8,000
rows client-side. **Every column prints what it measured, under the header
rather than in a tooltip**, because offering a touches filter without saying it
measured negative for upside invites ranking backwards.
`orchestrator` -- `zones` step at position 8, right after `bounce` and before
anything expensive, for the reason the bounce screen taught us.

VERIFIED
  zones selftest      merge, separation, right-edge, magnitude, drawdown, empty/short
  validate            4 new zone checks, each PROVED to fail on an injected defect
  page audit          zones pages 0 literal-null cells
  contrast            light min 4.78, dark min 5.32 (AA 4.5)
  interactions        chips, 3 numeric filters composing, clear, sort blanks-last,
                      show-all 7,983, search
  step                107s via the orchestrator, 1 ran 0 failed
  pins 28/28, report/ui/classify/strategies/watchlist selftests OK

A NAN THAT NEARLY SHIPPED: `str(v or "")` renders float("nan") as the literal
"nan", because nan is TRUTHY. Every one of the 396 no-support rows printed it in
`last_touch` until the browser check caught it.

### 2026-08-29 (later) — THE "no_support" CAVEAT WAS TESTED. THE GUESS WAS WRONG.

The shipped caveat said no-support might just be "extended stocks fall back".
Tested against two extension measures over the same 12 non-overlapping dates:

    mean pct_of_250d_high   no-support 0.617   has-support 0.768
    corr(no_support, pct_of_250d_high) = -0.237

**Backwards.** No-support names are BEATEN DOWN, not extended. Head to head over
the same dates the most extended quartile OUTPERFORMED (+1.72pp, t=+2.89) --
momentum, not mean reversion.

Stratified, the effect is entirely CONDITIONAL:

    by % of 250d high      Q1 -5.32pp t=-5.81 | Q2..Q4 gone
    by ext vs 200d MA      Q1 -3.68pp t=-4.23 | Q2..Q4 gone

It is a falling knife with no floor. Among already-weak names, nothing beneath
the price means what it sounds like; among extended names the same flag is
structural and predicts nothing (t=-0.96 with 2,143 of them in the bucket). So
`pct_hi` now ships as a column AND a filter -- no_support + "<=64% of high"
gives the 112 names the measured effect actually applies to.

A DISTRIBUTION THAT LOOKED LIKE A BUG AND WAS NOT: today's no-support median
pct_hi is 0.826 against phase 3's 0.617. Not a contradiction -- one is a median
and the other a mean, and the no-support distribution is BIMODAL
(10th pct 0.269, 90th 0.978). Names cluster at both ends: fell through
everything, or at a high with support far below. Worth remembering that the
flag means two different things at the two ends.

### AND A REAL DATA BUG THE SAME CHECK EXPOSED

11 names carried a "% of 250d high" under 2%. AIIO read $2.36 against a 250-day
high of $563 -- not a 99.6% decline, an unadjusted reverse split. CISS and FFAI
were among them, and the bounce screen ALREADY rejects both as SUSPECT_SPLIT.
Every one was being labelled NO SUPPORT, the strongest signal on the page, on
the strength of corrupt prices.

`zones.is_suspect` now applies the screen's own `pattern.suspect_split` PLUS a
price-range floor, because the return test alone caught CISS and missed FFAI and
AIIO: it needs one big bar with quiet volume behind it and misses a decay spread
over many bars or sitting outside the window it sees. Below
`DATA_SUSPECT_PCT_HI` (5%) the level history is unusable whether the cause is a
split or a genuine collapse -- levels drawn from prices twenty times higher are
not information.

48 names are now REPORTED as `SUSPECT` rather than dropped or miscounted, and
no-support fell 396 -> 376. Validate asserts a suspect row is never also
no-support, never carries a level, and that nothing below the floor goes
unflagged. Lowest pct_hi among trusted names is now 0.0623.


## State at 2026-08-28 (later) — FUNDAMENTAL WAS 97% ONE PYTHON LOOP

`fundamental` cost 2h20m for a current session and 3-5h for a backfilled one.
PROFILED rather than guessed, 400 tickers:

    facts_asof prior_3y   53.84s  24.8%
    facts_asof prior_q    53.02s  24.4%
    facts_asof cur        51.91s  23.9%
    facts_asof prior_1y   51.68s  23.8%
    _price_inputs          6.69s   3.1%
    FM.compute             0.36s   0.2%

**The four point-in-time queries are 97% of the step**, each ~52s regardless of
date -- so adding `prior_q` and `prior_3y` on 08-23 really did double it. Worth
noting what the profile SAVED me from: `_price_inputs` reads five years of daily
bars for every ticker and `_beta` computes covariance with a per-column
`.apply`, which looks exactly like the bottleneck and is 3%.

### THE HOTSPOT WAS A MERGE WRITTEN AS A NESTED SCAN

    facts_asof                     38.87s
      _ttm                         35.07s  (90%)
        _q4_rows                   33.82s  (87%)
          comp_method_OBJECT_ARRAY 15.65s  x 9,481 calls

`_q4_rows` looped over every annual row (~3,800) and rebuilt a full-frame
boolean mask of `q` on each iteration. `q["ticker"] == r.ticker` on an
object-dtype column is evaluated element by element by pandas -- 9,481 such
comparisons. It also called `q["filed"].max()` INSIDE the loop, 3,808 times,
for a value that never changes.

Rewritten as one merge on (ticker, concept) plus one groupby. `_latest` leaves
exactly one annual row per (ticker, concept), so the merge is 1-to-few and the
groupby reproduces both original conditions exactly: three quarters inside the
annual's trailing twelve months, and none sharing its period end.

### MEASURED, AND PROVED IDENTICAL

Captured the real (q, ann) inputs from a live `facts_asof` and ran the ORIGINAL
implementation and the new one side by side:

    old   25.29s -> 27,611 rows
    new    0.11s -> 27,611 rows      228x
    IDENTICAL: True

End to end on the same 400 tickers, four queries: **210.45s -> 25.31s, 8.3x**.
The step is ~97% those queries, so a session should fall from ~2h20m to roughly
20 minutes, and a backfilled session from 3-5h to well under an hour. That
changes what the 5h `BACKFILL_RUN_CUTOFF_S` permits: several gap sessions per
run instead of one, so the 11 fundamental and 4 hype gaps close in days rather
than weeks.

Verified: regression pins 28/28, with `_ttm` reporting the identical
"dropped 45 / rolled 275" diagnostics as before the change; fundamentals
selftest OK; fund_metrics selftest OK.

### AND ttm_invariants HAD BEEN DEAD

Running it surfaced `KeyError: 'ends_latest'`. `check()` was rewritten to read
production windows and renamed that column to `complete`; the print loop in
`main()` still asked for the old name, so the report died before printing a
single line and had been dead since that rewrite. NOT caused by today's change
-- `ends_latest` appears nowhere in `fundamentals.py`. One-word fix, and the
store is healthy:

    44,557 four-quarter windows across 6,562 tickers
      spans_year  44,536 pass   21 FAIL (0.05%)
      no_overlap  44,557 pass    0 FAIL
      no_gap      44,519 pass   38 FAIL (0.09%)
      complete    44,557 pass    0 FAIL
      OVERALL     44,508 pass   49 FAIL (0.11%)

The 49 failures are odd fiscal calendars -- GFLT/BNC 182-day gaps, AIV and BRID
123-day gaps from fiscal-year changes -- not a systematic fault.


## State at 2026-08-28 — WHY THE BOUNCE SCREEN WAS THE STALEST PAGE ON THE SITE

Reported as "why is the bounce screener not updating every day, I thought the
job runs every day". The job did run every day. Two separate things were wrong.

### 1. BOUNCE TAKES 37 SECONDS AND WAS NINTH IN LINE

`bounce` declares `depends_on=("bars",)` and measures 33-40s. It sat behind
`hype`, which takes three hours or more.

    session 08-21  ->  built 08-24 07:57
    session 08-24  ->  built 08-25 08:07
    session 08-25  ->  built 08-26 21:14   (16h after that run started)
    session 08-26  ->  never built
    session 08-27  ->  never built

It is now SEVENTH, immediately after `sentiment` -- about 90 seconds into the
run. It needs `bars` (29s) and reads `sentiment` (31s) for the card
decoration; nothing else between them was ever required. Verified no dependency
is declared later than the step that needs it.

**Anything that blows up in hype, provider or fundamental now costs a stale
hype score, never a stale bounce screen.** The screen the project is named
after should not be the last thing built.

### 2. ONE OVERRUNNING RUN SILENTLY DELETES THE NEXT DAY

The scheduled task is **`MultipleInstances = IgnoreNew`** with
`ExecutionTimeLimit = PT12H`. When a run is still alive at the next 05:00
trigger, Windows does not queue it and does not warn: it **drops the day
entirely**. No process, no log line, and `NumberOfMissedRuns` stays **0**,
because by the scheduler's accounting nothing was missed.

    08-25  killed at the 12h limit mid-backfill, taking dip, combo and every
           page rebuild down with it
    08-26  still running past 22:30
    08-27  NOT RUN AT ALL -- trigger ignored, nothing logged anywhere

Do not go looking for a lock message for 08-27. `acquire_lock` does log when it
gives up, and that code never executed, because no process was ever started.

### THE ROOT CAUSE IS RUN DURATION, AND THE HOLE WAS ALREADY HALF-KNOWN

`_with_backfill`'s docstring already records this failing on 2026-08-14. The
fix then was "today first" and "the budget is TIME, not a session count". The
residual hole: `BACKFILL_BUDGET_S` is checked BEFORE each session with `spent`
starting at **zero**, so exactly one session always runs however long it takes
-- and one fundamental backfill measured **5.5 hours** on 08-25.

`BACKFILL_RUN_CUTOFF_S` (5h) closes it. No module may START a new backfill
session more than five hours into the run. Five hours leaves the expensive
current-session work intact (hype ~3h, provider ~1.2h, fundamental ~2.3h) and
blocks the fundamental backfill, which begins around eight hours in.

**Backfill is history. A run that eats the next day's run is not a trade worth
making.**

Verified at both sides of the boundary, with a stubbed compute:

    0.5h into run   today scored, 3 sessions backfilled
    4.9h into run   today scored, 3 sessions backfilled
    5.1h into run   today scored, 0 backfilled, reason logged
    8.0h into run   today scored, 0 backfilled, reason logged
    RUN_T0 unset    today scored, 3 backfilled  (no regression)

`today_scored` is True in every case -- the guard must never starve the current
session, which is the mistake the 08-14 fix was itself correcting.

### WHAT IS STILL TRUE AND UNFIXED

`fundamental` costs ~2h20m for a current session and 3-5h for a backfilled one,
roughly double what it should, because `prior_q`/`prior_3y` took `facts_asof`
from 2 calls per session to 4. The cutoff stops that cost from destroying a run;
it does not reduce it. **Cut the cost before paying for any historical
backfill** -- at present a gap closes at one session per day at best, and there
are 11 fundamental and 4 hype sessions outstanding.


## State at 2026-08-24 (evening) — A JOB THAT COULD NOT CONVERGE

`sec_gap` ran 08:37-10:37 and reported `FAILED after 7204.3s: companyfacts
failed for 344 company(ies)`, `ORCH DONE | 0 ran, 0 skipped, 1 failed`.

**The log was wrong about the important part. The data was saved.** 76 quarterly
partitions under `data/fundamentals_cf/` were rewritten during the run.
`backfill_companyfacts` flushes every `CF_BATCH` (200) companies and only
returns at the end, so two hours of fetching landed on disk and the step still
reported that nothing ran.

### THE 344 WERE TRANSIENT, AND THAT WAS CHECKED, NOT ASSUMED

Five of the named failures were retried individually afterwards:

    STNG                    716 facts, 0 failed
    SRAD HAFN BBAR BLSH     761 facts, 0 failed

5 of 5 recovered on a plain retry. Transient means transient: network blips
against SEC over a two-hour run at `CF_WORKERS = 6`.

### THE DEFECT

`backfill_companyfacts` returns `ok = not failed` — **ANY single failure out of
thousands marks the whole run failed** — and `_step_sec_gap` raised on it. The
consequences compound:

  * the step is recorded `0 ran`, so the orchestrator does not count it done
  * the next run redoes ALL of it, not the 344 stragglers
  * that run has a fresh chance to hit one more blip and fail the same way

A weekly two-hour job that can never record success. Nothing was corrupted;
it simply could not converge.

### THE FIX: RETRY, THEN JUDGE BY SHARE

`_step_sec_gap` now retries the failed set ONCE and judges the run by the share
still failing against `config.SEC_GAP_MAX_FAIL` (0.25). **The retry is the part
that converges; the threshold only decides whether to shout.** Above the ceiling
it still raises loudly — that is a real fault (SEC down, blocked user agent),
not a blip.

The detail line now says what happened rather than hiding it:
`N refreshed of M targeted (G with no facts, S stale), F row(s); R recovered on
retry, K still failing (x.x%, under the 25% ceiling)`.

Verified against a stubbed fetcher, six cases, including both boundaries:

    clean run                        OK
    100 fail -> all recovered        OK    100 recovered, 0 still failing
    100 fail -> 20 remain (5%)       OK     80 recovered, 20 still failing
    100 fail -> 0 recovered (25%)    OK    at the ceiling, does not raise
    150 fail -> 0 recovered (37.5%)  RAISED
    everything fails (100%)          RAISED

The retry pass is confirmed to actually fire (`calls=[400, 100]`), which is the
half that makes the job converge — a threshold alone would only have silenced
the error while still leaving 344 names unfetched for another week.

### PROCESS NOTE WORTH KEEPING

The two-hour job was launched WITHOUT first knowing how many targets it had,
because the probe measuring that was still running and got killed to free CPU.
`coverage_gap()` and `stale_names()` each scan the whole facts store and take
minutes. The step's own detail line reports the target count for free — read
that instead of measuring separately.

Also: an unrelated `python -m experiments.phase15_pattern_tuning` (not this
repo) was running throughout. Check what else holds CPU before blaming a job
for being slow — see [[never-test-heavy-while-pipeline-runs]].


## State at 2026-08-24 — THE BOUNCE TAB NOW SHOWS EVERY STOCK IT SCREENED

The page showed 10 flags and a 12-row near-miss list, which made a screen that
examines the whole market look like one that examines a handful of names. It
now shows **all 5,439**, each with where it stopped and why.

### THE STORED RECORD WAS MISSING 88% OF THE RUN

`screen_universe` computed two tiers of reject and returned one. `panel_rej`
was built, had `asof_date` assigned to it on the line above — and then
`return passed, rej_out` dropped it. So the daily file held **669 rows on a day
the screen looked at 5,439**, and the 4,760 names dismissed by the vectorized
prefilter left no trace at all.

Both tiers are now returned, and `--gates-only` and the no-rows path return the
same shape the full run does, so a file written by any path renders identically.

    5,439 in the panel
      NEAR_HIGHS      2,500   within reach of the 250d high - no drawdown to bounce from
      ILLIQUID          941
      SHORT_HISTORY     691
      PENNY             523
      FLAT_RANGE         84
      STALE_DATA         20
      NO_TRADES           1
      -> 679 reached the pattern math -> 669 rejected, 10 flagged

**NEAR_HIGHS is the answer to "why only ten names".** It is not a data gap and
not a fetch that failed: 2,500 stocks are too close to their highs for a
support bounce to be a coherent question. A dismissal is a stated reason.

Cost of storing 8x the rows: **none.** 193 KB before, 193 KB after — the panel
rows are almost all null and zstd dictionary-encodes them away.

### `tier`, BECAUSE THE REJECT CODE CANNOT TELL YOU WHICH PASS REJECTED A NAME

SHORT_HISTORY, PENNY, ILLIQUID and NO_TRADES are declared at stage 0 AND run in
the panel prefilter. 20 names pass the panel test and fail the same test in the
pattern math on fresher per-ticker data. The code alone is ambiguous, so rows
carry `tier` = `panel` | `pattern`. Files written before the column existed are
read back by testing `run_x` for null, which is what reaching the pattern math
actually implies.

### THE REAL BUG: `_blank()` SEEDS EVERY PATTERN METRIC WITH 0.0

`screen._blank()` initialises `score`, `run_x`, `dd_from_peak`,
`retrace_of_run` and `touches_prior` to `0.0`, and every early return keeps the
seed. A name rejected at stage 1 therefore carries `score 0.0` that was never
computed — and displayed as `0.00` it sorts as *measured, and the worst on the
page* rather than *never measured*. **639 of 669 pattern rows had a fabricated
score.** Fifth place this project has hit not-reported-is-not-zero.

A metric is real only if the math reached the line that assigns it:

| field | assigned before | real only if |
|---|---|---|
| `run_x` | stage-2 gate | stage >= 2 |
| `dd_from_peak`, `retrace_of_run` | stage-3 gate | stage >= 3 |
| `touches_prior` | stage-5 gate | stage >= 4 |
| `score` | after stage 5 passes | stage >= 6 |

Result: 40 real scores (10 flags + 30 stage-6), **0 exact zeros**. Everything
else renders as a dash.

**How the threshold error was caught, because the method matters more than the
fix:** `dd_from_peak` was first assigned 2, and AAON then showed a 2.06x run
with a 0.0 drawdown while trading 47% below its high. Those cannot both be
true. `dd_from_peak` comes from `pattern.retrace_metrics`, which runs *after*
the stage-2 gate. The cross-check that found it is now a standing assertion in
`report.universe_invariants`: **no row may have `dd_from_peak == 0.0` while
`pct_of_250d_high < 0.9`.** Run `python report.py --selftest`; it also runs
nightly as validate's `screen` group, which additionally asserts the stored
run covers the whole panel. Verified to FAIL (451 rows) when the stage-2 bug
is reintroduced -- a check that cannot fail is not a check.
`touches_prior` is additionally withheld on NO_LEVEL_NEAR_LOW, where no level
was selected for a touch to be counted against.

### THE PAGE

One inlined JSON array, rendered client-side — the only place the page does
this, and for the opposite reason the cards blob was removed: at 5,400 rows the
JSON is ~429 KB against ~1.9 MB for the same table server-rendered, and sorting
a numeric array beats re-sorting 5,400 DOM nodes. Page is 550 KB total.

Sort any column (blanks last in **both** directions, ties break on score then
ticker), filter by outcome or by any of 27 stop reasons, search tickers, 300
rows rendered until "show all". Verified in-browser, not by grepping the HTML:
search `co` -> 88 matches COIN first, NEAR_HIGHS chip -> 2,500 all matching,
show-all -> 5,439 rows, sticky headers, both themes.

### NOT DONE, AND WHY

**No backfill was run.** The measured cost for Jun 1 - Aug 21 is ~170 h (~7
days continuous): `fundamental` 40 missing sessions x 185 min, `hype` 35 x 78
min, the rest ~1 h combined. It buys **measurement history only** — today's
screening is already complete, and bounce needs no ticker backfill at all.
Worth knowing before paying for it: adding `prior_q`/`prior_3y` on 2026-08-23
doubled `facts_asof` calls per session from 2 to 4, which is why a backfilled
`fundamental` session now costs 185 min instead of ~90. **Cut that cost before
backfilling, not after.**


## State at 2026-08-23 — THE SCREENER BECAME A SCREENER

Three things landed: the metrics strategies need, a strategy template, and a
watchlist that drives verification rather than decorating a page.

### THE CONTRACT, stated once and enforced in code

**A stock missing any input a strategy names is NOT LISTED for that strategy.**
Never imputed, never renormalised over the inputs that happen to exist.

Renormalising would let a name scored on one of three inputs sit beside one
scored on three and look comparable. `strategies.selftest` asserts the rule, so
a later refactor cannot quietly reintroduce it.

It earned its keep within minutes: three strategies read `0 of 3,506` instead
of producing plausible-but-wrong rankings, which is how I found that
`explore.collect` only fetched DISPLAYED metrics and never loaded `gpoa` or
`interest_cover`.

### METRICS: 32 -> 40, growth pillar 5 -> 13

Growth was the thinnest pillar and the one most strategies key on; one of its
five members was price momentum.

| added | why |
|---|---|
| `rev_growth_q`, `eps_growth_q` | sequential growth, TTM vs TTM one quarter back |
| `rev_cagr_3y`, `eps_cagr_3y` | a trend, where one YoY figure is one comparison |
| `gross_margin_chg`, `op_margin_chg` | margin DIRECTION, in percentage points |
| `ebitda_growth`, `book_growth` | `combo.THEMES["growth"]` listed both and NEITHER existed -- a dead reference reading as configured |

QoQ is TTM-over-TTM, not raw Q vs Q-1: a TTM sum spans four quarters so
seasonality cancels by construction. All eight are scale-free, so non-USD
filers keep them.

### TWO GUARDS, and the second exists because I checked the first

A growth rate needs two different reports. Unguarded, a filer that published
nothing since the prior frame yields exactly `0.0` -- a fabricated number that
ranks mid-pack, which is worse than a missing one. 19% of names last filed more
than 91 days ago.

    guard 1   the PERIOD moved, bounded both sides so a two-quarter jump is
              not relabelled as one quarter's growth
    guard 2   the VALUE changed too

**Guard 1 alone was not enough.** `last_ddate` is the newest period across ALL
concepts, so a filer can advance on one line item while the metric in hand
still comes from the same stale annual. Six names passed guard 1 and still
produced an exact `0.0` with byte-identical TTM revenue on both sides -- CBIO
11,883,000, COLB 177,000,000, NAVI 271,000,000 and three more. Identical TTM
revenue to the dollar across a quarter, six times in 170, is one report read
twice.

    exact zeros across all ten growth metrics: 6 -> 0

The pre-existing YoY metrics had no guard of any kind. They now route through
the same `_growth()` helper.

### `strategies.py` — the template

A strategy is DATA: name, inputs, directions, weights. Adding one is appending
an entry; tabs, columns, coverage counts and checks all derive from the
registry. One engine (`rank`), two callers (the page, and anything that wants
to measure it).

Explore gained a **strategy rail on the right**, **ANDed multi-filters**
(add/remove rows, persisted to localStorage so a nightly rebuild does not wipe
a screen you set up), and the new growth columns.

**A strategy is a metric column.** That is the whole simplification -- sorting,
filtering, the column chooser and the session picker work on it with no new
table code.

Each tab states its own coverage, because a strategy that ranks a quarter of
the universe must say so BEFORE anyone reads its top ten.

**Coverage falls as a strategy adds inputs.** `quality` needs all four of roic,
gpoa, f_score and interest_cover and ranks 950 of 3,286; `value` ranks 1,018.
That is the direct cost of the all-or-nothing rule and it makes strategy design
partly a coverage decision. Sanity check: `quality`'s top names are IDXX, NATR,
TPR, APP, GIC, FICO, RL, AAPL -- what a ROIC and gross-profitability screen
should produce.

### THE WATCHLIST DRIVES VERIFICATION — this is the point of it

`data/_watchlist.json`, not localStorage, because a browser-local list is
invisible to everything else.

`validate` samples 60 rotating names a night out of ~3,500, so any one name
waits weeks for its turn. **Starred names are checked EVERY run**, PREPENDED to
the sample so if the budget cuts the network checks short it is the random tail
that drops, not the names that were asked for.

Three ways in, one source of truth: the star in Explore, `python watchlist.py
--add NVDA,HZO`, or the page adopting the server's copy on load. Served, it
persists through `/api/watchlist`; opened as a plain file it falls back to
localStorage and the star still works.

Deliberately NOT a portfolio tracker -- no notes, targets or position sizes.
Unknown tickers are REPORTED, not dropped: a delisted name you still watch is
legitimate.

### THE STRATEGIES WERE MEASURED. NEITHER PASSES.

Bar stated BEFORE running, the same one `combo.admitted` applies to any metric:
`|t| >= 2.0` AND `|IC| > |IC_random|`.

    73 sessions, 2024-01-30 .. 2026-08-21

    Value    ~871 names   IC -0.0006 (t=-0.01) h=20   IC -0.0097 (t=-0.13) h=60
    Quality  ~949 names   IC +0.0186 (t=+0.37) h=20   hit 61.7%

**Value is indistinguishable from a random ranking of the same stocks.**
**Quality beats its random control (+0.0186 vs -0.0017) and has a 61.7% hit
rate -- and t=0.37 against a bar of 2.0 means that hit rate is noise landing
above half, not evidence.** A hit rate without a t-stat is the same mistake as
the bounce screen's +2.16% that turned out to be +1.60% for a random pick from
the same pool.

`Growth` and `Quality growth` are NOT MEASURABLE: `rev_growth_q` and
`gross_margin_chg` were added 2026-08-23 and have zero sessions of history.
There is no past to measure over, and running the test anyway would produce a
number that looks like evidence and is not.

WHAT WAS DONE ABOUT IT. `Strategy.evidence` carries the verdict and the rail
prints it ON the button -- `no edge measured` / `unmeasured` -- not in a
tooltip, plus a note under the rail. Hiding it behind a hover is how four
unproven models come to look like four working ones.

**Read the strategies as a way to FIND CANDIDATES, not as a reason to buy.**
That is what the evidence supports and what the page now says.

To re-measure after history accrues: `/tmp/measure.py` pattern -- compute the
strategy rank per stored session, feed `(date, ticker, value)` to
`factor_lab.evaluate`. No storage module needed, because a strategy is a
re-rank of metrics already on disk.

### A SCHEDULING TRAP WORTH KNOWING

`fundamental` skips when it has already run for the current `asof`. On a
weekend the last closed session does not move, so new metrics added on a
Saturday do not reach the store until the next trading session is scored --
two days later. Force it with `orchestrator.py --force --step fundamental`.

And when chaining a job to wait for the daily run: `pgrep -f "pythonw.*orchestrator"`
does NOT match on Windows. A wait built on it fires immediately, the forced run
hits `ANOTHER RUN HOLDS THE LOCK` and exits having done nothing -- silently,
because that is a normal message. Check with `Get-CimInstance Win32_Process`
and read the log to confirm the work actually happened.

### NEXT

1. `growth` and `quality_growth` rank 0 until `fundamental` next runs and writes
   the new metrics. Stated on the tab rather than hidden.
2. Measure the strategies: `factor_lab --module ...`, bar stated first --
   |t| >= 2 and beat a random control, else the tab says "unmeasured".
3. `sue` (earnings surprise) is implemented at `fund_metrics.py:396` and still
   unregistered; it needs a per-ticker EPS panel, a different data path.
4. The level/bounce factor: when `scores/level.py` emits distance-to-support for
   ALL stocks, it becomes an ordinary metric any strategy can name.

## State at 2026-08-22 08:40 — THE CHECKERS WERE THE BUG, FOUR TIMES

Read `regression_pins.py` before changing anything in `fundamentals`. It pins 28
figures verified against filings or issuer press releases, each with its source,
and it is the reason today's work did not ship two regressions.

### ONE STRUCTURAL CAUSE BEHIND FOUR SEPARATE BUGS

Several XBRL tags map to one concept and they carry DIFFERENT numbers. Every
place that picks a leg must pick a tag, and four places did not:

| where | symptom |
|---|---|
| proxy vs statement definition | EE net income $175.3M vs a real ~$47M |
| `check_rollforward` ignored rank | SRZN capex called a 40% failure; production was right |
| `_ttm` roll mixed tags | HL published 784,448,000 from a rank-0 annual + rank-1 stubs; same-tag gives 734,269,000 |
| near-duplicate period ends | KR tagged Q2 at 2025-08-16 AND 2025-08-31 |

**If a future bug looks like "our number disagrees with the obvious
arithmetic", check the TAGS first.** It has been the cause four times.

### THE RULE THAT EMERGED, and the two wrong turns before it

All three legs of a roll must come from the SAME tag. But "always prefer
rank 0" is ALSO wrong:

* HL — rank 0 has a complete set, so use it (734,269,000, not the mixed
  784,448,000).
* LBRDA — rank 0 stops in mid-2025, so its newest annual is 2024-12-31 and it
  yields a roll ending 2025-06-30. Rank 1 runs to 2026. Collapsing to rank 0
  first threw the current window away and left a bare annual eight months
  stale: -327M against a correct -389M.

So: every tag with a complete set keeps its own candidate, and the existing
ends-later rule chooses between them. **Rank is a TIE-BREAK between windows
ending on the same day, never an override.**

### NEAR-DUPLICATE PERIOD ENDS: keep the BEST-TAGGED end, not the earliest

KR tags Q2 net income at both 2025-08-16 and 2025-08-31, and Q3 at both
2025-11-08 and 2025-10-31. Keeping the later end left a 69-day gap; keeping the
earlier left a 76-day gap. Both malformed, because in each pair one date is
genuine and one is a mis-tag and **they point in opposite directions**.

What separates them is which tags appear: the real fiscal ends carry
`NetIncomeLoss` (rank 0) plus two more, the mis-tags carry only `ProfitLoss`.
A filer tags its primary concept on the period it actually closed. The collapse
now keeps the lowest-ranked end in each cluster, and KR's `net_income` window
finally matches its `revenue` window.

`NEAR_PERIOD_DAYS` 10 -> 16, since KR's duplicates sit 15 days apart. Still far
below QUARTER_MIN (80), so it cannot merge two genuinely distinct periods.

### ttm_invariants NOW CHECKS PRODUCTION INSTEAD OF ITSELF

`check()` rebuilt windows from raw `qtrs==1` rows and passed **3 of 127**. Its
premise did not hold: no 10-K reports fiscal Q4 separately, so every filer shows
a ~182-day hole once a year. `_ttm` has always RECORDED the window it summed;
`fundamentals.ttm_windows()` now exposes it and `check()` validates that.

    was      3/127 (2.4%)
    now    268/268 on a random 40-name sample, 54/54 large caps

A NEGATIVE CONTROL confirms it still fails an HD-style 644-day window, a
three-quarter window and one with skipped quarters. **100% means the windows
are clean, not that the check is asleep.** Now wired into daily `validate` and
counted.

`check_rollforward` had three defects, all making it cry wolf: it ignored alias
rank, it applied the roll identity to values produced by a 4q sum or an annual
(HTLD, SMPL), and it never received the YTD-stub guard `_ttm` got (NVR).
`QUARTER_MAX` 100 -> 125, because KR's retail 4-4-5 calendar has a SIXTEEN-week
Q1 (revenue 45.118B against ~33.9B for the twelve-week quarters).

### EPS COMES FROM THE PROVIDER NOW

`eps_diluted_ttm` was in neither the registry nor the metrics frame, so
`overlay` could never reach it -- the one displayed ratio the provider layer
could not cover. Two filers make it necessary and NEITHER is fixable by a
refetch:

* **CMP** did not tag annual diluted EPS in its FY2025 10-K at all. SEC's
  companyfacts has `NetIncomeLoss` (fp=FY, -79,800,000) and ZERO
  `EarningsPerShareDiluted` rows at that period end. Our EPS rolled to a window
  ending 2025-06-30 beside a 2026-06-30 net income and read -2.90 against a
  reported +0.17. With the overlay: **0.163**.
* **NCDL** stopped tagging `EarningsPerShareDiluted` after 2025-12-31. Its BDC
  replacement, `InvestmentCompanyInvestmentIncomeLossFromOperationsPerShare`,
  is NET INVESTMENT income per share -- 1.30 against 1.86 for the same FY2025.
  A different measure; aliasing it would repeat the EE mistake.

STILL OPEN: the profile page's financial-statement TABLE reads the facts layer,
not the overlay, so CMP still shows -2.90 there. Deriving EPS from
net_income/shares would fabricate a figure the filer never reported.

### GRACEFUL DEGRADATION HOLDS — verified, not assumed

BTX has ZERO rows in the fact store. Its page renders **128 KB** with nine chart
blocks, the price section and **92 explicit `—` empty states**. Nothing
collapses, nothing crashes. The principle is already argued in `stock_profile`:
a missing row is the least visible state a page has, so rows render an empty
state rather than disappearing.

### MEASURED

    validate, 60 names      257-300s against a 1200s budget
    validate problems       10 -> 4 -> (window class now 0)
    regression pins         28/28 after every change, including the one that
                            broke LBRDA -- which is how it was caught

### NEXT

1. Diagnose the 3 remaining roll failures.
2. **Re-score and rebuild pages.** Every fix so far is in CODE; the stored
   scores and rendered pages still hold pre-fix values.
3. Teach the financial-statement table to prefer the overlaid EPS.
4. A dedicated pass on multi-tag concepts, given four bugs traced to it.

## State at 2026-08-21 06:00 — SEVEN DATA BUGS FOUND AND FIXED

Every fix below is applied at READ time. No stored partition was rewritten, so
nothing needs a rebuild and nothing was lost. `FD_PROXY_FILTER=0` disables the
proxy handling if it ever needs isolating.

### The one that mattered most: values depended on the BATCH

`facts_asof` returned a different number for a ticker depending on which OTHER
tickers were queried with it. HZO's `net_income_ttm` read 3,986,000 alone and
35,986,000 next to LOPE — identical input rows, identical candidates, different
winner. The pipeline runs `facts_asof` over the whole universe, so the BATCH
answer is the one that reaches the pages, and **no single-ticker check could
ever reproduce it.** Three independent causes:

1. `sort_values` defaulted to quicksort, which is not stable, so a tie on
   `ddate` was broken by array order. All six sorts are mergesort now, with an
   explicit `_prio` tie-break.
2. The roll-forward accepted a `qtrs=1` stub as if it were year-to-date. It is
   YTD only for fiscal Q1; elsewhere it yields the fiscal year with one quarter
   swapped out. The validity filter runs BEFORE the stub is chosen.
3. `_ttm` returned early on an empty `out` and discarded `avg_out`, losing
   share counts for filers whose only duration facts are share counts.

### DEF 14A proxies corrupt net income two different ways

A proxy carries the Item 402(v) pay-versus-performance table, tagged
`NetIncomeLoss` exactly like the income statement, and is filed AFTER the 10-K
so it won the `keep="last"` tie-break.

* **Units.** LOPE's proxy reports THOUSANDS (216,170 for $216.17M) so
  `net_income_ttm` read $8.25M against $219.9M. Now rescaled, but only when the
  filer's own statement rows for the same concept are >=100x larger.
* **Definition.** EE's proxy reports the CONSOLIDATED total ($167.0M) while its
  statements report ATTRIBUTABLE ($39.2M) under a lower-ranked tag, so
  `net_income_ttm` read $175.3M against ~$47M — margins/ROE/ROA on a different
  basis from EPS. A statement row now beats a proxy row AHEAD of alias rank.
  Deprioritised, not dropped: for LOPE the proxy is the only annual row there is.

**Do not "fix" this by dropping proxy rows.** That was tried, and HZO regressed
to $36M — which turned out to be the batch bug, not the drop. HZO has no proxy
rows at all.

### EPS is not additive

Each quarter's weighted-average share count differs, and in a loss quarter
diluted collapses to basic. The 4q path summed four quarters AND added a
derived Q4. HZO: 0.66 - 0.12 - 0.36 + 0.08 = 0.26, against Yahoo 0.16,
Finnhub ~0.18, Simply Wall St 0.18. `NON_ADDITIVE_CONCEPTS` now (a) flips the
ordering so the roll-forward wins and (b) is excluded from `_q4_rows`.

### 52/53-week filers double-counted a quarter

The quarterly dedup keys on EXACT `ddate`, so a filer reporting one fiscal
quarter under two period ends kept both. MRVL: 2025-07-31 and 2025-08-02 both
194,800,000, summing to 2,325.4M against a reported TTM of $2.53B. It also
broke `_q4_rows`, which needs exactly three quarters inside the year and found
five. `NEAR_PERIOD_DAYS` already encoded this rule and `ttm_invariants` always
applied it — **the production path never did.**

### Impossible share counts are dropped

Filers contradict themselves inside one document. ALMU tags
`WeightedAverageNumberOfDilutedSharesOutstanding` = 1.735437e10 while
`CommonStockSharesOutstanding` in the SAME filing is 1.811355e7 — 958x. TEM is
the mirror image at 182,397 for a company with ~180 MILLION shares, and has no
`shares_out` row at all, so the reference falls back to net_income / EPS (both
reported). Dropped, never rescaled: 1000x is the obvious guess and probably
right, but guessing a scale factor invents a number.

### The provider overlay had NEVER run

Zero occurrences of "provider overlay on" in the entire run log. `_is_current`
compared `asof` against a LIVE clock, and the orchestrator fixes `asof` at
05:00 while the fundamental step runs 13-17h later — by then
`last_closed_session()` had rolled to the next day and the comparison lost,
every single day, silently. It now compares against the newest session in the
bar store: what the run ingested, stable mid-run, and correctly False for a
BACKFILLED session. A declined overlay now says so.

### Verification

| check | result |
|---|---|
| batch-dependence | 0 differences across ~1,500 (ticker, field) comparisons |
| EPS vs net_income/shares | 23% -> 11% inconsistent; median error 0.0291 -> 0.0136 |
| `_selftest_rollforward` | PASS (KO/HD/UPS pinned) |
| `fundamentals.selftest` | OK |

Confirmed against filings/third parties: STX eps 13.90 vs $13.90 and revenue
$12.2B; MRVL eps 2.91 vs $2.91 and ni 2,526,700,000 vs $2.53B; RGTI net loss
-238,672,000 vs $238.672M to the dollar; BETA eps -10.01 vs -10.02; LASR three
quarterly legs exact; HHH Q2 eps 2.68 exact; CACI/NVDA/QCOM/MU/NOW/HOOD/SCCO/
MRNA/DK/VIRT/PARR/FCFS all internally consistent.

### KNOWN AND DELIBERATELY NOT FIXED

* **CMP** — its FY2025 annual carries 17 rows including `NetIncomeLoss` and the
  diluted share count but NO `EarningsPerShareDiluted`. The tag is in our map;
  that row is absent from the source we hold. EPS therefore rolls to a window
  ending 2025-06-30 beside a 2026-06-30 net income and reads -2.90 against a
  reported +0.17. Deriving it from net income / shares would fabricate a figure
  the filer never reported. **Fix = targeted companyfacts refetch for CMP.**
* **NCDL** — a BDC that stopped tagging per-share results under our mapped tags
  in 2026, so EPS sits at FY2025. Needs new tags AND a refetch, because the
  store filters to `TAGS` at write time.
* **A share-count change breaks the EPS/net-income cross-check, not the data.**
  BETA (~10x at IPO) and USAR (~2x at SPAC listing) both flag as inconsistent
  and both are correct — BETA's -10.01 matches the reported -10.02. Treat that
  check as a SCREEN, never a verdict.
* **NCDL / ROIV / HRI / CTRI** — noncontrolling interests and preferred
  dividends legitimately separate "net income" from "income available to
  common". Not errors.
* **`ttm_invariants.check()`** — still 3/127 windows passing on untouched code.
  Its premise does not hold for SEC bulk data: no 10-K reports fiscal Q4
  separately, so every filer shows a 182-day gap once a year. Deliberately NOT
  wired into the daily `validate` step. **Fix = read the `_window` that `_ttm`
  already records instead of re-deriving it from raw rows.**
* **No us-gaap at all:** ARM, IQMX, FUTU, DAO, CBRS, SPCX, BTX. Foreign private
  issuers (20-F/40-F) and recent listings. NEXA, ERO and BTDR have partial data
  ending 2025-12-31.

### NEXT STEPS, in the order I would do them

1. **Refetch CMP and NCDL companyfacts** — closes the two stale-EPS cases.
   Cheap, targeted, no logic change.
2. **Fix `ttm_invariants.check()`** to read `_window`, then wire it into
   `validate`. It is the only structural check that could have caught the
   52/53-week double-count automatically.
3. **Add `eps_diluted_ttm` to the fundamental module's declared metrics** so
   the provider overlay covers it. EPS is a ratio, and the standing rule is
   that ratios come from the API.
4. **Measure `validate` at the full 60-name sample.** Only ever measured at 6
   names (282s). `VALIDATE_BUDGET_S` bounds it meanwhile.
5. **Re-run the leaderboard** once the corrected TTM values are scored — every
   IC in the current scoreboard was computed on pre-fix numbers.

## State at 2026-08-16 08:00 — ALL TRACKED ITEMS CLOSED

### Read this first: which number to quote

| check | reads | what it means |
|---|---|---|
| `providers --compare` | **89.1%** | our figures vs Finnhub/Yahoo. **The headline.** |
| `ttm_invariants --rollforward` | **61/61** | every rolled TTM re-derived from its reported legs |
| `audit_metrics` | **0 high** | 109 stored metrics |
| `verify_metrics` | **76.3%** | **EXPECTED TO BE LOW — see below** |

> **`verify_metrics` CANNOT follow the roll-forward.** Two attempts to teach it
> both made the CHECKER wrong (eagerly it corrupted AAPL revenue to 431.5B
> against a true 466.8B; gated it silently failed to fire). So `cfo_ttm`,
> `capex_ttm`, `sbc_ttm` show as mismatches there while `ttm_invariants` proves
> them correct 61/61. **A low number here is not a data regression.** On
> 2026-08-15 a CORRECT implementation was reverted on exactly this misreading.

### The four items, closed

**1. Four page rows nothing could check** — `net_margin`, `gross_margin`, `roa`,
`debt_to_equity` were computed for display but absent from
`fund_metrics.REGISTRY`, so `providers.compare` reported "we do not compute"
for 456 comparisons. Added (registry now 32 metrics, 22 scale-free).
`net_margin` **100% / 0.00% gap**, `gross_margin` 0.21%.

`roa` and `debt_to_equity` disagree for a PROVEN reason, not a bug: Yahoo's own
`totalDebt` divided by OUR equity reproduces their ratio exactly (AAPL
84.3/107.5 = 0.784 vs their 0.784; MSFT 0.291 vs 0.291). Our equity is right;
the gap is finance leases, which `debt_lt + debt_st` does not carry. `roa` runs
a consistent ~0.80x of ours across AAPL/MSFT/KO — an averaged denominator.

**2. Roll-forward unverifiable** — `ttm_invariants.py --rollforward` re-derives
each rolled value from `FY + YTD_now - YTD_prior` using only reported figures.
**61 of 61 hold.** No second implementation, so nothing to drift.

**3. CIK succession** — now gated on TOTAL ASSETS within 0.75-1.35x, because a
reorganisation preserves the balance sheet and a merger does not:

```
XOM   464.5B / 449.0B = 1.03   LINKED    blank -> revenue 366.8B (1.58% vs Yahoo)
CLBK   12.2B /  11.0B = 1.10   LINKED
NVRI    1.7B /   2.7B = 0.64   rejected
PNFP  129.1B /  56.0B = 2.31   rejected  (Pinnacle/Synovus merger)
```

Enabled by default; `FD_CIK_SUCCESSION=0` disables.

**4. `_rank` scale** — 0-1 in sentiment, 0-100 in dip. DOCUMENTED, deliberately
not rescaled: a monotonic rescale preserves IC but would split stored history
against the ICs already recorded in `metrics_doc`.

### Known LIMITATIONS (not bugs, do not "fix")

- **Banks** (CLBK, PNFP) have no `revenue_ttm` — they report interest income,
  not `Revenues`. Needs bank-specific tags, not a repair.
- **288 tradeable tickers file 20-F/40-F** — foreign issuers using IFRS. No
  us-gaap data exists for them at any CIK.
- **`fcf` 50% vs providers** — Yahoo's `freeCashflow` exceeded our CFO for PFE,
  impossible under its own formula. Ours is SEC-verified.
- **10 of 61 pages carry a staleness banner** — accurate, and the point.

---

## State at 2026-08-16 02:15 — two investigations closed, one shipped as opt-in

### `op_margin` / `roe` / `fcf` disagreements — CLEARED, not bugs

The nightly provider cross-check reported 229 disagreements. ~150 are now
explained with evidence, and **none of them are our bug**:

| field | n | verdict |
|---|---|---|
| `op_margin` | 75 | CELH hand-checked: opinc TTM 160.3M on revenue 3,047.3M, **both legs pass SEC**. The company took an **80.0M operating loss in Q3-2025**; our TTM includes it, Yahoo's 19.1% does not. Directions are mixed across names (CLMT opposite sign) — a definition gap, not an offset. |
| `fcf` | 40 | Yahoo's `freeCashflow` EXCEEDS our CFO for PFE, impossible under its own formula. MSFT capex 115.9B is verbatim from the FY2026 10-K. |
| `roe` | 34 | we divide by ENDING equity, vendors use AVERAGE equity. Median gap 9.8%, neither wrong. |

Tolerances widened with the evidence in the code. **Do not "fix" these toward
the provider** — that would corrupt figures SEC verifies as correct.

### CIK succession — built, measured, SHIPPED DISABLED

`cik_succession.py` finds a ticker's predecessor CIK by exact normalised
entity-name match. Four links out of 86 candidates:

```
XOM   2115436 (94 rows)  <- 34088    (743)   reorganisation
NVRI  2104052 (185)      <- 45876    (868)   reorganisation
PNFP  2082866 (248)      <- 1115055  (623)   MERGER
CLBK  2115119 (70)       <- 1723596  (573)   ?
```

Wired in, it fixed XOM (blank → revenue 332.2B) and NVRI (0.4% vs Yahoo) —
**and broke PNFP**: assets 129B against SEC's 56B, because Pinnacle Financial
MERGED with Synovus in 2025. The new CIK is the combined entity; splicing the
predecessor produces a history that is not any single company. Verification on
the four fell to **30.3%**.

**A name match cannot distinguish a reorganisation from a merger.** So it is
`FD_CIK_SUCCESSION=1` opt-in, off by default. The map and tool are kept so the
next attempt starts from four candidates and a per-link judgement, not scratch.

> Also fixed here: `_succession_aliases` had a bare `except Exception` that
> swallowed a `NameError` from a missing `import json`. The alias silently never
> applied while every line appeared to run. **A blanket catch turns a code bug
> into a silent data gap.** Narrowed to `(OSError, ValueError, TypeError)`.

> And: a patch script printed "json import added" while its anchor matched
> nothing, so it wrote the file unchanged and reported success. **Verify the
> edit landed, never trust the patch's own message.**

### Scope note that ends the "374 broken tickers" panic

374 tradeable tickers have a CIK with <8 periods. **288 file 20-F/40-F/6-K —
foreign issuers using IFRS tags, which carry no us-gaap data at all.** They are
not fixable by any CIK work and are not broken; they are out of scope. Only 86
were domestic candidates.

---

## State at 2026-08-15 03:35 — READ THIS FIRST

### The one number that matters, and the population it belongs to

| population | verified vs SEC | what it is |
|---|---|---|
| 15 mega-caps | 97.6% | the old default check list — **too narrow, it is why COLL slipped through** |
| **tradeable universe (3,480)** | **96.0%** | 602 fields, 24 mismatches — **the names the screener serves** |
| SEC ticker map (~8,000 CIKs) | 65.1% | includes thousands never maintained — **measures the wrong thing** |

**Always quote the TRADEABLE number.** Sampling `FD.ticker_map()` draws names
outside `bars.tradeable_universe()`, which are never refetched and sit 226–591
days stale *by design*. `verify_metrics.py --n N --tradeable` samples the right
population; without `--tradeable` it samples the map and the result is not a
statement about the product.

### The failure mode that produced every bug this week

**A missing input silently becoming a plausible number.** Not arithmetic
errors — compositions where one leg was absent and the code substituted zero:

| bug | what it looked like |
|---|---|
| `stock_profile._add(opinc, dna)` with `fill_value=0` | COLL EBITDA **$4M** vs a real **$68M** — operating income mislabelled |
| `_ttm` taking `tail(4)` with no span check | HD amortisation summed a **644-day** window: 538M vs a true 639M |
| `cash_conversion_cycle` all-legs `fillna(0)` (fixed 08-10) | zero days, a flattering value, for filers tagging nothing |

The rule already existed in this codebase — *"not reported is not zero"*,
`fund_metrics._sum_reported` — and each new helper broke it again. **Before
adding any composed metric, ask what it renders when one leg is missing.**
`_add_strict` and `_fcf` in `stock_profile` are the patterns to copy.

### Verification: three layers, three different standards of proof

1. **`verify_metrics.py`** — recomputes TTM from raw SEC JSON, deliberately NOT
   importing `_ttm`. 13 concepts + a derived-EBITDA check. **A mismatch is a
   bug.** Use `--tradeable`.
2. **`providers.py --compare`** — 15 fields vs Finnhub/Yahoo, nightly inside the
   `provider` step. **A gap is a QUESTION, not a verdict** — our FCF disagreed
   with Yahoo on 43% of names and SEC proved *us* right.
3. **`audit_metrics.py`** — all 109 stored metrics, invariants only. Proprietary
   scores have no external truth; what is checkable is that they do not exhibit
   the fabrication signature.

> **THE CHECKER IS WRONG AS OFTEN AS THE DATA.** Tally for this week: checker
> wrong on KO, CSCO, the COLL EBITDA window, and 4 of 5 of `audit_metrics`'
> first HIGH issues (bad bounds — Beneish M is legitimately −15..15). Checker
> RIGHT on HD's amortisation. **Never report a mismatch as a data bug until you
> have shown which side is wrong.**

### Fixed 2026-08-14/15

- **`AmortizationOfIntangibleAssets` was never ingested.** The store filters to
  `TAGS` at write time, so adding a tag requires a **refetch**. `dna` (totals,
  chosen) is now split from `deprec`+`amort` (components, **summed** — alias
  preference picks one, which gave COLL 1.8M instead of 64.8M).
- **`_ttm` span guard** (`MAX_4Q_SPAN_DAYS = 310`) — rejects four-quarter
  windows with holes; dropped 11 malformed windows on first run.
- **Staleness banner** — `serve.py` builds a page for ANY ticker typed, but only
  tradeable names are refreshed. MTEX rendered as **profitable with positive
  equity** when SEC had it **loss-making with negative equity**. Pages whose
  newest filing exceeds `STALE_FILING_DAYS` ({"Q": 200, "A": 500} — annual
  periods are legitimately old, a single threshold warned on every ticker) now
  carry a banner. Verified: MTEX warns at 591 days, COLL/AAPL do not.
- **Backfill**: scores TODAY first, then a **time** budget (`BACKFILL_BUDGET_S`
  = 25 min). A count-based bound blew a 6-hour task limit because `hype` costs
  ~70 min/session against dip and combo at ~5 seconds.
- **`has_dip`** emitted 0/1 over the whole population (758/1), not hardcoded 1.

### Still open — do not claim these are fine

- 24 mismatches in the tradeable sweep, not individually run down.
- `roa`, `net_margin`, `gross_margin`, `debt_to_equity` are page-derived but
  absent from the scored frame, so the nightly cross-check cannot reach them.
- `_rank` is 0–1 in sentiment, 0–100 in dip. Documented, not unified —
  rescaling would invalidate the stored history against measured ICs.
- `rebuild_all_pages.py` exists because the `profiles` step only builds TODAY'S
  FLAGGED names and reports success ("no flags file for this session") while
  doing nothing after a `stock_profile` change.

---

## State at 2026-08-14 15:00 — BACKFILL BLEW THE DAILY RUN, now fixed

**What happened.** The 2026-08-13 night run finished cleanly at 00:48: Finnhub
swept 3,477/3,480, `fundamental` scored 3,179 names on provider data, `dip` and
`combo` backfilled their gaps, 56 profile pages rebuilt. Then the 05:00 run on
08-14 was **killed by Task Scheduler at its 6-hour limit** (`LastTaskResult
267014`), and that day's session was never scored.

**Why — a 23x estimate error.** Backfill was budgeted at ~3 min/session from
the daily cost. Measured:

| module | per backfilled session |
|---|---|
| `dip` | ~5 seconds |
| `combo` | ~5 seconds |
| **`hype`** | **~70 minutes** |

Scoring a PAST session cannot reuse the warm caches the daily path builds.
Four hype backfills consumed 5.5 hours and the run died before reaching today.

**Two design errors, both fixed:**

1. **Backfill ran BEFORE today's session** — least important work ahead of most
   important. `_with_backfill` now scores `asof` FIRST, always. Today's data can
   never be starved by history again.
2. **The bound was a session count, not time.** A count is meaningless when the
   per-item cost varies 800x between modules and is unknown up front.
   `BACKFILL_BUDGET_S = 25 * 60` now caps it: dip and combo clear their backlog
   instantly, hype makes ~one session per night and catches up within a week,
   and the daily pass is never at risk. Remaining gaps are logged, not hidden.

### Verification run 2026-08-14 (stored session 2026-08-12)

| check | result |
|---|---|
| `verify_metrics` (independent SEC arithmetic) | **92 fields, 0 mismatches, 100%** — flagged itself INCOMPLETE on one request timeout rather than reporting clean |
| stored vs Yahoo, 10 large caps | P/E median gap **1.07%**, mktcap **0.62%** |
| provider agreement, 150 tradeable names | **81.0%** — price 100%, shares_out 89%, mktcap 83% (median gap 0.13%), P/E 67% (median gap 2.37%) |
| module selftests | `fundamentals`, `fund_metrics`, `providers` all OK |

The P/E and mktcap pass rates are dragged partly by comparing a **two-day-old
stored session against live Yahoo prices** — the median gaps (0.13%, 2.37%) are
the honest measure of the pipeline.

---

## State at 2026-08-13 21:40 — ONE PIPELINE, PROVIDER-FIRST

**The daily job is `orchestrator.py` and nothing else.** One scheduled task,
`Screener-Orchestrator`, runs it at 05:00. Everything below this heading is
history; read it for the reasoning, not for the current state.

### What changed tonight

**Ten scheduled tasks became one.** Seven were one-shot rescue chains that had
already fired (`Screener-Chain`, `-FixAll`, `-FixData`, `-Overnight`,
`-SentiRebuild`, `-Study`, `-Refetch`) — all unregistered.
`PatternScan-DailyRun` was a genuine DUPLICATE: `orchestrator._step_bounce`
calls `daily_run.run(...)` internally, so the 03:00 task re-fetched bars and
re-ran the screen two hours before the 05:00 pass did it again — and on
2026-08-12 the two collided on the run lock and cost a whole day of scores.
That task was created under an elevated context this account cannot unregister
("Access is denied"), so the duplication is stopped in code: `daily_run.main()`
now exits immediately unless given `--standalone`. `run()` is untouched, so the
orchestrator's internal call is unaffected.

**Finnhub is the primary provider; yfinance is on-demand only.** Measured:
Finnhub 0.89 s/name, 133 metrics, documented 60/min → ~61 min for the universe,
0 failures. yfinance handled 24 and 150 names fine and then **hung** on the
3,480-name sweep — 53 minutes for ~20 CPU-seconds, two sockets open, no timeout
to break it. yfinance is kept because it alone carries float, short interest,
the next earnings date and analyst targets.

> **UNITS DIFFER BETWEEN PROVIDERS AND THE DIFFERENCE IS SILENT.** Finnhub
> returns `marketCapitalization` in **millions** (4,476,472.5) and
> `roeTTM`/margins as **percent** (137.18, 48.65); Yahoo returns absolute
> (4,439,768,301,568) and fractions (1.488, 0.48653). An unconverted merge is a
> **1,000,000× market-cap error**.

#### Four layers stop a naive merge, and each was proven by breaking it

The first version had a conversion table for Finnhub only, on the assumption
that Yahoo was already normalised. **Two Yahoo fields were 100× out** and a
three-field, one-ticker selftest passed anyway:

```
debt_to_equity   finnhub 0.7844   yahoo 78.44    (yahoo is percent)
dividend_yield   finnhub 0.00357  yahoo 0.36     (yahoo is percent)
```

| layer | what it catches |
|---|---|
| `FINNHUB_FIELDS` / `YAHOO_SCALE` multipliers | conversion declared per field, in one place |
| `SANE` bounds, applied on **ingest and on read** | percent-as-fraction (a 27.6 net margin is impossible); read-time too, because the cache outlives any one version of this file |
| `_selftest_cross_scale` | compares **every** field both backends emit across 5 tickers; fails outside [0.25, 4]. Also fails if a field is silently **dropped** — a wrong multiplier makes a value fail its bound and vanish, which shrinks the comparison instead of failing it |
| `_selftest_identity` | `mktcap == price × shares`. The only check that catches millions-vs-absolute, since 4,476,472 is a plausible cap for a microcap |
| `_selftest_declared` | a new field with no declared bound fails immediately |

Verified by deliberately breaking each: dropped `mktcap` multiplier → caught;
`net_margin` left as percent → caught (via the drop assertion); Yahoo
`dividend_yield` scale removed → caught; undeclared field → caught.

**`providers.selftest()` runs at the top of the `provider` orchestrator step**,
before anything is written. A step that fails writes nothing; a step that writes
and then notices has already put 3,480 corrupt rows in the store.

> **`np.int64` is not a subclass of `int`.** `isinstance(v, (int, float))`
> returns False for every integer field after a parquet round-trip — market cap
> and share count are exactly those. That made `_selftest_identity` skip itself
> in silence. Use `providers._num()`, never `isinstance`.

**Missed days are now backfilled.** Score steps scored exactly the session they
were handed, so a closed laptop left permanent holes. `_with_backfill` wraps
`hype`, `dip` and `combo` (daily modules only — `fundamental` is weekly and its
empty days are by design, so "backfilling" it would build six sessions at the
~94 min/session measured on the enlarged fact store). Bounded to
`BACKFILL_MAX_SESSIONS = 10`. It immediately found real holes:
`2026-07-30, 07-31, 08-03, 08-04, 08-05` missing from hype/dip/combo, which the
next run that actually executes those steps will fill.

**`archive/`** holds the eight retired rescue scripts with a README recording
what each measured and why it is retired — including the trap that
`rebuild_history.py` resumes from a state file and will rebuild *one* session
unless given `--fresh`.

**`docs/DATA_DICTIONARY.md`** is generated by `data_dict.py` from the live
registries and regenerated by the `docs` step, so it cannot go stale. Its third
section is the point: what each source offers that we do **not** take.

### Daily cost after the change

| | |
|---|---|
| daily steps | ~17 min (`profiles` is 12 of it) |
| `provider` sweep | ~61 min |
| **daily total** | **~78 min** |
| weekly day (+`leaderboard` 22.7 m, `events` 8.3 m, `sec_gap` 3.5 m) | ~115 min |
| per missed session backfilled | ~3 min |

---

## State at 2026-08-13 04:10 — earlier the same day

> ### ⚠ THE 2026-08-12 REBUILD IS ALREADY SUPERSEDED
>
> The 507-min rebuild that finished 22:45 on 08-12 is **correct on everything
> it was checked against** (SEC reconciliation 15/15 at 0.000%, still true) but
> it was built before two TTM bugs found on 08-13 were known. The stored
> fundamental sessions therefore carry wrong TTM figures.
>
> **Measured, not estimated** — 200-ticker random sample of stored session
> 2026-08-07, P/E comparable on 137 of them, corrected code vs stored:
>
> | | |
> |---|---|
> | P/E unchanged (<1%) | **7.3%** |
> | P/E changed >5% | **70.8%** (±8pp, 95% CI, n=137) |
> | P/E changed >25% | **30.7%** |
> | median absolute change | **12.3%** |
> | market cap unchanged (<1%) | 85.0% — the share bug barely touched mktcap |
>
> Stored values included clip-ceiling artefacts: SCHW and RUN both sat at the
> P/E ceiling of 1000 (true values 19.60 and 6.89); MDLN read 0.02 (true 29.56).
>
> **The SEC reconciliation did not catch this** because it compared *reported
> quarterly figures* — which were right — not the *TTM aggregation built on
> top of them*, which is where both bugs lived. Any future audit must check
> the derived TTM column, not only the raw quarters.
>
> A history rebuild (~8.5 h) + remeasure (~7.5 h) is required before the
> fundamental figures on any page can be trusted. **Not yet started.**

### The two TTM bugs (both fixed in code 2026-08-13, neither yet in the data)

**1. `_ttm()` preferred a stale annual figure over newer quarters.** It
short-circuited on any visible `qtrs==4` row. A 10-K is filed once a year, so
for most of the year the four most recent quarters end *later* than the last
annual period. Measured 2026-08-11: **2,799 of 2,964 filers (94%)** were in
that position, median **181 days** stale, worst 639. AAPL's `net_income_ttm`
read $112.0B (fiscal 2025) instead of $128.9B, putting the displayed P/E at
**40.87** against a true **34.70**. Fixed: whichever window *ends later* wins,
never both, never added — plus `_q4_rows()` to derive the fiscal Q4 that no
10-Q reports, so "four most recent quarters" is really twelve months.

**2. Weighted-average share counts were treated as flows.** `shares_basic` and
`shares_diluted` carry a duration, so `qtrs > 0` put them on the flow path —
but they do not accumulate. The Q4 derivation `FY − Q1 − Q2 − Q3` gave AAPL
**−30,150,480,000 diluted shares**, a negative share count rendered on the
profile page; and the TTM summed four quarterly averages (~4× too high, or
~1× by accident when the negative Q4 cancelled it). New `AVERAGE_CONCEPTS`
frozenset is excluded from both Q4 derivations and reduced by *latest* in
`_ttm`. Cross-check `net_income_ttm / shares_diluted_ttm` vs reported
`eps_diluted_ttm` went from **5% apart to 0.6%** on AAPL. Pinned by
`_selftest_average_concepts`.

**3. `pe` now uses the filer's own diluted EPS.** `mktcap / net_income` divides
a *current* share count by *trailing-average* earnings; every quote site shows
`price / diluted EPS`. AAPL: 34.25 the old way, **34.70** the new way, against
the ~34.6 quoted publicly. Falls back to `mktcap / net_income` where EPS is
untagged, so coverage does not drop.

### THE REAL ROOT CAUSE OF "GOOGLE SAYS SOMETHING ELSE" — freshness, not arithmetic

Once the two TTM bugs were fixed, the large caps became exactly right. A wider
check then showed the actual problem, and it is not computation:

**`providers.py` (new)** cross-checks our figures against Yahoo — the source
Google shows — so the user's own comparison is now automated instead of
anecdotal. 150 random tickers, 113 with Yahoo data, 345 field comparisons:

| field | agree | median gap |
|---|---|---|
| price | **100%** | 0.00% |
| mktcap | 70.2% | 0.63% |
| shares_out | 66.1% | 0.63% |
| pe | 67.7% | 0.23% |
| revenue_ttm | 57.4% | 1.89% |
| eps_ttm | **45.3%** | **10.51%** |

**Median staleness where we DISAGREE: 225 days. Where we AGREE: 44 days.** The
median gap is tiny (0.23% on P/E) — most comparisons are near-identical and a
stale minority drags the rate down. VSBC's newest stored period was **925 days**
old; its stored value was *correct for Jan-2024* and meaningless in Aug-2026.

**Why the data is stale, definitively:** SEC's bulk Financial Statement Data
Sets are the backbone, and **2026q2 is not published yet — probed 2026-08-13,
HTTP 404**. 2026q1 is the newest that exists. The bulk route *structurally
cannot* be current; it is not a missed fetch.

**And the mechanism meant to cover that gap is broken.** `stale_names()` /
`refresh_targets()` / `backfill_companyfacts()` were written 2026-08-10 to
top up from the companyfacts API per company. The runner that called them,
`run_refetch.py`, **no longer exists**, while the `Screener-Refetch` scheduled
task still points at it and therefore fails silently (last real run 2026-08-08,
no future trigger). That is why 94% of the universe froze at the last bulk
quarter and nobody was told.

**The fix is bounded**: `FD.backfill_companyfacts(FD.refresh_targets())`, which
is ~3,000 API calls. Not yet run.

### Two verification tools now exist, and both are honest about their limits

**`verify_metrics.py`** — checks the numbers the app DISPLAYS (TTM revenue, net
income, diluted EPS, share counts, equity, assets) against TTM arithmetic
written from scratch against raw SEC JSON. It does not import `_ttm`, so it can
disagree with the code under test. **91/91 fields, 100%, on 15 large caps.**
It found four bugs *in itself* before it was trustworthy — stale-tag fallback,
an API returning empty payloads with HTTP 200, a one-day off-by-one in the Q4
derivation, and counting unreachable data as a pass. It now fails loudly on
fetch errors rather than reporting a clean run.

**`providers.py`** — Yahoo second opinion. NOT a source of truth: unofficial,
no point-in-time history, wrong sometimes too. Where two independent pipelines
agree, confidence is real; where they disagree, that is information to surface,
not to hide.

### Also fixed 2026-08-13 (independent of the data rebuild)

**Clicking a ticker in Explore hung the browser for four minutes.** Explore
links to `stock/XXXX.html` as a plain `<a href>`; the loader with the spinner
existed only on the Profiles index, so arriving from Explore gave no feedback
at all while `serve.py` blocked the GET on a cold on-demand build (measured at
252 s). The browser usually gave up first, and the abandoned socket then threw
`ConnectionAbortedError` out of `do_GET`, burying the real cause in a stack
trace that looked like a server crash. Now the build runs in a thread and the
request returns a self-refreshing waiting page **in 0.002 s**; `_write()`
swallows client disconnects. Verified end to end: JNJ returned the waiting
page instantly, built in 160 s in the background, then served 267 KB in
0.004 s on refresh.

**The daily pass skipped a whole day on a 94-second lock collision.** On
2026-08-12 the 05:00 `Screener-Orchestrator` found the lock held by a rebuild
chain that had started at 04:58:33, logged `Exiting without work`, and did not
return for 24 hours — which is why profile pages still showed 08-11 data.
`acquire_lock()` now takes `wait_min`, and the scheduled task passes
`--wait-for-lock 300` (task `ExecutionTimeLimit` raised PT3H → PT6H to match;
`ORCH_LOCK_STALE_HOURS` is 6 and long jobs refresh the lock, so a live holder
is never broken). Default stays 0, so interactive runs and the chained jobs
keep exiting immediately. Four cases tested: no-wait/held → False in 0.1 s;
budget expiry → False after the full wait; **holder releases mid-wait → lock
acquired**; dead pid → broken with no wait.

### The data reconciles against SEC, 15/15 at 0.000%

Not "looks plausible" — compared field by field against SEC's own XBRL API,
quarter by quarter:

```
AAPL MSFT NVDA AMD KO WMT JNJ PG INTC CSCO HD MCD NKE ORCL ADBE
   all 0.000% max difference across ~7 quarters each
```

Every fix verified in the rebuilt data:

| | before | now |
|---|---|---|
| negative P/E | 932 | **0** |
| negative P/B | 171 | **0** |
| negative EV/EBITDA | 296 | **0** |
| RDW P/E (loss-making) | −14.9 | **blank** |
| turnover max | 414,549 | **37.6** |
| market caps under $1M | 9 | **3** |
| blank quarterly columns (58 largest) | 19 | **7 of 696 (1%)** |

### Two bugs found by that reconciliation

**`history()` ignored the largest-alias rule that `facts_asof` already had.**
Walmart files BOTH `RevenueFromContractWithCustomerExcludingAssessedTax`
(net sales, 175.68B) and `Revenues` (total, 177.75B) in the same 10-Q. The
table took whichever was filed last, so quarters came out on net sales while
the ANNUAL figure came out on the total — and since Q4 is derived as
`FY − Q1 − Q2 − Q3`, mixing the two inflated it. Our TTM read 713–718B against
SEC's 700B. This is the REXR 1,700x bug in a second code path; the fix existed
and had simply never been applied here.

**Blank quarterly columns.** A company whose quarter does not end on a
month-end files its income statement against the fiscal date and its balance
sheet against the calendar month-end, so one quarter became two rows and one
rendered empty. NVDA showed revenue at 2025-07-27 beside an empty 2025-07-31.
`_merge_near_periods` collapses dates within 10 days, keeping the fiscal one.
19 blank columns → 7, and the remainder are companies SEC itself has no
discrete quarters for (XOM files cumulative periods only).

### Known and accepted

- **~1% of quarterly cells are blank** where SEC has no discrete-quarter fact.
  Deriving them from cumulative (6-month, 9-month) figures is the next
  improvement if it ever matters.
- **`facts_asof` TTM and `history()` 4-quarter sum differ ~0.75%** for names
  with a derived Q4. By design they are different objects — the first is
  point-in-time and filed-only, the second lets restatements win — but the gap
  is worth remembering before comparing them.

### Verify it yourself, any time

```bash
python validate.py                       # 33 checks
python -c "import fundamentals as FD; print(FD.history('AAPL',freq='Q',periods=6))"
```

## Superseded — 2026-08-12 01:50

**Running:** `Screener-FixAll` → `fix_all.py`, restarted **01:38** on the fully
corrected metric definitions.

**TWO MILESTONES, and only the first one matters for data:**

```
11:21   history rebuilt, every page regenerated  <<< DATA ACCURATE
20:40   study + walk-forward finished            <<< predictive only
```

The second touches no fundamental figure. Spot-check from 11:30; do not wait
for 20:40.

```bash
tail -20 "data/_rebuild.log"     # want 4 modules complete + "integrity audit clean"
tail -30 "data/_fix_all.log"     # want FIX ALL DONE ... 2 ok, 0 failed
```

### Why it restarted at 01:38

A systematic sweep — every ratio's negative values checked against its ranking
direction, across the whole universe — found three more bugs of the RDW family:

| metric | defect |
|---|---|
| `net_debt_ebitda` | 430 cash-burning filers ranked the LEAST leveraged in the market (mean rank 0.86 vs 0.36) |
| `roe` | 99 loss-makers showed a POSITIVE return — loss ÷ negative equity flips sign and hides in the good bucket |
| `roic` | the same flip on negative invested capital |

The earlier run had imported the metric code before those landed, so its 50
completed sessions were already stale. Stopped, state cleared, restarted.

**The sweep now passes and its remaining negatives are provably right:**
`pe`/`pb`/`ev_ebitda` have none; `roe`/`roic` negatives are genuine losses on
positive equity and rank low; all 299 negative `net_debt_ebitda` are NET CASH
companies (AAON, ACM, ACMR) which deserve the best leverage rank; negative
`accruals` mean cash-backed earnings, which is good.

**Write the check, do not ask the user to eyeball tickers.** Every bug this week
was found by looking at data. The rank-direction sweep is the generalisation of
that and should be run after any metric change.

### The server must be restarted after a metric change

`serve.py` builds profiles on demand and holds `fund_metrics` imported from
whenever it started. It was still serving yesterday's logic hours after the
fixes landed. Restarted 01:47. `python serve.py --stop` then relaunch.

## Superseded — 2026-08-12 01:20

**Running:** scheduled task `Screener-FixAll` → `fix_all.py`, started 00:05.
**ETA ~18:00 today.** Log `data/_fix_all.log`; the two inner chains log to
`data/_rebuild.log` and `data/_remeasure.log`.

```bash
tail -30 "data/_fix_all.log"     # want: FIX ALL DONE ... 2 ok, 0 failed
python validate.py               # want: 0 FAILED
```

### It survives the laptop shutting down — TESTED, not assumed

| | |
|---|---|
| progress saved | every 10 sessions to `data/_rebuild_state.json` (40 already banked) |
| stage-level resume | `_remeasure_state.json` — a reboot 8h in no longer restarts the study |
| chain-level resume | `_fix_all_state.json` — a finished chain is never re-run |
| after a reboot | `shell:startup\Screener-Resume.bat` relaunches it at logon |
| hook cleanup | `fix_all` deletes that .bat once both chains complete |

**The bug this nearly shipped with.** Both inner chains returned **exit 0** when
another instance held the run lock. So the resume hook firing during a live run
would have made the wrapper mark the chain *complete having done nothing* — the
project's own #1 trap. They now exit **75**, and `fix_all` treats that as "leave
it alone", never as done.

Verified by running a second instance against the live job:

```
ANOTHER RUN HOLDS THE LOCK (pid 12476). Exiting without work.
[history] another instance is already running -- nothing marked done
state AFTER: (still none)
```

`Register-ScheduledTask` is denied in this environment, which is why the resume
is a Startup-folder .bat rather than an `AtStartup` trigger. Same effect for a
laptop that reboots and gets logged into.

### Why this run exists — two bugs found 2026-08-11 via RDW

- **A negative P/E ranked as the cheapest stock in the market.** `pe`, `pb` and
  `ev_ebitda` sort lower-is-better, so a negative denominator sorted best:
  **932 of 2,918 filers (32%)** carried a negative P/E, and loss-makers earned a
  mean value rank of **0.84** against **0.34** for profitable companies. Redwire,
  unprofitable every year since 2021, scored 0.85 on "cheapness". All three now
  withhold on a non-positive denominator, exactly as `peg` always did.
- **Placeholder share counts produced a $14 market cap.** Some filers tag
  cover-page `shares_out` as **1, 10 or 100**, and since `shares_out` is
  preferred first those beat a real count in the same frame — FBYD resolved to
  **10 shares** while carrying 39,255,880 diluted. HQ's market cap read
  **$14.13**, making its turnover **414,549x**. `MIN_SHARES = 10_000` now
  rejects them. Turnover max fell 414,549 → **37.6**.

Both change what the metrics ARE, which is why history is being rebuilt before
the study re-measures it.

## State at 2026-08-11 20:10

**Running:** scheduled task `Screener-Remeasure`, started 20:05, **ETA ~03:40**.
Log `data/_remeasure.log`. Parented by Task Scheduler, so it is independent of
any editor.

```
study   ~4.5h   re-measure every cell on the corrected data
combo   ~5min   re-score; the study changes which metrics are admitted
oos     ~2.5h   4-fold walk-forward, stale train caches deleted first
pages   ~35min  rebuild so every figure comes from the new tables
audit   ~6min   integrity audit, last, on the finished state
```

**When it finishes:**

```bash
tail -40 "data/_remeasure.log"      # want: REMEASURE DONE ... 5 ok, 0 failed
python validate.py                  # want: 0 FAILED
powershell -Command "Unregister-ScheduledTask -TaskName Screener-Remeasure -Confirm:\$false"
```

### Why this run exists

The data was corrected on 2026-08-10/11 but **the factor study was not
re-measured** — all 1,600 cells dated from 2026-08-09, before the `debt`/`ccc`
fabrications were removed, before two missing quarters were recovered, and
before 153 non-USD filers entered. That is not cosmetic: **`combo` picks its
ingredients FROM the study**, so the live composite was assembled from
measurements of data that no longer exists, and the out-of-sample and
walk-forward conclusions (including the decay finding) rest on the same
foundation. They may survive re-measurement; right now they are unproven.

Nothing caught it, because the `claims` guard proved the labels agreed with the
study while both were stale together. There is now a check for exactly that:
`the factor study measures the CURRENT data`, which compares the study's
newest cell against the mtime of every PAST month partition.

### No measured number is typed into a label any more

Every hand-written t-stat has been removed from `metrics_doc`. The prose says
what a metric IS; the figures are rendered at build time from `study.py` and
from `_oos_walkforward.parquet` via a new out-of-sample block on
`reports/metrics.html`. That kills the drift class outright — one label had
been quoting a *different metric's* t-stat for months.

The `claims` guard was updated to match: zero quoted figures is now the goal,
so it verifies the label tables were FOUND rather than that they still contain
numbers. Conflating those would have made it warn forever precisely because the
problem was fixed.

### The archived comparison

`study.py` skips cells it already holds, so the old table was moved to
`data/_factor_study_pre_datafix.parquet` rather than deleted. That is the
before/after: it answers whether the fabricated `debt` and `ccc` values were
holding any result up. `net_debt_ebitda` — one of the five metrics the honest
fit picked — lost 37% of its values in the correction, so expect movement.

## State at 2026-08-10 16:45

**Running:** scheduled task `Screener-RebuildHistory`, resumed 16:36, **ETA ~19:50**.
Log `data/_rebuild.log`, progress `data/_rebuild_state.json` (saved every 10
sessions, so it resumes rather than restarts). Independent of any editor.

**When it finishes**, the whole verdict is one command:

```bash
python validate.py
```

Want `0 FAILED` and `fundamentals are CURRENT, not merely present` reading ok.
Then unregister the one-off:

```bash
powershell -Command "Unregister-ScheduledTask -TaskName Screener-RebuildHistory -Confirm:\$false"
```

### The bug that cost a 10-hour run

`fetch_companyfacts` never filtered to `WANTED`, though the bulk path always
had. Harmless while it served ~200 gap names; pointed at the whole universe by
the staleness refresh it stored **25M facts, 79% of them tags nothing reads**,
and every `facts_asof` had to page through them. Sessions went from a measured
48s to 25 minutes and Task Scheduler killed the run at its 10-hour limit, 90 of
182 done.

Filter added; the store was pruned in place — **53.4M → 12.7M rows, 246 → 67
MB** — with no re-fetching. Re-measured after: **68–118s per session**.

The process lesson, which is the one worth keeping: the ETA came from a
measurement taken BEFORE the job that invalidated it. Measure, and re-measure
after anything that changes what you measured.

### Fixed since the 02:00 note

- **Fiscal Q4 was missing from every quarterly table since 2020.** A 10-K
  reports the full year and no 10-Q covers Q4, so the series had a hole every
  September — one blank column in four. Now derived as FY − Q1 − Q2 − Q3, flow
  concepts only, and refused outright if any of the three is missing. Verified
  against Apple's actuals including FQ4-2024's anomalous $14.7B net income (the
  EU State Aid charge).
- **"Bulk always wins" was silently discarding the fresh data.** Bulk held one
  AAPL row in `2026q1`, which dropped every companyfacts row for AAPL in that
  partition — including the 2026-03-28 quarter only companyfacts has. The
  staleness refresh could therefore never fix a filer bulk already covered,
  which was nearly all of them. Authority is now whichever source **filed more
  recently**, still exactly one per (cik, partition).
- **Absurd period dates** (1986, 6016, LEGH's 2033) rejected at ingest, not
  just at read.
- **Two UI defects**: the twin scrollbar sized itself from the table's width
  while the container scrolled by the container's width — 12px apart, which
  clipped the last column mid-character (`143.8B` as `143.8E`). Financial
  tables now also open on the NEWEST period. And `IC by horizon` is now
  `Predictive strength · by holding period · 1/5/20/60 days` with a visible
  legend, left-aligned on a fixed grid.
- **`_fill_q4` had the same class of bug it was fixing**: the date index was
  built once then rows inserted in a loop, so every concept after the first got
  a misaligned mask and was left blank. Two-pass now, pinned by selftest.

**Staleness is largely resolved already: 3,207 → 273 names (92% → 8%).**

## State at 2026-08-10 02:00

The walk-forward **finished 01:05** (result in the next section) and its one-off
task has been unregistered. Nothing is scheduled now except the daily
orchestrator at 05:00, which will rebuild every page with the corrected labels.

**Why that job was a Scheduled Task and not `nohup`.** The walk-forward was first launched
with `nohup ... &` from the agent's shell. That process was **inside a Windows
job object**, so closing the editor could have killed it — checked with
`IsProcessInJob` rather than assumed, after noticing `overnight.py` had already
learned this ("launched detached via a one-time Scheduled Task so it survives
any terminal or editor being closed"). Relaunched under Task Scheduler, the
process is parented by `svchost.exe` and is independent of any editor session.

Restarting cost only the fold in flight: **the per-split train fits are cached**
(`data/_oos_train_<split>.parquet`), so folds 1 and 2 resumed in 36 seconds
instead of refitting for 50 minutes. That cache is the reason a restart is
cheap, and it is worth keeping that property in anything similar.

## READ FIRST: "not reported" was being published as zero

Found 2026-08-10 while auditing whether the metrics are actually *correct*
rather than merely self-consistent. Two metric families were inventing
favourable numbers out of missing data, and the audit could not see it because
every invented value was in range, non-null and internally consistent.

**`debt` was `debt_lt.fillna(0) + debt_st.fillna(0)`.** In pandas `NaN + NaN` is
NaN, but `fillna(0) + fillna(0)` is **0** — so a filer tagging no debt line came
out not as "unknown" but as the flattering "this company has no borrowings".

- 1,376 of 3,270 tradeable names (42%) tag no debt line at all
- **849 of those report total liabilities above 30% of assets** — they plainly
  do carry obligations
- it flowed into `net_debt_ebitda` (lower is better), into EV for `ev_ebitda` /
  `ev_sales` / `fcf_yield`, and into `invested_capital` for `roic` / `eva` /
  `wacc`

**`ccc` was `dio.fillna(0) + dso.fillna(0) - dpo.fillna(0)`.** A filer tagging
none of inventory, receivables or payables scored `0 + 0 - 0` = **zero days**,
published as fact. `ccc` ranks lower-is-better, so:

| | median score on this axis |
|---|---|
| company with NO working-capital data | **62 / 100** |
| company with all three legs reported | **25 / 100** |

Missing data was outranking disclosed data by 2.5x.

**Fixed** with `_sum_reported` (sum the legs actually reported; NaN if none
were) and a `ccc` that requires revenue, receivables and cogs. `invested_capital`,
`ev` and `wacc` no longer re-introduce the zero one layer down. Pinned by
selftest, because the wrong idiom is one character from the right one.

The cost is coverage, and that is the correct trade — every remaining value is
real:

| metric | before | after |
|---|---:|---:|
| `ccc` | 3,270 | 1,412 |
| `roic` | 2,474 | 1,518 |
| `roic_wacc`, `eva` | 2,343 | 1,306 |
| `fcf_yield` | 2,313 | 1,346 |
| `ev_sales` | 2,366 | 1,448 |
| `net_debt_ebitda` | 1,798 | 1,128 |
| `ev_ebitda` | 1,668 | 1,028 |

**Only the 2026-08-07 session is recomputed.** History still holds the old
fabricated values, so the study, the backtests and the walk-forward below were
all measured on partly-invented data — `net_debt_ebitda` is one of the five
metrics the honest fit picked. A full rebuild plus re-measure is now the single
highest-value job outstanding.

## The walk-forward overturned the headline below

The single-split result in the next section is real but **misleading on its
own**. A 4-fold walk-forward finished 2026-08-10 01:05 and shows every combo
score decaying toward zero as the test window approaches today:

| score | h | 2020-22 | 2022-23 | 2023-25 | **2025-26** |
|---|---|---|---|---|---|
| `combo_h20` | 20 | +4.40 | +2.04 | +1.46 | **+0.16** |
| `combo_h60` | 20 | +3.60 | +2.28 | +0.94 | **+0.42** |
| `combo_h60` | 60 | +3.18 | +2.05 | +1.11 | **+0.02** |

Hit rate falls with it: `combo_h20` 81% → 65% → 62% → **50%**, `combo_h60`
81% → 62% → 62% → **54%**. Fifty per cent is a coin flip.

**The fold with the most training data is the worst fold.** Fold 4 trained on
148 sessions and admitted 31 metrics; fold 1 trained on 70 and admitted 10. More
data, more ingredients, worse result — the signature of overfitting, a decayed
edge, or both.

**Why the single split looked fine:** its 88 test dates ran 2021→2026 and were
dominated by the strong early period. Splitting that same span into four windows
shows where the strength actually lives.

**The honest caveat in the other direction:** each fold has only 26 test dates
(n_eff ≈ 18 at h=20), so any *one* weak fold would be noise. What is not noise
is the same monotonic slide appearing in three scores and every horizon at once.

**My own verdict logic got this wrong first.** It printed `STABLE ACROSS FOLDS`
because all four numbers were positive — a criterion that cannot fail on a dying
signal. It now checks the most recent fold and the trend, and reports `DECAYING`
when t slides monotonically. A test that can only pass is worth nothing.

Labels on every combo score have been corrected to say this.

## The single split, for the record — `combo_h60` t=+3.19

The horizons are now named for their **evidence**, not a promise:
`combo_short/medium/long` → **`combo_h1` / `combo_h20` / `combo_h60`**. The old
names claimed a horizon each score did not predict best; the suffix now says
only "built from N-day evidence", and where each actually peaks is measured and
stated in `metrics_doc` instead of encoded in a name.

Then the test that mattered. Split at **2021-09-29**: 88 train sessions, 88 held
out. The admission rule, signs and theme weights were refit on train dates only,
frozen, and applied to dates the fit never saw. t-stats are overlap-corrected
(n_eff ≈ 60, not 86).

| score | h | in-sample (175 dates) | **out-of-sample (86 unseen)** |
|---|---|---|---|
| `combo_h60` | 20 | IC .0441 · t=+4.76 · 64% | **IC .0266 · t=+3.19 · 69%** |
| `combo_h60` | 60 | IC .0671 · t=+4.24 · 74% | **IC .0354 · t=+2.21 · 77%** |
| `combo_h20` | 20 | IC .0379 · t=+3.60 · 59% | IC .0165 · t=+2.03 · 63% |
| `combo_h20` | 60 | IC .0548 · t=+3.09 · 67% | IC .0178 · t=+1.21 · 65% |
| `combo_h1` | 1 | IC .0219 · t=+2.00 · 59% | IC .0069 · t=+0.30 · 54% |

**`combo_h60` survives this test** — IC shrinks ~40%, hit rate rises 64% → 69%,
which reads like a real effect carrying a normal selection premium. The
walk-forward above shows why that reading was too generous: these 86 dates span
2021→2026 and the strength is concentrated in the first half.

**`combo_h1` was selection, not signal.** It sat exactly on the |t|≥2 bar
in-sample and reads t=+0.30 out of sample. Now labelled FAILED OUT OF SAMPLE.

**`combo_h20` is marginal** — t=+2.03 at its own horizon, fails at h=60.

The honest fit admitted **5 metrics for `combo_h60` where the full sample picks
21** (half the dates shrinks every |t| by ~√2): `avg_trade_size` +3.49, `gpoa`
+2.93, `net_debt_ebitda` −2.88, `turnover` +2.73, `z_score` −2.51. So what held
is the **procedure**, not these exact 21 weights. And the metric definitions,
theme assignments and exclusion list were authored with the full history already
seen — no split removes that.

Reproduce in seconds, not 22 minutes: the train fit is cached per split in
`data/_oos_train_<split>.parquet`.

```bash
python oos.py --split 2021-09-29 --reuse-train
```

## Recency weighting LOST

Plain beats decayed in **10 of 12** window/horizon pairs. The two decay wins are
hairline (30d h=1: 3.22 vs 3.21) and it loses badly at the long end (90d h=60:
0.65 vs 1.86). Both stay on the page, neither is promoted. `sent_age` was the
real fix all along -- DPRO's problem was never that old articles outweighed new
ones, it was that there were no new ones.

## The size look-ahead is gone, and it mattered more than expected

`mktcap` now covers all 182 fundamental sessions, so `study.py` logs
**POINT-IN-TIME across 182 sessions** instead of the snapshot warning. Removing
the bias did not nudge the size analysis, it inverted it -- significant metrics
at h=20 went **large 14 -> 44, mid 23 -> 39, small 51 -> 34**.

That **falsified the reason `days_since_filing` was excluded from combo.** The
old note said it was significant only among small caps; on unbiased buckets it
is significant everywhere. Re-investigated, the real mechanism is SIZE:
Spearman **+0.355** with mktcap, median cap rising $0.66B -> $6.38B across
filing-recency quintiles, because SEC deadlines scale with filer status
(60/75/90 days). Still excluded -- `mktcap` already is -- but on evidence that
holds.

## reports/ is one convention

74 flat files with four naming schemes became:

```
reports/index.html · metrics.html
reports/explore/latest.html      + <session>.html
reports/bounce/latest.html       + index.html, <session>.html, <session>.csv
reports/sentiment/latest.html · reports/fundamental/latest.html
reports/stock/<TICKER>.html
```

Moving files leaves their hrefs stale, and the dead-link gate caught **223** of
them. Two hid from the first grep: a hardcoded href in `dashboard_template.html`
(a Jinja template, not a `.py`) and a hub link in `senti_screen.py`. Now 63
pages, zero dead links.

## Bugs fixed since the last note

- **`sector` was emitted for ZERO companies** -- `_sector_for` returns a
  positionally-indexed Series and the caller looked up by ticker. 3,197 labels
  now; sector peer comparison had been running on nothing.
- **Report retention had never run.** `store.prune_dated` was hard-coded to
  `*.parquet` while being called on `reports/`, which holds .html/.csv -- and a
  comment in the caller described the ~93 MB/yr it was supposedly reclaiming. It
  now takes patterns, walks the subdirectories, and refuses to delete a file
  whose stem is not a date (`latest.html` would have sorted before any cutoff).
- **combo could have fed itself** -- its own `th_*` and `*_cov` outputs were not
  in its exclusion set, so a composite built from `th_profitability` would have
  counted every profitability metric twice and called it independent evidence.
- **`session_picker` built its "latest" alias by string surgery**
  (`pattern.split("_")[0]`), which only worked while every pattern contained an
  underscore. Explore's became `{d}.html` and it produced `{d}.html.html`.
- **`FUNDAMENTALS_MIN_FILERS` was declared for two years and never enforced.**
  Now relative to the four preceding quarters: 68/68 real quarters accepted,
  4/4 synthetic truncations rejected. A fixed floor would have rejected the
  genuine 2009-2011 XBRL phase-in.
- **The chart's window buttons never worked** -- a patch script printed "wired"
  without asserting its replace matched. Five windows now, verified by clicking;
  the guard greps the RENDERED script.
- Quarterly was capped at 16 of 73 periods. `wacc` exported, `sue` removed from
  REGISTRY rather than faked, `--exits` persists its table, explore names
  dropped columns, 9 dead config constants marked and audited.

## Bugs found by the two guards added tonight

Both were found by new checks, within minutes of those checks first running.

- **`trade_size_trend`'s label quoted the wrong metric's t-stat.** It claimed
  "MEASURED POSITIVE (t=3.06): bigger prints predicted better". It measures
  **negative and insignificant at every horizon** (best |t|=1.74). The +3.06
  belongs to `avg_trade_size` — a different metric, a level rather than a
  change — and the whole "institutional reading" interpretation had been
  written on top of the wrong number, in the label *and* in the `MEASURED`
  fallback dict. The level works; the level *growing* does not.
- **Theme sub-scores were silently dead.** `scores/combo.py` published `th_*`
  behind `if lab == "medium"`, and renaming the horizons deleted `"medium"`.
  No exception, nothing empty: 2.5M stale `th_*` rows stayed in the store, so
  every page would have gone on rendering a frozen number as current. Caught
  before a single session was scored post-rename, so the store has no gap. Now
  keyed off `THEME_HORIZON = 20`, a number, with a selftest asserting it names
  a real horizon.
- **`th_sentiment` can never exist** and was documented anyway. Themes publish
  at h=20; no sentiment metric survives 20 days — all nine admit at h=1 and
  nowhere else. Removed from both the declaration and the reference, with the
  reason recorded. The absence *is* the measurement.

## Nine large caps had no fundamentals because of a full stop

SEC writes `BRK-B`. The price feed writes `BRK.B`. Nothing reconciled the
separator, so **Berkshire Hathaway, Brown-Forman (A and B), Heico, Lennar,
Moog, Greif, U-Haul and Biglari** resolved to no CIK and received **no
fundamental data at all**.

It never raised, and that is the point: a missing key returns `None`, and "no
fundamentals" is a perfectly legitimate state for a closed-end fund or an ETF.
Nine operating companies looked exactly like the funds. `ticker_map()` now adds
dotted aliases on read (aliases are *added*, never substituted, and SEC uses no
dots so they cannot collide), and the 2026-08-07 fundamental session has been
rewritten: **3,197 → 3,206 tickers**.

The new `coverage` check exists so this class of bug cannot hide again. It
splits the unscored universe three ways instead of reporting one number:

```
274 of 3480 (7.9%): 7 absent from SEC's ticker file,
                    267 mapped but no facts (funds, non-USD filers),
                    0 unexplained
```

`unexplained` is the bucket worth waking up for, and a dual-class ticker
reappearing as unmapped is a hard FAIL rather than a warning.

The 7 genuinely absent (AEP, FRBA, HIFS, NBN, RCBC, STLN, TOWN) are missing
from SEC's own `company_tickers.json` — verified against a fresh fetch, not the
cache. That file is a convenience list, not a registry. **Their CIKs were
deliberately not hardcoded**: a CIK typed from memory that happens to be wrong
attaches another company's financials to a real ticker, which is far worse than
missing data.

## Non-USD filers are scored now — without an FX feed

**Built and live.** 64 large caps that had no fundamentals at all now score:
BTI, CNQ, BCE, BCS, DB, CCJ, E, CCEP, ASML, ENB, CP, Imperial Oil and 52 more.
The unscored universe went **274 → 210 (7.9% → 6.0%)**, and the tradeable count
with fundamentals went **3,206 → 3,270**.

No exchange rates were used, because most of the metric set does not need any.
A ratio of two figures from the same filing is currency-free: EUR revenue over
EUR assets is the same number as its USD equivalent. The split is now explicit
and asserted in `fund_metrics.selftest`:

- **18 scale-free** — `f_score`, `roe`, `roic`, `gpoa`, `op_margin`,
  `asset_turnover`, `ccc`, `m_score`, `accruals`, `net_debt_ebitda`,
  `current_ratio`, `interest_cover`, `net_issuance`, the four growth rates,
  `mom_12_1`
- **10 need FX and are WITHHELD** — `pe`, `pb`, `ev_ebitda`, `ev_sales`,
  `fcf_yield`, `peg`, `shareholder_yield`, `z_score` (its X4 term is market cap
  / liabilities), and `roic_wacc` / `eva` (WACC's equity weight is market cap)

Withheld, not converted. A wrong rate produces a *plausible* P/E, which is the
dangerous kind of wrong. The value pillar is entirely price-relative, so a
non-USD filer scores on quality, safety and growth and its `fund_cov` can never
exceed 0.75 — surfaced through two new outputs, `currency` and `reports_usd`,
so a page can say "value metrics withheld: reports in EUR" instead of showing a
gap that looks like missing data.

Three things this turned up:

- **The cheap route was per-company, not bulk.** Re-ingesting non-USD rows from
  the bulk sets means 68 quarters × 85 MB. There are only ~129 such filers, so
  one small `companyfacts` request each is three orders of magnitude less
  traffic for the same history. 75 fetched, 75 succeeded, 373,872 facts.
- **"Bulk always wins" was the wrong invariant for these names.** Canadian
  Pacific, Imperial Oil and Enbridge each had **2–8 stray USD rows** in the
  bulk store against 92–178 real ones in companyfacts. Preferring bulk would
  have scored three large caps off two facts each. The rule is now: one
  authority per (cik, quarter) still, but companyfacts is that authority when
  the filer's home currency is not USD, because the bulk copy is incomplete
  *by construction*. Both the selftest and `validate` test this by **accession
  provenance**, not row counts — a count from a multi-quarter read compared
  against one quarter's file proves nothing, and an earlier version of that
  assertion reported 875 rows against an expected 178.
- **`ARS/EUR` is an exchange rate, not an amount in pesos.** The unit parser
  read the numerator as the currency. A composite unit only names a currency
  when its denominator is `shares`.

### Still open here

**History has not been rebuilt.** The 81 quarters of companyfacts history are
stored, so `facts_asof` finds them at any past date — but only the 2026-08-07
score session has been recomputed. Until the rest is rebuilt (~182 sessions,
~1.5 h) history excludes these 64 names while new sessions include them, which
is a discontinuity for anything measuring across the boundary.

**Rebuilding it invalidates the out-of-sample numbers above** and they would
need re-measuring: adding 64 names changes every cross-sectional percentile.
Do the rebuild and the re-measure together, or not at all.

## Left

1. **Nothing blocking.** The remeasure chain above closes the last known gap.
2. **374 names (11%) have no period within 150 days** — mostly dormant or
   delisted filers that genuinely have not filed. Below the audit's warn
   threshold; revisit only if it climbs.
3. **Whether there is any edge at all** is open until the walk-forward reruns.
   If it still decays, the honest next step is to test on BOUNCE CANDIDATES
   rather than the whole market — this is a support-bounce screener, and
   ranking 3,400 names is not the question it exists to answer.

## Traps this project has already paid for

- **A job that reports success after failing is worse than one that crashes.**
  Count successes, exit non-zero, verify against the DATA. A patch script that
  prints "done" without asserting its edit landed is the same bug.
- **A guard that fires on correct data is worse than no guard.** Four now: the
  CSS collision check read class names out of comments, the audit flagged Altman
  Z as a broken percentile, the dead-config check flagged a constant used two
  lines below its definition, and the filer guard rejected real 2009 data.
- **A guard that inspects nothing reports "ok".** The first `claims` check read
  `metrics_doc.METRICS`, which does not exist, and passed cleanly having
  examined **0** of 13 t-stats. Any check counting what it examined must
  *report* that count, and treat zero as a warning rather than a pass.
- **Never compare against a renameable string.** `if lab == "medium"` silently
  stopped every theme sub-score the moment the labels were renamed. Compare
  against the number, and assert the number is reachable.
- **Two identifiers for the same company are two companies** until something
  reconciles them. `BRK.B` vs `BRK-B` cost Berkshire its entire fundamental
  history, and it looked exactly like a closed-end fund with nothing to file.
- **A ratio does not need a currency.** Before building an FX pipeline, check
  which metrics are scale-free — most of the fundamental set is.
- **"NOT REPORTED" IS NOT ZERO.** `a.fillna(0) + b.fillna(0)` is `0` where
  `a + b` is `NaN`, and the two read identically. It made 42% of filers look
  debt-free and gave no-data companies the BEST working-capital score.
- **Measure, then re-measure after anything that changes what you measured.**
  A 10-hour run was killed on an ETA built from a measurement its own earlier
  stage had invalidated.
- **Coverage and freshness are different questions.** Every check asked whether
  data EXISTED; none asked whether it was CURRENT, so 92% of fundamentals sat
  two quarters stale with a green audit.
- **A "last N" slice must sort on meaning, not on string order.** Forward-dated
  disclosures create partitions out to 2028, so `stored_quarters()[-8:]`
  sampled empty future files and silently reverted 153 non-USD filers to USD.
- **The reference date of a check is part of the check.** Auditing against the
  last CLOSED session when a weekly module had not run yet reported "3,479 of
  3,479 have no fundamental score" — a pure false alarm.
- **Two stores of the same thing need a stated authority rule, and "the older
  one always wins" is rarely it.** Preferring the lagging bulk sets discarded
  the only copy of two published quarters.
- **Never type a measured number into prose.** Labels quoted t-stats by hand;
  one quoted a DIFFERENT metric's. Render from the table at build time.
- **A guard that inspects nothing reports ok.** Count what you examined and
  treat zero as a failure of the guard, not a pass of the subject.
- **A function whose caller believes it works is worse than one obviously
  unused** -- `prune_dated` on `reports/`.
- **CSS that looks right can behave wrong.** `position:sticky` is ignored on
  cells of a `border-collapse:collapse` table, and an ancestor with
  `overflow:hidden` re-parents a sticky element. Check the RENDERED page.
- **A stale constant becomes a silent cap** (`dip`'s floor) and **a biased
  control invalidates the reason you excluded something** (the size buckets).
- A float NaN is TRUTHY, `str(nan)` is the truthy STRING `"nan"`, `pd.NA`
  **raises** on `bool()`, and a Series indexed by position looked up by key
  returns None for everything.
- Never name a module after a stdlib one (`profile.py` broke `cProfile`).
- **Judge a signal at the wrong horizon and you will call it dead.**
- **Never mix reporting currencies in one numeric column.**
- Bulk SEC downloads are 128 MB each. Rate-limit them.

---

## Costs (generated)

<!-- GENERATED:costs -->
_Generated 2026-08-29 18:17 — do not edit by hand._

| step | cadence | last | median | slowest (last 5) | budget | runs |
|---|---|---:|---:|---:|---:|---:|
| `universe` | daily | 6s | 7s | 18.2 min ⚠ | 2.0 min | 18 |
| `bars` | daily | 46s | 39s | 54s | 10.0 min | 18 |
| `macro` | daily | 1.5 min | 1.8 min | 2.7 min | 15.0 min | 18 |
| `news` | daily | 4s | 5s | 9s | 10.0 min | 18 |
| `senti_cache` | daily | 5s | 3s | 5s | 10.0 min | 18 |
| `sentiment` | daily | 25s | 13s | 31s | 15.0 min | 19 |
| `bounce` | daily | 39s | 39s | 62s | 15.0 min | 19 |
| `zones` | daily | 82s | 1.6 min | 1.8 min | 30.0 min | 2 |
| `shortvol` | daily | 4s | 3s | 4s | 10.0 min | 16 |
| `hype` | daily | 2.3 min | 97.4 min | 950.5 min ⚠ | 120.0 min | 19 |
| `provider` | daily | 51.3 min | 64.4 min | 75.5 min | 120.0 min | 12 |
| `fundamental` | daily | 14.6 min | 89.2 min | 633.7 min ⚠ | 180.0 min | 18 |
| `sec_facts` | quarterly | 4s | 4s | 31s | 60.0 min | 6 |
| `sec_gap` | weekly | 2.6 min | 176.9 min | 212.9 min ⚠ | 20.0 min | 5 |
| `events` | weekly | 7.8 min | 8.0 min | 13.1 min | 30.0 min | 6 |
| `leaderboard` | weekly | 50.4 min | 28.2 min | 617.6 min ⚠ | 90.0 min | 7 |
| `dip` | daily | 37s | 17s | 68s | 15.0 min | 18 |
| `combo` | daily | 29s | 14s | 90s | 15.0 min | 14 |
| `validate` | daily | 627.6 min | 5.0 min | 627.6 min ⚠ | 60.0 min | 3 |
| `explore` | daily | 15s | 7s | 15s | 5.0 min | 20 |
| `snapshots` | daily | 13s | 3s | 27s | 5.0 min | 24 |
| `profiles` | daily | 34.5 min | 17.3 min | 34.5 min ⚠ | 15.0 min | 19 |
| `retention` | daily | 0s | 0s | 0s | 5.0 min | 17 |
| `dashboard` | daily | 0s | 0s | 0s | 2.0 min | 23 |
| `docs` | daily | 0s | 0s | 0s | 5.0 min | 22 |

**Daily total ≈ 279.1 min.** Weekly adds 213.1 min on top. ⚠ marks a step whose slowest run of the last 5 exceeded its budget.
<!-- /GENERATED:costs -->

## Stores (generated)

<!-- GENERATED:stores -->
_Generated 2026-08-29 18:17 — do not edit by hand._

| store | files | MB | span |
|---|---:|---:|---|
| bars 1d | 122 | 245.7 | 2016-07 → 2026-08 |
| bars 1h | 4 | 0.6 | 2026-05 → 2026-08 |
| bars ETF | 122 | 2.9 | 2016-07 → 2026-08 |
| news | 121 | 168.9 | 2016-08 → 2026-08 |
| sentiment cache | 121 | 11.7 | 2016-08 → 2026-08 |
| scores | 121 | 273.0 | 2016-08 → 2026-08 |
| fundamentals | 69 | 335.1 | 2009q2 → 2026q2 |
| short volume | 73 | 55.0 | 2020-08 → 2026-08 |
| flags | 21 | 1.8 | 2026-07-31 → 2026-08-28 |
| rejects | 21 | 5.2 | 2026-07-31 → 2026-08-28 |
| loose (macro, universe, jobs, study) | 29 | 3.7 | — |

**`data/` total ≈ 1,104 MB.** `reports/` is a further 36 MB across 159 pages.

Measured bytes per stored row (zstd-9): bars **25.0**, news **91.8**, fundamentals **11.6**, scores **3.2**, short volume **12.1**.
<!-- /GENERATED:stores -->

## Modules (generated)

<!-- GENERATED:modules -->
_Generated 2026-08-29 18:17 — do not edit by hand._

| module | metrics | stored sessions | span |
|---|---:|---:|---|
| `sentiment` | 26 | 347 | 2016-09-27 → 2026-08-28 |
| `fundamental` | 61 | 205 | 2016-08-25 → 2026-08-28 |
| `hype` | 20 | 322 | 2016-10-25 → 2026-08-28 |
| `dip` | 10 | 249 | 2016-09-27 → 2026-08-28 |
| `combo` | 15 | 204 | 2016-11-04 → 2026-08-28 |
<!-- /GENERATED:modules -->

## Study (generated)

<!-- GENERATED:study -->
_Generated 2026-08-29 18:17 — do not edit by hand._

1,536 cells measured across 95 metrics, horizons [1, 5, 20, 60], buckets ['all', 'large', 'mid', 'small'].

**58 metric(s) reach |t| >= 2 at their best horizon.**
| metric | module | best h | IC | t | hit |
|---|---|---:|---:|---:|---:|
| `fund_score` | fundamental | 20 | +0.0254 | +5.21 | 65% |
| `z_score` | fundamental | 60 | -0.0567 | -4.63 | 22% |
| `dip_gate` | dip | 20 | +0.0151 | +4.33 | 66% |
| `net_issuance` | fundamental | 20 | -0.0307 | -4.17 | 41% |
| `avg_trade_size` | hype | 60 | +0.0463 | +4.11 | 74% |
| `roic` | fundamental | 20 | +0.0302 | +4.08 | 64% |
| `interest_cover` | fundamental | 20 | +0.0345 | +4.05 | 61% |
| `roe` | fundamental | 20 | +0.0324 | +3.95 | 62% |
| `ev_sales` | fundamental | 20 | -0.0345 | -3.91 | 39% |
| `f_score` | fundamental | 1 | +0.0258 | +3.91 | 63% |
| `roic_wacc` | fundamental | 20 | +0.0319 | +3.71 | 63% |
| `du_asset_turnover` | fundamental | 20 | +0.0218 | +3.69 | 58% |
| `asset_turnover` | fundamental | 20 | +0.0218 | +3.69 | 58% |
| `quality_score` | fundamental | 20 | +0.0262 | +3.58 | 61% |
| `combo_h60_cov` | combo | 20 | +0.0203 | +3.47 | 58% |
| `safety_score` | fundamental | 1 | +0.0147 | +3.46 | 60% |
| `combo_h20_cov` | combo | 20 | +0.0215 | +3.43 | 58% |
| `fund_rank` | dip | 20 | +0.0187 | +3.37 | 62% |
| `du_leverage` | fundamental | 20 | +0.0260 | +3.32 | 58% |
| `combo_h1_cov` | combo | 60 | +0.0311 | +3.29 | 68% |
<!-- /GENERATED:study -->

