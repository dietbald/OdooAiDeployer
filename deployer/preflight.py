"""Odoo-aware pre-deployment validation. Runs against the dev DB.

Catches mistakes that static validation cannot: model doesn't exist, target
field doesn't exist, view key doesn't exist, inherited view missing, user
lacks write access, current state can't be read.

V1: stub. The deploy step itself catches most of these via handler errors;
this module exists so CI can fail-fast with structured output before any
write happens. Will grow as we see real failure modes.
"""
from __future__ import annotations

from . import Paths
from .deploy import load_manifest
from .odoo_client import call, connect


def cmd_preflight(paths: Paths, env_name: str, changeset_id: str) -> int:
    cdir, manifest = load_manifest(paths, changeset_id)
    print(f"[preflight] env={env_name}")
    ctx = connect()
    print(f"[preflight] authenticated uid={ctx['uid']} db={ctx['db']}")

    issues: list[str] = []
    for i, op in enumerate(manifest["operations"]):
        model = op.get("model")
        if model:
            recs = call(ctx, "ir.model", "search_read",
                        [[("model", "=", model)]],
                        {"fields": ["id"], "limit": 1})
            if not recs:
                issues.append(f"op {i}: model '{model}' does not exist on this DB")
        if op.get("type") == "update_view" and op.get("key"):
            ids = call(ctx, "ir.ui.view", "search",
                       [[("key", "=", op["key"])]])
            if not ids:
                issues.append(f"op {i}: no ir.ui.view with key '{op['key']}'")

    if issues:
        for msg in issues:
            print(f"[preflight] FAIL: {msg}")
        return 1
    print(f"[preflight] OK ({len(manifest['operations'])} ops checked)")
    return 0
