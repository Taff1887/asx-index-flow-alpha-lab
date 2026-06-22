"""make build-events — merge holdings diffs + announcements + custom into events.

Writes a preliminary reports/tables/discovered_events.csv (price-derived metrics
are filled by run_event_study). Prints a breakdown by source and event type.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from index_flow.config import load_config
from index_flow.event_builder import build_events
from index_flow.reporting import write_events


def main() -> int:
    cfg = load_config()
    cfg.ensure_dirs()
    events = build_events(cfg)
    write_events(cfg, events)

    print(f"Built {len(events)} events.")
    if events.empty:
        print("\nNo events yet. This is expected before you ingest data — the engine "
              "does not fabricate events. To produce events, provide either:")
        print("  * >=2 dated holdings snapshots per product "
              "(data/manual/holdings_snapshots/<PRODUCT>/<YYYY-MM-DD>.csv), and/or")
        print("  * rebalance announcements "
              "(data/manual/rebalance_announcements/*.csv), and/or")
        print("  * custom events (data/manual/overrides/custom_events.csv).")
        return 0
    print(events["source_type"].value_counts().to_string())
    print(events["event_type"].value_counts().to_string())
    print("\nNext: make event-study")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
