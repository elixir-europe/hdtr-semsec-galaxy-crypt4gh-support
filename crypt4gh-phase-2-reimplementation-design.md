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
- shared metadata/contract needed across Galaxy, A, and B
- non-Pulsar-first execution strategy
- follow-on Pulsar-compatible architecture
- acceptance-test definition for the first tracer bullet

Out of scope for this design:

- exact REST route names and payload schemas for recryptor changes
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
- storing dataset metadata needed for later recryption/execution
- recording which inputs require Crypt4GH handling
- emitting a **declarative crypto execution plan** with the job
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
- participating in the return path from Galaxy-accessible encrypted outputs back to the user's own keypair

#### Recryptor B: compute-side REST service

B is responsible for:

- minting and holding time-bounded compute-side private keys
- associating those keys with key ids and expiration metadata
- transforming headers from the B-managed temporary key context into a job-local execution key context
- never disclosing B-managed private keys to Galaxy or the job runtime

#### Execution-side Crypt4GH helper

The execution-side helper is responsible for:

- consuming Galaxy's declarative crypto execution plan
- creating a per-job crypto workspace
- minting or loading the short-lived job-local execution keypair
- coordinating with B for the second input-header recryption step
- materializing plaintext input paths or decryptable local views for the tool
- encrypting outputs before they are exported back into Galaxy-managed storage
- cleaning up plaintext intermediates and short-lived key material

### Chosen execution strategy

Use a **shared crypto contract with two runtime adapters**:

1. **Non-Pulsar first:** execution-side logic behind `remote_tool_eval.py`
2. **Pulsar later:** Pulsar-native staging/finalization/runtime integration using the same metadata contract

This preserves one common design without forcing one brittle runtime hook across both deployment models.

## Core runtime design

### 1. Input preparation already implemented through A and B

The current user-side flow remains the upstream prerequisite:

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

Before Galaxy re-imports or publishes them:

1. the execution-side helper encrypts each selected output into the job-local execution key context
2. the encrypted output header is then recrypted into the user-return context
3. Galaxy receives only encrypted-at-rest files plus the metadata needed for later user-side recovery

The design goal is that no final exported dataset path in Galaxy points to plaintext content when encrypted return is required.

### 6. Return-to-user path

The return path must stay consistent with the two-recryptor model:

- Galaxy should store an encrypted output in a form recoverable through recryption, not in plaintext
- A remains the user-facing recryption bridge from Galaxy-visible encrypted state to the user's final keypair

The exact ownership of the user-return temporary key context belongs in the A/B service contract, but Galaxy's role is only to preserve the encrypted artifact plus metadata required to continue that flow.

## Shared metadata and job contract

Galaxy should standardize a runtime contract that is independent of non-Pulsar vs Pulsar execution.

### Dataset metadata contract

For Crypt4GH job inputs, Galaxy should persist at least:

- stored recrypted header
- compute-side key id
- compute-side key expiration timestamp
- indication that the dataset requires Crypt4GH execution handling

For encrypted outputs returned to Galaxy, Galaxy should persist at least:

- encrypted header suitable for the user-return flow
- metadata linking the output to its return/recryption context
- indication that the dataset is encrypted-at-rest and must not be treated as plaintext

### Declarative crypto execution plan

Galaxy should attach a job-scoped plan describing:

- which inputs require Crypt4GH handling
- where each tool-visible plaintext path should appear
- which outputs must be re-encrypted before export
- any required key ids / expirations / policy flags

This plan is intentionally declarative. It describes **what** crypto handling is required, not **how** shell commands should be wrapped on the orchestrator.

## Deployment-specific adapters

### Non-Pulsar first

The first implementation should use `tool_evaluation_strategy = remote` and execution-side logic in or behind `lib/galaxy/tools/remote_tool_eval.py`.

Responsibilities of the non-Pulsar adapter:

- load the declarative crypto execution plan
- allocate the per-job crypto workspace
- obtain the second-stage recrypted headers from B
- materialize plaintext input files for tool-visible paths
- rewrite the tool command to those plaintext paths
- encrypt designated outputs before export
- clean up plaintext and short-lived keys

This is the best first shared Galaxy-side execution hook identified by the findings and avoids the old orchestrator-side `_apply_crypt4gh_staging()` model.

### Pulsar follow-on

Pulsar support should use the same metadata contract and lifecycle, but enforcement should move into Pulsar-side responsibilities:

- input staging/materialization
- runtime/container launch policy
- output finalization/collection

This gives stronger compute-owned staging for Pulsar without forcing the non-Pulsar adapter to mimic Pulsar internals.

## Required external capabilities

Galaxy's redesign depends on A/B gaining additional external capabilities beyond the currently implemented endpoints.

At a minimum, the combined service contract must support:

- B validating a compute-side key id and rejecting expired ones
- B recrypting an input header from the B-managed temporary key context into a job-local execution public key
- a return-path mechanism so encrypted outputs can move from the job-local execution context into a user-return context without exposing the relevant private keys to Galaxy
- A completing the final user-facing recryption step to the user's own keypair

Exact route names, payloads, and service-to-service authentication are intentionally left to a separate cross-service API specification.

## Failure model

The design should fail closed.

Required failure behavior:

- expired or unknown compute-side key id -> fail before tool launch
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
- the output is encrypted before export back to Galaxy-managed storage
- Galaxy stores only encrypted output plus return-path metadata

### Acceptance test 3: fail-closed expired key

Given an expired compute-side key id,

When the job starts, then:

- the job fails before tool launch
- no plaintext input path is materialized for the tool

### Acceptance test 4: contract reuse for Pulsar

Given the same dataset metadata and declarative crypto execution plan,

When a Pulsar adapter is later added, then:

- it can consume the same contract without changing stored dataset semantics

## Key decisions

| Decision | Rationale |
| --- | --- |
| Replace single re-encryptor assumption with A + B model | Matches the actual system concept and keeps private material on the correct side |
| Replace orchestrator-side runtime wrapping with execution-side handling | Aligns with the trust findings and reduces orchestrator control over plaintext handling |
| Use second-stage recryption into a job-local execution keypair | Lets B keep its temporary private keys while enabling per-job decrypt/encrypt |
| Use declarative crypto plans from Galaxy | Supports both non-Pulsar and Pulsar without duplicating dataset semantics |
| Materialize plaintext temp files first | Simplest first implementation behind `remote_tool_eval.py` |
| Support both deployment styles, but implement non-Pulsar first | Matches requested delivery order and the current findings |
| Fail closed on key/crypto/runtime errors | Prefer failed jobs over plaintext exposure |

## Risks and follow-up specs

Known follow-up work that should be specified separately if this design is accepted:

- exact A/B API additions and payloads
- Galaxy job metadata schema for the declarative crypto execution plan
- non-Pulsar implementation plan
- Pulsar adapter implementation plan
- operator/deployment documentation for trusted execution prerequisites

## User Check-in markers

### User Check-in 1

Confirm before implementation planning that the shared-contract / two-adapter architecture remains the approved direction, rather than forcing one runtime hook across non-Pulsar and Pulsar.

### User Check-in 2

Confirm before implementation planning that output return should stay encrypted-at-rest in Galaxy and continue through an A-mediated return-to-user flow, rather than introducing any Galaxy-held long-lived user decryption key.
