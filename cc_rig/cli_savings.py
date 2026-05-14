"""`cc-rig savings` subcommand: longitudinal token-economics report.

Reads ~/.claude/projects/<encoded-cwd>/*.jsonl session logs, aggregates
30-day rollups, renders a human-readable report (or JSON), and persists
weekly rollups to ~/.cc-rig/baseline.json for cross-project comparison.

The CLI surface is intentionally small: one positional-free invocation,
three flags. The work happens in cc_rig.baseline.{jsonl, savings}; this
module is the wire between argparse and that pure logic.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from cc_rig.baseline.jsonl import (
    PRICING_VERIFIED_DATE,
    discover_session_files,
    parse_sessions,
)
from cc_rig.baseline.paths import (
    BASELINE_PATH,
    PARSE_CACHE_PATH,
    claude_projects_dir,
    project_path_hash,
)
from cc_rig.baseline.savings import (
    SavingsReport,
    compute_savings_report,
    update_baseline_with_report,
)
from cc_rig.baseline.schema import Baseline, BaselineSchemaError, load_baseline, save_baseline


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the `cc-rig savings` flags on an existing parser."""
    parser.add_argument(
        "-d",
        "--dir",
        default=".",
        help="Project directory (default: current)",
    )
    parser.add_argument(
        "--weeks",
        type=int,
        default=4,
        help="Number of trailing weeks in the trend (default: 4)",
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=30,
        help="Aggregation window in days (default: 30)",
    )
    parser.add_argument(
        "--tier",
        default="standard",
        help="Tier label recorded into baseline.json (default: standard)",
    )
    parser.add_argument(
        "--no-baseline",
        action="store_true",
        help="Skip cross-project rank and do not read or write ~/.cc-rig/baseline.json",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit the report as JSON instead of a rendered text view",
    )
    parser.add_argument(
        "--baseline-path",
        type=Path,
        default=None,
        help=argparse.SUPPRESS,  # test-only override
    )
    parser.add_argument(
        "--projects-dir",
        type=Path,
        default=None,
        help=argparse.SUPPRESS,  # test-only override
    )
    parser.add_argument(
        "--cache-path",
        type=Path,
        default=None,
        help=argparse.SUPPRESS,  # test-only override
    )


def run(args: argparse.Namespace) -> int:
    """Entrypoint dispatched from cc_rig.cli.main."""
    project_dir = Path(getattr(args, "dir", ".") or ".").resolve()
    project_name = project_dir.name or "project"
    p_hash = project_path_hash(project_dir)

    projects_dir = (
        Path(args.projects_dir).resolve()
        if getattr(args, "projects_dir", None)
        else claude_projects_dir(project_dir)
    )
    cache_path = Path(args.cache_path) if getattr(args, "cache_path", None) else PARSE_CACHE_PATH
    baseline_path = (
        Path(args.baseline_path) if getattr(args, "baseline_path", None) else BASELINE_PATH
    )

    session_files = discover_session_files(projects_dir)
    summaries = parse_sessions(session_files, cache_path=cache_path)

    baseline: Optional[Baseline] = None
    if not args.no_baseline:
        try:
            baseline = load_baseline(baseline_path)
        except BaselineSchemaError as exc:
            print(f"warning: baseline.json unreadable, ignoring ({exc})", file=sys.stderr)
            baseline = None

    report = compute_savings_report(
        summaries,
        project_hash=p_hash,
        project_name=project_name,
        baseline=baseline,
        window_days=args.window_days,
        weeks=args.weeks,
    )

    if not args.no_baseline:
        if baseline is None:
            baseline = Baseline()
        update_baseline_with_report(
            baseline,
            project_hash=p_hash,
            project_name=project_name,
            tier=args.tier,
            report=report,
            now=datetime.now(timezone.utc),
        )
        save_baseline(baseline, baseline_path)

    output = (
        json.dumps(report.to_dict(), indent=2, sort_keys=True)
        if args.as_json
        else render_text(report, sources_dir=projects_dir, sessions_seen=len(session_files))
    )
    print(output)
    return 0


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _fmt_cost(cost: float) -> str:
    return f"${cost:,.2f}"


def render_text(report: SavingsReport, *, sources_dir: Path, sessions_seen: int) -> str:
    """Render a SavingsReport to the human-readable terminal view."""
    lines: list = []
    lines.append("")
    lines.append(f"## /cc-rig savings (last {report.window_days} days)")
    lines.append("")

    if report.session_count == 0:
        lines.append(f"No sessions found yet for {report.project_name}.")
        if sessions_seen == 0:
            lines.append(f"  Looked in: {sources_dir}")
            lines.append("  Run a Claude Code session in this project, then try again.")
        else:
            lines.append(
                f"  Found {sessions_seen} JSONL file(s), none in the last {report.window_days}d."
            )
        return "\n".join(lines) + "\n"

    cache_total = report.cache_read_tokens + report.cache_create_tokens
    saved = max(0.0, report.cost_uncached_usd - report.cost_usd)

    lines.append(f"This project ({report.project_name}):")
    lines.append(f"  Sessions: {report.session_count}")
    lines.append(
        f"  Input tokens: {_fmt_tokens(report.input_tokens + cache_total)}"
        f" (cache read: {_fmt_tokens(report.cache_read_tokens)}"
        f" = {report.cache_read_ratio * 100:.0f}%)"
    )
    lines.append(f"  Total cost: {_fmt_cost(report.cost_usd)}")
    lines.append(
        f"  Vs uncached baseline: {_fmt_cost(report.cost_uncached_usd)}"
        f" (saved {_fmt_cost(saved)}, {report.savings_pct:.1f}%)"
    )
    if report.primary_family:
        lines.append(f"  Primary model: {report.primary_family}")

    lines.append("")
    lines.append(f"Trend ({len(report.weekly_trend)}-week rolling):")
    for i, w in enumerate(report.weekly_trend, start=1):
        marker = "  <- you are here" if i == len(report.weekly_trend) else ""
        if w.session_count == 0:
            lines.append(f"  Week {i} ({w.week_start}): no sessions{marker}")
        else:
            lines.append(
                f"  Week {i} ({w.week_start}): "
                f"{w.savings_pct:.0f}% cache rate, "
                f"{w.session_count} sessions, "
                f"{_fmt_cost(w.cost_usd)}{marker}"
            )

    if report.cache_breakers:
        lines.append("")
        lines.append(f"Top cache breakers (last {report.window_days}d):")
        for b in report.cache_breakers:
            cost_note = (
                f" (~{_fmt_cost(b.estimated_cost_usd)} estimated)"
                if b.estimated_cost_usd > 0
                else ""
            )
            lines.append(f"  {b.name}: {b.session_count} session(s){cost_note}")

    if report.cross_project_rank is not None:
        rank, total = report.cross_project_rank
        if rank == 1:
            lines.append("")
            lines.append(f"Cross-project rank: 1 of {total} (highest-saving project).")
        else:
            lines.append("")
            lines.append(f"Cross-project rank: {rank} of {total}.")

    lines.append("")
    lines.append(f"  Pricing verified: {PRICING_VERIFIED_DATE}.")
    return "\n".join(lines) + "\n"


__all__ = ["add_arguments", "render_text", "run"]
