"""Rebalance / index-change announcement ingestion.

Authoritative add/delete announcements from index providers are largely licensed
or PDF/JS-gated, so the **trusted** path is manual: drop the public announcement
rows into ``data/manual/rebalance_announcements/*.csv`` (see the template there).

For genuinely public announcement *pages* you are permitted to read, the
best-effort scraper below fetches a page and extracts low-confidence (ASX ticker,
action, date) candidates via regex — always marked for manual verification, never
emitted as fact.
"""

from __future__ import annotations

import re
from datetime import date

import pandas as pd

from .asx_universe import normalise_asx
from .config import Config
from .rebalance_calendar import load_manual_rebalance_announcements
from .utils import get_logger, to_date
from .web_client import Blocked, WebClient

log = get_logger("index_flow.announcements")

ANNOUNCEMENT_COLUMNS = [
    "provider", "index_name", "asx_ticker", "company_name", "action",
    "announcement_date", "effective_date", "source_url", "confidence", "needs_verification",
]

_ADD_WORDS = re.compile(r"\b(add(?:ed|ition)?|inclu(?:de|sion)|join)\b", re.I)
_DEL_WORDS = re.compile(r"\b(delet(?:e|ion)|remov(?:e|al)|exclu(?:de|sion))\b", re.I)
_CODE_TOKEN = re.compile(r"\b(?:ASX[:\s]+)?([A-Z0-9]{3})\b")
_DATE_TOKEN = re.compile(
    r"\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}|"
    r"\d{4}-\d{2}-\d{2})\b",
    re.I,
)


def parse_announcement_text(
    text: str, provider: str | None = None, index_name: str | None = None,
    source_url: str | None = None,
) -> pd.DataFrame:
    """Extract low-confidence add/delete candidates from announcement prose."""
    rows: list[dict] = []
    if not text:
        return pd.DataFrame(columns=ANNOUNCEMENT_COLUMNS)
    dates = [to_date(m.group(1)) for m in _DATE_TOKEN.finditer(text)]
    eff = next((d for d in dates if d), None)
    for line in re.split(r"[\n\.]", text):
        is_add = bool(_ADD_WORDS.search(line))
        is_del = bool(_DEL_WORDS.search(line))
        if not (is_add or is_del):
            continue
        for m in _CODE_TOKEN.finditer(line):
            sym = normalise_asx(m.group(1))
            if not sym:
                continue
            rows.append(
                {
                    "provider": provider,
                    "index_name": index_name,
                    "asx_ticker": sym,
                    "company_name": None,
                    "action": "ADD" if is_add else "DELETE",
                    "announcement_date": eff,
                    "effective_date": None,
                    "source_url": source_url,
                    "confidence": 0.2,           # regex guess — low
                    "needs_verification": True,
                }
            )
    return pd.DataFrame(rows, columns=ANNOUNCEMENT_COLUMNS).drop_duplicates()


def scrape_announcement_url(cfg: Config, url: str, provider: str | None = None,
                            index_name: str | None = None, web: WebClient | None = None) -> pd.DataFrame:
    """Fetch a public announcement page and parse candidates. Blocked -> empty."""
    web = web or WebClient(cfg)
    try:
        html = web.get(url)
    except Blocked as exc:
        log.info("Announcement page blocked (%s); use manual ingestion", exc)
        return pd.DataFrame(columns=ANNOUNCEMENT_COLUMNS)
    text = html if isinstance(html, str) else html.decode("utf-8", "ignore")
    text = re.sub(r"<[^>]+>", " ", text)  # strip tags
    return parse_announcement_text(text, provider, index_name, url)


def load_manual_announcements(cfg: Config) -> pd.DataFrame:
    """Normalise manual rebalance-announcement CSVs to ANNOUNCEMENT_COLUMNS."""
    raw = load_manual_rebalance_announcements(cfg)
    if raw.empty:
        return pd.DataFrame(columns=ANNOUNCEMENT_COLUMNS)
    df = raw.copy()
    lower = {c.lower().strip(): c for c in df.columns}

    def col(*names, default=None):
        for n in names:
            if n in lower:
                return df[lower[n]]
        return pd.Series([default] * len(df))

    out = pd.DataFrame(
        {
            "provider": col("provider", "index_provider"),
            "index_name": col("index_name", "index", "benchmark"),
            "asx_ticker": col("asx_ticker", "ticker", "symbol").map(normalise_asx),
            "company_name": col("company_name", "name"),
            "action": col("action", "change", "event").astype(str).str.upper().str.strip(),
            "announcement_date": col("announcement_date", "ann_date", "date").map(to_date),
            "effective_date": col("effective_date", "eff_date").map(to_date),
            "source_url": col("source_url", "source", "url"),
            "confidence": col("confidence", default=0.9),
            "needs_verification": col("needs_verification", default=False),
        }
    )
    out["action"] = out["action"].replace(
        {"ADDED": "ADD", "ADDITION": "ADD", "INCLUDE": "ADD", "INCLUSION": "ADD",
         "DELETED": "DELETE", "DELETION": "DELETE", "REMOVE": "DELETE", "REMOVAL": "DELETE"}
    )
    return out[ANNOUNCEMENT_COLUMNS]
