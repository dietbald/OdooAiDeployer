"""create_menu — create or update an ir.ui.menu entry."""
from __future__ import annotations

from .. import Paths, die
from ..odoo_client import call
from ._common import resolve_xml_id_to_res_id, upsert_by_xml_id, values_match


def _build_values(ctx: dict, op: dict) -> dict:
    vals = {"name": op.get("name") or op["xml_id"]}
    if op.get("sequence") is not None:
        vals["sequence"] = int(op["sequence"])
    if op.get("parent_xml_id"):
        rid = resolve_xml_id_to_res_id(ctx, op["parent_xml_id"], "ir.ui.menu")
        if rid:
            vals["parent_id"] = rid
    if op.get("action_xml_id"):
        m, n = op["action_xml_id"].split(".", 1)
        recs = call(ctx, "ir.model.data", "search_read",
                    [[("module", "=", m), ("name", "=", n)]],
                    {"fields": ["res_id", "model"], "limit": 1})
        if recs:
            vals["action"] = f"{recs[0]['model']},{recs[0]['res_id']}"
    return vals


def apply(ctx, op, *, paths: Paths, env_name, changeset_id, op_index, dry_run=False):
    if not op.get("xml_id"):
        die("create_menu requires 'xml_id'")
    values = _build_values(ctx, op)
    if dry_run:
        return {"type": "create_menu", "target": f"xml_id:{op['xml_id']}",
                "status": "would-upsert"}
    rec_id, action = upsert_by_xml_id(ctx, "ir.ui.menu", op["xml_id"], values)
    return {"type": "create_menu", "target": f"ir.ui.menu:{rec_id}",
            "xml_id": op["xml_id"], "status": action}


def verify(ctx, op, *, paths: Paths, changeset_id):
    values = _build_values(ctx, op)
    rec_id = resolve_xml_id_to_res_id(ctx, op["xml_id"], "ir.ui.menu")
    if not rec_id:
        return {"type": "create_menu", "xml_id": op["xml_id"],
                "matches": False, "reason": "not found"}
    fields = [k for k in values.keys() if k != "action"]
    current = call(ctx, "ir.ui.menu", "read", [[rec_id]],
                   {"fields": fields})[0] if fields else {}
    cmp_target = {k: v for k, v in values.items() if k != "action"}
    return {"type": "create_menu", "target": f"ir.ui.menu:{rec_id}",
            "matches": values_match(current, cmp_target)}
