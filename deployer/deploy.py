"""Deploy a single named changeset to a single env.

Enforces:
  * --force only on dev (pre-checked by CLI but re-asserted here)
  * Promotion gate: staging requires dev audit; production requires both
  * Manifest sha256 binding: lower-env audits must match current content sha
  * Per-operation idempotency via the typed handler
  * Backup before any write (handler responsibility)
  * Audit file written on success (and on failed_partial)
"""
from __future__ import annotations

from pathlib import Path

import yaml

from . import Paths, VALID_ENVS, die, now_iso
from .audit import (
    audit_read, audit_write, backup_record, git_commit, git_head_sha,
    log_op, registry_lookup, registry_record,
)
from .handlers import DISPATCH
from .hash_changeset import changeset_sha256
from .odoo_client import connect


def load_manifest(paths: Paths, changeset_id: str) -> tuple[Path, dict]:
    cdir = paths.changeset_dir(changeset_id)
    if not cdir.is_dir():
        die(f"changeset folder not found: {cdir}")
    mpath = cdir / "manifest.yaml"
    if not mpath.is_file():
        die(f"manifest.yaml missing in {cdir}")
    try:
        manifest = yaml.safe_load(mpath.read_text()) or {}
    except yaml.YAMLError as exc:
        die(f"manifest.yaml parse error: {exc}")
    if manifest.get("id") != changeset_id:
        die(f"manifest.id ({manifest.get('id')!r}) must equal folder name ({changeset_id!r})")
    if not isinstance(manifest.get("operations"), list) or not manifest["operations"]:
        die("manifest.operations must be a non-empty list")
    return cdir, manifest


def check_promotion_gate(paths: Paths, env_name: str,
                         changeset_id: str, sha: str) -> None:
    """File-system gate: lower-env audits must exist with matching content sha.

    Cheap (no Odoo connection). Runs before connect. Catches the case where
    a changeset is being promoted but was never applied (or was applied with
    different content) on a lower env.
    """
    if env_name == "dev":
        return
    required = ["dev"] if env_name == "staging" else ["dev", "staging"]
    for lower in required:
        audit = audit_read(paths, lower, changeset_id)
        if not audit:
            die(
                f"promotion gate: cannot apply {changeset_id} on {env_name} — "
                f"missing audits/{lower}/{changeset_id}.json. "
                f"Apply on {lower} first."
            )
        if audit.get("manifest_sha256") != sha:
            die(
                f"promotion gate: audits/{lower}/{changeset_id}.json has "
                f"manifest_sha256={audit.get('manifest_sha256','')[:12]}... but "
                f"current changeset hashes to {sha[:12]}.... Re-apply on {lower}."
            )
        if audit.get("status") == "failed_partial":
            die(
                f"promotion gate: audits/{lower}/{changeset_id}.json has "
                f"status=failed_partial. Roll back or fix on {lower} first."
            )


def check_env_alignment(paths: Paths, env_name: str, changeset_id: str,
                        manifest: dict, ctx: dict) -> None:
    """Live-Odoo check: the target env's pre-deploy state for op 0 must match
    what the lower env recorded as its pre-deploy state.

    The change was tested on dev with starting state X. If staging/prod is
    in state Y instead of X (because someone Studio-edited Odoo behind our
    backs, or a previous changeset wasn't promoted), then the test isn't
    valid for this env — refuse before doing any harm.

    Only checks op 0 (sufficient: if op 0 starts from the same state on both
    envs and the deployer is deterministic, ops 1..N also align). Skipped on
    env=dev (where iteration is expected). Skipped per-op if the lower-env
    audit is from before this feature shipped (no `before_sha256_canonical`
    field), or if the handler doesn't expose `read_current_canonical_sha`.
    """
    if env_name == "dev":
        return
    operations = manifest.get("operations") or []
    if not operations:
        return
    op0 = operations[0]
    handler = DISPATCH.get(op0.get("type"))
    if handler is None or not hasattr(handler, "read_current_canonical_sha"):
        print(f"[align] op 0 ({op0.get('type')}): handler doesn't expose "
              f"read_current_canonical_sha — env-alignment check skipped")
        return

    required = ["dev"] if env_name == "staging" else ["dev", "staging"]
    for lower in required:
        audit = audit_read(paths, lower, changeset_id)
        if not audit:
            continue  # absence already caught by check_promotion_gate
        ops = audit.get("operations") or []
        if not ops:
            continue
        expected = ops[0].get("before_sha256_canonical")
        if not expected:
            print(f"[align] {lower} audit op 0 has no before_sha256_canonical "
                  f"(pre-feature audit) — env-alignment check skipped for {lower}")
            continue
        actual = handler.read_current_canonical_sha(ctx, op0)
        if actual != expected:
            die(
                f"env-alignment check FAILED for op 0 ({op0.get('type')}, "
                f"target={op0.get('xml_id') or op0.get('key')}).\n"
                f"\n"
                f"  expected (from {lower} audit): {expected[:20]}...\n"
                f"  actual on {env_name}:           {actual[:20]}...\n"
                f"\n"
                f"The change was built and tested on '{lower}' against a starting\n"
                f"state that does NOT match what '{env_name}' currently has. The\n"
                f"test isn't valid for this env. Resolve by either:\n"
                f"\n"
                f"  1. Restore '{env_name}' to the expected baseline state\n"
                f"     (often: run rollback for whatever drifted it, or re-clone\n"
                f"     staging from prod if this is a fresh-pre-prod scenario), OR\n"
                f"  2. Re-test the changeset on dev against '{env_name}'s current\n"
                f"     state (deploy on dev with --force; the new dev audit will\n"
                f"     record the new before_sha256_canonical, alignment will pass).\n"
            )
        print(f"[align] op 0 vs {lower} audit: MATCH")


def cmd_deploy(paths: Paths, env_name: str, changeset_id: str, *,
               force: bool, dry_run: bool, commit: bool) -> int:
    if env_name not in VALID_ENVS:
        die(f"unknown env '{env_name}'. Valid: {', '.join(VALID_ENVS)}")
    if force and env_name != "dev":
        die(f"--force is only allowed on dev, not {env_name}")

    cdir, manifest = load_manifest(paths, changeset_id)
    sha = changeset_sha256(cdir)
    git_sha = git_head_sha(paths.instance_root)

    check_promotion_gate(paths, env_name, changeset_id, sha)

    print(f"[connect] env={env_name}")
    ctx = connect(expected_env_name=env_name)
    print(f"[connect] authenticated uid={ctx['uid']} db={ctx['db']}")

    # Live env-alignment check: target env's pre-deploy state for op 0 must
    # match the lower env's recorded before_sha256_canonical. Catches the
    # "tested against state X, deployed against state Y" failure mode.
    check_env_alignment(paths, env_name, changeset_id, manifest, ctx)

    existing = registry_lookup(ctx, changeset_id)
    if existing and existing.get("manifest_sha256") == sha and not force and not dry_run:
        print(f"[skip] {changeset_id} already applied to {env_name} with matching sha256.")
        print("       Use --force on dev to re-apply, or bump the changeset.")
        return 0
    if existing and existing.get("manifest_sha256") != sha and not force:
        if env_name == "dev":
            print(f"[note] in-DB sha differs from local — re-applying on dev")
        else:
            die(
                f"in-DB registry shows {changeset_id} applied with "
                f"manifest_sha256={existing.get('manifest_sha256','')[:12]}..., "
                f"but local content hashes to {sha[:12]}.... Apply on dev first."
            )

    print(f"[apply] {changeset_id} on {env_name} "
          f"({len(manifest['operations'])} operations)"
          f"{' [DRY RUN]' if dry_run else ''}")

    started_at = now_iso()
    op_results: list[dict] = []
    failed_index: int | None = None
    failure_error: str | None = None

    for i, op in enumerate(manifest["operations"]):
        op_type = op.get("type")
        if op_type not in DISPATCH:
            die(f"operation {i}: unknown type '{op_type}'")
        handler = DISPATCH[op_type]
        try:
            result = handler.apply(
                ctx, op,
                paths=paths, env_name=env_name,
                changeset_id=changeset_id, op_index=i, dry_run=dry_run,
            )
        except SystemExit:
            raise
        except Exception as exc:
            failed_index = i
            failure_error = f"{type(exc).__name__}: {exc}"
            print(f"[apply] op {i} ({op_type}): FAILED — {failure_error}")
            log_op(paths, env_name, {"changeset": changeset_id,
                                     "op_index": i, "type": op_type,
                                     "status": "error", "error": failure_error})
            break

        result["op_index"] = i
        print(f"[apply] op {i} ({op_type}) target={result.get('target','?')}: {result.get('status')}")
        op_results.append(result)
        log_op(paths, env_name, {"changeset": changeset_id, **result})

    if dry_run:
        print("[dry-run] no writes; registry not updated; no audit file written.")
        return 0

    status = "deployed" if failed_index is None else "failed_partial"

    audit_payload = {
        "changeset": changeset_id,
        "environment": env_name,
        "git_commit_sha": git_sha,
        "manifest_sha256": sha,
        "status": status,
        "started_at": started_at,
        "finished_at": now_iso(),
        "applied_by": "ai" if env_name == "dev" else "tj",
        "description": manifest.get("description", ""),
        "operations": op_results,
    }
    if failed_index is not None:
        audit_payload["failed_operation"] = failed_index
        audit_payload["error"] = failure_error
        audit_payload["completed_operations"] = list(range(failed_index))
        audit_payload["recovery_hint"] = (
            f"Run `odoo-deploy --repo . rollback --env {env_name} --changeset "
            f"{changeset_id}` to undo ops 0..{failed_index - 1}, OR fix the "
            f"changeset and re-deploy with --force on dev. Do NOT re-deploy "
            f"without rolling back: ops 0..{failed_index - 1} would re-snapshot "
            f"the now-mutated state as the 'before' baseline, losing the true "
            f"pre-deploy reference."
        )

    # Order matters: write registry first (in-DB, authoritative for "what's
    # on this DB"), then the audit file (git-tracked, authoritative for
    # "what's promotable"). If audit-write fails after registry succeeds,
    # the next gate refuses to promote (no audit) and re-running with --force
    # is safe (registry/state match → idempotent skip → audit re-written).
    # The reverse order would let the audit lie: claim 'deployed' on disk
    # while the in-DB registry has no record.
    if failed_index is None:
        registry_record(ctx, changeset_id, git_sha, sha)

    audit_path = audit_write(paths, env_name, changeset_id, audit_payload)
    print(f"[audit] wrote {audit_path.relative_to(paths.instance_root)}")

    if commit:
        msg = f"deploy: {env_name}/{changeset_id} ({status})"
        committed = git_commit(paths.instance_root, [audit_path], msg)
        if committed:
            print(f"[git] committed: {msg}")

    if status == "failed_partial":
        print(f"[recovery] {audit_payload['recovery_hint']}")

    return 0 if status == "deployed" else 2
