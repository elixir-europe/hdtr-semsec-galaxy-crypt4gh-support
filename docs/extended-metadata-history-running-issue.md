# Extended metadata discovered-output history remains `running`

Reference fix commit: `994f5d2d15` (`fix(jobs): normalize pending output association states after successful finish`)

## Problem

In extended-metadata mode, jobs that discover outputs (notably when using `assign_primary_output="true"`) can complete with `job.state == ok`, while one output dataset association still reports a pending association state (`running`/`queued`/`new`/`upload`).

History state aggregation uses dataset-association state, so history can remain `running` even after successful job completion and finalized outputs.

## Why this happens

`HistoryDatasetAssociation.state` resolves association-local `_state` first, then falls back to underlying `dataset.state`.

In this finish path, imported output associations can retain pending `_state`; if that pending state is not normalized on successful completion, the history view remains non-terminal.

## User-visible impact

- `wait_for_history(..., assert_ok=True)` may time out.
- History remains `running` with one stuck output association.
- Automation that waits for terminal history state is delayed or fails.

## Failing reproduction (programmatic)

This test shape fails pre-fix and passes with commit `994f5d2d15`:

```python
def test_extended_metadata_discovered_primary_output_history_not_stuck_running(dataset_populator):
    history_id = dataset_populator.new_history()
    input_hda = dataset_populator.new_dataset(
        history_id,
        content="a\nb\n",
        file_type="txt",
        wait=True,
    )

    run_response = dataset_populator.run_tool(
        "multi_output_assign_primary",
        {
            "num_param": 7,
            "input": {"src": "hda", "id": input_hda["id"]},
        },
        history_id,
    )
    job_id = run_response["jobs"][0]["id"]

    dataset_populator.wait_for_job(job_id, assert_ok=True)

    # Pre-fix failure: TimeoutAssertionError because history remains "running"
    dataset_populator.wait_for_history(history_id, assert_ok=True)
```

Typical failing pre-fix response shape:

- `job.state == "ok"`
- `history.state == "running"`
- `history.state_ids.running` contains one output dataset id
- other outputs already in `ok`

## Manual reproduction (optional)

1. Configure runner with extended metadata enabled.
2. Upload a small input dataset.
3. Run `multi_output_assign_primary`.
4. Wait for job `ok`.
5. Poll `/api/histories/{id}`.

Pre-fix behavior: history can remain `running` with one output association in `state_ids.running`.

## Root cause + fix summary

In extended-metadata finish flow, discovered output associations could retain pending association-local state (`_state`), so `HistoryDatasetAssociation.state` still resolved to `running` after successful job completion. This left history state non-terminal even though job and underlying datasets were complete.

Fix (commit `994f5d2d15`): normalize pending output association states (`new/upload/queued/running`) to `ok` on successful extended-metadata completion, while preserving existing behavior for error/deferred/failed-metadata states.
