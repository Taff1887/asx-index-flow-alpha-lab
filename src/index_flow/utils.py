"""Small shared utilities: logging, IO, dates, hashing, safe math.

Deliberately dependency-light so every other module can import this without
creating cycles.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_LOG_CONFIGURED = False


def get_logger(name: str = "index_flow") -> logging.Logger:
    """Return a module logger, configuring a sane default handler once."""
    global _LOG_CONFIGURED
    if not _LOG_CONFIGURED:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
            datefmt="%H:%M:%S",
        )
        _LOG_CONFIGURED = True
    return logging.getLogger(name)


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------
def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_csv(df: pd.DataFrame, path: str | Path, index: bool = False) -> Path:
    p = Path(path)
    ensure_dir(p.parent)
    df.to_csv(p, index=index)
    return p


def read_csv(path: str | Path, **kwargs: Any) -> pd.DataFrame:
    return pd.read_csv(path, **kwargs)


def write_parquet(df: pd.DataFrame, path: str | Path) -> Path:
    p = Path(path)
    ensure_dir(p.parent)
    df.to_parquet(p, index=False)
    return p


def read_parquet(path: str | Path) -> pd.DataFrame:
    return pd.read_parquet(path)


def write_json(obj: Any, path: str | Path, indent: int = 2) -> Path:
    p = Path(path)
    ensure_dir(p.parent)
    with p.open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=indent, default=_json_default)
    return p


def read_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _json_default(o: Any) -> Any:
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, Path):
        return str(o)
    raise TypeError(f"Object of type {type(o)} is not JSON serializable")


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------
def to_date(value: Any) -> date | None:
    """Best-effort coercion of strings/timestamps to a ``date``; None on failure."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    ts = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(ts) else ts.date()


def parse_dates(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce")


# ---------------------------------------------------------------------------
# Hashing / IDs
# ---------------------------------------------------------------------------
def stable_hash(*parts: Any, length: int = 12) -> str:
    """Deterministic short hash from any pieces (used for event_ids)."""
    joined = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:length]


# ---------------------------------------------------------------------------
# Safe math
# ---------------------------------------------------------------------------
def safe_div(numer: float, denom: float, default: float = np.nan) -> float:
    try:
        if denom == 0 or denom is None or (isinstance(denom, float) and np.isnan(denom)):
            return default
        return numer / denom
    except (TypeError, ZeroDivisionError):
        return default


def clip01(x: float) -> float:
    """Clip a probability-like number to [0, 1]."""
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return 0.0
    return float(min(1.0, max(0.0, x)))


def pct(x: float, digits: int = 2) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "n/a"
    return f"{x * 100:.{digits}f}%"
