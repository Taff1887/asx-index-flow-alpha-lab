"""Bounded self-improvement research loop.

Each iteration:
  1. review the events found and rank by opportunity score;
  2. (re)run the event study enrichment if needed;
  3. backtest the current best strategy/params;
  4. diagnose with the claims gate + bucket breakdowns;
  5. propose three new, simple hypotheses (parameter perturbations / a new
     enable);
  6. evaluate them and adopt the best *if and only if* it improves the
     cost-and-harsh-survived objective;
  7. log every trial — successes and failures — to failed_hypotheses.csv.

It is intentionally a small, transparent local search, not an optimiser that can
data-mine: the objective penalises results that don't survive harsher costs, and
sparse data simply yields "insufficient events" — never a fabricated edge.
"""

from __future__ import annotations

import pandas as pd

from .backtester import backtest_trades
from .config import Config
from .diagnostics import bootstrap_ci, claims_gate, compute_metrics
from .price_data import PriceStore
from .reporting import append_failed_hypothesis
from .strategies import DelayedAnnouncementReaction
from .utils import get_logger

log = get_logger("index_flow.research")


def _objective(ledger: pd.DataFrame) -> float:
    """Higher is better. Mean harsh-net return, penalised if it doesn't survive
    costs or has too few trades; rewarded if the bootstrap CI lower bound > 0."""
    m = compute_metrics(ledger)
    n = m.get("n_trades", 0)
    if n < 3:
        return -9.0 + n  # strongly disfavour sparse results, but keep ordering
    harsh = m.get("avg_return_after_harsh_costs", float("nan"))
    if pd.isna(harsh):
        return -5.0
    obj = harsh
    if m.get("avg_return_after_costs", 0) <= 0:
        obj -= 0.05
    lo, _ = bootstrap_ci(ledger[~ledger["rejected"].fillna(True).astype(bool)]["net_return"])
    if lo is not None and not pd.isna(lo) and lo > 0:
        obj += 0.02
    return float(obj)


def _evaluate(cfg: Config, events: pd.DataFrame, params: dict, store: PriceStore) -> dict:
    strat = DelayedAnnouncementReaction(name="DelayedAnnouncementReaction", cfg=cfg)
    strat.params = {**strat.params, **params, "enabled": True}
    trades = strat.generate_trades(events)
    ledger = backtest_trades(cfg, trades, events, store)
    return {"ledger": ledger, "metrics": compute_metrics(ledger),
            "gate": claims_gate(ledger), "objective": _objective(ledger),
            "params": strat.params}


def propose_hypotheses(base_params: dict, iteration: int) -> list[tuple[str, dict]]:
    """Three simple, economically-motivated perturbations of the current params."""
    mi = base_params.get("max_immediate_reaction_pct", 0.03)
    fp = base_params.get("min_flow_pressure", 0.5)
    return [
        (f"iter{iteration}: tighten immediate-reaction filter (only true underreactions)",
         {"max_immediate_reaction_pct": round(max(0.005, mi * 0.7), 4)}),
        (f"iter{iteration}: demand higher flow pressure (bigger forced demand vs ADV)",
         {"min_flow_pressure": round(fp * 1.5, 3)}),
        (f"iter{iteration}: exit later to capture more implementation drift",
         {"exit": "eff_close_plus5"}),
    ]


def run_research_loop(cfg: Config, events: pd.DataFrame, max_iterations: int = 5) -> dict:
    store = PriceStore(cfg)
    if events.empty:
        log.warning("No events to research; loop is a no-op until data is ingested.")
        append_failed_hypothesis(cfg, {
            "iteration": 0, "timestamp": pd.Timestamp.now(tz="UTC").isoformat(),
            "hypothesis": "baseline", "test": "build events",
            "outcome": "insufficient_data",
            "metric": "n_events=0",
            "notes": "Ingest holdings snapshots / announcements (see README) to enable research.",
        })
        return {"status": "no_events", "iterations": 0}

    base = DelayedAnnouncementReaction(name="DelayedAnnouncementReaction", cfg=cfg).params.copy()
    base["enabled"] = True
    best = _evaluate(cfg, events, base, store)
    log.info("Baseline objective=%.4f, n=%d, claim=%s",
             best["objective"], best["metrics"].get("n_trades", 0), best["gate"].get("claim_alpha"))
    append_failed_hypothesis(cfg, {
        "iteration": 0, "timestamp": pd.Timestamp.now(tz="UTC").isoformat(),
        "hypothesis": "baseline DelayedAnnouncementReaction",
        "test": "backtest", "outcome": "kept",
        "metric": f"obj={best['objective']:.4f}; harsh={best['metrics'].get('avg_return_after_harsh_costs')}",
        "notes": f"claim_alpha={best['gate'].get('claim_alpha')}",
    })

    history = [{"iteration": 0, **best["metrics"], "objective": best["objective"],
                "claim_alpha": best["gate"].get("claim_alpha")}]

    for it in range(1, max_iterations + 1):
        improved = False
        for hyp_name, override in propose_hypotheses(best["params"], it):
            trial = _evaluate(cfg, events, {**best["params"], **override}, store)
            better = trial["objective"] > best["objective"] + 1e-6
            append_failed_hypothesis(cfg, {
                "iteration": it, "timestamp": pd.Timestamp.now(tz="UTC").isoformat(),
                "hypothesis": hyp_name, "test": "backtest",
                "outcome": "adopted" if better else "rejected",
                "metric": f"obj={trial['objective']:.4f} vs best={best['objective']:.4f}; "
                          f"n={trial['metrics'].get('n_trades',0)}",
                "notes": f"claim_alpha={trial['gate'].get('claim_alpha')}",
            })
            if better:
                best = trial
                improved = True
        history.append({"iteration": it, **best["metrics"], "objective": best["objective"],
                        "claim_alpha": best["gate"].get("claim_alpha")})
        log.info("Iter %d: best objective=%.4f (improved=%s)", it, best["objective"], improved)
        if not improved:
            log.info("No improving hypothesis at iter %d; stopping early.", it)
            break

    return {
        "status": "done",
        "best_params": best["params"],
        "best_metrics": best["metrics"],
        "best_gate": best["gate"],
        "history": pd.DataFrame(history),
    }
