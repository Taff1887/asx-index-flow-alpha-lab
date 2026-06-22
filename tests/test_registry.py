from __future__ import annotations

import pandas as pd

from index_flow.registry import (
    REGISTRY_COLUMNS,
    build_registry,
    compute_confidence,
    split_and_save,
)


def test_seed_registry_builds_with_schema(cfg):
    reg = build_registry(cfg)
    assert not reg.empty
    assert list(reg.columns) == REGISTRY_COLUMNS
    # confidence in [0, 1]
    assert reg["confidence_score"].between(0, 1).all()
    # ASX-themed seeds flagged
    assert reg["asx_exposure_flag"].fillna(False).astype(bool).any()


def test_confidence_rewards_completeness():
    sparse = {"source": "seed"}
    rich = {
        "source": "manual", "product_name": "X", "issuer": "Y",
        "benchmark_index_name": "Z Index", "holdings_url": "http://h",
        "methodology_url": "http://m", "current_asx_holdings_count": 3,
        "historical_data_available_flag": True, "number_of_holdings": 50, "aum": 1e8,
    }
    assert compute_confidence(rich) > compute_confidence(sparse)


def test_manual_override_wins(cfg):
    folder = cfg.path("data_manual") / "etf_registry"
    folder.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [{"product_ticker": "URNM", "product_name": "Sprott Uranium Miners ETF",
          "issuer": "Sprott", "benchmark_index_name": "North Shore Sprott Uranium Miners Index",
          "holdings_url": "http://example/holdings.csv", "asx_exposure_flag": True}]
    ).to_csv(folder / "my_products.csv", index=False)

    reg = build_registry(cfg)
    row = reg[reg["product_ticker"] == "URNM"]
    assert len(row) == 1
    assert row.iloc[0]["source"] == "manual"
    assert row.iloc[0]["product_name"] == "Sprott Uranium Miners ETF"


def test_split_and_save_writes_three_tables(cfg):
    reg = build_registry(cfg)
    paths = split_and_save(cfg, reg)
    for key in ("etf_registry", "index_registry", "asx_exposed_indices"):
        assert paths[key].exists()
