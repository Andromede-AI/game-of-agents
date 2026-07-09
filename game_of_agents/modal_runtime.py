from __future__ import annotations

import os
from pathlib import Path

import modal


SECRET_KEYS = (
    "API_TOKEN",
    "ANTHROPIC_API_KEY",
    "CLAUDE_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "XAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENROUTER_API_KEY",
    "VERCEL_AI_GATEWAY_API_KEY",
    "VERCEL_AI_GATEWAY_BASE_URL",
    "CONVEX_URL",
    "CONVEX_SITE_URL",
    "CONVEX_DEPLOYMENT",
    "CONVEX_DEPLOY_KEY",
    "CONVEX_SYNC_TOKEN",
    "NEXT_PUBLIC_CONVEX_URL",
    "MODAL_TOKEN_ID",
    "MODAL_TOKEN_SECRET",
)

SANDBOX_ROLE_ENV = "GOA_SANDBOX_ROLE"
CONTROLLER_ROLE = "controller"
AGENT_ROLE = "agent"


def _read_env_file(path: str = ".env") -> dict[str, str]:
    env_path = Path(path)
    if not env_path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def modal_secret() -> modal.Secret:
    file_values = _read_env_file(".env")
    selected = {
        key: value
        for key in SECRET_KEYS
        if (value := os.environ.get(key) or file_values.get(key))
    }
    if "ANTHROPIC_API_KEY" not in selected and (claude_key := selected.get("CLAUDE_API_KEY")):
        selected["ANTHROPIC_API_KEY"] = claude_key
    return modal.Secret.from_dict({"GOA_SECRET_SENTINEL": "1", **selected})


app_secret = modal_secret()

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("curl", "git", "gnupg", "ripgrep", "procps")
    .run_commands(
        "mkdir -p /etc/apt/keyrings",
        "curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg",
        "echo 'deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main' > /etc/apt/sources.list.d/nodesource.list",
        "apt-get update",
        "apt-get install -y nodejs",
        "node --version",
        "npm --version",
        "id -u goaagent >/dev/null 2>&1 || useradd --create-home --shell /bin/bash goaagent",
        "mkdir -p /root/.codex /root/.gemini /root/.local/share/opencode /home/goaagent/.codex /home/goaagent/.gemini /home/goaagent/.local/share/opencode",
        "chown -R goaagent:goaagent /home/goaagent/.codex /home/goaagent/.gemini /home/goaagent/.local",
        "npm install -g @anthropic-ai/claude-code @openai/codex @google/gemini-cli opencode-ai",
        "claude --version",
        "codex --version",
        "gemini --version",
        "opencode --version",
    )
    .pip_install_from_pyproject("pyproject.toml")
    .env({"GOA_IMAGE_VERSION": "scale-workshop-v2"})
    .add_local_dir("game_of_agents", remote_path="/root/game_of_agents")
    .add_local_dir("configs", remote_path="/root/configs")
    .add_local_dir("vendor", remote_path="/root/vendor")
    .add_local_file("pyproject.toml", remote_path="/root/pyproject.toml")
    .add_local_file("README.md", remote_path="/root/README.md")
)
app = modal.App("game-of-agents")
data_volume = modal.Volume.from_name("game-of-agents-data", create_if_missing=True)


def running_inside_modal() -> bool:
    return bool(
        os.environ.get("MODAL_TASK_ID")
        or os.environ.get("MODAL_SANDBOX_ID")
        or os.environ.get("GOA_SECRET_SENTINEL")
    )


def sandbox_role() -> str | None:
    return os.environ.get(SANDBOX_ROLE_ENV)


def in_controller_sandbox() -> bool:
    return sandbox_role() == CONTROLLER_ROLE


async def lookup_app() -> modal.App:
    return await modal.App.lookup.aio("game-of-agents", create_if_missing=True)
