"""Configuration loading.

Resolves the repo root, loads the YAML configs in ``configs/`` and the ``.env``
file, and exposes a single :class:`Config` object with attribute + dict access
and path helpers. Everything in the engine takes a ``Config`` so there is one
source of truth and tests can inject a temp config.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

try:  # optional: .env is convenient but not required (env vars also work)
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - dotenv is a declared dep, this is defensive
    def load_dotenv(*_args, **_kwargs):  # type: ignore
        return False


def find_repo_root(start: Path | None = None) -> Path:
    """Walk upward from ``start`` until a repo marker is found."""
    start = (start or Path(__file__)).resolve()
    for parent in [start, *start.parents]:
        if (parent / "pyproject.toml").exists() or (parent / "configs").is_dir():
            return parent
    # Fallback: two levels up from src/index_flow/config.py
    return Path(__file__).resolve().parents[2]


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@dataclass
class Config:
    """Resolved configuration. Attribute access for the four YAML namespaces,
    plus environment-derived secrets and convenient path resolution."""

    root: Path
    config: dict[str, Any] = field(default_factory=dict)
    providers: dict[str, Any] = field(default_factory=dict)
    strategy_params: dict[str, Any] = field(default_factory=dict)
    watchlists: dict[str, Any] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)

    # ----- path helpers -----------------------------------------------------
    def path(self, key: str) -> Path:
        """Resolve a path declared under ``config.paths`` (e.g. 'data_processed')."""
        rel = self.config.get("paths", {}).get(key)
        if rel is None:
            raise KeyError(f"Unknown path key '{key}' in config.paths")
        return self.resolve(rel)

    def resolve(self, rel: str | Path) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else (self.root / p)

    def ensure_dirs(self) -> None:
        """Create every directory declared in ``config.paths`` (idempotent)."""
        for key in self.config.get("paths", {}):
            self.path(key).mkdir(parents=True, exist_ok=True)

    # ----- secrets ----------------------------------------------------------
    @property
    def fmp_api_key(self) -> str | None:
        return self.env.get("FMP_API_KEY") or os.environ.get("FMP_API_KEY") or None

    @property
    def fmp_base_url(self) -> str:
        return (
            self.env.get("FMP_BASE_URL")
            or os.environ.get("FMP_BASE_URL")
            or "https://financialmodelingprep.com/api"
        )

    @property
    def web_user_agent(self) -> str:
        return (
            self.env.get("WEB_USER_AGENT")
            or os.environ.get("WEB_USER_AGENT")
            or "asx-index-flow-alpha-lab/0.1 (research)"
        )

    @property
    def web_rate_limit_rpm(self) -> int:
        raw = self.env.get("WEB_RATE_LIMIT_RPM") or os.environ.get("WEB_RATE_LIMIT_RPM")
        try:
            return int(raw) if raw else 20
        except ValueError:
            return 20

    # ----- generic getters --------------------------------------------------
    def get(self, *keys: str, default: Any = None) -> Any:
        """Nested lookup into the main config, e.g. cfg.get('costs', 'brokerage_bps')."""
        node: Any = self.config
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node


def load_config(root: Path | str | None = None, load_env: bool = True) -> Config:
    """Load all configs from ``configs/`` and (optionally) ``.env``."""
    root = Path(root).resolve() if root else find_repo_root()
    cfg_dir = root / "configs"

    if load_env:
        load_dotenv(root / ".env")

    env_keys = (
        "FMP_API_KEY",
        "FMP_BASE_URL",
        "WEB_USER_AGENT",
        "WEB_RATE_LIMIT_RPM",
    )
    env = {k: os.environ[k] for k in env_keys if os.environ.get(k)}

    cfg = Config(
        root=root,
        config=_read_yaml(cfg_dir / "config.yaml"),
        providers=_read_yaml(cfg_dir / "providers.yaml"),
        strategy_params=_read_yaml(cfg_dir / "strategy_params.yaml"),
        watchlists=_read_yaml(cfg_dir / "watchlists.yaml"),
        env=env,
    )
    return cfg
