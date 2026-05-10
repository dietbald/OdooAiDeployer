"""Audit, registry, backups, logs, and optional git commit.

Two layers of recording:

1. **In-DB registry** — `ir.config_parameter` row inside each Odoo
   database holds the JSON list of applied changesets. Travels with the
   data when prod is cloned to staging.

2. **Git-tracked audit files** — `audits/<env>/<changeset>.json` written
   after a successful apply. These are the promotion gate's source of
   truth and the AI feedback loop's failure record.

Backups (`rollback_snapshots/<env>/<changeset>/`) are git-tracked content
snapshots taken before every write — used by the rollback engine. They are
NOT gitignored: rollback after a CI run depends on the snapshot being in
the repo (see `templates/instance-repo-template/.gitignore`).
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import REGISTRY_KEY, Paths, now_iso
from .odoo_client import call


# ---------------------------------------------------------------------------
# In-DB registry (ir.config_parameter)
# ---------------------------------------------------------------------------
def registry_read(ctx: dict) -> list[dict]:
    """Return the in-DB list of applied changesets (may be empty)."""
    recs = call(ctx, "ir.config_parameter", "search_read",
                [[("key", "=", REGISTRY_KEY)]],
                {"fields": ["id", "value"], "limit": 1})
    if not recs:
        return []
    raw = recs[0].get("value") or "[]"
    try:
        data = json.loads(raw)
    except Exception:
        return []
    return data if isinstance(data, list) else []


def registry_write(ctx: dict, entries: list[dict]) -> None:
    raw = json.dumps(entries, sort_keys=True)
    recs = call(ctx, "ir.config_parameter", "search",
                [[("key", "=", REGISTRY_KEY)]], {"limit": 1})
    if recs:
        call(ctx, "ir.config_parameter", "write", [recs, {"value": raw}])
    else:
        call(ctx, "ir.config_parameter", "create",
             [{"key": REGISTRY_KEY, "value": raw}])


def registry_record(ctx: dict, changeset_id: str,
                    git_commit_sha: str, manifest_sha256: str) -> None:
    """Insert or replace the registry entry for a changeset."""
    entries = registry_read(ctx)
    entries = [e for e in entries if e.get("id") != changeset_id]
    entries.append({
        "id": changeset_id,
        "git_commit_sha": git_commit_sha,
        "manifest_sha256": manifest_sha256,
        "applied_at": now_iso(),
    })
    entries.sort(key=lambda e: e.get("id", ""))
    registry_write(ctx, entries)


def registry_lookup(ctx: dict, changeset_id: str) -> dict | None:
    for e in registry_read(ctx):
        if e.get("id") == changeset_id:
            return e
    return None


# ---------------------------------------------------------------------------
# Audit files (git-tracked)
# ---------------------------------------------------------------------------
def audit_read(paths: Paths, env_name: str, changeset_id: str) -> dict | None:
    p = paths.audit_file(env_name, changeset_id)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def audit_write(paths: Paths, env_name: str, changeset_id: str,
                payload: dict) -> Path:
    p = paths.audit_file(env_name, changeset_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return p


# ---------------------------------------------------------------------------
# Backups / rollback snapshots
# ---------------------------------------------------------------------------
def backup_record(paths: Paths, env_name: str, changeset_id: str,
                  op_index: int, model: str, record_id: int,
                  before_value: Any, ext: str = "xml") -> Path:
    """Snapshot a record's current state before mutation.

    File path: rollback_snapshots/<env>/<changeset>/operation_<NNN>_<model>_<id>.<ext>
    """
    folder = paths.rollback_dir(env_name, changeset_id)
    folder.mkdir(parents=True, exist_ok=True)
    safe_model = model.replace(".", "_").replace("/", "_")
    name = f"operation_{op_index:03d}_{safe_model}_{record_id}.{ext}"
    p = folder / name
    if isinstance(before_value, str):
        p.write_text(before_value)
    else:
        p.write_text(json.dumps(before_value, indent=2, sort_keys=True))
    return p


# ---------------------------------------------------------------------------
# Logs (jsonl, gitignored)
# ---------------------------------------------------------------------------
def log_op(paths: Paths, env_name: str, payload: dict) -> None:
    paths.logs.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"ts": now_iso(), "env": env_name, **payload})
    with (paths.logs / f"{env_name}.jsonl").open("a") as f:
        f.write(line + "\n")


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------
def git_head_sha(repo_root: Path) -> str:
    """Return the current HEAD commit sha, or 'unknown' if not a git repo."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root, check=True, capture_output=True, text=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def git_commit(repo_root: Path, paths_to_add: list[Path], message: str) -> bool:
    """Stage and commit. Best-effort — never aborts the deploy on failure.

    In GitHub Actions the workflow handles commits explicitly, so this is
    primarily for local dev convenience.
    """
    if not (repo_root / ".git").exists():
        print("[git] not a git repo; skipping commit", file=sys.stderr)
        return False
    try:
        for p in paths_to_add:
            subprocess.run(["git", "add", "--", str(p)],
                           cwd=repo_root, check=True)
        if subprocess.run(["git", "diff", "--cached", "--quiet"],
                          cwd=repo_root).returncode == 0:
            return False
        subprocess.run(["git", "commit", "-m", message],
                       cwd=repo_root, check=True)
        return True
    except subprocess.CalledProcessError as exc:
        print(f"[git] commit failed: {exc}", file=sys.stderr)
        return False
