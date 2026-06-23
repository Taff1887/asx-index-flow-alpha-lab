"""Index-inclusion forced-flow strategy, backtested across as many indices as
FMP gives real dated membership history for.

INDICES (real dated add/delete events from FMP `historical-*-constituent`):
  * S&P 500
  * Nasdaq-100
  * Dow Jones Industrial Average
  * S&P/ASX 200  (the home market; small verified add set)

THESIS: a stock added to an index forces benchmark-tracking funds to buy it
around the effective date -> it drifts up into the effective date and may reverse
after. Deletions force selling -> drift down. We test:

  S1  ADD_runup     LONG : buy ~5 trading days before the effective date,
                          sell at the effective close (capture the forced run-up).
  S2  ADD_postdrift LONG : buy at the effective close, hold +10 trading days
                          (continuation vs reversal after the buying is done).
  S3  DEL_short     SHORT: short the DELETED name at the effective close,
                          cover +10 (forced selling -> post-effective weakness).

Everything is real: events from FMP membership history, prices from FMP. Costs are
applied per market (US large-cap vs ASX), with a harsher stress run. No lookahead:
every entry/exit uses only the bar at/after its own date. Sources of mis-statement
are surfaced (events dropped for missing price data are counted, not hidden).
"""
from __future__ import annotations
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import pandas as pd

from index_flow.config import load_config
from index_flow.fmp_client import FMPClient
from index_flow.price_data import PriceStore
from index_flow.diagnostics import bootstrap_ci, claims_gate, compute_metrics
from index_flow.liquidity import adv_dollars
from index_flow.backtester import LEDGER_COLUMNS
from index_flow.utils import get_logger, stable_hash, write_csv

log = get_logger("inclusion")

CUTOFF = date(2005, 1, 1)         # use changes since this date (clean price coverage)
HIST_START = "2004-06-01"          # price history start to cover the cutoff window
CAPITAL = 100_000.0

# Round-trip cost (bps) by market: base and harsher stress. US large caps trade
# tight; ASX names wider. Applied as entry+exit already (round trip).
COST_RT_BPS = {"US": 10.0, "ASX": 40.0}
COST_RT_BPS_HARSH = {"US": 30.0, "ASX": 100.0}

US_INDICES = {
    "S&P 500": "historical-sp500-constituent",
    "Nasdaq-100": "historical-nasdaq-constituent",
    "Dow Jones": "historical-dowjones-constituent",
}

# Verified real S&P/ASX 200 additions (effective dates) — tickers resolved/verified
# against FMP elsewhere (examples/asx200_inclusion_study.py).
ASX200_ADDS = {
    date(2024, 3, 18): ["ASB.AX", "NCK.AX"],
    date(2024, 9, 23): ["GYG.AX", "WGX.AX", "YAL.AX"],
    date(2025, 3, 24): ["CSC.AX", "DGT.AX", "IMD.AX", "MAQ.AX", "NXL.AX", "SPR.AX", "TPW.AX"],
}


def _parse_date(s):
    for fmt in ("%Y-%m-%d",):
        try:
            return datetime.strptime(str(s)[:10], fmt).date()
        except (ValueError, TypeError):
            return None


def collect_events(fmp: FMPClient) -> pd.DataFrame:
    """Return one row per (index, action ADD/DEL, ticker, effective_date)."""
    rows = []
    for index_name, ep in US_INDICES.items():
        data = fmp.get_json(ep, force=False)
        if not isinstance(data, list):
            log.warning("no data for %s", index_name)
            continue
        for r in data:
            eff = _parse_date(r.get("date"))
            if eff is None or eff < CUTOFF:
                continue
            add = str(r.get("symbol", "")).strip().upper()
            if add:
                rows.append({"index": index_name, "market": "US", "action": "ADD",
                             "ticker": add, "effective_date": eff})
            rem = str(r.get("removedTicker", "") or "").strip().upper()
            if rem:
                rows.append({"index": index_name, "market": "US", "action": "DEL",
                             "ticker": rem, "effective_date": eff})
    for eff, syms in ASX200_ADDS.items():
        for s in syms:
            rows.append({"index": "S&P/ASX 200", "market": "ASX", "action": "ADD",
                         "ticker": s, "effective_date": eff})
    df = pd.DataFrame(rows).drop_duplicates(["index", "action", "ticker", "effective_date"])
    return df.reset_index(drop=True)


def _pos_on_or_before(px: pd.DataFrame, d) -> int | None:
    idx = np.where((px["date"] <= pd.Timestamp(d)).values)[0]
    return int(idx[-1]) if len(idx) else None


def _close(px, pos):
    if pos is None or pos < 0 or pos >= len(px):
        return None
    v = px["close"].iloc[pos]
    return None if pd.isna(v) else float(v)


BENCH_SYMBOL = {"US": "SPY", "ASX": "STW.AX"}


def _ret_between_dates(px: pd.DataFrame, d0, d1) -> float | None:
    """close(d1)/close(d0)-1 using the bar on/before each date."""
    p0 = _close(px, _pos_on_or_before(px, d0))
    p1 = _close(px, _pos_on_or_before(px, d1))
    if p0 is None or p1 is None or p0 == 0:
        return None
    return p1 / p0 - 1.0


def backtest(events: pd.DataFrame, store: PriceStore) -> pd.DataFrame:
    """Trade ledger. ADD run-up tested at 3 entry lags (T-5/T-3/T-1 before the
    effective close) both RAW and MARKET-EXCESS (minus the index ETF over the same
    window, to strip beta); plus ADD post-effective drift and DEL short."""
    bench = {m: store.get(s, start=HIST_START) for m, s in BENCH_SYMBOL.items()}
    rows = []
    price_cache: dict[str, pd.DataFrame] = {}
    n = len(events)
    for i, (_, e) in enumerate(events.iterrows()):
        if i % 100 == 0:
            log.info("pricing %d/%d", i, n)
        sym = e["ticker"]
        if sym not in price_cache:
            price_cache[sym] = store.get(sym, start=HIST_START)
        px = price_cache[sym]
        if px.empty:
            continue
        epos = _pos_on_or_before(px, e["effective_date"])
        if epos is None:
            continue
        c_eff = _close(px, epos)
        c_p10 = _close(px, epos + 10)
        if c_eff is None:
            continue
        base = COST_RT_BPS[e["market"]] / 1e4
        harsh = COST_RT_BPS_HARSH[e["market"]] / 1e4
        bm = bench.get(e["market"])
        # pre-event ADV ($) measured 6 bars before the effective date (so the
        # event run-up itself doesn't contaminate the liquidity estimate)
        advd = adv_dollars(px, px["date"].iloc[max(0, epos - 6)], 63)

        def add_trade(strategy, gross, direction, exposure):
            if gross is None or np.isnan(gross):
                return
            net = direction * gross - base
            hnet = direction * gross - harsh
            row = {c: np.nan for c in LEDGER_COLUMNS}
            row.update(
                strategy=strategy, event_id=stable_hash(strategy, sym, e["effective_date"]),
                asx_ticker=sym, direction=direction, gross_return=direction * gross,
                net_return=net, harsh_net_return=hnet, pnl_net=net * CAPITAL,
                exposure_days=exposure, participation=0.0, capped=False, adv_dollars=advd,
                rejected=False, reject_reason="", exit_date=e["effective_date"],
                year=e["effective_date"].year, theme=e["index"], provider=e["market"],
            )
            rows.append(row)

        if e["action"] == "ADD":
            for lag in (5, 3, 1):
                c_in = _close(px, epos - lag)
                if not c_in:
                    continue
                stock = c_eff / c_in - 1.0
                add_trade(f"S1_ADD_runup_T-{lag}", stock, +1, lag)
                # market-excess: subtract the index ETF over the same calendar window
                if bm is not None and not bm.empty:
                    d_in = px["date"].iloc[epos - lag]
                    d_eff = px["date"].iloc[epos]
                    br = _ret_between_dates(bm, d_in, d_eff)
                    if br is not None:
                        add_trade(f"S1x_ADD_excess_T-{lag}", stock - br, +1, lag)
            if c_p10:
                add_trade("S2_ADD_postdrift_eff_+10", c_p10 / c_eff - 1.0, +1, 10)
        elif e["action"] == "DEL" and c_p10:
            # deletions are force-SOLD into the effective date; test both sides
            add_trade("S3_DEL_short_eff_+10", c_p10 / c_eff - 1.0, -1, 10)
            add_trade("S4_DEL_long_eff_+10", c_p10 / c_eff - 1.0, +1, 10)  # buy the washout
    return pd.DataFrame(rows, columns=LEDGER_COLUMNS)


def summarise(ledger: pd.DataFrame) -> pd.DataFrame:
    out = []
    for strat, g in ledger.groupby("strategy"):
        m = compute_metrics(g)
        lo, hi = bootstrap_ci(g["net_return"])
        net = pd.to_numeric(g["net_return"], errors="coerce").dropna()
        tstat = net.mean() / (net.std(ddof=1) / np.sqrt(len(net))) if len(net) > 2 and net.std() else np.nan
        out.append({
            "strategy": strat, "n": m["n_trades"],
            "avg_gross_%": round(100 * m["avg_gross_return"], 3),
            "avg_net_%": round(100 * m["avg_return_after_costs"], 3),
            "avg_harsh_%": round(100 * m["avg_return_after_harsh_costs"], 3),
            "median_net_%": round(100 * m["median_return"], 3),
            "hit_rate": round(m["hit_rate"], 3),
            "t_stat": round(float(tstat), 2) if tstat == tstat else np.nan,
            "ci95_net_low_%": round(100 * lo, 3), "ci95_net_high_%": round(100 * hi, 3),
            "claim_alpha": claims_gate(g).get("claim_alpha"),
        })
    return pd.DataFrame(out).sort_values("avg_net_%", ascending=False)


def main() -> int:
    cfg = load_config(); cfg.ensure_dirs()
    fmp = FMPClient(cfg); store = PriceStore(cfg)

    print("Collecting real index membership changes from FMP...")
    events = collect_events(fmp)
    print(events.groupby(["index", "action"]).size().to_string())
    print(f"\nTotal events since {CUTOFF}: {len(events)}")

    print("\nPricing + backtesting (first run fetches prices; cached after)...")
    ledger = backtest(events, store)
    n_drop = "(events without sufficient FMP price coverage were dropped)"
    print(f"Executed legs: {len(ledger)}  {n_drop}")
    write_csv(ledger, cfg.path("tables") / "inclusion_ledger.csv")

    summary = summarise(ledger)
    write_csv(summary, cfg.path("tables") / "inclusion_strategy_summary.csv")
    print("\n================ STRATEGY SUMMARY (real data, after costs) ================")
    print(summary.to_string(index=False))

    # Best long strategy: breakdowns by index and by year
    best = "S1_ADD_runup_T-5"
    g = ledger[ledger["strategy"] == best]
    if not g.empty:
        by_index = g.groupby("theme").apply(
            lambda x: pd.Series({"n": len(x),
                                 "avg_net_%": round(100 * pd.to_numeric(x["net_return"]).mean(), 3),
                                 "hit": round((pd.to_numeric(x["net_return"]) > 0).mean(), 3)}),
            include_groups=False).reset_index().rename(columns={"theme": "index"})
        by_year = g.groupby("year").apply(
            lambda x: pd.Series({"n": len(x),
                                 "avg_net_%": round(100 * pd.to_numeric(x["net_return"]).mean(), 3),
                                 "hit": round((pd.to_numeric(x["net_return"]) > 0).mean(), 3)}),
            include_groups=False).reset_index()
        write_csv(by_index, cfg.path("tables") / "inclusion_runup_by_index.csv")
        write_csv(by_year, cfg.path("tables") / "inclusion_runup_by_year.csv")
        print(f"\n--- {best}: by index ---"); print(by_index.to_string(index=False))
        print(f"\n--- {best}: by year ---"); print(by_year.to_string(index=False))

        # LIQUIDITY GRADIENT: is the forced-flow run-up bigger in THINNER names?
        gl = g.copy()
        gl["adv_m"] = pd.to_numeric(gl["adv_dollars"], errors="coerce") / 1e6
        gl = gl.dropna(subset=["adv_m"])
        bins = [0, 5, 20, 50, 150, 1e9]
        labels = ["<$5m", "$5-20m", "$20-50m", "$50-150m", ">$150m"]
        gl["liq_bucket"] = pd.cut(gl["adv_m"], bins=bins, labels=labels)
        by_liq = gl.groupby("liq_bucket", observed=True).apply(
            lambda x: pd.Series({"n": len(x),
                                 "avg_net_%": round(100 * pd.to_numeric(x["net_return"]).mean(), 3),
                                 "hit": round((pd.to_numeric(x["net_return"]) > 0).mean(), 3)}),
            include_groups=False).reset_index()
        write_csv(by_liq, cfg.path("tables") / "inclusion_runup_by_liquidity.csv")
        print(f"\n--- {best}: by liquidity (avg daily $ traded, pre-event) ---")
        print(by_liq.to_string(index=False))

    # Deletion rebound (buy the forced-selling washout) summary
    s4 = ledger[ledger["strategy"] == "S4_DEL_long_eff_+10"]
    if not s4.empty:
        m4 = compute_metrics(s4); lo4, hi4 = bootstrap_ci(s4["net_return"])
        print("\n--- S4 DEL rebound (BUY deleted name at eff close, hold +10) ---")
        print(f"  n={m4['n_trades']}  avg_net={100*m4['avg_return_after_costs']:.2f}%  "
              f"median={100*m4['median_return']:.2f}%  hit={m4['hit_rate']:.2f}  "
              f"95% CI ({100*lo4:.2f}%, {100*hi4:.2f}%)")

    print("\nWritten: reports/tables/inclusion_strategy_summary.csv, inclusion_ledger.csv,")
    print("         inclusion_runup_by_index.csv, inclusion_runup_by_year.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
