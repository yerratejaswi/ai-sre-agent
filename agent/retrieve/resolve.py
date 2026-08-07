"""Workload → source location.

Resolution is a lookup, not a guess. A workload named `orders-api` might
live in a repo of any name, so the Deployment declares its own source via
annotations. If it doesn't, we say so and every downstream strategy is
skipped — an unresolved repo is a legitimate outcome, not an error.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agent.models import CodeContext

REPO_ANNOTATION = "sre.agent/repo"
SOURCE_ROOT_ANNOTATION = "sre.agent/source-root"


def resolve(context: dict[str, Any], local_checkout: str | None = None) -> CodeContext:
    """Read source location off the workload spec.

    `context` is a collected IncidentContext (dict form, as saved to
    fixtures). `local_checkout` is the path to a clone on disk; in the lab
    this is the repo we're already running from.
    """
    spec = context.get("workload_spec") or {}
    annotations = spec.get("annotations") or {}

    repo_url = annotations.get(REPO_ANNOTATION)
    source_root = annotations.get(SOURCE_ROOT_ANNOTATION)

    ctx = CodeContext(repo_url=repo_url, repo_path=None, commit=None)

    if not repo_url:
        ctx.notes.append(
            f"workload declares no {REPO_ANNOTATION} annotation; "
            "code retrieval unavailable"
        )
        return ctx

    if local_checkout is None:
        ctx.notes.append(f"repo {repo_url} declared but no local checkout provided")
        return ctx

    root = (Path(local_checkout) / source_root) if source_root else Path(local_checkout)
    root = root.as_posix()
    if not os.path.isdir(root):
        ctx.notes.append(f"declared source root does not exist: {root}")
        return ctx

    ctx.repo_path = root
    if source_root:
        ctx.notes.append(f"scoped to source root {source_root}")
    return ctx