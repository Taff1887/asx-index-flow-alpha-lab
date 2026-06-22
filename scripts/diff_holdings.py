"""make diff-holdings — diff stored snapshots into constituent change records.

Writes data/processed/holdings_diffs.parquet and reports how many changes map to
ASX-listed names (the raw flow signal).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from index_flow.asx_universe import tag_asx_constituents
from index_flow.config import load_config
from index_flow.holdings_diff import diff_all
from index_flow.holdings_downloader import list_snapshots
from index_flow.utils import write_parquet


def main() -> int:
    cfg = load_config()
    cfg.ensure_dirs()
    base = cfg.path("data_processed") / "holdings_snapshots"
    products = [d.name for d in base.iterdir() if d.is_dir() and len(list_snapshots(cfg, d.name)) >= 2] if base.exists() else []
    if not products:
        print("No products with >=2 snapshots yet. Add dated holdings files and "
              "re-run fetch-holdings (see README -> What you must provide).")
        return 0

    diffs = diff_all(cfg, products)
    if diffs.empty:
        print("Snapshots present but no changes detected.")
        return 0
    diffs = tag_asx_constituents(diffs)
    out = cfg.path("data_processed") / "holdings_diffs.parquet"
    write_parquet(diffs, out)

    print(f"Diffed {len(products)} products -> {len(diffs)} changes ({out})")
    print(diffs["change_type"].value_counts().to_string())
    print(f"ASX-mapped changes: {int(diffs['is_asx'].sum())}")
    asx = diffs[diffs["is_asx"]]
    if not asx.empty:
        print("\nTop ASX constituent changes:")
        print(asx[["product_ticker", "asx_symbol", "change_type", "weight_delta", "as_of_date"]]
              .head(20).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
