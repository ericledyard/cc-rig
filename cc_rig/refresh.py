"""Regenerate-and-diff engine shared by `cc-rig refresh` and `cc-rig drift`.

Both subcommands answer "what would cc-rig generate for this project now, and
how does that compare to what's on disk?". `refresh` shows the diff for one
area and writes it on confirm; `drift` reports the diff across all areas
read-only. The shared work lives here:

  - reconstruct_config: load the persisted ProjectConfig from .cc-rig.json
  - plan_changes:        generate an area into a temp dir, diff vs disk
  - apply_area:          run the generator against the real project, update
                         the manifest

The config is fully persisted in .cc-rig.json (ProjectConfig.to_json), so no
detection or re-prompting is needed; we reload it and re-run a generator.
"""

from __future__ import annotations

import difflib
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from cc_rig.config.project import ProjectConfig
from cc_rig.generators.fileops import FileTracker

# User-facing area -> the generator(s) responsible. settings owns
# settings.json (permissions, hooks, enabledPlugins) plus the hook scripts,
# so plugins/hooks are aliases of settings.
_AREA_ALIASES = {"plugins": "settings", "hooks": "settings"}
_GRANULAR_AREAS = ["agents", "commands", "skills", "rules", "settings", "playbook"]
VALID_AREAS = sorted(set(_GRANULAR_AREAS) | set(_AREA_ALIASES) | {"all"})

# Files that always differ between a temp regen and the real project (they
# encode paths/metadata or mutable state), so they are never treated as drift.
_DIFF_DENYLIST = {
    ".claude/.cc-rig-manifest.json",
    ".claude/cc-rig-state.json",
    ".cc-rig.json",
}


@dataclass
class FileChange:
    """One file's delta between freshly-generated and on-disk content."""

    rel_path: str
    status: str  # "added" | "modified" | "unchanged"
    diff: str = ""


def reconstruct_config(project_dir: Path) -> ProjectConfig:
    """Load the persisted ProjectConfig from <project>/.cc-rig.json."""
    cfg_path = Path(project_dir) / ".cc-rig.json"
    if not cfg_path.exists():
        raise FileNotFoundError(f"no .cc-rig.json in {project_dir}; run `cc-rig init` here first")
    return ProjectConfig.from_json(cfg_path.read_text(encoding="utf-8"))


def _load_prior_manifest(project_dir: Path) -> Optional[dict]:
    p = Path(project_dir) / ".claude" / ".cc-rig-manifest.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text()).get("file_metadata") or {}
    except (json.JSONDecodeError, OSError):
        return None


def resolve_area(area: str) -> str:
    """Normalize an alias (plugins/hooks -> settings); raise on unknown."""
    if area not in VALID_AREAS:
        raise ValueError(f"unknown area '{area}'; choose from {', '.join(VALID_AREAS)}")
    return _AREA_ALIASES.get(area, area)


def _run_one(area: str, config: ProjectConfig, out_dir: Path, prior: Optional[dict]) -> list:
    """Run a single granular generator into out_dir, return relative paths."""
    from cc_rig.generators.agents import generate_agents
    from cc_rig.generators.commands import generate_commands
    from cc_rig.generators.playbook import generate_playbook
    from cc_rig.generators.rules import generate_rules
    from cc_rig.generators.settings import generate_settings
    from cc_rig.generators.skills import generate_skills

    tracker = FileTracker(out_dir)
    if area == "agents":
        return generate_agents(config, out_dir, tracker=tracker)
    if area == "commands":
        return generate_commands(config, out_dir, tracker=tracker)
    if area == "skills":
        return generate_skills(config, out_dir, tracker=tracker)
    if area == "settings":
        return generate_settings(config, out_dir, tracker=tracker)
    if area == "playbook":
        return generate_playbook(config, out_dir, tracker=tracker)
    if area == "rules":
        return generate_rules(config, out_dir, tracker=tracker, prior_manifest=prior)
    raise ValueError(f"no generator for area '{area}'")


def _areas_for(area: str) -> list:
    return list(_GRANULAR_AREAS) if area == "all" else [resolve_area(area)]


def _unified(old: str, new: str, rel: str) -> str:
    lines = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{rel}",
        tofile=f"b/{rel}",
        n=2,
    )
    return "".join(lines)


def plan_changes(config: ProjectConfig, project_dir: Path, area: str) -> list:
    """Generate `area` into a temp dir and diff every produced file vs disk.

    Returns a list of FileChange. Files on the deny-list (manifest, state,
    config) are skipped so they never read as drift. The temp dir is removed
    before returning.
    """
    project_dir = Path(project_dir)
    prior = _load_prior_manifest(project_dir)
    tmp = Path(tempfile.mkdtemp(prefix="cc-rig-refresh-"))
    changes: list = []
    try:
        seen: set = set()
        for one in _areas_for(area):
            for rel in _run_one(one, config, tmp, prior):
                if rel in _DIFF_DENYLIST or rel in seen:
                    continue
                seen.add(rel)
                new_path = tmp / rel
                if not new_path.exists():
                    continue
                new = new_path.read_text(encoding="utf-8", errors="replace")
                cur_path = project_dir / rel
                if not cur_path.exists():
                    changes.append(FileChange(rel, "added", _unified("", new, rel)))
                    continue
                cur = cur_path.read_text(encoding="utf-8", errors="replace")
                if cur == new:
                    changes.append(FileChange(rel, "unchanged"))
                else:
                    changes.append(FileChange(rel, "modified", _unified(cur, new, rel)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    changes.sort(key=lambda c: c.rel_path)
    return changes


def apply_area(config: ProjectConfig, project_dir: Path, area: str) -> list:
    """Run the area's generator(s) against the real project and update the manifest.

    Returns the sorted list of files (re)written. FileTracker backs up any
    pre-existing files it overwrites, matching `cc-rig init` behavior.
    """
    project_dir = Path(project_dir)
    prior = _load_prior_manifest(project_dir)
    written: set = set()
    metadata: dict = {}
    for one in _areas_for(area):
        tracker = FileTracker(project_dir)
        for rel in _run_one(one, config, project_dir, prior):
            written.add(rel)
        metadata.update(tracker.metadata())
    _merge_manifest(project_dir, written, metadata)
    return sorted(written)


def stamp_state(project_dir: Path, field: str, config: Optional[ProjectConfig] = None) -> None:
    """Stamp a last_* timestamp on the project's state file.

    If the state file is missing (older project predating the loop) and a
    config is supplied, a fresh state is bootstrapped so the loop starts
    tracking. Without a config and without an existing file, this is a no-op.
    """
    from cc_rig.baseline.state import (
        ProjectState,
        StateSchemaError,
        config_hash,
        load_state,
        save_state,
        state_path,
    )

    p = state_path(project_dir)
    try:
        st = load_state(p)
    except StateSchemaError:
        st = None
    if st is None:
        if config is None:
            return
        st = ProjectState(
            config_hash=config_hash(config.to_dict()),
            config_snapshot={"tier": config.workflow, "framework": config.framework},
        )
    st.stamp(field)
    save_state(st, p)


def _merge_manifest(project_dir: Path, written: set, metadata: dict) -> None:
    path = Path(project_dir) / ".claude" / ".cc-rig-manifest.json"
    if not path.exists():
        return
    try:
        manifest = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return
    files = set(manifest.get("files", [])) | set(written)
    manifest["files"] = sorted(files)
    manifest.setdefault("file_metadata", {}).update(metadata)
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


__all__ = [
    "VALID_AREAS",
    "FileChange",
    "apply_area",
    "plan_changes",
    "reconstruct_config",
    "resolve_area",
    "stamp_state",
]
