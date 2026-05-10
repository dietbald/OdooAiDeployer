"""create_field — create or update a custom ir.model.fields record."""
from __future__ import annotations

from .. import Paths, die
from ..odoo_client import call
from ._common import resolve_xml_id_to_res_id, upsert_by_xml_id, values_match


def _build_values(ctx: dict, op: dict) -> dict:
    if not op.get("model") or not op.get("name") or not op.get("field_type"):
        die("create_field requires 'model', 'name', 'field_type'")
    model_recs = call(ctx, "ir.model", "search_read",
                      [[("model", "=", op["model"])]],
                      {"fields": ["id"], "limit": 1})
    if not model_recs:
        die(f"model '{op['model']}' not found")
    vals = {
        "model_id": model_recs[0]["id"],
        "name": op["name"],
        "ttype": op["field_type"],
        "field_description": op.get("label") or op["name"],
    }
    if op.get("required") is not None:
        vals["required"] = bool(op["required"])
    if op.get("help"):
        vals["help"] = op["help"]
    if op.get("selection"):
        vals["selection"] = repr(op["selection"])
    if op.get("relation"):
        vals["relation"] = op["relation"]
    if op.get("relation_field"):
        vals["relation_field"] = op["relation_field"]
    return vals


def apply(ctx, op, *, paths: Paths, env_name, changeset_id, op_index, dry_run=False):
    if not op.get("xml_id"):
        die("create_field requires 'xml_id'")
    values = _build_values(ctx, op)
    if dry_run:
        return {"type": "create_field", "target": f"xml_id:{op['xml_id']}",
                "status": "would-upsert", "fields": list(values.keys())}
    rec_id, action = upsert_by_xml_id(ctx, "ir.model.fields", op["xml_id"], values)
    return {"type": "create_field", "target": f"ir.model.fields:{rec_id}",
            "xml_id": op["xml_id"], "status": action}


def verify(ctx, op, *, paths: Paths, changeset_id):
    values = _build_values(ctx, op)
    rec_id = resolve_xml_id_to_res_id(ctx, op["xml_id"], "ir.model.fields")
    if not rec_id:
        return {"type": "create_field", "xml_id": op["xml_id"],
                "matches": False, "reason": "not found"}
    current = call(ctx, "ir.model.fields", "read", [[rec_id]],
                   {"fields": list(values.keys())})[0]
    return {"type": "create_field", "target": f"ir.model.fields:{rec_id}",
            "matches": values_match(current, values)}
