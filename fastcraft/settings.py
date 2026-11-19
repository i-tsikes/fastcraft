"""Local settings. The OpenRouter key never goes into a craft or scenario file."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from fastcraft.yamlio import dump_yaml, load_yaml

DEFAULT_MODEL = "openai/gpt-4o-mini"
_LOCAL_NAME = "fastcraft.settings.yaml"


class Settings(BaseModel):
    """Application settings with validation."""

    model: str = Field(default=DEFAULT_MODEL, description="OpenRouter model identifier")
    api_key: str = Field(default="", description="OpenRouter API key (env var takes precedence)")
    kernel: str = Field(default="", description="Path to JPL DE kernel (.bsp)")

    def resolved_api_key(self) -> str:
        """Return the API key, preferring the environment variable."""
        env = os.environ.get("OPENROUTER_API_KEY", "")
        if env:
            return env
        return self.api_key

    @field_validator("model")
    @classmethod
    def _non_empty_model(cls, v: str) -> str:
        return v.strip() or DEFAULT_MODEL

    @field_validator("kernel")
    @classmethod
    def _expand_kernel_path(cls, v: str) -> str:
        return v.strip()


def settings_path() -> Path:
    """User settings live outside the repository."""
    return Path.home() / ".config" / "fastcraft" / "settings.yaml"


def project_settings_path() -> Path:
    """Optional gitignored settings file in the working directory."""
    return Path.cwd() / _LOCAL_NAME


def load_settings() -> Settings:
    """Load settings. The environment variable wins over a saved API key."""
    data: dict[str, Any] = {}
    for path in (settings_path(), project_settings_path()):
        if path.is_file():
            loaded = load_yaml(path)
            if isinstance(loaded, dict):
                data.update(loaded)
    return Settings.model_validate(data)


def save_settings(settings: Settings) -> Path:
    """Write settings to the user config directory, not the repository."""
    path = settings_path()
    payload = settings.model_dump(exclude_none=True)
    # Don't persist empty api_key; env var takes precedence
    if not payload.get("api_key"):
        payload.pop("api_key", None)
    dump_yaml(payload, path)
    return path


def kernel_path() -> Path | None:
    """Kernel path from settings, if the user set one."""
    text = load_settings().kernel.strip()
    if not text:
        return None
    return Path(text).expanduser()


# For type hints in other modules that import the old dataclass
__all__ = [
    "Settings",
    "DEFAULT_MODEL",
    "settings_path",
    "project_settings_path",
    "load_settings",
    "save_settings",
    "kernel_path",
]
