"""Tests for cc_rig.baseline.audit (workflow-discipline scoring)."""

from __future__ import annotations

from cc_rig.baseline.audit import compute_audit
from cc_rig.baseline.jsonl import SessionSummary


def _summary(idx: int, *, claudemd=0, switches=0, cache_read=9000, cache_create=1000, turns=5):
    return SessionSummary(
        session_id=f"s{idx}",
        file_path=f"/x/s{idx}.jsonl",
        file_mtime=float(idx),
        started_at=f"2026-05-{10 + idx:02d}T08:00:00+00:00",
        ended_at=f"2026-05-{10 + idx:02d}T09:00:00+00:00",
        primary_family="opus",
        cache_read_tokens=cache_read,
        cache_create_tokens=cache_create,
        claudemd_edits=claudemd,
        model_switches=switches,
        assistant_turns=turns,
    )


def test_empty_returns_zero_and_message():
    r = compute_audit([], tier="standard")
    assert r.sessions_analyzed == 0
    assert "No sessions" in r.verdict


def test_clean_sessions_all_pass():
    sums = [
        _summary(i, claudemd=0, switches=0, cache_read=9500, cache_create=500) for i in range(3)
    ]
    r = compute_audit(sums, tier="rigorous")
    statuses = {c.status for c in r.checks if c.name != "Session shape"}
    assert "warn" not in statuses
    assert "consistent with rigorous" in r.verdict


def test_breakers_produce_warnings_and_looser_verdict():
    sums = [
        _summary(i, claudemd=2, switches=1, cache_read=4000, cache_create=6000) for i in range(4)
    ]
    r = compute_audit(sums, tier="standard")
    warns = [c for c in r.checks if c.status == "warn"]
    assert len(warns) >= 2
    assert r.claudemd_edit_sessions == 4
    assert r.model_switch_sessions == 4
    assert "Looser cache discipline" in r.verdict


def test_last_n_windowing():
    sums = [_summary(i) for i in range(20)]
    r = compute_audit(sums, tier="standard", last_n=5)
    assert r.sessions_analyzed == 5


def test_to_dict_is_serializable():
    import json

    r = compute_audit([_summary(0)], tier="quick")
    json.dumps(r.to_dict())  # must not raise
    assert isinstance(r.to_dict()["checks"], list)


def test_tier_appears_in_verdict():
    r = compute_audit([_summary(0)], tier="speedrun")
    assert "speedrun" in r.verdict
