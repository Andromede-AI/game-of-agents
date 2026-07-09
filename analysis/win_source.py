"""Win source decomposition — proposal Analysis Priority item #3.

> Win source decomposition → tournament rating vs marketplace equity

For each agent in each run, split the final payout into:
  1. tournament contribution (base score from poker rating)
  2. marketplace contribution (net transfer from buying/selling, signed)

Under net settlement the marketplace contributions sum to zero across agents
within a run (it's a pure transfer). Under additive settlement sellers get a
bonus with no offsetting cost to buyers, so the marketplace contributions
sum to a positive number.

This module reads the `final_scores` and `payouts` dicts directly from the
exported RunData (they come from the Convex state), so there is no
re-simulation.

Public surface:
  - decompose_run(run) -> list[AgentWinSource]
  - analyze_all_runs(run_dir, paper_runs) -> dict[condition, ConditionWinSource]
  - dump_case_study_markdown(...)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from analysis.loader import RunData, load_run


@dataclass
class AgentWinSource:
    run_id: str
    agent_id: str
    model: str | None
    base_score: float  # pre-marketplace, from tournament
    payout: float  # post-marketplace, final
    marketplace_delta: float  # payout - base_score (signed)
    n_purchased: int
    n_sold: int
    rank_by_base: int  # rank by base score in this run (1 = highest)
    rank_by_payout: int  # rank by payout in this run
    rank_shift: int  # rank_by_base - rank_by_payout (positive = marketplace helped)


def decompose_run(run: RunData) -> list[AgentWinSource]:
    base_scores = run.final_scores or {}
    payouts = run.payouts or {}

    # Handle runs where final_scores is empty (early runs had this issue)
    if not base_scores:
        base_scores = payouts

    purchases_by_buyer: dict[str, int] = defaultdict(int)
    purchases_by_seller: dict[str, int] = defaultdict(int)
    for p in run.purchases:
        purchases_by_buyer[p.buyer_agent_id] += 1
        purchases_by_seller[p.seller_agent_id] += 1

    # Ranks
    base_sorted = sorted(base_scores.items(), key=lambda kv: -kv[1])
    rank_by_base = {aid: i + 1 for i, (aid, _) in enumerate(base_sorted)}
    payout_sorted = sorted(payouts.items(), key=lambda kv: -kv[1])
    rank_by_payout = {aid: i + 1 for i, (aid, _) in enumerate(payout_sorted)}

    out: list[AgentWinSource] = []
    for a in run.agents:
        aid = a.agent_id
        base = float(base_scores.get(aid, 0.0))
        payout = float(payouts.get(aid, base))
        out.append(
            AgentWinSource(
                run_id=run.run_id,
                agent_id=aid,
                model=run.agent_model(aid),
                base_score=base,
                payout=payout,
                marketplace_delta=payout - base,
                n_purchased=purchases_by_buyer.get(aid, 0),
                n_sold=purchases_by_seller.get(aid, 0),
                rank_by_base=rank_by_base.get(aid, -1),
                rank_by_payout=rank_by_payout.get(aid, -1),
                rank_shift=rank_by_base.get(aid, -1) - rank_by_payout.get(aid, -1),
            )
        )
    return out


# ── cross-run aggregation ────────────────────────────────────────────────


@dataclass
class ConditionWinSource:
    condition: str
    run_ids: list[str] = field(default_factory=list)
    agent_rows: list[AgentWinSource] = field(default_factory=list)

    @property
    def n_runs(self) -> int:
        return len(self.run_ids)

    @property
    def n_agents(self) -> int:
        return len(self.agent_rows)

    @property
    def total_transfer_volume(self) -> float:
        """Sum of |marketplace_delta| / 2 — total money that moved."""
        return sum(abs(r.marketplace_delta) for r in self.agent_rows) / 2.0

    @property
    def mean_abs_delta(self) -> float:
        if not self.agent_rows:
            return 0.0
        return sum(abs(r.marketplace_delta) for r in self.agent_rows) / len(self.agent_rows)

    @property
    def n_rank_shifted(self) -> int:
        return sum(1 for r in self.agent_rows if r.rank_shift != 0)

    def biggest_winners(self, n: int = 3) -> list[AgentWinSource]:
        return sorted(self.agent_rows, key=lambda r: -r.marketplace_delta)[:n]

    def biggest_losers(self, n: int = 3) -> list[AgentWinSource]:
        return sorted(self.agent_rows, key=lambda r: r.marketplace_delta)[:n]


def analyze_all_runs(
    run_dir: str | Path,
    paper_runs: dict[str, str],
) -> dict[str, ConditionWinSource]:
    run_dir = Path(run_dir)
    by_cond: dict[str, ConditionWinSource] = {}

    for run_id, cond in paper_runs.items():
        path = run_dir / f"{run_id}.json"
        if not path.exists():
            continue
        run = load_run(path)
        rows = decompose_run(run)
        if cond not in by_cond:
            by_cond[cond] = ConditionWinSource(condition=cond)
        by_cond[cond].run_ids.append(run_id)
        by_cond[cond].agent_rows.extend(rows)

    return by_cond


# ── reporting ────────────────────────────────────────────────────────────


def dump_case_study_markdown(
    result: dict[str, ConditionWinSource],
    out_path: str | Path,
    condition_order: list[str] | None = None,
) -> None:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    order = condition_order or sorted(result.keys())

    lines: list[str] = [
        "# Win Source Decomposition — Case Study Material",
        "",
        "Generated by `analysis/win_source.py`. Per-agent split of final score ",
        "into tournament contribution (base) and marketplace contribution (delta).",
        "",
        "Under net settlement the deltas sum to zero within a run (pure transfer).",
        "Under additive settlement sellers get a bonus with no offsetting buyer cost.",
        "",
        "Raw material for §4 Results worked examples (T2.1).",
        "",
    ]
    for cond_name in order:
        cond = result.get(cond_name)
        if not cond or cond.n_agents == 0:
            continue
        lines.append(f"## {cond_name}")
        lines.append(
            f"N_runs={cond.n_runs}, N_agents={cond.n_agents}, "
            f"total_transfer_volume={cond.total_transfer_volume:.2f}, "
            f"mean_abs_delta={cond.mean_abs_delta:.3f}, "
            f"rank_shifted={cond.n_rank_shifted}"
        )
        lines.append("")

        winners = cond.biggest_winners(3)
        if winners and winners[0].marketplace_delta > 0.01:
            lines.append("### Biggest marketplace winners")
            for w in winners:
                if w.marketplace_delta > 0.01:
                    lines.append(
                        f"- `{w.run_id}` / `{w.agent_id}` ({w.model or '?'}) "
                        f"— Δ=+{w.marketplace_delta:.2f} "
                        f"(base {w.base_score:.2f} → payout {w.payout:.2f}), "
                        f"sold {w.n_sold}, bought {w.n_purchased}, "
                        f"rank {w.rank_by_base} → {w.rank_by_payout}"
                    )
            lines.append("")

        losers = cond.biggest_losers(3)
        if losers and losers[0].marketplace_delta < -0.01:
            lines.append("### Biggest marketplace losers")
            for l in losers:
                if l.marketplace_delta < -0.01:
                    lines.append(
                        f"- `{l.run_id}` / `{l.agent_id}` ({l.model or '?'}) "
                        f"— Δ={l.marketplace_delta:.2f} "
                        f"(base {l.base_score:.2f} → payout {l.payout:.2f}), "
                        f"bought {l.n_purchased}, sold {l.n_sold}, "
                        f"rank {l.rank_by_base} → {l.rank_by_payout}"
                    )
            lines.append("")

    out_path.write_text("\n".join(lines))
    print(f"Wrote {out_path}")


def print_report(result: dict[str, ConditionWinSource], order: list[str]) -> None:
    print(f"{'condition':<18} {'N_runs':>6} {'|Δ|̄':>8} {'transfer':>10} {'rank_shift':>10}")
    for cond_name in order:
        cond = result.get(cond_name)
        if not cond or cond.n_agents == 0:
            continue
        print(
            f"{cond_name:<18} {cond.n_runs:>6} {cond.mean_abs_delta:>8.3f} "
            f"{cond.total_transfer_volume:>10.2f} {cond.n_rank_shifted:>10}"
        )


if __name__ == "__main__":
    from analysis.paper_runs import CONDITION_ORDER as COND_ORDER, condition_map

    result = analyze_all_runs(".goa_data/runs", condition_map())
    print_report(result, COND_ORDER)
    dump_case_study_markdown(result, "paper/data/win_source.md", condition_order=COND_ORDER)
