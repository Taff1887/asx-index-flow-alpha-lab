"""Liquidity & microstructure proxies, all point-in-time (trailing window).

These feed both the flow-pressure denominator (ADV$) and the tradeability /
cost model. Every function uses only bars on/before the reference date.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .price_data import bar_on_or_before


def _trailing(prices: pd.DataFrame, as_of, lookback: int) -> pd.DataFrame:
    if prices.empty:
        return prices
    sub = prices[prices["date"] <= pd.Timestamp(as_of)]
    return sub.tail(lookback)


def adv_dollars(prices: pd.DataFrame, as_of, lookback: int = 63) -> float:
    """Trailing average daily traded value (A$) over ``lookback`` bars."""
    sub = _trailing(prices, as_of, lookback)
    if sub.empty:
        return float("nan")
    dv = sub["dollar_volume"] if "dollar_volume" in sub else sub["close"] * sub["volume"]
    return float(dv.mean())


def adv_shares(prices: pd.DataFrame, as_of, lookback: int = 63) -> float:
    sub = _trailing(prices, as_of, lookback)
    return float(sub["volume"].mean()) if not sub.empty else float("nan")


def spread_proxy_bps(prices: pd.DataFrame, as_of, lookback: int = 21) -> float:
    """Rough relative spread proxy in bps from the daily high-low range.

    Not a true quoted spread (we have no quotes), but a serviceable, monotone
    stand-in: thin/volatile names show wider ranges. Used only for cost shading.
    """
    sub = _trailing(prices, as_of, lookback)
    if sub.empty:
        return float("nan")
    rng = ((sub["high"] - sub["low"]) / sub["close"].replace(0, np.nan)).dropna()
    if rng.empty:
        return float("nan")
    # Range overstates spread; scale down by a conventional factor (~0.25).
    return float(rng.mean() * 0.25 * 10000)


def volatility(prices: pd.DataFrame, as_of, lookback: int = 63) -> float:
    """Annualised daily-return volatility over the trailing window."""
    sub = _trailing(prices, as_of, lookback + 1)
    if len(sub) < 5:
        return float("nan")
    rets = sub["close"].pct_change().dropna()
    return float(rets.std() * np.sqrt(252))


def liquidity_tightness(adv_dollars_value: float, reference: float = 5_000_000.0) -> float:
    """Map ADV$ to a 0..1 'tightness' score (thinner name -> closer to 1).

    tightness = reference / (reference + ADV$).  0.5 at ADV == reference.
    """
    if adv_dollars_value is None or (isinstance(adv_dollars_value, float) and np.isnan(adv_dollars_value)):
        return 0.0
    if adv_dollars_value <= 0:
        return 1.0
    return float(reference / (reference + adv_dollars_value))


def is_tradeable(
    prices: pd.DataFrame,
    as_of,
    min_price: float = 0.02,
    min_adv_dollars: float = 50_000.0,
    lookback: int = 63,
) -> bool:
    """Basic tradeability gate as of a date: priced, and liquid enough."""
    bar = bar_on_or_before(prices, as_of)
    if bar is None:
        return False
    px = bar.get("close")
    if pd.isna(px) or px < min_price:
        return False
    return adv_dollars(prices, as_of, lookback) >= min_adv_dollars
