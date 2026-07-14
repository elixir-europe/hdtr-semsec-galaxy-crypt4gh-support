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

## Security invariant (normative)

For Crypt4GH jobs, final success is permitted **only if all persisted output payloads are encrypted**.

Persisted output payloads include:

1. Primary dataset files for all persisted output dataset instances (declared + discovered)
2. `extra_files` payload files associated with those datasets

If any persisted payload lacks encryption evidence at pre-success verification time, job result MUST fail (ERROR/fail-closed).

## Encryption scope and plaintext allow-list

### Must be encrypted

- persisted primary output payload files
- persisted discovered output payload files
- persisted `extra_files` payload files

### Allowed plaintext (current phase)

- `metadata/**` and other framework/control files required for orchestration/import
- tool script/control artifacts
- `stdout/stderr` (deferred for separate secure-log design)

## Enforcement flow (normative)

1. Build candidate payload set from core output/discovery/persistence mapping (dataset-centric, not directory-centric).
2. Encrypt each candidate payload before persistence success is finalized.
3. Emit marker/manifest evidence for each encrypted payload.
4. Run a pre-success fail-closed verifier against persisted payload set.
5. If verifier fails, force job failure and preserve diagnostics.

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
5. Plaintext allow-list: framework/control files remain readable; output payload policy still enforced

## User check-in marker

This addendum is intended as the authoritative clarification for output-enforcement semantics before any further planner updates.

Follow-up requested by user:

- Launch new brainstormer/planner sessions later to edit earlier spec/plan text and explicitly mark superseded assumptions as no longer in effect.
