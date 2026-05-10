"""Content fingerprint for a changeset folder.

The sha256 covers manifest.yaml + every referenced content file (XML,
Python, YAML, JSON, CSV) with stable ordering. README.md is included
so doc-only edits also bump the hash, keeping the audit honest.

Per the architecture doc, audit files and report files MUST NOT
contribute to the hash — they're outputs of deployment, not inputs.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

# Files inside a changeset folder that don't count toward content hash.
# (Currently empty — README.md is intentionally included so a description
# tweak forces a re-promote; if you want README ignored, add it here.)
EXCLUDED_NAMES = set()


def changeset_sha256(changeset_dir: Path) -> str:
    """Return the deterministic content hash of a changeset folder.

    Walks the folder in sorted order. For each file: hash its relative
    path then its bytes. This makes renames and reorders detectable.
    """
    if not changeset_dir.is_dir():
        raise FileNotFoundError(f"changeset folder not found: {changeset_dir}")
    h = hashlib.sha256()
    for path in sorted(changeset_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.name in EXCLUDED_NAMES:
            continue
        rel = path.relative_to(changeset_dir).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()
