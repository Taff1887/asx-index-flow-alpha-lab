from __future__ import annotations

import pandas as pd

from index_flow.reaction_detector import opportunity_score, study_event
from tests.conftest import make_prices


def _scenario_prices():
    # 60 bdays: flat at 1.00 through the announcement (index 30), then a small
    # immediate gap and an upward drift into the effective date (index 40).
    closes = [1.00] * 31
    for i in range(31, 41):
        closes.append(round(1.00 + 0.10 * (i - 30) / 10, 4))  # 1.01 .. 1.10
    closes += [1.10] * (60 - len(closes))
    df = make_prices(closes)
    df.loc[31, "open"] = 1.005  # immediate reaction = +0.5%
    return df


def test_study_event_measures_underreaction(cfg):
    df = _scenario_prices()
    ann = df["date"].iloc[30].date()
    eff = df["date"].iloc[40].date()
    ev = pd.Series({"announcement_date": ann, "detected_date": ann,
                    "effective_date": eff, "confidence_score": 0.8})
    res = study_event(cfg, ev, df)

    assert abs(res["immediate_reaction"] - 0.005) < 1e-6
    assert res["delayed_return"] > 0.08            # ~9.4% drift to effective
    assert res["underreaction_score"] > 5          # small immediate, big delayed
    assert res["tradeable_flag"] is True
    assert not pd.isna(res["es_next_open_to_effective"])
    assert not pd.isna(res["es_eff_close_plus10"])


def test_opportunity_score_positive_when_flow_and_underreaction():
    ev = pd.Series({
        "flow_pressure": 2.0, "_prob_real": 0.6, "_prob_not_complete": 1.0,
        "_liquidity_tightness": 0.8, "_expected_remaining_flow": 1.0,
        "immediate_reaction": 0.005,
    })
    score = opportunity_score(ev)
    assert score > 0


def test_opportunity_score_nan_without_flow():
    ev = pd.Series({"flow_pressure": float("nan"), "immediate_reaction": 0.01})
    assert pd.isna(opportunity_score(ev))
