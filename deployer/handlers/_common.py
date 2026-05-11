"""Shared helpers used by typed handlers — kept private to the handlers package."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .. import Paths, die
from ..audit import backup_record
from ..odoo_client import call


def sha256_of(text: str) -> str:
    """Stable hex sha256 of a string. Used by handlers to record canonical
    state shas in audit operations (before/after) for env-alignment checks."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


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


def _read_for_backup(ctx: dict, model: str, rec_id: int, fields: list[str]) -> dict:
    """Read current values for the keys we're about to write, to snapshot
    them. m2o/m2m readbacks are normalized into write-side shapes so the
    snapshot can be passed straight back into `write()` on rollback."""
    raw = call(ctx, model, "read", [[rec_id]], {"fields": fields})[0]
    out: dict = {}
    for k in fields:
        v = raw.get(k)
        if isinstance(v, list) and len(v) == 2 and isinstance(v[1], str) \
                and isinstance(v[0], int):
            # m2o read: [id, name] → just id (write-side accepts int)
            out[k] = v[0]
        elif isinstance(v, list) and v and all(isinstance(x, int) for x in v):
            # m2m read: [id, id, ...] → set-exactly command
            out[k] = [(6, 0, list(v))]
        elif isinstance(v, list) and not v:
            # Empty m2m → set-to-empty command
            out[k] = [(6, 0, [])]
        else:
            out[k] = v
    return out


def upsert_by_xml_id(ctx: dict, model: str, xml_id: str,
                     values: dict, *,
                     backup_ctx: tuple | None = None) -> tuple[int, str, Path | None]:
    """Create or update a record keyed by xml_id.

    Returns (record_id, action, backup_path). action ∈ {created, updated, skipped}.
    backup_path is set only when action == 'updated' and a backup_ctx was passed.

    `backup_ctx` is `(paths, env_name, changeset_id, op_index)` — passed by
    handlers that want a rollback snapshot of the prior record state. The
    snapshot is JSON of the keys we're about to overwrite, in write-side
    shape (m2m become `[(6, 0, [ids])]` so rollback can pass them straight
    back into `write()`).
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
            return rec_id, "skipped", None
        backup_path: Path | None = None
        if backup_ctx is not None:
            paths, env_name, changeset_id, op_index = backup_ctx
            prior = _read_for_backup(ctx, model, rec_id, list(values.keys()))
            backup_path = backup_record(
                paths, env_name, changeset_id, op_index,
                model, rec_id, prior, ext="json",
            )
        call(ctx, model, "write", [[rec_id], values])
        return rec_id, "updated", backup_path
    rec_id = call(ctx, model, "create", [values])
    call(ctx, "ir.model.data", "create", [{
        "module": module, "name": name, "model": model, "res_id": rec_id,
        "noupdate": False,
    }])
    return rec_id, "created", None


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


def _parse_target(target: str) -> tuple[str, int] | None:
    """Parse 'model.name:rec_id' into (model, rec_id). None if unparseable
    (e.g. 'xml_id:foo.bar' from a dry-run record — nothing to roll back)."""
    if not target or ":" not in target or target.startswith("xml_id:"):
        return None
    model, _, rec_id_s = target.partition(":")
    try:
        return model, int(rec_id_s)
    except ValueError:
        return None


def _delete_xml_id(ctx: dict, model: str, rec_id: int) -> None:
    """Remove the ir.model.data row for a (model, rec_id). Odoo's unlink
    cascades this for most models, but defensive cleanup keeps the xml_id
    re-usable for re-creation in case the cascade ever changes."""
    rows = call(ctx, "ir.model.data", "search",
                [[("model", "=", model), ("res_id", "=", rec_id)]])
    if rows:
        call(ctx, "ir.model.data", "unlink", [rows])


def rollback_upsert(ctx: dict, op_record: dict, *,
                    paths: Paths, dry_run: bool = False) -> dict:
    """Generic rollback for handlers that use upsert_by_xml_id.

    Status semantics:
      created  → unlink the record (and its ir.model.data row)
      updated  → restore prior values from JSON snapshot
      skipped  → no-op
      anything else (e.g. would-upsert from dry-run audits) → manual-required
    """
    target = op_record.get("target", "")
    parsed = _parse_target(target)
    if not parsed:
        return {"target": target, "status": "skipped",
                "reason": "no concrete target (dry-run or pre-snapshot audit)"}
    model, rec_id = parsed

    status = op_record.get("status")

    if status == "skipped":
        return {"target": target, "status": "skipped",
                "reason": "op was a no-op; nothing to undo"}

    if status == "created":
        if dry_run:
            return {"target": target, "status": "would-unlink"}
        call(ctx, model, "unlink", [[rec_id]])
        _delete_xml_id(ctx, model, rec_id)
        return {"target": target, "status": "unlinked"}

    if status == "updated":
        snap_rel = op_record.get("rollback_snapshot")
        if not snap_rel:
            return {"target": target, "status": "manual-required",
                    "reason": "op recorded status=updated but no snapshot path"}
        snap_path = paths.instance_root / snap_rel
        if not snap_path.is_file():
            return {"target": target, "status": "manual-required",
                    "reason": f"snapshot file missing: {snap_rel}"}
        try:
            prior = json.loads(snap_path.read_text())
        except json.JSONDecodeError as exc:
            return {"target": target, "status": "manual-required",
                    "reason": f"snapshot is not JSON: {exc}"}
        if dry_run:
            return {"target": target, "status": "would-restore",
                    "from": snap_rel, "fields": list(prior.keys())}
        call(ctx, model, "write", [[rec_id], prior])
        return {"target": target, "status": "restored", "from": snap_rel}

    return {"target": target, "status": "manual-required",
            "reason": f"unhandled status {status!r}"}
