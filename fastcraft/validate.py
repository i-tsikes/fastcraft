"""Shared validation helpers for YAML document parsing.

These are used by craft.py and scenario.py to validate document structure
and collect human-readable errors. They do not use Pydantic because
craft and scenario YAML files must round-trip comments via ruamel.yaml.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any


def reject_unknown(
    data: Mapping[str, Any],
    allowed: set[str],
    where: str,
    errors: list[str],
) -> None:
    """Record an error for each key in `data` not in `allowed`."""
    for key in data:
        if str(key) not in allowed:
            errors.append(f"{where}: unknown field {key!r}")


def require_mapping(
    value: Any,
    where: str,
    allowed: set[str] | None,
    errors: list[str],
) -> Mapping[str, Any] | None:
    """Validate that `value` is a mapping with only allowed keys.

    Returns the mapping on success, or None and records an error on failure.
    """
    if not isinstance(value, Mapping):
        errors.append(f"{where}: expected a mapping")
        return None
    if allowed is not None:
        reject_unknown(value, allowed, where, errors)
    return value


def parse_number(
    value: Any,
    where: str,
    errors: list[str],
    *,
    minimum: float | None = 0.0,
) -> float:
    """Parse a non-boolean int/float. Records an error and returns 0.0 on failure."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{where}: expected a number")
        return 0.0
    number = float(value)
    if minimum is not None and number < minimum:
        errors.append(f"{where}: must be >= {minimum:g}")
    return number


def parse_integer(
    value: Any,
    where: str,
    errors: list[str],
    *,
    minimum: int | None = 0,
) -> int:
    """Parse a non-boolean int. Records an error and returns 0 on failure."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{where}: expected an integer")
        return 0
    if isinstance(value, float) and not value.is_integer():
        errors.append(f"{where}: expected an integer")
        return 0
    number = int(value)
    if minimum is not None and number < minimum:
        errors.append(f"{where}: must be >= {minimum}")
    return number


def parse_string(value: Any, where: str, errors: list[str]) -> str:
    """Parse a string. Records an error and returns empty string on failure."""
    if not isinstance(value, str):
        errors.append(f"{where}: expected a string")
        return ""
    return value


def parse_vector3(
    value: Any,
    where: str,
    errors: list[str],
) -> tuple[float, float, float] | None:
    """Parse a 3-element list of numbers. Returns None on failure."""
    if not isinstance(value, list) or len(value) != 3:
        errors.append(f"{where}: expected a list of three numbers")
        return None
    nums = [
        parse_number(item, f"{where}[{i}]", errors, minimum=None) for i, item in enumerate(value)
    ]
    return (nums[0], nums[1], nums[2])


def parse_iso_datetime(value: Any, where: str, errors: list[str]) -> datetime | None:
    """Parse an ISO-8601 UTC datetime string. Returns None on failure."""
    from datetime import UTC

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            errors.append(f"{where}: expected an ISO-8601 UTC time")
            return None
    else:
        errors.append(f"{where}: expected an ISO-8601 UTC time")
        return None
    if parsed.tzinfo is None:
        errors.append(f"{where}: time zone is required")
        return None
    return parsed.astimezone(UTC)
