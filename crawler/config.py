from __future__ import annotations

import os


def get_env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def get_required_env(name: str) -> str:
    value = get_env(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def is_truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}
