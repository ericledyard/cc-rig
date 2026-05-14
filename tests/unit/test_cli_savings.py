"""Tests for the `cc-rig savings` CLI subcommand.

The CLI is a thin wire over baseline.{jsonl,savings}; these tests verify
the wiring and flag handling, not the math (covered in test_baseline_*).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cc_rig.cli import main

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "jsonl"


@pytest.fixture
def isolated_paths(tmp_path):
    """Yield (project_dir, projects_dir, baseline_path, cache_path) all under tmp."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    projects_dir = tmp_path / "claude_projects"
    projects_dir.mkdir()
    baseline_path = tmp_path / "cc-rig" / "baseline.json"
    cache_path = tmp_path / "cc-rig" / "parse-cache.json"
    return project_dir, projects_dir, baseline_path, cache_path


def _copy_fixtures(into: Path, *names: str) -> None:
    for name in names:
        tier, fname = name.split("/", 1)
        src = FIXTURES / tier / fname
        (into / fname).write_bytes(src.read_bytes())


def _invoke_savings(isolated_paths, *extra_args, capsys=None) -> str:
    project_dir, projects_dir, baseline_path, cache_path = isolated_paths
    rc = main(
        [
            "savings",
            "-d",
            str(project_dir),
            "--projects-dir",
            str(projects_dir),
            "--baseline-path",
            str(baseline_path),
            "--cache-path",
            str(cache_path),
            *extra_args,
        ]
    )
    assert rc == 0
    return capsys.readouterr().out if capsys else ""


def test_savings_no_sessions_message(isolated_paths, capsys):
    out = _invoke_savings(isolated_paths, capsys=capsys)
    assert "No sessions found yet" in out
    # Pointed at the right place
    assert str(isolated_paths[1]) in out


def test_savings_renders_text_with_fixture_sessions(isolated_paths, capsys):
    project_dir, projects_dir, baseline_path, cache_path = isolated_paths
    _copy_fixtures(projects_dir, "standard/session_s1.jsonl", "standard/session_s3.jsonl")
    # Window must cover the fixture dates (early May 2026); use a very wide window
    # so the test is not time-sensitive.
    out = _invoke_savings(
        isolated_paths,
        "--window-days",
        "100000",
        "--weeks",
        "2",
        capsys=capsys,
    )
    assert "/cc-rig savings" in out
    assert "Sessions:" in out
    assert "Trend" in out
    assert "Pricing verified" in out


def test_savings_json_flag_emits_valid_json(isolated_paths, capsys):
    project_dir, projects_dir, baseline_path, cache_path = isolated_paths
    _copy_fixtures(projects_dir, "standard/session_s1.jsonl")
    out = _invoke_savings(
        isolated_paths,
        "--window-days",
        "100000",
        "--json",
        capsys=capsys,
    )
    data = json.loads(out)
    assert data["project_name"] == project_dir.name
    assert data["window_days"] == 100000
    assert data["session_count"] == 1


def test_savings_writes_baseline(isolated_paths, capsys):
    project_dir, projects_dir, baseline_path, cache_path = isolated_paths
    _copy_fixtures(projects_dir, "standard/session_s1.jsonl")
    _invoke_savings(
        isolated_paths,
        "--window-days",
        "100000",
        "--tier",
        "rigorous",
        capsys=capsys,
    )
    assert baseline_path.exists()
    data = json.loads(baseline_path.read_text())
    assert data["schema_version"] == 1
    # One project, with our tier
    project_entries = list(data["projects"].values())
    assert len(project_entries) == 1
    assert project_entries[0]["tier"] == "rigorous"
    assert project_entries[0]["name"] == project_dir.name


def test_savings_no_baseline_flag_skips_io(isolated_paths, capsys):
    project_dir, projects_dir, baseline_path, cache_path = isolated_paths
    _copy_fixtures(projects_dir, "standard/session_s1.jsonl")
    _invoke_savings(
        isolated_paths,
        "--window-days",
        "100000",
        "--no-baseline",
        capsys=capsys,
    )
    assert not baseline_path.exists()


def test_savings_writes_parse_cache(isolated_paths, capsys):
    project_dir, projects_dir, baseline_path, cache_path = isolated_paths
    _copy_fixtures(projects_dir, "standard/session_s1.jsonl")
    _invoke_savings(isolated_paths, "--window-days", "100000", capsys=capsys)
    assert cache_path.exists()
    cached = json.loads(cache_path.read_text())
    # One key per JSONL file
    assert len(cached) == 1


def test_savings_idempotent_baseline_update(isolated_paths, capsys):
    """Running twice does not duplicate weekly history entries."""
    project_dir, projects_dir, baseline_path, cache_path = isolated_paths
    _copy_fixtures(projects_dir, "standard/session_s1.jsonl")
    _invoke_savings(isolated_paths, "--window-days", "100000", capsys=capsys)
    first = json.loads(baseline_path.read_text())
    first_entry = list(first["projects"].values())[0]
    first_weeks = [w["week_start"] for w in first_entry["weekly_savings_history"]]

    _invoke_savings(isolated_paths, "--window-days", "100000", capsys=capsys)
    second = json.loads(baseline_path.read_text())
    second_entry = list(second["projects"].values())[0]
    second_weeks = [w["week_start"] for w in second_entry["weekly_savings_history"]]

    assert first_weeks == second_weeks


def test_savings_handles_unreadable_baseline_gracefully(isolated_paths, capsys):
    project_dir, projects_dir, baseline_path, cache_path = isolated_paths
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text("not valid json", encoding="utf-8")
    _copy_fixtures(projects_dir, "standard/session_s1.jsonl")
    # Should not crash. A warning goes to stderr; the report still prints.
    out = _invoke_savings(isolated_paths, "--window-days", "100000", capsys=capsys)
    assert "Sessions:" in out
