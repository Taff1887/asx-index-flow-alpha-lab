"""Render the result CSVs in reports/tables/ into PNGs for reports/README.md.

Pure read-from-disk -> matplotlib. Reproducible: re-run after any backtest to
refresh the committed figures. Light background so they render on GitHub.
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from index_flow.config import load_config

GREEN, GRAY, RED, BLUE = "#639922", "#888780", "#E24B4A", "#378ADD"
plt.rcParams.update({"figure.dpi": 120, "font.size": 10, "axes.spines.top": False,
                     "axes.spines.right": False, "axes.grid": True, "grid.alpha": 0.25})


def main() -> int:
    cfg = load_config()
    t = cfg.path("tables")
    figs = cfg.path("figures"); figs.mkdir(parents=True, exist_ok=True)

    summ = pd.read_csv(t / "inclusion_strategy_summary.csv")
    by_idx = pd.read_csv(t / "inclusion_runup_by_index.csv")
    by_yr = pd.read_csv(t / "inclusion_runup_by_year.csv")
    # prefer the universal map (all ETFs x all ASX stocks); fall back to the old one
    if (t / "universal_ownership_map.csv").exists():
        own = pd.read_csv(t / "universal_ownership_map.csv").rename(
            columns={"etf_pct_float": "etf_pct_of_shares_out"})
    else:
        own = pd.read_csv(t / "forced_ownership_map.csv")

    # 1) decay by entry lag
    lag = {r["strategy"]: r["avg_net_%"] for _, r in summ.iterrows()}
    vals = [lag.get("S1_ADD_runup_T-5"), lag.get("S1_ADD_runup_T-3"), lag.get("S1_ADD_runup_T-1")]
    fig, ax = plt.subplots(figsize=(6.4, 3.0))
    bars = ax.bar(["Enter T-5", "Enter T-3", "Enter T-1"], vals, color=[GREEN, GRAY, RED])
    ax.axhline(0, color="black", lw=0.8)
    ax.set_ylabel("avg net return per event (%)")
    ax.set_title("Index-inclusion run-up decays within ~2 days (t=3.1 at T-5)")
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + (0.05 if v >= 0 else -0.12),
                f"{v:+.2f}%", ha="center", fontsize=9)
    fig.tight_layout(); fig.savefig(figs / "fig_inclusion_decay.png"); plt.close(fig)

    # 2) by index
    fig, ax = plt.subplots(figsize=(6.4, 3.0))
    cols = [GREEN if x != "S&P/ASX 200" else GRAY for x in by_idx["index"]]
    bars = ax.bar(by_idx["index"], by_idx["avg_net_%"], color=cols)
    ax.axhline(0, color="black", lw=0.8)
    ax.set_ylabel("avg net return per event (%)")
    ax.set_title("By index: scales with passive AUM; faint on the ASX 200")
    for b, v, n in zip(bars, by_idx["avg_net_%"], by_idx["n"]):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.05, f"{v:+.2f}%\nn={int(n)}", ha="center", fontsize=8)
    fig.tight_layout(); fig.savefig(figs / "fig_inclusion_by_index.png"); plt.close(fig)

    # 3) by year
    fig, ax = plt.subplots(figsize=(7.2, 3.2))
    cols = [GREEN if v >= 0 else RED for v in by_yr["avg_net_%"]]
    ax.bar(by_yr["year"].astype(str), by_yr["avg_net_%"], color=cols)
    ax.axhline(0, color="black", lw=0.8)
    ax.set_ylabel("avg net return (%)")
    ax.set_title("By year: positive in 15 of 22 years (S&P500+Nasdaq+Dow adds)")
    ax.tick_params(axis="x", rotation=60, labelsize=8)
    fig.tight_layout(); fig.savefig(figs / "fig_inclusion_by_year.png"); plt.close(fig)

    # 4) forced-ownership overhang (top 10 by % of shares out)
    own = own.dropna(subset=["etf_pct_of_shares_out"]).copy()
    own["pct"] = own["etf_pct_of_shares_out"] * 100
    top = own.sort_values("pct", ascending=False).head(10).iloc[::-1]
    labels = [f"{r.asx_ticker}  {str(r.company)[:18]}" for r in top.itertuples()]
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    ax.barh(labels, top["pct"], color=BLUE)
    ax.set_xlabel("% of shares outstanding held by ETFs")
    ax.set_title("Forced-ownership overhang — all 274 ETFs x all ASX stocks (top 10)")
    for i, v in enumerate(top["pct"]):
        ax.text(v + 0.4, i, f"{v:.1f}%", va="center", fontsize=8)
    fig.tight_layout(); fig.savefig(figs / "fig_forced_ownership.png"); plt.close(fig)

    # 5) liquidity gradient of the run-up (does thinness amplify it?)
    liq_p = t / "inclusion_runup_by_liquidity.csv"
    if liq_p.exists():
        liq = pd.read_csv(liq_p)
        fig, ax = plt.subplots(figsize=(6.4, 3.0))
        cols = [GREEN if v >= 0 else RED for v in liq["avg_net_%"]]
        ax.bar(liq["liq_bucket"].astype(str), liq["avg_net_%"], color=cols)
        ax.axhline(0, color="black", lw=0.8)
        ax.set_ylabel("avg net run-up (%)")
        ax.set_title("Run-up by name liquidity (US adds): thinness does NOT amplify it")
        for i, (v, n) in enumerate(zip(liq["avg_net_%"], liq["n"])):
            ax.text(i, v + 0.04, f"{v:+.2f}%\nn={int(n)}", ha="center", fontsize=8)
        fig.tight_layout(); fig.savefig(figs / "fig_inclusion_by_liquidity.png"); plt.close(fig)

    # 6) deletion-rebound distribution (buy the forced-selling washout)
    led_p = t / "inclusion_ledger.csv"
    if led_p.exists():
        led = pd.read_csv(led_p)
        s4 = pd.to_numeric(led[led["strategy"] == "S4_DEL_long_eff_+10"]["net_return"],
                           errors="coerce").dropna() * 100
        if len(s4):
            s4c = s4.clip(-30, 50)
            fig, ax = plt.subplots(figsize=(6.4, 3.0))
            ax.hist(s4c, bins=40, color=BLUE, alpha=0.85)
            ax.axvline(0, color="black", lw=0.8)
            ax.axvline(s4.mean(), color=GREEN, lw=1.5, label=f"mean {s4.mean():+.1f}%")
            ax.axvline(s4.median(), color="#854F0B", lw=1.5, ls="--", label=f"median {s4.median():+.1f}%")
            ax.set_xlabel("net return, buy deletion at eff close, hold +10 days (%)")
            ax.set_ylabel("count"); ax.legend(fontsize=9)
            ax.set_title(f"Deletion rebound (n={len(s4)}): positive, fat right tail")
            fig.tight_layout(); fig.savefig(figs / "fig_deletion_rebound.png"); plt.close(fig)

    print("Wrote figures to", figs)
    for p in sorted(figs.glob("fig_*.png")):
        print(" ", p.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
