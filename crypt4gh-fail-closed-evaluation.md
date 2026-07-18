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
- **Mitigated?**: **Largely**. Canonical allowed-root checks now guard finalize and purge path operations for declared and discovered hooks in current flow.
- **Still needed**:
  - explicit symlink/traversal race stress tests,
  - permission-denied behavior hardening and diagnostics polish.
- **Priority / severity**: **Medium**.

## 4) Pulsar / `for_pulsar` branch divergence

- **Gap**: Cleanup/finalization wrapping may not execute identically across Pulsar-oriented command assembly paths.
- **Mitigated?**: **Partially**. Core wrapper exists, but parity across all Pulsar branches is not fully verified.
- **Still needed**:
  - targeted Pulsar branch tests asserting wrapper + postrun + cleanup execution ordering,
  - verification that failure semantics match non-Pulsar remote path.
- **Priority / severity**: **High**.

## 5) Symlink / race / permission failures in best-effort purge

- **Gap**: Cleanup may fail due to permission errors, symlink behavior, concurrent file mutation, mount behavior.
- **Mitigated?**: **Partially**. Cleanup markers and exception logging exist; best-effort cleanup attempts run.
- **Still needed**:
  - hardened deletion strategy with safe path validation,
  - clearer operator diagnostics + retry/backoff where safe,
  - tests for permission-denied and symlink edge cases.
- **Priority / severity**: **Medium-High**.

## 6) Output written outside job working directory

- **Gap**: Tool/postrun may still write plaintext outside tracked target set; finalize/purge routines handle only tracked paths.
- **Mitigated?**: **Partially**. Current readiness requires `outputs_to_working_directory=true` for Crypt4GH path and discovered hooks now finalize from working-dir paths only.
- **Still needed**:
  - stronger constraints on writable paths for Crypt4GH jobs,
  - verification that unexpected plaintext artifacts are detected/fail the job before success.
- **Priority / severity**: **High**.

## 7) Path construction validation for finalize-about-to-persist

- **Gap**: `finalize_about_to_persist_crypt4gh_payload` accepts string paths from caller context; limited defensive path validation.
- **Mitigated?**: **Partially (improved)**. Allowed-root checks are enforced and discovered hooks no longer pass `dataset_output_path` into finalize calls.
- **Still needed**:
  - validate path provenance and allowed roots,
  - refuse unsafe/ambiguous targets before finalization/purge actions.
- **Priority / severity**: **Medium-High**.

## 8) Compute-key TTL / expiration window

- **Gap**: Long-running jobs can approach key expiry between early TTL checks and finalization time.
- **Mitigated?**: **Partially**. Minimum TTL checks and finalization-time expiry checks are in place.
- **Still needed**:
  - stronger end-to-end TTL policy for long walltime jobs,
  - explicit tests for near-expiry and mid-run expiry scenarios.
- **Priority / severity**: **Medium-High**.

## 9) Discovery callers not consistently using `require_crypt4gh_extension`

- **Gap**: Some paths can call module-level extension resolution in ways that bypass context-enforced requirement semantics.
- **Mitigated?**: **Partially (improved)**. Key discovered-output hooks are now aligned to working-dir-only finalization semantics and context-driven extension enforcement.
- **Still needed**:
  - audit and align all discovery/ext resolution entry points,
  - avoid direct calls that bypass context-aware enforcement.
- **Priority / severity**: **Medium**.

## 10) Incomplete edge-case test coverage

- **Gap**: Coverage is still thin for containment safety, local evaluation behavior, Pulsar parity, traversal/symlink attacks, and TTL race windows.
- **Mitigated?**: **Partially (improved)**. Unit/integration coverage now includes discovered-hook working-dir-only finalization semantics and `outputs_to_working_directory` readiness requirement.
- **Still needed**:
  - add explicit negative tests for path traversal/containment,
  - add Pulsar branch parity tests,
  - add symlink/permission/race cleanup tests.
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
- **Mitigated?**: **Not yet**.
- **Still needed**:
  - enforce canonical metadata reset/rewrite policy for all Crypt4GH output finalization paths,
  - add coverage for modify-input tool archetypes.
- **Priority / severity**: **High**.

## 13) At least one discovery path still bypasses encryption

- **Gap**: At least one dataset discovery path (reported example: `toolshed.g2.bx.psu.edu/repos/bgruening/split_file_to_collection/split_file_to_collection/0.5.2`) appears to persist unencrypted output and produced no `CRYPT4GH_DEBUG` traces.
- **Mitigated?**: **Not yet**.
- **Still needed**:
  - reproduce and isolate the specific discovery branch/path,
  - route that branch through Crypt4GH finalization hooks,
  - add regression/integration coverage for this tool pattern.
- **Priority / severity**: **High**.

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

1. **Gap #13** — close discovery-path encryption bypass (`split_file_to_collection` archetype) with regression coverage.
2. **Gap #12** — enforce canonical Crypt4GH metadata reset/rewrite for modify-input tool patterns.
3. **Gap #7** — strengthen path-provenance validation for finalize-about-to-persist callers.
4. **Gap #9** — complete discovery caller audit and align all extension resolution paths to context-aware enforcement.
5. **Gap #3** — finish containment hardening with traversal-focused negative coverage.
6. **Gap #5** — harden best-effort purge under symlink/permission/race edge conditions.
7. **Gap #10** — broaden edge-case test coverage for containment and cleanup behaviors.
8. **Gap #1** — add broader destination parity/performance coverage for local-vs-remote enforcement boundaries.
9. **Gap #6** — define and enforce policy for out-of-tree writes and pre-success plaintext detection.
10. **Gap #8** — define stronger TTL/walltime policy and implement boundary regression coverage.
11. **Gap #2** — settle marker-timing/race handling policy and remove marker-only decision windows.
12. **Gap #11** — resolve dynamic datatype registration behavior for non-preregistered `.c4gh` variants.
13. **Gap #14** — remove/gate `CRYPT4GH_DEBUG` output before merge/release.

### Follow-on notes

- Gaps near the end of the order are intentionally the ones most likely to require policy confirmation or threshold decisions.
- Gap #10 is still broad; in practice, tests should be added incrementally while fixing each earlier gap.

---

## Bottom line

The Crypt4GH fail-closed posture is **substantially stronger** after recent hardening, including working-dir-only discovered-output finalization and stricter readiness prerequisites. Remaining work is concentrated in **Gap #4 plus unresolved gaps #2/#5/#6/#8/#10/#11/#12/#13/#14**, where correctness and complete fail-closed coverage must still be proven across all discovery and metadata edge paths.
