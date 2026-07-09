"""Price distribution + offer-quality analysis under additive vs net settlement.

Reviewer Q3 + Q7: do agents endogenously adjust posted prices under additive
to target bonus extraction? Do reviews or other accountability signals mediate
offer quality under additive?

Compares homogeneous-Claude 3h B1 (net) vs homogeneous-Claude 3h Clean Additive
using offers already parsed by analysis.loader. For each condition pooled
over replications we report:

- Posted-price distribution (percentiles): does additive push prices up?
- Offers per seller-hour: volume shift beyond purchase-count change
- Fraction of offers with zero reviews (accountability cold-start)
- Fraction of offers that are ever purchased
- Mean review count per offer

Output: paper/data/additive_price_quality.md

Usage:
    uv run python scripts/additive_price_quality.py
"""

from __future__ import annotations

import csv
import statistics
from collections import defaultdict
from pathlib import Path

from analysis.loader import load_run_dir
from analysis.paper_runs import PAPER_RUNS

DATA_DIR = Path(".goa_data/runs")
OUT_MD = Path("paper/data/additive_price_quality.md")
RUN_SUMMARY = Path("paper/data/run_summary.csv")

# Homogeneous-Claude 3h cells only — hold population + horizon fixed so
# the only difference between cells is settlement_mode.
NET_RUNS = [r.run_id for r in PAPER_RUNS
            if r.condition == "B1 (full env)" and r.duration_hours == 3.0]
ADDITIVE_RUNS = [r.run_id for r in PAPER_RUNS
                 if r.condition == "Clean additive" and r.duration_hours == 3.0]


def expected_offer_counts(run_ids: list[str]) -> dict[str, int]:
    """Authoritative offer counts from run_summary.csv.

    Local exports historically undercounted offers because the Convex query
    defaulted to 50 rows. Refuse to analyze capped exports.
    """
    wanted = set(run_ids)
    counts: dict[str, int] = {}
    with RUN_SUMMARY.open(newline="") as fh:
        for row in csv.DictReader(fh):
            rid = row.get("run_id", "")
            if rid in wanted:
                counts[rid] = int(row["n_offers"])
    missing = sorted(wanted - set(counts))
    if missing:
        raise RuntimeError(f"run_summary.csv missing authoritative offer counts for: {missing}")
    return counts


def duration_hours(run_config: dict) -> float:
    if "duration_minutes" in run_config:
        return float(run_config["duration_minutes"]) / 60.0
    if "duration_hours" in run_config:
        return float(run_config["duration_hours"])
    cfg_dur = run_config.get("duration", {})
    if isinstance(cfg_dur, dict) and "hours" in cfg_dur:
        return float(cfg_dur["hours"])
    return 3.0


def analyze_condition(run_ids: list[str], label: str) -> dict:
    """Pool offers across runs in a condition; return summary dict."""
    expected_counts = expected_offer_counts(run_ids)
    offers_prices: list[float] = []
    offers_review_counts: list[int] = []
    offers_per_seller_hour: list[float] = []
    n_offers_total = 0
    n_offers_zero_reviews = 0
    n_offers_purchased = 0
    n_runs_used = 0
    count_mismatches: list[str] = []

    for rid in run_ids:
        try:
            run = load_run_dir(DATA_DIR, rid)
        except FileNotFoundError:
            print(f"  skip {rid}: export missing")
            continue

        n_runs_used += 1
        offers = run.offers
        expected_n_offers = expected_counts[rid]
        if len(offers) != expected_n_offers:
            count_mismatches.append(f"{rid}: export has {len(offers)} offers, run_summary.csv has {expected_n_offers}")
        purchase_offer_ids = {p.offer_id for p in run.purchases}
        dur_hours = duration_hours(run.config)

        # Per-seller offer count for rate normalization.
        seller_counts: dict[str, int] = defaultdict(int)
        for o in offers:
            offers_prices.append(o.price_pct)
            offers_review_counts.append(o.review_count)
            n_offers_total += 1
            if o.review_count == 0:
                n_offers_zero_reviews += 1
            if o.offer_id in purchase_offer_ids:
                n_offers_purchased += 1
            seller_counts[o.seller_agent_id] += 1

        for _seller, count in seller_counts.items():
            offers_per_seller_hour.append(count / dur_hours)

    if n_runs_used != len(run_ids):
        raise RuntimeError(f"{label}: only {n_runs_used}/{len(run_ids)} run exports were available")
    if count_mismatches:
        joined = "\n  - ".join(count_mismatches)
        raise RuntimeError(
            f"{label}: refusing to analyze capped/stale offer exports:\n  - {joined}\n"
            "Resolve the run_summary.csv/export mismatch before regenerating this artifact."
        )

    def pct(xs: list[float], q: float) -> float:
        if not xs:
            return float("nan")
        s = sorted(xs)
        k = int(q * (len(s) - 1))
        return s[k]

    return {
        "label": label,
        "n_runs": n_runs_used,
        "n_offers": n_offers_total,
        "price_mean": statistics.fmean(offers_prices) if offers_prices else float("nan"),
        "price_median": pct(offers_prices, 0.5),
        "price_p25": pct(offers_prices, 0.25),
        "price_p75": pct(offers_prices, 0.75),
        "price_p90": pct(offers_prices, 0.90),
        "price_p10": pct(offers_prices, 0.10),
        "review_mean": statistics.fmean(offers_review_counts) if offers_review_counts else float("nan"),
        "offers_per_seller_hour_mean": (
            statistics.fmean(offers_per_seller_hour) if offers_per_seller_hour else float("nan")
        ),
        "zero_review_rate": (
            n_offers_zero_reviews / n_offers_total if n_offers_total else float("nan")
        ),
        "purchase_rate": (
            n_offers_purchased / n_offers_total if n_offers_total else float("nan")
        ),
    }


def fmt(v: float, prec: int = 2) -> str:
    if v != v:  # nan
        return "—"
    return f"{v:.{prec}f}"


def main() -> int:
    print(f"NET_RUNS ({len(NET_RUNS)}): {NET_RUNS}")
    print(f"ADDITIVE_RUNS ({len(ADDITIVE_RUNS)}): {ADDITIVE_RUNS}")

    net = analyze_condition(NET_RUNS, "net (B1, 3h)")
    add = analyze_condition(ADDITIVE_RUNS, "clean additive (3h)")

    lines = [
        "# Posted-price distribution and offer quality — additive vs net",
        "",
        "Addresses reviewer Q3/Q7: do agents endogenously raise prices under "
        "additive settlement to target bonus extraction, and how does offer "
        "quality (proxied by review count and purchase probability) shift?",
        "",
        "Population + horizon held fixed (homogeneous Claude Sonnet 4.6, 3 h). "
        "Only settlement mode differs. All offers from the corpus runs in the "
        "cell are pooled.",
        "",
        f"- net cell: {net['n_runs']} runs, {net['n_offers']} offers",
        f"- clean additive cell: {add['n_runs']} runs, {add['n_offers']} offers",
        "",
        "## Posted-price distribution (% of buyer score)",
        "",
        "| Settlement | n offers | mean | p10 | p25 | median | p75 | p90 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        (
            f"| {net['label']} | {net['n_offers']} | "
            f"{fmt(net['price_mean'])} | {fmt(net['price_p10'])} | {fmt(net['price_p25'])} | "
            f"{fmt(net['price_median'])} | {fmt(net['price_p75'])} | {fmt(net['price_p90'])} |"
        ),
        (
            f"| {add['label']} | {add['n_offers']} | "
            f"{fmt(add['price_mean'])} | {fmt(add['price_p10'])} | {fmt(add['price_p25'])} | "
            f"{fmt(add['price_median'])} | {fmt(add['price_p75'])} | {fmt(add['price_p90'])} |"
        ),
        "",
        "## Volume and accountability",
        "",
        "| Settlement | offers per seller-hour | mean reviews / offer | offers with 0 reviews | offers ever purchased |",
        "|---|---:|---:|---:|---:|",
        (
            f"| {net['label']} | {fmt(net['offers_per_seller_hour_mean'])} | "
            f"{fmt(net['review_mean'])} | {fmt(net['zero_review_rate']*100, 1)}% | "
            f"{fmt(net['purchase_rate']*100, 1)}% |"
        ),
        (
            f"| {add['label']} | {fmt(add['offers_per_seller_hour_mean'])} | "
            f"{fmt(add['review_mean'])} | {fmt(add['zero_review_rate']*100, 1)}% | "
            f"{fmt(add['purchase_rate']*100, 1)}% |"
        ),
        "",
        "## Reading",
        "",
        "If the additive median / p75 price is similar to the net cell, the "
        "volume increase under additive (2.6× purchases) is not being "
        "achieved by sellers posting higher headline prices to extract bonus; "
        "it is being achieved by buyers paying more often at roughly the "
        "same price distribution. A shift in offers-per-seller-hour without "
        "a review-count collapse would indicate supply growth with "
        "accountability largely intact; a collapse to zero-review offers "
        "would indicate review signals failing to keep up with supply.",
        "",
        f"Reproduce: `uv run python scripts/additive_price_quality.py`.",
    ]

    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(lines) + "\n")
    print(f"Wrote {OUT_MD}")
    print()
    for line in lines[7:]:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
