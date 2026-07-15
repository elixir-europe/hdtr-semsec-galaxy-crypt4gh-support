# Crypt4GH Phase 2 Output Enforcement Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align Galaxy's Phase 2 Crypt4GH runtime with the 2026-07-14 output-enforcement addendum so every persisted payload for a Crypt4GH job is encrypted before success, or the job fails closed with diagnostics.

**Architecture:** Reuse Galaxy's core discovery/persistence path as the source of truth for discovered outputs, applying Crypt4GH encryption at that hook before persistence success is finalized. Keep the existing declared-output finalization path only for non-discovery outputs, and unify both routes at the same about-to-persist dataset-payload boundary so the verifier sees one persisted-payload contract with no narrower parallel discovered-output selector.

**Tech Stack:** Galaxy Python runtime, `lib/galaxy/tools/crypt4gh_remote_execution.py`, Galaxy metadata/discovery persistence code, pytest unit tests, Galaxy integration test harness, existing mock compute-side recryptor.

---

## Scope

### In scope

- Implement the output-enforcement addendum in the Galaxy worktree only.
- Replace directory-centric and pattern-only assumptions with dataset-centric persisted-payload selection.
- Route discovered-output enforcement through Galaxy's core discovery/persistence hook instead of a separate Crypt4GH-specific traversal.
- Cover declared outputs, discovered outputs, and `extra_files` payload files.
- Emit and verify encryption evidence before success is finalized.
- Preserve the current plaintext allow-list only for framework/control artifacts and `stdout/stderr`.
- Update operator-facing manual verification guidance for the stricter contract.

### Out of scope

- Reopening the broader Phase 2 architecture or the A/B route contract.
- Pulsar support.
- Any secure-log redesign for `stdout/stderr`.
- Future packed/indexed `extra_files` optimizations.
- New recryptor service routes or protocol changes unless a User Check-in explicitly re-approves that scope.

## Binding inputs and precedence

- Primary authority for this slice: `crypt4gh-phase-2-output-enforcement-spec.md`
- Historical context only where non-conflicting:
  - `crypt4gh-phase-2-implementation-plan.md`
  - `crypt4gh-phase-2-reimplementation-design.md`
- If older plan/design text narrows output scope to declared outputs, pattern-only discovery, or `/outputs` membership, this plan follows the addendum instead.

## Enforcement boundary shape

- Discovered outputs must reuse core discovery's own matched/persisted payload candidate set.
- Discovered payload encryption must happen in the discovery/persistence route before persistence success is finalized.
- Declared-output Crypt4GH finalization remains in place only for non-discovery outputs.
- Both routes must converge at the same "about-to-persist dataset payload" boundary for evidence and fail-closed verification.
- The implementation must not keep a narrower parallel Crypt4GH discovered-output selector.

## Expected file surface

### Likely implementation files

- `lib/galaxy/tools/crypt4gh_remote_execution.py`
- `lib/galaxy/model/store/discover.py`
- `lib/galaxy/metadata/set_metadata.py`
- `lib/galaxy/tools/remote_tool_eval.py` (only if metadata/evidence plumbing requires it)

### Likely test files

- `test/unit/jobs/test_crypt4gh_remote_execution.py`
- `test/unit/app/tools/test_crypt4gh_remote_execution.py`
- `test/integration/test_crypt4gh_remote_execution.py`

### Likely docs/manual verification files

- `test-data/crypt4gh/MANUAL_TESTING.md`

## Verification strategy

The implementation should stay TDD-first at the contract level that best protects this slice:

1. Unit tests pin down the shared about-to-persist boundary, including the split between the discovery/persistence hook and the non-discovery declared-output hook.
2. Integration tests prove the persisted-payload contract end-to-end against the mock compute-side recryptor, including discovery-route encryption before persistence success.
3. Manual-testing notes are updated only after automated behavior is green.

Minimum final verification for the completed slice:

- `pytest test/unit/app/tools/test_crypt4gh_remote_execution.py -q`
- `pytest test/unit/jobs/test_crypt4gh_remote_execution.py -q`
- `pytest test/integration/test_crypt4gh_remote_execution.py -q`

## Tasks

### Task 1: Lock the shared about-to-persist enforcement boundary

**Files:**
- Modify: `test/unit/jobs/test_crypt4gh_remote_execution.py`
- Modify: `test/unit/app/tools/test_crypt4gh_remote_execution.py`
- Modify: `test/integration/test_crypt4gh_remote_execution.py`
- Modify: `lib/galaxy/tools/crypt4gh_remote_execution.py`
- Modify: `lib/galaxy/model/store/discover.py`

**Acceptance tests:**

- A unit test proves discovered outputs are sourced from core discovery's matched/persisted payload set, not a separate Crypt4GH selector.
- A unit or integration test proves a `false_path` / `real_path` divergence case still resolves the payload Galaxy is about to persist.
- A test proves the non-discovery declared-output hook remains active only for outputs that do not flow through the discovery/persistence route.

- [ ] Write failing unit and integration tests for the shared about-to-persist boundary first.
- [ ] Verify those tests fail for the current implementation because discovered outputs still imply narrower Crypt4GH-side traversal assumptions.
- [ ] Introduce or refactor a single boundary contract so discovered outputs enter through `lib/galaxy/model/store/discover.py`, while non-discovery outputs still enter through `lib/galaxy/tools/crypt4gh_remote_execution.py`.
- [ ] Keep the change surgical: the shared boundary should define one persisted-payload contract for later evidence and verifier tasks instead of duplicating discovered-output selection logic.
- [ ] Re-run the targeted unit/integration tests and get them green.
- [ ] Perform the mandatory refactor checkpoint for the shared boundary and re-run the same tests.

**Verification:**

- `pytest test/unit/app/tools/test_crypt4gh_remote_execution.py -q`
- `pytest test/unit/jobs/test_crypt4gh_remote_execution.py -k "false_path or real_path or discovered" -q`
- `pytest test/integration/test_crypt4gh_remote_execution.py -k "discovered or persisted" -q`

### Task 2: Move discovered-output encryption onto the discovery/persistence hook

**Files:**
- Modify: `lib/galaxy/tools/crypt4gh_remote_execution.py`
- Modify: `lib/galaxy/model/store/discover.py`
- Modify: `lib/galaxy/metadata/set_metadata.py`
- Modify: `test/unit/jobs/test_crypt4gh_remote_execution.py`
- Modify: `test/integration/test_crypt4gh_remote_execution.py`

**Acceptance tests:**

- A discovered-output test proves encryption runs from the discovery/persistence hook before persistence success is finalized.
- A discovered-output test proves encryption marker evidence is emitted for each persisted discovered payload sourced from the core discovery route.
- A verifier-oriented test proves missing or invalid discovered-output mapping evidence fails the job with diagnostics that identify the evidence class.

- [ ] Start with failing tests that exercise discovered outputs through the core discovery/persistence route, not through a separate pattern-regex Crypt4GH pass.
- [ ] Make the smallest code changes needed so discovered outputs are encrypted at the discovery hook using the matched/persisted payload set Galaxy already decided to persist.
- [ ] Ensure discovered-output evidence remains readable by Galaxy's metadata/discovery persistence code without introducing a second source of truth for designation mapping or payload selection.
- [ ] Re-run discovered-output unit/integration coverage until green.
- [ ] Perform the mandatory refactor checkpoint around discovered-output evidence serialization and re-run the same tests.

**Verification:**

- `pytest test/unit/jobs/test_crypt4gh_remote_execution.py -k "discovered or designation" -q`
- `pytest test/integration/test_crypt4gh_remote_execution.py -k discovered -q`

### Task 3: Add `extra_files` payload encryption and manifest evidence

**Files:**
- Modify: `lib/galaxy/tools/crypt4gh_remote_execution.py`
- Modify: `lib/galaxy/model/store/discover.py`
- Modify: `lib/galaxy/metadata/set_metadata.py`
- Modify: `test/unit/jobs/test_crypt4gh_remote_execution.py`
- Modify: `test/integration/test_crypt4gh_remote_execution.py`

**Acceptance tests:**

- An integration test proves persisted `extra_files` payload files for a Crypt4GH job are individually encrypted before success, whether they arrive through the discovery route or the non-discovery declared-output route.
- A test proves an `extra_files` manifest links encrypted payloads back to the owning dataset.
- A fail-closed test proves missing `extra_files` manifest entries or plaintext `extra_files` payloads force job failure.

- [ ] Add failing tests for `extra_files` coverage before changing implementation.
- [ ] Extend the shared about-to-persist boundary so `extra_files` payload files are first-class candidates, not a post-processing special case.
- [ ] Emit manifest evidence that is complete enough for the later universal verifier to prove ownership and coverage.
- [ ] Re-run targeted unit/integration tests until green.
- [ ] Perform the mandatory refactor checkpoint and re-run the same tests.

**Verification:**

- `pytest test/unit/jobs/test_crypt4gh_remote_execution.py -k extra_files -q`
- `pytest test/integration/test_crypt4gh_remote_execution.py -k extra_files -q`

### Task 4: Add the universal pre-success verifier and fail-closed diagnostics

**Files:**
- Modify: `lib/galaxy/tools/crypt4gh_remote_execution.py`
- Modify: `lib/galaxy/tools/remote_tool_eval.py` (if the success gate must move earlier in the publish/import flow)
- Modify: `test/unit/jobs/test_crypt4gh_remote_execution.py`
- Modify: `test/integration/test_crypt4gh_remote_execution.py`

**Acceptance tests:**

- A verifier test proves success is blocked unless every persisted payload candidate from both the discovery hook and the non-discovery declared-output hook has encryption evidence.
- A verifier test proves diagnostics identify which evidence class is missing: payload marker, discovered-output mapping, or `extra_files` manifest.
- An integration test proves a mid-finalization failure still leaves the job failed closed and prevents plaintext persistence.

- [ ] Write failing verifier tests first for declared outputs, discovered outputs, and `extra_files` payloads.
- [ ] Implement a single pre-success verification pass over the shared about-to-persist payload set so success is gated by completeness rather than by individual best-effort finalization calls.
- [ ] Preserve existing failure context wherever possible while adding explicit evidence-class diagnostics for the new verifier failures.
- [ ] Re-run verifier unit/integration coverage until green.
- [ ] Perform the mandatory refactor checkpoint and re-run the same tests.

**Verification:**

- `pytest test/unit/jobs/test_crypt4gh_remote_execution.py -k "verifier or fail_closed or cleanup" -q`
- `pytest test/integration/test_crypt4gh_remote_execution.py -k "verifier or finalization or fail" -q`

### Task 5: Prove the plaintext allow-list and update operator-facing validation

**Files:**
- Modify: `test/integration/test_crypt4gh_remote_execution.py`
- Modify: `test-data/crypt4gh/MANUAL_TESTING.md`
- Modify: `lib/galaxy/tools/crypt4gh_remote_execution.py` (only if allow-list handling needs to be made explicit)

**Acceptance tests:**

- An integration test proves framework/control artifacts remain readable plaintext while dataset payload policy remains enforced.
- An integration test proves `stdout/stderr` is the only remaining payload-adjacent plaintext exception in this phase.
- Manual guidance documents how to verify declared outputs, discovered outputs, and `extra_files` coverage in a live Galaxy job directory.

- [ ] Write or extend failing integration tests for the allow-list boundaries.
- [ ] Make the minimum implementation changes needed to keep control artifacts readable without weakening persisted-payload enforcement.
- [ ] Update `test-data/crypt4gh/MANUAL_TESTING.md` only after the automated allow-list and verifier tests are green.
- [ ] Run the full unit/integration verification set.
- [ ] Perform the mandatory refactor checkpoint for this slice, then re-run the full verification set.

**Verification:**

- `pytest test/integration/test_crypt4gh_remote_execution.py -q`
- `pytest test/unit/app/tools/test_crypt4gh_remote_execution.py -q`
- `pytest test/unit/jobs/test_crypt4gh_remote_execution.py -q`

## Acceptance-test matrix for the finished slice

The completed implementation must satisfy all six addendum-driven proof cases:

1. Declared outputs: all persisted payloads are encrypted before success.
2. Discovered outputs: non-pattern discovery paths that persist datasets are covered through the discovery/persistence hook.
3. `extra_files`: payload encryption plus manifest completeness is enforced.
4. Universal verifier: any missing evidence forces ERROR/fail-closed.
5. Dataset-centric proof: target selection follows persisted dataset mapping rather than `/outputs` assumptions, with no narrower parallel discovered-output selector.
6. Plaintext allow-list: framework/control files remain readable while payload policy still holds.

## User Check-in markers

- **User Check-in A:** Pause if the shared about-to-persist payload boundary cannot be derived from existing Galaxy persistence/discovery hooks without broadening scope beyond the files listed in this plan.
- **User Check-in B:** Pause if non-pattern discovered-output coverage requires a new persistence contract rather than extending the existing discovery/import metadata path and using its matched/persisted payload set as source of truth.
- **User Check-in C:** Pause if `extra_files` correctness appears to require packed/indexed transport or any recryptor API change in this phase.
- **User Check-in D:** Pause if the verifier cannot be inserted before final success without changing unrelated job-runner architecture.
- **User Check-in E:** Pause if any new plaintext exception beyond framework/control artifacts and temporary `stdout/stderr` seems necessary.

## Review focus

The docs review should specifically confirm:

- the plan stays within the addendum's output-enforcement slice and does not reopen the broader phase-2 redesign;
- every required proof case from the addendum is mapped to at least one acceptance test;
- discovered outputs are routed through the discovery/persistence hook rather than a parallel Crypt4GH traversal;
- `extra_files` and non-pattern discovered outputs are explicit first-class work items, not implied follow-ups;
- the verifier is defined as a universal pre-success gate over persisted payload candidates.
