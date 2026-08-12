# Score modules

Handover doc for the multi-score layer built on top of the bounce screener.
`README.md` covers the bounce screener; `PROJECT_LOG.md` has the dated findings
and the reasoning behind reversals. **This file is the map.**

The goal: every stock carries several independently-updated scores (sentiment,
fundamental, later hype and price-structure), each separately measurable, feeding
one holistic ranking once — and only once — the evidence justifies the weights.

---

## Architecture

```
scores/__init__.py      registry + Module interface + the tidy table
scores/sentiment.py     module 1
scores/fundamental.py   module 2
data/scores/YYYY-MM.parquet     session | ticker | module | metric | value | label
```

**The one architectural decision that matters: scores live in ONE TIDY TABLE, so
a new module adds ROWS, NOT COLUMNS.** A wide table (one column per metric) looks
friendlier and is a trap — every new module migrates the schema of every stored
partition, modules cannot be backfilled independently, and a module emitting a
different metric set per date cannot be represented at all. Tidy means
backtesting one module is a filter and comparing two is a groupby.

A module is any object with `.name`, `.metrics()`, `.compute(asof, tickers)` and
`.selftest()`. Register it, add it to `config.SCORE_MODULES`, done.
`scores.write()` stamps `session` and `module` itself so a module cannot
mislabel its own rows, and refuses any metric not in `.metrics()`.

**Aggregates are never stored.** They are recomputed from a window ending at
`asof` on every call. A stored aggregate is a number whose provenance you cannot
check later, and if its window ever reached one bar too far, nothing errors — the
backtest just quietly improves.

---

## Modules

| file | role | key command |
|---|---|---|
| `news.py` | Alpaca news fetch + store | `--backfill` / `--update` |
| `sentiment.py` | lexicon, event taxonomy, severity (pure) | `--selftest`, `--survey`, `--bench` |
| `events.py` | empirical severity calibration | `--calibrate`, `--show` |
| `macro.py` | breadth, sector ETFs, SIC map, FRED, GPR/EPU | `--update`, `--show DATE` |
| `senti_screen.py` | sentiment screener + interval runner | `--interval`, `--only SYM --explain` |
| `senti_backtest.py` | sentiment vs random control | `--start 2024-02 --seeds 10` |
| `fundamentals.py` | SEC XBRL fact store (point-in-time) | `--backfill`, `--explain SYM` |
| `fund_metrics.py` | 29 metrics, 4 pillars (pure) | `--selftest`, `--list` |
| `fund_screen.py` | fundamental dashboard | `--catchup`, `--only SYM --explain` |
| `scores/hype.py` | attention + narrative premium (module 3) | via `orchestrator --step hype` |
| **`factor_lab.py`** | **any metric → forward returns** | `--module M --leaderboard` |
| **`orchestrator.py`** | **the one scheduled entry point** | `--status`, `--dry-run`, `--step N` |
| **`dashboard.py`** | **master hub, `reports/index.html`** | `--open`, `--print` |
| **`stock_profile.py`** | **per-stock sheet: radar + financials** | `AAPL PLTR --open`, `--flags` |
| `settings.py` | what the profile view shows | `--show`, `--enable X`, `--order` |
| `serve.py` | opt-in local server; makes hub buttons live | `--open`, `--port` |

### orchestrator owns the schedule

Twelve registry steps in fixed execution order, replacing three independent
schedules. Each writes a row to `data/_jobs.parquet`
(`run_id | step | cadence | watermark | started | ended | duration_s | status |
rows | detail | error | traceback`), which is the only thing the master
dashboard reads — the dashboard never triggers work.

Statuses are deliberately distinct: `ok`, `error`, **`blocked`** (a dependency
failed — not the same thing as failing), `skipped` (cadence says not due), and
`slow` (finished, but over its timeout budget). Dueness is a **watermark, never
a wall clock**, so a laptop closed for a week catches up on resume and one
closed for a quarter falls back to `ORCH_MAX_CATCHUP_SESSIONS`.

Cadences: `universe, bars, macro, news, senti_cache, sentiment, bounce,
retention, dashboard` daily; `fundamental, events, leaderboard` weekly;
`sec_facts` quarterly, and its dueness is derived from which quarters are
missing on disk rather than from a calendar.

### the report pages, and who writes which

**One folder per report type, `latest.html` inside each** (reorganised
2026-08-09). Dated files are `<session>.html` in the same folder.

| file | written by | what it is |
|---|---|---|
| `reports/index.html` | **`dashboard.py`** | **the hub** — job status, stores, run history, links |
| `reports/metrics.html` | `metrics_doc.py` | the metric dictionary |
| `reports/explore/latest.html` | `explore.py` | every stock x every score |
| `reports/bounce/latest.html` | `report.build()` | newest bounce report |
| `reports/bounce/index.html` | `report.write_index()` | bounce sessions, newest first |
| `reports/sentiment/latest.html` | `senti_screen` | sentiment dashboard |
| `reports/fundamental/latest.html` | `fund_screen` | fundamental dashboard |
| `reports/stock/<TICKER>.html` | `stock_profile.py` | per-stock sheet |

Before this there were 74 files in one flat directory under four naming
conventions — `explore_<date>` lowercase beside `Pattern_<date>`,
`Sentiment_<date>` and `Fundamental_<date>` capitalised, plus three spellings of
"latest". `index.html` is the hub because that is what a browser opens when
pointed at `reports/`.

**Moving a page does not move its hrefs.** The reorganisation left 223 dead
links in already-rendered HTML, and two of them lived where a `.py` grep could
not see them: `dashboard_template.html` (Jinja) and a hub link inside an
f-string. `python status.py --pages` is the gate — it resolves every relative
href and is the only check that would have caught those.

### factor_lab is the important one

It knows nothing about sentiment or fundamentals. It takes `(ticker, session,
value)` from the tidy table and answers: *ranked on this, what happened next — at
what horizon, in which sector, in which year?* Every future module gets tested by
this same code on these same forward returns, so results stay comparable. A
per-module bespoke backtest would guarantee they are not.

It reports IC (rank correlation, per date, then averaged), quantile spreads with
both legs shown, decay across horizons, turnover, and **a random-ranking control
on every number**.

---

## Data sources — measured, not assumed

| source | status |
|---|---|
| **Alpaca news** `/v1beta1/news` | free, 200/min, history ≥2015, ~1,580 art/day, `limit` caps at **50** |
| **SEC Financial Statement Data Sets** | free, no key, 85 MB/quarter → 3.2 MB stored |
| **SEC EDGAR** SIC + ticker map | free, 10 req/s, User-Agent required |
| **FRED / ALFRED** | free key, 120/min, **vintages** = point-in-time |
| **GPR daily** (Fed Board) | free .xls, needs `xlrd`, back to 1985 |
| **EPU daily** | free CSV, verified 15,191 rows |
| Sector/macro proxy ETFs | via existing `bars.py` path |

**Rejected, with reasons:**

- **SEC `frames` API** — returns 6,251 companies in one 1.6 s request and is
  *unusable*: rows carry no `filed` date, so there is no way to know when a
  number became public.
- **GDELT** — 429s persistently (1 req/5 s enforced; failed 3 of 4 attempts even
  at 20 s spacing). Free but not reliable enough for a scheduled path.
- Polygon (5 req/min), Alpha Vantage (25/day), Finnhub sentiment (premium),
  NewsAPI/Marketaux (100/day), Reddit/StockTwits (restricted, mega-cap skewed).
- **Street consensus** — not reliably free. Macro surprise is therefore
  market-implied (the reaction *is* the surprise) and SUE uses the Foster (1977)
  seasonal random walk. Both are substitutions with known limits, not silent gaps.

---

## Look-ahead discipline

Three separate chokepoints, each in exactly one place:

1. **News → session** (`news.attribute_session`). An article belongs to session S
   iff its timestamp < S's 16:00 ET close; anything later belongs to S+1. An
   article stamped 21:00Z is 17:00 ET — *after* the close.
2. **Facts → visibility** (`fundamentals.facts_asof`). A fact is visible on D iff
   `filed <= D`. **Never `ddate <= D`** — a quarter ending 2024-03-31 is not
   public until the 10-Q lands in May.
3. **Forward returns** (`factor_lab`, `backtest.py`). Entry at the NEXT session's
   open, never the signal bar's close.

`python replay.py --leaktest` asserts all of it: session attribution across
648,595 articles, truncation equivalence, and future-scramble.

**Overlap correction.** `factor_lab` divides the IC t-stat by
`n_eff = dates / (horizon / spacing)`. Without it a 120-session window sampled
every 3 sessions produced t=10.98 — the same result counted forty times. With it,
1.74.

---

## Findings so far

**Sentiment** (`sent_mean_30d`, 308k obs, 151 dates): IC +0.0158 at h=1,
**t=3.29** — the only horizon surviving overlap correction. IC is positive while
the 20d quantile spread is **negative** (−0.35%): the ordering is mildly
informative but the extremes mean-revert, so naive long-top/short-bottom is the
wrong trade. Backtest vs random control: best edge +1.07%, t=1.85, **win rates
identical** (36% vs 35%).

**Fundamentals** (47 monthly dates, h=20): `fund_score` **+0.0351, t=4.36, hit
77%** — the composite beats all 29 of its components, which is the argument for
keeping weights equal. `asset_turnover` t=2.78, `net_issuance` −2.52 (dilution
punished, as theory says).

**Altman Z has an inverted sign** — IC −0.0233, t=−2.90, hit 34%. Higher Z (safer)
predicted *lower* returns: the distress-risk premium. Direction stays +1 anyway;
flipping it would mean systematically ranking fragile balance sheets highest. The
whole safety pillar is flat (t=0.73).

**Event severity is measured, not assumed** (`data/_event_severity.parquet`). The
"dramatic" classes move stocks *less* than average on the headline day —
BANKRUPTCY lift 0.90, MA 0.91, FDA 0.98 — because by the time it is written the
move already happened. Only HALT (2.46x), guidance and earnings are genuinely
high-severity. Hand priors were wrong by up to 6x.

---

## Module 3: hype (added 2026-08-07)

"How much of this price is attention rather than business?" Built only from data
already stored — bars carry `volume` **and** `trades`, the fact store carries
revenue. No new source, key or rate limit.

The novel input is **average trade size** (`volume / trades`): retail attention
raises volume while *shrinking* the average print, whereas institutional
accumulation raises both. Free, already on disk, and it separates the two.

Three pillars, grouped by what kind of quantity each is — `attention` (flow),
`premium` (level, = `ps_ratio`), `stretch` (delta). `premium_score` is the leg
that identifies the PLTR/TSLA case and it works (PLTR 94, MSTR 93, NVDA 90;
AMC 4, F 6).

**Nothing in this module has been measured.** `hype_score` is a magnitude, not a
direction — the literature says attention predicts short-horizon continuation
*and* long-horizon reversal, and this project has twice been wrong by assuming a
sign. `factor_lab` must test all four scores before any earn weight.

## Gotchas that cost real time

- **`facts_asof` suffixes flow concepts.** Weighted-average share counts come
  back as `shares_diluted_ttm` / `shares_basic_ttm`, never bare. Two callers
  looked for the bare names, always fell through to `shares_out`, and **549 of
  2,873 names (19.1%) silently had NaN market cap** — and so NaN `pe`, `pb`,
  `ev_ebitda`, `ev_sales`, `fcf_yield`, `peg`. Always go through
  `fundamentals.share_count()`.
- **Coverage measured against a shrunken denominator cannot detect a missing
  input.** The hype composite reported `cov=1.0` while two of eight members had
  never been built, because the denominator counted only the members that
  existed. Divide by the declared count, not the present count.
- **Do not normalise "extension" by volatility.** Dividing by ATR makes a quiet
  staple look violently extended and a meme look calm — it inverts the signal.
  Use a self-referential percentile instead.
- **Never name a module in the project root after a stdlib module.** `profile.py`
  shadowed `profile` and broke `import cProfile`. Renamed to `stock_profile.py`.
- **Per-share figures are not split-adjusted across the fact store.** Each 10-K
  restates only the comparatives it carries, so a period last reported before a
  split keeps its old denominator: AAPL reads EPS −68% in FY2018 while net income
  rose 23%. `stock_profile.py` detects and flags this; do not "fix" it by
  joining bars.py's split data, which would create a second source of truth.
- **`fundamentals.history()` is NOT point-in-time** — latest restatement wins.
  Right for display, wrong for backtests. `facts_asof` is the only door to
  point-in-time facts, and nothing on a profile page may feed `factor_lab`.

- **Windows `os.replace` fails (WinError 5) when any other process holds the
  target open**, including a plain reader. Killed a 10-hour backfill at chunk
  128/209. All writes go through `store.atomic_replace()`, which retries.
- **"No data" vs "no signal"** must never be conflated. `has_news` and
  `has_fundamentals` are explicit states; `SENTI_MIN_COVERAGE` refuses to score a
  window the news store does not cover. Caught live twice — 13/30 flags read as
  silent when the API said 4, and AAPL/MSFT/ORCL scored NaN because TTM needs 4
  quarters while CELH passed purely for having filed a 10-K.
- **27.6% of (article, ticker) pairs are republications** of the same
  (ticker, session, event). HALT is 1.98x. Dedupe before aggregating.
- **News volume grew ~4x since 2016** (371 → 1,580/day). Rank counts
  cross-sectionally per date; never use raw levels across time.
- **Per-ticker news is sparse**: median flag has 3 articles/30d, 13% have none.
  The sector/macro layer is load-bearing, not decorative.
- `_typed()` must be idempotent — `write()` calls it twice, and `fillna("")` on
  an existing Categorical raises.
- A float `NaN` is **truthy**, so `r.get("x") or "?"` renders the string `"nan"`.

---

## What to run

```bash
python orchestrator.py                 # THE entry point: runs whatever is owed
python orchestrator.py --status        # read the job table, run nothing
python orchestrator.py --dry-run       # what is due, with per-step estimates
python orchestrator.py --step macro    # one step, ignoring cadence
python replay.py --leaktest            # must pass before trusting anything
```

Individual modules still run standalone (`python macro.py --update`,
`python fund_screen.py`, …) and that path is unchanged — the orchestrator calls
the same functions. Only `news.py --backfill --years 10` still needs running by
hand; it is ~6h and belongs overnight.

**Storage, measured (zstd-9 bytes per stored row):** bars **25.0**, news
**91.8**, fundamentals **11.6**, scores **3.2**. `data/` is ~726 MB and projects
to ~810 MB once news reaches 10y. Daily growth ~1.3 MB/session ≈ 330 MB/yr, of
which the **score store is the largest component, not bars** — which is why
`fundamental` is a weekly step.

## Open items

1. **Weight the pillars from measured ICs** — `FUND_WEIGHTS` is all 1.0 as a
   placeholder. Sharper question than "set weights": should `safety_score` be in
   the composite at all, given t=0.73 and the composite currently beating its
   parts? Test by re-running the leaderboard with it excluded.
2. **Re-run everything on the full news backfill** — severity calibration and the
   sentiment backtest both ran on partial coverage.
3. **FinBERT ONNX** behind the `sentiment.score()` seam (~22 ms/article measured
   on this machine), then measure it against the lexicon before promoting it.
4. **FRED key** → `.env` as `FRED_API_KEY=...` (verified working, incl. vintages).
5. Next modules: hype (social/volume), price-structure. Both slot into
   `scores/` and are tested by the same `factor_lab`.

**The standing rule: no metric earns weight in a composite until `factor_lab` has
measured it against the random control.** `W_SUPPORT` was set to 20 by intuition
and support grade A then measured as the weakest grade. That mistake is expensive
and does not need repeating.

## Module 4: dip (added 2026-08-07)

"Strong business, depressed price." A **GATE then a RANK**, never a blend:
`fund_score` top 30% and `growth_score` top 50% to qualify at all; below the gate
there is **no score, not a low one**. Averaging quality with drawdown lets a
collapsing company score highly because the fall compensates for the balance
sheet — the falling knife the thesis exists to avoid.

Depression legs: `drawdown`, `senti_gap` (inverted 30d sentiment), `not_extended`
(inverted `extension_pct`). `mktcap` is emitted so factor_lab can slice by size.

**Measured on 107 dates (2026-08-08).** The **gate works and the ranking does
not**: `dip_gate` reaches t=+2.75, `dip_score` only t=+0.52. Deepening the series
from 43 to 107 dates did not rescue it, which makes this a real negative result
rather than a thin one — being in the qualifying set carries information; how
depressed a qualifier is does not.

## Module 5: combo (added 2026-08-08)

**Three scores, one per evidence horizon** — `combo_h1`, `combo_h20`,
`combo_h60`. Three rather than one because the measurement says three:
sentiment's only signal is at h=1, most fundamentals peak at h=20, `z_score` is
strongest at h=60. Measured Spearman between them is 0.52–0.79, with 34–46% of
names moving more than 20 percentile points — they are not the same ranking.

**The suffix names the EVIDENCE, not a promise.** These were `combo_short` /
`_medium` / `_long` until 2026-08-09. The old names read as claims about when
each score works, and one of them was false: `combo_long` was built from
h=60-significant metrics but peaks at **h=20**. Renaming was the honest fix —
`combo_h60` says only "assembled from 60-day evidence", which is checkable,
and where each actually peaks is measured and stated in `metrics_doc` rather
than encoded in a name.

**No weight is written by hand.** A metric enters a horizon only if its
`study.py` cell at *that* horizon clears |t| ≥ 2 **and** beats the random
control, and its sign is the measured sign. If the study has not run the module
emits nothing rather than falling back to equal weights.

Three refusals, each of which would otherwise inflate the score:

- **No composite of composites.** `fund_score` is already a blend of 29 metrics.
- **No double counting.** Near-duplicates are clustered at |ρ| ≥ 0.9 and one
  survives — this dropped `du_asset_turnover`, `op_margin`, `roic_wacc`,
  `sent_net_*` and `ps_ratio`.
- **No metrics that predict for the wrong reason.** `days_since_filing` clears
  the bar (t=+2.84 at h=60) and is excluded. **The original reason for
  excluding it was wrong** — it said "significant only among small caps", which
  was an artefact of the biased size buckets; on point-in-time buckets it is
  significant everywhere. Re-investigated, the real mechanism is SIZE: Spearman
  **+0.355** with `mktcap`, median cap rising $0.66B → $6.38B across
  filing-recency quintiles, because SEC filing deadlines scale with filer
  status (60/75/90 days). Still excluded, since `mktcap` already is — but now
  on evidence that holds.

### The numbers below are being re-measured (2026-08-11)

Everything in this section was measured before the data was corrected on
2026-08-10/11 — `debt` and `ccc` no longer fabricate zeros, two missing
quarters were recovered, fiscal Q4 is derived, and 153 non-USD filers entered.
`net_debt_ebitda`, one of the five metrics the honest fit picked, lost 37% of
its values in that correction.

`remeasure.py` re-runs the study, re-scores combo on the new admissions, and
redoes the walk-forward. **Treat the figures below as the pre-correction
record** until `data/_remeasure.log` reads `REMEASURE DONE`; the live page at
`reports/metrics.html` renders the current ones from the result files.

### Out-of-sample: `combo_h60` holds, `combo_h1` does not (2026-08-09)

Everything above is fitted and graded on the same history, which is **in-sample**
and will always look good. `oos.py` splits it: refit the admission rule, signs
and theme weights on train dates only, freeze, and grade on dates the fit never
saw. Split 2021-09-29, 88 train / 88 test, overlap-corrected t.

| score | h | in-sample | **out-of-sample** |
|---|---|---|---|
| `combo_h60` | 20 | t=+4.76 · 64% | **t=+3.19 · 69%** |
| `combo_h60` | 60 | t=+4.24 · 74% | **t=+2.21 · 77%** |
| `combo_h20` | 20 | t=+3.60 · 59% | t=+2.03 · 63% |
| `combo_h1` | 1 | t=+2.00 · 59% | t=+0.30 · 54% |

`combo_h1` sat exactly on the |t|≥2 bar in-sample and is indistinguishable from
zero out of sample — selection, not signal, and now labelled as such.

Two limits that must travel with the result. The train fit admitted only **5
metrics for `combo_h60` where the full sample picks 21** (half the dates shrinks
every |t| by ~√2), so what generalised is the *procedure*, not these exact
weights. And the metric definitions, theme assignments, dedup rule and exclusion
list were authored with the full history already seen — researcher degrees of
freedom no split can remove. This bounds the optimism; it does not eliminate it.

**Theme sub-scores publish at `THEME_HORIZON = 20` only**, keyed off the horizon
number. There is deliberately no `th_sentiment`: no sentiment metric survives 20
days, so the series cannot exist. The absence is the measurement.

### The size buckets are now point-in-time (fixed 2026-08-09)

`mktcap` covers all 182 fundamental sessions, so `study.size_bucket_frame()`
reports **POINT-IN-TIME across 182 sessions** rather than the snapshot warning.

Removing the bias inverted the size analysis rather than nudging it: metrics
reaching |t| >= 2 at h=20 went **large 14 -> 44, mid 23 -> 39, small 51 -> 34**.
Any per-size conclusion drawn before this date was measuring "companies that
became large", not large companies.

It also falsified the stated reason for excluding `days_since_filing` from
`combo` -- see the note in `scores/combo.py`. The exclusion stands; the evidence
for it is now Spearman +0.355 with market cap rather than a size split that was
itself biased.

### The old note, kept because the reasoning is the lesson

`study.py` slices by market-cap tercile, and `mktcap` is stored for **1 of 182**
fundamental sessions — it was declared long before `FM.compute()` passed price
inputs through. So terciles fall back to a snapshot applied to all dates, which
means "large" is really *"companies that became large"*: a survivorship and
momentum bias favouring large and punishing small.

`size_bucket_frame()` logs this on every run and switches to true point-in-time
bucketing automatically once ≥ 24 sessions carry `mktcap`. **The fix is a data
backfill, not a code change.** Until then, read the `all` bucket as sound and
every per-size number as directional only.

Note this does *not* involve the pre-2012 XBRL phase-in (19 filers in 2009q2
rising to 6,747 by 2012q1) — the study samples 2016-08 onward, so that cliff
affects only the deep financial-history tables on profile pages.

## Viewing and exploring

| page | built by | what |
|---|---|---|
| `reports/explore.html` | `explore.py` | all tickers x all metrics, sortable + filterable |
| `reports/stock/<T>.html` | `stock_profile.py` | radar, financials, ratio trends |

`explore.py`: a missing metric **sorts last in both directions and is excluded by
a numeric filter** — never treated as 0. Rows render on demand so sorting stays
instant across 3,464 names.

`stock_profile.py`: any key in `DERIVED` (margins, roe, roa, current_ratio,
debt_to_equity, asset_turnover, rnd_intensity) or any `fundamentals.TAGS` concept
can be a row, with a sparkline and a trend strip. **Margin rows trend in
percentage POINTS** — "+50%" on a margin that went 2% to 3% reads like a
different business.
