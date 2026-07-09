from __future__ import annotations

import logging
from typing import Any


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def event(name: str, **fields: Any) -> dict[str, Any]:
    payload = {"event": name}
    payload.update(fields)
    return payload

