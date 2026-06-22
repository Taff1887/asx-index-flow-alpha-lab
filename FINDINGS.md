# Findings — real data, honest results

> All numbers below come from **real data**: FMP `stable` API for ASX prices and
> ETF holdings, and publicly-announced S&P/ASX 200 changes. Reproduce with the
> scripts named in each section. Dates/values are point-in-time as of late June
> 2026 and will drift as holdings/prices update.

## TL;DR

1. **The obvious effect is dead after costs.** The classic S&P/ASX 200
   index-inclusion drift is **+1.2% gross** (announcement → effective date) but
   **+0.14% after costs and −0.8% after harsher costs**, hit-rate 50%, bootstrap
   CI on net return **(−4.3%, +4.4%)** — straddles zero. `claim_alpha = False`.
   Don't trade obvious ASX 200 adds.
2. **The real inefficiency is the obscure overhang.** A handful of thin ASX
   uranium/critical-mineral names carry *enormous* forced ownership by foreign
   thematic ETFs relative to their own liquidity — the structural pre-condition
   for under-priced forced flow.

This is exactly the thesis the lab was built on, now evidenced on real data.

---

## 1. S&P/ASX 200 inclusion drift — the benchmark (arbitraged)

**Script:** `examples/asx200_inclusion_study.py` (tickers resolved + prices
verified via FMP; any unresolved name is dropped, never invented).

12 real ASX 200 additions across 3 quarters (Mar-2024, Sep-2024, Mar-2025).
Strategy: buy next open after the announcement, exit at the effective-date close.

| metric | value |
|---|---|
| events | 12 |
| avg gross return (ann → effective) | **+1.24%** |
| avg after costs | +0.14% |
| avg after **harsh** costs | **−0.80%** |
| hit rate | 50% |
| best / worst | +17.0% (SPR) / −16.4% (DGT) |
| bootstrap CI (net) | (−4.3%, +4.4%) |
| **claim_alpha** | **False** |

**Read:** big winners (Spartan Resources +18%, Austal, Temple & Webster) are
offset by big losers (DigiCo −15.5%, Nuix −5.4%). The mean is a rounding error
once you pay the spread. The well-watched, liquid ASX 200 add is efficiently
priced — front-run by index arbitrageurs before the effective date.

---

## 2. Forced-ownership overhang map — where the inefficiency lives

**Script:** `scripts/forced_ownership_map.py`. Pulls **current real holdings**
of 20 global thematic ETFs (uranium, gold/silver miners, rare-earth/lithium,
copper, mining) from FMP, keeps the ASX-listed constituents (89 names), and
aggregates per ASX stock: how many ETFs hold it, ETF shares as a % of shares
outstanding, and as days of the stock's own average volume to unwind.

Top of the map (ranked by days-to-exit at a realistic 20%-of-ADV participation):

| ASX | company | #ETFs | ETFs | ETF own % of shares | ADV $m | days to exit @20% ADV |
|---|---|---|---|---|---|---|
| PEN.AX | Peninsula Energy | 3 | URA, URNJ, URNM | **47.2%** | 1.6 | 118 |
| DYL.AX | Deep Yellow | 4 | NLR, URA, URNJ, URNM | **21.7%** | 10.6 | 172 |
| PDN.AX | Paladin Energy | 4 | NLR, URA, URNJ, URNM | **19.9%** | 35.3 | 117 |
| BOE.AX | Boss Energy | 3 | URA, URNJ, URNM | 19.7% | 8.2 | 72 |
| BMN.AX | Bannerman Energy | 3 | URA, URNJ, URNM | 18.6% | 3.8 | 193 |
| SLX.AX | Silex Systems | 2 | NLR, URA | 13.8% | 5.2 | 177 |
| EL8.AX | Elevate Uranium | 2 | URA, URNJ | 11.4% | 0.4 | 139 |

(Gold miners EVN, WGX, RMS, GMD and copper SFR also appear, but with far lower
overhang — single-digit % of float, ~60 days — because they are larger/more
liquid.)

**Read:** these uranium juniors are, in effect, **majority/heavily owned by a
handful of foreign passive products**. When any of those ETFs reconstitutes
(URA, URNM, URNJ all rebalance semi-annually) the forced trade is many multiples
of a single day's volume. That is the precise setup the lab hunts: large forced
flow into a thin name. Note these % use ETF-reported share counts vs current
shares outstanding and should be confirmed against the share register for
position-sizing precision; the *ranking* is the signal.

---

## Why we can't (yet) backtest #2 — and what unblocks it

To backtest the overhang/flow edge you need **dated holdings history** (to know
when each ETF added/grew a position) or **dated index-change announcements**.
Confirmed during this work:

- **FMP exposes only *current* ETF holdings** — no historical-by-date on this
  plan; `index-constituent`/`historical-index-constituent` return nothing for
  ASX (`^AXJO`).
- **Every authoritative web source is bot-blocked** (spglobal.com, marketindex,
  the ASX/markitdigital announcement CDN all return 403).

This is exactly the constraint the repo was architected around. Two real,
working paths now exist:

1. **Build history going forward (zero manual work):**
   `scripts/fetch_etf_holdings_fmp.py` saves a **real dated snapshot** of all 20
   ETFs' holdings each run (19 saved today). Schedule it weekly; after two runs
   `make diff-holdings && make build-events` emit **real holdings-change events**
   and the full event-study/backtest runs on them.
2. **Backfill history (manual):** drop dated holdings CSVs or announced
   add/delete lists into `data/manual/` (templates included).

---

## 3. Multi-index inclusion strategy — a real, significant, costed edge

**Script:** `examples/index_inclusion_backtest.py`. **1,514** real dated
membership changes since 2005 from FMP (`historical-*-constituent`): S&P 500,
Nasdaq-100, Dow Jones, plus the verified S&P/ASX 200 adds — **4,878 backtested
trade-legs** across strategies. Real prices, per-market costs (US 10bps RT / 40bps
ASX) + a 3x harsh stress, no lookahead. Events without FMP price coverage dropped
(counted, not hidden).

**Strategies & results (after costs):**

| strategy | n | avg gross | avg net | avg harsh | hit | t-stat | 95% CI (net) | claim |
|---|---|---|---|---|---|---|---|---|
| **S1 ADD run-up T‑5→effective (long)** | 642 | +1.19% | **+1.09%** | +0.88% | 57% | **3.08** | (+0.41%, +1.75%) | **True** |
| S1x same, **market-excess** (minus index ETF) | 635 | +1.10% | +0.99% | +0.78% | 57% | **3.00** | (+0.29%, +1.61%) | **True** |
| S1 ADD run-up **T‑3**→eff | 647 | +0.47% | +0.37% | +0.16% | 53% | 1.04 | (−0.32%, +1.06%) | False |
| S1 ADD run-up **T‑1**→eff | 650 | +0.06% | −0.04% | −0.25% | 45% | −0.23 | (−0.40%, +0.38%) | False |
| S2 ADD post-effective drift eff→+10 (long) | 648 | −0.53% | −0.63% | −0.84% | 45% | −1.28 | (−1.51%, +0.40%) | False |
| S3 DEL short eff→+10 (short) | 373 | −4.39% | −4.49% | −4.69% | 43% | −1.95 | (−9.44%, −0.74%) | False |

**What it means:**

- **The index-inclusion run-up is real and statistically significant.** Buying an
  addition ~5 trading days before its effective date and selling at the effective
  close earns **+1.09% net per event, t‑stat 3.1**, and the 95% bootstrap CI
  excludes zero. It survives **3x harsher costs** (+0.88%) and is **beta-neutral**
  — the market-excess version (minus the index ETF over the same window) is almost
  identical (+0.99%, t=3.0), so it isn't just market drift.
- **It decays within days — this IS the thesis.** The whole edge is in the first
  ~2 days after the announcement: by **T‑3 it's +0.37% (insignificant)** and by
  **T‑1 it's gone (−0.04%)**. The obvious add is arbitraged almost immediately;
  the only way to capture it is to trade at/just-after the announcement. Wait, and
  there's nothing left. (Caveat: FMP gives the *effective* date, not the
  announcement date; "T‑5" proxies "at announcement", which for S&P/Nasdaq is
  typically ~5 business days before effective. For M&A-driven adds the lead can be
  shorter, so the very front of the window may not always be tradeable — the decay
  profile, not a turnkey P&L, is the robust finding.)
- **No post-inclusion momentum** (S2 = −0.6%) and **deletions rebound, not fall**
  (S3 short loses 4.4% ⇒ deleted names *bounce* ~+4.4% mean / +1.2% median over 10
  days — a fat-tailed, contrarian "buy the forced-selling washout" effect worth a
  closer look, but skew-driven).

**By index (S1 T‑5):** Dow +2.50% (n=31, hit 68%), Nasdaq-100 +1.27% (n=197),
S&P 500 +0.92% (n=402), **S&P/ASX 200 +0.15% (n=12)**. The effect scales with
passive AUM mechanically tracking the index — strongest in the Dow/Nasdaq, and
**weakest in the ASX 200**, consistent with §1's benchmark result that the obvious
ASX effect is arbitraged.

**By year (S1 T‑5):** positive in **15 of 22 years**; negatives cluster in
crisis/idiosyncratic years (2008 −2.2%, 2010, 2016, 2018, 2022). Not driven by a
single year.

**Bottom line:** the forced-flow inclusion edge is genuinely there and survives
costs — but it is fast (≤2 days) and largest where passive AUM is largest (US
mega-indices), and faint on the ASX 200. The implication for *this* lab is direct:
the tradeable ASX opportunity is **not** the well-watched ASX 200 add — it's the
obscure thematic-ETF flow into thin names (§2), where the same mechanical buying
hits far less liquidity and far less arbitrage attention.

---

## Reproduce

```powershell
.\.venv\Scripts\python.exe scripts/forced_ownership_map.py        # the overhang map (§2)
.\.venv\Scripts\python.exe examples/asx200_inclusion_study.py     # ASX 200 benchmark (§1)
.\.venv\Scripts\python.exe examples/index_inclusion_backtest.py   # multi-index backtest (§3)
.\.venv\Scripts\python.exe scripts/fetch_etf_holdings_fmp.py      # save today's real snapshots
```

Outputs land in `reports/tables/` (`forced_ownership_map.csv`,
`asx200_inclusion_events.csv`, `asx200_inclusion_ledger.csv`).
