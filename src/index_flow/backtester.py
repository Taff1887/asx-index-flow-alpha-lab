"""Event-driven backtester.

Resolves each strategy trade spec against real price history and applies the
cost model. Realism baked in:

* Post-close announcement => earliest entry is the **next bar** (open/close/vwap
  proxy per the trade's ``entry_ref``).
* Exit per the spec: at the effective date close, effective+N, or entry+N.
* Costs: brokerage + half-spread + slippage + impact, plus a **harsher-costs**
  re-run. Round trip applied.
* Trades are **rejected** (not silently zero'd) when there is no entry bar, no
  liquidity, or the name fails the tradeability gate. Participation is capped at
  ``costs.max_participation`` and the cap is recorded for capacity analysis.

The output is a trade ledger; :mod:`index_flow.diagnostics` turns it into metrics.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Config
from .costs import cost_breakdown, net_return
from .liquidity import adv_dollars
from .price_data import PriceStore
from .utils import get_logger, to_date

log = get_logger("index_flow.backtest")

LEDGER_COLUMNS = [
    "strategy", "event_id", "asx_ticker", "direction",
    "entry_date", "entry_price", "exit_date", "exit_price",
    "gross_return", "adv_dollars", "participation", "capped",
    "cost_bps_roundtrip", "net_return", "harsh_net_return",
    "exposure_days", "capital", "pnl_net", "rejected", "reject_reason",
    "year", "theme", "provider", "market_cap",
]


def _pos_on_or_before(prices: pd.DataFrame, d) -> int | None:
    if prices.empty or d is None:
        return None
    idx = np.where((prices["date"] <= pd.Timestamp(d)).values)[0]
    return int(idx[-1]) if len(idx) else None


def _price_at(prices: pd.DataFrame, pos: int, field: str) -> float | None:
    if pos < 0 or pos >= len(prices):
        return None
    v = prices[field].iloc[pos]
    return None if pd.isna(v) else float(v)


def _entry_price(prices: pd.DataFrame, entry_pos: int, entry_ref: str) -> float | None:
    if entry_ref == "next_close":
        return _price_at(prices, entry_pos, "close")
    if entry_ref == "vwap_proxy":
        return _price_at(prices, entry_pos, "vwap")
    return _price_at(prices, entry_pos, "open")  # next_open default


def _resolve_exit_pos(prices: pd.DataFrame, entry_pos: int, eff_pos: int | None,
                      exit_spec: str, max_hold: int) -> int | None:
    if exit_spec == "effective":
        base = eff_pos if eff_pos is not None else entry_pos + max_hold
        return base
    if exit_spec.startswith("eff_close_plus:"):
        n = int(exit_spec.split(":")[1])
        base = (eff_pos + n) if eff_pos is not None else entry_pos + max_hold
        return base
    if exit_spec.startswith("plus:"):
        n = int(exit_spec.split(":")[1])
        return entry_pos + n
    return eff_pos if eff_pos is not None else entry_pos + max_hold


def backtest_trades(
    cfg: Config,
    trades: pd.DataFrame,
    events: pd.DataFrame,
    store: PriceStore | None = None,
) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(columns=LEDGER_COLUMNS)
    store = store or PriceStore(cfg)
    capital = float(cfg.get("backtest", "capital_per_trade", default=100000))
    adv_lb = int(cfg.get("flow", "adv_lookback_days", default=63))

    meta = events.set_index("event_id")
    price_cache: dict[str, pd.DataFrame] = {}
    rows = []
    for _, t in trades.iterrows():
        eid = t["event_id"]
        sym = t["asx_ticker"]
        ev = meta.loc[eid] if eid in meta.index else None
        row = {c: np.nan for c in LEDGER_COLUMNS}
        row.update(
            strategy=t["strategy"], event_id=eid, asx_ticker=sym,
            direction=int(t["direction"]), capital=capital, rejected=True,
        )
        if ev is None or not isinstance(sym, str) or not sym:
            row["reject_reason"] = "no_event_or_symbol"
            rows.append(row); continue
        if isinstance(ev, pd.DataFrame):
            ev = ev.iloc[0]
        row["theme"] = ev.get("_theme")
        row["provider"] = ev.get("provider")
        row["market_cap"] = ev.get("market_cap")

        if sym not in price_cache:
            price_cache[sym] = store.get(sym)
        prices = price_cache[sym]
        if prices.empty:
            row["reject_reason"] = "no_prices"
            rows.append(row); continue

        ann_date = to_date(ev.get("announcement_date")) or to_date(ev.get("detected_date"))
        apos = _pos_on_or_before(prices, ann_date)
        if apos is None:
            row["reject_reason"] = "no_bar_at_announcement"
            rows.append(row); continue
        entry_pos = apos + 1
        entry_price = _entry_price(prices, entry_pos, t["entry_ref"])
        if entry_price is None or entry_price <= 0:
            row["reject_reason"] = "no_entry_bar"
            rows.append(row); continue

        eff_pos = _pos_on_or_before(prices, to_date(ev.get("effective_date")))
        exit_pos = _resolve_exit_pos(prices, entry_pos, eff_pos, t["exit_spec"], int(t["max_hold_days"]))
        if exit_pos is None or exit_pos <= entry_pos:
            # clamp to entry+max_hold if effective preceded entry
            exit_pos = min(entry_pos + int(t["max_hold_days"]), len(prices) - 1)
        exit_price = _price_at(prices, exit_pos, "close")
        if exit_price is None:
            row["reject_reason"] = "no_exit_bar"
            rows.append(row); continue

        entry_date = prices["date"].iloc[entry_pos].date()
        exit_date = prices["date"].iloc[exit_pos].date()
        advd = adv_dollars(prices, entry_date, adv_lb)

        gross = int(t["direction"]) * (exit_price / entry_price - 1.0)
        cost = cost_breakdown(cfg, capital, advd, harsh=False)
        harsh = cost_breakdown(cfg, capital, advd, harsh=True)
        if np.isnan(cost.round_trip_bps):
            row["reject_reason"] = "no_liquidity"
            rows.append(row); continue

        net = net_return(gross, cost.round_trip_bps)
        harsh_net = net_return(gross, harsh.round_trip_bps)

        row.update(
            entry_date=entry_date, entry_price=entry_price,
            exit_date=exit_date, exit_price=exit_price,
            gross_return=gross, adv_dollars=advd,
            participation=cost.participation, capped=cost.capped,
            cost_bps_roundtrip=cost.round_trip_bps,
            net_return=net, harsh_net_return=harsh_net,
            exposure_days=int(exit_pos - entry_pos),
            pnl_net=net * capital,
            rejected=False, reject_reason="",
            year=entry_date.year,
        )
        rows.append(row)

    ledger = pd.DataFrame(rows, columns=LEDGER_COLUMNS)
    n_ok = int((~ledger["rejected"]).sum())
    log.info("Backtest: %d trades, %d executed, %d rejected", len(ledger), n_ok, len(ledger) - n_ok)
    return ledger


def run_all_strategies(cfg: Config, events: pd.DataFrame, store: PriceStore | None = None) -> pd.DataFrame:
    """Generate + backtest trades for every enabled strategy; stacked ledger."""
    from .strategies import enabled_strategies

    store = store or PriceStore(cfg)
    ledgers = []
    for strat in enabled_strategies(cfg):
        trades = strat.generate_trades(events)
        if trades.empty:
            continue
        ledgers.append(backtest_trades(cfg, trades, events, store))
    if not ledgers:
        return pd.DataFrame(columns=LEDGER_COLUMNS)
    return pd.concat(ledgers, ignore_index=True)
