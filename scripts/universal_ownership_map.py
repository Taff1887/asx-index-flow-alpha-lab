"""UNIVERSAL forced-ownership map: every ASX stock × ALL ETFs.

Unions the global thematic/sector ETF set (flow_scanner.ETFS) with every
ASX-listed ETF FMP knows (~185), pulls current holdings from all of them, and
aggregates per ASX stock the TOTAL ETF ownership — across uranium, gold, REIT,
bank, small-cap, broad-index, global-sector, everything. No theme cherry-picking.

Output: reports/tables/universal_ownership_map.csv — one row per ASX stock with
#ETFs, total ETF shares, ETF % of shares outstanding, days-to-exit vs ADV, and a
forced-flow score. Printed three ways (by % float, by #ETFs, by days-to-exit) so
no single lens dominates.
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # to import flow_scanner

import numpy as np
import pandas as pd

from index_flow.config import load_config
from index_flow.fmp_client import FMPClient
from index_flow.liquidity import adv_dollars, adv_shares
from index_flow.price_data import PriceStore
from index_flow.utils import get_logger, write_csv
from flow_scanner import ETFS as GLOBAL_ETFS

log = get_logger("universal_map")
# Cash / FX / non-equity lines that show up as fake tickers in holdings.
JUNK = {"AUD", "USD", "NZD", "CASH", "AUD.AX", "USD.AX"}


def all_etfs(fmp: FMPClient) -> list[str]:
    lst = fmp.get_json("etf-list")
    asx = [r["symbol"] for r in lst if str(r.get("symbol", "")).endswith(".AX")] if isinstance(lst, list) else []
    return sorted(set(GLOBAL_ETFS) | set(asx))


def main() -> int:
    cfg = load_config(); cfg.ensure_dirs()
    fmp = FMPClient(cfg); store = PriceStore(cfg)
    etfs = all_etfs(fmp)
    etf_set = set(etfs)  # exclude ETF-in-ETF holdings (fund-of-funds hold other ETFs)
    log.info("Universal scan over %d ETFs (global + every ASX-listed)", len(etfs))

    rows = []
    for i, etf in enumerate(etfs):
        if i % 40 == 0:
            log.info("etf %d/%d", i, len(etfs))
        h = fmp.get_json("etf/holdings", {"symbol": etf})
        if not isinstance(h, list) or not h:
            continue
        info = fmp.get_json("etf/info", {"symbol": etf})
        aum = info[0].get("assetsUnderManagement") if isinstance(info, list) and info else None
        for r in h:
            a = str(r.get("asset", "")).upper(); isin = str(r.get("isin", "")).upper()
            if not (a.endswith(".AX") or (isin.startswith("AU") and len(a) <= 6)):
                continue
            sym = a if a.endswith(".AX") else a.split(".")[0] + ".AX"
            if sym in JUNK or sym.replace(".AX", "") in JUNK:
                continue
            if sym in etf_set:  # the holding is itself an ETF (fund-of-funds) -> not a stock
                continue
            rows.append({"etf": etf, "asx_ticker": sym, "shares": r.get("sharesNumber"),
                         "weight_pct": r.get("weightPercentage"), "aum": aum})
    if not rows:
        print("No ASX holdings found."); return 0
    raw = pd.DataFrame(rows)
    raw["inflow5_usd"] = (pd.to_numeric(raw["weight_pct"], errors="coerce") / 100.0
                          * pd.to_numeric(raw["aum"], errors="coerce") * 0.05)
    agg = raw.groupby("asx_ticker").agg(
        n_etfs=("etf", "nunique"), etf_shares=("shares", "sum"),
        inflow5_usd=("inflow5_usd", "sum"),
        etfs=("etf", lambda s: ",".join(sorted(set(s))[:12])),
    ).reset_index()
    log.info("Unique ASX stocks held by >=1 ETF: %d", len(agg))

    recs = []
    for _, r in agg.iterrows():
        sym = r["asx_ticker"]
        px = store.get(sym)
        a_d = a_s = last = so = np.nan
        if not px.empty:
            d = px["date"].max()
            a_d = adv_dollars(px, d, 63); a_s = adv_shares(px, d, 63)
            last = float(px[px["date"] <= d]["close"].iloc[-1])
        prof = fmp.profile(sym)
        if prof.get("marketCap") and prof.get("price"):
            so = float(prof["marketCap"]) / float(prof["price"])
        recs.append({"asx_ticker": sym, "company": prof.get("companyName"),
                     "n_etfs": r["n_etfs"], "etfs": r["etfs"],
                     "etf_shares": r["etf_shares"], "adv_dollars_63d": a_d,
                     "etf_pct_float": (r["etf_shares"] / so) if so else np.nan,
                     "days_to_exit_20pct_adv": (r["etf_shares"] / (0.2 * a_s)) if a_s else np.nan,
                     "inflow5_buy_days": (r["inflow5_usd"] / a_d) if a_d else np.nan})
    out = pd.DataFrame(recs)
    for c in ["etf_pct_float", "days_to_exit_20pct_adv", "inflow5_buy_days", "n_etfs"]:
        out[c + "_r"] = out[c].rank(pct=True)
    out["forced_flow_score"] = out[[c + "_r" for c in
        ["etf_pct_float", "days_to_exit_20pct_adv", "inflow5_buy_days", "n_etfs"]]].mean(axis=1)
    out = out.drop(columns=[c for c in out.columns if c.endswith("_r")])
    out = out.sort_values("forced_flow_score", ascending=False)
    write_csv(out, cfg.path("tables") / "universal_ownership_map.csv")

    def show(df, by, title, asc=False):
        d = df.sort_values(by, ascending=asc).head(15).copy()
        d["etf_pct_float"] = (d["etf_pct_float"] * 100).round(1)
        d["adv_$m"] = (d["adv_dollars_63d"] / 1e6).round(2)
        d["days_to_exit_20pct_adv"] = d["days_to_exit_20pct_adv"].round(0)
        print(f"\n{title}")
        print(d[["asx_ticker", "company", "n_etfs", "etf_pct_float", "adv_$m",
                 "days_to_exit_20pct_adv"]].to_string(index=False))

    pd.set_option("display.width", 200)
    print(f"\nUNIVERSAL MAP: {len(etfs)} ETFs -> {len(out)} ASX stocks held by >=1 ETF.")
    show(out, "etf_pct_float", "Most ETF-OWNED (% of shares outstanding):")
    show(out, "n_etfs", "Held by the MOST ETFs:")
    show(out[out["adv_dollars_63d"] < 5e6], "days_to_exit_20pct_adv",
         "Thinnest names with biggest overhang (ADV<$5m, by days-to-exit):")
    print("\nFull table: reports/tables/universal_ownership_map.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
