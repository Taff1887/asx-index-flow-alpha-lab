"""asx-index-flow-alpha-lab.

An index-flow alpha lab: discover tradable inefficiencies in ASX stocks caused
by forced index/ETF buying and selling, with emphasis on obscure/thematic/global
products where the flow is under-priced.

Public surface is intentionally small; import the submodules you need, e.g.::

    from index_flow.config import load_config
    from index_flow.fmp_client import FMPClient
    from index_flow.event_builder import build_events
"""

from __future__ import annotations

__version__ = "0.1.0"

# Canonical event-type vocabulary used across the engine.
EVENT_TYPES = (
    "OFFICIAL_INDEX_ADD",
    "OFFICIAL_INDEX_DELETE",
    "ETF_HOLDINGS_NEW_POSITION",
    "ETF_HOLDINGS_WEIGHT_INCREASE",
    "ETF_HOLDINGS_WEIGHT_DECREASE",
    "METHODOLOGY_CHANGE",
    "BENCHMARK_CHANGE",
    "RECONSTITUTION",
    "REBALANCE",
    "CUSTOM_MANUAL_EVENT",
)

__all__ = ["__version__", "EVENT_TYPES"]
