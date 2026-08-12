# Cycle 02 — Venice Jar Static Review

Verdict: **NOT_DONE**

Again: this is a static application of Venice's canonical Judgment Jar, not live local Venice speech.

## Repairs made from Cycle 01

- idempotency keys now reject different work rather than aliasing it;
- lease claims use an immediate transaction plus conditional update;
- every lease has a unique token;
- execution and terminal transition require the live token;
- stale worker output is blocked before canonical promotion;
- receipt JSONL is hash-verified and reconciled event-for-event against SQLite;
- adversarial fixtures now attack receipt deletion/modification and stale-worker promotion.

## Remaining blockers

### Retry poison remains unresolved

A permanently crashing mission can cycle forever. Add bounded retry policy with explicit `blocked` state and manual retry/requeue semantics. Failure must close toward autonomy, not battery drain.

### Snapshot claim remains unimplemented

`snapshots/` is still a directory rather than a recovery mechanism. Either implement atomic verified snapshot/export + restore or remove the claim from public doctrine.

### Artifact promotion has a narrow stale-lease race

The executor checks the lease before and after writing the content-addressed artifact, but expiry can occur during the atomic write. The artifact is immutable/content-addressed, so this does not corrupt a different artifact, but a stale worker can leave an unreferenced promoted blob. Canonical DB state remains protected. Required improvement: treat artifact store as immutable blob cache and add garbage collection/reconciliation; only DB reference constitutes promotion.

### Receipt dual-write crash window is acknowledged but not healed

SQLite commit occurs before JSONL fsync. Process death in that window causes a deliberate verification failure, which is safer than false success, but recovery cannot reconstruct the missing audit line. Add deterministic receipt reconciliation/rebuild from the authoritative SQLite event ledger, producing a separate repair receipt rather than silently rewriting history.

## Jar ruling

Cycle 2 is materially stronger. It still does not earn first play. Durability means not merely detecting damage but providing a bounded, auditable return path.
