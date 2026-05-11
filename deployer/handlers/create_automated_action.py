"""create_automated_action — base.automation record (Odoo 17+ schema).

In Odoo 17+ `base.automation` was refactored: it no longer carries `code`
or `state` directly. Automations now wrap one or more `ir.actions.server`
records via the `action_server_ids` m2m. The handler:

  1. Creates / updates a sibling `ir.actions.server` (xml_id derived as
     `<original_xml_id>__action`) holding the Python body.
  2. Creates / updates the `base.automation` referencing that action.

Either record's xml_id is reusable across deploys → idempotent updates.

Manifest (unchanged from v0):
    type: create_automated_action
    xml_id: my_module.my_automation
    name: <human name>
    model: <target model>
    trigger: on_create | on_write | on_create_or_write | on_unlink |
             on_change | on_time | on_state_set | on_priority_set |
             on_archive | on_unarchive | ...
    code_file: relative path to .py file (server-action body)
    filter_domain: "[('field','=',value)]"   # optional
"""
from __future__ import annotations

from .. import Paths, die, load_file_text
from ..odoo_client import call
from ._common import (
    resolve_xml_id_to_res_id, rollback_upsert, upsert_by_xml_id, values_match,
)


def _action_xml_id(automation_xml_id: str) -> str:
    """Deterministic xml_id for the sibling server action."""
    return f"{automation_xml_id}__action"


def _build_action_values(ctx: dict, op: dict, paths: Paths,
                         changeset_id: str, model_id: int) -> dict:
    if not op.get("code_file"):
        die("create_automated_action requires 'code_file' (the server-action body)")
    return {
        "name": (op.get("name") or op["xml_id"]) + " (server action)",
        "model_id": model_id,
        "state": "code",
        "code": load_file_text(paths.changeset_dir(changeset_id), op["code_file"]),
        "usage": "base_automation",
    }


def _build_automation_values(op: dict, model_id: int,
                             action_server_id: int) -> dict:
    vals = {
        "name": op.get("name") or op["xml_id"],
        "model_id": model_id,
        "trigger": op["trigger"],
        "action_server_ids": [(6, 0, [action_server_id])],
        "active": bool(op.get("active", True)),
    }
    if op.get("filter_domain"):
        vals["filter_domain"] = op["filter_domain"]
    return vals


def _resolve_model_id(ctx: dict, model: str) -> int:
    recs = call(ctx, "ir.model", "search_read",
                [[("model", "=", model)]], {"fields": ["id"], "limit": 1})
    if not recs:
        die(f"model '{model}' not found")
    return recs[0]["id"]


def apply(ctx, op, *, paths: Paths, env_name, changeset_id, op_index, dry_run=False):
    if not op.get("xml_id") or not op.get("model") or not op.get("trigger"):
        die("create_automated_action requires 'xml_id', 'model', 'trigger'")
    model_id = _resolve_model_id(ctx, op["model"])
    action_xml_id = _action_xml_id(op["xml_id"])

    if dry_run:
        return {"type": "create_automated_action",
                "target": f"xml_id:{op['xml_id']}",
                "sub_target": f"xml_id:{action_xml_id}",
                "status": "would-upsert"}

    # Step 1: sibling ir.actions.server holding the code.
    action_values = _build_action_values(ctx, op, paths, changeset_id, model_id)
    action_id, action_status, action_backup = upsert_by_xml_id(
        ctx, "ir.actions.server", action_xml_id, action_values,
        backup_ctx=(paths, env_name, changeset_id, op_index),
    )

    # Step 2: base.automation pointing at that action.
    automation_values = _build_automation_values(op, model_id, action_id)
    auto_id, auto_status, auto_backup = upsert_by_xml_id(
        ctx, "base.automation", op["xml_id"], automation_values,
        backup_ctx=(paths, env_name, changeset_id, op_index),
    )

    sub = {"target": f"ir.actions.server:{action_id}",
           "xml_id": action_xml_id, "status": action_status}
    if action_backup:
        sub["rollback_snapshot"] = str(action_backup.relative_to(paths.instance_root))

    result = {
        "type": "create_automated_action",
        "target": f"base.automation:{auto_id}",
        "xml_id": op["xml_id"],
        "status": auto_status,
        "sub_records": [sub],
    }
    if auto_backup:
        result["rollback_snapshot"] = str(auto_backup.relative_to(paths.instance_root))
    return result


def verify(ctx, op, *, paths: Paths, changeset_id):
    if not op.get("model") or not op.get("trigger"):
        return {"type": "create_automated_action", "xml_id": op["xml_id"],
                "matches": False, "reason": "manifest missing model/trigger"}
    model_id = _resolve_model_id(ctx, op["model"])
    action_xml_id = _action_xml_id(op["xml_id"])

    action_id = resolve_xml_id_to_res_id(ctx, action_xml_id, "ir.actions.server")
    if not action_id:
        return {"type": "create_automated_action", "xml_id": op["xml_id"],
                "matches": False, "reason": f"sibling action {action_xml_id} not found"}
    auto_id = resolve_xml_id_to_res_id(ctx, op["xml_id"], "base.automation")
    if not auto_id:
        return {"type": "create_automated_action", "xml_id": op["xml_id"],
                "matches": False, "reason": "automation record not found"}

    action_values = _build_action_values(ctx, op, paths, changeset_id, model_id)
    auto_values = _build_automation_values(op, model_id, action_id)

    current_action = call(ctx, "ir.actions.server", "read", [[action_id]],
                          {"fields": list(action_values.keys())})[0]
    current_auto = call(ctx, "base.automation", "read", [[auto_id]],
                        {"fields": list(auto_values.keys())})[0]

    matches = (values_match(current_action, action_values)
               and values_match(current_auto, auto_values))
    return {"type": "create_automated_action",
            "target": f"base.automation:{auto_id}",
            "matches": matches}


def rollback(ctx, op_record, *, paths: Paths, env_name: str, dry_run: bool = False):
    """Composite rollback: undo base.automation first (it references the
    server action), then undo the sibling ir.actions.server. Reverse order
    from apply() to avoid foreign-key violations on unlink."""
    results: list[dict] = []
    # 1. Top-level base.automation
    auto_result = rollback_upsert(ctx, op_record, paths=paths, dry_run=dry_run)
    auto_result["record"] = "base.automation"
    results.append(auto_result)
    # 2. Sibling action(s)
    for sub in op_record.get("sub_records") or []:
        sub_result = rollback_upsert(ctx, sub, paths=paths, dry_run=dry_run)
        sub_result["record"] = "ir.actions.server"
        results.append(sub_result)
    # Roll-up status: if any sub returned manual-required, surface that;
    # otherwise inherit the top-level status.
    rollup = "restored" if all(r.get("status") in
                               ("restored", "unlinked", "skipped",
                                "would-restore", "would-unlink")
                               for r in results) else "manual-required"
    return {"type": "create_automated_action",
            "target": op_record.get("target"),
            "status": rollup, "sub_results": results}
