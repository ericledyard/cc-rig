"""CLI-level tests for `cc-rig refresh` and `cc-rig drift` (via main())."""

from __future__ import annotations

import json

import pytest

from cc_rig.baseline.state import load_state, state_path
from cc_rig.cli import main
from cc_rig.config.defaults import compute_defaults
from cc_rig.generators.orchestrator import generate_all


@pytest.fixture
def project(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    cfg = compute_defaults("fastapi", "standard", project_name="demo", output_dir=str(d))
    (d / ".cc-rig.json").write_text(cfg.to_json())
    generate_all(cfg, d)
    return d


def _tamper_agent(project):
    agent = next((project / ".claude" / "agents").glob("*.md"))
    agent.write_text(agent.read_text() + "\n<!-- tampered -->\n")
    return agent


# --- refresh -----------------------------------------------------------------


def test_refresh_json_clean_project(project, capsys):
    rc = main(["refresh", "agents", "-d", str(project), "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["area"] == "agents"
    assert data["changes"] == []


def test_refresh_json_reports_modified(project, capsys):
    _tamper_agent(project)
    rc = main(["refresh", "agents", "-d", str(project), "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data["changes"]) == 1
    assert data["changes"][0]["status"] == "modified"


def test_refresh_dry_run_does_not_write(project, capsys):
    agent = _tamper_agent(project)
    tampered = agent.read_text()
    rc = main(["refresh", "agents", "-d", str(project), "--dry-run"])
    assert rc == 0
    assert agent.read_text() == tampered  # untouched
    assert "Dry run" in capsys.readouterr().out


def test_refresh_yes_writes_and_stamps(project, capsys):
    agent = _tamper_agent(project)
    rc = main(["refresh", "agents", "-d", str(project), "--yes"])
    assert rc == 0
    assert "<!-- tampered -->" not in agent.read_text()
    assert "Refreshed agents" in capsys.readouterr().out
    assert load_state(state_path(project)).last_refresh is not None


def test_refresh_unknown_area_returns_2(project, capsys):
    rc = main(["refresh", "bogus", "-d", str(project)])
    assert rc == 2
    assert "unknown area" in capsys.readouterr().err


def test_refresh_no_config_returns_1(tmp_path, capsys):
    rc = main(["refresh", "agents", "-d", str(tmp_path)])
    assert rc == 1
    assert ".cc-rig.json" in capsys.readouterr().err


def test_refresh_plugins_alias_runs(project, capsys):
    rc = main(["refresh", "plugins", "-d", str(project), "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["area"] == "plugins"


# --- drift -------------------------------------------------------------------


def test_drift_json_clean(project, capsys):
    rc = main(["drift", "-d", str(project), "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["drifted_files"] == []
    assert data["config_changed"] is False
    assert "cc_version" in data and "status" in data["cc_version"]


def test_drift_detects_tamper(project, capsys):
    agent = _tamper_agent(project)
    rc = main(["drift", "-d", str(project), "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert any(c["path"].endswith(agent.name) for c in data["drifted_files"])


def test_drift_stamps_state(project, capsys):
    main(["drift", "-d", str(project), "--json"])
    assert load_state(state_path(project)).last_drift_check is not None


def test_drift_no_config_returns_1(tmp_path, capsys):
    rc = main(["drift", "-d", str(tmp_path)])
    assert rc == 1
