"""Label construction for the ML tasks.

Tasks (see README / notebook 05):
1. Which ASX names get added to niche indices  -> handled by candidate sampling
   in ``models.py`` (needs negatives); a helper stub is provided here.
2. Which announced adds underreact immediately  -> ``label_underreaction``.
3. Which adds have delayed positive drift        -> ``label_delayed_positive``.
4. Which providers/themes create inefficiency    -> aggregate of (2)/(3).

Labels use only realised post-event returns already computed by the event study,
so they are honest targets — never derived from features.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Config

LABEL_COLUMNS = [
    "label_underreaction",        # 1 if immediate move was small (under-reacted)
    "label_delayed_positive",     # 1 if delayed_return beat the drift hurdle
    "label_delayed_return",       # raw regression target
    "label_tradeable_winner",     # 1 if tradeable AND delayed_return > hurdle
]


def build_labels(
    cfg: Config,
    events: pd.DataFrame,
    immediate_threshold: float | None = None,
    drift_hurdle: float | None = None,
) -> pd.DataFrame:
    """Return event_id + label columns. Rows with missing returns get NaN labels."""
    if events.empty:
        return pd.DataFrame(columns=["event_id", *LABEL_COLUMNS])

    if immediate_threshold is None:
        immediate_threshold = float(
            cfg.strategy_params.get("DelayedAnnouncementReaction", {}).get(
                "max_immediate_reaction_pct", 0.03
            )
        )
    if drift_hurdle is None:
        # cost-aware hurdle: round-trip cost in return space (rough)
        bps = (
            float(cfg.get("costs", "brokerage_bps", default=8))
            + float(cfg.get("costs", "half_spread_bps", default=15))
            + float(cfg.get("costs", "slippage_bps", default=10))
        )
        drift_hurdle = 2 * bps / 10000.0  # both sides

    out = pd.DataFrame({"event_id": events["event_id"].values})
    imm = pd.to_numeric(events.get("immediate_reaction"), errors="coerce")
    dly = pd.to_numeric(events.get("delayed_return"), errors="coerce")
    tradeable = events.get("tradeable_flag")
    tradeable = (
        tradeable.fillna(False).astype(bool)
        if tradeable is not None else pd.Series([False] * len(events))
    )

    out["label_underreaction"] = np.where(imm.abs() <= immediate_threshold, 1, 0)
    out.loc[imm.isna(), "label_underreaction"] = np.nan

    out["label_delayed_positive"] = np.where(dly > drift_hurdle, 1, 0)
    out.loc[dly.isna(), "label_delayed_positive"] = np.nan

    out["label_delayed_return"] = dly.values
    out["label_tradeable_winner"] = np.where(
        tradeable.values & (dly > drift_hurdle).values, 1, 0
    )
    out.loc[dly.isna(), "label_tradeable_winner"] = np.nan
    return out
