"""Price path BEFORE and AFTER an index inclusion / removal (event study).

Classic CAAR (cumulative average abnormal return) study around the effective
date (day 0) for ADDITIONS and REMOVALS separately, on real dated membership
changes (S&P 500 + Nasdaq-100 + Dow). Abnormal = stock daily return minus SPY
(strip the market). The S&P/Nasdaq announcement lands ~5 trading days before the
effective date, so the run-up over days -5..0 is the post-announcement window.

Why global indices and not ASX ETFs: ASX-ETF holdings have NO history in any
feed, so the inclusion/removal *dates* don't exist to study. The mechanism is
identical; this measures it where the history is real. (The ASX-specific version
runs forward off daily snapshots — see scripts/detect_etf_accumulation.py.)
"""
from __future__ import annotations
import sys
from datetime import date, datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from index_flow.config import load_config
from index_flow.fmp_client import FMPClient
from index_flow.price_data import PriceStore
from index_flow.utils import get_logger, write_csv

log = get_logger("event_path")
CUTOFF = date(2012, 1, 1)
PRE, POST = 20, 20            # trading days each side of the effective date
US = {"S&P 500": "historical-sp500-constituent",
      "Nasdaq-100": "historical-nasdaq-constituent",
      "Dow Jones": "historical-dowjones-constituent"}


def _d(s):
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def collect(fmp):
    adds, dels = [], []
    for ep in US.values():
        data = fmp.get_json(ep)
        if not isinstance(data, list):
            continue
        for r in data:
            eff = _d(r.get("date"))
            if not eff or eff < CUTOFF:
                continue
            a = str(r.get("symbol", "")).upper()
            d = str(r.get("removedTicker", "") or "").upper()
            if a:
                adds.append((a, eff))
            if d:
                dels.append((d, eff))
    return adds, dels


def caar(events, store, spy):
    """Cumulative average abnormal return path over [-PRE, +POST]."""
    K = PRE + POST + 1
    acc = np.zeros(K); cnt = np.zeros(K)
    for sym, eff in events:
        px = store.get(sym, start="2010-01-01")
        if px.empty:
            continue
        idx = np.where((px["date"] <= pd.Timestamp(eff)).values)[0]
        if len(idx) == 0:
            continue
        e = int(idx[-1])
        if e - PRE - 1 < 0 or e + POST >= len(px):
            continue
        sret = px["close"].values[e - PRE: e + POST + 1] / px["close"].values[e - PRE - 1: e + POST] - 1.0
        # align SPY by date
        dts = px["date"].values[e - PRE: e + POST + 1]
        sp = spy.set_index("date")["close"].reindex(pd.to_datetime(dts)).ffill().values
        spret = sp[1:] / sp[:-1] - 1.0
        spret = np.concatenate([[np.nan], spret])  # first day no prior -> nan, drop below
        ab = sret - np.nan_to_num(spret)
        acc += np.nan_to_num(ab); cnt += ~np.isnan(ab)
    mean_daily = np.where(cnt > 0, acc / np.maximum(cnt, 1), 0.0)
    return np.cumsum(mean_daily) * 100.0, int(cnt.max())


def main() -> int:
    cfg = load_config(); cfg.ensure_dirs()
    fmp = FMPClient(cfg); store = PriceStore(cfg)
    spy = store.get("SPY", start="2010-01-01")
    adds, dels = collect(fmp)
    log.info("events: %d adds, %d dels", len(adds), len(dels))

    add_path, n_add = caar(adds, store, spy)
    del_path, n_del = caar(dels, store, spy)
    xs = list(range(-PRE, POST + 1))

    out = pd.DataFrame({"day_rel_effective": xs,
                        "ADD_caar_%": np.round(add_path, 3),
                        "DEL_caar_%": np.round(del_path, 3)})
    write_csv(out, cfg.path("tables") / "event_path_caar.csv")

    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    ax.plot(xs, add_path, color="#639922", lw=2, label=f"ADDITIONS (n≈{n_add})")
    ax.plot(xs, del_path, color="#E24B4A", lw=2, label=f"REMOVALS (n≈{n_del})")
    ax.axvline(0, color="black", lw=1, ls="--")
    ax.axvline(-5, color="#888", lw=0.8, ls=":")
    ax.text(-5, ax.get_ylim()[0], " ~announcement", fontsize=8, color="#555", va="bottom")
    ax.text(0, ax.get_ylim()[0], " effective", fontsize=8, color="#222", va="bottom")
    ax.axhline(0, color="black", lw=0.6)
    ax.set_xlabel("trading days relative to effective date")
    ax.set_ylabel("cumulative abnormal return vs SPY (%)")
    ax.set_title("Price before & after index inclusion vs removal (US, since 2012)")
    ax.legend(); ax.grid(alpha=0.25)
    fig.tight_layout(); fig.savefig(cfg.path("figures") / "fig_event_path.png"); plt.close(fig)

    print(out.to_string(index=False))
    print(f"\nADD path: {add_path[PRE-5]:.2f}% at -5  ->  {add_path[PRE]:.2f}% at effective  "
          f"->  {add_path[-1]:.2f}% at +20")
    print(f"DEL path: {del_path[PRE-5]:.2f}% at -5  ->  {del_path[PRE]:.2f}% at effective  "
          f"->  {del_path[-1]:.2f}% at +20")
    print("\nWritten: reports/tables/event_path_caar.csv, reports/figures/fig_event_path.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
