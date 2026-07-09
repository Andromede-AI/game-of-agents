"""Canonical list of paper-grade runs — single source of truth.

Every script that needs to iterate over the paper corpus (figure
generation, batch analysis, taxonomy classification, chat/offer/transcript
scans, tier-1 integration, chip-game extraction) should import from here
instead of maintaining its own list.

Adding a new run
----------------
1. Append a ``PaperRun`` entry to ``PAPER_RUNS`` below.
2. If the condition is new, append it to ``CONDITION_ORDER``.
3. No other files need to change.

Schema helpers
--------------
Call sites historically used four schema variants:

- ``list[run_id]``                                 → :func:`run_ids`
- ``dict[run_id, condition]``                      → :func:`condition_map`
- ``dict[run_id, (condition, hours, rep)]``        → :func:`run_tuple_map`
- ``list[(run_id, condition, duration_str, rep)]`` → :func:`run_tuple_list`
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PaperRun:
    run_id: str
    condition: str
    duration_hours: float
    rep: int


# ── canonical corpus ────────────────────────────────────────────────────────
# Order is historical (earliest first). Figures re-order via CONDITION_ORDER.
PAPER_RUNS: list[PaperRun] = [
    # 60-min exploratory reps (Apr 7-10)
    PaperRun("run_k171het0ovc8cg", "A1 (no mkt)",             1.0, 1),
    PaperRun("run_vrtjyprxpag8qy", "A1 (no mkt)",             1.0, 2),
    PaperRun("run_vskkr3r3ov8jeq", "B1 (full env)",           1.0, 1),
    PaperRun("run_7w9i4y0spafbbj", "B1 (full env)",           1.0, 2),
    PaperRun("run_oa7r0sc1pd876i", "Hetero (full)",           1.0, 1),
    PaperRun("run_hcgt8jiaqpv1r5", "Hetero (full)",           1.0, 2),
    PaperRun("run_vn0kkhf8pd8faz", "Homo GPT",                1.0, 1),
    PaperRun("run_vrthglo8qpvabb", "Competitive",             1.0, 1),
    PaperRun("run_fml59hvpshuew6", "Adversarial",             1.0, 1),
    PaperRun("run_u7d7q3kpshuk59", "Bad actor",               1.0, 1),
    PaperRun("run_bpcpv0awsiqp15", "No reviews",              1.0, 1),
    # 3h, 8-agent definitive runs
    PaperRun("run_1dnl6vtlsiq5pb", "A1 (no mkt)",             3.0, 3),
    PaperRun("run_x32qdlx4siq1pk", "B1 (full env)",           3.0, 3),
    PaperRun("run_isj7ubddsisynd", "Hetero (full)",           3.0, 3),
    PaperRun("run_ts13lewssiqa0x", "Competitive",             3.0, 2),
    PaperRun("run_sub8j16vsiqezi", "Adversarial",             3.0, 2),
    PaperRun("run_r3drk73gsiqkoa", "Bad actor",               3.0, 2),
    PaperRun("run_5bcaq5c5skri6t", "No reviews",              3.0, 2),
    PaperRun("run_pxcchuz7sisn0d", "Additive (confounded)",   3.0, 1),
    # Tier 1 / 1.5 (Apr 10–12)
    PaperRun("run_rap9xfrst9jyzd", "Hetero baseline",         3.0, 1),
    PaperRun("run_v0ke7u5mt9k84x", "Hetero (full)",           3.0, 4),
    PaperRun("run_x62b63l5tkp3cg", "Hetero baseline",         3.0, 2),
    PaperRun("run_a4ubdvpptkpekw", "B1 (full env)",           3.0, 4),
    PaperRun("run_lj7qu06ktkpp5e", "Hetero (full)",           3.0, 5),
    # Apr 14–15: clean additive pre-batch
    PaperRun("run_57vd316xyfk16q", "Clean additive",          3.0, 1),
    PaperRun("run_kf2kp5qrz0mmsy", "Clean additive",          3.0, 2),
    # Apr 15: 6h B1
    PaperRun("run_5ejgk15qzwkqyk", "B1 (full env)",           6.0, 7),
    # Apr 16 batch
    PaperRun("run_dblkxlpy1udeei", "Clean additive",          3.0, 3),
    PaperRun("run_ruh1jenp1udfke", "Clean additive",          3.0, 4),
    PaperRun("run_yrajp77a1udd84", "B1 (full env)",           3.0, 5),
    PaperRun("run_74ks03yd1udc27", "B1 (full env)",           3.0, 6),
    PaperRun("run_dxfjtmfa1udf6a", "No reviews",              3.0, 3),
    PaperRun("run_pkploa771udfhc", "No reviews + additive",   3.0, 1),
    PaperRun("run_zaeqhzla1udfcf", "No reviews + additive",   3.0, 2),
    PaperRun("run_4mo6nm0q1udf06", "Hetero + additive",       3.0, 1),
    PaperRun("run_q4p4v3n51udcmv", "Hetero + additive",       3.0, 2),
    PaperRun("run_6ajrbhvx1uddt7", "Clean additive",          6.0, 5),
    # Apr 16 late: Opus 4.7 Andon replication probes
    PaperRun("run_uqv8d2ir21mhxq", "Homo Opus 4.7 B1",           3.0, 1),
    PaperRun("run_cpvw7kfp21n1qg", "Hetero Opus47/Sonnet46",  3.0, 1),
]


# Display order for x-axis in condition figures (cooperative → adversarial → exotic).
CONDITION_ORDER: list[str] = [
    "A1 (no mkt)",
    "B1 (full env)",
    "Hetero baseline",
    "Hetero (full)",
    "Homo GPT",
    "Homo Opus 4.7 B1",
    "Hetero Opus47/Sonnet46",
    "Competitive",
    "Adversarial",
    "No reviews",
    "Bad actor",
    "Clean additive",
    "Additive (confounded)",
    "No reviews + additive",
    "Hetero + additive",
]


# ── schema helpers ──────────────────────────────────────────────────────────

def run_ids() -> list[str]:
    """List of run IDs in canonical order."""
    return [r.run_id for r in PAPER_RUNS]


def condition_map() -> dict[str, str]:
    """{run_id: condition} — used by chat_content, offer_honesty, transcript_scan, win_source, integrate_tier1."""
    return {r.run_id: r.condition for r in PAPER_RUNS}


def run_tuple_map() -> dict[str, tuple[str, float, int]]:
    """{run_id: (condition, duration_hours, rep)} — used by make_figures."""
    return {r.run_id: (r.condition, r.duration_hours, r.rep) for r in PAPER_RUNS}


def _duration_str(hours: float) -> str:
    if hours < 2:
        return "1h"
    if hours < 5:
        return "3h"
    return "6h"


def run_tuple_list() -> list[tuple[str, str, str, int]]:
    """[(run_id, condition, duration_str, rep)] — used by extract_chip_games."""
    return [(r.run_id, r.condition, _duration_str(r.duration_hours), r.rep) for r in PAPER_RUNS]


def run_duration_minutes() -> list[tuple[str, str, int]]:
    """[(run_id, condition, duration_minutes)] — used by batch_analysis."""
    return [(r.run_id, r.condition, int(round(r.duration_hours * 60))) for r in PAPER_RUNS]
