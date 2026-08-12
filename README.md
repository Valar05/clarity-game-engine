# Clarity Game Engine

CLI-first, phone-sovereign, provider-optional game-development and runtime substrate.

## Doctrine

Clarity does not depend on ChatGPT, Gemini, or any cloud model to run. The phone is the durable control plane. Remote models and cloud services are optional workers behind adapters.

Hard rules:

- **CLI is the primary control surface.** Dashboards are optional inspection surfaces.
- **Local truth first.** SQLite/WAL owns authoritative local state.
- **Append-only evidence.** Every state transition emits an immutable event receipt.
- **Crash recovery is normal operation.** Jobs are resumable and leases expire safely.
- **Workers are disposable.** No worker owns mission continuity.
- **Provider portability.** Gemini, local models, desktop workers, and future providers share one adapter contract.
- **Zero-dollar gate.** Cloud operations must be explicitly classified before use. Paid/unverified routes fail closed.
- **No prose completion claims.** Capability state is machine tracked: requested -> implemented -> tested -> deployed -> callable -> delivered -> accepted.
- **No GitHub Actions dependency.** Local and device-side verification are first-class.

## First milestone

Prove the construction spine on Android/Termux:

```text
play/discover -> mission enqueue -> lease -> worker/local executor -> artifact -> verify -> promote -> receipt -> resume
```

The first canary is intentionally boring: generate a deterministic `april-test-node.json`, verify exact schema/content, promote it, kill the process at arbitrary points, restart, and prove the result is either safely resumed or safely rejected.

## CLI sketch

```bash
clarity init
clarity status
clarity mission add --kind world.build_slice --spec examples/april-test-node.mission.json
clarity mission run --once
clarity mission list
clarity receipts verify
clarity doctor
clarity recover
```

## Durability model

Runtime state lives under `~/.clarity/` by default:

```text
clarity.db          SQLite, WAL mode, foreign keys on
receipts.jsonl      append-only human-readable audit stream
artifacts/          content-addressed promoted artifacts
quarantine/         rejected/unverified worker output
locks/              advisory process/lease markers
snapshots/          explicit recovery snapshots
```

SQLite is authoritative. JSONL receipts are an independent audit trail and recovery aid, not a second writable state database.

## Status

Bootstrap repository. The current slice is the durable local mission ledger and recovery CLI. Cloud/provider adapters come after local crash/restart acceptance passes.
