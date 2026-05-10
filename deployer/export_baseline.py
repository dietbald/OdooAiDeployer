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

# (model, payload_fields, file_extension, payload_key)
EXPORT_PLAN = [
    ("ir.ui.view", ["arch_db"], "xml", "arch_db"),
    ("ir.actions.server", ["code"], "py", "code"),
    ("base.automation", ["code"], "py", "code"),
    ("ir.cron", ["code", "interval_number", "interval_type"], "json", None),
    ("ir.model.fields", ["ttype", "field_description", "relation"], "json", None),
    ("ir.ui.menu", ["name", "sequence", "parent_id"], "json", None),
    ("mail.template", ["body_html", "subject"], "json", None),
]


def _sha(s: str | dict) -> str:
    if not isinstance(s, str):
        s = json.dumps(s, sort_keys=True)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def cmd_export_baseline(paths: Paths, env_name: str = "production") -> int:
    out_root = paths.baseline / env_name
    out_root.mkdir(parents=True, exist_ok=True)

    ctx = connect()
    print(f"[baseline] connected uid={ctx['uid']} db={ctx['db']}")

    manifest = {"env": env_name, "exported_at": now_iso(), "records_per_model": {}}

    for model, fields, ext, payload_key in EXPORT_PLAN:
        model_dir = out_root / model.replace(".", "_")
        model_dir.mkdir(parents=True, exist_ok=True)
        recs = call(ctx, model, "search_read",
                    [[]], {"fields": ["id", "write_date"] + fields,
                           "order": "id"})
        # Cross-reference xml_ids
        xml_links = call(ctx, "ir.model.data", "search_read",
                         [[("model", "=", model)]],
                         {"fields": ["res_id", "module", "name"]})
        xml_by_id = {x["res_id"]: f"{x['module']}.{x['name']}" for x in xml_links}

        count = 0
        for r in recs:
            rec_id = r["id"]
            xml_id = xml_by_id.get(rec_id)
            stem = (xml_id or f"id_{rec_id}").replace("/", "_")
            payload = r.get(payload_key) if payload_key else {k: r.get(k) for k in fields}
            metadata = {
                "model": model, "id": rec_id, "xml_id": xml_id,
                "write_date": r.get("write_date"),
                "exported_at": now_iso(),
                "payload_sha256": _sha(payload or ""),
            }
            if ext in ("xml", "py"):
                (model_dir / f"{stem}.{ext}").write_text(payload or "")
                (model_dir / f"{stem}.meta.json").write_text(
                    json.dumps(metadata, indent=2, sort_keys=True) + "\n")
            else:
                metadata["payload"] = payload
                (model_dir / f"{stem}.json").write_text(
                    json.dumps(metadata, indent=2, sort_keys=True) + "\n")
            count += 1
        manifest["records_per_model"][model] = count
        print(f"[baseline] {model}: exported {count}")

    (out_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"[baseline] manifest written to {(out_root / 'manifest.json').relative_to(paths.instance_root)}")
    return 0
