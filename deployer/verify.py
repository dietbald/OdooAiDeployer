"""Read-only state check — does the env match the changeset's intended state?

Used by AI against pre-prod / production (where it has no write access) and
by GitHub Actions post-deploy as a sanity gate before marking success.
"""
from __future__ import annotations

import yaml

from . import Paths, die
from .deploy import load_manifest
from .handlers import DISPATCH
from .odoo_client import connect


def cmd_verify(paths: Paths, env_name: str, changeset_id: str) -> int:
    cdir, manifest = load_manifest(paths, changeset_id)
    print(f"[verify] env={env_name}")
    ctx = connect(expected_env_name=env_name)
    print(f"[verify] authenticated uid={ctx['uid']} db={ctx['db']}")

    checks_run = 0
    all_match = True
    for i, op in enumerate(manifest["operations"]):
        op_type = op.get("type")
        if op_type not in DISPATCH:
            die(f"operation {i}: unknown type '{op_type}'")
        handler = DISPATCH[op_type]
        if not hasattr(handler, "verify"):
            print(f"[verify] op {i} ({op_type}): no verify(), skipping")
            continue
        result = handler.verify(ctx, op, paths=paths, changeset_id=changeset_id)
        checks_run += 1
        flag = "MATCH" if result.get("matches") else "DIFF"
        all_match = all_match and bool(result.get("matches"))
        print(f"[verify] op {i} ({op_type}) target={result.get('target','?')}: {flag}")

    # Fail-closed: a verify that checked nothing is not a green verify.
    if checks_run == 0:
        die(f"verify checked 0 operations (no handler exposes verify()) — "
            f"refusing to report success for zero checks. Add verify() to the "
            f"handlers in this changeset, or treat this as a structural error.")

    return 0 if all_match else 2
