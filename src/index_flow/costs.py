"""Transaction-cost model.

Per side, in basis points of notional:

    brokerage  + (half_spread + slippage + impact) * stress

where  impact_bps = impact_coef * participation * 10000  and
       participation = trade_value / ADV_dollars (clipped at max_participation).

A round trip applies the per-side cost twice. The "harsher costs" stress run
multiplies the spread/slippage/impact components by ``costs.harsh_multiplier``.
Brokerage is not stressed (it's contractual).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import Config


@dataclass
class CostResult:
    participation: float
    per_side_bps: float
    round_trip_bps: float
    impact_bps: float
    capped: bool          # True if requested size exceeded max participation
    executable_value: float


def cost_breakdown(
    cfg: Config,
    trade_value: float,
    adv_dollars: float,
    harsh: bool = False,
) -> CostResult:
    c = cfg.config.get("costs", {})
    brokerage = float(c.get("brokerage_bps", 8.0))
    half_spread = float(c.get("half_spread_bps", 15.0))
    slippage = float(c.get("slippage_bps", 10.0))
    impact_coef = float(c.get("impact_coef", 0.10))
    max_part = float(c.get("max_participation", 0.10))
    stress = float(c.get("harsh_multiplier", 2.0)) if harsh else 1.0

    if adv_dollars is None or np.isnan(adv_dollars) or adv_dollars <= 0:
        # No liquidity info: treat as fully capped/illiquid, very high impact.
        return CostResult(
            participation=np.nan, per_side_bps=np.nan, round_trip_bps=np.nan,
            impact_bps=np.nan, capped=True, executable_value=0.0,
        )

    requested_part = trade_value / adv_dollars
    capped = requested_part > max_part
    participation = min(requested_part, max_part)
    executable_value = participation * adv_dollars

    impact_bps = impact_coef * participation * 10000.0
    per_side = brokerage + (half_spread + slippage + impact_bps) * stress
    return CostResult(
        participation=participation,
        per_side_bps=per_side,
        round_trip_bps=2 * per_side,
        impact_bps=impact_bps * stress,
        capped=capped,
        executable_value=executable_value,
    )


def net_return(gross_return: float, round_trip_bps: float) -> float:
    """Apply round-trip cost (bps) to a gross return."""
    if gross_return is None or np.isnan(gross_return):
        return np.nan
    return gross_return - round_trip_bps / 10000.0
