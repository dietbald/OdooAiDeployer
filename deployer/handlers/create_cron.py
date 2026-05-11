"""create_cron — create or update an ir.cron scheduled action."""
from __future__ import annotations

from .. import Paths, die, load_file_text
from ..odoo_client import call
from ._common import (
    resolve_xml_id_to_res_id, rollback_upsert, upsert_by_xml_id, values_match,
)


def _build_values(ctx: dict, op: dict, paths: Paths, changeset_id: str) -> dict:
    if not op.get("model") or not op.get("code_file"):
        die("create_cron requires 'model' and 'code_file'")
    model_recs = call(ctx, "ir.model", "search_read",
                      [[("model", "=", op["model"])]],
                      {"fields": ["id"], "limit": 1})
    if not model_recs:
        die(f"model '{op['model']}' not found")
    vals = {
        "name": op.get("name") or op["xml_id"],
        "model_id": model_recs[0]["id"],
        "state": "code",
        "code": load_file_text(paths.changeset_dir(changeset_id), op["code_file"]),
        "interval_number": int(op.get("interval_number", 1)),
        "interval_type": op.get("interval_type", "hours"),
        "active": bool(op.get("active", True)),
    }
    if op.get("user_id_xml_id"):
        rid = resolve_xml_id_to_res_id(ctx, op["user_id_xml_id"], "res.users")
        if rid:
            vals["user_id"] = rid
    return vals


def apply(ctx, op, *, paths: Paths, env_name, changeset_id, op_index, dry_run=False):
    if not op.get("xml_id"):
        die("create_cron requires 'xml_id'")
    values = _build_values(ctx, op, paths, changeset_id)
    if dry_run:
        return {"type": "create_cron", "target": f"xml_id:{op['xml_id']}",
                "status": "would-upsert"}
    rec_id, action, backup_path = upsert_by_xml_id(
        ctx, "ir.cron", op["xml_id"], values,
        backup_ctx=(paths, env_name, changeset_id, op_index),
    )
    result = {"type": "create_cron", "target": f"ir.cron:{rec_id}",
              "xml_id": op["xml_id"], "status": action}
    if backup_path:
        result["rollback_snapshot"] = str(backup_path.relative_to(paths.instance_root))
    return result


def verify(ctx, op, *, paths: Paths, changeset_id):
    values = _build_values(ctx, op, paths, changeset_id)
    rec_id = resolve_xml_id_to_res_id(ctx, op["xml_id"], "ir.cron")
    if not rec_id:
        return {"type": "create_cron", "xml_id": op["xml_id"],
                "matches": False, "reason": "not found"}
    current = call(ctx, "ir.cron", "read", [[rec_id]],
                   {"fields": list(values.keys())})[0]
    return {"type": "create_cron", "target": f"ir.cron:{rec_id}",
            "matches": values_match(current, values)}


def rollback(ctx, op_record, *, paths: Paths, env_name: str, dry_run: bool = False):
    out = rollback_upsert(ctx, op_record, paths=paths, dry_run=dry_run)
    out["type"] = "create_cron"
    return out
