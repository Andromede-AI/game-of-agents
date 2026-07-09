# Additive Price-Quality Check

This offer-level diagnostic is intentionally withdrawn from the main paper.

The checked-in `paper/data/run_summary.csv` remains the paper's authoritative
per-run summary source, but the available full dashboard export for
`run_57vd316xyfk16q` currently reports 103 offers while `run_summary.csv`
reports 50. Because this count mismatch affects the pooled offer-level
distribution for the clean-additive 3-hour cell, the manuscript no longer uses
the posted-price distribution, per-seller offer volume, or mean-review-per-offer
claim that previously depended on this artifact.

`scripts/additive_price_quality.py` now refuses to regenerate this file unless
the analyzed local exports match the authoritative offer counts in
`paper/data/run_summary.csv`.
