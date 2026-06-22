"""Feature engineering for the ML tasks.

Builds a per-event feature matrix from price history, the flow/study enrichment,
registry metadata, and (optionally) FMP fundamentals. Every price feature is
computed *as of the announcement date* so the matrix is point-in-time.

Unavailable inputs (e.g. news coverage without a feed) are left as NaN rather
than imputed with fabricated values.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Config
from .fmp_client import FMPClient
from .liquidity import adv_dollars, spread_proxy_bps, volatility
from .price_data import PriceStore, bar_on_or_before
from .utils import get_logger, safe_div, to_date

log = get_logger("index_flow.features")

FEATURE_COLUMNS = [
    "market_cap",
    "adv_dollars",
    "dollar_volume",
    "spread_proxy_bps",
    "volatility",
    "mom_1m",
    "mom_3m",
    "mom_6m",
    "mom_12m",
    "recent_runup_1m",
    "volume_shock",
    "sector",
    "industry",
    "theme",
    "fund_aum",
    "number_of_holdings",
    "expected_buy_dollars",
    "flow_pressure",
    "n_products_holding",
    "peer_inclusion_count",
    "prior_index_membership",
    "immediate_reaction",
    "days_to_effective",
    "source_obscurity_score",
    "news_coverage_score",
]

_MAJOR_PROVIDERS = ("S&P", "MSCI", "FTSE", "DOW", "NASDAQ", "RUSSELL")


def _momentum(prices: pd.DataFrame, as_of, trading_days: int) -> float:
    pos_mask = prices["date"] <= pd.Timestamp(as_of)
    sub = prices[pos_mask]
    if len(sub) <= trading_days:
        return np.nan
    p_now = sub["close"].iloc[-1]
    p_then = sub["close"].iloc[-1 - trading_days]
    return safe_div(p_now, p_then, np.nan) - 1.0 if p_then else np.nan


def _volume_shock(prices: pd.DataFrame, as_of, lookback: int = 63) -> float:
    sub = prices[prices["date"] <= pd.Timestamp(as_of)]
    if len(sub) < 5:
        return np.nan
    last_vol = sub["volume"].iloc[-1]
    base = sub["volume"].tail(lookback).mean()
    return safe_div(last_vol, base, np.nan)


def _source_obscurity(event: pd.Series) -> float:
    """0 = widely-covered major index change, 1 = obscure thematic/holdings diff."""
    provider = str(event.get("provider") or "").upper()
    stype = event.get("source_type")
    if any(m in provider for m in _MAJOR_PROVIDERS):
        base = 0.15
    elif stype == "holdings_diff":
        base = 0.75
    elif stype == "custom_manual":
        base = 0.5
    else:
        base = 0.5
    # thinner / smaller confidence -> more obscure
    conf = event.get("confidence_score")
    if pd.notna(conf):
        base = min(1.0, base + 0.2 * (1 - float(conf)))
    return round(float(base), 3)


def build_features(
    cfg: Config,
    events: pd.DataFrame,
    store: PriceStore | None = None,
    fmp: FMPClient | None = None,
    use_fundamentals: bool = True,
) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame(columns=["event_id", *FEATURE_COLUMNS])
    store = store or PriceStore(cfg)
    fmp = fmp or FMPClient(cfg)
    adv_lb = int(cfg.get("flow", "adv_lookback_days", default=63))

    # cross-event aggregates
    holding_counts = events.groupby("asx_ticker")["event_id"].nunique().to_dict()

    price_cache: dict[str, pd.DataFrame] = {}
    mcap_cache: dict[str, pd.DataFrame] = {}
    profile_cache: dict[str, dict] = {}
    rows = []
    for _, ev in events.iterrows():
        sym = ev.get("asx_ticker")
        eid = ev.get("event_id")
        feat = {c: np.nan for c in FEATURE_COLUMNS}
        feat["event_id"] = eid
        if not isinstance(sym, str) or not sym:
            rows.append(feat)
            continue
        if sym not in price_cache:
            price_cache[sym] = store.get(sym)
        prices = price_cache[sym]
        as_of = to_date(ev.get("announcement_date")) or to_date(ev.get("detected_date"))

        if not prices.empty and as_of is not None:
            bar = bar_on_or_before(prices, as_of)
            feat["adv_dollars"] = adv_dollars(prices, as_of, adv_lb)
            feat["dollar_volume"] = float(bar["dollar_volume"]) if bar is not None and pd.notna(bar.get("dollar_volume")) else np.nan
            feat["spread_proxy_bps"] = spread_proxy_bps(prices, as_of)
            feat["volatility"] = volatility(prices, as_of, adv_lb)
            feat["mom_1m"] = _momentum(prices, as_of, 21)
            feat["mom_3m"] = _momentum(prices, as_of, 63)
            feat["mom_6m"] = _momentum(prices, as_of, 126)
            feat["mom_12m"] = _momentum(prices, as_of, 252)
            feat["recent_runup_1m"] = feat["mom_1m"]
            feat["volume_shock"] = _volume_shock(prices, as_of, adv_lb)

        # fundamentals (optional / cached)
        if use_fundamentals:
            if sym not in mcap_cache:
                mcap_cache[sym] = fmp.historical_market_cap(sym)
            mc = mcap_cache[sym]
            if not mc.empty and as_of is not None:
                sub = mc[mc["date"] <= pd.Timestamp(as_of)]
                if not sub.empty:
                    feat["market_cap"] = float(sub["market_cap"].iloc[-1])
            if sym not in profile_cache:
                profile_cache[sym] = fmp.profile(sym)
            prof = profile_cache[sym]
            feat["sector"] = prof.get("sector")
            feat["industry"] = prof.get("industry")
            if pd.isna(feat["market_cap"]) and prof.get("mktCap"):
                feat["market_cap"] = prof.get("mktCap")

        feat["theme"] = ev.get("_theme")
        feat["fund_aum"] = ev.get("_aum")
        feat["number_of_holdings"] = ev.get("_n_holdings")
        feat["expected_buy_dollars"] = ev.get("estimated_buy_sell_dollars")
        feat["flow_pressure"] = ev.get("flow_pressure")
        feat["n_products_holding"] = holding_counts.get(sym, 1)
        feat["peer_inclusion_count"] = holding_counts.get(sym, 1)
        feat["prior_index_membership"] = np.nan      # needs membership history (manual)
        feat["immediate_reaction"] = ev.get("immediate_reaction")
        eff = to_date(ev.get("effective_date"))
        entry = to_date(ev.get("entry_date")) or as_of
        feat["days_to_effective"] = (eff - entry).days if (eff and entry) else np.nan
        feat["source_obscurity_score"] = _source_obscurity(ev)
        feat["news_coverage_score"] = np.nan         # no feed wired up
        rows.append(feat)

    return pd.DataFrame(rows)[["event_id", *FEATURE_COLUMNS]]
