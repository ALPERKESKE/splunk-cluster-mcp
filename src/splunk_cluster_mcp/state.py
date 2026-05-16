"""Persistent state for scenarios (so we can recover even after MCP restart).

Stored at: ~/.local/state/splunk-cluster-mcp/scenarios.json
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def _state_dir() -> Path:
    xdg = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    p = Path(xdg) / "splunk-cluster-mcp"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _state_file() -> Path:
    return _state_dir() / "scenarios.json"


def load_state() -> dict[str, Any]:
    f = _state_file()
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text())
    except Exception as e:
        log.warning("Could not read state file %s: %s. Starting fresh.", f, e)
        return {}


def save_state(state: dict[str, Any]) -> None:
    f = _state_file()
    f.write_text(json.dumps(state, indent=2, default=str))
    log.info("State saved to %s", f)


def upsert(key: str, value: Any) -> dict[str, Any]:
    s = load_state()
    s[key] = value
    save_state(s)
    return s


def pop(key: str) -> Any:
    s = load_state()
    v = s.pop(key, None)
    save_state(s)
    return v
