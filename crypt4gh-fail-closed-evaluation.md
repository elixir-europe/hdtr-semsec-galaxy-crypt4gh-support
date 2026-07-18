# Crypt4GH fail-closed evaluation (post newest plan implementation)

## Context

This report documents the **post-implementation fail-closed evaluation** after the newest Crypt4GH output-enforcement plan cycle was implemented.

It summarizes current fail-closed behavior and known remaining gaps across:

- `lib/galaxy/tools/crypt4gh_remote_execution.py`
- `lib/galaxy/tools/remote_tool_eval.py`
- `lib/galaxy/model/store/discover.py`
- `lib/galaxy/job_execution/output_collect.py`
- `lib/galaxy/jobs/__init__.py`
- related command/bootstrap and wrapper paths

> Note: this write-up is based on already-collected findings for this branch/work item (no new investigation requested for this document update).

---

## Code paths evaluated and current coverage status

| Code path | Current fail-closed coverage | Status |
|---|---|---|
| **Remote input preparation** (`build_crypt4gh_remote_compute_environment`, `_prepare_plaintext_inputs`) | Enforces Crypt4GH presence + metadata + key TTL checks before remote call; fails closed on malformed/failed recrypt/decrypt. | **Covered (strong)** |
| **Remote tool command wrapping** (`build_crypt4gh_cleanup_wrapped_command`) | Preserves tool/postrun failures and runs cleanup path, propagating cleanup failure marker/exit. | **Covered (strong)** |
| **Declared output finalization** (`finalize_declared_crypt4gh_outputs`) | Requires explicit `output_path` targets, enforces working-directory containment for plaintext candidates, and purges on exception. | **Covered (strong)** |
| **Discovered output finalization** (`_maybe_finalize_crypt4gh_about_to_persist_payload`, `_maybe_finalize_crypt4gh_assigned_primary_output`) | Finalizes `.c4gh` discovered/assigned outputs from working-dir `output_path` only (no `dataset_output_path` passed), then persists encrypted bytes. | **Covered (strong)** |
| **Extension resolution + marker application** (`_resolve_discovered_crypt4gh_extension`, `_apply_crypt4gh_marked_extensions`) | `require_crypt4gh_extension` hardens resolution when finalization context is active; marker-based extension re-application exists post-collection. | **Covered (partial)** |
| **Pre-success output evidence verifier** (`verify_crypt4gh_pre_success_output_evidence`) | Validates marker and mapping evidence before successful job completion; can force job error on verifier failure. | **Covered (strong)** |
| **Error-state cleanup in remote eval** (`remote_tool_eval.py` exception handler, cleanup snippet) | Best-effort cleanup invoked on remote eval failures before script finalization. | **Covered (partial)** |
| **Local (non-remote) evaluation path** | Crypt4GH readiness now fail-closes unless all required remote settings are present, including `outputs_to_working_directory=true`. | **Covered (guarded fail-closed)** |
| **Pulsar / `for_pulsar` branch behavior parity** | Known divergence risk in wrapper/cleanup equivalence depending on command assembly path. | **Gap** |
| **Out-of-tree / arbitrary path payload writes** | Finalize/purge logic can be bypassed by tool writes outside tracked targets/job dir. | **Gap** |

---

## Known fail-closed gaps (with mitigation status)

## 1) Local vs remote evaluation

- **Gap**: Crypt4GH finalization/cleanup orchestration is tied to remote tool-evaluation flow; local destinations can skip equivalent finalization safeguards.
- **Mitigated?**: **Yes (for Crypt4GH inputs in this execution model)**. Readiness now fail-closes unless remote prerequisites are met, including `tool_evaluation_strategy=remote`, `metadata_strategy=extended`, remote staging + transparent matching, `outputs_to_working_directory=true`, and reencryption URL.
- **Validation added**:
  - readiness-unit coverage for remote prerequisite combinations,
  - integration coverage asserting local strategy is rejected for transparent-adapted Crypt4GH inputs,
  - readiness-unit coverage asserting local strategy is rejected for explicit `.c4gh` tool inputs and non-transparent Crypt4GH input handling.
- **Still needed**:
  - broader parity/performance testing for additional destination edge combinations where appropriate.
- **Priority / severity**: **Reduced (Low-Medium, mostly parity/coverage-related)**.

## 2) Marker-dir timing / race conditions

- **Gap**: Marker presence can lag discovery-time extension resolution; race windows can misclassify ext if marker-only heuristics are used.
- **Mitigated?**: **Partially**. `require_crypt4gh_extension` reduces this risk when finalization context exists.
- **Still needed**:
  - ensure all relevant callers use context-driven `require_crypt4gh_extension`,
  - reduce dependence on marker-dir existence as sole signal in mixed call paths.
- **Priority / severity**: **Medium-High**.

## 3) Purge path safety (containment)

- **Gap**: Purge/delete paths require continued hardening against edge-case filesystem behavior.
- **Mitigated?**: **Yes (for current declared/discovered finalize + purge containment paths)**.
- **Mitigation implemented**:
  - Added traversal-focused negative coverage that proves declared-finalization targets with `..` parent-segment traversal are rejected before encryption side effects:
    - `test_finalize_declared_outputs_rejects_traversal_output_target_via_parent_segments`
  - Added symlink traversal negative coverage for extra-files finalization and hardened runtime containment checks:
    - `test_finalize_declared_outputs_rejects_extra_files_symlink_traversal`
    - `_finalize_extra_files_payloads(...)` now explicitly enforces `_assert_path_within_allowed_roots(...)` per resolved extra-files payload before encryption/rewrite.
  - Existing allowed-root checks continue to guard output/plaintext/marker/manifest paths in finalize and purge flows.
- **Residual risk / follow-up**:
  - add permission-denied/race-condition stress coverage to improve diagnostics confidence under hostile filesystem timing.
- **Priority / severity**: **Reduced (Low-Medium; race/permissions follow-up)**.

## 4) Pulsar / `for_pulsar` branch divergence

- **Gap**: Cleanup/finalization wrapping may not execute identically across Pulsar-oriented command assembly paths.
- **Mitigated?**: **Partially**. Core wrapper exists, but parity across all Pulsar branches is not fully verified.
- **Still needed**:
  - targeted Pulsar branch tests asserting wrapper + postrun + cleanup execution ordering,
  - verification that failure semantics match non-Pulsar remote path.
- **Priority / severity**: **High**.

## 5) Symlink / race / permission failures in best-effort purge

- **Gap**: Cleanup may fail due to permission errors, symlink behavior, concurrent file mutation, mount behavior.
- **Mitigated?**: **Partially (improved)**.
- **Mitigation implemented**:
  - Best-effort postrun purge now validates each purge candidate against `_ALLOWED_ROOTS` before delete operations in the embedded finalize script.
  - Best-effort postrun purge now treats symlink paths as unlink targets (using `lexists` + `islink` + `unlink`) rather than recursing through symlinked directories.
  - Added regression coverage for both behaviors:
    - `test_finalize_command_best_effort_purge_skips_paths_outside_allowed_roots_on_import_failure`
    - `test_finalize_command_best_effort_purge_unlinks_extra_files_directory_symlink_on_import_failure`
- **Still needed**:
  - permission-denied and concurrent-mutation stress coverage,
  - clearer operator diagnostics for partial purge outcomes under hostile runtime conditions.
- **Priority / severity**: **Reduced (Medium; permission/race follow-up remains)**.

## 6) Output written outside job working directory

- **Gap**: Tool/postrun may still write plaintext outside tracked target set; finalize/purge routines handle only tracked paths.
- **Mitigated?**: **Partially**. Current readiness requires `outputs_to_working_directory=true` for Crypt4GH path and discovered hooks now finalize from working-dir paths only.
- **Still needed**:
  - stronger constraints on writable paths for Crypt4GH jobs,
  - verification that unexpected plaintext artifacts are detected/fail the job before success.
- **Priority / severity**: **High**.

## 7) Path construction validation for finalize-about-to-persist

- **Gap**: `finalize_about_to_persist_crypt4gh_payload` accepts string paths from caller context; limited defensive path validation.
- **Mitigated?**: **Yes (for current finalize-about-to-persist call paths)**.
- **Mitigation implemented**:
  - `finalize_about_to_persist_crypt4gh_payload(...)` now requires explicit non-empty `allowed_root_paths`; it fail-closes with `Crypt4GHRemoteExecutionError` when caller provenance is missing.
  - Existing path containment assertions continue to enforce that output, marker, plaintext, and optional auxiliary paths remain within declared roots before finalization and purge actions.
  - Added regression coverage for missing-provenance refusal and updated direct finalize callsites/tests to pass explicit roots:
    - `test_finalize_about_to_persist_payload_requires_explicit_allowed_root_provenance`
    - Updated direct finalize tests in `test_crypt4gh_output_finalization_about_to_persist.py` and `test_crypt4gh_remote_execution.py` to provide explicit `allowed_root_paths`.
- **Residual risk / follow-up**:
  - keep expanding caller-audit coverage so any newly introduced finalize-about-to-persist entry points must also provide explicit provenance roots.
- **Priority / severity**: **Reduced (Low-Medium; caller-audit follow-up)**.

## 8) Compute-key TTL / expiration window

- **Gap**: Long-running jobs can approach key expiry between early TTL checks and finalization time.
- **Mitigated?**: **Partially**. Minimum TTL checks and finalization-time expiry checks are in place.
- **Still needed**:
  - stronger end-to-end TTL policy for long walltime jobs,
  - explicit tests for near-expiry and mid-run expiry scenarios.
- **Priority / severity**: **Medium-High**.

## 9) Discovery callers not consistently using `require_crypt4gh_extension`

- **Gap**: Some paths can call module-level extension resolution in ways that bypass context-enforced requirement semantics.
- **Mitigated?**: **Yes (for current discovery metadata and collection entry points)**.
- **Mitigation implemented**:
  - `ModelPersistenceContext.set_datasets_metadata(...)` now accepts and forwards `require_crypt4gh_extension` into `_resolve_discovered_crypt4gh_extension(...)` for discovered metadata paths.
  - Discovery callsites now pass context-derived enforcement consistently:
    - `ModelPersistenceContext.create_dataset(...)` passes `require_crypt4gh_extension=bool(self.crypt4gh_output_finalization_context())`
    - `ModelPersistenceContext._populate_elements(...)` passes the same flag for collection/discovery metadata assignment.
  - Added regression coverage to prevent extension-resolution bypass in metadata path:
    - `test_set_datasets_metadata_can_require_crypt4gh_extension_resolution`
  - Updated existing monkeypatch signature coverage in `test_discovered_crypt4gh_metadata_path_clears_compute_keypair_without_generic_set_meta` to keep direct-call regression aligned with the new parameter contract.
- **Residual risk / follow-up**:
  - continue auditing any newly introduced extension-resolution callers to ensure context-enforced semantics remain the default and no module-level bypasses are reintroduced.
- **Priority / severity**: **Reduced (Low-Medium; caller-audit follow-up)**.

## 10) Incomplete edge-case test coverage

- **Gap**: Coverage is still thin for containment safety, local evaluation behavior, Pulsar parity, traversal/symlink attacks, and TTL race windows.
- **Mitigated?**: **Partially (improved)**. Unit/integration coverage now includes discovered-hook working-dir-only finalization semantics, `outputs_to_working_directory` readiness requirement, explicit symlink-cleanup failure-path coverage for declared-output purge, permission-denied purge diagnostics assertions, and concurrent-mutation diagnostics assertions for output payload, marker, and manifest cleanup races.
- **Still needed**:
  - add broader concurrent-mutation cleanup stress tests for extra-files directory race patterns,
  - add Pulsar branch parity tests,
  - add additional TTL boundary and race-window scenarios.
- **Priority / severity**: **High**.

## 11) Dynamic datatype registration warning for non-preregistered `*.c4gh` variants

- **Gap**: Datasets whose base datatype lacks a preregistered `.c4gh` variant (example: `txt.c4gh`) may encrypt successfully but emit registration/runtime warning sequences, including `SafeStringWrapper__...NoneDataset... type() doesn't support MRO entry resolution`.
- **Mitigated?**: **Not yet**.
- **Still needed**:
  - isolate root cause between dynamic datatype creation and wrapper/subclass handling,
  - add targeted regression tests for non-preregistered datatype families.
- **Priority / severity**: **Medium-High**.

## 12) Metadata reset gap for “modify input dataset” style tools

- **Gap**: For tools that transform/overwrite based on a specific input dataset, output metadata can retain stale Crypt4GH fields from input metadata. Observed behavior: tags removed and `crypt4gh_dataset_header_sha256` recalculated, but `crypt4gh_header`, `crypt4gh_metadata_header_sha256`, `crypt4gh_compute_keypair_id`, and `crypt4gh_compute_keypair_expiration_date` may not be reset.
- **Mitigated?**: **Yes (for metadata-source / modify-input archetype in current flow)**.
- **Mitigation implemented**:
  - `Crypt4GHDynamicCompressedArchive.set_meta(...)` now treats `crypt4gh_clear_compute_keypair=True` as a canonical reset path that falls back `crypt4gh_header` to the actual dataset header unless an explicit replacement header is supplied.
  - This ensures `crypt4gh_metadata_header_sha256` is recomputed from the dataset header in clear mode and can no longer retain stale recrypt metadata-header hashes from inherited input metadata.
  - Added unit coverage for stale-header reset semantics:
    - `test_crypt4gh_set_meta_clear_compute_keypair_resets_stale_metadata_header`
  - Added integration coverage for metadata-source output behavior in remote Crypt4GH flow:
    - `test_metadata_source_outputs_reset_crypt4gh_header_metadata_for_crypt4gh_jobs`
- **Residual risk / follow-up**:
  - broaden coverage to additional modify-input patterns beyond current metadata-source fixture and keep parity checks for non-primary/discovered output variants.
- **Priority / severity**: **Reduced (Low-Medium; coverage breadth follow-up)**.

## 13) Discovery-path encryption bypass

- **Gap**: A collection-discovery branch could persist discovered outputs without running Crypt4GH finalization before object-store persistence.
- **Mitigated?**: **Yes (for the collection discovery branch in current flow)**.
- **Mitigation implemented**:
  - `ModelPersistenceContext.update_object_store_with_datasets()` now calls `_maybe_finalize_crypt4gh_about_to_persist_payload(...)` before `object_store.update_from_file(...)`.
  - Added unit coverage for both success and fail-closed behavior:
    - `test_collection_discovery_path_finalizes_crypt4gh_before_object_store_persist`
    - `test_collection_discovery_path_fails_closed_when_crypt4gh_finalization_fails`
  - Added integration coverage for collection discovery output encryption and plaintext cleanup in remote Crypt4GH flow:
    - `test_collection_discovery_split_outputs_are_encrypted_for_crypt4gh_jobs`
- **Residual risk / follow-up**:
  - keep broader discovery archetype coverage expanding (especially third-party toolshed variants) as additional fixtures become available.
- **Priority / severity**: **Reduced (Low-Medium; coverage breadth follow-up)**.

## 14) `CRYPT4GH_DEBUG` output still enabled

- **Gap**: `CRYPT4GH_DEBUG` diagnostic prints are still emitted.
- **Mitigated?**: **Intentionally deferred** (still useful for current debugging).
- **Still needed**:
  - remove or gate debug output behind a dedicated opt-in debug flag before merge/release.
- **Priority / severity**: **Low (operational hygiene)**.

---

## Current fail-closed posture

Overall posture is **materially improved but not yet complete fail-closed across all execution modes**.

- **Strongest coverage**: remote evaluation path, declared/discovered output finalization hooks (working-dir-first), pre-success verifier, cleanup wrapping, marker/mapping evidence checks.
- **Residual risk concentration**: Pulsar parity, untracked output locations, metadata-reset consistency, and unresolved discovery subpaths.

---

## Hardened in this evaluation cycle

Key hardening already present on this branch/work item includes:

- fail-closed rejection of legacy discovered-selector targets in declared finalization,
- context-driven `require_crypt4gh_extension` support in discovery resolution,
- remote-tool bootstrap/interpreter hardening (GALAXY_PYTHON usage in command factory path),
- cleanup-wrapped command flow and remote-eval failure cleanup path,
- pre-success verification gates for payload markers, discovered mapping, and extra-files manifest evidence,
- working-dir-only discovered-output finalization hook alignment (no `dataset_output_path` in discovered hooks),
- readiness requirement upgrade to include `outputs_to_working_directory=true` for Crypt4GH flow.

---

## Recommendations and proposed next steps

### Completed gap closures in this cycle

- **Gap #3 (partial)**: canonical allowed-root checks now guard finalize and purge path operations for current declared/discovered hooks.
- **Gap #7 (partial)**: finalize-about-to-persist path handling now includes allowed-root checks, and discovered hooks no longer pass `dataset_output_path` into finalize calls.
- **Gap #1 (major mitigation)**: readiness now fail-closes local execution for Crypt4GH inputs unless required remote settings are present, including `outputs_to_working_directory=true`.

### Proposed fix order for remaining gaps (excluding Gap #4)

This order prioritizes highest fail-closed risk first, defers likely policy/threshold decisions toward the end, and keeps **Gap #14 last** as requested.

1. **Gap #12** — enforce canonical Crypt4GH metadata reset/rewrite for modify-input tool patterns.
2. **Gap #7** — strengthen path-provenance validation for finalize-about-to-persist callers.
3. **Gap #9** — complete discovery caller audit and align all extension resolution paths to context-aware enforcement.
4. **Gap #3** — finish containment hardening with traversal-focused negative coverage.
5. **Gap #5** — harden best-effort purge under symlink/permission/race edge conditions.
6. **Gap #10** — broaden edge-case test coverage for containment and cleanup behaviors.
7. **Gap #1** — add broader destination parity/performance coverage for local-vs-remote enforcement boundaries.
8. **Gap #6** — define and enforce policy for out-of-tree writes and pre-success plaintext detection.
9. **Gap #8** — define stronger TTL/walltime policy and implement boundary regression coverage.
10. **Gap #2** — settle marker-timing/race handling policy and remove marker-only decision windows.
11. **Gap #11** — resolve dynamic datatype registration behavior for non-preregistered `.c4gh` variants.
12. **Gap #14** — remove/gate `CRYPT4GH_DEBUG` output before merge/release.

### Follow-on notes

- Gaps near the end of the order are intentionally the ones most likely to require policy confirmation or threshold decisions.
- Gap #10 is still broad; in practice, tests should be added incrementally while fixing each earlier gap.

---

## Bottom line

The Crypt4GH fail-closed posture is **substantially stronger** after recent hardening, including working-dir-only discovered-output finalization and stricter readiness prerequisites. Remaining work is concentrated in **Gap #4 plus unresolved gaps #2/#5/#6/#8/#10/#11/#12/#14**, where correctness and complete fail-closed coverage must still be proven across all discovery and metadata edge paths.

---

## Decision record

### 2026-07-18 — Gap #13 regression coverage targets in-tree collection discovery fixture

- **Decision**: use the built-in `split` collection-discovery tool fixture for the Gap #13 integration regression test instead of a toolshed-only `split_file_to_collection` ID.
- **Why**: the toolshed fixture is not guaranteed to be present in this hermetic integration test environment (HTTP 400 tool-not-found), which produces a false negative unrelated to Crypt4GH logic.
- **Security impact**: neutral-to-positive. The test still exercises the collection discovery branch that previously bypassed encryption and verifies fail-closed properties (encrypted payloads + no plaintext residue + designation mapping to encrypted outputs).
- **Follow-up**: add/enable third-party toolshed variant coverage when a stable fixture/install path is available in CI.

### 2026-07-18 — Gap #12 canonical metadata reset in clear-compute-keypair path

- **Decision**: treat `crypt4gh_clear_compute_keypair=True` in `Crypt4GHDynamicCompressedArchive.set_meta(...)` as an explicit canonical reset path for metadata header selection.
- **Why**: modify-input/metadata-source outputs can inherit stale recrypt header metadata from inputs. Clearing compute keypair fields without resetting the metadata header leaves inconsistent Crypt4GH metadata (`metadata_header_sha256 != dataset_header_sha256`) and stale header provenance.
- **Security impact**: positive. Returned outputs now clear compute keypair metadata and re-anchor metadata-header hash state to the actual dataset header unless an explicit replacement header is provided by the finalization path.
- **Follow-up**: expand regression coverage across more modify-input archetypes and non-primary output paths to ensure reset behavior remains consistent.

### 2026-07-18 — Gap #7 require explicit allowed-root provenance for finalize-about-to-persist

- **Decision**: make `allowed_root_paths` mandatory for `finalize_about_to_persist_crypt4gh_payload(...)` and fail closed when absent.
- **Why**: deriving fallback roots from target-path strings alone is weaker provenance and can obscure caller intent. Requiring explicit roots forces the caller to bind finalization to a known safe scope.
- **Security impact**: positive. Finalization now refuses ambiguous/no-provenance invocations before any encrypt/rewrite/purge side effects begin.
- **Follow-up**: maintain caller-audit tests for any new finalize-about-to-persist entry points to prevent regressions to implicit root derivation.

### 2026-07-18 — Gap #9 enforce context-driven extension resolution in discovery metadata paths

- **Decision**: plumb `require_crypt4gh_extension` through `ModelPersistenceContext.set_datasets_metadata(...)` and all current discovery callers in that path.
- **Why**: metadata-setting entry points could otherwise call `_resolve_discovered_crypt4gh_extension(...)` without context-enforced requirement semantics, allowing extension resolution behavior to drift from active Crypt4GH finalization context.
- **Security impact**: positive. Discovery metadata and collection paths now consistently honor Crypt4GH-context extension enforcement, reducing risk of unencrypted extension assignment drift.
- **Follow-up**: keep caller-audit regression coverage for new discovery/metadata entry points that invoke extension resolution.

### 2026-07-18 — Gap #3 traversal-focused containment hardening for finalize/purge flows

- **Decision**: harden and verify containment by adding explicit traversal/symlink negative coverage in declared output finalization, including extra-files payload handling.
- **Why**: allowed-root checks existed, but traversal-focused regressions needed to prove parent-segment and symlink escape attempts fail closed across both primary output and extra-files payload paths.
- **Security impact**: positive. Finalization now rejects out-of-root traversal payloads earlier and enforces per-extra-file allowed-root containment before encryption/rewrite.
- **Follow-up**: add race/permission stress tests and diagnostics assertions for best-effort purge edge conditions.

### 2026-07-18 — Gap #5 harden best-effort postrun purge path handling

- **Decision**: harden embedded postrun purge behavior to fail safer under import/finalize errors by validating purge candidates against allowed roots and unlinking symlink paths directly.
- **Why**: fallback purge previously deleted without explicit root validation in the embedded script and relied on `exists`/`isdir` handling that could leave symlink directory entries behind.
- **Security impact**: positive. Best-effort purge no longer attempts out-of-scope deletions and removes symlink path entries without traversing target directories.
- **Follow-up**: add permission-denied and race-condition stress coverage with stronger diagnostics assertions.

### 2026-07-18 — Gap #10 prioritize symlink cleanup failure-path coverage in declared-output purge

- **Decision**: add a high-risk edge-case regression for declared-output purge when `extra_files_output_path` is a directory symlink and finalization fails before payload rewrite.
- **Why**: current purge behavior used `shutil.rmtree(...)` for extra-files directories, which raises `OSError` on symlink paths and can leave plaintext-linked entries behind in failure cleanup paths.
- **Security impact**: positive. Failure-path purge now unlinks symlink extra-files entries directly (without recursive traversal) and preserves fail-closed cleanup behavior when encryption fails.
- **Follow-up**: add permission-denied and concurrent-mutation assertions for purge diagnostics to complete broader edge-case coverage.

### 2026-07-18 — Gap #10 add permission-denied diagnostics assertions for failure-path purge

- **Decision**: require purge failure logs to include exception-class and message context, then add regression coverage asserting `PermissionError` diagnostics for extra-files cleanup failure.
- **Why**: prior logs captured traceback data, but primary log messages did not reliably include explicit exception-class context; assertions for operator-visible diagnostics were missing.
- **Security impact**: positive. Fail-closed behavior remains unchanged while diagnostics become more explicit and test-enforced for permission-denied cleanup failures.
- **Follow-up**: add concurrent-mutation/race cleanup diagnostics assertions and stress tests to complete this Gap #10 subtrack.

### 2026-07-18 — Gap #10 add concurrent-mutation diagnostics assertions for manifest purge races

- **Decision**: classify `FileNotFoundError` during extra-files manifest purge as concurrent mutation and log it explicitly at warning level with exception context.
- **Why**: cleanup races can remove manifest files between existence-check and unlink; this is an expected best-effort race condition and should emit explicit, non-ambiguous diagnostics distinct from general cleanup failures.
- **Security impact**: positive. Fail-closed behavior is preserved while operator diagnostics now distinguish race-driven manifest cleanup events from generic purge failures.
- **Follow-up**: extend concurrent-mutation assertions to additional purge targets (output payloads, markers, extra-files directories) and add stress-oriented coverage where feasible.

### 2026-07-18 — Gap #10 extend concurrent-mutation diagnostics assertions to output payload and marker purge targets

- **Decision**: treat `FileNotFoundError` during output-payload and encrypted-marker purge as explicit concurrent-mutation warnings, aligned with the manifest race policy.
- **Why**: output and marker files can be concurrently removed by external runtime events between existence checks and unlink calls; diagnostics should classify these as race conditions rather than generic purge failures.
- **Security impact**: positive. Cleanup remains fail-closed while diagnostics become consistent across core purge targets (payload/marker/manifest), improving incident triage clarity.
- **Follow-up**: add extra-files directory race-path assertions and stress-oriented variants to complete concurrent-mutation coverage breadth.
