"""Tiny config loader for config/settings.yaml (cached)."""

from __future__ import annotations

import functools
import os

import yaml

_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "settings.yaml")


@functools.lru_cache(maxsize=1)
def load_settings() -> dict:
    with open(os.path.abspath(_PATH)) as f:
        return yaml.safe_load(f)


def set_execution_mode(mode: str) -> None:
    """Flip execution.mode between 'paper' and 'live' in settings.yaml (GUI control)."""
    if mode not in ("paper", "live"):
        raise ValueError(mode)
    path = os.path.abspath(_PATH)
    with open(path) as f:
        lines = f.readlines()
    for i, line in enumerate(lines):
        # The 'mode:' key under the execution: block.
        if line.lstrip().startswith("mode:") and ("paper" in line or "live" in line):
            indent = line[: len(line) - len(line.lstrip())]
            lines[i] = f"{indent}mode: {mode}                   # paper | live\n"
            break
    with open(path, "w") as f:
        f.writelines(lines)
    load_settings.cache_clear()
