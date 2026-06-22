# Crypt4GH Phase 2 Non-Pulsar Reimplementation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the approved non-Pulsar Crypt4GH Phase 2 runtime redesign across Galaxy and compute-side recryptor B, replacing orchestrator-side staging with execution-side handling, fail-closed key checks, and encrypted-at-rest output return.

**Architecture:** Galaxy remains the holder of encrypted datasets and ordinary dataset metadata, while execution-side logic behind `lib/galaxy/tools/remote_tool_eval.py` performs second-stage header recryption, plaintext materialization under `_crypt/`, output re-encryption, and cleanup. Recryptor B extends its existing compute-key and hashed-user-key storage model with job-key and user-key header rewrite routes, while Galaxy removes the old `prepare_job()` wrapper path and compute-private-key configuration.

**Tech Stack:** Galaxy Python runtime/tests, FastAPI recryptor service, Crypt4GH CLI/library, pytest, Galaxy integration test harness, Poetry in the recryptor repo.

---

## Scope locks from approved design + user confirmations

- Non-Pulsar only in this plan; Pulsar stays follow-up work.
- One coordinated plan covers both repos:
  - Galaxy worktree: `/workspaces/dotfiles/repos/hdtr-semsec-galaxy-crypt4gh-support/work/explore-crypt4gh-library-support-merged-with-is-recryptor-from-26.0`
  - Recryptor worktree: `/workspaces/dotfiles/repos/crypt4gh-recryptor-service/work/work/phase2-recryptor-routes`
- Recryptor B must reuse the existing hashed directory linkage between `user_keys/` and `compute_keys/`; do not duplicate user-key knowledge.
- Final encrypted outputs are indicated by existing `.c4gh` dataset semantics plus `crypt4gh_header`; no new encrypted-at-rest metadata flag in this slice.
- `lib/galaxy/tools/remote_tool_eval.py` stays a thin entrypoint; new Crypt4GH runtime logic belongs in a dedicated helper module.
- Selected Galaxy-imported outputs must write plaintext only under `_crypt/outputs/`; normal published/imported paths must receive only reassembled encrypted files.
- Discovered datasets are in scope, but only after the first declared-output tracer bullet is green.

## Repo/file map

### Galaxy repo

**Create:**
- `lib/galaxy/tools/crypt4gh_remote_execution.py`
- `test/unit/jobs/test_crypt4gh_remote_execution.py`
- `test/integration/test_crypt4gh_remote_execution.py`
- `test/functional/tools/crypt4gh_phase2_roundtrip.xml`

**Modify:**
- `lib/galaxy/tools/remote_tool_eval.py`
- `lib/galaxy/jobs/runners/__init__.py`
- `lib/galaxy/datatypes/binary.py`
- `lib/galaxy/config/schemas/config_schema.yml`
- `lib/galaxy/config/sample/galaxy.yml.sample`
- `doc/source/admin/galaxy_options.rst`
- `test/unit/data/datatypes/test_crypt4gh.py`
- `test/integration/test_config_schema.py`
- `test-data/crypt4gh/MANUAL_TESTING.md`

**Delete:**
- `lib/galaxy/jobs/crypt4gh_staging.py`
- `test/unit/jobs/test_crypt4gh_staging.py`

### Recryptor repo

**Create:**
- `tests/test_compute_routes.py`

**Modify:**
- `src/crypt4gh_recryptor_service/models.py`
- `src/crypt4gh_recryptor_service/compute.py`
- `src/crypt4gh_recryptor_service/storage.py`
- `src/crypt4gh_recryptor_service/crypt.py`
- `README.md`

## Verification strategy

Primary verification is test-first and contract-first:

1. Recryptor route contract tests define the new B API.
2. Galaxy unit tests define local TTL gating, metadata handling, path rewriting, and fail-closed cleanup behavior.
3. Galaxy integration tests define the first non-Pulsar tracer bullet with a mock/test B service.
4. Cleanup/config/doc changes are verified after behavior is green.

Minimum verification commands for the finished slice:

- Recryptor repo: `poetry run pytest tests/test_compute_routes.py -q`
- Galaxy unit: `pytest test/unit/jobs/test_crypt4gh_remote_execution.py test/unit/data/datatypes/test_crypt4gh.py -q`
- Galaxy integration: `pytest test/integration/test_crypt4gh_remote_execution.py test/integration/test_config_schema.py -q`

## Task 1: Recryptor B route contract tests

**Worktree:** `/workspaces/dotfiles/repos/crypt4gh-recryptor-service/work/work/phase2-recryptor-routes`

**Files:**
- Create: `tests/test_compute_routes.py`
- Modify: `src/crypt4gh_recryptor_service/models.py`
- Modify: `README.md`

- [ ] Write failing route-contract tests for:
  - reused `POST /get_compute_key_info` behavior with persisted user-key linkage
  - new `POST /recrypt_header_to_job_key`
  - new `POST /recrypt_header_to_user_key`
  - HTTP contract: unknown key id = `404`, expired key id = `410`, malformed/undecryptable header = `422`
- [ ] Verify the new tests fail with `poetry run pytest tests/test_compute_routes.py -q`.
- [ ] Extend request/response models only as far as needed to support the contract.
- [ ] Re-run `poetry run pytest tests/test_compute_routes.py -q` until green.
- [ ] Refactor shared test helpers or model helpers if needed, then re-run the same test command.
- [ ] Create a checkpoint commit in the recryptor repo once the route contract is stable.

## Task 2: Recryptor B route implementation on top of existing storage semantics

**Worktree:** `/workspaces/dotfiles/repos/crypt4gh-recryptor-service/work/work/phase2-recryptor-routes`

**Files:**
- Modify: `src/crypt4gh_recryptor_service/compute.py`
- Modify: `src/crypt4gh_recryptor_service/storage.py`
- Modify: `src/crypt4gh_recryptor_service/crypt.py`

- [ ] Implement reverse lookup from `crypt4gh_compute_keypair_id` back to the existing hashed user-key directory without duplicating stored user-key knowledge.
- [ ] Implement compute-side header recryption to a supplied job public key and return the compute public key in the same response.
- [ ] Implement compute-side header recryption back to the stored user public key using the existing hashed directory linkage.
- [ ] Enforce fail-closed route behavior for unknown, expired, and undecryptable inputs.
- [ ] Verify green with `poetry run pytest tests/test_compute_routes.py -q`.
- [ ] Refactor any lookup/crypto helper duplication, then re-run the same command.
- [ ] Create a checkpoint commit in the recryptor repo once B behavior is green.

## Task 3: Galaxy helper contract and old-path freeze

**Worktree:** `/workspaces/dotfiles/repos/hdtr-semsec-galaxy-crypt4gh-support/work/explore-crypt4gh-library-support-merged-with-is-recryptor-from-26.0`

**Files:**
- Create: `lib/galaxy/tools/crypt4gh_remote_execution.py`
- Create: `test/unit/jobs/test_crypt4gh_remote_execution.py`
- Modify: `lib/galaxy/tools/remote_tool_eval.py`
- Modify: `lib/galaxy/jobs/runners/__init__.py`

- [ ] Write failing unit tests that define:
  - local minimum-TTL gate before any B call
  - execution workspace layout under `_crypt/`
  - explicit input path rewriting to plaintext materialized files
  - fail-closed behavior when helper setup or B calls fail
- [ ] Verify red with `pytest test/unit/jobs/test_crypt4gh_remote_execution.py -q`.
- [ ] Add the thin `remote_tool_eval.py` handoff into a dedicated helper module.
- [ ] Stop `BaseJobRunner.prepare_job()` from owning Crypt4GH decrypt/encrypt wrapping once the execution-side helper path exists.
- [ ] Re-run `pytest test/unit/jobs/test_crypt4gh_remote_execution.py -q` until green.
- [ ] Refactor helper boundaries while keeping `remote_tool_eval.py` thin, then re-run the same test command.
- [ ] Create a checkpoint commit in the Galaxy repo after the input-side helper contract is green.

## Task 4: Galaxy input-side tracer bullet in non-Pulsar remote evaluation

**Worktree:** `/workspaces/dotfiles/repos/hdtr-semsec-galaxy-crypt4gh-support/work/explore-crypt4gh-library-support-merged-with-is-recryptor-from-26.0`

**Files:**
- Modify: `lib/galaxy/tools/crypt4gh_remote_execution.py`
- Create: `test/functional/tools/crypt4gh_phase2_roundtrip.xml`
- Create: `test/integration/test_crypt4gh_remote_execution.py`

- [ ] Write the first failing integration tracer-bullet test for non-Pulsar remote execution with a mock/test B service.
- [ ] Verify red with `pytest test/integration/test_crypt4gh_remote_execution.py -q`.
- [ ] Implement second-stage header recryption to a job-local keypair, plaintext materialization under `_crypt/inputs/`, and tool-visible input path rewriting.
- [ ] Verify that the tool receives only compute-local plaintext paths and that plaintext stays under `_crypt/`.
- [ ] Re-run `pytest test/integration/test_crypt4gh_remote_execution.py -q` until the input-side slice is green.
- [ ] Refactor helper/setup code if needed, then re-run the same test command.
- [ ] Create a checkpoint commit for the first end-to-end input-side slice.

## Task 5: Declared-output encrypted return path

**Worktree:** `/workspaces/dotfiles/repos/hdtr-semsec-galaxy-crypt4gh-support/work/explore-crypt4gh-library-support-merged-with-is-recryptor-from-26.0`

**Files:**
- Modify: `lib/galaxy/tools/crypt4gh_remote_execution.py`
- Modify: `lib/galaxy/datatypes/binary.py`
- Modify: `test/unit/data/datatypes/test_crypt4gh.py`
- Modify: `test/integration/test_crypt4gh_remote_execution.py`

- [ ] Extend the integration test so a declared Galaxy-imported output is written as plaintext only under `_crypt/outputs/` and published back as a final encrypted `.c4gh` artifact.
- [ ] Add/adjust unit coverage for returned-output metadata expectations: preserve `crypt4gh_header`, keep `.c4gh` semantics, and do not retain compute-key id/expiry on final returned outputs in this slice.
- [ ] Verify red with:
  - `pytest test/unit/data/datatypes/test_crypt4gh.py -q`
  - `pytest test/integration/test_crypt4gh_remote_execution.py -q`
- [ ] Implement output selection for the first slice: if any input triggered Crypt4GH runtime handling, all declared Galaxy-imported dataset outputs in that job are encrypted for return.
- [ ] Implement local encryption to the compute public key, header rewrite through B, and final encrypted-file reassembly on the publish/import path.
- [ ] Re-run both commands until green.
- [ ] Refactor output-path and metadata handling, then re-run both commands.
- [ ] Create a checkpoint commit once declared-output encrypted return is green.

## Task 6: Discovered datasets and fail-closed expiry cases

**Worktree:** `/workspaces/dotfiles/repos/hdtr-semsec-galaxy-crypt4gh-support/work/explore-crypt4gh-library-support-merged-with-is-recryptor-from-26.0`

**Files:**
- Modify: `lib/galaxy/tools/crypt4gh_remote_execution.py`
- Modify: `test/integration/test_crypt4gh_remote_execution.py`

- [ ] Extend the existing integration test module with:
  - discovered-dataset encryption after the declared-output slice is already green
  - fail-before-launch when stored TTL is below threshold
  - fail-closed output-finalization when a key expires mid-run
- [ ] Verify red with `pytest test/integration/test_crypt4gh_remote_execution.py -q`.
- [ ] Implement discovered-output selection and finalization using the same job-level encryption decision.
- [ ] Implement TTL preflight in Galaxy before B contact, while keeping B as the authority for unknown/expired route rejection.
- [ ] Re-run `pytest test/integration/test_crypt4gh_remote_execution.py -q` until green.
- [ ] Refactor any duplicated selection/finalization logic, then re-run the same command.
- [ ] Create a checkpoint commit once discovered outputs and expiry behavior are green.

## Task 7: Remove old staging path and update operator surfaces

**Worktree:** `/workspaces/dotfiles/repos/hdtr-semsec-galaxy-crypt4gh-support/work/explore-crypt4gh-library-support-merged-with-is-recryptor-from-26.0`

**Files:**
- Delete: `lib/galaxy/jobs/crypt4gh_staging.py`
- Delete: `test/unit/jobs/test_crypt4gh_staging.py`
- Modify: `lib/galaxy/config/schemas/config_schema.yml`
- Modify: `lib/galaxy/config/sample/galaxy.yml.sample`
- Modify: `doc/source/admin/galaxy_options.rst`
- Modify: `test/integration/test_config_schema.py`
- Modify: `test-data/crypt4gh/MANUAL_TESTING.md`

- [ ] Write or extend tests so config/schema validation fails until the old compute-private-key options are removed and `crypt4gh_reencryption_service_url` text is updated to describe compute-side recryptor B.
- [ ] Verify red with `pytest test/integration/test_config_schema.py -q` if test changes are needed.
- [ ] Remove the old compute-key config options and all remaining operator guidance that documents the `prepare_job()` / orchestrator-side staging design.
- [ ] Replace manual-testing guidance so it matches the execution-side `_crypt/` model and header-only B interactions.
- [ ] Re-run:
  - `pytest test/integration/test_config_schema.py -q`
  - `pytest test/unit/jobs/test_crypt4gh_remote_execution.py test/unit/data/datatypes/test_crypt4gh.py -q`
  - `pytest test/integration/test_crypt4gh_remote_execution.py -q`
- [ ] Refactor or delete any now-orphaned helpers/imports caused by removing the old staging path, then re-run the same verification commands.
- [ ] Create a checkpoint commit once the old path is fully removed.

## Task 8: Final verification and handoff

**Galaxy worktree:** `/workspaces/dotfiles/repos/hdtr-semsec-galaxy-crypt4gh-support/work/explore-crypt4gh-library-support-merged-with-is-recryptor-from-26.0`

**Recryptor worktree:** `/workspaces/dotfiles/repos/crypt4gh-recryptor-service/work/work/phase2-recryptor-routes`

- [ ] Run fresh final verification in the recryptor repo: `poetry run pytest tests/test_compute_routes.py -q`.
- [ ] Run fresh final verification in the Galaxy repo:
  - `pytest test/unit/jobs/test_crypt4gh_remote_execution.py test/unit/data/datatypes/test_crypt4gh.py -q`
  - `pytest test/integration/test_crypt4gh_remote_execution.py test/integration/test_config_schema.py -q`
- [ ] Confirm the deleted old-path files are absent and no remaining code path depends on `crypt4gh_compute_key_path` or `crypt4gh_compute_key_passphrase_env`.
- [ ] Perform the mandatory refactor checkpoint in both repos; either apply small behavior-preserving cleanups or record that no refactor was needed.
- [ ] Prepare a handoff note that includes:
  - exact verification commands and outcomes
  - pragmatic-programmer diagnostic score
  - clean-code review outcome
  - manual test suggestions for a human operator

## User check-in markers for implementers

- **User Check-in A:** Pause if Galaxy's real output-import/discovery behavior makes it impossible to keep tool-visible plaintext entirely under `_crypt/outputs/` without a broader architecture change.
- **User Check-in B:** Pause if `remote_tool_eval.py` cannot support the required non-Pulsar tracer bullet without changing the approved “thin entrypoint + dedicated helper” boundary.
- **User Check-in C:** Pause if recryptor B needs a new persistent index or duplicated user-key metadata to resolve `crypt4gh_compute_keypair_id`; the approved assumption is to reuse the existing hashed directory linkage.

## Follow-up work explicitly out of this plan

- Pulsar compatibility verification and implementation
- Authentication/authorization hardening between Galaxy, A, and B
- Any mixed encrypted/plain final dataset import policy
- Any compute-key renewal workflow
