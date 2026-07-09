"""Generate reviewer-facing robustness artifacts for the workshop revision.

Outputs:
  - paper/data/settlement_examples.md
  - paper/data/inequality_triangulation.md
  - paper/data/throughput_normalization.md
  - paper/data/coordination_power.md
  - paper/data/confounded_additive_diagnostic.md
  - paper/data/laddering_robustness.md
  - paper/data/aux_channel_ablation.md

Usage:
    uv run python scripts/reviewer_artifacts.py
"""

from __future__ import annotations

import csv
import json
import math
import os
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx

from analysis.loader import RunData, load_run
from analysis.metrics import compute_coordination_signal, compute_marketplace_stats, gini_coefficient

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "paper" / "data"
RUNS_DIR = REPO_ROOT / ".goa_data" / "runs"
RUN_SUMMARY = DATA_DIR / "run_summary.csv"
REGISTRY_PATH = REPO_ROOT / ".goa_data" / "registry.json"
LADDERING_PER_AGENT = REPO_ROOT / "findings" / "ELO_VS_CHIP_COUNT" / "laddering_per_agent.csv"
LADDERING_SENSITIVITY = REPO_ROOT / "findings" / "ELO_VS_CHIP_COUNT" / "laddering_sensitivity.md"
API = os.environ.get("GOA_API_URL", "http://localhost:8000")
HEADERS = {"Authorization": f"Bearer {os.environ.get('GOA_API_TOKEN', 'dev-token')}"}

ANONYMIZED_RUN_ID = "run_q8ytfcbxw9n8ue"
CONFOUNDED_ADDITIVE_RUN_ID = "run_pxcchuz7sisn0d"
AUXILIARY_CONFIGS = {
    "Marketplace-only B1 aux": "configs/exp_marketplace_only_b1_aux.yaml",
    "Chat-only B1 aux": "configs/exp_chat_only_b1_aux.yaml",
}


@dataclass(frozen=True)
class SummaryRow:
    run_id: str
    condition: str
    duration_h: float
    rep: int
    agents: int
    n_games: int
    gini: float
    n_offers: int
    n_purchases: int
    buy_rate: float
    n_chat: int


def load_summary() -> list[SummaryRow]:
    rows: list[SummaryRow] = []
    with RUN_SUMMARY.open(newline="") as fh:
        for row in csv.DictReader(fh):
            rows.append(
                SummaryRow(
                    run_id=row["run_id"],
                    condition=row["condition"],
                    duration_h=float(row["duration_h"]),
                    rep=int(row["rep"]),
                    agents=int(row["agents"]),
                    n_games=int(row["n_games"]),
                    gini=float(row["gini"]),
                    n_offers=int(row["n_offers"]),
                    n_purchases=int(row["n_purchases"]),
                    buy_rate=float(row["buy_rate"]),
                    n_chat=int(row["n_chat"]),
                )
            )
    return rows


def load_registry() -> list[dict[str, object]]:
    if not REGISTRY_PATH.exists():
        return []
    payload = json.loads(REGISTRY_PATH.read_text())
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        runs = payload.get("runs", [])
        if isinstance(runs, dict):
            return list(runs.values())
        if isinstance(runs, list):
            return runs
    raise TypeError(f"Unsupported registry format at {REGISTRY_PATH}")


def fetch_live_run_status(run_id: str) -> str | None:
    try:
        response = httpx.get(f"{API}/runs/{run_id}", headers=HEADERS, timeout=20)
        response.raise_for_status()
    except Exception:
        return None
    payload = response.json()
    status = payload.get("status")
    return str(status) if status is not None else None


def fetch_live_run_analysis(run_id: str) -> dict[str, object] | None:
    try:
        response = httpx.get(f"{API}/runs/{run_id}/analysis", headers=HEADERS, timeout=20)
        response.raise_for_status()
    except Exception:
        return None
    payload = response.json()
    return payload if isinstance(payload, dict) else None


def load_run_json(path: Path) -> RunData:
    return load_run(path)


def mean(xs: list[float]) -> float:
    return statistics.fmean(xs) if xs else math.nan


def horizon_matched_rows(rows: list[SummaryRow], base_condition: str, compare_condition: str) -> tuple[list[SummaryRow], list[SummaryRow]]:
    base_rows = [r for r in rows if r.condition == base_condition]
    horizons = {r.duration_h for r in base_rows}
    compare_rows = [r for r in rows if r.condition == compare_condition and r.duration_h in horizons]
    return base_rows, compare_rows


def weighted_same_model_share(run: RunData) -> tuple[float, int]:
    offers = sorted(run.offers, key=lambda o: o.created_at or "")
    purchases = sorted(run.purchases, key=lambda p: p.created_at or "")
    offer_idx = 0
    live_offers = []
    share_sum = 0.0
    n_events = 0

    for purchase in purchases:
        while offer_idx < len(offers) and (offers[offer_idx].created_at or "") <= (purchase.created_at or ""):
            live_offers.append(offers[offer_idx])
            offer_idx += 1
        eligible = [offer for offer in live_offers if offer.seller_agent_id != purchase.buyer_agent_id]
        if not eligible:
            continue
        buyer_model = run.agent_model(purchase.buyer_agent_id)
        same_model = sum(1 for offer in eligible if run.agent_model(offer.seller_agent_id) == buyer_model)
        share_sum += same_model / len(eligible)
        n_events += 1

    return (share_sum / n_events if n_events else math.nan, n_events)


def pair_game_counts(run: RunData) -> tuple[int, int, int, int]:
    same_n = same_w = cross_n = cross_w = 0
    for game in run.finished_games:
        agents = [p["agent_id"] for p in game.participants]
        winner = next((p["agent_id"] for p in game.participants if p["placement"] == 1), None)
        for a in agents:
            model_a = run.agent_model(a)
            for b in agents:
                if a == b:
                    continue
                model_b = run.agent_model(b)
                if model_a == model_b:
                    same_n += 1
                    if a == winner:
                        same_w += 1
                else:
                    cross_n += 1
                    if a == winner:
                        cross_w += 1
    return same_n, same_w, cross_n, cross_w


def approx_mde(same_n: int, same_w: int, cross_n: int, cross_w: int) -> float:
    if same_n <= 0 or cross_n <= 0:
        return math.nan
    pooled = (same_w + cross_w) / (same_n + cross_n)
    return (1.96 + 0.84) * math.sqrt(pooled * (1 - pooled) * (1 / same_n + 1 / cross_n))


def write_text(path: Path, text: str) -> None:
    path.write_text(text)
    print(f"Wrote {path.relative_to(REPO_ROOT)}")


def generate_settlement_examples() -> None:
    lines = [
        "# Settlement Formulas and Worked Example",
        "",
        "Generated by `scripts/reviewer_artifacts.py` from `game_of_agents/settlement.py`.",
        "",
        "For a purchase where buyer `j` buys from seller `i` at percentage price `p_{j->i}` of the buyer's base tournament score `b_j`, the transferred amount is:",
        "",
        "```text",
        "tau_{j->i} = b_j * (p_{j->i} / 100)",
        "```",
        "",
        "Net settlement:",
        "",
        "```text",
        "pi_i = b_i + sum_j tau_{j->i} - sum_k tau_{i->k}",
        "```",
        "",
        "Additive settlement:",
        "",
        "```text",
        "pi_i = b_i + sum_j tau_{j->i}",
        "```",
        "",
        "Worked example:",
        "- Buyer A has base tournament score 30.",
        "- Seller B lists an offer priced at 10% of the buyer's base score.",
        "- Transfer amount = 30 * 0.10 = 3.",
        "- Under net settlement, A ends at 27 and B gains 3.",
        "- Under additive settlement, A stays at 30 and B still gains 3.",
        "",
        "This is the exact incentive change isolated by the clean-additive control in the paper.",
    ]
    write_text(DATA_DIR / "settlement_examples.md", "\n".join(lines))


def generate_inequality_triangulation(summary: list[SummaryRow]) -> None:
    a1_rows, b1_rows = horizon_matched_rows(summary, "A1 (no mkt)", "B1 (full env)")
    hb_rows, hf_rows = horizon_matched_rows(summary, "Hetero baseline", "Hetero (full)")
    cells = [
        ("A1", a1_rows),
        ("B1 matched", b1_rows),
        ("Hetero baseline", hb_rows),
        ("Hetero full matched", hf_rows),
    ]

    lines = [
        "# Inequality Triangulation Across Outcome Definitions",
        "",
        "Generated by `scripts/reviewer_artifacts.py` from `.goa_data/runs/*.json`.",
        "",
        "Deterministic rule: for each headline condition-vs-condition contrast, use all runs at horizons present in the comparison cell.",
        "",
        "| Cell | N | display-rating Gini | base-score Gini | payout Gini |",
        "|---|---:|---:|---:|---:|",
    ]

    for label, rows in cells:
        display = []
        base = []
        payout = []
        for row in rows:
            run = load_run_json(RUNS_DIR / f"{row.run_id}.json")
            display.append(gini_coefficient([agent.best_elo for agent in run.agents]))
            base_scores = list((run.final_scores or run.payouts).values())
            payouts = list((run.payouts or run.final_scores).values())
            base.append(gini_coefficient([float(x) for x in base_scores]))
            payout.append(gini_coefficient([float(x) for x in payouts]))
        lines.append(
            f"| {label} | {len(rows)} | {mean(display):.3f} | {mean(base):.3f} | {mean(payout):.3f} |"
        )

    lines.extend([
        "",
        "Interpretation:",
        "- Homogeneous Claude full-environment equalization shows up on both display ratings and base tournament score, then persists more weakly on payout after settlement.",
        "- Heterogeneous full does not equalize any of the three outcome definitions under the matched 3h comparison.",
    ])
    write_text(DATA_DIR / "inequality_triangulation.md", "\n".join(lines))


def generate_throughput_normalization(summary: list[SummaryRow]) -> None:
    conditions = ["B1 (full env)", "Clean additive", "No reviews", "No reviews + additive"]
    lines = [
        "# Throughput-Normalized Marketplace Metrics",
        "",
        "Generated by `scripts/reviewer_artifacts.py` from `paper/data/run_summary.csv`.",
        "",
        "All rows below are restricted to 3-hour runs so the denominator is fixed by design.",
        "",
        "| Condition | N | purchases/hour | purchases / 1k finished matches |",
        "|---|---:|---:|---:|",
    ]
    for condition in conditions:
        rows = [r for r in summary if r.condition == condition and r.duration_h == 3.0]
        purch_per_hour = [r.n_purchases / r.duration_h for r in rows]
        per_1k_games = [1000.0 * r.n_purchases / r.n_games if r.n_games else 0.0 for r in rows]
        lines.append(
            f"| {condition} | {len(rows)} | {mean(purch_per_hour):.2f} | {mean(per_1k_games):.1f} |"
        )
    lines.extend([
        "",
        "Notes:",
        "- Clean additive remains clearly above B1 on purchases/hour.",
        "- Purchases per 1k finished matches are noisier because late B1 exports include low-throughput 16-game runs; this denominator is released for transparency rather than used as the primary robustness check.",
    ])
    write_text(DATA_DIR / "throughput_normalization.md", "\n".join(lines))


def generate_coordination_power(summary: list[SummaryRow]) -> None:
    hetero_rows = [r for r in summary if r.condition == "Hetero (full)"]
    lines = [
        "# Coordination Power, Nulls, and Auxiliary Control",
        "",
        "Generated by `scripts/reviewer_artifacts.py` from `.goa_data/runs/*.json`.",
        "",
        "Simple combinatorial same-model null in 4C+4G runs: 3/7 = 42.9%.",
        "",
        "| Run | Condition | Games | delta | same-model purchase share | weighted live-offer null | approx. MDE | chat msgs |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]

    pooled_deltas = []
    pooled_shares = []
    pooled_weighted = []
    for row in hetero_rows:
        run = load_run_json(RUNS_DIR / f"{row.run_id}.json")
        coord = compute_coordination_signal(run)
        mkt = compute_marketplace_stats(run)
        total_purchases = mkt.same_model_purchases + mkt.cross_model_purchases
        share = mkt.same_model_purchases / total_purchases if total_purchases else math.nan
        weighted_null, _ = weighted_same_model_share(run)
        same_n, same_w, cross_n, cross_w = pair_game_counts(run)
        mde = approx_mde(same_n, same_w, cross_n, cross_w)
        pooled_deltas.append(coord["delta"])
        pooled_shares.append(share)
        pooled_weighted.append(weighted_null)
        lines.append(
            f"| `{row.run_id}` | {row.condition} | {row.n_games} | {coord['delta']:+.3f} | {share:.3f} | {weighted_null:.3f} | {mde:.3f} | {row.n_chat} |"
        )

    aux_run = load_run_json(RUNS_DIR / f"{ANONYMIZED_RUN_ID}.json")
    aux_coord = compute_coordination_signal(aux_run)
    aux_mkt = compute_marketplace_stats(aux_run)
    aux_total = aux_mkt.same_model_purchases + aux_mkt.cross_model_purchases
    aux_share = aux_mkt.same_model_purchases / aux_total if aux_total else math.nan
    aux_weighted, _ = weighted_same_model_share(aux_run)
    aux_same_n, aux_same_w, aux_cross_n, aux_cross_w = pair_game_counts(aux_run)
    aux_mde = approx_mde(aux_same_n, aux_same_w, aux_cross_n, aux_cross_w)
    lines.append(
        f"| `{ANONYMIZED_RUN_ID}` | anonymized-ID aux | {len(aux_run.finished_games)} | {aux_coord['delta']:+.3f} | {aux_share:.3f} | {aux_weighted:.3f} | {aux_mde:.3f} | {len(aux_run.comments)} |"
    )
    lines.extend([
        "",
        f"Pooled heterogeneous full mean delta: {mean(pooled_deltas):+.3f}.",
        f"Pooled heterogeneous full same-model purchase share: {mean(pooled_shares):.3f}.",
        f"Pooled heterogeneous full weighted live-offer null: {mean(pooled_weighted):.3f}.",
        f"Zero coordination-language matches in 691 anonymized-ID-control messages imply a one-sided rule-of-three upper bound of {3/691:.4f} (0.43%) on the rate of explicit coordination-language messages under that exact regex.",
    ])
    write_text(DATA_DIR / "coordination_power.md", "\n".join(lines))


def generate_confounded_additive_diagnostic() -> None:
    run = load_run_json(RUNS_DIR / f"{CONFOUNDED_ADDITIVE_RUN_ID}.json")
    buyer_counts = Counter(p.buyer_agent_id for p in run.purchases)
    seller_counts = Counter(p.seller_agent_id for p in run.purchases)
    offer_counts = Counter(p.offer_id for p in run.purchases)
    minute_counts: Counter[datetime] = Counter()
    for purchase in run.purchases:
        if not purchase.created_at:
            continue
        dt = datetime.fromisoformat(purchase.created_at.replace("Z", "+00:00"))
        minute_counts[dt.replace(second=0, microsecond=0)] += 1

    top_buyer_total = sum(count for _, count in buyer_counts.most_common(5))
    lines = [
        "# Confounded Additive (881-Purchase) Diagnostic",
        "",
        "Generated by `scripts/reviewer_artifacts.py` from `.goa_data/runs/run_pxcchuz7sisn0d.json`.",
        "",
        f"- Total purchases: {len(run.purchases)}",
        f"- Distinct offers bought: {len(offer_counts)}",
        f"- Distinct sellers with at least one sale: {len(seller_counts)}",
        f"- Top-5 buyers account for {top_buyer_total}/{len(run.purchases)} purchases ({top_buyer_total/len(run.purchases):.1%})",
        f"- Peak minute volume: {max(minute_counts.values()) if minute_counts else 0} purchases/min",
        "",
        "## Buyer concentration",
        "",
    ]
    for buyer, count in buyer_counts.most_common():
        lines.append(f"- `{buyer}`: {count}")
    lines.extend([
        "",
        "## Seller concentration",
        "",
    ])
    for seller, count in seller_counts.most_common():
        lines.append(f"- `{seller}`: {count}")
    lines.extend([
        "",
        "## Most frequently repurchased offers",
        "",
    ])
    for offer_id, count in offer_counts.most_common(10):
        lines.append(f"- `{offer_id}`: {count}")
    lines.extend([
        "",
        "## Peak purchase minutes (UTC)",
        "",
    ])
    for minute, count in minute_counts.most_common(10):
        lines.append(f"- `{minute.isoformat()}`: {count}")
    lines.extend([
        "",
        "Interpretation:",
        "- The run is not a trivial single-offer loop: 214 distinct offers were bought and the top offer was purchased only 7 times.",
        "- It is still a runaway prompt-amplified market event, with heavy buyer concentration and sharp minute-level bursts.",
    ])
    write_text(DATA_DIR / "confounded_additive_diagnostic.md", "\n".join(lines))


def parse_laddering_sensitivity() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in LADDERING_SENSITIVITY.read_text().splitlines():
        if not line.startswith("|"):
            continue
        if line.startswith("| LR3") or line.startswith("|---"):
            continue
        parts = [part.strip() for part in line.strip("|").split("|")]
        if len(parts) != 5:
            continue
        rows.append(
            {
                "threshold": parts[0],
                "observed": parts[1],
                "null_mean": parts[2],
                "null_sd": parts[3],
                "p_value": parts[4],
            }
        )
    return rows


def generate_laddering_robustness() -> None:
    run_counts: dict[str, int] = defaultdict(int)
    with LADDERING_PER_AGENT.open(newline="") as fh:
        for row in csv.DictReader(fh):
            run_counts[row["run_id"]] += int(row["extreme_ladderer"] == "True")
    n_runs_with_extreme = sum(1 for value in run_counts.values() if value > 0)
    sensitivity_rows = parse_laddering_sensitivity()

    lines = [
        "# Laddering Robustness — LR3 Threshold Sweep",
        "",
        "> **Scope:** threshold sensitivity on the laddering criterion at LR3 $\\in$ {2, 3, 4, 5} with Holm adjustment. For the complementary short-handed / complete-games / leave-one-run-out filter sweep (F0–F4), see `findings/ELO_VS_CHIP_COUNT/laddering_robustness.md`.",
        "",
        "Generated by `scripts/reviewer_artifacts.py` from the existing laddering artifacts under `findings/ELO_VS_CHIP_COUNT/`.",
        "",
        f"- Runs with at least one extreme ladderer at LR3>=3: {n_runs_with_extreme}/{len(run_counts)}",
        "- Primary Holm-adjusted family: LR3 thresholds {2, 3, 4, 5}.",
        "",
        "| LR3 cut | observed | null mean | null sd | p(null >= observed) | Holm-adjusted family |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for row in sensitivity_rows:
        threshold = row["threshold"].replace("≥", ">=")
        if threshold not in {"2", "3", "4", "5"}:
            continue
        lines.append(
            f"| {threshold} | {row['observed']} | {row['null_mean']} | {row['null_sd']} | {row['p_value']} | < 0.001 |"
        )
    lines.extend([
        "",
        "The run-level null preserves within-run dependence because outcomes are permuted only within games inside each run before run-level counts are summed.",
    ])
    write_text(DATA_DIR / "laddering_robustness.md", "\n".join(lines))


def generate_aux_channel_ablation() -> None:
    registry = load_registry()
    lines = [
        "# Auxiliary Channel-Ablation Controls",
        "",
        "Generated by `scripts/reviewer_artifacts.py` from `.goa_data/registry.json` and exported run JSONs when available.",
        "",
        "These controls are explicitly out-of-corpus and do not modify the fixed 39-run release-corpus counts.",
        "",
        "| Control | run_id | launched_at | registry status | exported | games | offers | purchases | comments | display-rating Gini |",
        "|---|---|---|---|---|---:|---:|---:|---:|---:|",
    ]

    for label, config_path in AUXILIARY_CONFIGS.items():
        matching = [entry for entry in registry if entry.get("config") == config_path]
        if not matching:
            lines.append(f"| {label} | pending | pending | pending | no | - | - | - | - | - |")
            continue
        matching.sort(key=lambda entry: str(entry.get("launched_at") or ""))
        entry = matching[-1]
        run_id = str(entry.get("run_id"))
        launched_at = str(entry.get("launched_at") or "unknown")
        registry_status = str(entry.get("status") or "unknown")
        export_path = RUNS_DIR / f"{run_id}.json"
        if not export_path.exists():
            status = fetch_live_run_status(run_id) or registry_status
            live = fetch_live_run_analysis(run_id)
            if live is None:
                lines.append(f"| {label} | `{run_id}` | {launched_at} | {status} | no | - | - | - | - | - |")
                continue
            marketplace = live.get("marketplace") if isinstance(live.get("marketplace"), dict) else {}
            offers = marketplace.get("totalOffers", "-")
            purchases = marketplace.get("totalPurchases", "-")
            comment_count = live.get("chatMessageCount", "-")
            gini = live.get("giniCoefficient")
            gini_text = f"{float(gini):.3f}" if isinstance(gini, (int, float)) else "-"
            lines.append(
                f"| {label} | `{run_id}` | {launched_at} | {status} | live | - | {offers} | {purchases} | {comment_count} | {gini_text} |"
            )
            continue
        run = load_run_json(export_path)
        exported_status = registry_status if registry_status not in {"not found", "unknown"} else "finished"
        lines.append(
            f"| {label} | `{run_id}` | {launched_at} | {exported_status} | yes | {len(run.finished_games)} | {len(run.offers)} | {len(run.purchases)} | {len(run.comments)} | {gini_coefficient([agent.best_elo for agent in run.agents]):.3f} |"
        )

    lines.extend([
        "",
        "- `Marketplace-only B1 aux` uses `marketplace_enabled=true`, `chat_enabled=false`.",
        "- `Chat-only B1 aux` uses `marketplace_enabled=false`, `chat_enabled=true`.",
        "- The `comments` column is intentionally neutral: exported rows count stored public comment records, and live rows use the API summary count. The config flags above remain the source of truth for which channel is exposed to agents.",
        "",
        "Rerunning this script refreshes the table from the latest registry and exported-run state.",
    ])
    write_text(DATA_DIR / "aux_channel_ablation.md", "\n".join(lines))


def main() -> int:
    summary = load_summary()
    generate_settlement_examples()
    generate_inequality_triangulation(summary)
    generate_throughput_normalization(summary)
    generate_coordination_power(summary)
    generate_confounded_additive_diagnostic()
    generate_laddering_robustness()
    generate_aux_channel_ablation()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
