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
- Runtime-controlled plaintext output staging should stay under `_crypt/outputs/` where feasible, but output-enforcement scope is defined by persisted dataset mapping rather than `_crypt/outputs/` membership alone; normal published/imported paths must receive only encrypted payloads.
- Discovered datasets and associated `extra_files` are in scope, but only after the first declared-output tracer bullet is green.
- Approved scope waiver for spec acceptance test 5: the user explicitly said `keep pulsar out of the plan (mention it as a follow-up)`, so Pulsar contract-reuse acceptance is deferred to follow-up work rather than required for green in this plan.

## Output-enforcement alignment with the 2026-07-14 addendum

- Binding addendum for this topic: `docs/superpowers/specs/2026-07-14-crypt4gh-output-enforcement-spec.md`
- For output-enforcement semantics, the addendum is authoritative and overrides conflicting wording in this plan.
- Superseded / no-longer-authoritative assumptions from the earlier plan text:
  - Any wording that narrows output scope to only “selected Galaxy-imported outputs”. The required final scope is all persisted payloads for Crypt4GH jobs: declared outputs, discovered outputs, and associated `extra_files` payloads.
  - Any discovered-output handling that relies only on Crypt4GH-specific collector or pattern-centric traversal. Required behavior must follow core persisted dataset mapping.
  - Any success path that depends only on per-output encryption steps without a universal pre-success fail-closed verifier across persisted payload candidates.
  - Any interpretation that `_crypt/outputs/` is a sufficient proxy for deciding which payloads must be encrypted. `_crypt/outputs/` remains a preferred staging area when runtime-controlled, but proof cases must be validated against `false_path` / `real_path` divergence and discovered-output persistence mapping.
  - Any implicit treatment of `extra_files` as out of scope. `extra_files` payload files are in scope and require encryption plus manifest evidence.

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
2. Galaxy unit tests define execution-decision rules: `enable_crypt4gh_transparent_staging` as the top-level gate, `tool_evaluation_strategy = remote` as the required execution path, at least one crypt4gh dataset input, local TTL gating, and fail-closed cleanup behavior.
3. Galaxy integration tests define the first non-Pulsar tracer bullet with a mock/test B service.
4. The input-compatibility tracer uses the existing `test/functional/tools/inheritance_simple.xml` tool through the integration harness.
5. The output-finalization tracer uses the existing `test/functional/tools/output_format.xml` tool through the integration harness.
6. Later integration slices must prove the addendum contract: declared + discovered persisted payload coverage, `extra_files` encryption/manifest completeness, dataset-centric selection despite path-model divergence, universal pre-success fail-closed verification, and the current plaintext allow-list for framework/control files plus `stdout/stderr`.
7. Cleanup/config/doc changes are verified after behavior is green.

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

## Task 5: Declared-output encrypted return path (first persisted-payload slice)

**Worktree:** `/workspaces/dotfiles/repos/hdtr-semsec-galaxy-crypt4gh-support/work/explore-crypt4gh-library-support-merged-with-is-recryptor-from-26.0`

**Files:**
- Modify: `lib/galaxy/tools/crypt4gh_remote_execution.py`
- Modify: `lib/galaxy/datatypes/binary.py`
- Modify: `test/unit/data/datatypes/test_crypt4gh.py`
- Modify: `test/integration/test_crypt4gh_remote_execution.py`

- Note: Previous plan wording could be read as if declared outputs were the full output-enforcement scope. That reading is superseded by the 2026-07-14 addendum. This task establishes the first persisted-payload slice only; Task 7 completes the universal persisted-payload contract.

- [ ] Extend the integration test so the existing `output_format` tool is loaded for the output tracer through `integration_tool_runner(["output_format"])`.
- [ ] Extend the integration test so a declared Galaxy-imported output from `output_format` is selected for encryption from the persisted dataset mapping once any input triggered Crypt4GH runtime handling.
- [ ] Extend the integration test so any runtime-controlled plaintext staging for that declared output stays under `_crypt/outputs/`, while the final published/imported dataset path receives only encrypted payload content.
- [ ] Extend unit coverage for returned-output metadata expectations: preserve `crypt4gh_header`, keep `.c4gh` semantics, and do not retain compute-key id/expiry on final returned outputs in this slice.
- [ ] Extend the integration assertions so declared-output finalization emits payload-marker evidence that can later be consumed by the universal pre-success verifier.
- [ ] Run: `pytest test/unit/data/datatypes/test_crypt4gh.py -q`
  Expected: FAIL with metadata expectation mismatches for returned outputs.
- [ ] Run: `pytest test/integration/test_crypt4gh_remote_execution.py -q`
  Expected: FAIL with `output_format` output-finalization assertions.
- [ ] Implement job-level output selection for declared Galaxy-imported dataset outputs using core persisted dataset mapping rather than directory membership alone.
- [ ] Implement local encryption of selected plaintext outputs to the compute public key.
- [ ] Implement header rewrite through B and final encrypted-file reassembly on the publish/import path.
- [ ] Implement declared-output payload-marker evidence emission for later verifier reuse.
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
- [ ] Extend unit and/or integration tests so cleanup failure has a concrete observable contract: the final job state is failed/error via `job_wrapper.fail(...)`, the failure text or captured log includes a fixed marker such as `CRYPT4GH_PLAINTEXT_CLEANUP_FAILED`, and the surfaced diagnostics include both the original tool failure (if any) and the cleanup exception.
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

## Task 7: Discovered outputs, `extra_files`, and fail-closed persisted-payload verification

**Worktree:** `/workspaces/dotfiles/repos/hdtr-semsec-galaxy-crypt4gh-support/work/explore-crypt4gh-library-support-merged-with-is-recryptor-from-26.0`

**Files:**
- Modify: `lib/galaxy/tools/crypt4gh_remote_execution.py`
- Modify: `test/unit/jobs/test_crypt4gh_remote_execution.py`
- Modify: `test/integration/test_crypt4gh_remote_execution.py`

- Note: TTL guards were implemented in task 3 due to plan inconsistencies, but not thoroughly tested.
- Note: Previous Task 7 wording that focused only on discovered datasets + expiry is superseded. This task now owns the remaining output-enforcement alignment work required by the 2026-07-14 addendum.
- [ ] Extend the integration test module with discovered-output encryption after the declared-output slice is already green, including at least one non-pattern discovery path that still persists datasets.
- [ ] Extend unit and/or integration coverage with discovered-output designation/path mapping evidence assertions: emitted evidence must link each persisted discovered payload to its discovery designation/path mapping, and missing or invalid mapping evidence must fail the verifier with diagnostics that name the evidence class.
- [ ] Extend the integration test module with `extra_files` payload encryption and manifest-completeness assertions.
- [ ] Extend the integration test module with a dataset-centric proof case where payload selection cannot be justified by `/outputs` membership alone (for example `false_path` vs `real_path` divergence and/or a persisted discovered payload outside `/outputs`).
- [ ] Extend unit and/or integration coverage with a universal pre-success fail-closed verifier assertion: if any declared/discovered/`extra_files` persisted payload lacks encryption evidence, final success is blocked and the job fails with diagnostics.
- [ ] Extend the integration test module with plaintext allow-list assertions proving framework/control artifacts stay readable while dataset payload policy remains enforced; keep `stdout/stderr` as the only current payload-adjacent plaintext exception.
- [ ] Extend the integration test module with fail-before-launch behavior when stored TTL is below threshold.
- [ ] Extend the integration test module with fail-closed output finalization when a key expires mid-run.
- [ ] Add/adjust unit coverage in `test/unit/jobs/test_crypt4gh_remote_execution.py` for the local minimum-TTL launch gate:
  - local minimum-TTL gate runs before any B call
- [ ] Add/adjust unit coverage in `test/unit/jobs/test_crypt4gh_remote_execution.py` for verifier diagnostics:
  - missing payload-marker evidence identifies the evidence class
  - missing or invalid discovered-output designation/path mapping evidence identifies the evidence class
  - missing `extra_files` manifest evidence identifies the evidence class
- [ ] Run: `pytest test/integration/test_crypt4gh_remote_execution.py -q`
  Expected: FAIL with discovered-output, `extra_files`, verifier, allow-list, or expiry-behavior assertions.
- [ ] Implement discovered-output selection using core persisted dataset mapping rather than collector-subset logic.
- [ ] Implement discovered-output designation/path mapping evidence emission and verifier consumption for persisted discovered payloads.
- [ ] Implement encryption coverage for `extra_files` payload files plus manifest evidence linked to the owning dataset.
- [ ] Implement/reuse payload-marker evidence for declared/discovered payloads and add `extra_files` manifest evidence that the verifier can consume.
- [ ] Implement a universal pre-success fail-closed verifier over all persisted payload candidates for Crypt4GH jobs.
- [ ] Implement plaintext allow-list exclusions for framework/control artifacts and the current `stdout/stderr` exception without weakening dataset-payload enforcement.
- [ ] Implement Galaxy-side TTL preflight before the first B contact.
- [ ] Implement fail-closed mid-run expiry handling during output finalization.
- [ ] Re-run: `pytest test/integration/test_crypt4gh_remote_execution.py -q`
  Expected: PASS.
- [ ] Refactor any duplicated selection, evidence, verifier, or expiry logic.
- [ ] Re-run: `pytest test/integration/test_crypt4gh_remote_execution.py -q`
  Expected: PASS.
- [ ] Commit checkpoint in the Galaxy repo:
  `git add lib/galaxy/tools/crypt4gh_remote_execution.py test/unit/jobs/test_crypt4gh_remote_execution.py test/integration/test_crypt4gh_remote_execution.py && git commit -m "feat: enforce crypt4gh persisted output coverage"`

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
- [ ] Confirm the full integration module now includes the addendum proof cases for discovered outputs, `extra_files`, dataset-centric path divergence, verifier failure, and plaintext allow-list behavior.
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

- **User Check-in A:** Pause if Galaxy's real output-import/discovery behavior makes it impossible to preserve the dataset-centric persisted-payload contract while keeping runtime-controlled plaintext staging under `_crypt/outputs/` where applicable.
- **User Check-in B:** Pause if `remote_tool_eval.py` cannot support the required non-Pulsar tracer bullet without changing the approved “thin entrypoint + dedicated helper” boundary.
- **User Check-in C:** Pause if recryptor B needs lookup state beyond the approved minimal key-id index metadata (`user_hash`, `expiration`) or cannot preserve hashed-directory linkage as source of truth.
- **User Check-in D:** Pause if output-enforcement correctness appears to require plaintext exceptions beyond the approved framework/control allow-list plus the temporary `stdout/stderr` exception.

## Follow-up work explicitly out of this plan

- Pulsar compatibility verification and implementation
- Authentication/authorization hardening between Galaxy, A, and B
- Any mixed encrypted/plain final dataset import policy
- Any compute-key renewal workflow
- Secure `stdout/stderr` handling beyond the temporary plaintext exception noted in the 2026-07-14 addendum
