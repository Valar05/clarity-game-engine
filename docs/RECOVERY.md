# Recovery Contract

Clarity is designed to survive process death, lost network, worker disappearance, duplicate commands, partial writes, provider outages, device restarts, and user curve balls without inventing success.

## Failure model

The runtime assumes any instruction can be interrupted after any durable boundary. Therefore every mutation must be one of:

1. atomic and idempotent;
2. journaled before external execution;
3. quarantined until verified;
4. recoverable from an expired lease; or
5. terminal only after evidence is durable.

## Mission state

```text
queued -> working -> promoted
                  -> rejected
queued/working -> cancelled
working --lease expiry--> queued
```

Unknown worker/process death is **not** rejection. The lease expires and recovery returns the mission to `queued`.

Terminal states never silently reopen. A retry that must supersede a terminal mission is a new mission linked by higher-level provenance.

## Two independent evidence surfaces

### SQLite/WAL

Authoritative runtime state. Settings:

- WAL journal mode
- `synchronous=FULL`
- foreign keys enabled
- busy timeout enabled
- explicit integrity checks

### `receipts.jsonl`

Append-only, canonical-JSON receipts. Every event includes:

- unique event ID
- mission ID where applicable
- event type
- payload
- timestamp
- previous event hash
- current event hash

Each acknowledged receipt is fsynced. The chain detects database event tampering and gives an independent human-readable trail.

The JSONL log is not concurrently editable state. It is evidence.

## Artifact promotion

Worker output never lands directly in the promoted artifact tree.

```text
worker/local output
  -> quarantine/<mission>/...
  -> byte readback
  -> SHA-256 verification
  -> atomic content-addressed write
  -> promoted readback
  -> artifact database row
  -> mission promoted event
```

A process crash before the final state transition leaves a recoverable lease. Existing content-addressed artifacts are safe to rediscover because promotion is idempotent by hash.

## Startup recovery

`clarity recover` performs only deterministic recovery work:

1. open SQLite with WAL;
2. recover expired `working` leases back to `queued`;
3. verify SQLite integrity;
4. verify the event hash chain;
5. checkpoint WAL;
6. report, never conceal, corruption.

Later recovery slices may add snapshot restore and artifact-index rebuilding. They may not replace the original evidence in place.

## Curve-ball fixtures required before 1.0

- SIGKILL before lease acquisition
- SIGKILL immediately after lease acquisition
- SIGKILL after quarantine write
- SIGKILL after promoted artifact write but before state promotion
- duplicate enqueue with identical idempotency key
- duplicate worker completion
- stale worker returns after lease reassignment
- disk full during receipt append
- disk full during artifact promotion
- SQLite WAL present at reboot
- corrupt/tampered event payload
- missing promoted artifact
- promoted artifact hash mismatch
- clock moves backward/forward
- provider disappears mid-request
- provider returns malformed output
- network disappears for hours
- phone reboot during active mission
- old binary opens newer schema
- newer binary migrates older schema
- cloud copy is stale or contradictory
- user edits/deletes files manually

Every fixture must end in one of three outcomes: safely resumed, safely rejected/quarantined, or loudly blocked. Never ambiguous success.

## Authority

The phone runtime owns mission continuity. Cloud copies, Git repositories, models, chat sessions, desktop workers, and web UIs are replicas, workers, or distribution surfaces. None may promote a mission solely by claiming completion.
