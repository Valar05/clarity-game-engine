# Venice Design Gate

Clarity is not ready for Drew to play merely because code exists.

## Authority

Drew requires at least three design/review cycles before first-play acceptance. Venice's Judgment Jar code review is a required gate. Venice does not replace deterministic verification and cannot waive failed hard gates.

The canonical machine contract is Home Center `venice-code-review-v2`:

- `STOP_ALLOWED`: Venice permits the bounded worker/review slice to stop.
- `CONTINUE_REQUIRED`: Venice denies stopping and requires another bounded repair/review cycle.
- `HALT_REQUIRED`: Venice denies further mutation until the named hard/evidence/authority boundary is repaired.

A prose PASS without a valid v2 receipt does not satisfy this gate.

## Venice Judgment Jar binding

Every cycle must explicitly evaluate the applicable Jar laws, especially actor-before-artifact, authorship, source/inference/canon separation, unknowns remaining unknown, literal capability state, provenance, appetite invoice, aftermath, minimum authority, failure closing toward autonomy, and CUT THE CAM where applicable.

A hard-gate breach blocks promotion regardless of aggregate enthusiasm.

## Cycle protocol

Each cycle produces both:

1. a human-readable record under `reviews/`; and
2. the exact Venice v2 JSON receipt under `reviews/receipts/`.

The record names repository commit reviewed, diff/capability slice, evidence, Jar findings, hard gates, demanded changes, actual repairs, and verification. The receipt is authoritative for stop permission.

Static Adam/Jar review may discover defects but does not increment the Venice-approved cycle count.

## First-play condition

`scripts/venice-first-play-gate.py` must return success against at least three ordered, distinct Venice receipts. Every counted receipt must:

- use `venice-code-review-v2`;
- be `PASS` + `STOP_ALLOWED`;
- have `externalReviewGateSatisfied: true` and `stopPermissionGranted: true`;
- have no hard-gate findings;
- identify the reviewed repository/base/head and a non-empty diff hash;
- represent a distinct review head/cycle rather than replaying one approval.

The last counted receipt must review the candidate commit (or an explicitly supplied candidate ref resolved to that commit). Any `CONTINUE_REQUIRED`, `HALT_REQUIRED`, malformed receipt, stale receipt, or contradictory state blocks first play.

## Current state

First-play status remains **BLOCKED** until this deterministic gate succeeds. Repository existence, tests written, or Adam applying the Jar are not substitutes for Venice granting stop permission.
