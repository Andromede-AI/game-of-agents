from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import shlex
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

import modal

from game_of_agents.agents.base import AgentContext, AgentRunner
from game_of_agents.modal_runtime import (
    AGENT_ROLE,
    SANDBOX_ROLE_ENV,
    app_secret,
    data_volume,
    image,
    in_controller_sandbox,
    lookup_app,
)
from game_of_agents.models import AgentConfig, BotArtifact, BotSubmissionRequest
from game_of_agents.prompting import (
    prompt_values_for_context,
    render_prompt_template,
)
from game_of_agents.runtime_commands import prepare_runtime_command, prompt_flag
from game_of_agents.settings import settings


@dataclass
class CommandRunState:
    started: bool = False
    submission_count: int = 0
    last_submitted_hash: str | None = None
    last_prompt: str = ""
    last_summary: str = ""
    last_full_output: str = ""
    sandbox_id: str | None = None
    structured_buffer: str = ""
    structured_output: str = ""
    auth_bootstrapped: bool = False


class CommandAgentRunner(AgentRunner):
    def __init__(
        self,
        runtime_name: str,
        default_timeout_seconds: float | None = None,
        read_hook: Callable[[], None] | None = None,
        write_hook: Callable[[], None] | None = None,
    ) -> None:
        self.runtime_name = runtime_name
        self.default_timeout_seconds = default_timeout_seconds
        self._read_hook = read_hook
        self._write_hook = write_hook
        self._state: dict[tuple[str, str], CommandRunState] = {}
        self._sandboxes: dict[str, modal.Sandbox] = {}

    async def step(self, context: AgentContext) -> BotSubmissionRequest | None:
        agent_config = self._agent_config(context)
        command = self._command(agent_config, context)
        workspace = self._workspace_path(context.agent.workspace)
        self._prepare_workspace(workspace)
        bot_path = workspace / "bot.py"
        before = bot_path.read_text(encoding="utf-8") if bot_path.exists() else ""
        before_hash = self._hash(before)
        state = self._state_for(context)
        prompt = self._prompt(context, state)
        state.last_prompt = prompt
        state.structured_buffer = ""
        state.structured_output = ""
        combined_output, returncode = await self._run_command(
            context=context,
            command=command,
            prompt=prompt,
            workspace=workspace,
        )
        state.started = True
        display_output = state.structured_output.strip() or combined_output
        state.last_full_output = display_output
        state.last_summary = self._shorten(display_output) or f"{self.runtime_name} completed without output."
        if returncode != 0:
            raise RuntimeError(
                f"{self.runtime_name} exited with {returncode}: {state.last_summary}"
            )

        after = await self._read_workspace_text(context, bot_path)
        after_hash = self._hash(after)
        if after_hash == before_hash or after_hash == state.last_submitted_hash:
            return None

        state.submission_count += 1
        state.last_submitted_hash = after_hash
        return BotSubmissionRequest(
            agent_id=context.agent.agent_id,
            name=f"{context.agent.agent_id}-bot-{state.submission_count}",
            description=self._shorten(
                f"{self.runtime_name} revision {state.submission_count}. {state.last_summary}",
                limit=220,
            ),
            entrypoint="WorkspaceBot",
            module_path="bot.py",
            artifacts=[BotArtifact(path="bot.py", content=after)],
        )

    async def continue_message(self, context: AgentContext) -> str:
        state = self._state_for(context)
        agent_config = self._agent_config(context)
        template = (
            self._warning_template(agent_config, context)
            if context.last_warning
            else self._continue_template(agent_config, context)
        )
        return render_prompt_template(
            template,
            prompt_values_for_context(context, agent_config, last_summary=state.last_summary),
        )

    def last_full_output(self, context: AgentContext) -> str | None:
        state = self._state.get((context.run.run_id, context.agent.agent_id))
        if state and state.last_full_output:
            return state.last_full_output
        return None

    def last_prompt(self, context: AgentContext) -> str | None:
        state = self._state.get((context.run.run_id, context.agent.agent_id))
        if state and state.last_prompt:
            return state.last_prompt
        return None

    def step_prompt(self, context: AgentContext) -> str | None:
        return self._prompt(context, self._state_for(context))

    def last_summary(self, context: AgentContext) -> str | None:
        state = self._state.get((context.run.run_id, context.agent.agent_id))
        if state and state.last_summary:
            return state.last_summary
        return None

    async def shutdown_run(self, run) -> None:
        sandbox_ids = {
            state.sandbox_id
            for (run_id, _), state in self._state.items()
            if run_id == run.run_id and state.sandbox_id
        }
        for sandbox_id in sandbox_ids:
            await self._terminate_sandbox(sandbox_id)

    def _agent_config(self, context: AgentContext) -> AgentConfig:
        for agent_config in context.run.config.agents:
            if agent_config.agent_id == context.agent.agent_id:
                return agent_config
        raise RuntimeError(f"missing agent config for {context.agent.agent_id}")

    def _command(self, agent_config: AgentConfig, context: AgentContext) -> list[str]:
        state = self._state_for(context)
        return prepare_runtime_command(
            agent_config.runtime,
            list(agent_config.command),
            model=agent_config.model,
            workspace=self._workspace_path(context.agent.workspace),
            started=state.started,
        )

    def _prompt(self, context: AgentContext, state: CommandRunState) -> str:
        agent_config = self._agent_config(context)
        if state.started:
            return self._continue_prompt(context, agent_config)
        return render_prompt_template(
            self._initial_template(agent_config, context),
            prompt_values_for_context(context, agent_config, last_summary=state.last_summary),
        )

    def _continue_prompt(self, context: AgentContext, agent_config: AgentConfig) -> str:
        return render_prompt_template(
            self._continue_template(agent_config, context),
            prompt_values_for_context(
                context,
                agent_config,
                last_summary=self._state_for(context).last_summary,
            ),
        )

    def _initial_template(self, agent_config: AgentConfig, context: AgentContext) -> str:
        return agent_config.initial_prompt_template or context.run.config.initial_prompt_template

    def _continue_template(self, agent_config: AgentConfig, context: AgentContext) -> str:
        return agent_config.continue_prompt_template or context.run.config.continue_prompt_template

    def _warning_template(self, agent_config: AgentConfig, context: AgentContext) -> str:
        return agent_config.warning_prompt_template or context.run.config.warning_prompt_template

    def _state_for(self, context: AgentContext) -> CommandRunState:
        key = (context.run.run_id, context.agent.agent_id)
        state = self._state.get(key)
        if state is None:
            state = CommandRunState()
            self._state[key] = state
        return state

    def _timeout_seconds(self, context: AgentContext) -> float:
        remaining_budget = max(5.0, context.minutes_left * 60.0)
        if self.default_timeout_seconds is None:
            return remaining_budget
        return min(float(self.default_timeout_seconds), remaining_budget)

    def _hash(self, content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _shorten(self, content: str, limit: int = 320) -> str:
        single_line = " ".join(content.split())
        return single_line[:limit].strip()

    def _workspace_path(self, raw_workspace: str) -> Path:
        workspace = Path(raw_workspace)
        if workspace.exists():
            return workspace
        if raw_workspace.startswith("/__modal/volumes/") and "/workspaces/" in raw_workspace:
            _, suffix = raw_workspace.split("/workspaces/", 1)
            mounted_path = settings.data_dir / "workspaces" / suffix
            if mounted_path.exists():
                return mounted_path
        if raw_workspace.startswith("/root/.goa_data/"):
            relative = Path(raw_workspace).relative_to("/root/.goa_data")
            mounted_path = settings.data_dir / relative
            if mounted_path.exists():
                return mounted_path
        if not workspace.is_absolute():
            mounted_workspace = Path("/root") / workspace
            if mounted_workspace.exists():
                return mounted_workspace
        return workspace

    async def _run_command(
        self,
        context: AgentContext,
        command: list[str],
        prompt: str,
        workspace: Path,
    ) -> tuple[str, int]:
        if in_controller_sandbox():
            return await self._run_in_modal_sandbox(context, command, prompt, workspace)
        return await self._run_locally(context, command, prompt, workspace)

    async def _run_locally(
        self,
        context: AgentContext,
        command: list[str],
        prompt: str,
        workspace: Path,
    ) -> tuple[str, int]:
        env = await self._ensure_local_runtime_auth(context, workspace, command)
        process = await self._spawn_process(
            command=command,
            prompt=prompt,
            workspace=workspace,
            env=env,
        )
        chunks: list[str] = []
        readers = [
            asyncio.create_task(self._read_stream(context, process.stdout, "stdout", chunks)),
            asyncio.create_task(self._read_stream(context, process.stderr, "stderr", chunks)),
        ]
        timeout = self._timeout_seconds(context)
        try:
            returncode = await asyncio.wait_for(process.wait(), timeout=timeout)
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            for reader in readers:
                await reader
            raise RuntimeError(f"{self.runtime_name} step timed out") from exc
        for reader in readers:
            await reader
        return "".join(chunks).strip(), returncode

    async def _spawn_process(
        self,
        command: list[str],
        prompt: str,
        workspace: Path,
        env: dict[str, str] | None = None,
    ) -> asyncio.subprocess.Process:
        args = self._command_with_prompt(command, prompt)
        if self.runtime_name == "claude" and os.geteuid() == 0:
            return await self._spawn_claude_as_non_root(args, workspace, env=env)
        return await asyncio.create_subprocess_exec(
            *args,
            cwd=workspace,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def _run_in_modal_sandbox(
        self,
        context: AgentContext,
        command: list[str],
        prompt: str,
        workspace: Path,
    ) -> tuple[str, int]:
        sandbox = await self._ensure_sandbox(context)
        await self._ensure_modal_runtime_auth(context, sandbox, workspace, command)
        args = self._command_with_prompt(command, prompt)
        process = await sandbox.exec.aio(
            *self._sandbox_exec_args(args, workspace),
            timeout=max(5, int(self._timeout_seconds(context))),
            workdir="/root" if self.runtime_name == "claude" else str(workspace),
            text=True,
            pty=self.runtime_name == "claude",
        )
        chunks: list[str] = []
        readers = [
            asyncio.create_task(self._read_modal_stream(context, process.stdout, "stdout", chunks)),
            asyncio.create_task(self._read_modal_stream(context, process.stderr, "stderr", chunks)),
        ]
        returncode = await process.wait.aio()
        for reader in readers:
            await reader
        if returncode == -1:
            sandbox_id = self._state_for(context).sandbox_id
            if sandbox_id:
                await self._terminate_sandbox(sandbox_id)
            raise RuntimeError(f"{self.runtime_name} step timed out")
        return "".join(chunks).strip(), returncode

    async def _ensure_sandbox(self, context: AgentContext) -> modal.Sandbox:
        state = self._state_for(context)
        if state.sandbox_id:
            sandbox = self._sandboxes.get(state.sandbox_id)
            if sandbox is None:
                sandbox = await modal.Sandbox.from_id.aio(state.sandbox_id)
                self._sandboxes[state.sandbox_id] = sandbox
            exit_code = await sandbox.poll.aio()
            if exit_code is None:
                return sandbox
            await self._emit_event(
                context,
                "agent.sandbox.exited",
                {"agent_id": context.agent.agent_id, "sandbox_id": state.sandbox_id, "exit_code": exit_code},
            )
            self._sandboxes.pop(state.sandbox_id, None)
            state.sandbox_id = None
        elif context.agent.sandbox_name:
            try:
                sandbox = await modal.Sandbox.from_name.aio("game-of-agents", context.agent.sandbox_name)
            except Exception:
                sandbox = None
            if sandbox is not None:
                exit_code = await sandbox.poll.aio()
                if exit_code is None:
                    state.sandbox_id = sandbox.object_id
                    self._sandboxes[sandbox.object_id] = sandbox
                    await self._update_agent_state(
                        context,
                        {
                            "sandbox_id": sandbox.object_id,
                            "sandbox_status": "running",
                            "last_activity_at": self._now(),
                        },
                    )
                    return sandbox

        app = await lookup_app()
        sandbox_name = context.agent.sandbox_name or self._sandbox_name(context)
        sandbox = await modal.Sandbox.create.aio(
            "sleep",
            "7200",
            app=app,
            name=sandbox_name,
            image=image,
            secrets=[app_secret],
            volumes={"/goa_data": data_volume},
            timeout=max(900, int(context.minutes_left * 60.0) + 900),
            idle_timeout=max(600, int(context.minutes_left * 60.0) + 300),
            workdir="/root",
            cpu=2,
            memory=2048,
            env={
                SANDBOX_ROLE_ENV: AGENT_ROLE,
                "GOA_AGENT_ID": context.agent.agent_id,
                "GOA_RUN_ID": context.run.run_id,
                "DATA_DIR": "/goa_data",
            },
        )
        state.sandbox_id = sandbox.object_id
        self._sandboxes[sandbox.object_id] = sandbox
        await self._update_agent_state(
            context,
            {
                "sandbox_id": sandbox.object_id,
                "sandbox_name": sandbox_name,
                "sandbox_status": "running",
                "last_activity_at": self._now(),
            },
        )
        await self._emit_event(
            context,
            "agent.sandbox.started",
            {
                "agent_id": context.agent.agent_id,
                "sandbox_id": sandbox.object_id,
                "sandbox_name": sandbox_name,
            },
        )
        return sandbox

    async def _terminate_sandbox(self, sandbox_id: str) -> None:
        sandbox = self._sandboxes.pop(sandbox_id, None)
        if sandbox is None:
            sandbox = await modal.Sandbox.from_id.aio(sandbox_id)
        try:
            await sandbox.terminate.aio()
        except Exception:
            return

    def _sandbox_exec_args(self, args: list[str], workspace: Path) -> list[str]:
        if self.runtime_name != "claude":
            if self.runtime_name == "codex":
                codex_home = self._codex_home(workspace)
                shell_command = (
                    f"export HOME={shlex.quote(str(codex_home))} "
                    f"CODEX_HOME={shlex.quote(str(codex_home))}; "
                    f"mkdir -p {shlex.quote(str(codex_home))}; "
                    f"cd {shlex.quote(str(workspace))}; "
                    f"exec {shlex.join(args)}"
                )
                return ["bash", "-lc", shell_command]
            return [*args]
        user = os.environ.get("GOA_AGENT_USER", "goaagent")
        home = Path("/home") / user
        shell_command = (
            f"export HOME={shlex.quote(str(home))} "
            f"USER={shlex.quote(user)} "
            f"LOGNAME={shlex.quote(user)}; "
            f"cd {shlex.quote(str(workspace))}; "
            f"exec {shlex.join(args)}"
        )
        return ["su", "-m", user, "-s", "/bin/bash", "-c", shell_command]

    async def _spawn_claude_as_non_root(
        self,
        args: list[str],
        workspace: Path,
        env: dict[str, str] | None = None,
    ) -> asyncio.subprocess.Process:
        user = os.environ.get("GOA_AGENT_USER", "goaagent")
        home = Path("/home") / user
        shell_command = (
            f"export HOME={shlex.quote(str(home))} "
            f"USER={shlex.quote(user)} "
            f"LOGNAME={shlex.quote(user)}; "
            f"cd {shlex.quote(str(workspace))}; "
            f"exec {shlex.join(args)}"
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

    async def _ensure_local_runtime_auth(
        self,
        context: AgentContext,
        workspace: Path,
        command: list[str],
    ) -> dict[str, str] | None:
        if Path(command[0]).name != "codex":
            return None
        state = self._state_for(context)
        env = self._codex_env(workspace)
        if state.auth_bootstrapped:
            return env
        await self._codex_login_local(workspace, env)
        state.auth_bootstrapped = True
        return env

    async def _ensure_modal_runtime_auth(
        self,
        context: AgentContext,
        sandbox: modal.Sandbox,
        workspace: Path,
        command: list[str],
    ) -> None:
        if Path(command[0]).name != "codex":
            return
        state = self._state_for(context)
        if state.auth_bootstrapped:
            return
        codex_home = self._codex_home(workspace)
        login_command = (
            f"export HOME={shlex.quote(str(codex_home))} "
            f"CODEX_HOME={shlex.quote(str(codex_home))}; "
            f"mkdir -p {shlex.quote(str(codex_home))}; "
            "printf %s \"$OPENAI_API_KEY\" | codex login --with-api-key"
        )
        process = await sandbox.exec.aio(
            "bash",
            "-lc",
            login_command,
            workdir=str(workspace),
            text=True,
        )
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        async for chunk in process.stdout:
            stdout_chunks.append(chunk if isinstance(chunk, str) else chunk.decode("utf-8", errors="replace"))
        async for chunk in process.stderr:
            stderr_chunks.append(chunk if isinstance(chunk, str) else chunk.decode("utf-8", errors="replace"))
        returncode = await process.wait.aio()
        if returncode != 0:
            message = "\n".join(part.strip() for part in ("".join(stdout_chunks), "".join(stderr_chunks)) if part.strip())
            raise RuntimeError(f"codex login failed: {message or returncode}")
        state.auth_bootstrapped = True

    async def _codex_login_local(self, workspace: Path, env: dict[str, str]) -> None:
        api_key = env.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required for Codex")
        process = await asyncio.create_subprocess_exec(
            "codex",
            "login",
            "--with-api-key",
            cwd=workspace,
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

    def _codex_env(self, workspace: Path) -> dict[str, str]:
        codex_home = self._codex_home(workspace)
        codex_home.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["HOME"] = str(codex_home)
        env["CODEX_HOME"] = str(codex_home)
        return env

    def _codex_home(self, workspace: Path) -> Path:
        return workspace / ".codex-home"

    async def _read_stream(
        self,
        context: AgentContext,
        stream: asyncio.StreamReader | None,
        stream_name: str,
        chunks: list[str],
    ) -> None:
        if stream is None:
            return
        while True:
            chunk = await stream.read(1024)
            if not chunk:
                break
            text = chunk.decode("utf-8", errors="replace")
            chunks.append(text)
            await self._on_output(context, stream_name, text)

    async def _read_modal_stream(
        self,
        context: AgentContext,
        stream,
        stream_name: str,
        chunks: list[str],
    ) -> None:
        async for chunk in stream:
            text = chunk if isinstance(chunk, str) else chunk.decode("utf-8", errors="replace")
            text = self._strip_ansi(text)
            if not text:
                continue
            chunks.append(text)
            await self._on_output(context, stream_name, text)

    async def _on_output(self, context: AgentContext, stream_name: str, text: str) -> None:
        payload = {
            "agent_id": context.agent.agent_id,
            "step_id": context.step_id,
            "stream": stream_name,
            "chunk": text,
        }
        await self._emit_event(context, "agent.output.chunk", payload)
        await self._emit_structured_output(context, stream_name, text)
        now = self._now()
        await self._update_agent_state(
            context,
            {
                "last_activity_at": now,
                "last_output_at": now,
            },
        )

    async def _emit_event(self, context: AgentContext, kind: str, payload: dict[str, object]) -> None:
        if context.report_event is not None:
            await context.report_event(kind, payload)

    async def _update_agent_state(self, context: AgentContext, updates: dict[str, object]) -> None:
        if context.update_agent_state is not None:
            await context.update_agent_state(updates)

    def _now(self) -> datetime:
        return datetime.now(tz=UTC)

    def _prepare_workspace(self, workspace: Path) -> None:
        self._run_hook(self._read_hook)
        for ancestor in (workspace, *workspace.parents):
            if not ancestor.exists():
                continue
            self._chmod_path(ancestor, is_dir=True)
            if ancestor == ancestor.parent:
                break
        for path in workspace.rglob("*"):
            self._chmod_path(path, is_dir=path.is_dir())
        self._run_hook(self._write_hook)

    def _chmod_path(self, path: Path, *, is_dir: bool) -> None:
        mode = 0o777 if is_dir else 0o666
        try:
            path.chmod(mode)
        except OSError:
            return

    def _command_with_prompt(self, command: list[str], prompt: str) -> list[str]:
        if flag := prompt_flag(command):
            index = command.index(flag)
            return [*command[: index + 1], prompt, *command[index + 1 :]]
        return [*command, prompt]

    def _strip_ansi(self, text: str) -> str:
        # PTY-backed Claude runs include terminal control sequences we don't want in logs or summaries.
        ansi = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\].*?(?:\x07|\x1B\\))")
        cleaned = ansi.sub("", text).replace("\x07", "").replace("\r", "")
        return cleaned.replace("0;", "")

    def _sandbox_name(self, context: AgentContext) -> str:
        return f"goa-agent-{context.run.run_id}-{context.agent.agent_id}"

    async def _emit_structured_output(self, context: AgentContext, stream_name: str, text: str) -> None:
        if self.runtime_name != "claude" or stream_name != "stdout":
            return
        state = self._state_for(context)
        state.structured_buffer += text
        while "\n" in state.structured_buffer:
            line, state.structured_buffer = state.structured_buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            await self._emit_structured_json_event(context, payload)

    async def _emit_structured_json_event(
        self,
        context: AgentContext,
        payload: dict[str, object],
    ) -> None:
        event_type = str(payload.get("type") or "")
        block_index = payload.get("index")
        block = payload.get("content_block") or payload.get("contentBlock") or payload.get("block")
        delta = payload.get("delta")
        if not isinstance(block, dict):
            block = {}
        if not isinstance(delta, dict):
            delta = {}

        if event_type == "content_block_start":
            self._append_structured_output(context, self._structured_block_title(self._structured_block_type(block, None) or "text", block), text=self._structured_block_text(block))
            await self._emit_block_event(
                context,
                block_index,
                block,
                action="start",
                text=self._structured_block_text(block),
            )
            return
        if event_type == "content_block_delta":
            block_type = self._structured_block_type(block, delta)
            if block_type is None:
                return
            text = self._structured_delta_text(delta)
            if not text:
                return
            self._append_structured_output(context, self._structured_block_title(block_type, block), text=text, block_type=block_type)
            await self._emit_event(
                context,
                "agent.output.block",
                {
                    "agent_id": context.agent.agent_id,
                    "step_id": context.step_id,
                    "block_id": self._structured_block_id(context, block_index, block_type),
                    "block_kind": block_type,
                    "title": self._structured_block_title(block_type, block),
                    "action": "append",
                    "text": text,
                },
            )
            return
        if event_type == "content_block_stop":
            block_type = self._structured_block_type(block, delta) or "text"
            await self._emit_event(
                context,
                "agent.output.block",
                {
                    "agent_id": context.agent.agent_id,
                    "step_id": context.step_id,
                    "block_id": self._structured_block_id(context, block_index, block_type),
                    "block_kind": block_type,
                    "title": self._structured_block_title(block_type, block),
                    "action": "stop",
                    "text": "",
                },
            )

    async def _emit_block_event(
        self,
        context: AgentContext,
        block_index: object,
        block: dict[str, object],
        *,
        action: str,
        text: str,
    ) -> None:
        block_type = self._structured_block_type(block, None)
        if block_type is None:
            return
        await self._emit_event(
            context,
            "agent.output.block",
            {
                "agent_id": context.agent.agent_id,
                "step_id": context.step_id,
                "block_id": self._structured_block_id(context, block_index, block_type),
                "block_kind": block_type,
                "title": self._structured_block_title(block_type, block),
                "action": action,
                "text": text,
            },
        )

    def _structured_block_id(self, context: AgentContext, block_index: object, block_type: str) -> str:
        index = block_index if isinstance(block_index, int) else 0
        return f"{context.step_id or 'step'}:{block_type}:{index}"

    def _structured_block_type(
        self,
        block: dict[str, object] | None,
        delta: dict[str, object] | None,
    ) -> str | None:
        block = block or {}
        delta = delta or {}
        raw_type = str(block.get("type") or delta.get("type") or "")
        if raw_type in {"tool_use", "tool"}:
            return "tool"
        if raw_type in {"text", "thinking"}:
            return "text"
        if "text" in delta or "partial_json" in delta or "input_json_delta" in delta:
            return "text" if "text" in delta else "tool"
        return None

    def _structured_block_title(self, block_type: str, block: dict[str, object]) -> str:
        if block_type == "tool":
            return str(block.get("name") or "Tool")
        return "Response"

    def _structured_delta_text(self, delta: dict[str, object]) -> str:
        for key in ("text", "partial_json", "input_json_delta"):
            value = delta.get(key)
            if isinstance(value, str):
                return value
        return ""

    def _structured_block_text(self, block: dict[str, object]) -> str:
        for key in ("text", "partial_json"):
            value = block.get(key)
            if isinstance(value, str):
                return value
        input_value = block.get("input")
        if isinstance(input_value, str):
            return input_value
        if input_value is not None:
            try:
                return json.dumps(input_value, sort_keys=True)
            except TypeError:
                return str(input_value)
        return ""

    def _append_structured_output(
        self,
        context: AgentContext,
        title: str,
        *,
        text: str,
        block_type: str = "text",
    ) -> None:
        if not text:
            return
        state = self._state_for(context)
        if block_type == "tool":
            prefix = f"\n[{title}] "
        else:
            prefix = ""
        state.structured_output += f"{prefix}{text}"

    async def _read_workspace_text(self, context: AgentContext, path: Path) -> str:
        if not in_controller_sandbox():
            self._run_hook(self._read_hook)
            return path.read_text(encoding="utf-8") if path.exists() else ""
        sandbox_id = self._state_for(context).sandbox_id
        if not sandbox_id:
            self._run_hook(self._read_hook)
            return path.read_text(encoding="utf-8") if path.exists() else ""
        sandbox = self._sandboxes.get(sandbox_id)
        if sandbox is None:
            sandbox = await modal.Sandbox.from_id.aio(sandbox_id)
            self._sandboxes[sandbox_id] = sandbox
        process = await sandbox.exec.aio(
            "python",
            "-c",
            (
                "from pathlib import Path; import base64, sys; "
                "path = Path(sys.argv[1]); "
                "sys.stdout.write(base64.b64encode(path.read_bytes()).decode() if path.exists() else '')"
            ),
            str(path),
            workdir="/root",
            timeout=30,
            text=True,
        )
        chunks: list[str] = []
        async for chunk in process.stdout:
            chunks.append(chunk if isinstance(chunk, str) else chunk.decode("utf-8", errors="replace"))
        await process.wait.aio()
        encoded = "".join(chunks).strip()
        content = base64.b64decode(encoded).decode("utf-8") if encoded else ""
        if content:
            path.write_text(content, encoding="utf-8")
            self._run_hook(self._write_hook)
        return content

    def _run_hook(self, hook: Callable[[], None] | None) -> None:
        if hook is None:
            return
        try:
            hook()
        except RuntimeError as exc:
            if "open files preventing the operation" not in str(exc):
                raise
