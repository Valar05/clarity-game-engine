from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .storage import Store, canonical_json, sha256_bytes


class VerificationError(RuntimeError):
    pass


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        dirfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def execute_local(store: Store, mission: dict[str, Any]) -> dict[str, Any]:
    kind = mission["kind"]
    spec = json.loads(mission["spec_json"])
    mission_id = mission["id"]

    if kind != "world.build_slice":
        raise VerificationError(f"unsupported local mission kind: {kind}")

    logical_name = str(spec.get("logical_name", "")).strip()
    content = spec.get("content")
    expected_sha = spec.get("expected_sha256")
    if not logical_name or not isinstance(content, dict):
        raise VerificationError("world.build_slice requires logical_name and object content")

    data = (canonical_json(content) + "\n").encode("utf-8")
    digest = sha256_bytes(data)
    if expected_sha and digest != expected_sha:
        raise VerificationError(f"content hash mismatch: expected {expected_sha}, got {digest}")

    quarantine = store.paths.quarantine / mission_id / logical_name
    _atomic_write(quarantine, data)

    # Readback is mandatory before promotion.
    readback = quarantine.read_bytes()
    if readback != data or sha256_bytes(readback) != digest:
        raise VerificationError("quarantine readback mismatch")

    promoted = store.paths.artifacts / digest[:2] / digest / logical_name
    _atomic_write(promoted, readback)
    promoted_readback = promoted.read_bytes()
    if sha256_bytes(promoted_readback) != digest:
        raise VerificationError("promoted artifact readback mismatch")

    rel = str(promoted.relative_to(store.paths.root))
    with store.conn:
        store.conn.execute(
            "INSERT OR IGNORE INTO artifacts(sha256,mission_id,logical_name,relative_path,byte_count,promoted_ms) VALUES(?,?,?,?,?,?)",
            (digest, mission_id, logical_name, rel, len(data), __import__("time").time_ns() // 1_000_000),
        )
    return {"sha256": digest, "logical_name": logical_name, "relative_path": rel, "byte_count": len(data)}
