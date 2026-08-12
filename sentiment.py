"""
Sentiment scoring: lexicon, event taxonomy, severity. Pure functions.

    python sentiment.py --selftest      fixtures + invariants
    python sentiment.py --bench         ms/article on this machine
    python sentiment.py --score "..."   score one headline, showing the working
    python sentiment.py --survey        what the taxonomy does to the live store

Nothing here touches the network or the store. `score_frame` takes a frame of
headlines and returns a frame of scores, which is what makes the leak tests in
replay.py possible: the same input must produce the same output regardless of
what else exists on disk.

THE TAXONOMY WAS MEASURED, NOT IMAGINED
---------------------------------------
Built from 4,947 stored Benzinga headlines (2026-08). Frequency of the terms that
actually appear, which is not what you would guess:

    Price Target  1,314     Sales    1,509     EPS      1,289
    Q2            1,103     Beats      621     Guidance   437
    Misses          261     Analyst     66     Downgrade   51
    Upgrade          43     Offering    40     Halt        33
    Acquisition      28     FDA         23     Lawsuit     10

Three findings that shaped the design:

1. ANALYST NOTES DOMINATE, and most are non-events. The single most common
   pattern is "<Bank> Maintains <Rating> on <Company>" -- 59 + 41 + 39 + 29 + ...
   hundreds of rows. "Maintains" is a restatement, not news, and it outnumbers
   real rating CHANGES ~15:1 (1,314 price-target mentions vs 94 up/downgrades).
   Lumping them into one ANALYST class would drown the signal in restatements, so
   ANALYST_MAINTAIN is separate and scores near zero.

2. ~2% OF THE CORPUS IS CONTENT-FARM FILLER. "Here's How Much $1000 Invested In X
   Would Be Worth Today" and "If You Invested $100 In..." appear ~90 times in one
   month. They mention a ticker, carry a positive-sounding lexicon, and contain
   zero information. They are classified NOISE and excluded from aggregation
   rather than scored, because a stock that gets three of these in a week would
   otherwise read as a positive sentiment burst.

3. EARNINGS IS THE LARGEST REAL CATEGORY and needs sub-classing. "Beats" (621)
   and "Misses" (261) are separable, and Benzinga's format is regular enough
   ("Q2 Adj. EPS $4.73, Inline, Sales $3.334B Beats") to parse reliably.

WHY NOT THE FULL LOUGHRAN-McDONALD DICTIONARY BY DEFAULT
--------------------------------------------------------
LM was built for 10-K prose and is the right tool there. Headlines are a
different register: "beats", "jumps", "halts", "offering", "dilutive" carry the
signal, and LM misses most of them while flagging boilerplate legal vocabulary
that appears in every filing summary. The embedded lexicon below is
headline-tuned. If you want the real thing, `--fetch-lm` caches the official
master dictionary and it is used in preference when present.

Either way the accuracy question is settled by `backtest.py --sentiment`, not by
the lexicon's pedigree. That is the whole reason both engines are stored.
"""

from __future__ import annotations

import argparse
import re
import sys
import time

import numpy as np
import pandas as pd

import config
import store

# ===========================================================================
# Event taxonomy
# ===========================================================================
# ORDER MATTERS: first match wins, so the most specific pattern must come first.
# NOISE leads deliberately -- the filler templates also match EARNINGS and
# ANALYST patterns ("Here's How Much ... Would Have Made Owning Amkor Stock"),
# and classifying those as real events is precisely the failure mode measured
# above. Direction is a separate axis: `dirn` is -1/0/+1 and only encodes what
# the CLASS implies, with the lexicon refining it.
#
# `prior` is a fallback severity in ATR units, used ONLY until
# data/_event_severity.parquet exists. Real values are measured from the bar
# store by events.calibrate() -- see config.EVENT_SEVERITY_FILE.
EVENTS: list[tuple[str, str, int, float]] = [
    # (name, pattern, direction, prior_severity_atr)
    # NOISE also absorbs TV punditry and the "how to earn" content farm. Measured
    # from the OTHER bucket: 'Live On CNBC, Josh...', 'Halftime Report Final
    # Trades', 'How To Earn $500 A Month From...'. These name a ticker and carry
    # a cheerful lexicon while saying nothing about the company.
    ("NOISE",            r"here'?s how much|if you invested|would have made|"
                         r"would be worth|how much you would|"
                         r"\blive on\b|halftime report|final trades?|"
                         r"how to earn|benzinga (?:pro|edge)|"
                         r"^(?:bitcoin|ethereum|dogecoin)\b", 0, 0.0),
    ("EARNINGS_PREVIEW", r"earnings (?:scheduled|preview|expectations)|"
                         r"to report .*earnings|ahead of earnings", 0, 0.4),
    ("EARNINGS_BEAT",    r"\bbeats?\b(?!.*\bmiss)|tops? (?:estimates|views|consensus)", 1, 1.6),
    ("EARNINGS_MISS",    r"\bmiss(?:es|ed)?\b|falls? short of (?:estimates|views)", -1, 1.8),
    # Up to 3 filler tokens between the verb and the noun, rather than an
    # enumerated period grammar: real headlines interleave "FY2026", "Q3",
    # "Full-Year", "Revenue", "Adj. EPS" in orders no fixed alternation survives.
    ("GUIDANCE_RAISE",   r"(?:raises?|lifts?|boosts?|hikes?|ups)\s+(?:\S+\s+){0,3}?"
                         r"(?:guidance|outlook|forecast)", 1, 2.0),
    ("GUIDANCE_CUT",     r"(?:cuts?|lowers?|slashes|trims?|reduces?)\s+(?:\S+\s+){0,3}?"
                         r"(?:guidance|outlook|forecast)|"
                         r"withdraws? (?:guidance|outlook)", -1, 2.4),
    # Forward-looking colour that is not an explicit raise or cut: 'Conference
    # Call: Calix Sees FY26 Revenue Growth At Higher End Of 15%-20%'. Direction 0
    # on purpose -- 'higher end' vs 'lower end' is exactly what the lexicon reads.
    ("GUIDANCE",         r"\bsees\b\s+(?:fy|q[1-4]|full.year|\d{4})|"
                         r"conference call:|reaffirms? (?:fy|guidance)", 0, 1.6),
    ("EARNINGS",         r"\bq[1-4]\b.*\b(?:eps|sales|revenue)\b|adj\.? eps|"
                         r"reports? (?:q[1-4]|first|second|third|fourth).*(?:results|earnings)", 0, 1.5),
    ("OFFERING",         r"\boffering\b|prices? \$?[\d.]+ ?(?:m|b|million|billion)? (?:common )?"
                         r"stock|public offering|registered direct|\batm\b program|"
                         r"shelf registration|convertible notes? offering", -1, 3.0),
    ("DILUTION",         r"dilut|warrant exercise|share issuance|increases? authorized shares", -1, 2.5),
    ("BANKRUPTCY",       r"bankrupt|chapter 11|chapter 7|going concern|insolven|receivership", -1, 5.0),
    ("DELISTING",        r"delist|deficiency (?:letter|notice)|non.?compliance with.*listing|"
                         r"reverse split", -1, 3.5),
    ("SHORT_REPORT",     r"short (?:report|seller)|hindenburg|muddy waters|citron|"
                         r"fuzzy panda|scorpion capital|grizzly research", -1, 4.0),
    ("MA",               r"\bacquir|\bmerger\b|\bmerges?\b|to be acquired|takeover|"
                         r"buyout|definitive agreement to (?:acquire|merge)|\bLBO\b", 1, 3.5),
    ("FDA",              r"\bfda\b|phase [123]|clinical (?:trial|data)|topline|"
                         r"\bpdufa\b|breakthrough therapy|orphan drug|\bnda\b|\bbla\b|ind clearance", 0, 3.5),
    ("HALT",             r"trading halt|halted|resumes? trading|circuit breaker", 0, 3.0),
    ("LAWSUIT",          r"lawsuit|class action|\bsues?\b|litigation|settlement|"
                         r"investigation|subpoena|\bsec charges?\b|doj", -1, 2.0),
    ("INDEX_ADD",        r"(?:join|add(?:ed)?|inclusion).{0,20}(?:s&p|russell|nasdaq.100|index)", 1, 2.5),
    ("CONTRACT",         r"\bcontract\b|\bawarded\b|\bwins?\b .{0,24}(?:deal|order|award)|"
                         r"purchase order|task order|selected by", 1, 2.0),
    ("PARTNERSHIP",      r"partnership|collaborat|joint venture|strategic (?:alliance|investment)|"
                         r"teams? up with", 1, 1.5),
    ("BUYBACK",          r"buyback|repurchase program|tender offer", 1, 1.5),
    ("DIVIDEND",         r"dividend|distribution declared", 0, 0.8),
    ("INSIDER",          r"insider (?:buy|sell|purchase)|\bform 4\b|"
                         r"(?:ceo|cfo|director) (?:buys?|sells?)", 0, 1.2),
    ("EXEC_CHANGE",      r"(?:appoints?|names?|steps? down|resigns?|departs?|"
                         r"to retire).{0,30}(?:ceo|cfo|coo|president|chairman)|"
                         r"leadership transition", 0, 1.8),
    # RESTATEMENTS FIRST, and the ordering is the whole point. "Wells Fargo
    # Maintains Overweight on Oracle, Raises Price Target to $180" is NOT an
    # upgrade -- the rating did not move, only the target did. Measured, these
    # outnumber real rating changes ~15:1 (1,314 price-target mentions vs 94
    # up/downgrades), so letting the trailing "Raises Price Target" clause win
    # would relabel most of the corpus as rating changes.
    #
    # Direction is not lost by doing this: ANALYST_MAINTAIN has dir 0, so
    # combined_score falls through to the lexicon, where "raises"/"lowers" carry
    # the sign. The class sets the (low) severity, the lexicon sets the polarity.
    ("ANALYST_MAINTAIN", r"maintains?|reiterat|reaffirm|initiates? coverage|assumes? coverage", 0, 0.5),
    # Real rating CHANGES; see finding #1.
    ("ANALYST_UP",       r"upgrade|raises? price target|\bpt raised\b", 1, 1.4),
    ("ANALYST_DOWN",     r"downgrade|(?:cuts?|lowers?) price target|\bpt (?:cut|lowered)\b", -1, 1.6),
    # 'Why Is X Stock Falling On Friday?' and "What's Going On With X" are the
    # two most common shapes in the OTHER bucket (~83 in one month). They are
    # price-explanation stories, i.e. MOVER, and the original "shares jump"
    # phrasing missed all of them.
    ("MOVER",            r"shares? (?:jump|surge|soar|spike|plunge|tumble|slide|sink|fall|rise)|"
                         r"trading (?:higher|lower)|moving (?:higher|lower)|"
                         r"stocks? moving|why .* shares are|"
                         r"what'?s going on with|why is .* stock|"
                         r"stock (?:of the day|is (?:up|down)|rises|falls)", 0, 1.0),
    # Analyst ROUNDUPS: 'These Analysts Revise Their Forecasts', 'Top Wall Street
    # Forecasters'. Multi-name and low per-ticker relevance, so they are their own
    # class rather than being counted as a rating action on every name mentioned.
    ("ANALYST_ROUNDUP",  r"these analysts|top wall street forecasters|"
                         r"analysts? (?:revise|increase|slash|adjust)", 0, 0.6),
    ("RECALL",           r"\brecalls?\b .{0,40}(?:vehicle|unit|product|lot)|"
                         r"issues? (?:a )?recall|safety recall|product recall", -1, 2.2),
    # Economic indicator prints, not company news. Measured in OTHER: 'Redbook
    # Retail Sales Index', 'Texas Services Sector Outlook', 'Leading And Lagging
    # Sectors', 'Market-Moving News for July'.
    ("MACRO",            r"\bfed\b|fomc|federal reserve|inflation|\bcpi\b|\bppi\b|jobs report|"
                         r"tariff|interest rates?|treasury yield|s&p 500|dow (?:jones|futures)|"
                         r"nasdaq composite|oil price|opec|"
                         r"leading and lagging sectors|market.moving news|"
                         r"\b(?:pmi|ism|redbook|nonfarm|payrolls?|jobless claims)\b|"
                         r"(?:sector|business|consumer|manufacturing) (?:outlook|sentiment|index)|"
                         r"\bgdp\b|retail sales index", 0, 0.8),
    ("OPTIONS",          r"options? (?:activity|trade)|whale|unusual options", 0, 0.6),
]

_EVENT_RE = [(name, re.compile(pat, re.I), dirn, prior) for name, pat, dirn, prior in EVENTS]

EVENT_NAMES = [e[0] for e in EVENTS] + ["OTHER"]

# Classes that carry no information about the company and must never contribute
# to a per-ticker aggregate. NOISE is filler; MACRO is real but is about the
# market, not the name, and is routed to the macro layer instead.
NON_COMPANY = frozenset({"NOISE", "MACRO"})


def classify(headline: str) -> tuple[str, int, float]:
    """(event_type, direction, prior_severity_atr). First match wins."""
    h = headline or ""
    for name, rx, dirn, prior in _EVENT_RE:
        if rx.search(h):
            return name, dirn, prior
    return "OTHER", 0, 0.8


# ===========================================================================
# Lexicon
# ===========================================================================
# Headline-tuned, deliberately. Weights are magnitudes, not probabilities: a
# headline is scored by summing hits and squashing, so relative weight is what
# matters. Multi-word keys are matched as phrases before single tokens.
LEX_POS = {
    "beat": 2.0, "beats": 2.0, "tops": 1.8, "surges": 2.2, "surge": 2.0,
    "soars": 2.4, "soar": 2.2, "jumps": 2.0, "jump": 1.8, "spikes": 2.0,
    "rallies": 1.8, "rally": 1.5, "climbs": 1.4, "gains": 1.3, "rises": 1.2,
    "record": 1.6, "strong": 1.5, "robust": 1.5, "upgrade": 2.0, "upgrades": 2.0,
    "raises": 1.8, "boosts": 1.8, "lifts": 1.6, "outperform": 1.5, "buy": 1.0,
    "approval": 2.5, "approved": 2.5, "wins": 2.0, "awarded": 2.0, "secures": 1.8,
    "expands": 1.2, "launch": 1.2, "breakthrough": 2.5, "milestone": 1.5,
    "profitable": 1.8, "profit": 1.2, "growth": 1.2, "accelerates": 1.5,
    "exceeds": 2.0, "outpaces": 1.6, "bullish": 1.8, "optimistic": 1.4,
    "positive": 1.2, "успех": 0.0,  # placeholder guard: non-ascii must not crash
    "acquisition": 1.5, "acquires": 1.5, "partnership": 1.3, "buyback": 1.6,
    "repurchase": 1.5, "dividend increase": 1.8, "guidance raise": 2.5,
    "better than expected": 2.5, "ahead of estimates": 2.2, "price target raised": 1.8,
}

LEX_NEG = {
    "miss": 2.0, "misses": 2.0, "missed": 2.0, "plunges": 2.4, "plunge": 2.2,
    "tumbles": 2.2, "sinks": 2.0, "slides": 1.6, "falls": 1.4, "drops": 1.5,
    "declines": 1.4, "slumps": 1.8, "weak": 1.6, "weakness": 1.6, "soft": 1.2,
    "downgrade": 2.0, "downgrades": 2.0, "cuts": 1.8, "lowers": 1.6, "slashes": 2.2,
    "underperform": 1.5, "sell": 1.0, "warns": 2.2, "warning": 2.0, "halts": 2.0,
    "halted": 2.0, "recall": 2.2, "rejects": 2.4, "rejected": 2.4, "denied": 2.2,
    "lawsuit": 1.8, "investigation": 2.0, "probe": 1.8, "fraud": 3.0,
    "bankruptcy": 3.5, "bankrupt": 3.5, "delisting": 2.8, "dilution": 2.2,
    "dilutive": 2.2, "offering": 1.8, "loss": 1.5, "losses": 1.5, "deficit": 1.5,
    "layoffs": 1.8, "restructuring": 1.4, "impairment": 1.8, "writedown": 1.8,
    "resigns": 1.5, "steps down": 1.5, "delays": 1.8, "delayed": 1.8,
    "bearish": 1.8, "concerns": 1.4, "disappointing": 2.2, "shortfall": 2.0,
    "below estimates": 2.2, "worse than expected": 2.5, "guidance cut": 2.8,
    "going concern": 3.5, "price target cut": 1.8, "short report": 3.0,
}

# Words that flip the polarity of the next few tokens. "fails to beat" must not
# read as positive, and headline negation is almost always local.
NEGATORS = frozenset({"not", "no", "never", "fails", "fail", "failed", "without",
                      "despite", "lacks", "unable", "wont", "cannot", "cant"})
NEGATION_SCOPE = 3

INTENSIFIERS = {"sharply": 1.4, "significantly": 1.3, "substantially": 1.3,
                "massively": 1.5, "slightly": 0.6, "marginally": 0.5,
                "modestly": 0.7, "slight": 0.6}

_TOKEN_RE = re.compile(r"[a-z']+")
_PHRASES = sorted(
    [p for p in list(LEX_POS) + list(LEX_NEG) if " " in p], key=len, reverse=True)

# Optional: the real Loughran-McDonald master dictionary, if cached.
_LM_CACHE: tuple[dict, dict] | None = None
LM_FILE = config.DATA / "_lm_dictionary.parquet"


def _lm_lexicon() -> tuple[dict, dict] | None:
    """The official LM word lists if `--fetch-lm` has cached them, else None."""
    global _LM_CACHE
    if _LM_CACHE is not None:
        return _LM_CACHE
    if not LM_FILE.exists():
        return None
    df = pd.read_parquet(LM_FILE)
    pos = {w.lower(): 1.0 for w in df.loc[df["polarity"] == "pos", "word"]}
    neg = {w.lower(): 1.0 for w in df.loc[df["polarity"] == "neg", "word"]}
    _LM_CACHE = (pos, neg)
    return _LM_CACHE


def lexicon_score(text: str, use_lm: bool = False) -> tuple[float, int, int]:
    """(score in [-1, 1], n_pos, n_neg) for one string.

    Squashed with tanh rather than divided by token count: a headline with three
    negative hits is more negative than one with a single hit, but not three
    times as negative, and dividing by length would make a long neutral headline
    with one hit score the same as a short emphatic one.
    """
    if not text:
        return 0.0, 0, 0

    lm = _lm_lexicon() if use_lm else None
    pos_lex, neg_lex = (lm if lm else (LEX_POS, LEX_NEG))

    t = text.lower()
    hits: list[float] = []

    # Phrases first, then blank them so their component words are not re-counted
    # ("guidance cut" must not also score "cuts").
    if not lm:
        for p in _PHRASES:
            while p in t:
                w = LEX_POS.get(p, 0.0) or -LEX_NEG.get(p, 0.0)
                hits.append(w)
                t = t.replace(p, " ", 1)

    toks = _TOKEN_RE.findall(t)
    neg_until = -1
    mult = 1.0
    for i, tok in enumerate(toks):
        if tok in NEGATORS:
            neg_until = i + NEGATION_SCOPE
            continue
        if tok in INTENSIFIERS:
            mult = INTENSIFIERS[tok]
            continue
        w = pos_lex.get(tok, 0.0) - neg_lex.get(tok, 0.0)
        if w:
            if i <= neg_until:
                w = -w
            hits.append(w * mult)
            mult = 1.0

    if not hits:
        return 0.0, 0, 0
    n_pos = sum(1 for h in hits if h > 0)
    n_neg = sum(1 for h in hits if h < 0)
    return float(np.tanh(sum(hits) / 3.0)), n_pos, n_neg


# ===========================================================================
# Severity
# ===========================================================================
_SEV_CACHE: dict[str, float] | None = None


def severity_priors() -> dict[str, float]:
    """Measured |return|/ATR per event_type, falling back to the hand priors.

    The priors in EVENTS are placeholders with no evidence behind them. Once
    `events.calibrate()` has written EVENT_SEVERITY_FILE from the bar store,
    those measured values win. Which is which is visible in `--survey`, so a
    severity number is never mistaken for a measurement it is not.
    """
    global _SEV_CACHE
    if _SEV_CACHE is not None:
        return _SEV_CACHE

    out = {name: prior for name, _, _, prior in EVENTS}
    out["OTHER"] = 0.8
    if config.EVENT_SEVERITY_FILE.exists():
        df = pd.read_parquet(config.EVENT_SEVERITY_FILE)
        # The MEDIAN column, not the mean: event responses are violently
        # right-skewed (HALT medians +3.3% and means +25.8%, because 6.4% of its
        # rows exceed |100%|). Severity is meant to say what this kind of news
        # TYPICALLY does, and a tail-driven mean does not say that.
        col = "abs_ret_atr_med" if "abs_ret_atr_med" in df.columns else "abs_ret_atr"
        for _, r in df.iterrows():
            if r.get("n", 0) >= 30:      # below this it is not a measurement
                out[str(r["event_type"])] = float(r[col])
    _SEV_CACHE = out
    return out


def severity(event_type: str, burst_z: float = 0.0,
             n_symbols: int = 1) -> float:
    """0-100 magnitude. Direction lives in the sentiment score, deliberately.

    Conflating the two is the mistake that makes "very negative" and "very
    important" indistinguishable -- a guidance cut and a buyback are both major
    events and only one is bad news.

    Three inputs:
      - the event class's measured |return|/ATR   (how much this KIND of news moves a stock)
      - the coverage burst z-score               (how unusual it is for THIS name to be in the news)
      - a dilution penalty for roundups          (a 20-ticker story is not about any one of them)
    """
    base = severity_priors().get(event_type, 0.8)
    if event_type in NON_COMPANY:
        return 0.0

    burst = float(np.clip(burst_z, 0.0, 4.0)) if np.isfinite(burst_z) else 0.0
    # A story tagged to 20 tickers is a roundup; its relevance to any one of them
    # is a fraction of a single-name story's. Measured mean is 1.46 symbols.
    focus = 1.0 / (1.0 + 0.25 * max(0, int(n_symbols) - 1))

    raw = (base * 18.0 + burst * 8.0) * focus
    return float(np.clip(raw, 0.0, 100.0))


# CALIBRATED TO THE MEASURED SEVERITY SURFACE, not guessed. With severity now
# driven by measured median |ret|/ATR (which spans 0.39 for PARTNERSHIP to 1.15
# for HALT) and burst z clipped at 4, the attainable range is:
#
#     base=0.40, z=0 ->  7.2      base=0.90, z=2 -> 32.2
#     base=0.65, z=1 -> 19.7      base=1.15, z=4 -> 52.7
#
# The previous thresholds (60/40/22/8) were set against the HAND priors, whose
# scale ran to 5.0 ATR. Against measured severity, 60 was unreachable, so
# CRITICAL was dead and everything real piled into HIGH.
SEVERITY_BANDS = ((45.0, "CRITICAL"), (32.0, "HIGH"), (20.0, "MEDIUM"),
                  (10.0, "LOW"), (0.0, "NOISE"))


def severity_band(sev: float) -> str:
    for lo, name in SEVERITY_BANDS:
        if sev >= lo:
            return name
    return "NOISE"


# ===========================================================================
# Frame-level scoring
# ===========================================================================
SCORE_COLS = ["id", "session", "lm_score", "lm_pos", "lm_neg", "event_type",
              "event_dir", "severity", "severity_band", "is_company"]


def score_frame(df: pd.DataFrame, use_lm: bool = False,
                burst: pd.Series | None = None) -> pd.DataFrame:
    """Score a frame of articles. Pure: same input -> same output, always.

    `burst` is an optional per-row coverage z-score; it is supplied by the caller
    because computing it needs the ticker's own history, which is a store read
    and therefore not this module's business.
    """
    if df.empty:
        return pd.DataFrame(columns=SCORE_COLS)

    text = (df["headline"].fillna("").astype(str) + ". "
            + df.get("summary", pd.Series("", index=df.index)).fillna("").astype(str))

    lex = [lexicon_score(t, use_lm=use_lm) for t in text]
    cls = [classify(h) for h in df["headline"].fillna("").astype(str)]

    n_sym = df.get("n_symbols", pd.Series(1, index=df.index)).fillna(1).astype(int)
    bz = (burst if burst is not None
          else pd.Series(0.0, index=df.index)).fillna(0.0).astype(float)

    ev = [c[0] for c in cls]
    sev = [severity(e, z, n) for e, z, n in zip(ev, bz, n_sym)]

    out = pd.DataFrame({
        "id": df["id"].values,
        "session": df["session"].values,
        "lm_score": np.float32([l[0] for l in lex]),
        "lm_pos": np.int16([l[1] for l in lex]),
        "lm_neg": np.int16([l[2] for l in lex]),
        "event_type": ev,
        "event_dir": np.int8([c[1] for c in cls]),
        "severity": np.float32(sev),
        "severity_band": [severity_band(s) for s in sev],
        "is_company": [e not in NON_COMPANY for e in ev],
    })
    return out[SCORE_COLS]


def combined_score(lm_score: float, event_dir: int) -> float:
    """Fuse lexicon polarity with the event class's implied direction.

    The class is the stronger prior -- "Prices $50M Common Stock Offering" has no
    negative words in it at all, and an offering is unambiguously dilutive. The
    lexicon then refines within the class.
    """
    if event_dir == 0:
        return float(lm_score)
    return float(np.clip(0.6 * event_dir + 0.4 * lm_score, -1.0, 1.0))


# ===========================================================================
# Score cache: data/senti/YYYY-MM.parquet
# ===========================================================================
# Scoring is cheap per article (0.143 ms measured) but not free in bulk: a
# 90-session window is ~130,000 articles, and re-scoring it on every compute()
# cost 9.5s for 30 tickers -- which the backtest, at ~572 names x 186 dates,
# cannot afford. Articles are immutable once published, so their scores are too:
# score once, store, join thereafter.
#
# The cache is DERIVED and safe to delete. `--rebuild` regenerates it from the
# news store, which is why nothing downstream is allowed to treat it as a source
# of truth.
def part_path_senti(month: str):
    return config.SENTI / f"{month}.parquet"


def senti_months() -> list[str]:
    if not config.SENTI.exists():
        return []
    return sorted(p.stem for p in config.SENTI.glob("*.parquet")
                  if re.match(r"^\d{4}-\d{2}$", p.stem))


def build_cache(months: list[str] | None = None, rebuild: bool = False,
                verbose: bool = True) -> dict[str, int]:
    """Score every stored article into the cache. Idempotent, resumable."""
    import news

    todo = months or news.months()
    have = set() if rebuild else set(senti_months())
    out: dict[str, int] = {}
    t0 = time.time()
    total = 0

    for m in todo:
        # The newest month is always re-scored: it is still being appended to,
        # so a cached partition for it is complete only by accident.
        if m in have and m != (todo[-1] if todo else None):
            continue
        art = pd.read_parquet(news.part_path(m))
        if art.empty:
            continue
        sc = score_frame(art)
        tmp = part_path_senti(m).with_suffix(".parquet.tmp")
        config.SENTI.mkdir(parents=True, exist_ok=True)
        sc.to_parquet(tmp, compression=config.COMPRESSION,
                      compression_level=config.COMPRESSION_LEVEL, index=False)
        store.atomic_replace(tmp, part_path_senti(m))
        out[m] = len(sc)
        total += len(sc)
        if verbose:
            print(f"    {m}  {len(sc):>7,} scored")

    if verbose and total:
        print(f"  cache: {total:,} article(s) in {time.time() - t0:.1f}s "
              f"({len(out)} month file(s))")
    return out


def load_cached(start: str | None = None, end: str | None = None) -> pd.DataFrame:
    """Cached scores over a month range. Empty frame if the cache is cold."""
    want = senti_months()
    if start:
        lo = (pd.Timestamp(start) - pd.offsets.MonthBegin(1)).strftime("%Y-%m")
        want = [m for m in want if m >= lo]
    if end:
        hi = (pd.Timestamp(end) + pd.offsets.MonthBegin(1)).strftime("%Y-%m")
        want = [m for m in want if m <= hi]
    if not want:
        return pd.DataFrame(columns=SCORE_COLS)
    return pd.concat([pd.read_parquet(part_path_senti(m)) for m in want],
                     ignore_index=True)


# ===========================================================================
# LM dictionary fetch (optional)
# ===========================================================================
LM_URLS = (
    "https://drive.google.com/uc?export=download&id=17CmUZM9hGUdGYjCXcjQLyybjTrcjrhik",
    "https://raw.githubusercontent.com/rflugum/10K-MDA-Section/master/"
    "LoughranMcDonald_MasterDictionary_2018.csv",
)


def fetch_lm(verbose: bool = True) -> bool:
    """Cache the official LM master dictionary if any known mirror answers.

    Best-effort by design: the canonical host has moved repeatedly, and the
    embedded headline lexicon is the supported path. Returns False rather than
    raising so a scheduled run never dies over an optional enhancement.
    """
    import io

    import requests

    for url in LM_URLS:
        try:
            r = requests.get(url, timeout=60,
                             headers={"User-Agent": config.SEC_UA})
            if r.status_code != 200 or len(r.content) < 100_000:
                continue
            df = pd.read_csv(io.BytesIO(r.content))
            cols = {c.lower(): c for c in df.columns}
            if "word" not in cols or "negative" not in cols:
                continue
            pos = df.loc[df[cols["positive"]] > 0, cols["word"]]
            neg = df.loc[df[cols["negative"]] > 0, cols["word"]]
            out = pd.concat([
                pd.DataFrame({"word": pos.astype(str), "polarity": "pos"}),
                pd.DataFrame({"word": neg.astype(str), "polarity": "neg"}),
            ], ignore_index=True)
            out.to_parquet(LM_FILE, compression=config.COMPRESSION, index=False)
            if verbose:
                print(f"  cached {len(out):,} LM words -> {LM_FILE.name} "
                      f"({(out['polarity'] == 'pos').sum()} pos / "
                      f"{(out['polarity'] == 'neg').sum()} neg)")
            return True
        except Exception as exc:                                  # noqa: BLE001
            if verbose:
                print(f"  {url[:52]}... -> {repr(exc)[:70]}")
    if verbose:
        print("  no LM mirror answered; the embedded headline lexicon is used "
              "(which is the supported path anyway -- see the module docstring)")
    return False


# ===========================================================================
# Selftest / bench / survey
# ===========================================================================
FIXTURES = [
    # (headline, expected_event, expected_sign)   sign: +1 / 0 / -1
    ("Redwire Shares Jump as Federal Drone Funding Boosts Defense Tech Sector", "MOVER", 1),
    ("Vertex Pharmaceuticals Q2 Adj. EPS $4.73, Inline, Sales $3.334B Beats", "EARNINGS_BEAT", 1),
    ("Acme Corp Q3 EPS Misses Estimates, Sales Below Consensus", "EARNINGS_MISS", -1),
    ("XYZ Prices $50 Million Common Stock Offering", "OFFERING", -1),
    ("BioPharma Announces FDA Approval Of Lead Candidate", "FDA", 1),
    ("Company Cuts FY2026 Revenue Guidance", "GUIDANCE_CUT", -1),
    ("Company Raises FY2026 EPS Guidance", "GUIDANCE_RAISE", 1),
    # Rating unchanged -> ANALYST_MAINTAIN (low severity), but a price-target
    # RAISE is still mildly positive, and that sign has to come from the lexicon
    # because the class direction is deliberately 0.
    ("Wells Fargo Maintains Overweight on Oracle, Raises Price Target to $180",
     "ANALYST_MAINTAIN", 1),
    ("Goldman Sachs Downgrades Tesla To Neutral", "ANALYST_DOWN", -1),
    ("Here's How Much $1000 Invested In Amkor Technology 10 Years Ago Would Be Worth",
     "NOISE", 0),
    ("If You Invested $100 In Nvidia Stock 5 Years Ago", "NOISE", 0),
    ("Hindenburg Research Publishes Short Report On XYZ Corp", "SHORT_REPORT", -1),
    ("Acme Files For Chapter 11 Bankruptcy Protection", "BANKRUPTCY", -1),
    ("Trump Calls Iran Deal a Last Chance; Dow Futures Gain 95 Points", "MACRO", 0),
    ("MegaCorp To Acquire SmallCo In $2B Definitive Agreement", "MA", 1),
    ("Trading Halted In Shares Of XYZ Pending News", "HALT", 0),
]


def selftest(verbose: bool = True) -> None:
    fails = []

    for h, want_ev, want_sign in FIXTURES:
        ev, dirn, _ = classify(h)
        lm, _, _ = lexicon_score(h)
        got_sign = int(np.sign(round(combined_score(lm, dirn), 3)))
        if ev != want_ev:
            fails.append(f"class {h[:52]!r}: got {ev}, want {want_ev}")
        if want_sign and got_sign != want_sign:
            fails.append(f"sign  {h[:52]!r}: got {got_sign:+d} ({ev}), want {want_sign:+d}")

    # Negation must flip polarity, or "fails to beat" reads as a beat.
    plain, _, _ = lexicon_score("Company beats estimates")
    negated, _, _ = lexicon_score("Company fails to beat estimates")
    if not (plain > 0 > negated):
        fails.append(f"negation: 'beats'={plain:+.2f} 'fails to beat'={negated:+.2f}")

    # Phrase must not double-count its own component words.
    gc, _, _ = lexicon_score("guidance cut")
    if not (-1.0 <= gc < 0):
        fails.append(f"phrase 'guidance cut' scored {gc:+.2f}")

    # Purity: identical input, identical output, no hidden state.
    a = score_frame(pd.DataFrame({"id": [1], "session": ["2026-08-05"],
                                  "headline": [FIXTURES[0][0]], "summary": [""],
                                  "n_symbols": [1]}))
    b = score_frame(pd.DataFrame({"id": [1], "session": ["2026-08-05"],
                                  "headline": [FIXTURES[0][0]], "summary": [""],
                                  "n_symbols": [1]}))
    if not a.equals(b):
        fails.append("score_frame is not pure: two identical calls differ")

    # Severity must be monotone in burst and must ignore direction.
    if not severity("OFFERING", 0.0) < severity("OFFERING", 3.0):
        fails.append("severity is not increasing in burst_z")
    if severity("NOISE", 5.0) != 0.0:
        fails.append("NOISE must have zero severity regardless of burst")
    # A 20-ticker roundup must not carry a single-name story's weight.
    if not severity("MOVER", 1.0, n_symbols=20) < severity("MOVER", 1.0, n_symbols=1):
        fails.append("severity ignores the roundup dilution penalty")

    # Unicode and empties must not raise -- headlines carry smart quotes.
    for junk in ("", None, "Here’s How Much — éè", "\U0001F680" * 5):
        lexicon_score(junk or "")
        classify(junk or "")

    if fails:
        print("SELFTEST FAILURES:")
        for f in fails:
            print("  -", f)
        raise SystemExit(1)

    if verbose:
        measured = config.EVENT_SEVERITY_FILE.exists()
        print(f"sentiment selftest OK  ({len(FIXTURES)} fixtures, "
              f"{len(EVENTS)} event classes, "
              f"severity={'MEASURED' if measured else 'hand priors'})")


def bench(n: int = 4000) -> None:
    """ms/article for the lexicon path, so the interval budget is a fact."""
    base = [f[0] for f in FIXTURES]
    heads = [base[i % len(base)] for i in range(n)]
    df = pd.DataFrame({"id": range(n), "session": "2026-08-05",
                       "headline": heads, "summary": "", "n_symbols": 1})
    t = time.time()
    score_frame(df)
    dt = time.time() - t
    per = dt / n * 1000
    print(f"  lexicon: {n:,} articles in {dt:.2f}s  =  {per:.3f} ms/article")
    print(f"    one 30-min run (~60 articles) : {per * 60 / 1000:.2f} s")
    print(f"    one full day (1,580)          : {per * 1580 / 1000:.1f} s")
    print(f"    4y backfill (~1.3M)           : {per * 1.3e6 / 1000 / 60:.1f} min")


def survey(limit_months: int = 2) -> None:
    """What the taxonomy actually does to the stored corpus."""
    import news

    ms = news.months()[-limit_months:]
    if not ms:
        print("  (news store is empty -- run `python news.py --backfill`)")
        return
    df = pd.concat([pd.read_parquet(news.part_path(m)) for m in ms], ignore_index=True)
    sc = score_frame(df)

    n = len(sc)
    print(f"  {n:,} articles over {ms}\n")
    vc = sc["event_type"].value_counts()
    print(f"  {'event_type':<18} {'n':>7} {'share':>7}  {'mean lm':>8}  {'mean sev':>8}")
    for ev, c in vc.items():
        m = sc[sc["event_type"] == ev]
        print(f"  {ev:<18} {c:>7,} {c / n:>6.1%}  {m['lm_score'].mean():>+8.3f}  "
              f"{m['severity'].mean():>8.1f}")
    print(f"\n  non-company (excluded from per-ticker aggregates): "
          f"{(~sc['is_company']).sum():,} ({(~sc['is_company']).mean():.1%})")
    print(f"  severity bands: "
          f"{dict(sc['severity_band'].value_counts())}")
    print(f"  lexicon silent (score==0): {(sc['lm_score'] == 0).mean():.1%}")


def main() -> int:
    config.safe_console()
    ap = argparse.ArgumentParser(description="Sentiment scoring: lexicon + taxonomy.")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--bench", action="store_true")
    ap.add_argument("--survey", action="store_true")
    ap.add_argument("--fetch-lm", action="store_true")
    ap.add_argument("--score", metavar="HEADLINE", default=None)
    a = ap.parse_args()

    if a.selftest:
        selftest()
    elif a.bench:
        bench()
    elif a.survey:
        survey()
    elif a.fetch_lm:
        fetch_lm()
    elif a.score:
        ev, dirn, prior = classify(a.score)
        lm, npos, nneg = lexicon_score(a.score)
        sev = severity(ev, 0.0, 1)
        print(f"  headline   {a.score}")
        print(f"  event      {ev}  (dir {dirn:+d}, prior {prior:.1f} ATR)")
        print(f"  lexicon    {lm:+.3f}  ({npos} pos / {nneg} neg hits)")
        print(f"  combined   {combined_score(lm, dirn):+.3f}")
        print(f"  severity   {sev:.1f}  -> {severity_band(sev)}")
        print(f"  company    {ev not in NON_COMPANY}")
    else:
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
