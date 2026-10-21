"""A minimal, safe JSON path: dotted keys, `[index]`, and `length` on arrays/strings.

Examples: ``$`` (the whole body), ``error.code``, ``items[0].product.id``, ``items.length``.
No filters or expressions: plans stay data.
"""

from __future__ import annotations

import re
from typing import Any

_TOKEN_RE = re.compile(r"([^.\[\]]+)|\[(\d+)\]")
_PATH_RE = re.compile(r"^\$$|^\$?\.?([A-Za-z0-9_\-]+|\[\d+\])(\.[A-Za-z0-9_\-]+|\[\d+\])*$")

MISSING = object()


class JsonPathError(ValueError):
    pass


def is_valid(path: str) -> bool:
    return _PATH_RE.match(path) is not None


def get(document: Any, path: str) -> Any:
    """Returns the value at `path`, or `MISSING` when any segment does not exist."""
    if not is_valid(path):
        raise JsonPathError(f"invalid JSON path {path!r}")
    if path == "$":
        return document
    node = document
    for key, index in _TOKEN_RE.findall(path.removeprefix("$").removeprefix(".")):
        if index:
            if not isinstance(node, list) or int(index) >= len(node):
                return MISSING
            node = node[int(index)]
        elif key == "length" and isinstance(node, list | str):
            node = len(node)
        elif isinstance(node, dict) and key in node:
            node = node[key]
        else:
            return MISSING
    return node
