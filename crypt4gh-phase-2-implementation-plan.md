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
- Approved implementation detail for Task 2: persist a key-id lookup index under `compute_keys/index/<shard>/<key_id>.json` (shard = first two chars of key-id suffix, e.g. `cnk:abcde1234 -> ab/cnk:abcde1234.json`) with minimal metadata (`user_hash`, `expiration`) and immediate stale-entry deletion.
- Final encrypted outputs are indicated by existing `.c4gh` dataset semantics plus `crypt4gh_header`; no new encrypted-at-rest metadata flag in this slice.
- `lib/galaxy/tools/remote_tool_eval.py` stays a thin entrypoint; new Crypt4GH runtime logic belongs in a dedicated helper module.
- Selected Galaxy-imported outputs must write plaintext only under `_crypt/outputs/`; normal published/imported paths must receive only reassembled encrypted files.
- Discovered datasets are in scope, but only after the first declared-output tracer bullet is green.
- Approved scope waiver for spec acceptance test 5: the user explicitly said `keep pulsar out of the plan (mention it as a follow-up)`, so Pulsar contract-reuse acceptance is deferred to follow-up work rather than required for green in this plan.

## Repo/file map

### Galaxy repo

**Create:**
- `lib/galaxy/tools/crypt4gh_remote_execution.py`
- `test/unit/jobs/test_crypt4gh_remote_execution.py`
- `test/integration/test_crypt4gh_remote_execution.py`

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
2. Galaxy unit tests define execution-decision rules: `enable_crypt4gh_transparent_staging` as the top-level gate, `tool_evaluation_strategy = remote` as the required execution path, local TTL gating, and fail-closed cleanup behavior.
3. Galaxy integration tests define the first non-Pulsar tracer bullet with a mock/test B service.
4. The input-compatibility tracer uses the existing `test/functional/tools/inheritance_simple.xml` tool through the integration harness.
5. The output-finalization tracer uses the existing `test/functional/tools/output_format.xml` tool through the integration harness.
6. Cleanup/config/doc changes are verified after behavior is green.

Minimum verification commands for the finished slice:

- Recryptor repo: `poetry run pytest tests/test_compute_routes.py -q`
- Galaxy unit: `pytest test/unit/jobs/test_crypt4gh_remote_execution.py test/unit/data/datatypes/test_crypt4gh.py -q`
- Galaxy integration: `pytest test/integration/test_crypt4gh_remote_execution.py test/integration/test_config_schema.py -q`
- Input-compatibility tracer execution: `pytest test/integration/test_crypt4gh_remote_execution.py -k inheritance_simple -q`
- Output-finalization tracer execution: `pytest test/integration/test_crypt4gh_remote_execution.py -k output_format -q`

## Task 1: Recryptor B route contract tests and route docs

**Worktree:** `/workspaces/dotfiles/repos/crypt4gh-recryptor-service/work/work/phase2-recryptor-routes`

**Files:**
- Create: `tests/test_compute_routes.py`
- Modify: `src/crypt4gh_recryptor_service/models.py`
- Modify: `README.md`

- [ ] Write failing route-contract tests in `tests/test_compute_routes.py` for:
  - reused `POST /get_compute_key_info` behavior with hashed user-key linkage
  - new `POST /recrypt_header_to_job_key`
  - new `POST /recrypt_header_to_user_key`
  - HTTP contract: unknown key id = `404`, expired key id = `410`, malformed/undecryptable header = `422`
- [ ] Run: `poetry run pytest tests/test_compute_routes.py -q`
  Expected: FAIL with missing-route or wrong-response assertions mentioning `/recrypt_header_to_job_key` and `/recrypt_header_to_user_key`.
- [ ] Update `README.md` so the compute-mode API section names all three compute-side routes and the header-only contract.
- [ ] Extend `src/crypt4gh_recryptor_service/models.py` only as far as needed to express the new request/response payloads.
- [ ] Re-run: `poetry run pytest tests/test_compute_routes.py -q`
  Expected: still FAIL, but only on unimplemented handler behavior rather than missing models/imports.
- [ ] Commit checkpoint in the recryptor repo:
  `git add tests/test_compute_routes.py src/crypt4gh_recryptor_service/models.py README.md && git commit -m "test: define recryptor compute route contract"`

## Task 2: Recryptor B route implementation on top of existing storage semantics

**Worktree:** `/workspaces/dotfiles/repos/crypt4gh-recryptor-service/work/work/phase2-recryptor-routes`

**Files:**
- Modify: `src/crypt4gh_recryptor_service/compute.py`
- Modify: `src/crypt4gh_recryptor_service/storage.py`
- Modify: `src/crypt4gh_recryptor_service/crypt.py`

- [ ] Implement reverse lookup from `crypt4gh_compute_keypair_id` back to the existing hashed user-key directory without duplicating stored user-key knowledge.
- [ ] Implement reverse lookup using persisted key-id index entries at `compute_keys/index/<shard>/<key_id>.json` and fallback/backfill from hashed directory linkage when index entries are missing.
- [ ] Enforce key-id index safety and integrity rules: validate key id path components, keep index metadata minimal (`user_hash`, `expiration`), and delete stale entries immediately when detected.
- [ ] Implement compute-side header recryption to a supplied job public key and return the compute public key in the same response.
- [ ] Implement compute-side header recryption back to the stored user public key using the existing hashed directory linkage.
- [ ] Enforce fail-closed route behavior for unknown, expired, and undecryptable inputs.
- [ ] Run: `poetry run pytest tests/test_compute_routes.py -q`
  Expected: PASS.
- [ ] Refactor any lookup/crypto helper duplication that was introduced by the minimal implementation.
- [ ] Re-run: `poetry run pytest tests/test_compute_routes.py -q`
  Expected: PASS.
- [ ] Commit checkpoint in the recryptor repo:
  `git add src/crypt4gh_recryptor_service/compute.py src/crypt4gh_recryptor_service/storage.py src/crypt4gh_recryptor_service/crypt.py tests/test_compute_routes.py README.md && git commit -m "feat: add recryptor compute header rewrite routes"`

## Task 3: Galaxy helper contract, decision rules, and old-path freeze

**Worktree:** `/workspaces/dotfiles/repos/hdtr-semsec-galaxy-crypt4gh-support/work/explore-crypt4gh-library-support-merged-with-is-recryptor-from-26.0`

**Files:**
- Create: `lib/galaxy/tools/crypt4gh_remote_execution.py`
- Create: `test/unit/jobs/test_crypt4gh_remote_execution.py`
- Modify: `lib/galaxy/tools/remote_tool_eval.py`
- Modify: `lib/galaxy/jobs/runners/__init__.py`

- [ ] Write failing unit tests for the execution-decision rules:
  - `enable_crypt4gh_transparent_staging` remains the top-level gate
  - the execution-side helper path is only valid when `tool_evaluation_strategy = remote`
  - helper setup failures fail closed
  - `BaseJobRunner.prepare_job()` no longer owns Crypt4GH decrypt/encrypt wrapping when `tool_evaluation_strategy = remote`
- [ ] Run: `pytest test/unit/jobs/test_crypt4gh_remote_execution.py -q`
  Expected: FAIL with missing helper or decision-rule assertions.
- [ ] Add `lib/galaxy/tools/crypt4gh_remote_execution.py` with the smallest helper skeleton needed to satisfy the decision-rule tests.
- [ ] Keep `lib/galaxy/tools/remote_tool_eval.py` thin by delegating into the new helper rather than growing new logic inline.
- [ ] Stop `BaseJobRunner.prepare_job()` from owning Crypt4GH decrypt/encrypt wrapping once the execution-side helper path exists.
- [ ] Re-run: `pytest test/unit/jobs/test_crypt4gh_remote_execution.py -q`
  Expected: PASS for the decision-rule contract.
- [ ] Refactor helper boundaries while keeping `remote_tool_eval.py` thin.
- [ ] Re-run: `pytest test/unit/jobs/test_crypt4gh_remote_execution.py -q`
  Expected: PASS.
- [ ] Commit checkpoint in the Galaxy repo:
  `git add lib/galaxy/tools/crypt4gh_remote_execution.py lib/galaxy/tools/remote_tool_eval.py lib/galaxy/jobs/runners/__init__.py test/unit/jobs/test_crypt4gh_remote_execution.py && git commit -m "test: define galaxy crypt4gh remote execution contract"`

## Task 4: Galaxy input-side tracer bullet in non-Pulsar remote evaluation

**Worktree:** `/workspaces/dotfiles/repos/hdtr-semsec-galaxy-crypt4gh-support/work/explore-crypt4gh-library-support-merged-with-is-recryptor-from-26.0`

**Files:**
- Modify: `lib/galaxy/tools/crypt4gh_remote_execution.py`
- Create: `test/integration/test_crypt4gh_remote_execution.py`

- [ ] Wire `test/integration/test_crypt4gh_remote_execution.py` to load the existing `inheritance_simple` tool through `framework_tool_and_types = True` and `integration_tool_runner(["inheritance_simple"])`.
- [ ] Configure the integration instance to set `enable_crypt4gh_transparent_staging = True` and `tool_evaluation_strategy = "remote"`.
- [ ] Write the first failing integration tracer-bullet test around `inheritance_simple` and the mock/test B service, with explicit assertions that the tool-visible plaintext input path lives under `_crypt/inputs/`, the encrypted source dataset remains ciphertext at its normal storage path, and no sibling plaintext copy of that dataset is created elsewhere under the job working directory.
- [ ] Run: `pytest test/integration/test_crypt4gh_remote_execution.py -q`
  Expected: FAIL because `inheritance_simple` still receives ciphertext, the remote helper path is not complete, or the plaintext-location assertions are not yet satisfied.
- [ ] Implement second-stage header recryption to a job-local keypair.
- [ ] Implement plaintext materialization only under `_crypt/inputs/`, with no duplicate plaintext copy outside that subtree.
- [ ] Implement tool-visible input path rewriting to the plaintext materialized files.
- [ ] Re-run: `pytest test/integration/test_crypt4gh_remote_execution.py -q`
  Expected: PASS.
- [ ] Run the explicit input tracer command: `pytest test/integration/test_crypt4gh_remote_execution.py -k inheritance_simple -q`
  Expected: PASS and the selected test output references the `inheritance_simple` tool.
- [ ] Refactor helper/setup code if needed.
- [ ] Re-run: `pytest test/integration/test_crypt4gh_remote_execution.py -q`
  Expected: PASS.
- [ ] Commit checkpoint in the Galaxy repo:
  `git add lib/galaxy/tools/crypt4gh_remote_execution.py test/integration/test_crypt4gh_remote_execution.py && git commit -m "feat: support crypt4gh input compatibility for existing fastq tools"`

## Task 5: Declared-output encrypted return path

**Worktree:** `/workspaces/dotfiles/repos/hdtr-semsec-galaxy-crypt4gh-support/work/explore-crypt4gh-library-support-merged-with-is-recryptor-from-26.0`

**Files:**
- Modify: `lib/galaxy/tools/crypt4gh_remote_execution.py`
- Modify: `lib/galaxy/datatypes/binary.py`
- Modify: `test/unit/data/datatypes/test_crypt4gh.py`
- Modify: `test/integration/test_crypt4gh_remote_execution.py`

- [ ] Extend the integration test so the existing `output_format` tool is loaded for the output tracer through `integration_tool_runner(["output_format"])`.
- [ ] Extend the integration test so a declared Galaxy-imported output from `output_format` is selected for encryption once any input triggered Crypt4GH runtime handling.
- [ ] Extend the integration test so selected plaintext output is written only under `_crypt/outputs/`.
- [ ] Extend unit coverage for returned-output metadata expectations: preserve `crypt4gh_header`, keep `.c4gh` semantics, and do not retain compute-key id/expiry on final returned outputs in this slice.
- [ ] Run: `pytest test/unit/data/datatypes/test_crypt4gh.py -q`
  Expected: FAIL with metadata expectation mismatches for returned outputs.
- [ ] Run: `pytest test/integration/test_crypt4gh_remote_execution.py -q`
  Expected: FAIL with `output_format` output-finalization assertions.
- [ ] Implement job-level output selection for declared Galaxy-imported dataset outputs.
- [ ] Implement local encryption of selected plaintext outputs to the compute public key.
- [ ] Implement header rewrite through B and final encrypted-file reassembly on the publish/import path.
- [ ] Re-run: `pytest test/unit/data/datatypes/test_crypt4gh.py -q`
  Expected: PASS.
- [ ] Re-run: `pytest test/integration/test_crypt4gh_remote_execution.py -q`
  Expected: PASS.
- [ ] Run the explicit output tracer command: `pytest test/integration/test_crypt4gh_remote_execution.py -k output_format -q`
  Expected: PASS and the selected test output references the `output_format` tool.
- [ ] Refactor output-path and metadata handling.
- [ ] Re-run both commands.
  Expected: PASS for both.
- [ ] Commit checkpoint in the Galaxy repo:
  `git add lib/galaxy/tools/crypt4gh_remote_execution.py lib/galaxy/datatypes/binary.py test/unit/data/datatypes/test_crypt4gh.py test/integration/test_crypt4gh_remote_execution.py && git commit -m "feat: finalize encrypted outputs for existing galaxy tools"`

## Task 6: Cleanup reliability and operator-attention cleanup-failure contract

**Worktree:** `/workspaces/dotfiles/repos/hdtr-semsec-galaxy-crypt4gh-support/work/explore-crypt4gh-library-support-merged-with-is-recryptor-from-26.0`

**Files:**
- Modify: `lib/galaxy/tools/crypt4gh_remote_execution.py`
- Modify: `test/unit/jobs/test_crypt4gh_remote_execution.py`
- Modify: `test/integration/test_crypt4gh_remote_execution.py`

- [ ] Extend unit tests so cleanup must still run after tool failure and the surfaced failure text still includes the original tool exception.
- [ ] Extend unit and/or integration tests so cleanup failure has a concrete observable contract: the final job state is failed/error via `job_wrapper.fail(...)`, the failure text or captured log includes a fixed marker such as `CRYPT4GH_CLEANUP_FAILED`, and the surfaced diagnostics include both the original tool failure (if any) and the cleanup exception.
- [ ] Run: `pytest test/unit/jobs/test_crypt4gh_remote_execution.py -k cleanup -q`
  Expected: FAIL with missing cleanup-on-failure, fixed-marker, or combined-diagnostics assertions.
- [ ] Implement cleanup in a reliable post-run path that executes after both successful and failed tool runs.
- [ ] Implement cleanup-failure handling that preserves failure diagnostics, emits the fixed operator-attention marker, and leaves the job in failed state instead of hiding the original error.
- [ ] Re-run: `pytest test/unit/jobs/test_crypt4gh_remote_execution.py -k cleanup -q`
  Expected: PASS.
- [ ] Re-run: `pytest test/integration/test_crypt4gh_remote_execution.py -q`
  Expected: PASS with no regression to the tracer-bullet path.
- [ ] Commit checkpoint in the Galaxy repo:
  `git add lib/galaxy/tools/crypt4gh_remote_execution.py test/unit/jobs/test_crypt4gh_remote_execution.py test/integration/test_crypt4gh_remote_execution.py && git commit -m "feat: harden crypt4gh cleanup failure handling"`

## Task 7: Discovered datasets and fail-closed expiry cases

**Worktree:** `/workspaces/dotfiles/repos/hdtr-semsec-galaxy-crypt4gh-support/work/explore-crypt4gh-library-support-merged-with-is-recryptor-from-26.0`

**Files:**
- Modify: `lib/galaxy/tools/crypt4gh_remote_execution.py`
- Modify: `test/integration/test_crypt4gh_remote_execution.py`

- [ ] Extend the integration test module with discovered-dataset encryption after the declared-output slice is already green.
- [ ] Extend the integration test module with fail-before-launch behavior when stored TTL is below threshold.
- [ ] Extend the integration test module with fail-closed output finalization when a key expires mid-run.
- [ ] Add/adjust unit coverage in `test/unit/jobs/test_crypt4gh_remote_execution.py` for the local minimum-TTL launch gate:
  - local minimum-TTL gate runs before any B call
- [ ] Run: `pytest test/integration/test_crypt4gh_remote_execution.py -q`
  Expected: FAIL with discovered-output or expiry-behavior assertions.
- [ ] Implement discovered-output selection using the same job-level encryption decision.
- [ ] Implement Galaxy-side TTL preflight before the first B contact.
- [ ] Implement fail-closed mid-run expiry handling during output finalization.
- [ ] Re-run: `pytest test/integration/test_crypt4gh_remote_execution.py -q`
  Expected: PASS.
- [ ] Refactor any duplicated selection or expiry logic.
- [ ] Re-run: `pytest test/integration/test_crypt4gh_remote_execution.py -q`
  Expected: PASS.
- [ ] Commit checkpoint in the Galaxy repo:
  `git add lib/galaxy/tools/crypt4gh_remote_execution.py test/integration/test_crypt4gh_remote_execution.py && git commit -m "feat: cover discovered outputs and expiry failures"`

## Task 8: Remove old staging path and update operator/config surfaces

**Worktree:** `/workspaces/dotfiles/repos/hdtr-semsec-galaxy-crypt4gh-support/work/explore-crypt4gh-library-support-merged-with-is-recryptor-from-26.0`

**Files:**
- Delete: `lib/galaxy/jobs/crypt4gh_staging.py`
- Delete: `test/unit/jobs/test_crypt4gh_staging.py`
- Modify: `lib/galaxy/config/schemas/config_schema.yml`
- Modify: `lib/galaxy/config/sample/galaxy.yml.sample`
- Modify: `doc/source/admin/galaxy_options.rst`
- Modify: `test/integration/test_config_schema.py`
- Modify: `test-data/crypt4gh/MANUAL_TESTING.md`

- [ ] Extend `test/integration/test_config_schema.py` with an explicit assertion surface that:
  - loads `lib/galaxy/config/schemas/config_schema.yml`
  - asserts `enable_crypt4gh_transparent_staging` still exists
  - asserts `crypt4gh_reencryption_service_url` still exists and its description mentions compute-side recryptor B
  - asserts `crypt4gh_compute_key_path` and `crypt4gh_compute_key_passphrase_env` are absent
- [ ] Run: `pytest test/integration/test_config_schema.py -q`
  Expected: FAIL on at least one schema-surface assertion before the config/docs cleanup lands.
- [ ] Delete the old orchestrator-side staging module and its old unit test.
- [ ] Remove the old compute-private-key config options from schema, sample config, and admin docs.
- [ ] Update `test-data/crypt4gh/MANUAL_TESTING.md` to document the execution-side `_crypt/` model and header-only B interactions.
- [ ] Run: `pytest test/integration/test_config_schema.py -q`
  Expected: PASS.
- [ ] Run: `pytest test/unit/jobs/test_crypt4gh_remote_execution.py test/unit/data/datatypes/test_crypt4gh.py -q`
  Expected: PASS.
- [ ] Run: `pytest test/integration/test_crypt4gh_remote_execution.py -q`
  Expected: PASS.
- [ ] Refactor or delete any now-orphaned imports/helpers caused by removing the old staging path.
- [ ] Re-run the same three commands.
  Expected: PASS for all three.
- [ ] Commit checkpoint in the Galaxy repo:
  `git add lib/galaxy/config/schemas/config_schema.yml lib/galaxy/config/sample/galaxy.yml.sample doc/source/admin/galaxy_options.rst test/integration/test_config_schema.py test-data/crypt4gh/MANUAL_TESTING.md lib/galaxy/jobs/runners/__init__.py lib/galaxy/tools/crypt4gh_remote_execution.py test/unit/jobs/test_crypt4gh_remote_execution.py test/unit/data/datatypes/test_crypt4gh.py test/integration/test_crypt4gh_remote_execution.py && git rm lib/galaxy/jobs/crypt4gh_staging.py test/unit/jobs/test_crypt4gh_staging.py && git commit -m "refactor: remove legacy crypt4gh staging path"`

## Task 9: Final verification, live smoke, and handoff

**Galaxy worktree:** `/workspaces/dotfiles/repos/hdtr-semsec-galaxy-crypt4gh-support/work/explore-crypt4gh-library-support-merged-with-is-recryptor-from-26.0`

**Recryptor worktree:** `/workspaces/dotfiles/repos/crypt4gh-recryptor-service/work/work/phase2-recryptor-routes`

- [ ] Run fresh final verification in the recryptor repo:
  `poetry run pytest tests/test_compute_routes.py -q`
  Expected: PASS.
- [ ] Run fresh final verification in the Galaxy repo:
  - `pytest test/unit/jobs/test_crypt4gh_remote_execution.py test/unit/data/datatypes/test_crypt4gh.py -q`
  - `pytest test/integration/test_crypt4gh_remote_execution.py test/integration/test_config_schema.py -q`
  - `pytest test/integration/test_crypt4gh_remote_execution.py -k inheritance_simple -q`
  - `pytest test/integration/test_crypt4gh_remote_execution.py -k output_format -q`
  Expected: PASS for all commands.
- [ ] Define the live-service switch in `test/integration/test_crypt4gh_remote_execution.py` explicitly: `handle_galaxy_config_kwds` reads `GALAXY_TEST_CRYPT4GH_REENCRYPTION_SERVICE_URL`; when set, it writes that value to `config["crypt4gh_reencryption_service_url"]` and skips any in-process mock-service startup so the same integration tests can target a real compute-mode service unchanged.
- [ ] Run one live cross-repo smoke check against the real compute-mode service after the mock-based tests are green.
  Suggested shape:
  - start the real compute-mode FastAPI app from the recryptor worktree
  - export `GALAXY_TEST_CRYPT4GH_REENCRYPTION_SERVICE_URL=http://127.0.0.1:<port>` (or the equivalent command-scoped environment assignment)
  - point the Galaxy integration test at that service through the `handle_galaxy_config_kwds` → `crypt4gh_reencryption_service_url` hook instead of the mock B service
  - rerun `pytest test/integration/test_crypt4gh_remote_execution.py -k inheritance_simple -q`
  - rerun `pytest test/integration/test_crypt4gh_remote_execution.py -k output_format -q`
  Expected: PASS against the live service as well.
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
- **User Check-in C:** Pause if recryptor B needs lookup state beyond the approved minimal key-id index metadata (`user_hash`, `expiration`) or cannot preserve hashed-directory linkage as source of truth.

## Follow-up work explicitly out of this plan

- Pulsar compatibility verification and implementation
- Authentication/authorization hardening between Galaxy, A, and B
- Any mixed encrypted/plain final dataset import policy
- Any compute-key renewal workflow
