"""Index / ETF registry.

The registry is the catalogue of products and benchmarks that *could* force
buying/selling into ASX names. It is assembled from three sources, in increasing
order of trust:

1. **Seeds** from ``configs/watchlists.yaml`` (themes -> candidate products) and
   ``configs/providers.yaml`` (provider/issuer reference metadata). These are
   *unverified candidates* — low confidence, blank where unknown.
2. **Discovery** output (``provider_discovery.py``) — products/benchmarks found
   on issuer pages.
3. **Manual** CSVs dropped into ``data/manual/etf_registry/`` and
   ``data/manual/index_registry/`` — highest trust; these override seeds.

Nothing here invents AUM / holdings counts / methodology URLs. Unknown fields
are left blank and the ``confidence_score`` reflects how complete/verified the
row is.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

import numpy as np
import pandas as pd

from .config import Config
from .utils import get_logger, read_csv, write_csv

log = get_logger("index_flow.registry")

# Canonical column order for both the ETF and index registries (shared schema).
REGISTRY_COLUMNS = [
    "record_type",                    # "etf" | "index"
    "product_ticker",
    "product_name",
    "issuer",
    "exchange",
    "benchmark_index_name",
    "index_provider",
    "theme",
    "region",                         # country / region exposure
    "methodology_url",
    "holdings_url",
    "holdings_file_type",             # csv | xlsx | json | pdf | manual
    "holdings_update_frequency",      # daily | monthly | quarterly | ...
    "rebalance_frequency",
    "reconstitution_frequency",
    "rebalance_months",               # e.g. "3,6,9,12"
    "announcement_source",
    "aum",
    "management_fee",
    "number_of_holdings",
    "max_weight_rules",
    "liquidity_rules",
    "market_cap_rules",
    "weighting_scheme",
    "asx_exposure_flag",              # bool: known/likely to hold ASX names
    "current_asx_holdings_count",
    "historical_data_available_flag",
    "source",                         # provenance: seed | discovery | manual
    "confidence_score",               # 0..1 completeness/verification
]


@dataclass
class RegistryEntry:
    product_ticker: str
    record_type: str = "etf"
    product_name: str | None = None
    issuer: str | None = None
    exchange: str | None = None
    benchmark_index_name: str | None = None
    index_provider: str | None = None
    theme: str | None = None
    region: str | None = None
    methodology_url: str | None = None
    holdings_url: str | None = None
    holdings_file_type: str | None = None
    holdings_update_frequency: str | None = None
    rebalance_frequency: str | None = None
    reconstitution_frequency: str | None = None
    rebalance_months: str | None = None
    announcement_source: str | None = None
    aum: float | None = None
    management_fee: float | None = None
    number_of_holdings: float | None = None
    max_weight_rules: str | None = None
    liquidity_rules: str | None = None
    market_cap_rules: str | None = None
    weighting_scheme: str | None = None
    asx_exposure_flag: bool | None = None
    current_asx_holdings_count: float | None = None
    historical_data_available_flag: bool | None = None
    source: str = "seed"
    confidence_score: float = field(default=0.0)

    def to_row(self) -> dict:
        return {c: getattr(self, c, None) for c in REGISTRY_COLUMNS}


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------
_CONFIDENCE_WEIGHTS = {
    "product_name": 0.10,
    "issuer": 0.10,
    "benchmark_index_name": 0.10,
    "index_provider": 0.05,
    "methodology_url": 0.10,
    "holdings_url": 0.15,
    "rebalance_frequency": 0.05,
    "number_of_holdings": 0.05,
    "aum": 0.05,
    "historical_data_available_flag": 0.10,
    "current_asx_holdings_count": 0.15,
}


def _is_present(v) -> bool:
    if v is None:
        return False
    if isinstance(v, float) and np.isnan(v):
        return False
    if isinstance(v, str) and not v.strip():
        return False
    return True


def compute_confidence(row: dict) -> float:
    base = sum(w for k, w in _CONFIDENCE_WEIGHTS.items() if _is_present(row.get(k)))
    # Manual rows get a trust bump; pure seeds are capped low.
    src = row.get("source")
    if src == "manual":
        base = min(1.0, base + 0.15)
    elif src == "seed":
        base = min(base, 0.45)
    return round(float(base), 3)


# ---------------------------------------------------------------------------
# Seeding from configs
# ---------------------------------------------------------------------------
def seed_from_configs(cfg: Config) -> pd.DataFrame:
    """Build candidate rows from watchlists (products+themes) and providers."""
    rows: list[dict] = []

    # Map issuer key -> human name for any product we can attribute.
    issuer_names = {i["key"]: i["name"] for i in cfg.providers.get("etf_issuers", [])}

    # Theme -> products from watchlists become low-confidence ETF candidates.
    themes = cfg.watchlists.get("themes", {})
    for theme, spec in themes.items():
        for product in spec.get("products", []) or []:
            rows.append(
                RegistryEntry(
                    product_ticker=str(product).upper(),
                    record_type="etf",
                    theme=theme,
                    asx_exposure_flag=True,  # on the ASX-watch theme list by construction
                    holdings_file_type="manual",
                    source="seed",
                ).to_row()
            )

    # Provider reference rows (index providers) — type "index" placeholders.
    for prov in cfg.providers.get("index_providers", []):
        rows.append(
            RegistryEntry(
                product_ticker=f"PROVIDER:{prov['key']}",
                record_type="index",
                index_provider=prov.get("name"),
                methodology_url=prov.get("methodology_hint"),
                holdings_file_type="manual" if prov.get("holdings_access") != "auto" else "csv",
                announcement_source=prov.get("home_url"),
                source="seed",
            ).to_row()
        )

    df = pd.DataFrame(rows, columns=REGISTRY_COLUMNS)
    # Attach issuer human names where the issuer key is implied (best-effort: none
    # by default; manual/discovery fills issuer). Keep issuer_names for callers.
    df.attrs["issuer_names"] = issuer_names
    return df


# ---------------------------------------------------------------------------
# Manual ingestion
# ---------------------------------------------------------------------------
def load_manual_registry(cfg: Config) -> pd.DataFrame:
    """Read every CSV in data/manual/{etf_registry,index_registry}/ (except the
    ``_TEMPLATE.csv`` files) and stack them with source='manual'."""
    base = cfg.path("data_manual")
    frames: list[pd.DataFrame] = []
    for sub, rtype in (("etf_registry", "etf"), ("index_registry", "index")):
        folder = base / sub
        if not folder.exists():
            continue
        for csv in sorted(folder.glob("*.csv")):
            if csv.name.startswith("_TEMPLATE"):
                continue
            try:
                d = read_csv(csv)
            except Exception as exc:  # noqa: BLE001
                log.warning("Could not read manual registry %s: %s", csv, exc)
                continue
            if d.empty:
                continue
            if "record_type" not in d.columns:
                d["record_type"] = rtype
            d["source"] = "manual"
            frames.append(d)
    if not frames:
        return pd.DataFrame(columns=REGISTRY_COLUMNS)
    out = pd.concat(frames, ignore_index=True)
    # Keep only known columns, add any missing.
    for c in REGISTRY_COLUMNS:
        if c not in out.columns:
            out[c] = np.nan
    return out[REGISTRY_COLUMNS]


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------
def build_registry(cfg: Config, discovery: pd.DataFrame | None = None) -> pd.DataFrame:
    """Merge seeds + discovery + manual into one registry, manual winning."""
    parts = [seed_from_configs(cfg)]
    if discovery is not None and not discovery.empty:
        d = discovery.copy()
        for c in REGISTRY_COLUMNS:
            if c not in d.columns:
                d[c] = np.nan
        parts.append(d[REGISTRY_COLUMNS])
    parts.append(load_manual_registry(cfg))

    df = pd.concat(parts, ignore_index=True)
    df["product_ticker"] = df["product_ticker"].astype(str).str.strip().str.upper()

    # Dedup by product_ticker, preferring the most trusted source.
    rank = {"manual": 3, "discovery": 2, "seed": 1}
    df["_rank"] = df["source"].map(rank).fillna(0)
    df = (
        df.sort_values("_rank")
        .drop_duplicates(subset=["product_ticker"], keep="last")
        .drop(columns="_rank")
        .reset_index(drop=True)
    )

    df["confidence_score"] = df.apply(lambda r: compute_confidence(r.to_dict()), axis=1)
    return df[REGISTRY_COLUMNS]


def split_and_save(cfg: Config, registry: pd.DataFrame) -> dict[str, Path]:
    """Write etf_registry.csv, index_registry.csv and asx_exposed_indices.csv."""
    tables = cfg.path("tables")
    etf = registry[registry["record_type"] == "etf"].copy()
    idx = registry[registry["record_type"] == "index"].copy()
    asx = registry[registry["asx_exposure_flag"].fillna(False).astype(bool)].copy()

    paths = {
        "etf_registry": write_csv(etf, tables / "etf_registry.csv"),
        "index_registry": write_csv(idx, tables / "index_registry.csv"),
        "asx_exposed_indices": write_csv(asx, tables / "asx_exposed_indices.csv"),
    }
    log.info(
        "Registry saved: %d etf, %d index, %d ASX-exposed -> %s",
        len(etf), len(idx), len(asx), tables,
    )
    return paths


def load_registry(cfg: Config) -> pd.DataFrame:
    """Load the previously-built combined registry from the report tables."""
    tables = cfg.path("tables")
    frames = []
    for name in ("etf_registry.csv", "index_registry.csv"):
        p = tables / name
        if p.exists():
            frames.append(read_csv(p))
    if not frames:
        return pd.DataFrame(columns=REGISTRY_COLUMNS)
    return pd.concat(frames, ignore_index=True)


def registry_field_names() -> list[str]:
    return [f.name for f in fields(RegistryEntry)]
