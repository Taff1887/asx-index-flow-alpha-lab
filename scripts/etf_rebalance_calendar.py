"""ETF -> index -> rebalance schedule -> NEXT dates (forecast the forced buying).

For the ETFs that hold thin ASX names, this maps each to the index it tracks and
that index's published rebalance/reconstitution cadence (researched, sourced),
then computes the NEXT effective date and the window to watch for the pro-forma
announcement. Trade idea: when the provider publishes the pro-forma changes (a few
days before the effective date) and a newly-added thin name hasn't moved, you can
position before the ETF's forced buying on the effective date.

Schedules are PUBLIC facts; each row carries a source + confidence. Exact
pro-forma announcement timing varies — `watch_from` is effective − lead (typical).
"""
from __future__ import annotations
import sys
from datetime import date, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd

from index_flow.config import load_config
from index_flow.fmp_client import FMPClient
from index_flow.rebalance_calendar import third_friday
from index_flow.utils import get_logger, write_csv

log = get_logger("etf_rebal")

# Curated, sourced schedule registry for the ASX-thin-name-holding ETFs.
# rule: how the effective date is set; months: reconstitution/review months.
SCHEDULES = [
    {"etf": "URNJ", "index": "Nasdaq Sprott Junior Uranium Miners", "provider": "Nasdaq/Sprott",
     "freq": "semi-annual", "months": [6, 12], "rule": "3rd_friday", "lead": 6,
     "eligibility": "uranium classification; free-float mcap US$30m-$3bn ($25m/$5bn existing); seasoned >=3m",
     "source": "indexes.nasdaqomx.com/docs/methodology_NSURNJ.pdf", "confidence": "high"},
    {"etf": "URNM", "index": "North Shore Global Uranium Mining", "provider": "North Shore/Sprott",
     "freq": "semi-annual recon (Jun/Dec) + qtrly rebal (Mar/Sep)", "months": [3, 6, 9, 12],
     "rule": "3rd_friday", "lead": 6,
     "eligibility": "uranium miners; big recon in Jun & Dec (since Dec 2025), interim rebal Mar/Sep",
     "source": "sprottetfs.com/changes-to-index-tracked-by-sprott-uranium-miners-etf-urnm", "confidence": "high"},
    {"etf": "URA", "index": "Solactive Global Uranium & Nuclear Components", "provider": "Solactive",
     "freq": "semi-annual", "months": [2, 8], "rule": "month_start", "lead": 7,
     "eligibility": "uranium & nuclear components; effective ~Feb 1 / Aug 1",
     "source": "solactive.com/index/DE000SLA4825", "confidence": "med"},
    {"etf": "NLR", "index": "MVIS Global Uranium & Nuclear Energy", "provider": "MarketVector",
     "freq": "quarterly", "months": [3, 6, 9, 12], "rule": "3rd_friday", "lead": 5,
     "eligibility": "uranium + nuclear utilities", "source": "marketvector.com", "confidence": "med"},
    {"etf": "GDXJ", "index": "MVIS Global Junior Gold Miners", "provider": "MarketVector",
     "freq": "quarterly review (semi-annual recon)", "months": [3, 6, 9, 12], "rule": "3rd_friday", "lead": 5,
     "eligibility": "small-cap gold/silver miners; effective Monday after 3rd Friday",
     "source": "marketvector.com/indexes/hard-asset/mvis-global-junior-gold-miners", "confidence": "high"},
    {"etf": "GDX", "index": "NYSE Arca Gold Miners", "provider": "ICE/NYSE", "freq": "quarterly",
     "months": [3, 6, 9, 12], "rule": "3rd_friday", "lead": 5,
     "eligibility": "gold miners", "source": "vaneck.com", "confidence": "med"},
    {"etf": "REMX", "index": "MVIS Global Rare Earth/Strategic Metals", "provider": "MarketVector",
     "freq": "quarterly", "months": [3, 6, 9, 12], "rule": "3rd_friday", "lead": 5,
     "eligibility": "rare earth / strategic metals", "source": "marketvector.com", "confidence": "med"},
    {"etf": "LIT", "index": "Solactive Global Lithium", "provider": "Solactive", "freq": "semi-annual",
     "months": [2, 8], "rule": "month_start", "lead": 7,
     "eligibility": "lithium mining & battery", "source": "solactive.com", "confidence": "low"},
    {"etf": "COPX", "index": "Solactive Global Copper Miners", "provider": "Solactive", "freq": "semi-annual",
     "months": [5, 11], "rule": "month_start", "lead": 7,
     "eligibility": "copper miners", "source": "solactive.com", "confidence": "low"},
    {"etf": "ISO.AX", "index": "S&P/ASX Small Ordinaries", "provider": "S&P DJI", "freq": "quarterly",
     "months": [3, 6, 9, 12], "rule": "3rd_friday", "lead": 14,
     "eligibility": "ASX 101-300 by float-mcap; effective after close 3rd Friday; announced ~1st Friday",
     "source": "spglobal.com/spdji", "confidence": "high"},
    {"etf": "MVS.AX", "index": "MVIS Australia Small-Cap Dividend Payers", "provider": "MarketVector",
     "freq": "semi-annual", "months": [3, 9], "rule": "3rd_friday", "lead": 5,
     "eligibility": "ASX small-cap dividend payers", "source": "marketvector.com", "confidence": "low"},
]


def next_effective(months, rule, as_of):
    cands = []
    for y in (as_of.year, as_of.year + 1):
        for m in months:
            if rule == "3rd_friday":
                tf = third_friday(y, m)
                eff = tf + timedelta(days=3)  # effective Monday after close of 3rd Friday
            else:  # month_start
                eff = date(y, m, 1)
                while eff.weekday() >= 5:
                    eff += timedelta(days=1)
            if eff >= as_of:
                cands.append(eff)
    return min(cands) if cands else None


def uranium_add_candidates(cfg, fmp) -> pd.DataFrame:
    """URNJ-style rules: ASX uranium names with mcap in range NOT already held."""
    held = set()
    for etf in ("URNJ", "URNM", "URA"):
        h = fmp.get_json("etf/holdings", {"symbol": etf})
        if isinstance(h, list):
            for r in h:
                a = str(r.get("asset", "")).upper()
                if a.endswith(".AX"):
                    held.add(a)
    watch = cfg.watchlists.get("themes", {}).get("uranium", {}).get("watch_asx", [])
    rows = []
    for sym in watch:
        sym = sym.upper()
        prof = fmp.profile(sym)
        mc = prof.get("marketCap")
        if not mc:
            continue
        mc_usd = float(mc) * 0.66  # AUD->USD approx for the US$30m-$3bn rule
        in_range = 30e6 <= mc_usd <= 3e9
        rows.append({"asx_ticker": sym, "company": prof.get("companyName"),
                     "mcap_aud_$m": round(float(mc) / 1e6, 0),
                     "already_in_uranium_etf": sym in held,
                     "fits_URNJ_size_rule": in_range,
                     "candidate": (not sym in held) and in_range})
    return pd.DataFrame(rows).sort_values(["candidate", "mcap_aud_$m"], ascending=[False, False])


def main() -> int:
    cfg = load_config(); cfg.ensure_dirs()
    fmp = FMPClient(cfg)
    today = date.today()

    rows = []
    for s in SCHEDULES:
        eff = next_effective(s["months"], s["rule"], today)
        watch = eff - timedelta(days=s["lead"]) if eff else None
        rows.append({"etf": s["etf"], "index": s["index"], "provider": s["provider"],
                     "frequency": s["freq"], "next_effective": eff,
                     "days_until": (eff - today).days if eff else None,
                     "watch_pro_forma_from": watch, "eligibility": s["eligibility"],
                     "source": s["source"], "confidence": s["confidence"]})
    cal = pd.DataFrame(rows).sort_values("days_until")
    write_csv(cal, cfg.path("tables") / "etf_rebalance_calendar.csv")

    pd.set_option("display.width", 220); pd.set_option("display.max_colwidth", 40)
    print(f"ETF REBALANCE CALENDAR as of {today} (sorted by soonest)\n" + "=" * 70)
    print(cal[["etf", "index", "frequency", "next_effective", "days_until",
               "watch_pro_forma_from", "confidence"]].to_string(index=False))

    print("\nWHO BUYS NEXT (soonest forced-flow events):")
    for _, r in cal.head(4).iterrows():
        print(f"  {r['next_effective']} ({r['days_until']}d): {r['etf']} / {r['index']} "
              f"-> watch pro-forma from ~{r['watch_pro_forma_from']}")

    print("\nURANIUM next-add candidates (URNJ rule: US$30m-$3bn, not already held):")
    cand = uranium_add_candidates(cfg, fmp)
    write_csv(cand, cfg.path("tables") / "uranium_add_candidates.csv")
    print(cand.to_string(index=False))
    print("\nNext uranium reconstitution that would buy these: URNJ/URNM in December.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
