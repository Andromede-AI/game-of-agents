"""Plotting utilities for GoA run analysis."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from analysis.loader import RunData
from analysis.metrics import compute_agent_stats, compute_pairwise_stats


def _ensure_matplotlib():
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
        return plt, np
    except ImportError:
        raise ImportError("pip install matplotlib numpy")


def plot_rating_trajectories(run: RunData, out: str | Path = "ratings.png") -> None:
    """Plot ELO rating over time for each bot, colored by agent."""
    plt, np = _ensure_matplotlib()

    # Collect bot rating snapshots from events
    # Fallback: use bot final ratings if no events
    agent_colors = {}
    cmap = plt.cm.tab10
    for i, agent in enumerate(run.agents):
        agent_colors[agent.agent_id] = cmap(i % 10)

    fig, ax = plt.subplots(figsize=(12, 6))

    # Group bots by agent, plot final ratings as bars if no time series
    # For time series, we'd need event data — check if available
    bot_events = [e for e in run.events if e.kind == "bot.submitted"]
    game_events = sorted(
        [e for e in run.events if e.kind == "game.finished"],
        key=lambda e: e.created_at or "",
    )

    if game_events:
        # Build rating trajectory from game results
        # Track bot ratings over match sequence
        bot_ratings: dict[str, list[tuple[int, float]]] = defaultdict(list)
        bot_agent: dict[str, str] = {b.bot_id: b.agent_id for b in run.bots}

        for idx, event in enumerate(game_events):
            payload = event.payload
            participants = payload.get("participants", [])
            for p in participants:
                bid = p.get("bot_id", "")
                # We don't have per-game rating in events, use final
                # This is a limitation — we'll improve when we see real data
                pass

        # Fallback: plot final bot ratings grouped by agent
        for agent in run.agents:
            agent_bots = sorted(
                [b for b in run.bots if b.agent_id == agent.agent_id],
                key=lambda b: b.created_at or "",
            )
            if agent_bots:
                xs = list(range(len(agent_bots)))
                ys = [b.elo for b in agent_bots]
                color = agent_colors[agent.agent_id]
                ax.plot(xs, ys, "o-", color=color, label=agent.agent_id, markersize=4)
    else:
        # No events — just show final ratings
        for agent in run.agents:
            agent_bots = [b for b in run.bots if b.agent_id == agent.agent_id]
            if agent_bots:
                xs = list(range(len(agent_bots)))
                ys = [b.elo for b in agent_bots]
                color = agent_colors[agent.agent_id]
                ax.bar([x + 0.1 * list(agent_colors.keys()).index(agent.agent_id)
                        for x in xs], ys, width=0.1, color=color, label=agent.agent_id)

    ax.set_xlabel("Bot submission index")
    ax.set_ylabel("ELO Rating")
    ax.set_title(f"Rating Trajectories — {run.run_id}")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(str(out), dpi=150)
    plt.close(fig)
    print(f"Saved: {out}")


def plot_aggression_heatmap(run: RunData, out: str | Path = "aggression.png") -> None:
    """Plot pairwise aggression matrix as a heatmap."""
    plt, np = _ensure_matplotlib()

    pairwise = compute_pairwise_stats(run)
    agents = run.agent_ids

    n = len(agents)
    matrix = np.zeros((n, n))
    agent_idx = {a: i for i, a in enumerate(agents)}

    for ps in pairwise:
        if ps.agent_a in agent_idx and ps.agent_b in agent_idx:
            matrix[agent_idx[ps.agent_a], agent_idx[ps.agent_b]] = ps.a_aggression

    fig, ax = plt.subplots(figsize=(8, 7))
    im = ax.imshow(matrix, cmap="YlOrRd", aspect="auto", vmin=0)

    ax.set_xticks(range(n))
    ax.set_yticks(range(n))
    ax.set_xticklabels(agents, rotation=45, ha="right", fontsize=9)
    ax.set_yticklabels(agents, fontsize=9)
    ax.set_xlabel("Opponent (B)")
    ax.set_ylabel("Actor (A)")
    ax.set_title(f"Pairwise Aggression Factor — {run.run_id}\n"
                 f"(A's aggression when playing against B)")

    # Annotate cells
    for i in range(n):
        for j in range(n):
            if i != j:
                ax.text(j, i, f"{matrix[i, j]:.2f}", ha="center", va="center", fontsize=8)

    fig.colorbar(im, ax=ax, label="Aggression Factor (raises / passive)")
    fig.tight_layout()
    fig.savefig(str(out), dpi=150)
    plt.close(fig)
    print(f"Saved: {out}")


def plot_marketplace_flow(run: RunData, out: str | Path = "marketplace.png") -> None:
    """Plot marketplace transaction flow between agents."""
    plt, np = _ensure_matplotlib()

    if not run.purchases:
        print("No marketplace purchases to plot.")
        return

    agents = run.agent_ids
    n = len(agents)
    agent_idx = {a: i for i, a in enumerate(agents)}

    # Build flow matrix: [buyer, seller] = count
    flow = np.zeros((n, n))
    for p in run.purchases:
        bi = agent_idx.get(p.buyer_agent_id)
        si = agent_idx.get(p.seller_agent_id)
        if bi is not None and si is not None:
            flow[bi, si] += 1

    fig, ax = plt.subplots(figsize=(8, 7))
    im = ax.imshow(flow, cmap="Blues", aspect="auto")

    ax.set_xticks(range(n))
    ax.set_yticks(range(n))
    ax.set_xticklabels(agents, rotation=45, ha="right", fontsize=9)
    ax.set_yticklabels(agents, fontsize=9)
    ax.set_xlabel("Seller")
    ax.set_ylabel("Buyer")
    ax.set_title(f"Marketplace Purchases — {run.run_id}")

    for i in range(n):
        for j in range(n):
            if flow[i, j] > 0:
                ax.text(j, i, f"{int(flow[i, j])}", ha="center", va="center", fontsize=10)

    fig.colorbar(im, ax=ax, label="Purchase count")
    fig.tight_layout()
    fig.savefig(str(out), dpi=150)
    plt.close(fig)
    print(f"Saved: {out}")
