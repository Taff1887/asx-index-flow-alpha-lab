# Manual ingestion — what to drop here and why

The engine runs on whatever is present and **never fabricates** missing
index/ETF data. Most providers JS-render, gate, or licence their holdings, so the
high-signal inputs come from you. Each subfolder has a `_TEMPLATE.csv` showing the
exact columns. Filenames and columns matter — stick to the templates.

| Folder | File pattern | Purpose |
|---|---|---|
| `holdings_snapshots/<PRODUCT>/` | `<YYYY-MM-DD>.csv` | **Highest value.** Dated constituent lists per product. ≥2 dated files per product → the diff engine detects adds/weight changes = the raw flow signal. |
| `rebalance_announcements/` | `*.csv` | Real announced index add/delete rows with announcement & effective dates. → OFFICIAL_INDEX_ADD/DELETE events. |
| `etf_registry/` | `*.csv` | Extra ETF products the seed list misses (AUM, fee, # holdings, rebalance months, holdings_url). |
| `index_registry/` | `*.csv` | Extra benchmark indices / providers. |
| `methodology_pdfs/` | `<provider>_<index>.pdf` | Methodology PDFs → parsed for cap/weighting/eligibility/rebalance rules. |
| `overrides/` | `events_overrides.csv`, `custom_events.csv` | Correct an effective date / confirm an event / set confidence; or inject a fully manual event. |

## Column conventions

- **Tickers**: ASX names in any form — `PDN`, `PDN.AX`, `PDN AU`, `ASX:PDN`. The
  engine normalises to `CODE.AX`. For holdings of *global* products, include an
  `exchange` column so non-ASX lines aren't mis-tagged (a bare 3-letter code with
  no exchange is treated as *not* ASX, to avoid false positives).
- **Weights**: either fraction (`0.125`) or percent (`12.5` / `12.5%`) — both are
  handled; percent is auto-detected and divided by 100.
- **Dates**: `YYYY-MM-DD` preferred.

## Where to get the files (legitimately)

- ETF issuer fund pages (Sprott, VanEck, BetaShares, Global X, iShares, HANetf,
  …) publish a daily/holdings CSV per fund — download and save dated copies.
- Index providers publish methodology PDFs and rebalance press releases publicly;
  the *constituent lists* are usually licensed — paste the announced add/delete
  rows into `rebalance_announcements/`.
- Always respect each provider's terms of use.
