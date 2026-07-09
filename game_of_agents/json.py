from typing import Any

import orjson


def dumps(value: Any) -> bytes:
    return orjson.dumps(value)


def canonical_dumps(value: Any) -> bytes:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS)


def loads(value: bytes | str) -> Any:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return orjson.loads(value)
