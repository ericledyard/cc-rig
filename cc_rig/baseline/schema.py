"""Baseline document schema (v1, frozen).

The schema_version field is sacred: v1 must round-trip cleanly even when
future cc-rig versions add fields. `from_dict` deliberately drops unknown
keys so a newer baseline can be read by an older cc-rig without crashing
(forward compat); a newer cc-rig reading an older baseline gets defaults
for fields the older writer omitted (backward compat).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from cc_rig.baseline.paths import BASELINE_DIR, BASELINE_PATH, user_id_hash

SCHEMA_VERSION = 1


class BaselineSchemaError(ValueError):
    """Raised when a baseline document cannot be parsed at the schema level."""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class WeeklyRollup:
    """One ISO-week's worth of session aggregates for one project."""

    week_start: str  # ISO date of the Monday that starts the week
    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_create_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    savings_pct: float = 0.0
    session_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> WeeklyRollup:
        allowed = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**allowed)


@dataclass
class ProjectEntry:
    """All state we keep per project."""

    name: str
    first_seen: str = field(default_factory=_utcnow_iso)
    last_seen: str = field(default_factory=_utcnow_iso)
    tier: str = "standard"
    weekly_savings_history: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "tier": self.tier,
            "weekly_savings_history": [w.to_dict() for w in self.weekly_savings_history],
        }

    @classmethod
    def from_dict(cls, data: dict) -> ProjectEntry:
        history = [WeeklyRollup.from_dict(w) for w in data.get("weekly_savings_history", [])]
        allowed = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        allowed["weekly_savings_history"] = history
        return cls(**allowed)


@dataclass
class Baseline:
    """The user-scoped baseline document at ~/.cc-rig/baseline.json."""

    schema_version: int = SCHEMA_VERSION
    user_id_hash: str = field(default_factory=user_id_hash)
    projects: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "user_id_hash": self.user_id_hash,
            "projects": {k: v.to_dict() for k, v in self.projects.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> Baseline:
        version = data.get("schema_version")
        if version is None:
            raise BaselineSchemaError("baseline.json missing required 'schema_version' field")
        if not isinstance(version, int):
            raise BaselineSchemaError(
                f"baseline.json schema_version must be int, got {type(version).__name__}"
            )
        if version > SCHEMA_VERSION:
            # Forward compat: refuse to silently misread a newer file.
            raise BaselineSchemaError(
                f"baseline.json schema_version {version} is newer than supported {SCHEMA_VERSION}; "
                "upgrade cc-rig to read this file"
            )
        projects_raw = data.get("projects", {})
        if not isinstance(projects_raw, dict):
            raise BaselineSchemaError("baseline.json 'projects' must be an object")
        projects = {k: ProjectEntry.from_dict(v) for k, v in projects_raw.items()}
        return cls(
            schema_version=version,
            user_id_hash=str(data.get("user_id_hash") or user_id_hash()),
            projects=projects,
        )


def load_baseline(path: Optional[Path] = None) -> Baseline:
    """Read the baseline document from disk, or return a fresh one.

    Missing file -> fresh Baseline. Malformed JSON or schema -> raises
    BaselineSchemaError; the caller decides whether to back up + reset.
    """
    target = Path(path) if path is not None else BASELINE_PATH
    if not target.exists():
        return Baseline()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise BaselineSchemaError(f"baseline.json is not valid JSON: {e}") from e
    if not isinstance(raw, dict):
        raise BaselineSchemaError("baseline.json must be a JSON object")
    return Baseline.from_dict(raw)


def save_baseline(baseline: Baseline, path: Optional[Path] = None) -> Path:
    """Write the baseline document to disk atomically.

    Creates ~/.cc-rig/ if missing. Writes to a sibling tempfile and renames
    so a crash mid-write cannot corrupt the canonical file.
    """
    target = Path(path) if path is not None else BASELINE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(baseline.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(target)
    return target


__all__ = [
    "BASELINE_DIR",
    "BASELINE_PATH",
    "SCHEMA_VERSION",
    "Baseline",
    "BaselineSchemaError",
    "ProjectEntry",
    "WeeklyRollup",
    "load_baseline",
    "save_baseline",
]
