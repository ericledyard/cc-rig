"""Schema-level tests for the baseline document.

Covers round-trip, forward/backward compat, version mismatch, and malformed
input. These tests are intentionally heavy on edge cases because the v1
schema is frozen for the life of cc-rig 3.x -- bugs here are migration pain.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cc_rig.baseline import paths
from cc_rig.baseline.paths import project_path_hash, user_id_hash
from cc_rig.baseline.schema import (
    SCHEMA_VERSION,
    Baseline,
    BaselineSchemaError,
    ProjectEntry,
    WeeklyRollup,
    load_baseline,
    save_baseline,
)

# ---------- WeeklyRollup ---------------------------------------------------


def test_weekly_rollup_roundtrip():
    w = WeeklyRollup(
        week_start="2026-04-28",
        input_tokens=1000,
        cache_read_tokens=800,
        cache_create_tokens=50,
        output_tokens=200,
        cost_usd=0.42,
        savings_pct=84.4,
        session_count=7,
    )
    restored = WeeklyRollup.from_dict(w.to_dict())
    assert restored == w


def test_weekly_rollup_ignores_unknown_keys():
    """Forward-compat: a future cc-rig may add fields; older code must not crash."""
    data = {
        "week_start": "2026-04-28",
        "input_tokens": 100,
        "future_field_we_dont_know_about": "ignored",
    }
    w = WeeklyRollup.from_dict(data)
    assert w.input_tokens == 100
    assert w.week_start == "2026-04-28"


def test_weekly_rollup_defaults_for_missing_fields():
    """Backward-compat: an older writer may omit fields."""
    w = WeeklyRollup.from_dict({"week_start": "2026-04-28"})
    assert w.input_tokens == 0
    assert w.cost_usd == 0.0
    assert w.session_count == 0


# ---------- ProjectEntry ---------------------------------------------------


def test_project_entry_roundtrip_with_history():
    entry = ProjectEntry(
        name="claude-boot",
        first_seen="2026-04-01T10:00:00+00:00",
        last_seen="2026-05-04T18:00:00+00:00",
        tier="standard",
        weekly_savings_history=[
            WeeklyRollup(week_start="2026-04-28", input_tokens=1000, session_count=3),
            WeeklyRollup(week_start="2026-05-05", input_tokens=2000, session_count=5),
        ],
    )
    restored = ProjectEntry.from_dict(entry.to_dict())
    assert restored.name == "claude-boot"
    assert restored.tier == "standard"
    assert len(restored.weekly_savings_history) == 2
    assert restored.weekly_savings_history[1].input_tokens == 2000


def test_project_entry_ignores_unknown_keys():
    data = {
        "name": "demo",
        "tier": "quick",
        "weekly_savings_history": [],
        "experimental_metric": 9000,
    }
    entry = ProjectEntry.from_dict(data)
    assert entry.name == "demo"
    assert entry.tier == "quick"


# ---------- Baseline -------------------------------------------------------


def test_baseline_default_has_current_schema_version():
    b = Baseline()
    assert b.schema_version == SCHEMA_VERSION
    assert b.user_id_hash  # non-empty
    assert b.projects == {}


def test_baseline_roundtrip_with_multiple_projects():
    b = Baseline(
        schema_version=1,
        user_id_hash="abcd1234deadbeef",
        projects={
            "hashA": ProjectEntry(name="alpha", tier="quick"),
            "hashB": ProjectEntry(name="beta", tier="rigorous"),
        },
    )
    restored = Baseline.from_dict(b.to_dict())
    assert restored.schema_version == 1
    assert restored.user_id_hash == "abcd1234deadbeef"
    assert set(restored.projects.keys()) == {"hashA", "hashB"}
    assert restored.projects["hashB"].tier == "rigorous"


def test_baseline_rejects_missing_schema_version():
    with pytest.raises(BaselineSchemaError, match="schema_version"):
        Baseline.from_dict({"user_id_hash": "x", "projects": {}})


def test_baseline_rejects_non_int_schema_version():
    with pytest.raises(BaselineSchemaError, match="must be int"):
        Baseline.from_dict({"schema_version": "1", "projects": {}})


def test_baseline_rejects_future_schema_version():
    with pytest.raises(BaselineSchemaError, match="newer than supported"):
        Baseline.from_dict({"schema_version": SCHEMA_VERSION + 1, "projects": {}})


def test_baseline_rejects_non_dict_projects():
    with pytest.raises(BaselineSchemaError, match="'projects' must be an object"):
        Baseline.from_dict({"schema_version": 1, "projects": []})


# ---------- I/O ------------------------------------------------------------


def test_load_baseline_returns_fresh_when_missing(tmp_path: Path):
    target = tmp_path / "baseline.json"
    b = load_baseline(target)
    assert b.schema_version == SCHEMA_VERSION
    assert b.projects == {}


def test_save_then_load_roundtrip(tmp_path: Path):
    target = tmp_path / "nested" / "baseline.json"
    b = Baseline(
        schema_version=1,
        user_id_hash="testuser",
        projects={"h1": ProjectEntry(name="p1", tier="standard")},
    )
    save_baseline(b, target)
    assert target.exists()
    restored = load_baseline(target)
    assert restored.user_id_hash == "testuser"
    assert restored.projects["h1"].name == "p1"


def test_load_baseline_raises_on_malformed_json(tmp_path: Path):
    target = tmp_path / "baseline.json"
    target.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(BaselineSchemaError, match="not valid JSON"):
        load_baseline(target)


def test_load_baseline_raises_on_non_object_root(tmp_path: Path):
    target = tmp_path / "baseline.json"
    target.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(BaselineSchemaError, match="must be a JSON object"):
        load_baseline(target)


def test_save_baseline_writes_sorted_indented_json(tmp_path: Path):
    target = tmp_path / "baseline.json"
    b = Baseline(user_id_hash="u", projects={})
    save_baseline(b, target)
    text = target.read_text(encoding="utf-8")
    # sorted -> 'projects' comes before 'schema_version' before 'user_id_hash' lexicographically
    assert text.index("projects") < text.index("schema_version") < text.index("user_id_hash")
    # indented -> contains newlines and leading spaces
    assert "\n  " in text


def test_save_baseline_is_atomic(tmp_path: Path, monkeypatch):
    """The tempfile must be renamed on top of the canonical file, not appended."""
    target = tmp_path / "baseline.json"
    target.write_text(
        '{"schema_version": 1, "projects": {}, "user_id_hash": "old"}', encoding="utf-8"
    )
    b = Baseline(user_id_hash="new", projects={})
    save_baseline(b, target)
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["user_id_hash"] == "new"
    # tempfile must be gone
    assert not target.with_suffix(target.suffix + ".tmp").exists()


# ---------- paths ---------------------------------------------------------


def test_project_path_hash_is_stable_and_short():
    h = project_path_hash(Path("/home/u/projects/foo"))
    assert len(h) == 16
    assert h == project_path_hash(Path("/home/u/projects/foo"))


def test_project_path_hash_differs_for_different_paths():
    a = project_path_hash(Path("/home/u/projects/foo"))
    b = project_path_hash(Path("/home/u/projects/bar"))
    assert a != b


def test_user_id_hash_is_stable_within_process():
    assert user_id_hash() == user_id_hash()
    assert len(user_id_hash()) == 16


def test_claude_projects_dir_encodes_cwd(tmp_path, monkeypatch):
    # Use a path we control so the encoding is predictable.
    cwd = Path("/home/me/proj")
    d = paths.claude_projects_dir(cwd)
    assert d.name == "-home-me-proj"
    assert d.parent.name == "projects"
    assert d.parent.parent.name == ".claude"


def test_claude_projects_dir_encodes_underscores_as_dashes():
    # Real CC behavior verified live: /home/x/python_projects/foo_bar maps to
    # -home-x-python-projects-foo-bar under ~/.claude/projects/.
    cwd = Path("/home/me/python_projects/foo_bar")
    d = paths.claude_projects_dir(cwd)
    assert d.name == "-home-me-python-projects-foo-bar"
