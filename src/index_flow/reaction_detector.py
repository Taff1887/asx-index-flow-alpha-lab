"""Event study: measure immediate vs delayed reaction and score the opportunity.

For each event we compute a battery of windows (relative to the announcement /
holdings-change date and to the effective date), then the headline metrics:

    immediate_reaction      = next_open / announcement_close - 1   (the gap you
                              miss if you can only enter at the next open)
    delayed_return          = next_open -> effective (or +horizon) cumulative
    underreaction_score     = delayed_return / max(|immediate_reaction|, eps)

    delayed_flow_opportunity_score =
        flow_pressure * P(real) * P(buying_not_complete) * liquidity_tightness
        * max(0, expected_remaining_flow) / max(1, |immediate_reaction|*100)

All lookups are point-in-time (only bars dated on/before the reference date for
"as of" reads; the first bar strictly after for next-bar entries), so there is no
lookahead. See ``tests/test_no_lookahead.py``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Config
from .liquidity import is_tradeable
from .price_data import PriceStore
from .utils import get_logger, safe_div, to_date

log = get_logger("index_flow.reaction")

# Window columns produced (prefixed es_ = event study).
STUDY_COLUMNS = [
    "es_pre_ann_-20_-1",
    "es_ann_close_to_next_open",
    "es_ann_close_to_next_close",
    "es_next_open_to_plus3",
    "es_next_open_to_effective",
    "es_effective_open_to_close",
    "es_eff_close_plus1",
    "es_eff_close_plus3",
    "es_eff_close_plus5",
    "es_eff_close_plus10",
]


def _pos_on_or_before(prices: pd.DataFrame, d) -> int | None:
    if prices.empty or d is None:
        return None
    mask = prices["date"] <= pd.Timestamp(d)
    idx = np.where(mask.values)[0]
    return int(idx[-1]) if len(idx) else None


def _close(prices: pd.DataFrame, pos: int | None) -> float | None:
    if pos is None or pos < 0 or pos >= len(prices):
        return None
    v = prices["close"].iloc[pos]
    return None if pd.isna(v) else float(v)


def _open(prices: pd.DataFrame, pos: int | None) -> float | None:
    if pos is None or pos < 0 or pos >= len(prices):
        return None
    v = prices["open"].iloc[pos]
    return None if pd.isna(v) else float(v)


def study_event(cfg: Config, event: pd.Series, prices: pd.DataFrame) -> dict:
    """Compute the window battery + headline metrics for a single event."""
    out: dict = {c: np.nan for c in STUDY_COLUMNS}
    out.update({"immediate_reaction": np.nan, "delayed_return": np.nan,
                "underreaction_score": np.nan, "tradeable_flag": False,
                "entry_date": None, "entry_open": np.nan})

    eps = float(cfg.get("event_study", "small_number", default=0.005))
    horizon = int(cfg.get("event_study", "delayed_horizon_days", default=10))

    ann_date = to_date(event.get("announcement_date")) or to_date(event.get("detected_date"))
    eff_date = to_date(event.get("effective_date"))
    if prices.empty or ann_date is None:
        return out

    apos = _pos_on_or_before(prices, ann_date)
    if apos is None:
        return out
    ann_close = _close(prices, apos)
    npos = apos + 1                       # next trading bar (entry bar)
    next_open = _open(prices, npos)
    next_close = _close(prices, npos)

    # --- announcement-relative windows ---
    if ann_close and (c := _close(prices, apos - 1)) and (c0 := _close(prices, apos - 20)):
        out["es_pre_ann_-20_-1"] = c / c0 - 1.0
    if ann_close and next_open:
        out["es_ann_close_to_next_open"] = next_open / ann_close - 1.0
    if ann_close and next_close:
        out["es_ann_close_to_next_close"] = next_close / ann_close - 1.0
    if next_open and (c3 := _close(prices, apos + 3)):
        out["es_next_open_to_plus3"] = c3 / next_open - 1.0

    # --- effective-relative windows ---
    epos = _pos_on_or_before(prices, eff_date) if eff_date else None
    if epos is not None:
        eff_open = _open(prices, epos)
        eff_close = _close(prices, epos)
        if next_open and eff_close:
            out["es_next_open_to_effective"] = eff_close / next_open - 1.0
        if eff_open and eff_close:
            out["es_effective_open_to_close"] = eff_close / eff_open - 1.0
        for n, col in ((1, "es_eff_close_plus1"), (3, "es_eff_close_plus3"),
                       (5, "es_eff_close_plus5"), (10, "es_eff_close_plus10")):
            cN = _close(prices, epos + n)
            if eff_close and cN:
                out[col] = cN / eff_close - 1.0

    # --- headline metrics ---
    out["immediate_reaction"] = out["es_ann_close_to_next_open"]
    if epos is not None and not np.isnan(out["es_next_open_to_effective"]):
        out["delayed_return"] = out["es_next_open_to_effective"]
    elif next_open:  # no effective date -> drift to +horizon trading days
        cH = _close(prices, apos + horizon)
        out["delayed_return"] = (cH / next_open - 1.0) if cH else np.nan

    imm = out["immediate_reaction"]
    out["underreaction_score"] = safe_div(
        out["delayed_return"], max(abs(imm) if pd.notna(imm) else 0.0, eps)
    )

    # tradeability at entry (next bar after announcement)
    entry_date = prices["date"].iloc[npos].date() if npos < len(prices) else None
    out["entry_date"] = entry_date
    out["entry_open"] = next_open
    if entry_date is not None:
        out["tradeable_flag"] = bool(
            is_tradeable(
                prices, entry_date,
                min_price=float(cfg.get("universe", "min_price", default=0.02)),
                min_adv_dollars=float(cfg.get("universe", "min_adv_dollars", default=50000)),
                lookback=int(cfg.get("flow", "adv_lookback_days", default=63)),
            )
        )
    return out


def opportunity_score(event: pd.Series) -> float:
    """delayed_flow_opportunity_score for one (flow- and study-enriched) event."""
    fp = event.get("flow_pressure")
    if fp is None or (isinstance(fp, float) and np.isnan(fp)):
        return np.nan
    p_real = event.get("_prob_real") or 0.5
    p_inc = event.get("_prob_not_complete")
    p_inc = 1.0 if (p_inc is None or (isinstance(p_inc, float) and np.isnan(p_inc))) else p_inc
    tight = event.get("_liquidity_tightness") or 0.0
    rem = event.get("_expected_remaining_flow")
    rem = 1.0 if (rem is None or (isinstance(rem, float) and np.isnan(rem))) else rem
    imm = event.get("immediate_reaction")
    denom = max(1.0, abs(imm) * 100.0) if pd.notna(imm) else 1.0
    return float(abs(fp) * p_real * p_inc * tight * max(0.0, rem) / denom)


def run_event_study(
    cfg: Config, events: pd.DataFrame, store: PriceStore | None = None
) -> pd.DataFrame:
    """Enrich events with the full study + opportunity score."""
    if events.empty:
        return events
    store = store or PriceStore(cfg)
    events = events.copy()
    for c in STUDY_COLUMNS + ["entry_open", "delayed_flow_opportunity_score"]:
        if c not in events.columns:
            events[c] = np.nan
    # entry_date holds python dates -> must be object dtype, not float64
    if "entry_date" not in events.columns:
        events["entry_date"] = pd.Series([None] * len(events), dtype="object")

    price_cache: dict[str, pd.DataFrame] = {}
    for idx, ev in events.iterrows():
        sym = ev.get("asx_ticker")
        if not isinstance(sym, str) or not sym:
            continue
        if sym not in price_cache:
            price_cache[sym] = store.get(sym)
        res = study_event(cfg, ev, price_cache[sym])
        for k, v in res.items():
            events.at[idx, k] = v
        events.at[idx, "delayed_flow_opportunity_score"] = opportunity_score(events.loc[idx])
    n_trade = int(events["tradeable_flag"].fillna(False).astype(bool).sum())
    log.info("Event study complete: %d events, %d tradeable", len(events), n_trade)
    return events
