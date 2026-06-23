"""Forward ETF-accumulation detector — the live AGE/URNJ signal.

Diffs the two most recent dated holdings snapshots of each ETF
(data/processed/holdings_snapshots/<ETF>/<date>.parquet, saved by
fetch_etf_holdings_fmp.py) and aggregates the REAL change in shares held per ASX
name. Positive net change across ETFs = funds are actively BUYING that name; we
express it as days of the stock's own volume, so a thin name being accumulated
jumps out.

Needs >=2 snapshot dates per ETF. Run fetch_etf_holdings_fmp.py on at least two
different days first (it is harmless to run daily and is what accrues the
history). Until then this reports that more snapshots are needed.
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import pandas as pd

from index_flow.asx_universe import map_constituent_to_asx
from index_flow.config import load_config
from index_flow.holdings_diff import diff_snapshots
from index_flow.holdings_downloader import list_snapshots
from index_flow.liquidity import adv_shares
from index_flow.price_data import PriceStore
from index_flow.utils import get_logger, read_parquet, write_csv

log = get_logger("accumulation")


def main() -> int:
    cfg = load_config(); cfg.ensure_dirs()
    store = PriceStore(cfg)
    base = cfg.path("data_processed") / "holdings_snapshots"
    if not base.exists():
        print("No snapshots yet. Run scripts/fetch_etf_holdings_fmp.py first."); return 0

    etfs = [d.name for d in base.iterdir() if d.is_dir()]
    have_two = [e for e in etfs if len(list_snapshots(cfg, e)) >= 2]
    if not have_two:
        print(f"{len(etfs)} ETF snapshot folders found, but none has >=2 dated snapshots yet.")
        print("Run fetch_etf_holdings_fmp.py again on a LATER day, then re-run this.")
        print("(That second snapshot is what turns the share-delta into a real flow signal.)")
        return 0

    changes = []
    for etf in have_two:
        snaps = list_snapshots(cfg, etf)
        (d0, p0), (d1, p1) = snaps[-2], snaps[-1]
        diff = diff_snapshots(read_parquet(p0), read_parquet(p1), etf, d0, d1, include_unchanged=False)
        for _, r in diff.iterrows():
            sym = map_constituent_to_asx(r["constituent_ticker"], r["constituent_name"])
            if sym is None or pd.isna(r["shares_delta"]):
                continue
            changes.append({"etf": etf, "asx_ticker": sym, "shares_delta": r["shares_delta"],
                            "from": d0, "to": d1})
    if not changes:
        print("Snapshots present but no ASX share changes detected between the two dates.")
        return 0

    df = pd.DataFrame(changes)
    agg = df.groupby("asx_ticker").agg(
        net_shares=("shares_delta", "sum"),
        n_etfs_trading=("etf", "nunique"),
        etfs=("etf", lambda s: ",".join(sorted(set(s)))),
        window_to=("to", "max"),
    ).reset_index()

    recs = []
    for _, r in agg.iterrows():
        px = store.get(r["asx_ticker"])
        advs = adv_shares(px, px["date"].max(), 63) if not px.empty else np.nan
        recs.append({**r.to_dict(),
                     "adv_shares_63d": advs,
                     "buy_days_of_volume": (r["net_shares"] / advs) if advs else np.nan})
    out = pd.DataFrame(recs).sort_values("buy_days_of_volume", ascending=False)
    path = write_csv(out, cfg.path("tables") / "etf_accumulation.csv")

    print(f"\nETF accumulation between snapshots (latest pair per ETF). Written: {path}\n")
    acc = out[out["buy_days_of_volume"] > 0].head(20)
    print("ASX names being ACCUMULATED (net ETF buying, in days of own volume):")
    print(acc[["asx_ticker", "n_etfs_trading", "etfs", "net_shares", "buy_days_of_volume"]]
          .to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
