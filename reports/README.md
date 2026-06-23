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

### The standout edge: index-DELETION rebound (buy the forced-selling washout)
Buy the deleted name at the effective close, hold ~10 days. **373 real events:
+4.3% mean / +1.0% median net, hit 56%, 95% CI (+0.5%, +9.2%)** — excludes zero.
Fat right tail, so size small + use stops.
![deletion rebound](figures/fig_deletion_rebound.png)

### Honest control: thinner large-cap adds do NOT drift more
![by liquidity](figures/fig_inclusion_by_liquidity.png)
([`inclusion_runup_by_liquidity.csv`](tables/inclusion_runup_by_liquidity.csv))

---

## 2b. Live forced-flow scanner — the AGE/URNJ pattern

`scripts/flow_scanner.py` scans 32 global thematic/mining ETFs → 176 ASX names,
ranked by a forced-flow score (overhang × days-to-exit × #ETFs × inflow
sensitivity). ([`flow_scanner.csv`](tables/flow_scanner.csv)). Top of the list:

| ASX | company | #ETFs | ETF own % float | ADV $m | exit days @20% ADV | score |
|---|---|---|---|---|---|---|
| DYL | Deep Yellow | 4 | 21.7% | 10.6 | 172 | 0.96 |
| PDN | Paladin Energy | 4 | 19.9% | 35.3 | 117 | 0.95 |
| BMN | Bannerman Energy | 3 | 18.6% | 3.8 | 193 | 0.94 |
| PEN | Peninsula Energy | 3 | 47.2% | 1.6 | 118 | 0.93 |
| **AGE** | **Alligator Energy** | **3** | **9.2%** | **0.7** | **116** | **0.92** |
| LOT | Lotus Resources | 3 | 24.6% | 5.0 | 48 | 0.87 |
| EL8 | Elevate Uranium | 2 | 11.4% | 0.4 | 139 | 0.88 |

`scripts/detect_etf_accumulation.py` then confirms an *in-progress* buy from the
daily holdings snapshots (net Δshares ÷ ADV) — the AGE trade, mechanised.

**Full strategy playbook (what works, what doesn't, and the legal framing):**
see [`../FINDINGS.md`](../FINDINGS.md) §4.

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
