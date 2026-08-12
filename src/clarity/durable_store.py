from __future__ import annotations

import json
import os
from pathlib import Path

from .storage import Store, canonical_json


class DurableStore(Store):
    """Store with append-only receipt projection instead of full-ledger rewrites.

    SQLite remains authoritative. The JSONL receipt stream is a projection that can
    be rebuilt from the verified SQLite event chain after an interrupted append.
    """

    def _last_receipt(self):
        path = self.paths.receipts
        if not path.exists() or path.stat().st_size == 0:
            return None
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            pos = f.tell()
            buf = bytearray()
            while pos > 0:
                pos -= 1
                f.seek(pos)
                b = f.read(1)
                if b == b"\n" and buf:
                    break
                if b != b"\n":
                    buf.extend(b)
            line = bytes(reversed(buf))
        if not line:
            return None
        try:
            return json.loads(line.decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(f"receipt projection tail is corrupt: {exc}") from exc

    def _sync_receipts(self):
        last = self._last_receipt()
        if last is None:
            rows = list(self.conn.execute("SELECT * FROM events ORDER BY seq"))
            if not rows:
                return
            start_seq = 0
        else:
            event_hash = last.get("event_hash")
            if not event_hash:
                raise RuntimeError("receipt projection tail lacks event_hash")
            row = self.conn.execute("SELECT * FROM events WHERE event_hash=?", (event_hash,)).fetchone()
            if not row:
                raise RuntimeError("receipt projection tail is not present in authoritative event ledger")
            canonical_tail = canonical_json(self._receipt_from_row(row))
            if canonical_json(last) != canonical_tail:
                raise RuntimeError("receipt projection tail diverges from authoritative event ledger")
            start_seq = row["seq"]
            rows = list(self.conn.execute("SELECT * FROM events WHERE seq>? ORDER BY seq", (start_seq,)))
            if not rows:
                return

        fd = os.open(self.paths.receipts, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            for row in rows:
                os.write(fd, (canonical_json(self._receipt_from_row(row)) + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
