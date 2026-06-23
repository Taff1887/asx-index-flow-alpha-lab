"""Scan ALL ASX-listed ETFs for forced-flow exposure into thin ASX names.

Enumerates every .AX ETF FMP lists (~185), pulls current holdings, keeps ASX
constituents, and produces two tables:

  reports/tables/asx_etf_universe.csv  — one row per ASX ETF: AUM, #ASX holdings,
      #THIN ASX holdings (ADV < $2m). Sort by AUM to find the small/weird ones.
  reports/tables/asx_etf_scanner.csv   — one row per ASX stock: which ASX ETFs
      hold it, ETF % of float, days-to-exit, inflow sensitivity, forced-flow score.

Current holdings only (no ASX-ETF history exists anywhere), so this is a live map,
not a backtest.
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

log = get_logger("asx_etf_scanner")
THIN_ADV = 2_000_000  # A$ ADV below which a holding is "thin"


def list_asx_etfs(fmp: FMPClient) -> list[tuple[str, str]]:
    lst = fmp.get_json("etf-list")
    if not isinstance(lst, list):
        return []
    return [(r["symbol"], r.get("name")) for r in lst
            if str(r.get("symbol", "")).endswith(".AX")]


def main() -> int:
    cfg = load_config(); cfg.ensure_dirs()
    fmp = FMPClient(cfg); store = PriceStore(cfg)

    etfs = list_asx_etfs(fmp)
    log.info("ASX ETFs listed: %d", len(etfs))
    rows, uni = [], []
    for i, (etf, etf_name) in enumerate(etfs):
        if i % 25 == 0:
            log.info("etf %d/%d", i, len(etfs))
        h = fmp.get_json("etf/holdings", {"symbol": etf})
        if not isinstance(h, list) or not h:
            uni.append({"etf": etf, "etf_name": etf_name, "aum": None, "n_asx": 0})
            continue
        info = fmp.get_json("etf/info", {"symbol": etf})
        aum = info[0].get("assetsUnderManagement") if isinstance(info, list) and info else None
        n_asx = 0
        for r in h:
            a = str(r.get("asset", "")).upper(); isin = str(r.get("isin", "")).upper()
            if not (a.endswith(".AX") or (isin.startswith("AU") and len(a) <= 6)):
                continue
            sym = a if a.endswith(".AX") else a.split(".")[0] + ".AX"
            rows.append({"etf": etf, "etf_name": etf_name, "aum": aum, "asx_ticker": sym,
                         "shares": r.get("sharesNumber"), "weight_pct": r.get("weightPercentage"),
                         "value_usd": r.get("marketValue")})
            n_asx += 1
        uni.append({"etf": etf, "etf_name": etf_name, "aum": aum, "n_asx": n_asx})

    if not rows:
        print("No ASX constituents found across ASX ETFs."); return 0
    raw = pd.DataFrame(rows)

    # price/liquidity per unique ASX name (cached across runs)
    advd, advs, shout, lastpx = {}, {}, {}, {}
    for sym in sorted(raw["asx_ticker"].unique()):
        px = store.get(sym)
        if px.empty:
            continue
        d = px["date"].max()
        advd[sym] = adv_dollars(px, d, 63); advs[sym] = adv_shares(px, d, 63)
        lastpx[sym] = float(px[px["date"] <= d]["close"].iloc[-1])
        prof = fmp.profile(sym)
        if prof.get("marketCap") and prof.get("price"):
            shout[sym] = float(prof["marketCap"]) / float(prof["price"])

    raw["adv_dollars"] = raw["asx_ticker"].map(advd)
    raw["is_thin"] = raw["adv_dollars"] < THIN_ADV
    raw["inflow5_usd"] = (pd.to_numeric(raw["weight_pct"], errors="coerce") / 100.0
                          * pd.to_numeric(raw["aum"], errors="coerce") * 0.05)

    # per-ETF universe table (+ thin count)
    thin_by_etf = raw[raw["is_thin"]].groupby("etf")["asx_ticker"].nunique().to_dict()
    uni_df = pd.DataFrame(uni)
    uni_df["n_thin_asx"] = uni_df["etf"].map(thin_by_etf).fillna(0).astype(int)
    uni_df["aum_$m"] = (pd.to_numeric(uni_df["aum"], errors="coerce") / 1e6).round(1)
    uni_df = uni_df.sort_values(["n_thin_asx", "n_asx"], ascending=False)
    write_csv(uni_df[["etf", "etf_name", "aum_$m", "n_asx", "n_thin_asx"]],
              cfg.path("tables") / "asx_etf_universe.csv")

    # per-stock forced-flow table
    agg = raw.groupby("asx_ticker").agg(
        company=("etf_name", "size"), n_asx_etfs=("etf", "nunique"),
        etfs=("etf", lambda s: ",".join(sorted(set(s)))),
        etf_shares=("shares", "sum"), inflow5_usd=("inflow5_usd", "sum"),
    ).reset_index().drop(columns="company")
    recs = []
    for _, r in agg.iterrows():
        sym = r["asx_ticker"]; so = shout.get(sym); a_s = advs.get(sym); a_d = advd.get(sym)
        recs.append({**r.to_dict(), "adv_dollars_63d": a_d, "last_close": lastpx.get(sym),
                     "etf_pct_float": (r["etf_shares"] / so) if so else np.nan,
                     "days_to_exit_20pct_adv": (r["etf_shares"] / (0.2 * a_s)) if a_s else np.nan,
                     "inflow5_buy_days": (r["inflow5_usd"] / a_d) if a_d else np.nan})
    out = pd.DataFrame(recs)
    for c in ["etf_pct_float", "days_to_exit_20pct_adv", "inflow5_buy_days", "n_asx_etfs"]:
        out[c + "_r"] = out[c].rank(pct=True)
    out["forced_flow_score"] = out[[c + "_r" for c in
        ["etf_pct_float", "days_to_exit_20pct_adv", "inflow5_buy_days", "n_asx_etfs"]]].mean(axis=1)
    out = out.drop(columns=[c for c in out.columns if c.endswith("_r")]).sort_values(
        "forced_flow_score", ascending=False)
    write_csv(out, cfg.path("tables") / "asx_etf_scanner.csv")

    pd.set_option("display.width", 200)
    print(f"\nScanned {len(etfs)} ASX ETFs -> {len(uni_df[uni_df.n_asx>0])} hold ASX names; "
          f"{len(out)} unique ASX stocks.\n")
    print("Small/weird ASX ETFs holding the most THIN names:")
    weird = uni_df[(uni_df["n_thin_asx"] > 0) & (uni_df["aum_$m"] < 1000)].head(12)
    print(weird.to_string(index=False))
    print("\nTop ASX stocks by ASX-ETF forced-flow score:")
    disp = out.head(15)[["asx_ticker", "n_asx_etfs", "etf_pct_float", "adv_dollars_63d",
                         "days_to_exit_20pct_adv", "inflow5_buy_days", "forced_flow_score"]].copy()
    disp["etf_pct_float"] = (disp["etf_pct_float"] * 100).round(1)
    disp["adv_dollars_63d"] = (disp["adv_dollars_63d"] / 1e6).round(2)
    disp["days_to_exit_20pct_adv"] = disp["days_to_exit_20pct_adv"].round(0)
    disp["inflow5_buy_days"] = disp["inflow5_buy_days"].round(2)
    disp["forced_flow_score"] = disp["forced_flow_score"].round(3)
    print(disp.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
