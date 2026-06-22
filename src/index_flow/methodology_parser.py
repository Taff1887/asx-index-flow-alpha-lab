"""Parse index/ETF methodology PDFs into structured rules (best-effort).

Methodology documents are public for most index providers but are prose PDFs.
This extracts the handful of rules the lab cares about — capping, weighting
scheme, rebalance/reconstitution cadence, and eligibility (market-cap /
liquidity) thresholds — via robust regexes. Anything not found is left blank;
we do not guess.

Drop PDFs into ``data/manual/methodology_pdfs/`` named ``<provider>_<index>.pdf``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from .config import Config
from .utils import get_logger

log = get_logger("index_flow.methodology")

try:
    import pdfplumber
except Exception:  # pragma: no cover - declared dep; defensive for slim installs
    pdfplumber = None


_RULES = {
    "max_weight_rules": re.compile(
        r"(cap(?:ped)?|maximum weight|max(?:imum)? individual)[^.\n]{0,60}?(\d{1,2}(?:\.\d+)?\s?%)",
        re.I,
    ),
    "rebalance_frequency": re.compile(
        r"(rebalanc\w+)[^.\n]{0,40}?(quarterly|semi[- ]annually|semi[- ]annual|annually|monthly|daily)",
        re.I,
    ),
    "reconstitution_frequency": re.compile(
        r"(reconstitut\w+|index review)[^.\n]{0,40}?(quarterly|semi[- ]annually|semi[- ]annual|annually|monthly)",
        re.I,
    ),
    "weighting_scheme": re.compile(
        r"(market[- ]capitali[sz]ation[- ]weighted|free[- ]float[- ]adjusted|equal[- ]weighted|"
        r"modified market cap|liquidity[- ]weighted|fundamentally weighted)",
        re.I,
    ),
    "market_cap_rules": re.compile(
        r"(market capitali[sz]ation)[^.\n]{0,60}?((?:US\$|A\$|\$|USD|AUD)?\s?\d[\d,.]*\s?(?:million|billion|m|bn))",
        re.I,
    ),
    "liquidity_rules": re.compile(
        r"(average daily (?:value|volume) traded|adv|liquidity)[^.\n]{0,60}?"
        r"((?:US\$|A\$|\$)?\s?\d[\d,.]*\s?(?:million|m|thousand|k)?)",
        re.I,
    ),
}


def extract_text(pdf_path: str | Path) -> str:
    if pdfplumber is None:
        log.warning("pdfplumber not available; cannot parse %s", pdf_path)
        return ""
    text_parts: list[str] = []
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                text_parts.append(page.extract_text() or "")
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to read %s: %s", pdf_path, exc)
        return ""
    return "\n".join(text_parts)


def parse_rules(text: str) -> dict[str, str | None]:
    out: dict[str, str | None] = {k: None for k in _RULES}
    if not text:
        return out
    for field, rx in _RULES.items():
        m = rx.search(text)
        if m:
            out[field] = " ".join(g for g in m.groups() if g).strip()
    return out


def parse_methodology_pdfs(cfg: Config) -> pd.DataFrame:
    """Parse all PDFs in data/manual/methodology_pdfs/ into a rules table."""
    folder = cfg.path("data_manual") / "methodology_pdfs"
    if not folder.exists():
        return pd.DataFrame()
    rows = []
    for pdf in sorted(folder.glob("*.pdf")):
        text = extract_text(pdf)
        rules = parse_rules(text)
        rules["methodology_file"] = pdf.name
        rules["chars_extracted"] = len(text)
        rows.append(rules)
        log.info("Parsed %s (%d chars)", pdf.name, len(text))
    return pd.DataFrame(rows)
