"""Discover ETFs / benchmark indices and their methodology / holdings links.

Two complementary sources:

* **FMP** — the ETF universe + symbol search. Cheap, reliable, but only knows
  *listed* ETFs and not their benchmark/methodology URLs.
* **Issuer/provider web pages** — fund-list pages that expose benchmark names and
  holdings/methodology links. Often JS-rendered or bot-protected; when a page is
  :class:`~index_flow.web_client.Blocked` we record a *manual TODO* row rather
  than guessing.

Output is a DataFrame with the registry schema and ``source='discovery'`` (low
confidence) suitable for feeding into :func:`index_flow.registry.build_registry`.
"""

from __future__ import annotations

import re

import pandas as pd

from .config import Config
from .fmp_client import FMPClient
from .registry import REGISTRY_COLUMNS, RegistryEntry
from .utils import get_logger
from .web_client import Blocked, WebClient

log = get_logger("index_flow.discovery")

_BENCHMARK_RE = re.compile(r"([A-Z][\w&/. ]{3,60}?(?:Index|Indices|Benchmark))", re.I)
_HOLDINGS_LINK_RE = re.compile(r'href="([^"]+(?:holding|constituent)[^"]*\.(?:csv|xlsx|xls|json))"', re.I)


def discover_from_fmp(cfg: Config, fmp: FMPClient | None = None) -> pd.DataFrame:
    """Pull the FMP ETF universe + theme keyword search as candidate products."""
    fmp = fmp or FMPClient(cfg)
    rows: list[dict] = []

    etfs = fmp.etf_list()
    if not etfs.empty:
        # Restrict to symbols that plausibly touch ASX or global thematics; we
        # cannot know holdings yet, so we keep ALL and tag for later verification.
        for _, r in etfs.iterrows():
            sym = str(r.get("symbol", "")).upper()
            if not sym:
                continue
            rows.append(
                RegistryEntry(
                    product_ticker=sym,
                    record_type="etf",
                    product_name=r.get("name"),
                    exchange=r.get("exchangeShortName") or r.get("exchange"),
                    source="discovery",
                    holdings_file_type="csv",
                ).to_row()
            )

    # Theme keyword search (helps surface niche thematics by name).
    seen = {row["product_ticker"] for row in rows}
    for theme, spec in cfg.watchlists.get("themes", {}).items():
        for kw in spec.get("keywords", []) or []:
            res = fmp.search(kw, limit=25)
            if res.empty or "symbol" not in res.columns:
                continue
            for _, r in res.iterrows():
                sym = str(r.get("symbol", "")).upper()
                if not sym or sym in seen:
                    continue
                seen.add(sym)
                rows.append(
                    RegistryEntry(
                        product_ticker=sym,
                        record_type="etf",
                        product_name=r.get("name"),
                        exchange=r.get("exchangeShortName") or r.get("stockExchange"),
                        theme=theme,
                        source="discovery",
                    ).to_row()
                )

    return pd.DataFrame(rows, columns=REGISTRY_COLUMNS)


def discover_from_providers(cfg: Config, web: WebClient | None = None) -> pd.DataFrame:
    """Best-effort scrape of issuer fund-list pages for benchmark/holdings links.

    Blocked pages produce a single ``record_type='index'`` TODO row pointing the
    user at the manual ingestion path — we never invent the data.
    """
    web = web or WebClient(cfg)
    rows: list[dict] = []

    for issuer in cfg.providers.get("etf_issuers", []):
        url = issuer.get("fund_list_hint") or issuer.get("home_url")
        if not url:
            continue
        try:
            html = web.get(url)
        except Blocked as exc:
            log.info("Discovery blocked for %s (%s) -> manual TODO", issuer["key"], exc)
            rows.append(
                RegistryEntry(
                    product_ticker=f"TODO:{issuer['key']}",
                    record_type="index",
                    issuer=issuer.get("name"),
                    announcement_source=url,
                    holdings_file_type="manual",
                    source="discovery",
                    max_weight_rules="BLOCKED: provide holdings via data/manual/holdings_snapshots/",
                ).to_row()
            )
            continue
        except Exception as exc:  # noqa: BLE001
            log.warning("Discovery error for %s: %s", issuer["key"], exc)
            continue

        text = html if isinstance(html, str) else html.decode("utf-8", "ignore")
        benchmarks = sorted(set(m.group(1).strip() for m in _BENCHMARK_RE.finditer(text)))[:50]
        holdings_links = sorted(set(_HOLDINGS_LINK_RE.findall(text)))[:50]
        log.info(
            "%s: found %d benchmark mentions, %d holdings links",
            issuer["key"], len(benchmarks), len(holdings_links),
        )
        for bm in benchmarks:
            rows.append(
                RegistryEntry(
                    product_ticker=f"BENCH:{issuer['key']}:{abs(hash(bm)) % 100000}",
                    record_type="index",
                    issuer=issuer.get("name"),
                    benchmark_index_name=bm,
                    announcement_source=url,
                    source="discovery",
                ).to_row()
            )

    return pd.DataFrame(rows, columns=REGISTRY_COLUMNS)


def discover(cfg: Config, use_web: bool = True) -> pd.DataFrame:
    """Run both discovery sources and concatenate. FMP is always tried; web is
    optional (skip in CI/offline)."""
    parts = [discover_from_fmp(cfg)]
    if use_web:
        try:
            parts.append(discover_from_providers(cfg))
        except Exception as exc:  # noqa: BLE001
            log.warning("Web discovery skipped: %s", exc)
    out = pd.concat([p for p in parts if not p.empty], ignore_index=True) if any(
        not p.empty for p in parts
    ) else pd.DataFrame(columns=REGISTRY_COLUMNS)
    return out
