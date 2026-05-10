"""Static validation of a changeset (no Odoo connection needed).

Runs in CI (`validate.yml`) as the first gate. Output is both human-readable
on stdout AND a machine-readable report at reports/validation/<id>.json plus
an AI-friendly markdown summary at reports/ai_feedback/<id>.md.

Checks performed:
  * manifest.yaml parses and matches schema
  * id == folder name
  * each operation type is registered in handlers.DISPATCH
  * all referenced content files exist
  * .xml files are well-formed
  * .py files parse (ast.parse) and contain no forbidden imports/calls
  * generic operations require allow_generic_records: true
  * targets are not in config/blocklist/

This module is V1-stub-then-grow: the harness is in place, the rule set
will expand as new failure modes are seen.
"""
from __future__ import annotations

import ast
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

from . import Paths, die, now_iso
from .handlers import DISPATCH
from .hash_changeset import changeset_sha256

FORBIDDEN_NAMES = {
    "__import__", "eval", "exec", "compile", "open",
    "input", "globals", "locals", "vars", "delattr", "setattr",
}
FORBIDDEN_MODULE_PREFIXES = (
    "os", "sys", "subprocess", "socket", "requests", "urllib",
    "http", "ftplib", "smtplib", "pathlib", "shutil", "ctypes",
)
GENERIC_OPS = {"create_record", "update_record"}


def _check_python_body(code: str, source: str) -> list[dict]:
    issues: list[dict] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [{"file": source, "line": exc.lineno or 0,
                 "msg": f"Python syntax error: {exc.msg}"}]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                issues.append({
                    "file": source, "line": node.lineno,
                    "msg": f"`import {alias.name}` is not allowed in Odoo server actions / cron / automation code",
                })
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if any(mod == p or mod.startswith(p + ".") for p in FORBIDDEN_MODULE_PREFIXES) or True:
                issues.append({
                    "file": source, "line": node.lineno,
                    "msg": f"`from {mod} import ...` is not allowed in Odoo server actions / cron / automation code",
                })
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in FORBIDDEN_NAMES:
                issues.append({
                    "file": source, "line": node.lineno,
                    "msg": f"`{node.func.id}(...)` is not allowed in Odoo server actions / cron / automation code",
                })
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id in {"os", "sys", "subprocess", "socket"}:
                issues.append({
                    "file": source, "line": node.lineno,
                    "msg": f"`{node.value.id}.{node.attr}` is not allowed in Odoo server actions / cron / automation code",
                })
    return issues


def _load_blocklist(paths: Paths) -> dict:
    """Read config/blocklist/*.txt files (one entry per line, # comments)."""
    blocklist_dir = paths.config / "blocklist"
    out: dict[str, set[str]] = {}
    if not blocklist_dir.is_dir():
        return out
    for txt in sorted(blocklist_dir.glob("*.txt")):
        kind = txt.stem
        entries: set[str] = set()
        for raw in txt.read_text().splitlines():
            line = raw.split("#", 1)[0].strip()
            if line:
                entries.add(line)
        out[kind] = entries
    return out


def _check_blocklist(op: dict, blocklist: dict) -> list[str]:
    errs: list[str] = []
    if op.get("model") and op["model"] in blocklist.get("models", set()):
        errs.append(f"model '{op['model']}' is in config/blocklist/models.txt — AI cannot author changesets touching it")
    if op.get("xml_id") and op["xml_id"] in blocklist.get("xml_ids", set()):
        errs.append(f"xml_id '{op['xml_id']}' is in config/blocklist/xml_ids.txt")
    if op.get("type") in blocklist.get("operation_types", set()):
        errs.append(f"operation type '{op['type']}' is in config/blocklist/operation_types.txt")
    return errs


def cmd_validate(paths: Paths, changeset_id: str) -> int:
    cdir = paths.changeset_dir(changeset_id)
    issues: list[dict] = []
    structured: dict = {
        "changeset": changeset_id,
        "validated_at": now_iso(),
        "errors": [],
        "warnings": [],
    }

    if not cdir.is_dir():
        die(f"changeset folder not found: {cdir}")

    mpath = cdir / "manifest.yaml"
    if not mpath.is_file():
        issues.append({"file": str(mpath), "line": 0, "msg": "manifest.yaml missing"})
        return _write_reports(paths, changeset_id, structured, issues, ok=False)

    try:
        manifest = yaml.safe_load(mpath.read_text()) or {}
    except yaml.YAMLError as exc:
        issues.append({"file": "manifest.yaml", "line": 0, "msg": f"YAML parse error: {exc}"})
        return _write_reports(paths, changeset_id, structured, issues, ok=False)

    if manifest.get("id") != changeset_id:
        issues.append({"file": "manifest.yaml", "line": 0,
                       "msg": f"manifest.id ({manifest.get('id')!r}) must equal folder name ({changeset_id!r})"})

    ops = manifest.get("operations") or []
    if not isinstance(ops, list) or not ops:
        issues.append({"file": "manifest.yaml", "line": 0,
                       "msg": "operations must be a non-empty list"})
        return _write_reports(paths, changeset_id, structured, issues, ok=False)

    blocklist = _load_blocklist(paths)
    allow_generic = bool(manifest.get("allow_generic_records", False))

    for i, op in enumerate(ops):
        op_type = op.get("type")
        if not op_type:
            issues.append({"file": "manifest.yaml", "line": 0,
                           "msg": f"operation {i}: missing 'type'"})
            continue
        if op_type not in DISPATCH:
            issues.append({"file": "manifest.yaml", "line": 0,
                           "msg": f"operation {i}: unknown type '{op_type}'. "
                                  f"Known: {sorted(DISPATCH)}"})
            continue
        if op_type in GENERIC_OPS and not allow_generic:
            issues.append({"file": "manifest.yaml", "line": 0,
                           "msg": f"operation {i}: '{op_type}' requires "
                                  f"`allow_generic_records: true` in manifest"})

        for blk_msg in _check_blocklist(op, blocklist):
            issues.append({"file": "manifest.yaml", "line": 0,
                           "msg": f"operation {i}: {blk_msg}"})

        for ref_key in ("arch_file", "code_file"):
            if op.get(ref_key):
                ref_path = cdir / op[ref_key]
                if not ref_path.is_file():
                    issues.append({"file": op[ref_key], "line": 0,
                                   "msg": f"referenced file not found"})
                    continue
                if ref_key == "arch_file":
                    try:
                        ET.fromstring(ref_path.read_text())
                    except ET.ParseError as exc:
                        issues.append({"file": op[ref_key], "line": exc.position[0] if exc.position else 0,
                                       "msg": f"XML parse error: {exc}"})
                elif ref_key == "code_file":
                    issues.extend(_check_python_body(ref_path.read_text(), op[ref_key]))

    sha = changeset_sha256(cdir)
    structured["manifest_sha256"] = sha
    structured["operation_count"] = len(ops)

    return _write_reports(paths, changeset_id, structured, issues, ok=not issues)


def _write_reports(paths: Paths, changeset_id: str,
                   structured: dict, issues: list[dict], ok: bool) -> int:
    structured["errors"] = issues
    structured["status"] = "ok" if ok else "failed"

    val_path = paths.report_file("validation", f"{changeset_id}.json")
    val_path.parent.mkdir(parents=True, exist_ok=True)
    val_path.write_text(json.dumps(structured, indent=2, sort_keys=True) + "\n")

    fb_path = paths.report_file("ai_feedback", f"{changeset_id}.md")
    fb_path.parent.mkdir(parents=True, exist_ok=True)
    if ok:
        fb_path.write_text(f"# Validation OK — {changeset_id}\n\n"
                           f"All static checks passed. sha256={structured.get('manifest_sha256')}\n")
        print(f"[validate] OK ({structured.get('operation_count')} ops, sha={structured.get('manifest_sha256','')[:12]}...)")
        return 0

    lines = [f"# Validation FAILED — {changeset_id}\n",
             f"\n## Errors ({len(issues)})\n"]
    for i, iss in enumerate(issues, 1):
        loc = f"`{iss['file']}`"
        if iss.get("line"):
            loc += f":L{iss['line']}"
        lines.append(f"\n{i}. {loc}\n   {iss['msg']}")
    lines.append("\n\n## Required Fix\n\n"
                 f"Edit only `changesets/{changeset_id}/`. "
                 f"Do not edit deployer code or workflows.\n")
    fb_path.write_text("".join(lines))

    for iss in issues:
        line_part = f",line={iss['line']}" if iss.get("line") else ""
        print(f"::error file=changesets/{changeset_id}/{iss['file']}{line_part}::{iss['msg']}")
    print(f"[validate] FAILED — {len(issues)} issue(s); see {fb_path.relative_to(paths.instance_root)}")
    return 1
