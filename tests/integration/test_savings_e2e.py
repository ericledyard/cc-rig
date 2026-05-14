"""End-to-end test for the longitudinal `cc-rig savings` flow.

Walks the full path: synthesize 14 days of session JSONLs in a temp
~/.claude/projects/-encoded-/, invoke the CLI twice, then assert:

  - the rendered report shows the expected sessions / cost / savings,
  - ~/.cc-rig/baseline.json is created and contains weekly rollups,
  - a second invocation is idempotent (no duplicate weeks),
  - --no-baseline skips the file entirely,
  - --json emits valid, machine-parseable output.

This is the only test in the savings stack that exercises the full
CLI -> parser -> rollup -> baseline -> renderer pipeline together.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cc_rig.cli import main


def _synthesize_session(
    target: Path,
    *,
    session_id: str,
    ended_at: datetime,
    model: str = "claude-sonnet-4-6",
    turns: int = 4,
) -> None:
    """Write a minimal but realistic JSONL session file."""
    lines = []
    for i in range(turns):
        ts = ended_at - timedelta(seconds=(turns - i) * 30)
        usage = {
            "input_tokens": 100 + i * 20,
            "output_tokens": 200 + i * 30,
            "cache_creation_input_tokens": 5000 if i == 0 else 0,
            "cache_read_input_tokens": 0 if i == 0 else 5000 + i * 200,
        }
        lines.append(
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": ts.isoformat().replace("+00:00", "Z"),
                    "sessionId": session_id,
                    "message": {"role": "assistant", "model": model, "usage": usage},
                }
            )
        )
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def populated_env(tmp_path):
    """Build a tmp tree with two weeks of sessions and isolated baseline paths."""
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    projects_dir = tmp_path / "claude_projects"
    projects_dir.mkdir()
    baseline_path = tmp_path / "cc-rig" / "baseline.json"
    cache_path = tmp_path / "cc-rig" / "parse-cache.json"

    # 14 days of sessions, two per day (28 total). Use a clock anchored to
    # now() so the 30-day window catches them all without parametrizing
    # `--window-days` for time travel.
    now = datetime.now(timezone.utc)
    for day in range(14):
        for slot in range(2):
            ts = now - timedelta(days=day, hours=slot)
            _synthesize_session(
                projects_dir / f"d{day:02d}_s{slot}.jsonl",
                session_id=f"sess-{day:02d}-{slot}",
                ended_at=ts,
            )

    return {
        "project_dir": project_dir,
        "projects_dir": projects_dir,
        "baseline_path": baseline_path,
        "cache_path": cache_path,
    }


def _run_savings(env, *extra_args) -> int:
    return main(
        [
            "savings",
            "-d",
            str(env["project_dir"]),
            "--projects-dir",
            str(env["projects_dir"]),
            "--baseline-path",
            str(env["baseline_path"]),
            "--cache-path",
            str(env["cache_path"]),
            *extra_args,
        ]
    )


def test_e2e_renders_report_and_writes_baseline(populated_env, capsys):
    rc = _run_savings(populated_env)
    assert rc == 0
    out = capsys.readouterr().out

    # 28 sessions across the last 14 days
    assert "Sessions: 28" in out
    assert "Trend (4-week rolling):" in out
    assert "<- you are here" in out  # current week marker
    assert "Pricing verified:" in out

    # Baseline document was created.
    bp = populated_env["baseline_path"]
    assert bp.exists()
    data = json.loads(bp.read_text())
    assert data["schema_version"] == 1
    assert len(data["projects"]) == 1
    entry = next(iter(data["projects"].values()))
    assert entry["name"] == "demo"
    assert entry["tier"] == "standard"
    # We had sessions every day for 2 weeks -> at least 2 weekly rollups.
    assert len(entry["weekly_savings_history"]) >= 2


def test_e2e_idempotent_baseline_history(populated_env, capsys):
    """Running twice does not duplicate weekly history."""
    _run_savings(populated_env)
    capsys.readouterr()
    first = json.loads(populated_env["baseline_path"].read_text())
    first_entry = next(iter(first["projects"].values()))
    first_weeks = {w["week_start"] for w in first_entry["weekly_savings_history"]}

    _run_savings(populated_env)
    capsys.readouterr()
    second = json.loads(populated_env["baseline_path"].read_text())
    second_entry = next(iter(second["projects"].values()))
    second_weeks = {w["week_start"] for w in second_entry["weekly_savings_history"]}

    assert first_weeks == second_weeks


def test_e2e_no_baseline_flag_skips_disk(populated_env, capsys):
    rc = _run_savings(populated_env, "--no-baseline")
    assert rc == 0
    capsys.readouterr()
    assert not populated_env["baseline_path"].exists()


def test_e2e_json_flag_emits_parseable_json(populated_env, capsys):
    rc = _run_savings(populated_env, "--json")
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["session_count"] == 28
    assert "weekly_trend" in data
    assert isinstance(data["weekly_trend"], list)
    assert data["window_days"] == 30


def test_e2e_parse_cache_is_reused_on_second_run(populated_env, capsys):
    _run_savings(populated_env)
    capsys.readouterr()
    cache_path = populated_env["cache_path"]
    assert cache_path.exists()
    first_mtime = cache_path.stat().st_mtime

    # Second run: nothing changed on disk, so the cache should not be rewritten.
    _run_savings(populated_env)
    capsys.readouterr()
    assert cache_path.stat().st_mtime == first_mtime
