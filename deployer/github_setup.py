"""GitHub bootstrap helpers — used by scripts/bootstrap_instance_repo.py.

V1 scope: thin wrappers around `gh` CLI for repo creation, environment
creation, and printing manual-setup instructions for secrets (since we
don't want to prompt for passwords inside automation).
"""
from __future__ import annotations

import subprocess


def gh_run(args: list[str], check: bool = True) -> str:
    out = subprocess.run(["gh"] + args, check=check, capture_output=True, text=True)
    return out.stdout.strip()


def repo_exists(owner: str, name: str) -> bool:
    try:
        gh_run(["repo", "view", f"{owner}/{name}", "--json", "name"])
        return True
    except subprocess.CalledProcessError:
        return False


def create_repo(owner: str, name: str, private: bool = True) -> None:
    visibility = "--private" if private else "--public"
    gh_run(["repo", "create", f"{owner}/{name}", visibility, "--confirm"])


def create_environment(owner: str, name: str, env_name: str) -> None:
    """Create a deployment environment via the GitHub REST API.

    Note: setting required_reviewers needs the user/team id which `gh api`
    can resolve. For simplicity v1 prints manual instructions for reviewers.
    """
    gh_run([
        "api", "--method", "PUT",
        f"repos/{owner}/{name}/environments/{env_name}",
        "-f", "wait_timer=0",
    ])


def print_secret_setup_instructions(owner: str, name: str) -> None:
    print(f"""
Manual step required: add Odoo credentials to the three GitHub environments.

For each environment (dev, staging, production), run:

    gh secret set ODOO_URL      --env dev      --repo {owner}/{name}
    gh secret set ODOO_DB       --env dev      --repo {owner}/{name}
    gh secret set ODOO_USERNAME --env dev      --repo {owner}/{name}
    gh secret set ODOO_PASSWORD --env dev      --repo {owner}/{name}

(Repeat for `staging` and `production`.)

For staging and production, add required reviewers via:

    https://github.com/{owner}/{name}/settings/environments
""")
