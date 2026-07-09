"""Chat content analysis — proposal Analysis Priority item #1.

Every paper-grade run has a full chat feed (6,269 messages across the 39-run corpus).
Per the proposal:

> Chat content analysis → 200+ messages per run, mostly unexamined.

This module categorizes every chat message with keyword heuristics,
aggregates by condition, and surfaces the top-N most strategically loaded
messages per condition for use as quoted evidence in the paper.

Public surface:
  - categorize_message(text) -> list[str]
  - analyze_chat(run) -> ChatAnalysis
  - analyze_all_runs(run_dir, paper_runs) -> dict[condition, CondAnalysis]
  - top_messages_per_category(...)

The module is pure and deterministic — no API calls. Category definitions
are deliberately heuristic so they can be audited and tweaked.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from analysis.loader import CommentRecord, RunData, load_run


# ── categories ────────────────────────────────────────────────────────────
# Heuristic keyword patterns. A single message can carry multiple labels.
# The regexes are intentionally case-insensitive and word-boundaried where
# possible. Tuned against a sample of the actual data we have.

CATEGORIES: dict[str, re.Pattern] = {
    "boast": re.compile(
        r"\b(#\s*1|rank\s*1|top\s*\d|best|winning|dominat|crush|leader|"
        r"king|champion|supreme|unbeat|undefeated|undisputed)\b"
        r"|🔥|👑|💪|🏆|🥇",
        re.IGNORECASE,
    ),
    "offer_pitch": re.compile(
        r"\b(offer_|available|selling|buy (?:my|mine)|check\s*out|updated|"
        r"new version|v\d+|list(ed|ing)|price|discount|sale|fire\s*sale|"
        r"last chance|limited|clearance)\b",
        re.IGNORECASE,
    ),
    "question": re.compile(r"\?"),
    "hostility": re.compile(
        r"\b(weak|amateur|loser|FUD|desperate|pathetic|clueless|garbage|"
        r"trash|beat you|destroy|humiliat|embarrass|ruthless|ruthlessly|"
        r"bleed|prey|expose|exploit\s*(?:you|them)|cry)\b"
        r"|🔪|💀|🩸",
        re.IGNORECASE,
    ),
    "coordination": re.compile(
        r"\b(alliance|allied|ally|let['']?s\s*(?:all|both)|we should|together|"
        r"deal|cooperate|team\s*up|split|share info|mutual|agreement|pact|"
        r"ceasefire|truce|don't\s*(?:attack|target|raise))\b",
        re.IGNORECASE,
    ),
    "misdirection": re.compile(
        r"\b(don't\s*(?:tell|reveal|share)|won't\s*(?:tell|reveal|share)|"
        r"secret|hidden|bluff|trap|fake|misleading|lie|pretend)\b",
        re.IGNORECASE,
    ),
    "reporting": re.compile(
        r"\b(rating\s*\d|rank\s*#\d|purchased|bought|sold|submitted|"
        r"projected payout|ELO\s*\d|score\s*\d)\b",
        re.IGNORECASE,
    ),
    "info_exploit": re.compile(
        r"\b(reverse[- ]?engineer|analyz(?:e|ed)\s*(?:their|competitor)|"
        r"steal\s*(?:code|strategy)|copy\s*(?:their|the)|learn from|"
        r"decode|reverse|extract)\b",
        re.IGNORECASE,
    ),
    "hype": re.compile(
        r"\b(quantum|next-?gen|synthesis|breakthrough|revolution|cutting-edge|"
        r"state-of-the-art|unbeatable|unstoppable|supremacy|unlock\w*|hidden\s+"
        r"(?:equity|value|potential|layer)|early\s*(?:access|founder)|"
        r"founder\s*pricing|disruptive|proprietary)\b",
        re.IGNORECASE,
    ),
    "targeted": re.compile(
        # Messages that name-target another agent by ID ("claude-2, your bot...")
        r"\b(agent[- ]?\d|claude[- ]?\d|gpt[- ]?\d|grok[- ]?\d|bad[- ]?actor)\b",
        re.IGNORECASE,
    ),
}

# A "strategic score" weights the categories by how much they indicate
# behavior beyond simple reporting — higher = more paper-worthy.
STRATEGIC_WEIGHTS: dict[str, float] = {
    "boast": 1.0,
    "offer_pitch": 0.5,
    "hostility": 2.0,
    "coordination": 2.5,
    "misdirection": 2.0,
    "info_exploit": 1.5,
    "hype": 1.5,
    "targeted": 0.8,
    "question": 0.2,
    "reporting": 0.0,
}


def categorize_message(text: str) -> list[str]:
    """Return all categories that match the given message."""
    return [cat for cat, pat in CATEGORIES.items() if pat.search(text)]


def strategic_score(categories: list[str]) -> float:
    return sum(STRATEGIC_WEIGHTS.get(c, 0.0) for c in categories)


# ── per-run analysis ──────────────────────────────────────────────────────


@dataclass
class MessageRecord:
    run_id: str
    agent_id: str
    sequence: int
    text: str
    categories: list[str]
    score: float


@dataclass
class ChatAnalysis:
    run_id: str
    n_messages: int
    category_counts: Counter
    per_agent_counts: dict[str, Counter]  # agent_id -> Counter of categories
    messages: list[MessageRecord]

    def top_n_by_score(self, n: int = 5) -> list[MessageRecord]:
        return sorted(self.messages, key=lambda m: -m.score)[:n]

    def top_n_by_category(self, category: str, n: int = 3) -> list[MessageRecord]:
        matching = [m for m in self.messages if category in m.categories]
        return sorted(matching, key=lambda m: -m.score)[:n]


def analyze_chat(run: RunData) -> ChatAnalysis:
    messages: list[MessageRecord] = []
    category_counts: Counter = Counter()
    per_agent_counts: dict[str, Counter] = defaultdict(Counter)

    for c in run.comments:
        cats = categorize_message(c.text)
        score = strategic_score(cats)
        messages.append(
            MessageRecord(
                run_id=run.run_id,
                agent_id=c.author_agent_id,
                sequence=c.sequence,
                text=c.text,
                categories=cats,
                score=score,
            )
        )
        for cat in cats:
            category_counts[cat] += 1
            per_agent_counts[c.author_agent_id][cat] += 1

    return ChatAnalysis(
        run_id=run.run_id,
        n_messages=len(run.comments),
        category_counts=category_counts,
        per_agent_counts=dict(per_agent_counts),
        messages=messages,
    )


# ── cross-run aggregation ────────────────────────────────────────────────


@dataclass
class ConditionChatAnalysis:
    condition: str
    run_ids: list[str]
    n_messages: int
    n_runs: int
    category_counts: Counter
    category_fractions: dict[str, float]  # of all messages in this condition
    messages: list[MessageRecord]  # flat list of all messages
    top_messages: list[MessageRecord] = field(default_factory=list)

    def top_n(self, n: int = 5) -> list[MessageRecord]:
        return sorted(self.messages, key=lambda m: -m.score)[:n]

    def top_n_by_category(self, category: str, n: int = 3) -> list[MessageRecord]:
        matching = [m for m in self.messages if category in m.categories]
        return sorted(matching, key=lambda m: -m.score)[:n]


def analyze_all_runs(
    run_dir: str | Path,
    paper_runs: dict[str, str],
) -> dict[str, ConditionChatAnalysis]:
    """Iterate over paper_runs (run_id -> condition label), aggregate chat
    analysis per condition.
    """
    run_dir = Path(run_dir)
    by_cond: dict[str, ConditionChatAnalysis] = {}

    for run_id, cond in paper_runs.items():
        path = run_dir / f"{run_id}.json"
        if not path.exists():
            continue
        run = load_run(path)
        a = analyze_chat(run)
        if cond not in by_cond:
            by_cond[cond] = ConditionChatAnalysis(
                condition=cond,
                run_ids=[],
                n_messages=0,
                n_runs=0,
                category_counts=Counter(),
                category_fractions={},
                messages=[],
            )
        agg = by_cond[cond]
        agg.run_ids.append(run_id)
        agg.n_messages += a.n_messages
        agg.n_runs += 1
        agg.category_counts.update(a.category_counts)
        agg.messages.extend(a.messages)

    for cond, agg in by_cond.items():
        if agg.n_messages:
            agg.category_fractions = {
                cat: count / agg.n_messages
                for cat, count in agg.category_counts.items()
            }
        agg.top_messages = agg.top_n(10)

    return by_cond


# ── reporting ────────────────────────────────────────────────────────────


def format_message_for_paper(m: MessageRecord, max_chars: int = 220) -> str:
    text = m.text.strip()
    if len(text) > max_chars:
        text = text[: max_chars - 3] + "..."
    cats = ",".join(m.categories) if m.categories else "(none)"
    return f'[{m.run_id[:20]} / {m.agent_id} / seq={m.sequence} / cats={cats} / score={m.score:.1f}]\n  "{text}"'


def dump_case_study_markdown(
    result: dict[str, ConditionChatAnalysis],
    out_path: str | Path,
    condition_order: list[str] | None = None,
    n_per_cond: int = 6,
) -> None:
    """Write a human-readable markdown dump of the top strategic chat
    messages per condition. This is the raw material for the paper's
    Results section case studies."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    order = condition_order or sorted(result.keys())

    lines: list[str] = [
        "# Chat Content Analysis — Case Study Material",
        "",
        "Generated by `analysis/chat_content.py`. For each paper condition, "
        "this file lists the top strategic chat messages scored by category "
        "weighting (hostility, coordination, misdirection, info_exploit get "
        "high weights; reporting gets zero).",
        "",
        "Raw material for the §4 Results rewrite (T2.1). Each quote has "
        "run_id + agent_id + categories + verbatim text.",
        "",
    ]
    for cond_name in order:
        cond = result.get(cond_name)
        if not cond:
            continue
        lines.append(f"## {cond_name}")
        lines.append(
            f"N_runs={cond.n_runs}, N_messages={cond.n_messages}, "
            f"runs={', '.join(cond.run_ids)}"
        )
        lines.append("")
        if cond.n_messages == 0:
            lines.append("_(no chat messages — chat disabled or no activity)_")
            lines.append("")
            continue

        # Category fractions
        lines.append("Category fractions (of all messages in condition):")
        for cat in (
            "boast",
            "offer_pitch",
            "hostility",
            "coordination",
            "misdirection",
            "info_exploit",
            "hype",
            "targeted",
            "reporting",
        ):
            frac = cond.category_fractions.get(cat, 0.0)
            lines.append(f"- **{cat}**: {frac * 100:.1f}%")
        lines.append("")

        # Top messages
        lines.append(f"### Top {n_per_cond} strategic messages")
        lines.append("")
        for i, m in enumerate(cond.top_n(n_per_cond), 1):
            cats = ", ".join(m.categories) if m.categories else "(none)"
            text = m.text.replace("\n", " ").strip()
            lines.append(f"**{i}.** `{m.run_id}` / `{m.agent_id}` — [{cats}] (score={m.score:.1f})")
            lines.append("")
            lines.append(f"> {text}")
            lines.append("")
        lines.append("")

    out_path.write_text("\n".join(lines))
    print(f"Wrote {len(order)} conditions to {out_path}")


def print_condition_report(cond: ConditionChatAnalysis, n_top: int = 6) -> None:
    print(f"\n=== {cond.condition}  (N_runs={cond.n_runs}, N_messages={cond.n_messages}) ===")
    print(f"  runs: {', '.join(r[:20] for r in cond.run_ids)}")
    if cond.n_messages == 0:
        print("  (no messages)")
        return
    # Category breakdown
    print("  category breakdown (fraction of all messages in condition):")
    for cat in (
        "boast",
        "offer_pitch",
        "hostility",
        "coordination",
        "misdirection",
        "info_exploit",
        "hype",
        "targeted",
        "question",
        "reporting",
    ):
        frac = cond.category_fractions.get(cat, 0.0)
        bar = "█" * int(frac * 40)
        print(f"    {cat:<15} {frac * 100:5.1f}%  {bar}")

    # Top strategic messages
    print(f"\n  top {n_top} most strategic messages:")
    for i, m in enumerate(cond.top_n(n_top), 1):
        print(f"  {i}. {format_message_for_paper(m)}")


if __name__ == "__main__":
    from analysis.paper_runs import CONDITION_ORDER as COND_ORDER, condition_map

    result = analyze_all_runs(".goa_data/runs", condition_map())
    for cond in COND_ORDER:
        if cond in result:
            print_condition_report(result[cond])

    dump_case_study_markdown(
        result,
        "paper/data/chat_case_studies.md",
        condition_order=COND_ORDER,
    )
