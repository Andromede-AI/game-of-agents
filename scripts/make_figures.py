"""Generate the figures for the workshop paper.

Reads precomputed CSVs/JSONL from paper/data/ and emits PDFs into
paper/figures/. Hermetic and reproducible — re-running this script with
the same inputs produces byte-identical figures.

Figures produced (paper/figures/):
  1. fig_taxonomy_heatmap.pdf — exploratory behavioral taxonomy heatmap
  2. fig_marketplace_outcomes.pdf — purchases by condition
  3. fig_gini_comparison.pdf — final display-rating Gini by condition
  4. fig_coordination_signal.pdf — same-model vs cross-model win-rate delta

Usage:
    uv run python scripts/make_figures.py
    uv run python scripts/make_figures.py --only taxonomy
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

# Allow imports of analysis.* from repo root
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import NullFormatter  # noqa: E402
import numpy as np  # noqa: E402

from analysis.compare import _bootstrap_ci  # noqa: E402
from analysis.loader import load_run  # noqa: E402
from analysis.metrics import (  # noqa: E402
    compute_agent_stats,
    compute_coordination_signal,
    compute_marketplace_stats,
    gini_coefficient,
)
from analysis.paper_runs import (  # noqa: E402
    CONDITION_ORDER as CANON_CONDITION_ORDER,
    run_tuple_map,
)


# ── paths ─────────────────────────────────────────────────────────────────

DATA_DIR = REPO_ROOT / "paper" / "data"
FIG_DIR = REPO_ROOT / "paper" / "figures"
RUNS_DIR = REPO_ROOT / ".goa_data" / "runs"
TAXONOMY_CSV = DATA_DIR / "taxonomy_frequencies.csv"
RUN_SUMMARY_CSV = DATA_DIR / "run_summary.csv"


# ── condition labels ─────────────────────────────────────────────────────
# Canonical list lives in analysis.paper_runs — single source of truth.
PAPER_RUNS = run_tuple_map()
CONDITION_ORDER = CANON_CONDITION_ORDER

# Pretty taxonomy category labels
CATEGORY_LABELS = {
    "competitive_coding":       "Competitive\ncoding",
    "marketplace_exploitation": "Marketplace\nexploitation",
    "social_influence":         "Social\ninfluence",
    "information_exploitation": "Info\nexploitation",
    "collusion":                "Collusion",
}

CATEGORIES = list(CATEGORY_LABELS.keys())

CONDITION_DISPLAY_LABELS = {
    "A1 (no mkt)": "A1 (no market)",
    "B1 (full env)": "B1 (full env)",
    "Hetero baseline": "Hetero baseline",
    "Hetero (full)": "Hetero full",
    "Homo GPT": "Homo GPT",
    "Homo Opus 4.7 B1": "Homo Opus 4.7",
    "Hetero Opus47/Sonnet46": "Hetero Opus 4.7 / Sonnet 4.6",
    "Competitive": "Competitive",
    "Adversarial": "Adversarial",
    "No reviews": "No reviews",
    "Bad actor": "Bad actor",
    "Clean additive": "Clean additive",
    "Additive (confounded)": "Additive (confounded)",
    "No reviews + additive": "No reviews + additive",
    "Hetero + additive": "Hetero + additive",
}

GINI_BLOCKS = [
    ("Baselines", ["B1 (full env)", "A1 (no mkt)"], "#66c2a5"),
    (
        "Population variants",
        [
            "Hetero baseline",
            "Hetero (full)",
            "Homo GPT",
            "Homo Opus 4.7 B1",
            "Hetero Opus47/Sonnet46",
        ],
        "#8da0cb",
    ),
    (
        "Prompt and channel variants",
        ["Competitive", "Adversarial", "No reviews", "Bad actor"],
        "#fc8d62",
    ),
    (
        "Additive settlement variants",
        ["Clean additive", "Additive (confounded)", "No reviews + additive", "Hetero + additive"],
        "#e78ac3",
    ),
]

plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times"],
    "font.size": 9,
    "axes.titlesize": 10,
    "axes.labelsize": 9,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "legend.fontsize": 8,
    "figure.dpi": 150,
    "savefig.bbox": "tight",
    "pdf.use14corefonts": True,
})


# ── helpers ──────────────────────────────────────────────────────────────


def _save_pdf(fig, name: str) -> Path:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    path = FIG_DIR / name
    fig.savefig(path)
    plt.close(fig)
    print(f"  ✓ {path}")
    return path


def _save_pdf_and_png(fig, pdf_name: str, png_name: str, png_dpi: int = 300) -> tuple[Path, Path]:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = FIG_DIR / pdf_name
    png_path = FIG_DIR / png_name
    fig.savefig(pdf_path, bbox_inches="tight")
    fig.savefig(png_path, dpi=png_dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"  ✓ {pdf_path}")
    print(f"  ✓ {png_path}")
    return pdf_path, png_path


def _load_run(run_id: str):
    path = RUNS_DIR / f"{run_id}.json"
    if not path.exists():
        return None
    return load_run(path)


def _build_run_summary_table() -> dict:
    """Compute per-run summary metrics from local exports.

    Returns a dict keyed by run_id with: condition, duration, rep, gini,
    n_offers, n_purchases, buy_rate, n_chat, agent_count, n_games.

    Note: ``n_offers`` / ``buy_rate`` are overridden from
    ``paper/data/run_summary.csv`` when a matching row exists. The local
    export's offers dict is capped by Convex's ``listMarketplaceOffers``
    pagination (default limit=50); the CSV carries the authoritative
    totals backfilled from ``batch_summary.md``'s ``/analysis`` endpoint.
    """
    # Load CSV overrides for n_offers (authoritative) keyed by run_id
    csv_offers: dict[str, int] = {}
    csv_path = Path(__file__).resolve().parent.parent / "paper" / "data" / "run_summary.csv"
    if csv_path.exists():
        with csv_path.open() as fh:
            for row in csv.DictReader(fh):
                try:
                    csv_offers[row["run_id"]] = int(row["n_offers"])
                except (KeyError, ValueError):
                    continue

    out = {}
    for run_id, (cond, dur, rep) in PAPER_RUNS.items():
        run = _load_run(run_id)
        if run is None:
            continue
        agent_stats = compute_agent_stats(run)
        elos = [s.final_elo for s in agent_stats.values()]
        gini = gini_coefficient(elos) if elos else 0.0
        mkt = compute_marketplace_stats(run)
        n_offers = csv_offers.get(run_id, mkt.total_offers)
        n_purch = mkt.total_purchases
        buy_rate = n_purch / n_offers if n_offers else 0.0
        out[run_id] = {
            "run_id": run_id,
            "condition": cond,
            "duration_h": dur,
            "rep": rep,
            "gini": gini,
            "n_offers": n_offers,
            "n_purchases": n_purch,
            "buy_rate": buy_rate,
            "n_chat": len(run.comments),
            "agents": len(run.agents),
            "n_games": len(run.finished_games),
        }
    return out


# ── Figure 1: behavioral taxonomy heatmap ────────────────────────────────


def fig_taxonomy_heatmap() -> None:
    if not TAXONOMY_CSV.exists():
        print(f"  ⊘ taxonomy CSV missing: {TAXONOMY_CSV}")
        return
    rows = list(csv.DictReader(open(TAXONOMY_CSV)))
    if not rows:
        print("  ⊘ taxonomy CSV empty")
        return

    # Aggregate per-condition mean across all (run, agent) rows
    by_cond: dict[str, list[dict[str, float]]] = defaultdict(list)
    nonzero_counts: dict[tuple[str, str], int] = defaultdict(int)
    material_counts: dict[tuple[str, str], int] = defaultdict(int)
    for r in rows:
        cond = PAPER_RUNS.get(r["run_id"])
        if not cond:
            continue
        scores = {c: float(r[c]) for c in CATEGORIES}
        by_cond[cond[0]].append(scores)
        for cat, score in scores.items():
            if score > 0:
                nonzero_counts[(cond[0], cat)] += 1
            if score >= 0.3:
                material_counts[(cond[0], cat)] += 1

    conditions = [c for c in CONDITION_ORDER if c in by_cond]
    if not conditions:
        print("  ⊘ no taxonomy rows match known conditions")
        return

    # Mean score per (condition, category)
    mean_matrix = np.zeros((len(conditions), len(CATEGORIES)))
    for i, cond in enumerate(conditions):
        agent_rows = by_cond[cond]
        for j, cat in enumerate(CATEGORIES):
            mean_matrix[i, j] = float(np.mean([row[cat] for row in agent_rows]))

    fig, ax = plt.subplots(figsize=(6.0, 0.45 * len(conditions) + 1.4))
    im = ax.imshow(mean_matrix, cmap="viridis", vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(CATEGORIES)))
    ax.set_xticklabels([CATEGORY_LABELS[c] for c in CATEGORIES])
    ax.set_yticks(range(len(conditions)))
    ax.set_yticklabels(conditions)
    # Cell annotations
    for i in range(len(conditions)):
        for j in range(len(CATEGORIES)):
            v = mean_matrix[i, j]
            color = "white" if v < 0.5 else "black"
            cond = conditions[i]
            cat = CATEGORIES[j]
            suffix = ""
            if cond == "Hetero (full)" and cat == "collusion":
                suffix = "*"
            ax.text(j, i, f"{v:.2f}{suffix}", ha="center", va="center", color=color, fontsize=8)

    cbar = fig.colorbar(im, ax=ax, fraction=0.04, pad=0.02)
    cbar.set_label("mean score (0-1)")
    hetero_nonzero = nonzero_counts.get(("Hetero (full)", "collusion"), 0)
    hetero_material = material_counts.get(("Hetero (full)", "collusion"), 0)
    ax.text(
        0.0,
        -0.14,
        f"* Hetero-full collusion: {hetero_nonzero} positive scores; {hetero_material} material case.",
        transform=ax.transAxes,
        fontsize=8,
        ha="left",
        va="top",
    )

    _save_pdf(fig, "fig_taxonomy_heatmap.pdf")


# ── Figure 2: marketplace outcomes ───────────────────────────────────────


def fig_marketplace_outcomes(summary: dict) -> None:
    # For each condition, plot purchases only (use 3h reps preferentially).
    # Offer-level exports can be capped/stale, so avoid using offer counts in
    # this reviewer-facing figure.
    cond_to_runs: dict[str, list[dict]] = defaultdict(list)
    for r in summary.values():
        cond_to_runs[r["condition"]].append(r)

    conditions = [c for c in CONDITION_ORDER if c in cond_to_runs]
    if not conditions:
        return

    # Use 3h rep if available, else first rep
    chosen = []
    for c in conditions:
        threes = [r for r in cond_to_runs[c] if r["duration_h"] >= 3.0]
        chosen.append(threes[0] if threes else cond_to_runs[c][0])

    y = np.arange(len(conditions))
    purchases = [r["n_purchases"] for r in chosen]
    labels = [CONDITION_DISPLAY_LABELS.get(c, c) for c in conditions]

    fig, ax = plt.subplots(figsize=(4.8, 4.8))
    bars = ax.barh(y, purchases, 0.58, label="Purchases made", color="#EE6677")
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=7)
    ax.invert_yaxis()
    ax.set_xlabel("purchases per run (log-like scale)")
    ax.set_xscale("symlog", linthresh=1)
    ax.set_xticks([0, 1, 10, 100, 1000])
    ax.set_xticklabels(["0", "1", "10", "100", "1000"])
    ax.set_xlim(0, 1300)
    ax.xaxis.set_minor_formatter(NullFormatter())
    ax.grid(axis="x", linestyle=":", alpha=0.5, which="both")

    # Annotate purchase count above each bar; symlog keeps zero visible.
    for bar, value in zip(bars, purchases):
        large_value = value >= 100
        x_pos = value / 1.18 if large_value else value * 1.08 + 0.05 if value > 0 else 0.08
        ax.text(
            x_pos,
            bar.get_y() + bar.get_height() / 2,
            str(value),
            ha="right" if large_value else "left",
            va="center",
            fontsize=7,
            color="white" if large_value else "#222",
        )

    _save_pdf(fig, "fig_marketplace_outcomes.pdf")


# ── Figure 3: Gini comparison ────────────────────────────────────────────


def fig_gini_comparison(summary: dict) -> None:
    if not RUN_SUMMARY_CSV.exists():
        print(f"  ⊘ run summary CSV missing: {RUN_SUMMARY_CSV}")
        return

    rows = list(csv.DictReader(open(RUN_SUMMARY_CSV)))
    cond_to_ginis: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        try:
            cond_to_ginis[row["condition"]].append(float(row["gini"]))
        except (KeyError, ValueError):
            continue

    ordered: list[tuple[str, str, str, float]] = []
    y_positions: list[float] = []
    y = 0.0
    block_gap = 0.45
    for _, conditions, color in GINI_BLOCKS:
        for cond in conditions:
            if cond not in cond_to_ginis:
                print(f"  ⊘ missing Gini rows for condition: {cond}")
                continue
            ordered.append((cond, CONDITION_DISPLAY_LABELS.get(cond, cond), color, y))
            y_positions.append(y)
            y += 1.0
        y += block_gap

    conditions = [item[0] for item in ordered]
    labels = [
        f"{item[1]}*" if len(cond_to_ginis[item[0]]) == 1 else item[1]
        for item in ordered
    ]
    colors = [
        "#3f9a85" if item[0] == "B1 (full env)" else item[2]
        for item in ordered
    ]
    means = [float(np.mean(cond_to_ginis[c])) for c in conditions]
    n_reps = [len(cond_to_ginis[c]) for c in conditions]

    ci_pairs: list[tuple[float, float] | None] = []
    for c in conditions:
        vals = cond_to_ginis[c]
        ci_pairs.append(_bootstrap_ci(vals, n_boot=2000) if len(vals) >= 2 else None)

    fig, ax = plt.subplots(figsize=(5.2, 5.1))
    bars = ax.barh(y_positions, means, height=0.66, color=colors, edgecolor="none", alpha=0.92)

    annotation_padding = 0.0015
    for y_pos, mean, ci, n in zip(y_positions, means, ci_pairs, n_reps):
        if ci is not None:
            lo, hi = ci
            ax.errorbar(
                mean,
                y_pos,
                xerr=[[mean - lo], [hi - mean]],
                fmt="none",
                ecolor="#555555",
                elinewidth=0.8,
                capsize=2.5,
                capthick=0.8,
                zorder=3,
            )
        if n > 1:
            upper_edge = ci[1] if ci is not None else mean
            x_pos = upper_edge + annotation_padding
            ax.text(
                x_pos,
                y_pos,
                f"N={n}",
                va="center",
                ha="left",
                fontsize=7,
                color="#666666",
                bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.75, "pad": 0.4},
            )

    ax.set_yticks(y_positions)
    ax.set_yticklabels(labels, fontsize=9)
    ax.invert_yaxis()
    ax.set_xlim(0, 0.085)
    ax.set_xticks(np.arange(0, 0.081, 0.02))
    ax.set_xticklabels([f"{v:.2f}" for v in np.arange(0, 0.081, 0.02)], fontsize=9)
    ax.set_xlabel("Gini coefficient (final display rating)", fontsize=10)
    ax.grid(axis="x", linestyle=":", linewidth=0.7, color="#b7b7b7", alpha=0.75)
    ax.set_axisbelow(True)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.tick_params(axis="y", length=0)
    ax.tick_params(axis="x", labelsize=9)

    _save_pdf_and_png(fig, "fig_gini_comparison.pdf", "fig_gini_comparison.png", png_dpi=300)

    print("  Gini sanity check (paper/data/inequality_triangulation.md):")
    triangulation_path = DATA_DIR / "inequality_triangulation.md"
    if triangulation_path.exists():
        for line in triangulation_path.read_text().splitlines():
            if line.startswith("| A1 ") or line.startswith("| B1 matched ") or line.startswith("| Hetero baseline ") or line.startswith("| Hetero full matched "):
                parts = [part.strip() for part in line.strip("|").split("|")]
                print(f"    {parts[0]}: {float(parts[2]):.3f} (N={parts[1]})")
    else:
        print(f"    missing {triangulation_path}")
    print("  Plotted all-replication means:")
    for cond in ["A1 (no mkt)", "B1 (full env)", "Hetero baseline", "Hetero (full)"]:
        vals = cond_to_ginis[cond]
        print(f"    {CONDITION_DISPLAY_LABELS.get(cond, cond)}: {np.mean(vals):.3f} (N={len(vals)})")


# ── Figure 4: coordination signal ────────────────────────────────────────


def fig_coordination_signal() -> None:
    """Per-run coordination signal (dot plot) + bootstrap 95% CI on the mean
    across heterogeneous runs.

    The CI is computed by bootstrapping over the set of per-run deltas
    (treating each run as one observation), which is the cleanest
    sampling distribution we can produce without access to per-pair
    variances."""
    # Auto-discover all paper-runs labeled "Hetero (full)" so new reps
    # are picked up automatically.
    hetero_run_ids = [
        rid for rid, (cond, _, _) in PAPER_RUNS.items() if cond == "Hetero (full)"
    ]

    points = []
    for run_id in hetero_run_ids:
        run = _load_run(run_id)
        if run is None:
            continue
        sig = compute_coordination_signal(run)
        if sig is None:
            continue
        _, dur, rep = PAPER_RUNS[run_id]
        label = f"{'3h' if dur >= 3 else '60m'} rep{rep}"
        points.append({
            "run_id": run_id,
            "label": label,
            "delta": float(sig.get("delta", 0.0)),
            "same": float(sig.get("same_model_wr", 0.0)),
            "cross": float(sig.get("cross_model_wr", 0.0)),
            "n_same": int(sig.get("n_same", 0)),
            "n_cross": int(sig.get("n_cross", 0)),
            "n_games": len(run.finished_games),
        })

    if not points:
        print("  ⊘ no coordination data")
        return

    # Sort by label so multi-rep runs cluster
    points.sort(key=lambda p: p["label"])

    deltas = [p["delta"] for p in points]
    mean_delta = float(np.mean(deltas))
    ci_lo, ci_hi = _bootstrap_ci(deltas, n_boot=2000) if len(deltas) >= 2 else (mean_delta, mean_delta)

    minus = chr(0x2212)
    delta_symbol = chr(0x0394)

    def _signed_delta(value: float) -> str:
        return f"{value:+.3f}".replace("-", minus)

    def _tick_label(value: float) -> str:
        if abs(value) < 1e-12:
            return "0"
        return f"{value:.2f}".replace("-", minus)

    coord_rc = {
        "axes.unicode_minus": True,
        "font.family": "serif",
        "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
        "pdf.use14corefonts": False,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
    }
    with plt.rc_context(coord_rc):
        fig, ax = plt.subplots(figsize=(5.2, 2.7))
        y = np.arange(len(points))
        labels = [p["label"] for p in points]
        n_games = np.array([p["n_games"] for p in points], dtype=float)
        if float(n_games.max()) > float(n_games.min()):
            scaled = (np.sqrt(n_games) - np.sqrt(n_games.min())) / (np.sqrt(n_games.max()) - np.sqrt(n_games.min()))
            sizes = 45 + 70 * scaled
        else:
            sizes = np.full_like(n_games, 80.0)

        ax.axvspan(float(ci_lo), float(ci_hi), color="#4c78a8", alpha=0.20, zorder=0)
        ax.axvline(0, color="#b7b7b7", linestyle="--", linewidth=0.9, zorder=1)
        # Mean + 95% CI band across runs
        ax.axvline(mean_delta, color="#222222", linestyle="-", linewidth=1.5, zorder=2)
        ax.scatter(deltas, y, color="#4d4d4d", edgecolors="white", linewidths=0.6, s=sizes, zorder=3)
        ax.set_yticks(y)
        ax.set_yticklabels(labels, fontsize=9)
        ax.set_xlabel("same-model - cross-model win-rate delta", fontsize=10)
        ax.set_xlim(-0.15, 0.15)
        ticks = np.arange(-0.15, 0.151, 0.05)
        ax.set_xticks(ticks)
        ax.set_xticklabels([_tick_label(t) for t in ticks], fontsize=9)
        ax.grid(axis="x", linestyle=":", linewidth=0.7, color="#b7b7b7", alpha=0.75)
        ax.set_axisbelow(True)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)

        label_x = max(max(deltas), float(ci_hi)) + 0.012
        for i, p in enumerate(points):
            annot = f"{delta_symbol} = {_signed_delta(p['delta'])}"
            ax.text(label_x, i, annot, ha="left", va="center", fontsize=8, color="#666666")

        _save_pdf_and_png(fig, "fig_coordination_signal.pdf", "fig_coordination_signal.png", png_dpi=300)

    print("  Coordination sanity check (rendered top-to-bottom deltas):")
    print("    " + ", ".join(f"{p['delta']:+.3f}" for p in reversed(points)))
    print(f"    cross-run mean: {mean_delta:+.4f}")


# ── master ────────────────────────────────────────────────────────────────


def _write_summary_csv(summary: dict) -> None:
    """Persist the per-run summary table to paper/data/run_summary.csv."""
    if not summary:
        return
    out_path = DATA_DIR / "run_summary.csv"
    fieldnames = [
        "run_id",
        "condition",
        "duration_h",
        "rep",
        "agents",
        "n_games",
        "gini",
        "n_offers",
        "n_purchases",
        "buy_rate",
        "n_chat",
    ]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in summary.values():
            writer.writerow({k: r.get(k) for k in fieldnames})
    print(f"  ✓ {out_path}  ({len(summary)} runs)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["taxonomy", "marketplace", "gini", "coord", "summary"], help="Generate only one figure")
    parser.add_argument(
        "--rebuild-summary",
        action="store_true",
        help="Overwrite paper/data/run_summary.csv from local exports. The committed "
        "CSV is authoritative: some raw run exports have capped games/chat lists, and "
        "its n_games/n_chat columns were reconciled against the live API at paper time, "
        "so a rebuild from exports alone will not match it for every run.",
    )
    args = parser.parse_args()

    print("Building summary table from local exports…")
    summary = _build_run_summary_table()
    print(f"  loaded {len(summary)} runs from {RUNS_DIR}")

    print()
    print("Generating figures…")
    if args.rebuild_summary and args.only in (None, "summary"):
        _write_summary_csv(summary)
    if args.only in (None, "taxonomy"):
        fig_taxonomy_heatmap()
    if args.only in (None, "marketplace"):
        fig_marketplace_outcomes(summary)
    if args.only in (None, "gini"):
        fig_gini_comparison(summary)
    if args.only in (None, "coord"):
        fig_coordination_signal()
if __name__ == "__main__":
    main()
