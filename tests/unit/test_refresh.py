"""Tests for the regenerate/diff engine (cc_rig.refresh) used by refresh + drift.

The autouse _mock_skill_downloads fixture (tests/conftest.py) keeps
generate_all/generate_skills offline, so these run fast.
"""

from __future__ import annotations

import json

import pytest

from cc_rig.baseline.state import load_state, state_path
from cc_rig.config.defaults import compute_defaults
from cc_rig.generators.orchestrator import generate_all
from cc_rig.refresh import (
    VALID_AREAS,
    apply_area,
    plan_changes,
    reconstruct_config,
    resolve_area,
    stamp_state,
)


@pytest.fixture
def project(tmp_path):
    """A freshly generated fastapi/standard project with .cc-rig.json."""
    d = tmp_path / "proj"
    d.mkdir()
    cfg = compute_defaults("fastapi", "standard", project_name="demo", output_dir=str(d))
    (d / ".cc-rig.json").write_text(cfg.to_json())
    generate_all(cfg, d)
    return d


class TestReconstructConfig:
    def test_loads_persisted_config(self, project):
        cfg = reconstruct_config(project)
        assert cfg.framework == "fastapi"
        assert cfg.project_name == "demo"

    def test_missing_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            reconstruct_config(tmp_path)


class TestResolveArea:
    def test_aliases_map_to_settings(self):
        assert resolve_area("plugins") == "settings"
        assert resolve_area("hooks") == "settings"

    def test_identity(self):
        assert resolve_area("agents") == "agents"

    def test_unknown_raises(self):
        with pytest.raises(ValueError):
            resolve_area("bogus")

    def test_valid_areas_membership(self):
        for a in ("agents", "commands", "skills", "rules", "settings", "plugins", "hooks", "all"):
            assert a in VALID_AREAS


class TestPlanChanges:
    def test_fresh_project_has_no_actionable_changes(self, project):
        cfg = reconstruct_config(project)
        changes = plan_changes(cfg, project, "agents")
        assert changes  # produced something
        assert all(c.status == "unchanged" for c in changes)

    def test_detects_modified_file(self, project):
        cfg = reconstruct_config(project)
        agent = next((project / ".claude" / "agents").glob("*.md"))
        agent.write_text(agent.read_text() + "\n<!-- tampered -->\n")
        changes = plan_changes(cfg, project, "agents")
        modified = [c for c in changes if c.status == "modified"]
        assert len(modified) == 1
        assert modified[0].rel_path.endswith(agent.name)
        assert modified[0].diff  # carries a unified diff

    def test_detects_added_file(self, project):
        cfg = reconstruct_config(project)
        agent = next((project / ".claude" / "agents").glob("*.md"))
        agent.unlink()
        changes = plan_changes(cfg, project, "agents")
        added = [c for c in changes if c.status == "added"]
        assert any(c.rel_path.endswith(agent.name) for c in added)

    def test_denylist_excluded(self, project):
        cfg = reconstruct_config(project)
        changes = plan_changes(cfg, project, "all")
        paths = {c.rel_path for c in changes}
        assert ".claude/cc-rig-state.json" not in paths
        assert ".claude/.cc-rig-manifest.json" not in paths


class TestApplyArea:
    def test_realigns_modified_file(self, project):
        cfg = reconstruct_config(project)
        agent = next((project / ".claude" / "agents").glob("*.md"))
        original = agent.read_text()
        agent.write_text(original + "\n<!-- tampered -->\n")
        apply_area(cfg, project, "agents")
        assert agent.read_text() == original
        # drift resolved
        assert not [c for c in plan_changes(cfg, project, "agents") if c.status != "unchanged"]

    def test_updates_manifest_files(self, project):
        cfg = reconstruct_config(project)
        written = apply_area(cfg, project, "agents")
        manifest = json.loads((project / ".claude" / ".cc-rig-manifest.json").read_text())
        assert set(written).issubset(set(manifest["files"]))


class TestStampState:
    def test_stamps_existing(self, project):
        cfg = reconstruct_config(project)
        stamp_state(project, "last_refresh", cfg)
        st = load_state(state_path(project))
        assert st.last_refresh is not None

    def test_bootstraps_when_missing(self, tmp_path):
        cfg = compute_defaults("fastapi", "standard", project_name="x", output_dir=str(tmp_path))
        stamp_state(tmp_path, "last_drift_check", cfg)
        st = load_state(state_path(tmp_path))
        assert st is not None
        assert st.last_drift_check is not None

    def test_noop_when_missing_and_no_config(self, tmp_path):
        stamp_state(tmp_path, "last_refresh", None)
        assert load_state(state_path(tmp_path)) is None
