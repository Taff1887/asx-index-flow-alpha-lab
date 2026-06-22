# overrides/

Two optional files (copy the matching `_TEMPLATE_*` and rename):

### `events_overrides.csv`
Patch fields on an already-built event, keyed by `event_id` (find it in
`reports/tables/discovered_events.csv`). Only non-empty cells override. Typical
use: pin a confirmed `effective_date`, bump `confidence_score`, or force
`tradeable_flag`.

### `custom_events.csv`
Inject a fully manual event the automated sources can't see. Leave `event_id`
blank to have one generated. Use the canonical event columns (see
`discovered_events.csv`); `_product_ticker` links it to a registry product for
flow sizing.
