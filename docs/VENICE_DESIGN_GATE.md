# Venice Design Gate

Clarity is not ready for Drew to play merely because code exists.

## Authority

Drew requires at least three design/review cycles before first-play acceptance. Venice's Judgment Jar code review is a required gate. Venice does not replace deterministic verification and cannot waive failed hard gates.

Valid Venice review modes are:

- `local_venice` — the deployed/local Venice runtime performs the review;
- `judgment_jar_emulation` — Adam or another authorized worker explicitly puts on the Venice hat and applies Venice's durable Judgment Jars and stopping doctrine.

Drew explicitly authorized `judgment_jar_emulation` as doctrine on 2026-08-12. It counts as Venice judgment for code review and stop/continue decisions, provided provenance names the actual reviewer and mode. It may not masquerade as local Venice output.

The machine contract remains:

- `STOP_ALLOWED`: Venice permits the bounded worker/review slice to stop.
- `CONTINUE_REQUIRED`: Venice denies stopping and requires another bounded repair/review cycle.
- `HALT_REQUIRED`: Venice denies further mutation until the named hard/evidence/authority boundary is repaired.

## Venice Judgment Jar binding

Every cycle must explicitly evaluate applicable Jar laws, especially actor-before-artifact, source/inference/canon separation, unknowns remaining unknown, literal capability state, provenance, appetite invoice, aftermath, minimum authority, failure closing toward autonomy, and CUT THE CAM where applicable.

A hard-gate breach blocks promotion regardless of aggregate enthusiasm.

## Cycle protocol

Each cycle produces a human-readable record under `reviews/` and, when the review is machine-promoted, a JSON receipt under `reviews/receipts/`.

The record names repository commit reviewed, diff/capability slice, evidence, reviewer identity/mode, Jar findings, hard gates, demanded changes, actual repairs, and verification.

## First-play condition

`scripts/venice-first-play-gate.py` must return success against at least three ordered, distinct Venice approvals. Every counted approval must:

- use an authorized Venice reviewer mode;
- be `PASS` + `STOP_ALLOWED`;
- have no hard-gate findings;
- identify the reviewed repository/base/head and a non-empty diff hash;
- represent a distinct review head/cycle rather than replaying one approval;
- preserve reviewer provenance rather than claiming a different reviewer mode.

The last counted approval must review the candidate commit. Any `CONTINUE_REQUIRED`, `HALT_REQUIRED`, malformed approval, stale approval, contradictory state, or missing required deterministic evidence blocks first play.

## Current state

First-play status remains **BLOCKED** until the required review cycles and runtime evidence are complete. Judgment Jar emulation now counts as Venice review; it does not waive missing tests or device/runtime proof.
