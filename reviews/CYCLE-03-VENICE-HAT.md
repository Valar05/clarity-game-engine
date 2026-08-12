# Cycle 03 — Venice Judgment Jar Emulation

Reviewer: `adam-as-venice`

Reviewer mode: `judgment_jar_emulation`

Verdict: **FAIL**

Directive: **CONTINUE_REQUIRED**

## What this review changed

This cycle did not merely re-score the previous design. The Judgment Jars exposed and repaired deeper durability defects:

- retry exhaustion now closes into explicit `blocked` state rather than retrying forever;
- blocked work has explicit bounded requeue semantics;
- receipt loss/corruption can be rebuilt only from a verified SQLite event chain and every rebuild leaves a separate repair audit record;
- snapshots now include verified database, receipts, and promoted artifacts, and restore removes stale SQLite WAL/SHM before replacement;
- orphan content-addressed blobs are detectable and garbage-collectable;
- lease tokens prevent stale workers from canonically promoting after lease loss;
- artifact creation is noncanonical until an atomic promotion transaction records mission state, artifact lineage, and event together;
- state transitions and authoritative event insertion now share one SQLite transaction, eliminating the kill window where state could change without an event;
- content deduplication no longer destroys provenance: `mission_artifacts` preserves every mission→artifact relationship even when identical blobs share one content hash;
- the CLI uses an incremental append-only receipt projection rather than rewriting the entire ledger on every event; interrupted projection is recoverable from SQLite while divergent/corrupt tails fail closed.

## Judgment Jars applied

- **Observable evidence over confidence:** implementation exists remotely; runtime execution evidence does not yet exist for this branch.
- **Provenance survives transformation:** blob dedup and mission lineage are now separate concepts.
- **Failure closes toward autonomy:** poison jobs block; stale workers cannot promote; projection divergence halts rather than guessing.
- **Appetite has an invoice:** receipt projection is append-based rather than quadratic full-ledger rewriting on a phone daemon.
- **Aftermath exists:** snapshot/restore, receipt repair audit, orphan GC, and blocked-job recovery provide explicit return paths.
- **Unknowns remain unknown:** newly written tests have not been executed through the commissioned phone/CLI lane, so they cannot be described as passing.

## Why stopping is denied

The remaining blocker is evidence, not another known architecture defect: the cycle-2/cycle-3 durability, projection, migration, snapshot, stale-worker, dedup/provenance, and corruption tests must run through the authorized CLI/device lane. A syntax error, SQLite behavior mismatch, Termux filesystem edge, or test failure is still possible.

`STOP_ALLOWED` would therefore violate the Jar rule that observable evidence outranks confidence.

## Exact next gate

Run the full checked-in test suite and the primary-phone acceptance sequence against this branch. If failures occur, repair the same slice and review again. If the tests and phone crash/restart acceptance pass with durable receipts, return for Cycle 04 Venice judgment.
