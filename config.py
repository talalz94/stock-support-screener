"""
Central settings for the support-bounce screener.

Every tunable lives here. Nothing else in the project should contain a magic
number that you would plausibly want to change.

The pattern being screened for:

    parabolic run-up  ->  full retracement to the pre-breakout base
                      ->  the base holds  ->  price bounces

Calibration reference throughout is RDW (Redwire, NYSE): base ~7.50-8.55
(Feb-Mar 2026), dominant peak 26.64 on 2026-05-28, bottom 7.76 on 2026-07-29,
support line 7.77, bouncing to 10.21 five sessions later. Every threshold below
is annotated with RDW's value where it is meaningful, so a change that would
have excluded the motivating example is immediately obvious.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# ===========================================================================
# Paths
# ===========================================================================
# Resolved from this file, NEVER from cwd. The sibling projects all do
# `open(".env")` and only work when invoked from their own directory; under
# Task Scheduler that is a latent FileNotFoundError waiting to happen.
ROOT = Path(__file__).resolve().parent

DATA = ROOT / "data"
REPORTS = ROOT / "reports"

# ONE SUBDIRECTORY PER REPORT TYPE, one naming convention inside each.
#
# `reports/` had grown four conventions at once -- `explore_<date>.html`
# lowercase beside `Pattern_<date>.html`, `Sentiment_<date>.html` and
# `Fundamental_<date>.html` capitalised, plus three different spellings of
# "latest" (`latest.html`, `sentiment_latest.html`, `fundamental_latest.html`)
# and a stray `bounce_index.html`. 74 files in one flat directory.
#
# Now: every type owns a folder, the current view is always `latest.html`, and
# dated files are `<session>.html` inside that folder. `index.html` and
# `metrics.html` stay at the root because they are the two entry points that
# are not "a report about a session".
REPORTS_EXPLORE = REPORTS / "explore"
REPORTS_BOUNCE = REPORTS / "bounce"
REPORTS_SENTIMENT = REPORTS / "sentiment"
REPORTS_FUNDAMENTAL = REPORTS / "fundamental"
REPORTS_STOCK = REPORTS / "stock"

# Every subdirectory a report writer may create. `dirs()` makes them all.
REPORT_DIRS = (REPORTS_EXPLORE, REPORTS_BOUNCE, REPORTS_SENTIMENT,
               REPORTS_FUNDAMENTAL, REPORTS_STOCK)

BARS = DATA / "bars"                        # bars/1d/YYYY-MM.parquet, bars/1h/...
FLAGS = DATA / "flags"                      # flags/YYYY-MM-DD.parquet  (kept forever)
REJECTS = DATA / "rejects"                  # rejects/YYYY-MM-DD.parquet (30d)

# --- score modules (see scores/__init__.py) --------------------------------
# NEWS holds immutable articles; SENTI holds one score row per article; SCORES is
# the tidy (session, ticker, module, metric, value) table every module writes to.
# Aggregates are NOT stored -- they are recomputed from a window ending at `asof`,
# which is the same slice-once discipline that makes screen_one causal.
NEWS = DATA / "news"                        # news/YYYY-MM.parquet
SENTI = DATA / "senti"                      # senti/YYYY-MM.parquet
SCORES = DATA / "scores"                    # scores/YYYY-MM.parquet
BARS_ETF = DATA / "bars_etf"                # sector + macro proxy ETFs, 1d only
FUNDAMENTALS = DATA / "fundamentals"        # fundamentals/YYYYqQ.parquet (SEC XBRL)
# Per-company XBRL from data.sec.gov/api/xbrl/companyfacts, for filers the bulk
# Financial Statement Data Sets omit. A SEPARATE directory on purpose: a bulk
# refetch rewrites every file under FUNDAMENTALS, so anything stored there
# alongside it would be silently erased on the next refetch.
#
# MEASURED before building it: of 332 universe names with a CIK and no usable
# facts, 330 filed nothing at all in the bulk sets -- but probing 14 of the 187
# real operating companies found 8 fully present in companyfacts, including RY,
# RCI, BIRK and ZGN with 194-353 IFRS concepts each. So the bulk sets
# systematically omit large foreign issuers that this API has in full.
FUNDAMENTALS_CF = DATA / "fundamentals_cf"  # fundamentals_cf/YYYYqQ.parquet
SHORTVOL = DATA / "shortvol"                # FINRA Reg SHO daily short volume

EVENT_SEVERITY_FILE = DATA / "_event_severity.parquet"   # measured |ret|/ATR per event
MACRO_FILE = DATA / "_macro.parquet"                     # FRED + GPR + EPU + breadth
SECTOR_MAP_FILE = DATA / "_sector_map.parquet"           # ticker -> SIC/sector (SEC)
NEWS_STATE_FILE = DATA / "_news_state.json"              # fetch watermark

UNIVERSE_FILE = DATA / "universe.parquet"
ASSETS_RAW_FILE = DATA / "assets_raw.parquet"      # audit trail: why was X excluded?
NASDAQ_FILE = DATA / "nasdaqtraded.parquet"        # cached symbol directory
CALENDAR_FILE = DATA / "_calendar.parquet"
PANEL_STATS_FILE = DATA / "_panel_stats.parquet"   # per-ticker prefilter inputs
FLAG_STATE_FILE = DATA / "_flag_state.parquet"
OUTCOMES_FILE = DATA / "_outcomes.parquet"
# The exit-rule comparison. A ~33-minute run that previously printed and exited,
# leaving nothing to read afterwards -- its one overnight run scrolled its own
# headline table out of the log.
EXIT_RULES_FILE = DATA / "_exit_rules.parquet"
FUNDAMENTALS_FILE = DATA / "_fundamentals.parquet"  # yfinance market cap cache

STATE_FILE = DATA / "_state.json"
LOG_FILE = DATA / "_run.log"
DIGEST_FILE = DATA / "_digest.txt"

# Presence of this file makes the scheduled run a no-op. Same convention as the
# Binance Bot project's `scraper.disabled`.
DISABLED_SENTINEL = ROOT / "screener.disabled"

# ===========================================================================
# Credentials
# ===========================================================================
# Resolution chain:
#   1. real environment variables (nothing to configure for CI / containers)
#   2. this project's own .env               <- the steady state
#   3. the sibling WaveTrend project's .env  <- so step 1 of the build works
#      before any file has been copied. Absolute path, so cwd is irrelevant.
#      Set SIBLING_ENV_PATH in .env to use it; absent, the chain just stops at
#      step 2, which is the normal case for anyone but the original machine.
#      It lives in .env rather than here because an absolute path names a
#      person's home directory, and this file is public.
# ORDER MATTERS: the sibling path is itself configured in .env, so .env has to
# be loaded before it can be read. Computing SIBLING_ENV first would always see
# an empty variable and silently disable step 3.
load_dotenv(ROOT / ".env", override=False)
_sib = os.getenv("SIBLING_ENV_PATH", "").strip()
SIBLING_ENV = Path(_sib) if _sib else None
if not os.getenv("ALPACA_KEY_ID") and SIBLING_ENV and SIBLING_ENV.exists():
    load_dotenv(SIBLING_ENV, override=False)


def creds() -> tuple[str, str]:
    """(key_id, secret). Raises a useful message rather than a KeyError."""
    kid, sec = os.getenv("ALPACA_KEY_ID"), os.getenv("ALPACA_SECRET")
    if not kid or not sec:
        raise RuntimeError(
            "ALPACA_KEY_ID / ALPACA_SECRET not found. Checked (in order): the "
            f"process environment, {ROOT / '.env'}, and {SIBLING_ENV}."
        )
    return kid, sec


# The keys in the sibling .env are PAPER keys: https://api.alpaca.markets/v2/assets
# returns 401 with them, while paper-api returns the identical asset list. Set
# ALPACA_TRADING_BASE in .env if the account is ever upgraded to live.
TRADING_BASE = os.getenv("ALPACA_TRADING_BASE", "https://paper-api.alpaca.markets")
DATA_BASE = "https://data.alpaca.markets"
CALENDAR_BASE = "https://api.alpaca.markets"     # public endpoint, works on either host

# ===========================================================================
# Network behaviour
# ===========================================================================
# Alpaca's free tier allows 200 requests/min (confirmed by the X-Ratelimit-Limit
# response header). 170 leaves headroom for retries.
RATE_LIMIT = 170
RATE_PER = 60.0

FETCH_WORKERS = 8            # I/O-bound, costs no CPU
SCREEN_WORKERS = 2           # CPU-bound. 8-way saves ~9s and pegs every core;
                             # not a good trade on a laptop.
HTTP_TIMEOUT = 45
HTTP_ATTEMPTS = 15

# The `symbols` param accepts a comma-separated list; `limit` caps TOTAL bars per
# page across all symbols, and pagination can resume mid-symbol. Measured:
# 400 symbols x 3y daily = 25 pages, 246,850 bars, 36s. 2,000 symbols works;
# 4,000 returns 504 backend timeout.
BATCH_BACKFILL = 400
BATCH_DAILY = 1000           # 1 bar/ticker: ~6 requests for the whole universe
BATCH_HOURLY = 10            # intraday pages are ~250 bars regardless of `limit`

FEED = "sip"                 # full consolidated volume + history back to ~2016
FEED_FALLBACK = "iex"        # thinner, 2020+, but tolerates a same-day `end`
ADJUSTMENT = "split"         # NOT "all": dividend back-adjustment is look-ahead
                             # NOT "raw": a reverse split becomes a fake +900% bar

# Splits are applied retroactively, so recent stored bars can go stale.
SPLIT_RECHECK_DAYS = 10

# ~2520 sessions. IND_WARMUP + STRUCT_WIN needs 890, so the screener never used
# more than 4y -- this is set for the FACTOR WORK, not the screen. Raised 4 -> 10
# on 2026-08-07 after the bars backfill reached Alpaca's SIP floor (2016-07-28,
# 122 months, 9,772,178 bars, 244 MB).
#
# Four other constants derive from this and all four are intentional:
#   NEWS_HISTORY_YEARS  -> news backfill target widens to 10y (~+85 MB)
#   SCORE_KEEP_YEARS    -> scores.prune() stops trimming to 4y
#   store.prune("1d")   -> a prune now keeps the history just fetched
#   calendar_us.BACK_DAYS
# Lowering it again ARMS store.prune() to delete the backfill. Do not.
HISTORY_YEARS = 10
# Hourly lookback for the shortlist. 30, not 60: the annotations only look at
# behaviour since the bounce low (typically <=15 sessions back), and intraday pages
# are ~250 bars regardless of `limit`, so doubling the window doubles the slowest
# step in the run for no extra information. Measured 95s at 60 days vs ~45s at 30.
CONFIRM_DAYS = 30
CONFIRM_KEEP_DAYS = 120      # hourly retention
REJECT_KEEP_DAYS = 30
# Dated HTML/CSV in reports/. Measured 370 KB per session across the three
# dashboards (bounce 228K + sentiment 72K + fundamental 67K) = ~93 MB/yr, and
# until now NOTHING pruned them -- prune_dated was wired to REJECTS only.
# `latest.html` and the other `*_latest.html` aliases are never dated, so they
# survive any retention setting.
REPORT_KEEP_DAYS = 120

COMPRESSION = "zstd"
COMPRESSION_LEVEL = 9

NASDAQ_TRADED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"

# ===========================================================================
# Universe filters
# ===========================================================================
KEEP_EXCHANGES = {"NYSE", "NASDAQ", "AMEX"}
# ARCA (2,693) and BATS (1,573) are ETF/ETN listing venues -- excluding them
# removes ~4,200 funds for free. OTC (1,133) goes too.

# Alpaca has no instrument-type field but encodes it in the symbol suffix.
DROP_SUFFIX_PREFIXES = (".PR",)              # 363 preferred series
DROP_SUFFIXES = (".WS", ".WSA", ".WSB", ".U", ".RT")   # warrants, units, rights
KEEP_SUFFIXES = (".A", ".B", ".C", ".V")     # legitimate class shares (incl. BRK.A/B)

# NEGATIVE name filter, deliberately not a positive whitelist. A whitelist
# requiring "Common Stock|Ordinary Shares|..." produces ~100 false negatives
# (MTRN "Materion Corporation" with no descriptor; AAPG spelled "Depository"
# not "Depositary"; HAO "Class A Ord Share" abbreviated; SRL no descriptor at
# all) -- ~2% vs ~0.02%. The whole thesis is that this pattern lives in the
# obscure tail, so a 100x worse false-negative rate is the wrong trade.
NAME_KILL_PATTERN = (
    # Non-capturing groups only: pandas .str.contains warns on match groups.
    r"warrant|\bunits?\b|\brights?\b|preferred|\d+(?:\.\d+)?\s*%|\bnotes?\b|"
    r"debenture|\bETN\b|subordinated note|depositary shares|contingent value|"
    r"subscription|when.issued|closed end fund|\bmunicipal\b|"
    r"income (?:fund|trust)|\bbond (?:fund|trust)\b|royalty trust|structured|"
    r"STRATS|floating rate|\bETF\b|index fund|\bportfolio\b"
)

MAX_FAILS = 3                # dead after this many misses in HEALTHY runs
DEAD_RETRY_DAYS = 7
# Stock Screener uses 0.80, valid for the S&P 500 where everything trades daily.
# Here hundreds of legitimately illiquid names print zero bars on a given day
# (verified: BIO.B, BANXR both returned n=0), so 0.80 would be tripped by normal
# market quiet and mass-kill the universe.
HEALTHY_FRACTION = 0.60
HEALTHY_MAX_ERROR_RATE = 0.02   # a name returning no bars != a name that errored

# ===========================================================================
# Liquidity / price / history gates  ("wide" setting)
# ===========================================================================
MIN_PRICE = 1.50             # deliberately low: excluding sub-$5 would delete
                             # the "small price stocks" category entirely
MIN_DOLLAR_VOL = 1_000_000   # 20d median close*volume
MIN_TRADES_20D = 250         # median daily trade count; kills the n=0 zombies
MIN_BARS = 400               # peak + run + base + decline needs the context
IPO_QUARANTINE = 60          # reject if the first bar is this close to the peak:
                             # an IPO pop is not a run off a base (no base exists)

# ===========================================================================
# Detection: windows
# ===========================================================================
IND_WARMUP = 250             # FIXED length. "All available history" makes live
                             # and replayed indicator values differ.
STRUCT_WIN = 640             # MAX_DECLINE + MAX_RUN_LOOKBACK + BASE_WIN + 50
CONFIRM_BARS = 6             # pivots within this many bars of the right edge are
                             # unstable and must not be accepted

# ===========================================================================
# Detection: pivots (volatility-scaled so a 9%-ATR small cap and a 2%-ATR
# utility yield comparable pivot counts)
# ===========================================================================
ATR_PCT_CLIP = (0.015, 0.090)
PROM_MAJOR_K, PROM_MAJOR_FLOOR = 4.0, 0.15   # RDW: 0.36 -> 43%
PROM_MINOR_K, PROM_MINOR_FLOOR = 1.5, 0.05
# Minor pivots are the raw material for support levels, so they must stay DENSE.
# Measured on RDW (atr_pct clipped at 0.090 -> prom_minor 0.135 = 14.5%): only 20
# troughs in 896 bars, which left the genuine 7.77 shelf with a single pivot and
# therefore no cluster at all -- MIN_PIVOTS_PER_LEVEL needs two. Capping at 0.055
# yields ~68 troughs and the shelf forms. Volatility scaling is the right idea for
# identifying the DOMINANT PEAK; for level-building it starves the input on
# exactly the volatile small-caps this screener targets.
PROM_MINOR_MAX = 0.055
DIST_MAJOR, DIST_MINOR = 10, 4
WLEN = 252
DOMINANCE_FRAC = 0.60        # counts as a "major peak" vs the dominant one
SHOULDER_FRAC = 0.35

# ===========================================================================
# Detection: run and base
# ===========================================================================
# MIN_RUN_X = 2.20 is tied to DD_MIN by the pattern's own geometry: a run of X
# that fully retraces implies dd = 1 - 1/X, so X=2.2 -> 0.545. At X=1.5 the
# implied drawdown is only 33%, i.e. "back at the base" degenerates into "at a
# 6-month low" and the screen floods with falling knives. 2.20 is the smallest
# value that forces a >=55% drawdown, i.e. that guarantees a ROUND TRIP.
# RDW: 3.54.
MIN_RUN_X = 2.20
# Vol-normalized secondary gate: log(run_x) / (base_atr_pct * sqrt(run_bars)).
# Its job is to demand MORE than 2.2x from a very volatile name, not to be a
# primary filter -- MIN_RUN_X already does magnitude.
#
# CALIBRATED, not assumed. Measured over the 152 names clearing run_x + retrace:
# p05=0.90 p25=1.24 p50=1.58 p75=2.09 p95=3.14. The originally planned 3.0 would
# have kept 6% of them and rejected RDW itself (run_z 2.00, because its base-window
# ATR is 10% of price, not the 4% the estimate assumed). 1.25 keeps ~74%, trimming
# only the weakest vol-adjusted moves.
MIN_RUN_Z = 1.25
MIN_RUN_BARS, MAX_RUN_BARS = 10, 180         # RDW: ~42
# THE most sensitive parameter in the whole spec. A name that goes 3.5x routinely
# takes a 25-30% intermediate pullback; RDW's post-11.50 dip is 20-26%, so at
# 0.25 the clean segment breaks there and base_lo is corrupted. Log run_dd_max
# for every accepted name and histogram it when tuning.
MAX_RUN_PULLBACK = 0.33
RUN_PULLBACK_RETRY = 0.10    # one retry at MAX_RUN_PULLBACK + this
MAX_RUN_LOOKBACK = 250
BASE_WIN, BASE_TAIL = 40, 10                 # window = [b_lo-30, b_lo+10]
BASE_HI_Q = 0.85
MIN_DECLINE_BARS, MAX_DECLINE_BARS = 25, 300  # RDW: 45-47

# ===========================================================================
# Detection: support levels and touches
# ===========================================================================
LEVEL_TOL_LOG = 0.020        # +/-2.0%. Must be NARROWER than the touch band
                             # (5.5%) so a level is a line and touches are the
                             # band around it. At 1% a real base fragments into
                             # 3 phantom levels each with too few touches.
LEVEL_TOL_MIN, LEVEL_TOL_MAX = 0.015, 0.045
LEVEL_TOL_ATR_K = 0.30       # tol = clip(max(base, K*atr_pct), min, max)
MIN_PIVOTS_PER_LEVEL = 2
PEAK_PIVOT_WEIGHT = 0.7      # a prior low is stronger evidence than a prior high

TOUCH_TOL_UP = 0.025         # low may stop 2.5% above L
TOUCH_TOL_DN = 0.030         # low may pierce 3.0% below L
TOUCH_CLOSE_BREAK = 0.020    # close must be >= L*0.98 -> a touch, not a break
TOUCH_SEP = 8                # bars separating distinct visits (~1.5 weeks)
MIN_PRIOR_TOUCHES = 2        # EXCLUDES the current visit -- see levels.py
REQUIRE_PRE_RUN_TOUCH = True # the "major level" discriminator
STALE_BARS = 504             # a level whose only evidence is >2y old
BASE_PROX = 0.12             # L must be within 12% of base_center

# ===========================================================================
# Detection: retrace gating
# ===========================================================================
DD_MIN = 0.50                              # RDW: 0.703
RETRACE_LO, RETRACE_HI = 0.78, 1.10        # RDW: 0.979
# RETRACE_HI is run-magnitude-dependent (at 3.4x, 1.10 means 12% below base; at
# 1.5x only 2.5%), so the real rejector is the absolute undercut pair below.
UNDERCUT_LOW_MAX = 0.10                    # RDW: -0.019 (never undercut)
UNDERCUT_CLOSE_MAX = 0.04                  # the strict one
# How far the bounce low may sit from the level. Asymmetric: piercing below is
# normal (stop-run), stopping short above means it never quite reached.
LOW_TO_LEVEL = (-0.040, 0.060)
# Candidate SELECTION uses a wider band than the gate above, because cluster
# medians are quantised ~5.5% apart and a 7%-wide selection window can fall
# entirely into the gap between two real shelves. Measured on RDW: the two genuine
# levels were 7.47 and 8.23, and a [7.53, 8.07] window missed both by a hair.
# Selection prefers candidates that also satisfy LOW_TO_LEVEL; the tight gate is
# still enforced at stage 5, so widening this only ever adds candidates.
LEVEL_SELECT_BAND = 0.12
BREAK_CONSEC, BREAK_TOL = 2, 0.020         # 2 consecutive closes < L*0.98
BREAK_HARD = 0.060                         # or any single close < L*0.94
MAX_BARS_SINCE_LOW_BOUNCING = 15           # RDW: 5; NOT WIRED: screen.py uses MAX_BARS_SINCE_LOW
MAX_BARS_SINCE_LOW_TESTING = 25

# ===========================================================================
# Detection: bounce confirmation and extension
# ===========================================================================
MIN_BOUNCE_PCT = 0.02
MIN_BOUNCE_SCORE = 35        # out of 100. RDW: ~85
LOOKBACK_SIG = 6             # bars in which a cross/signal still "counts"
WT_OVERSOLD = -53.0          # sweep.py found os=-53/-70 top-ranked OOS-robust
RSI_CROSS_LEVEL = 38.0       # not 30: high-beta names rarely reach 30 on leg 2

# ATR sampled AT THE LOW, not now -- the bounce prints the widest bars of the
# sequence, so current ATR inflates the denominator 30-60% and makes late
# bounces look early, the exact opposite of the metric's purpose.
ATR_PCT_FLOOR, ATR_PCT_CAP = 0.015, 0.120
EXT_TURNING = 1.00
EXT_CONFIRMED = 1.75
EXT_EXTENDED = 5.00          # RDW: 2.02-4.03 across the whole plausible ATR
EXT_GONE = 8.00              #      range -> CONFIRMED regardless
EXT_PCT_HARD_CAP = 0.80      # loose guard only; % off low is never the primary

SUSPECT_SPLIT_RET = 0.85     # |log return| in one bar
SUSPECT_SPLIT_VOL_X = 3.0    # real news gaps come with >3x median volume;
                             # an unadjusted split comes with normal volume

# ===========================================================================
# Scoring
# ===========================================================================
W_SUPPORT = 20               # level quality Q
W_BOUNCE = 20                # B / 100
W_RETRACE = 12
W_RUN = 12
W_VOLUME = 9                 # V
W_TIGHTNESS = 7              # T
W_STAGE = 6                  # stage_fit
W_LIQUIDITY = 6
W_SIZE = 8                   # market cap. Set to 0 for a pure-signal ranking.
SIZE_BIAS_WEIGHT = 1.0       # multiplier on W_SIZE

MIN_SCORE = 45
SCORE_BANDS = ((75, "STRONG"), (60, "GOOD"), (45, "MARGINAL"))

MAX_FLAGS_REPORTED = 120     # a threshold regression should produce a visibly
                             # truncated report, not a 40 MB unopenable HTML

# ===========================================================================
# Categorisation boundaries
# ===========================================================================
# "small price stocks" = MICRO + LOW. RDW at 10.21 is LOW.
PRICE_TIERS = (
    (5.00, "MICRO"),         # MIN_PRICE .. 4.99
    (15.00, "LOW"),          # 5.00 .. 14.99
    (50.00, "MID"),
    (200.00, "HIGH"),
    (float("inf"), "PREMIUM"),
)

# Sessions since the dominant peak. "old" vs "new" -- RDW at 45-47 is RECENT,
# comfortably mid-band rather than on a boundary where one session would
# reclassify it.
AGE_BANDS = (
    (40, "FRESH"),           # 25 .. 40
    (90, "RECENT"),          # 41 .. 90   <- RDW
    (180, "MATURE"),         # 91 .. 180
    (MAX_DECLINE_BARS, "OLD"),  # 181 .. 300
)

SIZE_TIERS = (
    (300e6, "NANO"),
    (2e9, "MICRO_CAP"),
    (10e9, "SMALL"),
    (50e9, "MID_CAP"),
    (float("inf"), "LARGE"),
)

LIQUIDITY_TIERS = (
    (5e6, "OK"),             # MIN_DOLLAR_VOL .. 5M
    (25e6, "GOOD"),
    (float("inf"), "DEEP"),  # RDW: $143M
)

# Support grade: (min_touches, min_span_days, max_band_width)
SUPPORT_GRADES = (
    ("A", 4, 60, 0.08),
    ("B", 3, 30, 0.12),
    ("C", 2, 0, 0.20),
)

# Report section order.
#
# REVISED from measurement. The original order led with EARLY on the reasoning
# that an un-turned setup has the whole move ahead. The backtest (1,300 flags,
# 2024-02 -> 2026-08, 2-ATR trailing stop) says the opposite:
#
#   PRIME  +3.10% mean (n=706)   CONFIRMED +2.72% (n=857)
#   SPEC   +1.31%      (n=514)   TURNING   +1.27% (n=353)
#   EARLY  -0.72%      (n= 80)   STILL_TESTING +0.23% (n=90)
#
# EARLY is the ONLY bucket with a negative mean, and waiting for confirmation beat
# anticipating the turn. Intuition lost to the data, so the order follows the data.
BUCKET_ORDER = ("PRIME", "SPEC", "WATCH", "EARLY", "LATE")

# ===========================================================================
# Panel prefilter (vectorized, runs before any per-ticker Python)
# ===========================================================================
MAX_PCT_OF_250D_HIGH = 0.65   # cheapest, highest-yield filter. RDW: 0.383
MIN_250D_RANGE_X = 2.0        # RDW: 3.54

# ===========================================================================
# State tracking
# ===========================================================================
PEAK_DRIFT_TOLERANCE = 3      # peak moves more than this -> a NEW setup
COOLED_AFTER_RUNS = 1         # absent this many runs -> 'cooled'
RETIRED_AFTER_RUNS = 5        # absent this many runs -> 'retired'
OUTCOME_TRACK_DAYS = 120
FUNDAMENTALS_TTL_DAYS = 30

MAX_CATCHUP_SESSIONS = 45     # gaps beyond this re-backfill the months instead

EXCHANGE_TV_PREFIX = {"NYSE": "NYSE", "NASDAQ": "NASDAQ", "AMEX": "AMEX"}

# ===========================================================================
# News fetch  (score module 1)
# ===========================================================================
# https://data.alpaca.markets/v1beta1/news -- same keys, same limiter, free tier.
# MEASURED 2026-08-06 against the live API:
#   firehose volume   1,580 articles on 2026-08-04 | 1,145 on 2024-03-05
#                       371 on 2016-06-10  -> coverage grew ~4x since 2016
#   history depth     >= 2015-01-05
#   `symbols=`        3,000 symbols in one param: OK
#   source            'benzinga' on 100% of recent rows ('' pre-2016)
NEWS_URL_PATH = "/v1beta1/news"

# THE binding constraint. Bars allow limit=10,000; news caps at 50, so news is
# ~200x more page-hungry per row. One recent session is ~32 pages.
NEWS_PAGE_LIMIT = 50

# Fetch the FIREHOSE (no `symbols=` filter) and route each article by the
# `symbols` it carries. Measured 90% of articles tag >=1 universe ticker, so
# per-symbol querying would issue thousands of requests to retrieve the same rows.
NEWS_FIREHOSE = True  # NOT WIRED: news.py always uses the per-symbol endpoint

# Article body is 4-10 KB each (measured on ORCL). Storing it would take the
# store from ~120 MB to ~10 GB for no gain: headline+summary is what gets scored.
NEWS_INCLUDE_CONTENT = False

NEWS_HISTORY_YEARS = HISTORY_YEARS    # never fetch news the bar store cannot price
NEWS_BACKFILL_CHUNK_DAYS = 7          # resumable unit; a 403 costs one week, not all

# ===========================================================================
# Session attribution  -- the single biggest look-ahead risk in this module
# ===========================================================================
# An article stamped 21:00Z is 17:00 ET, i.e. AFTER the close. Attributing it to
# that day's session would let the screen "know" tonight's news when scoring
# tonight's close, and it fails SILENTLY -- the backtest simply looks good.
#
# One rule, one chokepoint (news.attribute_session), mirroring how store.write is
# the single partial-bar chokepoint:
#
#     an article belongs to session S iff its timestamp < S's 16:00 ET close;
#     anything later belongs to S+1.
#
# This composes with backtest.py's next-open entry: signal at S's close, entry at
# S+1's open.
NEWS_SESSION_CUTOFF_ET = "16:00"

# Benzinga revises articles in place. Measured drift was 0/50 sampled articles,
# but attributing by the ORIGINAL timestamp when the text you scored is the
# REVISED one is look-ahead, so availability is max(created_at, updated_at).
NEWS_USE_UPDATED_AT = True

# ===========================================================================
# Sentiment scoring
# ===========================================================================
# Two engines, deliberately. Loughran-McDonald is zero-dependency and instant, so
# a complete historical series exists on day one and the backtest is never
# blocked on a slow model. FinBERT is more accurate in the literature (~72-91% vs
# ~50% on financial news) but is a 160 MB dependency and a multi-hour backfill.
#
# BOTH are stored, and which one is primary is a QUESTION FOR THE BACKTEST, not
# an assumption. This project has been wrong twice by assuming (MIN_RUN_Z planned
# at 3.0, measured 1.25; W_SUPPORT weighted highest, measured weakest).
SENTI_ENGINES = ("lm",)               # add "finbert" once step 9 lands; NOT WIRED: only the lexicon path exists; see FinBERT note above
SENTI_PRIMARY = "lm"  # NOT WIRED: single engine, so nothing selects between them

# MEASURED on this machine (i7-10510U, 4c/8t): 137 GFLOPS burst -> 92 GFLOPS
# sustained (15W part, throttles to 67%). Headlines are 14.3 words mean / 21 p90,
# so seq=32 fits. BERT-base = 85M matmul params; Comet Lake has AVX2 but NO VNNI,
# so int8 buys ~2x, not the ~4x quoted for newer chips.
#   per article  ~44 ms fp32  /  ~22 ms int8
#   one 30-min run (~60 articles)   ~1.3 s   <- live scoring is effectively free
#   4-year backfill (~1.3M)         ~8 h     <- the only expensive part
FINBERT_MAX_TOKENS = 32  # NOT WIRED: FinBERT is deliberately deferred
FINBERT_BATCH = 64  # NOT WIRED: FinBERT is deliberately deferred

# Rolling windows for the per-ticker aggregates.
SENTI_WINDOWS = (5, 30, 90)
SENTI_BASELINE_DAYS = 90              # burst z-score baseline, per ticker

# RECENCY WEIGHTING, added ALONGSIDE the flat-mean metrics rather than replacing
# them. A flat 30-session mean treats a story from six weeks ago exactly like
# one from this morning, and with a median of 3 articles per name that is not a
# rounding error -- it is the whole score.
#
# The case that forced it: DPRO read `sent_mean_30d` = 0.4979 built from THREE
# articles, all 25-30 June, all positive, while the price fell on an earnings
# loss the store never saw. Nothing on the page said the newest article was 38
# sessions old.
#
# Half-life of 5 sessions: a one-week-old story keeps ~50% of its weight, a
# month-old one ~1.6%. `sent_mean_*` is untouched, so the study can measure
# whether the weighting actually helps before either is promoted.
SENTI_DECAY_HALFLIFE = 5.0
# Beyond this many sessions since the newest article, the score is STALE and the
# pages say so. Not a cutoff -- the number is still shown, with its age.
SENTI_STALE_SESSIONS = 10

# MEASURED: over 30 days across the 30 live flags, ORCL had 95 articles, the
# MEDIAN flag had 3, and 4/30 had ZERO (SGML, ABAT, DPRO, VUZI). Per-ticker
# sentiment is a sparse signal for exactly the micro-caps this screener surfaces.
# `has_news` is therefore an explicit state -- never impute 0 = neutral, because
# "no news" and "balanced news" are completely different and conflating them
# would put 13% of the list in the middle of the ranking by accident.
SENTI_MIN_ARTICLES = 2                # below this, per-ticker sentiment is untrusted

# "No news" and "no data" are indistinguishable in the output and completely
# different in meaning. Caught live during the build: with only 15 of the last 30
# sessions backfilled, 13 of 30 flags reported has_news=0 -- against 4 of 30
# measured directly from the API. Nothing errored; the module simply reported
# silence for names that were in the news. compute() therefore refuses to score a
# window the news store does not cover, and every run emits `news_coverage` so
# the number is visible rather than assumed.
SENTI_MIN_COVERAGE = 0.90

# Volume grew ~4x since 2016, so ANY raw count feature trends with Benzinga's
# output rather than with the market. Every count metric is ranked
# cross-sectionally WITHIN each date before it is used or compared.
SENTI_RANK_CROSS_SECTIONAL = True  # NOT WIRED: sent_rank is always cross-sectional

# ===========================================================================
# Sentiment screener schedule  (configurable interval)
# ===========================================================================
# Free-tier news carries a ~15-minute delay, so ~15 min is the practical floor.
# Watermark-based with catch-up, matching daily_run.py's gap detection: a closed
# laptop must never leave a hole in the series.
SENTI_INTERVAL_MIN = 30
SENTI_HOURS = ("09:30", "16:00")      # ET; the regular session
SENTI_EXTENDED = False                # True -> ("04:00", "20:00")
SENTI_DISABLED_SENTINEL = ROOT / "sentiment.disabled"

# ===========================================================================
# Macro / sector layer
# ===========================================================================
# MEASURED: per-ticker news is too sparse for micro-caps (median 3 articles/30d),
# so this layer is load-bearing, not decorative -- for most of the list it is the
# only sentiment signal that exists.

# Sector ETFs are EXCLUDED from the universe by the ETF flag, so they are fetched
# explicitly into BARS_ETF. 16 symbols is free next to the 5,383-name universe.
SECTOR_ETFS = ("XLK", "XLE", "XLF", "XLV", "XLI", "XLY", "XLP", "XLU",
               "XLB", "XLRE", "XLC", "SMH", "ITA", "TAN", "LIT", "IBB")

# The transmission channel for macro shocks, and the basket the release-day
# surprise is measured against.
MACRO_PROXIES = ("SPY", "QQQ", "IWM", "TLT", "GLD", "USO", "UUP", "HYG")

# FRED: free key, 120 req/min. ALFRED vintages are the point -- every observation
# carries realtime_start/realtime_end, so what was KNOWN on a past date can be
# reconstructed. These series are revised, so using current values in a backtest
# is look-ahead of exactly the kind replay.py --leaktest exists to catch.
FRED_BASE = "https://api.stlouisfed.org/fred"
FRED_KEY_ENV = "FRED_API_KEY"         # register free at fredaccount.stlouisfed.org
FRED_SERIES = {
    # actuals
    "CPIAUCSL": "cpi", "PPIACO": "ppi", "PAYEMS": "nfp", "UNRATE": "unemployment",
    "PCEPILFE": "core_pce", "GDPC1": "gdp_real",
    # market-implied expectations, daily -- these ARE the forecast, for free
    "T5YIE": "breakeven_5y", "T10YIE": "breakeven_10y", "T5YIFR": "fwd_5y5y",
    "DGS2": "ust_2y", "DGS10": "ust_10y", "T10Y2Y": "curve_10y2y",
    "DFEDTARU": "fed_target_hi", "DFEDTARL": "fed_target_lo",
    # official nowcasts, republished on FRED so no site scraping is needed
    "EXPINF1YR": "exp_inflation_1y", "GDPNOW": "gdpnow", "MICH": "mich_exp_inflation",
    # risk / stress
    "VIXCLS": "vix", "BAMLH0A0HYM2": "hy_spread", "DCOILWTICO": "wti",
    "DTWEXBGS": "dollar", "UMCSENT": "consumer_sentiment",
}
# Releases whose scheduled dates drive `event_proximity`.
FRED_RELEASES = {10: "CPI", 46: "PPI", 50: "Employment Situation",
                 53: "GDP", 21: "PCE", 9: "Retail Sales"}

# Geopolitical + policy uncertainty. Both VERIFIED downloading 2026-08-06 with no
# key. GPR is the Fed Board's own index (Caldara & Iacoviello) and is the
# academic standard for exactly the "Iran war" case; EPU is the policy/political
# channel. GPR needs `xlrd` (~100 KB) because the file is a real OLE2 .xls.
GPR_URL = "https://www.matteoiacoviello.com/gpr_files/data_gpr_daily_recent.xls"
EPU_URL = "https://www.policyuncertainty.com/media/All_Daily_Policy_Data.csv"
# GPR is refreshed monthly (~the 10th) despite being a daily series, so its last
# ~10 days are provisional. EPU verified at 15,191 rows, current to 2026-08-04.
GPR_PROVISIONAL_DAYS = 10

# GDELT is deliberately NOT a dependency. Measured 2026-08-06: it enforces one
# request per 5 seconds and 429'd on 3 of 4 attempts even at 20-second spacing,
# including every `timelinetone` call. Free, but not reliable enough to sit on a
# scheduled path. Left here so the next person does not re-derive this.
GDELT_ENABLED = False  # NOT WIRED: GDELT was evaluated and not built
GDELT_MIN_INTERVAL_S = 5  # NOT WIRED: see GDELT_ENABLED

# SEC EDGAR: free, no key, 10 req/s, User-Agent REQUIRED (they block otherwise).
# Used for the universe-wide sector map (SIC) and for 8-K item codes, which are a
# hard event taxonomy rather than a regex guess.
#
# SET `SEC_USER_AGENT` IN .env, as "Project Name (your@email)". SEC requires a
# real contact address and returns 403 without one, so this is functional
# rather than decorative -- and it is read from .env rather than written here
# because a public repository should not carry anyone's email address.
#
# The fallback below is deliberately NOT a working value: a plausible-looking
# default would let a fresh clone run and be throttled or blocked with no
# obvious cause, which is far harder to diagnose than an empty string.
SEC_UA = os.getenv("SEC_USER_AGENT", "").strip()
if not SEC_UA:
    SEC_UA = "Support Bounce Screener (SET SEC_USER_AGENT IN .env)"
# MEASURED, and the documented limit is not the real one. At 8 req/s the first
# 500 tickers took 237s (0.47 s each) and then throughput collapsed by ~25x --
# 2,500 took 31,583s. SEC advertises 10 req/s but throttles sustained submissions
# traffic well below that, so the full 5,383-ticker map is an OVERNIGHT job, not
# the ~11 minutes the arithmetic suggests.
#
# This is why the map is cached and refreshed monthly rather than daily, why
# build_sector_map skips tickers whose SIC is already known, and why sector is
# treated as a slow-moving attribute where a month-stale value is not an error.
SEC_RATE = 4                          # req/s. Slower start, far less throttling.
SEC_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

# Market breadth, computed from the bars already on disk. No rate limit, no new
# dependency, perfectly historical -- the most reliable macro signal available
# here, which is why it leads the macro layer rather than GDELT.
BREADTH_MA = 50
BREADTH_MIN_TICKERS = 500             # below this the breadth reading is not real

# ===========================================================================
# Score modules
# ===========================================================================
# ORDER MATTERS: `dip` reads fundamental/sentiment/hype, and `combo` reads all
# four, so each must appear after everything it consumes.
SCORE_MODULES = ("sentiment", "fundamental", "hype", "dip", "combo")
SCORE_KEEP_YEARS = HISTORY_YEARS

# ===========================================================================
# Fundamentals  (score module 2)
# ===========================================================================
# SEC Financial Statement Data Sets: bulk quarterly ZIPs, ~85 MB each, no key,
# no rate limit. Chosen over the frames API because frames rows carry no `filed`
# date -- see the fundamentals.py docstring for the measured comparison.
# Raised 6 -> 17 on 2026-08-07: the backfill reached the datasets' own floor,
# 2009q2 (68 quarters, 326.8 MB). This no longer needs to exceed HISTORY_YEARS
# for the TTM/YoY/Piotroski/Beneish warmup -- it is simply the whole archive.
FUNDAMENTALS_YEARS = 17

# A fact is visible on date D iff filed <= D. Never ddate <= D: a quarter ending
# 2024-03-31 is not public until the 10-Q lands in May, and screening on it in
# April is a six-week look-ahead that flatters every fundamental factor.
# A quarter is suspect when it carries far fewer filers than the quarters
# AROUND it -- see fundamentals.filer_shortfall. The absolute floor is only the
# fallback for when there is not enough history to compare against.
#
# A fixed floor alone would be worse than nothing here: nine real quarters sit
# below 2000 (19 filers in 2009q2 rising to 1,620 by 2011q2) because XBRL was
# phased in from the largest filers down. Rejecting those deletes true history;
# the relative test is what separates "the SEC published less" from "our
# download was cut short".
FUNDAMENTALS_MIN_FILERS = 2000
FUNDAMENTALS_MIN_FILER_FRAC = 0.55   # of the 8 nearest quarters' median

# Composite weights. DELIBERATELY EQUAL until factor_lab.py measures which of
# these actually predicts anything. The bounce screener's W_SUPPORT=20 was set
# by intuition and measured the WEAKEST grade -- guessing weights before
# measuring is the mistake this project has already made once.
FUND_WEIGHTS = {"quality": 1.0, "value": 1.0, "safety": 1.0, "growth": 1.0}
FUND_MIN_COVERAGE = 0.60      # fraction of pillar inputs present, else no score

# Winsorise before ranking: XBRL carries genuine 1e12 outliers from unit errors
# and micro-cap equity near zero makes ratios explode. Clipping at the 1/99th
# percentile keeps them in the ranking without letting them define the scale.
FUND_WINSOR = (0.01, 0.99)

# ===========================================================================
# Hype  (score module 3)
# ===========================================================================
# Measures ATTENTION and DETACHMENT, not tone -- the sentiment module already
# owns tone. Built entirely from bars (volume AND trades) plus the fact store,
# so it adds no source, no key and no rate limit. See scores/hype.py.
#
# `hype_score` is a MAGNITUDE, deliberately not a direction. Whether high hype
# predicts continuation or reversal is an open question for factor_lab, and this
# project has twice been wrong by assuming a sign before measuring.
HYPE_MIN_COVERAGE = 0.50      # fraction of composite components present, else no score

# FINRA Reg SHO daily short volume. Short INTEREST (days-to-cover) is no longer
# free -- the biweekly endpoint returns 403 -- so this is short VOLUME, which
# for a daily module is fresher anyway. Measured history floor: 2020-01.
SHORTVOL_YEARS = 6

# ===========================================================================
# Report snapshots
# ===========================================================================
# Dated copies of explore.html so the date picker can reach previous sessions
# with no server at all. Each is ~0.8 MB, and 20 of them was 15 MB to reach 20
# sessions -- while the score store already held 154.
#
# Now that `scores.sessions_stored` is indexed, rendering a session on demand
# costs ~4s instead of ~48s, so serve.py reaches the FULL history for no disk.
# These files exist only for the offline case, so a week is enough: the reachable
# history went 20 -> 154 while the disk went 15 MB -> 4 MB.
SNAPSHOT_SESSIONS = 5

# ===========================================================================
# Dip  (score module 4) -- "strong business, depressed price"
# ===========================================================================
# A GATE then a RANK, never a weighted blend. Averaging "good fundamentals" with
# "big drawdown" lets a collapsing company score highly because the size of the
# fall compensates for the weakness of the business -- which is the exact
# falling knife the thesis exists to avoid. See scores/dip.py.
DIP_QUALITY_PCT = 30          # top N% by fund_score may qualify at all
DIP_REQUIRE_GROWTH = True     # also require growth_score in the top band
DIP_GROWTH_PCT = 50
DIP_MIN_COVERAGE = 0.50       # fraction of depression components present

# ===========================================================================
# Combo  (score module 5) -- three combined scores, one per horizon
# ===========================================================================
# Weights are not set here, deliberately. Every one is derived from
# `data/_factor_study.parquet` at compute time: a metric enters a horizon's
# score only if its cell at THAT horizon clears |t| >= 2 and beats the random
# control, and its sign is the measured sign. If the study has not run, the
# module emits nothing rather than falling back to equal weights.
#
# Three scores rather than one because the measurement says three: sentiment
# peaks at h=1 and is admitted to `combo_h1` but not `combo_h20`;
# `z_score` is strongest at h=60. Measured on 2026-08-07, the three rank the
# universe differently enough to be worth carrying separately -- Spearman 0.52
# to 0.71, with 34-46% of names moving more than 20 percentile points.
#
# See scores/combo.py for the dedup rule and the excluded-with-reason list.
COMBO_MIN_T = 2.0
COMBO_DEDUP_RHO = 0.90        # above this, two metrics are one signal
COMBO_MIN_COVERAGE = 0.50

# ===========================================================================
# Orchestrator  (orchestrator.py -- the one scheduled entry point)
# ===========================================================================
# One job replaces the three independent schedules (daily_run, senti_screen
# --interval, fund_screen --catchup). Every step is a row in JOBS_FILE, so the
# master dashboard reads status from disk and never triggers work itself.
JOBS_FILE = DATA / "_jobs.parquet"        # run_id|step|started|ended|status|rows|error
ORCH_DISABLED_SENTINEL = ROOT / "orchestrator.disabled"
ORCH_LOCK_FILE = DATA / "_orchestrator.lock"   # pid + start; stale locks break open

# Watermark, NEVER wall-clock: a closed laptop must catch up on resume. Mirrors
# daily_run.detect_gap. Beyond this many missed sessions a step should re-backfill
# its months rather than replay them one at a time.
ORCH_MAX_CATCHUP_SESSIONS = MAX_CATCHUP_SESSIONS

# A lock older than this is assumed to belong to a dead process (laptop slept
# mid-run, or a hard kill). Longer than the slowest single step by a wide margin.
ORCH_LOCK_STALE_HOURS = 6

# Per-step wall-clock ceiling. A hung step must not hold the whole run.
ORCH_DEFAULT_TIMEOUT_S = 1800

# Retention for the job history itself. 1 row per step per run, ~14 rows/day.
ORCH_JOBS_KEEP_DAYS = 365

# --- cadences --------------------------------------------------------------
# Measured step costs (2026-08-07, this machine) -- do NOT re-estimate:
#   universe 7s | bars 32s | news ~10s | sentiment cache+score ~15s
#   screen+report ~70s | fund_screen 8s | macro 41s
#   fundamentals quarter 30s | events calibrate 133s | factor_lab ~60s
# Daily total ~3 min; weekly adds ~3 min; quarterly ~30s.
#
# `fundamental` is WEEKLY, not daily, and that is a storage decision as much as a
# compute one: it writes 91,683 rows/session (2,873 tickers x 47 metrics, measured)
# = 486 KB/day = 3.7x the daily bar cost, for metrics that only move when a filing
# lands. factor_lab samples monthly dates anyway -- the leaderboard used 60 of
# them -- so weekly costs no analytical resolution and saves ~120 MB/yr.
# `sentiment` stays daily: its only surviving signal is at h=1.
CADENCE_DAILY = "daily"
CADENCE_WEEKLY = "weekly"          # runs on the first session on/after WEEKLY_DOW
CADENCE_QUARTERLY = "quarterly"
WEEKLY_DOW = 5                     # Saturday; the heavy steps land off-market

def safe_console() -> None:
    """Make stdout/stderr UTF-8 tolerant, surviving the no-console case.

    Under Task Scheduler with pythonw.exe there is no console handle at all, so
    `sys.stdout` can be None -- and `sys.stdout.reconfigure(...)` then raises
    AttributeError before a single line of logging happens. The symptom is a task
    that reports LastTaskResult 1 with a completely empty run log, which is
    maximally unhelpful.

    Note that testing this by launching pythonw.exe from a shell does NOT
    reproduce it: the shell passes down its own stdout handle. Only a real
    scheduled run has no handle.

    print() is already safe (CPython makes it a no-op when sys.stdout is None), so
    guarding the reconfigure is sufficient.
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError, AttributeError):
            pass          # already closed or detached; nothing to do


def dirs() -> None:
    """Create every directory the pipeline writes to. Idempotent."""
    for d in (DATA, REPORTS, BARS / "1d", BARS / "1h", FLAGS, REJECTS,
              NEWS, SENTI, SCORES, BARS_ETF, SHORTVOL, FUNDAMENTALS,
              FUNDAMENTALS_CF, *REPORT_DIRS):
        d.mkdir(parents=True, exist_ok=True)
