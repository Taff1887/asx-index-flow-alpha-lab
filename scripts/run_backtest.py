"""make backtest — run every enabled strategy with realistic costs + claims gate.

Writes the trade ledger (data/processed/ledger.csv) and
reports/scorecards/strategy_scoreboard.csv, then prints per-strategy metrics, the
claims-discipline verdict, and key bucket breakdowns.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from index_flow.backtester import run_all_strategies
from index_flow.config import load_config
from index_flow.diagnostics import breakdown, claims_gate, compute_metrics
from index_flow.event_builder import build_and_enrich
from index_flow.reporting import write_strategy_scoreboard
from index_flow.utils import write_csv


def main() -> int:
    cfg = load_config()
    cfg.ensure_dirs()
    events, _ = build_and_enrich(cfg, use_fundamentals=True)
    if events.empty:
        print("No events to backtest. Ingest data and run event-study first.")
        return 0

    ledger = run_all_strategies(cfg, events)
    write_csv(ledger, cfg.path("data_processed") / "ledger.csv")
    write_strategy_scoreboard(cfg, ledger)

    if ledger.empty or (~ledger["rejected"].fillna(True).astype(bool)).sum() == 0:
        print("No executable trades (events present but none passed tradeability/"
              "liquidity filters, or strategies selected none). Scoreboard written.")
        return 0

    print("Overall metrics (all strategies):")
    for k, v in compute_metrics(ledger).items():
        print(f"  {k:32s} {v}")

    print("\nClaims-discipline gate:")
    for k, v in claims_gate(ledger).items():
        print(f"  {k:28s} {v}")

    for dim in ("strategy", "year", "theme", "provider", "flow_pressure_bucket",
                "immediate_reaction_bucket", "liquidity_bucket", "market_cap_bucket"):
        bd = breakdown(ledger, events, dim)
        if not bd.empty:
            cols = [dim, "n_trades", "avg_return", "hit_rate", "avg_return_after_harsh_costs"]
            cols = [c for c in cols if c in bd.columns]
            print(f"\nBy {dim}:")
            print(bd[cols].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
