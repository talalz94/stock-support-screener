# Support Bounce Screener

Finds US stocks that **ran up parabolically, retraced all the way back to the base
they launched from, held that base, and are now bouncing.**

> ## ⚠ READ THIS FIRST — the edge is small and not statistically established
>
> Backtest: **1,300 flags**, 2024-02 → 2026-08, 186 test dates, per-date eligibility
> computed without look-ahead, **entry at the next session's open** (the signal bar's
> close is not tradeable).
>
> | 2-ATR trailing stop | mean/trade | median | win | held |
> |---|---|---|---|---|
> | **screen flags** (n=1,300) | **+2.16%** | −3.68% | 41% | 13d |
> | **random pick, same pool, same rule** (n=13,000) | **+1.60%** | −3.5% | 41% | — |
> | **edge** | **+0.56%** | | ±0% | |
>
> **The edge is smaller than the ±0.96% standard error of the measurement.**
> Welch t-test: p = 0.28 — *not distinguishable from random selection*. Win rates are
> identical. Most of the screen's +2.16% is simply the pool it draws from.
>
> All 12 exit rules produce a positive mean; best by expectancy is `stop2_hold20`
> (+2.13%, edge +0.77%). Full table: `python status.py`.
>
> **What is genuinely suggestive**
>
> - **11 of 12 exit rules favour the screen** (sign-test p = 0.003). The rules are
>   highly correlated so this overstates the evidence, but the consistency of sign
>   is not what a zero edge looks like.
> - **`RECENT` setups (peak 41–90 sessions back)**: +4.69% mean, +3.09% edge, 47%
>   win, raw p = 0.005. It is the strongest signal in the data — but of **17 subgroup
>   comparisons, zero survive a Bonferroni correction** (RECENT lands at p = 0.088).
>   Treat it as a hypothesis for out-of-sample confirmation, not a filter to trade.
>
> **What is contradicted**
>
> - **`EARLY` is the only bucket with a negative mean** (−0.72%, edge −2.32%),
>   despite being designed as the earliest entry. Waiting for confirmation beat
>   anticipating the turn, so `BUCKET_ORDER` was changed to lead with `PRIME`.
> - **Support grade A is the weakest grade** (n=1,011, edge +0.04%) while B (+2.38%)
>   and C (+2.31%) look better. Support quality carries the largest score weight
>   (`W_SUPPORT = 20`) and the data does not justify it.
>
> **Why the numbers are optimistic anyway**
>
> - **Costs are not modelled.** No spread, commission, slippage or borrow. A
>   0.5–0.8% edge on sub-$5 names is plausibly consumed entirely by the spread.
> - **Survivorship.** The universe is Alpaca's *current* asset list, so companies
>   that delisted mid-window were never fetched. `python backtest.py --survivorship`
>   reports near-zero observable attrition, but that is a **structural blind spot**,
>   not good news: the store was built from today's list, so the missing names are
>   missing by construction. **This got worse on 2026-08-07, not better**: the bar
>   store was extended from 4y to 10y (back to 2016-07), and the further back the
>   window reaches, the larger the invisible graveyard of names that had already
>   delisted before today's list was drawn. Depth buys statistical power and buys
>   *more* survivorship bias at the same time.
>
> **Bottom line.** Treat the daily list as **a research queue, not a buy list.**
> There may be a real ~0.5% edge per trade; it is not proven, and it is small enough
> that execution quality would decide whether it survives. The distribution is
> right-skewed (median negative, mean positive), so taking *every* signal at equal
> size is the only way the mean is available to you — cherry-picking will get you the
> median.
>
> Reproduce: `python backtest.py --start 2024-02 --every 3 --exits --base-rates`
> then `python baseline.py --seeds 10`.
>
> *An earlier version of this file reported a clearly negative result. That was
> median-only on a right-skewed distribution, on a 183-flag sample, entering at an
> untradeable price. The numbers above supersede it.*

Calibration anchor is **RDW** (Redwire, NYSE): base ~7.4–9.7 through Feb–Mar 2026,
a run to **26.64 on 2026-05-28**, a two-month decline, a bottom at **7.76 on
2026-07-29** right on the pre-run base, then a bounce. Every threshold in
`config.py` is annotated with RDW's value, so a change that would have excluded
the motivating example is immediately visible.

---

## Quick start

```bash
python orchestrator.py --dry-run    # see what is due, touch nothing
python orchestrator.py              # everything that is owed, ~3-4 min
python orchestrator.py --status     # last run of every step, read-only
```

`orchestrator.py` is **the** scheduled entry point as of 2026-08-07: it runs the
bounce screen alongside the sentiment and fundamental score modules, catches up
whatever was missed while the laptop was off, and records every step to
`data/_jobs.parquet`. `daily_run.py` still works standalone and is unchanged —
it is now the bounce pipeline *inside* the orchestrator rather than a second
schedule.

```bash
python daily_run.py --dry-run       # bounce screener alone, ~60-90s
python daily_run.py
```

Then open **`reports\index.html`** — the status hub. Bookmark that one: it shows
whether each job step ran, what it produced, and what broke, and it links the
three analysis dashboards. It reads the job table and file sizes only, so
opening it never costs anything and never triggers work.

- **`reports\index.html`** — status hub (job steps, stores, run history)
- `reports\latest.html` — newest bounce report; `bounce_index.html` for all sessions
- `reports\sentiment_latest.html` — sentiment dashboard
- `reports\fundamental_latest.html` — fundamental dashboard
- `reports\stock\<TICKER>.html` — per-stock sheet: score radar, annual
  financials with sparklines and YoY shading, sector and TradingView link

```bash
python stock_profile.py AAPL PLTR --open   # build per-stock sheets
python settings.py --show                  # customise what they show
python serve.py --open                     # opt-in: makes the hub buttons live
```

`serve.py` is the only way to get buttons that actually run a job — a static
page cannot spawn a process. It binds `127.0.0.1` only, validates every step
name against the registry, and refuses to start while another run holds the
lock. Without it the hub still works exactly as before, with copy-able commands.

The same bounce rows are in `reports\Pattern_<date>.csv` for Excel.

To make it automatic:

```bash
powershell -ExecutionPolicy Bypass -File setup_schedule.ps1
```

That registers `PatternScan-DailyRun` at **03:00** local (= 17:00 ET the previous
day, an hour after the close) plus an at-logon catch-up, using the same proven
recipe as the `Stock Screener` project's live task.

---

## Reading the report

Sections are ordered by **how much of the move is still ahead**, not by
conviction:

| bucket | meaning |
|---|---|
| `EARLY` | At the level, **has not turned yet**. The whole move is ahead — highest value if you want to catch the bounce, lowest conviction. These can sit for weeks, which is why they stay on the list with a day counter. |
| `PRIME` | Structure complete and the turn has started, a few sessions in. |
| `SPEC` | Real pattern, thin tape or a weaker level. Size accordingly. |
| `WATCH` | Passed, weaker on one dimension. |
| `LATE` | Already ran. Shown deliberately so you know you are late **before** you buy. |

Each card carries six independent tags — price tier, setup age, size, bounce
stage, support grade, liquidity — and the **group by** selector re-sections the
whole page by any of them client-side. "Just the small price stocks" and "just the
old setups" are both one click.

Also on the page:

- **Near miss · large & liquid** — big names that failed *exactly one* gate, with
  the gate named. On a day when every flag is a micro-cap, this is where the names
  you would actually trade show up.
- **Dropped off** — names that left the list, **with the reason**:
  `support_broken` (thesis dead), `went_extended` (you missed it), or
  `bounce_stalled` (still alive, losing momentum). Without this a stock you were
  tracking just silently vanishes.
- **NEW / day N / PROMOTED** badges. `PROMOTED` means it moved up a bucket since
  the last session — an `EARLY → PRIME` transition is the base you were watching
  turning.

### The chart on each card

- solid line — price (envelope for older history, closes for the last 90 sessions)
- **dashed blue line** — the detected support level, spanning only its evidence,
  with a **tick per prior touch**
- **shaded band** — the pre-run base zone
- **red triangle** — the dominant peak
- **green dot** — the retracement low
- blue dot — the latest close

---

## Categories

**Price tier** — `MICRO` $1.50–4.99 · `LOW` $5–14.99 · `MID` $15–49.99 ·
`HIGH` $50–199.99 · `PREMIUM` ≥$200. "Small price stocks" = MICRO + LOW.

**Setup age**, sessions since the dominant peak — `FRESH` 25–40 · `RECENT` 41–90 ·
`MATURE` 91–180 · `OLD` 181–300. Outside that range is rejected: under 25 is a
two-week pullback, not a round trip.

**Bounce stage** is **ATR-normalised**, `(close − low) / ATR at the low`, not
percent off the low. This matters: RDW at +24% in three sessions reads as
"extended" against any fixed percentage that also admits a low-volatility name's
+8% bounce. In ATR units it is ~2.0 — `CONFIRMED`. ATR is sampled *at the low*
because the bounce prints the widest bars of the sequence, so using current ATR
inflates the denominator and makes late bounces look early.

**Support grade** — `A` ≥4 touches & ≥60d span · `B` 3 & ≥30d · `C` 2. Span counts
independently of touch count: four touches in one week is one event, not four.

---

## Daily operation

- **Runs itself** at 03:00, plus at logon if the laptop was closed. `pythonw.exe`,
  so no console window.
- **One shot** — no daemon, no polling loop, no resident process. Zero footprint
  between runs.
- **~70 seconds** measured end to end (`--no-confirm` drops it to ~45s):

  | step | time |
  |---|---|
  | universe refresh | 8s |
  | bars delta (already current) | 2s |
  | screen 5,374 → 717 → 24 | 30s |
  | hourly + market cap for 24 names | 23s |
  | report + state + outcomes | 4s |

  On a session with new bars to fetch, add ~45s for the delta and split recheck.
- **Skipped days are handled.** Gap detection is calendar-driven, so a week off
  fills in on the next run. Missed sessions are then reconciled from the
  already-local bars purely so `days_on_list` stays honest — otherwise a setup you
  were away for reads "NEW" when it has been holding for three sessions.
- **Enable / disable:**

```bash
python daily_run.py --pause      # task still fires, exits in <1s
python daily_run.py --resume
python daily_run.py --once       # run now, ignore the pause
```

`status.py` prints `AUTO: enabled` or `AUTO: PAUSED` at the top so the state is
never ambiguous. For a longer break:
`setup_schedule.ps1 -Remove`.

---

## Sentiment & news (score module 1)

A **separate** screener on its own interval, plus a read-only news badge on every
bounce card. It does not change a gate or a score weight in `screen.py` — that
waits on `senti_backtest.py`, for the same reason the bounce signal did.

```bash
python news.py --backfill            # ~2.6 h, resumable. 4y measured at 93 MB;
                                     # NEWS_HISTORY_YEARS is now 10, so a plain
                                     # --backfill targets ~178 MB / ~6 h
python senti_screen.py               # one pass: fetch, score, report (~15 s)
python senti_screen.py --interval    # every SENTI_INTERVAL_MIN during market hours
python senti_screen.py --only RDW --explain
```

Output is **`reports\sentiment_latest.html`**, ranked by **severity** — "what
happened", not "is it good news". Direction lives in a separate column,
deliberately: a guidance cut and a buyback are both major events and only one is
bad news.

Source is Alpaca's news API on the **same keys** — free, 200 req/min, history to
2015, ~1,580 articles/day. Nothing new to install.

### The three numbers that shape it

- **Coverage is brutally skewed.** Across the 30 live flags over 30 days: ORCL 95
  articles, the **median flag 3**, and **4 of 30 zero**. So `has_news` is an
  explicit state — a silent ticker emits `has_news=0` and *nothing else*, because
  imputing "neutral" would drop 13% of the list into the middle of the ranking by
  accident. And the macro/sector layer is load-bearing, not decorative.
- **Volume grew ~4x since 2016** (371 → 1,580 articles/day). Raw counts trend with
  Benzinga's output, not the market, so every count metric is ranked
  **cross-sectionally within each date**.
- **27.6% of (article, ticker) pairs are republications of the same event** — one
  circuit-breaker halt appeared six times. Deduped on `(ticker, session,
  event_type)`; otherwise one dramatic day carries the weight of six.

### Severity is measured, and the hand priors were wrong by up to 6x

Calibrated over 184,966 (article, session) pairs as the **median** |return| / ATR
on the attributed session (`python events.py --show`):

| event | measured lift | | event | measured lift |
|---|---|---|---|---|
| **HALT** | **2.46** | | MA | 0.91 |
| GUIDANCE_CUT | 1.92 | | BANKRUPTCY | **0.90** |
| EARNINGS_BEAT | 1.88 | | SHORT_REPORT | **0.83** |
| EARNINGS_MISS | 1.79 | | FDA | 0.98 |

**Nearly every "dramatic" class moves stocks less than average on the headline
day.** That is not a bug: by the time the wire writes "Files For Chapter 11", the
collapse already happened over prior sessions. Only **scheduled, discrete
repricing events** — earnings, guidance, halts — are genuinely high-severity.

Median rather than mean, because event responses are violently right-skewed: one
name printed **+842%** on a halt, dragging that class's mean to +25.8% against a
+3.3% median. The 2026-08-05 log entry records the same lesson pointing the other
way.

The taxonomy validates on signed returns — BEAT **+0.37%**, MISS **−0.78%**,
RAISE **+0.67%**, CUT **−0.60%** — and `events.py --selftest` asserts those four
signs, so a future regex edit that breaks them fails loudly.

### Macro, rates and geopolitics

`python macro.py --update` builds `data/_macro.parquet`, ordered by reliability —
which is **not** the same as "free":

1. **Breadth from your own bar store.** 5,383 tickers, no key, no rate limit,
   perfectly historical. The most reliable macro signal available here.
2. **24 sector + macro-proxy ETFs** through the existing bars path.
3. **SEC EDGAR SIC codes** for the sector map (RDW → 3760 → Aerospace → ITA).
4. **GPR** (Fed Board geopolitical risk, daily to 1985) and **EPU** (policy
   uncertainty, daily) — both free, no key. GPR printed 91 → **225** across
   2026-08-01→03, which is the Iran headlines as a number.
5. **FRED/ALFRED** — CPI, PPI, NFP, the fed target range, and *market-implied
   expectations* (breakevens, the 2y path, the curve). Needs a free key in `.env`
   as `FRED_API_KEY`; without it the rest still builds.

**Forecast vs actual.** Street consensus is the one input that is not reliably
free — and is not needed:

> **surprise = ATR-normalised move of the macro proxy basket on a session with a
> scheduled release**

The market's reaction *is* the surprise. Caveat, stated plainly: this measures
**reaction**, not **deviation from expectation**; the two differ when the market
has pre-positioned.

**GDELT is deliberately not used.** Measured: one request per 5 seconds enforced,
and it 429'd on 3 of 4 attempts even at 20-second spacing. Free, not reliable.

### Correctness

`python replay.py --leaktest` gained three assertions beside the existing two.
The important one is **session attribution**:

> an article belongs to session **S** iff its timestamp < S's close; anything at
> or after S's close belongs to **S+1**

An article stamped 21:00Z is 17:00 ET — an hour *after* the close. Attributing it
to that day lets a screen computed on that close "know" tonight's news, and
nothing errors. Currently passing across **376,278 articles**, plus truncation
equivalence and a future-scramble test that rewrites every post-`asof` headline
as bankruptcy text and asserts no metric moves.

---

## Files

| file | role |
|---|---|
| `config.py` | **every** tunable: paths, gates, batch sizes, tag boundaries, score weights |
| `scores/` | score-module registry + the tidy `session\|ticker\|module\|metric\|value` table |
| `news.py` | Alpaca news fetch + month-partitioned store; **the session-attribution chokepoint** |
| `sentiment.py` | lexicon, event taxonomy, severity. Pure. `--selftest`, `--bench`, `--survey` |
| `events.py` | empirical severity calibration from the bar store |
| `macro.py` | breadth, sector ETFs, SEC sector map, FRED, GPR/EPU, release surprise |
| `senti_screen.py` | the sentiment screener and its interval runner |
| `senti_backtest.py` | sentiment vs a random pick from the same eligible pool |
| `alpaca.py` | REST layer: rate limiter, retry, batched bars with mid-symbol-safe pagination |
| `calendar_us.py` | trading sessions; `last_closed_session()` and `bars_end_ts()` |
| `universe.py` | Alpaca assets + Nasdaq directory → operating companies, with a lifecycle registry |
| `store.py` | month-partitioned parquet; the single partial-bar chokepoint |
| `bars.py` | `--backfill` / `--update`, plus the panel-stats cache |
| `dataset.py` | read-only seam over the store |
| `indicators.py` | copied **unmodified** from the WaveTrend project (Pine-validated) |
| `levels.py` | pivots, level clustering, touch counting, level score `Q` |
| `pattern.py` | run/base identification, retrace metrics, bounce score `B`, extension, composite |
| `screen.py` | the gate funnel; `--only SYM --explain` for a full per-gate trace |
| `classify.py` | tags + bucket. Pure. `--selftest` |
| `confirm.py` | optional hourly + market-cap enrichment of the shortlist |
| `state.py` | setup identity, days-on-list, forward outcomes |
| `report.py` | CSV + HTML + text digest |
| `replay.py` | **`--leaktest`** and historical replay |
| `status.py` | health report |
| `daily_run.py` | the bounce pipeline; standalone, and the `bounce` step of the orchestrator |
| **`orchestrator.py`** | **THE scheduled entry point** — 13-step registry, watermark catch-up, `data/_jobs.parquet` |
| **`dashboard.py`** | **the status hub**, `reports/index.html` — read-only, never triggers work |

---

## Tuning

Every rejected ticker records **all** the gates it failed, not just the first, so
threshold questions are a groupby rather than twelve re-runs:

```python
import pandas as pd
r = pd.read_parquet("data/rejects/2026-08-03.parquet")
r["failed_gates"].str.split(",").explode().value_counts()
```

Trace one name end to end:

```bash
python screen.py --only RDW --explain
```

Rules of thumb: **0 results → loosen `MIN_RUN_X` first** (highest-leverage gate).
**Too many → raise `MIN_PRIOR_TOUCHES` to 3 and `RETRACE_LO` to 0.85**, which
tightens quality without changing what the pattern *is*.

To rank purely on signal and ignore company size, set `SIZE_BIAS_WEIGHT = 0`.

### Calibrated, not assumed

`MIN_RUN_Z` was planned at 3.0 and measured wrong: across the 152 names clearing
the run and retrace gates the distribution is p05=0.90, p50=1.58, p95=3.14, so 3.0
would have kept 6% of them — and rejected RDW itself, whose base-window ATR is 10%
of price rather than the assumed 4%. It is **1.25**, keeping ~74%.

---

## Correctness

```bash
python replay.py --leaktest
python classify.py --selftest
```

`--leaktest` is the important one. `scipy.signal.find_peaks` is **not causal**: a
peak's prominence is `min(left drop, right drop)`, so computed over a full series a
peak 30 bars back can be flagged *only because* of a plunge five bars in the
future. Filtering pivot indices to `<= asof` does not fix it — the prominence
values themselves are contaminated. `screen_one` therefore slices its window
**once** at the top and recomputes pivots from the truncated series at every
`asof`. Two assertions verify it:

1. **truncation equivalence** — `screen_one(df, a) == screen_one(df[:a+1], a)`
2. **future scramble** — replace every bar after `asof` with noise; not one output
   field may move

Both currently pass across 72 (ticker, asof) pairs. This matters because a leaking
screener produces a beautiful backtest and mediocre live results, and *nothing
errors*.

Other traps handled, each with a comment at the site: the current test must not
count as a support touch for itself (otherwise every fresh 52-week low "has
support"); level selection must not re-derive the base per candidate (per-ticker
in-sample fitting); the volume baseline is taken *at* the low, not trailing
(otherwise the best setups score worst); and any bar dated at or after the last
closed session is dropped in one chokepoint, because an in-progress bar fakes a
bounce low.

---

## Caveats

- **Screening only.** No execution costs, slippage, or borrow are modelled, and
  this is not advice.
- **Replay results are an upper bound.** Alpaca's asset list is current-only, so a
  historical replay omits names that have since delisted — and those skew toward
  failed bounces. `state.py`'s forward tracking has no such bias, which is why
  both exist.
- **Free-tier SIP** cannot be queried inside the last 15 minutes. `end` always
  comes from `calendar_us.bars_end_ts()`; a bare date of today resolves to
  *end of day* and 403s even hours after the close.
- **Split adjustment is retroactive**, so a 10-session trailing window is re-fetched
  and overwritten on each update, and outcome returns are recomputed from fresh
  bars rather than from a stored scalar.
- **Sector clustering is real.** These setups arrive in clusters (space, EV,
  lithium, solar all ran and crashed together), so a day's list is far less
  diversified than its length suggests.
