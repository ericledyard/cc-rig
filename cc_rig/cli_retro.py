"""`cc-rig retro`: one weekly view combining savings, audit, and drift.

The return-trigger destination. It reads the last 7 days of sessions, folds
the week into the cross-project baseline, scores discipline, checks drift,
and renders a single shareable summary with one suggestion for the week.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from cc_rig.baseline.audit import compute_audit
from cc_rig.baseline.jsonl import discover_session_files, parse_sessions
from cc_rig.baseline.paths import (
    BASELINE_PATH,
    PARSE_CACHE_PATH,
    claude_projects_dir,
    project_path_hash,
)
from cc_rig.baseline.savings import (
    compute_savings_report,
    iso_week_start,
    update_baseline_with_report,
)
from cc_rig.baseline.schema import Baseline, BaselineSchemaError, load_baseline, save_baseline


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the `cc-rig retro` flags."""
    parser.add_argument("-d", "--dir", default=".", help="Project directory (default: current)")
    parser.add_argument(
        "--no-baseline", action="store_true", help="Do not read or write ~/.cc-rig/baseline.json"
    )
    parser.add_argument(
        "--json", dest="as_json", action="store_true", help="Emit the report as JSON"
    )
    parser.add_argument("--projects-dir", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--baseline-path", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--cache-path", type=Path, default=None, help=argparse.SUPPRESS)


def _fmt_cost(cost: float) -> str:
    return f"${cost:,.2f}"


def _tier_and_config(project_dir: Path):
    """Return (tier, config_or_None) from the persisted config/state."""
    from cc_rig.baseline.state import StateSchemaError, load_state, state_path
    from cc_rig.refresh import reconstruct_config

    config = None
    try:
        config = reconstruct_config(project_dir)
    except FileNotFoundError:
        config = None
    if config is not None and config.workflow:
        return config.workflow, config
    try:
        st = load_state(state_path(project_dir))
    except StateSchemaError:
        st = None
    tier = ""
    if st is not None and st.config_snapshot:
        tier = str(st.config_snapshot.get("tier", ""))
    return tier or "your tier", config


def _drift_summary(config, project_dir: Path) -> dict:
    if config is None:
        return {"files": None, "cc_version": None}
    from cc_rig.cli_drift import _cc_version_drift
    from cc_rig.refresh import plan_changes

    changes = plan_changes(config, project_dir, "all")
    drifted = [c.rel_path for c in changes if c.status in ("added", "modified")]
    return {"files": drifted, "cc_version": _cc_version_drift()}


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

    summaries = parse_sessions(discover_session_files(projects_dir), cache_path=cache_path)
    tier, config = _tier_and_config(project_dir)

    baseline: Optional[Baseline] = None
    if not args.no_baseline:
        try:
            baseline = load_baseline(baseline_path)
        except BaselineSchemaError as exc:
            print(f"warning: baseline.json unreadable, ignoring ({exc})", file=sys.stderr)
            baseline = None

    savings = compute_savings_report(
        summaries,
        project_hash=p_hash,
        project_name=project_name,
        baseline=baseline,
        window_days=7,
        weeks=4,
    )
    audit = compute_audit(summaries, tier=tier, last_n=10)
    drift = _drift_summary(config, project_dir)
    week_of = iso_week_start(datetime.now(timezone.utc)).isoformat()

    if not args.no_baseline:
        if baseline is None:
            baseline = Baseline()
        update_baseline_with_report(
            baseline,
            project_hash=p_hash,
            project_name=project_name,
            tier=tier,
            report=savings,
            now=datetime.now(timezone.utc),
        )
        save_baseline(baseline, baseline_path)

    if args.as_json:
        print(
            json.dumps(
                {
                    "week_of": week_of,
                    "savings": savings.to_dict(),
                    "audit": audit.to_dict(),
                    "drift": drift,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        _render(week_of, project_name, savings, audit, drift)

    _stamp(project_dir, config)
    return 0


def _render(week_of, project_name, savings, audit, drift) -> None:
    from rich.console import Console
    from rich.panel import Panel

    console = Console(no_color=bool(os.environ.get("NO_COLOR")))
    console.print()
    console.print(f"[bold cyan]/cc-rig retro[/]  [dim]· {project_name} · week of {week_of}[/]")
    console.print()

    if savings.session_count == 0:
        console.print("[yellow]No sessions in the last 7 days.[/] Nothing to retro this week.")
        return

    saved = max(0.0, savings.cost_uncached_usd - savings.cost_usd)
    console.print(
        f"[bold]This week:[/] {savings.session_count} sessions · "
        f"{_fmt_cost(savings.cost_usd)} spent · "
        f"{savings.savings_pct:.0f}% saved ({_fmt_cost(saved)})"
    )
    console.print()

    # Wins: discipline passes + week-over-week cache movement.
    wins = [c.name for c in audit.checks if c.status == "pass"]
    active = [w for w in savings.weekly_trend if w.session_count > 0]
    if len(active) >= 2 and active[-1].savings_pct - active[-2].savings_pct >= 3:
        wins.append(
            f"Savings up from {active[-2].savings_pct:.0f}% to {active[-1].savings_pct:.0f}%"
        )
    if wins:
        console.print("[green]Wins[/]")
        for w in wins:
            console.print(f"  [green]+[/] {w}")
        console.print()

    # Drift: discipline warns + file/version drift.
    warns = [
        f"{c.name} ({c.detail})" if c.detail else c.name for c in audit.checks if c.status == "warn"
    ]
    if drift.get("files"):
        warns.append(f"{len(drift['files'])} generated file(s) diverged from config")
    cc = drift.get("cc_version")
    if cc and cc.get("status") == "ahead":
        warns.append(f"Claude Code {cc['installed']} newer than cc-rig pin {cc['pinned']}")
    if warns:
        console.print("[yellow]Drift[/]")
        for w in warns:
            console.print(f"  [yellow]-[/] {w}")
        console.print()

    # One suggestion + cross-project context.
    console.print(
        Panel(
            audit.verdict,
            title="This week",
            title_align="left",
            border_style="cyan",
            expand=False,
            padding=(0, 2),
        )
    )
    if savings.cross_project_rank is not None:
        rank, total = savings.cross_project_rank
        note = "highest-saving project" if rank == 1 else f"rank {rank} of {total}"
        console.print(f"[dim]Cross-project: {note}.[/]")


def _stamp(project_dir: Path, config) -> None:
    from cc_rig.refresh import stamp_state

    stamp_state(project_dir, "last_retro", config)


__all__ = ["add_arguments", "run"]
