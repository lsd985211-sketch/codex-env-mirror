#!/usr/bin/env python3
"""Private durable artifacts for large read-only Hub MCP results.

Ownership: exact result persistence, content identity, and private local access.
Non-goals: MCP execution, permission changes, result interpretation, projection,
or durable memory.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


SCHEMA = "local_mcp_hub.result_artifact.v1"
DEFAULT_ROOT = Path.home() / ".codex-app" / "runtime" / "mcp-result-artifacts"


def artifact_root() -> Path:
    return Path(os.environ.get("CODEX_MCP_RESULT_ARTIFACT_ROOT", str(DEFAULT_ROOT))).expanduser().resolve()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def payload_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def persist_result(result: Any, *, profile: str, tool: str) -> dict[str, Any]:
    """Persist one exact result under its canonical content hash."""

    root = artifact_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    source_sha256 = payload_sha256(result)
    provenance_sha256 = hashlib.sha256(
        _canonical_bytes({"profile": str(profile), "tool": str(tool)})
    ).hexdigest()
    provenance_directory = root / provenance_sha256[:16]
    provenance_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(provenance_directory, 0o700)
    directory = provenance_directory / source_sha256[:2]
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    path = directory / f"{source_sha256}.json"
    wrapper = {
        "schema": SCHEMA,
        "profile": str(profile),
        "tool": str(tool),
        "source_sha256": source_sha256,
        "result": result,
    }
    reused = path.is_file()
    if not reused:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{source_sha256}.", suffix=".tmp", dir=directory)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(wrapper, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            try:
                os.link(temporary, path)
            except FileExistsError:
                reused = True
        finally:
            temporary.unlink(missing_ok=True)
    os.chmod(path, 0o600)
    return {
        "schema": f"{SCHEMA}.receipt",
        "ok": True,
        "artifact_ref": f"artifact:{path}",
        "artifact_path": str(path),
        "source_sha256": source_sha256,
        "provenance_sha256": provenance_sha256,
        "reused": reused,
    }
