"""`cc-rig audit`: workflow-discipline read of recent sessions.

Reads the project's session JSONLs, scores observable discipline signals
(cache hygiene, model pinning, cache health, session shape) against the
project tier, and prints a tier-fit verdict. See cc_rig.baseline.audit for
why this reports observable signals rather than slash-command compliance.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from cc_rig.baseline.audit import AuditReport, compute_audit
from cc_rig.baseline.jsonl import discover_session_files, parse_sessions
from cc_rig.baseline.paths import PARSE_CACHE_PATH, claude_projects_dir
from cc_rig.baseline.state import StateSchemaError, load_state, state_path


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the `cc-rig audit` flags."""
    parser.add_argument("-d", "--dir", default=".", help="Project directory (default: current)")
    parser.add_argument(
        "--last", type=int, default=10, help="Recent sessions to audit (default: 10)"
    )
    parser.add_argument("--tier", default="", help="Override tier (default: from project state)")
    parser.add_argument(
        "--json", dest="as_json", action="store_true", help="Emit the report as JSON"
    )
    parser.add_argument("--projects-dir", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--cache-path", type=Path, default=None, help=argparse.SUPPRESS)


def _tier_from_state(project_dir: Path) -> str:
    try:
        st = load_state(state_path(project_dir))
    except StateSchemaError:
        return ""
    if st is None:
        return ""
    return str(st.config_snapshot.get("tier", "")) if st.config_snapshot else ""


def _render(report: AuditReport, *, project_name: str) -> None:
    from rich.console import Console
    from rich.panel import Panel

    console = Console(no_color=bool(os.environ.get("NO_COLOR")))
    console.print()
    console.print(f"[bold cyan]/cc-rig audit[/]  [dim]· {project_name} · tier {report.tier}[/]")
    console.print()

    if report.sessions_analyzed == 0:
        console.print(f"[yellow]{report.verdict}[/]")
        return

    console.print(
        f"Audited the last [bold]{report.sessions_analyzed}[/] session(s)."
        f"  [dim](slash-command chains are not in session logs; "
        f"this reads observable discipline.)[/]"
    )
    console.print()
    for c in report.checks:
        if c.status == "pass":
            mark = "[green]✓[/]"
        elif c.status == "warn":
            mark = "[yellow]⚠[/]"
        else:
            mark = "[dim]•[/]"
        detail = f"  [dim]{c.detail}[/]" if c.detail else ""
        console.print(f"  {mark} {c.name}{detail}")
    console.print()
    console.print(Panel(report.verdict, border_style="cyan", expand=False, padding=(0, 2)))


def run(args: argparse.Namespace) -> int:
    """Entrypoint dispatched from cc_rig.cli.main."""
    project_dir = Path(getattr(args, "dir", ".") or ".").resolve()
    projects_dir = (
        Path(args.projects_dir).resolve()
        if getattr(args, "projects_dir", None)
        else claude_projects_dir(project_dir)
    )
    cache_path = Path(args.cache_path) if getattr(args, "cache_path", None) else PARSE_CACHE_PATH

    summaries = parse_sessions(discover_session_files(projects_dir), cache_path=cache_path)
    tier = args.tier or _tier_from_state(project_dir) or "your tier"
    report = compute_audit(summaries, tier=tier, last_n=args.last)

    if args.as_json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        _render(report, project_name=project_dir.name)

    _stamp(project_dir)
    return 0


def _stamp(project_dir: Path) -> None:
    from cc_rig.refresh import reconstruct_config, stamp_state

    try:
        config = reconstruct_config(project_dir)
    except FileNotFoundError:
        config = None
    stamp_state(project_dir, "last_audit", config)


__all__ = ["add_arguments", "run"]
