# rebalance_announcements/

Paste real, public index add/delete announcements here (any number of CSVs).
One row per (index, ticker, action).

Columns (see `_TEMPLATE.csv`):
- `provider`, `index_name` — who/what changed.
- `ticker` — ASX code in any form (normalised to `.AX`).
- `action` — `ADD` / `DELETE` (synonyms like ADDITION/REMOVE are accepted).
- `announcement_date` — when it became public (drives the earliest-entry logic).
- `effective_date` — when the change takes effect (the forced-flow date).
- `source_url`, `confidence` — provenance and how sure you are (0–1).

These become `OFFICIAL_INDEX_ADD` / `OFFICIAL_INDEX_DELETE` events.
