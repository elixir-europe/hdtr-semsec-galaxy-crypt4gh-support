# Crypt4GH branch handover

## Overview

This handover covers the full Crypt4GH story on this branch, from the first ability to upload and recognize Crypt4GH datasets, through the browser-side recrypt workflow, through the remote-execution redesign, and finally through the fail-closed hardening cycle.

If you want a live example before reading code, see **Appendix A** for a public Galaxy history that demonstrates the end-to-end Crypt4GH flow.

> **Current state vs historical artifacts**
>
> This handover describes the current code path on this branch. Older documents and local testing notes in the tree still use earlier naming, especially `enable_crypt4gh_transparent_staging` and older `re-encryptor` / `reencryption` wording. When this handover and older artifacts differ, prefer the current code surfaces cited here: `enable_crypt4gh_transparent_input_matching`, `enable_crypt4gh_remote_execution_staging`, and `crypt4gh_reencryption_service_url`.

### The short version

- Galaxy can now recognize Crypt4GH-encrypted datasets, keep their type information, and carry their encryption header as metadata.
- Users can recrypt eligible datasets from the UI so compute-side jobs can work on them without exposing user private keys to Galaxy.
- Remote execution moved the sensitive decrypt, finalize, and cleanup work closer to the compute environment.
- The later hardening work made jobs fail earlier when the secure path is missing and made encrypted outputs get checked before Galaxy records success or saves discovered files.
- Pulsar follow-up work was split into its own branch so it can be reviewed separately.
- The checked-in UI recrypt button still depends on a browser-local service endpoint and is not yet generalized by Galaxy config.

In repo-specific terms, the branch spans six phases: dataset and datatype support, client-side recrypt UI, design and planning artifacts, remote-evaluation implementation, follow-up fixes and enforcement work, and the final fail-closed hardening cycle (tracked elsewhere as Gap #1–#14).

### Who should read which part

- **Overview**: for anyone who wants the main outcome in plain language.
- **Quick start / setup guide**: for operators and anyone trying to reproduce the supported path.
- **Deep dive**: for maintainers and reviewers who want the branch history, architecture evolution, and file-by-file coverage.
- **Appendix**: for the public demo, reading map, and supporting reference material.

### What this branch added over time

Seen as one long branch rather than one hardening cycle, the work progressed through six steps:

1. **Dataset support**: Galaxy learned how to detect `.c4gh` files, preserve their underlying datatype, and store Crypt4GH header metadata.
2. **User workflow support**: the history UI gained a recrypt action so encrypted datasets could be prepared for compute use.
3. **Architecture and planning**: design and plan documents reworked the trust model and separated user-side and compute-side responsibilities.
4. **Remote execution**: job execution moved from earlier staging ideas toward remote-evaluation handling close to the compute node.
5. **Fixes and output enforcement**: later work closed gaps in runtime extension handling, discovered outputs, extra-files handling, and postrun behavior.
6. **Fail-closed hardening**: the final cycle tightened path containment, TTL handling, output evidence checks, purge safety, and discovered-output finalization.

### Current code state at a glance

The branch now blocks unsafe behavior at four clearer checkpoints in the job lifecycle:

1. **Before launch**: jobs stop early if the supported secure path is missing; technically, readiness checks require the remote-evaluation path and its required config.
2. **During execution**: encryption cleanup happens closer to where files are produced; technically, the compute-side path owns more of the finalize and cleanup flow.
3. **Before success / before persistence**: Galaxy checks encrypted outputs before treating the run as complete or saving discovered files.
4. **After finalization errors**: cleanup reports failures more clearly and handles risky filesystem cases more defensively.

In practical terms, the branch is stronger around:

- upload and type inference for encrypted datasets,
- header metadata capture and dynamic `.c4gh` datatype handling,
- browser-driven recrypt workflows,
- remote input staging and compute-side reheadering,
- declared and discovered output encryption before persistence,
- marker and extension handling,
- TTL / expiry fail-closed behavior,
- plaintext staging containment,
- metadata reset for modify-input style flows,
- extra-files handling,
- cleanup diagnostics,
- and keeping debug output opt-in.

### Remaining risks at a glance

- Browser-side UI recrypt still depends on the hard-coded `https://localhost:61357/recrypt_header` endpoint.
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
4. Confirm the user-side recrypt flow that prepares compute-readable inputs (see section 6).
5. Review the demo-grade vs production-grade caveats (see section 7).
6. Set TTL and debug preferences (see sections 8 and 9).
7. Run manual verification (see section 10).

### 1. Supported execution model

The current branch supports a specific remote execution model. In plain terms: Galaxy stores encrypted data and the metadata needed to work with it, the user-side flow prepares inputs for compute use, and the compute-side path handles the sensitive decrypt, finalize, and cleanup work. Local tool evaluation is intentionally not part of the supported Crypt4GH flow.

### 2. Recryptor A vs recryptor B

The later phases of the branch split the recrypt workflow into two roles:

- **Recryptor A (user-side)**: the upstream or browser-adjacent service that works with user-side material and obtains compute-side key context for input staging.
- **Recryptor B (compute-side)**: the service Galaxy talks to during remote execution for header recrypt operations against compute-side key context.

This split is important because Galaxy is not meant to hold user private keys or compute-side private keys.

### 3. Minimum Galaxy configuration

Minimum Galaxy-side settings for the supported remote Crypt4GH path:

```yaml
galaxy:
  enable_crypt4gh_transparent_input_matching: true
  enable_crypt4gh_remote_execution_staging: true
  crypt4gh_reencryption_service_url: http://recryptor-b:36667
  metadata_strategy: extended
  outputs_to_working_directory: true
  tool_evaluation_strategy: remote
```

Important notes:

- `enable_crypt4gh_remote_execution_staging` is the current runtime gate for the remote Crypt4GH path and requires `enable_crypt4gh_transparent_input_matching = true`.
- `crypt4gh_reencryption_service_url` is the current in-repo config key and refers to compute-side recryptor B.
- `metadata_strategy: extended` is required for this execution model.
- `outputs_to_working_directory: true` is required for the current fail-closed path.
- `tool_evaluation_strategy: remote` is required; local evaluation is intentionally rejected for this Crypt4GH flow.
- Older docs and local examples in this tree may still show `enable_crypt4gh_transparent_staging`; treat that as historical naming, not the current split flag surface.

### 4. Job destination expectations

If the deployment uses destination config, the effective execution mode still needs to resolve to:

- transparent input matching enabled,
- remote Crypt4GH staging enabled,
- remote tool evaluation,
- extended metadata,
- working-directory outputs.

In other words, destination-level settings and global defaults still need to combine into the same supported behavior.

### 5. Compute-side recryptor B setup

The compute-side service must:

- be reachable from the compute environment,
- mint and track time-bounded compute-side key ids,
- recrypt headers from compute-side key context into job-local key context,
- and recrypt output headers back toward user-readable context.

Galaxy runtime directly depends on these routes:

- `POST /recrypt_header_to_job_key`
- `POST /recrypt_header_to_user_key`

Related but separate route:

- `POST /get_compute_key_info` is used by upstream/user-side/manual/live-service flows that fetch compute-key context before Galaxy runtime begins.

For local or manual testing, the repo already contains a mock compute-side service via the integration test harness for the two runtime routes above. The live-smoke notes in `test-data/crypt4gh/HANDOFF.md` show the separate `/get_compute_key_info` probe against a real compute-mode service.

### 6. User-side recryptor A expectations

The user-side service is upstream of this branch’s runtime hardening work. It obtains compute-side key information and recrypts input headers into compute-side context so later jobs can run without handing user private keys to Galaxy.

The history UI recrypt button is one visible piece of that flow. This branch does **not** reimplement user-side key custody inside Galaxy.

Current checked-in UI constraints:

- `client/src/components/History/Content/Dataset/DatasetActions.vue` posts to `https://localhost:61357/recrypt_header`.
- That browser-side endpoint is not Galaxy-configured in this branch.
- The button therefore assumes a browser-local or otherwise user-adjacent recryptor A service.
- Deploying compute-side recryptor B alone is not enough to make the UI button work.

### 7. Demo-grade vs production-grade edges

- **UI endpoint assumption:** the checked-in UI still hard-codes `https://localhost:61357/recrypt_header` instead of reading a Galaxy-managed setting.
- **Pulsar proof level:** the separate Pulsar branch has targeted parity unit tests, but not the same runtime proof level as the main non-Pulsar path.
- **TLS / AAI maturity:** the demo and live-smoke material show trusted connections, not full production authentication/authorization hardening.
- **Plaintext scope:** the strongest fail-closed guarantees cover tracked declared/discovered outputs, not arbitrary untracked plaintext files written elsewhere.

### 8. TTL-related settings

Current practical policy:

Notes:

- this worktree does **not** currently expose verified YAML keys for the minimum TTL policy in the active code/config schema;
- the branch preserves a conservative default floor of 24 hours via `_DEFAULT_MINIMUM_TTL = timedelta(days=1)`;
- destination walltime can raise that floor through `_minimum_ttl_for_destination(...)`;
- equality with the minimum boundary is treated fail-closed (`ttl_left <= minimum_ttl`);
- operators may eventually want a more flexible policy, but the current default is intentionally cautious.

### 9. Debugging

Debug output is now opt-in:

```bash
GALAXY_CRYPT4GH_DEBUG=1
```

Without that environment variable, the branch avoids noisy default debug output.

### 10. Manual verification references

Useful operator-oriented docs already in the tree:

- `test-data/crypt4gh/MANUAL_TESTING.md`
- `test-data/crypt4gh/HANDOFF.md`

Use them with two cautions:

- `test-data/crypt4gh/MANUAL_TESTING.md` still uses older naming such as `enable_crypt4gh_transparent_staging`; follow the current flag names in this handover when they differ.
- `test-data/crypt4gh/HANDOFF.md` is the better source for exact live-smoke commands and recorded command output.

These are still the best starting points for practical manual checks and live-smoke setup.

If you only need supported setup and manual verification, you can stop here. The remaining sections are for reviewers and maintainers.

---

Everything below is optional **deep-dive material for reviewers and maintainers**.

## Deep dive (for reviewers and maintainers)

### Branch scope and chronology

This handover now covers the entire branch history from its divergence from `dev` / `origin/dev`, not only the last hardening cycle.

The chronology below follows the Crypt4GH-relevant branch story, not every unrelated upstream merge visible in the raw branch log.

#### Phase 1 — Initial Crypt4GH dataset support

This phase taught Galaxy to treat Crypt4GH as a real encrypted dataset type instead of opaque binary content.

Main outcomes:

- Crypt4GH compression detection was added.
- Dynamic `.c4gh` datatype handling was added, including nested cases such as `.gz.c4gh`.
- Upload type inference improved so Galaxy could keep the inner datatype, such as `fastqsanger.c4gh`.
- Tests and fixtures were added for encrypted FASTQ handling.
- The branch moved away from a `crypt4ghfs` mounting idea toward direct `crypt4gh` library usage.
- Output encryption support and datatype support were merged into the later `Crypt4GHDynamicCompressedArchive` design.
- The file suffix standardized on `.c4gh` instead of `.crypt4gh`.
- The `crypt4gh` package was added as a dependency.

Why this phase mattered:

- it made encrypted files first-class datasets,
- it preserved useful inner-type information,
- and it created the metadata hooks needed for later recrypt operations and remote execution work.

#### Phase 2 — Client-side recrypt UI

This phase added a user-visible way to prepare encrypted datasets for compute use.

Main outcomes:

- the history UI gained a recrypt button,
- the recrypt key icon was shown for dynamic file types as well as static ones,
- supporting Galaxy metadata behavior was adjusted so Crypt4GH metadata could flow through the UI path,
- and one earlier binary-datatype bugfix was later reverted after the branch clarified how the path should behave.

Why this phase mattered:

- it turned the earlier datatype support into an operator and user workflow,
- and it made the recrypt process visible and usable instead of purely architectural.

Operational note:

- the checked-in UI slice is still specialized: `DatasetActions.vue` posts to a hard-coded browser-local endpoint rather than a Galaxy-configured route.

#### Phase 3 — Documentation and design

This phase reworked the trust model before deeper runtime implementation continued.

Main outcomes:

- the branch documented Galaxy/Pulsar remote-execution findings,
- the Phase 2 reimplementation design was written and revised,
- the implementation plan was added and tightened,
- output-enforcement addenda and supersession notes were added,
- and a final phase handoff note was recorded.

Why this phase mattered:

- it marked the shift from an earlier single recryptor assumption and Galaxy-authored runtime model to the later split recryptor A/B model,
- and it gave the later implementation and hardening work a documented contract.

#### Phase 4 — Remote evaluation implementation

This phase turned the new design into working execution behavior.

Main outcomes:

- remote-execution contract and integration tests were defined first,
- Crypt4GH remote-eval settings were propagated into metadata handling,
- remote input staging was added for remote tool evaluation,
- reheadered input was streamed directly into decrypt,
- declared remote outputs were finalized,
- cleanup failure handling, TTL preflight, and fail-closed expiry handling were added,
- the legacy staging path was removed,
- live recryptor smoke-environment routing was supported,
- remote tool evaluation stdout and stderr preservation was fixed,
- cleanup postrun and walltime TTL gating were added,
- and remote execution helpers were split and reorganized.

Why this phase mattered:

- it made the architecture real,
- it established the compute-side runtime path,
- and it created the surfaces that later hardening work would tighten.

#### Phase 5 — Fixes and hardening iterations before the main gap cycle

This phase closed many correctness and enforcement gaps before the formal Gap #1–#14 cycle.

Main outcomes:

- remote helper logs and warning diagnostics were preserved,
- `.c4gh` suffix handling and runtime datatype lookup were corrected,
- remote output finalization and cleanup invariants were hardened,
- aiohttp-based recryptor calls and batched input recrypt runs improved the runtime path,
- discovered outputs were kept encrypted through metadata collection,
- discovered output finalization was enforced in the metadata path,
- duplicate discovered extensions were avoided,
- declared and discovered `extra_files` payloads were finalized fail-closed,
- a pre-success output evidence verifier was added,
- plaintext allow-list checks were added,
- embedded postrun Python mismatch handling was made fail-closed,
- encrypted extension resolution during finalization was required,
- transparent input readiness checks were enforced,
- and embedded Python cleanup/finalize flow was stabilized.

Why this phase mattered:

- it connected the broad design to many awkward real-world runtime edges,
- and it set up the final hardening cycle by making the biggest enforcement points explicit.

#### Phase 6 — Main fail-closed hardening cycle

This is the final cycle that the earlier handover version focused on.

Main outcomes:

- path containment and local-strategy fail-closed handling were strengthened,
- the collection discovery encryption bypass was fixed,
- metadata reset and allowed-root provenance were tightened,
- extension resolution and traversal containment were hardened,
- best-effort purge behavior became more defensive,
- TTL boundary behavior and marker evidence handling were tightened,
- the dynamic wrapper warning path was fixed,
- debug output was gated behind explicit opt-in,
- plaintext-root constraints replaced an over-aggressive earlier scope gate,
- Pulsar work was extracted to a separate worktree,
- and follow-up coverage broadened the regression net.

Why this phase mattered:

- it made the runtime safer under failure,
- it corrected a few earlier over-broad or under-specified checks,
- and it produced the branch’s current fail-closed posture.

### Architecture evolution across the branch

The architecture changed substantially over the life of the branch. The easiest way to understand it is as three major transitions.

#### 1. From “encrypted file upload” to “encrypted typed dataset”

At first, the key problem was basic: Galaxy needed to know that a file was Crypt4GH-encrypted without losing the useful information about what was inside it.

That led to:

- magic-byte detection,
- dynamic `.c4gh` datatype registration,
- nested compressed-type support,
- and header extraction into dataset metadata.

This phase made Crypt4GH a dataset and metadata problem, not just a raw file problem.

#### 2. From `crypt4ghfs` ideas to direct library usage

An earlier direction relied more on mount-style access. The branch later moved to direct `crypt4gh` library usage.

Why that mattered:

- it reduced operational coupling,
- it made testing and packaging simpler,
- and it gave later remote-execution code more direct control over header handling and output finalization.

This was an important early simplification before the larger runtime redesign.

#### 3. From orchestrator-authored behavior to compute-side remote evaluation

The later design work concluded that Galaxy should not author too much of the sensitive decrypt and finalize behavior centrally.

That led to:

- splitting the recrypt concept into user-side recryptor A and compute-side recryptor B,
- treating Galaxy as the keeper of encrypted datasets and metadata rather than private keys,
- moving runtime crypto actions behind `remote_tool_eval.py`,
- and keeping plaintext handling and cleanup closer to the compute workspace.

Why the newer approach was chosen:

- the code seeing the real files can make better cleanup and fail-closed decisions,
- compute-side key custody stays separated from Galaxy,
- output finalization can happen before persistence,
- and the trust model is easier to explain and audit.

This does **not** eliminate every architecture tension. Pulsar still needed its own parity branch because its command assembly differs from the main remote-evaluation path. But the branch direction is now much clearer than in the earlier phases.

### Branches and review boundaries

#### Primary branch: `explore-crypt4gh-library-support-merged-with-is-recryptor-from-26.0`

This branch contains the full non-Pulsar Crypt4GH story from datatype support through hardening.

#### Pulsar branch: `work/pulsar-tail-20260718`

This separate worktree and branch contains the Pulsar-specific command assembly fixes and parity tests that were intentionally extracted from the main line of work.

Summary of the Pulsar branch:

- files changed: `lib/galaxy/jobs/command_factory.py`, `test/unit/app/jobs/test_command_factory.py`, plus branch metadata context;
- targeted parity unit tests were reported as passing in the branch-local handoff, but this handover does not independently restate an exact count here;
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
- a temporary logging commit existed during the branch history and was later removed.

### Reference snapshot

If you need the current branch inventory details while reviewing, use this snapshot:

> - Primary branch: `explore-crypt4gh-library-support-merged-with-is-recryptor-from-26.0`
> - Earlier hardening-cycle base reference: `5b9b6d3f20`
> - Branch scope: full non-Pulsar Crypt4GH history from initial dataset support through hardening
> - Pulsar follow-up branch: `work/pulsar-tail-20260718`
> - Other related branch: `work/fix-remote-tool-eval-python-fail-closed`

### Code changes by file and area

The branch touches many files. The most useful way to read them is by capability rather than by commit.

#### Phase 1 surfaces: detection, datatypes, and upload handling

##### `lib/galaxy/util/checkers.py`, `lib/galaxy/util/crypt4gh.py`, `lib/galaxy/datatypes/sniff.py`

These files gave Galaxy the ability to recognize Crypt4GH files and reason about them without decrypting payloads.

Why they matter:

- they distinguish Crypt4GH from generic binary content,
- they let upload and sniff logic preserve useful inner-type information,
- and they provide the low-level header validation used across the rest of the branch.

##### `lib/galaxy/datatypes/binary.py`, `lib/galaxy/datatypes/registry.py`, `lib/galaxy/config/sample/datatypes_conf.xml.sample`

These files made `.c4gh` a first-class dynamic datatype family.

Key changes included:

- `Crypt4GHDynamicCompressedArchive`,
- header metadata extraction,
- transparent input matching gates,
- nested `.gz.c4gh` support,
- and on-demand runtime datatype creation for base types that were not preregistered.

Why they matter:

- most of the early branch value depends on these types existing,
- later metadata reset and runtime lookup fixes build on them,
- and they define the dataset-level contract that the rest of the runtime follows.

##### `lib/galaxy/datatypes/upload_util.py`, `lib/galaxy_test/api/test_tools_upload.py`, `test/unit/data/datatypes/test_sniff.py`, `test/unit/data/datatypes/test_datatypes_registry.py`, `test/unit/data/datatypes/test_crypt4gh.py`

These files cover the upload and datatype flows.

Why they matter:

- they prove that `.c4gh` datasets are recognized correctly,
- they cover nested datatype generation and metadata behavior,
- and they hold some of the earliest regression coverage in the branch.

##### `lib/galaxy/dependencies/pinned-requirements.txt`, `test-data/*crypt4gh*`, `lib/galaxy/datatypes/test/*.c4gh`

These files add the actual dependency and encrypted fixtures.

Why they matter:

- without them, the rest of the branch could not be exercised realistically,
- and they anchor both unit and manual testing.

#### Phase 2 surfaces: client-side recrypt workflow

##### `client/src/components/History/Content/Dataset/DatasetActions.vue`

This file added the visible recrypt action in the history UI.

Why it matters:

- it gives users and testers a concrete recrypt action,
- it shows the key icon for `.c4gh` datasets, including dynamic types,
- and it copies the dataset, attaches recrypt metadata, and triggers datatype redetection.

##### `lib/galaxy/metadata/__init__.py`, `lib/galaxy/metadata/set_metadata.py`, `lib/galaxy/model/__init__.py`

These files are not “the recrypt button” themselves, but they are the server-side surfaces with direct current Crypt4GH evidence for metadata loading, metadata reset, and dynamic datatype resolution.

Why they matter:

- they participate in metadata collection and reload,
- and they help runtime `.c4gh` types survive later phases of the branch.

Adjacent files such as `lib/galaxy/webapps/galaxy/controllers/dataset.py` and `lib/galaxy/managers/datasets.py` may still appear in branch history or supporting flows, but they do not currently carry direct Crypt4GH-specific evidence on inspection.

#### Phase 3 surfaces: design and planning artifacts

##### `crypt4gh-galaxy-support.md`

This document captures the early support plan and earlier design assumptions.

Why it matters now:

- it is the best record of the earliest phase goals,
- especially the initial sniffing, metadata, and early staging ideas.

##### `crypt4gh-remote-exec-findings.md`, `crypt4gh-phase-2-reimplementation-design.md`, `crypt4gh-phase-2-implementation-plan.md`

These documents explain the trust-model shift and the move toward remote evaluation.

Why they matter now:

- they document why the earlier runtime ideas were revised,
- and they remain the main architecture and plan trail for the middle of the branch.

##### `crypt4gh-phase-2-output-enforcement-plan.md`, `crypt4gh-phase-2-output-enforcement-spec.md`, `crypt4gh-fail-closed-evaluation.md`

These documents track the later enforcement and hardening semantics.

Why they matter now:

- they record the branch’s output-enforcement and fail-closed decisions,
- and they are the best supporting references for the last two phases.

#### Phase 4 and 5 surfaces: remote execution and runtime support

##### `lib/galaxy/tools/remote_tool_eval.py`

This file is the runtime bridge that lets remote evaluation own more of the Crypt4GH path.

Key branch themes here:

- remote input staging support,
- runtime setup propagation,
- embedded cleanup and finalize script hardening,
- interpreter alignment fixes,
- stdout/stderr preservation,
- and failure propagation.

Why it matters:

- it is where the architectural redesign becomes real execution behavior,
- and it is the clearest non-Pulsar compute-side seam in the branch.

##### `lib/galaxy/tools/crypt4gh_remote_execution.py`

This file is now the main control point for non-Pulsar Crypt4GH runtime safety.

Key branch themes here:

- remote input preparation,
- declared and discovered output finalization,
- extra-files finalization,
- pre-success output evidence verification,
- TTL and compute-key checks,
- path containment,
- plaintext-root provenance,
- and purge diagnostics.

Why it matters:

- most of the branch’s mature fail-closed logic lives here,
- and it concentrates many later fixes that were scattered across several phases.

##### `lib/galaxy/jobs/__init__.py`, `lib/galaxy/job_execution/output_collect.py`, `lib/galaxy/model/store/discover.py`

These files connect the runtime helper to Galaxy job completion, output collection, and discovered-output persistence.

Why they matter:

- they are the bridge between runtime behavior and stored datasets,
- they are where finalize-before-persist semantics became real,
- and they are where several tricky discovered-output and object-store issues were corrected.

##### `lib/galaxy/config/sample/galaxy.yml.sample`, `lib/galaxy/config/schemas/config_schema.yml`, `doc/source/admin/galaxy_options.rst`

These files expose the operational configuration surface.

Why they matter:

- they define how operators enable and configure the feature,
- and they reflect the branch’s move toward remote-evaluation requirements and compute-side recryptor routing.

This is also where the current-vs-legacy naming split is easiest to verify: `config_schema.yml` and `galaxy_options.rst` document `enable_crypt4gh_transparent_input_matching` and `enable_crypt4gh_remote_execution_staging`, not the older single-flag naming.

##### `lib/galaxy/util/crypt4gh.py`

This utility module centralizes low-level Crypt4GH header handling.

Why it matters:

- it gives the branch one place for basic header validation and encrypted-data checks,
- and it reduces duplication between datatype logic and runtime logic.

#### Phase 6 and follow-up surfaces: hardening corrections and general fixes

##### `lib/galaxy/security/object_wrapper.py`

This file contains a clean general-purpose bug fix discovered while reviewing Crypt4GH-adjacent behavior.

Key change:

- `wrap_with_safe_string()` now derives dynamic wrapper naming from the resolved wrapped class instead of the wrapped instance value, which removes the `NoneDataset`-related warning path.

Why it matters:

- it eliminates a noisy and misleading runtime warning,
- and the fix itself is not Crypt4GH-specific.

This remains the clearest **general Galaxy PR candidate** in the worktree.

##### `lib/galaxy/jobs/command_factory.py` (Pulsar branch only)

This file is the Pulsar-side parity follow-up, not part of the primary worktree head.

Why it matters:

- it makes Pulsar command assembly more faithful to the non-Pulsar chain shape,
- especially around ordering, shell choice, success gating, and path resolution.

### Test coverage by phase and file

#### Phase 1 tests: datatypes, sniffing, and upload

##### `test/unit/data/datatypes/test_sniff.py`

This file covers Crypt4GH detection and extension inference.

##### `test/unit/data/datatypes/test_datatypes_registry.py`

This file covers datatype registration, nested `.c4gh` variants, and transparent matching behavior.

##### `test/unit/data/datatypes/test_crypt4gh.py`

This file covers metadata extraction and later metadata reset behavior directly.

##### `lib/galaxy_test/api/test_tools_upload.py`

This file proves the upload path sees encrypted datasets as typed `.c4gh` content rather than generic binary data.

#### Phase 4–6 tests: runtime, discovery, and hardening

##### `test/unit/jobs/test_crypt4gh_remote_execution.py`

This is now the main regression net for the branch’s non-Pulsar runtime security behavior.

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

##### `test/unit/jobs/test_remote_tool_eval.py`

This file gives direct coverage to the runtime seam where cleanup, finalize, and failure handling meet.

Assessment:

- useful because it exercises the embedded-script/runtime boundary, not just the higher-level API,
- but it overlaps with the main remote-execution test file and may deserve future consolidation.

##### `test/unit/data/model/test_model_discovery_crypt4gh.py`

This file keeps the discovery-time rules in a dedicated and sensible place.

It covers:

- discovery-time extension resolution,
- race cases,
- and `require_crypt4gh_extension` propagation.

##### `test/integration/test_crypt4gh_remote_execution.py`

This is the highest-confidence test file because it exercises end-to-end behavior.

It covers integrated behavior including:

- collection discovery paths,
- metadata-source outputs resetting Crypt4GH header metadata for Crypt4GH jobs,
- collection discovery outputs being encrypted before persistence,
- TTL boundary scenarios,
- and the broader runtime path rather than isolated helpers.

##### `test/unit/app/jobs/test_job_wrapper_crypt4gh.py`

This file adds small, focused job-wrapper integration checks.

##### `test/unit/app/jobs/test_output_collect_crypt4gh.py`

This file carries the minimal coverage needed for plaintext-root propagation.

##### `test/unit/util/test_object_wrapper.py`

This file is a clean, targeted regression test for the dynamic wrapper fix.

##### `test/unit/app/jobs/test_command_factory.py` (Pulsar branch)

This file gives useful branch-local parity coverage for Pulsar command order and failure gating.

### Tests that may need later cleanup

If this work is later split into smaller follow-up PRs, the following test categories deserve another look:

1. **Exact command-shape Pulsar tests** where the real concern is runtime behavior.
2. **Some long purge and race unit tests** whose names and structure encode internal detail more than externally meaningful behavior.
3. **Duplicated unit coverage across `test_crypt4gh_remote_execution.py` and `test_remote_tool_eval.py`** where both tests prove nearly the same security property from adjacent seams.

Recommendation: do **not** remove them immediately. Keep them while the branch is settling, then consolidate once the intended permanent PR boundaries are decided.

### Key corrections and trade-offs during the branch

This branch improved materially, but it also needed a few important corrections on the way.

#### Early runtime model correction: away from `crypt4ghfs`

Problem:

- an earlier mount-style direction added operational complexity and made later runtime control harder to keep local and explicit.

Resolution:

- the branch moved toward direct `crypt4gh` library usage and later remote-evaluation handling instead.

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
7. Generalize the **browser-side UI recrypt endpoint** so it is configurable instead of hard-coded to `https://localhost:61357/recrypt_header`.
8. If desired, split out the **object-wrapper fix** into a general PR.

### Final assessment

For the **non-Pulsar** scope, this branch is no longer just a hardening branch. It is the full Crypt4GH branch history from typed dataset support through runtime redesign and fail-closed hardening.

The key outcomes are summarized in the overview; the deep-dive sections above explain how the branch evolved from upload support to UI support to remote execution to the final hardening posture.

The main caution is no longer “is this work useful?” but “how should it now be reviewed and split?” The best near-term split candidates are the general object-wrapper fix, any reusable containment or diagnostic helpers, and the isolated Pulsar parity branch.

## Appendix

### Appendix A. Published demo history (optional, but useful as a live demonstrator)

A publicly shared history is available at:
[https://galaxy.semsec.bsc.es/u/sveinugu/h/handoff-demo](https://galaxy.semsec.bsc.es/u/sveinugu/h/handoff-demo)

The history (id `eaa9b06464bd346f`, Galaxy 26.0) contains 25 items and demonstrates the complete Crypt4GH encryption → analysis → output encryption → recrypt flow with publicly viewable datasets.

Provenance note:

- this public history is easiest to inspect interactively in Galaxy rather than via static fetch;
- the summary below was transcribed from interactive review and cross-checked against the live-smoke notes in `test-data/crypt4gh/HANDOFF.md`.

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
- **__SET_METADATA__** — metadata management during recrypt and dataset operations.

#### Architecture notes

- **User-side recryptor** → **compute-side recryptor** at `https://galaxy.semsec.bsc.es:8443`.
- The UI key icon triggers a user-side recrypt step that talks to the compute-side recryptor over this TLS connection.
- Galaxy `remote_eval` code talks only to the compute-side recryptor; it never contacts the user-side recryptor or handles user private keys.
- No private user keys or compute key materials were shared between the two service boundaries; only encrypted content traverses the two trusted connections.
- The compute key ID `cnk:38al0qyb` visible in dataset tags across the history confirms consistent compute-key binding.

#### Future work for the demo environment

- The two TLS connections (user → compute recryptor and Galaxy `remote_eval` → compute recryptor) should be hardened with authentication and authorization infrastructure (AAI).
- Proper key-management lifecycles such as rotation, revocation, and auditing remain to be implemented for production deployments.
- The current demo setup trusts both connections at the network level; production environments should add token-based or certificate-based authentication per connection.

#### Coverage notes

The demo history exercises the core encryption → analysis → output → recrypt loop for simple outputs and collection discovery paths. It does **not** yet manually cover:

- tools that produce extra-files outputs,
- dataset discovery pathways beyond the `split_file_to_collection` pattern,
- or workflow execution, since all steps were run individually rather than inside a Galaxy workflow.

### Appendix B. Review annex

This annex keeps the full external review record in one place so later readers can compare the final handover against the original review asks.

#### Pointer checklist resolved in this revision

1. **Config keys were stale** → Quick start now uses `enable_crypt4gh_transparent_input_matching` and `enable_crypt4gh_remote_execution_staging`, with a top-level current-vs-historical note.
2. **TTL config snippet looked unsupported** → YAML TTL keys were removed from the handover; the section now documents the verified code behavior instead.
3. **UI recrypt endpoint was underspecified** → sections 6, 7, Remaining risks, and Future work now call out the hard-coded `https://localhost:61357/recrypt_header` dependency.
4. **Compute-side route scope was too broad** → compute runtime routes are now separated from `/get_compute_key_info`, which is described as upstream/manual/live-flow support.
5. **Phase 2 file attribution was too broad** → the Phase 2 server-side file list now narrows to files with direct Crypt4GH evidence.
6. **Current vs historical note was missing** → added near the top and reinforced in config/manual-testing sections.
7. **Terminology drift** → this handover now consistently prefers `recryptor` / `recrypt` wording for current branch behavior and only mentions older terms as historical artifacts.
8. **Integration coverage was under-described** → the integration test section now calls out metadata reset and collection-discovery encryption-before-persist coverage explicitly.
9. **Demo-grade vs production-grade note was missing** → added as a compact operator-facing subsection in the quick start.
10. **Pulsar pass-count claim needed provenance** → softened; the handover now avoids restating an unverified exact count.
11. **Full reviews should appear in the annex** → both full review transcripts are preserved below verbatim.

#### Review 1 (verbatim)

```text
Overall: strong structure, but I would not treat it as a reliable operator handover yet. The biggest problem is drift between the "current-state" sections and the actual code/config surface.

## What works well

- The top-level split is good: **Overview → Quick start → Deep dive → Appendix**.
- The "Everything below is optional deep-dive material" marker is effective.
- The phase-based deep dive matches the git history well overall.
- Most non-Pulsar runtime-hardening claims align with code/tests in:
  - `lib/galaxy/tools/crypt4gh_remote_execution.py`
  - `lib/galaxy/tools/remote_tool_eval.py`
  - `test/unit/jobs/test_crypt4gh_remote_execution.py`
  - `test/integration/test_crypt4gh_remote_execution.py`

## Must-fix accuracy issues

1. **The config keys in the quick start are stale/wrong for current code**
   - The doc says:
     - `enable_crypt4gh_transparent_staging: true`
   - Current schema/runtime use:
     - `enable_crypt4gh_transparent_input_matching`
     - `enable_crypt4gh_remote_execution_staging`
   - Verified in:
     - `lib/galaxy/config/schemas/config_schema.yml:4387-4413`
     - `doc/source/admin/galaxy_options.rst:5942-5973`
     - `lib/galaxy/tools/crypt4gh_remote_execution.py:563-625`
   - Action: rewrite the "Minimum Galaxy configuration" section to use the actual current names, or explicitly say the branch/worktree still contains older local config examples.

2. **The TTL config section appears unsupported by the codebase**
   - The doc presents:
     - `crypt4gh_ttl_minimum`
     - `gx_import_reencrypt_key_ttl`
   - I could not find those keys anywhere else in the repo.
   - Current code uses an in-code default floor of 24h plus destination walltime logic:
     - `_DEFAULT_MINIMUM_TTL = timedelta(days=1)`
     - `_minimum_ttl_for_destination(...)`
   - Verified in `lib/galaxy/tools/crypt4gh_remote_execution.py`.
   - Action: either remove those YAML keys, or clearly label them as out-of-tree/demo-specific if that's what they are.

3. **The UI recrypt flow is underspecified and the current implementation is much rougher than the doc implies**
   - `DatasetActions.vue` still posts directly to:
     - `https://localhost:61357/recrypt_header`
   - That is hard-coded and not covered by the quick start.
   - Verified in:
     - `client/src/components/History/Content/Dataset/DatasetActions.vue`
   - Action: add a blunt note in Quick Start/Section 6:
     - the browser-side recrypt button currently depends on a browser-local/user-side service,
     - its endpoint is not Galaxy-configured in this branch,
     - compute-side B setup alone is not enough to make the button work.

4. **The compute-side route description is slightly misleading**
   - The doc lists `/get_compute_key_info`, `/recrypt_header_to_job_key`, `/recrypt_header_to_user_key`, then says the repo contains a mock compute-side service.
   - The in-tree mock integration service defines the two `/recrypt_header_to_*` routes, but not `/get_compute_key_info`.
   - `/get_compute_key_info` is only used for the live external-service path in integration support.
   - Action: split this into:
     - routes Galaxy runtime directly uses,
     - route used by the upstream/user-side preparation flow.

## Readability improvements

1. **Remove branch-internal jargon from the overview**
   - "Gap #1–#14 fail-closed hardening cycle" is too insider-heavy for the overview.
   - Action: keep that in deep dive only, or explain it in one short clause.

2. **Add a small "current state vs historical artifacts" note**
   - Right now the document mixes:
     - current runtime truth,
     - older design vocabulary,
     - demo-environment specifics.
   - Action: add one short boxed note near the top explaining that some older docs/local configs still use earlier naming.

3. **Clarify chronology scope**
   - `git log` from the branch base includes unrelated upstream commits before the first Crypt4GH change.
   - Action: add a one-liner that the chronology covers the **Crypt4GH-relevant** branch story, not every merged upstream commit.

## Completeness gaps

1. **Quick start is not actually enough to reproduce the UI flow**
   - It tells operators to confirm the user-side recrypt path, but not how to provide/run that service.
   - Action: add a dedicated subsection:
     - required service,
     - expected endpoint,
     - where it runs,
     - what failure looks like if absent.

2. **The manual-testing pointers may mislead readers**
   - `CRYPT4GH-HANDOVER.md` points readers to `test-data/crypt4gh/MANUAL_TESTING.md`, but that doc also uses older config naming.
   - Action: either update that doc or warn that it uses older terminology.

3. **Appendix A is hard to audit**
   - The public Galaxy URL is JS-gated from a static fetch, so the appendix claims are less independently checkable than the in-repo claims.
   - Action: add a small provenance note or link an exported history summary/screenshot.

4. **The Pulsar "14/14 tests passed" claim needs provenance**
   - I verified the separate Pulsar branch exists, but not that exact pass count.
   - Action: cite the command/CI result, or soften the wording.

## Consistency/style

- Mostly consistent, but terminology drifts:
  - `recryptor`
  - `re-encryptor`
  - `reencryption service`
  - `remote evaluation`
  - `remote execution`
  - `remote-eval`
- Action: standardize terms once and stick to them.

## Bottom line

Best next edits:
1. Fix the **current config** section.
2. Add an explicit **UI recrypt prerequisites/limitations** note.
3. Separate **current runtime truth** from **historical/demo-specific details**.

### Review scores
- Pragmatic-programmer: **6/10** — main issue is duplicated/stale config knowledge.
- Clean-code/readability: **8/10** — structure is strong; terminology drift and current-vs-historical mixing are the main readability costs.
```

#### Review 2 (verbatim)

```text
Overall: strong document, much more readable than a typical branch handover, and the overview/deep-dive split works well. But I found a few accuracy issues that are important enough to fix before relying on it as the authoritative handoff.

## What's working well

- **Readability is good overall.**
  - The "short version", "who should read which part", and explicit deep-dive divider are effective.
  - The overview is accessible to non-implementers.
  - The phase breakdown is easy to follow.

- **The core branch story is mostly accurate.**
  - Phase progression in the handover matches the code/test/history arc: datatype support → UI recrypt path → remote-eval redesign → fail-closed hardening.
  - The runtime hardening claims are generally supported by:
    - `lib/galaxy/tools/crypt4gh_remote_execution.py`
    - `lib/galaxy/tools/remote_tool_eval.py`
    - `test/unit/jobs/test_crypt4gh_remote_execution.py`
    - `test/integration/test_crypt4gh_remote_execution.py`

## High-priority fixes

### 1. Current config names are inconsistent with the actual runtime code
The handover's setup section uses:

- `enable_crypt4gh_transparent_staging` in the YAML example (`CRYPT4GH-HANDOVER.md:98-105`)

But the current runtime/config schema/docs use:

- `enable_crypt4gh_transparent_input_matching`
- `enable_crypt4gh_remote_execution_staging`

See:
- `lib/galaxy/tools/crypt4gh_remote_execution.py:521-624`
- `lib/galaxy/config/schemas/config_schema.yml:4387-4413`
- `doc/source/admin/galaxy_options.rst:5942-5973`

This is the biggest accuracy problem in the doc. The handover currently mixes **legacy naming** with **current behavior**.

**Suggestion:** in the setup guide, either:
- switch fully to the current names, or
- explicitly say: "earlier docs/config examples used `enable_crypt4gh_transparent_staging`; the current split runtime flags are ..."

### 2. TTL settings section appears inaccurate
The handover says:

- `crypt4gh_ttl_minimum: 86400`
- `gx_import_reencrypt_key_ttl: 86400`

at `CRYPT4GH-HANDOVER.md:148-162`.

I could not find those as active current config surfaces in code/docs. The actual behavior I verified is:
- a hardcoded default floor of 24h in runtime logic
- destination walltime can raise that floor
- equality fails closed

See:
- `lib/galaxy/tools/crypt4gh_remote_execution.py:723-729`
- `lib/galaxy/tools/crypt4gh_remote_execution.py:2442-2456`

So the behavioral claim is right, but the **operator-facing config snippet looks unsupported or at least undocumented in current code**.

**Suggestion:** rewrite this section to describe runtime policy, not config keys, unless you can cite the actual current configuration surface.

### 3. The UI recrypt description is incomplete / somewhat misleading operationally
The doc says the UI recrypt button is "one visible piece of that flow" (`CRYPT4GH-HANDOVER.md:142-146`), which is fair, but it omits a big operational constraint:

`client/src/components/History/Content/Dataset/DatasetActions.vue:109-127` posts directly to:

- `https://localhost:61357/recrypt_header`

That is much more specific than the handover implies. It is not described as a configurable Galaxy-side service endpoint.

**Suggestion:** add a note that the current UI action is still wired to a specific localhost HTTPS browser-side endpoint, so operators/reviewers do not assume this path is fully generalized/config-driven.

### 4. Compute-side recryptor B section overstates `get_compute_key_info` as a current branch runtime requirement
The handover's setup section lists all three routes as the "relevant route family":
- `/get_compute_key_info`
- `/recrypt_header_to_job_key`
- `/recrypt_header_to_user_key`

The code does use the latter two in runtime:
- `lib/galaxy/tools/crypt4gh_remote_execution.py`
- integration mock app in `test/integration/test_crypt4gh_remote_execution.py`

But `get_compute_key_info` is more clearly part of surrounding workflow/manual/live-service setup than the main Galaxy runtime path described in this branch.

**Suggestion:** distinguish:
- **runtime-required by Galaxy remote execution:** `/recrypt_header_to_job_key`, `/recrypt_header_to_user_key`
- **used by upstream/user-side/manual/live setup flows:** `/get_compute_key_info`

## Medium-priority accuracy/precision issues

### 5. Some file attribution in Phase 2 is too broad
This section:
- `CRYPT4GH-HANDOVER.md:480-488`

groups:
- `lib/galaxy/webapps/galaxy/controllers/dataset.py`
- `lib/galaxy/managers/datasets.py`
- `lib/galaxy/metadata/__init__.py`
- `lib/galaxy/metadata/set_metadata.py`
- `lib/galaxy/model/__init__.py`

But only some of these have clear Crypt4GH-specific evidence on inspection:
- `metadata/__init__.py`
- `metadata/set_metadata.py`
- `model/__init__.py`

I did **not** find direct Crypt4GH references in:
- `lib/galaxy/webapps/galaxy/controllers/dataset.py`
- `lib/galaxy/managers/datasets.py`

Those files may still be in the branch diff, but the current wording implies stronger Crypt4GH-specific ownership than the code supports.

**Suggestion:** either:
- narrow that list to the files with direct Crypt4GH behavior, or
- rephrase as "adjacent server-side surfaces touched during the branch" rather than "surfaces the UI and runtime depend on".

### 6. Test section slightly under-describes integration coverage
The integration test summary is good, but it could mention two especially important verified behaviors:
- metadata reset on metadata-source outputs
- collection discovery outputs encrypted before persistence

Confirmed by:
- `test/integration/test_crypt4gh_remote_execution.py`:
  - `test_metadata_source_outputs_reset_crypt4gh_header_metadata_for_crypt4gh_jobs`
  - `test_collection_discovery_split_outputs_are_encrypted_for_crypt4gh_jobs`

These are important because they reinforce later sections about stale metadata cleanup and discovered-output enforcement.

## Completeness suggestions

### 7. Add a brief "Known mismatch between older docs/config naming and current code" note
Because the branch spans many phases, the doc would benefit from a short note near the top saying some older artifact names remain in historical documents, while the handover should prefer current code terminology. Right now that transition is implicit and causes confusion.

### 8. Add a tiny "What is still demo-grade vs production-grade" subsection
You hint at this in multiple places, but one compact list would help:
- UI/browser-side recrypt endpoint assumptions
- live Pulsar runtime still under-proven
- demo TLS/authn/authz still not production AAI
- untracked plaintext outputs remain outside strongest guarantees

That would improve operator readability.

## Style / consistency

- Tone is mostly consistent and much improved over many multi-phase handovers.
- The repeated "Why this phase mattered" pattern works well.
- One mild issue: the document alternates between:
  - very plain-language operator-facing explanation
  - very reviewer-centric wording like "fail-closed posture", "designated mapping", "context-required enforcement"

That's okay in the deep dive, but in the setup section I'd keep wording simpler and push jargon downward.

## Bottom line

**Recommendation:** revise before treating it as the final handoff.

Main fixes I'd ask for:
1. Correct the **current config flag names**.
2. Fix or soften the **TTL config snippet**.
3. Add a note about the **hard-coded browser-side recrypt endpoint**.
4. Tighten a few **over-broad file attributions**.
5. Clarify which recryptor routes are **runtime-required** vs **upstream/manual-flow**.

## Review scores

- **Pragmatic-programmer:** 8/10
  Failing rows: single authoritative representation (config naming drift), "working tracer slice" communication is good but operator contract is slightly inconsistent.

- **Clean-code review of the document:** 8/10
  Strong structure and naming; main issues are precision drift and a few sections mixing historical and current terms.

Remediation:
1. Normalize current-vs-legacy naming in setup/config sections.
2. Make operator-facing constraints explicit where the code is still specialized.
3. Trim or relabel broad file lists to match actual Crypt4GH-specific ownership.
```

### Appendix C. Reference documents and suggested reading order

If someone new needs to understand this work quickly, this order should minimize context switching:

1. `CRYPT4GH-HANDOVER.md` (this file)
2. `crypt4gh-galaxy-support.md`
3. `crypt4gh-phase-2-reimplementation-design.md`
4. `crypt4gh-phase-2-implementation-plan.md`
5. `crypt4gh-fail-closed-evaluation.md`
6. `lib/galaxy/datatypes/binary.py`
7. `lib/galaxy/tools/crypt4gh_remote_execution.py`
8. `test/integration/test_crypt4gh_remote_execution.py`
9. Pulsar branch diff (`work/pulsar-tail-20260718`) if Pulsar parity matters for the next step

Supporting document map:

- `crypt4gh-galaxy-support.md`
  - the best record of the earliest Phase 1 and early Phase 2 goals.

- `crypt4gh-remote-exec-findings.md`
  - the findings document that explains why the branch moved away from the earlier runtime model.

- `crypt4gh-phase-2-reimplementation-design.md`
  - the best document for understanding the user-side recryptor A / compute-side recryptor B split and the trust-model redesign.

- `crypt4gh-phase-2-implementation-plan.md`
  - the approved implementation path for the remote-evaluation redesign.

- `crypt4gh-phase-2-output-enforcement-plan.md`
  - the narrower output-enforcement slice and the reasoning behind discovered-output finalization.

- `crypt4gh-phase-2-output-enforcement-spec.md`
  - the tighter requirements companion to the output-enforcement plan.

- `crypt4gh-fail-closed-evaluation.md`
  - the best current-state document for the later gap-by-gap fail-closed posture.

- `test-data/crypt4gh/MANUAL_TESTING.md`
  - operator-oriented manual checks covering the earlier phases and later runtime flow.

- `test-data/crypt4gh/HANDOFF.md`
  - prior live-smoke and delivery context.
