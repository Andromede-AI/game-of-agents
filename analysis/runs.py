"""Persistent run registry. Tracks all experiments locally so we don't
lose run IDs when the server restarts.

Stored at .goa_data/registry.json — survives server restarts and doesn't
depend on the API server being up.
"""

from __future__ import annotations

import json
from datetime import datetime, UTC
from pathlib import Path
from typing import Any


REGISTRY_PATH = Path(".goa_data/registry.json")


def _load() -> list[dict]:
    if not REGISTRY_PATH.exists():
        return []
    return json.loads(REGISTRY_PATH.read_text())


def _save(entries: list[dict]) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(entries, indent=2, default=str))


def register(run_id: str, config_path: str, name: str, condition: str = "") -> None:
    """Register a new experiment run."""
    entries = _load()
    # Don't duplicate
    if any(e["run_id"] == run_id for e in entries):
        return
    entries.append({
        "run_id": run_id,
        "config": config_path,
        "name": name,
        "condition": condition or name,
        "launched_at": datetime.now(UTC).isoformat(),
        "status": "launched",
    })
    _save(entries)


def update_status(run_id: str, status: str, **extra: Any) -> None:
    """Update a run's status and optional metadata."""
    entries = _load()
    for e in entries:
        if e["run_id"] == run_id:
            e["status"] = status
            e.update(extra)
            break
    _save(entries)


def list_runs(status: str | None = None) -> list[dict]:
    """List all registered runs, optionally filtered by status."""
    entries = _load()
    if status:
        entries = [e for e in entries if e.get("status") == status]
    return entries


def get_run(run_id: str) -> dict | None:
    """Get a specific run by ID."""
    for e in _load():
        if e["run_id"] == run_id:
            return e
    return None
