"""update_record — generic update of an existing record by xml_id.

Same MODEL_WHITELIST as create_record. Differs in that the record MUST
already exist. Backs up the prior values to rollback_snapshots before write.
"""
from __future__ import annotations

from .. import Paths, die
from ..audit import backup_record
from ..odoo_client import call
from ._common import resolve_xml_id_to_res_id, values_match
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

    backup_path = backup_record(paths, env_name, changeset_id, op_index,
                                op["model"], rec_id, current, ext="json")
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
