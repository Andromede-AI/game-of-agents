"""Integrate clean additive control results into paper data.

Usage:
    uv run python scripts/integrate_clean_additive.py

Requires the run to be exported first:
    uv run python -m analysis.cli export run_57vd316xyfk16q

This script:
1. Loads the exported run
2. Computes metrics matching run_summary.csv schema
3. Appends a row to paper/data/run_summary.csv
4. Prints a comparison table vs B1 and confounded additive
5. Generates preliminary paper text suggestions
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

from analysis.loader import load_run


RUN_ID = "run_57vd316xyfk16q"
RUN_PATH = Path(f".goa_data/runs/{RUN_ID}.json")
CSV_PATH = Path("paper/data/run_summary.csv")


def main():
    if not RUN_PATH.exists():
        print(f"Run not exported yet. Run:")
        print(f"  uv run python -m analysis.cli export {RUN_ID}")
        sys.exit(1)

    run = load_run(RUN_PATH)

    # Compute metrics
    n_agents = len(run.agents)
    n_games = len(run.finished_games)

    # Gini coefficient on final ELO
    elos = sorted([a.best_elo for a in run.agents])
    n = len(elos)
    if n > 0 and sum(elos) > 0:
        gini = sum(sum(abs(elos[i] - elos[j]) for j in range(n)) for i in range(n)) / (2 * n * sum(elos))
    else:
        gini = 0.0

    # Marketplace
    n_offers = len(run.offers) if hasattr(run, 'offers') else 0
    n_purchases = len(run.purchases) if hasattr(run, 'purchases') else 0
    buy_rate = n_purchases / n_offers if n_offers > 0 else 0.0

    # Chat
    n_chat = len(run.comments) if hasattr(run, 'comments') else 0

    row = {
        'run_id': RUN_ID,
        'condition': 'Additive (clean)',
        'duration_h': 3.0,
        'rep': 3,  # Third additive run (after confounded 1 & 2)
        'agents': n_agents,
        'n_games': n_games,
        'gini': gini,
        'n_offers': n_offers,
        'n_purchases': n_purchases,
        'buy_rate': buy_rate,
        'n_chat': n_chat,
    }

    # Print results
    print(f"\n=== Clean Additive Control Results ===")
    print(f"  Run: {RUN_ID}")
    print(f"  Agents: {n_agents}")
    print(f"  Games: {n_games}")
    print(f"  Gini: {gini:.6f}")
    print(f"  Offers: {n_offers}")
    print(f"  Purchases: {n_purchases}")
    print(f"  Buy rate: {buy_rate:.1%}")
    print(f"  Chat: {n_chat}")

    # Load comparison data
    print(f"\n=== Comparison Table ===")
    print(f"  {'Condition':<25} {'Offers':>7} {'Purchases':>10} {'Buy Rate':>10} {'Gini':>8}")
    print(f"  {'-'*60}")

    if CSV_PATH.exists():
        with open(CSV_PATH) as f:
            reader = csv.DictReader(f)
            for r in reader:
                cond = r['condition']
                if cond in ['B1 (full env)', 'Additive'] and float(r['duration_h']) == 3.0:
                    print(f"  {cond + ' (' + r['run_id'][-6:] + ')':<25} {r['n_offers']:>7} {r['n_purchases']:>10} {float(r['buy_rate']):.1%}   {float(r['gini']):.6f}")

    print(f"  {'Additive (clean)':<25} {n_offers:>7} {n_purchases:>10} {buy_rate:.1%}   {gini:.6f}")

    # Append to CSV
    print(f"\n=== Appending to {CSV_PATH} ===")
    with open(CSV_PATH, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        writer.writerow(row)
    print(f"  Done. Row appended.")

    # Paper text suggestions
    print(f"\n=== Paper Text Suggestions ===")
    b1_purchases = 9  # B1 3h run
    confounded_purchases = 881

    if n_purchases > b1_purchases * 2:
        print(f"  SCENARIO 1: Clean additive ({n_purchases}) >> B1 ({b1_purchases})")
        print(f"  → Incentive structure alone drives ~{n_purchases/b1_purchases:.0f}x increase in purchasing")
        print(f"  → Confounded prompt inflated this further to {confounded_purchases/n_purchases:.0f}x")
        print(f"  → System-design effect is REAL; prompt amplified but didn't create it")
    elif n_purchases > b1_purchases * 1.2:
        print(f"  SCENARIO 2: Clean additive ({n_purchases}) moderately > B1 ({b1_purchases})")
        print(f"  → Modest incentive-structure effect ({n_purchases/b1_purchases:.1f}x)")
        print(f"  → Most of the 881-purchase effect was prompt-driven")
    else:
        print(f"  SCENARIO 3: Clean additive ({n_purchases}) ≈ B1 ({b1_purchases})")
        print(f"  → 881 purchases was ENTIRELY prompt-driven")
        print(f"  → Incentive structure alone doesn't change behavior")
        print(f"  → Major rewrite of §4.3 needed")


if __name__ == "__main__":
    main()
