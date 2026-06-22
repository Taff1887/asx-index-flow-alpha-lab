"""Financial Modeling Prep (FMP) client.

Covers what the lab needs from FMP: ASX OHLCV, market caps, company profiles,
sectors/industries, volume/liquidity, and (best-effort, premium) ETF holdings.

Design notes
------------
* Every call is **cached on disk** under ``data/raw/fmp/`` keyed by endpoint +
  params. Within ``cache_ttl_days`` the cache is reused; older entries are
  refreshed when an API key is present.
* **Offline / no key**: the client never fabricates data. If there is no API key
  it serves whatever is cached (regardless of age, with a warning) and otherwise
  returns an empty frame and logs exactly what was missing.
* All public price methods return tidy DataFrames with snake_case columns and a
  proper ``date`` dtype, so downstream code is insulated from FMP's JSON shapes.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from .config import Config
from .utils import get_logger, read_json, stable_hash, write_json

log = get_logger("index_flow.fmp")


class FMPClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.api_key = cfg.fmp_api_key
        self.base_url = cfg.fmp_base_url.rstrip("/")
        fmp_cfg = cfg.config.get("fmp", {})
        self.cache_dir = cfg.resolve(fmp_cfg.get("cache_dir", "data/raw/fmp"))
        self.cache_ttl_days = float(fmp_cfg.get("cache_ttl_days", 1))
        self.max_retries = int(fmp_cfg.get("max_retries", 4))
        self.backoff = float(fmp_cfg.get("backoff_seconds", 1.5))
        self.suffix = fmp_cfg.get("suffix", ".AX")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._session = requests.Session()
        if not self.api_key:
            log.warning(
                "No FMP_API_KEY set — FMPClient runs in OFFLINE mode "
                "(serves cache only, never fabricates)."
            )

    # ------------------------------------------------------------------ core
    def _cache_path(self, endpoint: str, params: dict[str, Any]) -> Path:
        safe = endpoint.strip("/").replace("/", "__")
        key = stable_hash(safe, sorted((params or {}).items()))
        return self.cache_dir / safe / f"{key}.json"

    def _cache_fresh(self, path: Path) -> bool:
        if not path.exists():
            return False
        age_days = (time.time() - path.stat().st_mtime) / 86400.0
        return age_days <= self.cache_ttl_days

    def get_json(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        force: bool = False,
    ) -> Any:
        """GET ``{base}/{endpoint}`` (FMP *stable* API) with disk caching.
        Returns parsed JSON (list/dict) or ``None`` when unavailable offline."""
        params = dict(params or {})
        cache_path = self._cache_path(endpoint, params)

        if not force and self._cache_fresh(cache_path):
            return read_json(cache_path)

        if not self.api_key:
            if cache_path.exists():
                log.warning("Offline: serving stale cache for %s", endpoint)
                return read_json(cache_path)
            log.warning("Offline and no cache for %s — returning None", endpoint)
            return None

        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        call_params = {**params, "apikey": self.api_key}
        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._session.get(url, params=call_params, timeout=30)
                if resp.status_code == 429:
                    wait = self.backoff * attempt
                    log.warning("Rate limited (429); sleeping %.1fs", wait)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                write_json(data, cache_path)
                return data
            except Exception as exc:  # noqa: BLE001 - want to retry on anything transient
                last_err = exc
                time.sleep(self.backoff * attempt)
        log.error("FMP GET failed for %s after %d tries: %s", endpoint, self.max_retries, last_err)
        if cache_path.exists():
            return read_json(cache_path)
        return None

    # -------------------------------------------------------------- prices
    def historical_prices(
        self,
        symbol: str,
        start: str | date | None = None,
        end: str | date | None = None,
        force: bool = False,
    ) -> pd.DataFrame:
        """Daily OHLCV for ``symbol`` (already suffixed, e.g. 'PDN.AX').

        Returns columns: date, open, high, low, close, adj_close, volume, vwap,
        dollar_volume. Empty DataFrame (typed) if nothing is available."""
        params: dict[str, Any] = {"symbol": symbol}
        if start:
            params["from"] = str(start)
        if end:
            params["to"] = str(end)
        # FMP stable: historical-price-eod/full returns a FLAT list of bars.
        data = self.get_json("historical-price-eod/full", params, force=force)
        rows = data if isinstance(data, list) else (data or {}).get("historical", []) if isinstance(data, dict) else []
        if not rows:
            return _empty_price_frame()
        df = pd.DataFrame(rows)
        rename = {
            "date": "date",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "adjClose": "adj_close",
            "volume": "volume",
            "vwap": "vwap",
        }
        df = df.rename(columns=rename)
        keep = [c for c in rename.values() if c in df.columns]
        df = df[keep].copy()
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
        if "adj_close" not in df.columns:
            df["adj_close"] = df.get("close")
        if "vwap" not in df.columns:
            df["vwap"] = (df["high"] + df["low"] + df["close"]) / 3.0
        df["symbol"] = symbol
        df["dollar_volume"] = df["close"] * df["volume"]
        return df

    def profile(self, symbol: str, force: bool = False) -> dict[str, Any]:
        data = self.get_json("profile", {"symbol": symbol}, force=force)
        if isinstance(data, list) and data:
            return data[0]
        return {}

    def profiles(self, symbols: list[str]) -> pd.DataFrame:
        """Company profiles for many symbols -> tidy frame (sector/industry/mcap)."""
        records = []
        for sym in symbols:
            p = self.profile(sym)
            if not p:
                continue
            records.append(
                {
                    "symbol": sym,
                    "company_name": p.get("companyName"),
                    "sector": p.get("sector"),
                    "industry": p.get("industry"),
                    "market_cap": p.get("marketCap", p.get("mktCap")),
                    "exchange": p.get("exchange", p.get("exchangeShortName")),
                    "currency": p.get("currency"),
                    "country": p.get("country"),
                    "is_etf": p.get("isEtf"),
                    "is_active": p.get("isActivelyTrading"),
                    "avg_volume": p.get("averageVolume", p.get("volAvg")),
                }
            )
        if not records:
            return pd.DataFrame(
                columns=[
                    "symbol", "company_name", "sector", "industry", "market_cap",
                    "exchange", "currency", "country", "is_etf", "is_active", "avg_volume",
                ]
            )
        return pd.DataFrame.from_records(records)

    def historical_market_cap(self, symbol: str, limit: int = 2000, force: bool = False) -> pd.DataFrame:
        data = self.get_json(
            "historical-market-capitalization", {"symbol": symbol, "limit": limit}, force=force
        )
        if not isinstance(data, list) or not data:
            return pd.DataFrame(columns=["symbol", "date", "market_cap"])
        df = pd.DataFrame(data).rename(columns={"marketCap": "market_cap"})
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"]).sort_values("date")
        df["symbol"] = symbol
        return df[["symbol", "date", "market_cap"]].reset_index(drop=True)

    # ----------------------------------------------------- ETF holdings (stable)
    def etf_holder(self, etf_symbol: str, force: bool = False) -> pd.DataFrame:
        """CURRENT ETF holdings via stable etf/holdings. Returns asset, name,
        weight%, shares, market value. Empty if unavailable on your plan."""
        data = self.get_json("etf/holdings", {"symbol": etf_symbol}, force=force)
        if not isinstance(data, list) or not data:
            return pd.DataFrame(columns=["asset", "name", "weight_pct", "shares", "market_value"])
        df = pd.DataFrame(data).rename(
            columns={
                "asset": "asset",
                "name": "name",
                "weightPercentage": "weight_pct",
                "sharesNumber": "shares",
                "marketValue": "market_value",
            }
        )
        cols = [c for c in ["asset", "name", "weight_pct", "shares", "market_value"] if c in df.columns]
        return df[cols].copy()

    def etf_holdings_historical(self, etf_symbol: str, as_of: str | date, force: bool = False) -> pd.DataFrame:
        """Historical ETF holdings by date are not exposed on the stable plan;
        use the manual snapshot ingestion path instead. Returns empty."""
        log.info("Historical etf-holdings for %s @ %s requires manual ingestion", etf_symbol, as_of)
        return pd.DataFrame()

    # ------------------------------------------------------------- discovery
    def search(self, query: str, limit: int = 50, exchange: str | None = None) -> pd.DataFrame:
        params: dict[str, Any] = {"query": query, "limit": limit}
        if exchange:
            params["exchange"] = exchange
        data = self.get_json("search-symbol", params)
        return pd.DataFrame(data) if isinstance(data, list) and data else pd.DataFrame()

    def etf_list(self) -> pd.DataFrame:
        data = self.get_json("etf-list")
        return pd.DataFrame(data) if isinstance(data, list) and data else pd.DataFrame()


def _empty_price_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "date", "open", "high", "low", "close", "adj_close",
            "volume", "vwap", "symbol", "dollar_volume",
        ]
    )


def default_history_start(years: int = 12) -> str:
    """Default lookback start (FMP-friendly YYYY-MM-DD)."""
    return (datetime.now(timezone.utc) - timedelta(days=365 * years)).strftime("%Y-%m-%d")
