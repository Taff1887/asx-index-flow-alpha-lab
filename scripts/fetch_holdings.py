"""make fetch-holdings — ingest manual snapshots + auto-acquire where possible.

Order: ingest data/manual/holdings_snapshots/**, then per-product FMP current
holdings, then registry holdings_url downloads. Prints exactly which products
still need a manual drop-in (nothing is faked).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from index_flow.config import load_config
from index_flow.holdings_downloader import acquire_all
from index_flow.registry import load_registry


def main() -> int:
    cfg = load_config()
    cfg.ensure_dirs()
    reg = load_registry(cfg)
    if reg.empty:
        print("No registry found. Run build-registry first.")
        return 1

    summary = acquire_all(cfg, reg)
    print("Holdings acquisition summary:")
    print(f"  manual ingested:  {summary['manual']}")
    print(f"  fmp current:      {summary['fmp']}")
    print(f"  url downloads:    {summary['download']}")
    need = summary["blocked_or_missing"]
    print(f"  need manual file: {len(need)}")
    if need:
        print("\nProvide dated holdings CSVs for these under "
              "data/manual/holdings_snapshots/<PRODUCT>/<YYYY-MM-DD>.csv :")
        print("  " + ", ".join(sorted(need)[:40]) + (" ..." if len(need) > 40 else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
