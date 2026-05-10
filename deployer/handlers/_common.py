"""Shared helpers used by typed handlers — kept private to the handlers package."""
from __future__ import annotations

from .. import die
from ..odoo_client import call


def _normalize_target(v):
    """Normalize a write-side value to its read-side equivalent.

    m2m / o2m commands like `[(6, 0, [ids])]` (set-to-exactly-these) become
    a sorted list of ids — same shape Odoo returns on `read()`.
    """
    if isinstance(v, list) and v and isinstance(v[0], (list, tuple)) \
            and len(v[0]) == 3 and v[0][0] == 6:
        return sorted(v[0][2])
    return v


def _normalize_current(cur):
    """Normalize a read-side value to be comparable with a normalized target.

    Disambiguates m2o (`[id, "Display Name"]`, second element is str) from
    a 2-id m2m (`[id, id]`, second element is int) by inspecting the type of
    the second element — both are 2-element lists otherwise.
    """
    if isinstance(cur, list):
        # m2o read: [id, name_str]
        if len(cur) == 2 and isinstance(cur[1], str) \
                and isinstance(cur[0], int):
            return cur[0]
        # m2m / o2m read: [id, id, ...]
        if cur and all(isinstance(x, int) for x in cur):
            return sorted(cur)
        # Empty list — m2m with no records, returns as-is
        if not cur:
            return cur
    return cur


def values_match(current: dict, target: dict) -> bool:
    """Compare current Odoo record values to a target dict.

    Handles many2one (`[id, name]` → id), many2many command tuples
    (`[(6, 0, [ids])]` → sorted ids vs read-back `[ids]`), and falls back
    to plain equality for scalars. Translatable fields and HTML fields
    that Odoo round-trip-normalizes are still false-negative-prone — those
    are addressed at the handler level (see `update_view._canonicalize_xml`).
    """
    for k, v in target.items():
        cur = _normalize_current(current.get(k))
        v_norm = _normalize_target(v)
        if cur != v_norm:
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
