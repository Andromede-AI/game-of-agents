"""Compute inter-rater agreement between classifier and the human rater's hand labels.

Reads:
  paper/data/taxonomy_audit_20.md         — labeling template, filled in
  paper/data/taxonomy_audit_scores.json   — classifier scores sidecar

Writes:
  paper/data/taxonomy_audit_results.md    — κ, ρ, agreement per category + overall

Metrics:
  - Cohen's κ (binarized at 0.5): agreement on presence/absence of each behavior
  - Spearman ρ: rank correlation on continuous scores (per category + overall pooled)
  - Agreement-within-0.25: fraction of cases where |classifier - human| ≤ 0.25

Usage:
    uv run python scripts/taxonomy_audit_compute.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

CATEGORIES = [
    "competitive_coding",
    "marketplace_exploitation",
    "social_influence",
    "information_exploitation",
    "collusion",
]
AUDIT_MD = Path("paper/data/taxonomy_audit_20.md")
SCORES_JSON = Path("paper/data/taxonomy_audit_scores.json")
OUT_MD = Path("paper/data/taxonomy_audit_results.md")


def parse_labels(md_text: str) -> dict[int, dict[str, float]]:
    """Parse `case_N:` YAML-like blocks from the audit markdown."""
    labels: dict[int, dict[str, float]] = {}
    # Match each case_N block up through the closing triple backticks.
    pattern = re.compile(
        r"case_(\d+):\s*(.*?)(?=case_\d+:|```)",
        re.DOTALL,
    )
    for m in pattern.finditer(md_text):
        case_num = int(m.group(1))
        body = m.group(2)
        case_labels: dict[str, float] = {}
        for cat in CATEGORIES:
            line_m = re.search(rf"{cat}:\s*([0-9.]+|_+)", body)
            if not line_m:
                continue
            val = line_m.group(1)
            if "_" in val:
                continue  # unfilled
            try:
                case_labels[cat] = float(val)
            except ValueError:
                continue
        if case_labels:
            labels[case_num] = case_labels
    return labels


def cohen_kappa_binary(a: list[int], b: list[int]) -> float:
    """Cohen's κ for binary labels."""
    n = len(a)
    if n == 0:
        return float("nan")
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pa1 = sum(a) / n
    pb1 = sum(b) / n
    pe = pa1 * pb1 + (1 - pa1) * (1 - pb1)
    if pe == 1.0:
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1 - pe)


def spearman_rho(a: list[float], b: list[float]) -> float:
    """Spearman ρ via rank-Pearson; ties broken with average rank."""
    n = len(a)
    if n < 2:
        return float("nan")

    def rank(xs: list[float]) -> list[float]:
        sorted_pairs = sorted(enumerate(xs), key=lambda p: p[1])
        ranks = [0.0] * len(xs)
        i = 0
        while i < len(sorted_pairs):
            j = i
            while j + 1 < len(sorted_pairs) and sorted_pairs[j + 1][1] == sorted_pairs[i][1]:
                j += 1
            avg_rank = (i + j) / 2 + 1  # 1-indexed average
            for k in range(i, j + 1):
                ranks[sorted_pairs[k][0]] = avg_rank
            i = j + 1
        return ranks

    ra = rank(a)
    rb = rank(b)
    mean_a = sum(ra) / n
    mean_b = sum(rb) / n
    num = sum((x - mean_a) * (y - mean_b) for x, y in zip(ra, rb))
    den_a = sum((x - mean_a) ** 2 for x in ra) ** 0.5
    den_b = sum((y - mean_b) ** 2 for y in rb) ** 0.5
    if den_a == 0 or den_b == 0:
        return float("nan")
    return num / (den_a * den_b)


def main() -> int:
    if not AUDIT_MD.exists():
        print(f"Missing {AUDIT_MD}. Run scripts/taxonomy_audit_sample.py first.")
        return 1
    if not SCORES_JSON.exists():
        print(f"Missing {SCORES_JSON}. Run scripts/taxonomy_audit_sample.py first.")
        return 1

    human = parse_labels(AUDIT_MD.read_text())
    sidecar = json.loads(SCORES_JSON.read_text())
    classifier = {entry["case"]: entry["classifier_scores"] for entry in sidecar}

    filled_cases = sorted(human.keys())
    total_cases = len(sidecar)
    print(f"Filled cases: {len(filled_cases)} / {total_cases}")
    if not filled_cases:
        print("No hand labels found. Fill in `case_N:` blocks in the audit md.")
        return 1

    # Per-category pair lists.
    pair_lists: dict[str, tuple[list[float], list[float]]] = {
        cat: ([], []) for cat in CATEGORIES
    }
    for case in filled_cases:
        hlabels = human[case]
        clabels = classifier.get(case, {})
        for cat in CATEGORIES:
            if cat in hlabels and cat in clabels:
                pair_lists[cat][0].append(clabels[cat])
                pair_lists[cat][1].append(hlabels[cat])

    lines = [
        "# Taxonomy Classifier Audit — Agreement Results",
        "",
        f"Cases labeled: {len(filled_cases)} / {total_cases}",
        "",
        "| Category | n | κ (bin@0.5) | Spearman ρ | agree ≤0.25 | mean |Δ| |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    pooled_c: list[float] = []
    pooled_h: list[float] = []
    for cat in CATEGORIES:
        c, h = pair_lists[cat]
        if not c:
            lines.append(f"| {cat} | 0 | — | — | — | — |")
            continue
        c_bin = [1 if x >= 0.5 else 0 for x in c]
        h_bin = [1 if x >= 0.5 else 0 for x in h]
        # κ is degenerate when either rater has no variance (single class).
        # Flag this explicitly so reviewers don't read 0.000 as "no agreement".
        degenerate = len(set(c_bin)) == 1 and len(set(h_bin)) == 1
        kappa_cell = "n/a (single class)" if degenerate else f"{cohen_kappa_binary(c_bin, h_bin):.3f}"
        rho = spearman_rho(c, h)
        within = sum(1 for x, y in zip(c, h) if abs(x - y) <= 0.25) / len(c)
        mad = sum(abs(x - y) for x, y in zip(c, h)) / len(c)
        lines.append(
            f"| {cat} | {len(c)} | {kappa_cell} | {rho:.3f} | "
            f"{within:.2f} | {mad:.3f} |"
        )
        pooled_c.extend(c)
        pooled_h.extend(h)

    if pooled_c:
        c_bin = [1 if x >= 0.5 else 0 for x in pooled_c]
        h_bin = [1 if x >= 0.5 else 0 for x in pooled_h]
        kappa = cohen_kappa_binary(c_bin, h_bin)
        rho = spearman_rho(pooled_c, pooled_h)
        within = sum(1 for x, y in zip(pooled_c, pooled_h) if abs(x - y) <= 0.25) / len(pooled_c)
        mad = sum(abs(x - y) for x, y in zip(pooled_c, pooled_h)) / len(pooled_c)
        lines.append(
            f"| **pooled** | {len(pooled_c)} | {kappa:.3f} | {rho:.3f} | "
            f"{within:.2f} | {mad:.3f} |"
        )

    lines.append("")
    lines.append("Interpretation guide:")
    lines.append("- κ ≥ 0.6 = substantial agreement; 0.4–0.6 = moderate; <0.4 = weak.")
    lines.append("- κ flagged *n/a (single class)* means the category is degenerate on this sample (both raters said 0 everywhere, or 1 everywhere) — κ is undefined, fall back to agree-within-0.25 and mean |Δ| to assess the category.")
    lines.append("- ρ ≥ 0.5 = classifier ranks cases similarly to human.")
    lines.append("- agree-within-0.25 ≥ 0.75 = useful for ordinal reporting.")

    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(lines) + "\n")
    print(f"Wrote {OUT_MD}")
    print()
    for line in lines[2:]:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
