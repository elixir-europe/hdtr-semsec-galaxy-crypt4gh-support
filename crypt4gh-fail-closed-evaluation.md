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
| **Declared output finalization** (`finalize_declared_crypt4gh_outputs`) | Requires explicit `output_path` targets (legacy discovered-selector target rejected), purges on exception. | **Covered (strong)** |
| **Discovered output finalization** (`_maybe_finalize_crypt4gh_about_to_persist_payload`, `_maybe_finalize_crypt4gh_assigned_primary_output`) | Finalizes `.c4gh` discovered/assigned outputs with marker evidence and optional extra-files manifest tracking. | **Covered (strong)** |
| **Extension resolution + marker application** (`_resolve_discovered_crypt4gh_extension`, `_apply_crypt4gh_marked_extensions`) | `require_crypt4gh_extension` hardens resolution when finalization context is active; marker-based extension re-application exists post-collection. | **Covered (partial)** |
| **Pre-success output evidence verifier** (`verify_crypt4gh_pre_success_output_evidence`) | Validates marker and mapping evidence before successful job completion; can force job error on verifier failure. | **Covered (strong)** |
| **Error-state cleanup in remote eval** (`remote_tool_eval.py` exception handler, cleanup snippet) | Best-effort cleanup invoked on remote eval failures before script finalization. | **Covered (partial)** |
| **Local (non-remote) evaluation path** | Crypt4GH remote finalization flow is not executed when destination strategy is local. | **Gap** |
| **Pulsar / `for_pulsar` branch behavior parity** | Known divergence risk in wrapper/cleanup equivalence depending on command assembly path. | **Gap** |
| **Out-of-tree / arbitrary path payload writes** | Finalize/purge logic can be bypassed by tool writes outside tracked targets/job dir. | **Gap** |

---

## Known fail-closed gaps (with mitigation status)

## 1) Local vs remote evaluation

- **Gap**: Crypt4GH finalization/cleanup orchestration is tied to remote tool-evaluation flow; local destinations can skip equivalent finalization safeguards.
- **Mitigated?**: **Partially**. Remote path is hardened; local path remains an exposure surface if transparent staging is enabled for local runs.
- **Still needed**:
  - explicit local-path policy (disable transparent staging locally or add equivalent local finalization hook),
  - tests proving plaintext cannot remain for local strategy.
- **Priority / severity**: **High**.

## 2) Marker-dir timing / race conditions

- **Gap**: Marker presence can lag discovery-time extension resolution; race windows can misclassify ext if marker-only heuristics are used.
- **Mitigated?**: **Partially**. `require_crypt4gh_extension` reduces this risk when finalization context exists.
- **Still needed**:
  - ensure all relevant callers use context-driven `require_crypt4gh_extension`,
  - reduce dependence on marker-dir existence as sole signal in mixed call paths.
- **Priority / severity**: **Medium-High**.

## 3) Purge path safety (containment)

- **Gap**: `_purge_output_targets_after_finalization_failure` deletes paths directly without strict containment checks.
- **Mitigated?**: **Not yet** (best-effort purge exists but no robust path containment guard).
- **Still needed**:
  - canonicalize + enforce allowed-root containment (e.g. job working dir / `_c4gh_stage` / known objectstore target),
  - reject traversal/symlink escape targets before unlink/rmtree.
- **Priority / severity**: **High**.

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

- **Gap**: Tool/postrun may write plaintext outside tracked target set; finalize/purge routines only handle known targets.
- **Mitigated?**: **Not fully**.
- **Still needed**:
  - stronger constraints on writable paths for Crypt4GH jobs,
  - verification that unexpected plaintext artifacts are detected/fail the job before success.
- **Priority / severity**: **High**.

## 7) Path construction validation for finalize-about-to-persist

- **Gap**: `finalize_about_to_persist_crypt4gh_payload` accepts string paths from caller context; limited defensive path validation.
- **Mitigated?**: **Partially** (some existence checks; no full trust-boundary validation).
- **Still needed**:
  - validate path provenance and allowed roots,
  - refuse unsafe/ambiguous targets before finalization/purge actions.
- **Priority / severity**: **High**.

## 8) Compute-key TTL / expiration window

- **Gap**: Long-running jobs can approach key expiry between early TTL checks and finalization time.
- **Mitigated?**: **Partially**. Minimum TTL checks and finalization-time expiry checks are in place.
- **Still needed**:
  - stronger end-to-end TTL policy for long walltime jobs,
  - explicit tests for near-expiry and mid-run expiry scenarios.
- **Priority / severity**: **Medium-High**.

## 9) Discovery callers not consistently using `require_crypt4gh_extension`

- **Gap**: Some paths can call module-level extension resolution in ways that bypass context-enforced requirement semantics.
- **Mitigated?**: **Partially**. `ModelPersistenceContext` path now sets `require_crypt4gh_extension` when finalization context exists.
- **Still needed**:
  - audit and align all discovery/ext resolution entry points,
  - avoid direct calls that bypass context-aware enforcement.
- **Priority / severity**: **Medium**.

## 10) Incomplete edge-case test coverage

- **Gap**: Coverage is still thin for containment safety, local evaluation behavior, Pulsar parity, traversal/symlink attacks, and TTL race windows.
- **Mitigated?**: **Partially**. Core unit coverage exists for many remote finalization/cleanup/error paths; known legacy mismatch test removed and updated behavior validated.
- **Still needed**:
  - add explicit negative tests for path traversal/containment,
  - add local-evaluation fail-closed tests,
  - add Pulsar branch parity tests,
  - add symlink/permission/race cleanup tests.
- **Priority / severity**: **High**.

---

## Current fail-closed posture

Overall posture is **materially improved but not yet complete fail-closed across all execution modes**.

- **Strongest coverage**: remote evaluation path, declared/discovered output finalization hooks, pre-success verifier, cleanup wrapping, marker/mapping evidence checks.
- **Residual risk concentration**: path safety/containment, local strategy behavior, Pulsar parity, and untracked output locations.

---

## Hardened in this evaluation cycle

Key hardening already present on this branch/work item includes:

- fail-closed rejection of legacy discovered-selector targets in declared finalization,
- context-driven `require_crypt4gh_extension` support in discovery resolution,
- remote-tool bootstrap/interpreter hardening (GALAXY_PYTHON usage in command factory path),
- cleanup-wrapped command flow and remote-eval failure cleanup path,
- pre-success verification gates for payload markers, discovered mapping, and extra-files manifest evidence.

---

## Recommendations and proposed next steps

### Immediate next steps (proposed)

1. **Path-containment checks (highest priority)**
   - Add strict canonical containment guards to purge/finalize deletion paths.
2. **Local evaluation tests and policy hardening**
   - Prove fail-closed behavior for non-remote destinations or explicitly disable Crypt4GH transparent staging for local strategy.
3. **Pulsar wrapper parity tests/hardening**
   - Verify cleanup/finalize wrapper equivalence and failure semantics for Pulsar/`for_pulsar` command paths.

### Follow-on recommendations

- Add symlink/permission/race stress tests for cleanup.
- Add out-of-tree write detection checks before marking job success.
- Add TTL boundary tests (near-expiry and expiration during long jobs).
- Complete caller audit for consistent `require_crypt4gh_extension` usage.

---

## Bottom line

The Crypt4GH fail-closed posture after the newest plan implementation is **substantially stronger**, especially in remote execution + finalization + verifier flows. Remaining work is focused on **closing path-safety and execution-mode parity gaps** so plaintext persistence is prevented consistently under all relevant runtime conditions.
