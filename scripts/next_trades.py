"""NEXT TRADES — a forward, actionable opportunity report (real data).

Three sections:

  A. LIVE index-DELETION rebounds (US, the proven edge): names just force-sold
     out of the S&P 500 / Nasdaq-100 / Dow that are still inside the ~10-trading-
     day snap-back window (backtest: +4.3% mean, CI excludes zero). "Buy now."

  B. ASX forced-flow, NOT-yet-rallied: high forced-flow-score ASX names (heavy
     ETF overhang across many products) whose recent price has gone nowhere —
     i.e. the forced demand is there but the stock hasn't moved. The user's exact
     ask ("an index needs to buy ABC but ABC hasn't rallied"). Watchlist: confirm
     an in-progress buy with detect_etf_accumulation.py once daily snapshots accrue.

  C. Calendar context: when the recurring forced buying happens next.

Honesty: section A is backtested; section B is a structural/under-reaction
screen, not a backtested signal (no thin-cap/ETF-flow history exists to test it).
"""
from __future__ import annotations
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import pandas as pd

from index_flow.config import load_config
from index_flow.fmp_client import FMPClient
from index_flow.price_data import PriceStore
from index_flow.rebalance_calendar import third_friday
from index_flow.utils import get_logger, write_csv

log = get_logger("next_trades")

US_INDICES = {
    "S&P 500": "historical-sp500-constituent",
    "Nasdaq-100": "historical-nasdaq-constituent",
    "Dow Jones": "historical-dowjones-constituent",
}
REBOUND_WINDOW = 10          # trading days
LOOKBACK_DAYS = 35           # calendar days back to find recent deletions


def _d(s):
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def deletion_rebounds(fmp: FMPClient, store: PriceStore, today: date) -> pd.DataFrame:
    rows = []
    cutoff = today - timedelta(days=LOOKBACK_DAYS)
    for index_name, ep in US_INDICES.items():
        data = fmp.get_json(ep, force=True)
        if not isinstance(data, list):
            continue
        for r in data:
            eff = _d(r.get("date"))
            rem = str(r.get("removedTicker", "") or "").strip().upper()
            if eff is None or not rem or eff < cutoff or eff > today:
                continue
            px = store.get(rem, start=str(today - timedelta(days=400)))
            if px.empty:
                continue
            after = px[px["date"] > pd.Timestamp(eff)]
            on_or_before = px[px["date"] <= pd.Timestamp(eff)]
            if on_or_before.empty:
                continue
            eff_close = float(on_or_before["close"].iloc[-1])
            bars_since = len(after)
            last_close = float(px["close"].iloc[-1])
            ret_since = last_close / eff_close - 1.0 if eff_close else np.nan
            rows.append({
                "ticker": rem, "index": index_name, "removed_security": r.get("removedSecurity"),
                "effective_date": eff, "bars_since_effective": bars_since,
                "days_left_in_window": max(0, REBOUND_WINDOW - bars_since),
                "ret_since_effective_%": round(100 * ret_since, 2),
                "reason": r.get("reason"),
                "live": bars_since <= REBOUND_WINDOW,
            })
    df = pd.DataFrame(rows)
    return df.sort_values(["live", "days_left_in_window"], ascending=[False, False]) if not df.empty else df


def asx_not_rallied(cfg, store: PriceStore, top_n: int = 25) -> pd.DataFrame:
    path = cfg.path("tables") / "flow_scanner.csv"
    if not path.exists():
        log.warning("flow_scanner.csv missing — run scripts/flow_scanner.py first")
        return pd.DataFrame()
    sc = pd.read_csv(path)
    sc = sc.sort_values("forced_flow_score", ascending=False).head(60)
    rows = []
    for _, r in sc.iterrows():
        sym = r["asx_ticker"]
        px = store.get(sym)
        if px.empty or len(px) < 70:
            continue
        c = px["close"]
        mom1 = c.iloc[-1] / c.iloc[-22] - 1.0
        mom3 = c.iloc[-1] / c.iloc[-64] - 1.0
        rows.append({
            "asx_ticker": sym, "company": r.get("company"), "n_etfs": r.get("n_etfs"),
            "etf_pct_float_%": round(100 * float(r.get("etf_pct_float", np.nan)), 1),
            "days_to_exit_20pct_adv": round(float(r.get("days_to_exit_20pct_adv", np.nan)), 0),
            "adv_$m": round(float(r.get("adv_dollars_63d", np.nan)) / 1e6, 2),
            "mom_1m_%": round(100 * mom1, 1), "mom_3m_%": round(100 * mom3, 1),
            "forced_flow_score": round(float(r.get("forced_flow_score", np.nan)), 3),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # "not rallied" = soft 1-month move; forced demand present but price flat/down
    df["not_rallied"] = df["mom_1m_%"] <= 3.0
    df = df.sort_values(["not_rallied", "forced_flow_score"], ascending=[False, False])
    return df.head(top_n)


def next_quarterly(today: date) -> date:
    for m in (3, 6, 9, 12):
        tf = third_friday(today.year, m)
        if tf >= today:
            return tf
    return third_friday(today.year + 1, 3)


def main() -> int:
    cfg = load_config(); cfg.ensure_dirs()
    fmp = FMPClient(cfg); store = PriceStore(cfg)
    today = date.today()
    print(f"NEXT TRADES as of {today}\n" + "=" * 60)

    print("\n[A] LIVE index-DELETION rebound longs (proven edge: +4.3% mean, 10d)")
    dr = deletion_rebounds(fmp, store, today)
    if dr.empty:
        print("  No US index deletions in the last", LOOKBACK_DAYS, "days.")
    else:
        write_csv(dr, cfg.path("tables") / "next_trades_deletion_rebounds.csv")
        live = dr[dr["live"]]
        if not live.empty:
            print("  LIVE (still inside the rebound window — BUY candidates):")
            print(live[["ticker", "index", "effective_date", "bars_since_effective",
                        "days_left_in_window", "ret_since_effective_%", "reason"]].to_string(index=False))
        else:
            print("  None currently inside the 10-day window. Most recent deletions:")
            print(dr[["ticker", "index", "effective_date", "bars_since_effective",
                      "ret_since_effective_%"]].head(8).to_string(index=False))

    print("\n[B] ASX forced-flow, NOT-yet-rallied (heavy ETF overhang, price flat/down)")
    nb = asx_not_rallied(cfg, store)
    if nb.empty:
        print("  Run scripts/flow_scanner.py first to populate flow_scanner.csv.")
    else:
        write_csv(nb, cfg.path("tables") / "next_trades_asx_watch.csv")
        watch = nb[nb["not_rallied"]]
        print(watch[["asx_ticker", "company", "n_etfs", "etf_pct_float_%",
                     "days_to_exit_20pct_adv", "adv_$m", "mom_1m_%", "mom_3m_%",
                     "forced_flow_score"]].head(15).to_string(index=False))

    print("\n[C] Calendar context")
    nq = next_quarterly(today)
    print(f"  Next quarterly index/ETF reconstitution (3rd Friday): {nq}")
    print("  Most VanEck/Global X thematic ETFs reconstitute quarterly on that date;")
    print("  Sprott uranium (URNM/URNJ) semi-annually. Forced buying clusters around it.")
    print("\n  NOTE: to catch the buying AS IT HAPPENS, run scripts/fetch_etf_holdings_fmp.py")
    print("  daily, then scripts/detect_etf_accumulation.py (net delta-shares / ADV).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
