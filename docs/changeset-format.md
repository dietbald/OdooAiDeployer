# Changeset format

Each change is a numbered folder under `changesets/` in an instance repo.

```
changesets/043_recruitment_reminder/
├── manifest.yaml             # required
├── reminder_action.py        # referenced by an op via `code_file:`
├── http_routing_404.xml      # referenced by an op via `arch_file:`
└── README.md                 # optional, human notes (still hashed)
```

The folder name is the changeset id and must equal `manifest.id`.

## manifest.yaml

```yaml
id: 043_recruitment_reminder           # must equal folder name
schema_version: 1                      # required; bumped only on breaking format changes
description: |
  One-line summary of intent and why.
author: ai
allow_generic_records: false           # default false; gates create_record/update_record

operations:
  - type: update_view
    key: http_routing.404
    arch_file: http_routing_404.xml
    backup: true                       # default true

  - type: create_server_action
    xml_id: bicc_recruitment.reminder_action
    name: Recruitment Reminder Action
    model: hr.applicant
    state: code
    code_file: reminder_action.py
```

Operations are applied in order. If an operation fails mid-way, the audit file records `status: failed_partial` with `failed_operation` index — promotion to higher envs is blocked until resolved.

## Supported operation types

| Type | Model | Required fields | Notes |
|---|---|---|---|
| `update_view` | `ir.ui.view` | `key` or `xml_id`, `arch_file` | Backup arch_db before write |
| `create_view` | `ir.ui.view` | `xml_id`, `arch_file` | Optional `inherit_id`, `model`, `priority` |
| `create_field` | `ir.model.fields` | `xml_id`, `model`, `name`, `field_type` | Custom fields (`x_*`) |
| `create_server_action` | `ir.actions.server` | `xml_id`, `model`, `code_file` | `state: code` only in v1 |
| `create_automated_action` | `base.automation` | `xml_id`, `model`, `trigger` | `code_file` if state=code |
| `create_cron` | `ir.cron` | `xml_id`, `model`, `code_file`, `interval_*` | |
| `create_menu` | `ir.ui.menu` | `xml_id`, `name` | Optional `parent_xml_id`, `action_xml_id` |
| `create_record` | whitelist | `xml_id`, `model`, `values` | **Requires `allow_generic_records: true`** |
| `update_record` | whitelist | `xml_id`, `model`, `values` | **Requires `allow_generic_records: true`** |

Whitelist for generic ops lives in `deployer/handlers/create_record.py:MODEL_WHITELIST`. Adding a new model = TJ-only edit to that file.

Unknown operation types cause hard failure during validation — no partial state.

## Server-action / cron / automation Python bodies

These live in sibling `.py` files referenced by `code_file:`. They are **data**: the deployer never imports or executes them locally — it sends them to Odoo as opaque strings, where they run inside Odoo's `safe_eval`.

Forbidden in these bodies (validator will reject):

```
import <anything>
from <module> import <anything>
__import__   eval   exec   compile   open
os.<attr>    sys.<attr>    subprocess.<attr>    socket.<attr>
```

Use the Odoo-provided `env`, `record`, `records`, `model`, `log`, `Warning`, `UserError` etc. — see `/odoo-v19-guide` skill for the SaaS-safe API surface.

## Idempotency

Every handler reads current state before any write:
- If current matches target → log `skipped`, no backup, no audit churn.
- If different → write a backup snapshot to `rollback_snapshots/<env>/<id>/`, apply, log `applied` (or `created`/`updated` for upsert handlers).

This makes `--force` (dev only) and partial reruns safe.

## Rollback

`odoo-deploy rollback --env <env> --changeset <id>` walks the audit file in reverse and dispatches each op to its handler's `rollback()`:

| Apply status | Rollback effect |
|---|---|
| `applied` (update_view, update_record) | Restore prior state from snapshot |
| `created` | `unlink` the record + remove its `ir.model.data` row |
| `updated` | Write prior values back from the JSON snapshot |
| `skipped` | No-op — nothing to undo |

`create_automated_action` is composite: rollback undoes the `base.automation` first (so the `ir.actions.server` is no longer referenced), then the sibling action.

If a handler can't undo cleanly (snapshot missing, FK constraint, etc.), it returns `manual-required` and the rollback CLI exits 2 — the operator gets a clear "this op needs hand-cleanup" signal without the rollback aborting halfway.

## Hashing

The `manifest_sha256` covers every file in the changeset folder (paths + contents, sorted). Any byte-level change anywhere in the folder bumps the hash and forces a re-promotion through dev → staging → production.

The hash does NOT include `audits/`, `reports/`, `rollback_snapshots/` — those are deployment outputs, not inputs.

## Blocklist

`config/blocklist/models.txt`, `xml_ids.txt`, and `operation_types.txt` in the instance repo list things AI cannot author changesets against. Validator rejects the changeset if any operation matches a listed entry.
