"""Workflow-discipline audit from Claude Code session logs.

The v3.2 spec framed `audit` around slash-command chain compliance
(/plan -> /review -> /test). That data is NOT in the JSONL session stream
(only assistant events with model/usage/tool_use survive), so reporting
compliance numbers would be fabricated. Instead this audits the discipline
signals that ARE observable per session:

  - cache hygiene: CLAUDE.md edits and model switches mid-session
  - cache health:  the realized cache read ratio
  - session shape: assistant turns and the dominant model

and renders a tier-fit verdict from them. Honest by construction: it reports
what the logs actually contain.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Iterable

from cc_rig.baseline.jsonl import SessionSummary


@dataclass
class AuditCheck:
    """One pass/warn/info discipline signal."""

    name: str
    status: str  # "pass" | "warn" | "info"
    detail: str = ""


@dataclass
class AuditReport:
    """Shape rendered by `cc-rig audit`. JSON-serializable via to_dict."""

    tier: str
    window_sessions: int
    sessions_analyzed: int = 0
    claudemd_edit_sessions: int = 0
    model_switch_sessions: int = 0
    avg_cache_ratio: float = 0.0
    avg_turns: float = 0.0
    primary_family: str = ""
    checks: list = field(default_factory=list)  # list[AuditCheck]
    verdict: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["checks"] = [asdict(c) for c in self.checks]
        return d


def _session_end(s: SessionSummary) -> str:
    return s.ended_at or s.started_at or ""


def _verdict(tier: str, breaker_rate: float, avg_ratio: float) -> str:
    tier = tier or "your tier"
    if breaker_rate <= 0.10 and avg_ratio >= 0.80:
        return f"Clean cache discipline, consistent with {tier}."
    if breaker_rate >= 0.40 or avg_ratio < 0.60:
        return (
            f"Looser cache discipline than {tier} implies. Pin the model and keep "
            "CLAUDE.md stable mid-session, or drop to a lighter tier."
        )
    return f"Reasonable discipline for {tier}, with room to tighten."


def compute_audit(
    summaries: Iterable[SessionSummary],
    *,
    tier: str = "",
    last_n: int = 10,
) -> AuditReport:
    """Audit the most recent `last_n` sessions for workflow discipline."""
    dated = [s for s in summaries if _session_end(s)]
    window = sorted(dated, key=_session_end, reverse=True)[: max(1, last_n)]
    n = len(window)

    if n == 0:
        return AuditReport(
            tier=tier,
            window_sessions=last_n,
            sessions_analyzed=0,
            verdict="No sessions to audit yet. Run a Claude Code session in this project.",
        )

    cme = sum(1 for s in window if s.claudemd_edits > 0)
    msw = sum(1 for s in window if s.model_switches > 0)
    avg_ratio = sum(s.cache_read_ratio for s in window) / n
    avg_turns = sum(s.assistant_turns for s in window) / n
    fams = [s.primary_family for s in window if s.primary_family]
    primary = max(set(fams), key=fams.count) if fams else ""

    checks: list = []
    if cme == 0:
        checks.append(AuditCheck("CLAUDE.md stable during sessions", "pass"))
    else:
        checks.append(
            AuditCheck(
                "CLAUDE.md edited mid-session",
                "warn",
                f"{cme}/{n} sessions; editing CLAUDE.md invalidates the prompt cache",
            )
        )
    if msw == 0:
        checks.append(AuditCheck("Model pinned within sessions", "pass"))
    else:
        checks.append(
            AuditCheck(
                "Mid-session model switches",
                "warn",
                f"{msw}/{n} sessions; switching rebuilds the prompt cache",
            )
        )
    if avg_ratio >= 0.80:
        checks.append(AuditCheck("Cache read ratio healthy", "pass", f"avg {avg_ratio * 100:.0f}%"))
    elif avg_ratio >= 0.60:
        checks.append(
            AuditCheck("Cache read ratio moderate", "warn", f"avg {avg_ratio * 100:.0f}%")
        )
    else:
        checks.append(AuditCheck("Cache read ratio low", "warn", f"avg {avg_ratio * 100:.0f}%"))
    checks.append(
        AuditCheck(
            "Session shape", "info", f"avg {avg_turns:.0f} turns, primarily {primary or 'n/a'}"
        )
    )

    breaker_rate = (cme + msw) / (2 * n)
    return AuditReport(
        tier=tier,
        window_sessions=last_n,
        sessions_analyzed=n,
        claudemd_edit_sessions=cme,
        model_switch_sessions=msw,
        avg_cache_ratio=round(avg_ratio, 4),
        avg_turns=round(avg_turns, 1),
        primary_family=primary,
        checks=checks,
        verdict=_verdict(tier, breaker_rate, avg_ratio),
    )


__all__ = ["AuditCheck", "AuditReport", "compute_audit"]
