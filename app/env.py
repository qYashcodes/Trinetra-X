from __future__ import annotations

import os
import re
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_project_env(path: str | Path | None = None, *, override: bool = False) -> set[str]:
    """Load simple KEY=VALUE pairs from .env without overriding real environment values."""

    env_path = Path(path) if path is not None else ROOT_DIR / ".env"
    if not env_path.exists():
        return set()

    loaded: set[str] = set()
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not _ENV_KEY.match(key):
            continue
        if not override and key in os.environ:
            continue
        os.environ[key] = _parse_env_value(raw_value)
        loaded.add(key)
    return loaded


def _parse_env_value(raw_value: str) -> str:
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
        if raw_value.strip().startswith('"'):
            value = value.replace(r"\n", "\n").replace(r"\t", "\t")
        return value

    if " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    return value
