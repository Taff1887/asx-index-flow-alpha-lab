"""Write the report CSVs the project specifies.

Outputs (under ``reports/tables/`` unless noted):
  index_registry.csv, etf_registry.csv, asx_exposed_indices.csv   (registry.py)
  discovered_events.csv, delayed_reaction_events.csv
  strategy_scoreboard.csv                                          (scorecards/)
  failed_hypotheses.csv                                            (scorecards/)
  top_current_watchlist.csv
  provider_inefficiency_ranking.csv, theme_inefficiency_ranking.csv
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from .config import Config
from .diagnostics import compute_metrics
from .event_builder import EVENT_COLUMNS
from .strategies import STRATEGY_CLASSES
from .utils import get_logger, read_csv, to_date, write_csv

log = get_logger("index_flow.reporting")

CANONICAL_EVENT_OUTPUT = EVENT_COLUMNS + ["delayed_flow_opportunity_score"]


def _canonical(events: pd.DataFrame) -> pd.DataFrame:
    cols = [c for c in CANONICAL_EVENT_OUTPUT if c in events.columns]
    return events[cols].copy()


def write_events(cfg: Config, events: pd.DataFrame) -> dict:
    tables = cfg.path("tables")
    paths = {}
    out = _canonical(events) if not events.empty else pd.DataFrame(columns=CANONICAL_EVENT_OUTPUT)
    paths["discovered_events"] = write_csv(out, tables / "discovered_events.csv")

    if not events.empty and "delayed_flow_opportunity_score" in events.columns:
        delayed = events[
            pd.to_numeric(events["delayed_flow_opportunity_score"], errors="coerce").notna()
        ].copy()
        delayed = delayed.sort_values("delayed_flow_opportunity_score", ascending=False)
        paths["delayed_reaction_events"] = write_csv(
            _canonical(delayed), tables / "delayed_reaction_events.csv"
        )
    else:
        paths["delayed_reaction_events"] = write_csv(
            pd.DataFrame(columns=CANONICAL_EVENT_OUTPUT), tables / "delayed_reaction_events.csv"
        )
    return paths


def write_strategy_scoreboard(cfg: Config, ledger: pd.DataFrame) -> str:
    scdir = cfg.path("scorecards")
    rows = []
    if not ledger.empty:
        for strat, grp in ledger.groupby("strategy"):
            m = compute_metrics(grp)
            m["strategy"] = strat
            rows.append(m)
    board = pd.DataFrame(rows)
    if not board.empty:
        cols = ["strategy"] + [c for c in board.columns if c != "strategy"]
        board = board[cols].sort_values("avg_return", ascending=False)
    return str(write_csv(board, scdir / "strategy_scoreboard.csv"))


def _inefficiency_table(events: pd.DataFrame, by: str) -> pd.DataFrame:
    if events.empty or by not in events.columns:
        return pd.DataFrame()
    df = events.copy()
    df["immediate_reaction"] = pd.to_numeric(df.get("immediate_reaction"), errors="coerce")
    df["delayed_return"] = pd.to_numeric(df.get("delayed_return"), errors="coerce")
    df["underreaction_score"] = pd.to_numeric(df.get("underreaction_score"), errors="coerce")
    df["delayed_flow_opportunity_score"] = pd.to_numeric(
        df.get("delayed_flow_opportunity_score"), errors="coerce"
    )
    g = df.groupby(by).agg(
        n_events=("event_id", "nunique"),
        avg_immediate_reaction=("immediate_reaction", "mean"),
        avg_delayed_return=("delayed_return", "mean"),
        median_delayed_return=("delayed_return", "median"),
        avg_underreaction_score=("underreaction_score", "mean"),
        avg_opportunity_score=("delayed_flow_opportunity_score", "mean"),
    ).reset_index()
    return g.sort_values("avg_opportunity_score", ascending=False)


def write_inefficiency_rankings(cfg: Config, events: pd.DataFrame) -> dict:
    tables = cfg.path("tables")
    prov = _inefficiency_table(events, "provider")
    theme = _inefficiency_table(events.rename(columns={"_theme": "theme"}), "theme")
    return {
        "provider_inefficiency_ranking": str(
            write_csv(prov, tables / "provider_inefficiency_ranking.csv")
        ),
        "theme_inefficiency_ranking": str(
            write_csv(theme, tables / "theme_inefficiency_ranking.csv")
        ),
    }


def write_watchlist(cfg: Config, events: pd.DataFrame, as_of: date | None = None) -> str:
    """Current open opportunities: tradeable, opportunity score present, and
    either no effective date yet passed or detected very recently — ranked."""
    tables = cfg.path("tables")
    if events.empty:
        return str(write_csv(pd.DataFrame(columns=CANONICAL_EVENT_OUTPUT),
                             tables / "top_current_watchlist.csv"))
    df = events.copy()
    df["delayed_flow_opportunity_score"] = pd.to_numeric(
        df.get("delayed_flow_opportunity_score"), errors="coerce"
    )
    mask = df["tradeable_flag"].fillna(False).astype(bool)
    if as_of is not None:
        eff = df["effective_date"].map(to_date)
        future = eff.map(lambda d: (d is None) or (d >= as_of))
        mask &= future
    watch = df[mask & df["delayed_flow_opportunity_score"].notna()]
    watch = watch.sort_values("delayed_flow_opportunity_score", ascending=False).head(50)
    return str(write_csv(_canonical(watch), tables / "top_current_watchlist.csv"))


# ---------------------------------------------------------------------------
# Failed-hypotheses log
# ---------------------------------------------------------------------------
FAILED_COLUMNS = ["iteration", "timestamp", "hypothesis", "test", "outcome", "metric", "notes"]


def append_failed_hypothesis(cfg: Config, record: dict) -> str:
    scdir = cfg.path("scorecards")
    path = scdir / "failed_hypotheses.csv"
    existing = read_csv(path) if path.exists() else pd.DataFrame(columns=FAILED_COLUMNS)
    row = {c: record.get(c) for c in FAILED_COLUMNS}
    out = pd.concat([existing, pd.DataFrame([row])], ignore_index=True)
    return str(write_csv(out, path))


def write_all(cfg: Config, events: pd.DataFrame, ledger: pd.DataFrame,
              as_of: date | None = None) -> dict:
    cfg.ensure_dirs()
    paths = {}
    paths.update(write_events(cfg, events))
    paths["strategy_scoreboard"] = write_strategy_scoreboard(cfg, ledger)
    paths.update(write_inefficiency_rankings(cfg, events))
    paths["top_current_watchlist"] = write_watchlist(cfg, events, as_of)
    log.info("Reports written: %s", ", ".join(paths))
    return paths
