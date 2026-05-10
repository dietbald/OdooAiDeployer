"""Odoo XML-RPC connection + call wrapper.

Credentials come from the process environment. In GitHub Actions these are
set from environment secrets by the workflow. For local development, source
an env file (`set -a; source envs/dev.env; set +a`) before running the CLI.

Required env vars:
    ODOO_URL       — e.g. https://bicc-xerxes.odoo.com
    ODOO_DB        — database name
    ODOO_USERNAME  — login email
    ODOO_PASSWORD  — password or API key
    ODOO_ENV_NAME  — 'dev' | 'staging' | 'production' — must match --env

ODOO_ENV_NAME is the env-mismatch sentinel: connect() asserts it equals the
expected env passed by the CLI. This catches the failure mode where a
workflow accidentally exposes the wrong env's secrets (e.g. production
credentials reach a job invoked with --env dev).
"""
from __future__ import annotations

import os
import xmlrpc.client
from typing import Any

from . import die

REQUIRED_ENV = ("ODOO_URL", "ODOO_DB", "ODOO_USERNAME", "ODOO_PASSWORD")


def env_creds() -> dict[str, str]:
    """Read Odoo credentials from os.environ. Exits if any are missing."""
    out = {}
    missing = []
    for k in REQUIRED_ENV:
        v = os.environ.get(k)
        if not v:
            missing.append(k)
        else:
            out[k] = v
    if missing:
        die(
            "missing Odoo credentials in environment: " + ", ".join(missing)
            + "\nIn GitHub Actions these come from environment secrets."
            + "\nLocally: `set -a; source envs/<env>.env; set +a` before running."
        )
    return out


def _assert_env_matches(expected_env_name: str) -> None:
    """Refuse to connect if ODOO_ENV_NAME doesn't match the CLI --env flag."""
    actual = os.environ.get("ODOO_ENV_NAME")
    if actual is None:
        die(
            f"refusing to connect: ODOO_ENV_NAME is not set.\n"
            f"GitHub Actions workflows must set:\n"
            f"  env:\n"
            f"    ODOO_ENV_NAME: {expected_env_name}\n"
            f"so the deployer can prove the loaded secrets match the --env flag.\n"
            f"Locally: `export ODOO_ENV_NAME={expected_env_name}` before running."
        )
    if actual != expected_env_name:
        die(
            f"environment mismatch — refusing to connect.\n"
            f"  --env flag says: {expected_env_name!r}\n"
            f"  ODOO_ENV_NAME says: {actual!r}\n"
            f"This usually means a workflow loaded the wrong env's secrets, or\n"
            f"a local shell sourced the wrong envs/<env>.env file."
        )


def connect(expected_env_name: str | None = None,
            creds: dict[str, str] | None = None) -> dict[str, Any]:
    """Authenticate against Odoo. Returns a context dict for call().

    `expected_env_name`: if set, asserts ODOO_ENV_NAME matches before any
    network call. CLI subcommands always pass this; library callers may
    skip if they know what they're doing.
    """
    if expected_env_name is not None:
        _assert_env_matches(expected_env_name)
    c = creds or env_creds()
    # .strip() each credential — guards against trailing newlines that
    # `echo "value" | gh secret set` (and similar pipelines) silently add.
    # Without this, ODOO_URL ends up as 'https://...com\n' and ServerProxy
    # rejects with 'unsupported XML-RPC protocol'.
    url = c["ODOO_URL"].strip().rstrip("/")
    db = c["ODOO_DB"].strip()
    user = c["ODOO_USERNAME"].strip()
    pw = c["ODOO_PASSWORD"].strip()
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common", allow_none=True)
    try:
        uid = common.authenticate(db, user, pw, {})
    except Exception as exc:
        die(f"Odoo connection failed: {exc}")
    if not uid:
        die("Odoo authentication failed (bad db / user / password)")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object", allow_none=True)
    return {"url": url, "db": db, "uid": uid, "pw": pw, "models": models}


def call(ctx: dict, model: str, method: str, args: list, kwargs: dict | None = None):
    """Thin wrapper around execute_kw. All handler Odoo calls go through here."""
    return ctx["models"].execute_kw(ctx["db"], ctx["uid"], ctx["pw"],
                                    model, method, args, kwargs or {})
