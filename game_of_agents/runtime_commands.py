from __future__ import annotations

from pathlib import Path

from game_of_agents.models import AgentRuntime


OPENCODE_MODEL_ALIASES = {
    "grok-4": "openrouter/x-ai/grok-4",
    "grok-3": "openrouter/x-ai/grok-3-beta",
    "deepseek-r1": "openrouter/deepseek/deepseek-r1:free",
    "deepseek-v3": "openrouter/deepseek/deepseek-chat-v3.1",
    "qwen-3-coder": "openrouter/qwen/qwen3-coder",
    "qwen-3-235b": "openrouter/qwen/qwen3-235b-a22b",
}


def resolve_opencode_model(model: str) -> str:
    if "/" in model:
        return model
    return OPENCODE_MODEL_ALIASES.get(model, model)


def default_runtime_command(runtime: AgentRuntime, model: str) -> list[str]:
    if runtime == AgentRuntime.CLAUDE:
        return ["claude", "--model", model]
    if runtime == AgentRuntime.CODEX:
        return [
            "codex",
            "exec",
            "--model",
            model,
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "-c",
            'model_reasoning_effort="medium"',
        ]
    if runtime == AgentRuntime.GEMINI:
        return [
            "gemini",
            "--model",
            model,
            "--prompt",
            "--yolo",
        ]
    if runtime == AgentRuntime.OPENCODE:
        return [
            "opencode",
            "run",
            "--model",
            resolve_opencode_model(model),
            "--format",
            "json",
        ]
    return [runtime.value, "--model", model]


def prepare_runtime_command(
    runtime: AgentRuntime,
    command: list[str],
    *,
    model: str | None,
    workspace: Path,
    started: bool,
) -> list[str]:
    if not command:
        if not model:
            return [runtime.value]
        command = default_runtime_command(runtime, model)
    binary = Path(command[0]).name
    if runtime == AgentRuntime.CLAUDE and binary == "claude":
        return _prepare_claude(command, workspace=workspace, started=started)
    if runtime == AgentRuntime.CODEX and binary == "codex":
        return _prepare_codex(command, model=model, started=started)
    if runtime == AgentRuntime.GEMINI and binary == "gemini":
        return _prepare_gemini(command, model=model, started=started)
    if runtime == AgentRuntime.OPENCODE and binary == "opencode":
        return _prepare_opencode(command, model=model, workspace=workspace, started=started)
    return list(command)


def prompt_flag(command: list[str]) -> str | None:
    for flag in ("--prompt", "-p", "--print"):
        if flag in command:
            return flag
    return None


def _prepare_claude(command: list[str], *, workspace: Path, started: bool) -> list[str]:
    prepared = list(command)
    if "-p" not in prepared and "--print" not in prepared:
        prepared.append("-p")
    if "--permission-mode" not in prepared:
        prepared.extend(["--permission-mode", "bypassPermissions"])
    if "--output-format" not in prepared:
        prepared.extend(["--output-format", "stream-json"])
    if "--verbose" not in prepared:
        prepared.append("--verbose")
    if "--include-partial-messages" not in prepared:
        prepared.append("--include-partial-messages")
    if "--add-dir" not in prepared:
        prepared.extend(["--add-dir", str(workspace)])
    if started and "--continue" not in prepared and "-c" not in prepared:
        prepared.append("--continue")
    return prepared


def _prepare_codex(command: list[str], *, model: str | None, started: bool) -> list[str]:
    prepared = [command[0]]
    remainder = list(command[1:])
    if remainder and remainder[0] == "exec":
        remainder.pop(0)
    if remainder and remainder[0] == "resume":
        remainder.pop(0)
    remainder = [item for item in remainder if item != "--last"]
    prepared.append("exec")
    if started:
        prepared.extend(["resume", "--last"])
    prepared.extend(remainder)
    if model and "--model" not in prepared and "-m" not in prepared:
        prepared.extend(["--model", model])
    if "--skip-git-repo-check" not in prepared:
        prepared.append("--skip-git-repo-check")
    if "--dangerously-bypass-approvals-and-sandbox" not in prepared:
        prepared.append("--dangerously-bypass-approvals-and-sandbox")
    if "--json" not in prepared:
        prepared.append("--json")
    if not _has_codex_reasoning_effort(prepared):
        prepared.extend(["-c", 'model_reasoning_effort="medium"'])
    return prepared


def _has_codex_reasoning_effort(command: list[str]) -> bool:
    for index, item in enumerate(command):
        if item in {"-c", "--config"} and index + 1 < len(command):
            if command[index + 1].split("=", 1)[0] == "model_reasoning_effort":
                return True
    return False


def _prepare_gemini(command: list[str], *, model: str | None, started: bool) -> list[str]:
    prepared = list(command)
    if model and "--model" not in prepared and "-m" not in prepared:
        prepared.extend(["--model", model])
    if "--prompt" not in prepared and "-p" not in prepared and "--prompt-interactive" not in prepared:
        prepared.append("--prompt")
    if "--yolo" not in prepared and "-y" not in prepared and "--approval-mode" not in prepared:
        prepared.append("--yolo")
    if "--output-format" not in prepared and "-o" not in prepared:
        prepared.extend(["--output-format", "stream-json"])
    if started and "--resume" not in prepared and "-r" not in prepared:
        prepared.extend(["--resume", "latest"])
    return prepared


def _prepare_opencode(
    command: list[str],
    *,
    model: str | None,
    workspace: Path,
    started: bool,
) -> list[str]:
    prepared = [command[0]]
    remainder = list(command[1:])
    if remainder and remainder[0] == "run":
        remainder.pop(0)
    prepared.extend(["run", *remainder])
    resolved_model = resolve_opencode_model(model) if model else None
    if resolved_model and "--model" not in prepared and "-m" not in prepared:
        prepared.extend(["--model", resolved_model])
    if "--format" not in prepared:
        prepared.extend(["--format", "json"])
    if "--dir" not in prepared:
        prepared.extend(["--dir", str(workspace)])
    if started and "--continue" not in prepared and "-c" not in prepared and "--session" not in prepared:
        prepared.append("--continue")
    return prepared
