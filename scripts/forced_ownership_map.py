"""Forced-ownership map (REAL data).

For a broad set of global thematic ETFs, pull CURRENT holdings from FMP, keep the
ASX-listed constituents, and aggregate per ASX name:

  * how many ETFs hold it and which,
  * total shares held by those ETFs,
  * that as a multiple of the stock's own average daily volume (days-to-exit),
  * and as a share of shares outstanding.

High days-to-exit = a thin ASX name carrying a large passive/thematic overhang:
the most exposed to forced flow when any of those products rebalances. 100% real
(FMP holdings + FMP prices); nothing synthetic.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd

from index_flow.config import load_config
from index_flow.fmp_client import FMPClient
from index_flow.liquidity import adv_dollars, adv_shares
from index_flow.price_data import PriceStore, bar_on_or_before
from index_flow.utils import write_csv

# Global thematic ETFs that plausibly hold ASX names (uranium, gold/silver
# miners, rare earth/strategic metals, lithium/battery, copper, broad mining).
ETFS = [
    "URNM", "URNJ", "URA", "NLR", "NUKZ",            # uranium / nuclear
    "GDX", "GDXJ", "RING", "SGDM", "GOAU", "SILJ",   # gold & silver miners
    "REMX", "LIT", "BATT", "ILIT",                    # rare earth / lithium / battery
    "COPX", "PICK", "XME", "SLX", "MOO",             # copper / mining / materials / agri
]


def asx_holdings(fmp: FMPClient) -> pd.DataFrame:
    rows = []
    for etf in ETFS:
        h = fmp.get_json("etf/holdings", {"symbol": etf}, force=True)
        if not isinstance(h, list):
            continue
        for r in h:
            asset = str(r.get("asset", "")).upper()
            isin = str(r.get("isin", "")).upper()
            is_asx = asset.endswith(".AX") or (isin.startswith("AU") and len(asset) <= 6)
            if not is_asx:
                continue
            sym = asset if asset.endswith(".AX") else asset.split(".")[0] + ".AX"
            rows.append({
                "etf": etf, "asx_ticker": sym, "name": r.get("name"),
                "shares": r.get("sharesNumber"), "weight_pct": r.get("weightPercentage"),
                "market_value_usd": r.get("marketValue"),
            })
    return pd.DataFrame(rows)


def main() -> int:
    cfg = load_config(); cfg.ensure_dirs()
    fmp = FMPClient(cfg)
    store = PriceStore(cfg)

    raw = asx_holdings(fmp)
    if raw.empty:
        print("No ASX constituents found across the ETF set (check FMP plan).")
        return 0

    agg = raw.groupby("asx_ticker").agg(
        company=("name", "first"),
        n_etfs=("etf", "nunique"),
        etfs=("etf", lambda s: ",".join(sorted(set(s)))),
        etf_shares=("shares", "sum"),
        etf_value_usd=("market_value_usd", "sum"),
    ).reset_index()

    recs = []
    for _, r in agg.iterrows():
        sym = r["asx_ticker"]
        px = store.get(sym)
        last_close = adv_d = adv_s = shares_out = float("nan")
        if not px.empty:
            bar = bar_on_or_before(px, px["date"].max())
            last_close = float(bar["close"]) if bar is not None else float("nan")
            adv_d = adv_dollars(px, px["date"].max(), 63)
            adv_s = adv_shares(px, px["date"].max(), 63)
        prof = fmp.profile(sym)
        if prof.get("marketCap") and prof.get("price"):
            shares_out = float(prof["marketCap"]) / float(prof["price"])
        recs.append({
            **r.to_dict(),
            "last_close_aud": last_close,
            "adv_shares_63d": adv_s,
            "adv_dollars_63d_aud": adv_d,
            "shares_outstanding": shares_out,
            "etf_pct_of_shares_out": (r["etf_shares"] / shares_out) if shares_out == shares_out and shares_out else float("nan"),
            "days_to_exit_at_100pct_adv": (r["etf_shares"] / adv_s) if adv_s == adv_s and adv_s else float("nan"),
            "days_to_exit_at_20pct_adv": (r["etf_shares"] / (0.2 * adv_s)) if adv_s == adv_s and adv_s else float("nan"),
        })
    out = pd.DataFrame(recs).sort_values("days_to_exit_at_20pct_adv", ascending=False)

    path = write_csv(out, cfg.path("tables") / "forced_ownership_map.csv")
    pd.set_option("display.width", 200); pd.set_option("display.max_columns", 20)
    print(f"\nASX names held across {len(ETFS)} global thematic ETFs: {len(out)}")
    print(f"Written: {path}\n")
    cols = ["asx_ticker", "company", "n_etfs", "etfs", "etf_pct_of_shares_out",
            "adv_dollars_63d_aud", "days_to_exit_at_20pct_adv"]
    top = out[cols].head(20).copy()
    top["etf_pct_of_shares_out"] = (top["etf_pct_of_shares_out"] * 100).round(2)
    top["adv_dollars_63d_aud"] = (top["adv_dollars_63d_aud"] / 1e6).round(2)
    top["days_to_exit_at_20pct_adv"] = top["days_to_exit_at_20pct_adv"].round(1)
    top = top.rename(columns={"etf_pct_of_shares_out": "etf_own_%",
                              "adv_dollars_63d_aud": "adv_$m",
                              "days_to_exit_at_20pct_adv": "days_exit@20%ADV"})
    print(top.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
