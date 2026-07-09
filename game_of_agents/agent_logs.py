from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from game_of_agents.json import loads
from game_of_agents.models import AgentConversationBlock, EventRecord
from game_of_agents.settings import settings


def list_agent_conversation(
    run_id: str,
    agent_id: str,
    *,
    limit: int = 400,
    root: Path | None = None,
) -> list[AgentConversationBlock]:
    blocks: list[AgentConversationBlock] = []
    by_id: dict[str, AgentConversationBlock] = {}
    raw_block_for_step: dict[str, str] = {}
    structured_steps: set[str] = set()
    output_steps: set[str] = set()

    for event in iter_run_events(run_id, root=root):
        payload = event.payload
        if payload.get("agent_id") != agent_id:
            continue
        step_id = str(payload.get("step_id") or "")

        if event.kind == "agent.prompt":
            _append_block(
                blocks,
                by_id,
                AgentConversationBlock(
                    block_id=f"{event.event_id}:prompt",
                    run_id=run_id,
                    agent_id=agent_id,
                    step_id=step_id or None,
                    role="user",
                    kind="prompt",
                    title="Prompt",
                    text=str(payload.get("message") or ""),
                    collapsed=True,
                    streaming=False,
                    created_at=event.created_at,
                    updated_at=event.created_at,
                ),
            )
            continue

        if event.kind in {"agent.warning", "agent.message"}:
            _append_block(
                blocks,
                by_id,
                AgentConversationBlock(
                    block_id=f"{event.event_id}:system",
                    run_id=run_id,
                    agent_id=agent_id,
                    step_id=step_id or None,
                    role="system",
                    kind="warning" if event.kind == "agent.warning" else "summary",
                    title="Warning" if event.kind == "agent.warning" else "Controller",
                    text=str(payload.get("message") or ""),
                    collapsed=True,
                    streaming=False,
                    created_at=event.created_at,
                    updated_at=event.created_at,
                ),
            )
            continue

        if event.kind == "agent.output.block":
            if step_id:
                structured_steps.add(step_id)
                output_steps.add(step_id)
                raw_id = raw_block_for_step.pop(step_id, None)
                if raw_id and raw_id in by_id:
                    block = by_id.pop(raw_id)
                    blocks = [item for item in blocks if item.block_id != block.block_id]
            _apply_structured_block(blocks, by_id, run_id, agent_id, event)
            continue

        if event.kind == "agent.output.chunk":
            if step_id and step_id in structured_steps:
                continue
            text = str(payload.get("chunk") or "")
            if not text:
                continue
            if step_id:
                output_steps.add(step_id)
            block_id = raw_block_for_step.get(step_id)
            if block_id is None:
                block_id = f"{step_id or event.event_id}:text"
                raw_block_for_step[step_id] = block_id
                block = AgentConversationBlock(
                    block_id=block_id,
                    run_id=run_id,
                    agent_id=agent_id,
                    step_id=step_id or None,
                    role="assistant",
                    kind="text",
                    title="Response",
                    text=text,
                    collapsed=False,
                    streaming=True,
                    created_at=event.created_at,
                    updated_at=event.created_at,
                )
                _append_block(blocks, by_id, block)
            else:
                block = by_id[block_id]
                block.text += text
                block.updated_at = event.created_at
            continue

        if event.kind == "agent.output":
            if step_id and step_id in structured_steps:
                _stop_step_blocks(by_id, step_id, event.created_at)
                continue
            text = str(payload.get("output") or "")
            if not text:
                continue
            if step_id:
                output_steps.add(step_id)
            block_id = raw_block_for_step.get(step_id)
            if block_id is None:
                block = AgentConversationBlock(
                    block_id=f"{step_id or event.event_id}:output",
                    run_id=run_id,
                    agent_id=agent_id,
                    step_id=step_id or None,
                    role="assistant",
                    kind="text",
                    title="Response",
                    text=text,
                    collapsed=False,
                    streaming=False,
                    created_at=event.created_at,
                    updated_at=event.created_at,
                )
                _append_block(blocks, by_id, block)
            else:
                block = by_id[block_id]
                if len(text) > len(block.text):
                    block.text = text
                block.streaming = False
                block.updated_at = event.created_at
            continue

        if event.kind == "agent.step":
            if step_id:
                _stop_step_blocks(by_id, step_id, event.created_at)
            summary = str(payload.get("message") or "").strip()
            if summary:
                if step_id and step_id in output_steps:
                    continue
                _append_block(
                    blocks,
                    by_id,
                    AgentConversationBlock(
                        block_id=f"{event.event_id}:response",
                        run_id=run_id,
                        agent_id=agent_id,
                        step_id=step_id or None,
                        role="assistant",
                        kind="text",
                        title="Response",
                        text=summary,
                        collapsed=False,
                        streaming=False,
                        created_at=event.created_at,
                        updated_at=event.created_at,
                    ),
                )
            continue

        if event.kind == "agent.failed":
            if step_id:
                _stop_step_blocks(by_id, step_id, event.created_at)
            _append_block(
                blocks,
                by_id,
                AgentConversationBlock(
                    block_id=f"{event.event_id}:error",
                    run_id=run_id,
                    agent_id=agent_id,
                    step_id=step_id or None,
                    role="system",
                    kind="error",
                    title="Error",
                    text=str(payload.get("error") or ""),
                    collapsed=False,
                    streaming=False,
                    created_at=event.created_at,
                    updated_at=event.created_at,
                ),
            )

    return blocks[-limit:]


def iter_run_events(run_id: str, *, root: Path | None = None) -> Iterator[EventRecord]:
    events_root = root or settings.data_dir / "events"
    path = events_root / f"{run_id}.jsonl"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    buffer: list[str] = []
    depth = 0
    in_string = False
    escape = False
    for char in text:
        buffer.append(char)
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
            continue
        if char != "}":
            continue
        depth -= 1
        if depth != 0:
            continue
        raw = "".join(buffer).strip()
        buffer.clear()
        if raw:
            yield EventRecord.model_validate(loads(raw))


def _append_block(
    blocks: list[AgentConversationBlock],
    by_id: dict[str, AgentConversationBlock],
    block: AgentConversationBlock,
) -> None:
    blocks.append(block)
    by_id[block.block_id] = block


def _stop_step_blocks(
    by_id: dict[str, AgentConversationBlock],
    step_id: str,
    updated_at,
) -> None:
    for block in by_id.values():
        if block.step_id != step_id:
            continue
        block.streaming = False
        block.updated_at = updated_at


def _apply_structured_block(
    blocks: list[AgentConversationBlock],
    by_id: dict[str, AgentConversationBlock],
    run_id: str,
    agent_id: str,
    event: EventRecord,
) -> None:
    payload = event.payload
    block_id = str(payload.get("block_id") or f"{event.event_id}:block")
    action = str(payload.get("action") or "append")
    kind = str(payload.get("block_kind") or "text")
    text = str(payload.get("text") or "")
    title = str(payload.get("title") or ("Tool" if kind == "tool" else "Response"))
    block = by_id.get(block_id)
    if block is None:
        block = AgentConversationBlock(
            block_id=block_id,
            run_id=run_id,
            agent_id=agent_id,
            step_id=str(payload.get("step_id") or "") or None,
            role="tool" if kind == "tool" else "assistant",
            kind="tool" if kind == "tool" else "text",
            title=title,
            text="",
            collapsed=kind == "tool",
            streaming=action != "stop",
            created_at=event.created_at,
            updated_at=event.created_at,
        )
        _append_block(blocks, by_id, block)
    if action in {"start", "append"} and text:
        block.text += text
    block.streaming = action != "stop"
    block.updated_at = event.created_at
