"""create_field — create or update a custom ir.model.fields record.

Critical: custom fields created at runtime via XML-RPC must have
`state='manual'`. Without it, Odoo's registry treats the row as a
module-owned field, the field is not loaded into the ORM after the
next module-update reload, and Odoo SaaS's housekeeping may delete it.

Manual fields must also use the `x_` (or `x_studio_`) name prefix —
Odoo enforces this server-side; we enforce locally too so failures
surface in `validate` instead of mid-deploy.
"""
from __future__ import annotations

from .. import Paths, die
from ..odoo_client import call
from ._common import resolve_xml_id_to_res_id, upsert_by_xml_id, values_match


def _build_values(ctx: dict, op: dict) -> dict:
    if not op.get("model") or not op.get("name") or not op.get("field_type"):
        die("create_field requires 'model', 'name', 'field_type'")

    name = op["name"]
    if not (name.startswith("x_") or name.startswith("x_studio_")):
        die(f"create_field: name '{name}' must start with 'x_' "
            f"(Odoo requires the prefix for manual fields)")

    model_recs = call(ctx, "ir.model", "search_read",
                      [[("model", "=", op["model"])]],
                      {"fields": ["id"], "limit": 1})
    if not model_recs:
        die(f"model '{op['model']}' not found")
    vals = {
        "model_id": model_recs[0]["id"],
        "name": name,
        "ttype": op["field_type"],
        "field_description": op.get("label") or name,
        "state": "manual",  # required for runtime-created fields
    }
    if op.get("required") is not None:
        vals["required"] = bool(op["required"])
    if op.get("help"):
        vals["help"] = op["help"]
    if op.get("copied") is not None:
        vals["copied"] = bool(op["copied"])
    if op.get("translate") is not None:
        vals["translate"] = bool(op["translate"])
    if op.get("index") is not None:
        vals["index"] = bool(op["index"])
    if op.get("store") is not None:
        vals["store"] = bool(op["store"])
    if op.get("selection"):
        # Odoo expects a Python literal of tuples like "[('a','A'),('b','B')]".
        # `repr` of a list of lists also parses, but Odoo rewrites it on read
        # so the string round-trip differs and idempotency check fails.
        # Build the canonical tuple form.
        pairs = ", ".join(f"({k!r}, {v!r})" for k, v in op["selection"])
        vals["selection"] = f"[{pairs}]"
    if op.get("relation"):
        vals["relation"] = op["relation"]
    if op.get("relation_field"):
        vals["relation_field"] = op["relation_field"]
    if op.get("ondelete"):
        vals["on_delete"] = op["ondelete"]  # 'set null' | 'cascade' | 'restrict'
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
