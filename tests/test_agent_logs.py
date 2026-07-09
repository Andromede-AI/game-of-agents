from __future__ import annotations

from pathlib import Path

from game_of_agents.agent_logs import list_agent_conversation
from game_of_agents.events import JsonlEventSink
from game_of_agents.models import EventRecord


async def _emit(root: Path, events: list[EventRecord]) -> None:
    sink = JsonlEventSink(root)
    for event in events:
        await sink.emit(event)


def test_list_agent_conversation_groups_streamed_blocks(tmp_path: Path) -> None:
    run_id = "run_test"
    step_id = "step_123"
    root = tmp_path / "events"

    import asyncio

    asyncio.run(
        _emit(
            root,
            [
                EventRecord(run_id=run_id, kind="agent.prompt", payload={"agent_id": "alpha", "step_id": step_id, "message": "prompt body"}),
                EventRecord(run_id=run_id, kind="agent.output.chunk", payload={"agent_id": "alpha", "step_id": step_id, "stream": "stdout", "chunk": "hello "}),
                EventRecord(run_id=run_id, kind="agent.output.chunk", payload={"agent_id": "alpha", "step_id": step_id, "stream": "stdout", "chunk": "world"}),
                EventRecord(run_id=run_id, kind="agent.step", payload={"agent_id": "alpha", "step_id": step_id, "message": "step summary"}),
            ],
        )
    )

    blocks = list_agent_conversation(run_id, "alpha", root=root, limit=20)

    assert [block.kind for block in blocks] == ["prompt", "text"]
    assert blocks[0].collapsed is True
    assert blocks[1].text == "hello world"
    assert blocks[1].streaming is False


def test_list_agent_conversation_uses_response_block_when_only_summary_exists(tmp_path: Path) -> None:
    run_id = "run_summary_only"
    step_id = "step_999"
    root = tmp_path / "events"

    import asyncio

    asyncio.run(
        _emit(
            root,
            [
                EventRecord(run_id=run_id, kind="agent.prompt", payload={"agent_id": "alpha", "step_id": step_id, "message": "prompt body"}),
                EventRecord(run_id=run_id, kind="agent.step", payload={"agent_id": "alpha", "step_id": step_id, "message": "step summary"}),
            ],
        )
    )

    blocks = list_agent_conversation(run_id, "alpha", root=root, limit=20)

    assert [block.kind for block in blocks] == ["prompt", "text"]
    assert blocks[1].title == "Response"
    assert blocks[1].text == "step summary"


def test_list_agent_conversation_prefers_structured_blocks_over_raw_json(tmp_path: Path) -> None:
    run_id = "run_structured"
    step_id = "step_456"
    root = tmp_path / "events"

    import asyncio

    asyncio.run(
        _emit(
            root,
            [
                EventRecord(run_id=run_id, kind="agent.output.chunk", payload={"agent_id": "alpha", "step_id": step_id, "stream": "stdout", "chunk": '{"type":"content_block_start"}\n'}),
                EventRecord(
                    run_id=run_id,
                    kind="agent.output.block",
                    payload={
                        "agent_id": "alpha",
                        "step_id": step_id,
                        "block_id": f"{step_id}:tool:0",
                        "block_kind": "tool",
                        "title": "shell",
                        "action": "start",
                        "text": '{"cmd":"ls"}',
                    },
                ),
            ],
        )
    )

    blocks = list_agent_conversation(run_id, "alpha", root=root, limit=20)

    assert len(blocks) == 1
    assert blocks[0].kind == "tool"
    assert blocks[0].collapsed is True
