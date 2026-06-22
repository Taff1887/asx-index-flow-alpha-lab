# methodology_pdfs/

Drop index/ETF methodology PDFs here, named `<provider>_<index>.pdf`, e.g.:

```
sprott_north-shore-uranium-miners.pdf
marketvector_mvis-rare-earth.pdf
spdji_asx-200.pdf
```

`methodology_parser.py` extracts (best-effort) the capping rule, weighting
scheme, rebalance/reconstitution cadence, and market-cap/liquidity eligibility
thresholds. Anything it can't find is left blank — it does not guess.

These feed the `MethodologyChangeAnticipation` strategy and weight-rule context.
