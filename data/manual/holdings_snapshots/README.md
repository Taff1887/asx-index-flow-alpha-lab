# holdings_snapshots/

Drop dated constituent files here, one folder per product:

```
holdings_snapshots/
  URNM/
    2026-05-01.csv
    2026-05-15.csv
    2026-06-01.csv
  ATOM/
    2026-05-01.csv
    ...
```

- **Folder name = the product ticker** used in the registry (e.g. `URNM`, `GDXJ`,
  `ATOM`). Diffs are computed within a folder, across dates.
- **File name = the snapshot date** as `YYYY-MM-DD.csv`.
- Columns: see `_TEMPLATE.csv`. `exchange` is optional but strongly recommended
  for global products so non-ASX lines aren't mistaken for ASX names.
- You need **at least two dated files** per product before any change can be
  detected. More history → more events.

These diffs are the single most important input to the lab.
