"""OdooAiDeployer — fixed deployment engine for Odoo customizations.

This package is the reusable deployer. It is consumed by per-instance
repositories (BiccOdoo, ContourOdoo, AutopilotOdoo, ...) which provide
the changesets, baseline, audits, and config.

Public modules:
    cli                    — argparse entry point (`odoo-deploy ...`)
    deploy                 — apply a changeset to an env
    verify                 — read-only state check
    validate_changeset     — static validation
    preflight              — Odoo-aware pre-deploy validation
    rollback               — operation-level rollback
    export_baseline        — capture current Odoo state into baseline/
    audit                  — audit files + in-DB registry + git commit
    hash_changeset         — content-hash a changeset folder
    odoo_client            — XML-RPC connection + call wrapper
    github_setup           — gh API helpers for bootstrapping new instance repos
    handlers               — one module per supported operation type
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

__version__ = "0.1.0"

# Three environments. Names are final.
#   dev         — AI iteration sandbox; auto-deploys allowed; --force allowed
#   staging     — fresh-from-prod validation; manual deploy; gated on dev audit
#   production  — real prod; manual deploy; gated on dev + staging audits
VALID_ENVS = ("dev", "staging", "production")

# In-DB registry key — one ir.config_parameter row holds the JSON list of
# applied changesets per environment database.
REGISTRY_KEY = "odoo_ai_deployer.applied_changesets"


def die(msg: str, code: int = 1) -> None:
    """Exit with an error printed to stderr."""
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def now_iso() -> str:
    """UTC timestamp in ISO-8601 with second precision and Z suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Paths:
    """Resolved filesystem paths for a single instance repository.

    Built once by the CLI from --repo and passed down. Handlers receive
    this so they can locate changeset content, write backups, and emit
    audit files in the correct repo.
    """

    def __init__(self, instance_root: Path):
        self.instance_root = Path(instance_root).resolve()
        self.changesets = self.instance_root / "changesets"
        self.audits = self.instance_root / "audits"
        self.rollback_snapshots = self.instance_root / "rollback_snapshots"
        self.baseline = self.instance_root / "baseline"
        self.reports = self.instance_root / "reports"
        self.config = self.instance_root / "config"
        self.logs = self.instance_root / "logs"

    def changeset_dir(self, changeset_id: str) -> Path:
        return self.changesets / changeset_id

    def audit_file(self, env_name: str, changeset_id: str) -> Path:
        return self.audits / env_name / f"{changeset_id}.json"

    def rollback_dir(self, env_name: str, changeset_id: str) -> Path:
        return self.rollback_snapshots / env_name / changeset_id

    def report_file(self, kind: str, name: str) -> Path:
        return self.reports / kind / name


def load_file_text(changeset_dir: Path, rel: str) -> str:
    """Read a file relative to a changeset folder. Used by handlers for
    arch_file / code_file references in manifests.
    """
    path = changeset_dir / rel
    if not path.is_file():
        die(f"referenced file not found: {path}")
    return path.read_text()
