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
import os
import sys
from datetime import date, datetime, timezone
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

    if args.as_json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        from rich.console import Console

        console = Console(no_color=bool(os.environ.get("NO_COLOR")))
        render_report(console, report, sources_dir=projects_dir, sessions_seen=len(session_files))
    return 0


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _fmt_cost(cost: float) -> str:
    return f"${cost:,.2f}"


def _fmt_week(week_start: str) -> str:
    """Render an ISO Monday date as a compact 'Mon DD' label."""
    try:
        return date.fromisoformat(week_start).strftime("%b %d")
    except (ValueError, TypeError):
        return week_start


def _pct_style(pct: float) -> str:
    """Semantic color for a savings/cache percentage."""
    if pct >= 80:
        return "green"
    if pct >= 60:
        return "yellow"
    return "red"


def _bar(value: float, vmax: float, width: int = 14) -> str:
    """A horizontal Unicode bar whose length is proportional to value/vmax.

    Uses full blocks plus a left-eighth partial for sub-character precision.
    Returns "" for non-positive values (e.g. empty weeks).
    """
    if vmax <= 0 or value <= 0:
        return ""
    frac = min(1.0, value / vmax)
    units = frac * width
    full = int(units)
    rem = units - full
    bar = "█" * full
    if full < width:
        eighths = " ▏▎▍▌▋▊▉"
        idx = int(rem * 8)
        if idx > 0:
            bar += eighths[idx]
    return bar or "▏"


def _breaker_remedy(name: str) -> str:
    """One-line fix for a named cache breaker."""
    remedies = {
        "Mid-session model switches": "pin the model for the next session.",
        "CLAUDE.md edits during session": "keep CLAUDE.md stable while a session is live.",
    }
    return remedies.get(name, "tighten your cache hygiene.")


def _lower_first(s: str) -> str:
    return s[:1].lower() + s[1:] if s else s


def _coaching_read(report: SavingsReport) -> list:
    """Compose an adaptive editorial paragraph (data-driven, not always praise)."""
    sentences: list = []
    pct = report.savings_pct

    if pct >= 80:
        sentences.append(
            f"Your {pct:.1f}% savings rate is strong, comfortably above what most projects sustain."
        )
    elif pct >= 60:
        sentences.append(f"Your {pct:.1f}% savings rate is solid, with room to push higher.")
    elif pct >= 40:
        sentences.append(
            f"Your {pct:.1f}% savings rate is middling; cache hygiene is leaving money "
            "on the table."
        )
    else:
        sentences.append(
            f"Your {pct:.1f}% savings rate is low; most of your spend is uncached input."
        )

    active = [w for w in report.weekly_trend if w.session_count > 0]
    if len(active) >= 2:
        last, prev = active[-1], active[-2]
        delta = last.savings_pct - prev.savings_pct
        if delta >= 3:
            sentences.append(
                f"It is trending up from {prev.savings_pct:.0f}% the prior active week."
            )
        elif delta <= -3:
            sentences.append(
                f"It slipped from {prev.savings_pct:.0f}% the prior active week, worth a look."
            )

    if report.cache_breakers:
        top = max(report.cache_breakers, key=lambda b: b.estimated_cost_usd)
        remedy = _breaker_remedy(top.name)
        if top.estimated_cost_usd > 0 and report.cost_usd > 0:
            share = top.estimated_cost_usd / report.cost_usd * 100
            sentences.append(
                f"The {_lower_first(top.name)} cost roughly {_fmt_cost(top.estimated_cost_usd)} "
                f"(~{share:.0f}% of this period's spend); {remedy}"
            )
        else:
            sentences.append(f"Watch the {_lower_first(top.name)}: {remedy}")
    elif pct >= 80:
        sentences.append(
            "No cache breakers showed up this period; keep the model pinned and CLAUDE.md stable."
        )

    return sentences


def render_report(console, report: SavingsReport, *, sources_dir: Path, sessions_seen: int) -> None:
    """Render a SavingsReport to a Rich console (color/box adapt to the terminal).

    Color and Unicode box drawing are produced when stdout is a real terminal;
    piped/non-TTY output (and NO_COLOR) degrade to plain text automatically, so
    the same code path serves humans and tests.
    """
    from rich.box import ROUNDED, SIMPLE_HEAVY
    from rich.padding import Padding
    from rich.panel import Panel
    from rich.table import Table

    console.print()
    console.print(f"[bold cyan]/cc-rig savings[/]  [dim]· last {report.window_days} days[/]")
    console.print()

    if report.session_count == 0:
        console.print(f"[yellow]No sessions found yet for {report.project_name}.[/]")
        if sessions_seen == 0:
            console.print(f"  Looked in: {sources_dir}", soft_wrap=True)
            console.print("  Run a Claude Code session in this project, then try again.")
        else:
            console.print(
                f"  Found {sessions_seen} JSONL file(s), none in the last {report.window_days}d.",
                soft_wrap=True,
            )
        return

    cache_total = report.cache_read_tokens + report.cache_create_tokens
    saved = max(0.0, report.cost_uncached_usd - report.cost_usd)

    # Hero panel: the saved figure is the headline, not buried in the ledger.
    sub = f"{report.project_name} · {report.session_count} sessions"
    if report.primary_family:
        sub += f" · primarily {report.primary_family}"
    if saved > 0:
        headline = (
            f"[bold]You saved [/][bold green]{_fmt_cost(saved)}[/][bold] in "
            f"{report.window_days} days[/]   "
            f"[{_pct_style(report.savings_pct)}]{report.savings_pct:.1f}% vs uncached[/]"
        )
        border = "green"
    else:
        headline = (
            f"[bold]Tracking {report.session_count} sessions over {report.window_days} days[/]"
        )
        border = "cyan"
    console.print(
        Panel(
            f"{headline}\n[dim]{sub}[/]",
            box=ROUNDED,
            border_style=border,
            padding=(0, 2),
            expand=False,
        )
    )
    console.print()

    # Ledger: plain styled lines (keeps "Sessions: N" intact for parsing/tests).
    console.print(f"[bold]{report.project_name}[/]")
    console.print(f"  Sessions: {report.session_count}", soft_wrap=True)
    console.print(
        f"  Input tokens: {_fmt_tokens(report.input_tokens + cache_total)}"
        f"  [dim](cache read {_fmt_tokens(report.cache_read_tokens)} = "
        f"{report.cache_read_ratio * 100:.0f}%)[/]",
        soft_wrap=True,
    )
    console.print(f"  Total cost: {_fmt_cost(report.cost_usd)}", soft_wrap=True)
    console.print(
        f"  Vs uncached: {_fmt_cost(report.cost_uncached_usd)}"
        f"  [green]→ saved {_fmt_cost(saved)} ({report.savings_pct:.1f}%)[/]",
        soft_wrap=True,
    )
    console.print()

    # Trend table with a per-week spend bar and a "you are here" marker.
    max_cost = max((w.cost_usd for w in report.weekly_trend), default=0.0)
    table = Table(
        title=f"Trend ({len(report.weekly_trend)}-week rolling):",
        title_justify="left",
        title_style="bold",
        box=SIMPLE_HEAVY,
        expand=False,
        pad_edge=False,
    )
    table.add_column("Week")
    table.add_column("Saved", justify="right")
    table.add_column("Sessions", justify="right")
    table.add_column("Cost", justify="right")
    table.add_column("Spend", no_wrap=True)
    table.add_column("", no_wrap=True)
    for i, w in enumerate(report.weekly_trend):
        marker = "[dim]<- you are here[/]" if i == len(report.weekly_trend) - 1 else ""
        if w.session_count == 0:
            table.add_row(_fmt_week(w.week_start), "[dim]—[/]", "0", "[dim]$0[/]", "", marker)
        else:
            table.add_row(
                _fmt_week(w.week_start),
                f"[{_pct_style(w.savings_pct)}]{w.savings_pct:.0f}%[/]",
                str(w.session_count),
                _fmt_cost(w.cost_usd),
                f"[cyan]{_bar(w.cost_usd, max_cost)}[/]",
                marker,
            )
    console.print(table)

    # Cache-breaker callout: severity-colored panel with a one-line "why".
    if report.cache_breakers:
        n = len(report.cache_breakers)
        worst_cost = max((b.estimated_cost_usd for b in report.cache_breakers), default=0.0)
        lines = []
        for b in report.cache_breakers:
            note = (
                f" (~{_fmt_cost(b.estimated_cost_usd)} estimated)"
                if b.estimated_cost_usd > 0
                else ""
            )
            lines.append(f"{b.name}: {b.session_count} session(s){note}")
            if b.detail:
                lines.append(f"[dim]{b.detail}[/]")
        title = f"⚠  {n} cache breaker{'' if n == 1 else 's'} this period"
        border = "red" if report.cost_usd > 0 and worst_cost >= 0.2 * report.cost_usd else "yellow"
        console.print()
        console.print(
            Panel(
                "\n".join(lines),
                title=title,
                title_align="left",
                border_style=border,
                box=ROUNDED,
                padding=(0, 2),
                expand=False,
            )
        )

    # Coaching read: one editorial paragraph naming the biggest lever.
    coaching = _coaching_read(report)
    if coaching:
        console.print()
        console.print("[bold cyan]▸ Coaching read[/]")
        console.print(Padding(" ".join(coaching), (0, 0, 0, 3)))

    if report.cross_project_rank is not None:
        rank, total = report.cross_project_rank
        console.print()
        if rank == 1:
            console.print(f"[green]Cross-project rank: 1 of {total} (highest-saving project).[/]")
        else:
            console.print(f"Cross-project rank: {rank} of {total}.")

    console.print()
    console.print(
        f"[dim]Pricing verified: {PRICING_VERIFIED_DATE} · run cc-rig savings --json for raw.[/]"
    )


__all__ = ["add_arguments", "render_report", "run"]
