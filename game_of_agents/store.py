from __future__ import annotations

import asyncio
from pathlib import Path
import time
from typing import Awaitable, Callable, Iterable

from game_of_agents.json import dumps, loads
from game_of_agents.models import RunState, RunStatus


class RunStore:
    def __init__(
        self,
        root: Path,
        save_hooks: Iterable[Callable[[RunState], Awaitable[None]]] | None = None,
        read_hook: Callable[[], None] | None = None,
        write_hook: Callable[[], None] | None = None,
    ) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._save_hooks = list(save_hooks or [])
        self._read_hook = read_hook
        self._write_hook = write_hook

    async def list_runs(self) -> list[RunState]:
        async with self._lock:
            self._run_hook(self._read_hook)
            runs = []
            for path in sorted(self.root.glob("run_*.json")):
                runs.append(RunState.model_validate(loads(path.read_bytes())))
            return runs

    async def save_run(self, run: RunState) -> None:
        async with self._lock:
            self._write_locked(run)
        for hook in self._save_hooks:
            await hook(run)

    async def get_run(self, run_id: str) -> RunState | None:
        async with self._lock:
            self._run_hook(self._read_hook)
            path = self.root / f"{run_id}.json"
            if not path.exists():
                return None
            return RunState.model_validate(loads(path.read_bytes()))

    async def update_run(self, run_id: str, updater: Callable[[RunState], RunState | None]) -> RunState:
        async with self._lock:
            self._run_hook(self._read_hook)
            path = self.root / f"{run_id}.json"
            if not path.exists():
                raise FileNotFoundError(run_id)
            run = RunState.model_validate(loads(path.read_bytes()))
            updated = updater(run) or run
            self._write_locked(updated)
        for hook in self._save_hooks:
            await hook(updated)
        return updated

    async def replace_run(self, run: RunState) -> RunState:
        await self.save_run(run)
        return run

    async def save_all(self, runs: Iterable[RunState]) -> None:
        for run in runs:
            await self.save_run(run)

    async def delete_run(self, run_id: str) -> bool:
        async with self._lock:
            path = self.root / f"{run_id}.json"
            if not path.exists():
                return False
            path.unlink()
            self._run_hook(self._write_hook)
            return True

    def _write_locked(self, run: RunState) -> None:
        path = run.path(self.root)
        if path.exists():
            existing = RunState.model_validate(loads(path.read_bytes()))
            run = self._merge_progress(existing, run)
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_bytes(dumps(run.model_dump(mode="json")))
        tmp_path.replace(path)
        self._run_hook(self._write_hook)

    def _merge_progress(self, existing: RunState, incoming: RunState) -> RunState:
        if self._status_rank(existing.status) > self._status_rank(incoming.status):
            incoming.status = existing.status
            if existing.finished_at and incoming.finished_at is None:
                incoming.finished_at = existing.finished_at
            if existing.last_error and incoming.last_error is None:
                incoming.last_error = existing.last_error
        return incoming

    def _status_rank(self, status: RunStatus) -> int:
        return {
            RunStatus.PENDING: 0,
            RunStatus.RUNNING: 1,
            RunStatus.STOPPING: 2,
            RunStatus.FINISHED: 3,
            RunStatus.FAILED: 3,
        }[status]

    def _run_hook(self, hook: Callable[[], None] | None) -> None:
        if not hook:
            return
        for attempt in range(5):
            try:
                hook()
                return
            except RuntimeError as exc:
                if "open files preventing the operation" not in str(exc):
                    raise
                if attempt == 4:
                    return
                time.sleep(0.05 * (attempt + 1))
