# Crypt4GH / remote execution findings

## Scope

This note summarizes findings from investigating where Galaxy and Pulsar:

- prepare jobs
- stage datasets
- build remote commands
- could host compute-controlled decrypt/encrypt logic

The goal was to evaluate options for **keeping decrypt/encrypt under trusted compute-side control**, rather than under control of the orchestrating Galaxy instance.

---

## Trust model used in the discussion

Assumed:

- the **orchestrating Galaxy** is less trusted
- the **compute environment** is trusted
- it is acceptable if the compute side also runs trusted **Galaxy/Pulsar code**
- protecting against a **malicious tool command** is out of scope
- if decrypt injection is missing, a preferred failure mode is that the tool runs on ciphertext and fails / produces bad output, rather than plaintext exposure

---

## High-level conclusions

### 1. `prepare_job()` runs on the orchestrating Galaxy side

Relevant code:

- `lib/galaxy/jobs/runners/__init__.py`

`BaseJobRunner.prepare_job()`:

- runs in the Galaxy job handler / runner process
- calls `job_wrapper.prepare()`
- builds the command line
- currently applies `_apply_crypt4gh_staging()` there

Implication:

- current `_apply_crypt4gh_staging()` is **orchestrator-controlled**, not compute-controlled

### 2. `_apply_crypt4gh_staging()` wraps the generated command/script

Current design:

- detects crypt4gh inputs
- prepares staging information
- rewrites `tool_script.sh` or command line
- injects pre/post shell for decrypt/mount/encrypt/cleanup

Implication:

- this is convenient for runner reuse
- but it keeps sensitive execution planning on the orchestrator
- that is a poor fit if the orchestrator is not trusted enough to author decryption behavior

### 3. The best compute-side hook inside Galaxy is `remote_tool_eval.py`

Relevant code:

- `lib/galaxy/jobs/command_factory.py`
- `lib/galaxy/tools/remote_tool_eval.py`

If `tool_evaluation_strategy == "remote"`, Galaxy:

- serializes job/model/tool state in `prepare()`
- later injects execution of `remote_tool_eval.py`
- `remote_tool_eval.py` runs on the execution side and rebuilds the tool command there

This makes `remote_tool_eval.py` the **closest thing to a single shared execution-side hook**.

But:

- the orchestrator still decides to enable it
- the orchestrator still injects the command that runs it
- Pulsar support for this path is explicitly marked as rough / hacky in `command_factory.py`

### 4. For Pulsar, normal file staging is not managed by `remote_tool_eval.py`

`remote_tool_eval.py`:

- consumes staged/imported job state
- rebuilds the command remotely
- may materialize deferred/file-source inputs via evaluator logic

But it is **not** the main remote file transfer/staging layer.

For Pulsar:

- Galaxy describes staged inputs via `ClientInput` / `ClientInputs`
- Pulsar performs the remote staging implementation

---

## Where different pieces run

### Orchestrating Galaxy

Runs:

- `prepare_job()`
- `job_wrapper.prepare()`
- `build_command()`
- `__handle_remote_command_line_building()`
- current `_apply_crypt4gh_staging()`
- Pulsar **client** code used from Galaxy

### Pulsar host / intermediate host

Runs:

- Pulsar app / managers
- remote staging logic
- possibly the actual tool process, depending on manager type

### Final compute node

Runs the actual tool process when Pulsar uses a manager that submits onward to cluster/pod/container compute.

---

## Meaning of `remote_tool_eval.py`

### Where it is triggered

Relevant code:

- `lib/galaxy/jobs/command_factory.py::__handle_remote_command_line_building`

This function runs on the **orchestrator**, not compute.

It prepends a command like:

```sh
PYTHONPATH="$GALAXY_LIB:$PYTHONPATH" python "$GALAXY_LIB"/galaxy/tools/remote_tool_eval.py
```

So:

- `__handle_remote_command_line_building()` runs on orchestrating Galaxy
- `remote_tool_eval.py` runs later on the execution side

### What `remote_tool_eval.py` loads

Relevant code:

- `lib/galaxy/tools/remote_tool_eval.py`
- `lib/galaxy/jobs/__init__.py` (`prepare()`)

When remote evaluation is enabled, Galaxy prepares:

- `metadata/outputs_new/job_io.json`
- `metadata/outputs_new/tool_data_tables.json`
- imported model-store content
- `registry.xml`

Then `remote_tool_eval.py`:

- imports the model store
- loads `JobIO`
- reconstructs the tool
- runs `RemoteToolEvaluator.build()`
- appends the resulting command to `tool_script.sh`

---

## Dataset metadata access in `remote_tool_eval.py`

Relevant objects:

- `job_io`
- `job_io.job`
- `job_io.get_input_datasets()`

Recommended access pattern:

```python
for dataset in job_io.get_input_datasets():
    value = dataset.metadata.get("some_key", None)
```

Generic enumeration:

```python
for dataset in job_io.get_input_datasets():
    for key in dataset.metadata.spec:
        value = dataset.metadata.get(key, None)
```

Notes:

- file-backed metadata may come back as metadata file objects
- not all metadata values should be assumed eagerly materialized
- `job_io.get_input_datasets()` is the cleanest entry point

---

## Dataset staging findings

### Without Pulsar

Dataset path handling is primarily managed by Galaxy-side job preparation and command generation:

- `lib/galaxy/job_execution/setup.py`
  - `JobIO.get_input_datasets()`
  - `JobIO.get_input_paths()`
  - `JobIO.get_input_path()`
- `lib/galaxy/jobs/command_factory.py`
- runner job scripts / command wrappers

### With Pulsar

Split of responsibility:

- Galaxy describes inputs through:
  - `lib/galaxy/jobs/runners/pulsar.py`
  - `ClientInput`, `ClientInputs`, `ClientOutputs`
- Pulsar performs remote staging / transfer / materialization

### In relation to `remote_tool_eval.py`

`remote_tool_eval.py` is:

- a consumer of already-prepared job/model state
- a remote command builder
- not the primary normal staging layer for standard Pulsar input transfer

It is most relevant for:

- command reconstruction
- possibly decrypt policy based on dataset metadata
- temp path rewriting

---

## Failure mode if no decrypt injection happens

If the orchestrator does **not** inject `remote_tool_eval.py` and there is **no other compute-side decrypt hook**, then the likely result is:

- the tool reads ciphertext bytes
- the job fails, or
- the tool succeeds but produces nonsense / invalid output

That is generally preferable to unauthorized plaintext exposure.

Important nuance:

- the failure is not guaranteed to be a clean failure
- some tools may produce garbage output instead of erroring

---

## Candidate places for compute-controlled decrypt/encrypt

### Best shared Galaxy-side execution hook

#### `lib/galaxy/tools/remote_tool_eval.py`

Best when the goal is:

- one code location
- compute-side command reconstruction
- reuse across local/shared-filesystem/cluster-style execution

Weaknesses:

- still orchestrator-triggered
- Pulsar support is currently not clean

### Best Pulsar-native hooks

Good if the main deployment is Pulsar and stronger compute-side control is preferred:

- Pulsar-side input staging/materialization
- Pulsar-side output finalization / collection
- Pulsar-side container/runtime launch

These are better for strict compute control, but are not a single shared hook across all Galaxy deployment types.

---

## Recommended design for plaintext temp paths

If implementing around `remote_tool_eval.py`, the recommended model is:

1. inspect input datasets and metadata
2. decide which inputs require decrypt/mount
3. create a per-job compute-local crypto workspace
4. rewrite the tool-visible paths to plaintext temp paths
5. run the tool
6. encrypt selected outputs before export
7. clean up plaintext intermediates

Suggested path layout:

```text
<job_working_directory>/
  _crypt/
    inputs/
      ds_<id>/plaintext
    outputs/
    scratch/
```

Guidelines:

- keep plaintext outside normal output discovery locations
- do not place plaintext temp files under exported output paths
- prefer a dedicated mapping from encrypted input path -> plaintext temp path
- clean up in a reliable post-run step

### Input-side choice

Two patterns were considered:

- materialize plaintext temp files
- mount a decrypted view (e.g. FUSE)

For a first implementation inside `remote_tool_eval.py`, materialized plaintext temp files are simpler.

### Output-side choice

Treat tool outputs as plaintext intermediates and encrypt them before export.

Do not let final exported dataset paths directly point at plaintext files if encrypted-at-rest return is required.

---

## Suggested next implementation direction

### If aiming for one shared hook

Use:

- `tool_evaluation_strategy = remote`
- central decrypt/encrypt/path-rewrite logic in or behind `lib/galaxy/tools/remote_tool_eval.py`

### If aiming for the strongest Pulsar-based trust separation

Put crypto logic in Pulsar-side:

- input staging
- output finalization
- runtime/container launch enforcement

and avoid orchestrator-side crypt4gh command wrapping.

---

## Practical takeaway

- Current `_apply_crypt4gh_staging()` is convenient but orchestrator-controlled.
- `remote_tool_eval.py` is the best existing **shared execution-side hook** in Galaxy.
- Pulsar is the better place for fully compute-owned staging/finalization, but that is a more deployment-specific solution.
- For a first compute-side redesign with minimal architecture churn, `remote_tool_eval.py` is the best place to prototype decrypt/encrypt + plaintext temp path management.

