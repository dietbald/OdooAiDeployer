"""create_custom_model — register a runtime-defined model on ir.model.

Used to create `x_*` models for instance-specific data structures (e.g.
BICC's x_contract_allowance, x_bir_annual). Odoo enforces two requirements
on runtime-created models that the handler mirrors so failures surface in
`validate` instead of mid-deploy:

  1. Technical name MUST start with `x_` (or `x_studio_`). Without the
     prefix Odoo's registry treats the row as module-defined; it gets
     wiped on the next module-update reload.
  2. `state='manual'` is required. Same reason — without it, Odoo tries
     to load a Python class for the model and crashes (or quietly drops
     the record).

Manifest fields:
    type: create_custom_model
    xml_id: bicc_payroll.x_contract_allowance_model
    model: x_contract_allowance      # the technical name, must start with x_
    name: "Contract Allowance"       # the human-readable label
    info: |                          # optional description shown in the UI
      Tracks per-contract allowance buckets used by salary rules.

After this op succeeds, you can target the new model by name in:
  * create_field (add x_* fields to it)
  * create_record (via the generic whitelist if you add the model name,
    OR through a sibling data/<id>/ CSV import for bulk rows)
  * create_server_action's model: field (server actions on the model)
  * create_view's model: field

ACLs (`ir.model.access`) for the new model are NOT created by this op —
they're a separate manual TJ step via Settings → Technical → Access Rights
(security-sensitive, intentionally out of the deployer's scope).
"""
from __future__ import annotations

from .. import Paths, die
from ..odoo_client import call
from ._common import (
    resolve_xml_id_to_res_id, rollback_upsert, upsert_by_xml_id, values_match,
)

# Field set we manage. Limiting to these keeps the manifest interface narrow
# — other ir.model fields (modules, view_ids, etc.) are auto-managed by Odoo.
MANAGED_FIELDS = ["name", "model", "state", "info", "transient"]


def _build_values(op: dict) -> dict:
    if not op.get("model"):
        die("create_custom_model requires 'model' (the technical name, "
            "e.g. 'x_contract_allowance')")
    if not (op["model"].startswith("x_") or op["model"].startswith("x_studio_")):
        die(f"create_custom_model: model '{op['model']}' must start with 'x_' "
            f"(or 'x_studio_'). Odoo enforces this for runtime-created models; "
            f"without the prefix the row is treated as module-defined and "
            f"wiped on the next module reload.")
    vals = {
        "name": op.get("name") or op["model"],
        "model": op["model"],
        "state": "manual",  # required — see module docstring
    }
    if op.get("info"):
        vals["info"] = op["info"]
    if op.get("transient") is not None:
        vals["transient"] = bool(op["transient"])
    return vals


def apply(ctx, op, *, paths: Paths, env_name, changeset_id, op_index, dry_run=False):
    if not op.get("xml_id"):
        die("create_custom_model requires 'xml_id'")
    values = _build_values(op)
    if dry_run:
        return {"type": "create_custom_model",
                "target": f"xml_id:{op['xml_id']}",
                "model_name": op["model"],
                "status": "would-upsert"}
    rec_id, action, backup_path = upsert_by_xml_id(
        ctx, "ir.model", op["xml_id"], values,
        backup_ctx=(paths, env_name, changeset_id, op_index),
    )
    result = {"type": "create_custom_model",
              "target": f"ir.model:{rec_id}",
              "xml_id": op["xml_id"],
              "model_name": op["model"],
              "status": action}
    if backup_path:
        result["rollback_snapshot"] = str(backup_path.relative_to(paths.instance_root))
    return result


def verify(ctx, op, *, paths: Paths, changeset_id):
    values = _build_values(op)
    rec_id = resolve_xml_id_to_res_id(ctx, op["xml_id"], "ir.model")
    if not rec_id:
        return {"type": "create_custom_model", "xml_id": op["xml_id"],
                "matches": False, "reason": "not found"}
    current = call(ctx, "ir.model", "read", [[rec_id]],
                   {"fields": list(values.keys())})[0]
    return {"type": "create_custom_model", "target": f"ir.model:{rec_id}",
            "matches": values_match(current, values)}


def rollback(ctx, op_record, *, paths: Paths, env_name: str, dry_run: bool = False):
    # Rolling back a created custom model calls ir.model.unlink, which Odoo
    # cascades into dropping the DB table and removing the model from the
    # registry. If the model has acquired rows since creation (data writes
    # in later ops), the unlink will fail — that's the correct behaviour:
    # rolling back the model after data has landed in it would silently
    # destroy data. Auto-rollback walks ops in reverse, so a clean unwind
    # is: undo the data rows first, then this op runs safely.
    out = rollback_upsert(ctx, op_record, paths=paths, dry_run=dry_run)
    out["type"] = "create_custom_model"
    return out
