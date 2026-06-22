# Crypt4GH Phase 2+ Reimplementation Design

## Status

This design supersedes the **Phase 2** and **Phase 3** runtime architecture described in `crypt4gh-galaxy-support.md`.

The earlier design assumed a single external re-encryptor service and let Galaxy-side job preparation author decrypt/encrypt behavior. `crypt4gh-remote-exec-findings.md` invalidates that approach for the desired trust model: sensitive runtime crypto behavior must move to the execution side, and the recryptor concept must be split into a user-side service (**A**) and a compute-side service (**B**).

## Goal

Reimplement Crypt4GH execution support beyond Phase 1 so that:

- Galaxy supports both non-Pulsar and Pulsar deployments
- non-Pulsar is the first implementation target
- user private keys stay on the user side
- compute-side temporary private keys stay inside the compute-side recryptor
- plaintext exists only in a compute-local, job-scoped workspace
- final exported datasets can return to encrypted-at-rest storage

## Scope

In scope:

- replacement runtime design for encrypted input execution
- replacement output re-encryption design
- shared metadata/config/route contract needed across Galaxy, A, and B
- non-Pulsar-first execution strategy
- follow-on Pulsar-compatible architecture
- exact REST route names and payload schemas for required recryptor changes, based on the current service implementation
- acceptance-test definition for the first tracer bullet

Out of scope for this design:

- authentication details between Galaxy, A, and B
- protection against malicious tool commands after plaintext is made available inside the trusted compute job
- Phase 1 datatype sniffing and metadata extraction already covered by the previous work

## Trust Model

This design assumes:

- the Galaxy orchestrator is less trusted than the compute environment
- the execution-side job workspace is trusted to hold short-lived plaintext during a job
- A and B are trusted to hold their own private material and never disclose it to Galaxy
- a failed crypto handoff is preferable to accidental plaintext exposure

Important nuance for non-Pulsar-first support: some deployments may run orchestration and execution on closely related infrastructure. Even there, this redesign still improves the model by moving crypto decisions and plaintext handling to execution-time hooks and by keeping B-managed private keys out of Galaxy.

## Architecture Overview

### Components and responsibilities

#### Galaxy orchestrator

Galaxy is responsible for:

- storing encrypted datasets
- storing standard dataset metadata needed for later recryption/execution
- deciding whether Crypt4GH execution handling is needed from existing config plus dataset metadata
- passing existing job context into execution-side logic behind `remote_tool_eval.py`
- importing only encrypted outputs back into normal dataset storage

Galaxy is not responsible for:

- holding user private keys
- holding compute-side recryptor private keys
- authoring shell-wrapped decrypt/encrypt logic during `prepare_job()`

#### Recryptor A: user-side local REST service

A is responsible for:

- local interaction with the Galaxy browser client
- using user-side private material
- obtaining compute-side temporary public-key information from B
- recrypting input headers from the user key context into the compute-side temporary key context

#### Recryptor B: compute-side REST service

B is responsible for:

- minting and holding time-bounded compute-side private keys
- associating those keys with key ids and expiration metadata
- storing the user public key association for each issued compute-side key id
- transforming headers from the B-managed temporary key context into a job-local execution key context
- recrypting output headers from the compute-side temporary key context back to the stored user public key
- never disclosing B-managed private keys to Galaxy or the job runtime

#### Execution-side Crypt4GH helper

The execution-side helper is responsible for:

- consuming ordinary Galaxy job context, config, and dataset metadata
- creating a per-job crypto workspace
- minting or loading the short-lived job-local execution keypair
- coordinating with B for the second input-header recryption step
- materializing plaintext input paths or decryptable local views for the tool
- encrypting selected Galaxy-imported outputs into the B-managed temporary compute-key context before they are exported back into Galaxy-managed storage
- cleaning up plaintext intermediates and short-lived key material

### Chosen execution strategy

Use one **shared metadata-driven execution model** behind `remote_tool_eval.py`:

1. **Non-Pulsar first:** implement and verify the execution-side logic here first
2. **Pulsar later:** reuse the same mechanism and avoid Pulsar setup changes beyond ensuring `remote_tool_eval.py` runs on the compute node

This keeps one runtime model across both deployment styles and avoids introducing a separate job-plan artifact.

## Core runtime design

### 1. Upstream prerequisite: input preparation already happens outside this repo through A and B

This design assumes the following user-side flow already exists outside this repo and remains the upstream prerequisite:

1. user imports a Crypt4GH file encrypted for their own keypair
2. user runs A locally
3. Galaxy client invokes A
4. A asks B for a time-bounded compute-side public key + key id + expiry
5. A recrypts the dataset header into that compute-side temporary key context
6. Galaxy stores:
   - recrypted header
   - key id
   - expiration timestamp

This is the handoff state for execution.

### 2. Second recryption at job start

At job execution time, Galaxy must not use the B-managed temporary private key directly.

Instead:

1. the execution-side helper creates a **job-local execution keypair** for the job
2. the helper sends B:
   - the stored recrypted header
   - the stored key id
   - the job public key
3. B verifies the key id is valid and not expired
4. B recrypts the header from the B-managed temporary key context into the job-local execution key context
5. B returns the recrypted header and related freshness metadata

This is the central replacement decision for the old Phase 2 design. It keeps B-managed private keys inside B while still allowing decryption for the actual job runtime.

### 3. Compute-local plaintext workspace

The execution-side helper creates a per-job workspace with a layout equivalent to:

```text
<job_working_directory>/
  _crypt/
    inputs/
      ds_<id>/plaintext
    outputs/
    scratch/
```

Rules:

- plaintext must stay under `_crypt/`
- plaintext must not be placed under normal exported output paths
- path rewriting must be explicit and reversible
- cleanup must run in a reliable post-run step even after tool failure

### 4. Input representation for the tool

For the first implementation, the helper should **materialize plaintext temp files**, not depend on a FUSE mount as the primary mechanism.

Rationale:

- this follows the findings recommendation for a first execution-side redesign
- it reduces runtime coupling for the first tracer bullet
- it works cleanly with path rewriting in `remote_tool_eval.py`

Mount-based decrypted views may still be revisited later if they prove operationally simpler in specific deployments.

### 5. Output handling

Tool outputs are treated as plaintext intermediates within the job-local crypto workspace.

Selection rule for this slice:

- encrypted return is required for the whole job if the job consumes at least one input dataset marked with Crypt4GH execution metadata
- once that job-level trigger is true, selected outputs are every Galaxy-managed dataset artifact that the job would otherwise import into dataset storage
- this includes declared output datasets and discovered datasets that Galaxy will import as datasets
- this excludes stdout/stderr, metadata sidecars, helper scratch files, and any other runtime artifact that is not imported into Galaxy dataset storage
- the first tracer bullet uses a single job-level decision for imported datasets: if any input triggered Crypt4GH execution handling, all Galaxy-imported dataset artifacts from that job are selected; mixed encrypted/plain final dataset imports are out of scope for this slice

Before Galaxy re-imports or publishes them:

1. the execution-side helper encrypts each selected plaintext output locally into a temporary Crypt4GH file under `_crypt/outputs/` using the compute public key associated with the still-valid compute key id
2. the helper base64-encodes only that temporary file's Crypt4GH header bytes and sends the header string plus the compute key id to B
3. B recrypts that header from the compute-side temporary key context to the stored user public key and returns only the replacement header string plus freshness metadata
4. the helper reconstructs the final encrypted dataset locally by writing `returned_user_header + encrypted_body_from_step_1` to the final staged path
5. Galaxy imports only that complete encrypted-at-rest file; B does not assemble or return whole-file artifacts in this slice

The design goal is that no final exported dataset path in Galaxy points to plaintext content when encrypted return is required.

### 6. Return-to-user path

The return path must stay consistent with the two-recryptor model:

- Galaxy should store an encrypted output in a form recoverable through recryption, not in plaintext
- B, not A, should complete the output-header recryption back to the user's public key

The user-side service A is still part of the input-side browser-local flow, but it is not part of the output return path in this redesign.

## Shared metadata, config, and execution decisions

Galaxy should drive runtime behavior from existing config plus standard dataset metadata, not from a new standalone crypto-plan object.

### Dataset metadata contract

For Crypt4GH job inputs, Galaxy should persist at least:

- stored recrypted header
- compute-side key id
- compute-side key expiration timestamp
- indication that the dataset requires Crypt4GH execution handling

For encrypted outputs returned to Galaxy, Galaxy should persist at least:

- the final encrypted header after B has recrypted it to the user's public key
- indication that the dataset is encrypted-at-rest and must not be treated as plaintext

If additional metadata becomes necessary beyond the existing Crypt4GH metadata fields, it should be added as ordinary Galaxy dataset metadata only after confirming that it is generally acceptable in Galaxy, not as a one-off ad hoc job blob.

### Execution decision rules

- `enable_crypt4gh_transparent_staging` remains the top-level feature gate
- `tool_evaluation_strategy = remote` is required so `remote_tool_eval.py` runs on the execution side
- existing Crypt4GH dataset metadata fields determine which datasets need recryption/decryption behavior at runtime
- if any job input is marked for Crypt4GH runtime handling, the job is treated as requiring encrypted return for all Galaxy-imported dataset artifacts in this slice
- `crypt4gh_reencryption_service_url` remains the config key for this slice for compatibility, but its schema/help text must be updated to say it points at compute-side recryptor B
- `crypt4gh_compute_key_path` is part of the old design and should be removed from the final implementation because Galaxy must no longer hold a compute-side private key

The implementation should prefer reusing these existing controls over inventing a second planning/configuration layer.

### Compute-key expiry policy

- this slice does not introduce a renewal mechanism for B-issued compute keys
- minimum TTL assumption at job launch: the remaining compute-key lifetime must be at least the job destination walltime plus a one-hour buffer when walltime is known; otherwise operators must provision B with a minimum 24-hour TTL for eligible jobs
- if the stored key is already expired or below the minimum TTL threshold when the helper contacts B, the job fails before tool launch
- if a key still expires mid-run despite that guard, output finalization fails closed: B rejects the header rewrite, Galaxy imports no plaintext artifact, and the job is marked failed

## Execution deployment behavior

### Non-Pulsar first

The first implementation should use `tool_evaluation_strategy = remote` and execution-side logic in or behind `lib/galaxy/tools/remote_tool_eval.py`.

Responsibilities of the non-Pulsar adapter:

- load the relevant dataset metadata and existing config settings
- allocate the per-job crypto workspace
- obtain the second-stage recrypted headers from B
- materialize plaintext input files for tool-visible paths
- rewrite the tool command to those plaintext paths
- encrypt selected Galaxy-imported outputs to the B-managed temporary compute public key
- ask B to rewrite those output headers to the stored user public key
- clean up plaintext and short-lived keys

This is the best first shared Galaxy-side execution hook identified by the findings and avoids the old orchestrator-side `_apply_crypt4gh_staging()` model.

### Pulsar follow-on

Pulsar support should use the same metadata contract and the same `remote_tool_eval.py`-driven execution behavior.

- The preferred outcome is that no Pulsar service setup changes are required.
- The only required behavior should be that `remote_tool_eval.py` runs on the compute node.
- If compatibility fixes are required because the current Galaxy/Pulsar remote-command path is rough, those fixes should stay Galaxy-side and should not introduce a separate Pulsar-specific crypto architecture.

## REST routes and payload schemas

The following route names and payloads are in scope for this design.

Encoding rules for all schemas in this section:

- every `crypt4gh_header` field is a base64-encoded ASCII string containing the raw Crypt4GH header bytes
- every `*_public_key` field is a JSON string containing the literal PEM-like UTF-8 text content of the corresponding Crypt4GH public-key file
- no whole Crypt4GH body bytes are sent to B in this slice; only headers cross the REST boundary

### Verified current routes in the recryptor service

Verified against the current `crypt4gh-recryptor-service` implementation:

- **User mode**
  - `GET /info`
  - `POST /recrypt_header`
    - request JSON:
      ```json
      {
        "crypt4gh_header": "base64-encoded Crypt4GH header bytes"
      }
      ```
    - response JSON:
      ```json
       {
         "crypt4gh_header": "base64-encoded Crypt4GH header bytes",
         "crypt4gh_compute_keypair_id": "opaque key-id string",
         "crypt4gh_compute_keypair_expiration_date": "ISO 8601 datetime string"
       }
      ```
- **Compute mode**
  - `GET /info`
  - `POST /get_compute_key_info`
    - request JSON:
      ```json
       {
         "crypt4gh_user_public_key": "literal Crypt4GH user public-key file contents as a PEM-like UTF-8 string"
       }
      ```
    - response JSON:
      ```json
       {
         "crypt4gh_compute_public_key": "literal Crypt4GH compute public-key file contents as a PEM-like UTF-8 string",
         "crypt4gh_compute_keypair_id": "opaque key-id string",
         "crypt4gh_compute_keypair_expiration_date": "ISO 8601 datetime string"
       }
      ```

### Required compute-side route changes

1. `POST /get_compute_key_info` must additionally persist the submitted `crypt4gh_user_public_key` together with the issued `crypt4gh_compute_keypair_id`, so that B can later recrypt output headers back to the user key without involving A.

2. Add `POST /recrypt_header_to_job_key`

    - request JSON:
      ```json
       {
         "crypt4gh_header": "base64-encoded Crypt4GH header bytes",
         "crypt4gh_compute_keypair_id": "opaque key-id string",
         "crypt4gh_job_public_key": "literal job Crypt4GH public-key file contents as a PEM-like UTF-8 string"
       }
      ```

    - response JSON:
      ```json
       {
         "crypt4gh_header": "base64-encoded Crypt4GH header bytes",
         "crypt4gh_compute_public_key": "literal Crypt4GH compute public-key file contents as a PEM-like UTF-8 string",
         "crypt4gh_compute_keypair_id": "opaque key-id string",
         "crypt4gh_compute_keypair_expiration_date": "ISO 8601 datetime string"
       }
     ```

   - behavior:
     - reject unknown or expired `crypt4gh_compute_keypair_id`
     - recrypt the input header from the B-managed temporary key context to the supplied job public key
     - return the compute public key as part of the same response so the execution helper can later encrypt outputs back into the same compute-key context without adding another Galaxy metadata field or a second lookup route

3. Add `POST /recrypt_header_to_user_key`

    - request JSON:
      ```json
       {
         "crypt4gh_header": "base64-encoded Crypt4GH header bytes",
         "crypt4gh_compute_keypair_id": "opaque key-id string"
       }
      ```

    - response JSON:
      ```json
       {
         "crypt4gh_header": "base64-encoded Crypt4GH header bytes",
         "crypt4gh_compute_keypair_id": "opaque key-id string",
         "crypt4gh_compute_keypair_expiration_date": "ISO 8601 datetime string"
       }
     ```

   - behavior:
     - reject unknown or expired `crypt4gh_compute_keypair_id`
     - use the stored user public key associated with that key id
     - recrypt the output header from the compute-side temporary key context to the user's public key

No new user-side routes are required for this slice.

## Required code removal and cleanup

The current implementation contains orchestrator-side code that should not survive this redesign.

- remove `lib/galaxy/jobs/crypt4gh_staging.py`
- remove `_apply_crypt4gh_staging()` from `BaseJobRunner.prepare_job()` in `lib/galaxy/jobs/runners/__init__.py` once the execution-side replacement is in place
- remove shell-wrapper logic and tests that assume `prepare_job()` authors decrypt/encrypt behavior on the orchestrator, including `test/unit/jobs/test_crypt4gh_staging.py`
- remove Galaxy's dependency on `crypt4gh_compute_key_path` and `crypt4gh_compute_key_passphrase_env` from `lib/galaxy/config/schemas/config_schema.yml`, `lib/galaxy/config/sample/galaxy.yml.sample`, and `doc/source/admin/galaxy_options.rst`
- update the schema/help text for `crypt4gh_reencryption_service_url` in those same config-doc surfaces so it explicitly describes compute-side recryptor B
- remove or rewrite `test-data/crypt4gh/MANUAL_TESTING.md`, because it documents the old `prepare_job()` / `crypt4ghfs` / FUSE-first path

## Failure model

The design should fail closed.

Required failure behavior:

- expired or unknown compute-side key id -> fail before tool launch
- compute-side key below the minimum TTL threshold at job start -> fail before tool launch
- B unavailable or second recryption fails -> fail before plaintext materialization
- execution-side helper unavailable -> do not silently fall back to ciphertext-as-plaintext path rewriting
- output re-encryption failure -> fail the job and do not export plaintext as the final dataset artifact
- cleanup failure -> preserve failure diagnostics and treat the job as requiring operator attention

Preferred failure outcome: the tool never receives unauthorized plaintext, even if the user instead sees a failed job.

## Acceptance tests

The first tracer bullet should be integration-first and non-Pulsar-first.

### Tracer bullet acceptance test 1: non-Pulsar encrypted input execution

Given:

- a `.c4gh` dataset with stored recrypted header + key id + expiry metadata
- a reachable mock or test B service
- non-Pulsar execution using `remote_tool_eval.py`

When a job starts, then:

- the execution-side helper obtains a second-stage header for a job-local key
- the tool receives a compute-local plaintext input path
- plaintext exists only inside the job-local crypto workspace

### Tracer bullet acceptance test 2: encrypted output return

Given the same job,

When the tool produces output, then:

- Galaxy does not import the plaintext file as the final dataset artifact
- if any input triggered Crypt4GH execution handling, all Galaxy-imported dataset outputs from that job are selected for encryption in this slice
- the execution-side helper encrypts each selected output to the B-managed temporary compute public key
- B returns only a user-key-recrypted header, not a whole output file
- the helper reassembles the final encrypted file locally and Galaxy stores only that final encrypted output

### Acceptance test 3: fail-closed expired or insufficient-TTL key

Given an expired compute-side key id or one below the minimum launch TTL,

When the job starts, then:

- the job fails before tool launch
- no plaintext input path is materialized for the tool

### Acceptance test 4: fail-closed mid-run expiry during output finalization

Given a key that was valid at launch but expires before output finalization,

When the helper asks B to rewrite the output header to the user key, then:

- B rejects the request
- Galaxy imports no plaintext output artifact
- the job is marked failed

### Acceptance test 5: contract reuse for Pulsar

Given the same dataset metadata and existing config,

When a Pulsar adapter is later added, then:

- it can reuse the same `remote_tool_eval.py`-based flow without changing stored dataset semantics

### Acceptance test 6: old prepare_job wrapper removed

Given the execution-side redesign is enabled,

When job preparation runs, then:

- `BaseJobRunner.prepare_job()` no longer wraps commands with `_apply_crypt4gh_staging()`
- Galaxy no longer requires `crypt4gh_compute_key_path` or `crypt4gh_compute_key_passphrase_env`

## Key decisions

| Decision | Rationale |
| --- | --- |
| Replace single re-encryptor assumption with A + B model | Matches the actual system concept and keeps private material on the correct side |
| Replace orchestrator-side runtime wrapping with execution-side handling | Aligns with the trust findings and reduces orchestrator control over plaintext handling |
| Use second-stage recryption into a job-local execution keypair | Lets B keep its temporary private keys while enabling per-job decrypt/encrypt |
| Drive execution from existing config plus standard dataset metadata | Reuses Galaxy's existing control surfaces and avoids inventing a second planning entity |
| Materialize plaintext temp files first | Simplest first implementation behind `remote_tool_eval.py` |
| Use a job-level encrypted-return trigger from Crypt4GH-marked inputs | Removes output-selection ambiguity for the tracer bullet and keeps mixed-output policy out of the first slice |
| Keep output return in B, not A | Lets B use the stored user public key bound to the compute key id and keeps A out of the return path |
| Reassemble final encrypted output files locally in the execution helper | Keeps encrypted bodies on the compute side and limits B traffic to header-only operations |
| No compute-key renewal in the first slice | Keeps the tracer bullet reversible and fail-closed; expiry is handled by minimum TTL checks plus job failure |
| Support both deployment styles with the same `remote_tool_eval.py` model, but implement non-Pulsar first | Matches requested delivery order while keeping Pulsar changes minimal |
| Fail closed on key/crypto/runtime errors | Prefer failed jobs over plaintext exposure |

## Risks and follow-up specs

Known follow-up work that should be specified separately if this design is accepted:

- non-Pulsar implementation plan
- Pulsar compatibility verification / implementation plan
- operator/deployment documentation for trusted execution prerequisites

## User Check-in markers

### User Check-in 1

Confirm before implementation planning that existing config + dataset metadata, without a separate crypto-plan artifact, remains the approved execution-control model.

### User Check-in 2

Confirm before implementation planning that output return should stay encrypted-at-rest in Galaxy and that B, not A, should perform the final header recryption back to the user's public key.
