"""Odoo XML-RPC connection + call wrapper.

Credentials come from the process environment. In GitHub Actions these are
set from environment secrets by the workflow. For local development, source
an env file (`set -a; source envs/dev.env; set +a`) before running the CLI.

Required env vars:
    ODOO_URL       — e.g. https://bicc-xerxes.odoo.com
    ODOO_DB        — database name
    ODOO_USERNAME  — login email
    ODOO_PASSWORD  — password or API key
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


def connect(creds: dict[str, str] | None = None) -> dict[str, Any]:
    """Authenticate against Odoo. Returns a context dict for call()."""
    c = creds or env_creds()
    url = c["ODOO_URL"].rstrip("/")
    db = c["ODOO_DB"]
    user = c["ODOO_USERNAME"]
    pw = c["ODOO_PASSWORD"]
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
