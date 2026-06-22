"""Shared pytest fixtures.

A ``cfg`` rooted in a tmp dir (real config values, sandboxed paths) plus helpers
to fabricate *price fixtures for tests only* — this is unit-test scaffolding, not
research data, so synthetic prices here are appropriate and never used to make
research claims.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from index_flow.config import Config, load_config
from index_flow.price_data import PRICE_COLUMNS


@pytest.fixture
def cfg(tmp_path) -> Config:
    real = load_config()
    c = Config(
        root=tmp_path,
        config=real.config,
        providers=real.providers,
        strategy_params=real.strategy_params,
        watchlists=real.watchlists,
        env={},
    )
    c.ensure_dirs()
    return c


def make_prices(closes: list[float], start: str = "2023-01-02",
                volume: float = 1_000_000.0, gap: float = 0.0) -> pd.DataFrame:
    """Build a tidy OHLCV frame from a list of closes on consecutive bdays.

    ``gap`` opens each bar at prev_close*(1+gap); first bar opens at its close.
    """
    dates = pd.bdate_range(start=start, periods=len(closes))
    rows = []
    prev = closes[0]
    for i, (d, c) in enumerate(zip(dates, closes)):
        o = prev * (1 + gap) if i > 0 else c
        hi = max(o, c) * 1.01
        lo = min(o, c) * 0.99
        rows.append(
            {
                "date": d, "open": o, "high": hi, "low": lo, "close": c,
                "adj_close": c, "volume": volume, "vwap": (hi + lo + c) / 3,
                "dollar_volume": c * volume,
            }
        )
        prev = c
    return pd.DataFrame(rows)[PRICE_COLUMNS]


@pytest.fixture
def make_prices_fixture():
    return make_prices


def write_prices(cfg: Config, symbol: str, df: pd.DataFrame) -> Path:
    """Persist a price frame where PriceStore.get will read it as cache."""
    d = cfg.path("data_processed") / "prices"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{symbol.upper()}.parquet"
    df.to_parquet(path, index=False)
    return path


@pytest.fixture
def write_prices_fixture():
    return write_prices
