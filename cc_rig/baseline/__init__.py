"""User-scoped baseline state for longitudinal /cc-rig savings."""

from __future__ import annotations

from cc_rig.baseline.paths import (
    BASELINE_DIR,
    BASELINE_PATH,
    PARSE_CACHE_PATH,
    claude_projects_dir,
    project_path_hash,
    user_id_hash,
)
from cc_rig.baseline.schema import (
    SCHEMA_VERSION,
    Baseline,
    ProjectEntry,
    WeeklyRollup,
    load_baseline,
    save_baseline,
)

__all__ = [
    "BASELINE_DIR",
    "BASELINE_PATH",
    "PARSE_CACHE_PATH",
    "SCHEMA_VERSION",
    "Baseline",
    "ProjectEntry",
    "WeeklyRollup",
    "claude_projects_dir",
    "load_baseline",
    "project_path_hash",
    "save_baseline",
    "user_id_hash",
]
