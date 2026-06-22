"""Build the unified events table from every source.

Sources merged here:
* **Holdings diffs** (``holdings_diff``) — ETF_HOLDINGS_{NEW_POSITION,
  WEIGHT_INCREASE, WEIGHT_DECREASE} for constituents that map to an ASX symbol.
* **Manual announcements** (``announcement_scraper``) — OFFICIAL_INDEX_ADD /
  OFFICIAL_INDEX_DELETE with real announcement & effective dates.
* **Custom manual events** — ``data/manual/overrides/custom_events.csv``.

Each row is enriched with registry metadata (provider/issuer/benchmark/theme/AUM)
and given a stable ``event_id``. Price-derived columns (immediate_reaction,
delayed_return, flow_pressure, ...) are left blank here and filled by
``flow_estimator`` + ``reaction_detector``. Per-event overrides from
``data/manual/overrides/events_overrides.csv`` are applied last.
"""

from __future__ import annotations

import pandas as pd

from .asx_universe import tag_asx_constituents
from .config import Config
from .holdings_diff import diff_all
from .holdings_downloader import list_snapshots
from .registry import load_registry
from .announcement_scraper import load_manual_announcements
from .utils import get_logger, read_csv, stable_hash, to_date

log = get_logger("index_flow.events")

EVENT_COLUMNS = [
    "event_id",
    "source_type",                 # holdings_diff | announcement | custom_manual
    "provider",
    "issuer",
    "etf_index_name",
    "benchmark",
    "asx_ticker",
    "company_name",
    "event_type",                  # one of EVENT_TYPES
    "announcement_date",
    "detected_date",
    "effective_date",
    "old_weight",
    "new_weight",
    "estimated_buy_sell_dollars",
    "adv_dollars",
    "flow_pressure",
    "immediate_reaction",
    "delayed_return",
    "underreaction_score",
    "tradeable_flag",
    "confidence_score",
    "source_url_or_file",
    "notes",
]

# Working columns carried in-memory for flow estimation / features. They are
# prefixed with "_" and dropped before the canonical discovered_events.csv is
# written (see reporting.py).
EVENT_HELPER_COLUMNS = [
    "_product_ticker",
    "_shares_delta",
    "_weight_delta",
    "_aum",
    "_theme",
    "_n_holdings",
]

_DIFF_TO_EVENT = {
    "NEW_POSITION": "ETF_HOLDINGS_NEW_POSITION",
    "WEIGHT_INCREASE": "ETF_HOLDINGS_WEIGHT_INCREASE",
    "WEIGHT_DECREASE": "ETF_HOLDINGS_WEIGHT_DECREASE",
}


def _registry_lookup(cfg: Config) -> pd.DataFrame:
    reg = load_registry(cfg)
    if reg.empty:
        return reg
    reg = reg.copy()
    reg["product_ticker"] = reg["product_ticker"].astype(str).str.upper()
    return reg.set_index("product_ticker")


def _meta_for(reg: pd.DataFrame, product: str) -> dict:
    product = str(product).upper()
    if reg.empty or product not in reg.index:
        return {}
    row = reg.loc[product]
    if isinstance(row, pd.DataFrame):  # duplicate index safety
        row = row.iloc[0]
    return {
        "provider": row.get("index_provider"),
        "issuer": row.get("issuer"),
        "etf_index_name": row.get("product_name"),
        "benchmark": row.get("benchmark_index_name"),
        "theme": row.get("theme"),
        "aum": row.get("aum"),
        "number_of_holdings": row.get("number_of_holdings"),
        "confidence_score": row.get("confidence_score"),
    }


def _products_with_snapshots(cfg: Config) -> list[str]:
    base = cfg.path("data_processed") / "holdings_snapshots"
    if not base.exists():
        return []
    out = []
    for d in base.iterdir():
        if d.is_dir() and len(list_snapshots(cfg, d.name)) >= 2:
            out.append(d.name)
    return out


def _empty_events() -> pd.DataFrame:
    return pd.DataFrame(columns=EVENT_COLUMNS + EVENT_HELPER_COLUMNS)


def _with_helpers(df: pd.DataFrame, helpers: list[dict]) -> pd.DataFrame:
    """Attach helper columns (list of dicts aligned row-wise) to a base frame."""
    h = pd.DataFrame(helpers, columns=EVENT_HELPER_COLUMNS) if helpers else pd.DataFrame(
        columns=EVENT_HELPER_COLUMNS
    )
    for c in EVENT_HELPER_COLUMNS:
        df[c] = h[c].values if len(h) == len(df) else None
    return df


def events_from_holdings(cfg: Config, reg: pd.DataFrame) -> pd.DataFrame:
    products = _products_with_snapshots(cfg)
    if not products:
        return _empty_events()
    diffs = diff_all(cfg, products)
    if diffs.empty:
        return _empty_events()
    diffs = tag_asx_constituents(diffs)
    diffs = diffs[diffs["is_asx"] & diffs["change_type"].isin(_DIFF_TO_EVENT)]
    rows = []
    helpers = []
    for _, d in diffs.iterrows():
        meta = _meta_for(reg, d["product_ticker"])
        event_type = _DIFF_TO_EVENT[d["change_type"]]
        helpers.append(
            {
                "_product_ticker": str(d["product_ticker"]).upper(),
                "_shares_delta": d.get("shares_delta"),
                "_weight_delta": d.get("weight_delta"),
                "_aum": meta.get("aum"),
                "_theme": meta.get("theme"),
                "_n_holdings": meta.get("number_of_holdings"),
            }
        )
        rows.append(
            {
                "event_id": stable_hash(
                    "holdings", d["product_ticker"], d["asx_symbol"], event_type, d["as_of_date"]
                ),
                "source_type": "holdings_diff",
                "provider": meta.get("provider"),
                "issuer": meta.get("issuer"),
                "etf_index_name": meta.get("etf_index_name") or d["product_ticker"],
                "benchmark": meta.get("benchmark"),
                "asx_ticker": d["asx_symbol"],
                "company_name": d.get("constituent_name"),
                "event_type": event_type,
                "announcement_date": to_date(d["as_of_date"]),  # holdings file is the public signal
                "detected_date": to_date(d["as_of_date"]),
                "effective_date": None,                          # ongoing implementation
                "old_weight": d.get("old_weight"),
                "new_weight": d.get("new_weight"),
                "estimated_buy_sell_dollars": None,
                "adv_dollars": None,
                "flow_pressure": None,
                "immediate_reaction": None,
                "delayed_return": None,
                "underreaction_score": None,
                "tradeable_flag": None,
                "confidence_score": meta.get("confidence_score"),
                "source_url_or_file": f"holdings_snapshot:{d['product_ticker']}",
                "notes": f"shares_delta={d.get('shares_delta')}; weight_delta={d.get('weight_delta')}",
            }
        )
    return _with_helpers(pd.DataFrame(rows, columns=EVENT_COLUMNS), helpers)


def events_from_announcements(cfg: Config) -> pd.DataFrame:
    ann = load_manual_announcements(cfg)
    if ann.empty:
        return _empty_events()
    rows = []
    for _, a in ann.iterrows():
        action = str(a.get("action", "")).upper()
        event_type = "OFFICIAL_INDEX_ADD" if action == "ADD" else (
            "OFFICIAL_INDEX_DELETE" if action == "DELETE" else "RECONSTITUTION"
        )
        rows.append(
            {
                "event_id": stable_hash(
                    "ann", a.get("provider"), a.get("index_name"), a.get("asx_ticker"),
                    event_type, a.get("announcement_date"),
                ),
                "source_type": "announcement",
                "provider": a.get("provider"),
                "issuer": None,
                "etf_index_name": a.get("index_name"),
                "benchmark": a.get("index_name"),
                "asx_ticker": a.get("asx_ticker"),
                "company_name": a.get("company_name"),
                "event_type": event_type,
                "announcement_date": to_date(a.get("announcement_date")),
                "detected_date": to_date(a.get("announcement_date")),
                "effective_date": to_date(a.get("effective_date")),
                "old_weight": None,
                "new_weight": None,
                "estimated_buy_sell_dollars": None,
                "adv_dollars": None,
                "flow_pressure": None,
                "immediate_reaction": None,
                "delayed_return": None,
                "underreaction_score": None,
                "tradeable_flag": None,
                "confidence_score": a.get("confidence"),
                "source_url_or_file": a.get("source_url"),
                "notes": "needs_verification" if a.get("needs_verification") else "",
            }
        )
    return _with_helpers(pd.DataFrame(rows, columns=EVENT_COLUMNS), [])


def events_from_custom(cfg: Config) -> pd.DataFrame:
    path = cfg.path("data_manual") / "overrides" / "custom_events.csv"
    if not path.exists():
        return _empty_events()
    raw = read_csv(path)
    if raw.empty:
        return _empty_events()
    out = pd.DataFrame(columns=EVENT_COLUMNS)
    for c in EVENT_COLUMNS:
        out[c] = raw[c] if c in raw.columns else None
    out = _with_helpers(out, [])
    out["_product_ticker"] = raw["_product_ticker"] if "_product_ticker" in raw.columns else None
    out["source_type"] = "custom_manual"
    out["event_type"] = out["event_type"].fillna("CUSTOM_MANUAL_EVENT")
    # backfill event_id where missing
    missing = out["event_id"].isna() | (out["event_id"].astype(str).str.len() == 0)
    out.loc[missing, "event_id"] = out.loc[missing].apply(
        lambda r: stable_hash("custom", r["asx_ticker"], r["event_type"], r["announcement_date"]), axis=1
    )
    return out


def apply_overrides(cfg: Config, events: pd.DataFrame) -> pd.DataFrame:
    """Apply per-event overrides keyed by event_id from
    data/manual/overrides/events_overrides.csv (only non-null cells override)."""
    path = cfg.path("data_manual") / "overrides" / "events_overrides.csv"
    if not path.exists() or events.empty:
        return events
    ovr = read_csv(path)
    if ovr.empty or "event_id" not in ovr.columns:
        return events
    events = events.set_index("event_id")
    ovr = ovr.set_index("event_id")
    for eid, row in ovr.iterrows():
        if eid not in events.index:
            continue
        for col in ovr.columns:
            val = row[col]
            if col in events.columns and pd.notna(val):
                events.loc[eid, col] = val
    return events.reset_index()


def build_events(cfg: Config) -> pd.DataFrame:
    """Assemble, dedup, and persist the base events table (pre price-enrichment)."""
    reg = _registry_lookup(cfg)
    parts = [
        events_from_holdings(cfg, reg),
        events_from_announcements(cfg),
        events_from_custom(cfg),
    ]
    parts = [p for p in parts if not p.empty]
    events = pd.concat(parts, ignore_index=True) if parts else _empty_events()
    if not events.empty:
        events = events.drop_duplicates(subset=["event_id"]).reset_index(drop=True)
        events = apply_overrides(cfg, events)
    log.info("Built %d base events from %d sources", len(events), len(parts))
    return events


def build_and_enrich(cfg: Config, use_fundamentals: bool = True, as_of=None):
    """Full base->flow->study->features pipeline. Returns (events, features).

    Merges ``market_cap`` and ``source_obscurity_score`` from the feature matrix
    back onto events so price/size-aware strategies (e.g. ForgottenSmallCapAdd)
    can filter. Cheap to recompute (prices are cached), so scripts call this
    rather than serialising enriched events with fragile dtypes.
    """
    from .features import build_features
    from .flow_estimator import estimate_flows
    from .price_data import PriceStore
    from .reaction_detector import run_event_study

    events = build_events(cfg)
    if events.empty:
        return events, events
    store = PriceStore(cfg)
    events = estimate_flows(cfg, events, store, as_of=as_of)
    events = run_event_study(cfg, events, store)
    feats = build_features(cfg, events, store, use_fundamentals=use_fundamentals)
    if not feats.empty:
        merge_cols = ["event_id", "market_cap", "source_obscurity_score"]
        events = events.merge(
            feats[[c for c in merge_cols if c in feats.columns]], on="event_id", how="left"
        )
    return events, feats
