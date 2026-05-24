"""`cc-rig refresh <area>`: re-run one generator with a diff preview + confirm.

Reloads the persisted config, regenerates the requested area into a temp dir,
shows what would change, and (on confirm) writes it back. The companion of
`cc-rig drift`: drift tells you something diverged, refresh realigns it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from cc_rig.refresh import (
    VALID_AREAS,
    apply_area,
    plan_changes,
    reconstruct_config,
    resolve_area,
    stamp_state,
)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the `cc-rig refresh` flags."""
    parser.add_argument(
        "area",
        nargs="?",
        default="all",
        help=f"Area to refresh: {', '.join(VALID_AREAS)} (default: all)",
    )
    parser.add_argument("-d", "--dir", default=".", help="Project directory (default: current)")
    parser.add_argument("-y", "--yes", action="store_true", help="Apply without confirmation")
    parser.add_argument("--dry-run", action="store_true", help="Show the diff but do not write")
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit the change plan as JSON (implies no write)",
    )


def _print_diff(console, diff: str) -> None:
    for line in diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            console.print(line, style="dim", soft_wrap=True)
        elif line.startswith("+"):
            console.print(line, style="green", soft_wrap=True)
        elif line.startswith("-"):
            console.print(line, style="red", soft_wrap=True)
        elif line.startswith("@@"):
            console.print(line, style="cyan", soft_wrap=True)
        else:
            console.print(line, style="dim", soft_wrap=True)


def run(args: argparse.Namespace) -> int:
    """Entrypoint dispatched from cc_rig.cli.main."""
    project_dir = Path(getattr(args, "dir", ".") or ".").resolve()
    area = getattr(args, "area", None) or "all"

    try:
        resolve_area(area)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        config = reconstruct_config(project_dir)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    changes = plan_changes(config, project_dir, area)
    actionable = [c for c in changes if c.status in ("added", "modified")]

    if args.as_json:
        print(
            json.dumps(
                {
                    "area": area,
                    "changes": [{"path": c.rel_path, "status": c.status} for c in actionable],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    from rich.console import Console

    console = Console(no_color=bool(os.environ.get("NO_COLOR")))
    console.print()
    console.print(f"[bold cyan]/cc-rig refresh {area}[/]  [dim]· {project_dir.name}[/]")
    console.print()

    if not actionable:
        console.print(f"[green]Already up to date.[/] {area} matches the current config.")
        return 0

    for c in actionable:
        tag = "[green]+ added[/]" if c.status == "added" else "[yellow]~ modified[/]"
        console.print(f"{tag}  {c.rel_path}")
        if c.diff:
            _print_diff(console, c.diff)
            console.print()

    if args.dry_run:
        console.print(f"[dim]Dry run: {len(actionable)} file(s) would change. Nothing written.[/]")
        return 0

    if not args.yes:
        try:
            resp = input(f"Apply {len(actionable)} change(s) to {area}? [y/N] ").strip().lower()
        except EOFError:
            resp = ""
        if resp not in ("y", "yes"):
            console.print("[dim]Aborted; nothing written.[/]")
            return 0

    apply_area(config, project_dir, area)
    stamp_state(project_dir, "last_refresh", config)
    console.print(f"[green]Refreshed {area}: {len(actionable)} file(s) updated.[/]")
    return 0


__all__ = ["add_arguments", "run"]
