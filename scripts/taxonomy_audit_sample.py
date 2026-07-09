"""Stratified random sample for hand-labeled taxonomy validation (item 7).

Selects 20 (run, agent) classifier outputs — 4 per category, spanning
the score range — and writes:

  paper/data/taxonomy_audit_20.md         — labeling template (blind)
  paper/data/taxonomy_audit_scores.json   — classifier scores (sidecar)

After the human rater fills in labels, run scripts/taxonomy_audit_compute.py to
compute Cohen's κ, Spearman ρ, and agreement rate.

Usage:
    uv run python scripts/taxonomy_audit_sample.py
    uv run python scripts/taxonomy_audit_sample.py --seed 17
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

from analysis.loader import RunData, load_run

CATEGORIES = [
    "competitive_coding",
    "marketplace_exploitation",
    "social_influence",
    "information_exploitation",
    "collusion",
]
EVIDENCE_JSONL = Path("paper/data/taxonomy_evidence.jsonl")
OUT_MD = Path("paper/data/taxonomy_audit_20.md")
OUT_JSON = Path("paper/data/taxonomy_audit_scores.json")
RUNS_DIR = Path(".goa_data/runs")


def load_evidence() -> list[dict]:
    records = []
    with EVIDENCE_JSONL.open() as f:
        for line in f:
            records.append(json.loads(line))
    return records


def _clean_reasoning_text(block: dict[str, Any]) -> str:
    """Mirror the transcript scanner's conservative text extraction."""
    if (block.get("kind") or "").lower() != "text":
        return ""
    text = block.get("text") or block.get("body") or ""
    if not isinstance(text, str):
        return ""
    if text.strip().startswith(('{"type"', "{'type'", '[{"', '{"tool_')):
        return ""
    title = (block.get("title") or "").strip().lower()
    if title in ("raw stdout", "raw stderr", "stdout", "stderr"):
        return ""
    return " ".join(text.split())


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text[:limit] + ("..." if len(text) > limit else "")


def load_run_cached(run_id: str, cache: dict[str, RunData]) -> RunData:
    if run_id not in cache:
        cache[run_id] = load_run(RUNS_DIR / f"{run_id}.json")
    return cache[run_id]


def raw_chat_window(run: RunData, agent_id: str) -> list[str]:
    comments = sorted(
        [c for c in run.comments if c.author_agent_id == agent_id],
        key=lambda c: c.sequence,
    )
    if not comments:
        return ["(No raw chat messages stored for this agent/run.)"]
    window_size = min(3, len(comments))
    center = len(comments) // 2
    start = max(0, min(center - window_size // 2, len(comments) - window_size))
    window = comments[start:start + window_size]
    return [f"- {c.created_at}: {_truncate(c.text, 320)}" for c in window]


def raw_reasoning_window(run: RunData, agent_id: str) -> list[str]:
    blocks = run.transcripts.get(agent_id, []) if isinstance(run.transcripts, dict) else []
    usable: list[tuple[int, dict[str, Any], str]] = []
    for idx, block in enumerate(blocks):
        text = _clean_reasoning_text(block)
        if text:
            usable.append((idx, block, text))
    if usable:
        mid = len(usable) // 2
        idx, block, text = usable[mid]
        title = (block.get("title") or "untitled").strip()
        return [
            f"- Transcript block {idx} ({title}): {_truncate(text, 700)}",
        ]

    raw_actions: list[tuple[str, str]] = []
    for offer in run.offers:
        if offer.seller_agent_id == agent_id:
            raw_actions.append(
                (
                    offer.created_at,
                    f"Offer: '{offer.title}' at {offer.price_pct:.2f}% "
                    f"with {len(offer.artifact_paths)} artifact(s)",
                )
            )
    for purchase in run.purchases:
        if purchase.buyer_agent_id == agent_id:
            raw_actions.append(
                (
                    purchase.created_at,
                    f"Purchase: bought from {purchase.seller_agent_id} at "
                    f"{purchase.price_pct:.2f}%",
                )
            )
        elif purchase.seller_agent_id == agent_id:
            raw_actions.append(
                (
                    purchase.created_at,
                    f"Sale: sold to {purchase.buyer_agent_id} at "
                    f"{purchase.price_pct:.2f}%",
                )
            )
    if raw_actions:
        raw_actions.sort(key=lambda item: item[0])
        window_size = min(3, len(raw_actions))
        center = len(raw_actions) // 2
        start = max(0, min(center - window_size // 2, len(raw_actions) - window_size))
        window = raw_actions[start:start + window_size]
        lines = [
            "(No reasoning transcript stored for this agent/run; using a "
            "mechanically selected raw marketplace-action window instead.)"
        ]
        lines.extend(f"- {ts}: {desc}" for ts, desc in window)
        return lines

    return [
        "(No reasoning transcript or marketplace-action context block stored for this agent/run.)"
    ]


def pick_stratified(records: list[dict], rng: random.Random) -> list[tuple[dict, str]]:
    """Pick 4 cases per category, balancing high/mid/low scores where possible.

    Returns list of (record, focus_category) — the focus_category determines
    which category's evidence is shown primarily in the template.
    """
    picks: list[tuple[dict, str]] = []
    used_keys: set[tuple[str, str]] = set()

    for cat in CATEGORIES:
        # Bucket records by score on this category.
        scored = [(r, r[cat]["score"]) for r in records if cat in r]
        # Buckets: high (>=0.6), mid (0.3-0.6), low (<0.3)
        hi = [r for r, s in scored if s >= 0.6]
        mid = [r for r, s in scored if 0.3 <= s < 0.6]
        lo = [r for r, s in scored if s < 0.3]

        # Target: 2 high, 1 mid, 1 low when all buckets have content;
        # fall back to whatever's available if a bucket is empty.
        target = [("hi", 2), ("mid", 1), ("lo", 1)]
        buckets = {"hi": hi, "mid": mid, "lo": lo}

        for bname, want in target:
            pool = [r for r in buckets[bname]
                    if (r["run_id"], r["agent_id"]) not in used_keys]
            if not pool:
                # Fallback: pull from any non-empty bucket.
                for alt in ("mid", "hi", "lo"):
                    pool = [r for r in buckets[alt]
                            if (r["run_id"], r["agent_id"]) not in used_keys]
                    if pool:
                        break
            if not pool:
                continue
            rng.shuffle(pool)
            for _ in range(want):
                if not pool:
                    break
                rec = pool.pop()
                used_keys.add((rec["run_id"], rec["agent_id"]))
                picks.append((rec, cat))

    return picks


def render_case(
    i: int,
    rec: dict,
    focus_cat: str,
    run: RunData,
) -> list[str]:
    """Render one blind labeling case — evidence and rationale per category,
    but NO scores."""
    lines = [
        f"## Case {i} — `{rec['run_id']}` / `{rec['agent_id']}`",
        "",
        f"Model: `{rec.get('model', '?')}`. Focus category: **{focus_cat}**"
        " (but rate all five).",
        "",
    ]
    for cat in CATEGORIES:
        c = rec.get(cat)
        if not c:
            continue
        lines.append(f"### {cat}")
        lines.append("")
        lines.append("**Rationale (classifier):**")
        lines.append("")
        lines.append(f"> {c.get('rationale', '(none)')}")
        lines.append("")
        ev = c.get("evidence") or []
        if ev:
            lines.append("**Evidence cited:**")
            lines.append("")
            for e in ev[:4]:  # cap at 4 quotes per category
                truncated = e[:400] + ("..." if len(e) > 400 else "")
                lines.append(f"- {truncated}")
            lines.append("")
    lines.append("### Raw chat window (chronological, non-salience-selected)")
    lines.append("")
    lines.extend(raw_chat_window(run, rec["agent_id"]))
    lines.append("")
    lines.append("### Raw reasoning/context window (mechanically selected)")
    lines.append("")
    lines.extend(raw_reasoning_window(run, rec["agent_id"]))
    lines.append("")
    lines.append("**Your label** (one of `0`, `0.25`, `0.5`, `0.75`, `1.0` per category):")
    lines.append("")
    lines.append("```")
    lines.append(f"case_{i}:")
    for cat in CATEGORIES:
        lines.append(f"  {cat}: ___")
    lines.append("  notes: \"\"")
    lines.append("```")
    lines.append("")
    lines.append("---")
    lines.append("")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=17)
    args = ap.parse_args()

    records = load_evidence()
    print(f"Loaded {len(records)} classifier outputs from {EVIDENCE_JSONL}")

    rng = random.Random(args.seed)
    picks = pick_stratified(records, rng)
    print(f"Selected {len(picks)} cases (target: 20; 4 per category).")
    run_cache: dict[str, RunData] = {}

    # Render labeling template.
    md_lines = [
        "# Taxonomy Classifier Audit — 20-case hand-label template",
        "",
        f"Generated by `scripts/taxonomy_audit_sample.py` (seed={args.seed}).",
        "",
        "For each case, read the classifier's evidence + rationale for all 5"
        " categories plus two raw, mechanically selected context windows"
        " from the underlying run export, then write your own score per"
        " category on the `0, 0.25, 0.5, 0.75, 1.0` scale. You have NOT"
        " seen the classifier's scores — this is blind.",
        "",
        "After filling in labels, run `scripts/taxonomy_audit_compute.py` to"
        " compute Cohen's κ (binarized at 0.5), Spearman ρ on continuous"
        " scores, and agreement-within-0.25.",
        "",
        f"Categories: {', '.join(CATEGORIES)}",
        "",
        "Selection rule for added raw windows: chat uses a chronological"
        " middle window of the agent's own messages; reasoning uses the"
        " median retained transcript block after conservative cleaning, or"
        " a chronological marketplace-action fallback when no transcript is"
        " stored for that agent/run.",
        "",
        "---",
        "",
    ]
    for i, (rec, focus) in enumerate(picks, 1):
        run = load_run_cached(rec["run_id"], run_cache)
        md_lines.extend(render_case(i, rec, focus, run))

    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(md_lines))
    print(f"Wrote {OUT_MD}")

    # Sidecar: classifier scores for post-labeling comparison.
    sidecar = []
    for i, (rec, focus) in enumerate(picks, 1):
        scores = {cat: rec[cat]["score"] for cat in CATEGORIES if cat in rec}
        sidecar.append({
            "case": i,
            "run_id": rec["run_id"],
            "agent_id": rec["agent_id"],
            "focus_category": focus,
            "classifier_scores": scores,
        })
    OUT_JSON.write_text(json.dumps(sidecar, indent=2))
    print(f"Wrote {OUT_JSON}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
