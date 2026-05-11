"""Export current Odoo customization records into baseline/<env>/.

Captures the configuration / customization layer of an Odoo instance — view
definitions, server actions, automations, crons, custom field definitions,
menus, mail templates, ACLs, record rules, custom models, etc. — at a point
in time. NOT business data: products, partners, orders, invoices, employees,
applicants, etc. are deliberately excluded.

The export is intentionally unfiltered: even Odoo's own stock customization
records (e.g., the ~22K `ir.model.fields` defined by stock modules) are
included. This makes the baseline a faithful snapshot of "what this database
looks like" rather than only "what BICC added on top". The cost is bytes
(~150MB for a real BICC export); the benefit is a reliable diff target for
any record on any model.

Each record is written as one file (xml/py for text payloads, json for
structured) plus a `.meta.json` sidecar with `id`, `xml_id`, `write_date`,
and `payload_sha256` so a future drift detector can compare cheaply.
"""
from __future__ import annotations

import hashlib
import json

from . import Paths, now_iso
from .odoo_client import call, connect

# (model, body_fields, file_extension, body_payload_key, lookup_fields)
# `body_fields` go into the on-disk payload (xml/py file body or .json payload).
# `lookup_fields` go ONLY into the .meta.json sidecar so AI can grep meta files
# to find "which view has key=X with website_id=Y" without having to also parse
# the arch_db bodies. Critical for ir.ui.view: when Odoo's website module
# COW-clones a view, `t-name=` in the arch XML stays the same but the row's
# actual `key` field is rewritten to a unique value — AI seeing only the arch
# would silently target the wrong row. Lookup fields close that gap.
# All fields are filtered against fields_get() at runtime so the plan tolerates
# schema differences across Odoo versions.
EXPORT_PLAN = [
    ("ir.ui.view", ["arch_db"], "xml", "arch_db",
     ["key", "name", "website_id", "type", "inherit_id", "mode", "active"]),
    ("ir.actions.server", ["code"], "py", "code",
     ["name", "model_id", "state", "binding_model_id", "usage"]),
    ("ir.actions.act_window",
     ["name", "res_model", "view_mode", "domain", "context", "target"],
     "json", None, []),
    # Odoo 17+: base.automation no longer has its own `code` field — it wraps
    # ir.actions.server via action_server_id(s). We capture the wiring fields
    # here; the actual code is covered by the ir.actions.server export above.
    ("base.automation", ["name", "trigger", "active", "filter_domain",
                         "model_id", "action_server_id", "action_server_ids"],
     "json", None, []),
    ("ir.cron", ["name", "code", "interval_number", "interval_type",
                 "active", "model_id", "user_id"], "json", None, []),
    ("ir.model", ["name", "model", "state", "modules"], "json", None, []),
    ("ir.model.fields", ["name", "ttype", "field_description", "relation",
                         "state", "model_id", "required", "store", "translate"],
     "json", None, []),
    ("ir.model.access", ["name", "model_id", "group_id",
                         "perm_read", "perm_write", "perm_create", "perm_unlink"],
     "json", None, []),
    ("ir.rule", ["name", "model_id", "domain_force", "groups", "active",
                 "perm_read", "perm_write", "perm_create", "perm_unlink"],
     "json", None, []),
    ("ir.ui.menu", ["name", "sequence", "parent_id", "action", "groups_id"],
     "json", None, []),
    ("mail.template", ["name", "model_id", "subject", "body_html",
                       "email_from", "email_to", "lang"], "json", None, []),
    # website.page only exists if the website module is installed; the
    # _existing_fields probe + outer try/except will skip it cleanly otherwise.
    ("website.page", ["name", "url", "view_id", "website_id",
                      "is_published", "website_indexed"], "json", None, []),
]


def _sha(s) -> str:
    """sha256 of any JSON-serializable payload."""
    if not isinstance(s, str):
        s = json.dumps(s, sort_keys=True, default=str)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _existing_fields(ctx: dict, model: str, candidates: list[str]) -> list[str]:
    """Filter `candidates` to fields that actually exist on `model`.

    Returns [] if the model itself doesn't exist or fields_get fails — caller
    should treat that as "skip this model on this DB".
    """
    try:
        info = call(ctx, model, "fields_get", [candidates], {"attributes": []})
    except Exception:
        return []
    return [f for f in candidates if f in info]


def _export_one_model(ctx: dict, paths: Paths, env_name: str,
                      model: str, body_candidates: list[str],
                      ext: str, payload_key: str | None,
                      lookup_candidates: list[str] | None = None) -> int:
    body_fields = _existing_fields(ctx, model, body_candidates)
    if not body_fields:
        print(f"[baseline] {model}: no usable fields on this DB "
              f"(plan asked for {body_candidates}); skipping")
        return 0
    if payload_key and payload_key not in body_fields:
        # Fall back to dict-payload when the named single field is gone.
        payload_key = None
        ext = "json"

    # Lookup fields land in meta.json only (so AI can grep meta for the right
    # key/website_id/etc. without parsing arch payloads). Skipped silently for
    # any field the schema doesn't have.
    lookup_fields = (_existing_fields(ctx, model, lookup_candidates or [])
                     if lookup_candidates else [])

    model_dir = paths.baseline / env_name / model.replace(".", "_")
    model_dir.mkdir(parents=True, exist_ok=True)
    all_fields = list(dict.fromkeys(body_fields + lookup_fields))
    recs = call(ctx, model, "search_read",
                [[]], {"fields": ["id", "write_date"] + all_fields, "order": "id"})
    xml_links = call(ctx, "ir.model.data", "search_read",
                     [[("model", "=", model)]],
                     {"fields": ["res_id", "module", "name"]})
    xml_by_id = {x["res_id"]: f"{x['module']}.{x['name']}" for x in xml_links}

    count = 0
    for r in recs:
        rec_id = r["id"]
        xml_id = xml_by_id.get(rec_id)
        # Sanitize stem: file-system safe, no path separators or colons.
        stem = (xml_id or f"id_{rec_id}").replace("/", "_").replace(":", "_")
        payload = (r.get(payload_key) if payload_key
                   else {k: r.get(k) for k in body_fields})
        metadata = {
            "model": model,
            "id": rec_id,
            "xml_id": xml_id,
            "write_date": str(r.get("write_date") or ""),
            "exported_at": now_iso(),
            "payload_sha256": _sha(payload or ""),
        }
        if lookup_fields:
            # Strip the m2o display-name half ([id, name] → id) so AI grep can
            # match website ids without quoting tuples. Name lookups still
            # available via the `name` field if present in lookup_fields.
            lookup = {}
            for k in lookup_fields:
                v = r.get(k)
                if (isinstance(v, list) and len(v) == 2
                        and isinstance(v[0], int) and isinstance(v[1], str)):
                    lookup[k] = v[0]
                    lookup[f"{k}_display"] = v[1]
                else:
                    lookup[k] = v
            metadata["lookup"] = lookup
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

    ctx = connect(expected_env_name=env_name)
    print(f"[baseline] connected uid={ctx['uid']} db={ctx['db']}")

    manifest = {
        "env": env_name,
        "exported_at": now_iso(),
        "deployer_version": __import__("deployer").__version__,
        "records_per_model": {},
        "errors": {},
    }

    for entry in EXPORT_PLAN:
        # Tolerate 4-tuple legacy entries (no lookup_fields) and 5-tuple new shape.
        if len(entry) == 4:
            model, fields, ext, payload_key = entry
            lookup_fields: list[str] = []
        else:
            model, fields, ext, payload_key, lookup_fields = entry
        try:
            count = _export_one_model(ctx, paths, env_name, model, fields,
                                      ext, payload_key, lookup_fields)
            manifest["records_per_model"][model] = count
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            print(f"[baseline] {model}: FAILED — {msg[:200]}")
            manifest["errors"][model] = msg[:500]

    (out_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n")
    print(f"[baseline] manifest written to "
          f"{(out_root / 'manifest.json').relative_to(paths.instance_root)}")
    return 0 if not manifest["errors"] else 2
