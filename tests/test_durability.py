from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from clarity.executor import execute_local
from clarity.storage import Store, canonical_json, sha256_bytes


def spec():
    return {
        "logical_name": "april-test-node.json",
        "content": {
            "schema": "clarity.world-node.v1",
            "id": "april-test-node",
            "offline": True,
            "requires_model": False,
        },
    }


def test_idempotent_enqueue(tmp_path: Path):
    s = Store(tmp_path)
    try:
        a = s.add_mission("world.build_slice", spec(), "same-key")
        b = s.add_mission("world.build_slice", spec(), "same-key")
        assert a == b
        assert len(s.list_missions()) == 1
    finally:
        s.close()


def test_expired_lease_recovers(tmp_path: Path):
    s = Store(tmp_path)
    try:
        mission_id = s.add_mission("world.build_slice", spec())
        leased = s.lease_next("dead-worker", ttl_ms=1)
        assert leased["id"] == mission_id
        time.sleep(0.01)
        assert s.recover_expired() == 1
        assert s.get_mission(mission_id)["state"] == "queued"
    finally:
        s.close()


def test_artifact_promotes_only_after_readback(tmp_path: Path):
    s = Store(tmp_path)
    try:
        mission_id = s.add_mission("world.build_slice", spec())
        mission = dict(s.lease_next("worker"))
        result = execute_local(s, mission)
        s.transition(mission_id, "promoted", payload=result)
        artifact = s.paths.root / result["relative_path"]
        assert artifact.exists()
        assert sha256_bytes(artifact.read_bytes()) == result["sha256"]
        assert s.get_mission(mission_id)["state"] == "promoted"
    finally:
        s.close()


def test_hash_chain_detects_db_tamper(tmp_path: Path):
    s = Store(tmp_path)
    try:
        mission_id = s.add_mission("world.build_slice", spec())
        ok, _ = s.verify_chain()
        assert ok
        with s.conn:
            s.conn.execute("UPDATE events SET payload_json='{}' WHERE mission_id=?", (mission_id,))
        ok, reason = s.verify_chain()
        assert not ok
        assert "event_hash" in reason
    finally:
        s.close()


def test_receipt_stream_exists_and_is_parseable(tmp_path: Path):
    s = Store(tmp_path)
    try:
        s.add_mission("world.build_slice", spec())
        lines = s.paths.receipts.read_text("utf-8").splitlines()
        assert lines
        for line in lines:
            obj = json.loads(line)
            assert obj["event_hash"]
            assert obj["type"]
    finally:
        s.close()


def test_sqlite_integrity_and_chain_survive_reopen(tmp_path: Path):
    s = Store(tmp_path)
    mission_id = s.add_mission("world.build_slice", spec())
    s.checkpoint()
    s.close()

    reopened = Store(tmp_path)
    try:
        assert reopened.get_mission(mission_id) is not None
        assert reopened.integrity_check() == (True, "ok")
        assert reopened.verify_chain() == (True, "ok")
    finally:
        reopened.close()
