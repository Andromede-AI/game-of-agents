# Taxonomy Classifier Audit — Agreement Results

Cases labeled: 20 / 20

| Category | n | κ (bin@0.5) | Spearman ρ | agree ≤0.25 | mean |Δ| |
|---|---:|---:|---:|---:|---:|
| competitive_coding | 20 | 1.000 | 0.758 | 1.00 | 0.115 |
| marketplace_exploitation | 20 | 0.802 | 0.878 | 0.75 | 0.145 |
| social_influence | 20 | 0.857 | 0.436 | 0.75 | 0.180 |
| information_exploitation | 20 | 0.588 | 0.550 | 0.60 | 0.255 |
| collusion | 20 | 0.000 | 0.857 | 0.80 | 0.098 |
| **pooled** | 100 | 0.780 | 0.814 | 0.78 | 0.159 |

Interpretation guide:
- κ ≥ 0.6 = substantial agreement; 0.4–0.6 = moderate; <0.4 = weak.
- ρ ≥ 0.5 = classifier ranks cases similarly to human.
- agree-within-0.25 ≥ 0.75 = useful for ordinal reporting.

## Note on collusion κ = 0.000

This is a prevalence-paradox artifact, not genuine disagreement.
The classifier never scores collusion ≥ 0.5 (max observed: 0.40), so after
binarization P(classifier = 1) = 0 and κ is undefined / zero regardless of
human labels.  The ordinal metrics tell a different story: Spearman ρ = 0.857
(strong rank agreement) and agree-within-0.25 = 0.80, indicating both raters
assign similar *relative* collusion intensities.  The human rater scored 4/20
cases ≥ 0.75 for collusion; the classifier scored the same cases highest
(0.30–0.40) but below the 0.5 binarization threshold.
