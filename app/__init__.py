"""TRINETRA application package."""

import os

from app.env import load_project_env


if (os.getenv("TRINETRA_DISABLE_DOTENV") or "").strip().lower() not in {"1", "true", "yes", "on"}:
    load_project_env()
