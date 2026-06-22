"""Acquire and normalise ETF/index holdings snapshots.

A *snapshot* is the full constituent list of a product on a given date. Diffs of
successive snapshots are the rawest, highest-signal flow source in this lab.

Acquisition order per product:
1. **Manual** drop-ins in ``data/manual/holdings_snapshots/<PRODUCT>/<DATE>.csv``
   (most trusted; the only way to get *history* for most products).
2. **FMP** ``etf-holder`` (current holdings) where the product is on FMP.
3. **Direct download** of a stable ``holdings_url`` (csv/xlsx/json) from the
   registry, via the polite web client.

Everything is normalised to a canonical long schema and stored as dated parquet
snapshots under ``data/processed/holdings_snapshots/<PRODUCT>/``.
"""

from __future__ import annotations

import io
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from .config import Config
from .fmp_client import FMPClient
from .utils import get_logger, read_csv, write_parquet
from .web_client import Blocked, WebClient

log = get_logger("index_flow.holdings")

# Canonical snapshot schema.
HOLDINGS_COLUMNS = [
    "product_ticker",
    "as_of_date",
    "constituent_ticker",
    "constituent_name",
    "weight_pct",        # fraction in [0,1]  (15% -> 0.15)
    "shares",
    "market_value",
    "source",            # manual | fmp | download
]

# Column-name variants seen across issuers, mapped to canonical names.
_TICKER_KEYS = ["ticker", "symbol", "asset", "issuer ticker", "security ticker", "holding ticker", "code", "asx code"]
_NAME_KEYS = ["name", "security name", "holding", "company", "description", "constituent", "security"]
_WEIGHT_KEYS = [
    "weight", "weight (%)", "weight%", "% weight", "weightpercentage",
    "% of net assets", "% of fund", "% net assets", "portfolio weight", "weighting",
]
_SHARES_KEYS = ["shares", "quantity", "sharesnumber", "shares held", "units", "quantity held"]
_VALUE_KEYS = ["market value", "marketvalue", "value", "market value (aud)", "notional value"]


def _pick(cols_lower: dict[str, str], keys: list[str]) -> str | None:
    for k in keys:
        if k in cols_lower:
            return cols_lower[k]
    # fuzzy contains
    for k in keys:
        for low, orig in cols_lower.items():
            if k in low:
                return orig
    return None


def _coerce_weight(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(
        series.astype(str).str.replace("%", "", regex=False).str.replace(",", "", regex=False),
        errors="coerce",
    )
    # If values look like percents (e.g. 0.5..100) convert to fraction.
    if s.dropna().abs().max() is not None and s.dropna().abs().max() > 1.5:
        s = s / 100.0
    return s


def normalise_holdings(
    df: pd.DataFrame, product: str, as_of: str | date, source: str
) -> pd.DataFrame:
    """Map an arbitrary issuer holdings frame onto :data:`HOLDINGS_COLUMNS`."""
    if df is None or df.empty:
        return pd.DataFrame(columns=HOLDINGS_COLUMNS)
    cols_lower = {str(c).strip().lower(): c for c in df.columns}
    tcol = _pick(cols_lower, _TICKER_KEYS)
    ncol = _pick(cols_lower, _NAME_KEYS)
    wcol = _pick(cols_lower, _WEIGHT_KEYS)
    scol = _pick(cols_lower, _SHARES_KEYS)
    vcol = _pick(cols_lower, _VALUE_KEYS)

    out = pd.DataFrame()
    out["constituent_ticker"] = (
        df[tcol].astype(str).str.strip().str.upper() if tcol else pd.Series([None] * len(df))
    )
    out["constituent_name"] = df[ncol].astype(str).str.strip() if ncol else None
    out["weight_pct"] = _coerce_weight(df[wcol]) if wcol else pd.NA
    out["shares"] = pd.to_numeric(df[scol], errors="coerce") if scol else pd.NA
    out["market_value"] = (
        pd.to_numeric(df[vcol].astype(str).str.replace(",", "", regex=False), errors="coerce")
        if vcol else pd.NA
    )
    out["product_ticker"] = str(product).upper()
    out["as_of_date"] = pd.to_datetime(as_of).date()
    out["source"] = source

    # Drop rows with neither a ticker nor a name (footers/cash lines often blank).
    out = out[~(out["constituent_ticker"].isna() & out["constituent_name"].isna())]
    out = out[out["constituent_ticker"].fillna("").str.lower() != "nan"]
    return out[HOLDINGS_COLUMNS].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Snapshot storage
# ---------------------------------------------------------------------------
def snapshot_dir(cfg: Config, product: str) -> Path:
    return cfg.path("data_processed") / "holdings_snapshots" / str(product).upper()


def save_snapshot(cfg: Config, product: str, as_of: str | date, df: pd.DataFrame) -> Path:
    norm = df if list(df.columns) == HOLDINGS_COLUMNS else df
    d = pd.to_datetime(as_of).date()
    path = snapshot_dir(cfg, product) / f"{d.isoformat()}.parquet"
    write_parquet(norm, path)
    return path


def list_snapshots(cfg: Config, product: str) -> list[tuple[date, Path]]:
    folder = snapshot_dir(cfg, product)
    if not folder.exists():
        return []
    out = []
    for p in sorted(folder.glob("*.parquet")):
        try:
            d = datetime.fromisoformat(p.stem).date()
        except ValueError:
            continue
        out.append((d, p))
    return out


# ---------------------------------------------------------------------------
# Manual ingestion
# ---------------------------------------------------------------------------
def _date_from_filename(name: str) -> date | None:
    stem = Path(name).stem
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(stem, fmt).date()
        except ValueError:
            continue
    return None


def ingest_manual_snapshots(cfg: Config, product: str | None = None) -> int:
    """Read CSVs from data/manual/holdings_snapshots/<PRODUCT>/<DATE>.csv,
    normalise, and store as parquet snapshots. Returns count ingested."""
    base = cfg.path("data_manual") / "holdings_snapshots"
    if not base.exists():
        return 0
    count = 0
    product_dirs = [base / product.upper()] if product else [p for p in base.iterdir() if p.is_dir()]
    for pdir in product_dirs:
        if not pdir.exists():
            continue
        prod = pdir.name.upper()
        for csv in sorted(pdir.glob("*.csv")):
            if csv.name.startswith("_TEMPLATE"):
                continue
            as_of = _date_from_filename(csv.name)
            if as_of is None:
                log.warning("Skip %s: filename is not a date (use YYYY-MM-DD.csv)", csv)
                continue
            try:
                raw = read_csv(csv)
            except Exception as exc:  # noqa: BLE001
                log.warning("Could not read %s: %s", csv, exc)
                continue
            norm = normalise_holdings(raw, prod, as_of, source="manual")
            if norm.empty:
                log.warning("No usable rows in %s after normalisation", csv)
                continue
            save_snapshot(cfg, prod, as_of, norm)
            count += 1
            log.info("Ingested manual snapshot %s @ %s (%d holdings)", prod, as_of, len(norm))
    return count


# ---------------------------------------------------------------------------
# Automated acquisition
# ---------------------------------------------------------------------------
def fetch_fmp_current(cfg: Config, product: str, fmp: FMPClient | None = None) -> pd.DataFrame:
    """Current holdings via FMP etf-holder, normalised. Empty if unavailable."""
    fmp = fmp or FMPClient(cfg)
    raw = fmp.etf_holder(product)
    if raw.empty:
        return pd.DataFrame(columns=HOLDINGS_COLUMNS)
    today = datetime.now(timezone.utc).date()
    return normalise_holdings(raw, product, today, source="fmp")


def _read_download(content: bytes, file_type: str) -> pd.DataFrame:
    bio = io.BytesIO(content)
    ft = (file_type or "").lower()
    if ft in ("xlsx", "xls"):
        return pd.read_excel(bio)
    if ft == "json":
        return pd.read_json(bio)
    # default: try csv, skipping junk preamble rows issuers prepend
    for skip in (0, 1, 2, 3, 4, 5):
        try:
            bio.seek(0)
            df = pd.read_csv(bio, skiprows=skip)
            if df.shape[1] >= 2 and len(df) > 1:
                return df
        except Exception:  # noqa: BLE001
            continue
    return pd.DataFrame()


def download_from_url(
    cfg: Config, product: str, url: str, file_type: str, web: WebClient | None = None
) -> pd.DataFrame:
    """Download + normalise a stable holdings file. Raises nothing — returns
    empty (and logs) on Blocked/parse failure so callers can fall back."""
    web = web or WebClient(cfg)
    try:
        content = web.get(url, binary=True)
    except Blocked as exc:
        log.info("Holdings download blocked for %s: %s -> use manual", product, exc)
        return pd.DataFrame(columns=HOLDINGS_COLUMNS)
    except Exception as exc:  # noqa: BLE001
        log.warning("Holdings download error for %s: %s", product, exc)
        return pd.DataFrame(columns=HOLDINGS_COLUMNS)
    raw = _read_download(content if isinstance(content, bytes) else content.encode(), file_type)
    today = datetime.now(timezone.utc).date()
    return normalise_holdings(raw, product, today, source="download")


def acquire_all(cfg: Config, registry: pd.DataFrame, fmp: FMPClient | None = None) -> dict:
    """Run the full acquisition pass over the registry. Returns a summary dict.

    Order: manual ingest (all) -> per-product FMP current -> registry holdings_url.
    """
    summary = {"manual": 0, "fmp": 0, "download": 0, "blocked_or_missing": []}
    summary["manual"] = ingest_manual_snapshots(cfg)

    fmp = fmp or FMPClient(cfg)
    web = WebClient(cfg)
    etfs = registry[registry["record_type"] == "etf"] if "record_type" in registry else registry

    for _, row in etfs.iterrows():
        product = str(row["product_ticker"]).upper()
        if product.startswith(("TODO:", "BENCH:", "PROVIDER:")):
            continue
        got = False

        # FMP current holdings
        cur = fetch_fmp_current(cfg, product, fmp)
        if not cur.empty:
            save_snapshot(cfg, product, cur["as_of_date"].iloc[0], cur)
            summary["fmp"] += 1
            got = True

        # registry holdings_url direct download
        url = row.get("holdings_url")
        ftype = row.get("holdings_file_type")
        if isinstance(url, str) and url.strip() and ftype not in ("manual", None, ""):
            dl = download_from_url(cfg, product, url, str(ftype), web)
            if not dl.empty:
                save_snapshot(cfg, product, dl["as_of_date"].iloc[0], dl)
                summary["download"] += 1
                got = True

        if not got:
            summary["blocked_or_missing"].append(product)

    log.info(
        "Holdings acquisition: %d manual, %d fmp, %d download, %d need manual",
        summary["manual"], summary["fmp"], summary["download"], len(summary["blocked_or_missing"]),
    )
    return summary
