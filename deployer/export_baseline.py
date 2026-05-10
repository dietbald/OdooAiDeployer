"""Export current Odoo customization records into baseline/<env>/.

Captures the production state at a point in time so later changesets can
diff against a known starting point and the drift detector has a reference.

Per the architecture doc, the baseline includes (at minimum):
    ir.ui.view
    ir.actions.server
    base.automation
    ir.cron
    ir.model.fields
    ir.ui.menu
    ir.actions.act_window
    mail.template
    ir.model.access
    ir.rule
    website.page

For each record we store: model, db_id, xml_id (if any), key (if any), name,
write_date, the meaningful payload field(s), a sha256, and exported_at.

V1: stub harness in place; expand model coverage as we tackle them.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import Paths, now_iso
from .odoo_client import call, connect

# (model, candidate_payload_fields, file_extension, payload_key_or_None)
# `candidate_payload_fields` are filtered against fields_get() at runtime so
# the plan tolerates schema differences across Odoo versions.
EXPORT_PLAN = [
    ("ir.ui.view", ["arch_db"], "xml", "arch_db"),
    ("ir.actions.server", ["code"], "py", "code"),
    # Odoo 17+: base.automation no longer has its own `code` field — it wraps
    # ir.actions.server via action_server_id(s). We capture the wiring fields
    # here; the actual code is already covered by the ir.actions.server export.
    ("base.automation", ["name", "trigger", "active", "filter_domain",
                         "model_id", "action_server_id", "action_server_ids"],
     "json", None),
    ("ir.cron", ["code", "interval_number", "interval_type", "active"], "json", None),
    ("ir.model.fields", ["ttype", "field_description", "relation"], "json", None),
    ("ir.ui.menu", ["name", "sequence", "parent_id"], "json", None),
    ("mail.template", ["body_html", "subject"], "json", None),
]


def _sha(s: str | dict) -> str:
    if not isinstance(s, str):
        s = json.dumps(s, sort_keys=True, default=str)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _existing_fields(ctx: dict, model: str, candidates: list[str]) -> list[str]:
    """Filter `candidates` to the fields that actually exist on `model`."""
    try:
        info = call(ctx, model, "fields_get", [candidates], {"attributes": []})
    except Exception:
        # If the model itself doesn't exist or fields_get fails, return [].
        return []
    return [f for f in candidates if f in info]


def _export_one_model(ctx: dict, paths: Paths, env_name: str,
                      model: str, candidate_fields: list[str],
                      ext: str, payload_key: str | None) -> int:
    fields = _existing_fields(ctx, model, candidate_fields)
    if not fields:
        print(f"[baseline] {model}: no usable fields on this DB (plan asked for {candidate_fields}); skipping")
        return 0
    if payload_key and payload_key not in fields:
        # Fall back to dict-payload when the named single field is gone.
        payload_key = None
        ext = "json"

    model_dir = paths.baseline / env_name / model.replace(".", "_")
    model_dir.mkdir(parents=True, exist_ok=True)
    recs = call(ctx, model, "search_read",
                [[]], {"fields": ["id", "write_date"] + fields, "order": "id"})
    xml_links = call(ctx, "ir.model.data", "search_read",
                     [[("model", "=", model)]],
                     {"fields": ["res_id", "module", "name"]})
    xml_by_id = {x["res_id"]: f"{x['module']}.{x['name']}" for x in xml_links}

    count = 0
    for r in recs:
        rec_id = r["id"]
        xml_id = xml_by_id.get(rec_id)
        stem = (xml_id or f"id_{rec_id}").replace("/", "_").replace(":", "_")
        payload = r.get(payload_key) if payload_key else {k: r.get(k) for k in fields}
        metadata = {
            "model": model, "id": rec_id, "xml_id": xml_id,
            "write_date": str(r.get("write_date") or ""),
            "exported_at": now_iso(),
            "payload_sha256": _sha(payload or ""),
        }
        if ext in ("xml", "py"):
            (model_dir / f"{stem}.{ext}").write_text(payload or "")
            (model_dir / f"{stem}.meta.json").write_text(
                json.dumps(metadata, indent=2, sort_keys=True, default=str) + "\n")
        else:
            metadata["payload"] = payload
            (model_dir / f"{stem}.json").write_text(
                json.dumps(metadata, indent=2, sort_keys=True, default=str) + "\n")
        count += 1
    print(f"[baseline] {model}: exported {count}")
    return count


def cmd_export_baseline(paths: Paths, env_name: str = "production") -> int:
    out_root = paths.baseline / env_name
    out_root.mkdir(parents=True, exist_ok=True)

    ctx = connect()
    print(f"[baseline] connected uid={ctx['uid']} db={ctx['db']}")

    manifest = {
        "env": env_name,
        "exported_at": now_iso(),
        "records_per_model": {},
        "errors": {},
    }

    for model, fields, ext, payload_key in EXPORT_PLAN:
        try:
            count = _export_one_model(ctx, paths, env_name, model, fields, ext, payload_key)
            manifest["records_per_model"][model] = count
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            print(f"[baseline] {model}: FAILED — {msg[:200]}")
            manifest["errors"][model] = msg[:500]

    (out_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n")
    print(f"[baseline] manifest written to {(out_root / 'manifest.json').relative_to(paths.instance_root)}")
    return 0 if not manifest["errors"] else 2
