"""make build-registry — assemble the index/ETF registry from configs + manual.

Builds from seeds (watchlists/providers) + any manual CSVs (and, with --discover,
FMP/web discovery), then writes index_registry.csv / etf_registry.csv /
asx_exposed_indices.csv to reports/tables/.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from index_flow.config import load_config
from index_flow.registry import build_registry, split_and_save


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--discover", action="store_true", help="also run FMP/web discovery")
    args = ap.parse_args()

    cfg = load_config()
    cfg.ensure_dirs()

    discovery = None
    if args.discover:
        from index_flow.provider_discovery import discover
        discovery = discover(cfg, use_web=True)

    reg = build_registry(cfg, discovery=discovery)
    paths = split_and_save(cfg, reg)

    print(f"Registry built: {len(reg)} rows")
    print(f"  ETF rows:   {(reg['record_type'] == 'etf').sum()}")
    print(f"  Index rows: {(reg['record_type'] == 'index').sum()}")
    print(f"  ASX-exposed:{reg['asx_exposure_flag'].fillna(False).astype(bool).sum()}")
    for k, v in paths.items():
        print(f"  wrote {k}: {v}")
    print("\nTip: drop niche products into data/manual/etf_registry/*.csv to extend.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
