"""Local settings. The OpenRouter key never goes into a craft or scenario file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastcraft.yamlio import dump_yaml, load_yaml

DEFAULT_MODEL = "openai/gpt-4o-mini"
_LOCAL_NAME = "fastcraft.settings.yaml"


@dataclass(frozen=True)
class Settings:
    model: str = DEFAULT_MODEL
    api_key: str = ""
    kernel: str = ""

    def resolved_api_key(self) -> str:
        env = os.environ.get("OPENROUTER_API_KEY", "")
        if env:
            return env
        return self.api_key


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
    model = data.get("model", DEFAULT_MODEL)
    api_key = data.get("api_key", "")
    kernel = data.get("kernel", "")
    return Settings(
        model=str(model) if model else DEFAULT_MODEL,
        api_key=str(api_key) if api_key else "",
        kernel=str(kernel) if kernel else "",
    )


def save_settings(settings: Settings) -> Path:
    """Write settings to the user config directory, not the repository."""
    path = settings_path()
    payload: dict[str, Any] = {
        "model": settings.model,
        "kernel": settings.kernel,
    }
    if settings.api_key:
        payload["api_key"] = settings.api_key
    dump_yaml(payload, path)
    return path


def kernel_path() -> Path | None:
    """Kernel path from settings, if the user set one."""
    text = load_settings().kernel.strip()
    if not text:
        return None
    return Path(text).expanduser()
