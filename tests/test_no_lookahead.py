"""Guard rails: nothing computed 'as of date D' may depend on bars after D."""

from __future__ import annotations

import pandas as pd

from index_flow.liquidity import adv_dollars
from index_flow.price_data import (
    bar_on_or_before,
    next_trading_bar,
    return_between,
)
from index_flow.reaction_detector import study_event
from tests.conftest import make_prices


def _trending():
    closes = [round(1.0 * (1.001 ** i), 6) for i in range(120)]
    return make_prices(closes)


def test_bar_on_or_before_never_uses_future():
    df = _trending()
    d = df["date"].iloc[60].date()
    bar = bar_on_or_before(df, d)
    assert bar["date"].date() <= d


def test_as_of_reads_are_truncation_invariant():
    df = _trending()
    d = df["date"].iloc[60]
    trunc = df[df["date"] <= d]
    # bar on/before D identical whether or not future bars exist
    assert bar_on_or_before(df, d)["close"] == bar_on_or_before(trunc, d)["close"]
    # trailing ADV identical
    assert adv_dollars(df, d, 63) == adv_dollars(trunc, d, 63)


def test_next_bar_is_strictly_after():
    df = _trending()
    d = df["date"].iloc[60]
    nb = next_trading_bar(df, d)
    assert nb["date"] > d


def test_return_between_only_uses_window():
    df = _trending()
    d0 = df["date"].iloc[40]
    d1 = df["date"].iloc[60]
    full = return_between(df, d0, d1)
    trunc = return_between(df[df["date"] <= d1], d0, d1)
    assert full == trunc


def test_study_immediate_reaction_truncation_invariant(cfg):
    df = _trending()
    ann = df["date"].iloc[60].date()
    ev = pd.Series({"announcement_date": ann, "detected_date": ann, "effective_date": None})
    # truncate to the entry bar (index 61): immediate reaction must be unchanged
    trunc = df[df["date"] <= df["date"].iloc[61]]
    full_imm = study_event(cfg, ev, df)["immediate_reaction"]
    trunc_imm = study_event(cfg, ev, trunc)["immediate_reaction"]
    assert full_imm == trunc_imm
