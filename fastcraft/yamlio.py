"""ruamel.yaml read and write for craft and scenario documents.

Craft and scenario files are not parsed with Pydantic. Comments and key
order round-trip through ruamel.yaml.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


class DocumentError(ValueError):
    """A craft or scenario document failed validation."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("\n".join(errors))


def yaml_rt() -> YAML:
    """Round-trip YAML loader that keeps comments and key order."""
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 4096
    return yaml


def load_yaml(path: Path) -> Any:
    """Load a YAML document. Raises DocumentError on an empty file or a parse error."""
    yaml = yaml_rt()
    try:
        with path.open("r", encoding="utf-8") as handle:
            data: Any = yaml.load(handle)
    except Exception as exc:
        raise DocumentError([f"could not read {path}: {exc}"]) from exc
    if data is None:
        raise DocumentError([f"{path}: document is empty"])
    return data


def dump_yaml(data: Any, path: Path) -> None:
    """Write a YAML document, preserving comments on a round-tripped tree."""
    path.parent.mkdir(parents=True, exist_ok=True)
    yaml = yaml_rt()
    with path.open("w", encoding="utf-8") as handle:
        yaml.dump(data, handle)


def loads_yaml(text: str) -> Any:
    """Parse a YAML string with the same round-trip loader."""
    from io import StringIO

    yaml = yaml_rt()
    data: Any = yaml.load(StringIO(text))
    if data is None:
        raise DocumentError(["document is empty"])
    return data
