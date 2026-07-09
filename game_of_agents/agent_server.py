from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path
import pwd
import re
import shlex
import sys
import tempfile
from typing import Any

import httpx

from game_of_agents.comment_models import resolve_comment_feed_model
from game_of_agents.convex_runtime import ConvexRuntimeClient
from game_of_agents.logging import configure_logging
from game_of_agents.models import AgentConfig, AgentRuntime, RunConfig
from game_of_agents.prompting import prompt_values_for_run, render_prompt_template
from game_of_agents.runtime_commands import prepare_runtime_command, prompt_flag
from game_of_agents.settings import settings
from game_of_agents.workspace_scaffold import scaffold_workspace


ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\].*?(?:\x07|\x1B\\))")
CONTINUATION_TITLE_RE = re.compile(r" \(cont\. \d+\)$")
MAX_LOG_BLOCK_CHARS = 64_000
MAX_RAW_STREAM_BLOCK_CHARS = 24_000
RAW_STREAM_TRUNCATED_MARKER = "\n[raw stream truncated]\n"
STARTUP_QUERY_TIMEOUT_SECONDS = 20.0
AUTO_SUBMIT_TIMEOUT_SECONDS = 30.0
RUNTIME_CALL_TIMEOUT_SECONDS = 20.0
HEARTBEAT_CALL_TIMEOUT_SECONDS = 5.0
SESSION_TOUCH_TIMEOUT_SECONDS = 8.0
LOG_FLUSH_TIMEOUT_SECONDS = 10.0
COMMENT_RUNTIME_TIMEOUT_SECONDS = 15.0
HEARTBEAT_HELPER_RESTART_DELAY_SECONDS = 2.0
STEP_STALL_THRESHOLD_SECONDS = 300.0
STREAM_DRAIN_TIMEOUT_SECONDS = 15.0


@dataclass
class BlockBuffer:
    blocks: dict[str, dict[str, Any]] = field(default_factory=dict)
    pending: list[dict[str, Any]] = field(default_factory=list)

    def upsert(self, block: dict[str, Any]) -> None:
        self.blocks[block["block_id"]] = block
        self.pending.append(block)

    def drain(self) -> list[dict[str, Any]]:
        drained = self.pending[:]
        self.pending.clear()
        return drained

    def requeue(self, blocks: list[dict[str, Any]]) -> None:
        if not blocks:
            return
        self.pending = list(blocks) + self.pending


class AgentServer:
    def __init__(self, run_id: str, agent_id: str) -> None:
        if not settings.convex_url:
            raise RuntimeError("CONVEX_URL is required")
        self.run_id = run_id
        self.agent_id = agent_id
        self.runtime = self._new_runtime_client()
        self.session_runtime = self._new_runtime_client()
        self.dashboard_runtime = self._new_runtime_client()
        self.submission_runtime = self._new_runtime_client()
        self.heartbeat_runtime = self._new_runtime_client()
        self.log_runtime = self._new_runtime_client()
        self.comment_runtime = self._new_runtime_client()
        self.sandbox_id = os.environ.get("MODAL_SANDBOX_ID", "")
        self.workspace = Path(tempfile.gettempdir()) / "goa" / run_id / agent_id
        self.block_buffer = BlockBuffer()
        self.started = False
        self.last_summary = ""
        self._mock_revision = 0
        self._auto_submit_revision = 0
        self._last_auto_submit_hash: str | None = None
        self._last_comment_action_at: datetime | None = None
        self._last_comment_error: str | None = None
        self._ship_task: asyncio.Task[None] | None = None
        self._diagnostics_task: asyncio.Task[None] | None = None
        self._codex_auth_bootstrapped = False
        self._heartbeat_helper: asyncio.subprocess.Process | None = None
        self._diagnostics_state = {
            "started_at": _utcnow_iso(),
            "last_progress_at": _utcnow_iso(),
            "last_progress_reason": "startup",
            "last_prompt_at": None,
            "last_visible_output_at": None,
            "last_step_started_at": None,
            "last_step_completed_at": None,
            "last_step_runtime": None,
            "last_step_exit_code": None,
            "last_submission_at": None,
            "last_submission_id": None,
            "active_process": None,
            "scopes": {
                "main": self._new_scope_state("startup"),
                "comment": self._new_scope_state("idle"),
                "logs": self._new_scope_state("idle"),
                "heartbeat": self._new_scope_state("running"),
            },
            "recent_failures": [],
        }

    def _new_runtime_client(self) -> ConvexRuntimeClient:
        return ConvexRuntimeClient(
            settings.convex_url or "",
            site_url=settings.convex_site_url,
            auth_token=settings.convex_sync_token,
        )

    def _new_scope_state(self, phase: str) -> dict[str, Any]:
        now = _utcnow_iso()
        return {
            "phase": phase,
            "operation": None,
            "since": now,
            "last_progress_at": None,
            "last_progress_reason": None,
            "last_success_at": None,
            "last_error": None,
            "last_error_at": None,
            "failure_count": 0,
        }

    def _scope_state(self, scope: str) -> dict[str, Any]:
        scopes = self._diagnostics_state.setdefault("scopes", {})
        state = scopes.get(scope)
        if isinstance(state, dict):
            return state
        state = self._new_scope_state("idle")
        scopes[scope] = state
        return state

    def _set_scope_phase(self, scope: str, phase: str, *, operation: str | None = None) -> None:
        state = self._scope_state(scope)
        state["phase"] = phase
        state["operation"] = operation
        state["since"] = _utcnow_iso()

    def _mark_scope_success(self, scope: str) -> None:
        self._scope_state(scope)["last_success_at"] = _utcnow_iso()

    def _record_scope_failure(self, scope: str, message: str) -> None:
        now = _utcnow_iso()
        state = self._scope_state(scope)
        state["last_error"] = message[:400]
        state["last_error_at"] = now
        state["failure_count"] = int(state.get("failure_count") or 0) + 1
        failures = self._diagnostics_state.setdefault("recent_failures", [])
        failures.append(
            {
                "scope": scope,
                "created_at": now,
                "message": message[:400],
            }
        )
        del failures[:-12]

    def _mark_progress(self, reason: str, *, scope: str = "main") -> None:
        self._diagnostics_state["last_progress_at"] = _utcnow_iso()
        self._diagnostics_state["last_progress_reason"] = reason[:160]
        state = self._scope_state(scope)
        state["last_progress_at"] = self._diagnostics_state["last_progress_at"]
        state["last_progress_reason"] = reason[:160]

    def _note_prompt_started(self) -> None:
        now = _utcnow_iso()
        self._diagnostics_state["last_prompt_at"] = now
        self._diagnostics_state["last_step_started_at"] = now
        self._mark_progress("prompt_built")

    def _note_output(self, source: str) -> None:
        now = _utcnow_iso()
        self._diagnostics_state["last_visible_output_at"] = now
        self._mark_progress(source)

    def _note_process_started(self, step_id: str, runtime_name: str, process: asyncio.subprocess.Process) -> None:
        self._diagnostics_state["last_step_runtime"] = runtime_name
        self._diagnostics_state["active_process"] = {
            "step_id": step_id,
            "runtime": runtime_name,
            "pid": process.pid,
            "started_at": _utcnow_iso(),
        }
        self._mark_progress(f"{runtime_name}_process_started")

    def _note_process_finished(self, returncode: int) -> None:
        now = _utcnow_iso()
        self._diagnostics_state["last_step_completed_at"] = now
        self._diagnostics_state["last_step_exit_code"] = returncode
        active = self._diagnostics_state.get("active_process")
        if isinstance(active, dict):
            active["finished_at"] = now
            active["exit_code"] = returncode
        self._diagnostics_state["active_process"] = None
        self._mark_progress(f"step_exit:{returncode}")

    def _note_submission(self, submission_id: str) -> None:
        now = _utcnow_iso()
        self._diagnostics_state["last_submission_at"] = now
        self._diagnostics_state["last_submission_id"] = submission_id
        self._mark_progress("bot_submitted")

    def _diagnostics_payload(self) -> dict[str, Any]:
        payload = json.loads(json.dumps(self._diagnostics_state))
        process = self._heartbeat_helper
        payload["heartbeat_helper"] = {
            "pid": process.pid if process is not None else None,
            "alive": bool(process is not None and process.returncode is None),
        }
        payload["derived"] = self._derived_diagnostics()
        return payload

    def _derived_diagnostics(self) -> dict[str, Any]:
        now = datetime.now(tz=UTC)
        last_progress_at = _parse_iso(self._diagnostics_state.get("last_progress_at"))
        main_scope = self._scope_state("main")
        main_last_progress_at = _parse_iso(main_scope.get("last_progress_at"))
        last_visible_output_at = _parse_iso(self._diagnostics_state.get("last_visible_output_at"))
        last_step_started_at = _parse_iso(self._diagnostics_state.get("last_step_started_at"))
        active_process = self._diagnostics_state.get("active_process")
        seconds_since_last_progress = (
            (now - last_progress_at).total_seconds() if last_progress_at is not None else None
        )
        seconds_since_main_progress = (
            (now - main_last_progress_at).total_seconds() if main_last_progress_at is not None else None
        )
        seconds_since_last_visible_output = (
            (now - last_visible_output_at).total_seconds() if last_visible_output_at is not None else None
        )
        seconds_since_step_started = (
            (now - last_step_started_at).total_seconds() if last_step_started_at is not None else None
        )
        stall_reason: str | None = None
        if isinstance(active_process, dict):
            if (
                seconds_since_last_visible_output is not None
                and seconds_since_last_visible_output >= STEP_STALL_THRESHOLD_SECONDS
            ):
                stall_reason = "active_process_without_visible_output"
            elif (
                seconds_since_step_started is not None
                and seconds_since_step_started >= STEP_STALL_THRESHOLD_SECONDS * 2
            ):
                stall_reason = "active_process_exceeded_step_threshold"
        elif (
            str(self._scope_state("main").get("phase") or "") == "idle_sleep"
            and seconds_since_main_progress is not None
            and seconds_since_main_progress >= STEP_STALL_THRESHOLD_SECONDS
        ):
            stall_reason = "idle_sleep_without_next_step"
        return {
            "seconds_since_last_progress": round(seconds_since_last_progress, 3)
            if seconds_since_last_progress is not None
            else None,
            "seconds_since_main_progress": round(seconds_since_main_progress, 3)
            if seconds_since_main_progress is not None
            else None,
            "seconds_since_last_visible_output": round(seconds_since_last_visible_output, 3)
            if seconds_since_last_visible_output is not None
            else None,
            "seconds_since_step_started": round(seconds_since_step_started, 3)
            if seconds_since_step_started is not None
            else None,
            "suspected_stall": stall_reason is not None,
            "suspected_stall_reason": stall_reason,
        }

    def _scope_for_runtime_attr(self, runtime_attr: str) -> str:
        if runtime_attr == "comment_runtime":
            return "comment"
        if runtime_attr == "log_runtime":
            return "logs"
        if runtime_attr == "heartbeat_runtime":
            return "heartbeat"
        return "main"

    async def _call_runtime(
        self,
        runtime_attr: str,
        method_name: str,
        *args: Any,
        timeout: float = RUNTIME_CALL_TIMEOUT_SECONDS,
        recreate_on_failure: bool = True,
        **kwargs: Any,
    ) -> Any:
        client = getattr(self, runtime_attr)
        scope = self._scope_for_runtime_attr(runtime_attr)
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(getattr(client, method_name), *args, **kwargs),
                timeout=timeout,
            )
            self._mark_scope_success(scope)
            return result
        except Exception:
            exc = sys.exc_info()[1]
            if exc is not None:
                self._record_scope_failure(scope, f"{method_name}: {type(exc).__name__}: {exc}")
            if recreate_on_failure:
                setattr(self, runtime_attr, self._new_runtime_client())
            raise

    async def run(self) -> None:
        self._set_scope_phase("main", "startup", operation="get_run_summary")
        run_summary = await self._run_summary()
        self._mark_progress("run_summary_loaded")
        run_config = RunConfig.model_validate(run_summary["config"])
        agent_config = self._agent_config(run_config, self.agent_id)
        await self._restore_session_state_from_conversation()
        scaffold_workspace(self.workspace, run_config, agent_config)
        self._ensure_workspace_permissions()
        self._set_scope_phase("main", "startup", operation="announce_sandbox")
        await self._announce_sandbox(run_config)
        self._mark_progress("sandbox_announced")
        await self._start_heartbeat_helper(run_config)
        self._diagnostics_task = asyncio.create_task(self._diagnostics_loop(run_config))
        self._ship_task = asyncio.create_task(self._ship_logs_loop())
        chat_task = asyncio.create_task(self._chat_loop(run_config, agent_config))
        try:
            self._set_scope_phase("main", "startup", operation="seed_autosubmit")
            seed_summary = await self._auto_submit_workspace_bot(run_config)
            if seed_summary:
                self._upsert_block(
                    block_id="seed:autosubmit",
                    step_id=None,
                    role="system",
                    kind="summary",
                    title="Seed Submission",
                    text=seed_summary,
                    collapsed=False,
                    streaming=False,
                )
            self._set_scope_phase("main", "loop", operation="agent_loop")
            await self._agent_loop(run_config, agent_config)
            await self._flush_blocks()
            await self._call_runtime(
                "session_runtime",
                "finish_sandbox",
                self.run_id,
                sandbox_id=self.sandbox_id,
                status="finished",
                error=None,
                timeout=SESSION_TOUCH_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            await self._flush_blocks()
            await self._call_runtime(
                "session_runtime",
                "finish_sandbox",
                self.run_id,
                sandbox_id=self.sandbox_id,
                status="failed",
                error=str(exc),
                timeout=SESSION_TOUCH_TIMEOUT_SECONDS,
            )
            raise
        finally:
            chat_task.cancel()
            if self._ship_task:
                await self._flush_blocks()
                self._ship_task.cancel()
            if self._diagnostics_task:
                self._diagnostics_task.cancel()
            await self._stop_heartbeat_helper()

    def _restore_session_state(self, dashboard: dict[str, Any]) -> None:
        state = dict(dashboard.get("run", {}).get("state") or {})
        agents = state.get("agents") or {}
        payload = agents.get(self.agent_id) if isinstance(agents, dict) else None
        if not isinstance(payload, dict):
            return
        last_message = payload.get("last_message")
        if isinstance(last_message, str) and last_message.strip():
            self.last_summary = last_message.strip()
        self.started = bool(
            self.last_summary
            or payload.get("current_step_started_at")
            or payload.get("last_output_at")
        )

    async def _restore_session_state_from_conversation(self) -> None:
        try:
            blocks = await self._call_runtime(
                "dashboard_runtime",
                "get_agent_conversation",
                self.run_id,
                self.agent_id,
                limit=80,
                timeout=STARTUP_QUERY_TIMEOUT_SECONDS,
            )
        except Exception:
            return
        for block in reversed(blocks):
            text = str(getattr(block, "text", "") or "").strip()
            title = str(getattr(block, "title", "") or "")
            role = str(getattr(block, "role", "") or "")
            kind = str(getattr(block, "kind", "") or "")
            if not text:
                continue
            if title in {"Sandbox Restarted", "Seed Submission", "Auto Submission", "Step Finalized"}:
                continue
            if role == "system":
                continue
            if kind not in {"prompt", "text", "tool"}:
                continue
            self.last_summary = " ".join(text.split())[:320]
            self.started = True
            return

    async def _announce_sandbox(self, run_config: RunConfig) -> None:
        ttl = max(120, int(max(1.0, run_config.agent_poll_seconds) * 10))
        metadata = {"workspace": str(self.workspace), "name": self.agent_id}
        try:
            await self._call_runtime(
                "runtime",
                "heartbeat_sandbox",
                self.run_id,
                sandbox_id=self.sandbox_id,
                status="running",
                metadata_patch=metadata,
                heartbeat_ttl_seconds=ttl,
                timeout=HEARTBEAT_CALL_TIMEOUT_SECONDS,
            )
            return
        except Exception:
            pass
        await self._call_runtime(
            "runtime",
            "register_sandbox",
            self.run_id,
            role="agent",
            sandbox_id=self.sandbox_id,
            agent_id=self.agent_id,
            status="running",
            metadata=metadata,
            heartbeat_ttl_seconds=ttl,
            timeout=SESSION_TOUCH_TIMEOUT_SECONDS,
        )

    async def _agent_loop(self, run_config: RunConfig, agent_config: AgentConfig) -> None:
        warning_sent = False
        while True:
            self._set_scope_phase("main", "fetch_step_context", operation="get_agent_step_context")
            context = await self._step_context()
            self._mark_progress("step_context_loaded")
            run = context["run"]
            if run["status"] in {"stopping", "finished", "failed"}:
                self._set_scope_phase("main", "stopped", operation=run["status"])
                return
            started_at = _from_millis(run["startedAt"])
            deadline = started_at + timedelta(minutes=run_config.duration_minutes)
            now = datetime.now(tz=UTC)
            if now >= deadline:
                self._set_scope_phase("main", "deadline_reached")
                return
            minutes_left = max(0.0, (deadline - now).total_seconds() / 60.0)
            rank = int(context.get("rank") or max(1, len(run_config.agents)))
            total_agents = int(context.get("totalAgents") or max(1, len(run_config.agents)))
            projected_rank = int(context.get("projectedRank") or rank)
            best_rating = float(context.get("bestRating") or 0.0)
            projected_payout = float(context.get("projectedPayout") or best_rating)
            equity_delta = float(context.get("equityDelta") or 0.0)
            self._set_scope_phase("main", "fetch_runtime_notice", operation="get_agent_conversation")
            runtime_notice = await self._latest_runtime_notice()
            self._set_scope_phase("main", "claim_operator_steers", operation="claim_pending_agent_steers")
            operator_steers = await self._claim_operator_steers()
            warning = not warning_sent and minutes_left <= run_config.last_warning_minutes
            prompt = self._prompt(
                run_config,
                agent_config,
                minutes_left,
                rank,
                total_agents=total_agents,
                projected_rank=projected_rank,
                best_rating=best_rating,
                projected_payout=projected_payout,
                equity_delta=equity_delta,
                runtime_notice=runtime_notice,
                operator_steers=operator_steers,
                warning=warning,
            )
            if warning:
                warning_sent = True
            step_id = f"step-{int(now.timestamp() * 1000)}"
            self._note_prompt_started()
            self._upsert_block(
                block_id=f"{step_id}:prompt",
                step_id=step_id,
                role="user",
                kind="prompt",
                title="Prompt",
                text=prompt,
                collapsed=True,
                streaming=False,
            )
            try:
                await self._call_runtime(
                    "session_runtime",
                    "touch_agent_session",
                    self.run_id,
                    agent_id=self.agent_id,
                    status="running",
                    last_message=self.last_summary,
                    sandbox_id=self.sandbox_id or None,
                    timeout=SESSION_TOUCH_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                print(f"session touch (running) failed: {type(exc).__name__}: {exc}", flush=True)
            self._set_scope_phase("main", "step_running", operation=agent_config.runtime.value)
            output, summary = await self._run_step(step_id, run_config, agent_config, prompt)
            self._set_scope_phase("main", "auto_submit", operation="workspace_bot")
            auto_submission_summary = await self._auto_submit_workspace_bot(run_config)
            if auto_submission_summary:
                auto_submit_failed = auto_submission_summary.startswith("Auto-submit skipped:")
                if not auto_submit_failed:
                    output = f"{output}\n{auto_submission_summary}".strip() if output else auto_submission_summary
                    summary = " ".join(output.split())[:320]
                self._upsert_block(
                    block_id=f"{step_id}:autosubmit",
                    step_id=step_id,
                    role="system",
                    kind="error" if auto_submit_failed else "summary",
                    title="Auto Submission Failed" if auto_submit_failed else "Auto Submission",
                    text=auto_submission_summary,
                    collapsed=not auto_submit_failed,
                    streaming=False,
                )
                if auto_submit_failed:
                    summary = auto_submission_summary[:320]
            self.started = True
            self.last_summary = summary
            if output and not any(block["step_id"] == step_id and block["role"] == "assistant" for block in self.block_buffer.blocks.values()):
                self._upsert_block(
                    block_id=f"{step_id}:response",
                    step_id=step_id,
                    role="assistant",
                    kind="text",
                    title="Response",
                    text=output,
                    collapsed=False,
                    streaming=False,
                )
            try:
                await self._call_runtime(
                    "session_runtime",
                    "touch_agent_session",
                    self.run_id,
                    agent_id=self.agent_id,
                    status="idle",
                    last_message=summary,
                    sandbox_id=self.sandbox_id or None,
                    timeout=SESSION_TOUCH_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                print(f"session touch (idle) failed: {type(exc).__name__}: {exc}", flush=True)
            self._upsert_block(
                block_id=f"{step_id}:finalized",
                step_id=step_id,
                role="system",
                kind="summary",
                title="Step Finalized",
                text=(
                    f"Step complete. Auto-submit: {'yes' if auto_submission_summary else 'no'}. "
                    f"Sleeping {run_config.agent_poll_seconds:.0f}s before next context fetch."
                ),
                collapsed=True,
                streaming=False,
            )
            self._mark_progress("step_finalized")
            try:
                await self._flush_blocks()
            except Exception as exc:
                print(f"post-step flush failed: {type(exc).__name__}: {exc}", flush=True)
            self._set_scope_phase("main", "idle_sleep", operation="sleep")
            await asyncio.sleep(run_config.agent_poll_seconds)

    async def _run_step(
        self,
        step_id: str,
        run_config: RunConfig,
        agent_config: AgentConfig,
        prompt: str,
    ) -> tuple[str, str]:
        if agent_config.runtime == AgentRuntime.MOCK:
            return await self._run_mock_step(step_id, run_config, prompt)
        command = self._command(agent_config)
        self._set_scope_phase("main", "spawn_process", operation=Path(command[0]).name)
        process = await self._spawn_process(command, prompt)
        self._note_process_started(step_id, agent_config.runtime.value, process)
        chunks: list[str] = []
        stdout_task = asyncio.create_task(self._read_stream(step_id, process.stdout, "stdout", chunks))
        stderr_task = asyncio.create_task(self._read_stream(step_id, process.stderr, "stderr", chunks))
        progress_task = asyncio.create_task(self._step_progress_loop(step_id, agent_config.runtime.value))
        returncode: int | None = None
        try:
            returncode = await process.wait()
        finally:
            progress_task.cancel()
            await asyncio.gather(progress_task, return_exceptions=True)
        self._set_scope_phase("main", "drain_streams", operation=agent_config.runtime.value)
        self._upsert_block(
            block_id=f"{step_id}:process-exit",
            step_id=step_id,
            role="system",
            kind="summary",
            title="Process Exited",
            text=(
                f"{agent_config.runtime.value} child exited with code "
                f"{returncode if returncode is not None else 'unknown'}. Draining stdout/stderr."
            ),
            collapsed=True,
            streaming=False,
        )
        done, pending = await asyncio.wait(
            {stdout_task, stderr_task},
            timeout=STREAM_DRAIN_TIMEOUT_SECONDS,
        )
        if pending:
            pending_names = sorted(
                "stdout" if task is stdout_task else "stderr"
                for task in pending
            )
            self._record_scope_failure(
                "main",
                f"stream_drain_timeout: {','.join(pending_names)} after {STREAM_DRAIN_TIMEOUT_SECONDS:.0f}s",
            )
            self._upsert_block(
                block_id=f"{step_id}:stream-drain-timeout",
                step_id=step_id,
                role="system",
                kind="error",
                title="Stream Drain Timeout",
                text=(
                    f"Child process exited, but {', '.join(pending_names)} did not close within "
                    f"{STREAM_DRAIN_TIMEOUT_SECONDS:.0f}s. Cancelling the stuck stream drain and continuing."
                ),
                collapsed=False,
                streaming=False,
            )
            for task in pending:
                task.cancel()
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        self._note_process_finished(returncode)
        self._finalize_step_blocks(step_id)
        output = self._render_step_output(step_id, fallback="".join(chunks).strip())
        summary = " ".join(output.split())[:320] if output else f"{agent_config.runtime.value} completed without output."
        if returncode != 0:
            raise RuntimeError(f"{agent_config.runtime.value} exited with {returncode}: {summary}")
        return output, summary

    async def _run_mock_step(
        self,
        step_id: str,
        run_config: RunConfig,
        prompt: str,
    ) -> tuple[str, str]:
        self._mock_revision += 1
        revision = self._mock_revision
        aggressiveness = 2 + revision + (sum(ord(ch) for ch in self.agent_id) % 4)
        bot_source = "\n".join(
            [
                "from game_of_agents.games.base import BotAction",
                "from game_of_agents.games.poker.bot import PokerBot, PokerObservation",
                "",
                "",
                "class GeneratedBot(PokerBot):",
                "    def choose_action(self, observation: PokerObservation) -> BotAction:",
                f"        threshold = {aggressiveness}",
                "        if 'raise_to' in observation.legal_actions and observation.min_raise_to is not None:",
                "            if observation.to_call <= threshold:",
                "                return BotAction('raise_to', observation.min_raise_to)",
                "        if 'check_call' in observation.legal_actions:",
                "            return BotAction('check_call')",
                "        return BotAction('fold')",
            ]
        )
        bot_path = self.workspace / "bot.py"
        bot_path.write_text(bot_source, encoding="utf-8")
        output_lines = [
            f"Mock agent {self.agent_id} revision {revision}.",
            f"Prompt excerpt: {prompt[:120]}",
        ]
        try:
            bundle = await asyncio.to_thread(
                self.runtime.upload_bundle,
                files=[bot_path],
                root=self.workspace,
                max_total_bytes=run_config.artifact_bundle_max_bytes,
            )
            submission = await asyncio.to_thread(
                self.runtime.register_bot_submission,
                self.run_id,
                agent_id=self.agent_id,
                name=f"{self.agent_id}-bot-{revision}",
                description=f"Mock revision {revision} with aggressiveness {aggressiveness}",
                entrypoint="GeneratedBot",
                module_path="bot.py",
                bundle_storage_id=str(bundle["storageId"]),
                bundle_bytes=int(bundle["totalFileBytes"]),
                file_paths=list(bundle["filePaths"]),
            )
            self._note_submission(str(submission["submission_id"]))
            output_lines.append(f"Submitted {submission['submission_id']} with threshold {aggressiveness}.")
        except Exception as exc:
            output_lines.append(f"No submission this step: {exc}")
        text = "\n".join(output_lines)
        self._upsert_block(
            block_id=f"{step_id}:response",
            step_id=step_id,
            role="assistant",
            kind="text",
            title="Response",
            text=text,
            collapsed=False,
            streaming=False,
        )
        await asyncio.sleep(0.05)
        return text, " ".join(text.split())[:320]

    async def _auto_submit_workspace_bot(self, run_config: RunConfig) -> str | None:
        bot_path = self.workspace / "bot.py"
        if not bot_path.exists():
            return None
        digest = hashlib.sha256(bot_path.read_bytes()).hexdigest()
        if digest == self._last_auto_submit_hash:
            return None
        self._auto_submit_revision += 1
        try:
            self._set_scope_phase("main", "auto_submit_upload", operation="upload_bundle")
            bundle = await asyncio.wait_for(
                asyncio.to_thread(
                    self.submission_runtime.upload_bundle,
                    files=[bot_path],
                    root=self.workspace,
                    max_total_bytes=run_config.artifact_bundle_max_bytes,
                ),
                timeout=AUTO_SUBMIT_TIMEOUT_SECONDS,
            )
            self._set_scope_phase("main", "auto_submit_register", operation="register_bot_submission")
            payload = await asyncio.wait_for(
                asyncio.to_thread(
                    self.submission_runtime.register_bot_submission,
                    self.run_id,
                    agent_id=self.agent_id,
                    name=f"{self.agent_id}-auto-{self._auto_submit_revision}",
                    description=f"Auto-submitted workspace bot revision {self._auto_submit_revision}",
                    entrypoint="WorkspaceBot",
                    module_path="bot.py",
                    bundle_storage_id=str(bundle["storageId"]),
                    bundle_bytes=int(bundle["totalFileBytes"]),
                    file_paths=list(bundle["filePaths"]),
                ),
                timeout=AUTO_SUBMIT_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            self.submission_runtime = self._new_runtime_client()
            self._record_scope_failure("main", f"auto_submit: {type(exc).__name__}: {exc}")
            return f"Auto-submit skipped: {exc}"
        self._last_auto_submit_hash = digest
        self._note_submission(str(payload["submission_id"]))
        return (
            f"Auto-submitted bot.py as {payload['submission_id']} "
            f"(revision {self._auto_submit_revision})."
        )

    def _command(self, agent_config: AgentConfig) -> list[str]:
        return prepare_runtime_command(
            agent_config.runtime,
            list(agent_config.command),
            model=agent_config.model,
            workspace=self.workspace,
            started=self.started,
        )

    async def _spawn_process(self, command: list[str], prompt: str) -> asyncio.subprocess.Process:
        args = self._command_with_prompt(command, prompt)
        env = self._command_env(args[0])
        if Path(args[0]).name == "codex":
            await self._ensure_codex_auth(env)
        if Path(args[0]).name == "claude" and os.geteuid() == 0:
            user = os.environ.get("GOA_AGENT_USER", "goaagent")
            home = Path("/home") / user
            command_line = shlex.join(args)
            shell_command = (
                f"export HOME={shlex.quote(str(home))} "
                f"USER={shlex.quote(user)} "
                f"LOGNAME={shlex.quote(user)}; "
                f"cd {shlex.quote(str(self.workspace))}; "
                f"exec script -q -c {shlex.quote(command_line)} /dev/null"
            )
            return await asyncio.create_subprocess_exec(
                "su",
                "-m",
                user,
                "-s",
                "/bin/bash",
                "-c",
                shell_command,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        return await asyncio.create_subprocess_exec(
            *args,
            cwd=self.workspace,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    def _command_env(self, binary: str) -> dict[str, str] | None:
        if Path(binary).name != "codex":
            return None
        codex_home = self.workspace / ".codex-home"
        codex_home.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["HOME"] = str(codex_home)
        env["CODEX_HOME"] = str(codex_home)
        return env

    async def _ensure_codex_auth(self, env: dict[str, str] | None) -> None:
        if self._codex_auth_bootstrapped:
            return
        if env is None:
            raise RuntimeError("codex environment is unavailable")
        api_key = env.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required for Codex")
        process = await asyncio.create_subprocess_exec(
            "codex",
            "login",
            "--with-api-key",
            cwd=self.workspace,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate(api_key.encode("utf-8"))
        if process.returncode != 0:
            message = "\n".join(
                part.strip()
                for part in (
                    stdout.decode("utf-8", errors="replace"),
                    stderr.decode("utf-8", errors="replace"),
                )
                if part.strip()
            )
            raise RuntimeError(f"codex login failed: {message or process.returncode}")
        self._codex_auth_bootstrapped = True

    def _command_with_prompt(self, command: list[str], prompt: str) -> list[str]:
        if flag := prompt_flag(command):
            index = command.index(flag)
            return [*command[: index + 1], prompt, *command[index + 1 :]]
        return [*command, prompt]

    async def _read_stream(
        self,
        step_id: str,
        stream: asyncio.StreamReader | None,
        stream_name: str,
        chunks: list[str],
    ) -> None:
        if stream is None:
            return
        buffer = ""
        while True:
            chunk = await stream.read(1024)
            if not chunk:
                break
            text = ANSI_RE.sub("", chunk.decode("utf-8", errors="replace")).replace("\r", "")
            if not text:
                continue
            self._note_output(f"{stream_name}_chunk")
            self._append_raw_stream_chunk(step_id, stream_name, text)
            chunks.append(text)
            buffer += text
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                self._emit_stream_line(step_id, stream_name, line)
        if buffer:
            self._emit_stream_line(step_id, stream_name, buffer)

    def _emit_stream_line(self, step_id: str, stream_name: str, line: str) -> None:
        if stream_name != "stdout":
            if line:
                self._note_output(f"{stream_name}_line")
                self._append_text_block(step_id, f"{line}\n", title="Stderr")
            return
        stripped = line.strip()
        if not stripped:
            return
        self._note_output("stdout_line")
        if self._handle_structured_event(step_id, stripped):
            return
        self._append_text_block(step_id, f"{line}\n")

    def _handle_structured_event(self, step_id: str, line: str) -> bool:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return False
        if str(payload.get("type") or "") == "stream_event" and isinstance(payload.get("event"), dict):
            payload = dict(payload["event"])
        event_type = str(payload.get("type") or "")
        if event_type in {
            "system",
            "message_start",
            "message_delta",
            "message_stop",
            "ping",
            "assistant",
            "result",
        }:
            return True
        if event_type not in {"content_block_start", "content_block_delta", "content_block_stop"}:
            return False
        block = payload.get("content_block") or payload.get("contentBlock") or {}
        delta = payload.get("delta") or {}
        block_type = self._structured_block_type(block, delta)
        if block_type is None:
            return True
        block_index = payload.get("index", 0)
        block_id = f"{step_id}:{block_type}:{block_index}"
        title = self._structured_block_title(block_type, block)
        if event_type == "content_block_start":
            self._upsert_block(
                block_id=block_id,
                step_id=step_id,
                role="tool" if block_type == "tool" else "assistant",
                kind="tool" if block_type == "tool" else "text",
                title=title,
                text=self._structured_block_text(block),
                collapsed=block_type == "tool",
                streaming=True,
            )
            return True
        if event_type == "content_block_delta":
            self._append_to_block(
                block_id,
                self._structured_delta_text(delta),
                streaming=True,
            )
            return True
        self._set_block_streaming(block_id, False)
        return True

    def _structured_block_type(self, block: dict[str, Any], delta: dict[str, Any]) -> str | None:
        raw_type = str(block.get("type") or delta.get("type") or "")
        if raw_type in {"tool_use", "tool"}:
            return "tool"
        if raw_type in {"text", "thinking"}:
            return "text"
        if "text" in delta:
            return "text"
        if "partial_json" in delta or "input_json_delta" in delta:
            return "tool"
        return None

    def _structured_block_title(self, block_type: str, block: dict[str, Any]) -> str:
        if block_type == "tool":
            return str(block.get("name") or "Tool")
        return "Response"

    def _structured_block_text(self, block: dict[str, Any]) -> str:
        for key in ("text", "partial_json"):
            value = block.get(key)
            if isinstance(value, str):
                return value
        input_value = block.get("input")
        if input_value is not None:
            try:
                return json.dumps(input_value, sort_keys=True)
            except TypeError:
                return str(input_value)
        return ""

    def _structured_delta_text(self, delta: dict[str, Any]) -> str:
        for key in ("text", "partial_json", "input_json_delta"):
            value = delta.get(key)
            if isinstance(value, str):
                return value
        return ""

    def _append_text_block(self, step_id: str, text: str, *, title: str = "Response") -> None:
        block_id = f"{step_id}:text"
        if block_id not in self.block_buffer.blocks:
            self._upsert_block(
                block_id=block_id,
                step_id=step_id,
                role="assistant",
                kind="text",
                title=title,
                text=text,
                collapsed=False,
                streaming=True,
            )
            return
        self._append_to_block(block_id, text, streaming=True)

    def _append_raw_stream_chunk(self, step_id: str, stream_name: str, text: str) -> None:
        block_id = f"{step_id}:raw:{stream_name}"
        title = "Raw Stdout" if stream_name == "stdout" else "Raw Stderr"
        role = "assistant" if stream_name == "stdout" else "system"
        kind = "text"
        now = _utcnow_iso()
        existing = self.block_buffer.blocks.get(block_id)
        if existing is None:
            stored = text[:MAX_RAW_STREAM_BLOCK_CHARS]
            if len(text) > MAX_RAW_STREAM_BLOCK_CHARS:
                stored += RAW_STREAM_TRUNCATED_MARKER
            self._upsert_block(
                block_id=block_id,
                step_id=step_id,
                role=role,
                kind=kind,
                title=title,
                text=stored,
                collapsed=True,
                streaming=True,
            )
            return
        current = str(existing.get("text") or "")
        if current.endswith(RAW_STREAM_TRUNCATED_MARKER) or len(current) >= MAX_RAW_STREAM_BLOCK_CHARS:
            return
        remaining = MAX_RAW_STREAM_BLOCK_CHARS - len(current)
        addition = text[:remaining]
        updated = dict(existing)
        updated["text"] = current + addition
        if len(text) > remaining:
            updated["text"] += RAW_STREAM_TRUNCATED_MARKER
        updated["streaming"] = True
        updated["updated_at"] = now
        self.block_buffer.upsert(updated)

    def _upsert_block(
        self,
        *,
        block_id: str,
        step_id: str | None,
        role: str,
        kind: str,
        title: str,
        text: str,
        collapsed: bool,
        streaming: bool,
    ) -> None:
        now = _utcnow_iso()
        block = self.block_buffer.blocks.get(block_id)
        if block is None:
            block = {
                "block_id": block_id,
                "run_id": self.run_id,
                "agent_id": self.agent_id,
                "step_id": step_id,
                "role": role,
                "kind": kind,
                "title": title,
                "text": text,
                "sandbox_id": self.sandbox_id or None,
                "collapsed": collapsed,
                "streaming": streaming,
                "created_at": now,
                "updated_at": now,
            }
        else:
            block.update(
                {
                    "text": text,
                    "streaming": streaming,
                    "updated_at": now,
                }
            )
        self.block_buffer.upsert(block)

    def _append_to_block(self, block_id: str, text: str, *, streaming: bool) -> None:
        remaining = text
        target_id = block_id
        while remaining:
            target_id = self._ensure_appendable_block(target_id)
            block = dict(self.block_buffer.blocks[target_id])
            available = MAX_LOG_BLOCK_CHARS - len(str(block["text"]))
            if available <= 0:
                target_id = self._spawn_log_continuation(target_id)
                continue
            chunk = remaining[:available]
            remaining = remaining[available:]
            block["text"] = f"{block['text']}{chunk}"
            block["streaming"] = streaming
            block["updated_at"] = _utcnow_iso()
            self.block_buffer.upsert(block)

    def _ensure_appendable_block(self, block_id: str) -> str:
        if block_id not in self.block_buffer.blocks:
            raise KeyError(block_id)
        block = self.block_buffer.blocks[block_id]
        if len(str(block["text"])) < MAX_LOG_BLOCK_CHARS:
            return block_id
        return self._spawn_log_continuation(block_id)

    def _spawn_log_continuation(self, block_id: str) -> str:
        next_id = self._next_log_segment_id(block_id)
        if next_id in self.block_buffer.blocks:
            return next_id
        source = self.block_buffer.blocks[block_id]
        segment = dict(source)
        segment["block_id"] = next_id
        segment["title"] = self._continued_title(str(source.get("title") or "Response"), next_id)
        segment["text"] = ""
        segment["created_at"] = _utcnow_iso()
        segment["updated_at"] = segment["created_at"]
        self.block_buffer.upsert(segment)
        return next_id

    def _next_log_segment_id(self, block_id: str) -> str:
        base_id, _, suffix = block_id.partition(":part")
        index = int(suffix) if suffix.isdigit() else 1
        next_index = index + 1
        return f"{base_id}:part{next_index}"

    def _continued_title(self, title: str, block_id: str) -> str:
        base_title = CONTINUATION_TITLE_RE.sub("", title)
        _, _, suffix = block_id.partition(":part")
        if suffix.isdigit():
            return f"{base_title} (cont. {suffix})"
        return base_title

    def _set_block_streaming(self, block_id: str, streaming: bool) -> None:
        block = dict(self.block_buffer.blocks[block_id])
        block["streaming"] = streaming
        block["updated_at"] = _utcnow_iso()
        self.block_buffer.upsert(block)

    def _finalize_step_blocks(self, step_id: str) -> None:
        for block_id, block in list(self.block_buffer.blocks.items()):
            if block.get("step_id") != step_id or not block.get("streaming"):
                continue
            updated = dict(block)
            updated["streaming"] = False
            updated["updated_at"] = _utcnow_iso()
            self.block_buffer.upsert(updated)

    def _render_step_output(self, step_id: str, *, fallback: str) -> str:
        parts: list[str] = []
        blocks = sorted(
            [block for block in self.block_buffer.blocks.values() if block.get("step_id") == step_id],
            key=lambda item: (item.get("created_at") or "", item.get("block_id") or ""),
        )
        for block in blocks:
            if block.get("role") != "assistant":
                continue
            text = str(block.get("text") or "").strip()
            if text:
                parts.append(text)
        rendered = "\n\n".join(parts).strip()
        return rendered or fallback

    def _ensure_workspace_permissions(self) -> None:
        user = os.environ.get("GOA_AGENT_USER", "goaagent")
        if os.geteuid() != 0:
            return
        try:
            record = pwd.getpwnam(user)
        except KeyError:
            return
        uid = record.pw_uid
        gid = record.pw_gid
        targets = [self.workspace, *self.workspace.rglob("*")]
        for path in targets:
            try:
                os.chown(path, uid, gid)
            except FileNotFoundError:
                continue

    async def _ship_logs_loop(self) -> None:
        while True:
            try:
                self._set_scope_phase("logs", "flush", operation="append_log_blocks")
                await self._flush_blocks()
                self._set_scope_phase("logs", "sleep", operation="sleep")
                await asyncio.sleep(0.5)
            except Exception as exc:
                print(f"log shipper retrying after error: {type(exc).__name__}: {exc}", flush=True)
                await asyncio.sleep(1.0)

    async def _flush_blocks(self) -> None:
        blocks = self.block_buffer.drain()
        if not blocks:
            return
        try:
            await self._call_runtime(
                "log_runtime",
                "append_log_blocks",
                self.run_id,
                agent_id=self.agent_id,
                blocks=blocks,
                timeout=LOG_FLUSH_TIMEOUT_SECONDS,
            )
            self._mark_progress("logs_flushed", scope="logs")
        except Exception:
            self.block_buffer.requeue(blocks)
            raise

    async def _heartbeat_loop(self, run_config: RunConfig) -> None:
        while True:
            try:
                await self._call_runtime(
                    "heartbeat_runtime",
                    "heartbeat_sandbox",
                    self.run_id,
                    sandbox_id=self.sandbox_id,
                    status="running",
                    heartbeat_ttl_seconds=max(120, int(max(1.0, run_config.agent_poll_seconds) * 10)),
                    timeout=HEARTBEAT_CALL_TIMEOUT_SECONDS,
                )
                await asyncio.sleep(run_config.agent_poll_seconds)
            except Exception as exc:
                print(f"heartbeat retrying after error: {type(exc).__name__}: {exc}", flush=True)
                await asyncio.sleep(1.0)

    async def _start_heartbeat_helper(self, run_config: RunConfig) -> None:
        ttl_seconds = max(120, int(max(1.0, run_config.agent_poll_seconds) * 10))
        self._heartbeat_helper = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "game_of_agents.sandbox_heartbeat",
            self.run_id,
            self.sandbox_id,
            "agent",
            str(run_config.agent_poll_seconds),
            str(ttl_seconds),
            env=os.environ.copy(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self._mark_progress("heartbeat_helper_started", scope="heartbeat")

    async def _stop_heartbeat_helper(self) -> None:
        process = self._heartbeat_helper
        self._heartbeat_helper = None
        if process is None or process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except TimeoutError:
            process.kill()
            await process.wait()

    async def _diagnostics_loop(self, run_config: RunConfig) -> None:
        interval = max(5.0, run_config.agent_poll_seconds * 2)
        ttl = max(120, int(max(1.0, run_config.agent_poll_seconds) * 10))
        while True:
            try:
                await self._ensure_heartbeat_helper_alive(run_config)
                self._refresh_stall_block()
                await self._call_runtime(
                    "heartbeat_runtime",
                    "heartbeat_sandbox",
                    self.run_id,
                    sandbox_id=self.sandbox_id,
                    status="running",
                    metadata_patch={"diagnostics": self._diagnostics_payload()},
                    heartbeat_ttl_seconds=ttl,
                    timeout=HEARTBEAT_CALL_TIMEOUT_SECONDS,
                )
                await asyncio.sleep(interval)
            except Exception as exc:
                print(f"diagnostics heartbeat retrying after error: {type(exc).__name__}: {exc}", flush=True)
                await asyncio.sleep(2.0)

    async def _ensure_heartbeat_helper_alive(self, run_config: RunConfig) -> None:
        process = self._heartbeat_helper
        if process is None or process.returncode is None:
            return
        self._record_scope_failure("heartbeat", f"heartbeat_helper exited with {process.returncode}")
        self._upsert_block(
            block_id="heartbeat-helper:restarted",
            step_id=None,
            role="system",
            kind="error",
            title="Heartbeat Helper Restarted",
            text=(
                f"The heartbeat helper exited with code {process.returncode}. "
                "Restarting it to preserve sandbox liveness diagnostics."
            ),
            collapsed=False,
            streaming=False,
        )
        await asyncio.sleep(HEARTBEAT_HELPER_RESTART_DELAY_SECONDS)
        await self._start_heartbeat_helper(run_config)

    def _refresh_stall_block(self) -> None:
        derived = self._derived_diagnostics()
        if not derived.get("suspected_stall"):
            return
        self._upsert_block(
            block_id="diagnostics:stall",
            step_id=None,
            role="system",
            kind="error",
            title="Potential Stall Detected",
            text=(
                f"reason={derived.get('suspected_stall_reason')}; "
                f"since_progress={derived.get('seconds_since_last_progress')}; "
                f"since_main_progress={derived.get('seconds_since_main_progress')}; "
                f"since_visible_output={derived.get('seconds_since_last_visible_output')}; "
                f"main_phase={self._scope_state('main').get('phase')}; "
                f"active_process={bool(self._diagnostics_state.get('active_process'))}"
            ),
            collapsed=False,
            streaming=False,
        )

    async def _step_progress_loop(self, step_id: str, runtime_name: str) -> None:
        started_at = datetime.now(tz=UTC)
        while True:
            await asyncio.sleep(60.0)
            elapsed_seconds = int((datetime.now(tz=UTC) - started_at).total_seconds())
            self._mark_progress(f"{runtime_name}_still_running:{elapsed_seconds}")
            self._upsert_block(
                block_id=f"{step_id}:running",
                step_id=step_id,
                role="system",
                kind="summary",
                title="Still Running",
                text=f"{runtime_name} step still running after {elapsed_seconds}s.",
                collapsed=True,
                streaming=False,
            )

    async def _chat_loop(self, run_config: RunConfig, agent_config: AgentConfig) -> None:
        if not run_config.comment_feed.enabled:
            return
        comment_runtime = getattr(self, "comment_runtime", self.runtime)
        poll_seconds = run_config.comment_feed.cadence_seconds
        cooldown_seconds = max(60.0, poll_seconds)
        while True:
            try:
                self._set_scope_phase("comment", "fetch_analytics", operation="get_agent_analytics")
                analytics = await self._agent_analytics(runtime=comment_runtime)
                if analytics["run"]["status"] in {"stopping", "finished", "failed"}:
                    self._set_scope_phase("comment", "stopped", operation=str(analytics["run"]["status"]))
                    return
                now = datetime.now(tz=UTC)
                if self._last_comment_action_at is not None:
                    elapsed = (now - self._last_comment_action_at).total_seconds()
                    if elapsed < cooldown_seconds:
                        self._set_scope_phase("comment", "cooldown", operation="sleep")
                        await asyncio.sleep(poll_seconds)
                        continue
                self._set_scope_phase("comment", "list_recent_messages", operation="list_recent_messages")
                messages = await self._call_runtime(
                    "comment_runtime",
                    "list_recent_messages",
                    self.run_id,
                    limit=20,
                    timeout=COMMENT_RUNTIME_TIMEOUT_SECONDS,
                )
                self._set_scope_phase("comment", "get_conversation", operation="get_agent_conversation")
                conversation = await self._call_runtime(
                    "comment_runtime",
                    "get_agent_conversation",
                    self.run_id,
                    self.agent_id,
                    limit=12,
                    timeout=COMMENT_RUNTIME_TIMEOUT_SECONDS,
                )
                self._set_scope_phase("comment", "decide", operation="comment_action")
                action = await self._comment_action(run_config, agent_config, analytics, messages, conversation)
                if action.get("type") == "post":
                    self._set_scope_phase("comment", "post_comment", operation="post_comment")
                    await self._call_runtime(
                        "comment_runtime",
                        "post_comment",
                        self.run_id,
                        author_agent_id=self.agent_id,
                        commentator_id=f"{self.agent_id}-commentator",
                        text=str(action["text"]),
                        parent_message_id=action.get("parent_message_id"),
                        timeout=COMMENT_RUNTIME_TIMEOUT_SECONDS,
                    )
                    self._last_comment_action_at = datetime.now(tz=UTC)
                    self._last_comment_error = None
                    self._mark_progress("comment_posted", scope="comment")
                elif action.get("type") == "react":
                    self._set_scope_phase("comment", "react_comment", operation="react_comment")
                    await self._call_runtime(
                        "comment_runtime",
                        "react_comment",
                        self.run_id,
                        author_agent_id=self.agent_id,
                        message_id=str(action["message_id"]),
                        emoji=str(action["emoji"]),
                        timeout=COMMENT_RUNTIME_TIMEOUT_SECONDS,
                    )
                    self._last_comment_action_at = datetime.now(tz=UTC)
                    self._last_comment_error = None
            except Exception as exc:
                message = f"Comment feed sidecar error: {type(exc).__name__}: {exc}"
                if message != self._last_comment_error:
                    self._last_comment_error = message
                    self._upsert_block(
                        block_id="comment-feed:error",
                        step_id=None,
                        role="system",
                        kind="error",
                        title="Comment Feed Error",
                        text=message,
                        collapsed=False,
                        streaming=False,
                    )
            self._set_scope_phase("comment", "sleep", operation="sleep")
            await asyncio.sleep(poll_seconds)

    async def _comment_action(
        self,
        run_config: RunConfig,
        agent_config: AgentConfig,
        analytics: dict[str, Any],
        messages: list[dict[str, Any]],
        conversation: list[Any],
    ) -> dict[str, Any]:
        self_row = dict(analytics.get("self") or {})
        leaderboard = list(analytics.get("leaderboard") or [])
        total_agents = int(analytics.get("total_agents") or len(leaderboard) or 1)
        rank = int(self_row.get("tournament_rank") or total_agents)
        projected_rank = int(self_row.get("projected_rank") or rank)
        best_rating = float(self_row.get("best_rating") or 0.0)
        projected_payout = float(self_row.get("projected_payout") or best_rating)
        equity_delta = float(self_row.get("equity_delta") or 0.0)
        leaderboard_lines = [
            (
                f"#{int(row.get('projected_rank') or index + 1)}/{total_agents} "
                f"{row.get('agent_id')}: payout {float(row.get('projected_payout') or 0.0):.2f} "
                f"(rating {float(row.get('best_rating') or 0.0):.2f}, delta {float(row.get('equity_delta') or 0.0):+.2f})"
            )
            for index, row in enumerate(leaderboard[:5])
            if isinstance(row, dict)
        ]
        if run_config.comment_feed.runtime.value == "anthropic":
            api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY")
            if api_key:
                prompt = "\n".join(
                    [
                        f"You are posting in first person as agent {self.agent_id}.",
                        f"Your commentator handle is {self.agent_id}-commentator.",
                        f"Mirror this agent personality: {agent_config.prompt}",
                        f"Maximum {run_config.comment_feed.max_chars} characters.",
                        f"Available reactions: {', '.join(run_config.chat_allowed_reactions)}.",
                        f"Tournament rank: #{rank}/{total_agents}.",
                        f"Projected payout rank: #{projected_rank}/{total_agents}.",
                        f"Best rating: {best_rating:.2f}.",
                        f"Projected payout including marketplace equity: {projected_payout:.2f}.",
                        f"Equity delta from marketplace: {equity_delta:+.2f}.",
                        "Top projected leaderboard:",
                        *leaderboard_lines,
                        "Never post more than once per minute.",
                        "Recent agent conversation:",
                        *[
                            f"{block.role}/{block.kind}: {block.text[-200:]}"
                            for block in conversation[-6:]
                        ],
                        "Recent chat messages:",
                        *[
                            f"{msg.get('author_agent_id') or msg.get('authorAgentId') or msg.get('commentator_id') or msg.get('author') or 'unknown'}: {msg.get('text') or msg.get('body') or ''}"
                            for msg in messages[-8:]
                        ],
                        'Return JSON: {"type":"post","text":"..."} or {"type":"react","message_id":"...","emoji":"..."} or {"type":"noop"}',
                    ]
                )
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        "https://api.anthropic.com/v1/messages",
                        headers={
                            "x-api-key": api_key,
                            "anthropic-version": "2023-06-01",
                            "content-type": "application/json",
                        },
                        json={
                            "model": resolve_comment_feed_model(run_config.comment_feed.model),
                            "max_tokens": 150,
                            "temperature": 0.8,
                            "messages": [{"role": "user", "content": prompt}],
                        },
                    )
                    response.raise_for_status()
                text = "".join(
                    part.get("text", "")
                    for part in response.json().get("content", [])
                    if isinstance(part, dict)
                )
                try:
                    parsed = json.loads(text[text.find("{") : text.rfind("}") + 1])
                except Exception:
                    parsed = {"type": "noop"}
                if parsed.get("type") == "post" and "text" in parsed:
                    parsed["text"] = str(parsed["text"])[: run_config.comment_feed.max_chars]
                return parsed
        if messages:
            latest = messages[-1]
            latest_author = (
                latest.get("author_agent_id")
                or latest.get("authorAgentId")
                or latest.get("commentator_id")
                or latest.get("author")
            )
            if latest_author != self.agent_id:
                return {
                    "type": "react",
                    "message_id": latest.get("message_id") or latest.get("messageId") or latest.get("commentId"),
                    "emoji": run_config.chat_allowed_reactions[0],
                }
        return {
            "type": "post",
            "text": (
                f"I'm #{projected_rank}/{total_agents} on projected payout "
                f"({projected_payout:.2f}, {equity_delta:+.2f} equity) and still shipping. Keep up."
            )[: run_config.comment_feed.max_chars],
        }

    def _prompt(
        self,
        run_config: RunConfig,
        agent_config: AgentConfig,
        minutes_left: float,
        rank: int,
        *,
        total_agents: int,
        projected_rank: int,
        best_rating: float,
        projected_payout: float,
        equity_delta: float,
        runtime_notice: str | None,
        operator_steers: list[str],
        warning: bool,
    ) -> str:
        values = prompt_values_for_run(_PromptRun(run_config), agent_config)
        values.update(
            {
                "minutes_left": f"{minutes_left:.1f}",
                "best_elo": f"{best_rating:.2f}",
                "rank": rank,
                "last_summary": self.last_summary or "No prior step summary.",
            }
        )
        template = (
            agent_config.warning_prompt_template or run_config.warning_prompt_template
            if warning
            else (
                agent_config.continue_prompt_template or run_config.continue_prompt_template
                if self.started
                else agent_config.initial_prompt_template or run_config.initial_prompt_template
            )
        )
        payout_label = (
            "Projected payout with marketplace equity"
            if run_config.marketplace_enabled
            else "Projected payout"
        )
        suffix = (
            "\n\nUse the local skill commands documented in ./TOOLS.md. "
            "Submit bot bundles with `python -m game_of_agents.agent_tools submit-bot ...`."
            "\n\nLive standings snapshot:"
            f"\n- Tournament rank: #{rank}/{total_agents}"
            f"\n- Projected payout rank: #{projected_rank}/{total_agents}"
            f"\n- Best rating: {best_rating:.2f}"
            f"\n- {payout_label}: {projected_payout:.2f}"
        )
        if run_config.marketplace_enabled:
            suffix += f"\n- Equity delta from marketplace: {equity_delta:+.2f}"
        suffix += (
            "\n\nUse `python -m game_of_agents.agent_tools stats` for a full score and equity snapshot "
            "and `python -m game_of_agents.agent_tools query-games ...` for filtered structured match data."
        )
        if run_config.marketplace_enabled:
            suffix += (
                "\nInspect marketplace offers with `python -m game_of_agents.agent_tools show-offer <offer_id>` "
                "before buying."
            )
        if run_config.chat_enabled:
            suffix += (
                "\nChat is available in this run; use the commands in `./TOOLS.md` if it helps your strategy."
            )
        if operator_steers:
            steer_lines = "\n".join(
                f"{index}. {text.strip()[:600]}"
                for index, text in enumerate(operator_steers, start=1)
                if text.strip()
            )
            if steer_lines:
                suffix += (
                    "\n\nOperator steering note:\n"
                    f"{steer_lines}\n"
                    "Address this advice directly in your next step while keeping the bot legal and stable."
                )
        if runtime_notice:
            suffix += f"\n\nTournament runtime notice:\n{runtime_notice.strip()[:800]}"
        return render_prompt_template(template, values) + suffix

    async def _dashboard(self, *, runtime: ConvexRuntimeClient | None = None) -> dict[str, Any]:
        client = runtime or self.dashboard_runtime
        runtime_attr = "comment_runtime" if client is self.comment_runtime else "dashboard_runtime"
        try:
            payload = await self._call_runtime(
                runtime_attr,
                "get_run_dashboard",
                self.run_id,
                timeout=30.0,
            )
        except Exception:
            raise
        if payload is None:
            raise RuntimeError(f"run {self.run_id} not found")
        return payload

    async def _step_context(self) -> dict[str, Any]:
        payload = await self._call_runtime(
            "dashboard_runtime",
            "get_agent_step_context",
            self.run_id,
            self.agent_id,
            timeout=RUNTIME_CALL_TIMEOUT_SECONDS,
        )
        if payload is None:
            raise RuntimeError(f"run {self.run_id} not found")
        return payload

    async def _agent_analytics(self, *, runtime: ConvexRuntimeClient | None = None) -> dict[str, Any]:
        client = runtime or self.dashboard_runtime
        runtime_attr = "comment_runtime" if client is self.comment_runtime else "dashboard_runtime"
        payload = await self._call_runtime(
            runtime_attr,
            "get_agent_analytics",
            self.run_id,
            self.agent_id,
            timeout=RUNTIME_CALL_TIMEOUT_SECONDS,
        )
        if payload is None:
            raise RuntimeError(f"run {self.run_id} not found")
        return payload

    async def _run_summary(self) -> dict[str, Any]:
        try:
            payload = await self._call_runtime(
                "dashboard_runtime",
                "get_run_summary",
                self.run_id,
                timeout=STARTUP_QUERY_TIMEOUT_SECONDS,
            )
        except Exception:
            raise
        if payload is None:
            raise RuntimeError(f"run {self.run_id} not found")
        return payload

    async def _latest_runtime_notice(self) -> str | None:
        blocks = await self._call_runtime(
            "runtime",
            "get_agent_conversation",
            self.run_id,
            self.agent_id,
            limit=40,
            timeout=RUNTIME_CALL_TIMEOUT_SECONDS,
        )
        for block in reversed(blocks):
            if (
                block.role == "system"
                and block.kind == "error"
                and block.title in {"Bot Runtime Error", "Bot Retired"}
                and block.text
            ):
                return block.text
        return None

    async def _claim_operator_steers(self) -> list[str]:
        payloads = await self._call_runtime(
            "runtime",
            "claim_pending_agent_steers",
            self.run_id,
            agent_id=self.agent_id,
            timeout=RUNTIME_CALL_TIMEOUT_SECONDS,
        )
        steers: list[str] = []
        for payload in payloads:
            text = str(payload.get("text", "")).strip()
            if not text:
                continue
            steer_id = str(payload.get("steer_id") or payload.get("steerId") or f"operator-{len(steers)}")
            self._upsert_block(
                block_id=f"steer:{steer_id}",
                step_id=None,
                role="user",
                kind="prompt",
                title="Operator Steer",
                text=text,
                collapsed=False,
                streaming=False,
            )
            steers.append(text)
        if steers:
            await self._flush_blocks()
        return steers

    def _rank_for_agent(self, dashboard: dict[str, Any]) -> int:
        leaderboard = dashboard["run"]["leaderboard"]["agents"]
        return next((index + 1 for index, row in enumerate(leaderboard) if row["agentId"] == self.agent_id), len(leaderboard))

    def _agent_config(self, run_config: RunConfig, agent_id: str) -> AgentConfig:
        for agent in run_config.agents:
            if agent.agent_id == agent_id:
                return agent
        raise RuntimeError(f"missing agent config for {agent_id}")


class _PromptRun:
    def __init__(self, config: RunConfig) -> None:
        self.config = config


def _from_millis(value: int | None) -> datetime:
    if value is None:
        return datetime.now(tz=UTC)
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _utcnow_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


async def main_async(run_id: str, agent_id: str) -> None:
    configure_logging()
    server = AgentServer(run_id, agent_id)
    await server.run()


def main() -> None:
    import sys

    if len(sys.argv) != 3:
        raise SystemExit("usage: python -m game_of_agents.agent_server <run_id> <agent_id>")
    asyncio.run(main_async(sys.argv[1], sys.argv[2]))


if __name__ == "__main__":
    main()
