"""ASX price data: fetch, cache, and point-in-time access helpers.

:class:`PriceStore` fetches daily OHLCV from FMP and caches a tidy parquet per
symbol under ``data/processed/prices/``. The free functions below operate on a
single symbol's price frame and are pure (no IO) so they're easy to unit test and
guaranteed lookahead-free: every "as of date D" lookup uses only rows with
``date <= D`` (or the first row strictly after D for *next*-bar entries).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from .config import Config
from .fmp_client import FMPClient, default_history_start
from .utils import get_logger, read_parquet, write_parquet

log = get_logger("index_flow.prices")

PRICE_COLUMNS = ["date", "open", "high", "low", "close", "adj_close", "volume", "vwap", "dollar_volume"]


class PriceStore:
    def __init__(self, cfg: Config, fmp: FMPClient | None = None):
        self.cfg = cfg
        self.fmp = fmp or FMPClient(cfg)
        self.dir = cfg.path("data_processed") / "prices"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, symbol: str) -> Path:
        return self.dir / f"{symbol.upper()}.parquet"

    def get(self, symbol: str, start: str | None = None, end: str | None = None, refresh: bool = False) -> pd.DataFrame:
        symbol = symbol.upper()
        path = self._path(symbol)
        if path.exists() and not refresh:
            df = read_parquet(path)
        else:
            start = start or default_history_start()
            df = self.fmp.historical_prices(symbol, start=start, end=end)
            if not df.empty:
                write_parquet(df, path)
        if df.empty:
            return df
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").reset_index(drop=True)

    def get_many(self, symbols: list[str], **kwargs) -> dict[str, pd.DataFrame]:
        out = {}
        for s in symbols:
            d = self.get(s, **kwargs)
            if not d.empty:
                out[s] = d
        return out


# ---------------------------------------------------------------------------
# Pure point-in-time helpers (operate on one symbol's price frame)
# ---------------------------------------------------------------------------
def _as_ts(d) -> pd.Timestamp:
    return pd.Timestamp(d)


def bar_on_or_after(prices: pd.DataFrame, d, strictly_after: bool = False) -> pd.Series | None:
    """First bar with date >= d (or > d if strictly_after). None if none exist."""
    if prices.empty:
        return None
    ts = _as_ts(d)
    mask = prices["date"] > ts if strictly_after else prices["date"] >= ts
    sub = prices[mask]
    return None if sub.empty else sub.iloc[0]


def bar_on_or_before(prices: pd.DataFrame, d) -> pd.Series | None:
    """Last bar with date <= d. None if none exist (lookahead-safe 'as of')."""
    if prices.empty:
        return None
    sub = prices[prices["date"] <= _as_ts(d)]
    return None if sub.empty else sub.iloc[-1]


def next_trading_bar(prices: pd.DataFrame, d) -> pd.Series | None:
    """The next bar strictly after date d (e.g. for next-open entry)."""
    return bar_on_or_after(prices, d, strictly_after=True)


def shift_bars(prices: pd.DataFrame, d, n: int) -> pd.Series | None:
    """The bar n trading days after the bar on/after d (n can be negative)."""
    if prices.empty:
        return None
    idx = prices.index[prices["date"] >= _as_ts(d)]
    if len(idx) == 0:
        return None
    pos = idx[0] + n
    if pos < 0 or pos >= len(prices):
        return None
    return prices.iloc[pos]


def price_field(bar: pd.Series | None, field: str) -> float | None:
    if bar is None:
        return None
    val = bar.get(field)
    return None if pd.isna(val) else float(val)


def simple_return(p0: float | None, p1: float | None) -> float | None:
    if p0 is None or p1 is None or p0 == 0:
        return None
    return p1 / p0 - 1.0


def return_between(
    prices: pd.DataFrame, d0, d1, from_field: str = "close", to_field: str = "close"
) -> float | None:
    """Return from ``from_field`` of the bar on/after d0 to ``to_field`` of the
    bar on/before d1. Lookahead-safe."""
    b0 = bar_on_or_after(prices, d0)
    b1 = bar_on_or_before(prices, d1)
    return simple_return(price_field(b0, from_field), price_field(b1, to_field))
