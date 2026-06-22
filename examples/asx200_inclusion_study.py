"""REAL benchmark study: the S&P/ASX 200 index-inclusion drift.

Events are real, publicly-announced S&P/ASX 200 additions (sources cited inline).
Every company name is resolved to its ASX ticker via FMP and dropped if FMP has
no price history around the event — so no ticker is invented and no event without
real data survives. Prices are real (FMP stable). Announcement date = first Friday
of the rebalance month (S&P's usual cadence); effective date as announced.

This tests the OBVIOUS effect the lab deliberately de-prioritises (well-watched
ASX 200 adds). It is the benchmark against which the obscure thematic-overhang
opportunities should be compared.
"""
from __future__ import annotations
import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd
from index_flow.config import load_config
from index_flow.fmp_client import FMPClient
from index_flow.price_data import PriceStore
from index_flow.flow_estimator import estimate_flows
from index_flow.reaction_detector import run_event_study
from index_flow.backtester import backtest_trades
from index_flow.diagnostics import compute_metrics, claims_gate
from index_flow.event_builder import EVENT_COLUMNS, EVENT_HELPER_COLUMNS
from index_flow.rebalance_calendar import nth_weekday
from index_flow.utils import stable_hash, write_csv

# Real S&P/ASX 200 ADDITIONS by effective date (company names; tickers resolved
# via FMP below). Sources: S&P DJI quarterly announcements as reported by
# marketindex.com.au / stockhead / livewire / nasdaq (June 2024 had no ASX 200
# adds, so it is omitted).
EVENTS = {
    date(2024, 3, 18): ["Austal", "Nick Scali"],
    date(2024, 9, 23): ["Guzman y Gomez", "Westgold Resources", "Yancoal Australia"],
    date(2025, 3, 24): ["Capstone Copper", "DigiCo Infrastructure REIT", "Imdex",
                         "Macquarie Technology Group", "Nuix", "Spartan Resources",
                         "Temple & Webster"],
}


def resolve_ticker(fmp: FMPClient, name: str) -> str | None:
    for ep in ("search-name", "search-symbol"):
        data = fmp.get_json(ep, {"query": name, "limit": 15})
        if not isinstance(data, list):
            continue
        # prefer an ASX (.AX) listing
        for r in data:
            sym = str(r.get("symbol", "")).upper()
            exch = str(r.get("exchange", "")) + str(r.get("exchangeShortName", ""))
            if sym.endswith(".AX") or "ASX" in exch.upper():
                return sym if sym.endswith(".AX") else sym.split(".")[0] + ".AX"
    return None


def build_events(fmp: FMPClient, store: PriceStore) -> pd.DataFrame:
    rows, helpers = [], []
    for eff, names in EVENTS.items():
        ann = nth_weekday(eff.year, eff.month, weekday=4, n=1)  # ~first Friday
        for nm in names:
            sym = resolve_ticker(fmp, nm)
            if not sym:
                print(f"  [skip] could not resolve ticker for '{nm}'")
                continue
            px = store.get(sym)
            if px.empty or px[px["date"] <= pd.Timestamp(eff)].empty:
                print(f"  [skip] no FMP prices for {nm} ({sym}) around {eff}")
                continue
            rows.append({c: None for c in EVENT_COLUMNS})
            rows[-1].update(
                event_id=stable_hash("asx200", sym, eff), source_type="announcement",
                provider="S&P Dow Jones Indices", etf_index_name="S&P/ASX 200",
                benchmark="S&P/ASX 200", asx_ticker=sym, company_name=nm,
                event_type="OFFICIAL_INDEX_ADD", announcement_date=ann,
                detected_date=ann, effective_date=eff, confidence_score=0.95,
                source_url_or_file="S&P DJI quarterly announcement",
            )
            helpers.append({c: None for c in EVENT_HELPER_COLUMNS})
        print(f"  {eff}: resolved {sum(1 for r in rows if r['effective_date']==eff)} / {len(names)}")
    df = pd.DataFrame(rows, columns=EVENT_COLUMNS)
    for c in EVENT_HELPER_COLUMNS:
        df[c] = [h[c] for h in helpers]
    return df


def main() -> int:
    cfg = load_config(); cfg.ensure_dirs()
    fmp = FMPClient(cfg); store = PriceStore(cfg)
    print("Resolving tickers + prices via FMP...")
    events = build_events(fmp, store)
    if events.empty:
        print("No events resolved."); return 0

    events = estimate_flows(cfg, events, store)
    events = run_event_study(cfg, events, store)

    cols = ["asx_ticker", "company_name", "announcement_date", "effective_date",
            "immediate_reaction", "es_next_open_to_plus3", "delayed_return",
            "es_eff_close_plus5", "tradeable_flag"]
    print("\n=== REAL S&P/ASX 200 inclusion event study ===")
    show = events[[c for c in cols if c in events.columns]].copy()
    for c in ["immediate_reaction", "es_next_open_to_plus3", "delayed_return", "es_eff_close_plus5"]:
        if c in show.columns:
            show[c] = (pd.to_numeric(show[c], errors="coerce") * 100).round(2)
    print(show.to_string(index=False))

    # Inclusion strategy: enter next open after announcement, exit at effective close.
    trades = pd.DataFrame({
        "strategy": "IndexInclusionDrift", "event_id": events["event_id"],
        "asx_ticker": events["asx_ticker"], "direction": 1, "entry_ref": "next_open",
        "exit_spec": "effective", "max_hold_days": 30, "selection_notes": "ASX200 add",
    })
    ledger = backtest_trades(cfg, trades, events, store)
    write_csv(ledger, cfg.path("tables") / "asx200_inclusion_ledger.csv")
    write_csv(events[[c for c in EVENT_COLUMNS if c in events.columns] +
                     ["delayed_flow_opportunity_score"]],
              cfg.path("tables") / "asx200_inclusion_events.csv")

    print("\n=== Announcement -> effective-date drift, after real costs ===")
    m = compute_metrics(ledger)
    for k in ["n_trades", "avg_return", "median_return", "hit_rate",
              "avg_gross_return", "avg_return_after_costs", "avg_return_after_harsh_costs",
              "worst_trade", "best_trade"]:
        if k in m:
            v = m[k]
            print(f"  {k:30s} {round(v,4) if isinstance(v,float) else v}")
    g = claims_gate(ledger)
    print(f"\n  claim_alpha: {g.get('claim_alpha')}  | bootstrap_ci(net): "
          f"{tuple(round(x,4) for x in g.get('bootstrap_ci',(float('nan'),float('nan'))))}")
    print("\nWritten: reports/tables/asx200_inclusion_events.csv, asx200_inclusion_ledger.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
