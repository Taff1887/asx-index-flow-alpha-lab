"""Diff successive holdings snapshots into constituent-level change records.

A change record is the atom the event builder turns into ETF_HOLDINGS_* events.
We compute the diff between consecutive dated snapshots of the same product and
classify each constituent as NEW_POSITION / DELETED / WEIGHT_INCREASE /
WEIGHT_DECREASE / UNCHANGED, with weight and share deltas.
"""

from __future__ import annotations

import pandas as pd

from .config import Config
from .holdings_downloader import list_snapshots
from .utils import get_logger, read_parquet

log = get_logger("index_flow.holdings_diff")

DIFF_COLUMNS = [
    "product_ticker",
    "prev_date",
    "as_of_date",
    "constituent_ticker",
    "constituent_name",
    "change_type",
    "old_weight",
    "new_weight",
    "weight_delta",
    "old_shares",
    "new_shares",
    "shares_delta",
]

WEIGHT_EPS = 1e-4   # 1 bp: ignore noise below this absolute weight move


def _key(df: pd.DataFrame) -> pd.DataFrame:
    """Index a snapshot by constituent_ticker (falling back to name)."""
    df = df.copy()
    df["__key"] = df["constituent_ticker"].fillna("").astype(str)
    mask = df["__key"].isin(["", "nan", "NAN"])
    df.loc[mask, "__key"] = df.loc[mask, "constituent_name"].fillna("").astype(str).str.upper()
    df = df[df["__key"] != ""]
    # If duplicate keys (multi-line), sum weights/shares.
    agg = df.groupby("__key").agg(
        constituent_ticker=("constituent_ticker", "first"),
        constituent_name=("constituent_name", "first"),
        weight_pct=("weight_pct", "sum"),
        shares=("shares", "sum"),
    )
    return agg


def diff_snapshots(
    prev: pd.DataFrame,
    curr: pd.DataFrame,
    product: str,
    prev_date,
    curr_date,
    weight_eps: float = WEIGHT_EPS,
    include_unchanged: bool = False,
) -> pd.DataFrame:
    """Return constituent-level change records between two snapshots."""
    p = _key(prev)
    c = _key(curr)
    keys = sorted(set(p.index) | set(c.index))
    rows: list[dict] = []
    for k in keys:
        in_prev = k in p.index
        in_curr = k in c.index
        old_w = float(p.loc[k, "weight_pct"]) if in_prev and pd.notna(p.loc[k, "weight_pct"]) else float("nan")
        new_w = float(c.loc[k, "weight_pct"]) if in_curr and pd.notna(c.loc[k, "weight_pct"]) else float("nan")
        old_s = float(p.loc[k, "shares"]) if in_prev and pd.notna(p.loc[k, "shares"]) else float("nan")
        new_s = float(c.loc[k, "shares"]) if in_curr and pd.notna(c.loc[k, "shares"]) else float("nan")
        name = (c.loc[k, "constituent_name"] if in_curr else p.loc[k, "constituent_name"])
        tkr = (c.loc[k, "constituent_ticker"] if in_curr else p.loc[k, "constituent_ticker"])

        if in_curr and not in_prev:
            change = "NEW_POSITION"
        elif in_prev and not in_curr:
            change = "DELETED"
        else:
            dw = (new_w - old_w) if (pd.notna(new_w) and pd.notna(old_w)) else 0.0
            ds = (new_s - old_s) if (pd.notna(new_s) and pd.notna(old_s)) else 0.0
            if abs(dw) > weight_eps:
                change = "WEIGHT_INCREASE" if dw > 0 else "WEIGHT_DECREASE"
            elif ds != 0:
                change = "WEIGHT_INCREASE" if ds > 0 else "WEIGHT_DECREASE"
            else:
                change = "UNCHANGED"
        if change == "UNCHANGED" and not include_unchanged:
            continue
        rows.append(
            {
                "product_ticker": str(product).upper(),
                "prev_date": prev_date,
                "as_of_date": curr_date,
                "constituent_ticker": tkr,
                "constituent_name": name,
                "change_type": change,
                "old_weight": old_w,
                "new_weight": new_w,
                "weight_delta": (new_w - old_w) if (pd.notna(new_w) and pd.notna(old_w)) else (
                    new_w if change == "NEW_POSITION" else (-old_w if change == "DELETED" else float("nan"))
                ),
                "old_shares": old_s,
                "new_shares": new_s,
                "shares_delta": (new_s - old_s) if (pd.notna(new_s) and pd.notna(old_s)) else (
                    new_s if change == "NEW_POSITION" else (-old_s if change == "DELETED" else float("nan"))
                ),
            }
        )
    return pd.DataFrame(rows, columns=DIFF_COLUMNS)


def diff_product_history(cfg: Config, product: str, **kwargs) -> pd.DataFrame:
    """Diff every consecutive pair of snapshots stored for a product."""
    snaps = list_snapshots(cfg, product)
    if len(snaps) < 2:
        return pd.DataFrame(columns=DIFF_COLUMNS)
    out = []
    for (pd_date, ppath), (cd_date, cpath) in zip(snaps[:-1], snaps[1:]):
        prev = read_parquet(ppath)
        curr = read_parquet(cpath)
        out.append(diff_snapshots(prev, curr, product, pd_date, cd_date, **kwargs))
    res = pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=DIFF_COLUMNS)
    return res


def diff_all(cfg: Config, products: list[str]) -> pd.DataFrame:
    """Diff history for many products and stack the results."""
    frames = []
    for product in products:
        d = diff_product_history(cfg, product)
        if not d.empty:
            frames.append(d)
    if not frames:
        return pd.DataFrame(columns=DIFF_COLUMNS)
    return pd.concat(frames, ignore_index=True)
