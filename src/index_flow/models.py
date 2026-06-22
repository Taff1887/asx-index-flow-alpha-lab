"""ML models for the four prediction tasks.

1. Which ASX names get added to niche/global/thematic indices
   -> :func:`build_candidate_dataset` (needs a universe + membership history to
      sample negatives; framework provided, flagged where manual data is required).
2. Which announced adds underreact immediately   -> classifier on label_underreaction.
3. Which adds have delayed positive drift          -> classifier on label_delayed_positive.
4. Which providers/themes create inefficiency      -> aggregate of model outputs
   + the inefficiency rankings in reporting.py.

Models are deliberately simple, regularised, and time-aware (no shuffling across
the event timeline). With sparse data they refuse to "find" signal: training
returns ``None`` and logs why, rather than overfitting noise.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Config
from .utils import get_logger

log = get_logger("index_flow.models")

try:
    from sklearn.compose import ColumnTransformer
    from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor
    from sklearn.impute import SimpleImputer
    from sklearn.model_selection import TimeSeriesSplit, cross_val_score
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler
    _SKLEARN = True
except Exception:  # pragma: no cover
    _SKLEARN = False

_CATEGORICAL = ["sector", "industry", "theme"]
_MIN_ROWS = 30  # below this we don't pretend to train


def _split_features(features: pd.DataFrame) -> tuple[list[str], list[str]]:
    cats = [c for c in _CATEGORICAL if c in features.columns]
    nums = [
        c for c in features.columns
        if c not in cats and c != "event_id" and pd.api.types.is_numeric_dtype(features[c])
    ]
    return nums, cats


def _make_pipeline(nums: list[str], cats: list[str], task: str):
    num_pipe = Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())])
    cat_pipe = Pipeline(
        [("impute", SimpleImputer(strategy="constant", fill_value="NA")),
         ("oh", OneHotEncoder(handle_unknown="ignore"))]
    )
    pre = ColumnTransformer([("num", num_pipe, nums), ("cat", cat_pipe, cats)])
    est = GradientBoostingRegressor(random_state=7) if task == "reg" else GradientBoostingClassifier(random_state=7)
    return Pipeline([("pre", pre), ("est", est)])


def prepare_xy(features: pd.DataFrame, labels: pd.DataFrame, target: str):
    df = features.merge(labels[["event_id", target]], on="event_id", how="inner")
    df = df[df[target].notna()].reset_index(drop=True)
    y = df[target]
    X = df.drop(columns=[c for c in [target] if c in df.columns])
    return X, y


def train_classifier(cfg: Config, features: pd.DataFrame, labels: pd.DataFrame,
                     target: str = "label_delayed_positive") -> dict:
    """Train a time-aware classifier for a binary label. Returns model + CV AUC,
    or a {'trained': False, 'reason': ...} dict when data is insufficient."""
    if not _SKLEARN:
        return {"trained": False, "reason": "scikit-learn not installed"}
    X, y = prepare_xy(features, labels, target)
    if len(X) < _MIN_ROWS:
        return {"trained": False, "reason": f"only {len(X)} labelled rows (< {_MIN_ROWS})"}
    if y.nunique() < 2:
        return {"trained": False, "reason": "label has a single class"}
    nums, cats = _split_features(X)
    pipe = _make_pipeline(nums, cats, task="clf")
    try:
        scores = cross_val_score(pipe, X, y, cv=TimeSeriesSplit(n_splits=4), scoring="roc_auc")
        pipe.fit(X, y)
    except Exception as exc:  # noqa: BLE001
        return {"trained": False, "reason": f"fit failed: {exc}"}
    log.info("Trained %s: CV AUC %.3f ± %.3f (n=%d)", target, scores.mean(), scores.std(), len(X))
    return {"trained": True, "model": pipe, "cv_auc_mean": float(scores.mean()),
            "cv_auc_std": float(scores.std()), "n": int(len(X)), "features": nums + cats}


def train_regressor(cfg: Config, features: pd.DataFrame, labels: pd.DataFrame,
                    target: str = "label_delayed_return") -> dict:
    if not _SKLEARN:
        return {"trained": False, "reason": "scikit-learn not installed"}
    X, y = prepare_xy(features, labels, target)
    if len(X) < _MIN_ROWS:
        return {"trained": False, "reason": f"only {len(X)} labelled rows (< {_MIN_ROWS})"}
    nums, cats = _split_features(X)
    pipe = _make_pipeline(nums, cats, task="reg")
    try:
        scores = cross_val_score(pipe, X, y, cv=TimeSeriesSplit(n_splits=4),
                                 scoring="neg_mean_absolute_error")
        pipe.fit(X, y)
    except Exception as exc:  # noqa: BLE001
        return {"trained": False, "reason": f"fit failed: {exc}"}
    log.info("Trained regressor %s: CV MAE %.4f (n=%d)", target, -scores.mean(), len(X))
    return {"trained": True, "model": pipe, "cv_mae": float(-scores.mean()),
            "n": int(len(X)), "features": nums + cats}


def build_candidate_dataset(
    cfg: Config,
    add_events: pd.DataFrame,
    universe_features: pd.DataFrame | None = None,
) -> dict:
    """Frame the 'which ASX names get added' task.

    Positives = ASX names that *were* added (from add_events). Negatives = the
    rest of the eligible universe at each review (NOT added). Building honest
    negatives needs the eligible universe + membership history, which is licensed
    / manual; without ``universe_features`` we return positives only and flag the
    gap rather than fabricating negatives.
    """
    positives = add_events[add_events["event_type"].isin(
        {"OFFICIAL_INDEX_ADD", "ETF_HOLDINGS_NEW_POSITION"}
    )].copy()
    positives["is_added"] = 1
    if universe_features is None or universe_features.empty:
        return {
            "ready": False,
            "reason": (
                "Need eligible-universe features + membership history to sample "
                "negatives. Provide via data/manual/ (universe snapshots) to enable "
                "the candidate-prediction model."
            ),
            "positives": positives,
        }
    neg = universe_features.copy()
    neg["is_added"] = 0
    data = pd.concat([positives, neg], ignore_index=True)
    return {"ready": True, "data": data}
