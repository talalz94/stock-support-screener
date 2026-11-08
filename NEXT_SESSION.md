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


## IDEAS QUEUE — PARKED BY THE USER, NOT BUILT (design and ask before building)

**Remind the user of these whenever they ask for an update** (their instruction,
2026-11-02: "remind me whenever I ask for an update or something, dont schedule it"),
and ask whether to start the bounce-trade tests. No scheduled reminder exists for them.

### NARRATIVE SCORE (asked for 2026-11-02)
**What:** a score for which stories are likely to drive a stock or sector: sector and
industry trends, policy (regulation, budgets, tariffs, rates), technology cycles, and
seasonality, each now, in the near future and further out. It needs an AI (Claude)
reading the latest information. So: a skill with a fixed rubric, run automatically every
N days, writing a dated, structured result that the screener reads like any other
module and can finally bundle into the multi-criteria ranking.
**Constraints to settle first:**
1. **It cannot be backtested.** The model already knows how any past date turned out,
   so the only honest test is FORWARD: store each run as a dated snapshot (like the
   earnings calendar, `news_events.upcoming_earnings(asof)`) and measure it months
   later against outcomes. It gets no weight in combo until then (the house rule).
2. **Inputs:** our own stores first (sector scores, news events, Benzinga headlines,
   insider and House trades, FRED macro, the seasonality study -- measured NOT
   predictive, so context only), then web research, every claim with its source.
   No paid sources; no scraping against a site's terms.
3. **Output:** per sector and sub-industry (and per stock for the shortlist): themes,
   direction (+/-), horizon (now / ~3 months / ~12 months), confidence, evidence links,
   run date; a numeric -2..+2 per horizon. A stock's score = its group's score + its own
   exposure.
4. **Where it runs:** a Claude-app scheduled task (the app must be open: schedule often
   with a cheap "already done this period" exit, as `gainers-at-support-tracking` does;
   see the 2026-11-02 section), writing `data/narrative/<date>.json`; the screener reads
   the newest snapshot <= asof.
5. **Risks:** narratives follow price (an echo of momentum, not a cause); run-to-run churn
   (fixed rubric, change only on new evidence); token cost per run; stale views when the
   app is closed.
6. **Evaluation:** after 3-6 months of snapshots, test like every module (IC vs forward
   returns, against the validated sector score as the baseline); until then shown only.

### BOUNCE-TRADE REFINEMENTS (planned 2026-11-02; NOT RUN -- the user will refine first)
**Base:** `big_movers_study.py --trades` (the 11-02 morning section): the saved events,
buy at the next open, PRIMARY bracket b5_10 (+5% target / -5% stop / 10 sessions; a gap
fills at the open; a day touching both is the stop). These rules are fixed now, before
any result.
0. **More dates first.** 52 dates are thin for a regime split. Re-run the events at
   `STEP = 10` (about 220 dates from 2018-01; 10-session trades never overlap at that
   spacing, while the 40-session FAIL/HIT columns would, so keep the 42-step file for
   those): about 45 min, into `_big_movers_events_s10.parquet`.
1. **Closer to the level:** AT (0-2.5% above the zone) minus NEAR (2.5-6%), for every
   at-support name and for the hot third (rally_count, gain_days); and the level-stop
   bracket (+5%, stop 3% under the level) by band -- an AT entry has the nearer stop.
2. **Market uptrend:** signal dates with SPY's close (macro's ETF store,
   `macro.fetch_etfs`) above its 50-day average minus the dates below it; the stock's
   sector ETF (`macro.SECTOR_ETF_FOR`) the same way as a secondary.
3. **No earnings inside the hold:** drop names with an 8-K Item 2.02 public between the
   entry and the 10th session. The filing date stands in for the announced date, which
   companies publish weeks ahead (the Finnhub snapshots only begin 2026-11-01). Report
   the share of stops that were gaps, with and without.
4. **Dips won back, inside the hot names:** within the hot third (all-liquid cutoffs, as
   `level_value`), recovery_rate's top third minus its bottom third (>= 2 dips).
**Rules:** per date, t across dates, halves before / from 2022; baselines on the same
dates -- every at-support name, the list rule, every liquid name; count the tests and the
~5% expected at |t| >= 2 by chance. A filter is kept only if it holds in both halves.
Filters are combined only after each passes alone, and a combination is chosen on
pre-2022 dates and judged after 2022 (walk-forward). Code: a `--refine` mode in
`big_movers_study.py` reading the trades file (about 1 min after step 0).

**STARTED 2026-11-08, scope widened by the user's answers:**
- "try out different levels, by market cap and other stuff, may be different levels work for different stocks";
- entry "usually next morning open like after an hour", or later in the day "in case the price is edging closer";
- market filter: "research and try the best ones".

The design is `trade_rules_study.py`'s docstring, fixed before any result:
- **Rules:** 108 = 3 entries (open; a limit at the level + 0.5% for 3 sessions; next close) x 36 exits:
  - fixed % brackets;
  - 20-day-ATR brackets;
  - a stop 3% under the level.
- **Costs:** 0.10% a round trip.
- **Segments:** market cap (< $2B / $2-10B / $10-200B / > $200B, the scores store's `mktcap` as of each date), volatility third, hotness, sector, AT/NEAR.
- **Filters:** 11, from research.
  - Connors & Alvarez: pullbacks only above the 200-day.
  - Nagel 2012 (RFS): reversal returns rise WITH the VIX, so "calm only" may be backwards. Tested both ways.
  - The list: SPY / QQQ over the 50- and 200-day, breadth > 50%, VIX >= 20, VIX falling, high-yield spread falling, SPY > 200-day AND VIX >= 20, the sector ETF, the stock's own 200-day.
- **Selection:** everything is chosen on 2018-2021 and judged on 2022-2026 against the plain +5/-5 rule. Kept only if it holds in both 2018-19 and 2020-21 and on TEST.
- **Base:** `big_movers_study.py --step 10` (about 220 dates, `_big_movers_events_s10.parquet`).
- **Engine:** the self-test passes 17 hand-worked cases (daily and 30-minute).
- **Hourly check:** the store's hourly bars (88 names, 4 months, 4,201 name-days) put the price about an hour in a median 1.6% from the open, more than 2% away on 41% of days, and 0.5% lower on average.
- **INTRADAY, approved by the user ("if its free"):**
  - Alpaca's free tier serves SIP history but pages intraday bars at 248 a request even across symbols (measured).
  - So only 30-minute REGULAR-session bars are fetched (13 a day: 09:30 = the official open, 10:30 = "an hour after"), for the ENTRY DAY of every at-support case and the limit's fill day when it filled on day 2 or 3.
  - About 14k requests, about 1.5 h, into `data/trade_rules_30m/` (the study's own; nightly retention prunes the 1h store).
  - New entries: open+1h (buy at 10:30), and the limit in 30-minute order (only the fill's own bar stays pessimistic). Every exit, so 72 more rules.
  - I1: open+1h minus open. I2: limit in 30-minute order minus the pessimistic limit. Does the best rule change?
  - Fixed before any bar was downloaded (`trade_rules_study.py`, intraday block).

### RELATIVE STRENGTH VS THE MARKET AND THE SECTOR (planned 2026-11-02; NOT BUILT)
**The idea, in the user's words:** a stock's line should roughly follow its index and its
sector; one that has been ABOVE its sector is a healthy sign. Nothing like it exists yet
(checked 2026-11-02: no relative-strength metric anywhere in the code).
**Benchmarks -- every piece exists:** the market = SPY from macro's ETF store (QQQ, IWM
there too); the sector and the sub-industry = equal-weight indexes of liquid members from
`sectors.group_returns` (eligible the prior day, >= MIN_MEMBERS), with the stock itself
left out; the sector ETF (`macro.SECTOR_ETF_FOR`) as a cross-check.
**Candidate metrics** (pick before testing; windows 21 / 63 / 126 / 252 sessions):
- excess return vs the sector and vs the market (log-return difference);
- the RS line (stock / benchmark, 100 at the window start): above its own 50-day
  average, and its slope;
- time above: the share of the last 63 / 126 sessions the stock's rebased line sat
  above its sector's;
- an RS-line 52-week high (the classic "leader" sign, often before price makes one);
- residual (beta-adjusted) return vs the market from a 252-session regression: did it
  do better than its beta says it should.
**Where it shows:** a "vs market and sector" chart on the stock profile (the stock, its
sector and the market rebased to 100 a year ago, plus the RS line and its 50-day
average) with its numbers (1m / 3m / 6m / 1y vs each, % of time above); a "vs sector 3m"
column and a leading / lagging badge on the lists (candidates, floor, explore, Gainers at
support).
**How it is judged before any weight** (the house rule): as a factor -- IC vs forward 20
/ 60-session excess returns, split halves; and at support -- the bounce trade (b5_10)
and excess_20 by RS third, for every at-support name and for the hot third ("a leader
pulling back to support"). Baselines: plain 12-1 momentum (already measured at support:
+0.25pp excess_20, t 0.47 -- weak) and the validated sector score. In the literature:
industry momentum explains much of stock momentum (Moskowitz & Grinblatt 1999); residual
momentum is steadier than raw (Blitz, Huij & Martens 2011); 1-month strength tends to
reverse. Caveats: today's sub-industry map; survivorship.

### ONE FILTERING PIPELINE ACROSS ALL METRICS (the user's next step, 2026-11-02)
The user will come back to refine every metric and build a comprehensive analysis
pipeline that filters stocks on many criteria. Starting points: a catalogue of every
metric with its measured evidence (IC, at-support and bounce-trade effect, halves,
walk-forward -- most of it already sits in the studies' JSON files); `combo` (only
measured metrics enter, 100 dates of evidence each); the saved lists and the
`watchlists` step for forward checks. Every composite filter is judged like the rest:
chosen on early dates, tested on later ones, against the plain at-support rule.

## State at 2026-11-09 (later) — STOCK PAGES IN TABS, AND A SETTINGS PAGE

**Asked:** "yes go ahead with the stock page tabs", then "also there is a config page that is professional that can be used to tweak settings/configs, please be neat and elegant and consistent and bug free".

**Stock pages in tabs:**
- The header (name, price, sector) stays above the tabs: Overview (chart, vs market and sector, radar), Fundamentals (financials, metrics), Insiders & filings, News & sentiment.
- Measured on AAPL: 7,012 px -> 2,007 px at 1280 wide; 14,193 -> 4,437 px on a phone (the Overview tab).
- The tab bar sticks under the menu. On a phone it uses short labels and fits without scrolling.
- `#t-<tab>` opens a tab directly. Arrow keys, Home and End move between tabs. With scripting off every section shows, as before.
- `ui.tabs()` is a shared component, so other pages can use it.
- The grouping is a setting (`settings.TABS`, `tab_groups`). A section no tab names goes to a "More" tab, never nowhere.
- The subtitle's "scores from ..." list and the old footer paragraph now sit behind an (i).
- **Found while testing:** a wide table in a tab hidden at load never jumped to its newest column. It jumped at width 0, and when shown it was 11 px short because its own scrollbar appeared after the jump. `SCROLL_SYNC_JS` now pins once when the table is first visible, plus one settle pass, and never yanks a reader's scroll on later resizes.

**The Settings page** (`settings_page.py` -> `reports/settings.html`, System -> Settings; Today has a "Customize" link):
- **This browser** (localStorage `scr.prefs`, applied by the menu script on every page):
  - **Theme:** Auto / Light / Dark. An early head script applies it before the first paint, and the theme rules live in the refreshed menu style block, so old dated pages follow it too.
  - **Today's cards:** order and visibility. Today can never be left blank.
- **Project file** (needs `python serve.py --open`; opened as a file these sections are read-only and say how to change them):
  - **Stock page:** sections on/off, tab, order, the tab it opens on, chart and comparison windows, Annual or Quarterly first, growth rows, number of filings. Saved to `data/_profile.json`.
  - **Shortlist:** first ranking (its evidence shown under the choice), size, "skip warning filings", and the maximum distance above the level (0.5-6%; candidates end at 6%). Saved to `shortlist_recipes.json`, editing only the changed lines in place, so the git diff is exactly those lines.
- **Saving:**
  - Every save goes through the file owner's validator (`settings.apply_ui`, `shortlist.apply_ui`); nothing is saved when any value is wrong.
  - The server route is `POST /api/settings`: header-guarded and limited to 64 KB.
  - The shortlist redraws its page and Today at once.
  - The Rebuild buttons reuse `/api/run/profiles` and `/api/run/shortlist`, with progress and the result from the job table. A shortlist rebuild then refreshes Today.
- **Behaviour:**
  - Unsaved changes are marked, and leaving the page asks first.
  - Invalid values disable Save with a plain message.
  - "Restore defaults" fills the form; Save keeps it.
  - Export and import cover this browser's choices; project settings load into their forms for review.

**Checked:**
- In the browser, live mode:
  - **Stock save:** saved with the file checked; one page built with a non-default layout.
  - **Shortlist save:** the file changed on exactly 2 lines, and Today switched to that ranking's top 5.
  - **Restore defaults and save:** the file matched git exactly.
  - **Browser settings:** theme, card order and hiding, all reflected on Today.
- **The Rebuild button, end to end:**
  - It ran `profiles` through the server: ok, 501 s, "139 of 139 profile page(s), 20 for the shortlist", recorded in the job table.
  - The page showed the progress and then that result.
  - Reloaded mid-run, the page picked the running rebuild back up.
- **After the rebuild:** all 139 stock pages have tabs. NVDA's annual and quarterly tables open on their newest column at 375 px (exactly at the end, not 11 px short).
- **File mode:** a static server stood in for a file. Project sections locked with the banner; theme and Today still work.
- **Whole site:** the page audit is clean on 433 pages, and a second menu refresh changes 0 pages.
- **Phone width:** no sideways scroll on Settings or on the stock page; the tab bar fits.
- **Tests:**
  - Self-tests in `settings`, `shortlist` (`--selftest`), `settings_page` (`--selftest`), `ui` and `stock_profile`. They run on scratch copies; the real files are untouched.
  - Every inline script on 11 page types compiles under Node.

## State at 2026-11-09 — UI PHASE 1: A "TODAY" HOME, A GROUPED MENU ON ALL 432 PAGES, NO DEAD LINKS

**Asked:** "yes mock up the Today page and menu first and continue building and checking, no bugs please".

**Built:**
- **Today** (`today_page.py` -> `reports/index.html`, the home page):
  - **Data line:** whether the data is current, the last run, failing steps.
  - **Market:** SPY against its 50- and 200-day averages, VIX, breadth, the strongest and weakest sectors. A reading that lags the session is shown with its own date: FRED posts the VIX a day late, so 11-06 shows "reading of Thu Nov 5".
  - **Lists:** the Shortlist's top 5 with the trade plan (an earnings note sits under the ticker); the watchlists against the market.
  - **This week:** earnings by report slot, shortlist names in bold, and recent warning filings for listed names.
  - **Narrative:** a placeholder until the first checked run.
  - **Layout:** explanations sit behind an (i). Each card fails on its own. 1,292 px tall at 1280 px wide, against 6,100 px for the old job-table home.
- **The job table moved to `reports/system.html`** ("Status", under System). The `dashboard` step builds both pages; `serve.py` builds both at start-up.
- **Menu:** Today plus four groups (Lists, Research, Stocks, System) as native dropdowns with one-line hints, and a ticker box. On a phone the box moves into the Stocks menu, so the bar is one line (44 px). Dropdowns open just under the bar, however tall it is. The ticker box goes to `stock/index.html?t=T`, which opens T's page or offers to build it.
- **`ui.refresh_nav()` / `python ui.py --renav`:** swaps the current menu and its styles into every page already written. It keeps each page's session picker and highlighted entry, and fixes "status hub" body links. 409 of 412 pages had the old menu (most are dated snapshots no step rebuilds). It runs in the `dashboard` step each night; unchanged pages are not rewritten (3.7 s with nothing to do; 46-51 s when every page changes). Writes are atomic and skip a page something else rewrote meanwhile.
- **Dead links: 336 -> 0** (`status.py --pages`, 432 pages):
  - `ui.stock_href()` links a built page directly, else the stock list.
  - Over file://, the menu script routes script-drawn stock links (Explore, Candidates, Floor, Insiders, Sectors, Shortlist) through the stock list. The local server already builds a missing page on request.
- **The Shortlist's top 20 get stock pages nightly** (139 pages now; about 2 s each after loading). The `profiles` step measured 463 s for all 139, against its 900 s limit (207-454 s for 119 before).
  - A page built only for the shortlist is deleted 20 sessions after the name leaves it (`data/_profile_listed.json`, `stock_profile.listed_pages`).
  - Pages built for a bounce flag or on request are never touched.
- **ORDER BUG FIXED:** `narrative` and `shortlist` now run right after `sectors`, before `explore` and `profiles`. Explore's support, trade-plan and strength columns read "the newest shortlist build on or before the session". With the shortlist running after explore, every night's Explore would have shown the previous session's buy limits, targets and stops as current. It had not bitten yet: the shortlist began 11-06 and Explore was rebuilt by hand after it.
- **Phones:**
  - **Explore:** the table was 139 px wide beside the strategy rail, so the rail now stacks below it. The filter bar is no longer sticky (it held 200 of 812 px). Rule rows wrap; one had pushed the page 24 px sideways.
  - **Stock pages:** the radar grid's 9 px overflow was a stale build; the code fix was already in, and all pages are rebuilt.
- **The bounce archive** (`bounce/index.html`) uses the shared page and menu.
- **The Status page's bare `details` / `summary` rules** (meant for tracebacks in its step table) also restyled the menu: its bar was 6 px taller than on every other page. They are now scoped to table cells, and the menu sets its own margin.
- **`stock_profile.selftest` read only the first `<script>`.** The menu's script now comes first on every page, so the check failed on a page whose chart handler was present. It now checks every inline script.

**Checked:**
- In the browser at 1280, 768 and 375 px: no sideways scroll on any of 19 page types, or on 10 stock pages. The menu bar is 44-45 px; it is 81 px on a phone only where a page has a session picker (Explore, stock pages). No console errors.
- The menu opens and closes (one at a time; outside click and Escape close it). Typing "aapl" in the phone menu opens AAPL's page. Every (i) note opens inside the screen.
- `ui.selftest`: now includes a menu-refresh test on an old page, a current page and a page with no menu, with a second run that must change nothing.
- `stock_profile`, `report`, `watchlist_page` and `narrative` selftests: pass. All 10 touched modules import with warnings as errors.
- Every Today figure was recomputed independently: SPY -3.97% vs 50-day and +0.67% vs 200-day, matching `trade_rules_study.regimes` m1=1, m2=0; VIX 28.18; breadth 37.8%.

**Next (the plan's remaining phases):**
- **Stock page:** tabs; column presets per table; collapsible sections; (i) instead of paragraphs; plain labels.
- **Customising:** a gear panel (browser-saved, with export and import).
- **Polish:** a dark-mode toggle; lighter heavy pages (Insider 743 KB, House 956 KB, Zones 896 KB).

## State at 2026-11-08 (evening) — UI/UX AUDIT: MEASURED, PLAN PROPOSED, AWAITING THE USER

**Asked:** "its too cluttered with so much info, I want it better organized in a professional way with room to customize ... snappy, ui/ux should be friendly and bug free" -- analysis first.

**Measured** (local server, 16 pages):
- **Speed:** every page loads in 0.1-0.6 s; Explore is the slowest (0.64 s, 1.7 MB, 11,374 DOM nodes).
- **Heavy files for few rows:** Insider 743 KB, House 956 KB, Zones 896 KB, Bounce 602 KB. Stock pages are ~270 KB each.
- **Long pages at 1280 px:** Bounce 12,300 px, metrics 10,600, fundamental 7,500, a stock page 7,000, hub 6,100.
- **Long pages at phone width:** Bounce 21 screens, a stock page 17, the hub 11.
- **The menu:** 15 flat items, 180-260 px tall on a phone (a quarter to a third of the screen).
- **Density:** Explore shows 61 columns by default, the Shortlist 28; a stock page has 12 sections.
- **Overlap:** six pages are variants of "stocks near support" -- Bounce, Zones, Candidates, Floor retest, Shortlist, Watchlists.
- **Customising:** only through the CLI (`settings.py` for profile blocks), a JSON file (`shortlist_recipes.json`), or Explore's per-visit column chooser.

**Bugs found:**
- `status.py --pages`: 330+ dead links. Every Shortlist and Watchlist stock link points at a profile page that was never built (0 of the Shortlist's top 50 have one), because `profiles` builds that day's bounce flags plus pages already on disk.
- At phone width, stock pages scrolled sideways. The new `relative` table: fixed (scroll wrapper). The radar grid (`.grid2` items kept their content width): fixed (`min-width:0`). 637 -> 371 px on AAPL. STILL OPEN: the metric cards' fixed 74 px percentile bars leave about 13 px of overflow on a 375 px phone -- part of the stock-page redesign.
- At phone width, Explore's filter bar overflows: open.
- `reports/metrics.html` was built only by hand, stale since 10-14. Fixed: the `docs` step now rebuilds it nightly.

**Proposed** (the user to choose; nothing built):
1. Bugs: profile pages for every name the lists link to (nightly for the Shortlist's default top N and the watchlists, or built on demand through `serve.py`); links fall back cleanly; a nightly UI check (dead links, phone-width overflow, console errors) in `validate`.
2. Structure: 5 menu groups (Today, Lists, Research, Stock search, System).
   - A "Today" home: market regime, the Shortlist's top names with plans, watchlist moves, this week's warnings and earnings, one data-freshness badge.
   - Jobs, stores and runs move to "System".
   - The overlapping list pages merge into Lists as views.
3. Declutter: tabs on the stock page (Overview / Chart & levels / Fundamentals / Insiders & filings / News); column presets per table (Essentials / Trade plan / Quality / Momentum / All); collapsible sections that remember their state; one (i) per section instead of paragraphs; plain labels for the jargon.
4. Customising: a gear panel on every page -- sections and order, column presets and density, the default ranking and size, hidden menu items, theme, saved filters. Kept in the browser (instant, offline) with export / import; optionally saved through `serve.py` so the nightly pages follow it too.
5. Polish: one design system in `ui.py` (page header with purpose and data-as-of, cards, a single table component); a dark-mode toggle (prefers-color-scheme already works); shared JSON data files and lazy sections for the heavy pages.

## State at 2026-11-08 (later) — PLAN ITEMS 1-4 STARTED: THE TRADE RULES, RELATIVE STRENGTH, THE EVIDENCE CATALOGUE, THE NARRATIVE TASK

**Base:** `big_movers_study.py --step 10`: 294,161 liquid name-dates on 217 dates (2018-01-02 .. 2026-08-07), 175,937 at support. TRAIN 101 dates (before 2022), TEST 116.
- `--step` writes `_s10` files and never touches the 42-step study.
- Rows are built one frame per date, to fit 4x the dates.

**ITEM 1 -- `trade_rules_study.py` (108 rules = 3 entries x 36 exits, 0.10% costs).**
- **Checks:** the engine self-test passes 17 cases. `--check` recomputed 20 random trades x 5 rules bar by bar from raw store bars: 100/100 identical.
- **The default (open, +/-5%, 10 sessions) is about zero after costs:** +0.03% a trade on TRAIN, +0.05% on TEST.
- **The LIMIT entry (level + 0.5%, 3 sessions) beats the open on every exit.** The 36 limit rules average +0.24 / +0.27% vs the open's -0.05 / +0.03%. It is better in 7 of 9 years, about equal in 2018, and slightly worse only in 2022 with the 3-ATR rule.
- **Kept with touch fills** (both halves agree and TEST t >= 2): limit +5/-5 (TEST +0.33%, t 2.00), limit +5/-8 (+0.40, 2.04), limit +3/-8 (+0.31, 2.07).
- **BUT with a fill that must trade 0.2% THROUGH the limit,** the fill rate goes 56.7% -> 53.9% and every limit rule loses 0.12-0.14%. TEST is then +0.20 to +0.33%, t 1.0-1.5: NOT kept. The lost fills were the touch-and-go "perfect bounces", which real limit orders often miss.
- **Q1 (chosen on TRAIN):** limit + 3 x ATR / - 3 x ATR, 10 sessions. TRAIN +0.70% (t 2.05), TEST +0.44% (t 1.37): not kept.
- **Q2, per-segment rules** (cap, volatility, hotness, sector, AT/NEAR): none beats the single Q1 rule on TEST (-0.09 .. -0.01pp). The ATR brackets already scale to each stock.
- **Q3, filters:**
  - SPY / QQQ trend filters point the WRONG way: the trades did better below the averages. Not significant.
  - VIX >= 20 is the TRAIN pick: on +0.56% vs off -0.30% (t 2.10). TEST is the same direction (+0.20 vs -0.04, t 0.59), as Nagel 2012 predicts; not kept.
  - The high-yield spread filter is untestable: FRED keeps only about 3 years of the ICE series (29% of dates).
- **Q4, earnings inside the hold:** no difference on average (+0.10pp, t 0.81), but more stop-outs (40.6% vs 33.8%).
- **Q5, dips won back among the hot:** nothing. **Q6, AT vs NEAR:** nothing.
- **Hourly check:** 88 names, 4,201 days. An hour in, the price sat a median 1.6% from the open and was 0.5% lower on average.
- **INTRADAY (done 2026-11-08):**
  - **Data:** 648 days of 30-minute regular-session bars. Highs and lows match the daily bars 100%; the 09:30 open matches within 0.2% on 96-100%.
  - **Checks:** 400 random trades recomputed bar by bar: 400/400 identical for both entries.
  - **I1, buying at 10:30 instead of the open:** no difference on the main exits (-0.03 / +0.10pp). The stop-under-the-level exit is worse at 10:30 (-0.21pp, t -3.1).
  - **I2, the limit's fill day in 30-minute order:** +0.27 / +0.17pp a trade over the pessimistic daily rule, kept on 34 of 36 exits.
  - **Best rule with intraday included (TRAIN pick):** limit +3% / -8%, 10 sessions. TEST +0.45% (t 3.06).
  - **With fills that must trade 0.2% THROUGH the limit:** still KEPT. +3/-8: TRAIN +0.48% (halves +0.26 / +0.70), TEST +0.36% (t 2.42), positive in every year but 2018. +5/-5: TEST +0.38% (t 2.37).
  - **The shortlist's trade plan is now limit +3/-8, with +5/-5 shown as the alternative.**

**ITEM 2 -- `rs_study.py`: relative strength says NOTHING about the bounce.**
- **Indexes:** leave-one-out equal-weight sector and sub-industry indexes (>= 8 other members) and SPY. A spot check of NVDA's rs_sec_63 against an independent recomputation matched to 5 decimals.
- **Tested:** 10 metrics (vs sector over 1-12 months, vs sub-industry, vs market, line above its 50-day, share of time above, RS-line high, beta-adjusted residual), plus momentum as the baseline.
- **R1 at support:** none kept (best TEST |t| 1.29).
- **R2 hot third:** -0.05pp, t -0.35.
- **R3 factor IC:** none kept (best resid_252 TEST t 1.85, NW).
- 35 comparisons, about 1.8 expected by chance; none reached |t| 2.

**ITEM 3 -- `metric_evidence.py`, the evidence catalogue:** every stored metric with history (103) plus the studies' own. It tests at support with the default and the Q1 trade, and the 20-day IC across all liquid names. KEPT / STRONG (TEST |t| >= 3) labels feed the filter page and the shortlist.
- **Result:** 114 metrics, 321 tests (about 16 pass |t| >= 2 and about 0.9 pass |t| >= 3 by chance). Only 3 are KEPT, about the chance level under the both-halves rule. None is STRONG.
  - **At support:** only `dist` with the Q1 limit rule. The furthest third above the level minus the nearest: TEST +0.35pp (t 2.39), halves +0.35 / +0.53. It is the limit mechanism: a name 2.5-6% above its level fills only on a real pullback.
  - **Across all liquid names:** `loss_days` (IC -0.032, t -2.16) and `vol_surge` (-0.013, t -2.02), both NEGATIVE (lottery-stock behaviour).
- **Promising on TEST, but failed 2018-19:**
  - fund_score IC +0.027 (t 3.19);
  - combo_h60 +0.04 (t 2.86);
  - combo_h20 +0.034 (t 2.55);
  - growth_score at support +0.14pp (t 2.38);
  - dip_gate at support (t 2.98).
- **No edge for the bounce trade:** gain_days, rally_count, recovery_rate, insider_score, sentiment, hype, rs_sec_63, mom_12_1, stretch / extension.

**ITEM 4 -- `narrative.py` + the Claude-app task `narrative-weekly` (Saturdays about 10:43; first run 2026-11-14):**
- **Inputs:** the new nightly step `narrative` (33rd of 36, after `watchlists`) writes `data/narrative/inputs_latest.json`. Per group (20 sectors, 44 sub-industries) it carries the score and rank (sectors only), RS, breadth, seasonality (context only), news counts and sentiment, the week's strongest-toned headlines and filing events. Then it ingests any new answer.
- **The task:** reads that file, researches the web (about 40 searches), and writes `data/narrative/<date>.json` with the Write tool. NO shell commands. The prompt lives in `narrative.TASK_PROMPT`.
- **Checking:** `check_answer` rejects anything malformed: scores outside -2..2, a source dated after the answer, a missing group, untagged themes (self-test 6/6).
- **No weight** until months of snapshots beat the sector score forward.
- **The task was saved WITHOUT auto mode:** the user should set it or "Run now" once to approve its tools.

**SHORTLIST BUILT (item 3, part 1).**
- **Files:** `shortlist.py` and `shortlist_recipes.json` (editable: default ranking, n = 20, max_n = 50, filters).
- **Nightly step:** `shortlist` (37 steps now).
- **Page:** `reports/shortlist/latest.html` (nav "Shortlist"). Checked in the browser: no console errors; ranking switch, size and hide-earnings all work.
- **Ranking backtest** (`--backtest`, top 20 per date): **fund_score is best**. Picked on TRAIN under the limit rule; KEPT under the open rule (+0.22pp a trade vs every at-support name, t 2.21). The other rankings are listed with their evidence; gain_days has no edge.
- **Forward test:** every ranking's daily top 50 goes into `data/shortlist/ledger.parquet` and is scored when its 13 sessions pass.

**DONE later on 2026-11-08 (the rest of items 1-3):**
1. **Explore:** 15 new columns (at support, above level %, buy limit, target, stop, ATR %, gainer and loser days, rallies, dips won back, vs sector 3m / 12m, vs sub-industry, vs market, narrative). They are joined from `shortlist.explore_columns`, and the relative strength covers every liquid name.
   - Every header tooltip ends with the catalogue's MEASURED line (`explore.evidence_note`).
   - Checked in the browser: 3,474 rows; 1,379 at support, 590 other liquid names.
2. **Stock profiles:** a new `relative` block after the price chart. It shows the stock, SPY, its sector and its sub-industry rebased a year ago (leave-one-out, as Explore), plus the 1m / 3m / 6m / 1y table. Checked on KLAC; the profile self-test passes.
3. **The sector map (the user approved the Finnhub fix):**
   - `industry_map.py --fetch` gave 3,451 of 3,472 tradeable names a label. `--apply` writes 931 overrides to `data/_sector_overrides.parquet`.
   - `macro.load_sector_map` and `sectors.groups` lay them over the SIC file on every read, so the weekly SIC rebuild never undoes them (`raw=True` gives the file as built).
   - The mixed labels are handled by rule: "Electrical Equipment" overrides only misfits; "Communications" -> Technology; food distributors and grocers -> Staples.
   - **If `data/` is lost:** re-run `--fetch` (about 1 h) and then `--apply`.
4. **The sector score, re-checked** (the 10-20 study kept as `data/_sector_study_summary_sic_20261020.json`):
   - On the corrected groups the old rs21 + breadth200 still holds out of sample (IC 0.088, t 2.31).
   - sector_study's own rule now picks **breadth200 + fund** (IC 0.082, t 2.51; rs21 fell to t 1.82 on the first half). `sector_score.COMPOSITE` follows the rule.
   - Items 1 and 2 re-run on the corrected groups: the same conclusions (copies `_trade_rules_study_sic_20261108.json`, `_rs_study_sic_20261108.json`).

**STILL OPEN:** `narrative-weekly` is set to auto by the user (2026-11-08); its first run is 2026-11-14. Then judge its first answer file and measure its cost before any per-stock narrative.

**FIXED 2026-11-08 (see above): the SIC sector map.**
- `macro.py` maps 3827-3851 to Healthcare, so 3827 optical instruments (KLAC, NVMI, CAMT, COHR) and 3829 measuring devices (ONTO, MIR, GEOS) sit in Healthcare: 20 names.
- 3559 "special machinery" puts AMAT and LRCX in Industrials.
- 3826 lab instruments (A, BIO, AVTR) and DHR sit in Technology.
- This matters most for item 4, where stocks inherit their group's narrative.
- The proposed fix: Finnhub's free profile `finnhubIndustry` for every name (about 1 h at 60/min) to correct the map, then re-check the sector score.

## State at 2026-11-08 — A WEEK UNATTENDED: EVERY SESSION IN, TWO RUNS SLEPT THROUGH, THE LISTS MET A PULLBACK

**Runs (all automatic, 0 failed):**
- 11-05 05:00: took 11-04.
- 11-06 05:00: took 11-05, finishing 16:40. The keep-awake log shows the process FROZEN twice, 566 min in total: the PC slept mid-run.
- 11-07 00:06: the weekly steps. Frozen 524 min overnight. On waking, a PR Newswire SSL error cost that run the FDA-approval wire.
- 11-07 17:55: took Fri 11-06. The PC slept through the morning starts and StartWhenAvailable caught up.

Prices, scores and zones are in through 11-06. The 128 thin names missing a 11-03 bar were filled by the next run's 5-session re-fetch. Nothing needs changing: resilience in code, as the power-settings rule says.

**Fixed:** the bars step logged "0 bars" every night, because it read `res["bars"]` while
`bars.update` returns `rows`. It now logs the rows written.

**Validate:** CLEAN on 11-05 and 11-07; 9 TTM-window problems on 11-06's rotating sample.

Full check of all 3,472 tradeable names on 11-08 (`ttm_invariants.check`, asof 11-06): 23,027 of 23,099 windows ok. The 72 bad windows sit in 12 companies:
- **8 known** 52/53-week or 16-week-quarter calendars: PEP, DPZ, COST, ACI, AZO, KR, JACK, RRGB. `no_gap` fails at a 120-123-day quarter.
- **4 not explained yet:**
  - AEHR: 13 windows spanning 301 days.
  - AIV: a stray 2026-01-31 period end after 2025-12-31.
  - LCLN and PS: 183-day spans.

**OPEN:** look at those 4.

**The lists after the Wed-Fri pullback, vs every liquid name on the same dates.** "Failed" means a low under 90% of the level since entry.
- 10-23, after 10 sessions:
  - list -0.42% (median -0.96%, 47% up) vs -0.64% (median +0.29%, 52% up);
  - 77 per plan, 11 held, 14 below support, **78 failed** (25 on 11-03).
- 10-30, after 5 sessions:
  - list -3.98% (median -2.67%, 40% up) vs -1.50% (median -0.15%, 48% up);
  - 70 / 13 / 28 / **83 failed**.

As the study measured, hot names at support break their levels far more often: about 43% of each list within 5-10 sessions. This is a live case for the planned market-uptrend test.

**The claude.ai dashboard copy is stale at 10-30.**
- The app task's runs at 11-04 21:21Z and 11-05 14:45Z both report "succeeded", but `last_uploaded.txt` still says 10-30.
- The 11-04 run sat at its first command with no process running: an approval prompt nobody answered.
- It needs the user present, or an allow rule for its exact commands.

## State at 2026-11-02 (late morning) — DEBT FALLBACK TAGS: 244 NAMES GAIN A DEBT FIGURE, AND NO NAME THAT HAD ONE IS MOVED BY IT

Why: on 10-30, 989 of 3,312 universe names had NO debt figure, so every debt input
(debt/equity, net debt/EBITDA, EV ratios, ROIC, interest cover) was blank and pillars fell
just under coverage -- under the two-pillar rule above, the reason GM, WMB, DASH, CPRT lost
their fund_score. What they file, read from SEC: WMB LongTermDebtAndCapitalLeaseObligations
28,121M + ...Current 2,197M (+ CommercialPaper 475M); DASH ConvertibleLongTermNotesPayable
2,728M; CPRT nothing but a 2.7M finance lease (debt-free); GM ONLY by business group
(Automotive 15.5B, GM Financial 75.2B long-term, a VIE subset inside it) -- no consolidated
total, and we read non-dimensional rows only, so GM is NOT covered. Of the 900 no-debt
filers in the 2026q3 bulk set, 384 tag some debt-like line, most often the
...AndCapitalLeaseObligations pair (59/51), finance leases (52/45), convertibles, notes.

FIX (`fundamentals.TAGS`, `ALIAS_TIERS`, `_alias_tiers` -- in `facts_asof` and `history()`):
side-specific tags appended to debt_lt / debt_st, used ONLY by a filer with no primary debt
row (either concept) anywhere in the 12-quarter frame, and then its earliest tier: the
...AndCapitalLeaseObligations totals, then named components (convertible notes, long-term
notes, lines of credit, other), then finance leases. Left out on purpose: totals spanning
both sides (SeniorNotes, NotesPayable, DebtLongtermAndShorttermCombinedAmount -- they would
double-count against debt_st) and bank funding lines (SubordinatedDebt, OtherBorrowings).

TWO CUTS FAILED FIRST, both caught by measuring names that already had debt:
* alias RANK alone: `_latest` takes the newest PERIOD before the preferred alias, so 462
  names changed -- TMUS 87.5B -> 7.5B (its total only in the 10-K, a 1,136M lease in every
  10-Q), CRWV 24.9B -> 0.2B, KMX's "other" debt beat its total.
* tiers PER CONCEPT: 284 changed -- a LongTermDebt total that already holds the current part
  picked up ConvertibleDebtCurrent as debt_st (IRWD 199.5M -> 399.4M). Hence one family.

DATA. Bulk re-ingested 22 quarters (only change: +140,216 debt-tag rows; old copy
`data/fundamentals_pre20261101`); EDGAR rebuilt since 10-01 (old copies
`data/fundamentals_edgar_pre20261101`, `..._rebuilt20261101`).
A COMPANYFACTS REFETCH OF THE 1,208 OVERLAPPING CIKs WAS DONE AND REVERTED. After it
companyfacts won recent partitions for those names and (a) it carries NOTE-level facts: KNX
LongTermDebt 231.9M against a 1.75B total; (b) `_cf_qtrs` drops 52/53-week year-to-dates --
PEP's 36-week YTD is 252 days, outside the 264-284 band -- so PEP's EPS fell back to the Q1
roll, 7.95 -> 6.37, and its pin failed. Restored from `data/fundamentals_cf_pre20261101`; the
refetched copy is `data/fundamentals_cf_refetched20261102`. (b) is LATENT for any 52/53-week
filer the weekly `sec_gap` refetches -- open.

MEASURED, facts as of 10-30, before vs after: names with any debt figure 2,323 -> 2,567 of 3,312 (+244,
213 non-financial; 0 lost); debt_lt 2,138 -> 2,347, debt_st 1,581 -> 1,736; names that HAD a
figure and changed: 0. Largest gains: SCHW 36.6B, WMB 30.3B, CP 25.8B, CSX 18.8B, APH 17.9B,
LVS 15.4B, PSKY, NCLH, SYY, HLT, THC, LUMN, DVA, WCN, ACI, SIRI, LYV, CAH, AKAM 7.6B. Still
none: GM (business groups), CPRT (debt-free; its lease rows are not in the frame it reads).
regression_pins 48/48 (WMB, DASH, APH and TMUS added); fundamentals and edgar selftests OK
(`_selftest_debt_aliases`: rank, a newer lease must not beat TMUS's total, KMX's component
must not beat its total, IRWD's notes not counted twice).

RE-SCORED 10-30 (11 steps, 0 failed; fundamental 3,644 s -- the off-cycle Finnhub refetch):
value pillar 1,612 -> 1,761, safety 1,917 -> 1,985, quality 2,321 -> 2,363, growth unchanged;
fund_score 2,539 -> 2,596 (+57, 0 lost: SCHW, WMB 43.1, DASH 51.1, STT, LYV, RVMD, ACGL, L,
THC ...); kept names moved > 1 point: 133 of 2,539 (median 0.04 -- peers' sector ranks shift
as 244 names gain debt inputs); dip gate 587 -> 593; candidates 84 -> 88; validate: only the
known `ttm:392/393` window. Commit bb233ea (fundamentals.py, regression_pins.py), not pushed.

OPEN: GM (business-group debt only); bank funding tags; totals spanning both sides; debt_st
is still ONE alias (WMB's 475M commercial paper is not added to its 2,197M); IR reads
ShortTermBorrowings 0 over DebtCurrent 700M in one filing (alias order, pre-existing);
`_cf_qtrs` and 52/53-week YTDs.

## State at 2026-11-02 (morning) — STOCKS THAT KEEP RUNNING MOVE MORE BOTH WAYS AT SUPPORT; ONLY "WINS BACK ITS DIPS" LEANS UP

**Asked:** is a stock that is often among the daily top gainers more likely to go up at a
key level? Then: frequency, not one huge jump -- names (AI ones) that go up, come down and
go up again through the year. `big_movers_study.py`, design fixed before any result (its
docstring). **Nothing in the screener or the lists changed.**

**Population:** 52 dates, every 42nd session 2018-01-02 .. 2026-07-14 (40-session level
windows never overlap); 69,989 liquid name-dates (close >= $5, 20-day median $20M+),
**41,975 at support** (nearest zone 0-6% below the close; ~800 a date). Outcomes from the
next close: excess vs the same liquidity fifth at 20 / 60 sessions; FAIL = a close <= 90%
of the level within 40 sessions, HIT = a close >= 115%. At-support baseline (per-date
means): excess_20 -0.16pp, FAIL 27.1%, HIT 33.9%. 118 tests carry a t: ~6 expected at
|t| >= 2 by chance.

**Scores -- all count DAYS or separate runs, never size:** `gain_days` (top-20 gainer days
in 252, the 10-23 list's ranking); up/down tilt; `rally_count` (separate 20%+ rallies,
each ended by a 10% pullback: one +200% run = 1); `recovery_rate` (share of 15%+ dips won
back to the prior 63-session high within 63 sessions; >= 2 dips: half the at-support
names); `burst_months` (of 12, months with a top-20 day); `peer_rally_count` (the same
sub-industry). Sanity check on 10-30: AXTI 17 rallies, 8 of 9 dips won back; AMD 6, 3 of
4; MSTR 7, 0 of 3; SMCI 6, 0 of 3; KO 0.

**Top third minus bottom third, per date, t across the 52 dates:**

| score | excess 20 (t) | excess 60 (t) | FAIL top vs bottom | HIT top vs bottom |
|---|---|---|---|---|
| gain_days | +0.29pp (0.47) | +1.11 (1.08) | 41.8% vs 19.0% | 46.3% vs 25.9% |
| rally_count | +0.99 (1.34) | +2.04 (1.58) | 42.9% vs 18.9% | 48.1% vs 25.5% |
| rally_count, volatility-neutral | +0.35 (0.84) | +0.98 (1.36) | +10.0pt (10.7) | +8.5pt (9.6) |
| **recovery_rate** | +1.00 (1.95) | **+2.67 (2.68)** | 39.3% vs 34.9% | 46.1% vs 38.2% |
| recovery_rate, volatility-neutral | +0.67 (1.44) | **+2.08 (2.65)** | **-0.05pt (-0.04)** | +3.4pt (2.42) |
| recovery_rate, momentum-neutral | +0.58 (1.31) | +1.73 (2.58) | +4.2pt (3.81) | +5.8pt (6.25) |
| burst_months top20 (>= 2 up days) | +0.55 (0.75) | +1.69 (1.35) | 47.5% vs 29.0% | 51.9% vs 36.3% |
| gain_days, the same names | +0.43 (0.56) | +1.19 (0.95) | 48.6% vs 28.7% | 51.9% vs 36.3% |
| tilt fifth e63 | +0.27 (0.67) | +0.91 (1.42) | 38.1% vs 32.9% | 43.5% vs 38.2% |
| peer_rally_count | +0.69 (1.46) | +1.59 (2.08) | 33.6% vs 20.6% | 41.3% vs 25.6% |
| volatility 63 (reference) | +1.20 (1.54) | +2.07 (1.59) | 41.5% vs 15.1% | 47.8% vs 20.0% |
| momentum 12-1 (reference) | +0.25 (0.47) | +1.20 (1.57) | 28.8% vs 30.7% | 37.2% vs 36.1% |

**What it means:**
1. **A frequent gainer at support is a volatile stock at support.** It rallies 15% off the
   level far more often and breaks 10% under it far more often (pooled: rally_count 5+
   HIT 57.7% / FAIL 45.6%; 0 rallies 18.7% / 13.6%), and on average earns no more
   (gain_days positive on 26 of 52 dates, median -0.14pp). Rank corr with 63-day
   volatility: rally_count 0.67, gain_days 0.54. What the counts measure reliably
   (t 10-13) is RISK -- wider stops or smaller positions for the busiest names.
2. **Spreading the runs over the year adds nothing:** burst_months and gain_days on the
   same names give the same answer.
3. **recovery_rate is the one asymmetric score** (corr 0.15 with volatility, 0.60 with
   momentum): at equal volatility, more hits and no more breaks; +2.67pp over 60 sessions
   (t 2.52 allowing for the 60-session windows overlapping; +2.67 / +2.66pp in the two
   halves; +2.33pp t 2.31 without 2020; it survives the volatility and the momentum
   neutralising). At 20 sessions +1.00pp, t 1.95 (35 of 52 dates positive) -- just short.
   The walk-forward chose it on pre-2022 dates (t 1.92) and got +0.61pp, t 0.87 after
   2022 -- no better than gain_days there (+0.61, t 0.68); its top third +0.38pp vs the
   plain at-support +0.05pp. **A lead at 60 sessions, not a rule.**
4. The theme score (peer_rally_count) is weak (+1.59pp at 60, t 2.08; today's
   sub-industry map) and the tilt is volatility (vol-neutral FAIL/HIT t 1.96/2.40).

**Proposed, NOT built (the user asked "suggest first"):** show `rally_count` and "dips won
back" as context columns on the Gainers at support list and in each saved list folder,
so the 11-23 review and later ones can split the outcomes by them; judge recovery_rate
FORWARD at 60 sessions on the saved lists before it gets any ranking weight.

Files: `data/_big_movers_study.json` (summary; definitions inside),
`data/_big_movers_events.parquet` (the 69,989 scored name-dates; `--summarise` re-reads
it). The run took 614s (10 min), not the hour first guessed.

### THE BOUNCE TRADE -- the user's actual use: a quick % off the level, not a hold

**Rule** (`big_movers_study.py --trades`, fixed before any trade result, 18s): buy at the
next OPEN; sell at +5% or stop at -5% (a gap fills at that open; a day touching both is
the stop), else exit at the 10th close; no costs. Grid: +/-3% x5 / x10, +/-8% x10, +/-5%
x5 / x20, and +5% with the stop 3% under the level.

**+/-5%, 10 sessions, pooled** (41,975 at-support trades):

| top-20 gainer days, past year | +5% first | -5% first | neither | per trade |
|---|---|---|---|---|
| 15+, at support | 52.0% | 46.3% | 1.7% | +0.36% |
| 5-14, at support | 48.3% | 42.7% | 9.0% | +0.30% |
| 1-4, at support | 40.2% | 33.0% | 26.8% | +0.45% |
| 0, at support | 33.9% | 27.2% | 38.9% | +0.45% |
| 15+, NOT at support | 45.8% | 52.3% | 1.9% | **-0.33%** |
| 5-14, NOT at support | 46.8% | 45.4% | 7.8% | +0.03% |

Per-date baselines: every liquid name +0.42% a trade (40.0% / 33.1%); every at-support
name +0.49% (39.9% / 31.7%); the list rule (<= 10 per sector by gain_days) +0.41% (44.8% /
38.1%, median 3 sessions held).

1. **The level helps HOT stocks.** Hot names (the score's top third among all liquid
   names) at support minus the same-hot names elsewhere: rally_count +0.39pp a trade
   (t 3.57; halves 2.10 / 3.17), burst months +0.42 (3.34), gain_days +0.24 (2.30). For the
   cold third only +0.11 (t 1.07): the difference +0.28pp, t 2.70 (halves 1.93 / 1.85).
   It holds at +/-3% and at +/-5% over 5 or 20 sessions; not at +/-8%.
2. **Hot does not beat calm AT the level:** hot minus cold at support -0.25pp (gain_days
   t -1.38, rally_count -1.27; volatility-neutral -0.10 / +0.05). Hot trades are near
   50/50 and fast (median 2 sessions vs 8; st.dev 5.4% vs 4.4%). Walk-forward: every
   candidate's pre-2022 t was negative (gain_days chosen); after 2022 its hot third made
   +0.05% a trade (46.1% / 46.2%) vs every at-support +0.35%, the list rule +0.23%,
   every liquid +0.29%.
3. **The list rule** makes no more per trade than every at-support name (-0.08pp,
   t -0.99): it wins more (44.8% vs 39.9%) and stops more (38.1% vs 31.7%).
4. Before costs; 2018-2026's drift makes "any liquid stock, any two weeks" +0.42%.

**Next tests to pre-register (not run):** entries AT the level (<= 2.5%) vs NEAR; a
market filter (SPY over its 50-day); skip an earnings date inside the hold; the
level-stop bracket by band. Files: `data/_big_movers_trades.json`, `.parquet`.

## State at 2026-11-02 — JOBS CHECKED, THE APP TASK MADE LATE-PROOF, A 20-DAY REVIEW SCHEDULED

**Every job, checked 2026-11-02 05:10:**
- Windows task `Screener-Orchestrator`: 05:00 daily + every 2 h for 12 h,
  StartWhenAvailable (a missed start runs at wake), runs on battery, one instance, 12 h
  limit; last result 0. Last night: 7 ran, 0 failed (no new session: Monday).
  **Corrected 2026-11-05:** its 23:30 trigger is the one-off from 2026-08-13 (the 10-07 (later)
  section says so), not daily. No run before 02:00 PKT can take the US close anyway, so
  the first run after 02:00 -- 05:00, or the first wake after it -- takes each session;
  evening runs (seen at 19:00-23:00 on 11-04) all stop at "already ran".
- Orchestrator: 35 steps (README lists them from the registry). `news_events` 5th,
  before `bounce`; `watchlists` re-measures saved lists nightly.
- Claude-app task `gainers-at-support-tracking` (copies the watchlist numbers to the
  claude.ai dashboard -- only Claude can write there): was 09:23 Tue-Sat, which the app
  missed whenever it was closed (10-29, 10-30 never fired; 10-31 fired at 17:01). Now
  **every 2 hours 06:23-22:23, every day**, so a late-opened app catches up within 2 h
  or at once (the app runs one catch-up on launch). Most runs stop at
  "VERDICT: stop -- no new session" after one command. Saved in auto mode (the user).
- Claude-app one-time task `gainers-at-support-20-day-review` fires **2026-11-23 10:23**:
  after the 20th session (11-20) it measures the 10-23 list vs the liquid market AND vs a
  plain baseline (every other eligible stock AT/NEAR support on 10-23), writes
  `data/watchlists/gainers_at_support_20261023/evaluation_20.json` and reminds the user
  in plain terms. Its permission mode was NOT saved as auto -- the user may need to
  approve its first steps (or set auto on it in the app).
- Sessions: 5 finished helper sessions and 3 old tracking runs archived.
- **Open:** `fundamentals.py` holds the growth session's uncommitted DEBT-ALIAS change
  (more `debt`/`debt_st` tags; 989 of 3,312 names had no debt figure on 10-30). That
  session (local_04281b25) was asked to verify and commit it, or revert it.

## State at 2026-11-01 (night) — WARNING FILINGS AND NEXT EARNINGS ON EVERY STOCK LIST

Asked for by the user after the news-event study. `news_events.page_flags(asof)` ->
per ticker: WARNING filings (8-K 3.02 unregistered share sale, shown 60 sessions; 4.02
restatement, 20; 1.02 ended material agreement, 20 -- the three types the study found
negative) with the SEC link, and the NEXT EARNINGS date (Finnhub snapshot) with
bmo/amc and sessions away. Every tooltip and legend quotes `_news_events_study_summary.json`
(`flag_help()`), never a typed number.
- **Point in time:** a warning needs its 8-K public by `asof`'s close and inside its
  window; the earnings date comes from the newest calendar snapshot taken no later than
  the NEXT session's date (a Friday page may use the weekend's; the 10-29 page gets none,
  since the first snapshot was taken after 10-30 opened). Snapshots began 2026-11-01.
- **Where:** candidates (red "&#9888; share sale / restated / deal ended" link, amber
  "earnings mm-dd &middot; n" within 10 sessions, grey further out; chips "hide warning
  filings" and "hide earnings <= 10 sessions"), floor, explore (compact map + legend),
  each stock profile (new `events` block after insider: next earnings, warnings, last
  10 filings of 90 days typed by item), and the bounce cards ("filings & earnings" line).
- **10-30 counts:** 462 names with a warning, 3,343 with a next earnings date (2,823
  within 10 sessions -- peak season). Candidates: 2 warnings, 34 earnings within 10.
- **Step order:** `news_events` now runs right after `news` (5th), before `bounce`, so
  every page reads that night's filings and calendar.
- Checked: the page selftests (candidates also asserts no warning dated after the
  session and the field shapes), the three page scripts pass `node --check`, explore's
  tag function run under node, the candidates chips in the browser (84 -> 50 hiding
  earnings, 48 hiding both, clear -> 84), IONQ's profile block read back.

## State at 2026-11-01 (late) — STOCK NEWS EVENTS: 500,430 SEC FILINGS TYPED AND MEASURED; NONE IS A BUY SIGNAL

`news_events.py` (store + nightly step `news_events`) and `news_events_study.py`.
Data in data/news_events/ (local). NOT `events.py`: that name is the weekly severity
calibration -- this session overwrote it by mistake and restored it from git within the
hour; nothing in the nightly chain imported it meanwhile (only the weekly `events` step,
skipped this week, and status.py).

**Sources, each checked before use (2026-11-01).**
- SEC submissions JSON: every filing with `items` (8-K item codes) and
  `acceptanceDateTime` to the second; filings ABOUT a company appear in its own file
  (WBD: 90 x 425, SC TO-T, SC 14D9, DEFM14A, S-4, SCHEDULE 13D). Backfill 2016 -> now:
  5,392 companies read, 0 failed, 22 min, **500,430 filings from 5,039 companies**
  (427,138 8-K; 23,463 425; ~32,000 13D incl. the post-2024 "SCHEDULE 13D" names; 4,912
  S-4; 1,424 tender-offer forms; 773 merger proxies). Nightly: daily index for settled
  days + the latest-filings feed for today (`type=` is a prefix match; read to the end),
  then each named company's file; a day is settled only when every kept filing resolved.
- PR Newswire subject RSS (M&A, FDA approvals) and FDA press releases: 20 items span ~1
  day / ~1 week / ~7 weeks, so the 2-hourly runs miss nothing; newest items only, so
  recorded from today. Tickers from "(NYSE: X)" tags (9 of 20 M&A items); first-word name
  match ONLY in the FDA-approval feed ("Falcon Capital" matched FBYD in the M&A feed).
  GlobeNewswire 403s automated requests -- not bypassed. Business Wire M&A feed: empty.
- Finnhub earnings calendar (free key): upcoming dates, EPS/revenue estimates, bmo/amc;
  a response is CAPPED AT 1,500 ROWS (read one day per call); NO history (July: 0 rows),
  so stored as dated snapshots from today (`upcoming_earnings(asof)` = what was known
  then). First snapshot: 4,266 rows, 10-26..11-27, 682 with actual EPS, 0 capped days.
  A second call batch inside 20 minutes was throttled (40 s -> 5 min); nightly once.

**Study** (`data/_news_events_study_summary.json`): 445,718 filings since 2016-06 ->
607,595 typed events (8-K split by item; earnings also by Benzinga beat/miss from the
stored headlines: 49,796 beat, 25,266 miss of 126,561), 4,997 tickers. Entry at the first
close after acceptance; reaction day measured separately; excess vs the same liquidity
fifth; t across quarters; one episode per ticker per 60 sessions; split halves at
2021-07-01; CHANCE BASELINE = the same stock's own non-event days (5 placebo dates within
a year, 10+ sessions from any event of that type) -> `vs_own`.
- 34 of 128 peer-relative tests reach |t| >= 2 (6.4 expected), but MOST VANISH vs own
  days: frequent 8-K filers (small, financing-heavy) lag their peers regardless of event.
- Earnings: the move is the reaction day (beat +1.60pp, miss -2.81pp); nothing after the
  first close -- no post-earnings drift; a slight bounce after misses (60d vs own +0.94pp,
  t 2.45, halves 1.02/1.89 -- weak).
- Negative, survives vs own: unregistered equity sale (8-K 3.02) 20d -3.44pp vs peers
  (t -5.18), 60d -6.52pp, vs own 60d -3.64pp t -2.87, strongest since 2021 (halves
  -1.43/-8.59); restatement (4.02) next day -0.69pp, vs own t -3.40, both halves (355);
  termination of a material agreement (1.02) 5d vs own -0.57pp t -2.97; 8.01 other events
  20d vs own -0.51pp t -3.12 (second half only); new debt (2.03) 20d vs own -0.26pp.
- delisting notices (+6.1pp 60d vs own) and bankruptcies (13 episodes) are survivorship.
- Latency, earnings: Benzinga's first earnings headline vs the 8-K acceptance on 96,502
  matched filings -- Benzinga first 53.1%, median 40 s earlier, p10 1.4 h earlier (press
  release before the 8-K), p90 38 min later.
NOTHING ENTERS A SCORE. Candidates for the user to decide: a WARNING badge on pages for
a recent 3.02 / 4.02 / 1.02 filing; earnings dates from the calendar shown before entry.

**Validate:** new `news` group (WARN-only): watermark within 4 business days, recent
8-Ks carry items, feeds read within 30 h, calendar snapshot within 3 days.
**Fixed alongside:** the 11 archive tickers from the spin-off work had entered the
screen's panel stats -> `screen: stored run covers the whole panel` FAILED (5,556 of
5,567) in the 16:40 validate; `bars.build_panel_stats` now drops `~` tickers (5,556).

## State at 2026-11-01 (evening) — A FUND SCORE NEEDS TWO OF THE FOUR PILLARS

User decision, after the coverage change below showed 149 new fund scores on the growth
pillar alone: `fund_score` now needs `config.FUND_MIN_PILLARS` = 2 of quality, value, safety,
growth. Asked explicitly: the names below it must not become invisible or stop updating.

WITHHELD, NOT DROPPED -- checked consumer by consumer:
* still computed and stored every night: the `fundamental` step scores the whole tradeable
  universe and writes every row, so each pillar score, every input metric and `fund_cov`
  (0.25 = one pillar) are kept, and the name gains a fund_score the night a second pillar
  lands. Nothing filters the universe on fund_score.
* still shown: Explore's rows are every ticker with any stored metric (pillar scores, combo,
  hype ... and a blank fund_score); profiles are built from bounce flags and on demand; the
  bounce, floor, zones, sector and insider pages select on their own criteria; combo never
  used fund_score (it is in `COMPOSITE`).
* leaves only the lists whose rule IS the fund score: the dip quality gate (`dip_gate` 0,
  still emitted), candidates (fund_score >= 55), and the fundamentals ranking page, which now
  says how many sit below two pillars ("N on fewer than 2 pillars (pillar scores in
  Explore)").

MEASURED, 10-30 re-scored: (11 steps, 0 failed; fundamental 282 s). Population: the 10-30
fundamental/dip rows, stored copy before vs re-score after.

    fund_score   3,043 -> 2,539   504 withheld, 0 gained. Their one pillar: growth 433,
                 quality 62, value 5, safety 4; Financials 252, Biotech 107; 45 non-USD.
                 All 504 still have their rows (34 metrics each), the same pillar scores,
                 fund_cov 0.25 and a dip_gate (0). No kept name's fund_score moved (0 of 2,539).
    dip gate     711 -> 587       117 one-pillar names left; 7 more fell below a top-30% band
                 now drawn from fewer names (EXTR, MCS, MGA, MSA, PLPC, SPGI, WRBY); 0 entered.
    candidates   96 -> 84;  combo 3,011 unchanged;  validate: the same two pre-existing flags.
    page         "2,539 scored · 504 on fewer than 2 pillars (pillar scores in Explore) · 94
                 without enough filings"; Explore lists GM, BNS, BRK.B, CM with their growth
                 scores and a blank fund_score.

Withheld, largest first: BRK.B, TTE, SCHW, IBKR, RIO, PNC, WMB, KKR, DASH, HOOD, GM, AJG, APO,
DLR, AFL, O, STT, AMP, LYV, PRU, NTRS, RJF, ARES, CPRT (growth pillar only), EQNR, MSTR
(quality only). selftest: a one-pillar name keeps its pillar score and fund_cov and gets no
fund_score; two pillars give one.

WHY BIG NAMES ARE AMONG THEM -- A DEBT GAP, NOT A RULE. GM, WMB, DASH, CPRT have a growth
pillar and no other: quality 7/12, value 4/7 (3/7), safety 4/8 (3/8) -- each just under
0.60, and the same in the original 10-30 nightly, so not caused today. The missing inputs all
need DEBT (debt_to_equity, net_debt_ebitda, ev_ebitda, ev_sales, fcf_yield, roic, roic_wacc,
eva, interest_cover): `debt_lt` and `debt_st` are blank in `facts_asof` for all four, while
TAGS knows only LongTermDebtNoncurrent / LongTermDebt / LongtermBorrowings and
LongTermDebtCurrent / ShortTermBorrowings / DebtCurrent. 195 of the 252 non-financial names
withheld lack debt_to_equity. OPEN, proposed as its own session: dump the debt tags these
filers use, add aliases with care (total vs component, leases once; CPRT may truly have
none), re-ingest (WANTED filters at ingest), re-measure.


## State at 2026-11-01 (afternoon) — GROWTH COVERAGE COUNTS ONLY INPUTS THAT CAN EXIST

The user approved the coverage proposal in the 2026-11-01 (small hours) AZN section ("suggestion 2"). The growth
pillar still needs 0.60 coverage, but measured over the inputs that CAN exist for the filer.
`fund_metrics.compute` flags an input not applicable (`_na_<metric>`, never stored) and
`rank_pillars` drops it from the denominator unless a value arrived anyway (provider overlay).
Only the growth pillar carries flags; the other three are unchanged.

    not applicable when                          names excused (of 273 gainers)
    QoQ pair   no report 45-140 days earlier (`_q_ok` false):
               semi-annual filers always, quarterly ones between quarters    162
    CAGR pair  no frame three years back, or a non-positive end
               (a CAGR is undefined)                                  rev 60, eps 119
    gross_margin_chg / op_margin_chg   the line is filed in NEITHER
               period (banks, insurers: no gross profit; LLY: no
               operating income)                                      gross 206, op 111
    never      rev, EPS, FCF, EBITDA, asset, book growth, mom_12_1 -- a gap counts against

A FIRST CUT EXCUSED TOO MUCH, and two measurements caught it before anything was written:
excusing every YoY input whose line was absent in both periods gave +374 with 17 names on 3-4
inputs -- it excused DATA gaps (EPS without a tagged share count: 183 names); and starting the
denominator as a copy of "present" dropped every unflagged missing input from it, so names
scored on 1-2 inputs (+451). Both are pinned by `fund_metrics` selftest cases (THIN: 5 of 13
with the QoQ pair flagged stays blank).

MEASURED, 10-30 re-scored (11 steps; fundamental 328 s). Population: the 10-30 fundamental
and dip rows, stored copy before vs re-score after.

    growth_score   2,644 -> 2,917  (+273, 0 lost); gainers hold 5-7 inputs of 7-11 that can
                   exist (5: 2, 6: 74, 7: 197). Existing scores do not move: only 7 changed by
                   > 0.1, all names whose INPUT changed (DD, HON, XOM, MIDD, FDX, ANAB, REZI --
                   mom_12_1 after 9418128's bar fixes, and one EPS overlay).
    fund_score     2,894 -> 3,043  (+149, 0 lost) -- all 149 on the growth pillar ALONE, see
                   below. Gainers' fund_score: median +0.2 (IQR -5.7..+6.6); others: 3 of
                   2,770 moved > 1 point.
    dip gate       632 -> 711      (+79, 0 left): 59 gained a growth score (LLY, BAC, BP,
                   AEM, PSX, B, AU ...; 21 of them had no fund_score at all before), 20
                   entered on rank shifts as both pools grew.

    LLY  growth 72.5 on 7 of 10   fund 77.3 -> 74.9   fund_rank 98.3, growth_rank 94.7, gate 1
    BAC  growth 52.9 on 7 of 9    fund 66.2 -> 59.5   78.1 / 55.9, gate 1
    BP   growth 57.9 on 7 of 10   fund 57.6 -> 57.8   73.4 / 70.0, gate 1
    AZN  growth 56.6 (unchanged)  fund 67.5           93.5 / 67.1, gate 1

OPEN, FOR A DECISION -- A FUND SCORE ON ONE PILLAR. `fund_score` averages whatever pillars
exist, minimum one. That predates this change -- 284 names had a growth-only fund_score
before it -- and it now reaches 433 (504 on any single pillar). Those 21 gate
entrants have no other pillar (BNS, CM, IBKR, EWBC, RGA, TPG, PJT ...); the gated set on one
pillar went 92 of 632 -> 117 of 711. Requiring two pillars would remove them and the
pre-existing ones alike; not done without a decision.

validate after the re-score: 1 PROBLEM, `ttm:466/467` -- one of the 57 pre-existing
52/53-week windows (previous section). `screen` also reports 1 FAIL that is not this change:
"stored run covers the whole panel: 5,556 of 5,567" -- 11 series with no bounce record, the
11 cuts 9418128 made in the bar store; the bounce step has not run since.

NOT RE-SCORED: sessions before 10-30. The growth_score series steps up on 10-30 (+273 names),
so a factor study across that date sees a population change; `rebuild_history.py` would make
it consistent (heavy -- not run).


## State at 2026-11-01 — SPIN-OFF STEPS AND OTHER COMPANIES' PRICES REMOVED FROM 35 SERIES

Zones were drawn across prices that did not belong to the stock: a parent's history
before a spin-off still carried the business that left (HON -50.9% on 2026-06-29), and a
reused symbol carried the previous company's years (FLY: Fly Leasing to 2021). Fixed in
the bar store, re-applied on every fetch, and checked nightly.

**Reused symbols: 11 cuts (13,763 bars moved, none deleted).** Scan: every liquid name
(1,890; $20M+/day, $5+) for a gap of 20+ sessions inside its stored series -> exactly 9.
Each checked in SEC records: the company on the ticker today first registered AFTER the
old series ended -- CAI, CCXI, FLY, JAN, LIFE, NIQ, WLTH, XE -- or, NBIS, is Yandex
renamed after selling its Russian business and a 2-month break. Two more found among the
spin-offs: XPER and MSGE, spun-off companies that took the parent's old ticker (XPER's
pre-2022-10-03 closes are identical to ADEA's). Bars before `keep_from` move to an
archive ticker (`FLY~2021` ...), kept for studies, and `apply_cuts` drops them from every
fetch. `config.HISTORY_CUTS`, with the evidence per name.

**Spin-offs: 27 adjustments over 24 parents.** Found from the other side, because drop
size alone cannot: 6,748 drops of 12%+ (z <= -4) in liquid names, nearly all earnings
crashes, and some new listing starts on almost every day. So: every Form 10-12B
registrant since 2016 from SEC's quarterly form indexes (739 filings, 242 registrants,
117 first trading inside our history), each information statement read for its parent
and distribution ratio; the seven left blank (Honeywell Aerospace, FedEx Freight, Qnity,
Ralliant, Everus, Sandisk, MSG Rangers) from the parents' own 8-Ks via SEC full-text
search. `factor` = parent / (parent + ratio x spin-off) on the first common session,
from the SOURCE'S RAW prices -- because the source's "split" adjustment treats spin-offs
three ways, measured against `adjustment=raw`:
- already adjusted: CAG (pre-spin x0.7782 vs our 0.7724) -- left alone;
- wrongly scaled as a reverse split: RTX x2.0, CNX x8.0 (raw 16.16, stored 129.28) --
  undone, then adjusted (RTX -71.0% -> +5.2%, CNX -89.6% -> +1.5%);
- untouched: everything else. A real reverse split on the same day (HON 1-for-2, ENOV
  1-for-3) is kept and the ratio taken per post-split share.
Only STEPS are adjusted: a parent move beyond JOIN_MAX_Z (5 sds of its own) at ex. Every
entry removes its step on the stored series (`bars.py --corrections --dry-run`: 26 of
26 inside 5 sds; MSGK's 2020 entry applies once its history is joined). One by hand:
SNX keeps +12.9% (z 5.4), the raw combined value's real move that day (`max_z` 6).
Not adjusted, named: spin-offs inside the parent's normal volatility (ASIX, REZI 2018,
SOLS, KTB, DOW, CTVA, VNT, OGN, SLVM, KD, ONL, EMBC, MBC, GRAL, VSNT, VGNT, MBGL; |z| < 5),
CAG (source already did it), parents with no stored pre-spin history (HWM, BBWI, SPHR),
GTX 2018 (no stored GTX bars then), illiquid parents (TRN, IRWD, XRX, SWBI, SITC, CMPO).
NOT COVERED: spin-offs whose spun-off company no longer trades (125 of the 242 Form 10
registrants have no ticker today) -- their parents were not checked.

**Machinery.** `bars.apply_spinoffs` / `apply_cuts` inside `apply_corrections`, which every
fetch passes through. `python bars.py --corrections [--dry-run]`: refuses while the
nightly holds its lock, backs up the month files it touches (data/_bak/), measures before
writing (a parent with no step at ex is left alone, so a re-run is harmless; a factor
that does not remove the step is refused), moves cuts with `store.drop_rows`, ledger in
data/_corrections_ledger.json. A series too short for a z (MSGK, 9 bars before its
spin-off) is judged by plain size (> 10% = a step). validate `screen`: FAIL if a step or a
cut comes undone; WARN on any liquid name with an undeclared 20+-session gap
(`bars.py --corrections-audit`). Also fixed: `screen.py --only X` crashed when no named
stock reached the stage that sets `passed`.

**Written 2026-11-01 14:48-14:58** (lock free; backup of the 121 month files in
data/_bak/bars_1d_pre_corrections_20261101_1448): 26 spin-offs adjusted, 11 cuts, 48,952
bars written; then MSGK joined (2,542 bars from 2016, join -0.01%), after which both its
spin-off days read +4.4% and +2.6%. verify_corrections, verify_joins: clean; audit: no
undeclared gap. Spot checks: HON 236.15 -> 227.80 across 06-29 (was -50.9%), FDX +0.4%,
RTX +5.2%. Zones recomputed from the corrected bars for 10-30 (stored zone files are
history and were NOT rewritten; the next nightly draws from the corrected store):
FDX 306.83/294.76/284.30/274.17 -> 279.37 only; HON none -> 212.46/204.49/198.89; DD
131.14/123.69 -> 136.01/131.32/127.09/121.71; MSGK none -> 293.51; RTX, GE, FLY, NBIS
unchanged (their old steps lie outside the window the levels come from).

WORKFLOW FOR A NEW SPIN-OFF: add the entry, then run `--corrections` before the next
nightly -- the nightly fetch re-adjusts the last 10 sessions on its own, so an entry left
unwritten across a nightly would leave a recent parent half-adjusted.

**Left as is, named:** the same past bars under two tickers after a join or rename --
EQR/VMRK, PSKY/SKYD, ADIL/AZR, MSGS/MSGK. Scores were stored under the ticker of the day,
so each old ticker's bars still price its own score history; removing them would drop
those observations from studies.

## State at 2026-11-01 (small hours) — AZN'S GROWTH SCORE: A WIDER PILLAR, AND 6-K INTERIMS NEITHER INGEST READ

Asked: why AstraZeneca's growth_score was missing (so the dip gate dropped it at its 10-13
low of 154.00 before +21% to 186.65), who else lost theirs, and the fix. Two causes, one of
each kind. Measured from `data/scores` and the fact store; population named per number.

**1. THE SCORE NEVER "DISAPPEARED" IN OCTOBER -- IT LIVED SEVEN SESSIONS IN AUGUST.**
`data/scores` holds an AZN growth_score only on 08-12..08-20 (55.3-58.8). Those sessions --
08-05..08-20 generally -- were written by code BEFORE f880a66 (08-23), when the growth
pillar had 5 inputs and 3 of 5 cleared FUND_MIN_COVERAGE = 0.60 (rev_growth, asset_growth,
and eps_growth once the provider overlay supplied it on 08-12). Every session from 08-21 on,
and 07-24..08-04, was written after f880a66 widened the pillar to 13 inputs with the same
0.60, i.e. 8 of 13. Under the current code AZN had no growth_score on ANY stored session.
Identity checked on 10-30: growth_score present <=> >= 8 of the 13 inputs stored, 3,311
fundamental rows, 0 exceptions.

WHO LOST IT AT THE SAME BOUNDARY (fundamental rows, session 08-20 vs 08-21): 3,116 -> 2,517
growth scores; **606 lost, 7 gained**. Of the 606: 513 still had >= 3 of the 5 original
inputs (lost to the wider denominator alone), 89 lost inputs (mostly the new year-on-year
guard -- AZN's mechanism, below), 4 had no row. A rule change whose coverage cost was not
measured when it landed; NOT changed here -- proposal below. The brief's suspects (10-14
`_afm`/`_ANNUAL_FORM_RE`, bank revenue, in-filing AGGREGATE_MAX) are not it: 10-09 -> 10-13
lost 60 growth scores (withheld bank revenue), regained on 10-19.

**2. THE DATA REGRESSION: 6-K INTERIMS.** AZN files a 20-F and its half year on a 6-K with
inline XBRL (H1-2026: 0001104659-26-086846, filed 2026-07-27). The bulk ingest
(`fetch_quarter`) and EDGAR (`edgar.FORMS`) accepted only 10-K/10-Q/20-F/40-F, so 6-K rows
reached the store only via companyfacts -- which served AZN's 2022-2025 halves and, on
10-31, still not 2026's (its newest fact there is the 20-F of 02-24). From 2026-07-29 (the
day the year-back frame gained H1-2025) AZN's current frame stopped at FY2025 against a
year-back H1-2025: 184 days, outside `_yoy_ok`'s 270-550, so every YoY input (revenue,
assets, book, both margins) was withheld. The stored rows show the step: 07-28 six growth
inputs, 07-29 one.

SEC's own 2026q3 bulk set HAS the filing (AZN H1-26 revenue 30,672M, Q2 15,384M) and 373
6-Ks from 367 ticker-map filers, each with statement facts we read. On 10-30's frame **111
universe names had their newest period only in a 6-K** (median 181 days behind): AZN, BP,
HSBC, BCS, BBVA, ING, DB, BTI, BUD, BCE, BMO, BNS, CM, FTS, GRAB, MNDY, MT, ALC, FNV, AU, B,
AER ... 71 had a fund_score, 8 a growth_score.

FIX
* `fundamentals.STATEMENT_FORMS` / `statement_form`: ONE form rule for both ingests, now
  with 6-K and 6-K/A; `edgar.FORMS` is that tuple.
* `edgar.FORMS_KEY`: each index day records the form rule it was read under; a day read
  under an older rule is not "settled", so the nightly pass re-reads its lookback instead of
  skipping a day's 6-Ks. Cost: ~100-150 6-Ks a day from ticker-map filers (10-26..10-30),
  nearly all press releases without XBRL -- one index.json each, ledgered `no_xbrl`, never
  fetched again.
* `_ttm`: A COMPONENT CANNOT ROLL FORWARD IN PLACE OF THE TOTAL. Reading 6-Ks exposed it:
  ALVO's 20-F has RevenueFromContractsWithCustomers 586.3M beside RevenueFromSaleOfGoods
  276.3M; its H1-26 6-K tags only the second, so the only complete roll was the component's
  (276.3 + 105.9 - 204.7 = 177.5M) and its later end beat the total. For revenue a roll's
  annual leg must now be the largest alias in its own annual filing; else the annual total
  stands. 1 of 236 revenue rolls on 10-30's frame; 0 of 167 before 6-Ks were read.

DATA
* Bulk store re-ingested `--backfill --years 5 --force --newest-first`: 22 quarters
  (2021q3-2026q3), 0 failed, 15.6 min. Diffed per partition against the old copy: the ONLY
  change is 188,341 6-K rows from 3,163 filings; 0 other rows added or lost.
* `edgar.py --since 2026-10-01`: 2,071 fetched, 47 stored (44 6-K, 3 6-K/A; 3,146 rows),
  2,019 without XBRL, 4 without statements, 1 failed -- LOOP's 10-Q, malformed XML at SEC,
  failing since 10-17 (pre-existing, retried nightly).
* Old copies: `data/fundamentals_pre20261031`, `data/fundamentals_edgar_pre20261031`.
* Facts as of 10-30, before vs after: newest period advanced for 113 names, went back for 0;
  revenue_ttm changed for 93, every one with a period advance. Moves over 25% read from the
  filed legs: IVA (EUR 4.5M -> 0.05M), SLGL (19.4 -> 1.8M), NBTX are real -- 2025 milestone
  revenue that H1-2026 did not repeat; ALVO was the component case above.

VERIFIED
* EDGAR's parse vs the bulk rows for 12 2026q3 6-Ks (AZN, BMO, HSBC, FTS, GRAB, MT, BP,
  TSEM, ICLR, DSGX, BTI, AU): 800 rows, all matched, 0 value diffs (`fp` is blank in the bulk
  set for 3 of them; nothing reads it).
* AZN by hand from the filed legs = pipeline: revenue_ttm 58,739 + 30,672 - 28,045 =
  61,366M; net income 10,233 + 5,589 - 5,369 = 10,453M; assets 115,833M at 06-30. Pinned
  (DATED_PINS @ 2026-10-30). regression_pins 43/43.
* Selftests: `fundamentals._selftest_statement_forms` -- form truth table; `fetch_quarter`
  on an in-memory data set keeps 10-Q/6-K/6-K-A and drops 8-K; AZN's legs through `_ttm`
  (61,366M @ 06-30 with the 6-K, 58,739M @ 2025-12-31 without); ALVO's legs give the 586.3M
  total (177.5M with the guard off). edgar selftest: 6-K index line kept, 8-K dropped, a day
  read under the old rule not settled.

BEFORE/AFTER -- 10-30 re-scored (`orchestrator.py --step fundamental --step dip --step combo
... --step docs`; fundamental 3,973 s, the off-cycle Finnhub refetch). Population: the 10-30
fundamental/dip rows, stored copy before vs re-score after.

    fund_score       2,888 -> 2,894   (+6, 0 lost)
    growth_score     2,602 -> 2,644   (+42, 0 lost) -- all 42 are names whose newest period
                                       arrived in a 6-K; nothing else gained or lost one
    the 111 6-K names  fund_score 71 -> 76, growth_score 8 -> 50, median growth inputs 3 -> 7
    dip gate         616 -> 632       19 entered (AS, ASM, ASND, AZN, DDI, DSGX, FNV, GD, GEF,
                                       IMPP, MNDY, ONON, PBR, SHIP, TFPM, TOYO, UPBD, VIK, WPM),
                                       3 left (DCBO, DLO, FDX -- rank shifts as peers joined)

Gained a growth score: ALC, ALLT, AMBP, AQN, AS, ASM, ASND, AZ, AZN, BIRK, BUD, CLBT, CMRE,
DAC, DDI, DSGX, FNV, FTS, GFS, GILT, GRAB, GSL, IGIC, IHS, IMPP, MNDY, MT, NOMD, NVS, ONON, PBR,
REAX, SHIP, SLGL, SSYS, TATT, TFPM, TOYO, TU, VIK, WPM, ZGN.

**AZN NOW: growth_score 56.6 on 8 of 13 inputs** (rev 8.6%, EPS 17.4%, assets 3.0%, rev
CAGR-3y 11.3%, book 12.4%, gross margin +0.5pp, op margin +2.7pp, mom_12_1 -4.7%) --
exactly the threshold, and it took BOTH fixes: the 6-K takes it from 2 inputs (provider EPS
growth, rev CAGR) to 7, and the eighth, `mom_12_1`, exists only because c5572d7 (10-31)
joined AZN's ADS history to its NYSE line (bars from
2016-01-04, beta 0.31 against -0.47..+0.16 on nine months). fund_score 78.1 -> 67.5 (the
growth pillar now counts), fund_rank 93.0, growth_rank 66.4: `dip_gate` 1, dip_score 19.1 --
at 186.65 it is no longer depressed. Point-in-time history was NOT re-scored: sessions
before 10-30 still hold the old data, so AZN's 10-13 row is unchanged.

### PROPOSED, NOT CHANGED: THE DIP GATE AND A MISSING GROWTH SCORE

Today a missing growth_score fails the gate exactly like a low one: `dip_gate` 0 either way,
which is the conflation the module's own docstring forbids. Measured on 10-30 after the fix:
top-30% fund band 869; gated 632; 161 in the band ranked below the growth cut; **76 in the
band have NO growth score** -- 43 of them one input short (7/13), including LLY, BAC, AEM,
PSX, BP, B, TRI, TFC, DEO, CFG, AER, APA, UNM, FRO, ALLY. Across 08-24..10-30 (stored rows,
before the fix) that group was 84-136 a session.

    A  status quo                         632
    B  missing growth passes on quality   708  (+76, +12.0%; 9 non-USD; Financials 23,
                                                Mining 15, Transport 11; median cap $4.6B
                                                vs $3.6B gated)
    C  passes if >= 3 of the 5 original   687  (+55)
       growth inputs exist

RECOMMENDED: first make the state distinct, which moves no name -- `dip_gate` 0 with label
"no growth score" (or a `growth_known` 0/1 row), so the pages say "not judged on growth"
instead of "did not qualify". Then decide admission UPSTREAM in the coverage rule (next
section): B readmits names whose growth is unknown, which turns the AND of the thesis into a
one-leg gate for 12% of the band, and 17 of the 76 have 1-4 growth inputs. No forward-return
comparison is offered: ~50 overlapping sessions since the pillar changed is not a test.

### PROPOSED, NOT CHANGED: GROWTH-PILLAR COVERAGE -- APPROVED AND BUILT 2026-11-01 (AFTERNOON SECTION, TOP)

0.60 of 13 inputs (8) counts inputs that are ABSENT BY DESIGN as missing. Of the 108
fund-scored names at 7/13 on 10-30: 82 lack BOTH quarter-on-quarter inputs (withheld unless a
new quarter landed 45-140 days after the frame three months back -- never for a semi-annual
filer, and for quarterly filers for part of every quarter; 1,775-2,262 of 2,894 have them),
92 lack `eps_cagr_3y` (defined only when EPS is positive at both ends: 1,113 names). So
whether a name has a growth score drifts with the reporting calendar.

    >= 8 of 13 (now)                    2,644
    >= 7 of 13                          2,752  (+108)
    >= 6 of 13                          2,785
    >= 3 of the 5 original (pre-08-23)  2,788

RECOMMENDED for a decision: measure coverage against the inputs that CAN exist for the filer
-- drop the QoQ pair from the denominator when `_q_ok` is false, `eps_cagr_3y` when either
end's EPS is not positive, the margin changes when no gross profit / operating income is
filed. `fund_metrics.compute` knows each reason; the store does not, so this needs a run to
count. Changing it moves fund_score for every name that gains a growth pillar.

### ALSO FOUND, NOT FIXED
* LLY sits at 7/13 without `op_margin_chg` or `eps_cagr_3y`; PSX, AEM the same pair. Worth a
  (ddate, qtrs, value, tag, rank) dump before calling it structural.
* LOOP's 10-Q 0001437749-26-032746: SEC's instance is malformed XML (line 4578); `error` in
  the EDGAR ledger since 10-17, retried nightly.
* validate after the re-score: 1 PROBLEM, `ttm:392/393`. Not this change: the whole-universe
  check (`ttm_invariants.check`, 10-30) finds the SAME 57 bad 4-quarter windows on the SAME 9
  filers with the old stores and the new -- ACI, AIV, AZO, COST, DPZ, JACK, KR, PEP, RRGB, all
  but AIV 52/53-week calendars whose month-end rounding opens a 120-day "gap". The 6-Ks added
  38 windows (22,572 -> 22,610), all clean. Open.
* The re-score refreshed the Finnhub cache off-cycle (~62 min inside `fundamental`), and
  `candidates` took 813 s rebuilding the wide bar cache after another session's bar cuts.


## State at 2026-10-31 — 81 LIQUID NAMES WERE "SHORT_HISTORY": 65 NEW LISTINGS, 11 NEW TO US EXCHANGES, 5 GAPS IN OUR STORE (4 NOW JOINED)

**The question.** `data/rejects/2026-10-30.parquet`: of 1,890 liquid names (adv_usd >= $20M,
close >= $5), **81** were rejected SHORT_HISTORY, and zones skips them too. Which are new
listings, and which are history we lost? AZN was the example: 189 bars from 2026-02-02.

**Method, per name; three sources, and no cause inferred from a company name:**
- **our store:** first bar, and bars inside the screen's 895-session window;
- **Alpaca**, asked for 2016-01..2026-10 twice: with symbol mapping (what every fetch here
  does; it follows renames back) and with `asof='-'` (the literal symbol);
- **SEC submissions:** ticker -> CIK from `company_tickers.json`, former names, and the
  dated listing forms (424B4, Form 10, S-4, 8-A12B, 40FR12B/F-10/20-F, Form 25);
- **filing text**, for every listing change and every claim of trading elsewhere before.

The full table is at the end of this section.

    65  genuinely new:    46 IPO, 1 carve-out IPO (MMED), 13 spin-offs (Form 10),
                          5 SPAC or merger successors (CCXI, SECZ, INFQ, PURR, SHAZ)
    11  new to US exchanges; their earlier prices are not on SIP:
                          TSX (EMA, ALM, MDA, GLXY; AUGO also B3), B3 (JBS),
                          LSE (WSE, SUNB as Ashtead), OTC (BMNR, CBC),
                          a non-traded REIT's first listing (SMA)
     5  MISSING FROM OUR STORE; the source has the earlier series:
                          AZN, TEVA, VMRK, SKYD -> joined; MSGK -> withheld

**Why the 5 were missing (the root cause, in code).** `bars.update` fetches only a
10-session window, and `bars._backfill_todo` counts a ticker as done once its NEWEST bar is
recent. So a name that enters the universe late keeps those 10 sessions for good: a rename
(EQR -> VMRK on 08-18, PSKY -> SKYD on 10-06, MSGS -> MSGK), or an ADS that became an
ordinary share (TEVA, AZN). Its old history sat in the store under the dead ticker (EQR,
PSKY, MSGS) or nowhere. AZN's case also needs the ADS ratio, below.

**FLY (1,548 bars, still SHORT_HISTORY) is a reused symbol.** 1,237 of its stored bars are
Fly Leasing's (2016-09-01..2021-08-02; acquired in 2021); Firefly listed 2025-08-07. The panel
prefilter counts all 1,548 bars, but the screen reads only its 895-session window, which
holds Firefly's 311. CCXI (ChemoCentryx bars to 2022-10-19) and XE (171 bars from
2016-12/2017-09) are the same. **Not fixed, and it leaks:**
- the zones scan's 6-year window includes Fly Leasing's 2020-21 bars, and FLY's one zone
  (15.99) is drawn from pivots of both companies;
- CCXI is flagged `suspect_split` only because of the 51.99 -> 10.26 jump across its gap.

**What was joined, and how each join was verified** (`config.HISTORY_JOINS`, with the
evidence in each entry):
- **AZN.** The 6-K of 2026-01-20 says the ADSs represented ordinary shares "on a
  two-for-one basis". The ADSs left Nasdaq on 01-30; ordinary shares traded on NYSE from
  02-02. Alpaca's series is UNADJUSTED across that: +103.1% overnight (z +47). Bars before
  02-02 are x2 in price and /2 in volume. The join is then +1.55% (z +1.02), and
  01-30's close is 185.54 = 92.77 x 2.
- **TEVA.** The 8-K of 2026-09-14 says the ADS program ended "with each ADS exchanged for
  one ordinary share". Alpaca maps a NEW entity from 09-14, so the earlier series needs the
  literal symbol. The join is +4.39% (z +1.91, a move seen on 9.4% of its days).
- **VMRK.** Equity Residential renamed 2026-08-12 (same CIK 906107). The joined bars equal
  our stored EQR bars on all 2,502 shared days (max close diff 0.0). Join +0.58% (z +0.44).
- **SKYD.** Paramount Skydance renamed 2026-10-06 (same CIK). The joined bars equal our
  PSKY bars on all 292 shared days. Join -2.51% (z -0.89).
- **MSGK, withheld.** MSG Sports renamed 2026-10-09 (same CIK), and MSGR spun off on
  10-27: -20.5% that day (|z| 14). Pre-spin MSGS prices include the Rangers.
  Split-only adjustment already leaves every parent's spin-off step in the store (below),
  but joining MSGK would add one more and show levels at $380-410 under a $313 stock.

Written: 7,652 bars, 2026-10-31 18:05. Checks:
- **No other ticker's rows changed** in any of the 122 month files, compared with the
  backup at scratchpad `bars_1d_backup_20261031/`.
- **Every bar held before the write is unchanged**; only bars dated before each name's
  first bar were written.
- **No split flags:** `pattern.suspect_split` and `zones.is_suspect` are False on all
  four full series.
- **The largest remaining moves are dated single-day events**, not joins:
  AZN 2017-07-27 -14.9%, TEVA 2017-08-03 -24.0%, VMRK 2020-03-16 -16.9%.

**After.** Bars as of 10-30: AZN, TEVA and VMRK 2,555 each (896 in the screen's window).
They now pass SHORT_HISTORY and stop at NEAR_HIGHS (AZN at 0.877 of its 250-day high).
Zones: AZN 174.60 (6.9% below, 9 touches), 169.76, 164.06; VMRK 60.94 (AT, 20 touches).
TEVA and SKYD show NO SUPPORT within 16%. **AZN's 154 shelf is 17.5% below 186.65, beyond
the 16% band, so it is correctly not listed.** SKYD has 311 bars: it clears
`zones.MIN_BARS` but not the 400 the scan and screen use.

**The code: the store can no longer lose this silently.**
- `bars.py --history [T1,T2] [--dry-run]` is the vetted join. It fetches with the entry's
  `asof` and `factor`, and `join_check` writes nothing when the overlap disagrees with our
  bars (> 1e-4), there is a gap (> 3 sessions), there is a step at the join, or there is a
  step in our first 10 bars (> `JOIN_MAX_Z` = 5 sd). It also checks each declared join at
  its own date, writes only bars older than ours, and records each write in
  `data/_history_joins.json`.
- `JOIN_MAX_Z = 5`, measured on 400 random liquid names (2022-01..2026-10, 451,036 daily
  returns): |z| > 5 on 0.39% of days, so ~0.4% of good joins are wrongly refused and logged.
- `bars.apply_joins` runs inside `fetch_many`, so a forced refetch keeps AZN's x2 instead of
  restoring the ADS prices.
- **Nightly.** `update` now asks the source once for earlier bars for any name whose first
  bar falls inside its window, and joins what passes (`config.HISTORY_AUTO_JOIN`).
  Run end-to-end 18:28 (`--update --force`), 16 names new to the store:
  - **AZR** joined 2,066 bars. It is Adial Pharmaceuticals (CIK 1513525), renamed Azora
    after merging with "Legacy Azora". Our ADIL bars match on all 2,075 shared days. It keeps
    the security's history, as an unchanged ticker would; it does not judge whether the
    business changed.
  - **CTY** refused: a 1,466-session gap, i.e. a reused symbol.
  - **MSGR** and **NORD** refused: too little overlap to compare.
- `bars.py --history-audit`: liquid names under 400 bars, with the source's first bar mapped
  and literal. After the joins it lists only MSGK and MSGR (an unrelated 2021-23 MSGR) as
  mapped-earlier, and FIG, Q, MDA and SPCX as literal-only, which are other companies by
  their filings.
- validate `screen` group: **"history joins hold"**. It FAILS if a declared or ledger join
  loses its history or shows a step. Shown to fail on the real series with AZN put back on
  ADS prices (+103.1%, z +46.9) and with TEVA cut to 10 bars before its join.
  `python bars.py --selftest` proves each refusal.
- `alpaca.fetch_bars(asof=...)` passes Alpaca's symbol-mapping date.

**MIN_BARS: measured, NOT lowered** (`zonelen_study.py`, 408 s,
`data/_zonelen_study.json`).
- Design: 14 dates, 2019-07-01..2026-01-06, 126 sessions apart, so no test windows overlap.
  Names are point-in-time liquid. A zone's first test after the date is the first bar
  reaching within 2.5% above it, within 60 sessions. It FAILED if a close in the next 40
  bars is >= 10% below the level: the zones study's `dd_break_rate` rule, with crashes
  through the level counted.
- Arm A: the same 3,480 mature name-dates with their history cut to L bars, against the
  scan's 6-year window. Arm B: the young listings a lower minimum would actually admit.
- Baseline: random lines 0.5-16% below price, on the same names.

    arm (A: same stocks)   with a zone   scored   fail    vs full (pp, t)
    full 6y window            87.4%       5,648   33.3%
    last 400 bars             75.1%       3,506   33.6%   -0.41  t -0.73
    last 310 bars             70.2%       2,999   33.8%   +0.03  t +0.06
    last 250 bars             64.9%       2,593   33.7%   +0.29  t +0.37
    last 150 bars             48.4%       1,611   33.9%   +0.86  t +0.99
    random lines, mature         --       5,100   39.8%   +3.68  t +2.37
    (B) young, 310-399 bars   56.0%         152   47.4%
    (B) young, 150-399 bars      --         409   52.8%
    random lines on those young names     1,332   53.5%   zones -2.4pp, t -0.71

- **History length is not what makes a zone fail.** Cut to 310 bars, a mature stock's zones
  fail as often as on six years. 96% of them are also full-history zones; fewer are found.
- **A young listing's zones are no better than random lines on that stock** (310-399 bars:
  47.4% vs 51.9%, t -0.57).
- So lowering the scan's 400 (or the screen's) would admit 22 of the 81 (310-399 bars;
  FLY, SECZ and INFQ among them, with borrowed bars), whose zones measure as noise.
  **No threshold changed.**
- `zones.MIN_BARS` = 310 is in practice shadowed by the scan's `n_bars >= 400`; comment added.

**Found, not fixed:**
- **Parents keep their spin-off steps.** `ADJUSTMENT = "split"` does not adjust a
  distribution: HON -50.9% on 2026-06-29 (HONA, z -35.5), FDX -17.8% on 2026-06-01 (FDXF,
  z -8.8). Every level above the step is drawn from a company that no longer exists, and
  `zones.is_suspect` (5% of the 250-day high) does not catch a 51% drop.
- **Reused symbols mix two companies** (FLY, CCXI, XE above). A fix belongs in the read
  path (cut at a 10+ session hole); purging rows would come back on any refetch.
- **`screen.py --only X` crashes** (`KeyError: False`) when no named name reaches the
  stage that sets `passed`: `allrows.get("passed", False)` returns a scalar.
- **MSGK's stored 13 bars include 9 pre-spin bars** (10-14..10-26), with the -20.5% step
  on 10-27.
- **Duplicate histories:** EQR/VMRK, PSKY/SKYD and ADIL/AZR hold the same bars under two
  tickers for past dates, so a by-ticker cross-section counts each company twice (3 of
  ~5,500).
- **SKYD's -23.8% on 2026-10-14** is inside bars we already held, and was not examined.

**The 81** (bars and first bar as the screen saw them on 10-30; "source first" =
Alpaca with symbol mapping, plus the literal symbol where it differs):

| ticker | bars | first bar | source first | cause | evidence |
|---|---:|---|---|---|---|
| AZN | 189 | 2026-02-02 | 2016-01-04 | MISSING -> joined | ADS (Nasdaq) -> ordinary share (NYSE) 2026-02-02; 6-K 2026-01-20: 2 ADS = 1 share; x2 before the join |
| VMRK | 62 | 2026-08-05 | 2016-01-04 (literal 2026-08-18) | MISSING -> joined | Equity Residential renamed 2026-08-12 (same CIK 906107); history stored as EQR |
| TEVA | 45 | 2026-08-28 | 2026-09-14 (literal 2016-01-04) | MISSING -> joined | ADS -> ordinary share 2026-09-14; 8-K 2026-09-14: 1 ADS = 1 share; literal-symbol series |
| SKYD | 28 | 2026-09-23 | 2025-08-07 (literal 2026-10-06) | MISSING -> joined | Paramount Skydance renamed 2026-10-06 (same CIK); history stored as PSKY; 311 bars, still < 400 |
| MSGK | 13 | 2026-10-14 | 2016-01-04 (literal 2026-10-27) | RENAME + SPIN, withheld | MSG Sports renamed 2026-10-09 (same CIK); MSGR spun off 2026-10-27, -20.5% that day (z -14): MSGS history includes the Rangers |
| SMA | 398 | 2025-04-02 | same | FIRST LISTING: non-traded REIT | 10-K since 2014; 424B4 2025-04-02: no public market before |
| BMNR | 354 | 2025-06-05 | same | UPLISTING: OTC | reporting since 2021 (10-K/10-Q); NYSE American from 2025-06-05 with a 424B4; prospectus: 'could revert' to OTC |
| CBC | 237 | 2025-11-20 | same | UPLISTING: OTC | 424B4 2025-11-21: quoted on OTC Markets as CBCY before the offering |
| JBS | 348 | 2025-06-13 | same | VENUE: B3 | JBS N.V. holdco (F-4 2023-07-12) for B3-listed JBS S.A.; NYSE from 2025-06-13 |
| SUNB | 170 | 2026-03-02 | same | VENUE: LSE | Form 10 2026-01-27: LSE was the historical venue of Ashtead's shares; NYSE from 2026-03-02 |
| WSE | 121 | 2026-05-11 | same | VENUE: LSE | 20-F 2026-06-25: primary listing moved from the LSE to Nasdaq 2026-05-11 |
| GLXY | 367 | 2025-05-16 | same | VENUE: TSX | 424B4 2025-05-30: listed on Nasdaq and the TSX; reorganised (S-4) |
| EMA | 360 | 2025-05-28 | same | VENUE: TSX | 40FR12B 2025-05-02 (TSX rules on its listed shares); NYSE from 2025-05-28 |
| ALM | 329 | 2025-07-14 | same | VENUE: TSX | F-10 2025-07-07; TSX-listed; Nasdaq from 2025-07-14 |
| MDA | 162 | 2026-03-12 | same (literal 2017-10-05) | VENUE: TSX | F-10 2026-03-10: common shares listed on the TSX; NYSE from 2026-03-12 |
| AUGO | 327 | 2025-07-16 | same | VENUE: TSX/B3 | 424B1 2025-07-16 quotes its last TSX price |
| SECZ | 377 | 2025-05-02 | same (literal 2026-07-02) | DE-SPAC | S-4 2026-01-28; operating company from 2026-07-02 (literal symbol); the 292 bars before are the SPAC shell's |
| INFQ | 334 | 2025-07-07 | same (literal 2026-02-17) | DE-SPAC | Churchill Capital Corp X renamed 2026-02-18 (same CIK); S-4 2026-01-05; 155 SPAC-shell bars before |
| SHAZ | 178 | 2026-02-18 | same | DE-SPAC HOLDCO | S-4 2025-05-15; the predecessor SPAC was quoted on OTC Markets (USCTF) |
| PURR | 229 | 2025-12-03 | same | MERGER HOLDCO | S-4 2025-09-04; new registrant (first filing 2025-08-01); 8-A12B 2025-12-02 |
| CCXI | 184 | 2026-02-09 | 2016-01-04 | SPAC (itself) | 424B4 2025-12-17 (units); Class A from 2026-02-09; S-4 2026-09-04 for its merger; REUSED SYMBOL: store also holds 1,544 bars of ChemoCentryx, 2016-09-01..2022-10-19 |
| HNGE | 363 | 2025-05-22 | same | IPO | 424B4 2025-05-23; 8-A12B 2025-05-19 |
| CRCL | 354 | 2025-06-05 | same | IPO | 424B4 2025-06-05; 8-A12B 2025-05-27 |
| VOYG | 350 | 2025-06-11 | same | IPO | 424B4 2025-06-12; 8-A12B 2025-06-11 |
| CHYM | 349 | 2025-06-12 | same | IPO | 424B4 2025-06-12; 8-A12B 2025-06-10 |
| SLDE | 345 | 2025-06-18 | same | IPO | 424B4 2025-06-18; 8-A12B 2025-06-17 |
| ARX | 321 | 2025-07-24 | same | IPO | 424B4 2025-07-25; 8-A12B 2025-07-23 |
| AMBQ | 317 | 2025-07-30 | same | IPO | 424B4 2025-07-31; 8-A12B 2025-07-25 |
| FIG | 316 | 2025-07-31 | same (literal 2016-01-04) | IPO | 424B4 2025-07-31; 8-A12B 2025-07-22 |
| FLY | 311 | 2025-08-07 | 2016-01-04 | IPO | 424B4 2025-08-08; 8-A12B 2025-08-06; REUSED SYMBOL: store also holds 1,237 bars of Fly Leasing, 2016-09-01..2021-08-02 |
| WYFI | 311 | 2025-08-07 | same | IPO | 424B4 2025-08-08; 8-A12B 2025-08-04 |
| HTFL | 310 | 2025-08-08 | same | IPO | 424B4 2025-08-08; 8-A12B 2025-08-07 |
| BLSH | 307 | 2025-08-13 | same | IPO | 424B4 2025-08-13; 8-A12B 2025-08-11 |
| MIAX | 306 | 2025-08-14 | same | IPO | 424B4 2025-08-14; 8-A12B 2025-08-13 |
| KLAR | 288 | 2025-09-10 | same | IPO | 424B4 2025-09-10; 8-A12B 2025-09-09 |
| FIGR | 287 | 2025-09-11 | same | IPO | 424B4 2025-09-11; 8-A12B 2025-09-08 |
| LGN | 286 | 2025-09-12 | same | IPO | 424B4 2025-09-15; 8-A12B 2025-09-11 |
| STUB | 283 | 2025-09-17 | same | IPO | 424B4 2025-09-17; 8-A12B 2025-09-12 |
| NTSK | 282 | 2025-09-18 | same | IPO | 424B4 2025-09-18; 8-A12B 2025-09-15 |
| NAVN | 252 | 2025-10-30 | same | IPO | 424B4 2025-10-31; 8-A12B 2025-10-28 |
| BETA | 249 | 2025-11-04 | same | IPO | 424B4 2025-11-04; 8-A12B 2025-11-03 |
| BLLN | 247 | 2025-11-06 | same | IPO | 424B4 2025-11-06; 8-A12B 2025-11-03 |
| MDLN | 219 | 2025-12-17 | same | IPO | 424B4 2025-12-18; 8-A12B 2025-12-17 |
| EQPT | 195 | 2026-01-23 | same | IPO | 424B4 2026-01-26; 8-A12B 2026-01-20 |
| MANE | 187 | 2026-02-04 | same | IPO | 424B4 2026-02-04; 8-A12B 2026-02-03 |
| FPS | 186 | 2026-02-05 | same | IPO | 424B4 2026-02-06; 8-A12B 2026-02-05 |
| MWH | 182 | 2026-02-11 | same | IPO | 424B4 2026-02-11; 8-A12B 2026-02-11 |
| ARXS | 138 | 2026-04-16 | same | IPO | 424B4 2026-04-16; 8-A12B 2026-04-15 |
| MAIR | 138 | 2026-04-16 | same | IPO | 424B4 2026-04-17; 8-A12B 2026-04-16 |
| ALMR | 137 | 2026-04-17 | same | IPO | 424B4 2026-04-17; 8-A12B 2026-04-15 |
| AVEX | 137 | 2026-04-17 | same | IPO | 424B4 2026-04-20; 8-A12B 2026-04-17 |
| XE | 132 | 2026-04-24 | 2016-12-30 | IPO | 424B4 2026-04-27; 8-A12B 2026-04-24; REUSED SYMBOL: store also holds 171 bars of an earlier XE, 2016-12-30..2017-09-05 |
| FRVO | 119 | 2026-05-13 | same | IPO | 424B4 2026-05-14; 8-A12B 2026-05-11 |
| BXDC | 118 | 2026-05-14 | same | IPO | 424B4 2026-05-15; 8-A12B 2026-05-13 |
| CBRS | 118 | 2026-05-14 | same | IPO | 424B4 2026-05-14; 8-A12B 2026-05-11 |
| AADX | 105 | 2026-06-03 | same | IPO | 424B4 2026-06-04; 8-A12B 2026-06-03 |
| QNT | 104 | 2026-06-04 | same | IPO | 424B4 2026-06-05; 8-A12B 2026-06-04 |
| INIO | 104 | 2026-06-04 | same | IPO | 424B4 2026-06-04; 8-A12B 2026-06-04 |
| EROC | 100 | 2026-06-10 | same | IPO | 424B4 2026-06-10; 8-A12B 2026-06-09 |
| PBLS | 100 | 2026-06-10 | same | IPO | 424B4 2026-06-10; 8-A12B 2026-06-05 |
| SPCX | 98 | 2026-06-12 | same (literal 2020-12-16) | IPO | 424B4 2026-06-12; 8-A12B 2026-06-10 |
| DPC | 90 | 2026-06-25 | same | IPO | 424B4 2026-06-26; 8-A12B 2026-06-24 |
| BSP | 86 | 2026-07-01 | same | IPO | 424B4 2026-07-01; 8-A12B 2026-07-01 |
| CSQR | 76 | 2026-07-16 | same | IPO | 424B4 2026-07-16; 8-A12B 2026-07-15 |
| IOND | 68 | 2026-07-28 | same | IPO | 424B4 2026-07-28: no public market before the listing |
| JMKE | 66 | 2026-07-30 | same | IPO | 424B4 2026-07-31; 8-A12B 2026-07-30 |
| BBTX | 1 | 2026-10-30 | same | IPO | 424B4 2026-10-30; 8-A12B 2026-10-27 |
| MMED | 166 | 2026-03-06 | same | IPO (carve-out) | 424B4 2026-03-06, Medtronic carve-out |
| AMRZ | 343 | 2025-06-23 | same | SPIN-OFF | Form 10 2025-02-28 |
| RAL | 338 | 2025-06-30 | same | SPIN-OFF | Form 10 2025-05-05 |
| SOLS | 260 | 2025-10-20 | same (literal 2025-10-30) | SPIN-OFF | Form 10 2025-08-21; first bars are when-issued (regular way from 2025-10-30) |
| Q | 250 | 2025-11-03 | same (literal 2016-01-04) | SPIN-OFF | Form 10 2025-04-24 |
| VSNT | 221 | 2025-12-15 | same (literal 2026-01-05) | SPIN-OFF | Form 10 2025-09-18; first bars are when-issued (regular way from 2026-01-05) |
| VGNT | 148 | 2026-04-01 | same | SPIN-OFF | Form 10 2025-11-14 |
| TRAX | 146 | 2026-04-06 | same (literal 2026-04-20) | SPIN-OFF | Form 10 2026-03-03; first bars are when-issued (regular way from 2026-04-20) |
| OCTV | 113 | 2026-05-21 | same (literal 2026-05-28) | SPIN-OFF | Form 10 2026-02-11; first bars are when-issued (regular way from 2026-05-28) |
| FDXF | 107 | 2026-06-01 | same | SPIN-OFF | Form 10 2026-01-16 |
| HONA | 97 | 2026-06-15 | same (literal 2026-06-29) | SPIN-OFF | Form 10 2026-03-03; first bars are when-issued (regular way from 2026-06-29) |
| MBGL | 86 | 2026-07-01 | same | SPIN-OFF | Form 10 2026-05-07 |
| VYLR | 22 | 2026-10-01 | same | SPIN-OFF | Form 10 2026-06-29 |
| MSGR | 4 | 2026-10-27 | 2021-12-09 | SPIN-OFF | Form 10 2026-08-14; Alpaca also has 343 bars of an unrelated MSGR (2021-12-09..2023-04-21), not stored |


## State at 2026-10-28 — FORM 4s NO LONGER ARRIVE A DAY AND A HALF LATE

The 05:00 `insider` step logged "0 new Form 4 filing(s) over 17 index day(s)" while 341
had been filed on 10-27. SEC publishes day D's daily index at ~22:00 ET = **07:00 here,
two hours after the run**, so a Tuesday filing reached the store on Thursday. `sec_edgar`
solved this for 10-Ks with the latest-filings feed (`edgar.current_feed`); the insider
step never had it.

`insider._feed_form4` now reads that feed for Form 4 / 4/A and adds what is not in the
ledger to the same fetch list as the index rows (`_parse_form4_feed` parses a page; an
index row wins over a feed row for the same accession). Best effort: a failed page ends
the read, and anything missed still comes from the next index -- every run re-reads each
index day from the first uncovered quarter, so feeding a filing early never skips its day.

Measured 2026-10-28, against the index that appeared at 07:00:
- the feed listed exactly the 341 Form 4s that 10-27's index then held (plus 10-26's,
  already stored); paths built from a feed CIK -- issuer or reporting owner -- fetch the
  same file (12/12 sampled).
- **`type=4` is a PREFIX match**: of 3,663 entries, 1,586 were 424B2 prospectuses and
  1,316 Form 4. Each Form 4 is listed once per party, so 638 filings in all. A 25-page
  cap missed 23% of them, so `FEED_PAGES = 60`; the feed ended by itself at 37 pages.
- cost: the step went 9 s -> **28 s** (17 s of it the feed), timeout 1,800 s.
- not covered: Form 4s filed after ~20:00 ET (the run's start) on day D wait for the
  index, as before. Selftest covers the parser (party dedup, 4/A kept, 144 dropped,
  entry without a CIK dropped).

The study and score are unchanged: they measure from the filing date and enter the next
session, so this moves when a buy shows on the pages, not what it predicts.

**The watchlist refresh task stalled on approvals (same day).** Its first scheduled run
(10:31) stopped at its second shell command and never moved, even after an approval, so
the dashboard was refreshed by hand. The task now needs two FIXED commands, so an approval
(or an exact allow rule) covers them for good: `watchlist_track.py --refresh` (waits for a
live orchestrator lock -- a dead writer's lock does not count -- measures, prints one
`VERDICT:` line) and `--mark-uploaded` (writes summary.json's own session to
last_uploaded.txt; no typed date). It no longer deletes the old asset files, since a delete
may always need a person. Approvals given in a run are kept on the task.

**2026-10-31: the measuring moved into the orchestrator.** The Claude scheduled task
fires only while the Claude app is open and its window listening -- the app's log shows
10-28 fired 68 min late, 10-29 (PC asleep 07:19-11:25) and 10-30 (PC and app up) never
fired, and 10-31 fired at 17:01 when the app's scheduler re-initialised. A prompt left
unanswered closed itself after ~5 min and the run still reported "succeeded". So the new
`watchlists` step (after `bars`, before `retention`; `watchlist_page.py`) re-measures every
`data/watchlists/*/track.json` list each night and draws reports/watchlist/ (index + one
page per list; Watchlists in the nav and on the hub). Nothing is lost on a missed night:
the tracker recomputes from the bar store. Only the copy to the claude.ai dashboard still
needs a Claude session (the scheduled task, now auto-approved), and when it runs late it
uploads the latest numbers. Measured: 6.5 s for the 181-stock list.

## State at 2026-10-27 (night) — INSIDER PHASE 3: PAGES, BADGES, A SCORE, AND WHAT THE WALK-FORWARD DID AND DID NOT SHOW

Phase 3 of the insider module (phases 1-2: the section below). Built ONLY on what phase 2
measured; every number on every page is rendered from the study files at build time.

**One derivation, `insider_signals.py`.** `data/insider/_derived.parquet`: every purchase
filing since 2006 (388,903; 312,984 by directors/officers), each with the study's own
features -- believable price, rarity, point-in-time buyer record (37,207 have one), its
own 60-session result. Pages, badges, profiles and the score all read it. Rebuilt by the
`insider_score` step when the Form 4 store is newer: **279 s** measured. To get there,
three of phase 2's own functions were made fast WITHOUT changing a result (each checked
against the original on 95K purchases / 40K filings: 0 mismatches, identical records):
`insider.price_ok` 27 s -> 10 s per 95K (arrays, not per-row pandas), `insider_study.
history_features` 10 min -> ~10 s over the full store (a MultiIndex `.get` per row was
0.7 ms), `event_returns.Panel.measure` (vectorised `entry`). `insider.read(columns=...)`
prunes columns (the 5-year sale read).

**Pages.** `reports/insider/latest.html` (nav "Insiders"): last 90 days of purchase
FILINGS, aggregated client-side so every filter (window 7-90 d, min insiders, min $, role,
10b5-1, sector, market cap, small / rare / good-record / Ross / at-support chips, include
funds & 10%-only owners) changes the counts. Columns: insiders, $, roles, last filed,
rare, best buyer record, liquidity fifth, market cap, Zones support (AT/NEAR/APPROACHING +
distance), fund score, insider score; a row expands to each filing with its SEC link
(built from the accession), price, 10b5-1, rarity and the buyer's record. Beside each
filter: its own t, coloured by a fixed rule (`insider_page.verdict`: "measured to matter"
= t >= 2 overall AND >= 1 in each half). Today that rule names small companies, rare
buyers and CEO/CFO buying; buyers with a good record and unplanned buying are
"significant overall, weak in one half"; Ross's screen reads not significant.
`reports/insider/house.html` (nav "House trades"): 365 days of House trades, $50,001+
stock purchases in green with their measured line, lag, member, owner, amount range,
the Clerk's PDF link; Senate absence and paper filings (per-year counts, live from the
ledger) stated; 5 U.S.C. 13107(c) stated. reports/ is never committed.

**Profiles.** New block "Insider and House trades" (settings block `insider`, after the
radar): chips (30-day buying, rare, small, insider score, House $50K+, the badge), a
24-month $ bought/sold strip (square-root scale, counts in tooltips), the newest 40
purchase + sale filings with SEC links and buyer records, each buyer's record, House trades
with PTR links, and the measured t's read from the files (selling included: not bearish).
Buy / sale / House-buy markers on every chart window, at the FILING date (`ui.price_chart
(markers=...)`). Sales = directors/officers, last 5 years, filings over $500M dropped.
`stock_profile.prime_insider` reads it all once per batch (the profiles step calls it).

**Badge "insider buying at support"** on Candidates, Floor retest and Explore (purple
"insider"; a filter chip on Candidates): a rare OR small-company director/officer purchase
of $10K+ filed in 30 days, or a House $50,001+ purchase filed in 60 days, on a stock now
AT or NEAR a Zones level. Each part was measured; THE COMBINATION WITH SUPPORT WAS NOT
(zones history is kept 90 days, so it cannot be measured yet) -- every tooltip says so.
On 10-26: 45 names carry it -- 5 of the 82 Candidates, none of the 5 Floor names. (`insider_signals.badges`.)

**Score module 6, `scores/insider.py`** (SCORE_MODULES now sentiment, fundamental, hype,
dip, insider, combo). Metrics: buyers_30d, value_30d, rare_buy_30d, small_buy_30d,
ceo_cfo_buy_30d, house_buy_60d, house_big_buy_60d, insider_score. Insider metrics are
BLANK (not 0) for a company with no Form 4 of any kind in 3 years -- a foreign private
issuer files none (266 of 3,467 tradeable names on 10-26). Backfilled every 14 sessions,
2016-09-06..2026-10-26: 183 sessions, 19 min (`catchup_scores.py --modules insider`).
Combo theme `insider` (sub-score `th_insider`, blank while nothing is admitted);
`insider_score` and `th_insider` are in combo's COMPOSITE list.

**What the factor study says** (`study.py --module insider`: 128 cells, 28.7 min, up to 205
dates of the 206 stored: 183 backfilled every 14 sessions + 23 daily ones the first nightly
run filled). Ranked across the WHOLE market, t:

    metric              h1     h5     h20    h60
    buyers_30d        -1.19   0.66   0.23   0.67
    value_30d         -1.21   0.69   0.25   0.71
    rare_buy_30d      -0.92   1.58   0.83   0.35
    small_buy_30d     -1.01  -0.27  -0.88  -0.37
    ceo_cfo_buy_30d   -0.70   1.21   0.15   0.13
    insider_score     -1.06   0.79  -0.03   0.14
    house_buy_60d      2.20   2.17   2.96   2.12
    house_big_buy_60d  0.22   0.16   2.76   3.42

READ IT PLAINLY: no Form 4 metric ranks stocks across the market, so combo admits none of
them. Not a contradiction of phase 2: those effects are against SAME-LIQUIDITY peers in a
few hundred names a month, while a rank IC over ~3,400 stocks (most at 0) compares buyers
with everything -- and small companies trailed the market as a whole. The insider flags
are FILTERS (the pages), not rankers. House: `house_big_buy_60d` is admitted at h20 and
h60. `house_buy_60d` cleared the bar too and was EXCLUDED as a size proxy
(`combo.EXCLUDED`, the `days_since_filing` precedent), measured over 29 sessions: the
median cap of a stock members bought is ~17x the universe median (57x for $50K+ buys),
Spearman +0.23 with mktcap, its t sits in the large tercile, and against same-size peers
the average member purchase is t +1.28. The $50K+ flag stays: it beat same-size peers in
both halves. Effect on combo, 10-26 recomputed with and without: Spearman 1.0 / 0.9998
(h20 / h60), top-50 changes 1 / 0; p90 63.4 -> 61.8 and 64.3 -> 61.7 (a 0/1 metric ranks
~50 for everyone else, so its theme compresses the scale slightly); House-bought names up
to +7 / +13 points. Takes effect at the next combo run (session 10-27); earlier combo
sessions keep the old admission (the known mixed-history issue). `oos.py` NOT re-run.
Pre-insider study table kept: `data/_factor_study_pre_insider_20261027.parquet`.

**The combined score: walk-forward PASS, and why that is weaker than it sounds**
(`insider_score_wf.py`, `data/_insider_score_wf.json`). Design fixed before the test half
was read: flags small / rare / CEO-CFO / good record, 32 "k of subset" rules, chosen on
2016-09..mid-2021 with a 60-session embargo, tested on 2021-07..2026.
    chosen on train: small & rare     train t 4.07 (1,605 episodes, mean +2.51pp, median -1.06)
    test 2021-07..:                   t 2.61, 2,331 episodes, mean +2.04pp, median -0.27, beat peers 49.5%
                                      (20 sessions t 2.63, 40 sessions t 2.01)
    baseline, any $10K+ purchase:     test t 3.70, mean +1.10pp (8,159 episodes)
READ IT PLAINLY: the pre-registered rule says PASS, so `insider_score` (0/1/2 = how many
of small, rare) is emitted -- 28 names score 2 on 10-26. But the test half lifted nearly
everything: 28 of 32 rules reached t >= 2 there (median 2.95) and the chosen one ranks
24th. Its extra +0.94pp over plain insider buying comes with a LOWER t, so the score is NOT
shown to add to "an insider bought". The baseline was added after the first run, as a
reported comparison only (the PASS rule is unchanged); the page and the dictionary say
both. Not blind either: phase 2 had printed each single flag's halves.

**Nightly.** Two steps (33 now): `insider_score` before combo (depends on bars only;
`_with_backfill`: fills missed sessions newest-first) and `insider_pages` after floor
(depends on zones, fundamental). validate's WARN-only `insider` group gained "derived
purchase table current" and "insider_score walk-forward". Hub card "Insiders".
Measured tonight: `insider_score` 551 s on its FIRST run (23 missed sessions backfilled, ~20 s
each; a normal night is the derived rebuild, 279 s, plus seconds); `insider_pages` 12 s
in-process (68 s standalone, where the liquidity fifths load cold); `profiles` 232.7 s for
105 pages, 0 failed (was ~214 s; budget 900). The 22:48 run that did this was the
scheduler's 2-hourly repeat (`pythonw orchestrator.py --wait-for-lock 300`); the factor
study was stopped while it held the lock and rerun afterwards. Page audit: 357 pages clean.
Selftests: insider_page, insider_signals, scores (insider), candidates (badge field),
floor, stock_profile (markers + insider block), metrics_doc, ui, insider, congress.

**One writer at a time, `store_lock.py`** (asked for after 18:49 tonight: a scheduled
`congress` retry -- 437 reports re-read, 1,032 s -- ran WHILE a manual `congress.py
--backfill` wrote the same trades.parquet / _ledger.parquet; every write is tmp+replace so
nothing broke, but each merged into the copy it had read and the last writer won; a full
--reparse later rebuilt the store, 6,979 reports / 41,019 trades). Every writer in
`insider.py` (backfill, ingest_edgar, update) and `congress.py` (ingest, reparse, update)
now holds `<store>/.write.lock` = {pid, started, label}: O_EXCL create, re-entrant in one
process, a gone holder (dead pid, or a pid reused after a restart -- the orchestrator's
`_pid_alive`, now `store_lock.pid_alive`, one copy) is taken over and logged. The nightly
`insider` / `congress` steps wait 60 s, then raise the new `orchestrator.StepSkipped`:
recorded as `skipped` with the holder named, and since only ok/slow rows move a watermark,
the next 2-hourly retry runs it. A manual CLI run waits up to 4 h for the nightly instead.
Checked live: a second process held data/congress/.write.lock and `_step_congress` waited
60 s and skipped, naming it, with the store untouched. `python store_lock.py --selftest`
(re-entry, stale takeover, a live holder in another process, debris).

**Found on the way, NOT fixed (follow-ups):**
* PAM's market cap reads $6.94T in the fundamental store: an ADR's price times the
  ORDINARY share count (25 shares per ADR). Every page that shows mktcap shows it.
* Some foreign issuers report Form 4 prices in their home currency (Bradesco's officers
  at BRL 17.98 against a ~$3 ADR); the Insiders page marks a price far from our close
  with "?", but dollar totals for those filers are in the wrong currency.
* The House parser leaves report boilerplate in some asset descriptions ("F S: New S O:
  ...", "P T R Clerk of the House ..."); tickers are right, descriptions are not.
* Combo's out-of-sample check (oos.py) was not re-run; if an insider metric is admitted,
  combo changes from the next run.

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
_Generated 2026-11-09 02:20 — do not edit by hand._

| step | cadence | last | median | slowest (last 5) | budget | runs |
|---|---|---:|---:|---:|---:|---:|
| `universe` | daily | 1.9 min | 8s | 1.9 min | 2.0 min | 67 |
| `bars` | daily | 80s | 54s | 5.2 min | 10.0 min | 67 |
| `macro` | daily | 2.0 min | 1.6 min | 3.8 min | 15.0 min | 67 |
| `news` | daily | 18s | 5s | 31s | 10.0 min | 67 |
| `news_events` | daily | 4.0 min | 4.0 min | 531.0 min ⚠ | 30.0 min | 9 |
| `senti_cache` | daily | 9s | 3s | 12s | 10.0 min | 67 |
| `sentiment` | daily | 40s | 18s | 40s | 15.0 min | 68 |
| `bounce` | daily | 1.9 min | 41s | 1.9 min | 15.0 min | 68 |
| `zones` | daily | 2.5 min | 1.6 min | 3.1 min | 30.0 min | 51 |
| `shortvol` | daily | 11s | 4s | 11s | 10.0 min | 65 |
| `hype` | daily | 2.7 min | 2.1 min | 3.5 min | 120.0 min | 68 |
| `provider` | daily | 64.5 min | 65.0 min | 64.5 min | 120.0 min | 60 |
| `sec_facts` | quarterly | 6s | 5s | 26s | 60.0 min | 16 |
| `sec_gap` | weekly | 2.9 min | 5.2 min | 12.1 min | 20.0 min | 15 |
| `sec_edgar` | daily | 7s | 37s | 581.1 min ⚠ | 30.0 min | 29 |
| `insider` | daily | 28s | 1.9 min | 3.9 min | 30.0 min | 14 |
| `congress` | daily | 2s | 8s | 11s | 30.0 min | 14 |
| `fundamental` | daily | 3.8 min | 3.7 min | 7.3 min | 180.0 min | 76 |
| `events` | weekly | 8.0 min | 10.6 min | 590.9 min ⚠ | 30.0 min | 16 |
| `leaderboard` | weekly | 36.6 min | 40.0 min | 91.0 min ⚠ | 90.0 min | 17 |
| `dip` | daily | 10s | 17s | 10s | 15.0 min | 76 |
| `insider_score` | daily | 3.9 min | 5.3 min | 7.0 min | 30.0 min | 10 |
| `combo` | daily | 11s | 18s | 11s | 15.0 min | 75 |
| `candidates` | daily | 2s | 5s | 3s | 5.0 min | 33 |
| `floor` | daily | 42s | 53s | 3.0 min | 15.0 min | 27 |
| `insider_pages` | daily | 5s | 7s | 8s | 15.0 min | 10 |
| `sectors` | daily | 70s | 2.6 min | 5.8 min | 30.0 min | 26 |
| `validate` | daily | 9.7 min | 7.1 min | 23.8 min | 60.0 min | 62 |
| `explore` | daily | 7s | 9s | 9s | 5.0 min | 84 |
| `snapshots` | daily | 0s | 4s | 16s | 5.0 min | 98 |
| `profiles` | daily | 5.9 min | 21.3 min | 58.5 min ⚠ | 15.0 min | 79 |
| `watchlists` | daily | 3s | 3s | 5s | 10.0 min | 6 |
| `narrative` | daily | 10s | 10s | 10s | 10.0 min | 1 |
| `shortlist` | daily | 2.3 min | 2.3 min | 2.3 min | 15.0 min | 1 |
| `retention` | daily | 0s | 0s | 0s | 5.0 min | 88 |
| `dashboard` | daily | 1s | 0s | 9s | 2.0 min | 91 |
| `docs` | daily | 1s | 0s | 1s | 5.0 min | 109 |

**Daily total ≈ 123.5 min.** Weekly adds 55.8 min on top. ⚠ marks a step whose slowest run of the last 5 exceeded its budget.
<!-- /GENERATED:costs -->

## Stores (generated)

<!-- GENERATED:stores -->
_Generated 2026-11-09 02:20 — do not edit by hand._

| store | files | MB | span |
|---|---:|---:|---|
| bars 1d | 123 | 251.2 | 2016-09 → 2026-11 |
| bars 1h | 5 | 0.8 | 2026-07 → 2026-11 |
| bars ETF | 125 | 2.9 | 2016-07 → 2026-11 |
| news | 124 | 176.8 | 2016-08 → 2026-11 |
| sentiment cache | 124 | 12.2 | 2016-08 → 2026-11 |
| scores | 123 | 339.0 | 2016-09 → 2026-11 |
| fundamentals | 70 | 345.4 | 2009q2 → 2026q3 |
| short volume | 76 | 57.3 | 2020-08 → 2026-11 |
| flags | 70 | 5.9 | 2026-07-31 → 2026-11-06 |
| rejects | 20 | 7.6 | 2026-10-12 → 2026-11-06 |
| zones | 51 | 28.7 | 2026-08-27 → 2026-11-06 |
| fundamentals (companyfacts) | 131 | 75.0 | 1980q3 → 2028q3 |
| fundamentals (EDGAR filings) | 23 | 1.9 | 2009q4 → 2026q3 |
| loose (macro, universe, jobs, study) | 48 | 312.2 | — |

**`data/` total ≈ 1,617 MB.** `reports/` is a further 137 MB across 412 pages.

Measured bytes per stored row (zstd-9): bars **25.0**, news **91.8**, fundamentals **11.6**, scores **3.2**, short volume **12.1**.
<!-- /GENERATED:stores -->

## Modules (generated)

<!-- GENERATED:modules -->
_Generated 2026-11-09 02:20 — do not edit by hand._

| module | metrics | stored sessions | span |
|---|---:|---:|---|
| `sentiment` | 26 | 396 | 2016-09-27 → 2026-11-06 |
| `fundamental` | 61 | 253 | 2016-09-26 → 2026-11-06 |
| `hype` | 20 | 371 | 2016-10-25 → 2026-11-06 |
| `dip` | 10 | 298 | 2016-09-27 → 2026-11-06 |
| `insider` | 8 | 215 | 2016-09-06 → 2026-11-06 |
| `combo` | 16 | 253 | 2016-11-04 → 2026-11-06 |
<!-- /GENERATED:modules -->

## Study (generated)

<!-- GENERATED:study -->
_Generated 2026-11-09 02:20 — do not edit by hand._

1,984 cells measured across 123 metrics, horizons [1, 5, 20, 60], buckets ['all', 'large', 'mid', 'small'].

**73 metric(s) reach |t| >= 2 at their best horizon.**
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

