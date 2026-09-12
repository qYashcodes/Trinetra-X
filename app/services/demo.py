from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.settings import settings


@lru_cache(maxsize=1)
def load_demo_case(path: Path | None = None) -> dict[str, Any]:
    source = path or settings.demo_case_path
    with source.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def demo_case() -> dict[str, Any]:
    return load_demo_case()
