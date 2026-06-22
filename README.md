# asx-index-flow-alpha-lab

**An index-flow alpha lab for ASX equities.**

This is a research engine that scans the *world's* indices, ETFs, and benchmark
products for **forced buying/selling into ASX-listed stocks**, and tries to find
the cases where the market has **not yet fully priced the flow**.

It is **not** an S&P/ASX rebalance calendar. The well-known S&P/ASX 200 quarterly
changes are widely watched and largely arbitraged on announcement. This lab is
built to hunt the *obscure* end of the spectrum:

> Weird, thematic, small/micro-cap, sector-specific, global, commodity (uranium,
> gold, rare earths, battery metals), defence, clean energy, robotics/AI,
> agriculture, infrastructure, biotech indices where an ASX name gets added,
> the announcement is **public but under-covered**, the stock **doesn't jump
> enough**, and the product **still has to buy** — creating delayed price impact
> that is large versus the stock's liquidity.

## The core question

> *Which global indices or ETFs can force buying into ASX stocks, and which of
> those flows are underpriced?*

## What it does

1. Maintains an **index/ETF registry** (`reports/tables/index_registry.csv`,
   `etf_registry.csv`) seeded from known providers/issuers and extended by a
   discovery loop.
2. **Downloads or ingests holdings** snapshots (auto where a stable file exists,
   **manual drop-in** where scraping is blocked, licensed, or unreliable).
3. **Diffs successive snapshots** to detect new positions, deletions, and weight
   changes — and flags any **ASX-listed name** appearing in a global product.
4. Pulls **ASX price/volume/market-cap/sector** data from FMP.
5. Builds a **unified events table** from official index changes, ETF holdings
   diffs, methodology/benchmark changes, and manual events.
6. Estimates **flow pressure** (`estimated buy $ / 3m ADV $`) and runs an
   **event study** across many windows to measure immediate vs. delayed reaction.
7. **Ranks** events by a *delayed-flow opportunity score* and backtests
   strategies with **realistic costs, slippage, market impact, and ADV caps**.

## It will not fabricate data

Most index providers and many ETF issuers either render holdings via JavaScript,
gate them behind licences, or do not publish *historical* holdings at all. This
repo **never invents holdings, AUM, or constituents.** Where data cannot be
fetched legitimately it builds a **manual ingestion path** and tells you exactly
which file to drop in (see [What you must provide](#what-you-must-provide-manually)).

## Repository layout

```
configs/        config.yaml, providers.yaml, strategy_params.yaml, watchlists.yaml
data/
  raw/          fmp/, web/, issuer_holdings/, index_methodologies/, announcements/
  interim/      intermediate artefacts
  processed/    cleaned, analysis-ready tables
  manual/       <-- YOU drop files here (registries, holdings, PDFs, overrides)
notebooks/      01..05 progressive research notebooks
reports/        figures/, tables/ (the CSV outputs), scorecards/
src/index_flow/ the engine (config, clients, registry, holdings, events, study,
                strategies, backtester, costs, reporting, research loop, ...)
scripts/        CLI entrypoints (one per Makefile target)
tests/          pytest suite (registry, holdings diff, events, reaction,
                no-lookahead, backtester)
```

## Quickstart

```bash
# 1. Install (Python 3.11+). Using uv:
uv venv && uv pip install -e ".[dev]"
#   or plain pip:
python -m pip install -e ".[dev]"

# 2. Configure secrets
cp .env.example .env          # add your FMP_API_KEY

# 3. Build the registry from configs + seeds (+ any discovery)
python scripts/build_index_registry.py        # make build-registry

# 4. (Optional) discover more ETFs/benchmarks from provider pages
python scripts/discover_etfs.py               # make discover-etfs

# 5. Ingest holdings: auto-download where possible, else manual drop-ins
python scripts/fetch_holdings.py              # make fetch-holdings
python scripts/diff_holdings.py               # make diff-holdings

# 6. Pull ASX market data
python scripts/fetch_fmp_data.py              # make fetch-prices

# 7. Build events, run the study, backtest
python scripts/build_events.py                # make build-events
python scripts/run_event_study.py             # make event-study
python scripts/run_backtest.py                # make backtest

# 8. Bounded self-improvement loop (ranks, studies, backtests, logs hypotheses)
python scripts/run_research_loop.py           # make research-loop

# Tests
python -m pytest                              # make test
```

> **Windows:** `make` isn't installed by default — run the `python scripts/...`
> commands directly, or install GNU Make. All targets are thin wrappers.

## How an opportunity is scored

Per event the engine computes (definitions in `reaction_detector.py` /
`flow_estimator.py`):

```
immediate_reaction      = return(announcement close -> next open/close)
delayed_return          = return(next open/close -> effective/implementation date)
flow_pressure           = estimated_buy_dollars / 3m_ADV_dollars
underreaction_score     = delayed_return / max(|immediate_reaction|, small_number)

delayed_flow_opportunity_score =
    flow_pressure
  * probability_event_is_real
  * probability_buying_not_complete
  * liquidity_tightness
  * max(0, expected_remaining_flow)
  / max(1, immediate_price_reaction_percent)
```

High score = big forced demand, thin stock, buying not done, effective date still
ahead, and the price has barely moved yet.

## Strategies (see `strategies.py`, `configs/strategy_params.yaml`)

1. **DelayedAnnouncementReaction** — buy adds with small immediate move + high
   flow pressure; exit on/near the effective date. *(built first, fully wired)*
2. **HighFlowLowLiquidity** — rank by demand/ADV in thin names.
3. **HoldingsDiffSignal** — trade newly-detected ETF positions from snapshot diffs.
4. **MethodologyChangeAnticipation** — names that newly qualify after a rule change.
5. **ThematicIndexPrediction** — ML: which ASX names are likely to be *added*.
6. **RebalanceCalendarTrade** — pre-position only on high-confidence scheduled events.
7. **EffectiveDateFade** — *research only*; does not assume shorting is available.
8. **MultiETFStackedFlow** — names added to several products at once.
9. **ForgottenSmallCapAdd** — obscure source + thin + high flow + low coverage.

## Backtesting realism

- Post-close announcement ⇒ earliest entry is **next open**.
- Entry variants: next open, next close, VWAP proxy.
- Costs: brokerage + half-spread + slippage + **market impact** (`bps ∝ trade/ADV`),
  plus a **harsher-costs** stress run.
- Trades capped at a configurable **% of ADV**; thin/zero-volume/halt days handled.
- Events needing manual validation are flagged; capacity is tracked.

## Claims discipline

The lab will **not** call something alpha unless it: survives costs *and* harsher
slippage; isn't driven by a single trade; works across more than one event;
is economically sensible; uses only point-in-time data (see
`tests/test_no_lookahead.py`); has tradeable liquidity; and holds up
out-of-sample / walk-forward.

## What you must provide manually

The framework runs end-to-end on whatever data is present, but to produce *real*
results you will need to supply data the providers don't hand out freely. Drop
files into `data/manual/` using the templates there. The highest-leverage inputs:

| What | Where | Why |
|---|---|---|
| **Holdings snapshots** (dated CSVs per product) | `data/manual/holdings_snapshots/<PRODUCT>/<YYYY-MM-DD>.csv` | Most issuer holdings are JS-rendered/licensed; historical holdings are rarely public. This is the single most important input — diffs of these *are* the flow signal. |
| **Index reconstitution / rebalance announcements** | `data/manual/rebalance_announcements/*.csv` | Index providers' add/delete & effective dates are licensed; paste the public announcement rows. |
| **Methodology PDFs** | `data/manual/methodology_pdfs/<provider>_<index>.pdf` | For methodology-change strategies & weight rules. |
| **Registry overrides / extra products** | `data/manual/etf_registry/*.csv`, `index_registry/*.csv` | Add niche products the seed list misses (AUM, fees, # holdings, rebalance months). |
| **Per-event overrides** | `data/manual/overrides/events_overrides.csv` | Correct an effective date, confirm/flag an event, set confidence. |

Every manual folder contains a `_TEMPLATE.csv` and a `README.md` describing the
exact columns. Anything you don't provide simply produces fewer events — nothing
is faked to fill the gap.

`FMP_API_KEY` (in `.env`) is required for live price data; without it the price
layer falls back to any cached pulls in `data/raw/fmp/` and otherwise reports
what's missing.

## Status

Built first (per the project brief): repo skeleton, registry framework, FMP
client, holdings snapshot/diff system, manual ingestion, ASX ticker
normalisation, event builder, delayed-reaction event study, the
**DelayedAnnouncementReaction** strategy, reporting outputs, and tests. The other
strategies and the ML candidate predictor are scaffolded with a common interface
and progressively fleshed out via the research loop.
