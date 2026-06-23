"""Forced-flow opportunity scanner (REAL, live data).

Scans a broad set of global thematic/junior/mining ETFs, finds every ASX-listed
constituent, and scores each by how exposed it is to forced ETF buying/selling —
the AGE/URNJ pattern: a thin name a thematic ETF *must* keep trading.

Per ASX name it computes (all real, from FMP):
  * n_etfs / etfs            : how many products hold it (stacking)
  * etf_pct_float            : ETF-held shares as % of shares outstanding (overhang)
  * days_to_exit_20pct_adv   : ETF-held shares / (20% of ADV) = days to unwind
  * inflow5_buy_days         : if EACH holding ETF takes +5% net inflow, how many
                               days of this stock's own volume the forced buying =
                               sum_i(weight_i * AUM_i * 5%) / ADV$  (the AGE math)
  * forced_flow_score        : rank-blend of the above (higher = more exploitable)

Use it to build a watchlist; confirm an actual in-progress flow with
detect_etf_accumulation.py (snapshot diffs) before acting.
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import pandas as pd

from index_flow.config import load_config
from index_flow.fmp_client import FMPClient
from index_flow.liquidity import adv_dollars, adv_shares
from index_flow.price_data import PriceStore
from index_flow.utils import get_logger, write_csv

log = get_logger("flow_scanner")

# Broad set of global thematic / junior / mining ETFs that hold thin ASX names.
ETFS = [
    # uranium / nuclear
    "URNM", "URNJ", "URA", "NLR", "NUKZ",
    # gold & silver miners (incl. juniors)
    "GDX", "GDXJ", "RING", "SGDM", "GOAU", "SGDJ", "GOEX", "SIL", "SILJ", "SLVP",
    # rare earth / critical metals / lithium / battery
    "REMX", "LIT", "BATT", "ILIT",
    # copper / base / broad mining / materials
    "COPX", "PICK", "XME", "GNR", "MOO", "WOOD",
    # uranium fuel / clean energy
    "SLX", "HJEN", "TAN", "ICLN", "PBW",
    # broad Australia
    "EWA", "FLAU",
]


def asx_holdings(fmp: FMPClient) -> pd.DataFrame:
    rows = []
    for etf in sorted(set(ETFS)):
        h = fmp.get_json("etf/holdings", {"symbol": etf}, force=True)
        if not isinstance(h, list) or not h:
            continue
        info = fmp.get_json("etf/info", {"symbol": etf})
        aum = None
        if isinstance(info, list) and info:
            aum = info[0].get("assetsUnderManagement")
        n_asx = 0
        for r in h:
            asset = str(r.get("asset", "")).upper()
            isin = str(r.get("isin", "")).upper()
            if not (asset.endswith(".AX") or (isin.startswith("AU") and len(asset) <= 6)):
                continue
            sym = asset if asset.endswith(".AX") else asset.split(".")[0] + ".AX"
            rows.append({
                "etf": etf, "etf_aum": aum, "asx_ticker": sym, "name": r.get("name"),
                "shares": r.get("sharesNumber"), "weight_pct": r.get("weightPercentage"),
                "market_value_usd": r.get("marketValue"),
            })
            n_asx += 1
        log.info("%s: %d ASX holdings (AUM $%.0fm)", etf, n_asx, (aum or 0) / 1e6)
    return pd.DataFrame(rows)


def main() -> int:
    cfg = load_config(); cfg.ensure_dirs()
    fmp = FMPClient(cfg); store = PriceStore(cfg)

    raw = asx_holdings(fmp)
    if raw.empty:
        print("No ASX constituents found."); return 0

    # inflow contribution per (etf, name): weight * AUM * 5%
    raw["inflow5_usd"] = (
        pd.to_numeric(raw["weight_pct"], errors="coerce") / 100.0
        * pd.to_numeric(raw["etf_aum"], errors="coerce") * 0.05
    )
    agg = raw.groupby("asx_ticker").agg(
        company=("name", "first"), n_etfs=("etf", "nunique"),
        etfs=("etf", lambda s: ",".join(sorted(set(s)))),
        etf_shares=("shares", "sum"), etf_value_usd=("market_value_usd", "sum"),
        inflow5_usd=("inflow5_usd", "sum"),
    ).reset_index()

    recs = []
    for _, r in agg.iterrows():
        sym = r["asx_ticker"]
        px = store.get(sym)
        last = advd = advs = shares_out = np.nan
        if not px.empty:
            d = px["date"].max()
            last = float(px[px["date"] <= d]["close"].iloc[-1])
            advd = adv_dollars(px, d, 63); advs = adv_shares(px, d, 63)
        prof = fmp.profile(sym)
        if prof.get("marketCap") and prof.get("price"):
            shares_out = float(prof["marketCap"]) / float(prof["price"])
        recs.append({
            **r.to_dict(), "last_close_aud": last, "adv_dollars_63d": advd,
            "shares_outstanding": shares_out,
            "etf_pct_float": (r["etf_shares"] / shares_out) if shares_out else np.nan,
            "days_to_exit_20pct_adv": (r["etf_shares"] / (0.2 * advs)) if advs else np.nan,
            "inflow5_buy_days": (r["inflow5_usd"] / advd) if advd else np.nan,
        })
    out = pd.DataFrame(recs)

    # composite score: blend of percentile ranks (overhang, days-to-exit, inflow days, #etfs)
    for col in ["etf_pct_float", "days_to_exit_20pct_adv", "inflow5_buy_days", "n_etfs"]:
        out[col + "_r"] = out[col].rank(pct=True)
    out["forced_flow_score"] = out[[c + "_r" for c in
        ["etf_pct_float", "days_to_exit_20pct_adv", "inflow5_buy_days", "n_etfs"]]].mean(axis=1)
    out = out.sort_values("forced_flow_score", ascending=False)
    out = out.drop(columns=[c for c in out.columns if c.endswith("_r")])

    path = write_csv(out, cfg.path("tables") / "flow_scanner.csv")
    pd.set_option("display.width", 220); pd.set_option("display.max_columns", 25)
    print(f"\nScanned {len(set(ETFS))} ETFs -> {len(out)} ASX names. Written: {path}\n")
    disp = out.head(20)[["asx_ticker", "company", "n_etfs", "etfs", "etf_pct_float",
                         "adv_dollars_63d", "days_to_exit_20pct_adv", "inflow5_buy_days",
                         "forced_flow_score"]].copy()
    disp["etf_pct_float"] = (disp["etf_pct_float"] * 100).round(1)
    disp["adv_dollars_63d"] = (disp["adv_dollars_63d"] / 1e6).round(2)
    disp["days_to_exit_20pct_adv"] = disp["days_to_exit_20pct_adv"].round(0)
    disp["inflow5_buy_days"] = disp["inflow5_buy_days"].round(1)
    disp["forced_flow_score"] = disp["forced_flow_score"].round(3)
    disp = disp.rename(columns={"etf_pct_float": "own_%", "adv_dollars_63d": "adv_$m",
                                "days_to_exit_20pct_adv": "exit_days", "inflow5_buy_days": "inflow5_days"})
    print(disp.to_string(index=False))
    print("\ninflow5_days = days of this stock's volume the ETFs must buy if each "
          "holding fund takes +5% inflow (the AGE/URNJ mechanism).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
