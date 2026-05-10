"""create_automated_action — base.automation record."""
from __future__ import annotations

from .. import Paths, die, load_file_text
from ..odoo_client import call
from ._common import resolve_xml_id_to_res_id, upsert_by_xml_id, values_match


def _build_values(ctx: dict, op: dict, paths: Paths, changeset_id: str) -> dict:
    if not op.get("model") or not op.get("trigger"):
        die("create_automated_action requires 'model' and 'trigger'")
    model_recs = call(ctx, "ir.model", "search_read",
                      [[("model", "=", op["model"])]],
                      {"fields": ["id"], "limit": 1})
    if not model_recs:
        die(f"model '{op['model']}' not found")
    vals = {
        "name": op.get("name") or op["xml_id"],
        "model_id": model_recs[0]["id"],
        "trigger": op["trigger"],
        "state": op.get("state", "code"),
    }
    if vals["state"] == "code":
        if not op.get("code_file"):
            die("create_automated_action with state='code' requires 'code_file'")
        vals["code"] = load_file_text(paths.changeset_dir(changeset_id),
                                      op["code_file"])
    if op.get("filter_domain"):
        vals["filter_domain"] = op["filter_domain"]
    return vals


def apply(ctx, op, *, paths: Paths, env_name, changeset_id, op_index, dry_run=False):
    if not op.get("xml_id"):
        die("create_automated_action requires 'xml_id'")
    values = _build_values(ctx, op, paths, changeset_id)
    if dry_run:
        return {"type": "create_automated_action", "target": f"xml_id:{op['xml_id']}",
                "status": "would-upsert"}
    rec_id, action = upsert_by_xml_id(ctx, "base.automation", op["xml_id"], values)
    return {"type": "create_automated_action", "target": f"base.automation:{rec_id}",
            "xml_id": op["xml_id"], "status": action}


def verify(ctx, op, *, paths: Paths, changeset_id):
    values = _build_values(ctx, op, paths, changeset_id)
    rec_id = resolve_xml_id_to_res_id(ctx, op["xml_id"], "base.automation")
    if not rec_id:
        return {"type": "create_automated_action", "xml_id": op["xml_id"],
                "matches": False, "reason": "not found"}
    current = call(ctx, "base.automation", "read", [[rec_id]],
                   {"fields": list(values.keys())})[0]
    return {"type": "create_automated_action", "target": f"base.automation:{rec_id}",
            "matches": values_match(current, values)}
