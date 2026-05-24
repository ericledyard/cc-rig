"""CLI-level tests for `cc-rig audit` and `cc-rig retro` (via main())."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cc_rig.baseline.state import load_state, state_path
from cc_rig.cli import main
from cc_rig.config.defaults import compute_defaults
from cc_rig.generators.orchestrator import generate_all

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "jsonl" / "standard"


@pytest.fixture
def project(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    cfg = compute_defaults("fastapi", "standard", project_name="demo", output_dir=str(d))
    (d / ".cc-rig.json").write_text(cfg.to_json())
    generate_all(cfg, d)
    return d


# --- audit -------------------------------------------------------------------


def test_audit_json_reads_fixtures(project, tmp_path, capsys):
    rc = main(
        [
            "audit",
            "-d",
            str(project),
            "--projects-dir",
            str(FIXTURES),
            "--cache-path",
            str(tmp_path / "cache.json"),
            "--tier",
            "standard",
            "--json",
        ]
    )
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["sessions_analyzed"] == 3
    assert data["tier"] == "standard"
    assert data["checks"]


def test_audit_stamps_last_audit(project, tmp_path, capsys):
    main(
        [
            "audit",
            "-d",
            str(project),
            "--projects-dir",
            str(FIXTURES),
            "--cache-path",
            str(tmp_path / "cache.json"),
        ]
    )
    assert load_state(state_path(project)).last_audit is not None


def test_audit_no_sessions_message(project, tmp_path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    rc = main(
        [
            "audit",
            "-d",
            str(project),
            "--projects-dir",
            str(empty),
            "--cache-path",
            str(tmp_path / "cache.json"),
        ]
    )
    assert rc == 0
    assert "No sessions" in capsys.readouterr().out


# --- retro -------------------------------------------------------------------


def test_retro_json_structure(project, tmp_path, capsys):
    rc = main(
        [
            "retro",
            "-d",
            str(project),
            "--projects-dir",
            str(FIXTURES),
            "--cache-path",
            str(tmp_path / "cache.json"),
            "--baseline-path",
            str(tmp_path / "baseline.json"),
            "--json",
        ]
    )
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert "week_of" in data
    assert "savings" in data and "audit" in data and "drift" in data
    # config present (generated project) so drift was computed to a list
    assert isinstance(data["drift"]["files"], list)


def test_retro_stamps_last_retro(project, tmp_path, capsys):
    main(
        [
            "retro",
            "-d",
            str(project),
            "--projects-dir",
            str(FIXTURES),
            "--cache-path",
            str(tmp_path / "cache.json"),
            "--no-baseline",
            "--json",
        ]
    )
    assert load_state(state_path(project)).last_retro is not None


def test_retro_no_baseline_skips_file(project, tmp_path, capsys):
    bp = tmp_path / "baseline.json"
    main(
        [
            "retro",
            "-d",
            str(project),
            "--projects-dir",
            str(FIXTURES),
            "--cache-path",
            str(tmp_path / "cache.json"),
            "--baseline-path",
            str(bp),
            "--no-baseline",
            "--json",
        ]
    )
    assert not bp.exists()
