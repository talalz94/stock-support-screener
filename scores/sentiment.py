"""
Score module 1: per-ticker news sentiment.

Turns the article store into a point-in-time row per ticker. Registered under
the name "sentiment"; see scores/__init__.py for the interface and for why the
output is tidy rather than wide.

THE MEASUREMENT THAT DICTATED THIS DESIGN
-----------------------------------------
Over 30 days across the 30 live flags: ORCL had 95 articles, the MEDIAN flag had
3, and 4 of 30 had ZERO. Per-ticker news is a sparse signal for exactly the
micro-caps this screener surfaces, and three consequences follow:

1. `has_news` IS AN EXPLICIT STATE. A ticker with no coverage is not neutral, and
   imputing 0.0 would drop 13% of the list into the middle of the ranking by
   accident -- the single most misleading thing this module could do. Silent
   names emit has_news=0 and NO sentiment metrics at all.

2. COUNTS ARE RANKED CROSS-SECTIONALLY, NEVER USED RAW. Article volume grew ~4x
   between 2016 (371/day) and 2026 (1,580/day), so a raw count compares this
   year's Benzinga output to that year's, not this stock to that stock. Ranking
   within a date removes the trend by construction.

3. THE BURST IS RELATIVE TO THE TICKER'S OWN BASELINE. ORCL going 3 -> 8 articles
   is nothing; DPRO going 0 -> 8 is the entire signal. A cross-sectional count
   would rank ORCL above DPRO every single day and never say anything.

CAUSALITY
---------
`compute(asof)` reads only articles whose ATTRIBUTED SESSION is <= asof, where
attribution is news.attribute_session (an article published after S's close
belongs to S+1). Aggregates are recomputed from that window every call and never
read back from storage, so there is no path by which a stored number can carry a
window that once reached too far. replay.py --leaktest asserts both properties.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import config
import news
import sentiment as senti
from scores import register, write

NAME = "sentiment"

_W = tuple(config.SENTI_WINDOWS)          # (5, 30, 90) sessions


class SentimentModule:
    name = NAME

    def metrics(self) -> list[str]:
        m = ["has_news", "news_coverage", "news_z", "sent_delta", "severity_max",
             "severity_rank", "news_count_rank", "sent_rank",
             "top_event", "event_types", "top_severity_band", "top_headline",
             # Sessions since the NEWEST article, and the stale flag derived
             # from it. Without these a score built on six-week-old news is
             # indistinguishable from one built on this morning's.
             "sent_age", "sent_stale"]
        for w in _W:
            m += [f"sent_mean_{w}d", f"sent_net_{w}d", f"news_count_{w}d",
                  # Recency-weighted twin of sent_mean_*, added alongside so the
                  # study can measure whether decay helps before it is promoted.
                  f"sent_decay_{w}d"]
        return m

    # ------------------------------------------------------------------
    def compute(self, asof: str, tickers: list[str] | None = None,
                allow_partial: bool = False) -> pd.DataFrame:
        """Point-in-time sentiment for `asof`. Reads nothing published after it.

        Refuses to score a window the news store does not cover -- see
        `_assert_coverage`. That guard is not theoretical: it was added after a
        half-backfilled store silently reported 13 of 30 flags as having no news
        when the API said 4.
        """
        sessions = _sessions_upto(asof, max(_W) + config.SENTI_BASELINE_DAYS)
        if not sessions:
            return pd.DataFrame(columns=["ticker", "metric", "value", "label"])

        # Guard on the PRIMARY window, not the longest one. `has_news`, the
        # burst and the headline all come from the 30-session view; the 90d
        # metrics are secondary. Guarding on 90 made a usable daily screen wait
        # on a 2.6-hour backfill, which is the wrong trade -- so each window is
        # checked against what it actually needs, and any window that is not
        # covered has its metrics DROPPED rather than reported thin.
        cov = _assert_coverage(sessions[-_W[1]:], asof, allow_partial=allow_partial)
        stored_sess = news.stored_sessions()
        covered_w = {w for w in _W
                     if _coverage(sessions[-w:], stored_sess) >= config.SENTI_MIN_COVERAGE}
        if not covered_w:
            covered_w = {_W[1]}          # allow_partial got us here; keep the primary

        art = news.read(start=sessions[0], end=asof)
        if art.empty:
            return pd.DataFrame(columns=["ticker", "metric", "value", "label"])

        # Score once per ARTICLE (burst is a per-ticker property and is folded in
        # later, at the aggregate, where it is actually defined). Scores come from
        # the cache when it is warm: re-scoring the whole window on every call
        # cost 9.5s for 30 tickers, which the backtest cannot afford at 186 dates.
        art = _attach_scores(art)

        # NOISE and MACRO mention a ticker but say nothing about the company.
        # Dropping them here rather than down-weighting them keeps "3 articles"
        # meaning three pieces of company news.
        art = art[art["is_company"]]
        if art.empty:
            return pd.DataFrame(columns=["ticker", "metric", "value", "label"])

        art["combined"] = [senti.combined_score(l, d)
                           for l, d in zip(art["lm_score"], art["event_dir"])]

        ex = news.explode(art, tickers=tickers)
        if ex.empty:
            return pd.DataFrame(columns=["ticker", "metric", "value", "label"])

        # ONE EVENT, ONE ROW -- measured, 27.6% of (article, ticker) pairs are
        # republications of the same (ticker, session, event_type), and 1.98x for
        # HALT. Without this a name whose single halt was rewritten six times
        # shows a six-article coverage burst and six copies of the same polarity
        # in its mean, which is a story about Benzinga's editing, not the stock.
        ex = (ex.sort_values("id")
                .drop_duplicates(["ticker", "session", "event_type"], keep="first"))

        sess_idx = {s: i for i, s in enumerate(sessions)}
        ex["_si"] = ex["session"].map(sess_idx)
        ex = ex[ex["_si"].notna()]
        last_i = sess_idx[asof] if asof in sess_idx else len(sessions) - 1

        rows: list[dict] = []
        universe = sorted(set(ex["ticker"]) | set(tickers or []))

        # EVERYTHING BELOW IS GROUPBY, NOT A PER-TICKER LOOP. The first version
        # filtered `ex[ex["ticker"] == t]` inside a loop over the universe, which
        # is a full scan of a ~200k-row frame per ticker: measured 141s for 3,471
        # names. That is tolerable on a 30-minute interval and fatal for the
        # backtest, where 186 dates would have cost >7 hours.
        per_ticker: dict[str, dict] = {t: {} for t in universe}

        # ---- per-window aggregates (only windows the store actually covers)
        for w in _W:
            if w not in covered_w:
                continue
            win = ex[ex["_si"] >= last_i - w + 1]
            if win.empty:
                for t in universe:
                    per_ticker[t][f"news_count_{w}d"] = 0.0
                continue
            g = win.groupby("ticker", observed=True)["combined"]
            agg = pd.DataFrame({"cnt": g.size(), "mean": g.mean()})

            # Recency weight: 0.5 ** (age_in_sessions / half_life). Vectorised
            # for the same reason the rest of this block is -- a per-ticker loop
            # here cost 141s across 3,471 names.
            age = (last_i - win["_si"]).clip(lower=0)
            wt = np.power(0.5, age / float(config.SENTI_DECAY_HALFLIFE))
            wsum = wt.groupby(win["ticker"], observed=True).sum()
            wdot = (wt * win["combined"]).groupby(win["ticker"],
                                                  observed=True).sum()
            agg["decay"] = wdot / wsum.replace(0, np.nan)
            # `net` via sums of booleans -- .apply() here was a Python call per group.
            agg["net"] = ((win["combined"] > 0.05).groupby(win["ticker"], observed=True).sum()
                          - (win["combined"] < -0.05).groupby(win["ticker"], observed=True).sum()
                          ) / agg["cnt"]
            for t, r in agg.iterrows():
                d = per_ticker.setdefault(str(t), {})
                d[f"news_count_{w}d"] = float(r["cnt"])
                d[f"sent_mean_{w}d"] = float(r["mean"])
                d[f"sent_net_{w}d"] = float(r["net"])
                if pd.notna(r.get("decay")):
                    d[f"sent_decay_{w}d"] = float(r["decay"])
            for t in universe:
                per_ticker[t].setdefault(f"news_count_{w}d", 0.0)

        # ---- coverage burst, against each ticker's OWN baseline
        base_lo = last_i - config.SENTI_BASELINE_DAYS + 1
        recent_lo = last_i - _W[0] + 1
        base = ex[(ex["_si"] >= base_lo) & (ex["_si"] < recent_lo)]
        n_base_sess = max(recent_lo - base_lo, 1)

        if base.empty:
            mu_s = sd_s = pd.Series(dtype="float64")
        else:
            counts = base.groupby(["ticker", "_si"], observed=True).size()
            tot = counts.groupby(level=0).sum()
            # std over the FULL baseline including the zero sessions: a name with
            # one article in 85 sessions is bursty, and a std over only the days
            # it appeared would say it is perfectly regular.
            ss = counts.pow(2).groupby(level=0).sum()
            mu_s = tot / n_base_sess
            sd_s = (ss / n_base_sess - mu_s.pow(2)).clip(lower=0).pow(0.5)

        for t in universe:
            mu = float(mu_s.get(t, 0.0))
            sd = float(sd_s.get(t, 0.0))
            recent = per_ticker[t].get(f"news_count_{_W[0]}d", 0.0) / max(_W[0], 1)
            # sd floor of 0.5: a ticker with a dead-flat zero baseline would
            # otherwise produce an infinite z on its first ever article, which in
            # this universe is the common case, not an edge case.
            per_ticker[t]["news_z"] = float((recent - mu) / max(sd, 0.5))

        # ---- severity, now that burst exists, and the headline behind it
        recent = ex[ex["_si"] >= last_i - _W[1] + 1].copy()
        if not recent.empty:
            bz = recent["ticker"].map(
                {t: per_ticker[t].get("news_z", 0.0) for t in per_ticker}).fillna(0.0)
            # Column name carries no leading underscore on purpose: itertuples
            # silently renames those to positional `_11`-style attributes.
            recent["sevmax"] = [senti.severity(e, z, n) for e, z, n
                                in zip(recent["event_type"], bz, recent["n_symbols"])]
            top = recent.loc[recent.groupby("ticker", observed=True)["sevmax"].idxmax()]
            evs = (recent.groupby("ticker", observed=True)["event_type"]
                   .agg(lambda s: ",".join(sorted(set(s)))))
            for r in top.itertuples(index=False):
                d = per_ticker.setdefault(str(r.ticker), {})
                d["severity_max"] = float(r.sevmax)
                d["_top_event"] = str(r.event_type)
                d["_top_band"] = senti.severity_band(r.sevmax)
                d["_top_headline"] = str(r.headline)[:180]
                d["_events"] = str(evs.get(r.ticker, r.event_type))

        for t in universe:
            s5 = per_ticker[t].get(f"sent_mean_{_W[0]}d")
            s30 = per_ticker[t].get(f"sent_mean_{_W[1]}d")
            if s5 is not None and s30 is not None and np.isfinite(s5) and np.isfinite(s30):
                per_ticker[t]["sent_delta"] = float(s5 - s30)

        # ---- HOW OLD IS THE NEWEST ARTICLE
        #
        # The single most important missing number. A 30-session mean says
        # nothing about WHEN its inputs arrived, and with a median of 3 articles
        # per name the difference between "this morning" and "six weeks ago" is
        # the difference between a signal and a fossil. DPRO scored 0.4979 on
        # three articles whose newest was 38 sessions old, and no field on the
        # page contradicted it.
        if not ex.empty:
            newest = ex.groupby("ticker", observed=True)["_si"].max()
            for t, si in newest.items():
                t = str(t)
                if t not in per_ticker:
                    continue
                age = float(max(last_i - int(si), 0))
                per_ticker[t]["sent_age"] = age
                per_ticker[t]["sent_stale"] = (
                    1.0 if age > config.SENTI_STALE_SESSIONS else 0.0)

        # ---- emit
        for t in universe:
            d = per_ticker[t]
            has = d.get(f"news_count_{_W[1]}d", 0.0) >= config.SENTI_MIN_ARTICLES
            rows.append({"ticker": t, "metric": "has_news",
                         "value": 1.0 if has else 0.0, "label": None})
            # Stamped on every ticker, including silent ones, so a has_news=0 can
            # always be read against how much of the window was actually fetched.
            rows.append({"ticker": t, "metric": "news_coverage",
                         "value": float(cov), "label": None})
            if not has:
                # Deliberately emit NOTHING else. See design note 1: a silent
                # ticker must be absent from the ranking, not sitting at its
                # midpoint pretending to be balanced.
                continue
            for k, v in d.items():
                if k.startswith("_") or v is None or not np.isfinite(v):
                    continue
                rows.append({"ticker": t, "metric": k, "value": float(v),
                             "label": None})
            for k, mkey in (("_top_event", "top_event"),
                            ("_events", "event_types"),
                            ("_top_band", "top_severity_band"),
                            ("_top_headline", "top_headline")):
                if d.get(k):
                    rows.append({"ticker": t, "metric": mkey,
                                 "value": np.nan, "label": d[k]})

        out = pd.DataFrame(rows)
        return _add_ranks(out)

    def selftest(self) -> None:
        _selftest()


# ---------------------------------------------------------------- helpers
class NewsCoverageError(RuntimeError):
    """The news store does not cover the window being scored.

    Raised rather than returning a low score, because the two are
    indistinguishable downstream: a ticker with no articles and a ticker whose
    articles were never fetched both read as has_news=0. Measured during the
    build -- a half-backfilled store reported 13 of 30 flags silent when the API
    said 4, and nothing anywhere errored.
    """


def _sessions_upto(asof: str, back: int) -> list[str]:
    import calendar_us

    all_s = calendar_us.all_sessions()
    upto = [s for s in all_s if s <= asof]
    return upto[-back:] if upto else []


def _coverage(window: list[str], have: set[str]) -> float:
    return sum(1 for s in window if s in have) / max(len(window), 1)


def _assert_coverage(window: list[str], asof: str,
                     allow_partial: bool = False) -> float:
    """Fraction of `window` present in the news store. Raises if too low."""
    have = news.stored_sessions()
    cov = _coverage(window, have)
    if cov < config.SENTI_MIN_COVERAGE and not allow_partial:
        gaps = [s for s in window if s not in have]
        raise NewsCoverageError(
            f"news store covers {cov:.0%} of the {len(window)} session(s) ending "
            f"{asof}, below SENTI_MIN_COVERAGE={config.SENTI_MIN_COVERAGE:.0%}. "
            f"{len(gaps)} missing, first {gaps[:3]}, last {gaps[-3:]}. "
            "Scoring this window would report 'no news' for names that simply "
            "were not fetched. Run `python news.py --backfill`, or pass "
            "allow_partial=True if a partial reading is genuinely what you want."
        )
    return cov


def _attach_scores(art: pd.DataFrame) -> pd.DataFrame:
    """Article frame + its scores, from the cache where warm.

    Any article missing from the cache is scored inline, so a cold or stale
    cache costs time and never correctness.
    """
    keep = ["id", "session", "headline", "n_symbols", "symbols"]
    cached = senti.load_cached(start=str(art["session"].min()),
                               end=str(art["session"].max()))

    if cached.empty:
        sc = senti.score_frame(art)
    else:
        cached = cached.drop_duplicates("id", keep="last")
        hit = art["id"].isin(set(cached["id"]))
        parts = [cached[cached["id"].isin(set(art.loc[hit, "id"]))]]
        if (~hit).any():
            parts.append(senti.score_frame(art[~hit]))
        sc = pd.concat(parts, ignore_index=True)

    out = art[keep].merge(sc.drop(columns=["session"]), on="id", how="inner")
    return out.reset_index(drop=True)


def _add_ranks(df: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional percentile ranks, computed WITHIN this date.

    See design note 2. A raw count is a statement about Benzinga's output that
    year; a rank is a statement about this stock against its peers today, which
    is the only version that is comparable across a multi-year backtest.
    """
    if df.empty:
        return df
    src = {"sent_rank": f"sent_mean_{_W[1]}d",
           "news_count_rank": f"news_count_{_W[1]}d",
           "severity_rank": "severity_max"}
    add = []
    for out_metric, in_metric in src.items():
        sub = df[df["metric"] == in_metric]
        if len(sub) < 2:
            continue
        r = sub["value"].rank(pct=True)
        add.append(pd.DataFrame({"ticker": sub["ticker"].values,
                                 "metric": out_metric,
                                 "value": r.values, "label": None}))
    return pd.concat([df] + add, ignore_index=True) if add else df


MODULE = register(SentimentModule())


# ---------------------------------------------------------------- selftest
def _selftest() -> None:
    m = MODULE
    declared = set(m.metrics())

    # Every metric the module can emit must be declared, or scores.write refuses
    # it -- which is the check that keeps the report legend honest.
    internal = {"has_news", "news_z", "sent_delta", "severity_max",
                "top_event", "event_types", "top_severity_band", "top_headline",
                "sent_rank", "news_count_rank", "severity_rank"}
    for w in _W:
        internal |= {f"sent_mean_{w}d", f"sent_net_{w}d", f"news_count_{w}d"}
    missing = internal - declared
    assert not missing, f"metrics() omits {sorted(missing)}"

    # A silent ticker emits has_news=0 and nothing else.
    fake = pd.DataFrame([{"ticker": "ZZZZ", "metric": "has_news",
                          "value": 0.0, "label": None}])
    assert _add_ranks(fake).equals(fake), "ranks must no-op on a single row"

    print(f"  [sentiment] {len(declared)} metrics, windows={_W}")
