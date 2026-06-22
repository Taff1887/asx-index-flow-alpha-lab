"""Estimate forced flow ($) and flow pressure (flow$ / ADV$) per event.

Honesty rules:
* We only compute a dollar estimate when the **inputs exist**:
  - holdings diff with a share delta  -> ``shares_delta * reference_price``
    (the most reliable estimate — it is the actual change in the product's
    position);
  - else a weight delta with a known fund **AUM** -> ``weight_delta * AUM``.
* Official index adds/deletes need the AUM/target-weight of *every* product
  tracking the index to size the flow. Without that licensed data we leave the
  dollar estimate blank (``NaN``) and flag the row — we do **not** invent an AUM.

We also derive the soft factors used by the opportunity score: probability the
event is real, probability buying isn't complete, expected remaining flow
fraction, and liquidity tightness.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Config
from .liquidity import adv_dollars, liquidity_tightness
from .price_data import PriceStore, bar_on_or_before
from .utils import clip01, get_logger, safe_div, to_date

log = get_logger("index_flow.flow")

# Extra working columns produced here (dropped before canonical CSV).
FLOW_HELPER_COLUMNS = [
    "_reference_price",
    "_prob_real",
    "_liquidity_tightness",
    "_expected_remaining_flow",   # fraction in [0,1] of flow still to execute
    "_prob_not_complete",         # P(buying not complete) in [0,1]
    "_flow_estimable",            # bool: did we have inputs to size $?
]

_SELL_TYPES = {"OFFICIAL_INDEX_DELETE", "ETF_HOLDINGS_WEIGHT_DECREASE"}


def _reference_price(prices: pd.DataFrame, ref_date) -> float | None:
    bar = bar_on_or_before(prices, ref_date)
    if bar is None:
        return None
    val = bar.get("close")
    return None if pd.isna(val) else float(val)


def estimate_event(
    cfg: Config,
    event: pd.Series,
    prices: pd.DataFrame,
    as_of=None,
) -> dict:
    """Return the flow fields for one event given its price history."""
    ref_date = to_date(event.get("announcement_date")) or to_date(event.get("detected_date"))
    adv_lb = int(cfg.get("flow", "adv_lookback_days", default=63))
    impl_days = int(cfg.get("flow", "implementation_days", default=5))

    ref_px = _reference_price(prices, ref_date) if ref_date else None
    advd = adv_dollars(prices, ref_date, adv_lb) if (ref_date and not prices.empty) else np.nan

    # ---- dollar estimate ------------------------------------------------
    est = np.nan
    estimable = False
    shares_delta = event.get("_shares_delta")
    weight_delta = event.get("_weight_delta")
    aum = event.get("_aum")
    if pd.notna(shares_delta) and ref_px is not None:
        est = float(shares_delta) * ref_px
        estimable = True
    elif pd.notna(weight_delta) and pd.notna(aum):
        est = float(weight_delta) * float(aum)
        estimable = True

    # sign convention: sells negative
    if estimable and event.get("event_type") in _SELL_TYPES:
        est = -abs(est)
    elif estimable:
        est = abs(est)

    flow_pressure = safe_div(abs(est), advd) if estimable else np.nan

    # ---- soft factors ---------------------------------------------------
    prob_real = clip01(event.get("confidence_score")) or 0.5
    tight = liquidity_tightness(advd)

    # remaining-flow fraction: at detection all flow is ahead; it decays over the
    # implementation window. For historical event studies as_of==detected -> 1.0.
    detected = to_date(event.get("detected_date")) or ref_date
    if as_of is not None and detected is not None:
        elapsed = (to_date(as_of) - detected).days
        window_cal = max(1, int(impl_days * 1.4))  # trading->calendar approx
        remaining = clip01(1.0 - elapsed / window_cal)
    else:
        remaining = 1.0
    prob_not_complete = remaining

    return {
        "estimated_buy_sell_dollars": est,
        "adv_dollars": advd,
        "flow_pressure": flow_pressure,
        "_reference_price": ref_px,
        "_prob_real": prob_real,
        "_liquidity_tightness": tight,
        "_expected_remaining_flow": remaining,
        "_prob_not_complete": prob_not_complete,
        "_flow_estimable": estimable,
    }


def estimate_flows(
    cfg: Config, events: pd.DataFrame, store: PriceStore | None = None, as_of=None
) -> pd.DataFrame:
    """Fill flow fields for every event. Fetches price history per ticker once."""
    if events.empty:
        for c in FLOW_HELPER_COLUMNS:
            events[c] = None
        return events
    store = store or PriceStore(cfg)
    events = events.copy()
    for c in FLOW_HELPER_COLUMNS:
        if c not in events.columns:
            events[c] = None

    price_cache: dict[str, pd.DataFrame] = {}
    n_estimable = 0
    for idx, ev in events.iterrows():
        sym = ev.get("asx_ticker")
        if not isinstance(sym, str) or not sym:
            continue
        if sym not in price_cache:
            price_cache[sym] = store.get(sym)
        res = estimate_event(cfg, ev, price_cache[sym], as_of=as_of)
        for k, v in res.items():
            events.at[idx, k] = v
        n_estimable += int(bool(res.get("_flow_estimable")))
    log.info("Flow estimated for %d/%d events ($ sizeable)", n_estimable, len(events))
    return events
