"""create_server_action — create or update an ir.actions.server (state='code').

The Python body lives in a sibling .py file referenced by `code_file`. It is
sent to Odoo as an opaque string — never executed locally. Subject to Odoo
SaaS safe_eval rules (no `import`, no `__import__`, no `exec`, etc.).
"""
from __future__ import annotations

from .. import Paths, die, load_file_text
from ..odoo_client import call
from ._common import (
    resolve_xml_id_to_res_id, rollback_upsert, upsert_by_xml_id, values_match,
)


def _build_values(ctx: dict, op: dict, paths: Paths, changeset_id: str) -> dict:
    if not op.get("model") or not op.get("code_file"):
        die("create_server_action requires 'model' and 'code_file'")
    model_recs = call(ctx, "ir.model", "search_read",
                      [[("model", "=", op["model"])]],
                      {"fields": ["id"], "limit": 1})
    if not model_recs:
        die(f"model '{op['model']}' not found")
    code = load_file_text(paths.changeset_dir(changeset_id), op["code_file"])
    vals = {
        "name": op.get("name") or op["xml_id"],
        "model_id": model_recs[0]["id"],
        "state": "code",
        "code": code,
    }
    if op.get("binding_model"):
        bm = call(ctx, "ir.model", "search_read",
                  [[("model", "=", op["binding_model"])]],
                  {"fields": ["id"], "limit": 1})
        if bm:
            vals["binding_model_id"] = bm[0]["id"]
    if op.get("binding_view_types"):
        vals["binding_view_types"] = op["binding_view_types"]
    return vals


def apply(ctx, op, *, paths: Paths, env_name, changeset_id, op_index, dry_run=False):
    if not op.get("xml_id"):
        die("create_server_action requires 'xml_id'")
    values = _build_values(ctx, op, paths, changeset_id)
    if dry_run:
        return {"type": "create_server_action", "target": f"xml_id:{op['xml_id']}",
                "status": "would-upsert"}
    rec_id, action, backup_path = upsert_by_xml_id(
        ctx, "ir.actions.server", op["xml_id"], values,
        backup_ctx=(paths, env_name, changeset_id, op_index),
    )
    result = {"type": "create_server_action", "target": f"ir.actions.server:{rec_id}",
              "xml_id": op["xml_id"], "status": action}
    if backup_path:
        result["rollback_snapshot"] = str(backup_path.relative_to(paths.instance_root))
    return result


def verify(ctx, op, *, paths: Paths, changeset_id):
    values = _build_values(ctx, op, paths, changeset_id)
    rec_id = resolve_xml_id_to_res_id(ctx, op["xml_id"], "ir.actions.server")
    if not rec_id:
        return {"type": "create_server_action", "xml_id": op["xml_id"],
                "matches": False, "reason": "not found"}
    current = call(ctx, "ir.actions.server", "read", [[rec_id]],
                   {"fields": list(values.keys())})[0]
    return {"type": "create_server_action", "target": f"ir.actions.server:{rec_id}",
            "matches": values_match(current, values)}


def rollback(ctx, op_record, *, paths: Paths, env_name: str, dry_run: bool = False):
    out = rollback_upsert(ctx, op_record, paths=paths, dry_run=dry_run)
    out["type"] = "create_server_action"
    return out
