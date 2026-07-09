"""Batch driver for the LLM-as-judge behavioral-taxonomy classifier.

Iterates over a configured set of paper-grade runs, classifies every agent
in every run, appends results to JSONL, and emits a wide CSV.

Resume-safe: each (run_id, agent_id, classifier_version) is keyed and
skipped if already present in the JSONL cache.

Usage:
    uv run python scripts/run_taxonomy_classifier.py             # full batch
    uv run python scripts/run_taxonomy_classifier.py --runs run_isj7ubddsisynd
    uv run python scripts/run_taxonomy_classifier.py --dry-run   # token counts only
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Allow `import analysis.*` when running as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.loader import load_run  # noqa: E402
from analysis.paper_runs import run_ids as _paper_run_ids  # noqa: E402
from analysis.taxonomy import (  # noqa: E402
    CATEGORIES,
    CLASSIFIER_VERSION,
    DEFAULT_MODEL,
    AgentTaxonomyResult,
    classify_agent_run,
    load_results_jsonl,
    write_result_jsonl,
)
from analysis.taxonomy_format import (  # noqa: E402
    estimate_tokens,
    format_agent_prompt,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = REPO_ROOT / ".goa_data" / "runs"
OUT_DIR = REPO_ROOT / "paper" / "data"
JSONL_PATH = OUT_DIR / "taxonomy_evidence.jsonl"
CSV_PATH = OUT_DIR / "taxonomy_frequencies.csv"


# Paper-grade core runs: canonical list lives in analysis.paper_runs.
PAPER_GRADE_RUNS: list[str] = _paper_run_ids()


def _load_env() -> None:
    """Load .env so ANTHROPIC_API_KEY is available."""
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def _ensure_run_exported(run_id: str) -> Path | None:
    path = RUNS_DIR / f"{run_id}.json"
    if path.exists():
        return path
    print(f"  ⚠ {run_id} not exported locally — skipping (run analysis.cli export to fetch)")
    return None


def _existing_keys(jsonl_path: Path) -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    for row in load_results_jsonl(str(jsonl_path)):
        keys.add(
            (
                row.get("run_id", ""),
                row.get("agent_id", ""),
                row.get("classifier_version", ""),
            )
        )
    return keys


_write_lock = threading.Lock()


def _classify_one(
    run, agent_id: str, model: str, target_input_tokens: int
) -> tuple[str, AgentTaxonomyResult | None, str | None, int]:
    """Wrapper that returns either a result or an error message, plus the
    estimated input token count. Catches all exceptions so the worker pool
    keeps running on partial failures."""
    sys_p, user_p = format_agent_prompt(
        run, agent_id, target_input_tokens=target_input_tokens
    )
    in_tokens = estimate_tokens(sys_p) + estimate_tokens(user_p)
    try:
        result = classify_agent_run(
            run,
            agent_id,
            model=model,
            target_input_tokens=target_input_tokens,
        )
        return (agent_id, result, None, in_tokens)
    except Exception as exc:  # noqa: BLE001
        return (agent_id, None, str(exc), in_tokens)


def run_batch(
    run_ids: list[str],
    dry_run: bool = False,
    model: str = DEFAULT_MODEL,
    target_input_tokens: int = 10_000,
    sleep_seconds: float = 0.0,
    concurrency: int = 1,
) -> list[dict]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    existing = _existing_keys(JSONL_PATH)
    print(f"Cache: {len(existing)} existing classifications", flush=True)
    print(f"Output: {JSONL_PATH}", flush=True)
    print(f"Concurrency: {concurrency}", flush=True)
    print(flush=True)

    written_rows: list[dict] = []
    total_calls = 0

    for run_id in run_ids:
        path = _ensure_run_exported(run_id)
        if path is None:
            continue
        run = load_run(path)
        cfg_name = (run.config or {}).get("name", "?")
        print(f"=== {run_id}  [{cfg_name}]  agents={len(run.agents)} ===", flush=True)

        # Filter to agents that need classification
        todo = [
            agent
            for agent in run.agents
            if (run.run_id, agent.agent_id, CLASSIFIER_VERSION) not in existing
        ]
        cached = len(run.agents) - len(todo)
        if cached:
            print(f"  ({cached} cached, {len(todo)} to classify)", flush=True)

        if dry_run:
            for agent in todo:
                sys_p, user_p = format_agent_prompt(
                    run, agent.agent_id, target_input_tokens=target_input_tokens
                )
                in_tokens = estimate_tokens(sys_p) + estimate_tokens(user_p)
                print(
                    f"  ◌ {agent.agent_id}  ~{in_tokens:5d} input tokens (dry-run)",
                    flush=True,
                )
                total_calls += 1
            print(flush=True)
            continue

        if concurrency <= 1:
            for agent in todo:
                aid, result, err, in_tokens = _classify_one(
                    run, agent.agent_id, model, target_input_tokens
                )
                _record_result(
                    aid, result, err, in_tokens, existing, written_rows, run.run_id
                )
                total_calls += 1
                if sleep_seconds:
                    time.sleep(sleep_seconds)
        else:
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futs = {
                    pool.submit(
                        _classify_one,
                        run,
                        agent.agent_id,
                        model,
                        target_input_tokens,
                    ): agent.agent_id
                    for agent in todo
                }
                for fut in as_completed(futs):
                    aid, result, err, in_tokens = fut.result()
                    _record_result(
                        aid, result, err, in_tokens, existing, written_rows, run.run_id
                    )
                    total_calls += 1
        print(flush=True)

    print(f"Total calls: {total_calls}", flush=True)
    return written_rows


def _record_result(
    agent_id: str,
    result: AgentTaxonomyResult | None,
    err: str | None,
    in_tokens: int,
    existing: set,
    written_rows: list[dict],
    run_id: str,
) -> None:
    if err is not None:
        print(f"  ✗ {agent_id}  ERROR: {err[:120]}", flush=True)
        return
    assert result is not None
    with _write_lock:
        write_result_jsonl(result, str(JSONL_PATH))
        existing.add((run_id, agent_id, CLASSIFIER_VERSION))
        written_rows.append(result.to_csv_row())
    cc = result.competitive_coding.score
    mx = result.marketplace_exploitation.score
    si = result.social_influence.score
    ix = result.information_exploitation.score
    co = result.collusion.score
    print(
        f"  ✓ {agent_id}  conf={result.confidence}  "
        f"cc={cc:.2f} mx={mx:.2f} si={si:.2f} ix={ix:.2f} co={co:.2f}  "
        f"({in_tokens} in)",
        flush=True,
    )


def emit_csv() -> int:
    """Read the JSONL cache and (re)emit the wide CSV."""
    rows = load_results_jsonl(str(JSONL_PATH))
    if not rows:
        print("No results in cache yet.")
        return 0
    fieldnames = [
        "run_id",
        "agent_id",
        "model",
        *CATEGORIES,
        "confidence",
        "data_sparsity_flags",
        "classifier_model",
        "classifier_version",
        "input_tokens_est",
    ]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(CSV_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "run_id": row["run_id"],
                    "agent_id": row["agent_id"],
                    "model": row.get("model") or "",
                    "competitive_coding": row["competitive_coding"]["score"],
                    "marketplace_exploitation": row["marketplace_exploitation"]["score"],
                    "social_influence": row["social_influence"]["score"],
                    "information_exploitation": row["information_exploitation"]["score"],
                    "collusion": row["collusion"]["score"],
                    "confidence": row.get("confidence", "low"),
                    "data_sparsity_flags": ";".join(row.get("data_sparsity_flags") or []),
                    "classifier_model": row.get("classifier_model", ""),
                    "classifier_version": row.get("classifier_version", ""),
                    "input_tokens_est": row.get("input_token_estimate", 0),
                }
            )
    print(f"Wrote {len(rows)} rows → {CSV_PATH}")
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the LLM-as-judge taxonomy classifier across all paper-grade runs.")
    parser.add_argument("--runs", nargs="+", help="Override run id list")
    parser.add_argument("--dry-run", action="store_true", help="Print token counts, no API calls")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--target-input-tokens", type=int, default=10_000)
    parser.add_argument("--sleep", type=float, default=0.0, help="Seconds between API calls (only used when concurrency=1)")
    parser.add_argument("--concurrency", type=int, default=6, help="Concurrent in-flight classifier calls")
    parser.add_argument("--csv-only", action="store_true", help="Skip classifier; just (re)emit CSV from JSONL cache")
    args = parser.parse_args()

    _load_env()

    if args.csv_only:
        emit_csv()
        return

    run_ids = args.runs if args.runs else PAPER_GRADE_RUNS
    run_batch(
        run_ids,
        dry_run=args.dry_run,
        model=args.model,
        target_input_tokens=args.target_input_tokens,
        sleep_seconds=args.sleep,
        concurrency=args.concurrency,
    )
    if not args.dry_run:
        emit_csv()


if __name__ == "__main__":
    main()
