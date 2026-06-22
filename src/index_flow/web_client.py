"""Polite web client for issuer/provider pages and holdings files.

Principles
----------
* **Caches** every fetch under ``data/raw/web/`` so re-runs don't re-hit sites.
* **Throttles** to a configurable requests-per-minute.
* **Respects robots.txt** when ``config.web.respect_robots`` is true.
* Does **not** attempt to defeat bot-protection, paywalls, or licences. If a
  page is blocked it raises :class:`Blocked` and the caller falls back to the
  manual ingestion path — we never fabricate the missing data.
"""

from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

from .config import Config
from .utils import ensure_dir, get_logger, stable_hash

log = get_logger("index_flow.web")


class Blocked(RuntimeError):
    """Raised when a fetch is disallowed (robots) or refused (403/429/anti-bot)."""


class WebClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        web_cfg = cfg.config.get("web", {})
        self.cache_dir = ensure_dir(cfg.resolve(web_cfg.get("cache_dir", "data/raw/web")))
        self.respect_robots = bool(web_cfg.get("respect_robots", True))
        self.timeout = int(web_cfg.get("timeout_seconds", 30))
        self.min_interval = 60.0 / max(1, cfg.web_rate_limit_rpm)
        self._last_call = 0.0
        self._robots: dict[str, RobotFileParser] = {}
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": cfg.web_user_agent})

    # --------------------------------------------------------------- robots
    def _allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        rp = self._robots.get(base)
        if rp is None:
            rp = RobotFileParser()
            rp.set_url(f"{base}/robots.txt")
            try:
                rp.read()
            except Exception:  # if robots is unreachable, default to cautious-allow
                rp = None  # type: ignore
            self._robots[base] = rp  # type: ignore
        if rp is None:
            return True
        return rp.can_fetch(self.cfg.web_user_agent, url)

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call = time.time()

    def _cache_path(self, url: str, suffix: str = ".bin") -> Path:
        host = urlparse(url).netloc.replace(":", "_")
        return self.cache_dir / host / f"{stable_hash(url)}{suffix}"

    # ----------------------------------------------------------------- fetch
    def get(self, url: str, force: bool = False, binary: bool = False) -> str | bytes:
        """Fetch ``url`` (cached). Raises :class:`Blocked` if not permitted."""
        suffix = ".bin" if binary else ".txt"
        cache_path = self._cache_path(url, suffix)
        if cache_path.exists() and not force:
            return cache_path.read_bytes() if binary else cache_path.read_text("utf-8", "ignore")

        if not self._allowed(url):
            raise Blocked(f"robots.txt disallows fetching {url}")

        self._throttle()
        try:
            resp = self._session.get(url, timeout=self.timeout)
        except requests.RequestException as exc:
            raise Blocked(f"request error for {url}: {exc}") from exc

        if resp.status_code in (401, 402, 403, 429, 451):
            raise Blocked(f"HTTP {resp.status_code} for {url} (gated/blocked) — use manual ingestion")
        resp.raise_for_status()

        ensure_dir(cache_path.parent)
        if binary:
            cache_path.write_bytes(resp.content)
            return resp.content
        cache_path.write_text(resp.text, "utf-8")
        return resp.text

    def download(self, url: str, dest: str | Path, force: bool = False) -> Path:
        """Download a binary file (CSV/XLSX/PDF) to ``dest``."""
        dest = Path(dest)
        if dest.exists() and not force:
            return dest
        content = self.get(url, force=force, binary=True)
        ensure_dir(dest.parent)
        dest.write_bytes(content)  # type: ignore[arg-type]
        log.info("Downloaded %s -> %s", url, dest)
        return dest
