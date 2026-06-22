"""DEMO — show the engine producing results end to end.

WHAT THIS IS:
  * REAL ASX prices (fetched live from FMP) for real tickers.
  * An ILLUSTRATIVE event set: we *assume* each ticker was added to a product on
    the real S&P/ASX June-2024 rebalance dates. The membership is NOT verified —
    it exists only to drive the machinery so you can see real price reactions, a
    real costed backtest, and every report populated.

WHAT THIS IS NOT:
  * A research result. Real findings require verified index/holdings events
    (drop them into data/manual/ — see the README). The canonical reports/ dir
    is left untouched; this writes to examples/demo_reports/.

Run:  .venv\\Scripts\\python.exe examples\\demo_pipeline.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd

from index_flow.backtester import run_all_strategies
from index_flow.config import load_config
from index_flow.diagnostics import breakdown, claims_gate, compute_metrics
from index_flow.event_builder import EVENT_COLUMNS, EVENT_HELPER_COLUMNS
from index_flow.features import build_features
from index_flow.flow_estimator import estimate_flows
from index_flow.price_data import PriceStore
from index_flow.reaction_detector import run_event_study
from index_flow.reporting import write_all
from index_flow.utils import stable_hash, write_csv

# ASX names by theme/product (real tickers). Illustrative event set only.
DEMO = [
    ("PDN.AX", "uranium", "URNM", "Sprott"),
    ("BOE.AX", "uranium", "URNM", "Sprott"),
    ("DYL.AX", "uranium", "URNJ", "Sprott"),
    ("LOT.AX", "uranium", "URNJ", "Sprott"),
    ("NST.AX", "gold_miners", "GDX", "VanEck"),
    ("EVN.AX", "gold_miners", "GDX", "VanEck"),
    ("GMD.AX", "gold_miners", "GDXJ", "VanEck"),
    ("RMS.AX", "gold_miners", "GDXJ", "VanEck"),
]
ANNOUNCE = "2024-06-07"   # ~first Friday June 2024
EFFECTIVE = "2024-06-21"  # 3rd Friday June 2024 (real S&P/ASX quarterly effective)


def main() -> int:
    cfg = load_config()
    # Redirect outputs so the canonical reports/ dir stays clean.
    cfg.config["paths"]["tables"] = "examples/demo_reports/tables"
    cfg.config["paths"]["scorecards"] = "examples/demo_reports/scorecards"
    cfg.config["paths"]["data_processed"] = "examples/demo_work/processed"
    cfg.ensure_dirs()

    print("=" * 74)
    print("DEMONSTRATION — real ASX prices, ILLUSTRATIVE (unverified) event set.")
    print("Not a research result. Real findings need verified data in data/manual/.")
    print("FMP key present:", bool(cfg.fmp_api_key))
    print("=" * 74)

    store = PriceStore(cfg)

    rows = []
    for ticker, theme, product, provider in DEMO:
        base = {c: None for c in EVENT_COLUMNS + EVENT_HELPER_COLUMNS}
        base.update(
            event_id=stable_hash("demo", product, ticker, ANNOUNCE),
            source_type="holdings_diff", provider=provider, issuer=provider,
            etf_index_name=product, benchmark=product, asx_ticker=ticker,
            company_name=ticker.split(".")[0], event_type="ETF_HOLDINGS_NEW_POSITION",
            announcement_date=ANNOUNCE, detected_date=ANNOUNCE, effective_date=EFFECTIVE,
            new_weight=0.03, confidence_score=0.7,
            _product_ticker=product, _shares_delta=2_000_000, _theme=theme,
        )
        rows.append(base)
    events = pd.DataFrame(rows)

    # Enrich with REAL prices: flow pressure, event study, features.
    events = estimate_flows(cfg, events, store)
    events = run_event_study(cfg, events, store)
    feats = build_features(cfg, events, store, use_fundamentals=True)
    if not feats.empty:
        events = events.merge(
            feats[[c for c in ["event_id", "market_cap", "source_obscurity_score"] if c in feats.columns]],
            on="event_id", how="left",
        )

    print("\nEVENTS (real prices around the Jun-2024 rebalance):")
    cols = [c for c in ["asx_ticker", "provider", "flow_pressure", "immediate_reaction",
                        "delayed_return", "underreaction_score",
                        "delayed_flow_opportunity_score", "tradeable_flag"] if c in events.columns]
    print(events[cols].to_string(index=False))

    ledger = run_all_strategies(cfg, events)
    write_csv(ledger, cfg.path("data_processed") / "ledger.csv")
    paths = write_all(cfg, events, ledger, as_of=None)

    ex = ledger[~ledger["rejected"].fillna(True).astype(bool)]
    print(f"\nBACKTEST: {len(ledger)} trade specs, {len(ex)} executed.")
    if not ex.empty:
        show = ["strategy", "asx_ticker", "entry_date", "exit_date", "gross_return",
                "net_return", "harsh_net_return", "exposure_days"]
        print(ex[[c for c in show if c in ex.columns]].to_string(index=False))
        print("\nOVERALL METRICS (after costs):")
        for k, v in compute_metrics(ledger).items():
            print(f"  {k:30s} {round(v, 4) if isinstance(v, float) else v}")
        print("\nCLAIMS-DISCIPLINE GATE:")
        for k, v in claims_gate(ledger).items():
            print(f"  {k:28s} {v}")
        for dim in ("strategy", "theme", "provider"):
            bd = breakdown(ledger, events, dim)
            if not bd.empty:
                c = [dim, "n_trades", "avg_return", "hit_rate", "avg_return_after_harsh_costs"]
                print(f"\nBy {dim}:")
                print(bd[[x for x in c if x in bd.columns]].to_string(index=False))

    print("\nFILES WRITTEN (open these):")
    for k, v in paths.items():
        print(f"  {k:30s} {v}")
    print(f"  {'ledger':30s} {cfg.path('data_processed') / 'ledger.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
