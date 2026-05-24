"""`cc-rig drift`: report how a project has diverged from what cc-rig would generate.

Three drift signals, all read-only:
  1. File drift  - generated files on disk differ from a fresh regen of the
     current config (manual edits, or a stale catalog).
  2. Config drift - .cc-rig.json changed since the last init/refresh
     (config_hash mismatch vs the project state file).
  3. Version drift - the installed Claude Code differs from the version
     cc-rig is pinned to.

It suggests `cc-rig refresh <area>` to realign; it never writes generated files.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from cc_rig.baseline.state import StateSchemaError, config_hash, load_state, state_path
from cc_rig.config.cc_version import PINNED_CC_VERSION, PINNED_CC_VERSION_STR, detect_cc_version
from cc_rig.refresh import plan_changes, reconstruct_config, stamp_state


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the `cc-rig drift` flags."""
    parser.add_argument("-d", "--dir", default=".", help="Project directory (default: current)")
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit the drift report as JSON",
    )


def _cc_version_drift() -> dict:
    cc = detect_cc_version()
    out = {"pinned": PINNED_CC_VERSION_STR, "installed": cc.version_str, "status": "unknown"}
    if not cc.installed or cc.version is None:
        out["status"] = "not_installed"
    elif cc.version > PINNED_CC_VERSION:
        out["status"] = "ahead"  # CC newer than cc-rig's reference
    elif cc.version < PINNED_CC_VERSION:
        out["status"] = "behind"
    else:
        out["status"] = "match"
    return out


def run(args: argparse.Namespace) -> int:
    """Entrypoint dispatched from cc_rig.cli.main."""
    project_dir = Path(getattr(args, "dir", ".") or ".").resolve()

    try:
        config = reconstruct_config(project_dir)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    changes = plan_changes(config, project_dir, "all")
    drifted = [c for c in changes if c.status in ("added", "modified")]

    # Config drift: stored hash vs current config.
    cur_hash = config_hash(config.to_dict())
    config_changed = False
    try:
        st = load_state(state_path(project_dir))
    except StateSchemaError:
        st = None
    if st is not None and st.config_hash and st.config_hash != cur_hash:
        config_changed = True

    cc = _cc_version_drift()

    if args.as_json:
        print(
            json.dumps(
                {
                    "drifted_files": [{"path": c.rel_path, "status": c.status} for c in drifted],
                    "config_changed": config_changed,
                    "cc_version": cc,
                },
                indent=2,
                sort_keys=True,
            )
        )
        stamp_state(project_dir, "last_drift_check", config)
        return 0

    from rich.console import Console

    console = Console(no_color=bool(os.environ.get("NO_COLOR")))
    console.print()
    console.print(f"[bold cyan]/cc-rig drift[/]  [dim]· {project_dir.name}[/]")
    console.print()

    # 1. File drift
    if drifted:
        console.print(f"[yellow]Generated files diverged from current config: {len(drifted)}[/]")
        for c in drifted:
            tag = "added" if c.status == "added" else "modified"
            console.print(f"  [yellow]~[/] {c.rel_path} [dim]({tag})[/]")
        console.print("  [dim]Run[/] cc-rig refresh <area> [dim]to realign.[/]")
    else:
        console.print("[green]Generated files match the current config.[/]")
    console.print()

    # 2. Config drift
    if config_changed:
        console.print("[yellow].cc-rig.json changed since the last init/refresh.[/]")
        console.print("  [dim]Run[/] cc-rig refresh all [dim]to regenerate against it.[/]")
    else:
        console.print("[green]Config unchanged since last init/refresh.[/]")
    console.print()

    # 3. Version drift
    if cc["status"] == "ahead":
        console.print(
            f"[yellow]Claude Code {cc['installed']} is newer than cc-rig's pin "
            f"{cc['pinned']}.[/] [dim]cc-rig alignment may lag; watch for new settings keys.[/]"
        )
    elif cc["status"] == "behind":
        console.print(
            f"[yellow]Claude Code {cc['installed']} is older than cc-rig's pin "
            f"{cc['pinned']}.[/] [dim]Consider upgrading Claude Code.[/]"
        )
    elif cc["status"] == "not_installed":
        console.print(f"[dim]Claude Code not detected (cc-rig pinned {cc['pinned']}).[/]")
    else:
        console.print(f"[green]Claude Code {cc['installed']} matches cc-rig's pin.[/]")

    stamp_state(project_dir, "last_drift_check", config)
    return 0


__all__ = ["add_arguments", "run"]
