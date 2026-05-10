"""Shared helpers used by typed handlers — kept private to the handlers package."""
from __future__ import annotations

from .. import die
from ..odoo_client import call


def values_match(current: dict, target: dict) -> bool:
    """Compare current Odoo record values to a target dict.

    Many2one fields come back from Odoo as `[id, name]` tuples — we
    compare just the id.
    """
    for k, v in target.items():
        cur = current.get(k)
        if isinstance(cur, list) and len(cur) == 2:
            cur = cur[0]
        if cur != v:
            return False
    return True


def upsert_by_xml_id(ctx: dict, model: str, xml_id: str,
                     values: dict) -> tuple[int, str]:
    """Create or update a record keyed by xml_id.

    Returns (record_id, action) where action is 'created'|'updated'|'skipped'.
    Idempotency: if the record exists and current values match `values` on
    the keys provided, returns ('skipped').
    """
    if "." not in xml_id:
        die(f"xml_id must be 'module.name' form, got: {xml_id}")
    module, name = xml_id.split(".", 1)
    existing = call(ctx, "ir.model.data", "search_read",
                    [[("module", "=", module), ("name", "=", name),
                      ("model", "=", model)]],
                    {"fields": ["id", "res_id"], "limit": 1})
    if existing:
        rec_id = existing[0]["res_id"]
        current = call(ctx, model, "read", [[rec_id]],
                       {"fields": list(values.keys())})
        if current and values_match(current[0], values):
            return rec_id, "skipped"
        call(ctx, model, "write", [[rec_id], values])
        return rec_id, "updated"
    rec_id = call(ctx, model, "create", [values])
    call(ctx, "ir.model.data", "create", [{
        "module": module, "name": name, "model": model, "res_id": rec_id,
        "noupdate": False,
    }])
    return rec_id, "created"


def resolve_xml_id_to_res_id(ctx: dict, xml_id: str,
                             model: str | None = None) -> int | None:
    """Look up the database id for an xml_id, optionally constrained to a model."""
    m, n = xml_id.split(".", 1)
    domain = [("module", "=", m), ("name", "=", n)]
    if model:
        domain.append(("model", "=", model))
    recs = call(ctx, "ir.model.data", "search_read", [domain],
                {"fields": ["res_id"], "limit": 1})
    return recs[0]["res_id"] if recs else None
