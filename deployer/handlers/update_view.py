"""update_view — patch arch_db of an existing ir.ui.view.

Manifest fields:
    type: update_view
    key: <ir.ui.view key>            # OR xml_id: module.view_id
    website_id: false | <int>        # required when `key` matches multiple
                                     # views (multi-site COW). false = the
                                     # global template; int = a specific
                                     # website's COW copy.
    arch_file: relative path to XML
    backup: true                     # default true

Multi-website behaviour: when an Odoo website-aware view (http_routing.404,
website.layout, etc.) is touched in the Editor on a multi-site instance,
Odoo creates per-website COW copies. A bare `key:` lookup then returns
N+1 records and the handler used to silently pick the first. Now: if the
key resolves ambiguously, the handler refuses unless `website_id` is set.

Idempotency: arch_db is compared after lxml canonicalization (c14n2). Odoo
normalizes XML on write (attribute reorder, whitespace fold, self-closing
tag expansion); raw string compare lies and causes the handler to write
on every redeploy. Falls back to whitespace-collapsed compare if lxml is
unavailable or the content doesn't parse as XML.
"""
from __future__ import annotations

from .. import Paths, die, load_file_text
from ..audit import backup_record
from ..odoo_client import call
from ._common import resolve_xml_id_to_res_id


def _canonicalize_xml(text: str) -> str:
    """Return a canonical c14n2 form of the XML for safe equality comparison.

    Falls back to whitespace-collapsed text on any parse failure so we
    never throw — worst case is a slightly less reliable comparison.
    """
    if not text:
        return ""
    try:
        from lxml import etree
    except ImportError:
        return " ".join(text.split())
    try:
        root = etree.fromstring(text.encode("utf-8"))
    except etree.XMLSyntaxError:
        return " ".join(text.split())
    try:
        return etree.tostring(root, method="c14n2").decode("utf-8")
    except (TypeError, ValueError):
        # Older lxml without c14n2 — fall back to c14n1
        try:
            return etree.tostring(root, method="c14n").decode("utf-8")
        except Exception:
            return " ".join(text.split())


def _resolve_view_id(ctx: dict, op: dict) -> int:
    """Find the single ir.ui.view record this op targets. Fails if ambiguous."""
    if op.get("xml_id"):
        rec_id = resolve_xml_id_to_res_id(ctx, op["xml_id"], "ir.ui.view")
        if not rec_id:
            die(f"no ir.ui.view with xml_id {op['xml_id']}")
        return rec_id
    if op.get("key"):
        domain = [("key", "=", op["key"])]
        if "website_id" in op:
            wid = op["website_id"]
            # In Odoo, `website_id = False` means "global template"; an int
            # is a per-website copy. We pass through as-is.
            domain.append(("website_id", "=", wid if wid is not False else False))
        ids = call(ctx, "ir.ui.view", "search", [domain])
        if not ids:
            suffix = (f" with website_id={op['website_id']}"
                      if "website_id" in op else "")
            die(f"no ir.ui.view with key '{op['key']}'{suffix}")
        if len(ids) > 1:
            die(
                f"key '{op['key']}' is ambiguous — {len(ids)} matching views "
                f"({ids}). Multi-website Odoo COW'd this view per site. "
                f"Resolve by either:\n"
                f"  1. setting `xml_id:` (unambiguous), or\n"
                f"  2. setting `website_id: false` to target the global template, or\n"
                f"  3. setting `website_id: <int>` to target one specific site's copy."
            )
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

    current_canonical = _canonicalize_xml(current.get("arch_db") or "")
    target_canonical = _canonicalize_xml(target_arch)
    if current_canonical == target_canonical:
        return {"type": "update_view", "target": f"ir.ui.view:{view_id}",
                "status": "skipped", "reason": "arch_db matches (canonical)"}

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
    matches = (_canonicalize_xml(current.get("arch_db") or "")
               == _canonicalize_xml(target_arch))
    return {"type": "update_view", "target": f"ir.ui.view:{view_id}",
            "matches": matches}
