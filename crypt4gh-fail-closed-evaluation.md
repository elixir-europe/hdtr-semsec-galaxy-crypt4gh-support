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
- **Mitigated?**: **Yes (for Crypt4GH inputs in this execution model)**. Readiness now fail-closes unless remote prerequisites are met, including effective `tool_evaluation_strategy=remote` (destination override or global default), `metadata_strategy=extended`, remote staging + transparent matching, `outputs_to_working_directory=true`, and reencryption URL.
- **Validation added**:
  - readiness-unit coverage for remote prerequisite combinations,
  - integration coverage asserting local strategy is rejected for transparent-adapted Crypt4GH inputs,
  - readiness-unit coverage asserting local strategy is rejected for explicit `.c4gh` tool inputs and non-transparent Crypt4GH input handling,
  - readiness/helper coverage asserting effective global `tool_evaluation_strategy=remote` parity when destination params omit the strategy key.
- **Still needed**:
  - broader integration/performance parity checks for additional destination edge combinations where appropriate.
- **Priority / severity**: **Reduced (Low-Medium, mostly parity/coverage-related)**.

## 2) Marker-dir timing / race conditions

- **Gap**: Marker presence can lag discovery-time extension resolution; race windows can misclassify ext if marker-only heuristics are used.
- **Mitigated?**: **Yes (for current discovery/finalization call paths)**.
- **Mitigation implemented**:
  - `_resolve_discovered_crypt4gh_extension(...)` now short-circuits already-encrypted extensions before marker checks.
  - marker presence alone is no longer treated as sufficient evidence for implicit extension upgrades in non-required paths; evidence now requires marker files (`discovered_designations.json`, `path_*.encrypted`, or `ds_*.encrypted`).
  - marker-directory list races (`FileNotFoundError`/`OSError` between `isdir` and `listdir`) are treated as no marker evidence, while context-required paths still force `.c4gh` extension resolution through `require_crypt4gh_extension=True`.
  - added regression coverage for empty marker directories, `ds_*.encrypted` marker evidence, and list-race behavior in both required and non-required contexts.
  - added explicit `OSError` list-race regression coverage mirroring existing `FileNotFoundError` race semantics:
    - `test_resolve_discovered_extension_treats_marker_dir_oserror_race_as_no_evidence_without_required_context`
    - `test_resolve_discovered_extension_requires_crypt4gh_when_marker_dir_oserror_races`
- **Residual risk / follow-up**:
  - extend integration coverage to include more end-to-end discovery/finalize race simulations under concurrent cleanup pressure.
- **Priority / severity**: **Reduced (Low-Medium; mostly broader integration hardening)**.

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
- **Mitigated?**: **Partially (improved)**. Core wrapper exists; Pulsar branch command assembly now enforces explicit shell-command separation, groups the downstream command chain under a single gated segment, and preserves `&&`-gated follow-up sequencing between remote-eval wrapper invocation and tool-script execution, but parity across all Pulsar branches is not fully verified.
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
  - Best-effort postrun purge now emits explicit race/permission diagnostics when delete operations fail under hostile timing or filesystem permissions:
    - `Crypt4GH best-effort purge observed concurrent mutation for <path> (FileNotFoundError: ...)`
    - `Crypt4GH best-effort purge failed for <path> (PermissionError: ...)`
  - Added regression coverage for both behaviors:
    - `test_finalize_command_best_effort_purge_skips_paths_outside_allowed_roots_on_import_failure`
    - `test_finalize_command_best_effort_purge_unlinks_extra_files_directory_symlink_on_import_failure`
    - `test_finalize_command_best_effort_purge_logs_concurrent_mutation_for_unlink_race`
    - `test_finalize_command_best_effort_purge_logs_permission_errors_without_masking_finalize_failure`
  - Best-effort postrun purge now emits a partial-outcome summary when any purge candidate is skipped/failed/raced, with explicit counters for removed/missing/outside-root/concurrent-mutation/failed outcomes.
  - Added stress coverage for multi-path race and permission-denied partial outcomes:
    - `test_finalize_command_best_effort_purge_reports_partial_outcome_for_concurrent_mutation_stress`
    - `test_finalize_command_best_effort_purge_reports_partial_outcome_for_permission_denied_stress`
- **Still needed**:
  - mount-behavior and cross-filesystem edge coverage for best-effort purge paths.
- **Priority / severity**: **Reduced (Medium-Low; mount/filesystem follow-up remains)**.

## 6) Output written outside job working directory

- **Gap**: Tool/postrun may still write plaintext outside tracked target set; finalize/purge routines handle only tracked paths.
- **Mitigated?**: **Partially (improved)**. Current readiness requires `outputs_to_working_directory=true` for Crypt4GH path, discovered hooks finalize from working-dir paths only, pre-success evidence validates payload header bytes, and pre-success verifier fails closed on residual plaintext staging artifacts under `_crypt/outputs` and `_crypt/inputs` even when untracked by dataset associations. Finalize-path containment now distinguishes plaintext roots from broader output roots so object-store dataset targets remain valid while plaintext provenance stays constrained.
- **Mitigation implemented**:
  - Removed the over-aggressive JobWrapper pre-success scope gate for tracked output payload paths because it incorrectly blocked legitimate object-store dataset destinations.
  - Added explicit `plaintext_root_paths` provenance to declared output targets and threaded it through finalize checks.
  - Finalize now enforces `plaintext_path` containment against plaintext roots while continuing to enforce broader output/marker path containment via `allowed_root_paths`.
  - Added regression coverage:
    - `test_verify_crypt4gh_pre_success_evidence_allows_tracked_payload_on_object_store_path`
    - `test_finalize_declared_outputs_rejects_plaintext_path_outside_plaintext_root_even_when_output_paths_allowed`
    - `test_finalize_declared_outputs_allows_dataset_output_path_outside_working_root_with_plaintext_root_constrained`
- **Still needed**:
  - stronger constraints on writable paths for Crypt4GH jobs,
  - broader discovery/integration checks for unexpected out-of-tree plaintext artifacts beyond tracked output datasets.
- **Priority / severity**: **Reduced (Medium-High)**.

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
- **Mitigated?**: **Partially (improved)**. Minimum TTL checks and finalization-time expiry checks are in place; destination walltime-derived TTL now preserves the default floor instead of weakening it for short walltime declarations, and integration coverage now exercises both threshold-near launch acceptance and finalization-time expiry fail-closed behavior.
- **Mitigation implemented**:
  - Added integration coverage for near-threshold launch acceptance using effective destination-derived minimum TTL plus conservative slack:
    - `test_remote_helper_allows_launch_when_stored_ttl_is_just_above_threshold`
  - Added integration coverage for finalization-time key validity outcomes:
    - `test_output_finalization_fails_closed_when_compute_key_expires_mid_run`
    - `test_output_finalization_succeeds_when_compute_key_remains_valid_through_completion`
  - Added focused unit coverage for additional TTL boundary scenarios:
    - `test_should_run_allows_ttl_just_above_default_boundary`
    - `test_should_run_allows_ttl_just_above_destination_derived_boundary`
    - `test_build_environment_rejects_exact_destination_derived_ttl_boundary_before_any_recrypt_call`
- **Still needed**:
  - broader destination-class parity for TTL policy under non-default scheduling topologies.
- **Priority / severity**: **Reduced (Medium)**.

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
- **Mitigated?**: **Partially (improved)**. Unit/integration coverage now includes discovered-hook working-dir-only finalization semantics, `outputs_to_working_directory` readiness requirement, explicit symlink-cleanup failure-path coverage for declared-output purge, permission-denied purge diagnostics assertions, concurrent-mutation diagnostics assertions for output payload/marker/manifest cleanup races (including extra-files directory type-flip race patterns), pre-success verifier regression coverage for plaintext/symlinked/missing/unreadable extra-files payloads under manifest-presence and scan-race conditions, and expanded TTL race-window checks (exact-threshold fail-closed plus just-above-threshold acceptance).
- **Mitigation implemented (additional in this slice)**:
  - Added extra-files directory race-pattern stress coverage for type-flip concurrent mutation during purge:
    - `test_finalize_declared_outputs_logs_concurrent_mutation_when_extra_files_directory_type_flips_during_purge`
  - Hardened purge diagnostics classification so `NotADirectoryError`/`IsADirectoryError` path-type races are reported as concurrent mutation diagnostics rather than generic cleanup failures.
  - Added additional TTL race-window scenarios covering both acceptance and rejection boundaries in unit + integration paths.
- **Still needed**:
  - add Pulsar branch parity tests,
  - extend destination-class TTL race-window integration checks beyond current fixture topology.
- **Priority / severity**: **High**.

## 11) Dynamic datatype registration warning for non-preregistered `*.c4gh` variants

- **Gap**: Datasets whose base datatype lacks a preregistered `.c4gh` variant (example: `txt.c4gh`) may encrypt successfully but emit registration/runtime warning sequences, including `SafeStringWrapper__...NoneDataset... type() doesn't support MRO entry resolution`.
- **Mitigated?**: **Yes (for current runtime wrapper and warning path)**.
- **Mitigation implemented**:
  - `wrap_with_safe_string(...)` now derives wrapper-module naming from the resolved wrapped class (`inspect.getmodule(wrapped_class)`) instead of the wrapped instance value.
  - this avoids constructing malformed dynamic wrapper bases for `NoneDataset`-like values in the non-preregistered `*.c4gh` flow and removes the warning path (`type() doesn't support MRO entry resolution`) while preserving successful wrapping behavior.
  - added regression coverage:
    - `test_wrap_with_safe_string_does_not_warn_for_nonedataset_and_preserves_wrapper_type`
- **Residual risk / follow-up**:
  - broaden coverage for additional wrapper edge cases where sanitized wrappers compose with dynamically registered runtime datatypes.
- **Priority / severity**: **Reduced (Low-Medium; broader wrapper-edge coverage follow-up)**.

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
- **Mitigated?**: **Yes (gated)**.
- **Mitigation implemented**:
  - `_print_declared_output_target_debug(...)` now emits debug payloads only when `GALAXY_CRYPT4GH_DEBUG=1`.
  - default path no longer emits `CRYPT4GH_DEBUG` lines.
  - added regression coverage for both default-off and opt-in debug behavior:
    - `test_collect_declared_targets_does_not_emit_debug_resolution_payload_by_default`
    - `test_collect_declared_targets_emits_debug_resolution_payload_when_opted_in`
- **Residual risk / follow-up**:
  - if additional `CRYPT4GH_DEBUG` surfaces are introduced later, enforce the same env-gated opt-in policy.
- **Priority / severity**: **Reduced (Low; policy now explicit and test-enforced)**.

---

## Current fail-closed posture

Overall posture is **materially improved but not yet complete fail-closed across all execution modes**.

- **Strongest coverage**: remote evaluation path, declared/discovered output finalization hooks (working-dir-first), pre-success verifier, cleanup wrapping, marker/mapping evidence checks.
- **Residual risk concentration**: Pulsar parity, untracked output locations, metadata-reset consistency, and remaining coverage breadth.

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

The Crypt4GH fail-closed posture is **substantially stronger** after recent hardening, including working-dir-only discovered-output finalization and stricter readiness prerequisites. Remaining work is concentrated in **Gap #4 plus unresolved gaps #5/#6/#8/#10/#11/#12/#14**, where correctness and complete fail-closed coverage must still be proven across all discovery and metadata edge paths.

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

### 2026-07-18 — Gap #1 align effective local-vs-remote strategy parity for missing destination overrides

- **Decision**: use effective strategy resolution (`destination_params.tool_evaluation_strategy` with global-config fallback) in Crypt4GH helper-path and readiness gating.
- **Why**: destination-level strategy keys may be absent in some execution contexts even when global remote evaluation is configured; enforcing only destination-scoped strategy creates false local-vs-remote mismatches and non-parity behavior.
- **Security impact**: positive. Fail-closed semantics are preserved for non-remote effective strategy while reducing false-negative readiness/helper rejections when effective global remote strategy is active.
- **Follow-up**: expand integration destination-matrix coverage to include additional execution contexts where destination strategy propagation may differ.

### 2026-07-18 — Gap #6 add pre-success plaintext payload detection for tracked outputs

- **Decision**: extend pre-success evidence verification to assert Crypt4GH header bytes on tracked output dataset payloads, not only marker/map evidence.
- **Why**: marker/mapping evidence alone can be stale or inconsistent with actual payload bytes; tracked outputs should fail closed if bytes remain plaintext at success boundary.
- **Security impact**: positive. Jobs now fail before success when tracked output payloads are not Crypt4GH-encrypted, including discovered outputs with valid mapping but plaintext bytes.
- **Follow-up**: expand integration coverage for out-of-tree plaintext artifact detection and non-tracked path policy enforcement.

### 2026-07-18 — Gap #8 preserve default minimum TTL floor under short destination walltime

- **Decision**: compute destination-derived minimum TTL as `max(default_minimum_ttl, walltime + safety_buffer)` rather than replacing the default with walltime-derived values.
- **Why**: short declared walltime values (for example 10–15 minutes) could otherwise reduce a 24-hour default TTL floor and permit near-expiry keys that contradict conservative fail-closed policy.
- **Security impact**: positive. TTL gate remains conservative under short walltime declarations while still scaling upward for long walltime jobs.
- **Follow-up**: add integration coverage around destination-specific walltime propagation and end-to-end expiry windows.

### 2026-07-18 — Gap #2 harden marker-evidence semantics and marker-directory race handling

- **Decision**: make marker evidence explicit and race-tolerant in discovery extension resolution, and rely on context-required enforcement for fail-closed finalization paths.
- **Why**: marker-directory existence alone is timing-sensitive and can produce false extension upgrades in mixed call paths; concurrent cleanup can also remove marker directories between existence and listing checks.
- **Security impact**: positive. Non-required discovery paths no longer upgrade extensions based on marker-dir presence alone, while required Crypt4GH contexts still force encrypted extension assignment even when marker evidence races.
- **Follow-up**: add broader integration race simulations around discovery and finalization boundaries under concurrent cleanup stress.

### 2026-07-18 — Gap #11 eliminate dynamic wrapper warning path for non-preregistered `*.c4gh` variants

- **Decision**: keep dynamic runtime datatype registration allowed (no new fail-closed hard stop) and remove warning-path instability in safe-string wrapping.
- **Why**: non-preregistered encrypted extensions should continue to function for fail-closed encryption semantics; warning spam from wrapper-class construction obscured signal quality without improving safety.
- **Security impact**: positive. Encryption behavior remains intact while runtime warning noise tied to `NoneDataset` wrapper composition is removed, improving operator signal fidelity.
- **Follow-up**: extend wrapper/datatype composition tests across more dynamic-extension families.

### 2026-07-18 — Gap #14 gate `CRYPT4GH_DEBUG` output behind explicit opt-in

- **Decision**: gate declared-output debug prints behind `GALAXY_CRYPT4GH_DEBUG=1` and keep default execution free of `CRYPT4GH_DEBUG` stdout lines.
- **Why**: always-on debug prints are operational noise and should not leak into normal runtime output; developers still need an explicit troubleshooting switch.
- **Security impact**: positive. Reduces accidental sensitive-context exposure in routine logs/stdout while preserving controlled diagnostics when explicitly enabled.
- **Follow-up**: apply the same opt-in gate to any future `CRYPT4GH_DEBUG` diagnostic surfaces.

### 2026-07-18 — Gap #5 broaden best-effort purge diagnostics for race and permission stress

- **Decision**: strengthen embedded best-effort purge diagnostics in remote postrun finalize flow by classifying removal failures into explicit concurrent-mutation (`FileNotFoundError`) and purge-failure (`PermissionError`/other) messages.
- **Why**: prior best-effort purge behavior could swallow failure details due to `ignore_errors=True`/fallback branching, reducing operator visibility during race and permission incidents.
- **Security impact**: positive. Fail-closed behavior is unchanged while race/permission cleanup outcomes become explicit and test-enforced, improving incident triage and hardening confidence.
- **Follow-up**: extend stress variants to additional filesystem edge conditions (for example mount semantics and deeper extra-files directory mutation races) and keep diagnostics consistency across cleanup entry points.

### 2026-07-18 — Gap #6 add JobWrapper pre-success scope gate for tracked Crypt4GH payload paths

- **Decision**: enforce a pre-success fail-closed scope check in `JobWrapper` so tracked Crypt4GH output payloads must resolve within job-scope roots before the verifier runs.
- **Why**: payload header validation for tracked datasets is necessary but not sufficient if tracked payload references can drift to out-of-scope paths; this adds explicit provenance enforcement at the final success boundary.
- **Security impact**: positive. Jobs now fail before success when tracked Crypt4GH payload paths are out-of-scope, reducing risk of silently accepting out-of-tree payload provenance.
- **Follow-up**: evaluate tightening/parameterizing scope roots per destination mode and expand integration coverage for additional out-of-tree path archetypes.

### 2026-07-18 — Gap #10 fail closed on plaintext extra-files payloads even with valid manifest entries

- **Decision**: extend pre-success evidence verification to validate Crypt4GH header bytes for each expected extra-files payload path whenever a complete extra-files manifest is present.
- **Why**: manifest structure/entries alone can be stale or forged relative to on-disk payload bytes; without payload-byte checks, plaintext extra-files content can evade pre-success failure despite valid manifest metadata.
- **Security impact**: positive. Jobs now fail before success when any expected extra-files payload remains plaintext, even if marker+manifest evidence appears complete.
- **Follow-up**: add additional coverage for unreadable/missing extra-files payload diagnostics under concurrent mutation and permission-denied conditions.

### 2026-07-18 — Gap #10 fail closed on symlinked extra-files payload evidence

- **Decision**: treat symlinked expected extra-files payload paths as invalid pre-success evidence and fail closed even when manifest entries are complete.
- **Why**: reading header bytes through symlinks permits path indirection outside intended payload provenance; a symlink can satisfy marker/manifest shape while pointing at uncontrolled paths.
- **Security impact**: positive. Jobs now fail before success when expected extra-files payload entries resolve as symlinks, tightening provenance guarantees for pre-success evidence.
- **Follow-up**: extend pre-success extra-files checks for additional link-like path forms under destination-specific filesystems and Pulsar parity paths.

### 2026-07-18 — Gap #10 fail closed on missing/unreadable extra-files payload evidence under scan-race and permission-denied conditions

- **Decision**: when an extra-files manifest exists and includes file entries, treat those manifest entries as expected payload evidence even if directory scanning yields zero files, and fail pre-success for missing or unreadable payload bytes.
- **Why**: concurrent mutation can remove or hide directory entries between scan and verification; relying only on live `os.walk(...)` results can silently bypass payload-byte checks exactly when race/permission hardening matters most.
- **Security impact**: positive. Jobs now fail before success when manifest-declared extra-files payloads are missing or unreadable, including scan-race + permission-denied scenarios.
- **Follow-up**: add Pulsar parity coverage for this manifest-authoritative payload-evidence behavior and extend stress cases for transient filesystem semantics.

### 2026-07-18 — Gap #10 tighten TTL boundary checks to reduce race-window acceptance

- **Decision**: treat TTL equality with the configured minimum as insufficient for remote execution gating (`ttl_left <= minimum_ttl` fails closed).
- **Why**: allowing exact-threshold TTL values leaves no scheduling or transport slack and can admit jobs that cross expiry boundary during execution startup.
- **Security impact**: positive. Jobs now fail before remote execution when TTL sits exactly at the configured floor, reducing boundary race acceptance for both default and destination-derived minima.
- **Follow-up**: expand integration coverage for destination-specific and Pulsar paths to validate consistent boundary enforcement outside unit-level gating.

### 2026-07-18 — Gap #4 enforce Pulsar wrapper command separation in remote command assembly

- **Decision**: ensure Pulsar `remote_command_line` preamble insertion uses explicit shell-command separation (`;`) before subsequent command-builder steps.
- **Why**: without an explicit separator, the Pulsar wrapper chain (`... && bash ../tool_script.sh`) can concatenate directly into the next command segment (for example `cd working`), risking malformed shell execution and divergence from non-Pulsar sequencing.
- **Security impact**: positive. Tightens execution determinism for Pulsar command assembly and reduces risk of wrapper/finalization sequencing drift caused by shell-token concatenation.
- **Follow-up**: add additional Pulsar parity tests that assert wrapper + postrun + cleanup ordering and failure-propagation semantics against non-Pulsar paths.

### 2026-07-18 — Gap #4 enforce Pulsar wrapper success-gating parity for follow-up commands

- **Decision**: require Pulsar remote wrapper insertion to keep follow-up command-builder steps under `&&` success gating, matching non-Pulsar failure-propagation semantics.
- **Why**: semicolon-separated follow-up execution can continue job command segments even when remote-eval wrapper/tool-script chain fails, creating divergence in failure behavior and potentially bypassing intended stop-on-failure flow.
- **Security impact**: positive. Improves fail-closed consistency by preventing follow-up command execution after Pulsar wrapper failure in remote command assembly.
- **Follow-up**: expand Pulsar parity coverage to include explicit postrun/cleanup-failure propagation assertions and ordering checks against non-Pulsar wrappers.

### 2026-07-18 — Gap #4 group Pulsar downstream command chain under a single success gate

- **Decision**: wrap Pulsar follow-up command-builder output in a grouped segment (`&& ( ... )`) after the remote-eval/tool-script wrapper chain.
- **Why**: chaining only the first follow-up token (for example `cd working`) under `&&` still allows later command tail segments to execute with semicolon continuation after mid-chain failures; grouped gating aligns Pulsar behavior with non-Pulsar command-chain failure propagation.
- **Security impact**: positive. Reduces divergence risk where partial follow-up execution could continue after wrapper-chain failures, strengthening fail-closed sequencing semantics in Pulsar command assembly.
- **Follow-up**: add targeted parity tests for representative failure points inside grouped follow-up chains (working-dir transition, tool invocation, stdout/stderr capture) and compare against non-Pulsar behavior.

### 2026-07-18 — Gap #6 fail pre-success on residual plaintext staging artifacts outside tracked dataset associations

- **Decision**: extend pre-success evidence verification to scan `_crypt/outputs` for leftover `plaintext` artifacts and fail closed when any remain.
- **Why**: tracked dataset/marker evidence can be green while stale or untracked plaintext staging files persist, leaving residual plaintext risk beyond association-scoped verification.
- **Security impact**: positive. Jobs now fail before success when plaintext staging residues remain under Crypt4GH output staging roots, reducing acceptance of partially cleaned plaintext artifacts.
- **Follow-up**: add integration-level coverage for destination-specific staging layouts and mixed tracked/untracked output topologies.

### 2026-07-18 — Gap #6 extend residual plaintext pre-success checks to `_crypt/inputs`

- **Decision**: expand residual plaintext scan roots from `_crypt/outputs` to include `_crypt/inputs` in pre-success evidence verification.
- **Why**: input-side plaintext staging can remain after processing and is not always represented by tracked output associations; output-only residual scanning misses this class of residue.
- **Security impact**: positive. Jobs now fail before success when plaintext staging artifacts remain under either Crypt4GH output or input staging roots.
- **Follow-up**: add integration-level checks for destination-specific input staging cleanup behavior across remote/Pulsar execution paths.

### 2026-07-19 — Gap #6 constrain plaintext provenance without blocking object-store dataset outputs

- **Decision**: remove the JobWrapper payload-scope gate that rejected tracked object-store output paths, and enforce finalize-time `plaintext_path` containment against explicit `plaintext_root_paths` while keeping broader `allowed_root_paths` for encrypted output/marker containment.
- **Why**: object-store dataset destinations are expected outside the job working directory in supported deployments; fail-closed controls must target plaintext provenance specifically, not legitimate final encrypted destinations.
- **Security impact**: positive. Plaintext paths now fail closed when outside declared plaintext roots, while valid object-store encrypted output destinations remain accepted.
- **Follow-up**: add integration coverage for destination-specific object-store topologies and Pulsar parity for `plaintext_root_paths` propagation.

### 2026-07-19 — Gap #5 add best-effort purge stress diagnostics and partial-outcome counters

- **Decision**: extend best-effort purge fallback reporting with explicit partial-outcome counters and add stress tests for multi-path concurrent mutation and permission-denied purge outcomes.
- **Why**: single-path diagnostic lines are useful but do not summarize aggregate purge posture under hostile runtime behavior; operators need one concise signal describing what was removed, skipped, raced, or failed.
- **Security impact**: positive. Fail-closed behavior is preserved while post-failure purge observability improves, reducing ambiguity during incident triage.
- **Follow-up**: broaden mount/filesystem-behavior stress coverage where purge semantics differ across runtime environments.

### 2026-07-19 — Gap #10/#8 expand race-window and extra-files race-pattern coverage

- **Decision**: add targeted coverage for extra-files directory type-flip cleanup races and TTL boundary acceptance/rejection scenarios in both unit and integration paths, and classify path-type race `OSError`s as concurrent-mutation diagnostics.
- **Why**: previously covered races focused on missing-path mutation; type-flip races and just-above-threshold TTL acceptance were underrepresented despite being realistic in concurrent filesystems and scheduler timing.
- **Security impact**: positive. Improves confidence that fail-closed controls hold at TTL boundaries and under additional extra-files cleanup race patterns without broadening execution surface.
- **Follow-up**: continue with Pulsar parity and broader destination-class TTL integration coverage.

### 2026-07-19 — Gap #2 add explicit `OSError` marker-list race parity coverage

- **Decision**: add targeted tests to ensure marker-directory list `OSError` races follow the same required-vs-optional extension-resolution semantics as existing `FileNotFoundError` race handling.
- **Why**: `OSError` list races are a realistic filesystem concurrency mode and should preserve the same fail-open/fail-closed split already defined for required extension contexts.
- **Security impact**: positive. Reduces ambiguity in race handling semantics for extension resolution under marker-directory mutation.
- **Follow-up**: extend integration-level race simulations where feasible.
