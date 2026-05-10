"""update_view — patch arch_db of an existing ir.ui.view.

Manifest fields:
    type: update_view
    key: <ir.ui.view key>            # OR xml_id: module.view_id
    arch_file: relative path to XML
    backup: true                     # default true
"""
from __future__ import annotations

from .. import Paths, die, load_file_text
from ..audit import backup_record
from ..odoo_client import call
from ._common import resolve_xml_id_to_res_id


def _resolve_view_id(ctx: dict, op: dict) -> int:
    if op.get("xml_id"):
        rec_id = resolve_xml_id_to_res_id(ctx, op["xml_id"], "ir.ui.view")
        if not rec_id:
            die(f"no ir.ui.view with xml_id {op['xml_id']}")
        return rec_id
    if op.get("key"):
        ids = call(ctx, "ir.ui.view", "search", [[("key", "=", op["key"])]])
        if not ids:
            die(f"no ir.ui.view with key '{op['key']}'")
        if len(ids) > 1:
            print(f"[warn] multiple views with key={op['key']}: {ids} — using {ids[0]}")
        return ids[0]
    die("update_view requires either 'key' or 'xml_id'")


def apply(ctx: dict, op: dict, *, paths: Paths, env_name: str,
          changeset_id: str, op_index: int, dry_run: bool = False) -> dict:
    if not op.get("arch_file"):
        die("update_view requires 'arch_file'")
    target_arch = load_file_text(paths.changeset_dir(changeset_id), op["arch_file"])

    view_id = _resolve_view_id(ctx, op)
    current = call(ctx, "ir.ui.view", "read", [[view_id]],
                   {"fields": ["id", "key", "name", "arch_db", "website_id"]})[0]

    if (current.get("arch_db") or "").strip() == target_arch.strip():
        return {"type": "update_view", "target": f"ir.ui.view:{view_id}",
                "status": "skipped", "reason": "arch_db already matches"}

    if dry_run:
        return {"type": "update_view", "target": f"ir.ui.view:{view_id}",
                "status": "would-update",
                "current_preview": (current.get("arch_db") or "")[:200],
                "target_preview": target_arch[:200]}

    backup_path = None
    if op.get("backup", True):
        backup_path = backup_record(paths, env_name, changeset_id, op_index,
                                    "ir.ui.view", view_id,
                                    current.get("arch_db") or "", ext="xml")

    call(ctx, "ir.ui.view", "write", [[view_id], {"arch_db": target_arch}])

    return {
        "type": "update_view",
        "target": f"ir.ui.view:{view_id}",
        "status": "applied",
        "view_key": current.get("key"),
        "rollback_snapshot": str(backup_path.relative_to(paths.instance_root)) if backup_path else None,
    }


def verify(ctx: dict, op: dict, *, paths: Paths, changeset_id: str) -> dict:
    target_arch = load_file_text(paths.changeset_dir(changeset_id), op["arch_file"])
    view_id = _resolve_view_id(ctx, op)
    current = call(ctx, "ir.ui.view", "read", [[view_id]],
                   {"fields": ["arch_db"]})[0]
    matches = (current.get("arch_db") or "").strip() == target_arch.strip()
    return {"type": "update_view", "target": f"ir.ui.view:{view_id}",
            "matches": matches}
