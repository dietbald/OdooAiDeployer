"""Operation-level rollback.

Reads the audit file for a deployed changeset, walks operations in reverse,
and dispatches each one to its handler's `rollback()` function. Each handler
implements the inverse of its own `apply()`:

    update_view             — write arch_db back from the XML snapshot
    update_record           — write prior values back from the JSON snapshot
    create_view, create_field, create_menu, create_record, create_cron,
    create_server_action    — unlink (status=created) or restore values
                              (status=updated) via _common.rollback_upsert
    create_automated_action — composite: rollback base.automation, then the
                              sibling ir.actions.server (reverse of apply order)

If a handler doesn't expose `rollback()` the op is logged as 'manual-required'
and the workflow proceeds with the next op.

Production rollback always requires manual approval (enforced at the
GitHub Actions workflow level via environment protection rules).
"""
from __future__ import annotations

import json

from . import Paths, die, now_iso
from .audit import audit_read, git_head_sha
from .handlers import DISPATCH
from .odoo_client import connect


def cmd_rollback(paths: Paths, env_name: str, changeset_id: str,
                 *, dry_run: bool = False) -> int:
    audit = audit_read(paths, env_name, changeset_id)
    if not audit:
        die(f"no audit file at audits/{env_name}/{changeset_id}.json — nothing to roll back")

    print(f"[rollback] env={env_name} changeset={changeset_id}"
          f"{' [DRY RUN]' if dry_run else ''}")
    ctx = connect(expected_env_name=env_name)
    print(f"[rollback] authenticated uid={ctx['uid']} db={ctx['db']}")

    operations = audit.get("operations") or []
    rollback_results: list[dict] = []

    for op_record in reversed(operations):
        op_index = op_record.get("op_index")
        op_type = op_record.get("type")
        target = op_record.get("target", "?")
        handler = DISPATCH.get(op_type)
        if handler is None or not hasattr(handler, "rollback"):
            print(f"[rollback] op {op_index} ({op_type}) target={target}: "
                  f"handler has no rollback() — manual-required")
            rollback_results.append({
                "op_index": op_index, "type": op_type, "target": target,
                "status": "manual-required",
                "reason": f"no rollback() in handler '{op_type}'",
            })
            continue

        try:
            result = handler.rollback(ctx, op_record,
                                      paths=paths, env_name=env_name,
                                      dry_run=dry_run)
        except SystemExit:
            raise
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            print(f"[rollback] op {op_index} ({op_type}) target={target}: "
                  f"FAILED — {err}")
            rollback_results.append({
                "op_index": op_index, "type": op_type, "target": target,
                "status": "error", "error": err,
            })
            continue

        result.setdefault("op_index", op_index)
        result.setdefault("type", op_type)
        print(f"[rollback] op {op_index} ({op_type}) target={target}: "
              f"{result.get('status')}")
        rollback_results.append(result)

    if dry_run:
        return 0

    rollback_audit = {
        "changeset": changeset_id,
        "environment": env_name,
        "git_commit_sha": git_head_sha(paths.instance_root),
        "kind": "rollback",
        "performed_at": now_iso(),
        "rolled_back_audit": str(paths.audit_file(env_name, changeset_id).relative_to(paths.instance_root)),
        "operations": rollback_results,
    }
    rb_path = paths.audits / env_name / f"{changeset_id}.rollback.json"
    rb_path.parent.mkdir(parents=True, exist_ok=True)
    rb_path.write_text(json.dumps(rollback_audit, indent=2, sort_keys=True) + "\n")
    print(f"[rollback] audit written to {rb_path.relative_to(paths.instance_root)}")

    # Non-zero exit if any op needed manual intervention or errored — CI/TJ
    # should know without scrolling logs.
    if any(r.get("status") in ("manual-required", "error") for r in rollback_results):
        return 2
    return 0
