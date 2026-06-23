# Results

Real-data results from this repo. Figures regenerate via
`python examples/make_figures.py`; the underlying CSVs are in
[`tables/`](tables/). Full methodology + caveats: [`../FINDINGS.md`](../FINDINGS.md).

> All numbers are real: FMP `stable` API for prices + ETF holdings, and FMP
> `historical-*-constituent` for dated index membership changes. No synthetic
> data. As-of late June 2026.

---

## 1. Multi-index inclusion strategy — a real, significant, costed edge

**1,514 real index-membership changes since 2005** (S&P 500 + Nasdaq‑100 + Dow +
ASX 200), **4,878 backtested trade-legs**. Strategy: buy an index *addition*
ahead of its effective date, sell at the effective close; with real per-market
costs, a 3× harsh-cost stress, beta-stripped, no lookahead.
([`inclusion_strategy_summary.csv`](tables/inclusion_strategy_summary.csv),
[`inclusion_ledger.csv`](tables/inclusion_ledger.csv))

| strategy | n | avg net | harsh | hit | t-stat | 95% CI (net) | survives? |
|---|---|---|---|---|---|---|---|
| **ADD run-up T‑5 → effective (long)** | 642 | **+1.09%** | +0.88% | 57% | **3.08** | (+0.41%, +1.75%) | ✅ |
| same, market-excess (beta-stripped) | 635 | +0.99% | +0.78% | 57% | 3.00 | (+0.29%, +1.61%) | ✅ |
| ADD run-up T‑3 → effective | 647 | +0.37% | +0.16% | 53% | 1.04 | (−0.32%, +1.06%) | ❌ |
| ADD run-up T‑1 → effective | 650 | −0.04% | −0.25% | 45% | −0.23 | (−0.40%, +0.38%) | ❌ |
| ADD post-effective drift (eff→+10) | 648 | −0.63% | −0.84% | 45% | −1.28 | (−1.51%, +0.40%) | ❌ |
| DEL short (eff→+10) | 373 | −4.49% | −4.69% | 43% | −1.95 | (−9.44%, −0.74%) | ❌ |

### The edge decays within ~2 days of the announcement
![inclusion decay](figures/fig_inclusion_decay.png)

The whole effect (+1.09%, t=3.1) is captured entering ~5 trading days out; by T‑3
it's insignificant and by T‑1 it's gone. The obvious add is arbitraged almost
immediately — you have to be in the under-reaction window. (This is the lab's
core thesis, quantified.)

### It scales with passive AUM — and is faint on the ASX 200
![by index](figures/fig_inclusion_by_index.png)
([`inclusion_runup_by_index.csv`](tables/inclusion_runup_by_index.csv))

### Positive in 15 of 22 years — not a one-off
![by year](figures/fig_inclusion_by_year.png)
([`inclusion_runup_by_year.csv`](tables/inclusion_runup_by_year.csv))

### Price BEFORE & AFTER inclusion vs removal (cumulative abnormal return)
Additions run up ~+3.6% abnormal into the effective date then plateau; removals
only dip ~−1% and barely recover. The "deletion rebound" is small once you adjust
for the market — the raw +4.3% mean (histogram below) is fat-tailed and mostly
beta. ([`event_path_caar.csv`](tables/event_path_caar.csv))
![event path](figures/fig_event_path.png)
![deletion rebound](figures/fig_deletion_rebound.png)

### Honest control: thinner large-cap adds do NOT drift more
![by liquidity](figures/fig_inclusion_by_liquidity.png)
([`inclusion_runup_by_liquidity.csv`](tables/inclusion_runup_by_liquidity.csv))

---

## 2b. Live forced-flow scanner — the AGE/URNJ pattern

`scripts/flow_scanner.py` scans 32 global thematic/mining ETFs → 176 ASX names,
ranked by a forced-flow score (overhang × days-to-exit × #ETFs × inflow
sensitivity). Now **89 ETFs across every theme → 330 ASX names**
([`flow_scanner.csv`](tables/flow_scanner.csv)). Top of the list:

| ASX | company | #ETFs | ETF own % float | ADV $m | exit days @20% ADV | score |
|---|---|---|---|---|---|---|
| EVN | Evolution Mining | 11 | 6.0% | 107 | 72 | 0.96 |
| GMD | Genesis Minerals | 9 | 5.8% | 31 | 65 | 0.95 |
| DYL | Deep Yellow | 6 | 22.0% | 10.6 | 174 | 0.94 |
| PDN | Paladin Energy | 6 | 20.2% | 35.3 | 119 | 0.94 |
| RMS | Ramelius Resources | 8 | 6.7% | 34 | 66 | 0.93 |
| TCL | Transurban (infra) | 9 | 2.5% | 90 | 62 | 0.92 |
| BRE | Brazilian Rare Earths | 4 | 85.1%* | 3.0 | 2110 | 0.91 |

Broadening beyond miners pulls in infrastructure (Transurban, Qube), property
(Goodman) and mega-caps (BHP, 13 ETFs) via broad/sector funds. *BRE's 85% is
likely a low-free-float / recent-listing data quirk — verify before trusting.

`scripts/detect_etf_accumulation.py` then confirms an *in-progress* buy from the
daily holdings snapshots (net Δshares ÷ ADV) — the AGE trade, mechanised.

---

## 2c. Next trades (forward watchlist)

`scripts/next_trades.py` turns the above into a dated, actionable report
([`next_trades_asx_watch.csv`](tables/next_trades_asx_watch.csv),
[`next_trades_deletion_rebounds.csv`](tables/next_trades_deletion_rebounds.csv)):

**A — Proven edge (deletion rebound):** live only when a US index deletion is
inside its ~10-day window. *As of this run: none live* (most recent, EPAM, is
14 days out and −30% — a reminder the edge is fat-tailed with real left-tail risk).

**B — Heavy ETF overhang that hasn't rallied** (forced demand present, price
flat/down — a *screen*, not a backtested signal):

| ASX | company | #ETFs | % float | 1m | 3m | score |
|---|---|---|---|---|---|---|
| DYL | Deep Yellow | 6 | 22% | +1% | −3% | 0.94 |
| PDN | Paladin Energy | 6 | 20% | −6% | −3% | 0.94 |
| BOE | Boss Energy | 4 | 20% | −4% | −21% | 0.89 |
| BMN | Bannerman Energy | 3 | 19% | +0% | +2% | 0.88 |
| SLX | Silex Systems | 3 | 14% | +1% | +13% | 0.88 |
| VUL | Vulcan Energy | 5 | 9% | −5% | +14% | 0.89 |

**C — Catalyst calendar:** next quarterly reconstitution **2026-09-18** (VanEck/
Global X thematics rebalance then; Sprott uranium semi-annually). The live
"being bought now" confirmation comes from the daily-snapshot accumulation
detector.

> Confidence labels matter: only the deletion rebound (A) is backtested. The
> overhang watchlist (B) is the lab's hypothesis — high ETF ownership + no rally —
> not a proven trade. Full playbook + legal framing: [`../FINDINGS.md`](../FINDINGS.md) §4.

---

## 2. Where the ASX opportunity actually lives — forced-ownership overhang

Since the *obvious* ASX 200 add has no tradeable edge (+0.15%, above), the real
target is the *obscure* thematic-ETF flow into thin names. Current real holdings
of 20 global thematic ETFs → ASX constituents, ranked by ETF ownership as a share
of float. ([`forced_ownership_map.csv`](tables/forced_ownership_map.csv))

![forced ownership](figures/fig_forced_ownership.png)

Uranium juniors are heavily owned by a handful of foreign passive products —
Peninsula Energy ~47%, Deep Yellow ~22%, Paladin ~20% of shares outstanding, many
needing 100+ days of normal volume to unwind. That is the precise setup for
under-priced forced flow when those ETFs reconstitute.

---

## How to reproduce

```bash
python examples/index_inclusion_backtest.py   # the multi-index backtest (§1)
python scripts/forced_ownership_map.py        # the overhang map (§2)
python examples/make_figures.py               # rebuild these PNGs from the CSVs
```
