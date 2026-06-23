"""Did the forced-buy-before-effective logic work for THEMATIC ETF inclusions?

Unlike the large-cap indices, thematic ETFs (GDXJ etc.) have NO holdings history,
so the only real dated events are public inclusion press releases. This studies a
hand-collected, SOURCED set of real GDXJ (MVIS Junior Gold Miners) inclusions —
each on the published review schedule (3rd Friday of Mar/Sep, effective that
close) — and measures the price path around the effective date, ABNORMAL vs gold
(GLD) to strip the sector move.

HONEST LIMITS: small (n~10) and clustered in the Mar-2026 review, so this is a
CASE STUDY, not a statistically robust backtest. The rigorous historical test of
the same mechanism is the 1,514-event large-cap inclusion study (Finding 2/3).
Tickers use each name's primary listing (mostly NYSE/Nasdaq/TSX; ASX where listed).
"""
from __future__ import annotations
import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import pandas as pd

from index_flow.config import load_config
from index_flow.price_data import PriceStore
from index_flow.rebalance_calendar import third_friday
from index_flow.utils import get_logger, write_csv

log = get_logger("thematic")

# (ticker, company, GDXJ review year, review month, source) — all real, sourced.
EVENTS = [
    ("PPTA", "Perpetua Resources", 2025, 3, "prnewswire/perpetua GDXJ Mar-2025"),
    ("SPR.AX", "Spartan Resources", 2025, 3, "stockhead/Spartan GDXJ+AS51 Mar-2025"),
    ("ASM", "Avino Silver & Gold", 2025, 9, "juniorminingnetwork Avino GDXJ Sep-2025"),
    ("VOXR", "Vox Royalty", 2026, 3, "stocktitan/JMN Vox GDXJ Mar-2026"),
    ("ODV", "Osisko Development", 2026, 3, "stocktitan Osisko GDXJ Mar-2026"),
    ("ITRG", "Integra Resources", 2026, 3, "prnewswire Integra GDXJ Mar-2026"),
    ("USAS", "Americas Gold & Silver", 2026, 3, "globeandmail USAS GDXJ Mar-2026"),
    ("USAU", "US Gold Corp", 2026, 3, "cruxinvestor US Gold GDXJ Mar-2026"),
    ("SX2.AX", "Southern Cross Gold", 2026, 3, "JMN Southern Cross GDXJ Mar-2026"),
    ("SNWGF", "Snowline Gold", 2026, 3, "globenewswire Snowline GDXJ Mar-2026"),
]
BENCH = "GLD"   # gold ETF — strip the sector move


def _pos(px, d):
    idx = np.where((px["date"] <= pd.Timestamp(d)).values)[0]
    return int(idx[-1]) if len(idx) else None


def _ret(px, a, b):
    if a is None or b is None or a < 0 or b >= len(px):
        return None
    return float(px["close"].iloc[b]) / float(px["close"].iloc[a]) - 1.0


def _bench_ret(bench, d0, d1):
    p0 = _pos(bench, d0); p1 = _pos(bench, d1)
    if p0 is None or p1 is None:
        return 0.0
    return float(bench["close"].iloc[p1]) / float(bench["close"].iloc[p0]) - 1.0


def main() -> int:
    cfg = load_config(); cfg.ensure_dirs()
    store = PriceStore(cfg)
    gld = store.get(BENCH, start="2024-06-01")

    rows = []
    for tk, name, y, m, src in EVENTS:
        eff = third_friday(y, m)              # GDXJ review = 3rd Friday; effective that close
        px = store.get(tk, start="2024-06-01")
        if px.empty or _pos(px, eff) is None:
            log.info("skip %s (%s): no FMP prices", tk, name); continue
        e = _pos(px, eff)
        if e - 16 < 0 or e + 10 >= len(px):
            log.info("skip %s: insufficient window", tk); continue
        d = px["date"]
        # windows (trading-day offsets around effective day e)
        pre   = _ret(px, e - 15, e - 5)       # before announcement
        front = _ret(px, e - 5, e)            # announcement -> effective (the front-run)
        p5    = _ret(px, e, e + 5)            # effective -> +5
        p10   = _ret(px, e, e + 10)           # effective -> +10
        # abnormal vs GLD over the same calendar dates
        ab_front = front - _bench_ret(gld, d.iloc[e - 5], d.iloc[e])
        ab_p10   = (p10 if p10 is not None else np.nan)
        if p10 is not None:
            ab_p10 = p10 - _bench_ret(gld, d.iloc[e], d.iloc[e + 10])
        rows.append({"ticker": tk, "company": name, "review": f"{y}-{m:02d}",
                     "effective": eff, "is_asx": tk.endswith(".AX"),
                     "pre_ann_%": round(100 * pre, 1) if pre is not None else None,
                     "frontrun_T-5_to_eff_%": round(100 * front, 1) if front is not None else None,
                     "abn_frontrun_%": round(100 * ab_front, 1) if not pd.isna(ab_front) else None,
                     "eff_to_+5_%": round(100 * p5, 1) if p5 is not None else None,
                     "abn_eff_to_+10_%": round(100 * ab_p10, 1) if not pd.isna(ab_p10) else None,
                     "source": src})
    if not rows:
        print("No events resolved with FMP prices."); return 0
    df = pd.DataFrame(rows)
    write_csv(df, cfg.path("tables") / "thematic_inclusion_study.csv")

    pd.set_option("display.width", 200)
    print(f"\nGDXJ junior-gold inclusions — real, sourced (n={len(df)}; "
          f"{df['is_asx'].sum()} ASX). Abnormal = vs GLD.\n")
    print(df[["ticker", "company", "review", "frontrun_T-5_to_eff_%", "abn_frontrun_%",
              "eff_to_+5_%", "abn_eff_to_+10_%"]].to_string(index=False))

    def avg(c):
        v = pd.to_numeric(df[c], errors="coerce").dropna()
        return round(v.mean(), 2), round(v.median(), 2), int((v > 0).sum()), len(v)

    print("\nAVERAGES (mean / median / #pos / n):")
    for c in ["pre_ann_%", "frontrun_T-5_to_eff_%", "abn_frontrun_%", "eff_to_+5_%", "abn_eff_to_+10_%"]:
        mn, md, pos, n = avg(c)
        print(f"  {c:24s} mean {mn:>6}%  median {md:>6}%  pos {pos}/{n}")
    print("\nCAVEAT: small + clustered in Mar-2026 -> case study, not a robust backtest.")
    print("Rigorous version = the 1,514-event large-cap inclusion study (Finding 2/3).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
