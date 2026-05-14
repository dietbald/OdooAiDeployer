"""AI-branch restricted-folder guard.

Single source of truth for which paths AI is forbidden to touch on `ai/*`
branches. Previously lived as a bash regex inline in validate.yml — moved
here for testability and to keep the rule set in one place across all
instance repos (drift between BiccOdoo's workflow and the template caused
real outages once).

Workflows call `odoo-deploy guard-paths --base <ref>` which walks
the commit range `base..HEAD`, **skips commits authored by
github-actions[bot]** (CI auto-commits from deploy/promote/rollback that
legitimately write audits + rollback_snapshots back to the triggering
branch), accumulates files changed across the remaining AI-authored
commits, and feeds the result to `find_violations`.

Without the bot-skip, the guard would block any PR back to main after a
promote-staging or promote-prod run, since those workflows commit
audits/staging/ and audits/production/ JSONs to the source branch —
landing inside restricted prefixes.
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


# Author signatures used by CI auto-commits (audit + rollback_snapshot
# pushes from deploy-dev / promote-staging / promote-prod / rollback /
# export-baseline). These commits land back on the triggering branch and
# would otherwise look like AI edits to the cumulative diff.
_BOT_AUTHOR_MARKERS: tuple[str, ...] = (
    "github-actions[bot]",
    "41898282+github-actions[bot]@users.noreply.github.com",
)


def _is_bot_author(email: str) -> bool:
    return any(marker in email for marker in _BOT_AUTHOR_MARKERS)


def _git_changed_files(base: str, repo_root: Path) -> list[str]:
    """Return paths AI changed in HEAD vs `base`.

    Distinct from a plain `git diff <base>...HEAD`: this walks the commit
    range and unions only the file lists from commits NOT authored by
    `github-actions[bot]`. CI auto-commits (audit JSONs, rollback snapshots,
    baseline exports written back to the branch by deploy/promote/rollback
    workflows) are excluded so the guard doesn't blame AI for files CI
    itself wrote.

    Includes deletions on purpose: deleting a workflow is just as much a
    guard-bypass as modifying one. --diff-filter not applied here for that
    reason (deletes get caught).
    """
    log = subprocess.run(
        ["git", "log", "--format=%H|%ae", f"{base}..HEAD"],
        cwd=repo_root, capture_output=True, text=True,
    )
    if log.returncode != 0:
        print(f"ERROR: git log vs '{base}' failed: {log.stderr.strip()}",
              file=sys.stderr)
        sys.exit(2)

    non_bot_shas: list[str] = []
    bot_skipped = 0
    for line in log.stdout.splitlines():
        sha, _, email = line.partition("|")
        sha, email = sha.strip(), email.strip()
        if not sha:
            continue
        if _is_bot_author(email):
            bot_skipped += 1
            continue
        non_bot_shas.append(sha)

    if bot_skipped:
        print(f"[guard] excluded {bot_skipped} CI auto-commit(s) from diff",
              file=sys.stderr)
    if not non_bot_shas:
        return []

    files: set[str] = set()
    for sha in non_bot_shas:
        diff = subprocess.run(
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", sha],
            cwd=repo_root, capture_output=True, text=True,
        )
        if diff.returncode != 0:
            print(f"ERROR: git diff-tree for {sha} failed: "
                  f"{diff.stderr.strip()}", file=sys.stderr)
            sys.exit(2)
        for f in diff.stdout.splitlines():
            f = f.strip()
            if f:
                files.add(f)
    return sorted(files)


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
