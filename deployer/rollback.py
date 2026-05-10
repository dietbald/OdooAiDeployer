"""Operation-level rollback.

Reads the audit file for a deployed changeset, walks operations in reverse,
and restores each record's prior state from rollback_snapshots/.

V1: only handles update_view restoration. Other handler types will be added
as they accumulate snapshot files.

Production rollback always requires manual approval (enforced at the
GitHub Actions workflow level via environment protection rules).
"""
from __future__ import annotations

from pathlib import Path

from . import Paths, die, now_iso
from .audit import audit_read, audit_write, git_head_sha
from .odoo_client import call, connect


def cmd_rollback(paths: Paths, env_name: str, changeset_id: str,
                 *, dry_run: bool = False) -> int:
    audit = audit_read(paths, env_name, changeset_id)
    if not audit:
        die(f"no audit file at audits/{env_name}/{changeset_id}.json — nothing to roll back")

    print(f"[rollback] env={env_name} changeset={changeset_id}")
    ctx = connect()
    print(f"[rollback] authenticated uid={ctx['uid']} db={ctx['db']}")

    operations = audit.get("operations") or []
    rollback_results: list[dict] = []

    for op_record in reversed(operations):
        snap_rel = op_record.get("rollback_snapshot")
        if not snap_rel:
            print(f"[rollback] op {op_record.get('op_index')} ({op_record.get('type')}): no snapshot — skipping (likely create-only)")
            continue
        snap_path = paths.instance_root / snap_rel
        if not snap_path.is_file():
            die(f"snapshot file missing: {snap_path}")

        op_type = op_record.get("type")
        target = op_record.get("target", "")
        if not target:
            print(f"[rollback] op {op_record.get('op_index')}: no target recorded — skipping")
            continue
        model, _, rec_id_s = target.partition(":")
        rec_id = int(rec_id_s)

        if dry_run:
            print(f"[rollback] would restore {target} from {snap_rel}")
            rollback_results.append({"target": target, "status": "would-restore",
                                     "from": snap_rel})
            continue

        if op_type == "update_view":
            arch = snap_path.read_text()
            call(ctx, "ir.ui.view", "write", [[rec_id], {"arch_db": arch}])
            print(f"[rollback] restored {target} from {snap_rel}")
            rollback_results.append({"target": target, "status": "restored",
                                     "from": snap_rel})
        else:
            print(f"[rollback] op type '{op_type}' rollback not yet implemented — manual restore needed")
            rollback_results.append({"target": target, "status": "manual-required",
                                     "from": snap_rel})

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
    import json as _json
    rb_path.write_text(_json.dumps(rollback_audit, indent=2, sort_keys=True) + "\n")
    print(f"[rollback] audit written to {rb_path.relative_to(paths.instance_root)}")
    return 0
