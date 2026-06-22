"""Backtest diagnostics: metrics, bucketed breakdowns, bootstrap CIs, and the
"claims discipline" gate that decides whether a result may be called alpha.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Config
from .utils import get_logger

log = get_logger("index_flow.diagnostics")


def _executed(ledger: pd.DataFrame) -> pd.DataFrame:
    if ledger.empty:
        return ledger
    return ledger[~ledger["rejected"].fillna(True).astype(bool)].copy()


def max_drawdown(returns: pd.Series) -> float:
    """Max drawdown of an equal-weight, sequentially-compounded equity curve."""
    r = returns.dropna()
    if r.empty:
        return np.nan
    equity = (1.0 + r).cumprod()
    peak = equity.cummax()
    dd = equity / peak - 1.0
    return float(dd.min())


def compute_metrics(ledger: pd.DataFrame, return_col: str = "net_return") -> dict:
    ex = _executed(ledger)
    if ex.empty:
        return {"n_trades": 0}
    r = pd.to_numeric(ex[return_col], errors="coerce").dropna()
    gross = pd.to_numeric(ex["gross_return"], errors="coerce")
    harsh = pd.to_numeric(ex["harsh_net_return"], errors="coerce")
    wins = r[r > 0]
    losses = r[r < 0]
    ordered = ex.sort_values("exit_date")
    m = {
        "n_trades": int(len(r)),
        "avg_return": float(r.mean()),
        "median_return": float(r.median()),
        "hit_rate": float((r > 0).mean()),
        "payoff_ratio": float(wins.mean() / abs(losses.mean())) if len(losses) and losses.mean() != 0 else np.nan,
        "worst_trade": float(r.min()),
        "best_trade": float(r.max()),
        "max_drawdown": max_drawdown(ordered[return_col]),
        "avg_exposure_days": float(pd.to_numeric(ex["exposure_days"], errors="coerce").mean()),
        "total_exposure_days": float(pd.to_numeric(ex["exposure_days"], errors="coerce").sum()),
        "avg_gross_return": float(gross.mean()),
        "avg_return_after_costs": float(r.mean()),
        "avg_return_after_harsh_costs": float(harsh.mean()),
        "total_pnl_net": float(pd.to_numeric(ex["pnl_net"], errors="coerce").sum()),
        "median_participation": float(pd.to_numeric(ex["participation"], errors="coerce").median()),
        "pct_capped": float(ex["capped"].fillna(False).astype(bool).mean()),
    }
    return m


def bootstrap_ci(returns: pd.Series, n_boot: int = 2000, alpha: float = 0.05,
                 seed: int = 7) -> tuple[float, float]:
    """Percentile bootstrap CI for the MEAN of realised (real) returns."""
    r = pd.to_numeric(returns, errors="coerce").dropna().values
    if len(r) < 3:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    means = np.array([rng.choice(r, size=len(r), replace=True).mean() for _ in range(n_boot)])
    lo = float(np.percentile(means, 100 * alpha / 2))
    hi = float(np.percentile(means, 100 * (1 - alpha / 2)))
    return (lo, hi)


def _bucket(series: pd.Series, edges: list[float], labels: list[str]) -> pd.Series:
    return pd.cut(pd.to_numeric(series, errors="coerce"), bins=edges, labels=labels)


def breakdown(ledger: pd.DataFrame, events: pd.DataFrame, by: str) -> pd.DataFrame:
    """Metrics grouped by a dimension. Supports ledger columns and event-derived
    buckets: year, theme, provider, strategy, market_cap_bucket, liquidity_bucket,
    immediate_reaction_bucket, flow_pressure_bucket."""
    ex = _executed(ledger)
    if ex.empty:
        return pd.DataFrame()
    df = ex.merge(
        events[["event_id", "immediate_reaction", "flow_pressure"]],
        on="event_id", how="left",
    ) if not events.empty else ex.copy()

    if by == "market_cap_bucket":
        df[by] = _bucket(df["market_cap"], [-1, 1e8, 5e8, 2e9, 1e11],
                         ["<100m", "100-500m", "500m-2b", ">2b"])
    elif by == "liquidity_bucket":
        df[by] = _bucket(df["adv_dollars"], [-1, 2.5e5, 1e6, 5e6, 1e12],
                         ["<250k", "250k-1m", "1-5m", ">5m"])
    elif by == "immediate_reaction_bucket":
        df[by] = _bucket(df["immediate_reaction"].abs(), [-1e-9, 0.005, 0.02, 0.05, 1.0],
                         ["<0.5%", "0.5-2%", "2-5%", ">5%"])
    elif by == "flow_pressure_bucket":
        df[by] = _bucket(df["flow_pressure"], [-1e-9, 0.5, 1.0, 3.0, 1e9],
                         ["<0.5x", "0.5-1x", "1-3x", ">3x"])

    if by not in df.columns:
        log.warning("breakdown dimension '%s' not present", by)
        return pd.DataFrame()

    out = []
    for key, grp in df.groupby(by, observed=True):
        m = compute_metrics(grp)
        m[by] = key
        out.append(m)
    res = pd.DataFrame(out)
    if not res.empty:
        cols = [by] + [c for c in res.columns if c != by]
        res = res[cols].sort_values("n_trades", ascending=False)
    return res


def claims_gate(ledger: pd.DataFrame, min_trades: int = 5,
                single_trade_max_share: float = 0.5) -> dict:
    """Apply the README's claims discipline. Returns each check + overall verdict.

    A result may only be called alpha if ALL automatable checks pass. Economic
    sensibility and full walk-forward remain human-judged (reported as flags).
    """
    ex = _executed(ledger)
    checks: dict[str, object] = {}
    if ex.empty:
        return {"claim_alpha": False, "reason": "no executed trades"}

    net = pd.to_numeric(ex["net_return"], errors="coerce").dropna()
    harsh = pd.to_numeric(ex["harsh_net_return"], errors="coerce").dropna()
    pnl = pd.to_numeric(ex["pnl_net"], errors="coerce")

    checks["survives_costs"] = bool(net.mean() > 0)
    checks["survives_harsh_costs"] = bool(harsh.mean() > 0)
    checks["enough_events"] = bool(len(net) >= min_trades)
    checks["multi_event"] = bool(ex["event_id"].nunique() >= 2)

    pos = pnl[pnl > 0].sum()
    top = pnl.max()
    checks["no_single_trade_dominates"] = bool(pos <= 0 or top / pos <= single_trade_max_share)

    checks["liquidity_ok"] = bool(ex["capped"].fillna(False).astype(bool).mean() < 0.5)

    lo, hi = bootstrap_ci(net)
    checks["bootstrap_ci_positive"] = bool(lo is not np.nan and lo > 0)
    checks["bootstrap_ci"] = (lo, hi)

    # walk-forward: first 70% vs last 30% by exit_date
    ordered = ex.sort_values("exit_date")
    k = int(len(ordered) * 0.7)
    if k >= 2 and len(ordered) - k >= 2:
        oos = pd.to_numeric(ordered.iloc[k:]["net_return"], errors="coerce")
        checks["oos_positive"] = bool(oos.mean() > 0)
    else:
        checks["oos_positive"] = None  # insufficient sample

    automatable = [
        "survives_costs", "survives_harsh_costs", "enough_events", "multi_event",
        "no_single_trade_dominates", "liquidity_ok", "bootstrap_ci_positive",
    ]
    claim = all(bool(checks[k]) for k in automatable)
    if checks["oos_positive"] is False:
        claim = False
    checks["claim_alpha"] = claim
    checks["note"] = (
        "Economic sensibility and full walk-forward remain human-judged. "
        "'claim_alpha' requires sufficient real events; sparse data => False."
    )
    return checks
