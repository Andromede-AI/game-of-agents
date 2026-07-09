from __future__ import annotations

import abc
from pathlib import Path
from typing import Iterable

from game_of_agents.json import dumps
from game_of_agents.models import EventRecord


class EventSink(abc.ABC):
    @abc.abstractmethod
    async def emit(self, event: EventRecord) -> None:
        raise NotImplementedError


class JsonlEventSink(EventSink):
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    async def emit(self, event: EventRecord) -> None:
        path = self.root / f"{event.run_id}.jsonl"
        payload = dumps(event.model_dump(mode="json")).decode("utf-8")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(payload)
            handle.write("\n")


class CompositeEventSink(EventSink):
    def __init__(self, sinks: Iterable[EventSink]) -> None:
        self.sinks = list(sinks)

    async def emit(self, event: EventRecord) -> None:
        for sink in self.sinks:
            await sink.emit(event)


class ConvexEventSink(EventSink):
    def __init__(self, sync) -> None:
        self.sync = sync

    async def emit(self, event: EventRecord) -> None:
        await self.sync.emit_event(event)
