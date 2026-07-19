# Crypt4GH hardening handover

## Scope and status

This document summarizes the full Crypt4GH hardening state in the worktree branch `explore-crypt4gh-library-support-merged-with-is-recryptor-from-26.0` as of head `41589e4876`, relative to base commit `5b9b6d3f20` on `dev`.

- Primary branch scope: all non-Pulsar Crypt4GH fail-closed work in this worktree
- Pulsar follow-up scope: isolated in `work/pulsar-tail-20260718`
- Main branch diff size: 16 files changed, `+4012/-223`
- Main branch Crypt4GH verification status: 133 tests passed, 0 failed
- Pulsar branch diff size: 3 files changed, `+145/-7`
- Pulsar branch verification status: 14 tests passed, 0 failed

The short version is:

- the non-Pulsar Crypt4GH fail-closed gaps targeted in this cycle are now closed or materially reduced for the current execution model;
- output finalization moved decisively toward compute-side / remote-evaluation handling instead of orchestrator-authored wrapper logic;
- a production regression from an over-aggressive scope gate was removed and replaced with narrower plaintext provenance checks;
- Pulsar parity work was intentionally extracted into its own branch so it can be reviewed separately.

## Current code state at a glance

The branch now enforces Crypt4GH more consistently across four points where earlier versions were weaker:

1. **Before launch**: readiness checks fail closed unless the runtime is on the supported remote-evaluation path with the required config.
2. **During execution**: remote-evaluation tooling owns the finalize/cleanup path close to the compute environment instead of relying on orchestrator-side command packing alone.
3. **Before success / before persistence**: payloads are verified and finalized before Galaxy records success or persists discovered outputs.
4. **After finalization errors**: best-effort purge is more defensive, more diagnosable, and safer around traversal, symlink, permission, and race edge cases.

In practice, that means the current branch is much stronger around:

- declared output encryption,
- discovered output encryption before object-store persistence,
- marker and extension handling,
- TTL / expiry fail-closed behavior,
- plaintext staging containment,
- metadata reset behavior for modify-input style flows,
- extra-files handling,
- cleanup diagnostics,
- and noisy debug output staying opt-in.

## Brief chronology

This is the compressed history of the branch from its base, with emphasis on what changed materially rather than on every intermediate commit detail.

1. Initial hardening established path containment, fail-closed local-strategy handling, and discovered-output finalization limited to working-directory provenance.
2. Gap #13 closed the discovery encryption bypass by finalizing discovered payloads before object-store persistence.
3. Gap #12 fixed stale metadata retention for modify-input flows by resetting Crypt4GH metadata when compute-keypair state is cleared.
4. Gap #7 made explicit allowed-root provenance mandatory for finalize-about-to-persist paths.
5. Gap #9 threaded `require_crypt4gh_extension` through discovery contexts so extension enforcement could not be silently bypassed.
6. Gap #3 and Gap #5 hardened traversal and purge behavior, especially around parent-segment traversal and symlink handling.
7. Gap #10 added more security-edge coverage for races, permissions, and extra-files behavior.
8. Gap #1 aligned local-vs-remote strategy checks with effective configuration, including global fallback behavior.
9. Gap #6 originally introduced a pre-success output scope gate, but that proved too broad in production because it rejected legitimate object-store destinations.
10. Gap #8 tightened TTL boundary behavior while preserving a conservative floor.
11. Gap #2 hardened marker evidence and race-tolerant discovery logic.
12. Gap #11 removed a dynamic wrapper warning path for `NoneDataset`.
13. Gap #14 moved default debug output behind explicit opt-in.
14. Pulsar-related command-assembly work was extracted into a dedicated branch to keep the non-Pulsar branch reviewable.
15. The production regression from the scope gate was reverted, and the intended security property was reintroduced as `plaintext_root_paths` containment instead.
16. Follow-up tests broadened practical coverage for the non-Pulsar gaps, and the evaluation/status documents were updated accordingly.

## Architectural shift: from orchestrator-side packing to remote evaluation

This is the most important architectural theme in the branch.

Earlier Crypt4GH work leaned on **orchestrator-side command packing**: Galaxy prepared command behavior centrally and pushed that shape outward. The branch history and supporting design documents conclude that this is a poor fit for the trust model being pursued here.

### Why the old model was a problem

- It kept too much sensitive execution planning under the Galaxy orchestrator.
- It made fail-closed cleanup and finalization harder because the code deciding the wrapper shape was not the code observing the actual execution environment.
- It created divergence between non-Pulsar remote-evaluation flows and Pulsar command-assembly flows.
- It made security reasoning less direct: the place building the command and the place managing plaintext lifecycle were not the same place.

### Why remote evaluation was chosen

The branch moved further toward **remote evaluation** because it gives the compute-side path the information and control needed to make the Crypt4GH lifecycle more atomic and locally verifiable.

Benefits in the current branch:

- finalization happens near the produced files,
- cleanup/failure behavior is tied directly to execution outcome,
- pre-success verification can inspect real on-disk results,
- discovered outputs can be finalized before Galaxy persists them,
- and the fail-closed path is easier to reason about because the compute-side runtime owns more of the plaintext lifecycle.

This does **not** eliminate all remaining architecture tension. Pulsar still needed separate parity work because its command assembly differs from the main remote-evaluation path. But the branch direction is clear: keep decrypt/encrypt/finalize behavior as close as possible to the compute-side execution context.

## Branches and review boundaries

### Primary branch: `explore-crypt4gh-library-support-merged-with-is-recryptor-from-26.0`

This branch contains the full non-Pulsar hardening set and is the main subject of this handover.

### Pulsar branch: `work/pulsar-tail-20260718`

This separate worktree/branch contains the Pulsar-specific command assembly fixes and parity tests that were intentionally extracted from the main line of work.

Summary of the Pulsar branch:

- files changed: `lib/galaxy/jobs/command_factory.py`, `test/unit/app/jobs/test_command_factory.py`, plus branch metadata context;
- tests passed: 14/14;
- purpose: make Pulsar `remote_command_line` assembly match the non-Pulsar wrapper/failure-gating intent more closely.

The key Pulsar fixes were:

1. explicit shell-command separation before follow-up steps,
2. success gating for follow-up commands,
3. grouped gating for the whole follow-up chain,
4. configured shell usage instead of hard-coded `bash`,
5. script-directory-based path resolution,
6. quoted script path handling for spaces.

The added parity tests are useful, but they are still largely **command-shape assertions**, not runtime parity proof. So the Pulsar branch is mitigated, but not fully de-risked.

### Other already-existing PR-relevant branches

- `work/fix-remote-tool-eval-python-fail-closed` already exists for earlier remote-tool-eval hardening.
- `work/pulsar-tail-20260718` already exists for the Pulsar parity slice.
- `backup/explore-...-before-pulsar-rewrite-20260718` preserves the pre-extraction state.

## File-by-file summary of code changes

### `lib/galaxy/tools/crypt4gh_remote_execution.py`

This is the center of gravity of the branch.

Main current responsibilities added or strengthened:

- pre-success output evidence verification via `verify_crypt4gh_pre_success_output_evidence()`,
- safer finalization of extra-files payloads,
- allowed-root containment checks,
- race-tolerant marker / extension resolution,
- finalize-before-persist support for discovered outputs,
- plaintext root provenance support via `plaintext_root_paths`,
- stronger purge diagnostics and symlink handling,
- and opt-in debug emission.

Why it matters now:

- this file now carries most of the branch’s non-Pulsar fail-closed policy;
- discovered and declared output enforcement both depend on it;
- and it is where the current branch most clearly expresses the move toward compute-side finalization.

Possible general-PR candidates from this file:

- `_assert_path_within_allowed_roots()` is a generally useful containment helper,
- the best-effort purge diagnostic pattern is likely reusable outside Crypt4GH,
- and some of the safe extra-files handling ideas may generalize.

Not general-purpose:

- the pre-success Crypt4GH evidence verifier itself is strongly datatype- and workflow-specific.

### `lib/galaxy/tools/remote_tool_eval.py`

This file was hardened around embedded cleanup/finalize script behavior, failure propagation, and marker/cleanup handling.

Why it matters now:

- it is the runtime bridge that lets remote evaluation own more of the fail-closed path;
- it is one of the clearest concrete outcomes of the orchestrator-to-compute-side shift.

Possible general-PR candidates:

- fail-closed bootstrap / failure-propagation improvements,
- some remote-eval cleanup behavior,
- and possibly parts of the embedded script hardening.

Likely still Crypt4GH-tied in practice:

- finalize semantics and marker expectations.

### `lib/galaxy/model/store/discover.py`

This file now integrates `finalize_about_to_persist_crypt4gh_payload()` into object-store persistence paths and threads `require_crypt4gh_extension` through discovery metadata flows.

Why it matters now:

- it closes one of the most serious practical gaps: discovered outputs can no longer be persisted first and secured later;
- it also reduces the chance that extension enforcement varies by caller.

General-PR potential: low. The hooks are mostly Crypt4GH-specific even though the design pattern (pre-persist datatype-specific finalize hook) could inspire broader work.

### `lib/galaxy/jobs/__init__.py`

This file integrates the Crypt4GH pre-success verifier into job completion handling.

Just as important, it also reflects a key correction in branch history: the earlier output scope-gate helpers were removed after they proved too aggressive for real object-store layouts.

Why it matters now:

- it is where job success becomes conditional on Crypt4GH evidence;
- and it is where the branch corrected itself after the regression.

General-PR potential:

- the idea of a datatype- or policy-specific pre-success verifier is broadly interesting,
- but the current implementation is still tightly Crypt4GH-oriented.

### `lib/galaxy/datatypes/binary.py`

Small but important metadata behavior change: `Crypt4GHDynamicCompressedArchive.set_meta()` now resets header-related metadata correctly when clearing compute-keypair state.

Why it matters now:

- it fixes stale metadata leakage in modify-input style tools;
- it is easy to overlook in review because the diff is small, but it closes a real correctness gap.

General-PR potential: none in practice; this is Crypt4GH-specific datatype behavior.

### `lib/galaxy/job_execution/output_collect.py`

This file was updated to carry declared output target information needed for plaintext-root containment.

Why it matters now:

- it is part of the post-regression fix;
- it narrows the enforcement target from “all outputs must stay in job scope” to “plaintext provenance must stay in approved plaintext roots.”

General-PR potential: low.

### `lib/galaxy/security/object_wrapper.py`

`wrap_with_safe_string()` now derives dynamic wrapper naming from the resolved wrapped class instead of the wrapped instance value, which removes the `NoneDataset`-related warning path.

Why it matters now:

- it eliminates a noisy and misleading runtime warning discovered in Crypt4GH-adjacent behavior;
- but the fix itself is not Crypt4GH-specific.

This is the clearest **general Galaxy PR candidate** in the worktree.

### `lib/galaxy/jobs/command_factory.py` (Pulsar branch only)

This is not part of the primary worktree head, but it is part of the overall hardening story.

Why it matters:

- it makes Pulsar command assembly more faithful to the non-Pulsar chain shape,
- especially around ordering, shell choice, success gating, and path resolution.

Possible general-PR candidates:

- configured shell use,
- script-directory path resolution,
- path quoting with spaces.

The specific gating layout was motivated by Crypt4GH wrapper parity and may not need to be generalized as-is.

## File-by-file summary of test changes

### `test/unit/jobs/test_crypt4gh_remote_execution.py`

This is now the largest and most comprehensive test file for the branch’s security behavior.

It covers:

- declared output finalization,
- discovered output finalization,
- pre-success verification,
- extra-files handling,
- containment failures,
- purge diagnostics,
- metadata reset,
- extension resolution,
- helper/readiness checks,
- and TTL boundary behavior.

Assessment:

- high value as a regression net,
- but also the biggest maintainability concern in the test suite.

Tests that may be too process-focused:

- long, implementation-shaped purge/race tests with very specific internal naming;
- some detailed mock chains that may be more brittle than behavior-first tests;
- some edge-case resolution tests that assert security-relevant internals rather than only user-visible outcomes.

That said, these are not “bad tests” by default. In a security hardening branch, some implementation-proximate regression tests are justified. The main issue is size and concentration.

### `test/unit/jobs/test_remote_tool_eval.py`

This file adds substantial coverage for remote-tool-eval cleanup, finalize, and failure handling.

Assessment:

- useful because it tests the embedded-script/runtime boundary, not just the higher-level API,
- but it overlaps with the main remote-execution test file and may deserve future consolidation.

Possible removal candidates:

- some overlapping tests if equivalent behavior is already covered more clearly elsewhere.

Reason not to remove aggressively now:

- script-level behavior and API-level behavior are distinct failure surfaces.

### `test/unit/data/model/test_model_discovery_crypt4gh.py`

Strong coverage for discovery-time extension resolution, race cases, and `require_crypt4gh_extension` propagation.

Assessment:

- mostly behavior-aligned,
- some overlap with `test_crypt4gh_remote_execution.py`,
- but still a sensible dedicated home for discovery semantics.

### `test/integration/test_crypt4gh_remote_execution.py`

This is the most valuable test file by confidence level because it exercises end-to-end behavior, including collection discovery paths and TTL boundary scenarios.

Assessment:

- good behavioral coverage,
- should continue to be favored whenever a question is “does this actually work in the integrated runtime?”

Future priority:

- more destination-topology coverage,
- and real Pulsar/runtime parity coverage.

### `test/unit/app/jobs/test_job_wrapper_crypt4gh.py`

Small focused tests for job-wrapper integration, including the regression where tracked object-store paths must remain allowed.

Assessment: good signal, low maintenance.

### `test/unit/app/jobs/test_output_collect_crypt4gh.py`

Minimal update for plaintext-root propagation.

Assessment: tiny but justified.

### `test/unit/data/datatypes/test_crypt4gh.py`

Focused behavioral tests for metadata reset and explicit-header preference.

Assessment: good, behavior-led, worth keeping.

### `test/unit/util/test_object_wrapper.py`

Clean targeted regression test for the dynamic wrapper fix.

Assessment: excellent general regression test; this one should likely stay even if the Crypt4GH branch is split into smaller PRs.

### `test/unit/app/jobs/test_command_factory.py` (Pulsar branch)

Useful parity tests for wrapper order and failure-gating shape.

Assessment:

- currently more process-/string-shape-focused than behavior-focused,
- acceptable as branch-local parity tests,
- but a runtime Pulsar test would be more convincing long term.

## Tests that may be too narrow or too process-focused

If the branch is cleaned up into smaller follow-up PRs, the following test categories deserve review for possible simplification or removal:

1. **Exact command-shape Pulsar tests** where the real concern is runtime behavior.
2. **Some long purge/race unit tests** whose names and structure encode internal implementation detail more than externally meaningful behavior.
3. **Duplicated unit coverage across `test_crypt4gh_remote_execution.py` and `test_remote_tool_eval.py`** where both tests are proving nearly the same security property from adjacent seams.

My recommendation is **not** to remove them immediately. Instead:

- keep them while the branch is still settling,
- then consolidate once the intended permanent PR boundaries are decided.

## Top-level Crypt4GH-related documents

### `crypt4gh-fail-closed-evaluation.md`

This is the most important current-state document. It records the gap-by-gap status, mitigation notes, decision records, and residual risks. Anyone reviewing the security story should start here.

### `crypt4gh-galaxy-support.md`

This is the earlier support/design document. It is still useful for historical intent and the earlier shape of the feature, but it is no longer the best single source for the current fail-closed runtime story.

### `crypt4gh-phase-2-implementation-plan.md`

Important for tracing the approved implementation path, especially the shift toward compute-side recryptor B and remote evaluation.

### `crypt4gh-phase-2-output-enforcement-plan.md`

Important for the narrower output-enforcement slice and for understanding why discovered-output finalization became such a central concern.

### `crypt4gh-phase-2-output-enforcement-spec.md`

Useful as the tighter requirements/spec companion to the output-enforcement plan.

### `crypt4gh-phase-2-reimplementation-design.md`

This is the best document for understanding the architectural redesign itself, especially:

- the trust-model motivation,
- the user-side recryptor A / compute-side recryptor B split,
- and why the orchestrator-side approach became insufficient.

### `crypt4gh-remote-exec-findings.md`

Earlier findings document that explains why `remote_tool_eval.py` became the preferred compute-side hook.

### `test-data/crypt4gh/MANUAL_TESTING.md`

Still useful for operator-oriented manual checks and for understanding how the integration test fixtures are mirrored manually.

### `test-data/crypt4gh/HANDOFF.md`

Useful as a prior handoff note for live smoke verification and for the recryptor-related delivery context.

## Potential PR decomposition: what might deserve general PRs

The worktree is strongly Crypt4GH-focused overall, but a few pieces stand out as candidates for more general Galaxy hardening PRs.

### Strong candidates

1. **`lib/galaxy/security/object_wrapper.py` / `test/unit/util/test_object_wrapper.py`**
   - clearly general,
   - self-contained,
   - useful beyond Crypt4GH.

2. **Allowed-root containment helper patterns** from `crypt4gh_remote_execution.py`
   - especially if Galaxy wants a shared containment utility for file-sensitive code.

3. **Best-effort purge diagnostic patterns**
   - classifying concurrent mutation vs permission errors is generally valuable.

### Medium candidates

4. **Remote-tool-eval failure propagation hardening**
   - may be extractable if separated from the Crypt4GH-specific finalize script logic.

5. **General pre-success verifier hook pattern**
   - not the current Crypt4GH verifier itself, but the idea of pluggable datatype-/policy-specific success gates.

### Probably not worth generalizing first

6. **Discovery integration hooks**
   - conceptually interesting, but the concrete changes here are still tightly bound to Crypt4GH.

7. **Pulsar command-gating details**
   - some pieces are general, but the motivating chain shape is specific enough that a generalized PR would need careful reframing.

## Challenges met and how they were resolved

### 1. Discovery-path encryption bypass

Challenge:

- discovered outputs could bypass the intended encryption contract if Galaxy persisted them before Crypt4GH finalization.

Resolution:

- wire finalization into the about-to-persist discovery path,
- enforce explicit allowed-root provenance,
- and add coverage at both discovery and integration levels.

### 2. Over-aggressive scope gate causing real deployment breakage

Challenge:

- the original pre-success scope gate enforced the wrong boundary and rejected legitimate object-store dataset destinations.

Resolution:

- remove the gate,
- keep the security goal but restate it more precisely as plaintext provenance control,
- introduce `plaintext_root_paths`,
- and add regression tests for the correct distinction.

This was a good corrective move. The branch became safer by becoming more precise, not by simply becoming looser.

### 3. Marker and extension race behavior

Challenge:

- marker-only evidence is inherently race-prone, especially in cleanup-heavy or concurrent paths.

Resolution:

- require better evidence in non-required paths,
- force `.c4gh` semantics in required contexts,
- and treat specific directory races as “no evidence” rather than as accidental success.

### 4. Purge safety under hostile filesystem timing

Challenge:

- best-effort purge can easily become unsafe or too opaque around symlinks, permission failures, and concurrent mutation.

Resolution:

- validate roots before deletion,
- unlink symlinks instead of traversing them,
- and emit explicit diagnostics that distinguish categories of failure.

### 5. Interleaved Pulsar and non-Pulsar history

Challenge:

- the git history became hard to review because Pulsar and non-Pulsar changes were interleaved.

Resolution:

- extract Pulsar work to a separate worktree/branch and keep the primary branch focused on non-Pulsar hardening.

This was the right reviewability trade-off even though it made history reconstruction more manual.

## Remaining risks

1. **Pulsar parity is still under-verified at runtime**
   - unit parity tests exist, but real Pulsar runtime parity is still not proven.

2. **Untracked plaintext outputs are still outside the strongest guarantees**
   - the hardening work covers tracked / declared / discovered outputs, not arbitrary tool writes to unrelated paths.

3. **Destination topology coverage is incomplete**
   - current tests cover important cases, not every deployment pattern.

4. **Toolshed / fixture variation risk remains**
   - in-tree fixture confidence is better than before, but not universal.

5. **Large security unit files may become brittle over time**
   - especially if future reviewers struggle to separate behavior assertions from implementation scaffolding.

6. **TTL policy may be conservative for some operators**
   - the 24-hour floor is safe, but may not fit every deployment.

## Future work

1. Add **real Pulsar integration/runtime tests**.
2. Expand **destination-matrix integration coverage**.
3. Add more **toolshed/discovery fixture coverage**.
4. Consider whether **containment and purge diagnostics** should become shared Galaxy utilities.
5. Revisit **test consolidation** once PR boundaries stabilize.
6. Consider whether **TTL floor configuration** should be more operator-friendly.
7. If desired, split out the **object-wrapper fix** into a general PR.

## Self-critical review

### What went well

- The branch closes several real fail-closed gaps, not just theoretical ones.
- The production regression was corrected quickly and in a way that sharpened the model instead of diluting it.
- The test suite now gives materially better confidence in non-Pulsar Crypt4GH behavior.
- The Pulsar extraction was a good decision for review clarity.
- The documentation trail is strong: there is both implementation evidence and decision-history evidence.

### What did not go well

- The over-aggressive scope gate should have been caught before it reached production use.
- The largest unit test file became too big; even if justified, it now carries review and maintenance cost.
- Some tests lean heavily on internal mechanics and mock topology.
- The overall document set is comprehensive but spread across several large files, which raises the cost of understanding the whole story.
- The non-linear history introduced by extraction/cherry-picking makes later archaeology harder.

### Overall judgment

The branch is technically strong in its non-Pulsar scope, but not especially elegant yet. It solved real problems and improved the security posture, while also accumulating some review complexity and test concentration debt.

## Setup guide

This section is intentionally practical and reflects the **current branch naming and architecture**, which centers on `crypt4gh_reencryption_service_url` and the user-side recryptor A / compute-side recryptor B split described in the design documents.

### 1. Concepts: recryptor A vs recryptor B

The current design distinguishes:

- **Recryptor A (user-side)**: the upstream/browser-local or user-side service that uses user private material and obtains a compute-side key context for input-side staging.
- **Recryptor B (compute-side)**: the service Galaxy talks to for compute-side key info and header recryption in remote execution.

For this branch, the main Galaxy-facing configuration in-repo is for **compute-side recryptor B**.

### 2. Galaxy configuration

Minimum current Galaxy-side settings for the supported remote Crypt4GH path:

```yaml
galaxy:
  enable_crypt4gh_transparent_staging: true
  crypt4gh_reencryption_service_url: http://recryptor-b:36667
  metadata_strategy: extended
  outputs_to_working_directory: true
  tool_evaluation_strategy: remote
```

Important notes:

- `crypt4gh_reencryption_service_url` is the current in-repo config key and now refers to compute-side recryptor B.
- `metadata_strategy: extended` is required for this execution model.
- `outputs_to_working_directory: true` is required for the current fail-closed path.
- `tool_evaluation_strategy: remote` is required; local evaluation is intentionally rejected for this Crypt4GH flow.

### 3. Job destination / execution expectations

If the deployment uses destination config, the effective execution mode still needs to resolve to:

- remote tool evaluation,
- extended metadata,
- working-directory outputs.

In other words, destination-level settings or global defaults must combine to the same effective behavior.

### 4. Compute-side recryptor B setup

The compute-side service must:

- be reachable from the compute environment,
- mint and track time-bounded compute-side key ids,
- answer the compute-key info route,
- and support the header recryption routes used by the branch design.

From the design/docs context, the relevant route family is:

- `POST /get_compute_key_info`
- `POST /recrypt_header_to_job_key`
- `POST /recrypt_header_to_user_key`

For local/manual testing, the repo already contains a mock compute-side service via the integration test harness.

### 5. User-side recryptor A expectations

The user-side service A is upstream of this branch’s output hardening work. It is responsible for the user-side part of the input flow, including obtaining compute-side key info and recrypting input headers into compute-side context.

This branch does **not** reimplement A inside Galaxy. It assumes that user-side prerequisite flow exists.

### 6. TTL-related settings

Current practical policy:

```yaml
galaxy:
  crypt4gh_ttl_minimum: 86400
  gx_import_reencrypt_key_ttl: 86400
```

Notes:

- the branch preserves a conservative default floor of 24 hours;
- equality with the minimum boundary is treated fail-closed;
- operators may eventually want a more flexible policy, but the current default is intentionally cautious.

### 7. Debugging

Debug output is now opt-in.

```bash
GALAXY_CRYPT4GH_DEBUG=1
```

Without that environment variable, the new branch behavior avoids noisy default debug output.

### 8. Manual verification references

Useful operator docs already in the tree:

- `test-data/crypt4gh/MANUAL_TESTING.md`
- `test-data/crypt4gh/HANDOFF.md`

These are the best starting points for practical manual checks and live-smoke setup.

## Recommended review order for a new maintainer

If someone new needs to understand this work quickly, I would suggest this order:

1. `CRYPT4GH-HANDOVER.md` (this file)
2. `crypt4gh-fail-closed-evaluation.md`
3. `crypt4gh-phase-2-reimplementation-design.md`
4. `lib/galaxy/tools/crypt4gh_remote_execution.py`
5. `test/integration/test_crypt4gh_remote_execution.py`
6. `test/unit/jobs/test_crypt4gh_remote_execution.py`
7. Pulsar branch diff (`work/pulsar-tail-20260718`) if Pulsar parity matters for the next step.

## Final assessment

For the **non-Pulsar** scope, this branch is in a substantially better state than the branch base:

- discovered outputs are finalized earlier and more safely,
- plaintext provenance is constrained more precisely,
- job success is tied to stronger evidence,
- cleanup behavior is safer and more diagnosable,
- extension and marker handling is less racy,
- and the architectural direction is more coherent with the compute-side trust model.

The main remaining caution is not “is the branch useful?” but rather “how should it now be reviewed and split?” The best near-term split candidates are the general object-wrapper fix, any reusable containment/diagnostic helpers, and the isolated Pulsar parity branch.

## Published demo history

A publicly shared history is available at:  
[https://galaxy.semsec.bsc.es/u/sveinugu/h/handoff-demo](https://galaxy.semsec.bsc.es/u/sveinugu/h/handoff-demo)

The history (id `eaa9b06464bd346f`, Galaxy 26.0) contains 25 items and demonstrates the complete Crypt4GH encryption → analysis → output encryption → recryption flow with publicly viewable datasets.

### History summary

The starting point is a single Crypt4GH-encrypted FASTQsanger file uploaded via data fetch:

- **HID 1**: `1.fastqsanger.c4gh` — the initial encrypted input dataset

From there, two parallel analysis tracks were run against the compute-recrypted copies:

**Branch A — simple output (Select first):**
1. `Show beginning1` on the recrypted input (HID 4) → produced `fastqsanger.c4gh` output
2. `Show beginning1` on the FastQC RawData txt.c4gh output (HID 24) → produced `txt.c4gh`
3. Both outputs re-recrypted via UI key icon (HID 14, 25)

**Branch B — split + FastQC (collection discovery path):**
1. `split_file_to_collection` (toolshed `0.5.2`) on recrypted input (HID 8, 19) → collections of `fastq.c4gh` elements
2. `FastQC` (toolshed `0.74+galaxy1`) on the recrypted input (HID 9, 10) → `html.c4gh` + `txt.c4gh` outputs
3. Split outputs and FastQC outputs re-recrypted via UI key icon (HID 15, 16, 22, 23)

### Recryption pattern

Throughout the history, datasets are tagged with `Recrypted_for_compute` and `cnk:38al0qyb` (the compute key ID). The pattern:

1. A job produces an encrypted output (`.c4gh` extension)
2. The user clicks the UI key icon "Recrypt Crypt4GH-encrypted dataset" to obtain a compute-recrypted copy
3. The recrypted copy carries the same dataset content but with headers recrypted to the compute-side key context
4. Both original and recrypted copies are preserved in the history — the recrypted copy is the one usable for further compute-side operations

### Tools used

- **Show beginning1** (`Show beginning1`) — Galaxy built-in text selection/head tool
- **FastQC** (`toolshed.g2.bx.psu.edu/repos/devteam/fastqc/fastqc/0.74+galaxy1`) — quality control
- **Split file to collection** (`toolshed.g2.bx.psu.edu/repos/bgruening/split_file_to_collection/split_file_to_collection/0.5.2`) — collection discovery
- **__DATA_FETCH__** — file upload
- **__SET_METADATA__** — metadata management (applied during recryption and dataset operations)

### Architecture notes

- **User-side recryptor** → **compute-side recryptor** at `https://galaxy.semsec.bsc.es:8443`
- The UI key icon triggers a user-side recryption that talks to the compute-side recryptor over this TLS connection
- Galaxy `remote_eval` code talks only to the compute-side recryptor — it never contacts the user-side recryptor or handles user private keys
- No private user keys or compute key materials were shared between the two service boundaries — only encrypted content traverses the two trusted connections
- The compute key ID `cnk:38al0qyb` visible in dataset tags across the history confirms consistent compute-key binding

### Future work (connections and key management)

- The two TLS connections (user→compute recryptor and Galaxy remote_eval→compute recryptor) should be hardened with authentication and authorization infrastructure (AAI)
- Proper key management lifecycles (rotation, revocation, auditing) remain to be implemented for production deployments
- The current demo setup trusts both connections at the network level; production environments should add token-based or certificate-based auth per connection
