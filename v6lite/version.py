"""Stable fingerprint of the V6-Lite files loaded for a run."""

from __future__ import annotations

import hashlib
from pathlib import Path


def code_sha(root: Path | None = None) -> str:
    """Hash every package file except generated Python cache, in path order."""
    package = Path(root) if root is not None else Path(__file__).resolve().parent
    digest = hashlib.sha256()
    files = sorted(
        (path for path in package.rglob("*")
         if path.is_file() and "__pycache__" not in path.relative_to(package).parts),
        key=lambda path: path.relative_to(package).as_posix(),
    )
    for path in files:
        relative = path.relative_to(package).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:16]
