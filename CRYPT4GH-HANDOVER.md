# Crypt4GH hardening handover

## Overview

This handover explains what the current Crypt4GH hardening branch changes, how to run the supported setup, and where the main remaining risks are.

If you want a live example before reading code, see **Appendix A** for a public Galaxy history that demonstrates the end-to-end Crypt4GH flow.

### The short version

- This branch makes encrypted-output handling safer and earlier in the job lifecycle.
- Jobs fail sooner when the required secure runtime path is missing.
- Galaxy checks encrypted outputs before marking jobs successful or saving discovered results.
- Plaintext cleanup is more thorough and more explicit when something goes wrong.
- Pulsar follow-up work was split into its own branch so it can be reviewed separately.

In repo-specific terms, this means the non-Pulsar Crypt4GH hardening tightened remote-evaluation checks, finalize-before-persist handling for discovered outputs, plaintext-root containment, and cleanup diagnostics.

### Who should read which part

- **Overview**: for anyone who wants the main outcome in plain language.
- **Quick start / setup guide**: for operators and anyone trying to reproduce the supported path.
- **Deep dive**: for maintainers and reviewers who want file-by-file and test-by-test detail.
- **Appendix**: for the public demo, reading map, and supporting reference material.

### Current code state at a glance

The branch now blocks unsafe behavior at four clearer checkpoints in the job lifecycle:

1. **Before launch**: jobs stop early if the supported secure path is missing; technically, readiness checks require the remote-evaluation path and its required config.
2. **During execution**: encryption cleanup happens closer to where files are produced; technically, the compute-side path owns more of the finalize and cleanup flow.
3. **Before success / before persistence**: Galaxy checks encrypted outputs before treating the run as complete or saving discovered files.
4. **After finalization errors**: cleanup reports failures more clearly and handles risky filesystem cases more defensively.

In practical terms, the branch is stronger around:

- declared output encryption,
- discovered output encryption before object-store persistence,
- marker and extension handling,
- TTL / expiry fail-closed behavior,
- plaintext staging containment,
- metadata reset for modify-input style flows,
- extra-files handling,
- cleanup diagnostics,
- and keeping debug output opt-in.

### Remaining risks at a glance

- Pulsar parity still needs stronger runtime proof, not just command-shape tests.
- Untracked plaintext outputs are still outside the strongest guarantees.
- Destination-topology coverage is better than before, but not complete.
- The largest security-heavy unit test files may become hard to maintain over time.

## Quick start / setup guide

Use this section if you need the supported operator setup before reading the implementation details.

### Operator checklist

1. Deploy compute-side recryptor B (see section 5).
2. Apply the minimum Galaxy configuration (see section 3).
3. Confirm remote evaluation, extended metadata, and working-directory outputs (see section 4).
4. Set TTL and debug preferences (see sections 7 and 8).
5. Run manual verification (see section 9).

### 1. Supported execution model

The current branch supports a specific remote execution model. In plain terms: Galaxy prepares the job, the compute-side path handles the sensitive output-finalization work, and local tool evaluation is intentionally not part of the supported Crypt4GH flow.

### 2. Recryptor A vs recryptor B

The current design uses two separate roles:

- **Recryptor A (user-side)**: the upstream or browser-adjacent service that works with user-side material and obtains compute-side key context for input staging.
- **Recryptor B (compute-side)**: the service Galaxy talks to during remote execution for compute-key information and header recryption.

For this branch, the main Galaxy-facing configuration is for **compute-side recryptor B**.

### 3. Minimum Galaxy configuration

Minimum Galaxy-side settings for the supported remote Crypt4GH path:

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

### 4. Job destination expectations

If the deployment uses destination config, the effective execution mode still needs to resolve to:

- remote tool evaluation,
- extended metadata,
- working-directory outputs.

In other words, destination-level settings and global defaults still need to combine into the same supported behavior.

### 5. Compute-side recryptor B setup

The compute-side service must:

- be reachable from the compute environment,
- mint and track time-bounded compute-side key ids,
- answer the compute-key info route,
- and support the header recryption routes used by this design.

The relevant route family is:

- `POST /get_compute_key_info`
- `POST /recrypt_header_to_job_key`
- `POST /recrypt_header_to_user_key`

For local or manual testing, the repo already contains a mock compute-side service via the integration test harness.

### 6. User-side recryptor A expectations

The user-side service is upstream of this branch’s output hardening work. It obtains compute-side key information and recrypts input headers into compute-side context.

This branch does **not** reimplement recryptor A inside Galaxy. It assumes that prerequisite flow already exists.

### 7. TTL-related settings

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

### 8. Debugging

Debug output is now opt-in:

```bash
GALAXY_CRYPT4GH_DEBUG=1
```

Without that environment variable, the branch avoids noisy default debug output.

### 9. Manual verification references

Useful operator-oriented docs already in the tree:

- `test-data/crypt4gh/MANUAL_TESTING.md`
- `test-data/crypt4gh/HANDOFF.md`

These are the best starting points for practical manual checks and live-smoke setup.

If you only need supported setup and manual verification, you can stop here. The remaining sections are for reviewers and maintainers.

---

Everything below is optional **deep-dive material for reviewers and maintainers**.

## Deep dive (for reviewers and maintainers)

### How the branch changed

This branch history is easier to understand as a few themed changes than as a gap-by-gap list.

#### 1. Output finalization now happens earlier

The branch closes the most serious practical hole by finalizing discovered outputs before Galaxy persists them to the object store.

That includes:

- wiring finalization into the discovery persistence path,
- carrying `require_crypt4gh_extension` through discovery flows,
- and strengthening tests around discovery-time behavior.

#### 2. More control moved to the compute-side path

The branch shifts sensitive output handling away from centrally authored wrapper logic and closer to the place where files actually exist.

#### 3. Pre-success verification became stricter

Jobs now need better evidence before Galaxy reports success for Crypt4GH outputs.

#### 4. Plaintext containment became narrower and safer

An earlier scope gate turned out to be too broad in production because it rejected legitimate object-store destinations. The branch corrected this by removing that gate and replacing it with `plaintext_root_paths` containment.

This is an important design correction: the branch became safer by checking the right boundary, not by checking every possible boundary.

#### 5. Cleanup and diagnostics became more defensive

The purge path is more careful around traversal, symlinks, permission failures, and concurrent filesystem changes.

The practical outcome is better failure reporting and lower risk of unsafe cleanup behavior.

#### 6. Pulsar work was split out for separate review

Pulsar-related command assembly and parity tests were extracted into a dedicated branch so the non-Pulsar hardening story stayed reviewable.

### Architecture direction: more compute-side control, less central wrapper logic

The main architectural change is simple to describe: the branch pushes more of the sensitive Crypt4GH work to the place where the files are actually produced.

Earlier versions relied more on **orchestrator-side command packing**. In practice, that meant Galaxy authored too much of the wrapper behavior centrally.

#### Why the older approach was a problem

- It kept too much sensitive execution planning in the orchestrator.
- It separated command construction from the code that observed the real filesystem state.
- It made fail-closed cleanup and finalization harder to reason about.
- It increased divergence between non-Pulsar remote-evaluation flows and Pulsar command-assembly flows.

#### Why the branch moved toward remote evaluation

Remote evaluation gives the compute-side path the information and control needed to make finalization more local and more verifiable.

Benefits in the current branch:

- finalization happens near the produced files,
- cleanup and failure behavior follow real execution outcome,
- pre-success verification can inspect real on-disk results,
- discovered outputs can be finalized before persistence,
- and the fail-closed path is easier to reason about because more of the plaintext lifecycle is owned in one place.

This does **not** eliminate every architecture tension. Pulsar still needed its own parity branch because its command assembly differs from the main remote-evaluation path. But the direction is now clearer and easier to defend.

### Branches and review boundaries

#### Primary branch: `explore-crypt4gh-library-support-merged-with-is-recryptor-from-26.0`

This branch contains the full non-Pulsar hardening set and is the main subject of this handover.

#### Pulsar branch: `work/pulsar-tail-20260718`

This separate worktree and branch contains the Pulsar-specific command assembly fixes and parity tests that were intentionally extracted from the main line of work.

Summary of the Pulsar branch:

- files changed: `lib/galaxy/jobs/command_factory.py`, `test/unit/app/jobs/test_command_factory.py`, plus branch metadata context;
- tests passed: 14/14;
- purpose: make Pulsar `remote_command_line` assembly match the non-Pulsar wrapper and failure-gating intent more closely.

The key Pulsar fixes were:

1. explicit shell-command separation before follow-up steps,
2. success gating for follow-up commands,
3. grouped gating for the whole follow-up chain,
4. configured shell usage instead of hard-coded `bash`,
5. script-directory-based path resolution,
6. quoted script path handling for spaces.

The added parity tests are useful, but they are still mostly **command-shape assertions**, not runtime parity proof. The Pulsar branch is therefore mitigated, but not fully de-risked.

#### Other already-existing PR-relevant branches

- `work/fix-remote-tool-eval-python-fail-closed` already exists for earlier remote-tool-eval hardening.
- `work/pulsar-tail-20260718` already exists for the Pulsar parity slice.
- `backup/explore-...-before-pulsar-rewrite-20260718` preserves the pre-extraction state.

### Reference snapshot

If you need the branch inventory details while reviewing, use this snapshot:

> - Primary branch: `explore-crypt4gh-library-support-merged-with-is-recryptor-from-26.0`
> - Primary branch scope: all non-Pulsar Crypt4GH fail-closed work in this worktree
> - Primary branch head: `41589e4876`
> - Base commit on `dev`: `5b9b6d3f20`
> - Main branch diff size: 16 files changed, `+4012/-223`
> - Main branch verification status: 133 tests passed, 0 failed
> - Pulsar follow-up branch: `work/pulsar-tail-20260718`
> - Pulsar branch diff size: 3 files changed, `+145/-7`
> - Pulsar branch verification status: 14 tests passed, 0 failed

### Code changes by file

#### `lib/galaxy/tools/crypt4gh_remote_execution.py`

This file is now the main control point for non-Pulsar Crypt4GH safety checks.

Key changes:

- pre-success output evidence verification via `verify_crypt4gh_pre_success_output_evidence()`,
- safer finalization of extra-files payloads,
- allowed-root containment checks,
- race-tolerant marker and extension resolution,
- finalize-before-persist support for discovered outputs,
- plaintext root provenance support via `plaintext_root_paths`,
- stronger purge diagnostics and symlink handling,
- and opt-in debug emission.

Why it matters:

- most of the branch’s non-Pulsar fail-closed policy now lives here,
- both declared and discovered output enforcement depend on it,
- and it most clearly expresses the move toward compute-side finalization.

Generalization potential:

- `_assert_path_within_allowed_roots()` is a generally useful containment helper,
- the best-effort purge diagnostic pattern is likely reusable outside Crypt4GH,
- and some of the safe extra-files handling ideas may generalize.

What is still specific:

- the pre-success Crypt4GH evidence verifier itself is strongly datatype- and workflow-specific.

#### `lib/galaxy/tools/remote_tool_eval.py`

This file makes the remote runtime behave more safely when cleanup or finalization steps fail.

Key changes:

- embedded cleanup and finalize script hardening,
- stronger failure propagation,
- and improved marker and cleanup handling.

Why it matters:

- it is the runtime bridge that lets remote evaluation own more of the fail-closed path,
- and it is one of the clearest concrete outcomes of the move away from centrally packed wrapper logic.

Generalization potential:

- fail-closed bootstrap and failure-propagation improvements,
- some remote-eval cleanup behavior,
- and possibly parts of the embedded script hardening.

Still mostly Crypt4GH-specific in practice:

- finalize semantics and marker expectations.

#### `lib/galaxy/model/store/discover.py`

This file closes the “persist first, secure later” problem for discovered outputs.

Key changes:

- integration of `finalize_about_to_persist_crypt4gh_payload()` into object-store persistence paths,
- and propagation of `require_crypt4gh_extension` through discovery metadata flows.

Why it matters:

- discovered outputs can no longer be persisted first and finalized later,
- and extension enforcement is less likely to vary by caller.

Generalization potential: low. The design pattern may be interesting more broadly, but the concrete hooks are mostly Crypt4GH-specific.

#### `lib/galaxy/jobs/__init__.py`

This file now ties job success more directly to Crypt4GH output evidence.

Key changes:

- integration of the Crypt4GH pre-success verifier into job completion handling,
- and removal of the earlier over-broad output scope-gate helpers.

Why it matters:

- job success is now conditional on stronger Crypt4GH evidence,
- and this is where the branch corrected the production regression.

Generalization potential:

- the idea of a datatype- or policy-specific pre-success verifier is broadly interesting,
- but the current implementation remains tightly Crypt4GH-oriented.

#### `lib/galaxy/datatypes/binary.py`

This small change fixes a real metadata correctness problem.

Key change:

- `Crypt4GHDynamicCompressedArchive.set_meta()` now resets header-related metadata correctly when clearing compute-keypair state.

Why it matters:

- it fixes stale metadata leakage in modify-input style tools,
- even though the diff itself is easy to miss in review.

Generalization potential: none in practice; this is Crypt4GH-specific datatype behavior.

#### `lib/galaxy/job_execution/output_collect.py`

This file carries the output target information needed for the narrower plaintext-root checks.

Key change:

- declared output target information is threaded through for plaintext-root containment.

Why it matters:

- it is part of the post-regression fix,
- and it narrows the enforcement target from “all outputs must stay in job scope” to “plaintext provenance must stay in approved plaintext roots.”

Generalization potential: low.

#### `lib/galaxy/security/object_wrapper.py`

This file contains a clean general-purpose bug fix that was discovered while reviewing Crypt4GH-adjacent behavior.

Key change:

- `wrap_with_safe_string()` now derives dynamic wrapper naming from the resolved wrapped class instead of the wrapped instance value, which removes the `NoneDataset`-related warning path.

Why it matters:

- it eliminates a noisy and misleading runtime warning,
- and the fix itself is not Crypt4GH-specific.

This remains the clearest **general Galaxy PR candidate** in the worktree.

#### `lib/galaxy/jobs/command_factory.py` (Pulsar branch only)

This file is the Pulsar-side parity follow-up, not part of the primary worktree head.

Why it matters:

- it makes Pulsar command assembly more faithful to the non-Pulsar chain shape,
- especially around ordering, shell choice, success gating, and path resolution.

Possible general-PR candidates:

- configured shell use,
- script-directory path resolution,
- path quoting with spaces.

The specific gating layout was motivated by Crypt4GH wrapper parity and may not need to be generalized as-is.

### Test coverage by file

#### `test/unit/jobs/test_crypt4gh_remote_execution.py`

This is now the main regression net for the branch’s non-Pulsar security behavior.

It covers:

- declared output finalization,
- discovered output finalization,
- pre-success verification,
- extra-files handling,
- containment failures,
- purge diagnostics,
- metadata reset,
- extension resolution,
- helper and readiness checks,
- and TTL boundary behavior.

Assessment:

- high value as a regression net,
- but also the biggest maintainability concern in the test suite.

Tests that may be too process-focused:

- long purge and race tests with very specific internal naming,
- some detailed mock chains that may be more brittle than behavior-first tests,
- and some edge-case resolution tests that assert security-relevant internals rather than only user-visible outcomes.

That does not make them bad tests by default. In a security hardening branch, some implementation-proximate regression tests are justified. The main concern is size and concentration.

#### `test/unit/jobs/test_remote_tool_eval.py`

This file gives direct coverage to the runtime seam where cleanup, finalize, and failure handling meet.

Assessment:

- useful because it exercises the embedded-script/runtime boundary, not just the higher-level API,
- but it overlaps with the main remote-execution test file and may deserve future consolidation.

Possible future cleanup:

- remove some overlapping tests if equivalent behavior is covered more clearly elsewhere.

Reason not to simplify aggressively now:

- script-level behavior and API-level behavior are still distinct failure surfaces.

#### `test/unit/data/model/test_model_discovery_crypt4gh.py`

This file keeps the discovery-time rules in a dedicated and sensible place.

It covers:

- discovery-time extension resolution,
- race cases,
- and `require_crypt4gh_extension` propagation.

Assessment:

- mostly behavior-aligned,
- some overlap with `test_crypt4gh_remote_execution.py`,
- but still a reasonable home for discovery semantics.

#### `test/integration/test_crypt4gh_remote_execution.py`

This is the highest-confidence test file because it exercises end-to-end behavior.

It covers integrated behavior including:

- collection discovery paths,
- TTL boundary scenarios,
- and the broader runtime path rather than isolated helpers.

Assessment:

- good behavioral coverage,
- and still the best place to answer “does this actually work in the integrated runtime?”

Future priority:

- more destination-topology coverage,
- and real Pulsar runtime parity coverage.

#### `test/unit/app/jobs/test_job_wrapper_crypt4gh.py`

This file adds small, focused job-wrapper integration checks.

Assessment:

- good signal,
- low maintenance,
- and includes the regression where tracked object-store paths must remain allowed.

#### `test/unit/app/jobs/test_output_collect_crypt4gh.py`

This file carries the minimal coverage needed for plaintext-root propagation.

Assessment: tiny but justified.

#### `test/unit/data/datatypes/test_crypt4gh.py`

This file covers the datatype-specific metadata fixes directly.

Assessment:

- focused,
- behavior-led,
- and worth keeping.

#### `test/unit/util/test_object_wrapper.py`

This file is a clean, targeted regression test for the dynamic wrapper fix.

Assessment: an excellent general regression test that should likely stay even if the Crypt4GH branch is later split into smaller PRs.

#### `test/unit/app/jobs/test_command_factory.py` (Pulsar branch)

This file gives useful branch-local parity coverage for Pulsar command order and failure gating.

Assessment:

- currently more process- and string-shape-focused than behavior-focused,
- acceptable as branch-local parity tests,
- but a real runtime Pulsar test would be more convincing long term.

### Tests that may need later cleanup

If this work is later split into smaller follow-up PRs, the following test categories deserve another look:

1. **Exact command-shape Pulsar tests** where the real concern is runtime behavior.
2. **Some long purge and race unit tests** whose names and structure encode internal detail more than externally meaningful behavior.
3. **Duplicated unit coverage across `test_crypt4gh_remote_execution.py` and `test_remote_tool_eval.py`** where both tests prove nearly the same security property from adjacent seams.

Recommendation: do **not** remove them immediately. Keep them while the branch is settling, then consolidate once the intended permanent PR boundaries are decided.

### Potential PR decomposition

The worktree is strongly Crypt4GH-focused overall, but a few pieces stand out as candidates for more general Galaxy hardening PRs.

#### Strong candidates

1. **`lib/galaxy/security/object_wrapper.py` / `test/unit/util/test_object_wrapper.py`**
   - clearly general,
   - self-contained,
   - useful beyond Crypt4GH.

2. **Allowed-root containment helper patterns** from `crypt4gh_remote_execution.py`
   - especially if Galaxy wants a shared containment utility for file-sensitive code.

3. **Best-effort purge diagnostic patterns**
   - classifying concurrent mutation vs permission errors is generally valuable.

#### Medium candidates

4. **Remote-tool-eval failure propagation hardening**
   - may be extractable if separated from the Crypt4GH-specific finalize script logic.

5. **General pre-success verifier hook pattern**
   - not the current Crypt4GH verifier itself, but the idea of pluggable datatype- or policy-specific success gates.

#### Probably not worth generalizing first

6. **Discovery integration hooks**
   - conceptually interesting, but the concrete changes here are still tightly bound to Crypt4GH.

7. **Pulsar command-gating details**
   - some pieces are general, but the motivating chain shape is specific enough that a generalized PR would need careful reframing.

### Key corrections and trade-offs during the branch

This branch improved materially, but it also needed a few important corrections on the way.

#### Discovery-path encryption bypass

Problem:

- discovered outputs could bypass the intended encryption contract if Galaxy persisted them before Crypt4GH finalization.

Resolution:

- finalization was wired into the about-to-persist discovery path,
- allowed-root provenance was made explicit,
- and coverage was added at both discovery and integration levels.

#### Over-broad scope gate that caused real deployment breakage

Problem:

- the original pre-success scope gate enforced the wrong boundary and rejected legitimate object-store dataset destinations.

Resolution:

- the gate was removed,
- the security goal was restated more precisely as plaintext provenance control,
- `plaintext_root_paths` was introduced,
- and regression tests were added for the correct distinction.

This was a healthy correction. The branch became safer by becoming more precise.

#### Marker and extension race behavior

Problem:

- marker-only evidence is race-prone, especially in cleanup-heavy or concurrent paths.

Resolution:

- better evidence is required in non-required paths,
- `.c4gh` semantics are forced in required contexts,
- and specific directory races are treated as “no evidence” rather than accidental success.

#### Purge safety under hostile filesystem timing

Problem:

- best-effort purge can become unsafe or too opaque around symlinks, permission failures, and concurrent mutation.

Resolution:

- roots are validated before deletion,
- symlinks are unlinked instead of traversed,
- and diagnostics distinguish categories of failure.

#### Interleaved Pulsar and non-Pulsar history

Problem:

- the git history became hard to review because Pulsar and non-Pulsar changes were interleaved.

Resolution:

- Pulsar work was extracted into a separate worktree and branch,
- keeping the main branch focused on non-Pulsar hardening.

This improved reviewability even though it made history reconstruction more manual.

### Remaining risks

1. **Pulsar parity is still under-verified at runtime**
   - unit parity tests exist, but real Pulsar runtime parity is still not proven.

2. **Untracked plaintext outputs are still outside the strongest guarantees**
   - the hardening work covers tracked, declared, and discovered outputs, not arbitrary tool writes to unrelated paths.

3. **Destination topology coverage is incomplete**
   - current tests cover important cases, not every deployment pattern.

4. **Toolshed and fixture variation risk remains**
   - in-tree fixture confidence is better than before, but not universal.

5. **Large security unit files may become brittle over time**
   - especially if future reviewers struggle to separate behavior assertions from implementation scaffolding.

6. **TTL policy may be conservative for some operators**
   - the 24-hour floor is safe, but may not fit every deployment.

### Future work

1. Add **real Pulsar integration/runtime tests**.
2. Expand **destination-matrix integration coverage**.
3. Add more **toolshed/discovery fixture coverage**.
4. Consider whether **containment and purge diagnostics** should become shared Galaxy utilities.
5. Revisit **test consolidation** once PR boundaries stabilize.
6. Consider whether **TTL floor configuration** should be more operator-friendly.
7. If desired, split out the **object-wrapper fix** into a general PR.

### Final assessment

For the **non-Pulsar** scope, this branch is in a substantially better state than the branch base.

The key outcomes are summarized in the overview; the deep-dive sections above explain why those outcomes now hold and where the remaining risks still sit.

The main caution is no longer “is this work useful?” but “how should it now be reviewed and split?” The best near-term split candidates are the general object-wrapper fix, any reusable containment or diagnostic helpers, and the isolated Pulsar parity branch.

## Appendix

### Appendix A. Published demo history (optional, but useful as a live demonstrator)

A publicly shared history is available at:
[https://galaxy.semsec.bsc.es/u/sveinugu/h/handoff-demo](https://galaxy.semsec.bsc.es/u/sveinugu/h/handoff-demo)

The history (id `eaa9b06464bd346f`, Galaxy 26.0) contains 25 items and demonstrates the complete Crypt4GH encryption → analysis → output encryption → recryption flow with publicly viewable datasets.

#### History summary

The starting point is a single Crypt4GH-encrypted FASTQsanger file uploaded via data fetch:

- **HID 1**: `1.fastqsanger.c4gh` — the initial encrypted input dataset

From there, two parallel analysis tracks were run against the compute-recrypted copies.

**Branch A — simple output (Select first):**
1. `Show beginning1` on the recrypted input (HID 4) → produced `fastqsanger.c4gh` output.
2. `Show beginning1` on the FastQC RawData `txt.c4gh` output (HID 24) → produced `txt.c4gh`.
3. Both outputs were re-recrypted via the UI key icon (HID 14, 25).

**Branch B — split + FastQC (collection discovery path):**
1. `split_file_to_collection` (toolshed `0.5.2`) on recrypted input (HID 8, 19) → collections of `fastq.c4gh` elements.
2. `FastQC` (toolshed `0.74+galaxy1`) on the recrypted input (HID 9, 10) → `html.c4gh` and `txt.c4gh` outputs.
3. Split outputs and FastQC outputs were re-recrypted via the UI key icon (HID 15, 16, 22, 23).

#### Recryption pattern

Throughout the history, datasets are tagged with `Recrypted_for_compute` and `cnk:38al0qyb` (the compute key ID). The pattern is:

1. A job produces an encrypted output with a `.c4gh` extension.
2. The user clicks the UI key icon, **Recrypt Crypt4GH-encrypted dataset**, to obtain a compute-recrypted copy.
3. The recrypted copy keeps the same dataset content but uses headers recrypted to the compute-side key context.
4. Both the original and the recrypted copy are preserved in the history; the recrypted copy is the one used for further compute-side operations.

#### Tools used

- **Show beginning1** (`Show beginning1`) — Galaxy built-in text selection/head tool.
- **FastQC** (`toolshed.g2.bx.psu.edu/repos/devteam/fastqc/fastqc/0.74+galaxy1`) — quality control.
- **Split file to collection** (`toolshed.g2.bx.psu.edu/repos/bgruening/split_file_to_collection/split_file_to_collection/0.5.2`) — collection discovery.
- **__DATA_FETCH__** — file upload.
- **__SET_METADATA__** — metadata management during recryption and dataset operations.

#### Architecture notes

- **User-side recryptor** → **compute-side recryptor** at `https://galaxy.semsec.bsc.es:8443`.
- The UI key icon triggers a user-side recryption that talks to the compute-side recryptor over this TLS connection.
- Galaxy `remote_eval` code talks only to the compute-side recryptor; it never contacts the user-side recryptor or handles user private keys.
- No private user keys or compute key materials were shared between the two service boundaries; only encrypted content traverses the two trusted connections.
- The compute key ID `cnk:38al0qyb` visible in dataset tags across the history confirms consistent compute-key binding.

#### Future work for the demo environment

- The two TLS connections (user → compute recryptor and Galaxy `remote_eval` → compute recryptor) should be hardened with authentication and authorization infrastructure (AAI).
- Proper key-management lifecycles such as rotation, revocation, and auditing remain to be implemented for production deployments.
- The current demo setup trusts both connections at the network level; production environments should add token-based or certificate-based authentication per connection.

#### Coverage notes

The demo history exercises the core encryption → analysis → output → recryption loop for simple outputs and collection discovery paths. It does **not** yet manually cover:

- tools that produce extra-files outputs,
- dataset discovery pathways beyond the `split_file_to_collection` pattern,
- or workflow execution, since all steps were run individually rather than inside a Galaxy workflow.

### Appendix B. Reference documents and suggested reading order

If someone new needs to understand this work quickly, this order should minimize context switching:

1. `CRYPT4GH-HANDOVER.md` (this file)
2. `crypt4gh-fail-closed-evaluation.md`
3. `crypt4gh-phase-2-reimplementation-design.md`
4. `lib/galaxy/tools/crypt4gh_remote_execution.py`
5. `test/integration/test_crypt4gh_remote_execution.py`
6. `test/unit/jobs/test_crypt4gh_remote_execution.py`
7. Pulsar branch diff (`work/pulsar-tail-20260718`) if Pulsar parity matters for the next step

Supporting document map:

- `crypt4gh-fail-closed-evaluation.md`
  - the best current-state document for the gap-by-gap status, mitigations, and residual risks.

- `crypt4gh-galaxy-support.md`
  - the older support and design document; still useful for historical context, but no longer the best single source for the current fail-closed runtime story.

- `crypt4gh-phase-2-implementation-plan.md`
  - the approved implementation path, especially the shift toward compute-side recryptor B and remote evaluation.

- `crypt4gh-phase-2-output-enforcement-plan.md`
  - the narrower output-enforcement slice and the reasoning behind discovered-output finalization.

- `crypt4gh-phase-2-output-enforcement-spec.md`
  - the tighter requirements companion to the output-enforcement plan.

- `crypt4gh-phase-2-reimplementation-design.md`
  - the best document for understanding the architectural redesign, especially the trust model and the user-side recryptor A / compute-side recryptor B split.

- `crypt4gh-remote-exec-findings.md`
  - the earlier findings document that explains why `remote_tool_eval.py` became the preferred compute-side hook.

- `test-data/crypt4gh/MANUAL_TESTING.md`
  - operator-oriented manual checks and fixture mirroring guidance.

- `test-data/crypt4gh/HANDOFF.md`
  - prior live-smoke and delivery context.
