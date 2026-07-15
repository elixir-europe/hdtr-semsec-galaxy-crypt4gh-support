# Crypt4GH Output Enforcement Spec (Phase-2 Addendum)

Date: 2026-07-14
Status: Approved by user in-session; intended as an addendum that clarifies and tightens output-encryption behavior for Phase 2.

## Purpose

Define a fail-closed output-encryption contract that removes gaps between Crypt4GH-specific output handling and core Galaxy dataset discovery/persistence behavior.

## Prior artifacts and where this addendum differs

Primary prior artifact:

- `crypt4gh-phase-2-implementation-plan.md`

Additional prior architecture artifact:

- `crypt4gh-phase-2-reimplementation-design.md`

Related context artifact:

- `docs/extended-metadata-history-running-issue.md` (general history-state fix; not Crypt4GH-specific and not changed by this addendum)

## Precedence and supersession (normative)

For output-enforcement semantics, this addendum is authoritative.

If any output-enforcement statement in either of the following artifacts conflicts with this addendum, this addendum overrides that conflicting statement:

- `crypt4gh-phase-2-implementation-plan.md`
- `crypt4gh-phase-2-reimplementation-design.md`

Non-output-enforcement content in those artifacts remains in effect unless separately superseded.

### Differences from the previous plan (normative)

1. **Discovered outputs selection model is tightened**
   - Prior plan language (Task 7) allowed implementation detail flexibility for discovered outputs.
   - This addendum requires discovered-output encryption coverage to follow **core persisted dataset payload selection**, not a Crypt4GH-only collector subset.

2. **Collector compatibility requirement is widened**
   - Prior implementation trend used pattern-centric collector logic in Crypt4GH finalization.
   - This addendum requires support aligned with core discovery behavior, including non-pattern discovery paths that produce persisted datasets.

3. **`extra_files` sensitivity is elevated from implied to explicit MUST**
   - Prior plan did not define strict handling of `extra_files` payload confidentiality.
   - This addendum requires `extra_files` payload encryption for Crypt4GH jobs.

4. **Fail-closed output gate is made explicit and universal**
   - Prior plan had fail-closed behavior in parts (TTL/finalization/cleanup), but no single universal pre-success invariant over all persisted output payloads.
   - This addendum requires a pre-success verifier over every persisted payload candidate.

5. **Plaintext allow-list is explicit**
   - Prior plan did not enumerate a strict allow-list.
   - This addendum explicitly allows plaintext only for framework/control artifacts and (for now) stdout/stderr.

### Differences/clarifications relative to the reimplementation design (normative)

1. **Design-level intent becomes enforceable output invariant**
   - Reimplementation design states plaintext should exist only in compute-local, job-scoped workspace and final exported datasets return encrypted-at-rest.
   - This addendum makes that enforceable through a fail-closed verifier over persisted output payloads.

2. **Output selection is concretized as dataset-payload-centric**
   - Reimplementation design describes selected Galaxy-imported outputs and discovered outputs in scope.
   - This addendum requires selection semantics aligned with core persisted payload mapping, not collector-subtype-limited Crypt4GH-specific traversal.

3. **`extra_files` confidentiality is elevated from implicit to explicit MUST**
   - Reimplementation design does not define concrete `extra_files` enforcement semantics.
   - This addendum requires `extra_files` payload encryption for Crypt4GH jobs.

## Path model and why `/outputs` is optional

This repository’s job path model is execution-mode dependent. `/outputs` exists in some modes, but not as a universal contract root.

Relevant path categories:

- `false_path`: job-visible output path during tool execution
- `real_path`: canonical persisted dataset path
- discovered output directories: collector/tool-dependent locations
- framework/control paths (e.g., `metadata/**`, tool scripts, control artifacts)
- Crypt4GH staging roots (`_c4gh_stage/**`, `_crypt/**`)

Because `false_path`/`real_path` and discovery paths vary by runner/config/tool wiring, directory-only selection (e.g., “encrypt everything under `/outputs`”) is insufficient and can miss persisted payloads or encrypt framework files that must remain readable.

## Definition: Crypt4GH jobs (normative)

In this addendum, a **Crypt4GH job** is any job where input evaluation triggers Crypt4GH runtime handling (for example: at least one input dataset carries Crypt4GH metadata/header context that causes the runtime helper path to activate).

For such jobs, **all Galaxy-imported persisted dataset payloads produced by that job are in scope** for output-enforcement checks in this addendum.

## Security invariant (normative)

For Crypt4GH jobs, final success is permitted **only if all persisted output payloads are encrypted**.

Persisted output payloads include:

1. Primary dataset files for all persisted output dataset instances (declared + discovered)
2. `extra_files` payload files associated with those datasets

If any persisted payload lacks encryption evidence at pre-success verification time, job result MUST fail (ERROR/fail-closed).

## Discovery-route hook and de-duplication intent (normative)

This addendum requires a hook at the discovery/persistence route for discovered outputs.

Required intent:

1. Reuse core discovery’s own matched/persisted payload candidate set.
2. Encrypt discovered payload candidates before persistence success is finalized (not after DB-commit success handling).
3. Keep the existing declared-output hook for non-discovery outputs.
4. Unify both routes at the same "about-to-persist dataset payload" enforcement boundary.

De-duplication objective:

- Crypt4GH output enforcement must not maintain a narrower, parallel discovered-output selector that can diverge from core discovery behavior.
- Selection semantics for discovered outputs MUST be sourced from the same core discovery/persistence mapping used to decide persisted dataset payloads.

## Encryption scope and plaintext allow-list

### Must be encrypted

- persisted primary output payload files
- persisted discovered output payload files
- persisted `extra_files` payload files

### Allowed plaintext (current phase)

Allowed plaintext is limited to files that are **not imported/persisted as dataset payloads**.

Concrete allowed examples:

- metadata/control artifacts used by orchestration/import (for example `metadata/params.json`, `metadata/metadata_kwds_*`, `metadata/metadata_out_*`, `metadata/metadata_results_*`)
- tool script/control artifacts (for example `tool_script.sh`, exit-code/control files)
- `stdout/stderr` streams (deferred for separate secure-log design)

If a file is selected for persistence as dataset payload content (primary dataset payload or `extra_files` payload), it is out of this plaintext allow-list even if located near control paths.

## Enforcement flow (normative)

1. Build candidate payload set from core output/discovery/persistence mapping (dataset-centric, not directory-centric).
2. For discovered outputs, apply encryption through the discovery/persistence hook before persistence success is finalized.
3. For non-discovery outputs, retain declared-output hook behavior and apply the same enforcement boundary.
4. Encrypt each candidate payload before persistence success is finalized.
5. Emit marker/manifest evidence for each encrypted payload.
6. Run a pre-success fail-closed verifier against persisted payload set.
7. If verifier fails, force job failure and preserve diagnostics.

## Evidence model

Minimum evidence requirements:

- dataset payload marker evidence (existing marker approach can be reused/extended)
- discovered designation/path mapping evidence where applicable
- `extra_files` manifest evidence listing encrypted payload files and linkage to owning dataset

Exact filenames/format can evolve, but verifier semantics (completeness over persisted payload set) are mandatory.

## `extra_files` handling

### Current required behavior

- Treat `extra_files` payloads as sensitive.
- Encrypt payload files individually in this phase.

### Deferred optimization (future work)

- Evaluate packed/indexed representations (e.g., SquashFS-like or equivalent) for selective access efficiency with Crypt4GH wrapping.
- This optimization is not required for current enforcement correctness.

## Failure semantics

If any required payload is plaintext or lacks evidence:

- final job state MUST be error/fail-closed
- diagnostics MUST identify missing/invalid evidence class
- cleanup/failure handling MUST preserve original error context where possible

## Test contract updates (minimum)

1. Declared outputs: all persisted payloads encrypted
2. Discovered outputs: encryption coverage for non-pattern discovery paths that persist datasets
3. `extra_files`: encryption + manifest completeness
4. Fail-closed verifier: any missing evidence forces ERROR
5. Dataset-centric selection proof: encryption target selection follows persisted dataset mapping rather than directory assumptions (path-model divergence case, e.g. `false_path` vs `real_path`, and/or persisted discovered output outside `/outputs`)
6. Plaintext allow-list: framework/control files remain readable; output payload policy still enforced

## Planner alignment note (required follow-up)

At the time of this addendum, `crypt4gh-phase-2-implementation-plan.md` task/test text is not yet fully aligned with this enforcement scope.

Required follow-up planner edits must explicitly cover:

- `extra_files` payload encryption requirements
- universal pre-success fail-closed verification over persisted payloads
- dataset-centric proof cases (including path-model divergence)

## User check-in marker

This addendum is intended as the authoritative clarification for output-enforcement semantics before any further planner updates.

Follow-up requested by user:

- Launch new brainstormer/planner sessions later to edit earlier spec/plan text and explicitly mark superseded assumptions as no longer in effect.
