"""create_record — generic xml_id-keyed upsert.

Per the architecture doc this operation is RESTRICTED:
  1. The manifest must opt in with `allow_generic_records: true`.
  2. The target model must be in MODEL_WHITELIST below.

Use a typed handler whenever one exists. Generic operations are an escape
hatch only, intended for ir.actions.act_window / mail.template / ir.filters
where dedicated handlers haven't been written yet.
"""
from __future__ import annotations

from .. import Paths, die
from ..odoo_client import call
from ._common import resolve_xml_id_to_res_id, upsert_by_xml_id, values_match

MODEL_WHITELIST = {
    "ir.actions.act_window",
    "ir.actions.act_url",
    "ir.actions.report",
    "ir.filters",
    "ir.sequence",
    "mail.template",
    "mail.activity.type",
    "product.category",
    "uom.uom",
    "uom.category",
    "account.tax",
    "account.account.tag",
    "account.journal",
}


def _check_allowed(op: dict, model: str) -> None:
    if model not in MODEL_WHITELIST:
        die(
            f"create_record: model '{model}' not in whitelist.\n"
            f"If a typed handler exists for this model, use it instead.\n"
            f"Otherwise: add the model to handlers/create_record.py:MODEL_WHITELIST.\n"
            f"Currently allowed: {sorted(MODEL_WHITELIST)}"
        )


def apply(ctx, op, *, paths: Paths, env_name, changeset_id, op_index, dry_run=False):
    if not op.get("xml_id") or not op.get("model"):
        die("create_record requires 'xml_id' and 'model'")
    _check_allowed(op, op["model"])
    values = op.get("values") or {}
    if not isinstance(values, dict):
        die("create_record 'values' must be a mapping")
    if dry_run:
        return {"type": "create_record", "target": f"xml_id:{op['xml_id']}",
                "status": "would-upsert", "fields": list(values.keys())}
    rec_id, action = upsert_by_xml_id(ctx, op["model"], op["xml_id"], values)
    return {"type": "create_record", "target": f"{op['model']}:{rec_id}",
            "xml_id": op["xml_id"], "status": action}


def verify(ctx, op, *, paths: Paths, changeset_id):
    _check_allowed(op, op["model"])
    values = op.get("values") or {}
    rec_id = resolve_xml_id_to_res_id(ctx, op["xml_id"], op["model"])
    if not rec_id:
        return {"type": "create_record", "xml_id": op["xml_id"],
                "matches": False, "reason": "not found"}
    if not values:
        return {"type": "create_record", "target": f"{op['model']}:{rec_id}",
                "matches": True}
    current = call(ctx, op["model"], "read", [[rec_id]],
                   {"fields": list(values.keys())})[0]
    return {"type": "create_record", "target": f"{op['model']}:{rec_id}",
            "matches": values_match(current, values)}
