"""odoo-deploy — CLI entry point.

Subcommands:
    validate          Static validation of a changeset (no Odoo connection)
    preflight         Odoo-aware validation against an env (read-only)
    deploy            Apply a changeset to an env
    verify            Read-only state check against an env
    rollback          Restore a previous deployment from snapshots
    status            Print audit + registry state across all envs
    export-baseline   Snapshot the customization layer into baseline/<env>/

Every subcommand operates against an instance repository identified by
--repo (default: cwd). The deployer code itself lives in OdooAiDeployer
and contains no instance-specific state.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import Paths, VALID_ENVS, __version__, die
from .audit import audit_read
from .deploy import cmd_deploy
from .export_baseline import cmd_export_baseline
from .hash_changeset import changeset_sha256
from .preflight import cmd_preflight
from .rollback import cmd_rollback
from .validate_changeset import cmd_validate
from .verify import cmd_verify


def cmd_status(paths: Paths, changeset_id: str) -> int:
    cdir = paths.changeset_dir(changeset_id)
    sha_local = changeset_sha256(cdir) if cdir.is_dir() else None

    print(f"changeset: {changeset_id}")
    print(f"folder:    {cdir} {'(exists)' if cdir.is_dir() else '(MISSING)'}")
    print(f"sha256:    {sha_local or '-'}")
    print()
    print("audit files:")
    for env in VALID_ENVS:
        audit = audit_read(paths, env, changeset_id)
        if audit:
            sha_match = "(sha matches)" if sha_local and audit.get("manifest_sha256") == sha_local else "(SHA MISMATCH)"
            print(f"  {env:11} {audit.get('status','?'):16} {audit.get('finished_at','-')} by {audit.get('applied_by','?')} {sha_match}")
        else:
            print(f"  {env:11} -")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="odoo-deploy",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--repo", default=".",
                   help="Path to the instance repository (default: cwd)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("validate", help="Static validation of a changeset")
    s.add_argument("--changeset", required=True)

    s = sub.add_parser("preflight", help="Odoo-aware validation against an env")
    s.add_argument("--env", choices=VALID_ENVS, required=True)
    s.add_argument("--changeset", required=True)

    s = sub.add_parser("deploy", help="Apply a changeset to an env")
    s.add_argument("--env", choices=VALID_ENVS, required=True)
    s.add_argument("--changeset", required=True)
    s.add_argument("--force", action="store_true",
                   help="Re-apply even if registry says applied. Dev only.")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--commit", dest="commit", action="store_true", default=None)
    s.add_argument("--no-commit", dest="commit", action="store_false")

    s = sub.add_parser("verify", help="Read-only state check against an env")
    s.add_argument("--env", choices=VALID_ENVS, required=True)
    s.add_argument("--changeset", required=True)

    s = sub.add_parser("rollback", help="Roll back a deployment")
    s.add_argument("--env", choices=VALID_ENVS, required=True)
    s.add_argument("--changeset", required=True)
    s.add_argument("--dry-run", action="store_true")

    s = sub.add_parser("status", help="Show audit + registry state across envs")
    s.add_argument("--changeset", required=True)

    s = sub.add_parser("export-baseline",
                       help="Snapshot the customization layer into baseline/<env>/")
    s.add_argument("--env", choices=VALID_ENVS, default="production")

    args = p.parse_args(argv)

    repo_root = Path(args.repo).resolve()
    if not repo_root.is_dir():
        die(f"--repo path is not a directory: {repo_root}")
    paths = Paths(repo_root)

    if args.cmd == "validate":
        return cmd_validate(paths, args.changeset)
    if args.cmd == "preflight":
        return cmd_preflight(paths, args.env, args.changeset)
    if args.cmd == "deploy":
        commit_default = (args.env == "dev")
        commit = commit_default if args.commit is None else args.commit
        return cmd_deploy(paths, args.env, args.changeset,
                          force=args.force, dry_run=args.dry_run, commit=commit)
    if args.cmd == "verify":
        return cmd_verify(paths, args.env, args.changeset)
    if args.cmd == "rollback":
        return cmd_rollback(paths, args.env, args.changeset, dry_run=args.dry_run)
    if args.cmd == "status":
        return cmd_status(paths, args.changeset)
    if args.cmd == "export-baseline":
        return cmd_export_baseline(paths, args.env)
    die(f"unknown command: {args.cmd}")


if __name__ == "__main__":
    sys.exit(main())
