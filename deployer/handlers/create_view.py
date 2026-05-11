"""create_view — create or update an ir.ui.view by xml_id."""
from __future__ import annotations

from .. import Paths, die, load_file_text
from ..odoo_client import call
from ._common import (
    resolve_xml_id_to_res_id, rollback_upsert, upsert_by_xml_id, values_match,
)


def _build_values(ctx: dict, op: dict, paths: Paths, changeset_id: str) -> dict:
    if not op.get("arch_file"):
        die("create_view requires 'arch_file'")
    cdir = paths.changeset_dir(changeset_id)
    vals = {
        "name": op.get("name") or op["xml_id"],
        "type": op.get("type_", "form"),
        "arch_db": load_file_text(cdir, op["arch_file"]),
    }
    if op.get("model"):
        vals["model"] = op["model"]
    if op.get("priority") is not None:
        vals["priority"] = int(op["priority"])
    if op.get("inherit_id"):
        rid = resolve_xml_id_to_res_id(ctx, op["inherit_id"], "ir.ui.view")
        if rid:
            vals["inherit_id"] = rid
    return vals


def apply(ctx, op, *, paths: Paths, env_name, changeset_id, op_index, dry_run=False):
    if not op.get("xml_id"):
        die("create_view requires 'xml_id'")
    values = _build_values(ctx, op, paths, changeset_id)
    if dry_run:
        return {"type": "create_view", "target": f"xml_id:{op['xml_id']}",
                "status": "would-upsert", "fields": list(values.keys())}
    rec_id, action, backup_path = upsert_by_xml_id(
        ctx, "ir.ui.view", op["xml_id"], values,
        backup_ctx=(paths, env_name, changeset_id, op_index),
    )
    result = {"type": "create_view", "target": f"ir.ui.view:{rec_id}",
              "xml_id": op["xml_id"], "status": action}
    if backup_path:
        result["rollback_snapshot"] = str(backup_path.relative_to(paths.instance_root))
    return result


def verify(ctx, op, *, paths: Paths, changeset_id):
    values = _build_values(ctx, op, paths, changeset_id)
    rec_id = resolve_xml_id_to_res_id(ctx, op["xml_id"], "ir.ui.view")
    if not rec_id:
        return {"type": "create_view", "xml_id": op["xml_id"],
                "matches": False, "reason": "not found"}
    current = call(ctx, "ir.ui.view", "read", [[rec_id]],
                   {"fields": list(values.keys())})[0]
    return {"type": "create_view", "target": f"ir.ui.view:{rec_id}",
            "matches": values_match(current, values)}


def rollback(ctx, op_record, *, paths: Paths, env_name: str, dry_run: bool = False):
    out = rollback_upsert(ctx, op_record, paths=paths, dry_run=dry_run)
    out["type"] = "create_view"
    return out
