# Crypt4GH branch handover

## Overview

This handover covers the full Crypt4GH story on this branch: encrypted datatype support, browser-side recrypt workflows, the remote-execution redesign, and the later fail-closed runtime tightening.

The supported setup does **not** require sharing private keys with Galaxy at all: not user-side private keys, and not the temporary compute-side private keys created for the recrypt workflow.

If you want a live example before reading code, a public Galaxy history is available at:
[https://galaxy.semsec.bsc.es/u/sveinugu/h/handoff-demo](https://galaxy.semsec.bsc.es/u/sveinugu/h/handoff-demo)

That history demonstrates several rounds of analysis and recrypt, not just a single upload-and-run path. Appendix A walks through it in chronological order.

> **Current state vs historical artifacts**
>
> This handover describes the current code path on this branch. Older documents and local testing notes in the tree still use earlier naming, especially `enable_crypt4gh_transparent_staging` and older `re-encryptor` / `reencryption` wording. When this handover and older artifacts differ, prefer the current code surfaces cited here: `enable_crypt4gh_transparent_input_matching`, `enable_crypt4gh_remote_execution_staging`, and `crypt4gh_reencryption_service_url`.

### The short version

- Galaxy can recognize Crypt4GH-encrypted datasets, keep their underlying datatype information, and store the Crypt4GH header as metadata.
- Users can prepare encrypted datasets for compute use through a browser-visible recrypt step.
- Remote execution moved sensitive decrypt/finalize/cleanup behavior closer to the compute environment instead of keeping it in Galaxy-side staging logic.
- Output handling now covers declared outputs, discovered outputs, metadata-source outputs, and `extra_files` with stronger fail-closed checks before success.
- Pulsar follow-up work was split into its own branch so the main branch can focus on the non-Pulsar runtime path.
- The checked-in UI recrypt button still depends on a browser-local service endpoint and is not yet generalized by Galaxy config.

### Repositories and branch references

- **Galaxy support repo:** [elixir-europe/hdtr-semsec-galaxy-crypt4gh-support](https://github.com/elixir-europe/hdtr-semsec-galaxy-crypt4gh-support)
  - primary branch for this handover: `explore-crypt4gh-library-support-merged-with-is-recryptor-from-26.0`
  - related follow-up branches/worktrees: `work/pulsar-tail-20260718`, `work/fix-remote-tool-eval-python-fail-closed`
- **Recryptor repo:** [elixir-europe/crypt4gh-recryptor-service](https://github.com/elixir-europe/crypt4gh-recryptor-service)
  - recryptor route-work branch/worktree used during this effort: `work/phase2-recryptor-routes`

### What this branch added over time

Across the branch history, the work progressed through six steps:

1. **Dataset support**: Galaxy learned how to detect `.c4gh` files, preserve their underlying datatype, and store Crypt4GH header metadata.
2. **User workflow support**: the history UI gained a recrypt action so encrypted datasets could be prepared for compute use.
3. **Architecture and planning**: design and plan documents reworked the trust model and separated user-side and compute-side responsibilities.
4. **Remote execution**: job execution moved from earlier Galaxy-side staging ideas toward remote-evaluation handling near the compute node.
5. **Fixes and output enforcement**: later work closed gaps in runtime extension handling, discovered outputs, `extra_files`, metadata-source behavior, and postrun cleanup.
6. **Fail-closed tightening**: the final slice strengthened path containment, TTL handling, output evidence checks, purge safety, and discovered-output finalization.

### Current code state at a glance

The current branch state is best understood as four connected capability areas:

1. **Encrypted dataset intake**
   - upload/fetch can recognize Crypt4GH files,
   - datatype inference preserves inner type information,
   - and header metadata is stored for later recrypt and runtime decisions.

2. **User-visible recrypt workflow**
   - the Galaxy client exposes a recrypt action for eligible `.c4gh` datasets,
   - user-side recryptor A prepares compute-readable headers,
   - and compute-key metadata follows the dataset into later job runs.

3. **Remote compute execution path**
   - `remote_tool_eval.py` is the execution-side entrypoint,
   - readiness checks fail closed unless the required Crypt4GH execution mode is active,
   - and compute-side recryptor B plus `_crypt/` working directories handle the plaintext-compatible runtime path.

4. **Output finalization and verification**
   - declared outputs, discovered outputs, metadata-source outputs, and `extra_files` all flow through Crypt4GH-aware finalize/verify logic,
   - persisted payloads are checked before job success,
   - and cleanup, TTL, and containment errors are surfaced more defensively than in earlier slices.

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

The current branch supports a specific remote execution model.

In plain terms:

- Galaxy stores encrypted datasets and the metadata needed to reason about them.
- Recryptor A prepares user-owned encrypted inputs for compute use.
- Recryptor B rewrites headers for compute-side job/runtime use and output return.
- `remote_tool_eval.py` plus the Crypt4GH runtime helper own the sensitive plaintext-compatible execution path.
- Local tool evaluation is intentionally not part of the supported Crypt4GH flow.

No step in this setup requires Galaxy to receive or persist user private keys or the temporary compute-side private keys minted for compute use.

### 2. Recryptor A vs recryptor B

The later phases of the branch split the recrypt workflow into two roles:

- **Recryptor A (user-side)**
  - browser-adjacent or otherwise user-side,
  - handles user-side material,
  - obtains compute-key context,
  - default checked-in browser endpoint example: `https://localhost:61357/recrypt_header`

- **Recryptor B (compute-side)**
  - called by Galaxy during remote execution,
  - rewrites headers into job-local or user-returnable form,
  - default service URL example for docs/config: `https://recryptor-b:61358`

This split matters because Galaxy is not meant to hold user private keys or compute-side private keys.

### 3. Minimum Galaxy configuration

Minimum Galaxy-side settings for the supported remote Crypt4GH execution path:

```yaml
galaxy:
  enable_crypt4gh_transparent_input_matching: true
  enable_crypt4gh_remote_execution_staging: true
  crypt4gh_reencryption_service_url: https://recryptor-b:61358
  metadata_strategy: extended
  outputs_to_working_directory: true
  tool_evaluation_strategy: remote
```

Important notes:

- `enable_crypt4gh_remote_execution_staging` is the current runtime gate for the remote Crypt4GH path and requires `enable_crypt4gh_transparent_input_matching = true`.
- `crypt4gh_reencryption_service_url` is the current in-repo config key and refers to compute-side recryptor B.
- `metadata_strategy: extended` is required for the supported remote Crypt4GH execution path.
- `outputs_to_working_directory: true` is required for the supported remote Crypt4GH execution path.
- `tool_evaluation_strategy: remote` is required for the supported remote Crypt4GH execution path; local evaluation is intentionally rejected for this Crypt4GH flow.
- Older docs and local examples in this tree may still show `enable_crypt4gh_transparent_staging`; treat that as historical naming, not the current split flag surface.

### 4. Job destination expectations

If the deployment uses destination config, the effective execution mode still needs to resolve to:

- transparent input matching enabled,
- remote Crypt4GH staging enabled,
- remote tool evaluation,
- extended metadata,
- working-directory outputs.

In other words, destination-level settings and global defaults still need to combine into the same supported behavior for the Crypt4GH path.

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

The next two sections describe the same branch from different angles:

- the **architecture view** groups the work by runtime entity and end-to-end data flow;
- the **phase chronology** groups the same work by how it landed over time.

Some content intentionally overlaps between the two views.

### Architecture overview

The easiest way to understand the current branch is to look at the main entities first, then follow a typical Crypt4GH run across them.

#### Main entities and responsibilities

| Entity | Main files / surfaces | Role in the Crypt4GH path |
| --- | --- | --- |
| **Galaxy datatype and registry layer** | `lib/galaxy/datatypes/binary.py`, `lib/galaxy/datatypes/registry.py`, `lib/galaxy/datatypes/sniff.py`, `lib/galaxy/util/checkers.py`, `lib/galaxy/util/crypt4gh.py`, `lib/galaxy/util/compression_utils.py` | Recognizes Crypt4GH files, preserves inner datatype information, stores the header as metadata, prioritizes Crypt4GH detection before gzip during file opening, and generates `.c4gh` / nested dynamic datatypes. |
| **Galaxy metadata/reset layer** | `lib/galaxy/metadata/__init__.py`, `lib/galaxy/metadata/set_metadata.py`, `lib/galaxy/model/__init__.py` | Carries Crypt4GH metadata through dataset lifecycle events and resets stale compute-key metadata when outputs should stop looking like compute-recrypted inputs. |
| **Galaxy client / history UI** | `client/src/components/History/Content/Dataset/DatasetActions.vue` | Exposes the visible recrypt action and creates the user-facing “prepare for compute” workflow. |
| **Recryptor A (user-side)** | external/browser-adjacent service | Uses user-side key context to prepare headers for compute use. In the checked-in UI flow, this is still assumed to exist at `https://localhost:61357/recrypt_header`. |
| **Recryptor B (compute-side)** | external recryptor repo, compute-mode service | Rewrites headers for job-local compute execution and rewrites output headers back toward user-readable form. Galaxy points to it through `crypt4gh_reencryption_service_url`. |
| **Remote tool evaluation entrypoint** | `lib/galaxy/tools/remote_tool_eval.py` | Thin execution-side entrypoint that loads the tool context, resolves metadata/runtime state, and hands Crypt4GH-specific work to the dedicated helper. |
| **Crypt4GH runtime helper** | `lib/galaxy/tools/crypt4gh_remote_execution.py` | Main control point for readiness checks, TTL handling, recryptor B calls, plaintext staging, output finalization, evidence verification, and cleanup. |
| **Output collection / persistence path** | `lib/galaxy/job_execution/output_collect.py`, `lib/galaxy/model/store/discover.py`, `lib/galaxy/jobs/__init__.py` | Connects job runtime behavior to persisted datasets, including discovered outputs and `extra_files` payloads. |
| **Job runner boundary** | non-Pulsar main path plus Pulsar follow-up in `lib/galaxy/jobs/command_factory.py` | Keeps the main branch focused on the non-Pulsar `remote_tool_eval.py` path while the Pulsar command-shape parity work lives in a separate branch. |

#### Overall trace of a typical Crypt4GH run

This is the high-level path the code implements today.

##### Key-pair types used in the design

| Key type | When created | Where it lives | Longevity | Scope |
| --- | --- | --- | --- | --- |
| **User key pair** | Created outside Galaxy by the user or user-side key-management tooling | User-controlled systems / recryptor A-side context | Long-lived | Lets the user decrypt data and authorize recrypt into compute context |
| **Compute key pair** | Created by compute-side recryptor B for a user and time slice | Compute-side recryptor storage only | Temporary, bounded by the compute-key expiration window | Represents compute-readable access for a user/session slice without exposing the user private key |
| **Job key pair** | Created per job inside the runtime helper | In-memory during the running job | Per job, never persisted to disk | Gives one job a short-lived local decryption/encryption context for its own runtime path |

##### 1. Data ingestion and dataset typing

- A user uploads or fetches a `.c4gh` file.
- Galaxy detects Crypt4GH by header/magic-byte logic rather than by decrypting the payload.
- The datatype/registry layer preserves the inner datatype where possible (`fastqsanger.c4gh`, `fastqsanger.gz.c4gh`, and similar dynamic variants).
- `Crypt4GHDynamicCompressedArchive` stores the Crypt4GH header in metadata so later recrypt steps do not need to reopen or rewrite the dataset body just to work with the header.

##### 2. User-side recrypt for compute use

- The user clicks the history recrypt action in the Galaxy client.
- The browser-side flow sends the stored header to recryptor A.
- Recryptor A obtains compute-side key context (via the surrounding A/B workflow) and prepares a compute-readable header.
- Galaxy stores the returned metadata on a copied dataset, including the compute key id / expiration information needed by later runtime checks.
- At this point the relevant keys are:
  - the long-lived user key pair stays user-side,
  - the temporary compute key pair lives only with compute-side recryptor B,
  - and Galaxy sees only header material plus compute-key metadata such as key id / expiration.

##### 3. Job readiness and launch

- When a job includes any Crypt4GH input, `assert_crypt4gh_job_readiness(...)` in the runtime helper checks the supported execution contract.
- For the supported path, Galaxy must effectively resolve to:
  - transparent input matching enabled,
  - remote Crypt4GH execution staging enabled,
  - remote tool evaluation,
  - extended metadata,
  - working-directory outputs,
  - and a configured compute-side recryptor B URL.
- If that contract is not met, the job fails before unsafe or partial execution can begin.

##### 4. Compute-side runtime handling

- `remote_tool_eval.py` runs on the execution side and loads the minimal app/tool context.
- The Crypt4GH helper prepares `_crypt/inputs/.../plaintext` material under the job working directory rather than relying on the older Galaxy-side staging model.
- The helper generates a per-job key pair in memory only; the job public key is sent to recryptor B, while the job private key never leaves process memory and is not persisted to disk.
- Galaxy calls recryptor B to rewrite headers for job-local compute use through `POST /recrypt_header_to_job_key`.
- The actual tool then runs against plaintext-compatible paths inside the compute-local workspace.

##### 5. Output handling by output class

Different output classes need slightly different handling, but they all converge on the same “persisted payloads must be encrypted before success” rule.

| Output class | Main handling path | What the branch guarantees |
| --- | --- | --- |
| **Declared/simple outputs** | `crypt4gh_remote_execution.py` finalization helpers | Output payloads are finalized as `.c4gh`, checked before success, and cleaned up from plaintext staging paths. |
| **Discovered / collection outputs** | discovery/persistence route plus Crypt4GH finalization hooks | Collection discovery outputs are encrypted before persistence, not after Galaxy has already committed a plaintext result. |
| **`extra_files` payloads** | shared persisted-payload enforcement path | `extra_files` payload files are treated as sensitive outputs, finalized individually, and covered by manifest/evidence checks. |
| **Metadata-source / modify-input outputs** | metadata reset path via `Crypt4GHDynamicCompressedArchive.set_meta(...)` | Stale compute-key metadata and stale header provenance are cleared so outputs do not incorrectly look like reusable compute-recrypted inputs. |

##### Output-return path in more detail

- Galaxy encrypts plaintext output locally to the compute public key.
- It then extracts only the Crypt4GH header from that intermediate encrypted file and sends only that header to compute-side recryptor B via `POST /recrypt_header_to_user_key`.
- Recryptor B returns a user-readable replacement header.
- Galaxy rewrites the final output file as:
  - returned header from recryptor B,
  - plus the unchanged encrypted body from the compute-encrypted intermediate file.
- This means output return, like input preparation, stays header-only across the recryptor boundary; Galaxy never needs the user private key and does not send plaintext output bodies to recryptor B.

##### Output types and plaintext/encryption rules

This table follows `crypt4gh-phase-2-output-enforcement-spec.md`, especially the section **Encryption scope and plaintext allow-list**.

| Output type | Allowed plaintext / must be encrypted | How Crypt4GH code handles them (and where) | Comments |
| --- | --- | --- | --- |
| **Persisted primary output payloads (declared/simple outputs)** | Must be encrypted | Finalized in `lib/galaxy/tools/crypt4gh_remote_execution.py`; finish-time extension reapplication and verifier run in `lib/galaxy/jobs/__init__.py` | The simplest output class, but still depends on marker evidence before success. |
| **Persisted discovered output payloads** | Must be encrypted | Discovery/persistence hook plus Crypt4GH finalization in `lib/galaxy/model/store/discover.py`, `lib/galaxy/tools/crypt4gh_remote_execution.py`, and finish-time verification in `lib/galaxy/jobs/__init__.py` | Hardest output class conceptually because discovered datasets have multiple subtypes and path models; the branch routes them through persisted-dataset mapping rather than a narrower Crypt4GH-only selector. |
| **Persisted collection outputs** | Must be encrypted | Covered through the discovered/persisted output route and final verification | Collection discovery is a concrete high-risk discovered-output subtype because many datasets can be created at once. |
| **Persisted `extra_files` payloads** | Must be encrypted | Finalized in `lib/galaxy/tools/crypt4gh_remote_execution.py` with extra-files manifest evidence; verifier checks in `lib/galaxy/jobs/__init__.py` | Manifest completeness matters here; marker/manifest mismatches are fail-closed. |
| **Metadata-source / modify-input outputs** | Final persisted payload must be encrypted; stale input-side compute metadata must be cleared | Metadata reset path in `lib/galaxy/datatypes/binary.py` and metadata/discovery handling in `lib/galaxy/metadata/set_metadata.py` and `lib/galaxy/model/store/discover.py` | The tricky part is not just encryption, but avoiding stale compute-key metadata/header provenance from the source dataset. |
| **Metadata/control artifacts** | Allowed plaintext | Remain in the plaintext allow-list per the output-enforcement spec; runtime/orchestration paths use them without treating them as persisted payloads | These are framework/control files, not dataset payloads. |
| **Tool script / control artifacts** | Allowed plaintext | Remain outside dataset-payload enforcement per the output-enforcement spec | Same reasoning as metadata/control files: readable by the framework, not published as encrypted datasets. |
| **`stdout/stderr`** | Allowed plaintext for now | Explicit temporary allow-list in the output-enforcement spec; not treated as encrypted dataset payloads | Known weak spot: plaintext can escape here, which is why the docs/spec call this out as deferred secure-log work. |

##### 6. Pre-success verification and cleanup

- Before job success is finalized, the runtime helper verifies encryption evidence for persisted payloads.
- Marker files and manifests under `_c4gh_stage/outputs` record encrypted-extension/evidence state, and `JobWrapper.finish()` reapplies encrypted extensions plus runs the pre-success verifier before Galaxy declares success.
- TTL checks, containment checks, and output-evidence checks all fail closed.
- Cleanup removes `_crypt/inputs` and `_crypt/outputs` plaintext artifacts as defensively as possible.
- Diagnostics try to preserve the real failure class instead of turning everything into a generic cleanup error.

##### 7. What happens next for the user

After a job completes, the user usually has two choices:

- **Download and decrypt outside Galaxy** using their own keys and their own downstream tooling.
- **Recrypt again for another analysis round** through the UI recrypt action, producing a new compute-readable copy for another job.

That “analyze → recrypt → analyze again” loop is one of the main behaviors demonstrated in Appendix A.

#### Galaxy datatype and registry layer in more detail

This layer behaves like a compression-aware wrapper system, but with stricter metadata rules than ordinary gzip/bzip handling.

Key ideas:

- **Compression-like detection without server-side decryption**
  - Crypt4GH is handled similarly to a compressed wrapper in sniff/registry logic,
  - but Galaxy never treats it as something it can transparently decompress on the server.

- **Dynamic datatype generation**
  - `.c4gh` wrapper types are generated dynamically from base datatypes,
  - including nested cases such as `.gz.c4gh`.

- **Metadata is central**
  - `crypt4gh_header` stores the header used for later recrypt work,
  - `crypt4gh_dataset_header_sha256` and `crypt4gh_metadata_header_sha256` let the code detect when metadata header and dataset header differ,
  - compute key id / expiration metadata ties a dataset to a compute-side recrypt context when relevant.

- **Compatibility is gated**
  - `enable_crypt4gh_transparent_input_matching` allows the datatype layer to match compatible plaintext tool inputs,
  - but only when the runtime path can actually support the required Crypt4GH execution mode.

- **Metadata reset matters**
  - metadata-source outputs and related modify-input patterns can otherwise inherit stale compute-recrypt metadata,
  - so the branch adds a canonical reset path that falls back to the dataset’s actual header when compute-key metadata should be cleared.

#### Remote execution and output handling in more detail

The non-Pulsar runtime path is intentionally split between a thin entrypoint and a heavier helper.

- **`remote_tool_eval.py`**
  - remains the thin execution-side entrypoint,
  - loads tool/datatypes/object-store context,
  - and wraps the embedded cleanup/finalize behavior Galaxy needs on the compute side.

- **`crypt4gh_remote_execution.py`**
  - enforces the readiness contract,
  - handles recryptor B calls,
  - prepares `_crypt/inputs` and `_crypt/outputs` work areas,
  - finalizes output payloads,
  - verifies encryption evidence before success,
  - enforces TTL and containment rules,
  - and reports cleanup/purge failures more precisely than earlier iterations.

For the most detailed output-enforcement semantics, the best reference remains:

- `crypt4gh-phase-2-output-enforcement-spec.md`

That spec is especially useful if a reviewer wants the stricter behavior around discovered outputs, `extra_files`, and the persisted-payload verifier rather than just the branch-level summary in this handover.

### Phase chronology across the branch

This handover now covers the entire Crypt4GH-relevant branch history from its divergence from `dev` / `origin/dev`.

#### Phase 1 — Initial Crypt4GH dataset support

This phase taught Galaxy to treat Crypt4GH as a real encrypted dataset type instead of opaque binary content.

Main outcomes:

- Crypt4GH compression detection was added.
- Dynamic `.c4gh` datatype handling was added, including nested cases such as `.gz.c4gh`.
- Upload type inference improved so Galaxy could keep the inner datatype, such as `fastqsanger.c4gh`.
- Tests and fixtures were added for encrypted FASTQ handling.
- The branch moved away from a `crypt4ghfs` mounting idea toward direct `crypt4gh` library usage.

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
- and the operator/user loop became visible instead of purely architectural.

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

- it documented the shift from an earlier single-service assumption toward the later recryptor A/B model,
- and it gave the later implementation and hardening work a clearer contract.

#### Phase 4 — Remote evaluation implementation

This phase turned the design into working execution behavior.

Main outcomes:

- remote-execution contract and integration tests were defined first,
- Crypt4GH remote-eval settings were propagated into metadata handling,
- remote input staging was added for remote tool evaluation,
- reheadered input was streamed directly into decrypt,
- declared remote outputs were finalized,
- live recryptor smoke-environment routing was supported,
- and remote execution helpers were split and reorganized.

Why this phase mattered:

- it made the compute-side runtime path real,
- and it created the surfaces that later output-enforcement and fail-closed work would tighten.

#### Phase 5 — Incremental fixes and output-enforcement expansion

This phase closed many correctness and enforcement gaps around real runtime edges.

Main outcomes:

- `.c4gh` suffix handling and runtime datatype lookup were corrected,
- discovered outputs were kept encrypted through metadata collection,
- discovered output finalization was enforced in the metadata path,
- declared and discovered `extra_files` payloads were finalized fail-closed,
- plaintext allow-list checks were added,
- transparent input readiness checks were enforced,
- and metadata-source / cleanup edge cases were stabilized.

Why this phase mattered:

- it connected the broad design to awkward real-world output behavior,
- and it expanded the branch from “can run a Crypt4GH job” to “can defend a wider set of output classes.”

#### Phase 6 — Fail-closed runtime tightening

This final phase concentrated on containment, verifier behavior, cleanup, TTL, and other runtime guardrails.

Main outcomes:

- path containment and local-strategy fail-closed handling were strengthened,
- the collection discovery encryption bypass was fixed,
- metadata reset and allowed-root provenance were tightened,
- extension resolution and traversal containment were hardened,
- best-effort purge behavior became more defensive,
- TTL boundary behavior and marker evidence handling were tightened,
- debug output was gated behind explicit opt-in,
- and follow-up coverage broadened the regression net.

Why this phase mattered:

- it made the current runtime path safer under failure,
- and it produced the branch’s strongest current fail-closed posture for the non-Pulsar path.

### Branches and related work

#### Primary branch

- `explore-crypt4gh-library-support-merged-with-is-recryptor-from-26.0`

This branch contains the full non-Pulsar Crypt4GH story from datatype support through runtime tightening.

#### Pulsar follow-up branch

- `work/pulsar-tail-20260718`

This separate branch/worktree contains the Pulsar-specific command assembly fixes and parity tests that were intentionally extracted from the main line of work.

Summary of the Pulsar branch:

- files changed: `lib/galaxy/jobs/command_factory.py`, `test/unit/app/jobs/test_command_factory.py`, plus branch metadata context;
- targeted parity unit tests exist for this branch, but this handover does not independently restate a verified run result or pass count here;
- purpose: make Pulsar `remote_command_line` assembly match the non-Pulsar wrapper and failure-gating intent more closely.

The added parity tests are useful, but they are still mostly command-shape assertions, not runtime parity proof.

#### Other already-existing related branch

- `work/fix-remote-tool-eval-python-fail-closed`

### Key code surfaces by capability

If someone is reading the branch in code rather than in commit order, these are the highest-value entry points.

#### Datatype detection, metadata, and upload

- `lib/galaxy/util/checkers.py`
- `lib/galaxy/util/compression_utils.py`
- `lib/galaxy/util/crypt4gh.py`
- `lib/galaxy/datatypes/sniff.py`
- `lib/galaxy/datatypes/binary.py`
- `lib/galaxy/datatypes/registry.py`
- `lib/galaxy/datatypes/upload_util.py`

These files define how Galaxy recognizes Crypt4GH files, prioritizes Crypt4GH detection before gzip during file opening, registers dynamic wrapper datatypes, stores the header in metadata, and keeps the inner datatype available to tools and later runtime steps.

#### Browser/UI and dataset metadata flow

- `client/src/components/History/Content/Dataset/DatasetActions.vue`
- `lib/galaxy/metadata/__init__.py`
- `lib/galaxy/metadata/set_metadata.py`
- `lib/galaxy/model/__init__.py`

These files define the visible recrypt interaction and the metadata behavior that makes that interaction meaningful later in execution and output reuse.

#### Remote execution and runtime safety

- `lib/galaxy/tools/remote_tool_eval.py`
- `lib/galaxy/tools/crypt4gh_remote_execution.py`
- `lib/galaxy/jobs/__init__.py`
- `lib/galaxy/job_execution/output_collect.py`
- `lib/galaxy/model/store/discover.py`

These files define the current compute-side execution path, output finalization hooks, discovery/persistence interaction, cleanup, verifier behavior, and failure handling.

#### Operator-facing configuration surface

- `lib/galaxy/config/sample/galaxy.yml.sample`
- `lib/galaxy/config/schemas/config_schema.yml`
- `doc/source/admin/galaxy_options.rst`

These files define the current config names and their intended meaning.

### Key test coverage

#### Datatype, upload, and metadata coverage

- `test/unit/data/datatypes/test_sniff.py`
- `test/unit/data/datatypes/test_datatypes_registry.py`
- `test/unit/data/datatypes/test_crypt4gh.py`
- `lib/galaxy_test/api/test_tools_upload.py`

These prove that `.c4gh` datasets are recognized, typed, and populated with the expected metadata.

#### Runtime, discovery, and fail-closed coverage

- `test/unit/jobs/test_crypt4gh_remote_execution.py`
- `test/unit/jobs/test_remote_tool_eval.py`
- `test/unit/data/model/test_model_discovery_crypt4gh.py`
- `test/integration/test_crypt4gh_remote_execution.py`

Important integrated behaviors called out explicitly:

- metadata-source outputs reset Crypt4GH header metadata for Crypt4GH jobs,
- collection discovery outputs are encrypted before persistence,
- TTL boundary behavior is fail-closed,
- cleanup and verifier behavior are exercised across the end-to-end path.

### Remaining risks

1. **Pulsar parity is still under-verified at runtime**
   - unit parity tests exist, but real Pulsar runtime parity is still not proven.

2. **Untracked plaintext outputs are still outside the strongest guarantees**
   - the branch covers tracked declared/discovered outputs and `extra_files`, not arbitrary tool writes to unrelated paths.

3. **Destination topology coverage is incomplete**
   - current tests cover important cases, not every deployment pattern.

4. **Toolshed and fixture variation risk remains**
   - in-tree fixture confidence is better than before, but not universal.

5. **Large security unit files may become brittle over time**
   - especially if future reviewers struggle to separate behavior assertions from implementation scaffolding.

6. **TTL policy may be conservative for some operators**
   - the 24-hour floor is safe, but may not fit every deployment.

### Future work

1. Add real Pulsar integration/runtime tests.
2. Expand destination-matrix integration coverage.
3. Add more toolshed/discovery fixture coverage.
4. Consider whether containment and purge diagnostics should become shared Galaxy utilities.
5. Revisit test consolidation once PR boundaries stabilize.
6. Consider whether TTL floor configuration should be more operator-friendly.
7. Generalize the browser-side UI recrypt endpoint so it is configurable instead of hard-coded to `https://localhost:61357/recrypt_header`.
8. Add client-side visualization of encrypted datasets through interaction with recryptor A.
9. If desired, split out the object-wrapper fix into a general PR.

### Final assessment

For the non-Pulsar scope, this branch now reads most clearly as a full Crypt4GH support branch. The main operator-facing story is:

- encrypted datasets are typed and tracked correctly,
- the user can prepare them for compute use,
- remote execution handles the sensitive runtime path near the compute side,
- and persisted outputs are verified more strictly before Galaxy declares success.

The biggest remaining operator caveats are the browser-local UI dependency, incomplete Pulsar runtime proof, and the still-limited guarantees around untracked plaintext.

## Appendix

### Appendix A. Published demo history

Public history URL:
[https://galaxy.semsec.bsc.es/u/sveinugu/h/handoff-demo](https://galaxy.semsec.bsc.es/u/sveinugu/h/handoff-demo)

The history (id `eaa9b06464bd346f`, Galaxy 26.0) contains 25 items and demonstrates several rounds of analysis and recrypt on Crypt4GH datasets.

Provenance note:

- this public history is easiest to inspect interactively in Galaxy rather than via static fetch;
- the summary below was transcribed from interactive review and cross-checked against the live-smoke notes in `test-data/crypt4gh/HANDOFF.md`.

#### Demo run order

The most useful way to read the history is as a chronological list of tool runs and recrypt steps:

1. **Upload** one Crypt4GH-encrypted FASTQsanger dataset.
2. **Recrypt for compute** to create a compute-readable copy tagged for the active compute key.
3. Run **Show beginning1** on that recrypted input.
4. **Recrypt** the resulting encrypted output so it can be used again for compute-side analysis.
5. Run **split_file_to_collection** on a recrypted input to produce encrypted collection outputs.
6. **Recrypt** the split outputs for another analysis round.
7. Run **FastQC** on a recrypted input to produce encrypted `html.c4gh` and `txt.c4gh` outputs.
8. **Recrypt** the FastQC outputs for reuse.
9. Run **Show beginning1** again on the recrypted FastQC text output.
10. **Recrypt** the final text output.

In other words, the published history shows repeated cycles of:

- encrypted input,
- compute preparation,
- tool run,
- encrypted output,
- recrypt for the next round.

#### Recrypt pattern visible in the history

Throughout the history, datasets are tagged with `Recrypted_for_compute` and `cnk:38al0qyb` (the compute key id). The visible pattern is:

1. A job produces an encrypted output with a `.c4gh` extension.
2. The user clicks the UI key icon, **Recrypt Crypt4GH-encrypted dataset**, to obtain a compute-recrypted copy.
3. The recrypted copy keeps the same dataset content but uses headers recrypted to the compute-side key context.
4. Both the original and the recrypted copy are preserved in the history; the recrypted copy is the one used for further compute-side operations.

#### Tools used in the published history

- **Show beginning1** (`Show beginning1`) — Galaxy built-in text selection/head tool.
- **FastQC** (`toolshed.g2.bx.psu.edu/repos/devteam/fastqc/fastqc/0.74+galaxy1`) — quality control.
- **Split file to collection** (`toolshed.g2.bx.psu.edu/repos/bgruening/split_file_to_collection/split_file_to_collection/0.5.2`) — collection discovery.
- **__DATA_FETCH__** — file upload.
- **__SET_METADATA__** — metadata management during recrypt and dataset operations.

#### Architecture notes for the published demo

- **User-side recryptor A** → **compute-side recryptor B** at `https://galaxy.semsec.bsc.es:8443` in the published demo environment.
- The UI key icon triggers a user-side recrypt step that talks to the compute-side recryptor over that TLS connection.
- Galaxy `remote_eval` code talks only to the compute-side recryptor; it never contacts the user-side recryptor or handles user private keys.
- Across those two service boundaries, Crypt4GH headers and compute-key metadata/IDs can traverse the recryptor connections; encrypted payload bodies, plaintext payload bodies, and user private keys do not.
- The compute key id `cnk:38al0qyb` visible in dataset tags across the history confirms consistent compute-key binding.

#### Future work for the demo environment

- The two TLS connections (user → compute recryptor and Galaxy `remote_eval` → compute recryptor) should be hardened with authentication and authorization infrastructure (AAI).
- Proper key-management lifecycles such as rotation, revocation, and auditing remain to be implemented for production deployments.
- The current demo setup trusts both connections at the network level; production environments should add token-based or certificate-based authentication per connection.

#### Coverage notes

The demo history exercises the core encryption → analysis → output → recrypt loop for simple outputs and collection discovery paths. It does **not** yet manually cover:

- tools that produce `extra_files` outputs,
- dataset discovery pathways beyond the `split_file_to_collection` pattern,
- client-side visualization of encrypted datasets,
- or workflow execution, since all steps were run individually rather than inside a Galaxy workflow.

### Appendix B. Reference documents and suggested reading order

If someone new needs to understand this work quickly, this order should minimize context switching:

1. `CRYPT4GH-HANDOVER.md` (this file)
2. `crypt4gh-galaxy-support.md`
3. `crypt4gh-phase-2-reimplementation-design.md`
4. `crypt4gh-phase-2-implementation-plan.md`
5. `crypt4gh-phase-2-output-enforcement-spec.md`
6. `crypt4gh-fail-closed-evaluation.md`
7. `lib/galaxy/datatypes/binary.py`
8. `lib/galaxy/tools/crypt4gh_remote_execution.py`
9. `test/integration/test_crypt4gh_remote_execution.py`

Supporting document map:

- `crypt4gh-galaxy-support.md`
  - the best record of the earliest Phase 1 and early Phase 2 goals.

- `crypt4gh-remote-exec-findings.md`
  - the findings document that explains why the branch moved away from the earlier runtime model.

- `crypt4gh-phase-2-reimplementation-design.md`
  - the best document for understanding the user-side recryptor A / compute-side recryptor B split and the trust-model redesign.

- `crypt4gh-phase-2-implementation-plan.md`
  - the approved implementation path for the remote-evaluation redesign.

- `crypt4gh-phase-2-output-enforcement-spec.md`
  - the most precise reference for discovered outputs, `extra_files`, and the persisted-payload fail-closed verifier.

- `crypt4gh-fail-closed-evaluation.md`
  - the best current-state document for the later runtime-tightening decisions.

- `test-data/crypt4gh/MANUAL_TESTING.md`
  - operator-oriented manual checks covering the earlier phases and later runtime flow.

- `test-data/crypt4gh/HANDOFF.md`
  - prior live-smoke commands, outcomes, and cross-repo context.
