"""Strategy definitions.

A strategy is a thin, declarative object: it (a) *selects* events from the
enriched events table and (b) maps each selected event to a trade spec — entry
reference, exit rule, and direction. It does **not** touch prices; the backtester
resolves trade specs against price history (lookahead-safe) and applies costs.
This keeps every strategy comparable and the execution assumptions in one place.

Built first and fully wired: :class:`DelayedAnnouncementReaction`. The rest share
the same interface and are progressively enabled via ``strategy_params.yaml`` and
the research loop. ``EffectiveDateFade`` is research-only (no shorting assumed).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import Config
from .utils import get_logger

log = get_logger("index_flow.strategies")

_LONG_TYPES = {"OFFICIAL_INDEX_ADD", "ETF_HOLDINGS_NEW_POSITION", "ETF_HOLDINGS_WEIGHT_INCREASE",
               "RECONSTITUTION", "REBALANCE", "CUSTOM_MANUAL_EVENT"}


def _col(events: pd.DataFrame, name: str) -> pd.Series:
    """Numeric column or an all-NaN series if absent (filters then pass-through)."""
    if name in events.columns:
        return pd.to_numeric(events[name], errors="coerce")
    return pd.Series(np.nan, index=events.index)


def _normalise_entry(entry: str) -> str:
    return {"detected_next_open": "next_open"}.get(entry, entry or "next_open")


def _normalise_exit(exit_str: str) -> str:
    """Map config exit labels to canonical backtester exit specs."""
    m = {
        "effective_open": "effective",
        "effective": "effective",
        "eff_close_plus1": "eff_close_plus:1",
        "eff_close_plus3": "eff_close_plus:3",
        "eff_close_plus5": "eff_close_plus:5",
        "eff_close_plus10": "eff_close_plus:10",
        "plus3": "plus:3",
        "plus5": "plus:5",
        "plus10": "plus:10",
    }
    return m.get(exit_str, exit_str or "effective")


TRADE_COLUMNS = [
    "strategy", "event_id", "asx_ticker", "direction",
    "entry_ref", "exit_spec", "max_hold_days", "selection_notes",
]


@dataclass
class BaseStrategy:
    name: str = "BaseStrategy"
    cfg: Config | None = None
    params: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.cfg is not None:
            self.params = {**self.params, **self.cfg.strategy_params.get(self.name, {})}

    # --- overridable -----------------------------------------------------
    @property
    def enabled(self) -> bool:
        return bool(self.params.get("enabled", False))

    def select(self, events: pd.DataFrame) -> pd.Series:
        """Boolean mask of events this strategy trades. Override per strategy."""
        return pd.Series(True, index=events.index)

    def direction(self, event: pd.Series) -> int:
        return 1 if event.get("event_type") in _LONG_TYPES else 0

    # --- shared ----------------------------------------------------------
    def generate_trades(self, events: pd.DataFrame) -> pd.DataFrame:
        if events.empty or not self.enabled:
            return pd.DataFrame(columns=TRADE_COLUMNS)
        mask = self.select(events) & events.get(
            "tradeable_flag", pd.Series(True, index=events.index)
        ).fillna(False).astype(bool)
        chosen = events[mask]
        entry = _normalise_entry(self.params.get("entry", "next_open"))
        exit_spec = _normalise_exit(self.params.get("exit", "effective"))
        max_hold = int(self.params.get("max_hold_days", 30))
        rows = []
        for _, ev in chosen.iterrows():
            d = self.direction(ev)
            if d == 0:
                continue
            rows.append(
                {
                    "strategy": self.name,
                    "event_id": ev.get("event_id"),
                    "asx_ticker": ev.get("asx_ticker"),
                    "direction": d,
                    "entry_ref": entry,
                    "exit_spec": exit_spec,
                    "max_hold_days": max_hold,
                    "selection_notes": self.name,
                }
            )
        return pd.DataFrame(rows, columns=TRADE_COLUMNS)


# ---------------------------------------------------------------------------
class DelayedAnnouncementReaction(BaseStrategy):
    name: str = "DelayedAnnouncementReaction"

    def select(self, events: pd.DataFrame) -> pd.Series:
        p = self.params
        imm = _col(events, "immediate_reaction").abs()
        fp = _col(events, "flow_pressure")
        conf = _col(events, "confidence_score")
        is_add = events["event_type"].isin(_LONG_TYPES)
        cond = is_add
        cond &= imm.le(p.get("max_immediate_reaction_pct", 0.03)) | imm.isna()
        cond &= fp.ge(p.get("min_flow_pressure", 0.5)) | fp.isna()
        cond &= conf.ge(p.get("min_confidence", 0.4)) | conf.isna()
        return cond


class HighFlowLowLiquidity(BaseStrategy):
    name: str = "HighFlowLowLiquidity"

    def select(self, events: pd.DataFrame) -> pd.Series:
        p = self.params
        fp = _col(events, "flow_pressure")
        adv = _col(events, "adv_dollars")
        cond = events["event_type"].isin(_LONG_TYPES)
        cond &= fp.ge(p.get("min_flow_pressure", 1.0))
        cond &= adv.le(p.get("max_adv_dollars", 2_000_000)) | adv.isna()
        return cond


class HoldingsDiffSignal(BaseStrategy):
    name: str = "HoldingsDiffSignal"

    def select(self, events: pd.DataFrame) -> pd.Series:
        p = self.params
        new_w = _col(events, "new_weight")
        imm = _col(events, "immediate_reaction").abs()
        cond = events["source_type"].eq("holdings_diff")
        cond &= events["event_type"].isin(_LONG_TYPES)
        cond &= new_w.ge(p.get("min_new_weight_pct", 0.001)) | new_w.isna()
        cond &= imm.le(p.get("max_immediate_reaction_pct", 0.04)) | imm.isna()
        return cond


class MethodologyChangeAnticipation(BaseStrategy):
    name: str = "MethodologyChangeAnticipation"

    def select(self, events: pd.DataFrame) -> pd.Series:
        return events["event_type"].isin({"METHODOLOGY_CHANGE", "BENCHMARK_CHANGE"})


class ThematicIndexPrediction(BaseStrategy):
    name: str = "ThematicIndexPrediction"
    # Prediction lives in models.py; as a *traded* strategy it is disabled by
    # default and selects nothing here.
    def select(self, events: pd.DataFrame) -> pd.Series:
        return pd.Series(False, index=events.index)


class RebalanceCalendarTrade(BaseStrategy):
    name: str = "RebalanceCalendarTrade"

    def select(self, events: pd.DataFrame) -> pd.Series:
        p = self.params
        conf = _col(events, "confidence_score")
        cond = events["event_type"].isin({"REBALANCE", "RECONSTITUTION", "OFFICIAL_INDEX_ADD"})
        cond &= conf.ge(p.get("min_confidence", 0.7))
        return cond


class EffectiveDateFade(BaseStrategy):
    name: str = "EffectiveDateFade"

    def generate_trades(self, events: pd.DataFrame) -> pd.DataFrame:
        # Research-only: shorting availability is not assumed. The event study
        # (es_eff_close_plus*) is where the fade is *measured*; no trades emitted.
        log.info("EffectiveDateFade is study-only; emitting no trades")
        return pd.DataFrame(columns=TRADE_COLUMNS)


class MultiETFStackedFlow(BaseStrategy):
    name: str = "MultiETFStackedFlow"

    def select(self, events: pd.DataFrame) -> pd.Series:
        p = self.params
        min_products = int(p.get("min_products", 2))
        # count distinct products per ticker within the window
        counts = events.groupby("asx_ticker")["event_id"].transform("nunique")
        cond = events["event_type"].isin(_LONG_TYPES) & counts.ge(min_products)
        return cond


class ForgottenSmallCapAdd(BaseStrategy):
    name: str = "ForgottenSmallCapAdd"

    def select(self, events: pd.DataFrame) -> pd.Series:
        p = self.params
        adv = _col(events, "adv_dollars")
        mcap = _col(events, "market_cap")
        imm = _col(events, "immediate_reaction").abs()
        fp = _col(events, "flow_pressure")
        obsc = _col(events, "source_obscurity_score")
        cond = events["event_type"].isin(_LONG_TYPES)
        cond &= adv.le(p.get("max_adv_dollars", 1_000_000)) | adv.isna()
        cond &= mcap.le(p.get("max_market_cap", 500_000_000)) | mcap.isna()
        cond &= imm.le(p.get("max_immediate_reaction_pct", 0.02)) | imm.isna()
        cond &= fp.ge(p.get("min_flow_pressure", 0.75)) | fp.isna()
        cond &= obsc.ge(p.get("min_source_obscurity", 0.5)) | obsc.isna()
        return cond


STRATEGY_CLASSES = {
    c.name: c
    for c in (
        DelayedAnnouncementReaction,
        HighFlowLowLiquidity,
        HoldingsDiffSignal,
        MethodologyChangeAnticipation,
        ThematicIndexPrediction,
        RebalanceCalendarTrade,
        EffectiveDateFade,
        MultiETFStackedFlow,
        ForgottenSmallCapAdd,
    )
}


def build_strategy(name: str, cfg: Config) -> BaseStrategy:
    cls = STRATEGY_CLASSES[name]
    return cls(name=name, cfg=cfg)


def enabled_strategies(cfg: Config) -> list[BaseStrategy]:
    out = []
    for name in STRATEGY_CLASSES:
        s = build_strategy(name, cfg)
        if s.enabled:
            out.append(s)
    return out
