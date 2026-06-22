"""make-style helper: save REAL current ETF holdings as dated snapshots via FMP.

For each ETF in the watchlists (or the curated thematic set), pull current
holdings from FMP's stable etf/holdings endpoint, normalise, and store a dated
snapshot under data/processed/holdings_snapshots/<ETF>/<today>.parquet.

Run this on a schedule (e.g. weekly): once you have >=2 dated snapshots per
product, `diff_holdings` + `build_events` produce REAL holdings-change events
with no manual work. This is how the lab acquires genuine flow history over time.
"""
from __future__ import annotations
import sys
from datetime import date, timezone, datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from index_flow.config import load_config
from index_flow.fmp_client import FMPClient
from index_flow.holdings_downloader import normalise_holdings, save_snapshot

THEMATIC = [
    "URNM", "URNJ", "URA", "NLR", "NUKZ", "GDX", "GDXJ", "RING", "SGDM", "GOAU",
    "SILJ", "REMX", "LIT", "BATT", "ILIT", "COPX", "PICK", "XME", "SLX", "MOO",
]


def main() -> int:
    cfg = load_config(); cfg.ensure_dirs()
    fmp = FMPClient(cfg)
    today = datetime.now(timezone.utc).date()
    products = sorted(set(THEMATIC))
    saved = 0
    for etf in products:
        h = fmp.etf_holder(etf)
        if h.empty:
            print(f"  {etf}: no holdings from FMP")
            continue
        # add an 'exchange' hint from ISIN if present so ASX tagging is precise
        raw = h.rename(columns={"asset": "Ticker", "name": "Name",
                                "weight_pct": "Weight", "shares": "Shares",
                                "market_value": "MarketValue"})
        norm = normalise_holdings(raw, etf, today, source="fmp")
        if norm.empty:
            print(f"  {etf}: holdings present but none normalised")
            continue
        save_snapshot(cfg, etf, today, norm)
        saved += 1
        n_asx = norm["constituent_ticker"].str.endswith(".AX").sum()
        print(f"  {etf}: {len(norm)} holdings ({n_asx} ASX) -> snapshot {today}")
    print(f"\nSaved {saved}/{len(products)} ETF snapshots for {today}.")
    print("Re-run on another day, then: make diff-holdings && make build-events")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
