from __future__ import annotations

import pandas as pd

from index_flow.holdings_diff import diff_product_history, diff_snapshots
from index_flow.holdings_downloader import (
    HOLDINGS_COLUMNS,
    normalise_holdings,
    save_snapshot,
)


def _snap(rows):
    return pd.DataFrame(rows, columns=HOLDINGS_COLUMNS)


def test_normalise_handles_percent_and_aliases():
    raw = pd.DataFrame(
        {"Ticker": ["PDN", "BHP"], "Name": ["Paladin", "BHP"],
         "Weight (%)": ["12.5%", "8.0"], "Shares": [100, 200]}
    )
    norm = normalise_holdings(raw, "URNM", "2024-01-01", "manual")
    assert set(["constituent_ticker", "weight_pct"]).issubset(norm.columns)
    # 12.5% -> 0.125 fraction
    assert abs(norm.loc[norm.constituent_ticker == "PDN", "weight_pct"].iloc[0] - 0.125) < 1e-9


def test_diff_detects_all_change_types():
    prev = _snap([
        {"product_ticker": "URNM", "as_of_date": "2024-01-01", "constituent_ticker": "PDN",
         "constituent_name": "Paladin", "weight_pct": 0.10, "shares": 100, "market_value": 100, "source": "manual"},
        {"product_ticker": "URNM", "as_of_date": "2024-01-01", "constituent_ticker": "BOE",
         "constituent_name": "Boss", "weight_pct": 0.05, "shares": 50, "market_value": 50, "source": "manual"},
    ])
    curr = _snap([
        {"product_ticker": "URNM", "as_of_date": "2024-02-01", "constituent_ticker": "PDN",
         "constituent_name": "Paladin", "weight_pct": 0.15, "shares": 150, "market_value": 150, "source": "manual"},
        {"product_ticker": "URNM", "as_of_date": "2024-02-01", "constituent_ticker": "DYL",
         "constituent_name": "Deep Yellow", "weight_pct": 0.03, "shares": 30, "market_value": 30, "source": "manual"},
    ])
    d = diff_snapshots(prev, curr, "URNM", "2024-01-01", "2024-02-01")
    by = dict(zip(d["constituent_ticker"], d["change_type"]))
    assert by["PDN"] == "WEIGHT_INCREASE"
    assert by["DYL"] == "NEW_POSITION"
    assert by["BOE"] == "DELETED"


def test_diff_product_history_from_saved_snapshots(cfg):
    s1 = normalise_holdings(
        pd.DataFrame({"Ticker": ["PDN"], "Weight": [0.1], "Shares": [100]}),
        "URNM", "2024-01-01", "manual",
    )
    s2 = normalise_holdings(
        pd.DataFrame({"Ticker": ["PDN", "DYL"], "Weight": [0.1, 0.04], "Shares": [100, 40]}),
        "URNM", "2024-02-01", "manual",
    )
    save_snapshot(cfg, "URNM", "2024-01-01", s1)
    save_snapshot(cfg, "URNM", "2024-02-01", s2)
    d = diff_product_history(cfg, "URNM")
    assert "NEW_POSITION" in set(d["change_type"])
    assert (d["constituent_ticker"] == "DYL").any()
