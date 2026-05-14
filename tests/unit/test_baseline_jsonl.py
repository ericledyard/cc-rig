"""Tests for the JSONL session parser.

Drives parsing against synthetic fixtures with hand-computed token totals.
Synthetic > anonymized real: numbers are easy to eyeball, no privacy risk,
no need to keep fixtures in sync with private session history.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from cc_rig.baseline.jsonl import (
    DEFAULT_FAMILY,
    PRICING_PER_MILLION,
    SessionSummary,
    compute_cost,
    discover_session_files,
    model_family,
    parse_session,
    parse_sessions,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "jsonl"


# ---------- model_family ---------------------------------------------------


def test_model_family_recognizes_known_ids():
    assert model_family("claude-opus-4-6") == "opus"
    assert model_family("claude-sonnet-4-6") == "sonnet"
    assert model_family("claude-haiku-4-5-20251001") == "haiku"


def test_model_family_handles_empty_or_unknown():
    assert model_family("") == DEFAULT_FAMILY
    assert model_family("some-unknown-model") == DEFAULT_FAMILY


# ---------- compute_cost ---------------------------------------------------


def test_compute_cost_uses_family_pricing():
    cost = compute_cost(1_000_000, 0, 0, 0, "sonnet")
    assert cost == PRICING_PER_MILLION["sonnet"][0]


def test_compute_cost_zeros_yield_zero():
    assert compute_cost(0, 0, 0, 0, "opus") == 0.0


def test_compute_cost_unknown_family_falls_back():
    assert compute_cost(1_000_000, 0, 0, 0, "fictional") == PRICING_PER_MILLION[DEFAULT_FAMILY][0]


# ---------- parse_session: quick tier --------------------------------------


def test_parse_session_q1_sonnet_aggregates_correctly():
    summary = parse_session(FIXTURES / "quick" / "session_q1.jsonl")
    # q1 sums: input 100+50+30=180, output 200+150+100=450,
    # cache_create 1000, cache_read 0+1000+1000=2000.
    assert summary.input_tokens == 180
    assert summary.output_tokens == 450
    assert summary.cache_create_tokens == 1000
    assert summary.cache_read_tokens == 2000
    assert summary.assistant_turns == 3
    assert summary.primary_family == "sonnet"
    assert summary.claudemd_edits == 0
    assert summary.model_switches == 0


def test_parse_session_q1_cost_is_positive_and_below_uncached():
    summary = parse_session(FIXTURES / "quick" / "session_q1.jsonl")
    assert summary.cost_usd > 0
    assert summary.cost_uncached_usd > summary.cost_usd
    assert summary.savings_pct > 0
    assert summary.savings_pct < 100


def test_parse_session_q2_detects_haiku_family():
    summary = parse_session(FIXTURES / "quick" / "session_q2.jsonl")
    assert summary.primary_family == "haiku"
    assert summary.input_tokens == 300
    assert summary.cache_read_tokens == 3000


def test_parse_session_q3_records_model_switch():
    """sonnet -> opus is one switch (one adjacent family change)."""
    summary = parse_session(FIXTURES / "quick" / "session_q3_mixed.jsonl")
    assert summary.model_switches == 1
    # We saw both model ids
    assert any("sonnet" in m for m in summary.models_seen)
    assert any("opus" in m for m in summary.models_seen)


# ---------- parse_session: standard tier -----------------------------------


def test_parse_session_s1_high_cache_ratio():
    summary = parse_session(FIXTURES / "standard" / "session_s1.jsonl")
    # cache_create 10000 + cache_read (10000+10000+10500) = 40500 total cache-ish
    assert summary.cache_create_tokens == 10000
    assert summary.cache_read_tokens == 30500
    # cache_read_ratio = 30500 / (30500 + 10000) ~= 0.753
    assert 0.7 < summary.cache_read_ratio < 0.8


def test_parse_session_s2_counts_claudemd_edits():
    """Two CLAUDE.md tool_uses (one Edit, one Write) on different turns."""
    summary = parse_session(FIXTURES / "standard" / "session_s2_claudemd.jsonl")
    assert summary.claudemd_edits == 2


def test_parse_session_s3_timestamps_span_session():
    summary = parse_session(FIXTURES / "standard" / "session_s3.jsonl")
    assert summary.started_at == "2026-05-06T08:00:00.000Z"
    assert summary.ended_at == "2026-05-06T08:00:30.000Z"


# ---------- parse_session: rigorous tier + malformed input -----------------


def test_parse_session_r1_opus_pricing():
    summary = parse_session(FIXTURES / "rigorous" / "session_r1.jsonl")
    assert summary.primary_family == "opus"
    # Opus is more expensive per token; quick sanity check.
    sonnet_cost = compute_cost(
        summary.input_tokens,
        summary.output_tokens,
        summary.cache_create_tokens,
        summary.cache_read_tokens,
        "sonnet",
    )
    assert summary.cost_usd > sonnet_cost


def test_parse_session_r2_skips_malformed_lines():
    """A garbage line + an empty line + an event missing 'type' must not raise."""
    summary = parse_session(FIXTURES / "rigorous" / "session_r2_malformed.jsonl")
    # The two valid assistant turns: 800+200 input, 1500+600 output
    assert summary.input_tokens == 1000
    assert summary.output_tokens == 2100
    assert summary.assistant_turns == 2


def test_parse_session_r3_counts_family_switches():
    """sonnet -> opus -> sonnet -> haiku = three adjacent family changes."""
    summary = parse_session(FIXTURES / "rigorous" / "session_r3_switch.jsonl")
    assert summary.model_switches == 3


# ---------- robustness ----------------------------------------------------


def test_parse_session_empty_file(tmp_path):
    target = tmp_path / "empty.jsonl"
    target.write_text("", encoding="utf-8")
    summary = parse_session(target)
    assert summary.assistant_turns == 0
    assert summary.cost_usd == 0.0
    assert summary.cache_read_ratio == 0.0
    assert summary.savings_pct == 0.0


def test_parse_session_missing_usage_block_is_skipped(tmp_path):
    target = tmp_path / "no_usage.jsonl"
    target.write_text(
        '{"type":"assistant","timestamp":"2026-05-04T00:00:00Z",'
        '"message":{"role":"assistant","model":"claude-sonnet-4-6"}}\n',
        encoding="utf-8",
    )
    summary = parse_session(target)
    assert summary.assistant_turns == 0


def test_parse_session_handles_non_dict_event(tmp_path):
    target = tmp_path / "weird.jsonl"
    # Some lines are JSON but not objects -- must not raise.
    target.write_text('"a string"\n[1,2,3]\nnull\n', encoding="utf-8")
    summary = parse_session(target)
    assert summary.assistant_turns == 0


# ---------- discover_session_files ----------------------------------------


def test_discover_session_files_returns_sorted_jsonl(tmp_path):
    (tmp_path / "b.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "a.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "not_jsonl.txt").write_text("", encoding="utf-8")
    found = discover_session_files(tmp_path)
    assert [p.name for p in found] == ["a.jsonl", "b.jsonl"]


def test_discover_session_files_handles_missing_dir(tmp_path):
    assert discover_session_files(tmp_path / "nope") == []


# ---------- parse_sessions + cache ----------------------------------------


def test_parse_sessions_caches_by_mtime(tmp_path):
    """Second invocation must skip reparsing when mtime unchanged."""
    fixture = FIXTURES / "quick" / "session_q1.jsonl"
    copy = tmp_path / "q1.jsonl"
    copy.write_bytes(fixture.read_bytes())
    cache_path = tmp_path / "parse-cache.json"

    first = parse_sessions([copy], cache_path=cache_path)
    assert len(first) == 1
    assert cache_path.exists()
    cache_data = json.loads(cache_path.read_text())
    assert str(copy) in cache_data
    mtime_after_first = cache_path.stat().st_mtime

    # Sleep just long enough that a second write would have a different mtime.
    time.sleep(0.05)
    second = parse_sessions([copy], cache_path=cache_path)
    assert len(second) == 1
    assert second[0].input_tokens == first[0].input_tokens
    # Cache file was not rewritten because no entries changed.
    assert cache_path.stat().st_mtime == mtime_after_first


def test_parse_sessions_reparses_when_mtime_changes(tmp_path):
    fixture = FIXTURES / "quick" / "session_q1.jsonl"
    copy = tmp_path / "q1.jsonl"
    copy.write_bytes(fixture.read_bytes())
    cache_path = tmp_path / "parse-cache.json"

    parse_sessions([copy], cache_path=cache_path)

    # Append a new assistant turn and bump mtime.
    with copy.open("a", encoding="utf-8") as fp:
        fp.write(
            '{"type":"assistant","timestamp":"2026-05-04T10:01:00.000Z","sessionId":"q1",'
            '"message":{"role":"assistant","model":"claude-sonnet-4-6","usage":'
            '{"input_tokens":1000,"output_tokens":1000,"cache_creation_input_tokens":0,'
            '"cache_read_input_tokens":0}}}\n'
        )
    new_mtime = time.time() + 1
    import os

    os.utime(copy, (new_mtime, new_mtime))

    second = parse_sessions([copy], cache_path=cache_path)
    # Should reflect the new turn (input_tokens jumped by 1000).
    assert second[0].input_tokens == 180 + 1000


def test_parse_sessions_use_cache_false_always_reparses(tmp_path):
    fixture = FIXTURES / "quick" / "session_q1.jsonl"
    copy = tmp_path / "q1.jsonl"
    copy.write_bytes(fixture.read_bytes())
    cache_path = tmp_path / "parse-cache.json"

    parse_sessions([copy], cache_path=cache_path, use_cache=False)
    # No cache file should be written
    assert not cache_path.exists()


def test_parse_sessions_skips_missing_files(tmp_path):
    cache_path = tmp_path / "parse-cache.json"
    summaries = parse_sessions([tmp_path / "nope.jsonl"], cache_path=cache_path)
    assert summaries == []


# ---------- performance smoke test ----------------------------------------


def test_parse_sessions_under_2s_for_100_files(tmp_path):
    """Spec §Performance: <2s for 100 sessions / 50 MB total.

    We can't realistically build 50 MB of fixtures inline, but we can copy a
    representative session 100 times and require comfortably-under-2s. This
    guards against catastrophic regressions, not 50 MB scale.
    """
    fixture = FIXTURES / "standard" / "session_s1.jsonl"
    for i in range(100):
        (tmp_path / f"sess_{i:03d}.jsonl").write_bytes(fixture.read_bytes())
    files = list(tmp_path.glob("*.jsonl"))
    cache_path = tmp_path / "parse-cache.json"

    start = time.perf_counter()
    summaries = parse_sessions(files, cache_path=cache_path)
    elapsed = time.perf_counter() - start

    assert len(summaries) == 100
    assert elapsed < 2.0, f"parsing 100 files took {elapsed:.2f}s (budget 2.0s)"


# ---------- SessionSummary roundtrip --------------------------------------


def test_session_summary_roundtrip():
    s = SessionSummary(
        session_id="abc",
        file_path="/tmp/abc.jsonl",
        file_mtime=1234.0,
        input_tokens=100,
        output_tokens=200,
    )
    restored = SessionSummary.from_dict(s.to_dict())
    assert restored == s
