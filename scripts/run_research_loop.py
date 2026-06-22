"""make research-loop — bounded self-improvement over the primary strategy.

Ranks events, backtests, diagnoses, proposes 3 hypotheses/iteration, adopts only
those that improve the cost-and-harsh-survived objective, and logs every trial to
reports/scorecards/failed_hypotheses.csv.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from index_flow.config import load_config
from index_flow.event_builder import build_and_enrich
from index_flow.research_loop import run_research_loop


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iterations", type=int, default=5)
    args = ap.parse_args()

    cfg = load_config()
    cfg.ensure_dirs()
    events, _ = build_and_enrich(cfg, use_fundamentals=True)

    result = run_research_loop(cfg, events, max_iterations=args.iterations)
    print(f"Research loop status: {result['status']}")
    if result["status"] == "done":
        print("\nBest params:")
        for k, v in result["best_params"].items():
            print(f"  {k}: {v}")
        print("\nBest metrics:")
        for k, v in result["best_metrics"].items():
            print(f"  {k}: {v}")
        print(f"\nclaim_alpha: {result['best_gate'].get('claim_alpha')}")
        print("\nHistory:")
        hist = result["history"]
        cols = [c for c in ["iteration", "n_trades", "avg_return",
                            "avg_return_after_harsh_costs", "objective", "claim_alpha"]
                if c in hist.columns]
        print(hist[cols].to_string(index=False))
    print("\nTrial log -> reports/scorecards/failed_hypotheses.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
