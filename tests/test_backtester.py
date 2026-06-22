from __future__ import annotations

import pandas as pd

from index_flow.backtester import backtest_trades
from index_flow.event_builder import EVENT_COLUMNS, EVENT_HELPER_COLUMNS
from index_flow.price_data import PriceStore
from index_flow.strategies import DelayedAnnouncementReaction
from tests.conftest import make_prices, write_prices


def _prices():
    closes = [1.00] * 31
    for i in range(31, 41):
        closes.append(round(1.00 + 0.10 * (i - 30) / 10, 4))  # ramp to 1.10
    closes += [1.10] * (60 - len(closes))
    return make_prices(closes)


def _event_frame(df):
    ann = df["date"].iloc[30].date()
    eff = df["date"].iloc[40].date()
    base = {c: None for c in EVENT_COLUMNS + EVENT_HELPER_COLUMNS}
    base.update(
        event_id="EV1", source_type="announcement", asx_ticker="PDN.AX",
        event_type="OFFICIAL_INDEX_ADD", announcement_date=ann, detected_date=ann,
        effective_date=eff, immediate_reaction=0.005, flow_pressure=2.0,
        confidence_score=0.8, tradeable_flag=True, provider="S&P",
    )
    return pd.DataFrame([base])


def test_executed_trade_has_costs_applied(cfg):
    df = _prices()
    write_prices(cfg, "PDN.AX", df)
    events = _event_frame(df)

    strat = DelayedAnnouncementReaction(name="DelayedAnnouncementReaction", cfg=cfg)
    trades = strat.generate_trades(events)
    assert len(trades) == 1

    ledger = backtest_trades(cfg, trades, events, PriceStore(cfg))
    ex = ledger[~ledger["rejected"].astype(bool)]
    assert len(ex) == 1
    row = ex.iloc[0]
    assert row["gross_return"] > 0.09                 # ~+10% to effective
    assert row["net_return"] < row["gross_return"]    # costs bite
    assert row["harsh_net_return"] < row["net_return"]  # harsher costs bite more
    assert row["exposure_days"] == 9
    assert row["direction"] == 1


def test_missing_prices_is_rejected_not_faked(cfg):
    df = _prices()
    events = _event_frame(df)
    events.loc[0, "asx_ticker"] = "ZZZ.AX"  # no prices written for this symbol
    trades = pd.DataFrame(
        [{"strategy": "DelayedAnnouncementReaction", "event_id": "EV1",
          "asx_ticker": "ZZZ.AX", "direction": 1, "entry_ref": "next_open",
          "exit_spec": "effective", "max_hold_days": 25, "selection_notes": ""}]
    )
    ledger = backtest_trades(cfg, trades, events, PriceStore(cfg))
    assert bool(ledger.iloc[0]["rejected"]) is True
    assert ledger.iloc[0]["reject_reason"] == "no_prices"
