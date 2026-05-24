"""Project-scoped state for the v3.2 platform loop (.claude/cc-rig-state.json).

This file is project-local and git-friendly: it records when cc-rig last
ran each loop subcommand (audit/drift/refresh/retro) and a hash of the
resolved config so `drift` can tell when the config changed without
rerunning generation.

Like baseline/schema.py, schema_version is sacred: v1 must round-trip even
when newer cc-rig versions add fields. `from_dict` drops unknown keys
(forward compat) and supplies defaults for missing ones (backward compat).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

SCHEMA_VERSION = 1

# Relative to the project root (output_dir). Committed with the project so
# team-shared loop state stays consistent.
STATE_REL_PATH = ".claude/cc-rig-state.json"

# Config keys that change for reasons unrelated to the user's intent; they
# are excluded from config_hash so `drift` does not fire on every upgrade
# or regeneration timestamp.
_VOLATILE_CONFIG_KEYS = frozenset({"created_at", "cc_rig_version"})


class StateSchemaError(ValueError):
    """Raised when a state document cannot be parsed at the schema level."""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def config_hash(config_dict: dict) -> str:
    """Stable sha256 of a resolved config, ignoring volatile metadata.

    Accepts the dict from ProjectConfig.to_dict(). Volatile keys (created_at,
    cc_rig_version) are stripped so the hash reflects the *meaningful* config,
    letting `drift` distinguish a real config change from a version bump.
    """
    stable = {k: v for k, v in config_dict.items() if k not in _VOLATILE_CONFIG_KEYS}
    payload = json.dumps(stable, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class ProjectState:
    """The project-scoped state document at .claude/cc-rig-state.json."""

    schema_version: int = SCHEMA_VERSION
    created_at: str = field(default_factory=_utcnow_iso)
    config_hash: str = ""
    config_snapshot: dict = field(default_factory=dict)
    last_audit: Optional[str] = None
    last_drift_check: Optional[str] = None
    last_refresh: Optional[str] = None
    last_retro: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ProjectState:
        version = data.get("schema_version")
        if version is None:
            raise StateSchemaError("cc-rig-state.json missing required 'schema_version' field")
        if not isinstance(version, int):
            raise StateSchemaError(
                f"cc-rig-state.json schema_version must be int, got {type(version).__name__}"
            )
        if version > SCHEMA_VERSION:
            raise StateSchemaError(
                f"cc-rig-state.json schema_version {version} is newer than supported "
                f"{SCHEMA_VERSION}; upgrade cc-rig to read this file"
            )
        allowed = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**allowed)

    def stamp(self, field_name: str, now: Optional[datetime] = None) -> ProjectState:
        """Set one of the last_* timestamps to now (UTC ISO). Returns self."""
        if field_name not in {"last_audit", "last_drift_check", "last_refresh", "last_retro"}:
            raise ValueError(f"not a stampable field: {field_name}")
        stamp = (now or datetime.now(timezone.utc)).isoformat()
        setattr(self, field_name, stamp)
        return self


def state_path(project_dir: Path) -> Path:
    """Absolute path to the state file for a project directory."""
    return Path(project_dir) / STATE_REL_PATH


def load_state(path: Path) -> Optional[ProjectState]:
    """Read the state document, or None when it does not exist.

    Missing file -> None (project predates the loop, or was never init'd by a
    state-aware cc-rig). Malformed JSON or schema -> StateSchemaError; the
    caller decides whether to warn and continue.
    """
    target = Path(path)
    if not target.exists():
        return None
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise StateSchemaError(f"cc-rig-state.json is not valid JSON: {e}") from e
    if not isinstance(raw, dict):
        raise StateSchemaError("cc-rig-state.json must be a JSON object")
    return ProjectState.from_dict(raw)


def save_state(state: ProjectState, path: Path) -> Path:
    """Write the state document atomically (tempfile + rename)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(target)
    return target


__all__ = [
    "SCHEMA_VERSION",
    "STATE_REL_PATH",
    "ProjectState",
    "StateSchemaError",
    "config_hash",
    "load_state",
    "save_state",
    "state_path",
]
