"""Tests for cc_rig.baseline.state (project-scoped loop state, schema v1)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from cc_rig.baseline.state import (
    SCHEMA_VERSION,
    STATE_REL_PATH,
    ProjectState,
    StateSchemaError,
    config_hash,
    load_state,
    save_state,
    state_path,
)


class TestConfigHash:
    def test_stable_for_same_config(self):
        cfg = {"framework": "fastapi", "workflow": "standard", "agents": ["a", "b"]}
        assert config_hash(cfg) == config_hash(dict(cfg))

    def test_prefixed_sha256(self):
        assert config_hash({"x": 1}).startswith("sha256:")

    def test_changes_when_meaningful_field_changes(self):
        a = config_hash({"framework": "fastapi"})
        b = config_hash({"framework": "django"})
        assert a != b

    def test_ignores_volatile_keys(self):
        base = {"framework": "fastapi", "created_at": "2026-01-01", "cc_rig_version": "3.1.0"}
        bumped = {"framework": "fastapi", "created_at": "2026-09-09", "cc_rig_version": "3.2.0"}
        assert config_hash(base) == config_hash(bumped)


class TestRoundTrip:
    def test_to_from_dict(self):
        s = ProjectState(config_hash="sha256:abc", config_snapshot={"tier": "standard"})
        s.stamp("last_retro", now=datetime(2026, 5, 25, tzinfo=timezone.utc))
        restored = ProjectState.from_dict(s.to_dict())
        assert restored == s

    def test_save_load(self, tmp_path):
        p = tmp_path / STATE_REL_PATH
        s = ProjectState(config_hash="sha256:xyz", config_snapshot={"framework": "django"})
        save_state(s, p)
        loaded = load_state(p)
        assert loaded is not None
        assert loaded.config_hash == "sha256:xyz"
        assert loaded.config_snapshot == {"framework": "django"}

    def test_save_is_atomic_no_tmp_left(self, tmp_path):
        p = tmp_path / STATE_REL_PATH
        save_state(ProjectState(), p)
        assert p.exists()
        assert not p.with_suffix(p.suffix + ".tmp").exists()


class TestCompat:
    def test_forward_compat_drops_unknown_keys(self):
        data = ProjectState(config_hash="sha256:x").to_dict()
        data["a_future_field"] = {"nested": True}
        s = ProjectState.from_dict(data)
        assert s.config_hash == "sha256:x"

    def test_backward_compat_supplies_defaults(self):
        s = ProjectState.from_dict({"schema_version": 1})
        assert s.last_audit is None
        assert s.config_hash == ""

    def test_rejects_newer_schema(self):
        with pytest.raises(StateSchemaError):
            ProjectState.from_dict({"schema_version": SCHEMA_VERSION + 1})

    def test_rejects_missing_schema_version(self):
        with pytest.raises(StateSchemaError):
            ProjectState.from_dict({"config_hash": "sha256:x"})

    def test_rejects_non_int_schema_version(self):
        with pytest.raises(StateSchemaError):
            ProjectState.from_dict({"schema_version": "1"})


class TestLoadEdgeCases:
    def test_missing_file_returns_none(self, tmp_path):
        assert load_state(tmp_path / "nope.json") is None

    def test_malformed_json_raises(self, tmp_path):
        p = tmp_path / "state.json"
        p.write_text("{not json", encoding="utf-8")
        with pytest.raises(StateSchemaError):
            load_state(p)

    def test_non_object_raises(self, tmp_path):
        p = tmp_path / "state.json"
        p.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        with pytest.raises(StateSchemaError):
            load_state(p)


class TestStamp:
    def test_stamp_sets_iso_timestamp(self):
        s = ProjectState()
        s.stamp("last_drift_check", now=datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc))
        assert s.last_drift_check == "2026-05-25T12:00:00+00:00"

    def test_stamp_rejects_unknown_field(self):
        with pytest.raises(ValueError):
            ProjectState().stamp("last_lunch")


def test_state_path_uses_rel_constant(tmp_path):
    assert state_path(tmp_path) == tmp_path / STATE_REL_PATH
