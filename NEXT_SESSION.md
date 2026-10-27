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


## State at 2026-10-21 (early morning) — THE PROVIDER CHECK TELLS "NOT SENT" FROM "OUT OF BOUNDS"

This settles the open decision in the section below. `_cross_scale_once` now splits what
`_sane` dropped into three cases:

* **A number out of bounds fails, on any single ticker.** Unchanged. This is the multiplier
  bug: a wrong multiplier always leaves a number (the 27.62 net margin), never a None. It is
  still re-checked once on a fresh fetch before the step fails.
* **A value the provider did not send (None/NaN) is logged and passes**, as long as that
  provider sent the field for another of the five. The run log gets
  `[provider] 5 value(s) not sent by the provider, passed ...: yahoo JNJ fcf=None roe=None ...`,
  so the gap is visible on every fresh read, not silent.
* **A field sent for NO ticker fails** (`field(s) not sent for ANY large cap: yahoo roe`),
  because that is what a renamed Yahoo key looks like. "Sent" is read from the provider's
  frame, so a row served from cache this pass counts. An unknown source counts as not sent.
* **Live proof:** a fresh fetch at 04:06 failed under the old rule on the same five JNJ
  fields. At 04:22 the same fetch passed: "16 shared field(s) agree on scale across
  5 ticker(s), 0 out of bounds, 5 not sent". Full live `providers.py --selftest`: OK.
* **Test**, renamed `_selftest_recheck` -> `_selftest_cross_scale_offline`:
  * clean -> pass, no re-check;
  * KO not sending five fields -> pass at once, with the note;
  * roe not sent for any ticker -> fails twice;
  * KO roe = 41 then clean -> pass on a ttl-0 re-check; twice -> fail; then
    unreachable -> fail;
  * Finnhub P/E x100 -> SCALE MISMATCH.

  Five source mutations each fail it: the old rule, no all-tickers rule, one out-of-bounds
  ticker tolerated, re-check from cache, and skip-as-pass.
* **What it gives up:** a field missing on 1-4 of the five no longer stops the step, so the
  comparison can shrink by that much, logged each time. The Finnhub side never had this
  check: `_finnhub_one` keeps only numeric values, so a missing Finnhub key never reaches
  `_sane`. That is unchanged.
* **Expected 10-22 05:00**, the first fresh Yahoo read: pass, with a run-log note naming
  JNJ for as long as Yahoo lacks its data.

## State at 2026-10-21 (small hours) — THE `provider` FAILURES WERE NOT TRANSIENT: YAHOO'S JNJ ROW, MASKED BY ITS OWN CACHE

The step failed 10-16 05:06, 10-17 09:03 and 10-20 15:07 on `_selftest_cross_scale`:
`{'fcf': 1, 'roe': 1, 'roa': 1, 'current_ratio': 1, 'quick_ratio': 1}`, and each time
fundamental/dip/combo/candidates/floor/sectors/validate waited for the 2-hourly retry.

* **Now logged per ticker.** `_sane(row, src)` records (provider, ticker, field, value) while
  a trace is open; the cross-check logs one line per drop to `_providers.log` (value, the
  multiplier if one was applied, the bound, and what the OTHER provider has), and the
  assertion now leads with `yahoo JNJ fcf=None roe=None ...`, inside the 140 chars the run
  log keeps. The first live run with it named the cause straight away.
* **The cause, measured 10-21 04:06-04:11:** Yahoo's JNJ row has None for all five fields on
  **10 of 10** fresh fetches over 3 min (`mostRecentQuarter` 2026-09-27, reported 10-13);
  Finnhub has roe 0.257, roa 0.106, current 1.089, quick 0.725. `_sane` counts None as a drop.
  Not a multiplier: a wrong multiplier gives a number out of bounds, never a None.
* **Why every retry "passed":** `fetch` reads Yahoo through the 20 h cache, and the failed
  pass had cached JNJ's row with those fields already dropped. A default-TTL pass at 04:11
  read that row and reported "16 shared field(s) agree ... 0 dropped". Each failure was a
  first read more than 20 h after the previous pass (23.9 h, 25.6 h, 65.9 h), and each
  retry pass came less than 20 h after a failure had cached the row (2.3 h, 12.2 h, 1.5 h).
  The 10-20 15:55 "live re-fetch dropped nothing" probably went through that same cache
  (unverified). The
  drop check therefore only sees Yahoo once a day, on the first read after the cache expires.
* **Re-check added anyway** (`_cross_scale_with_recheck`): on a scale or drop failure, wait
  `CROSS_SCALE_RECHECK_WAIT` = 60 s and re-run ONCE, with Yahoo refetched (`ttl_hours=0`), so
  it cannot pass the way those retries did. A second failure fails the step, and so does a
  re-check that cannot reach a provider ("skipped" is not "agreed"). A rescue goes in the
  run log as `[provider] cross-scale passed on a fresh re-check ...`. **It does not fix the
  JNJ case**, because that gap persists past 60 s. Expect the next fresh read (the first
  `provider` run more than 20 h after the last one, so likely 10-22 05:00) to fail on
  `yahoo JNJ`, then pass on the cache at the 07:00 retry, unless Yahoo fills the row first.
* Test: `_selftest_recheck` (offline, in `providers.selftest()`): clean -> no re-check;
  bad then clean -> pass, re-check used ttl 0, log names `yahoo KO roe=None` and finnhub's
  value; bad twice -> fail; bad then unreachable -> fail; Finnhub P/E x100 twice -> SCALE
  MISMATCH, fail. Four deliberate breaks of the logic (re-check reading the cache, no
  re-check, skip-as-pass, untraced drops) each fail it. Full `providers.py --selftest`
  live: OK, 19 s. It writes nothing to `_providers.log`.
* **Decided the same morning** (section above): a value the provider did not SEND
  (None/NaN) is now told apart from one out of bounds, so the 10-22 failure expected
  above should not happen.

## State at 2026-10-27 (evening) — INSIDER AND HOUSE TRADES: OUR OWN STORE, AND WHAT THEY PREDICT

The user asked for a free, self-maintained version of a paid "insider buying" scanner
(Ross's Insiders IQ: corporate insiders + politicians, filters for cluster size, $ value,
role, market cap, rarity and each insider's track record). Phase 1 (data + automation)
and phase 2 (does it predict?) are done; pages and scoring (phase 3) wait on these results.

**Data (phase 1).**
* `insider.py` -- SEC Form 4. History: SEC's quarterly Insider Transactions Data Sets,
  2006q1-2026q3, 83 zips, **7,224,299 trades** in 18.5 min (`--backfill`); the file URL
  moved from `structureddata` to `datastandardsinnovation` in 2026, both tried. Nightly:
  EDGAR daily index -> each new Form 4's full submission (one request, ~6 KB) -> the same
  normalizer; 2026-10-01..26: 8,155 filings, 0 failed, 12,746 trades, 27.7 min (a normal
  night is ~600 filings). Personal fields (addresses, e-mail, phone) are never read in.
  One primary owner per filing (a director over her trust), `n_owners`/`owner_ciks` kept.
* `purchases()`: code P, acquired, price > 0, DIRECTORS AND OFFICERS only (10%-only
  holders, "Other" relationships and entities out -- an insurer's "$2e14 purchase" of
  closed-end fund DNP was the case). `price_ok()`: NOT against our bars -- they are
  split-adjusted, so 14% of checked purchases looked out of range, 400 of 467 by a plain
  split ratio, the rest serial reverse-splitters (WHLR adjusted close 4.6e10). Against
  PEERS: the company's other insider trades within +-15 days, 0.5-2x, plus caps (filing >
  $500M, price > $10,000 without peers). 3,766 purchases dropped (typos like "$40M per
  share", 1,000,000x off). Store health: 2% no price, 1.4% duplicate rows, 255 trades
  dated after their filing, 6.8% filed 30+ days late. 50% of purchases map to a CURRENT
  ticker: the rest are companies since delisted -- the survivorship gap, stated below.
* `congress.py` -- House PTRs: the Clerk's yearly index ZIP + one PDF per report, kept on
  disk (`--reparse` re-reads without downloading). THE SENATE IS NOT HERE: efdsearch
  answers HTTP 403 to an honest, declared user agent, and getting past it would mean
  defeating bot protection. PAPER filings are scanned images, counted not read: 41% of
  2016's PTRs, 18% of 2020's, 12% of 2026's. TWO LAYOUTS: 2016-17 extract as one run-on
  line ("FILINg STATUS:", no [ST] codes, "03/7/2016"); later reports line by line with
  codes. Detected per report; the old one needed an owner-code boundary rule because the
  generic camel join split surnames ("McExampleJTAnadarko"). The Clerk's server takes 4-6s
  a PDF: 4 workers, ~1 request/s overall. `kind` keeps what the filer's description says
  it was (dividend reinvestment, gift/donation, option exercise, rebalancing, transfer).
  Use: 5 U.S.C. 13107(c) bars commercial use; data stays in data/, never committed.
* Nightly steps `insider` and `congress` after `sec_edgar` (31 steps); NOTHING depends on
  them. validate group `insider` is WARN-only (outside data): store current, prices
  believable, trades dated after filing, unmapped, EDGAR failures, House coverage.

**Insider study (phase 2), `insider_study.py`, 2016-09..2026-09, current-universe tickers.**
Definitions fixed first (in the module). Event = (ticker, FILING date); entry the next
session's close; benchmark = same-day stocks in the same liquidity fifth; one episode per
ticker per 60 sessions; t across 41 calendar quarters. 61,350 buy-days, 173,618 sell-days.
60 tests, ~3 expected at |t| >= 2 by chance; 10 reached it:

    test (60 sessions unless noted)        episodes  mean pp  median pp  beat peers  t      t 1st / 2nd half
    any purchase >= $10K                      14,196    +0.68     -0.79      47.8%   1.71   -0.09 / 3.70
    Ross: 2+ buyers, $250K+, not largest       2,967    +0.95     -1.30      46.6%   1.49    0.76 / 1.43
    2+ / 3+ buyers in 30 days               5,809/2,984 +0.80/+1.15 -1.19/-0.99  ~47%  0.68/1.41
    >= $250K / >= $1M in 30 days            6,569/2,889 +0.62/+0.20 -0.83/-1.56  ~47%  0.40/0.49
    SMALL (bottom 2 liquidity fifths)          6,869    +1.35     -1.11      47.3%   3.69    1.67 / 3.58  (20d 3.04, 40d 2.37)
    first buy in 2+ years (rare)               9,115    +1.05     -0.47      48.7%   2.54    1.42 / 2.11  (+ at 20d, 40d)
    CEO or CFO bought                          5,315    +0.91     -1.33      46.7%   2.24    1.49 / 1.73
    buyer with a good track record             3,588    +1.34     -0.40      48.5%   2.60    0.57 / 3.21  (20/40d 1st half NEGATIVE)
    unplanned purchases, 2023+                 5,193    +1.37     -0.56      48.6%   2.81    (14 quarters only)
    routine buyer (40 sessions)                  364    -1.78     -2.00      44.2%  -2.48
    opportunistic buyer                        1,718    -0.24     -1.14      46.3%  -0.78    (CMP's split does NOT replicate here)
    3+ sellers, $1M+, unplanned (SELLING)     16,155    +0.65     -0.35      49.0%   2.11    3.48 / -0.26  (not bearish)

READ IT PLAINLY: in our data insider buying is a FAT-TAIL bet, not a typical-stock edge.
Every median is negative and fewer than half beat their same-size peers; the positive
means come from a minority of big winners. The cleanest effects: SMALL companies (stable
in both halves) and RARE buying (positive at every horizon, both halves) -- the two
things Ross and the literature stress. Clusters, dollar size, Ross's own screen,
beaten-down and the 10b5-1 split are not significant. Bias: only names still in our bar
store (half of all purchases are in companies since delisted, often failures), so these
results are FLATTERED; small-cap spreads and costs are not modelled.
Phase 3 candidate score: small x rare (x CEO/CFO), validated walk-forward before use.

**House study, `congress_study.py`.** Store after `--reparse` (both layouts): 6,979 PTRs
2016-2026, 41,019 trades (24,026 stock rows); coverage by year in the summary (electronic
reports 0-25 unreadable a year; paper 315 in 2016 -> 53 in 2026). Purchases = [ST], ttype P,
`kind` "trade" (dividend reinvestments, gifts, exercises out), current-universe tickers:
9,045. Event = the day the report was FILED; entry the next session. 27 tests, ~1.4 by chance:

    test (60 sessions unless noted)           episodes  mean pp  median pp  beat peers   t     t 1st / 2nd
    any member stock purchase                    4,530    +0.12     -0.74      47.7%   1.28    0.26 / 1.61
    purchase >= $15,001                          1,077    +1.37     +0.25      50.5%   1.73    1.48 / 0.96
    PURCHASE >= $50,001                            376    +3.46     +1.94      57.7%   3.36    2.76 / 2.10  (40d +1.84, t 2.34)
    purchase >= $250,001                            55    +3.71     +5.00      63.6%   0.74    (too few)
    call options bought                             38    -3.18     -1.10      42.1%  -0.84
    2+ members bought within 30 days               652    +0.12     -0.50      48.9%   0.73
    sale >= $15,001 (selling)                    1,295    +0.39     -0.37      49.0%   1.37    (not bearish)
    dividend reinvestment (CONTROL)                119    +1.26     +1.96      58.8%  -0.04    (~0, as it should be)
    spouse / joint / child purchase              3,011    +0.09     -0.77      47.5%   0.84
    entering on the TRADE date (not tradeable)   4,619    -0.05     -0.67      47.9%  -0.11

READ IT PLAINLY: the average House member's stock purchase predicts nothing -- not even
entered on the trade date, which no outsider can do, so the reporting delay is not what
hides an edge. LARGE purchases ($50K+) are the exception, stable in both halves, and the
one result in either study where the typical (median) event beats its peers. Report lag:
median 28 days, 10% over 245 days. House only; paper filings unread.

**Bugs met and fixed on the way:** a lone UTF-16 surrogate in one 2020 PDF's text stopped
the first backfill at Parquet (now dropped at extraction); a filer's "3031-04-30" trade
date overflowed pandas (dates outside 1995..next year are blank, at parse and on read).

## State at 2026-10-27 — A WATCHLIST TRACKED DAILY: "GAINERS AT SUPPORT" FROM 10-23

The user asked for stocks, by sector, that were often among the market's biggest daily
gainers over the past year and now sit at or near support -- and then to TRACK them, to
judge the method later. The list: 181 stocks (top 10 per sector by days in the daily
top-20 % gainers over 252 sessions, $5+ and $20M+/day, among stocks AT (<=2.5%) or NEAR
(<=6%) a Zones support level on the 10-23 close). Kept at
`data/watchlists/gainers_at_support_20261023/list.csv`.

`watchlist_track.py` recomputes it from the bar store each run (no appending): per stock
since the 10-23 close, and the list's own eligibility pool as the benchmark. Statuses fixed
before any result: per plan (above support and above entry), held, below support,
failed (a low >10% under the level, the zones study's definition; final). A session
counts only once 90% of entry-day names have a bar (the 10-19 partial-day lesson).
History per stock per session in `history.csv` -- what the later verdict should use.

Day 1 (10-26): per plan 96, held 48, below support 37, failed 0; list +0.46% vs pool
+0.35% (n=1,935), up 53% vs 58% -- no edge, one day. Judge it at ~20 sessions (the
studies' horizon), against the pool, and by status rates; also check whether a level's
past failure rate predicted anything (on day 1 it did not: 50-54% per plan in every band).

Dashboard: claude.ai artifact "Gainers at support" (datasets gainers / tracking /
tracking_summary). Refreshed by the desktop scheduled task `gainers-at-support-tracking`
(Tue-Sat 09:23: waits for the orchestrator lock, runs the tracker, uploads tracking.csv +
summary.json, repoints the two datasets, deletes the replaced files, writes
`last_uploaded.txt`). Not part of the orchestrator.

## State at 2026-10-20 (evening) — BANKS GET A REAL REVENUE: NII + NONINTEREST INCOME

Traditional banks tag no total revenue; they report net interest income and noninterest
income. Our `revenue` was their fee line, which the component rule (10-14) withholds,
so ~30 regional banks (FITB, HBAN, MTB, ZION...) had no fund_score at all.

* `fundamentals.TAGS["nonint_income"] = ["NoninterestIncome"]`. `nii` is
  `InterestIncomeExpenseNet`, which already belongs to `interest_exp` -- and the two alias
  maps disagree on duplicates (history() keeps the FIRST concept, facts_asof the LAST), so
  listing it twice would have silently moved it out of `interest_exp` in facts_asof.
  `SHARED_TAGS` + `_add_shared_concepts` COPY its rows under `nii` in both maps instead.
* `fund_metrics.derive`: where both halves exist over twelve months, their sum replaces a
  stored revenue that is MISSING or below `BANK_TOTAL_SHARE` = 90% of it, before the
  component check. Measured on the 251 filers with both halves: withheld fee lines sat at
  0.00-0.25 of the sum (median 0.08, 53 names); filers tagging their own total at 0.93-1.01
  (BNY 20,080M vs a sum of 21,367M, HDB, MYFW) -- kept, the filer's figure wins; nothing
  between 0.78 and 0.93 (NAVI 0.45, ASB 0.69, ISTR 0.78 replaced). Same rule on the profile
  pages' history table (`stock_profile._bank_revenue`) and in validate's rows WARN.
* Data: bulk re-ingest `--backfill --years 5 --force --newest-first` (22 quarters 2021q3-
  2026q3, 0 failed, 22.6 min); companyfacts refetched for the 307 FINANCIALS among the 623
  `nii` filers already in that store (308 companies, 0 failed) -- the newer companyfacts copy
  wins per partition and predated the tag; FITB had 4 `NoninterestIncome` rows since 2024
  against SEC's 225. Not the ~150 non-financial `nii` filers: they report no noninterest
  income, and a refetch would only have moved their other figures. EDGAR store rebuilt
  (`edgar.py --since 2026-07-28`, 152 filings, 1 failed; old copy
  `data/fundamentals_edgar_pre20261020`).
* Effect (asof 10-19): `nonint_income_ttm` 8 -> 251 names. Scored revenue changed for 203:
  198 had none and now do (145 had no revenue tag at all, 53 a withheld fee line), 5 larger
  (ASB, ISTR, NAVI by the rule; CBU, NTRS by refreshed data); 0 lost. Every other TTM field:
  4 names changed, all financials, no net-income move over 5%.
* Hand-checked from the filed legs, equal to the pipeline: FITB NII 5,982 + 4,149 - 2,932 =
  7,199M, noninterest 3,035 + 1,954 - 1,444 = 3,545M -> revenue 10,744M; HBAN 7,041 + 2,677 =
  9,718M; MTB 7,084 + 2,877 = 9,961M. Pins FITB nii/nonint, MTB nonint (40/40).
* Re-score of 10-19 (11 steps, 0 failed, validate CLEAN rows:pass): fund_score **2,789 ->
  2,926** (+137: 130 banks, 3 REITs, 3 other financials, 1 broker; 0 lost) -- FITB 55.1,
  HBAN 61.1, MTB 61.5, TFC 53.9 scored for the first time; ZION 80.6 on its value pillar
  alone -> 71.8 with growth too. dip 549 -> 630, Candidates 76 -> 96. 66 already-scored
  names moved >1 point, ALL financials -- fund_score ranks within sector and 137 peers
  joined (48 moved >5). Rows WARN "revenue is a component": 61 -> **7** (AD, ATEX, CPT,
  FVCB, HYPD, PURR, SITC; FVCB is a bank still missing one half). Revenue known for 3,044
  names (was 2,916). `profiles` in the pipeline: 134s.
* Read growth with care for acquirers: FITB rev_growth +24% (Comerica), HBAN +26%
  (Veritex/Cadence) are reported totals including the acquired bank, not organic growth.

## State at 2026-10-20 (afternoon) — PROFILES 2,527s -> 170s, PAGES IDENTICAL

Baseline: the 10-20 nightly `profiles` step, 96 pages, 2,527s (no stray process running;
it was 1,192s on 10-11, so the step was over its 900s budget on its own). After: the SAME
`orchestrator._step_profiles("2026-10-19")` run into a copy of those pages (so it picked the
same 96 tickers): **170.3s**. All 97 files (96 pages + index) are identical to the nightly's
except the two build timestamps on each page ("page built <b>..</b>", "Built ..").
Method: scratchpad `prof_step.py` (wall clock, no profiler); diff masks only
`(page built <b>|built )YYYY-MM-DD HH:MM(:SS)`, case-insensitive.

What was slow, and the fix (each returns the rows the old code returned):
1. **The fact-store prime never hit.** `_step_profiles` primed `prime_history(tk, 16, "Q")`
   from when pages showed 16 quarters; `build()` now asks for every period
   (`PERIODS_ALL` = 400), whose start is centuries back, so history()'s cache key was
   always later than the start it wanted: every page re-read the store twice. Now primed
   with `PERIODS_ALL, "A"`, which covers both tables.
2. **Score history read once per module.** `scores.read` opens each month file whole, so
   five modules read the same 72 files five times. `stock_profile.prime_histories` reads
   once and splits by module.
3. **Bars read three times per page for one ticker** (header 400d, chart 5y, support level).
   `prime_bars` reads the batch once; `_bars` slices it (`store.read` is a pure filter +
   sort, so a slice is the same rows).
4. **`latest_scores` read a month file per module per page.** It now shares
   `_SESSION_CACHE` with `peer_percentiles`.
5. `fundamentals.currency_of`: the unit -> currency map per DISTINCT unit (43) instead of
   per row (4.1M): 4.2s -> 1.1s per process, identical on every row and on NaN/None.

The 10-14 cProfile of one cold page (527s) badly overstated Python-call-heavy costs -- the
profiler's per-call overhead plus the stray `find` -- e.g. it put 48s on (5), which is 4.2s.
Measure wall clock first; profile only to locate.

**`sectors` failed in the 16:39 retry (`KeyError('group')`) -- caused by my 04:07 backfill.**
It wrote 10-19 bars for the five new names before the nightly fetched everyone else's;
`sectors.load_wide()`'s cache, rebuilt at 04:10, ended on 10-19 with 3 tickers on that row
and passed its date-only freshness check from then on, so every group fell below
MIN_MEMBERS. `_bars_newer_than_cache()`: the cache is also stale when any bar partition is
newer than it. Rebuilt (10-19: 5,373 tickers); `sectors`, `explore`, `dashboard`, `docs`
re-run 18:07 -- Energy 100, Healthcare 88, Consumer Discretionary 85.

The sector-score selftest then FAILED (live rs21 vs the study by 0.0004 on 2026-09-01):
not a code fault -- SNDK and CEG now carry 10 years of history, so Technology and
Utilities (breadth and returns) and every sector's return-vs-market (the market average)
moved, while `_sector_panel_sector.parquet` predated them. `sector_study.py` re-run (186s):
out-of-sample composite IC **+0.141, t +4.10** over 55 months (was +0.142, t +4.13);
selftest OK; quotes updated (sector_score, metrics_doc, orchestrator).

**`provider` failed again at 15:07** on its large-cap cross-check (one of AAPL/MSFT/KO/JNJ/
XOM had fcf, roe, roa, current and quick ratio out of bounds in ONE Finnhub response);
re-fetched live at 15:55 all five were clean. Third time in five days, each cured by the
retry 2h later, but each time every scoring step for the session waits. Open: re-check
once after a short pause before failing (a real multiplier bug survives a re-check).

## State at 2026-10-20 (small hours) — SANDISK AND CONSTELLATION ENERGY WERE NEVER IN THE UNIVERSE

Found by a top-gainers study (scratchpad `gainers.py`) that could not find SNDK. Nasdaq's
symbol directory still names SNDK "Sandisk Corporation - Common Stock When-Issued" (spun
from WDC, Feb 2025) and CEG "Constellation Energy ... When-Issued" (from Exelon, Feb 2022);
`config.NAME_KILL_PATTERN` drops "when.issued", so both large caps were filtered out since
listing. `universe._name_kill_hit`: "when-issued" now drops a name only when Alpaca's name
says it too (Alpaca's are current). `python universe.py --explain SNDK CEG` -> KEPT. Refresh
added CEG, DCM, LCU, SNDK, SRTM; `bars.py --backfill` fetched their 10 years (28 tickers,
63s). Their scores start with the 10-20 nightly run. Backup of the old registry:
scratchpad `universe_pre20261020.parquet`.

The week since 10-14 (run log): sessions 10-14/15/16 all scored, validate CLEAN rows:pass.
`provider` tripped its large-cap bounds guard twice (one name's fcf/roe/roa/current/quick
out of bounds) and passed on the retry; `sec_gap` failed once (companyfacts 316 of 478,
SEC-side); `events` took 35,456s on 10-17 against a 466-977s norm while the stray `find`
from 10-13 was still running (gone by 10-20). `profiles` is still over budget (24 min median).

`sectors.load_wide()` rebuilt its 3-minute cache on EVERY call this morning -- bars end
10-16 while the calendar's last closed session is 10-19, so it never looks fresh. Not fixed.

## State at 2026-10-14 — ROWS THAT CONTRADICTED THEMSELVES, BANK REVENUE, A STALE SECTOR MAP; A SOFI CATEGORY; A VALIDATED SECTOR SCORE; SEASONALITY MEASURED NOT PREDICTIVE

Done unattended (user away ~06:40-16:30, asked to keep going and to resume itself after
any usage-limit pause -- a session cron job every hour at :43 did that once). Order agreed
beforehand: fix the data, then the SOFI pattern, the sector score, seasonality (price
first, no EIA/NOAA keys), then the integrated view. 29 pipeline steps now.

### 1. THE DATA: ROWS, BANK REVENUE, THE SECTOR MAP

**Why the earlier audits missed it:** every check compared a FIELD with its SOURCE (SEC,
Finnhub, Yahoo). The provider overlay swaps fields one by one, so a row could hold
Finnhub's EPS beside our P/E and pass every field check while contradicting itself.
New nightly `rows` validation group (validate.check_rows, run by the validate step):

    10-13 before    positive P/E beside a loss EPS 65 | provider P/E vs EPS >25% apart 214
                    | margin sign vs EPS 38 | revenue below net income 75 (65 Financials)
    after re-score  validate CLEAN, rows:pass

`scores/fundamental._row_consistency` (after the overlay, before ranking): a loss EPS gets
no P/E; when P/E and EPS disagree by >25%, OUR SEC EPS breaks the tie (the provider's EPS
matched SEC 10 of 12 checked -- IBKR 2.512 vs 2.52 with a P/E of 131, a split the P/E
never absorbed -- but for ADRs the P/E is right and the EPS is per ordinary share: HSBC
2.204 x 5 = 11.0 per ADR); with no SEC EPS the P/E is kept and the EPS WITHHELD; a margin
contradicting the EPS sign is withheld with its sister margin. First run: pe_blank 65,
pe_recomputed 74, eps_recomputed 32, eps_withheld 109, margins_withheld 37.

**Bank revenue.** SOFI's revenue was its fee line ($610M TTM) -- banks report net interest
income separately. Added `RevenuesNetOfInterestExpense` to `revenue` and made the
AGGREGATE_MAX rule (largest alias wins -- the REXR fix) apply in the 4-quarter sum and the
same-day tie between rolls too; it applied only to the annual path. Data: bulk re-ingested
3 years (14 quarters, 7.7 min, 0 failed), EDGAR store rebuilt (91 filings), companyfacts
refetched for the 150 tickers / 43 CIKs that report the tag (read() lets the newer source
win per partition, and the old companyfacts copy lacked it). SOFI revenue_ttm 610.2M ->
**4,305.7M** = FY25 3,613.4 + H1-26 2,319.0 - H1-25 1,626.7 (pinned); net margin 104% ->
14.8%; fund_score 24.9 -> 55. Pins 35/35. Still 71 names (60 traditional banks) with no
total revenue tagged: their impossible revenue is now WITHHELD in `fund_metrics.derive`
(blank, not scored). OPEN, user decision: NII + noninterest income needs
`InterestIncomeExpenseNet` moved out of `interest_exp`, which 413 filers (147
non-financial) use. Also open: the Piotroski F-score is structurally low for banks (blank
current ratio / gross margin count as fails).

**The sector map** (`data/_sector_map.parquet`) was last written 2026-08-07 and never
refreshed: 614 of 3,467 tradeable names had no SIC and read "Other" -- the LARGEST sector on
every page -- including XOM (its 10-Q now comes from a new CIK). Rebuilt from the fact
store plus SEC submissions for the gaps (`macro._fill_missing_sic`, cached in
`data/_sic_cache.parquet`): no-SIC 614 -> 133, "Other" 630 -> 152; XOM -> Energy, NVS ->
pharma, TM -> autos. `fundamentals.sector_map` read companyfacts-only quarters as bulk
paths (flagged by edgar-ingest 10-12) -- fixed. The `sectors` step now rebuilds the map
weekly. fund_score ranks within sector, so 10-13 was re-scored again after this.

metrics_doc selftest repaired: its dip assertion froze "not significant" and broke when
the re-run measured t +2.06; PEG's documented range 0.5-3 -> 0.15-1.3 (measured IQR).

**Afternoon: a regression of mine, caught by checking who LOST a score.** After the
sector-map re-score 2,800 names had a fund_score against 2,839 on 10-12. Of the 49 lost:
38 had revenue withheld (30 regional banks -- FITB, HBAN, MTB, ZION...), 4 a margin
withheld, 7 had no rows (universe). Looking at the non-banks found two errors:

* **AD -$1,538M and TDS -$493M revenue_ttm.** My morning change put "largest alias wins"
  AHEAD of the filing date in `_ttm`'s quarterly dedup, so an ORIGINAL beat its own
  RESTATEMENT: AD restated 2025 for the wireless sale (Q1 $891M -> $27M), the originals
  won and the derived Q4 = FY - Q1 - Q2 - Q3 came out -$1,691M. Fixed: the filing date
  decides, the max applies only WITHIN a filing -- in `_ttm`, `_latest` and `history()`
  (the last two had the cross-filing max since 08-07). That exposed rows the max had
  hidden: FIX's Q1-2026 10-Q carries a twelve-month revenue of $1,831M for 2025-12-31
  beside its 10-K's $9,102M (JAKK $113M vs $571M) -- so an ANNUAL revenue now comes from
  an annual report when one exists (`_ANNUAL_FORM_RE`). Net: revenue_ttm moved on 118
  names; against each name's latest 10-K year the new figure is closer for 37 of 59 that
  moved >2%, and every large error went (3 negatives, WTRG 2.11x, VISN 1.52x, TFX 1.31x,
  CALY 1.26x, DD 1.21x); "before closer" are small gaps or real growth (GPN bought
  Worldpay). Pins AD 213.53M and FIX 11,228M added (37/37); a selftest feeds AD's filed
  rows through `_ttm` (old code -1,537,957,000, new 213,530,000).
* **"Revenue below net income" blanked real revenue** where a tax release or a gain on a
  sale lifts net income above it (AUPH, RIGL, TRS, AIV, VISN, DUOT). Now exempt when
  operating income (else pre-tax) is known and below revenue -- operating first, because
  pre-tax carries non-operating gains (BYND: pre-tax $300M on $259M revenue, operating
  LOSS $304M). validate's WARN uses the same rule and a new WARN flags negative revenue.

After both fixes (re-score 3): 2,804 scored; 45 that had a score on 10-12 do not -- 30
regional banks (NII + noninterest income is the open fix, below), 7 with no rows on 10-13
(universe), 3 margin-withheld (ALLT, PDS, PTRN), CPT (a REIT whose stored revenue is a
$13M component of ~$1.5B), AD and ATEX (licence/spectrum-sale gains put OPERATING income
above revenue, so the rule cannot tell them from a component), HYPD, OSG. TDS, AUPH, TRS,
RIGL, AIV, VISN, DUOT, XMAX are scored again.

**A stray process is slowing the machine.** `find / -iname *STEAL_The_1-Minute*` (Git
Bash, PID 14916) has run since 2026-10-13 07:02 with its parent gone -- 87,700 CPU-s, one
full core, plus a whole-disk walk contending for I/O. The profiles step took 5,355s (1,886s
in the morning, 1,192s on 10-11) and one cold page 527s, 420s of it in 748 score-parquet
reads. Stopping it was refused by the permission classifier -- the USER should end it
(Task Manager, `find.exe`). Profiles was over its 900s budget before this anyway.

### 2. FLOOR RETEST -- THE SOFI PATTERN (`floorretest_study.py`, `floor_page.py`)

Definition fixed before results: a run >= 2x within 2 years (high older than the floor
window); close 35-70% below that high and >= 1.3x the run start; a floor = lowest low of
the last 9 months with >= 3 swing lows within 3%, >= 10 sessions apart, over >= 60
sessions; close within 6% of it; >= $10M/day. ONE change before any result: the window
was declared 6 months and missed SOFI's own March/April floor tests; widened to 9.
RARE: 0.04% of liquid rows, usually none on a day -- so measured as an event study (each
episode once, vs the same-day liquid pool, t across quarters). 20 tests, ~1 by chance:

    tier                      episodes   40 days: up vs pool   excess (median)    t     floor broke
    pattern                      129       58% vs 54%         +4.29pp (+1.42)   +1.36      32%
    + quieting                    66       56% vs 54%         +4.70pp (+0.36)   +1.25      31%
    NEAR MISS (1 short)          411       49% vs 54%         -1.66pp (-2.18)   -2.88      34%
    pullback, no floor          6,003      50% vs 52%         +0.08pp           +1.28      18%
    floor, no prior run           941      51% vs 53%         -1.06pp           -1.97      17%

Positive but NOT proven, high variance (the floor broke twice as often as for controls).
Near misses are measured WORSE -- the page shows full matches by default and labels near
misses "measured WORSE". 10-13: SOFI the only full match (+ SVRA, BKNG, SRAD near misses).
Page `reports/zones/floor.html`, nav "Floor retest", step `floor`.

### 3. SECTOR SCORE (`sectors.py`, `sector_study.py`, `sector_score.py`)

Groups: the SIC sector + hand-named sub-industries (energy split into producers, drilling
& services, equipment, refiners, gas pipelines, gas utilities, electric utilities, coal);
equal-weight point-in-time returns over names tradeable that day. 32 tests declared.
After the sector-map repair (first-run figures in brackets):

    sector, next month (21 groups, 109 months 2017-09..2026-09)
      breadth200 (share above 200d)   t +4.42 [+3.95]   top-minus-bottom third +1.02pp/mo, t +3.23
      rs252 (12 mo vs market)         t +3.41 [+3.56]   +1.15pp/mo, t +3.27
      rs21  (1 mo)                    t +3.58 [+3.01]
      median fund_score               t +0.34
    composite chosen on 2017-09..2022-02 = rs21 + breadth200, then on UNSEEN 2022-03..2026-09:
      IC +0.142, t +4.13 over 55 months  [first run +0.106, t +2.95]
    sub-industries: rs252 t +3.07, breadth t +3.19 over the full history, but nothing passed
      on the first half alone -> context only, no score

`sector_score.py` computes it daily (selftest: equals the study exactly on 2026-09-01) ->
`data/sectors/`; Explore column "Sector score" (joined in explore.collect); Sectors page
`reports/sectors/latest.html` (sectors with score, sub-industries as context, each group's
stocks in Candidates / Floor retest / Bounce flag / AT a zone); step `sectors`.

### 4. SEASONALITY (`season_study.py`) -- MEASURED, NOT PREDICTIVE

Same-calendar-month history (prior years only, >= 4) vs that month's excess, 72 months
2020-10..2026-09: sectors IC -0.013 t -0.37, sub-industries -0.027 t -0.95, stocks +0.002
t +0.16. Energy calendar profiles: 4 of 108 cells |t| >= 2 (~5 by chance); gas utilities
show NO winter strength (Nov -1.1, Dec -1.1, Jan -0.9). As agreed, no demand data was added;
seasonality is shown on the Sectors page as context, labelled not predictive.

### PIPELINE AND FILES

Steps added: `floor`, `sectors` (29 total; order in README). New: floorretest_study.py,
floor_page.py, sectors.py, sector_study.py, season_study.py, sector_score.py,
sectors_page.py. Data: _floor_panel, _floor_study_summary.json, _wide_1d.parquet,
_sector_panel_*.parquet, _sector_study_summary.json, _season_study_summary.json,
data/sectors/, _sic_cache.parquet; backups _fundamentals_edgar_pre20261014/.

## State at 2026-10-12 (later morning) — COMBO IN EXPLORE, TWO METRICS NO LONGER DROPPED, REBUILD RUN DETACHED

- **Explore shows the combined scores**: `combo_h1`, `combo_h20`, `combo_h60` lead
  `explore.DEFAULT_METRICS` (they were in the store, never in the table). Their tooltips
  carry the out-of-sample verdicts. Checked on the page: AAPL 70.08 / 62.59 / 55.59.
- **Two significant metrics were silently refused as "unthemed"** (the combo selftest
  warning): `above_200dma` (hype; t +2.18 at h=1 on 344 dates) now sits in the
  `attention` theme and enters `combo_h1` (admitted pre-dedup 27 -> 28);
  `debt_to_equity` replaces `debt_equity` in `safety` -- no module ever emitted
  `debt_equity`. It is still held out by COMBO_MIN_DATES (29 dates at h=20) and enters on
  its own once its history reaches 100. Selftest: no unthemed warning.
- **The rebuild runs as a one-off Windows task, not under Claude**, so the app can be
  closed: task `Screener-OneOff-Rebuild-20261012` runs `data/_bg_rebuild_20261012.py`
  (pythonw, hidden): `orchestrator.py --step combo --step explore --step profiles --step
  dashboard --step docs`, then `metrics_doc.py --build`, the combo selftest and the page
  audit. Log `data/_bg_rebuild_20261012.log`; result `data/_bg_rebuild_20261012_status.json`
  (per step: rc, seconds). The task deletes itself when done (and expires after 12 h).
  Needs the laptop on and the user logged in, like the nightly task.

## State at 2026-10-12 (morning) — COMBO NOW REQUIRES 100 DATES OF EVIDENCE PER METRIC

The study re-run (section below) admitted seven metrics into `combo_h1`/`combo_h20` on
about one earnings season of history (23-48 dates; three at h=20 on ~two independent
windows, "hit" 100%). Fixed by rule, not by hand: a study cell must rest on at least
`config.COMBO_MIN_DATES` = 100 dates before its metric may enter a combo. Every
established admitted cell has >= 178 dates and the thin ones <= 48, so ANY floor from
49 to 178 admits the same set -- 100 is not tuned. Metrics re-enter on their own once
their history reaches it. `scores/combo.py` selftest now asserts it.

    admitted (pre-dedup)   h1 33 -> 27   h20 28 -> 25   h60 22 -> 22 (unchanged)
    dropped                eps_growth_q, ebitda_growth, gross_margin_chg, op_margin_chg,
                           roa (h1, 48 dates), gross_margin (h1 and h20), rev_growth_q (h20)

Combo for 10-09 rebuilt (`--step combo --step explore --step profiles --step dashboard
--step docs`, 0 failed) and `metrics_doc.py --build` rerun. Effect, same session:

    score   fixed vs thin-metric version              fixed vs the August weights
    h1      Spearman 0.98, top-50 41/50, +135 names   Spearman 0.91, top-50 22/50
    h20     Spearman 0.97, top-50 36/50, +218 names   Spearman 0.91, top-50 29/50
    h60     identical                                 Spearman 0.94, top-50 23/50

The change against August is the legitimate one: two more months of evidence
(fund_score h20 t +5.2 -> +7.6; mom_12_1 dropped out).

NOT DONE, NOTED: the combo selftest reports two significant metrics with no theme,
silently dropped -- `above_200dma`, and `debt_to_equity` (THEMES lists `debt_equity`,
which may be the same metric under an old name). Admitting them is a model change and
was left for a decision. Combo HISTORY is still on the old weights (only 10-09 is new),
so per-ticker combo trends show a step there; `combo_refresh.py` would rebuild it.

First nightly `sec_edgar` (05:00): ok, 9.7 s, 28 universe filings listed since 10-01, 26
already ingested, 2 already in bulk/companyfacts, 4 requests.

**Then 10-09 was re-scored** (`--step fundamental ... docs`, 10 ran, 0 failed, validate
CLEAN) so the pages carry the EDGAR filings and the two TTM fixes: PEP now scores on its
10-08 filing, DAL and MU on 10-09, NKE 10-02, CTAS/STZ/LEVI 10-07 (KO unchanged, 07-29).
Interface checked against the store: Explore (PEP 58.34, MU 78.71, KO 55.49, TM market cap
"-"), all 93 profiles rebuilt with combo @ 2026-10-09 (AAPL combo_h20 62.6 on page and in
store), hub lists `sec_edgar` and the EDGAR store, metrics page shows the re-run study
(fund_score h20 t 7.6), page audit 0 problems over all pages. Explore has NO combo column
-- the combined scores appear on profile pages only; that is how Explore was built.

**THE "UNEXPLAINED" SLOW FUNDAMENTAL RUNS ARE EXPLAINED.** This re-score took 3,739 s and
the process used 243 s of CPU in its first hour, holding one HTTPS connection to finnhub.io.
`providers.TTL_HOURS = 20`: the overlay re-fetches every name whose cached Finnhub row is
older than 20 h, at the free tier's ~54-60 names/min (3,200 of 3,527 after 59 min). In the
nightly run the `provider` step refreshes the cache minutes before `fundamental`, so it is
fast (100-250 s); any re-score OUTSIDE that run (10-11 07:33, 10-12 05:52, and almost
certainly 09-30) pays the full ~60-minute refetch inside `fundamental`. Not a bug; a
re-score off-cycle should expect it.


## State at 2026-10-12 (small hours) — THE FACTOR STUDY RE-RUN: TWO MORE MONTHS MOVED THE NUMBERS, AND SEVEN NEW METRICS ENTERED COMBO ON TOO LITTLE HISTORY

Asked: `validate --only claims` warned that the study measures stale data ("3 past
month(s) rewritten since the study ran (2026-08-13 04:30)"). Re-ran `study.py` from
scratch, rebuilt everything that reads it, and compared old against new.
**Done: study re-run, combo/explore/dashboard/docs rebuilt, `metrics.html` rebuilt,
`validate --only claims` 2 ok / 0 warn, page audit 297 pages clean.**

### WHAT RAN, AND ONE DEVIATION FROM THE BRIEF

- Old table kept: `data/_factor_study_pre_20261011.parquet` (md5-identical to the
  08-13 table, 1,536 cells).
- **The original was NOT moved aside.** The nightly `combo` step reads
  `data/_factor_study.parquet`; a half-built table there (or none) at a 04:40 stop
  would have fitted 05:00's combo on partial evidence or emitted nothing. `study.OUT`
  was pointed at staging files instead (same from-scratch measurement; `study.py`
  skips only what its OUT file holds). The merged table replaced the live one
  atomically at 01:24 on 10-12.
- **Speed.** One process measured **1.7 cells/min** against 3.8 in the August run
  (sentiment, same code; CPU-bound on ~1 core, the laptop was busy with other work).
  At that rate 1,936 cells was ~16 h, so it was killed at 96 saved cells and split by
  module over 3 processes at BelowNormal priority (A sentiment+dip 161 min, B
  fundamental 205 min, C hype+combo 195 min; ~1-2.5 GB each, 4-6 GB free). Done 23:47,
  4h24m wall. `keepawake` held the whole time: **0 sleep gaps** in all three. Nothing
  overlapped the 05:00 run; the 04:40 self-stop never fired.
- **1,856 cells written, not the 1,936 planned.** The 80 missing are five label
  metrics with no numeric rows (`top_event`, `event_types`, `top_severity_band`,
  `top_headline`, fundamental `currency`), absent from the old table too.
- To redo it in parallel: wrapper + merge notes are in the shared notes folder
  (`run_study.py`: `--out FILE --module M`, 04:40 deadline). Merge = concat the OUT
  files, `drop_duplicates(module, metric, horizon, size)`.
- Rebuilt with `orchestrator.py --wait-for-lock 60 --step combo --step explore --step
  dashboard --step docs` (4 ran, 0 failed, 25 s) **and `python metrics_doc.py
  --build`. That second one is in NO nightly step: `reports/metrics.html` was dated
  Aug 11 -- the metric dictionary had been two months stale.**
- That rebuild also explains the "−338 rows between two `validate --only key` runs"
  noted in the TTM section below: combo 10-09 is 39,206 rows under the old study and
  38,868 under the new, exactly −338. Not a loss; fewer names clear combo's coverage bar.

### POPULATION

Scored history 2016-09-26 .. 2026-10-09: 233 fundamental sessions, 278 dip, 375
sentiment dates (h1 cell), hype from 2020-08-19; 1,700-3,300 tickers per date,
depending on the metric. Forward
returns from the NEXT open at h = 1, 5, 20, 60. `t` is the study's own: mean per-date
Spearman IC over its standard error, with the overlap correction `n_eff = n_dates /
(h / spacing)`, floored at 2. (The random-draw "luck p" is not used anywhere below.)
`size = all` cells: 464, 438 with a t (26 NaN: 13 new fundamentals at h=60, which have
no 60-session window yet, 5 combo output cells, and 8 that were NaN in August too);
median 232 dates (min 23, max 375), against median 182 (max 321) in August. Cells:
1,536 -> 1,856; metrics 96 -> 116 (115 distinct names; `mktcap` sits in two
modules): 13 new fundamentals + 7 combo outputs; metrics reaching |t| >= 2 at their best
horizon 58 -> 71.

### WHAT CHANGED, AND WHY (the control that matters)

Cell by cell (376 `size = all` cells with a t in both): corr(IC) 0.97, corr(t) 0.93;
median |ΔIC| 0.0019, median |Δt| 0.28, p90 1.30. Cells at |t| >= 2: 108 -> 129
(31 crossed up, 10 crossed down). The IC changed sign in 37 cells, but in only 7 does
either t reach 1, and in none does either reach 2. By horizon, cells at |t| >= 2:
h1 32 -> 39, h5 5 -> 15, h20 40 -> 40, h60 31 -> 35.

**Is it new data or rewritten history?** Re-measured 152 old-metric cells (66
metrics; every cell that reaches |t| >= 2 in either study or moved >= 1.2) on the OLD window only
-- rows <= 2026-08-12, forward returns from bars <= 08-12 -- with today's stored scores.
**The history that existed in August measures the same today: median |Δt| 0.06, p90
0.35, largest 0.82, none above 1. 87% of the total |t| change is the two extra months;
13% is rewriting.** (So the "3 past months rewritten" warning was mostly sessions
appended to the July/August/September partitions, not changed values.)

| cell (size=all) | IC old -> new | t old (dates) | same window, today's data | t new (dates) |
|---|---|---|---|---|
| fundamental `fund_score` h20 | +0.0254 -> +0.0370 | +5.21 (179) | +5.21 (178) | **+7.63** (213) |
| fundamental `fund_score` h1 | +0.0152 -> +0.0229 | +3.13 (182) | +3.26 (191) | +4.97 (232) |
| fundamental `safety_score` h20 | +0.0046 -> +0.0176 | +1.16 (179) | +1.16 (178) | **+4.38** (213) |
| fundamental `net_debt_ebitda` h20 | -0.0143 -> -0.0277 | -2.66 (179) | -2.67 (178) | -5.18 (213) |
| fundamental `fcf_yield` h20 | +0.0232 -> +0.0358 | +3.24 (179) | +3.31 (178) | +5.36 (213) |
| fundamental `roic` h20 | +0.0302 -> +0.0352 | +4.08 (179) | +4.17 (178) | +5.22 (213) |
| fundamental `z_score` h60 | -0.0567 -> -0.0563 | -4.63 (175) | -4.62 (174) | -4.63 (178) |
| fundamental `mom_12_1` h20 | +0.0287 -> +0.0346 | +2.65 (167) | +2.65 (167) | **+0.79** (202) |
| dip `dip_gate` h20 | +0.0151 -> +0.0208 | +4.33 (219) | +4.33 (219) | +5.86 (258) |
| hype `avg_trade_size` h60 | +0.0463 -> +0.0446 | +4.11 (290) | +4.11 (290) | +3.56 (295) |
| sentiment `sent_mean_30d` h1 | +0.0110 -> +0.0103 | +3.18 (321) | +3.08 (334) | +3.25 (375) |
| combo `combo_h20` h20 | +0.0303 -> +0.0306 | +2.91 (174) | +2.91 (174) | +3.32 (213) |
| combo `combo_h60` h60 | +0.0464 -> +0.0457 | +2.62 (171) | +2.62 (171) | +2.62 (174) |
| combo `combo_h1` h1 | +0.0165 -> +0.0220 | +2.11 (178) | +2.02 (191) | +3.00 (232) |

Random-ranking control IC on every one of these is within ±0.004. Full cell table:
`data/_study_compare_20261012_cells.csv` (size=all, sorted by |Δt|); control:
`data/_study_compare_20261012_same_window_control.csv`.

Read plainly: **the old measurements were not wrong; they were two months short.**
The 08-13 -> 10-09 stretch was very kind to quality, safety and cash-flow metrics
(`safety_score` h20 went from nothing to t +4.4 on those months alone) and unkind to
momentum (`mom_12_1`'s IC rose slightly while its t fell from 2.65 to 0.79, which
implies a much wider date-to-date IC spread). Two months cannot say whether that is a
regime or persistence; the long-history metrics (`z_score`, `avg_trade_size`,
`dip_gate`) barely moved. `combo_h*` cells measure combo's STORED series, which is the
OLD-weights score on every session -- see "NOT DONE" below.

### WHICH METRICS COMBO ADMITS NOW

Rule: |t| >= 2 at that horizon, |IC| beats the random control, raw metrics only, one
per cluster at rho >= 0.90. Counts are after dedup.

| score | old -> new | left the score | joined the score |
|---|---|---|---|
| `combo_h1` | 17 -> 25 | `peg`, `sent_decay_30d`, `sent_net_5d`, `trade_size_trend` | `drawdown`, `du_tax_burden`, `eps_growth`, `fcf_yield`, `sent_decay_90d`, `shareholder_yield`, **`ebitda_growth`\*, `eps_growth_q`\*, `gross_margin`\*, `gross_margin_chg`\*, `op_margin_chg`\*, `roa`\*** |
| `combo_h20` | 22 -> 25 | `mom_12_1`, `turnover` | `eps_growth`, `short_ratio`, **`gross_margin`\*, `op_margin_chg`\*, `rev_growth_q`\*** |
| `combo_h60` | 16 -> 18 | `mom_12_1` | `du_op_margin`, `fcf_yield`, `gpoa` |

No sign flipped in any score. Theme weights (mean |t| of the theme's kept metrics) at
h20: safety 3.69 -> 4.23, valuation 3.12 -> 3.86, profitability 3.29 -> 3.79, growth
2.39 -> 3.49, efficiency 3.69 -> 3.50, attention 2.97 -> 2.55. At h60 the **growth theme
is gone** (`mom_12_1` was its only member).

**On the latest session (2026-10-09), same 3,527-name sibling frame.** Old combo was
reproduced from the old study first: Spearman 1.000000, max |diff| 0.0000 against the
stored rows, so the baseline is exact.

| score | scored old -> new (left / joined) | Spearman old vs new (n) | top-20 | top-50 | top-100 | median / p90 |Δ percentile rank| |
|---|---|---|---|---|---|---|
| `combo_h1` | 2,707 -> 2,819 (58 / 170) | 0.892 (2,649) | 7/20 | **21/50** | 46/100 | 7.4 / 23.1 |
| `combo_h20` | 3,054 -> 2,850 (208 / 4) | 0.898 (2,846) | 12/20 | **29/50** | 58/100 | 7.1 / 22.1 |
| `combo_h60` | 3,227 -> 3,044 (184 / 1) | 0.938 (3,043) | 10/20 | **24/50** | 61/100 | 6.7 / 16.9 |

The bottom-50 overlap is 34, 28 and 26 of 50. These are different scores, not a
refresh. Per-ticker old/new ranks: the three `combo_h*_old_vs_new.csv` files in the
notes folder.

### THE PROBLEM: SEVEN ENTRANTS WITH ONE EARNINGS SEASON OF HISTORY (\* above)

`ebitda_growth`, `eps_growth_q`, `gross_margin`, `gross_margin_chg`, `op_margin_chg`,
`roa`, `rev_growth_q` were added to `fund_metrics` after August. Each has **43-49
scored sessions, 2026-07-24 -> 2026-10-09, one earnings season** (13 new fundamentals
in all; none has an h=60 cell yet). Combo's rule is |t| >= 2 and IC above the random
control -- it has no minimum history, so they walked in:

- At **h20** the three that joined (`gross_margin` IC +0.0731 t +2.25, `op_margin_chg`
  +0.0577 / +3.35, `rev_growth_q` +0.0885 / +4.04) have **23-29 dates and n_eff = 2.0,
  the floor, with a 100% hit rate**. That t is two effective observations. It is not
  evidence of anything, and `rev_growth_q` (#18, with `ebitda_growth` at #17) sits in the
  regenerated "strongest metrics" table on the metrics page right now.
- At **h1** six joined, on 42-48 daily dates (n_eff 42-48, t +2.4 .. +4.4). The
  statistics are formally fine, but it is one 11-week window.

Cost, measured by rebuilding the live score without them (`sensitivity_out.txt`):
`combo_h20` live vs without-them Spearman 0.974, top-50 overlap 39/50, and the
short-history metrics cost **218 names** their h20 score (3,068 scored without, 2,850
with: a name needs >= 50% of the admitted metrics and they are sparse); `combo_h1`
0.984, top-50 43/50, 135 names. `combo_h60` contains none, so its change (above) is the
real re-fit. Even without them the rank order moves a lot (h20 vs old: Spearman 0.912,
top-50 29/50; h1: 0.907, 24/50), so the re-fit itself is large. I did NOT change the
admission rule -- that is a method decision (see NOT DONE).

### THE NON-USD FILERS IN THE SIZE TERCILES (asked: how many rows does it drop?)

**Almost none, because the fix reached one session.** Market cap was withheld for the
152 non-USD filers only in the 2026-10-09 re-score. The study's point-in-time size
frame is 620,343 (session, ticker) pairs over 233 sessions; **152 pairs dropped (the
10-09 session, which has no forward return at any horizon). The other 232 sessions
still hold 8,311 pairs for today's non-USD filers = 1.34% of 620,343**, in local
currency (sample session 2026-09-14: non-USD caps median 1.8e10, mean 4.2e12, max
2.6e14). They land in the top tercile: 89 of 150 `large`, 34 `mid`, 27 `small` (59%
vs 33% expected), ~10% of that tercile's names. The tercile edges barely move
($1.28B / $6.46B with them, $1.24B / $6.11B without). **`size = all` cells, and so
combo, are unaffected; the `large` / `mid` / `small` cells inherit this.** Per-size
cells at |t| >= 2, old -> new: large 103 -> 114, mid 88 -> 114, small 107 -> 143.
Fixing it needs a currency filter in `study.size_bucket_frame`.

### CHECKS

`validate --only claims` 2 ok, 0 warnings ("no past partition changed since
2026-10-11 23:47"); `status.audit_pages()` 297 pages clean.

### NOT DONE, IN ORDER OF WHAT I WOULD DO

1. **Decide the minimum-history rule for combo** (e.g. n_eff >= 5 and >= N scored
   sessions). Today's live `combo_h1`/`combo_h20` contain the seven entrants above.
   The rule is one line in `scores/combo.py:admitted`; `sensitivity_out.txt` shows what
   it would do. Until then treat `combo_h60` as the re-fit that stands on its evidence.
2. **Combo history is now mixed.** Only session 10-09 was rebuilt: it is on the new
   weights, the 232 sessions before it are on the old. Any per-ticker combo trend shows
   a step at 10-09 (median ~7 percentile points). `combo_refresh.py` rebuilds the whole
   series and re-measures combo's cells; it was not run (long, and it is a method
   choice). Tonight's 05:00 run scores 10-12 on the new weights.
3. **Wire `metrics_doc.build` into a nightly step** (it is called from nowhere, which is
   how `metrics.html` went two months stale). `orchestrator.py` is `edgar-ingest`'s file
   tonight, so not touched.
4. **Size terciles: filter non-USD out of `size_bucket_frame`** and re-measure the
   per-size cells only.
5. `scores/combo.py`'s module docstring still quotes hand-typed t-stats (combo_h1
   +1.96, combo_h20 +3.59, combo_h60 +4.75, `z_score` h60 -5.44 / h20 -3.37) that match no
   study table (now +3.00, +3.32, +3.25, -4.63). `claims` scans labels, not module
   docstrings. Not edited.
6. The TTM fix in the section below changes `derive` inputs for 35 names (fund_score
   moves for 1,584, by more than 5 points for 2) from session 10-12. History is untouched unless someone rebuilds it; if it is, `validate` will warn
   again and the study needs this re-run (use the parallel recipe above).

Local files: `data/_study_20261011_{p0,A,B,C}.log`, `data/_study_rebuild_20261012.log`,
`data/_nonusd_mktcap_by_session.csv`, the three `data/_study_compare_20261012_*.csv`,
and the notes folder `study-rerun-tables/` (combo old-vs-new ranks, theme weights,
population of the new metrics, sensitivity, all console output).

## State at 2026-10-12 — TWO TTM BUGS FIXED: 52/53-WEEK ROLLS, AND STUBS POSING AS YEARS

Both were found while verifying the EDGAR ingest (section below) and fixed the same
night. Read-path changes only, in `fundamentals.py`; no ingest changed, nothing
scored here. The next scoring run (05:00 Tue, session 10-12) is the first to use them.

### 1. THE ROLL-FORWARD REJECTED 52/53-WEEK CALENDARS

`_ttm`'s roll (FY + YTD_now - YTD_prior) had two fixed tolerances: the YTD stub must
end "within 20 days of qtrs x 91.31" after the fiscal year end, and last year's stub
"350-380 days" earlier. Both fail on real calendars:

- PEP (12/12/12/16 weeks): Q3 YTD is 36 weeks = 252 days vs 274 -- 22 off.
  eps_diluted_ttm stayed at the Q1 roll, **6.37**, while revenue had moved to Q3.
- KR (16/12/12/12): Q1 is 112 days vs 91 -- 21 off. And H1 ended 2026-08-15 vs
  2025-08-16, which month-end rounding turns into 07-31 vs 08-31: a 334-day gap.

Now `is_ytd_stub` / `is_one_year`: "rounds to the same number of quarters" -- the
rule `qtrs` itself is assigned by. Half a quarter still separates the cases the
guards exist for (HZO's and NVR's bare fiscal Q3/Q2 labelled qtrs=1 round to 3 and
2). `ttm_invariants.py` now imports these instead of carrying its own copy.

### 2. A SUB-QUARTER STUB COULD STAND IN FOR A YEAR

The bulk sets (and edgar.py, which matches them) store a duration under half a
quarter as qtrs=0. `facts_asof` put every qtrs=0 row in the instant frame, so a flow
concept got a LEVEL column, and `fund_metrics.derive` falls back to the level when
no `_ttm` exists: BRR's 28-day inception income 24,618,000 as its net income, BSX's
two one-day $2.0B accelerated buybacks as its buybacks, a single $131.5M SUI
dividend as a year's. Now only STOCK_CONCEPTS enter the instant frame, the rule
`history()` already used. The stub still counts toward `last_ddate`.

### MEASURED, as of 2026-10-09, whole universe (3,369 names with facts), same store

- **Rolls:** 100 TTM windows in 10 tickers moved LATER (median 209 days fresher,
  max 242), 0 earlier, 0 changed in place: AAP, ACI, AZO, DPZ, FLO, GEF, GEF.B, KR,
  LFCR, PEP. PEP eps_diluted_ttm 6.37 -> **7.95** (= FY25 6.00 + 6.10 - 4.15; by
  quarter 1.85 + 1.70 + 2.18 + 2.23 = 7.96); KR 1.54 -> **1.85**. `ttm_invariants
  --rollforward` re-derives every one from its reported legs: 112 of 112 hold.
- **Stubs:** 23 names took at least one flow figure from a stub (dividends 7, net
  income 5, buybacks 4, operating income 3, ...); all 24 flow level columns are
  gone. Every source row checked was a short period or a one-day event, none a year.
- **Inputs to `fund_metrics.derive`** change for 35 names. Side effect worth knowing:
  `derive` takes `shares_diluted` (a LEVEL) first, and that column only ever came
  from stubs -- so 10 names now use shares outstanding (BNC 1,410 -> 41.2M, PURR
  0 -> 200.6M, WOLF 189M pre-emergence -> 52.2M) and USDE has no share count.
- **Scores** (`mod.compute`, both rule sets on the same store, provider overlay
  off, nothing written): fund_score moved for 1,584 names but by more than 5 points
  for 2 (AAP +9.9, WOLF +5.3), none by more than 10. Outside the directly affected
  names it is percentile ripple: largest 0.63 points, median 0.02.
- **Checks:** `regression_pins.py` 33/33 -- five new pins (KR at 08-19, PEP and KR at
  10-09 via the new DATED_PINS list, BRR and BSX stubs must stay null), and all five
  FAIL on the old rules. `fundamentals.py --selftest` (new `_selftest_ytd_rules`,
  `_selftest_stub_not_level`; the latter fails on the old rule); `edgar.py
  --selftest`; validate `time` 4 ok + the known 221-row warning, `key` 3/3, `value`
  4 ok + the known `peg` range warning.

### OPEN

- `derive`'s share preference (`shares_diluted`, then `shares_out`, then
  `shares_basic`) reads level columns that no longer exist, so it is now simply
  `shares_out`. Whether it should read `shares_diluted_ttm` is a separate decision:
  it would change every per-share growth metric. USDE is the one name left blank.
- `shareholder_yield` treats a blank buyback as 0, so BSX, EROC, O and VZ lose a
  partial (event-only) buyback figure rather than gaining a true TTM one.
- Observed, not caused here: the score store went from 78,025,991 rows (10-11 20:56)
  to 78,025,653 (10-12 01:40) between two `validate --only key` runs.


## State at 2026-10-11 (evening) — NEW FILINGS NOW COME STRAIGHT FROM EDGAR, THE NIGHT AFTER THEY ARE FILED

Built the per-filing source designed in the audit below ("A FASTER SOURCE FOR NEW
FILINGS"), in a side session while `study.py` re-ran. Nothing was scored here: the
new rows reach `data/scores` at the next 05:00 run.

### WHAT WAS BUILT

- **`edgar.py`**. Discover: EDGAR's daily index (`master.YYYYMMDD.idx`, ~95 KB gzip),
  plus the latest-filings Atom feed for the day whose index is not out yet (SEC
  publishes it ~22:00 ET = 07:00 PKT, two hours after the run). Forms: the bulk
  ingest's own rule (10-K, 10-Q, 20-F, 40-F and /A, KT). CIKs: `ticker_map()`.
  Submissions API for `--tickers`. Fetch: index.json, FilingSummary.xml, the
  presentation linkbase and the `*_htm.xml` instance, gzip. Parse into the bulk
  SCHEMA through `fundamentals._typed`. Store `data/fundamentals_edgar/YYYYqQ.parquet`
  (period quarter), plus `_ledger.parquet` (one row per filing, status) and
  `_days.json` (which index days are settled). `--diff ADSH` re-checks any filing
  against the bulk set; `--selftest` runs offline.
- **`fundamentals.read`** drops any EDGAR row whose accession the bulk or
  companyfacts store holds, before the bulk/cf partition choice, so one filing is
  never read twice. `reporting_currency` and the read-cache fingerprint include the
  new store. `edgar.prune` deletes rows once the bulk set holds the accession.
- **Step `sec_edgar`** (27 steps): after `sec_gap`, before `fundamental`, order
  only. Daily by calendar, not session, so Friday's index is read on Sunday rather
  than Tuesday. Same convergence as `sec_gap`: retry failures once, fail the step
  above `SEC_GAP_MAX_FAIL`; a failed filing stays `error` in the ledger and is
  retried nightly; an index day that could not be read stays unsettled.
- Fair access: `config.SEC_UA`, 6 workers sharing one 5 req/s pacer (measured
  4.8 req/s on the peak day).
- Also: `validate.py`'s look-ahead check now reads the EDGAR partitions too;
  `dashboard.py` and `docs.py` list the store; README and SCORE_MODULES updated.

### MATCHING THE BULK SET -- EVERY RULE WAS FORCED BY A DIFF

| rule | case that forced it |
|---|---|
| only concepts on a FACE statement (FilingSummary "Statements" roles) | KO: `InterestExpense` sits in a segment note only; bulk lacks it |
| period end rounded to the nearest month end | KO quarter ended 07-03, bulk says 06-30 |
| uom = numerator (EPS is "USD"); -0.00 stored as 0 | AMFN EPS |
| a stub under half a quarter kept as qtrs 0, as bulk keeps it | a SPAC's 28-day inception period |
| a dei fact counts only on a date a statement fact uses | BBRY cover-date share count (bulk omits) vs SCI balance-sheet date (bulk keeps) |
| one key, two facts: the more precise wins, then the better-fitting period | MUFG 11,980,601,000 vs 11,981,000,000; LOGC tax at decimals -5 vs -6, capex 91 vs 123 days |
| linkbase may be embedded in the .xsd | MSFT FY2026 10-K has no `_pre.xml` |
| an Archives 403 `AccessDenied` is a missing file, not throttling | Labor Day's index: cost 70s of backoff and stayed "unsettled" |

### MEASURED

- **Agreement with the bulk set**, 2026q3 filings it already holds: **261 distinct
  filings** (171 10-Q, 19 10-K, 20 20-F, 4 40-F, 2 10-KT, 45 amendments; KO, AAPL,
  MSFT, JPM, WFC, NKE, NVDA, KR, MUFG, BHP, DEO plus three seeded random draws),
  **20,272 bulk rows, all matched on every field** (value, cik, filed, form, fy,
  fp, sic). **One residual: 2 extra rows** (FPS share counts for a 145-day post-IPO
  period, a column SEC's renderer suppresses; not reproduced -- guessing its
  threshold would be tuning). The rules were fixed on the first three sets; the
  fourth (106 filings, 8,070 rows) was drawn after all but the last rule and
  matched under both orderings of it.
- **Backfill since 2026-07-28 (75 days):** 54 index days, 5,102 universe filings
  listed, 4,965 already in bulk/companyfacts, 137 fetched: **60 stored** (4,244 rows,
  all filed 10-01..10-09: PEP, DAL, MU 10-K, NKE, CTAS, STZ, LEVI, JEF, LEN ...),
  5 empty, 30 without XBRL, 42 without statements (mostly Part III 10-K/As), **0
  failed**. **516 requests, 13.1 MB, 144 s.**
- **A typical day**, every universe filing of the day fetched into a temporary
  store with nothing skipped (the Q3/Q4 case, when no other store has them):
  median day 09-25 **21 filings, 82 requests, 3.1 MB, 32 s**; mid-season 08-12
  274 filings, 1,094 requests, 32.9 MB, 234 s; peak 08-06 **661 filings, 2,645
  requests, 113.4 MB, 552 s** (0.83 s per filing, rate-bound). A night with
  nothing new: 4 requests, 5 s. Step budget 1,800 s.
- **Read cost:** universe read from 2023q3, 18.5 s with the store vs 18.8 s
  without (cold first run 23.4 s), +2,494 rows. Not measurable.
- **Freshness, asof 2026-10-09, 3,369 universe names with facts:** newest period
  more than 120 days old **417 (12.4%) -> 387 (11.5%)**. 30 names crossed back
  under 120 days, 32 moved forward, 0 moved back. Newest FILING more than 120
  days old unchanged at 342 (10.2%); why those names have nothing since June was
  not investigated.
- **KO** unchanged, as designed (its 10-Q came with the bulk set on 10-10):
  eps_diluted_ttm 3.33.
- **Checks:** `regression_pins.py` 28/28; `validate.py` `time` 4 ok + the
  pre-existing warning (221 of 7,232,707 facts filed early; the 4,244 EDGAR rows
  contribute 0); `key` 3/3; `edgar.py --selftest` (rounding, parse, index/feed,
  read dedup + prune -- the dedup assertion fails when the filter is removed);
  `fundamentals.py --selftest`; `orchestrator.py --step sec_edgar` ran ok.

### LIMITS AND OPEN

- A filing accepted after ~20:00 ET misses that night's feed and arrives the next
  night with its index. The step runs once per calendar day, not on the 2-hourly
  repeats.
- Filers whose every statement fact carries a dimension (Presidio: Successor /
  Predecessor members) are empty here, exactly as in the bulk set.
- Found while verifying, NOT this module's, unchanged: (1) PEP's eps_diluted_ttm
  is stuck at the roll ending 2026-03-21: its 12/12/12/16-week calendar puts the
  36-week YTD stub at 252 days against the 274 +/- 20 `_ttm` requires, with bulk
  rows too. (2) Bulk keeps sub-half-quarter stubs as qtrs 0, and
  `fund_metrics.py:236` falls back to that level when no TTM exists, so a SPAC's
  28-day loss can stand as its net income; a read-time fix would cover all three
  stores. (3) `fundamentals.sector_map()` reads `part_path(q)` for every quarter
  in `stored_quarters(include_cf=True)` and would fail on a companyfacts-only
  quarter; it has no caller today.


## State at 2026-10-11 (later) — FULL ACCURACY AUDIT: ONE REAL DISPLAY BUG, ONE FRESHNESS GAP, TWO CHECKERS WRONG

Asked: "is all the stats, fundamentals and score accurate and updated? please audit and confirm".

### WHAT RAN

`validate.py` (all 12 groups, 36 checks), `regression_pins.py` (28 filing-verified
values), a freshness sweep (every score module, bars, zones, pages, provider cache),
the nightly SEC recompute and provider cross-checks read field by field, a fresh
Yahoo comparison on 80 random scored names, and direct SEC lookups for KO, NOG, XOM.

### UPDATED? YES, as of the 2026-10-09 close

All five score modules, bars, zones and every page are on 10-09 (the last session);
Finnhub refreshed 10-10 06:06. After the fixes below, 10-09 was re-scored through the
orchestrator (`--step fundamental ... docs`): 10 ran, 0 failed, validate CLEAN.

### FOUND AND FIXED

1. **SEC's companyfacts API lags filings by months for some companies, and only the
   quarterly bulk set fills the gap.** KO's Q2 10-Q (filed 07-29) was not in the store
   until the 2026q3 bulk set arrived 10-10; on 10-11 SEC's companyfacts for KO STILL
   ends at the 04-30 10-Q (fetched directly). The weekly refresh targeted KO five times
   (replayed: stale from 09-04, newest period 03-31) and got the same old answer.
   309 scored names (11%) got their newest period only from the 10-10 bulk set, a
   median 72 days after filing (p90 74, max 101). XOM's case is different: its Q2 10-Q
   was filed under a NEW CIK (2115436); the store already follows it.
2. **The 10-10 run scored before it ingested.** `fundamental` ran 06:08, the bulk set
   landed 06:10, so those 309 names waited for the next session (3 days over a weekend).
   `sec_facts` and `sec_gap` now run BEFORE `fundamental` (order only, no new
   dependency). Re-scoring 10-09: KO last_filed 04-30 -> 07-29, fund_score 54.1 -> 55.5,
   ROIC 20.2% -> 20.8%; 27 names moved > 5 points, 7 > 10; names whose latest filing is
   > 120 days old fell from 15% to 10%. NOTE: the first fundamental run after a fact-
   store change is cold -- it took 3,706s instead of ~100s.
3. **Market cap for non-USD filers was in their own currency and printed with "$".**
   Finnhub's figure: TM 3.4e13 = JPY 34T (~$230B), TSM 6.6e13 = TWD 66T, FMX 6.7e11 =
   MXN 665B (~$36B) -- the "$665B FMX" seen earlier. Now WITHHELD (a dash) for the 152
   non-USD filers, after the overlay, in `scores/fundamental.py`. Display-only: no score
   reads mktcap except through metrics already withheld for these filers; dip only
   passes it through; `study.py` size terciles had them misbucketed. Verified on the
   Explore page: TM and FMX show a dash, AAPL $4,912.98B, KO $378.84B.
4. **The KO regression pin was the stale value** (3.18 = TTM to Q1). Re-pinned at 3.33
   from the filings: FY25 3.04 + H1-26 1.94 - H1-25 1.65, = 0.86 + 0.53 + 0.91 + 1.03.
   Pins 28/28.
5. **The currency check failed every night on 256 correct values.** Since the provider
   overlay (2026-08-12) a non-USD filer's P/E and P/B are Finnhub's, and Finnhub's P/E is
   currency-consistent: close / pe / local EPS gives the real exchange rate on 8 names
   (EUR 1.10-1.24, CAD 0.70-0.73, GBP 1.36, DKK 0.155). The check now excuses values
   equal to the provider's and still fails on anything of our own. P/B was checked on
   one name only (ASML: not raw local currency, but its implied rate 1.45 is off our
   book value by ~25%) -- treat P/B for non-USD filers as unverified.
   SIDE EFFECT OF THE OVERLAY, NOT A BUG: non-USD filers now get a value pillar from
   those provider ratios, which the "3 of 4 pillars" design note no longer describes.

### CHECKED AND FOUND TO BE THE REFERENCE'S FAULT, NOT OURS

- **SEC recompute "30% mismatch"** (176 of 591): cash-flow TTMs are 77 of them, and for
  93% of cfo and 85% of capex mismatches the SEC-side value IS the latest full-year
  figure -- the naive checker cannot roll year-to-date cash flows (documented; the
  `rollforward` check re-derives them from the legs: 246/246). Of the income-statement
  and balance-sheet mismatches, XOM/EXLS/CLBK/SHEN are SEC's copy ending at Q1 while ours
  runs to Q2 (XOM: Q2-26 net income $14.5B vs $7.1B a year earlier, as filed), SNA is a
  definition (total revenues incl. financial services vs net sales), HZO is a filing-
  verified pin the checker disagrees with, and the rest are 1-5% EPS rounding.
- **Yahoo agreement 80.7%** (951 fields, 80 random names). Price 100%, mktcap 91%, P/E
  88%, revenue 87%, EPS 86%; ROA 49%, EBITDA 64%, FCF 65%, op margin 72%. Disagreements
  scatter both ways (no units error). Checked one: NOG op margin ours -24% vs Yahoo +57%
  -- NOG booked $856M of ceiling-test impairments in the TTM (SEC), so ours is the GAAP
  figure and Yahoo's excludes them. File: `data/_provider_compare_audit_20261011.csv`.

### A FASTER SOURCE FOR NEW FILINGS -- RECOMMENDATION (measured, not yet built)

**Compute is not the bottleneck; source latency is.** A full fact read for the
tradeable universe measured 36 s wall / 38 s CPU on 2026-10-11 (prior-year read 37 s),
and the fundamental step is 100-250 s on every normal day -- including the days after a
weekly `sec_gap` write, so a store write does not make the next run cold. Two runs were
far slower: 2,078 s on 09-30 and 3,706 s on the 10-11 re-score. EXPLAINED 10-12: an
off-cycle re-score finds the Finnhub cache older than its 20 h TTL and re-fetches every
name at ~60/min inside the step (see the 10-12 morning section).

**Use EDGAR itself, per filing.** Verified on KO: SEC's submissions API
(`data.sec.gov/submissions/CIK0000021344.json`) lists the 07-29 10-Q
(0001628280-26-050503); the filing folder's `index.json` names the extracted XBRL
instance (`ko-20260703_htm.xml`, 2.9 MB); it carries EarningsPerShareDiluted 1.03 /
0.88 / 1.94 / 1.65 -- exactly what the bulk set had. 3 requests, 7.6 s -- while
companyfacts still does not serve that filing at all.

    discover  EDGAR daily form index (one request a day lists every 10-Q/10-K/20-F/40-F
              accepted), filtered to universe CIKs; submissions API as a per-name fallback
    fetch     each new filing's `*_htm.xml` instance, gzip (XML compresses ~10x);
              ~150-400 universe filings a day in earnings season = 1-2 min at 6 req/s
    parse     non-dimensional contexts only, WANTED tags only, period start/end -> ddate
              and qtrs, written in the bulk SCHEMA through `fundamentals._typed`
    dedupe    by accession number (`adsh`): when the quarterly bulk set arrives it
              supersedes the same filing, so the bulk set stays the reconciliation
              backstop and companyfacts stays the history/backfill source
    when      nightly, before `fundamental` (the SEC steps already moved there)

Effect: a 10-Q filed after Monday's close is scored at Tuesday 05:00, instead of up to
~100 days later. Fallback if EDGAR parsing proves brittle: Finnhub
`/stock/financials-reported` (as-reported XBRL, key already in .env, 60 req/min) --
third-party, so it would need the same cross-checks. SEC's monthly "Financial Statement
and Notes" sets were considered and rejected: a month of lag and multi-GB downloads.

### STILL OPEN

- **Q3 10-Qs arrive mid-Oct to mid-Nov and, for names whose companyfacts lags, will not
  reach the store until the 2026q4 bulk set (early January).** Fix designed above;
  being built in a parallel session (2026-10-11).
- **`study.py` is stale**: 3 past months rewritten since it ran 2026-08-13; combo's
  weights and every MEASURED label come from it. Re-run (~4-5 h) started in a parallel
  session 2026-10-11.
- Warnings left as they were: peg documented range 0.5-3 vs actual median 0.49; 221 of
  7.2M facts filed before their period ended (0.003%); 2 unexplained coverage gaps
  (GMTL, XPRO); 14 near-duplicate sentiment metric pairs.
- `data/_tech_levels_20261009.csv` predates the mktcap fix but holds no non-USD names,
  so it is unaffected.


## State at 2026-10-11 — "LOWER LOW, NOW CLIMBING", MEASURED: NO EDGE, AND WORSE IN TECH

Asked for tech names "at or nearing a level, or at a lower low but climbing". The
list was shown first (below), marked unmeasured; then `lowclimb_study.py` measured
the pattern. ~4 min to build the panel (`data/_lowclimb_panel.parquet`, 777,024 rows),
seconds to analyse.

### THE PATTERN (fixed before any result; no tuning)

    recent low = lowest LOW of the last 20 sessions; prior low = lowest LOW of 21..120 back
    lower low  : recent low < prior low * 0.99
    climbing   : low >= 3 sessions old, close >= low * 1.03, close > close 5 sessions ago
    still near : close <= low * 1.15

On a median date it flags 74 names, 4.4% of eligible stocks. The study's flags
reproduce the 10-09 list exactly (15 of 15 tech-labelled names, 0 extra).

### RESULTS -- 245 dates 2017-01..2026-10, windows never overlap, 18 tests declared up front

    test (excess = picks minus the same-day pool)       h   dates  picks/date  excess    t
    PRIMARY  lower low, now climbing, all stocks        20   121     109     -0.26pp  -0.89
             same                                       10   241     120     -0.09pp  -0.59
             same                                       40    61     106     -0.73pp  -1.58
    tech     core-tech picks vs core-tech pool          10   166      17     -0.23pp  -0.76
             same                                       20    83      16     -0.93pp  -1.62
             same                                       40    41      17     -3.65pp  -2.74
    control  lower low, any                             20   122     513     -0.44pp  -1.80
             lower low, NOT climbing                    20   122     308     -0.41pp  -1.62
             lower low, any                             40    61     533     -0.79pp  -1.96
             lower low, NOT climbing                    40    61     311     -1.17pp  -2.23
    strength vs own quartile of % of 1y high, Q1..Q4    40   59/57/45/20     -0.85 / -0.55 / -0.98 / -1.21pp
                                                                             t -1.00 / -1.19 / -1.65 / -0.99
    support  (49 coupling dates) vs own band, AT/NEAR   40    47      84     -1.06pp  -1.68
             APPROACHING                                40    42      19     +0.78pp  +0.47
             no support                                 40    44      19     -1.13pp  -1.30
             no support & <= 64% of 1y high             40    40      14     -0.12pp  -0.09

Hit rate at the primary horizon: 51.6% of picks up vs 51.6% of the pool. Halves:
-0.52pp (2017-21) and -0.01pp (2021-26). Tails at 40 bars: lost >20% 11.1% of picks
vs 10.8% of the pool; gained >20% 8.6% vs 11.7%. Same disasters, fewer jackpots.

**The tech result is the only one that stands out, and it points the wrong way.**
Robustness on the same 41 dates: median-vs-median -2.18pp (t=-1.99); returns clipped
to -60%..+100% -3.14pp (t=-2.42); without the 3 most extreme dates -3.22pp (t=-2.83);
picks lagged on 66% of dates. It is still one of 18 tests and does not clear a
multiple-comparisons bar (|t| ~3.0), so: a consistent warning, not proof.

### VERDICT, PLAIN

No edge. Across all stocks, buying a lower low that has started to climb did the same
as buying an average stock over 2-4 weeks, and slightly worse over 2 months. In tech
it did worse than the average tech stock. Waiting for the climb is LESS BAD than buying
a lower low that is still falling (-0.73 vs -1.17pp at 40 bars) but both trail the
average stock. The pattern sits mostly in beaten-down names (77% in the bottom two
quartiles of % of 1-year high) and lagged its own peers in every quartile. Inside the
measured avoid profile (no support, <= 64% of high) a climb changed nothing (t=-0.09).

### A METHOD FLAW FOUND, AFFECTING rule_accuracy.py TOO

The "luck p" (random same-size draws from each date's pool) assumes the picks are
independent. They are not -- beaten-down names move together -- so with hundreds of
picks a day it read p = 0.000 for "lower low, any", whose t across dates is only -1.80.
The t across dates is the test; both scripts now say so (`lowclimb_study.py` prints it,
`rule_accuracy.py` in its docstring; the good-level luck p of 0.26 was non-significant
anyway, so that verdict stands).
A proper fix is a permutation that keeps each date's cross-sectional structure (e.g.
shuffle by sector blocks); not done.

### BIASES

Survivorship: 1.0% of rows (1.1% of pattern rows) are names not active today, and a
name that delists inside a window has no forward return. Both flatter weak stocks --
exactly what this pattern selects -- so the true result is likely WORSE than measured.
First run's avoid-profile stratum came back empty: the coupling panel records pct_hi
only when a level exists. Fixed by taking pct_hi from the bars (the two agree to 0.0000
over 111,188 rows where both exist).

### THE TECH LIST ITSELF (2026-10-09 close, `data/_tech_levels_20261009.csv`)

"Tech" = sector map Technology + Semiconductors, narrowed to core SIC codes
(3570-3579, 3661-3679, 3825, 7370-7379): 381 tradeable names, of 462 carrying the label.
(The chat answer said 456 -- wrong, never computed.) The SIC "Technology"
label also covers electrical equipment, instruments and defence electronics (OTIS,
Woodward, Danaher, Garmin, EyePoint -- a drug maker on SIC 3826); the 54 of those 81 that
made the list stay in the file with `core_tech=False`. At or near a level: 235 of the
381 (62%), so the filter alone selects little. Shown in chat: the 40 also within 20% of the 1-year high
with fund_score >= 55, and 11 core lower-low-climbing names (AVGO, TBLA, ADTN, COUR AT a
level; FSLR, ALKT NEAR; APP, TTWO with no level within 6%; TRIP, FLUT, KDK matching the
measured avoid profile). Only 1 of the 40 (IMMR) has hype <= 45, which is why tech barely
appears on the Candidates page. The list was built by a scratch script; the pattern
logic is now in `lowclimb_study.py`.


## State at 2026-10-09 — THE GOOD-LEVEL LIST CAN NOW BE SEEN, AND FILTERED BY SECTOR

Asked: "how can I visually see the line of level where these stocks are and the
strong fundamental/sentiment you are talking about, and how can I filter by sector?"

### WHAT WAS BUILT

**`candidates_page.py` -> `reports/zones/candidates.html`, nav "Candidates".** One row
per name on the good-level list (the rule in `good_level.py`, unchanged). Each row has
a 150x40 price chart (180 sessions) with the support level as a DASHED line and the
"at the level" strip (level to level x 1.025) shaded; beside it price, % above level,
band, touches, share of past tests after which the stock later fell >10%, % of 1-year
high, fundamental composite, interest cover, Piotroski, ROIC, P/E, hype, news
sentiment percentile, news count, market cap and $/day. Click a row for an 820x240
chart with price labels and the same facts as a sentence, plus a link to the profile
page. Filters: sector dropdown (with counts), AT / NEAR chips, "liquid only (>= $10M/day)",
ticker search, clear. Rows are ordered by distance to the level, never by a score.
The top of the page says the list has no measured edge (+0.14pp at 20 bars, t=+0.3).

**Sector filter on the Zones page too** (`zones_page.py`): a sector column and
dropdown. Counts there are LEVELS, not stocks. The page script now addresses row
fields through an `IX` name map instead of literal positions (r[1], r[4]...), because
inserting the sector column would otherwise have meant hand-editing ten indexes.
`candidates_page` still uses literal positions, so its `--selftest` asserts they match
the column list.

**New step `candidates`** (26 steps now), after `combo`, depends on
`zones, fundamental, hype, sentiment` (not on combo: a failed combo must not blank it).
It cannot live in the `zones` step, which runs before fundamentals are scored. If the
session has no stored fund_score/hype_score it REFUSES to build; if the rule selects
nobody it logs that and leaves the old page (`NoCandidates`), rather than counting an
error. ~6s.

2026-10-08 list: **85 names** (47 AT a level, 38 NEAR), recomputed independently from
the raw zones and scores stores -- same 85. Against 10-07's 93: 78 kept, 7 entered,
15 left.

### BUGS FOUND WHILE BUILDING IT

1. **Price from the wrong day.** `good_level.current(asof)` takes `last_close` and
   `dollar_vol_20` from the panel-stats cache, which holds the LATEST bar. Asked for
   10-07 on 10-09 it returned the 10-08 close, and a stock that had since dipped below
   its level looked as if the level sat above it. The page now takes price and 20-day
   dollar volume from the bars as of `asof`, and refuses to publish unless
   `price/level - 1` equals the distance the zones file recorded (a check that cannot
   hold with a price from another day). `good_level.current()` itself is unchanged: it
   is right for the newest session, which is all its `__main__` uses. Running
   `good_level.py` while the newest session is not yet scored writes an empty CSV.
2. **`nan` in the Fundamental page's sector column** (LEN.B, a name with no sector),
   since at least 07-24: `str(v or "?")` -- NaN is truthy, so it printed "nan" in the
   cell and in `data-sector`. Fixed in `fund_screen.py` with a `lab()` helper (the same
   bug as the zones page's `txt()`; this pattern keeps recurring). The 19
   archived copies were repaired by exact-string replacement of the one bad fragment.
   `status.audit_pages()` now reports **0 problems over 293 pages**. It had also been
   flagging the Explore page's "None" strategy button every night: that is a label
   (the no-strategy option), now exempted by exact match.
3. **AT badge contrast 4.48:1** on a hovered row (needs 4.5): text mixed 20% toward the
   ink colour. Both themes checked after: text >= 5.2:1, level line 5.1:1 light / 6.2:1
   dark. No page-level horizontal scroll at 375px on Candidates or Zones.

### CORRECTIONS

- The 10-08 section below said foreign filers "(FMX, ASND, NVS, DLO)" are scored on
  fewer metrics. **Only FMX and ASND report in a non-USD currency**; NVS, DLO and TIGO
  report USD and are scored normally. Across the universe 160 of 3,368 names are
  non-USD reporters (`reports_usd` = 0). The Candidates page marks them with a dagger.
- The 18-name list `data/_shortlist_20261007.csv`, made for the plain-language "best
  stocks right now" answer, was a hand cut of the 93 whose criteria were not recorded.
  The reproducible version is the page's "liquid only" chip (66 of 93 on 10-07). It is
  no better evidenced than the full list.

### LIMITS THAT MATTER FOR THE SECTOR FILTER

Sector is the SEC SIC map (`macro.load_sector_map`): 21 labels, and **"Other" is the
largest, 3,888 of 10,398 mapped names (37%)**. 13 of the 93 on 10-07 were "Other", so a
sector filter is coarse. The list leans Financials and Utilities because the rule picks
quiet, near-high names, not because the filter does anything.

### OPEN

- The page is a viewer for a list with no demonstrated return edge. A "near high +
  fundamentals + low hype" combination beyond this rule is still unbuilt and unmeasured.
- Still open from before: provider guard is all-or-nothing on two names; AZO checker
  too strict (QUARTER_MIN=80 vs a 75-day gap); `profiles` chronically over its 900s
  budget (last measured ~1,215s); backup last taken 2026-08-30; survivorship in all
  history (0.9% of rows from names that later vanished).
- Candidates for a later session: a "since last night" marker (entered / left), and a
  sector-relative fundamental rank in place of the raw composite.


## State at 2026-10-08 — THE "GOOD LEVEL" RULE, MEASURED: FEWER DISASTERS, NO EXTRA RETURN

Asked: show every stock at a good level to buy, and how accurate that is.
`good_level.py` builds the list; `rule_accuracy.py` measures it (~3 min, idle
machine). Neither is wired into the pipeline.

### THE RULE (round numbers fixed before scoring; no tuning)

  nearest support within 6% above price (AT or NEAR)
  within 20% of the 1-year high        pct_hi     >= 0.80   (strongest separator, t=+14.8)
  sound fundamentals                   fund_score >= 55     (t=+5.2)
  little attention                     hype_score <= 45     (hype ran the wrong way, t=-4.9)

A name missing any input is EXCLUDED, never filled in. On the 2026-10-06 close:

    3,222 trusted-price names -> 2,766 with a level -> 2,239 AT/NEAR
          -> 1,068 near the high -> 370 with fund_score >= 55 -> **96**

54 AT a level, 42 NEAR. NONE are flagged by the bounce screen (a different
pattern). 29 Financials, 12 Utilities, 12 Industrials -- the rule selects quiet,
high-quality, near-high names, so it leans defensive. Median market cap $5.4B,
median $31M/day; 30 are under $2B and 27 trade under $10M/day. List:
`data/_good_level_20261006.csv`. FMX shows a $665B market cap, which is wrong (an
ADR share-count error); FMX and ASND (non-USD reporters) are scored on fewer
metrics and deserve extra suspicion. (NVS and DLO were listed here too, wrongly:
they report USD. Corrected 2026-10-09.)

### ACCURACY, THREE WAYS

**A. History** -- 49 dates, 2018-04-27..2026-07-08, 5,084 picks (3.9% of eligible):

    horizon  dates  pick up  pool up  excess vs pool   t      luck p
    20 bars    49    55.2%    52.3%     +0.14pp      +0.30    0.26
    40 bars    49    54.2%    50.8%     +0.21pp      +0.35    0.27
    120 bars   16    54.5%    51.7%     -1.62pp      -0.80    0.91
    250 bars    8    54.1%    50.1%     -5.46pp      -0.89    0.96

40 bars, first half -0.13pp (t=-0.16), second half +0.56pp (t=+0.70): no
consistent edge. The one measurable difference is the TAILS: lost more than 20%
in 40 bars 3.9% of picks vs 9.9% of the pool, gained more than 20% 6.6% vs 11.7%.
Fewer disasters AND fewer jackpots.

**B. Luck** -- random same-size draws from each date's own pool did as well as the
rule 26-27% of the time at 20/40 bars (an edge needs p < 0.05). At 120/250 bars
the picks did WORSE than 91-96% of random draws, on only 16 and 8 independent
dates, so not significant either way.

**C. Live, on days after the study was finished (2026-08-30)**, scored with the
rule exactly as it stands. Start days 08-31..09-22, windows overlap heavily so
this is closer to one observation than sixteen:

    10 bars (16 starts): pick -2.7%  pool -2.3%  excess -0.42pp (+/-0.4)  up 25% vs 30%
    20 bars  (6 starts): pick -5.0%  pool -4.6%  excess -0.39pp (+/-0.6)  up 20% vs 26%

The window was a real small-cap selloff, not a data fault: IWM -4.7%, pool mean
-4.7% (median -5.2%), SPY -0.2%, QQQ +2.8%. 0 of 112 picks fell more than 40%
against 14 pool names.

**VERDICT: no demonstrated return edge. It is a lower-variance basket.**

### CORRECTIONS TO EARLIER STATEMENTS

1. **The "63% bounced vs 45%" figure (658 vs 565 cases) must not be quoted as the
   rule's accuracy.** It described the profile among the top and bottom OUTCOME
   deciles -- over-represented among extreme winners relative to extreme losers --
   and says nothing about the 80% of names in between. The rule's actual hit rate
   is 54-55% against 51-52% for any stock.
2. **The coupling panel is 49 usable dates, 2018-04-27..2026-07-08, not "50
   sessions 2016-2026".** The first dates lack the 400 bars of price history the
   eligibility test requires.
3. **SURVIVORSHIP -- every historical number in this project is flattered.** Only
   **0.9%** of panel rows belong to names that later disappeared, flat at 0.8-1.0%
   in every year. Real markets lose several percent of listed companies a year, so
   delisted names are badly under-represented (the universe knows only 128
   removed/dead names). It biases returns UP, which makes "no edge" a generous
   reading. The earlier survivorship guard recomputed eligibility from bars and
   could not fix this: the bars of delisted names are largely not in the store.

### DATA ACCURACY, SAME DATE

Regression pins 28/28 (hand-checked against SEC filings). Validate 10-07: audit 0
high, TTM windows 390/398 (the 8 are AZO, a false alarm), roll-forward 263/263,
screen invariants pass, provider agreement 77% (553/719) -- a definitional spread
between vendors, not an error rate.


## State at 2026-10-07 (later) — THE 05:00 TASK NOW RESUMES ITSELF, AND A RETRY CAP KEEPS THAT SAFE

Done at the user's request, after `keepawake.py` (above) could not stop a forced
restart: the task now repeats, so an interrupted run resumes within two hours
instead of waiting a full day.

### WHAT CHANGED IN WINDOWS

Task `Screener-Orchestrator`, daily 05:00 trigger: **repeat every 2h for 12h**
(05:00, 07:00 ... 15:00). Verified in the definition Windows holds, not just the
absence of an error. `StopAtDurationEnd` is false -- a run still working when the
window closes is never killed.

UNCHANGED, and checked afterwards: priority 7 (below normal, so the user's own
programs win), MultipleInstances=IgnoreNew (a repeat that fires while a run is
working is ignored), 12h execution limit, StartWhenAvailable, WakeToRun=False,
action `pythonw.exe orchestrator.py --wait-for-lock 300` (no console window). The
second trigger is a stale ONE-OFF from 2026-08-13 23:30; left alone, harmless.

Why 05:00-17:00: the machine is on Pakistan time (UTC+5), so the US close lands
around 01:00-02:00 local. `asof` therefore cannot change inside the window, and no
repeat can start a new session's pipeline mid-afternoon.

BACKUP of the previous definition: `data/_task_backup_Screener-Orchestrator_20261007.xml`
(gitignored: it names the user). ROLLBACK:
`Register-ScheduledTask -TaskName 'Screener-Orchestrator' -Force -Xml (Get-Content <that file> -Raw)`.

NOTE: this task is NOT reproducible from the repo. `setup_schedule.ps1` registers
the older `PatternScan-DailyRun`, not this one. This section and the XML backup
are the only record of how the live task is configured.

### THE COST, MEASURED

An idle pass (everything already done for the session) was timed three times as
`orchestrator.py --dry-run`, which does the same imports and due-checks:

    wall 3.1-3.2s   CPU 2.9-3.0s   peak RAM 106 MB   (one core, briefly)

Real idle days in the log took 2-10s start to finish. Five extra passes a day is
therefore ~15s of one core. The job table is 37 KB / 1,876 rows and gains ~25 rows
a pass; it is read and rewritten on every record, so that stays negligible for
years. The NIGHTLY RUN is not new cost: ~2h at about one core, below-normal
priority. NOT measured: the run's peak memory (free RAM read 3-5 GB of 15.8 when
sampled mid-run).

Two honest costs: while a run is WORKING the machine will not sleep (~2h a night),
which costs battery if unplugged; and a step killed mid-way restarts from its
beginning, so an interruption loses that one step's partial progress.

### THE TRAP THE REPEAT WOULD HAVE SPRUNG, AND THE CAP

`is_due` looks only at the last SUCCESS, so a step that fails stays due on EVERY
pass. With six passes a day a persistent failure would be retried six times, and
a step that fails only after a long run would grind a laptop all day. There was
no cap anywhere.

`config.ORCH_MAX_ERRORS_PER_DAY = 2`. The first retry rescues a transient fault
(09-12: "finnhub returned nothing for AAPL" at 0.0s was a network blip); two
failures mean it is not transient. Counted by calendar date, so a weekend failure
gets fresh attempts each day. `--force` / `--step` bypass it.

THE DETAIL THAT MATTERS: a capped step is marked BLOCKED, not skipped. Dependents
treat "skipped" as satisfied (`STATUS_SKIPPED` is in the accepted set), so a
skipped provider would have let `fundamental` run on provider data that was never
refreshed -- the exact thing the block exists to prevent. It also counts as
failed, so the exit code stays non-zero rather than looking healthy.

Proved with fake steps on a temp job table: a step that always fails got exactly
two automatic attempts then `BLOCKED -- failed 2x today (cap 2)`; its dependent
NEVER ran on its stale output; an independent step was undisturbed; the pass still
exited 1; a manual `--step` run bypassed the cap; and five failures dated
yesterday counted as zero today.

### LIMITS

Logon type is Interactive, so the task only runs while the user is logged on. A
Windows Update restart that waits at the login screen resumes nothing until login.


## State at 2026-10-07 — THE PIPELINE'S REAL ENEMY IS THE LAPTOP, AND THE SLEEP TIMER IS NOT OURS

Month review, 2026-09-04 .. 2026-10-06 (33 runs). **The data is complete**: 25
sessions, zero missing modules, and both days that failed were filled in
afterwards. Two failed steps in the month, each recovered:

  09-04  provider guard dropped payout_ratio / dividend_yield on 2 large caps and
         raised -> fundamental, dip, combo, validate, explore, snapshots BLOCKED
  09-12  "finnhub returned nothing for AAPL" (0.0s) -> the same downstream block

The 09-04 guard is still all-or-nothing (2 names out of ~3,500 cost a day of
scoring) -- the same shape as the `sec_gap` bug. NOT FIXED; waiting on a decision.

### THE MACHINE, MEASURED

The AC sleep timer was 5h on 08-24 and **1h on 10-07**. Neither of us set it, and
the user cannot hold it either (managed laptop). Since 09-04: **26 full sleeps,
3 shutdowns, 4 unclean reboots.** Six runs took 7-12h of wall clock for ~2h of
work (09-12 8.0h, 09-15 11.1h, 09-17 11.8h -- twelve minutes from the 12h kill --
09-19 11.6h, 09-26 7.2h, 10-06 9.7h), and on 10-06 the `universe` step logged
28,495s for a step that takes 6s. That is consistent with sleep. It was not
proven per run, because nothing recorded the machine being asleep.

The scheduled task has WakeToRun=False, so a run asleep at 05:00 simply starts
when the machine next wakes (10-05 06:26, 10-06 06:38).

**10-07: Windows restarted the machine twice, at 05:38 (SystemSettingsAdminFlows)
and 05:41 (TrustedInstaller), mid-run.** Task Scheduler recorded 0x40010004,
process terminated. Everything scored and all five main pages were already
current; only profiles, retention, dashboard and docs were cut off. The last two
were re-run by hand. The lock was left naming dead PID 27548.

### THE DECISION: STOP ASKING FOR A SETTING, PROTECT THE JOB

`powercfg /change standby-timeout-ac 0` was asked for repeatedly and cannot be
made to hold. So `keepawake.py` now wraps the run in `orchestrator.main`, AFTER
the lock is taken (a run that skipped never asks Windows to stay awake):

  * a Windows power REQUEST -- system-required (no idle sleep) and
    execution-required (no background throttling if Modern Standby happens
    anyway; this machine does, events 506/507). It is the mechanism a video
    player uses, and it is released when the run ends or the process dies. No
    system setting is touched.
  * a SLEEP-GAP DETECTOR: a 15s heartbeat. Two ticks more than 90s apart mean
    the whole process was frozen. The gap is logged and appended to
    **data/_sleep_gaps.csv** with a `request_held` column.

That file is how to judge it. Absent or empty after a few nights means the
request works. Rows with request_held=True mean Windows slept the machine despite
it (a policy override), and the rows give the exact windows.

VERIFIED: the gap arithmetic, including a 1h clock jump through the real
heartbeat thread; acquire and release; and an end-to-end run through the real
entry point (broke the stale lock, held system+execution, ran, released, 0 gaps).
NOT VERIFIED: that Windows honours the request under this machine's policy --
`powercfg /requests` needs administrator rights. The first nights of
_sleep_gaps.csv are the proof.

WHAT IT CANNOT STOP: a forced restart (Windows Update), a lid close with a sleep
action, or the user choosing Sleep. Those rely on resumability, which exists --
every step skips if it already ran for the session -- but NOTHING RE-TRIGGERS
after a mid-run kill until the next 05:00. Proposed, and needing the user's yes
because it edits the scheduled task: a repetition trigger every 2-3h. It is
cheap, because a finished day exits in ~3s ("3 ran, 22 skipped").

### THE LOCK AND REUSED PROCESS IDS

`_pid_alive` asked only "does a process with this ID exist?". After a restart
Windows reuses IDs, so a killed run's lock can name a PID that some unrelated
program now holds: the lock looks held, and the next run waits out its budget and
gives up. `_pid_alive` now also compares the process's creation time with the
lock's start time -- a process created AFTER the lock was written cannot be the
one that wrote it.

Tested on six cases: a genuine holder True; the same PID with a lock 2h older
False; a nonexistent PID False; the real stale lock False; no PID None; an
old-format lock with no start time True (cannot compare, so trust pid_exists).
And end to end on a temp lock: a reused-PID lock is broken and the run proceeds,
while a genuinely held lock still blocks a second run.

### AZO: 8 VALIDATION "PROBLEMS" ON TWO NIGHTS, AND THEY ARE A FALSE ALARM

`validate` reported 8 problems on 10-02 (ttm 403/411) and 10-07 (390/398) after a
month of clean runs. Reproduced from the date-seeded sample: **all 8 are
AutoZone**, one per line item (revenue, cogs, gross_profit, net_income, opinc,
pretax, sga, tax). Its four period ends are 2025-08-30, 2025-11-22, 2026-02-14
and **2026-04-30**, so the last gap is 75 days against a `QUARTER_MIN` of 80.

The data is right: Q1+Q2+Q3 = 4,628,630 + 4,274,098 + 4,840,950 = 13,743,678
against the filed nine-month 13,743,677 (thousands). The checker is too strict
for this filer. It only shows when AZO is drawn into the 60-name sample (plus the
3 watchlist names), which is why it looks intermittent. The 10-02 sample could not
be reproduced (the tradeable pool has changed), so that night being AZO is likely
-- 8 is exactly its line-item count -- but not confirmed. NOT FIXED.

### STILL OPEN

  * the provider guard: make it proportionate (see above)
  * the `profiles` step runs 1,100-2,700s against a 900s budget and has grown from
    75 to 92 pages -- chronic, and sleep makes it worse
  * the last backup is from 2026-08-30
  * the screen the coupling study pointed at -- near the 1-year high, good
    fundamentals, low hype -- is still unbuilt, and unmeasured as a combination


## State at 2026-08-30 — THE COUPLING TEST, DONE PROPERLY, AND WHAT IT OVERTURNS

The first coupling test reported "no edge" for good-fundamentals-at-a-good-level
(-0.04pp, t=-0.19). **That test was badly specified and the pushback was right
on two of three counts.** Rebuilt with four horizons, sector treatment and the
hype/sentiment modules: `couple_panel.py` (131,837 obs, 4,286 tickers, 50
49 usable sessions 2018-04-27..2026-07-08 [corrected 2026-10-08; this line
said "2016-2026"], 22 sectors, integrity OK) and `couple_study.py`.

### THE CONTRADICTION THAT RUNS THROUGH EVERYTHING

Fundamental IC rises with horizon, exactly as "fundamentals pay over time"
predicts -- and the top-minus-bottom quintile spread goes MORE NEGATIVE at the
same time:

    roic   h=20  IC +0.032   spread  -0.54pp
           h=250 IC +0.051   spread  -9.66pp

**The rank correlation is positive while the top quintile underperforms the
bottom.** The signal lives in the middle of the distribution, not at the top
where you would buy. This is the same shape as the Quality strategy (IC t=+2.99,
spread -1.54pp t=-3.37) and it is why every IC in this project now prints its
quantile spread beside it. A "PASSES" alone would have sent someone to buy the
highest-ROIC names, which is the losing end.

### SECTOR: NOT THE HIDDEN VARIABLE

Ranking WITHIN sector (so a quantum name competes only with quantum names)
changes almost nothing -- roic h=20 goes +0.032 raw to +0.025 neutral, slightly
weaker. Sector strength as its own factor does NOT predict (t=+0.77 to +1.22
across horizons). But a stock's forward return correlates **+0.31 to +0.36**
with its sector ETF's: sector is a large part of what happens to you, and its
trailing momentum still does not tell you which sector to be in.

### WHAT ACTUALLY SEPARATES A BOUNCE FROM A BREAK

83,884 zone arrivals, top forward-return decile vs bottom (8,407 vs 8,410):

    pct_hi           0.709 vs 0.662   t=+14.84   <- dominant
    premium_score   47.77  vs 51.84   t= -7.21
    touches          9.15  vs  8.74   t= +5.40
    fund_score      49.74  vs 48.64   t= +5.24
    bmed_atr         3.94  vs  4.23   t= -5.13
    hype_score      50.24  vs 51.37   t= -4.90
    interest_cover   7.54  vs  3.74   t= +4.11
    f_score          4.61  vs  4.50   t= +3.82
    roic            -0.090 vs -0.161  t= +3.69
    brk              0.482 vs 0.499   t= -3.58

**Fundamentals DO separate bouncers from breaks** -- fund_score, interest_cover,
f_score and roic all positive and significant. That is a direct win for the
thesis, and the first test missed it by asking only whether fundamentals predict
returns cross-sectionally rather than whether they predict WHICH ZONE HOLDS.

**Hype and sentiment run the OTHER WAY.** premium_score (t=-7.21),
hype_score (t=-4.90) and attention_score (t=-3.44) are all HIGHER in the names
that broke. At a support test, attention is a warning, not a tailwind. AMSC on
2026-05-06 is the illustration: hype 80, and it fell -33.2%.

**The dominant separator is `pct_hi` (t=+14.84)** -- proximity to the 250-day
high. A zone holds when the stock is near its highs and fails when it is deeply
beaten down. "Buy the biggest dip at support" is backwards.

Two zone attributes invert earlier findings and the difference is the QUESTION,
not a contradiction: `touches` predicts a smaller median rally (phase 1) but a
HIGHER PROBABILITY of landing in the top decile here; `bmed_atr` -- a level with
a big historical bounce -- is LESS likely to bounce now.

### DISCIPLINE

174 tests at |t| >= 2.0; **~9 expected to cross by chance**. Printed at the
bottom of the study output. Single cells at t~2.0 are unproven; what counts is
the same sign at several horizons.

### FILES

`couple_panel.py` (build, ~30 min, `--rebuild`), `couple_study.py` (analysis,
~4 min), `data/_couple_panel.parquet` (14 MB),
`data/_zone_cases_20260830.csv` (16,817 cases, 8,407 bounced / 8,410 broke,
every fundamental, hype, sentiment and zone attribute attached).

Verified after: pins 28/28, validate screen 3/3, zones selftest OK.


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
_Generated 2026-10-27 12:44 — do not edit by hand._

| step | cadence | last | median | slowest (last 5) | budget | runs |
|---|---|---:|---:|---:|---:|---:|
| `universe` | daily | 38s | 8s | 53s | 2.0 min | 58 |
| `bars` | daily | 5.9 min | 50s | 5.9 min | 10.0 min | 58 |
| `macro` | daily | 3.8 min | 1.6 min | 3.8 min | 15.0 min | 58 |
| `news` | daily | 20s | 5s | 20s | 10.0 min | 58 |
| `senti_cache` | daily | 15s | 3s | 22s | 10.0 min | 58 |
| `sentiment` | daily | 80s | 16s | 80s | 15.0 min | 59 |
| `bounce` | daily | 86s | 39s | 86s | 15.0 min | 59 |
| `zones` | daily | 2.7 min | 86s | 4.3 min | 30.0 min | 42 |
| `shortvol` | daily | 6s | 4s | 8s | 10.0 min | 56 |
| `hype` | daily | 4.2 min | 2.1 min | 5.6 min | 120.0 min | 59 |
| `provider` | daily | 64.7 min | 65.1 min | 73.8 min | 120.0 min | 51 |
| `sec_facts` | quarterly | 7s | 5s | 26s | 60.0 min | 14 |
| `sec_gap` | weekly | 12.1 min | 5.2 min | 12.1 min | 20.0 min | 13 |
| `sec_edgar` | daily | 48s | 22s | 3.3 min | 30.0 min | 16 |
| `fundamental` | daily | 4.5 min | 3.0 min | 12.5 min | 180.0 min | 63 |
| `events` | weekly | 12.1 min | 10.6 min | 590.9 min ⚠ | 30.0 min | 14 |
| `leaderboard` | weekly | 46.3 min | 40.0 min | 90.2 min ⚠ | 90.0 min | 15 |
| `dip` | daily | 39s | 17s | 43s | 15.0 min | 63 |
| `combo` | daily | 39s | 17s | 39s | 15.0 min | 62 |
| `candidates` | daily | 4s | 4s | 6s | 5.0 min | 19 |
| `floor` | daily | 56s | 54s | 85s | 15.0 min | 13 |
| `sectors` | daily | 4.6 min | 4.0 min | 7.6 min | 30.0 min | 13 |
| `validate` | daily | 11.0 min | 7.0 min | 24.2 min | 60.0 min | 49 |
| `explore` | daily | 16s | 8s | 42s | 5.0 min | 70 |
| `snapshots` | daily | 20s | 4s | 40s | 5.0 min | 85 |
| `profiles` | daily | 3.6 min | 23.6 min | 6.4 min | 15.0 min | 65 |
| `retention` | daily | 0s | 0s | 0s | 5.0 min | 75 |
| `dashboard` | daily | 1s | 0s | 2s | 2.0 min | 76 |
| `docs` | daily | 3s | 0s | 3s | 5.0 min | 92 |

**Daily total ≈ 112.0 min.** Weekly adds 55.8 min on top. ⚠ marks a step whose slowest run of the last 5 exceeded its budget.
<!-- /GENERATED:costs -->

## Stores (generated)

<!-- GENERATED:stores -->
_Generated 2026-10-27 12:44 — do not edit by hand._

| store | files | MB | span |
|---|---:|---:|---|
| bars 1d | 122 | 249.2 | 2016-09 → 2026-10 |
| bars 1h | 5 | 0.8 | 2026-06 → 2026-10 |
| bars ETF | 124 | 2.9 | 2016-07 → 2026-10 |
| news | 123 | 173.0 | 2016-08 → 2026-10 |
| sentiment cache | 123 | 12.0 | 2016-08 → 2026-10 |
| scores | 122 | 323.9 | 2016-09 → 2026-10 |
| fundamentals | 70 | 341.7 | 2009q2 → 2026q3 |
| short volume | 75 | 56.8 | 2020-08 → 2026-10 |
| flags | 61 | 5.2 | 2026-07-31 → 2026-10-26 |
| rejects | 21 | 7.9 | 2026-09-28 → 2026-10-26 |
| zones | 42 | 23.7 | 2026-08-27 → 2026-10-26 |
| fundamentals (companyfacts) | 131 | 75.0 | 1980q3 → 2028q3 |
| fundamentals (EDGAR filings) | 21 | 0.4 | 2009q4 → 2026q3 |
| loose (macro, universe, jobs, study) | 37 | 175.4 | — |

**`data/` total ≈ 1,448 MB.** `reports/` is a further 108 MB across 353 pages.

Measured bytes per stored row (zstd-9): bars **25.0**, news **91.8**, fundamentals **11.6**, scores **3.2**, short volume **12.1**.
<!-- /GENERATED:stores -->

## Modules (generated)

<!-- GENERATED:modules -->
_Generated 2026-10-27 12:44 — do not edit by hand._

| module | metrics | stored sessions | span |
|---|---:|---:|---|
| `sentiment` | 26 | 387 | 2016-09-27 → 2026-10-26 |
| `fundamental` | 61 | 244 | 2016-09-26 → 2026-10-26 |
| `hype` | 20 | 362 | 2016-10-25 → 2026-10-26 |
| `dip` | 10 | 289 | 2016-09-27 → 2026-10-26 |
| `combo` | 15 | 244 | 2016-11-04 → 2026-10-26 |
<!-- /GENERATED:modules -->

## Study (generated)

<!-- GENERATED:study -->
_Generated 2026-10-27 12:44 — do not edit by hand._

1,856 cells measured across 115 metrics, horizons [1, 5, 20, 60], buckets ['all', 'large', 'mid', 'small'].

**71 metric(s) reach |t| >= 2 at their best horizon.**
| metric | module | best h | IC | t | hit |
|---|---|---:|---:|---:|---:|
| `fund_score` | fundamental | 20 | +0.0370 | +7.63 | 68% |
| `dip_gate` | dip | 20 | +0.0208 | +5.86 | 68% |
| `fcf_yield` | fundamental | 20 | +0.0358 | +5.36 | 60% |
| `roic` | fundamental | 20 | +0.0352 | +5.22 | 67% |
| `interest_cover` | fundamental | 20 | +0.0408 | +5.21 | 63% |
| `net_debt_ebitda` | fundamental | 20 | -0.0277 | -5.18 | 33% |
| `fund_rank` | dip | 20 | +0.0286 | +5.07 | 65% |
| `quality_score` | fundamental | 20 | +0.0330 | +5.06 | 65% |
| `net_issuance` | fundamental | 20 | -0.0343 | -5.05 | 39% |
| `growth_score` | fundamental | 20 | +0.0221 | +4.99 | 65% |
| `safety_score` | fundamental | 1 | +0.0178 | +4.88 | 62% |
| `f_score` | fundamental | 1 | +0.0289 | +4.80 | 62% |
| `gpoa` | fundamental | 20 | +0.0284 | +4.77 | 61% |
| `z_score` | fundamental | 60 | -0.0563 | -4.63 | 23% |
| `roic_wacc` | fundamental | 20 | +0.0347 | +4.54 | 66% |
| `roe` | fundamental | 20 | +0.0344 | +4.50 | 63% |
| `ebitda_growth` | fundamental | 1 | +0.0407 | +4.40 | 69% |
| `rev_growth_q` | fundamental | 20 | +0.0885 | +4.04 | 100% |
| `ev_sales` | fundamental | 20 | -0.0297 | -3.93 | 39% |
| `fcf_growth` | fundamental | 1 | +0.0129 | +3.87 | 61% |
<!-- /GENERATED:study -->

