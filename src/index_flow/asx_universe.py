"""ASX ticker normalisation and universe handling.

Holdings files from global products reference ASX names in many formats:
``PDN``, ``PDN AU``, ``PDN.AX``, ``ASX:PDN``, ``PDN:AU``, ``PDN AU Equity``,
``PDN-AU``. CDIs (e.g. dual-listed names) sometimes carry the same code. This
module canonicalises everything to FMP's ``CODE.AX`` form and decides whether a
constituent is ASX-listed.
"""

from __future__ import annotations

import re

import pandas as pd

from .config import Config
from .fmp_client import FMPClient
from .utils import get_logger

log = get_logger("index_flow.universe")

# Tokens in an exchange/country field that indicate ASX listing.
_AU_EXCHANGE_TOKENS = {
    "ASX", "AX", "AU", "XASX", "AUSTRALIA", "AUSTRALIAN SECURITIES EXCHANGE",
    "AUSTRALIAN", "AUD", "SYDNEY",
}
# Suffix tokens appended to tickers by data vendors meaning "Australia".
_AU_SUFFIXES = ("AX", "AU", "AT")
_PREFIXES = ("ASX", "AX")

# An ASX ordinary code: 3 (sometimes 2-4) alphanumeric chars, often with digits
# (e.g. 360, 29M, 4DS, A2M, BHP, CSL).
_CODE_RE = re.compile(r"^[A-Z0-9]{2,4}$")


def normalise_asx(raw: str | None) -> str | None:
    """Return ``CODE.AX`` for any recognisable ASX ticker, else ``None``."""
    if raw is None:
        return None
    s = str(raw).strip().upper()
    if not s or s in ("NAN", "NONE", "CASH", "USD", "AUD"):
        return None

    # Drop trailing vendor words like "EQUITY"
    s = re.sub(r"\s+(EQUITY|EQ|ORD|FPO|CDI)\b.*$", "", s)

    # Strip an exchange prefix: "ASX:PDN" / "AX:PDN"
    for pre in _PREFIXES:
        if s.startswith(pre + ":"):
            s = s[len(pre) + 1:]
            break

    # Separator-based suffixes: PDN.AX / PDN:AU / PDN-AU / "PDN AU"
    token = None
    for sep in (".", ":", "-", " "):
        if sep in s:
            head, _, tail = s.rpartition(sep)
            tail = tail.strip()
            if tail in _AU_SUFFIXES and head:
                s = head.strip()
                token = tail
                break
            # If the tail isn't an AU suffix but is some other 2-letter exchange,
            # it's not ASX.
            if len(tail) == 2 and tail.isalpha() and tail not in _AU_SUFFIXES:
                return None
    code = s.replace(" ", "")
    if not _CODE_RE.match(code):
        return None
    return f"{code}.AX"


def looks_like_asx(ticker: str | None, name: str | None = None, exchange: str | None = None) -> bool:
    """Heuristic: does this constituent look ASX-listed?

    Strong signal: an explicit AU/ASX exchange token or an AU/AX suffix.
    Weak fallback: a bare 3-char code with no other exchange info is *ambiguous*
    and returns False (we prefer false negatives over fabricated ASX events).
    """
    ex = (exchange or "").strip().upper()
    if ex:
        if any(tok in ex.split() or tok == ex for tok in _AU_EXCHANGE_TOKENS):
            return True
        # An explicit non-AU exchange means not ASX.
        return False
    t = (ticker or "").strip().upper()
    for sep in (".", ":", "-", " "):
        if sep in t:
            tail = t.rpartition(sep)[2].strip()
            if tail in _AU_SUFFIXES:
                return True
    return False


def map_constituent_to_asx(
    ticker: str | None, name: str | None = None, exchange: str | None = None
) -> str | None:
    """Return the normalised ASX symbol for a holdings constituent, or None."""
    if not looks_like_asx(ticker, name, exchange):
        # Still attempt suffix-based normalisation (covers "PDN AU" with no
        # exchange column), but a bare code without AU context is rejected.
        norm = normalise_asx(ticker)
        if norm and any(sep in str(ticker).upper() for sep in (".", ":", "-", " ")):
            return norm
        return None
    return normalise_asx(ticker)


def tag_asx_constituents(holdings: pd.DataFrame) -> pd.DataFrame:
    """Add ``asx_symbol`` + ``is_asx`` columns to a holdings/diff frame."""
    df = holdings.copy()
    ex_col = "exchange" if "exchange" in df.columns else None
    df["asx_symbol"] = [
        map_constituent_to_asx(
            row.get("constituent_ticker"),
            row.get("constituent_name"),
            row.get(ex_col) if ex_col else None,
        )
        for _, row in df.iterrows()
    ]
    df["is_asx"] = df["asx_symbol"].notna()
    return df


class ASXUniverse:
    """Optional validation against the live ASX symbol set from FMP."""

    def __init__(self, cfg: Config, fmp: FMPClient | None = None):
        self.cfg = cfg
        self.fmp = fmp or FMPClient(cfg)
        self._symbols: set[str] | None = None

    def symbols(self) -> set[str]:
        if self._symbols is None:
            data = self.fmp.get_json("symbol/available-traded/list")  # may be premium
            syms: set[str] = set()
            if isinstance(data, list):
                for r in data:
                    sym = str(r.get("symbol", "")).upper()
                    if sym.endswith(".AX"):
                        syms.add(sym)
            self._symbols = syms
            if not syms:
                log.info("ASX universe unavailable from FMP (offline/plan); skipping validation")
        return self._symbols

    def is_valid(self, symbol: str) -> bool:
        syms = self.symbols()
        # If we couldn't load the universe, don't reject (return True).
        return True if not syms else symbol.upper() in syms
