# Project log

## 2026-08-08 (16:20) — a job that lied, and the foreign-filer fix that works

### My own refetch reported success after failing 51 times

`refetch_facts.py` printed `70/70 quarters, 5,821,116 facts` and `DONE`. It had
fetched **19 of 68**, then taken **51 consecutive ConnectionErrors** from 2014q1
on — SEC throttled it after ~19 back-to-back 128 MB downloads. No rate limit, no
retry, and the summary counted *attempts*.

This is the "partial result reading as a whole one" failure this project has
already fixed twice elsewhere, and I reintroduced it. `backfill()` now:
rate-limits at 2.5s, retries 3x with backoff, **counts successes not attempts**,
and **exits non-zero if any quarter failed**. `refetch_facts.py` is replaced by
`backfill(force=, newest_first=)` so there is one fetch path, not two.

`newest_first` matters: `facts_asof` looks back 12 quarters, so the recent ones
restore current scoring long before deep history finishes.

### Foreign filers: forms were necessary, tags were the actual fix

Accepting 20-F/40-F alone changed nothing. Measuring said why — downloading
2025q1 directly:

- The bulk sets **do** contain them: 337 20-F + 109 40-F, **317,268 fact rows**.
- **Only 16.5% matched our tag map.** They report under IFRS, whose concept
  names are near-misses.

| IFRS | US-GAAP we had |
|---|---|
| `Revenue`, `RevenueFromContract**s**WithCustomers` | `Revenues`, `RevenueFromContractWithCustomer…` |
| `Equity` | `StockholdersEquity` |
| `CostOfSales` | `CostOfGoodsSold` |
| `ProfitLossBeforeTax` | `IncomeLossFromContinuingOperationsBefore…` |
| `CashFlowsFromUsedInOperatingActivities` | `NetCashProvidedByUsedInOperating…` |

19 aliases added, 69 → 88 tags. **Verified on the first refetched quarter:
2025q4 carries 4,295 rows from 70 foreign CIKs, up from zero.** Full coverage
delta gets measured against the 588 baseline when the refetch completes — not
claimed in advance.

### The study died at 42%, having skipped hype

583 of 1,385 cells, no traceback. Covered `dip`, `fundamental`, `sentiment`;
**`hype` never ran** — the module with the most open questions. Resumed.

Both jobs now run as **Windows Scheduled Tasks** rather than bare detached
processes, since two in a row vanished without a trace. A task survives a parent
exit and records its own exit code.

### Study finding that already changed a conclusion

`z_score` at **h=60** measures **IC -0.0597, t=-5.44, hit 18%** — far stronger
than the -3.37 recorded at h=20, and in the same inverted direction. The distress
premium is a SLOW effect, and measuring it at h=20 understated it by a third.

Alongside the sentiment result (every metric peaks at h=1), that is two families
whose true horizon was not the one being tested.


## 2026-08-08 (late) — the study starts paying, and a false negative avoided

### Sentiment works at h=1 and would have read dead at h=20

The first study results justify the four-horizon decision outright:

| metric | best h | IC | t |
|---|---|---|---|
| `f_score` | **1** | +0.0282 | 4.03 |
| `sent_mean_90d` | **1** | +0.0213 | 3.71 |
| `sent_net_90d` | **1** | +0.0188 | 3.24 |
| `sent_mean_5d` | **1** | +0.0164 | 3.07 |
| `sent_mean_30d` | **1** | +0.0144 | 2.99 |
| `dip_gate` | 20 | +0.0181 | 2.95 |
| `roic` | 20 | +0.0133 | 2.93 |

`sent_mean_30d` decays 2.99 -> 2.22 -> **1.21** -> 1.74 across h=1/5/20/60. A
single-horizon study at h=20 would have labelled every sentiment metric useless.
That is the false negative the design was meant to prevent, and it happened.

`f_score` topping the table **at h=1** is the surprise — a Piotroski score is
usually read as a slow signal.

### Wired in, and self-updating

`metrics_doc` gained `horizon_curve()`, `best_horizon()`, `size_split()` and
`validated()`, all computed from `data/_factor_study.parquet`. The metrics page
now shows a small IC-by-horizon bar chart per metric, green where |t| >= 2, so a
glance says "fast", "slow" or "nothing". Nothing is hand-listed; entries went
24 -> 41 while the study was still running.

Explore gained a **column chooser** with an `all / validated / none` preset,
persisted in `localStorage`. "validated" is computed from the study, and
currently narrows 25 columns to 5.

### Module explainers

"What IS hype" was never stated anywhere. `MODULE_DOC` now carries two or three
sentences per module at the top of its section — including that hype is a
MAGNITUDE not a recommendation, and that dip is a gate rather than a blend.

### Foreign filers

`fundamentals.py` now accepts **20-F and 40-F** alongside 10-K/10-Q. POET is a
Canadian 40-F filer, which is why it had a CIK and no financials. Measured
before the refetch: 3,480 universe, 3,464 with a CIK, 2,876 with facts, **588
with a CIK and no facts**.

A `--coverage` report was added rather than assuming the fix works: these filers
often use IFRS tags that `TAGS` does not carry, so accepting the forms is
necessary but may not be sufficient. The store is being refetched now; the real
recovered count gets measured, not claimed.


## 2026-08-08 (evening) — direction-aware bars, the study, docs that regenerate

### The P/E bar was wrong, not just confusing

A screenshot asked why Apple's P/E showed `41.13` with bars at 77 and 93.
`41.13` is the VALUE (41x earnings); 77 and 93 are percentiles — dearer than 77%
of tech peers and **93% of its own history**. And it rendered as a **full green
bar**. `fund_metrics.REGISTRY` has carried `direction=-1` for P/E all along and
`pct_bar()` never consulted it, colouring green for anything above the median.

Now colour follows direction and LENGTH still means percentile — if both moved,
a short bar could mean "low value" or "bad" and the reader could not tell which.
Unknown-direction metrics (every hype metric, which the module deliberately
refuses to sign) render **neutral grey**; colouring them would invent a claim
the code declines to make.

Labels were duplicating descriptions too — `pe` printed "price / earnings" as
both its title and its explanation. Explicit `DISPLAY` names now.

### A documented range that was wrong, and a checker that was also wrong

Peak severity was documented "0-3". Actual distribution: 2.4 to 44, **median
16.85** — so the value in the screenshot was exactly average, not extreme. A
wrong range is worse than none, because it is trusted to judge whether a number
is unusual.

So `check_ranges()` compares every documented range against the live data. It
immediately flagged eight more — and **the checker itself was broken**: `"0-100"`
parsed as `[0, -100]` because the hyphen read as a minus sign. All eight were
false positives. Fixed, and now part of the selftest, so a wrong range fails.

### `study.py` — 1,385 cells, running

4 modules x horizons **1/5/20/60** x 4 size buckets (892 names each). One
horizon manufactures false negatives: sentiment's only surviving signal is at
h=1 and reads dead at h=20. Results land in `data/_factor_study.parquet`, and
**`metrics_doc.MEASURED` overlays itself from that file automatically** — 24
hand-typed entries became 33 while the study was still running, with no edit.

Building the size buckets exposed another silent gap: **`mktcap` had 0 stored
rows.** It is declared in `metrics()` but `FM.compute()` consumes the price
inputs without passing them through, so `mktcap`, `beta` and `mom_12_1` were
never written. No size analysis was possible at all. Fixed; mktcap now writes
2,676 rows.

### `docs.py` — the numbers regenerate themselves

Every hand-written cost table here has gone stale within a day: the leaderboard
budget was 78% wrong once it tested four modules, and `reports/` was called
badly bloated when it was 19 MB. So timings come from `_jobs.parquet` and sizes
from the stores, rewritten between `<!-- GENERATED:name -->` markers as the last
orchestrator step (now 20 steps).

**Measured, not estimated:** daily total **~38 min**, weekly adds **~32 min**,
`data/` **935 MB**, `reports/` 20 MB across 77 pages.

### Also

- **Sticky horizontal scrollbar** on financial tables — it used to sit below a
  30-row table, so reaching a later quarter meant scrolling down, dragging
  right, then scrolling back.
- **Any of 3,480 tickers is searchable** from the profiles index, not the 37
  built. Unbuilt names render on demand in ~20s with a real status line
  ("reading bars and fact store... scoring against peers...") rather than a
  blank 20-second stare.
- **`serve.py --idle-exit 30`** plus `Screener.bat`: one click starts the server
  and opens the hub, and it shuts itself down after 30 idle minutes. Not a
  daemon — which is what "runs, then terminates when done" actually needs, since
  a `file://` page cannot start a process.


## 2026-08-08 (group 2) — the metric dictionary, and a 10x profile speedup

### `metrics_doc.py` — 97 metrics documented, with a drift guard

One registry feeding three renderings: `reports/metrics.html`, the `title=`
tooltip on every explore column header, and the fuller panel. Writing the same
explanation into three templates is how they drift, and a stale explanation is
worse than none because it is believed.

Nothing is re-typed. `fund_metrics.REGISTRY` already carried
`(pillar, direction, description)` for 29 fundamentals and `ui.METRIC_LABELS`
merged that with the other modules' labels; this adds only what they lacked —
how to read the number, its typical range, and whether it has been measured.
28 keys needed new labels (16 sentiment, 8 fundamental, 3 hype, 1 dip).

**The drift guard is the point.** `selftest()` asserts every metric emitted by
every registered module has an entry, that no entry names a dead metric, and
that `PROVENANCE` agrees with `factor_lab.NON_SIGNAL` — so the reference page
and the leaderboard cannot disagree about what counts as a signal. Add a metric
and the test fails until it is documented.

**Measured results are on the page now.** `hype_score` t=-1.36, `dip_score`
t=0.24 and `attention_score` t=0.07 lived only in this log, where no dashboard
reader would see them. Each entry carries a verdict pill: *strong*,
*suggestive*, *not significant*, *not tested*, or *provenance*. 24 of 97 have a
measured number.

**#14 answered directly:** a "How the comparison columns work" panel states that
vs-industry is the percentile among same-sector names for that session (>= 5
peers or n/a), vs-history is the percentile within that ticker's own stored
series (>= 8 observations or n/a), and that a hatched bar means no data, which
is not the same as ranking last.

### The profile page was 58s per build. It is now ~20s.

Profiling one build instead of guessing found it immediately:
**`scores.sessions_stored()` was 51 of the 58 seconds.** It opens every score
partition to list a module's sessions, `latest_scores()` asks once per module,
and nothing cached it — 511 parquet reads per profile. The answer was never
wrong; it was simply recomputed for every ticker.

Three caches, each invalidated properly:

| cache | before | after |
|---|---|---|
| `scores.sessions_stored` (all modules in ONE pass, cleared on `write()`) | 60s for four | 27.5s once, then 0.000s |
| `stock_profile.peer_percentiles` (+ the session read under it) | re-read per ticker | 0.8s cold, 0.29s for four more |
| `fundamentals.prime_history` in `rebuild_profiles.py` | missing entirely | ~3x faster overall |

The last one is worth noting: the orchestrator step primed the fact store but the
standalone rebuild script did not, so the same work was fast in one path and slow
in the other. Two call sites, one primed — the same class of gap as the cache-key
mismatch found earlier.


## 2026-08-08 (later) — two regressions, and a guard that found three more

### #10 profile showed only Revenue

`stock_profile.py` rebound **`periods`** — the list of period LABELS — to the
growth-lag list `[(1,"YoY")]` inside the row loop. Every row after the first
then reindexed to a single element. Measured: Revenue 14 cells, every other
line item 3. Nothing raised and no value was wrong; the table was merely ragged.

Renamed to `lags`, and added the guard that catches the whole class: a selftest
that renders both frequency tables and **asserts every row has the same cell
count as the header**. Now: annual 22 rows x 14, quarterly 31 rows x 18.

### #4 explore's filter bar vanished

A CSS collision I introduced with the shared stylesheet. `ui.CSS` defines
`.bar{height:7px;overflow:hidden}` for percentile bars; explore's filter row was
also `class="bar"` and overrode only `display`, so it inherited the 7px height
and collapsed to a sliver. Renamed the shared component to `.pctbar`.

**The guard then found three more.** A check asserting no class is defined in
both `ui.CSS` and a generator's local sheet flagged `note`, `sub`, `wrap` in
explore and **nine** in dashboard — both files still carried their full
pre-`ui.py` stylesheets, so half of every shared rule was being silently
overridden. Both reduced to page-specific rules only.

The guard also caught a bug in itself: it read class names out of a CSS
*comment* explaining which classes not to redefine. Comments are stripped first.

### #1 trade_shrink flipped, provenance excluded from the leaderboard

`trade_shrink` was `-log(short/long)` on the argument that shrinking prints are
the retail-attention signature. Measured: raw `avg_trade_size` **t=3.06** vs the
negated version **t=1.25** — larger prints predict better. Renamed to
`trade_size_trend` and un-negated. **Renamed rather than silently flipped**: a
stored series whose sign changes under a stable name is the worst kind of quiet
break for anything reading history.

`factor_lab.NON_SIGNAL` now excludes provenance columns from every leaderboard —
`hype_cov` (t=3.27) and `bars_used` (t=2.78) had outranked every real metric
because complete data correlates with being an established company. Excluded per
module: hype 3, dip 2, fundamental 4. The count is printed in the header so the
exclusion is visible rather than silent.


## 2026-08-08 (04:00) — MEASURED: neither new composite works

The leaderboards were finally readable. Both `hype_score` and `dip_score` were
built with defensible structure and explicit warnings that nothing had been
measured. Measured, **neither survives**.

### dip, h=20, 43 dates

| metric | IC | t | hit |
|---|---|---|---|
| `quality_rank` | +0.0216 | 2.23 | 67% |
| **`dip_gate`** | **+0.0181** | **2.95** | 65% |
| `drawdown` | −0.0141 | −0.53 | 42% |
| `growth_rank` | +0.0137 | 1.50 | 60% |
| **`dip_score`** | **+0.0049** | **0.24** | **37%** |
| `not_extended` | +0.0047 | 0.39 | 51% |
| `senti_gap` | −0.0041 | −0.31 | 49% |

**The thesis does not hold.** Every "depressed price" leg -- `drawdown`,
`senti_gap`, `not_extended` -- measures approximately zero. What works is the
quality gate, which is `fund_score` under another name and was already known.

Worse than that: **`dip_score` (t=0.24) is far weaker than `dip_gate` alone
(t=2.95)**. Ranking by depression *inside* the gate actively destroys the signal
the gate provides. And `drawdown` carries a NEGATIVE IC -- deeper drawdown
predicted LOWER forward returns, the opposite of "buy the dip". Falling knives
are falling knives.

The structure was right to gate rather than blend; the gate is the only part
with evidence behind it.

### hype, h=20, 117 dates

| metric | IC | t | note |
|---|---|---|---|
| `ps_ratio` / `premium_score` | −0.0285 | −2.26 | negative -- the value effect |
| **`avg_trade_size`** | **+0.0197** | **3.06** | **the opposite of my prior** |
| `hype_cov` | +0.0195 | 3.27 | **artifact, not signal** |
| `turnover` | +0.0180 | 2.02 | |
| `bars_used` | +0.0140 | 2.78 | **artifact, not signal** |
| `hype_score` | −0.0132 | −1.36 | composite not significant |
| `short_ratio` | −0.0046 | −0.77 | 71 dates only |
| `attention_score` | +0.0005 | 0.07 | pure noise |

**I had the trade-size hypothesis backwards.** The module argues at length that
SHRINKING average print size is the retail-attention signature, and
`trade_shrink` is its negation. Measured: `avg_trade_size` **positive, t=3.06**
-- LARGER prints predict higher returns -- while `trade_shrink` sits at t=1.25.
The institutional-accumulation reading beats the retail one. `avg_trade_size`
was only emitted as a diagnostic; the composite carries the losing side.

**Two rows are artifacts and must not be read as signal.** `hype_cov` (t=3.27)
and `bars_used` (t=2.78) measure DATA AVAILABILITY -- a name with full coverage
and long history is an established company. They rank high precisely because
they are not signals. This is the leaderboard doing its job: it ranks whatever
correlates, and reading the top row without asking what it *is* would have
promoted "has complete data" to a factor.

`ps_ratio` negative is the one honest, reusable finding: expensive-on-sales
predicts lower returns, consistent with the `z_score` inversion already recorded.

### What this changes

Nothing gets promoted. `hype_score`, `dip_score`, `attention_score` and
`stretch_score` stay unweighted and out of any composite. `premium_score` has a
real negative sign and could be tested as a short/avoid leg. The gate-not-blend
structure in `dip` is validated even though the score is not.

Caveats: many correlated tests, so the top row of each table is the most likely
to be luck; h=20 only; `short_ratio`/`short_surge` have just 70-71 dates because
FINRA history starts 2020.


## 2026-08-08 (03:00) — full pipeline green on the new UI

`orchestrator.py --force`: **19 steps, 0 failed, 6832s (114 min).**

### The measurability blocker is gone

```
[leaderboard] 3210s -- sentiment:16  fundamental:41  hype:20  dip:8
```

Previously `hype:0 dip:0`. The rebuild gave hype 118 stored sessions and dip 44,
so `factor_lab` finally has enough dates to test them. **28 new testable metrics
that did not exist as measurements before.**

### Two budgets were wrong, not two steps

`leaderboard` 3210s vs a 1800s budget, `snapshots` 975s vs 900s. Neither is a
regression: the leaderboard budget was sized when it was silently testing two
modules instead of four, and the snapshot budget against a steady-state day
rather than a cold start building 17 pages. Corrected to 5400s and 2400s from
the measurements, with the reasoning in the registry.

### A cache that never hit

`profiles` took 1569s against a 900s budget. `fundamentals.prime_history()` was
added to stop `history()` re-scanning all 68 quarters per ticker — and it
**cached under a key nothing ever looked up**. The profile page primes with
quarterly params (`start_q 2019q3`) then asks for annual (`2012q3`), so every
lookup missed and did the full scan anyway.

The lesson is that the optimisation *looked* correct and the step still ran at
the unoptimised speed; only timing it end-to-end exposed it. Fixed so any cached
window starting at or before the requested one serves the request, and
`prime_history` caches the widest window either frequency can ask for.
**Measured: 6 tickers x both frequencies, 44.7s -> 0.49s after one 14.6s prime.**

### Verified

56 pages, **0 broken links**, every dashboard and profile carrying the same nav.
20 dated snapshots reachable from the date picker. All selftests pass.


## 2026-08-07 (10:30) — one visual system across every page

The data layer had outrun the presentation layer. Six report generators grew
independently and each hand-rolled its own stylesheet, header and metric row, so
pages that were individually fine read as unrelated products.

### What was actually wrong, measured against the built output

| symptom | cause |
|---|---|
| inconsistent | **6 stylesheets, 2 vocabularies** — `--ink/--muted/--accent/--panel` vs `--fg/--mut/--acc/--card` |
| explore unreachable | never added to `dashboard.DASHBOARDS`; nothing linked it |
| "+18 more" dead end | hub printed `pages[:12]` + a string. 37 profiles existed; the count was stale too |
| profile looked outdated | scores rendered as raw `snake_case` in a 2-column list |
| **radar labels truncated** | viewBox `0 0 250 250`, but "sentiment" ends at x=280 and "technicals" starts at x=−30 — they displayed as "sen" and "cas" |
| no QoQ view | `fundamentals.history(t,"Q")` worked; nothing exposed it |
| no history | **181 fundamental / 153 sentiment / 118 hype / 44 dip sessions stored and invisible** |

### `ui.py` — the fix at the root

One stylesheet, one nav, one set of components (`sparkline`, `radar`, `chip`,
`card`, `metric_row`, `pct_bar`, `session_picker`, `page`). Every generator now
supplies body content only.

`METRIC_LABELS` is built from **`fund_metrics.REGISTRY`**, which already carried
`(pillar, direction, description)` per metric. Re-authoring 29 labels by hand
would have drifted from the definitions they describe; 70 labels come out of it.

The two legacy dashboards were migrated by **aliasing** their old variable names
onto the new palette rather than rewriting their markup — consistency gained
without touching a single working layout rule.

### The radar bug is the interesting one

It was invisible in code and obvious on screen: a square viewBox cannot contain
labels placed at radius x1.19 on the left and right axes. Now `-62 -6 374 266`,
with a selftest that computes each label's bounding box and asserts it falls
inside — the class of bug that only a rendered check catches.

### Per-metric context

Each row now shows value + **vs industry** (percentile among sector peers this
session) + **vs history** (percentile within that ticker's own stored series).
Both use existing plumbing: `macro.load_sector_map()` and `scores.read()`.

A missing percentile renders as a **hatched track labelled n/a**, never a
zero-width bar — a grey empty bar is indistinguishable from "worst in sector",
which is the most misleading thing a metric row can do. `pct_bar(None)` and
`pct_bar(0)` are asserted to differ.

Self-history needs >= 8 observations; below that it returns absent rather than
50, because a made-up midpoint reads as "perfectly average" when the truth is
"unknown".

### Quarterly toggle, and QoQ labelled correctly

Both frequency tables are built into the page and toggled client-side. The
subtlety: on a quarterly table the 1-period change is **quarter-over-quarter,
not year-over-year**, so labelling it "YoY" would simply be wrong. The quarterly
pane therefore shows **both** QoQ (1 period) and YoY (4 periods) — QoQ alone is
misleading for any seasonal business, where Q1 always sits below Q4. Verified:
annual pane 9 growth rows all YoY; quarterly pane 18, half QoQ and half YoY.

### History is now reachable

`explore.py --session` writes dated snapshots and `ui.session_picker` navigates
between them. A new `snapshots` orchestrator step keeps the most recent
`SNAPSHOT_SESSIONS = 20` built, skipping any that already exist so the
steady-state cost is one snapshot a day rather than twenty.

20 was chosen against the alternative of exposing all 181 stored sessions: each
snapshot is ~0.8 MB of HTML, and past ~20 "reachable as a plain file" stops
being worth the disk. With `serve.py` running, any stored session can be
rendered on demand instead.

### Verified

19 orchestrator steps. All selftests pass. **53 pages, 0 broken links** with
`<script>` bodies stripped (the JS template-string false positive from before).
Every page carries the same 6-entry nav with the correct entry marked. Layout
measured in the browser: no horizontal overflow, radar 340x242 unclipped, all
four axis labels reading in full.


## 2026-08-07 (09:00) — FINRA short volume into hype

### Short INTEREST is no longer free. Short VOLUME is.

The ask was days-to-cover. FINRA's biweekly short-interest endpoint
`cdn.finra.org/equity/otcmarket/biweekly/shrt<date>.txt` returns **403
AccessDenied**, tested across three settlement dates (2026-07-31, 07-15, 06-30).
Every other free route is a scrape behind a bot wall or a paid API, so
**days-to-cover is a genuine gap, not an oversight.**

What IS free and works: the **Reg SHO daily short-volume file**,
`cdn.finra.org/equity/regsho/daily/CNMSshvol<date>.txt` — 200, pipe-delimited,
`Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market`. For a DAILY
module it is arguably the better input anyway: biweekly short interest is up to
two weeks stale when it publishes, this is yesterday's pressure.

Measured history floor: **2020-01 works, 2017-01 and 2016-08 both 403.** Six
years, which covers hype's 252-session baseline.

`finra.py` backfilled **4,449,390 rows over 1,506 sessions in 2.7 minutes,
54 MB**, 0 unavailable. Only universe tickers are stored — the raw files carry
~9,900 symbols including OTC, and filtering to ~3,400 cuts the store from
roughly 225 MB to 54 MB of names something actually scores.

### Two new hype members, in the attention pillar

`short_ratio` = mean(short_vol / total_vol) over 21 sessions.
`short_surge` = log(21d ratio / 252d ratio) — is that pressure unusual **for
this name**, since some names are structurally 50% short-flagged by how their
market makers report.

They join `attention` rather than getting their own pillar: a contested name is
one people are actively taking the other side of, which is the same phenomenon
as unusual volume.

**The RATIO is used, never the raw level.** FINRA's denominator is
FINRA-reported volume, not the full consolidated tape, so `short_vol` is not
comparable to the bar store's `volume` column. Dividing inside the same source
is the only safe comparison — mixing the two denominators would have produced a
plausible-looking number that meant nothing.

Live check, 99.5% coverage (792 of 796): GME tops `short_ratio` at **0.636**,
then PLTR 0.522, AAPL 0.492, TSLA 0.453. KO has the most negative `short_surge`
(-0.284) — declining short pressure on a staple. Sensible in both directions.

### The series had to be rebuilt, not extended

The 118 stored hype sessions were computed before short volume existed, so they
carry 9 of 11 composite members. Leaving them would make the stored series a
**mix of two different metric definitions**, and `factor_lab` would be measuring
a moving target rather than a signal. `scores.catchup` gained `rebuild=True` to
overwrite rather than skip. dip is rebuilt after hype because its
`not_extended` leg reads hype's output.

### Orchestrator is now 18 steps

`shortvol` runs before `hype`, and `hype` declares `depends_on=("bars",
"shortvol")` so a FINRA outage marks hype `blocked` rather than letting it score
on 9 of 11 members while reporting full coverage.

## 2026-08-07 (08:00) — full pipeline green, and the measurability gap

### The forced end-to-end run

`python orchestrator.py --force`: **17 steps ran, 0 failed, 3,247s (54 min).**
Every step exercised through the real scheduled path, not by hand.

| step | measured |
|---|---|
| leaderboard | 1,426s — now the heaviest by far, 4 modules not 2 |
| profiles | 941s, **over its 900s budget**, marked `slow` |
| events | 498s on the full 10y news store |
| senti_cache | 180s |
| hype | 28s for 3,420 names |
| dip | 20s |

### hype and dip were built but UNMEASURABLE, and nothing said so

The leaderboard reported `sentiment:16  fundamental:41  **hype:0  dip:0**`.

Not a bug: `factor_lab` requires `min_dates=10`, and both new modules had
exactly **one** stored session against sentiment's 153 and fundamental's 181.
`fundamental` is only measurable because `fund_screen.py --catchup` built its
history — and **neither new module had a catchup runner at all**.

This is a structural gap worth naming: a score module can be registered,
scheduled, rendered on three dashboards and still be incapable of ever being
tested, with nothing in the system objecting. The module looked healthy
everywhere a human would look.

Fixed by hoisting `scores.catchup(module, every, frm)` — **the third copy of
that loop**; `fund_screen` and `senti_screen` each had grown their own. It
anchors sampling at the recent end (sampling from the old end drops the newest
session whenever the span is not an exact multiple of `every`) and names failed
dates rather than skipping them, because a silently missing date is a hole
`factor_lab` cannot distinguish from a quiet market.

### `profiles` swallowed 7 of 30 failures and still reported success

The step counted successes and threw the exceptions away, so 23 of 30 pages
appeared and the run was green. The cause was **the project's own documented
gotcha, hit again in new code**: a float NaN is TRUTHY, so `if m.get("sic")`
passed for a missing SIC and `int(nan)` raised. ASM, DPRO, DXYZ, EXK, POET,
SGML and VLN — all ADRs or foreign filers with no SEC XBRL.

Both halves fixed: the NaN guard, and the step now names every failure in its
`detail` so a partial result can never read as a whole one again.

## 2026-08-07 (morning) — explorer, dip module, and a REIT-shaped revenue bug

### The bug: 79 names had revenue understated by up to 1,703x

Rexford Industrial's FY2025 10-K reports **both** `Revenues` = $1,003,133,000
(the total) and `RevenueFromContractWithCustomerExcludingAssessedTax` = $589,000
(an ancillary line). `TAGS["revenue"]` listed the contract tag first and alias
preference is list order, so REXR's revenue was read as **$589K instead of
$1.0B**, producing a P/S of 14,350.

Apple is why it survived so long: AAPL reports *only* the contract tag, and for
AAPL it genuinely is the total. The ordering looked correct on every mega-cap
and was wrong for anyone with mixed revenue streams.

**Measured: 79 of 2,427 names with revenue (3.3%) corrected upward**, worst
cases REXR 1,703x, AVB 432x, OSCR 409x, CTRE 389x, ESS 201x, UDR 151x — REITs
and insurers, i.e. **entire sectors** wrong on every revenue-derived metric
(margins, `ev_sales`, `asset_turnover`, `rev_growth`, `ps_ratio`).

Fixed with `AGGREGATE_MAX_CONCEPTS = {"revenue"}`: for these the largest alias in
a period wins, not the first listed. The max is safe *specifically* because a
revenue component cannot exceed the revenue total — that argument does not
generalise, so the set stays deliberately small, and `_selftest_alias_max`
asserts non-max concepts still honour alias order.

### `explore.py` — the whole universe, sortable and filterable

`reports/explore.html`: 3,464 tickers x 24 metrics across all four modules.
Header-click sort, text/sector/numeric-range filters, ticker links to the
profile page, glyph links to TradingView.

Two decisions that matter:

- **Rows render on demand.** 3,464 x 24 cells of DOM up front costs seconds;
  painting only the visible slice keeps sorting instant on the full universe.
- **A missing metric sorts LAST in both directions**, never as 0, and a numeric
  filter excludes it rather than treating it as zero. Sorting NaN as zero would
  put every unscored company at the top of an ascending P/E screen — the most
  dangerous possible presentation of "we don't know".

### `scores/dip.py` — the dip thesis, as a gate not a blend

The user's idea: strong fundamentals + strong growth + price down for a
non-business reason = opportunity.

**Implemented as a GATE then a RANK, and that is the whole design.** A weighted
average of "good fundamentals" and "big drawdown" lets a collapsing company
score highly because the size of the fall compensates for the weak balance
sheet — exactly the falling knife the thesis exists to avoid. So: `fund_score`
must be top 30% (and `growth_score` top 50%) to qualify at all; below the gate
there is **no score, not a low score**. Verified on live data — 684 of 3,445
qualified, every scored row passed the gate, every ungated row has `null`.

Depression components: `drawdown` (below its own 1y high), `senti_gap`
(inverted 30d sentiment — sentiment's only overlap-corrected signal is at h=1
and reverses at the extremes, which is the shape this needs), and
`not_extended` (inverted `extension_pct`, which makes the user's own "a good
stock at a record high is less exciting" measurable). `mktcap` is emitted so
`factor_lab` can slice by size — their caveat that this may only work for large
caps is testable, not assumed.

**Nothing here is measured.** `factor_lab --module dip --leaderboard` decides.

### Also

- **Any ratio can now carry a trend.** `stock_profile.DERIVED` gained
  `op_margin`, `fcf_margin`, `ebitda_margin`, `roe`, `roa`, `current_ratio`,
  `debt_to_equity`, `asset_turnover`, `rnd_intensity`; each can go in
  `financials.rows` or `growth.rows` via `settings.py`.
- **Margin trends render in percentage POINTS, not percent.** A margin going
  2% to 3% is "+50%", which reads like a different business; "+1.0pp" is what
  an analyst wants. Shading uses a tighter scale for pp (8pp saturates vs 60%).
- `hype` verified at full scale: **3,420 names in 23s, 296 KB/session
  (~76 MB/yr)**. `hype`, `dip`, `explore` and `profiles` are all registry steps
  now, so they run on the same schedule as everything else.

## 2026-08-07 (overnight) — per-stock view, settings, live buttons

### `stock_profile.py` — the Wisesheets-style sheet

`reports/stock/<TICKER>.html`. Years across the top, line items down the side,
inline SVG sparklines, YoY rows shaded red/green, a four-axis score radar, sector
and SIC chips, and a TradingView deep link. Layout comes from `settings.py`, so
which blocks appear and in what order is configuration, not code.

**It renamed itself.** The file was `profile.py` for about ten minutes, which
**shadows the standard library `profile` module and breaks `import cProfile`** —
Python's own error message suggests the rename. Never name a module in the
project root after a stdlib module.

Two limits are printed on the page rather than hidden:

- **The history is deliberately NOT point-in-time.** `fundamentals.history()`
  lets the latest restatement win, which is right for reading and wrong for
  backtesting. It must never feed `factor_lab`; `facts_asof` remains the
  point-in-time door.
- **Monthly and half-yearly views do not exist.** The brief asked for four
  frequencies; the fact store is quarterly, so annual and quarterly are real and
  the other two would be interpolation. An interpolated fundamental is a made-up
  number wearing a real one's clothes.

### The split artifact, found by reading the output

AAPL's sheet showed **EPS 2017 → 2018 at −68%** in a year net income rose 23%.
That is the 4:1 split, not earnings. Each 10-K restates only the comparative
years it carries, so a period last reported before a split keeps its old
denominator forever.

Not silently fixable — the fact store has no split factor, and joining bars.py's
split data here would create a second source of truth for the same number. So it
is **detected and flagged**: any per-share row whose YoY diverges from net-income
YoY by more than 35 percentage points raises a warning naming the years. AAPL
correctly flags 2018.

### `fundamentals.history()` — deep history from few filings

Keyed on `ddate`, not `fy`. Each 10-K carries comparatives — Apple's FY2023
filing reports 2021, 2022 *and* 2023 revenue — so keying on the filing's own year
would discard two thirds of the data and mislabel the rest. Latest `filed` wins
within a period, so restatements supersede. 14 years of Apple financials come
back matching the real figures (FY2025 revenue $416.2B, net income $112.0B).

### `serve.py` — buttons that actually run a job

The ask was a button rather than a copied command. A static file cannot spawn a
process and a resident server would discard the no-daemon design, so this is a
**separate, hand-started, opt-in** program. Started, the hub's run buttons go
live; not started, the page behaves exactly as before and the copy-able commands
remain. Progressive enhancement, gated on `location.protocol !== 'file:'`.

It executes code on request, so it is written for hostile input. All six guards
verified with live requests:

| test | result |
|---|---|
| serves the hub | 200 |
| `POST /api/run/*` with no guard header | **403** |
| unknown step name, header present | **409**, never reaches a process |
| `GET /../.env` traversal | **404** |
| CORS preflight | **403** |
| valid step while the overnight chain held the lock | **409** "a run already holds the lock (pid 592)" |

Binds `127.0.0.1` only, spawns with an argument list rather than a shell string,
and validates every step name against `orchestrator.BY_NAME`.

### `settings.py` — what the profile view shows

`data/_profile.json`, versioned. Blocks rather than a flat metric list, because
"2 plots and 1 support level" cannot be expressed as metric names. **Unknown
blocks are carried through, not deleted** — a validator that drops what it does
not recognise is indistinguishable from data loss across an upgrade.

### One more shadowing bug

`render(d)` in `dashboard.py` gained a local `d = config.REPORTS / ...` for the
profile-card directory listing, shadowing the data dict and turning every later
`d["steps"]` into a Path subscript. Caught immediately by the page failing to
build — the useful kind of failure.

## 2026-08-07 (later) — score module 3 (hype), and a 19% coverage bug

### The bug worth reading first: 549 names had no market cap

`facts_asof` classifies weighted-average share counts as FLOW concepts, so it
returns them **suffixed**: `shares_diluted_ttm`, `shares_basic_ttm`. Both
callers looked for `shares_diluted` and `shares_basic` — unsuffixed. Neither
name ever matched. The chain therefore always fell through to `shares_out`,
which only some filers tag.

**Measured: 2,125 of 2,873 names had a share count; 2,674 could have.
549 names (19.1%) silently had NaN market cap** — and market cap is
price x shares, so every valuation metric downstream was NaN too: `pe`, `pb`,
`ev_ebitda`, `ev_sales`, `fcf_yield`, `peg`, `mktcap`. KO, JNJ, PG, VZ and F
were all in that set. Nothing errored; nothing warned. The names simply scored
on the quality and growth pillars and were quietly absent from value.

Fixed by hoisting one implementation into `fundamentals.share_count()`, which
both `scores/fundamental.py` and `scores/hype.py` now call — the two call sites
were duplicated **and** identically wrong, which is the argument for the hoist.
`_selftest_share_count` asserts every name in the fallback chain maps to a real
tag group, so a future rename cannot silently stop matching again.

This is the same failure shape as the earlier `has_fundamentals` finding: a
missing input producing a plausible-looking score rather than an error.

### `scores/hype.py` — attention and narrative premium

Answers "how much of this price is attention rather than business", for names
like PLTR and TSLA. Built entirely from data already on disk — **no new source,
key or rate limit**. Deliberately does not re-measure tone; the sentiment module
owns that.

The one genuinely novel input is **average trade size** (`volume / trades`,
both already in every stored bar). When a name draws retail attention, volume
rises while the average print *shrinks* — many small orders. Rising volume with
rising print size is institutional accumulation, a different phenomenon. Most
retail-attention proxies cost money; this one separates the two cases for free.

Three pillars, split by what kind of quantity each member is:

| pillar | kind | members |
|---|---|---|
| `attention` | flow | vol_surge, trade_surge, trade_shrink, turnover, range_expansion, gap_freq |
| `premium` | level | ps_ratio |
| `stretch` | delta | extension_pct, px_vs_rev |

`premium_score` works and is the pillar that answers the original question:
PLTR 94.0, MSTR 92.6, NVDA 89.6, TSLA 83.6 at the top; AMC 4.4, F 6.0 at the
bottom.

### Three bugs found in this module, and one process failure

1. **Two of eight composite members never reached the ranker.** `turnover` and
   `px_vs_rev` were built in `_emit`, i.e. *after* ranking, so the composite
   silently averaged six of eight. Coverage could not detect it because the
   denominator was `len(present columns)` — a shrunken denominator can never
   catch a missing member. Fixed by ranking against `len(COMPOSITE)` and by a
   selftest that asserts every member reaches the ranker.
2. **ATR-normalising `above_200dma` inverted the signal.** Dividing extension by
   volatility means a quiet staple reads as violently extended (KO measured 5.3
   ATRs above its DMA) and a 90%-vol meme reads as calm (TSLA −6.7). Replaced
   with `extension_pct`, a self-referential percentile: where today's extension
   sits inside *this name's own* history of the same ratio.
3. **The synthetic hype fixture used `np.linspace`.** A steady ramp is never
   unusually extended for itself, so it failed the extension assertion. The
   fixture was wrong, not the metric — real hype accelerates, so the fixture now
   does too.

**The process failure is the one worth remembering.** Three successive versions
of the pillar grouping were adjusted because PLTR kept ranking below KO. That is
fitting a composite to a prior — precisely how `W_SUPPORT=20` happened. The
tuning was stopped at the grouping justifiable from first principles rather than
the one that produced the desired watchlist order, and **nothing in this module
has been measured**. `factor_lab` must evaluate all four scores independently
before any of them is trusted.

### Scheduling verified in production

The 05:00 `Screener-Orchestrator` run fired while `overnight.py` held the lock
and exited cleanly — `ANOTHER RUN HOLDS THE LOCK (pid 592)`. The lock-refresh
timer and the deferral path both work against a real 6-hour job.

`fund_catchup` measured **101 min** against a ~65 min estimate — the per-date
cost rose with the deeper fact store.

## 2026-08-07 — history extended to source floors, and one scheduled entry point

### The backfills finished. Every store now sits on its provider's floor.

| store | before | after | measured |
|---|---|---|---|
| bars 1d | 74 months, 2020-07 | **122 months, 2016-07** | 9,772,178 rows, **244 MB**, 509s |
| fundamentals | 24 quarters | **68 quarters, 2009q2** | 28,133,906 rows, **327 MB** |
| news | 49 months, 2022-08 | unchanged — the one leg left | 1,017,529 rows, 93 MB |

2016-07-28 is Alpaca's SIP floor and 2009q2 is where the SEC datasets begin, so
bars and fundamentals are not "deeper than before", they are **done**.

### Storage, measured per row rather than estimated

zstd-9 parquet, bytes per stored row: **bars 25.0**, **news 91.8**,
**fundamentals 11.6**, **scores 3.2**. Those four numbers make every capacity
question arithmetic instead of guesswork. `data/` is **~726 MB** today and
projects to **~810 MB** once news reaches 10y (+920k rows ≈ +85 MB, the only
extrapolated figure here — the pre-run estimate for the bars leg was 236 MB
against 244 MB actual, so the method holds to a few percent).

Daily growth is **~1.3 MB per trading session ≈ 330 MB/yr**, and the surprise is
what dominates it:

| component | KB/session | |
|---|---|---|
| scores — fundamental | **486** | 91,683 rows/session, measured |
| reports HTML | **370** | and nothing was pruning it |
| scores — sentiment | ~180 | |
| bars | 132 | |
| fundamentals (amortised) | ~90 | |
| news | 64 | |

**The score store, not the bar store, is the fastest-growing thing in the
project** — fundamental scores alone cost 3.7x what bars do, for metrics that
only move when a filing lands. Hence `fundamental` is a **weekly** step, not a
daily one: `factor_lab` samples monthly dates anyway (the leaderboard used 60),
so weekly costs no analytical resolution and saves ~120 MB/yr.

**`reports/` was never pruned.** `prune_dated` was wired to `REJECTS` only, so
370 KB/session of dashboards accumulated forever — invisible until now only
because the folder was four sessions old. New `REPORT_KEEP_DAYS = 120`, pruned
by the orchestrator's retention step. The undated `*_latest.html` aliases have
no date in the name and survive any retention setting.

### `HISTORY_YEARS` 4 → 10 was the load-bearing edit

Four constants derive from it and all four were wrong for the new stores:
`NEWS_HISTORY_YEARS` (news backfill would have silently capped at 4y),
`SCORE_KEEP_YEARS` (score partitions back to 2021-07 were one prune from
deletion), `store.prune("1d")` (would have deleted the 10y just fetched), and
`calendar_us.BACK_DAYS`. `FUNDAMENTALS_YEARS` went 6 → 17 to match the archive.
`calendar_us.start_for_history()` now returns 2016-07-28, exactly the bar
store's floor. **Lowering `HISTORY_YEARS` again arms `store.prune()` against the
backfill** — that warning is now in the config comment, not just in a handover.

### `orchestrator.py` — one scheduled entry point, twelve registry steps

Replaces three independent schedules (`daily_run`, `senti_screen --interval`,
`fund_screen --catchup`). Every step writes a row to `data/_jobs.parquet`, which
is the only thing the master dashboard will read.

Four decisions worth keeping:

1. **Dueness is a watermark, never a wall clock.** Session-indexed steps compare
   against the last close; housekeeping steps compare against the calendar day.
   Getting this wrong is not theoretical — the first version compared
   `retention` against `asof` and marked it permanently due on any non-session
   day. `sec_facts` goes further and derives dueness from the store itself
   (which quarters are missing), rate-limited to weekly because
   `fundamentals.quarters()` always includes the current, unpublished quarter,
   so "a quarter is missing" is the permanent steady state and a purely
   data-driven check would fetch a 404 every day forever.
2. **`blocked` is a distinct status from `error`.** The rule that "no data" and
   "no signal" must never look alike applies to job status too. Verified live:
   the `news` step failed on its first real run and `senti_cache`, `sentiment`,
   `events` and `leaderboard` recorded `blocked` while `bounce` ran to
   completion and wrote its report.
3. **Timeouts are recorded, not enforced by killing.** There is no safe way to
   kill a step mid-write in-process: `atomic_replace` protects the target
   partition, but a thread killed between `to_parquet(tmp)` and the replace
   leaks a temp file, and one merely abandoned keeps writing underneath its
   successor. Over-budget steps are marked `slow`.
4. **The job table is written after every step.** A crash in step 9 still leaves
   1-8 auditable, at ~14 rows/day.

The lock deliberately does **not** probe liveness with `os.kill(pid, 0)`: on
Windows that maps to `TerminateProcess` for any signal other than
CTRL_C/CTRL_BREAK, so the probe would kill the process it is asking about. It
uses `psutil` when present and falls back to age-based staleness.

### Duplicated work removed (asks 6.3-6.5)

`daily_run.run()` gained `do_universe` / `do_bars` opt-outs, both defaulting True
so the standalone path is untouched; the orchestrator passes False because it
owns those steps, saving a redundant 7s refresh + 32s split-recheck.
`senti_screen` is invoked with `do_fetch=False`, and `sentiment.build_cache()` is
its own daily step rather than a side effect of every screener call.

### Measured step costs on the WIDER stores — the spec's numbers are now stale

| step | spec said | measured now | why |
|---|---|---|---|
| macro | 41s | **60s** | breadth reads 9.77M bars, not 6.7M |
| events calibrate | 133s | **350s** | full fact store + 10y bars |
| senti_cache | ~15s | **199s** (cold) | rebuilt 19 months, 381,043 articles |
| fundamental | 8s | **40s** | 2,819 names scored, up from 1,770 |
| bounce (whole) | ~70s | **50s** | universe+bars no longer inside it |

Coverage improved alongside: the fundamental module now scores **2,819 names**
against 1,770 before, because the fact store reaches 2009q2 and the sector map
covers 10,398 tickers rather than 5,383.

**`leaderboard` measured at 990s — 16x the 60s the spec assumed.** It is weekly,
so 16 minutes once a week is affordable, but it is now by far the heaviest step
and the first thing to look at if the weekly run ever needs trimming.

### `dashboard.py` — the master hub, and the `index.html` handover

`reports/index.html` is now the status hub; the bounce session index moved to
`reports/bounce_index.html`. Two writers on one filename is the kind of
collision that surfaces later as a page that mysteriously reverts, so
`report.write_index()` was repointed rather than left to race.

Navigation is a closed loop, verified by a link checker that resolves every
`href` on all five pages (**0 broken**): hub → three dashboards + session index;
each dashboard → hub.

The three constraints are the same ones that make the rest of the project cheap:

- **It reads `data/_jobs.parquet` and file sizes. Nothing else.** No step is
  invoked, no store scanned, no network call. A status page that triggers work
  is one you become afraid to refresh.
- **"Run now" is a command to copy, not a button that runs.** A static file
  cannot spawn a process, and a resident local server would throw away the
  no-daemon design. Each row renders `python orchestrator.py --step <name>` with
  a copy button that copies and does not execute.
- **No framework, no external request.** Inline CSS plus ~20 lines of vanilla JS.

Four bugs worth recording because three were invisible in the source:

1. **`bool(pd.NA)` raises**, so `if s["error"]:` blew up on the first run against
   the nullable-dtype job table. This is the same family as "a float NaN is
   truthy" already in the gotchas list, and the fix is the same shape: normalise
   at the boundary, once, in `_na()`.
2. **Anchors cannot nest.** The dashboard cards were `<a class="card">` with an
   "all sessions" link inside; the browser silently hoisted the inner anchor out,
   rendering it as a stray link in its own empty box. The card is now a `<div>`
   with the title as the anchor.
3. **A flex container makes bare text its own item.** The status legend was
   emitted as `pill + text`, so the description wrapped away from the pill it
   described. Each pair is now one `inline-flex` child.
4. The `dashboard` step **builds the table it is about to be recorded in**, so
   it reported itself as never-run and showed its own lock as contention — every
   healthy run would have ended with a spurious amber banner. Both are now
   special-cased with the reasoning in the code.

Copy-to-clipboard was verified on both paths: `navigator.clipboard` where the
context is secure, and the `document.execCommand` fallback that `file://` needs
(not a secure context, so `navigator.clipboard` is undefined there). Neither
throws and neither leaks the temporary textarea.

## 2026-08-06 — score module 2 (fundamentals) + factor_lab

### The data-source decision, measured not assumed

Three free routes to SEC XBRL. The difference is not cosmetic:

| route | one request gets | point-in-time? |
|---|---|---|
| `companyfacts` API | all history for 1 company (3.9 MB for ORCL, 535 concepts) | yes |
| `frames` API | 1 concept x 1 period for **6,251 companies** in 0.83 MB / 1.6 s | **NO — no `filed` field** |
| **Financial Statement Data Sets** | one quarter of every filing, 85 MB zip | **yes** |

`frames` is the seductive one and it is unusable: its rows carry only
`accn/cik/end/val`, so there is no way to know when a number became public.
Every backtest built on it would silently use figures that did not exist yet —
the same class of bug as attributing after-close news to the session that just
closed, and equally invisible. Chose the bulk sets: `sub.txt` carries `filed`
per accession, `num.txt` the facts, join on `adsh`. 85 MB in compresses to
**3.2 MB stored** after filtering to 69 tags.

**The rule, in `facts_asof` and nowhere else: a fact is visible on D iff
`filed <= D`.** Not `ddate <= D` — a quarter ending 2024-03-31 is not public
until the 10-Q lands in May, and screening on it in April is a six-week
look-ahead that would flatter every fundamental factor ever tested here.

### factor_lab caught a false discovery in its first real run

Built as its own module precisely so every future score module is tested by the
same code on the same forward returns. It immediately earned that:

sentiment `sent_mean_30d`, 308k obs / 151 dates / 4,268 tickers —

| h | IC | naive t | **corrected t\*** | independent obs |
|---|---|---|---|---|
| 1 | +0.0158 | 3.29 | **3.29** | 151 |
| 20 | +0.0173 | 3.13 | 1.21 | 23 |
| 60 | +0.0300 | 7.76 | 1.73 | 8 |
| 120 | +0.0431 | **10.98** | **1.74** | **4** |

The naive t of 10.98 was 151 dates sharing a 120-session forward window — **the
same result counted forty times.** `n_eff = dates / (horizon / spacing)` now
corrects it, and only the 1-day IC survives.

Two further things the run says, both worth keeping:

- **IC positive while the quantile spread is NEGATIVE** at 20d (−0.35%) and 60d
  (−0.65%) — the bottom quintile beat the top. The overall ordering is mildly
  informative while the extremes mean-revert, so a naive long-top/short-bottom
  is the wrong way to trade it.
- Sector slice: Aerospace IC +0.071, Semiconductors +0.055 vs Financials +0.018.

### Other decisions

- **No mechanical DCF.** A DCF across 5,000 names measures the terminal-growth
  and WACC assumptions, not the companies. EVA and the ROIC−WACC spread answer
  the same economic question without a fabricated 10-year forecast.
- **SUE uses the Foster (1977) seasonal random walk**, not analyst consensus —
  consensus is not free at this scale, and the seasonal-random-walk SUE is the
  original academic construct rather than a degraded substitute. It measures
  surprise against the *series*, not against the *street*.
- **Pillar weights are all 1.0** and stay that way until factor_lab measures
  them. `W_SUPPORT = 20` was set by intuition and grade A measured weakest;
  that mistake does not need repeating.
- **`has_fundamentals` is an explicit state.** Caught live: with one quarter
  stored, AAPL/MSFT/ORCL returned every metric NaN because TTM flows need four
  quarterly periods or one annual, while CELH scored fine purely because it had
  filed a 10-K. Nothing errored.

### Windows concurrency bug, found by a 10-hour job

`os.replace` raises `PermissionError` (WinError 5) when any other process holds
the target open — including a plain reader. The news backfill died at chunk
128/209 because the score catchup was reading the partition it was replacing.
The bar pipeline never hit this because it is one-shot and single-process; the
interval screener reading while a backfill writes is normal operation. All
atomic-replace sites now route through `store.atomic_replace()`, which retries.



Running record of decisions, measurements, and things that turned out to be wrong.
House convention, matching `RESEARCH_LOG.md` / `ANALYSIS_LOG.md` in the sibling
projects.

---

## 2026-08-04 — build

Built from scratch in one session. The motivating input was an RDW hourly chart:
base ~$8, a run to $26.6, a full two-month retrace back to the base, and a bounce
in progress.

### Measured facts that changed the design

Everything here was verified against the live API, not assumed.

| finding | consequence |
|---|---|
| `api.alpaca.markets/v2/assets` returns **401** — the `.env` keys are **paper** keys | `TRADING_BASE` defaults to `paper-api.alpaca.markets`. The asset payload is identical. |
| Free-tier SIP **403s** on any `end` inside the last 15 min, and a **bare date resolves to end-of-day** | `end=<today>` 403s even hours after the close. `Stock Screener/daily_update.py:96` does exactly `end = today.isoformat()`; ported naively it would fail on every run forever. `end` now always comes from `bars_end_ts()` (a lagged timestamp), and `_assert_end_ok` rejects a bad one with a named error instead of a mystery 403. |
| `symbols=` batching works; `limit=10000` caps **total** bars per page; pagination resumes **mid-symbol** | Per-symbol lists must be `extend`ed, never assigned. 400 symbols x 4y = 25 pages in ~60s. Full backfill of 5,379 tickers: **4,713,307 bars in 236s**. One symbol per request would be a 32-minute floor. |
| In-progress bars are served | At 11:04 ET, RDW's 2026-08-04 bar showed `n=46,662` vs ~112,000 for a full session. Dropped in one chokepoint (`store.write`). |
| `nasdaqtraded.txt` has an authoritative ETF flag and joins Alpaca at 99.95% | Solves ETF/warrant/unit exclusion far better than name heuristics. QQQ is NASDAQ-listed so the exchange filter misses it — only the ETF flag catches it. |
| RDW's 2-year window max is **26.66 from 2025-01-31**, 376 sessions back | The dominant peak must be a **local** peak, never the window argmax. The real peak is 26.64 on 2026-05-28. |

### Universe funnel (measured)

```
active us_equity              14,180
tradable                      13,324
NYSE/NASDAQ/AMEX               8,747   (drops ARCA+BATS = ~4,200 funds for free)
symbol-suffix filter           8,246   (.PR* .WS* .U .RT out; .A/.B/.C/.V kept)
minus ETF flag                 6,918
minus Security Name regex      5,379   <- operating companies
+ price/ADV/history gates      ~717 reach the pattern math
```

Negative name filter, not a positive whitelist: a whitelist requiring
`Common Stock|Ordinary Shares` produces ~100 false negatives including `MTRN`
(no descriptor), `AAPG` ("Depos**i**tory", an *o*), `HAO` ("Ord Share"), `SRL`
(nothing at all). Verified all four are kept, and `GBDC`/`GGN` still dropped.

Fail **open** on join misses — four symbols do not join and one is **`BRK.A`**.

### Three thresholds that were wrong in the plan

1. **`MIN_RUN_Z` 3.0 → 1.25.** Measured across the 152 names clearing the run and
   retrace gates: p05=0.90, p50=1.58, p95=3.14. 3.0 would keep 6% of them and
   **reject RDW itself** (run_z 1.98) — because RDW's base-window ATR is **10% of
   price**, not the 4% the estimate assumed. Calibrated from the distribution.

2. **`prom_minor` needed a cap (`PROM_MINOR_MAX = 0.055`).** Volatility scaling
   pushed RDW's minor-pivot prominence to 14.5%, leaving only 20 troughs in 896
   bars. The genuine 7.77 shelf then had a single pivot and could not form a
   cluster (`MIN_PIVOTS_PER_LEVEL = 2`), so the screen reported
   `NO_LEVEL_NEAR_LOW` with *zero* candidates. Capping yields ~68 troughs and the
   shelf forms. Scaling by volatility is right for finding the dominant peak; for
   building levels it starves the input on exactly the volatile small-caps this
   screener targets.

3. **`LEVEL_OFF_BASE` measured from the wrong thing.** Distance from the base's
   geometric *centre* penalises a level at the base's *floor* — which is where
   support actually is. RDW's real level (7.44, with 8 prior touches over 626 days)
   is `base_lo` to the cent and scored 0.121 against a 0.12 threshold: rejected by
   0.001. Replaced with **zone membership** (0 if inside `[base_lo, base_hi]`).
   The low's own tightness to the level is separately gated by `LOW_OFF_LEVEL`.

Also: candidate **selection** needed a wider band than the gate
(`LEVEL_SELECT_BAND = 0.12`). Cluster medians sit ~10% apart, so a 7%-wide
selection window can fall entirely into the gap between two real shelves — RDW's
were 7.47 and 8.23, and `[7.53, 8.07]` missed both by a hair. Selection now
prefers candidates that also satisfy the tight gate.

### RDW verification (as of 2026-08-03, close 9.64)

| metric | value | gate | |
|---|---|---|---|
| peak | 26.64 on 2026-05-28, 45 sessions back | 25–300 | ok |
| base | 7.43 / 8.47 / 9.65, dated 2026-03-30 | | ok |
| run_x / run_z | 3.59x / 1.98 | ≥2.20 / ≥1.25 | ok |
| dd from peak | 70.9% | ≥50% | ok |
| retrace of run | 0.983 | 0.78–1.10 | ok |
| undercut low/close | −4.4% / −4.7% | ≤10% / ≤4% | ok |
| level | 7.44, Q=0.869, 8 prior touches (7 pre-run), 626-day span | ≥2 / ≥1 | ok |
| low vs level | +4.30% | −4%…+6% | ok |
| B / V | 68 / 0.44 | ≥35 | ok |
| **ext_atr** | **2.01 → CONFIRMED** (ATR at low 0.934) | | ok |
| composite | 73.2 GOOD | ≥45 | ok |
| tags | LOW / RECENT / SMALL / DEEP / grade A / SINGLE → **PRIME** | | |

The ATR-normalised extension is the point: RDW is **+24.2%** off its low in three
sessions. Any fixed percentage cutoff that also admits a low-volatility name's +8%
bounce would call this "extended" and skip it.

### Bucket cascade: two corrections found by the selftest

- **`SPEC` is about tradeability, not price.** A $3 stock with $143M ADV and a
  grade-A level is a good setup that happens to be cheap; demoting it to SPEC
  would bury it in the bucket least likely to be read. `price_tier` already carries
  nominal price as a tag.
- **Age must not decide the bucket.** ORCL on 2026-08-03 — grade A, DEEP, CONFIRMED,
  peak 224 sessions back — was landing in `WATCH` purely for being an older round
  trip, which demotes exactly the large caps the report is supposed to surface.
  Buckets now discriminate on quality and stage only; `age_band` is a tag.

Also caught by the selftest: `_tier` used an exclusive upper bound for everything,
but age bands are inclusive integer ranges ("25 through 40"), so age 40 fell into
`RECENT`. Two helpers now, `_tier` and `_tier_incl`.

### Look-ahead

Both assertions pass across 72 (ticker, asof) pairs:

- truncation equivalence: `screen_one(df, a) == screen_one(df[:a+1], a)`
- future scramble: bars after `asof` multiplied by noise; no output field moves

The second is the one that catches `find_peaks` prominence borrowing future bars.
Filtering pivot indices to `<= asof` is **not** sufficient — the prominence values
themselves are contaminated, so the pivot set must be recomputed from a truncated
series at every `asof`.

### Performance

| step | measured |
|---|---|
| universe refresh | ~4s |
| full 4-year backfill (5,379 tickers, 4.71M bars) | **236s**, 118 MB, 50 month files |
| bars delta + 10-session split recheck | ~45s |
| bars delta, recheck already done | **~2s** |
| panel prefilter 5,374 → 717 | ~2s |
| pattern math on 717 tickers | ~16s |
| hourly + market cap for 24 names | ~23s at `CONFIRM_DAYS=30` |
| report build | ~3s |
| **full `daily_run.py`** | **68s** (43s with `--no-confirm`) |

`CONFIRM_DAYS` was 60 and measured 95s for the hourly stage on one run (intraday
latency is highly variable -- the same fetch took 25s, 54s and 94s across three
runs). Halved to 30, which costs no information: the annotations only examine
behaviour since the bounce low, typically <=15 sessions back.

The split recheck is gated on `last_split_recheck == last_closed_session` so it
runs once per session, not once per invocation — otherwise the at-logon trigger
costs ~45s of network every time the laptop is unlocked, when the whole point of
that trigger is to be free on days you were already online.

`_panel_stats.parquet` exists so the prefilter never reads the full 4.7M-row store
(~20s of zstd decompression). Full history is loaded only for the ~717 survivors.

### Replay result: the pattern has no edge as specified

46 sessions (every 5th) 2025-08-01 → 2026-06-30, 500-ticker sample, 183 flags,
forward returns from the flag date's close. Leak tests passed first, so these are
causal.

| slice | n | 5d | 10d | 20d | MFE | MAE | win 10d |
|---|---:|---|---|---|---|---|---|
| PRIME | 126 | −0.76% | **−2.23%** | −4.80% | +9.2% | −11.0% | 43.7% |
| SPEC | 47 | −1.83% | −0.47% | −7.68% | +9.0% | −11.7% | 44.7% |
| EARLY | 10 | −1.89% | −2.75% | −6.09% | +4.9% | −15.3% | 30.0% |
| **baseline: same universe, same dates, NO screen** | **22,962** | **−0.23%** | **−0.49%** | **−1.42%** | **+9.4%** | **−9.3%** | **48.0%** |

**The screen underperforms its own prefiltered universe by 1.74pp at 10 days.**
Picking a random name from the pool beat the flags. The baseline is the number that
makes the result interpretable, and without it "−2%" could have been read as
market-wide.

Detail worth keeping:

- **The bounce is real but transient.** Flag MFE +9.2% vs MAE −11.0%. Price does
  travel ~9% up at some point in the next 20 sessions; it just gives it back. So
  the signal may be a short-hold pop, but **no exit rule is implemented or tested**,
  and the flags' MFE is not even better than the baseline's +9.4%.
- **`MICRO` price tier is the damage.** 10d by tier: MICRO −3.74% (MAE −15.5%),
  LOW −0.61%, MID −0.19%, HIGH −2.26%. Directly relevant since "small price stocks"
  was an explicit ask.
- **Two design hypotheses are unsupported.** Grade A (n=141, −2.25%) did worse than
  grade C (n=14, +3.40%) — small n, but no evidence touch count predicts anything,
  and `W_SUPPORT` is the largest score weight at 20. `FRESH` was the worst age band
  (−9.80%, MAE −20.1%) and `OLD` the least bad, the reverse of the intuition the
  bands encode.
- **`TURNING` marginally beat `CONFIRMED`** at 10d (−0.12% vs −3.29%), consistent
  with "do not chase", and the one result that went the expected way.
- **Bias note.** The replay universe is selected with *today's* panel stats, so it
  is conditioned on having fallen by today and the absolute drift is dragged down.
  The baseline shares that bias identically, so the relative figure is sound; the
  absolute figures are not a return estimate.

Consequence for the deliverable: the daily list is framed as a **research queue,
not a buy list**, and the finding is printed in the dashboard header every day
rather than buried in a doc. The screener does what was asked — it finds the
pattern, verified in detail on RDW — but the naive interpretation of the signal
lost money in this sample, which is exactly what the replay was built to reveal.

---

## 2026-08-05 — full backtest supersedes the replay

The 2026-08-04 conclusion ("no edge, −2.2% at 10d") was **wrong in method**. Three
defects, all mine:

1. **Median-only reporting on a right-skewed distribution.** The same 20-session
   hold that I reported as −4.80% median is **+1.61% mean**. When most trades lose a
   little and a few win big, the median is the wrong statistic and the mean is the
   decision variable.
2. **Untradeable entry.** The replay entered at the signal bar's *close* — but the
   signal is computed *from* that close. Real entry is the next open.
3. **Look-ahead in universe selection.** The replay picked eligible names using
   *today's* panel stats, conditioning the sample on having fallen by today.
   `backtest.py::eligibility()` now computes gates from rolling windows per date
   over all 4.7M (ticker, date) rows.

Also: n=183 on a 500-ticker sample was far too small. The new run is **1,300 flags
over 186 dates** (2024-02 → 2026-08, every 3rd session, full eligible universe,
~572 names/date). 44 min.

### Result

Every one of 12 exit rules has a **positive mean**. Best two:

| rule | mean | median | win | held |
|---|---|---|---|---|
| `trail_2` (2-ATR trailing) | +2.16% | −3.68% | 41% | 13d |
| `stop2_hold20` | +2.13% | −5.18% | 41% | 20d |
| `hold_3` (worst) | +0.17% | −0.46% | 46% | 3d |

**But the control group kills the story.** Random entries from the same eligible
pool, same dates, same rule, 10 seeds pooled (n=13,000):

| | screen | random | edge |
|---|---|---|---|
| `trail_2` mean | +2.16% | **+1.60%** | **+0.56%** |
| `stop2_hold20` mean | +2.13% | +1.35% | +0.77% |
| win rate | 41% | **41%** | **0pp** |

- Baseline SE for a 1,300-trade sample is **±0.96%** — larger than the edge itself.
- Welch t-test on the best rule: **t=1.09, p=0.28**. Not distinguishable from random.
- Baseline per-seed means ranged **+0.17% to +4.44%**, which is exactly why a single
  baseline draw would have been worthless.
- **Identical win rates** are the most damning single number: the screen is not
  selecting more winners, it is drawing from a pool that drifts up under a trailing
  stop.

### What survives scrutiny

- **11/12 exit rules favour the screen**, sign-test p=0.0032. The rules are highly
  correlated so this overstates the evidence, but a consistent sign across every
  rule is not what a zero edge produces.
- **`RECENT` age band (41–90 sessions since peak): +4.69% mean, +3.09% edge, 47%
  win, raw p=0.005.** The strongest thing in the data. But of **17 subgroup
  comparisons, ZERO survive Bonferroni** (RECENT → p=0.088). A hypothesis, not a
  filter. Note RDW is RECENT.

### Design decisions the data contradicted

- **`EARLY` is the only bucket with a negative mean** (−0.72%, edge −2.32%, n=80).
  It was deliberately placed FIRST in the report on the reasoning that an un-turned
  setup has the whole move ahead. Wrong: `CONFIRMED` (+2.72%) beat `TURNING`
  (+1.27%) beat `STILL_TESTING` (+0.23%). Waiting for confirmation beat anticipating
  the turn. `BUCKET_ORDER` now leads with `PRIME`.
- **Support grade A is the weakest grade** (n=1,011, edge +0.04%) against B (+2.38%)
  and C (+2.31%). `W_SUPPORT = 20` is the largest score weight and the data does not
  justify it. Unresolved — see below.
- Cheap names are NOT the disaster the median analysis suggested: `LOW` +2.97% and
  `MICRO` +2.33% both beat `MID` (+0.75%).

### Still optimistic

- **No costs.** No spread, commission, slippage or borrow. A 0.5–0.8% edge on
  sub-$5 names is plausibly consumed entirely by the spread.
- **Survivorship is a structural blind spot.** `--survivorship` reports 1 stale
  ticker out of 5,379, but that is meaningless: the store was built FROM today's
  asset list, so delisted names were never fetched and cannot be counted. Real
  attrition (~4–6%/yr, worse for micro-caps) concentrates in failed bounces.

### Daily surfacing

`backtest.py --base-rates` writes `data/_base_rates.parquet` (37 rows), and
`report.py` attaches the most specific match with n≥8 to every card. Each card now
reads e.g. *"historically, PRIME|CONFIRMED setups (n=528, exit trail_2): mean
+3.44%, median −2.9%, win 44%"*. `status.py` prints the same table. Verified 31/31
cards carry a rate.

### Open / deferred

- **Highest-value next step: test exit rules.** MFE +9.2% / MAE −11.0% says the
  move exists but is not capturable by holding. Try a 3-session hold, a
  1-ATR trailing stop, and a target at +1.5 ATR, measured over the same replay.
- **Second: find what separates the winners.** ~44% of flags are up at 10 days;
  the reject dumps already carry every metric, so a groupby on the flags that
  worked is a one-liner. If nothing separates them, the pattern is not tradeable
  and the honest move is to say so.
- Re-check whether `W_SUPPORT = 20` is justified given grade A underperformed.

- `SCREEN_WORKERS = 2` rather than 8: the pattern math is CPU-bound and takes ~16s
  single-threaded. 8-way would save ~12s and peg every core, which is a bad trade
  on a laptop.
- 24 flags on 2026-08-03 is above the 5–15 target. Not tuned down yet — the bucket
  split (PRIME 14 / SPEC 10) does the prioritisation, and it is better to watch a
  few real sessions before tightening. If it stays high: `MIN_PRIOR_TOUCHES` to 3
  and `RETRACE_LO` to 0.85.
- Sector clustering is strong (space, EV, lithium, solar). A day's list is far less
  diversified than its length suggests. Worth surfacing a sector-concentration
  warning in the report later.
- No notification channel, by choice. The seam is one file (`notify.py` exposing
  `send(subject, text, html_path)`).

---

## 2026-08-06 — score modules, and module 1: news sentiment

Built the pluggable score layer plus the first module. The eventual shape of this
project is a screener where each stock carries several independently-updated
scores; sentiment is the first, so the architecture went in before the feature.

### The architectural decision

Scores live in **one tidy table**, `data/scores/YYYY-MM.parquet`
(`session | ticker | module | metric | value | label`), behind a registry in
`scores/`. A new module adds **rows, not columns**.

A wide table looks friendlier and is a trap: every new module migrates the schema
of every stored partition, modules cannot be backfilled independently, and a
module whose metric set varies by date cannot be represented at all. Tidy makes
backtesting one module a filter and comparing two a groupby.

Rolling aggregates are **not stored** — they are recomputed from a window ending
at `asof` on every call. A stored aggregate is a number whose provenance cannot
be checked later, and if the window that built it ever reached one bar too far,
nothing errors; the backtest just quietly improves. This is the same slice-once
discipline that makes `screen_one` causal, and it is what makes leak test #2
possible at all.

### Measured facts that changed the design

| finding | consequence |
|---|---|
| Alpaca `/v1beta1/news` works on the existing keys, free, 200 req/min, history to **2015-01** | No new vendor, no new limiter. Same `alpaca._get`. |
| `limit` caps at **50** (bars allow 10,000) | News is ~200x more page-hungry per row. One session ≈ 32 pages. |
| Firehose: **1,580** articles/day (2026-08-04), 1,145 (2024-03-05), **371** (2016-06-10) | Coverage grew **~4x since 2016**, so any raw count feature trends with Benzinga's output, not the market. Every count metric is ranked **cross-sectionally within each date**. |
| **90%** of articles tag ≥1 universe ticker; near-duplicate headlines only **1%** | No cheap way to shrink the corpus. Fetch the firehose unfiltered and route by each article's own `symbols`; per-symbol querying would issue thousands of requests for the same rows. |
| Article bodies are 4–10 KB | `content` is never stored. It would take the store from ~120 MB to ~10 GB for no gain over headline+summary. Measured cost with headline+summary only: **92 bytes/article**, so 4 years ≈ 120 MB. |
| Backfill measured at **140 articles/s** | 4 years ≈ 1.3M articles ≈ 2.6 h. Chunked weekly and resumable, so a 403 costs one week. |

### Coverage is brutally skewed, and it dictated the whole module

Across the 30 live flags over 30 days: **ORCL 95 articles, the MEDIAN flag 3, and
4 of 30 zero** (SGML, ABAT, DPRO, VUZI). Per-ticker sentiment is a sparse signal
for exactly the micro-caps this screener surfaces. Three consequences:

1. **`has_news` is an explicit state.** A silent ticker emits `has_news=0` and
   *no other metrics*. Imputing 0.0 would drop 13% of the list into the middle of
   the ranking by accident.
2. **The macro/sector layer is load-bearing, not decorative.** For most of the
   list it is the only sentiment reading that will ever exist.
3. **Burst is measured against the ticker's own baseline.** ORCL going 3 → 8
   articles is nothing; DPRO going 0 → 8 is the entire signal.

### Four bugs caught by measurement, not by tests

1. **"No news" and "no data" were indistinguishable.** With only 15 of the last 30
   sessions backfilled, the module reported **13 of 30 flags silent** against
   **4 of 30** measured directly from the API. Nothing errored. Added
   `SENTI_MIN_COVERAGE` (0.90) and a `NewsCoverageError` that refuses to score an
   under-covered window, plus a `news_coverage` metric emitted on every ticker so
   the number is visible rather than assumed.

2. **27.6% of (article, ticker) pairs are republications of the same event.**
   CPHI's 2026-07-21 circuit-breaker halt appears **six times**; HALT duplicates
   at **1.98x**. An earlier check on exact/templated headline text put duplication
   at 1% — that was simply the wrong lens: the rewrites are not textually similar,
   they are the same *event*. Now deduped on `(ticker, session, event_type)` in
   both the calibration and the per-ticker aggregate. Left in, one dramatic day
   carried the weight of six.

3. **The score cache silently outlived its calibration.** `data/senti/*.parquet`
   stores a `severity` column derived from the priors in force when it was
   written, so recalibrating invalidated it invisibly — the stale cache put **more
   articles in CRITICAL than in HIGH**, an impossible shape for a severity ladder.
   `events.calibrate()` now rebuilds the cache itself rather than leaving it to
   the caller.

4. **`r.get("sector") or "?"` rendered the literal string `nan`.** A float NaN is
   truthy, so the idiom passes it straight through. Every unmapped ticker showed
   "nan" under its symbol in the report.

### Severity: the hand priors were wrong by up to 6x

`sentiment.EVENTS` shipped a hand-written severity prior per class. Measured
against 184,966 (article, session) pairs — |return|/ATR on the attributed
session, ATR sampled *before* it so a move cannot deflate its own normaliser:

| event | prior | measured (median \|ret\|/ATR) | lift |
|---|---|---|---|
| **HALT** | 3.0 | **1.152** | **2.46** |
| GUIDANCE | 1.6 | 0.944 | 2.01 |
| GUIDANCE_CUT | 2.4 | 0.903 | 1.92 |
| EARNINGS_BEAT | 1.6 | 0.880 | 1.88 |
| EARNINGS_MISS | 1.8 | 0.842 | 1.79 |
| **BANKRUPTCY** | **5.0** | **0.423** | **0.90** |
| **MA** | 3.5 | 0.428 | 0.91 |
| **FDA** | 3.5 | 0.459 | 0.98 |
| **SHORT_REPORT** | 4.0 | 0.388 | 0.83 |
| **OFFERING** | 3.0 | 0.471 | 1.00 |

**Nearly every "dramatic" class moves stocks LESS than average on the headline
day.** Bankruptcy at 0.90x lift is not an error: by the time Benzinga writes
"Acme Files For Chapter 11" the collapse has already happened over prior
sessions, and the headline is the aftermath. Only **scheduled, discrete
repricing events** — earnings, guidance, halts — are genuinely high-severity.

**Median, not mean, and this is the 2026-08-05 lesson in reverse.** Event
responses are violently right-skewed: 6.4% of HALT rows exceed |100%| and CPHI
alone printed **+842%**, dragging the HALT mean to +25.8% against a +3.3%
median. The mean is the right statistic for *returns*; for "how much does this
kind of news typically move a stock" it measures the tail. p90 is kept alongside
so the tail stays visible.

The taxonomy itself validated: BEAT **+0.37%**, MISS **−0.78%**, RAISE
**+0.67%**, CUT **−0.60%** signed. The classifier separates real economic
content, and `events.selftest` asserts those four signs so a future regex edit
that breaks them fails loudly.

### Severity bands were calibrated to the wrong scale

The original bands (60/40/22/8) were set against hand priors that ran to 5.0 ATR.
Against measured severity the attainable range is ~7 to ~53, so **CRITICAL was
unreachable and everything real piled into HIGH**. Rebanded to 45/32/20/10, which
gives the intended semantics: severity now requires **both** a high-impact class
**and** unusual coverage — max is MEDIUM at burst 0, HIGH at burst 2, CRITICAL
only at burst 4.

### Taxonomy built from the corpus, not from imagination

Frequencies over 4,947 stored headlines were not what intuition suggests:
`Price Target` 1,314 · `Sales` 1,509 · `EPS` 1,289 · `Beats` 621 · `Misses` 261
· `Offering` 40 · `FDA` 23 · `Bankrupt` 2.

- **Analyst notes dominate and most are non-events.** The commonest pattern is
  "*\<Bank\> Maintains \<Rating\> on \<Company\>*". Restatements outnumber real
  rating changes ~15:1, so `ANALYST_MAINTAIN` is a separate class ordered
  *before* `ANALYST_UP`/`DOWN`. "Maintains Overweight, Raises Price Target" is
  **not** an upgrade — the rating did not move. Direction is not lost: the class
  carries direction 0, so `combined_score` falls through to the lexicon where
  "raises"/"lowers" supply the sign.
- **~2% is content-farm filler** ("Here's How Much $1000 Invested In X…"). It
  names a ticker, carries a cheerful lexicon and says nothing. Classified NOISE
  and excluded from aggregation, not merely down-weighted.
- Sampling the `OTHER` bucket then drove a second pass (price-explanation stories
  "Why Is X Stock Falling", analyst roundups, economic indicator prints, TV
  punditry), taking OTHER from 32.1% → 28.7%. The remainder is a genuine long
  tail of operational news; further splitting would be overfitting.

### Two performance fixes that mattered

- **compute() was quadratic.** `ex[ex["ticker"] == t]` inside a loop over the
  universe is a full scan per ticker: **141s for 3,471 names**. Tolerable on a
  30-minute interval, fatal for a backtest at 186 dates (>7 hours). Rewritten as
  groupby: **15s**, identical output.
- **Article scores are cached.** Re-scoring a 90-session window on every call cost
  9.5s for 30 tickers; with `data/senti/` warm it is **0.6s** (16x). Articles are
  immutable once published, so their scores are too.

### Macro layer, ordered by reliability — which is not the same as "free"

1. **Breadth from the bar store.** 5,383 tickers already on disk, no key, no rate
   limit, perfectly historical. 1,012 sessions computed in 24s. The most reliable
   macro signal available here, which is why it leads.
2. **24 sector + macro-proxy ETFs** via the existing bars path (they are excluded
   from the universe by the ETF flag, so fetched explicitly into `bars_etf/`).
3. **SEC EDGAR SIC codes** for the sector map. RDW → SIC 3760 → Aerospace → ITA.
   Two ranges were wrong on the first pass: 45xx is transportation **by air**
   (AAL was mapping to an aerospace & defense ETF), and 376x is guided missiles
   and **space vehicles** — the RDW range — which had been falling into a generic
   Industrials bucket and would have put the whole space cohort under XLI.
4. **GPR and EPU daily indices**, both verified downloading with no key. EPU
   parsed at 15,191 rows current to 2026-08-04; GPR at 15,190 days back to 1985
   (needs `xlrd` — the file is a real OLE2 `.xls`). GPR printed 91 → **225** on
   2026-08-01→03, which is the Iran headlines showing up quantitatively.
5. **FRED/ALFRED** for actuals, market-implied expectations and release dates.
   Free key. Vintages are the point: every observation carries `realtime_start`,
   so what was *known* on a past date can be reconstructed. These series are
   revised, so using today's value in a 2024 backtest is look-ahead.

**GDELT is deliberately not a dependency.** Measured: it enforces one request per
5 seconds and 429'd on **3 of 4 attempts even at 20-second spacing**, including
every timeline query. Free, but not reliable enough to sit on a scheduled path.

**SEC throttles far below its documented limit.** At 8 req/s the first 500
tickers took 237s, then throughput collapsed ~25x (2,500 took 31,583s). The full
sector map is an *overnight* job, not the ~11 minutes the arithmetic suggests.
Rate lowered to 4/s, map cached and refreshed monthly.

### The surprise, without paying for consensus

Street consensus (the Econoday/Bloomberg "expected" number) is the one input here
that is **not reliably free**. It is also not needed:

> surprise = ATR-normalised move of the macro proxy basket (SPY, TLT, GLD, USO,
> UUP, HYG) on a session with a scheduled release.

The market's reaction *is* the surprise, it is free and fully historical, and it
is the only part that transmits to a stock anyway. Honest caveat, now in the
README: this measures **reaction**, not **deviation from expectation**. The two
differ when the market has pre-positioned.

### Look-ahead

The session-attribution rule is the expensive one and it is silent:

> an article belongs to session **S** iff its timestamp < S's close; anything at
> or after S's close belongs to **S+1**.

An article stamped 21:00Z is 17:00 ET — an hour *after* the close. Attributing it
to that day would let a screen computed on that close "know" tonight's news.
Timestamp is `max(created_at, updated_at)` because Benzinga revises in place, and
scoring the revised text while attributing it to the original timestamp is
look-ahead (measured drift was 0/50, so the guard is nearly free).

`replay.py --leaktest` gained three assertions alongside the existing two, all
passing: **376,278 articles, none attributed at or after their session's close**;
truncation equivalence; and a future scramble that replaces every post-`asof`
headline with "Chapter 11 bankruptcy / going concern / fraud" text and asserts no
metric moves.

### Open / deferred

- **The backtest is the decision point.** `senti_backtest.py` asks the only
  question that matters — does a sentiment-ranked pick beat a random pick from
  the same eligible pool — reusing `backtest.eligibility()`, the same 12 exit
  rules and the same next-open entry so the answer is comparable to the bounce
  result. The control draws from the eligible **and covered** pool: names with
  coverage are larger and more liquid, so a control drawn from the full universe
  would hand the strategy a size premium and report it as sentiment.
- **FinBERT is deliberately last.** Measured on this machine (i7-10510U, 137
  GFLOPS burst → **92 sustained**, AVX2 but no VNNI so int8 buys ~2x not ~4x):
  **~22 ms/article**, i.e. ~1.3 s per 30-minute run but **~8 h** for a 4-year
  backfill. The lexicon path measures **0.143 ms/article** (a full backfill scores
  in ~3 min), which is why it ships first: a complete historical series exists on
  day one and the backtest is never blocked. Whether FinBERT is actually better
  is a question for the backtest, not an assumption — the literature says
  72–91% vs ~50%, and this project has been wrong twice by trusting exactly that
  kind of number.
- Single source: 100% Benzinga. A coverage change is indistinguishable from a
  market change, and no cross-source validation is possible.
- Retroactive symbol tagging is unverifiable through this API — same class as the
  existing survivorship blind spot.
- The severity calibration currently runs on a partially-backfilled store and
  should be re-run once the 4-year backfill completes.

### Preliminary backtest — suggestive, NOT established

First run, on the ~8 months of news the backfill had reached. **54 test dates,
780 screen trades per rule, top-15 by `sent_mean_30d`, 10 random seeds drawn from
the same eligible-and-covered pool, entry at the next open.**

| exit | screen | random | edge | t | win s/r |
|---|---|---|---|---|---|
| `trail_1.5` | **+1.02%** | −0.06% | **+1.07%** | **1.85** | 36%/35% |
| `trail_2` | +0.64% | −0.41% | +1.05% | 1.65 | 39%/37% |
| `bracket_2x3` | +0.18% | −0.53% | +0.71% | 1.30 | 42%/42% |
| `hold_20` (worst) | −0.36% | −0.24% | −0.12% | −0.16 | 45%/44% |

- **9/11 rules favour the screen** (sign test p=0.033; the rules are highly
  correlated so this overstates the evidence — same caveat as 11/12 on the bounce).
- **Best edge +1.07% against a ±0.58% standard error, t=1.85. Below 2.**
  Not distinguishable from random selection at this sample size.
- **Win rates are identical again** (36% vs 35%), which is the same damning
  number the bounce backtest produced. The screen is not selecting more winners.

**Do not read this as a result yet.** The sample is ~1/4 the bounce backtest's
(780 trades over 54 dates vs 1,300 over 186), it covers one regime (2024), and
the news store only reached 2024-10. Note also that the random arm here is
*negative* where the bounce baseline was +1.60% — a different period and a
different pool (news-covered names are larger and more liquid), so the two
absolute numbers are not comparable. Only the edge column is.

The shape is the same as every honest result in this project: a positive-looking
number that does not clear its own error bar. Re-run on the full window before
believing it.

### What to run when the news backfill finishes

The backtest is **blocked on data, not on code**. `backtest.eligibility()` needs
`MIN_BARS=400` plus 250-session rolling windows, and the bar store starts
2022-07-25, so the first date with any eligible ticker is **2024-02-26** — which
is exactly why the bounce backtest starts at 2024-02. A first catchup filled
2022-12 → 2024-02, a window in which no backtest can ever run; `senti_backtest`
now detects that and prints the fix rather than dying with "no trades produced".

In order:

```bash
python news.py --backfill                          # resume; it is idempotent
python events.py --calibrate                       # re-measure on the full store
python senti_screen.py --from 2024-02-26 --every 3 # build the score series
python replay.py --leaktest                        # all five assertions
python senti_backtest.py --start 2024-02 --every 3 --seeds 10
```

Only the last command decides whether any of this is worth keeping. If the edge
is inside the standard error — as it was for the bounce signal — the honest move
is to say so in the README and leave the badge as context rather than promote
sentiment into the score.
