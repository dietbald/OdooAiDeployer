"""AI-branch restricted-folder guard.

Single source of truth for which paths AI is forbidden to touch on `ai/*`
branches. Previously lived as a bash regex inline in validate.yml — moved
here for testability and to keep the rule set in one place across all
instance repos (drift between BiccOdoo's workflow and the template caused
real outages once).

Workflows call `odoo-deploy guard-paths --base <ref>` which runs
`git diff --name-only <base>...HEAD` and feeds the result to `find_violations`.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Iterable

# Path prefixes AI cannot modify on ai/* branches.
# Keep entries trailing-slashed so prefix-matching never lets `deployer.md`
# slip past `deployer/`.
RESTRICTED_PREFIXES: tuple[str, ...] = (
    "deployer/",
    ".github/workflows/",
    "config/",
    "audits/staging/",
    "audits/production/",
    "rollback_snapshots/staging/",
    "rollback_snapshots/production/",
    "baseline/prod/",
)

# Carve-outs from RESTRICTED_PREFIXES. AI authors blocklists for itself —
# adding a model/xml_id/op-type to refuse-future-changesets is exactly the
# kind of self-regulating change that should not require TJ approval.
ALLOWED_OVERRIDES: tuple[str, ...] = (
    "config/blocklist/",
)


def is_restricted(path: str) -> bool:
    """True iff `path` is inside a restricted prefix and not allowlisted."""
    if not path:
        return False
    for allow in ALLOWED_OVERRIDES:
        if path.startswith(allow):
            return False
    return any(path.startswith(p) for p in RESTRICTED_PREFIXES)


def find_violations(paths: Iterable[str]) -> list[str]:
    """Return the subset of `paths` that AI is not allowed to modify.

    Result is sorted and deduplicated. Empty strings (blank diff lines) are
    ignored.
    """
    seen: set[str] = set()
    for raw in paths:
        p = raw.strip()
        if not p or p in seen:
            continue
        if is_restricted(p):
            seen.add(p)
    return sorted(seen)


def _git_changed_files(base: str, repo_root: Path) -> list[str]:
    """Return paths changed in HEAD vs `base` (any status: added/modified/
    deleted/renamed). Includes deletions on purpose: deleting a workflow
    is just as much a guard-bypass as modifying one."""
    proc = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD"],
        cwd=repo_root, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        print(f"ERROR: git diff vs '{base}' failed: {proc.stderr.strip()}",
              file=sys.stderr)
        sys.exit(2)
    return [line for line in proc.stdout.splitlines() if line.strip()]


def cmd_guard_paths(repo_root: Path, base: str) -> int:
    """CLI entry: diff against `base`, report any restricted-path violations.

    Prints GitHub Actions error annotations (`::error::...`) so violations
    show up inline in the PR / workflow log. Exit 1 on any violation, 0 if
    clean.
    """
    changed = _git_changed_files(base, repo_root)
    violations = find_violations(changed)
    if violations:
        print(f"::error::ai/* branch modified {len(violations)} restricted "
              f"path(s) (deployer/, .github/workflows/, config/ except "
              f"blocklist/, staging+production audits/snapshots, baseline/prod/):")
        for v in violations:
            print(f"::error::  {v}")
        print(f"\nGuard rules live at deployer/restricted_paths.py — "
              f"changing them requires editing the deployer.", file=sys.stderr)
        return 1
    print(f"Restricted-folder guard: OK ({len(changed)} files changed, "
          f"none restricted).")
    return 0
