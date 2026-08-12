from pathlib import Path
import json
import pytest
from clarity.durable_store import DurableStore


def spec(v):
    return {"logical_name":f"x-{v}.json","content":{"schema":"x.v1","value":v}}


def test_projection_appends_without_rewriting_history(tmp_path:Path):
    s=DurableStore(tmp_path)
    try:
        s.add_mission("world.build_slice",spec(1))
        before=s.paths.receipts.read_bytes()
        s.add_mission("world.build_slice",spec(2))
        after=s.paths.receipts.read_bytes()
        assert after.startswith(before)
        assert len(after)>len(before)
        assert s.verify_receipts()==(True,"ok")
    finally:s.close()


def test_missing_tail_after_db_commit_is_reconciled_by_next_sync(tmp_path:Path):
    s=DurableStore(tmp_path)
    try:
        s.add_mission("world.build_slice",spec(1))
        lines=s.paths.receipts.read_text().splitlines()
        # Simulate interrupted projection by deleting the last projected line only.
        s.add_mission("world.build_slice",spec(2))
        s.paths.receipts.write_text("\n".join(lines)+"\n")
        assert s.verify_receipts()[0] is False
        s._sync_receipts()
        assert s.verify_receipts()==(True,"ok")
    finally:s.close()


def test_corrupt_projection_tail_fails_closed(tmp_path:Path):
    s=DurableStore(tmp_path)
    try:
        s.add_mission("world.build_slice",spec(1))
        with s.paths.receipts.open("a") as f:f.write('{"event_hash":"not-in-db"}\n')
        with pytest.raises(RuntimeError,match="not present"):
            s._sync_receipts()
    finally:s.close()
