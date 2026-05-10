"""Operation handlers — one module per supported manifest `type:`.

Each handler module exposes:
    apply(ctx, op, *, paths, env_name, changeset_id, op_index, dry_run) -> dict
    verify(ctx, op, *, paths, changeset_id) -> dict

Adding a new operation type requires:
    1. New module here implementing apply() and verify()
    2. Registration in DISPATCH below
    3. Documentation in OdooAiDeployer/docs/changeset-format.md

Unknown operation types fail validation hard — no partial state.
"""
from . import (
    update_view,
    create_view,
    create_field,
    create_server_action,
    create_automated_action,
    create_cron,
    create_menu,
    create_record,
    update_record,
)

DISPATCH = {
    "update_view": update_view,
    "create_view": create_view,
    "create_field": create_field,
    "create_server_action": create_server_action,
    "create_automated_action": create_automated_action,
    "create_cron": create_cron,
    "create_menu": create_menu,
    "create_record": create_record,
    "update_record": update_record,
}
