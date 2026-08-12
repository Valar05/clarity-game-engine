from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path

from .executor import VerificationError, execute_local
from .storage import Store, canonical_json, paths


def _json(value):
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def cmd_init(args) -> int:
    s = Store(args.root)
    try:
        s.append_event("system.initialized", {"schema_version": 1, "host": socket.gethostname()})
        s.checkpoint()
        _json({"ok": True, "root": str(s.paths.root), "db": str(s.paths.db)})
        return 0
    finally:
        s.close()


def cmd_status(args) -> int:
    s = Store(args.root)
    try:
        integrity_ok, integrity = s.integrity_check()
        chain_ok, chain = s.verify_chain()
        missions = s.rows_as_dicts(s.list_missions())
        _json({
            "ok": integrity_ok and chain_ok,
            "root": str(s.paths.root),
            "sqlite_integrity": integrity,
            "event_chain": chain,
            "missions": missions,
        })
        return 0 if integrity_ok and chain_ok else 2
    finally:
        s.close()


def cmd_add(args) -> int:
    s = Store(args.root)
    try:
        spec = json.loads(Path(args.spec).read_text("utf-8"))
        mission_id = s.add_mission(args.kind, spec, args.idempotency_key)
        _json({"ok": True, "mission_id": mission_id})
        return 0
    finally:
        s.close()


def cmd_list(args) -> int:
    s = Store(args.root)
    try:
        _json(s.rows_as_dicts(s.list_missions()))
        return 0
    finally:
        s.close()


def cmd_run(args) -> int:
    s = Store(args.root)
    owner = args.owner or f"{socket.gethostname()}:{os.getpid()}"
    try:
        s.recover_expired()
        row = s.lease_next(owner, args.lease_ms)
        if not row:
            _json({"ok": True, "worked": False, "reason": "queue_empty"})
            return 0
        mission = dict(row)
        try:
            result = execute_local(s, mission)
            s.transition(mission["id"], "promoted", payload=result)
            _json({"ok": True, "worked": True, "mission_id": mission["id"], "result": result})
            return 0
        except VerificationError as exc:
            s.transition(mission["id"], "rejected", error=str(exc))
            _json({"ok": False, "worked": True, "mission_id": mission["id"], "error": str(exc)})
            return 3
        except BaseException as exc:
            # Do not terminally poison an unknown crash. Lease expiry/recover owns retry semantics.
            s.append_event("mission.worker_crash", {"owner": owner, "error": repr(exc)}, mission["id"])
            raise
    finally:
        s.close()


def cmd_recover(args) -> int:
    s = Store(args.root)
    try:
        count = s.recover_expired()
        integrity_ok, integrity = s.integrity_check()
        chain_ok, chain = s.verify_chain()
        s.checkpoint()
        _json({"ok": integrity_ok and chain_ok, "recovered": count, "sqlite_integrity": integrity, "event_chain": chain})
        return 0 if integrity_ok and chain_ok else 2
    finally:
        s.close()


def cmd_verify(args) -> int:
    s = Store(args.root)
    try:
        integrity_ok, integrity = s.integrity_check()
        chain_ok, chain = s.verify_chain()
        missing = []
        bad_hash = []
        for row in s.conn.execute("SELECT * FROM artifacts"):
            p = s.paths.root / row["relative_path"]
            if not p.exists():
                missing.append(row["relative_path"])
                continue
            from .storage import sha256_bytes
            if sha256_bytes(p.read_bytes()) != row["sha256"]:
                bad_hash.append(row["relative_path"])
        ok = integrity_ok and chain_ok and not missing and not bad_hash
        _json({"ok": ok, "sqlite_integrity": integrity, "event_chain": chain, "missing_artifacts": missing, "bad_artifact_hashes": bad_hash})
        return 0 if ok else 2
    finally:
        s.close()


def cmd_doctor(args) -> int:
    p = paths(args.root)
    checks = {
        "python": sys.version.split()[0],
        "root_parent_exists": p.root.parent.exists(),
        "root_parent_writable": os.access(p.root.parent, os.W_OK),
        "platform": sys.platform,
        "termux": bool(os.environ.get("PREFIX", "").endswith("/usr")) and "com.termux" in os.environ.get("PREFIX", ""),
    }
    _json({"ok": bool(checks["root_parent_exists"] and checks["root_parent_writable"]), "checks": checks})
    return 0 if checks["root_parent_exists"] and checks["root_parent_writable"] else 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="clarity")
    p.add_argument("--root", help="runtime root; defaults to CLARITY_HOME or ~/.clarity")
    sub = p.add_subparsers(dest="command", required=True)

    x = sub.add_parser("init")
    x.set_defaults(func=cmd_init)
    x = sub.add_parser("status")
    x.set_defaults(func=cmd_status)
    x = sub.add_parser("doctor")
    x.set_defaults(func=cmd_doctor)
    x = sub.add_parser("recover")
    x.set_defaults(func=cmd_recover)
    x = sub.add_parser("verify")
    x.set_defaults(func=cmd_verify)

    mission = sub.add_parser("mission")
    msub = mission.add_subparsers(dest="mission_command", required=True)
    x = msub.add_parser("add")
    x.add_argument("--kind", required=True)
    x.add_argument("--spec", required=True)
    x.add_argument("--idempotency-key")
    x.set_defaults(func=cmd_add)
    x = msub.add_parser("list")
    x.set_defaults(func=cmd_list)
    x = msub.add_parser("run")
    x.add_argument("--once", action="store_true", help="reserved: current runner always executes at most one mission")
    x.add_argument("--owner")
    x.add_argument("--lease-ms", type=int, default=120000)
    x.set_defaults(func=cmd_run)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
