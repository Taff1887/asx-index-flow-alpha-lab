"""make discover-etfs — scan FMP + provider/issuer pages for products & benchmarks.

Writes the discovery candidates to data/interim/discovered.parquet and rebuilds
the registry merging them in. Blocked pages are logged and become manual TODO
rows — never fabricated.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from index_flow.config import load_config
from index_flow.provider_discovery import discover
from index_flow.registry import build_registry, split_and_save
from index_flow.utils import write_parquet


def main() -> int:
    cfg = load_config()
    cfg.ensure_dirs()

    disc = discover(cfg, use_web=True)
    out = cfg.path("data_interim") / "discovered.parquet"
    if not disc.empty:
        write_parquet(disc, out)
    print(f"Discovered {len(disc)} candidate rows -> {out}")
    if not disc.empty:
        print(disc["record_type"].value_counts().to_string())

    reg = build_registry(cfg, discovery=disc)
    split_and_save(cfg, reg)
    print(f"Registry now {len(reg)} rows (merged seeds + discovery + manual).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
