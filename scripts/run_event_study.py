"""make event-study — flow estimation + delayed-reaction event study + reports.

Enriches events with flow pressure, the window battery, immediate/delayed
reaction, underreaction & delayed-flow opportunity scores, then writes
discovered_events.csv, delayed_reaction_events.csv, the inefficiency rankings and
top_current_watchlist.csv.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from index_flow.config import load_config
from index_flow.event_builder import build_and_enrich
from index_flow.reporting import write_events, write_inefficiency_rankings, write_watchlist


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", help="YYYY-MM-DD for watchlist 'still open' filter", default=None)
    ap.add_argument("--no-fundamentals", action="store_true", help="skip FMP market-cap/profile pulls")
    args = ap.parse_args()

    cfg = load_config()
    cfg.ensure_dirs()
    events, _ = build_and_enrich(cfg, use_fundamentals=not args.no_fundamentals)
    if events.empty:
        print("No events to study. Run build-events and ingest data first.")
        return 0

    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    write_events(cfg, events)
    write_inefficiency_rankings(cfg, events)
    write_watchlist(cfg, events, as_of)

    n_trade = int(events["tradeable_flag"].fillna(False).astype(bool).sum())
    print(f"Studied {len(events)} events ({n_trade} tradeable).")
    top = events.sort_values("delayed_flow_opportunity_score", ascending=False)
    cols = ["asx_ticker", "event_type", "provider", "flow_pressure",
            "immediate_reaction", "delayed_return", "delayed_flow_opportunity_score"]
    cols = [c for c in cols if c in top.columns]
    print("\nTop opportunities by delayed-flow score:")
    print(top[cols].head(15).to_string(index=False))
    print("\nNext: make backtest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
