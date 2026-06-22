# asx-index-flow-alpha-lab
#
# Windows note: `make` is not installed by default. Either use `uv`/`pip` with
# `python scripts/<name>.py`, or install GNU Make (e.g. via scoop/choco/Git for
# Windows). Every target below is a thin wrapper around a script in scripts/.

PY ?= python

.PHONY: help install discover-etfs build-registry fetch-holdings diff-holdings \
        fetch-prices build-events event-study backtest research-loop test lint all

help:
	@echo "Targets:"
	@echo "  install         pip install -e .[dev]"
	@echo "  discover-etfs   scan provider/issuer pages for ETF & benchmark candidates"
	@echo "  build-registry  assemble configs + seeds + discovery into index/etf registries"
	@echo "  fetch-holdings  download (or load manual) holdings snapshots"
	@echo "  diff-holdings   diff successive holdings snapshots -> add/del/weight events"
	@echo "  fetch-prices    pull ASX OHLCV / market-cap / profile data from FMP"
	@echo "  build-events    merge all sources into the unified events table"
	@echo "  event-study     run the delayed-reaction event study"
	@echo "  backtest        run strategy backtests with realistic costs"
	@echo "  research-loop   run the bounded self-improvement research loop"
	@echo "  test            run pytest"

install:
	$(PY) -m pip install -e ".[dev]"

discover-etfs:
	$(PY) scripts/discover_etfs.py

build-registry:
	$(PY) scripts/build_index_registry.py

fetch-holdings:
	$(PY) scripts/fetch_holdings.py

diff-holdings:
	$(PY) scripts/diff_holdings.py

fetch-prices:
	$(PY) scripts/fetch_fmp_data.py

build-events:
	$(PY) scripts/build_events.py

event-study:
	$(PY) scripts/run_event_study.py

backtest:
	$(PY) scripts/run_backtest.py

research-loop:
	$(PY) scripts/run_research_loop.py

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check src scripts tests

all: build-registry fetch-prices build-events event-study backtest
