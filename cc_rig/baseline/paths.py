"""Filesystem paths and identity hashing for baseline state.

Baseline state lives outside the project so multiple projects share one rollup:
  ~/.cc-rig/baseline.json     -- the canonical Baseline document
  ~/.cc-rig/parse-cache.json  -- JSONL parse cache keyed by file mtime

Session JSONLs are produced by Claude Code itself in:
  ~/.claude/projects/<cwd-with-slashes-as-dashes>/*.jsonl
"""

from __future__ import annotations

import getpass
import hashlib
import os
from pathlib import Path
from typing import Optional

BASELINE_DIR = Path.home() / ".cc-rig"
BASELINE_PATH = BASELINE_DIR / "baseline.json"
PARSE_CACHE_PATH = BASELINE_DIR / "parse-cache.json"


def project_path_hash(abs_path: Path) -> str:
    """Stable 16-hex-char hash of an absolute project path.

    Used as the key in Baseline.projects so raw paths never leave the user's
    machine. SHA256[:16] = 64 bits of entropy, ample for collision avoidance
    across the projects a single user touches.
    """
    resolved = str(Path(abs_path).resolve())
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]


def user_id_hash() -> str:
    """Stable 16-hex-char hash identifying this user on this machine.

    Combines hostname + login name. Used only for the baseline's user_id_hash
    field so a future sync feature can disambiguate machines. No raw identity
    leaves disk.
    """
    try:
        host = os.uname().nodename
    except AttributeError:
        host = os.environ.get("COMPUTERNAME", "unknown")
    try:
        user = getpass.getuser()
    except Exception:
        user = os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"
    return hashlib.sha256(f"{host}:{user}".encode("utf-8")).hexdigest()[:16]


def claude_projects_dir(cwd: Optional[Path] = None) -> Path:
    """Return the ~/.claude/projects/<encoded-cwd>/ directory for `cwd`.

    Claude Code encodes the cwd by replacing both '/' and '_' with '-'.
    Confirmed from an installed copy: /home/x/python_projects/foo on disk
    becomes -home-x-python-projects-foo under ~/.claude/projects/.
    """
    target = Path(cwd) if cwd is not None else Path.cwd()
    encoded = str(target.resolve()).replace("/", "-").replace("_", "-")
    return Path.home() / ".claude" / "projects" / encoded
