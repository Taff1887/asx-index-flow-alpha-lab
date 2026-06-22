"""make fetch-prices — pull ASX OHLCV / market-cap data from FMP into the cache.

Universe = every ASX ticker referenced by the watchlists plus any ticker that
appears in the current events / holdings diffs. Without FMP_API_KEY this serves
whatever is already cached and reports what is missing.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from index_flow.asx_universe import normalise_asx
from index_flow.config import load_config
from index_flow.price_data import PriceStore


def _universe(cfg) -> list[str]:
    syms: set[str] = set()
    for spec in cfg.watchlists.get("themes", {}).values():
        for t in spec.get("watch_asx", []) or []:
            n = normalise_asx(t)
            if n:
                syms.add(n)
    # also any tickers already in events / diffs
    try:
        from index_flow.event_builder import build_events
        ev = build_events(cfg)
        for t in ev.get("asx_ticker", []):
            n = normalise_asx(t)
            if n:
                syms.add(n)
    except Exception:
        pass
    return sorted(syms)


def main() -> int:
    cfg = load_config()
    cfg.ensure_dirs()
    if not cfg.fmp_api_key:
        print("WARNING: FMP_API_KEY not set — will only serve cached data.")
    store = PriceStore(cfg)
    syms = _universe(cfg)
    print(f"Fetching prices for {len(syms)} ASX symbols...")
    got, missing = 0, []
    for s in syms:
        df = store.get(s, refresh=bool(cfg.fmp_api_key))
        if df.empty:
            missing.append(s)
        else:
            got += 1
    print(f"  prices available: {got}")
    print(f"  missing:          {len(missing)}")
    if missing:
        print("  " + ", ".join(missing[:40]) + (" ..." if len(missing) > 40 else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
