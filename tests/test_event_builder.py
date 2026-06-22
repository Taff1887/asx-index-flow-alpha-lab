from __future__ import annotations

import pandas as pd

from index_flow.event_builder import EVENT_COLUMNS, build_events
from index_flow.holdings_downloader import normalise_holdings, save_snapshot


def _save_two_snapshots(cfg):
    s1 = normalise_holdings(
        pd.DataFrame({"Ticker": ["BHP.AX"], "Weight": [0.5], "Shares": [100]}),
        "URNM", "2024-01-01", "manual",
    )
    s2 = normalise_holdings(
        pd.DataFrame({"Ticker": ["BHP.AX", "PDN.AX"], "Weight": [0.45, 0.10], "Shares": [100, 80]}),
        "URNM", "2024-02-01", "manual",
    )
    save_snapshot(cfg, "URNM", "2024-01-01", s1)
    save_snapshot(cfg, "URNM", "2024-02-01", s2)


def test_holdings_new_position_becomes_event(cfg):
    _save_two_snapshots(cfg)
    events = build_events(cfg)
    assert not events.empty
    assert set(EVENT_COLUMNS).issubset(events.columns)
    new = events[(events.asx_ticker == "PDN.AX") &
                 (events.event_type == "ETF_HOLDINGS_NEW_POSITION")]
    assert len(new) == 1
    assert new.iloc[0]["source_type"] == "holdings_diff"


def test_manual_announcement_becomes_official_add(cfg):
    folder = cfg.path("data_manual") / "rebalance_announcements"
    folder.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [{"provider": "S&P Dow Jones", "index_name": "S&P/ASX 200", "ticker": "DRO",
          "action": "ADD", "announcement_date": "2024-03-01", "effective_date": "2024-03-15"}]
    ).to_csv(folder / "ann.csv", index=False)

    events = build_events(cfg)
    add = events[events.event_type == "OFFICIAL_INDEX_ADD"]
    assert len(add) == 1
    assert add.iloc[0]["asx_ticker"] == "DRO.AX"


def test_event_ids_are_unique_and_stable(cfg):
    _save_two_snapshots(cfg)
    e1 = build_events(cfg)
    e2 = build_events(cfg)
    assert e1["event_id"].is_unique
    assert sorted(e1["event_id"]) == sorted(e2["event_id"])  # deterministic
