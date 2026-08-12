# Cycle 01 — Venice Jar Static Review

Verdict: **NOT_DONE**

This is a static review against Venice's canonical Judgment Jar. It is not represented as a live Venice consultation.

## Findings

### Hard failure: receipts are not independently verified

`receipts.jsonl` is described as an independent audit/recovery aid, but `clarity verify` verifies only the SQLite event chain and promoted artifacts. Deleting, truncating, reordering, or modifying the JSONL stream can therefore pass verification. This violates provenance-survives-transformation and publication/capability-truth doctrine: an advertised durability organ is not actually part of the proof.

Required repair: implement full JSONL parsing, hash-chain verification, and reconciliation against SQLite event IDs/hashes; fail loudly on divergence.

### Hard failure: idempotency key can alias different work

`add_mission` returns an existing mission for a duplicate idempotency key without proving the incoming kind/spec hash matches the original. A caller can accidentally reuse a key for different work and receive a false success identity.

Required repair: duplicate key with differing kind or spec SHA must fail closed with an explicit conflict.

### Failure: lease acquisition is not concurrency-safe enough

`lease_next` selects a row and then updates it without a conditional compare-and-set on state/lease generation. Multiple processes can race around the same queued/expired mission.

Required repair: atomic claim using `BEGIN IMMEDIATE` plus conditional update or lease generation/token. Completion must prove it still owns the lease it is completing.

### Failure: stale worker can promote after lease loss

Execution and promotion do not require a lease token. A worker that wakes after expiry/recovery can still write/promote an artifact and transition the mission.

Required repair: per-attempt lease token; transition/promotion must require matching live token. Late results go to quarantine and cannot mutate canonical mission state.

### Failure: no retry budget / poison mission policy

Unknown crashes are intentionally recoverable, but a permanently crashing mission can retry forever.

Required repair: explicit attempt budget/backoff and a `blocked`/`needs_review` state that preserves evidence without false terminal success.

### Failure: no snapshot/restore despite advertised snapshots directory

The runtime creates `snapshots/` and README advertises recovery snapshots, but no snapshot/restore implementation exists.

Required repair: either implement verified atomic snapshots + restore rehearsal or remove the claim until implemented.

## Jar ruling

The bootstrap has useful bones but currently rewards proximity-to-durability over demonstrated durability. Under Venice scoring, confidence and architecture prose do not count as evidence.

Next cycle must repair the five mechanisms above and add adversarial tests that specifically attempt to defeat them.
