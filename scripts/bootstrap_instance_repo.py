#!/usr/bin/env python3
"""Bootstrap a new Odoo instance repository from the template.

Workflow:
  1. Clone the (empty) target GitHub repo locally
  2. Copy templates/instance-repo-template/ into it
  3. Substitute {{INSTANCE_NAME}}, {{INSTANCE_DESCRIPTION}}, etc. in
     config/instance.yaml and README.md
  4. Initial commit + push
  5. Create dev/staging/production GitHub environments
  6. Print manual instructions for setting environment secrets and reviewers

Usage:
  python scripts/bootstrap_instance_repo.py \\
    --owner dietbald \\
    --repo BiccOdoo \\
    --instance-name bicc-igebc \\
    --description "BICC + IGEBC shared Odoo instance" \\
    --odoo-version 19 \\
    --edition online \\
    --companies BICC IGEBC
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
ROOT = THIS_DIR.parent
TEMPLATE = ROOT / "templates" / "instance-repo-template"


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(cmd)}" + (f"   (cwd={cwd})" if cwd else ""))
    return subprocess.run(cmd, cwd=cwd, check=check)


def substitute(file: Path, mapping: dict[str, str]) -> None:
    text = file.read_text()
    for k, v in mapping.items():
        text = text.replace("{{" + k + "}}", v)
    file.write_text(text)


def copy_template(dest: Path, mapping: dict[str, str]) -> None:
    if not TEMPLATE.is_dir():
        sys.exit(f"template not found: {TEMPLATE}")
    for src in TEMPLATE.rglob("*"):
        rel = src.relative_to(TEMPLATE)
        target = dest / rel
        if src.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
    for f in (dest / "config" / "instance.yaml", dest / "README.md"):
        if f.is_file():
            substitute(f, mapping)


def create_environments(owner: str, repo: str) -> None:
    for env in ("dev", "staging", "production"):
        try:
            # -F sends typed values (integer here); -f would send a string and fail.
            run(["gh", "api", "--method", "PUT",
                 f"repos/{owner}/{repo}/environments/{env}",
                 "-F", "wait_timer=0"])
            print(f"  ✓ environment '{env}' created")
        except subprocess.CalledProcessError as exc:
            print(f"  ✗ failed to create env '{env}': {exc}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--owner", required=True, help="GitHub owner / org")
    p.add_argument("--repo", required=True, help="Repository name (must already exist as empty repo)")
    p.add_argument("--instance-name", required=True, help="Short slug, e.g. bicc-igebc")
    p.add_argument("--description", required=True)
    p.add_argument("--odoo-version", required=True, help="e.g. 19")
    p.add_argument("--edition", default="online")
    p.add_argument("--companies", nargs="+", default=["unknown"])
    p.add_argument("--workdir", help="Optional dir to clone into (default: temp)")
    p.add_argument("--skip-environments", action="store_true",
                   help="Don't create gh environments — print instructions instead")
    args = p.parse_args()

    workdir = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="bootstrap-"))
    workdir.mkdir(parents=True, exist_ok=True)
    target = workdir / args.repo

    print(f"Cloning https://github.com/{args.owner}/{args.repo}.git into {target} ...")
    run(["git", "clone", f"https://github.com/{args.owner}/{args.repo}.git", str(target)])

    if any(target.iterdir()) and not (target / ".git").is_dir():
        sys.exit(f"target {target} is not empty and not a git repo — aborting")
    has_files = any(p for p in target.iterdir() if p.name != ".git")
    if has_files:
        print(f"WARNING: {target} already has content — bootstrap will overlay the template.")
        ans = input("Continue? [y/N] ").strip().lower()
        if ans != "y":
            return 1

    mapping = {
        "INSTANCE_NAME": args.instance_name,
        "INSTANCE_DESCRIPTION": args.description,
        "ODOO_VERSION": str(args.odoo_version),
        "ODOO_EDITION": args.edition,
        "COMPANY_1": args.companies[0],
    }
    copy_template(target, mapping)
    if len(args.companies) > 1:
        # Insert remaining company entries directly after the {{COMPANY_1}} line
        # (which has already been substituted to args.companies[0]).
        cfg = target / "config" / "instance.yaml"
        lines = cfg.read_text().splitlines(keepends=True)
        first_line = f'  - "{args.companies[0]}"\n'
        out: list[str] = []
        inserted = False
        for line in lines:
            out.append(line)
            if not inserted and line == first_line:
                for c in args.companies[1:]:
                    out.append(f'  - "{c}"\n')
                inserted = True
        if not inserted:
            print(f"WARNING: could not locate '{first_line.strip()}' in instance.yaml — additional companies not inserted")
        cfg.write_text("".join(out))

    print("Committing and pushing ...")
    run(["git", "add", "."], cwd=target)
    run(["git", "commit", "-m", f"bootstrap: {args.instance_name} from template"], cwd=target)
    run(["git", "branch", "-M", "main"], cwd=target)
    run(["git", "push", "-u", "origin", "main"], cwd=target)

    if not args.skip_environments:
        print("Creating GitHub environments ...")
        create_environments(args.owner, args.repo)

    print(f"""

Bootstrap complete: {target}

Next steps (manual):

1. Add Odoo credentials to each GitHub environment. For each of dev, staging, production:

     gh secret set ODOO_URL      --env <env> --repo {args.owner}/{args.repo}
     gh secret set ODOO_DB       --env <env> --repo {args.owner}/{args.repo}
     gh secret set ODOO_USERNAME --env <env> --repo {args.owner}/{args.repo}
     gh secret set ODOO_PASSWORD --env <env> --repo {args.owner}/{args.repo}

2. Add required reviewers on staging and production environments at:

     https://github.com/{args.owner}/{args.repo}/settings/environments

3. Run the baseline export workflow against production to capture current state:

     gh workflow run export-baseline.yml --repo {args.owner}/{args.repo} -f env=production

4. Author your first changeset under changesets/, push to an ai/* branch, and watch validate.yml run.
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
