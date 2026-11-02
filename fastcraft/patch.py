"""Apply an RFC 6902 JSON Patch to a parsed YAML document.

The assistant proposes edits as a patch. The app applies that patch in
memory and writes YAML only after the user saves.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any, cast


class PatchError(ValueError):
    """The patch could not be applied."""


def extract_patch(text: str) -> tuple[str, list[dict[str, Any]]] | None:
    """Pull a JSON Patch out of an assistant reply.

    A fenced json block may be a patch array, or an object
    `{"target": "craft"|"scenario", "patch": [...]}`. A bare array applies
    to the scenario. Returns None when the reply has no patch.
    """
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    raw = match.group(1) if match is not None else None
    if raw is None:
        stripped = text.strip()
        if stripped.startswith("[") or stripped.startswith("{"):
            raw = stripped
    if raw is None:
        return None
    try:
        parsed: Any = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, list):
        return "scenario", _ops(parsed)
    if isinstance(parsed, dict) and "patch" in parsed:
        target = parsed.get("target", "scenario")
        if target not in ("craft", "scenario"):
            raise PatchError(f"patch target must be 'craft' or 'scenario', not {target!r}")
        patch = parsed["patch"]
        if not isinstance(patch, list):
            raise PatchError("patch must be a list of operations")
        return str(target), _ops(patch)
    return None


def apply_patch(document: Any, operations: list[dict[str, Any]]) -> Any:
    """Return a deep copy of `document` with the patch applied."""
    root: Any = deepcopy(document)
    for index, op in enumerate(operations):
        try:
            root = _apply_one(root, op)
        except PatchError as exc:
            raise PatchError(f"operation {index}: {exc}") from exc
    return root


def _ops(value: list[Any]) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict) or "op" not in item:
            raise PatchError("each patch operation must be an object with 'op'")
        ops.append(cast(dict[str, Any], item))
    return ops


def _apply_one(root: Any, op: dict[str, Any]) -> Any:
    name = op.get("op")
    path = op.get("path")
    if not isinstance(path, str):
        raise PatchError("operation is missing a string path")
    if name == "add":
        if "value" not in op:
            raise PatchError("add requires a value")
        return _add(root, path, op["value"])
    if name == "remove":
        _remove(root, path)
        return root
    if name == "replace":
        if "value" not in op:
            raise PatchError("replace requires a value")
        _replace(root, path, op["value"])
        return root
    if name == "move":
        from_path = op.get("from")
        if not isinstance(from_path, str):
            raise PatchError("move requires a string 'from'")
        value = deepcopy(_get(root, from_path))
        _remove(root, from_path)
        return _add(root, path, value)
    if name == "copy":
        from_path = op.get("from")
        if not isinstance(from_path, str):
            raise PatchError("copy requires a string 'from'")
        return _add(root, path, deepcopy(_get(root, from_path)))
    if name == "test":
        if "value" not in op:
            raise PatchError("test requires a value")
        current = _get(root, path)
        if current != op["value"]:
            raise PatchError(f"test failed at {path}")
        return root
    raise PatchError(f"unsupported operation {name!r}")


def _pointer(pointer: str) -> list[str]:
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise PatchError(f"JSON pointer must start with '/': {pointer}")
    parts: list[str] = []
    for raw in pointer.split("/")[1:]:
        parts.append(raw.replace("~1", "/").replace("~0", "~"))
    return parts


def _parent(root: Any, parts: list[str]) -> tuple[Any, str]:
    if not parts:
        raise PatchError("cannot modify the document root in place")
    node: Any = root
    for part in parts[:-1]:
        node = _child(node, part, create=False)
    return node, parts[-1]


def _child(node: Any, token: str, *, create: bool) -> Any:
    if isinstance(node, dict):
        if token not in node:
            if not create:
                raise PatchError(f"missing key {token!r}")
            node[token] = {}
        return node[token]
    if isinstance(node, list):
        index = _index(token, len(node), allow_end=False)
        return node[index]
    raise PatchError("JSON pointer entered a value that is not a collection")


def _index(token: str, length: int, *, allow_end: bool) -> int:
    if token == "-" and allow_end:
        return length
    if not token.isdigit():
        raise PatchError(f"list index must be an integer, not {token!r}")
    index = int(token)
    if index < 0 or index > length or (index == length and not allow_end):
        raise PatchError(f"list index {index} is out of range")
    return index


def _get(root: Any, pointer: str) -> Any:
    node: Any = root
    for part in _pointer(pointer):
        node = _child(node, part, create=False)
    return node


def _add(root: Any, pointer: str, value: Any) -> Any:
    parts = _pointer(pointer)
    if not parts:
        return deepcopy(value)
    parent, token = _parent(root, parts)
    if isinstance(parent, dict):
        parent[token] = deepcopy(value)
        return root
    if isinstance(parent, list):
        index = _index(token, len(parent), allow_end=True)
        parent.insert(index, deepcopy(value))
        return root
    raise PatchError("cannot add under that path")


def _remove(root: Any, pointer: str) -> None:
    parts = _pointer(pointer)
    if not parts:
        raise PatchError("cannot remove the document root")
    parent, token = _parent(root, parts)
    if isinstance(parent, dict):
        if token not in parent:
            raise PatchError(f"missing key {token!r}")
        del parent[token]
        return
    if isinstance(parent, list):
        index = _index(token, len(parent), allow_end=False)
        del parent[index]
        return
    raise PatchError("cannot remove that path")


def _replace(root: Any, pointer: str, value: Any) -> None:
    parts = _pointer(pointer)
    if not parts:
        raise PatchError("replace of the document root is not supported")
    parent, token = _parent(root, parts)
    if isinstance(parent, dict):
        if token not in parent:
            raise PatchError(f"missing key {token!r}")
        parent[token] = deepcopy(value)
        return
    if isinstance(parent, list):
        index = _index(token, len(parent), allow_end=False)
        parent[index] = deepcopy(value)
        return
    raise PatchError("cannot replace that path")
