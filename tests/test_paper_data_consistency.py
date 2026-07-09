"""Paper data consistency tests.

Assertions that guard against the staleness failure modes we've hit
repeatedly during the `agents-wild` branch work:

- `paper/data/run_summary.csv` has 39 paper-grade run rows (15 condition
  groups), not 19 or 29 or 24.
- `paper/data/taxonomy_frequencies.csv` has 292 (run, agent) classification
  rows, matching the ``N=292`` in the Figure~4 caption.
- Every condition name used in either CSV is a known paper condition.
- `paper/main.tex` never regresses to stale corpus sizes like "29 runs" or
  "19 runs".
- The taxonomy CSV exposes exactly the 5 scored categories named in the
  paper abstract.

These are cheap file/text checks; no Anthropic API calls.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RUN_SUMMARY = REPO_ROOT / "paper" / "data" / "run_summary.csv"
TAXONOMY_CSV = REPO_ROOT / "paper" / "data" / "taxonomy_frequencies.csv"
TRANSCRIPT_SCAN_MD = REPO_ROOT / "paper" / "data" / "transcript_scan.md"
MAIN_TEX = REPO_ROOT / "paper" / "main.tex"
AUX_CHANNEL_ABLATION = REPO_ROOT / "paper" / "data" / "aux_channel_ablation.md"
FINAL_FRAMING_DOCS = [
    REPO_ROOT / "paper" / "data" / "chip_vs_trueskill.md",
    REPO_ROOT / "paper" / "data" / "op47_findings.md",
]
REQUIRED_REVIEWER_ARTIFACTS = [
    REPO_ROOT / "paper" / "data" / "settlement_examples.md",
    REPO_ROOT / "paper" / "data" / "inequality_triangulation.md",
    REPO_ROOT / "paper" / "data" / "throughput_normalization.md",
    REPO_ROOT / "paper" / "data" / "coordination_power.md",
    REPO_ROOT / "paper" / "data" / "confounded_additive_diagnostic.md",
    REPO_ROOT / "paper" / "data" / "laddering_robustness.md",
    REPO_ROOT / "paper" / "data" / "aux_channel_ablation.md",
]

EXPECTED_RUN_ROWS = 39
EXPECTED_CONDITION_GROUPS = 15
EXPECTED_TAXONOMY_ROWS = 292

# These are the condition labels currently produced by
# scripts/make_figures.py. A drift here usually means a rename that hasn't
# been propagated into the figure-generator's condition mapping.
KNOWN_CONDITIONS = {
    "A1 (no mkt)",
    "Additive (confounded)",
    "Adversarial",
    "B1 (full env)",
    "Bad actor",
    "Clean additive",
    "Competitive",
    "Hetero (full)",
    "Hetero + additive",
    "Hetero baseline",
    "Hetero Opus47/Sonnet46",
    "Homo GPT",
    "Homo Opus 4.7 B1",
    "No reviews",
    "No reviews + additive",
}

# The 5 taxonomy categories that must be present as columns and named
# in the paper abstract.
TAXONOMY_CATEGORIES = {
    "competitive_coding",
    "marketplace_exploitation",
    "social_influence",
    "information_exploitation",
    "collusion",
}


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def _coordination_scan_total_blocks() -> int:
    total = 0
    in_coord_section = False
    for line in TRANSCRIPT_SCAN_MD.read_text().splitlines():
        if line.startswith("## 1. Same-model coordination null test"):
            in_coord_section = True
            continue
        if in_coord_section and line.startswith("## "):
            break
        if in_coord_section and line.startswith("| ") and not line.startswith("| condition") and not line.startswith("|---"):
            parts = [p.strip() for p in line.strip("|").split("|")]
            total += int(parts[3])
    return total


def test_run_summary_row_count() -> None:
    rows = _read_csv(RUN_SUMMARY)
    assert len(rows) == EXPECTED_RUN_ROWS, (
        f"run_summary.csv has {len(rows)} rows, expected "
        f"{EXPECTED_RUN_ROWS} paper-grade runs"
    )


def test_run_summary_condition_groups() -> None:
    rows = _read_csv(RUN_SUMMARY)
    groups = {r["condition"] for r in rows}
    assert len(groups) == EXPECTED_CONDITION_GROUPS, (
        f"run_summary.csv has {len(groups)} condition groups, "
        f"expected {EXPECTED_CONDITION_GROUPS}: {sorted(groups)}"
    )


def test_run_summary_conditions_are_known() -> None:
    rows = _read_csv(RUN_SUMMARY)
    groups = {r["condition"] for r in rows}
    unknown = groups - KNOWN_CONDITIONS
    assert not unknown, (
        f"run_summary.csv has unknown condition labels: {sorted(unknown)}. "
        "Update KNOWN_CONDITIONS in this test (and the figure caption) if "
        "this is intentional."
    )


def test_taxonomy_row_count() -> None:
    rows = _read_csv(TAXONOMY_CSV)
    assert len(rows) == EXPECTED_TAXONOMY_ROWS, (
        f"taxonomy_frequencies.csv has {len(rows)} rows, expected "
        f"{EXPECTED_TAXONOMY_ROWS} (run, agent) classifications"
    )


def test_taxonomy_columns_present() -> None:
    rows = _read_csv(TAXONOMY_CSV)
    assert rows, "taxonomy_frequencies.csv is empty"
    cols = set(rows[0].keys())
    missing = TAXONOMY_CATEGORIES - cols
    assert not missing, (
        f"taxonomy_frequencies.csv missing category columns: {sorted(missing)}"
    )


def test_taxonomy_scores_in_range() -> None:
    rows = _read_csv(TAXONOMY_CSV)
    for row in rows:
        for cat in TAXONOMY_CATEGORIES:
            score = float(row[cat])
            assert 0.0 <= score <= 1.0, (
                f"taxonomy score out of range for {row['run_id']}"
                f"/{row['agent_id']} {cat}={score}"
            )


def test_reviewer_artifacts_exist() -> None:
    missing = [path.name for path in REQUIRED_REVIEWER_ARTIFACTS if not path.exists()]
    assert not missing, (
        "missing reviewer-facing paper/data artifacts: "
        f"{missing}. Re-run scripts/reviewer_artifacts.py."
    )


@pytest.mark.parametrize("stale_phrase", [
    "29 paper-grade runs",
    "19 paper-grade runs",
    "24 paper-grade runs",
    "$N{=}29$ runs",
    "$N{=}19$ runs",
])
def test_main_tex_no_stale_corpus_size(stale_phrase: str) -> None:
    text = MAIN_TEX.read_text()
    assert stale_phrase not in text, (
        f"main.tex still references stale corpus size: {stale_phrase!r}. "
        "The paper corpus is 39 paper-grade runs / 292 classifications."
    )


def test_main_tex_mentions_current_corpus() -> None:
    text = MAIN_TEX.read_text()
    # Loose match — don't force an exact format string, just require that
    # at least one sentence advertises the 39-run / 292-classification headline.
    assert "39" in text and "292" in text, (
        "main.tex is missing one of the current headline numbers "
        "(39 runs, 292 classifications)"
    )


@pytest.mark.parametrize("marker", ["TO" "DO", "Place" "holder", "FIX" "ME"])
def test_main_tex_has_no_submission_markers(marker: str) -> None:
    text = MAIN_TEX.read_text()
    assert marker not in text, (
        f"main.tex still contains submission marker {marker!r}. "
        "Remove live placeholder markers before shipping."
    )


def test_figure_caption_corpus_numbers_consistent() -> None:
    """Figure 4's caption says 'N=292 classifications across 39 runs' --
    enforce that shape so a future rename of the CSV can't silently
    desync from the paper."""
    text = MAIN_TEX.read_text()
    caption_pattern = re.compile(
        r"N\{?=\}?292.*?39\s+runs", re.DOTALL
    )
    assert caption_pattern.search(text), (
        "main.tex does not contain the Figure 4 caption phrase "
        "'N=292 ... 39 runs'. Check figure caption sync."
    )


def test_hetero_full_collusion_support_count_is_described_correctly() -> None:
    run_conditions = {
        row["run_id"]: row["condition"]
        for row in _read_csv(RUN_SUMMARY)
    }
    scores = [
        float(row["collusion"])
        for row in _read_csv(TAXONOMY_CSV)
        if run_conditions.get(row["run_id"]) == "Hetero (full)"
    ]
    assert sum(score > 0 for score in scores) == 5
    assert sum(score >= 0.3 for score in scores) == 1

    text = MAIN_TEX.read_text()
    assert "five positive collusion scores" in text
    assert "one case above the paper's evidence-reporting threshold" in text
    assert r"n_{\text{nonzero}}{=}1" not in text


def test_main_tex_mentions_current_total_chat_messages() -> None:
    rows = _read_csv(RUN_SUMMARY)
    total_chat = sum(int(r["n_chat"]) for r in rows)
    text = MAIN_TEX.read_text()
    assert f"{total_chat:,}".replace(",", "{,}") in text, (
        f"main.tex is missing the current total chat-message count ({total_chat})."
    )


def test_main_tex_mentions_current_transcript_scan_total() -> None:
    total_blocks = _coordination_scan_total_blocks()
    text = MAIN_TEX.read_text()
    expected = f"{total_blocks:,}".replace(",", "{,}")
    assert expected in text, (
        "main.tex transcript-scan total drifted from paper/data/transcript_scan.md: "
        f"expected {expected}"
    )


def test_main_tex_does_not_pin_full_git_hash() -> None:
    text = MAIN_TEX.read_text()
    assert not re.search(r"\b[0-9a-f]{40}\b", text), (
        "main.tex contains a pinned 40-character git hash, which is prone to drift "
        "during submission prep. Use artifact-relative provenance wording instead."
    )


def test_main_tex_does_not_pin_repository_test_count() -> None:
    text = MAIN_TEX.read_text()
    assert not re.search(r"reports \$?\d+\$?\s+tests?\s+passing", text), (
        "main.tex contains a hardcoded repository test-count claim, which is "
        "prone to drift as guards are added. Prefer count-free wording."
    )


def test_taxonomy_validation_section_not_duplicated() -> None:
    text = MAIN_TEX.read_text()
    assert text.count(r"\section{Behavioral Taxonomy Validation}") == 1, (
        "main.tex contains a duplicated 'Behavioral Taxonomy Validation' section "
        "header. Remove the extra appendix heading."
    )


def test_aux_channel_ablation_uses_neutral_comments_label() -> None:
    text = AUX_CHANNEL_ABLATION.read_text()
    assert "chat msgs" not in text, (
        "aux_channel_ablation.md still uses the ambiguous 'chat msgs' label. "
        "Use a neutral comments label because the exported field is a public "
        "comment count, not a pure chat-only count."
    )
    assert "| comments |" in text, (
        "aux_channel_ablation.md should expose the neutral `comments` column label."
    )


@pytest.mark.parametrize("path", FINAL_FRAMING_DOCS)
def test_paper_facing_docs_avoid_final_elo_phrase(path: Path) -> None:
    text = path.read_text()
    assert "final ELO" not in text, (
        f"{path.name} still uses stale 'final ELO' terminology. Prefer "
        "display-rating wording in paper-facing docs."
    )
