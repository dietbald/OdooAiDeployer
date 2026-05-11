"""update_record — generic update of an existing record by xml_id.

Same MODEL_WHITELIST as create_record. Differs in that the record MUST
already exist. Backs up the prior values to rollback_snapshots before write.
"""
from __future__ import annotations

import json

from .. import Paths, die
from ..audit import backup_record
from ..odoo_client import call
from ._common import _read_for_backup, resolve_xml_id_to_res_id, values_match
from .create_record import _check_allowed


def apply(ctx, op, *, paths: Paths, env_name, changeset_id, op_index, dry_run=False):
    if not op.get("xml_id") or not op.get("model"):
        die("update_record requires 'xml_id' and 'model'")
    _check_allowed(op, op["model"])
    values = op.get("values") or {}
    rec_id = resolve_xml_id_to_res_id(ctx, op["xml_id"], op["model"])
    if not rec_id:
        die(f"update_record: no existing record for xml_id={op['xml_id']} on {op['model']}")

    current = call(ctx, op["model"], "read", [[rec_id]],
                   {"fields": list(values.keys())})[0]
    if values_match(current, values):
        return {"type": "update_record", "target": f"{op['model']}:{rec_id}",
                "status": "skipped", "reason": "values already match"}

    if dry_run:
        return {"type": "update_record", "target": f"{op['model']}:{rec_id}",
                "status": "would-update", "fields": list(values.keys())}

    # Snapshot in write-side shape (m2o → id, m2m → [(6,0,[ids])]) so
    # rollback can pass it straight back into write() with no translation.
    prior = _read_for_backup(ctx, op["model"], rec_id, list(values.keys()))
    backup_path = backup_record(paths, env_name, changeset_id, op_index,
                                op["model"], rec_id, prior, ext="json")
    call(ctx, op["model"], "write", [[rec_id], values])
    return {"type": "update_record", "target": f"{op['model']}:{rec_id}",
            "xml_id": op["xml_id"], "status": "applied",
            "rollback_snapshot": str(backup_path.relative_to(paths.instance_root))}


def verify(ctx, op, *, paths: Paths, changeset_id):
    _check_allowed(op, op["model"])
    values = op.get("values") or {}
    rec_id = resolve_xml_id_to_res_id(ctx, op["xml_id"], op["model"])
    if not rec_id:
        return {"type": "update_record", "xml_id": op["xml_id"],
                "matches": False, "reason": "not found"}
    current = call(ctx, op["model"], "read", [[rec_id]],
                   {"fields": list(values.keys())})[0]
    return {"type": "update_record", "target": f"{op['model']}:{rec_id}",
            "matches": values_match(current, values)}


def rollback(ctx, op_record, *, paths: Paths, env_name: str, dry_run: bool = False):
    target = op_record.get("target", "")
    if not target or ":" not in target or target.startswith("xml_id:"):
        return {"type": "update_record", "target": target, "status": "skipped",
                "reason": "no concrete target"}
    model, _, rec_id_s = target.partition(":")
    rec_id = int(rec_id_s)

    if op_record.get("status") == "skipped":
        return {"type": "update_record", "target": target, "status": "skipped",
                "reason": "op was a no-op; nothing to undo"}

    snap_rel = op_record.get("rollback_snapshot")
    if not snap_rel:
        return {"type": "update_record", "target": target,
                "status": "manual-required", "reason": "no snapshot recorded"}
    snap_path = paths.instance_root / snap_rel
    if not snap_path.is_file():
        return {"type": "update_record", "target": target,
                "status": "manual-required",
                "reason": f"snapshot file missing: {snap_rel}"}
    prior = json.loads(snap_path.read_text())

    if dry_run:
        return {"type": "update_record", "target": target,
                "status": "would-restore", "from": snap_rel,
                "fields": list(prior.keys())}

    call(ctx, model, "write", [[rec_id], prior])
    return {"type": "update_record", "target": target,
            "status": "restored", "from": snap_rel}
