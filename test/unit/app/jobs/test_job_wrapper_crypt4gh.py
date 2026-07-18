from types import SimpleNamespace

from pathlib import Path

import pytest

import galaxy.jobs as galaxy_jobs
from galaxy.jobs import JobWrapper
from galaxy.model import Dataset
from galaxy.tools.crypt4gh_remote_execution import Crypt4GHRemoteExecutionError


def _dataset_assoc_with_instances(*instances):
    dataset = SimpleNamespace(id=42, history_associations=list(instances), library_associations=[])
    dataset_instance = SimpleNamespace(dataset=dataset)
    return SimpleNamespace(dataset=dataset_instance)


def test_normalize_successful_output_association_states_marks_pending_instances_ok():
    wrapper = JobWrapper.__new__(JobWrapper)
    added = []
    wrapper.sa_session = SimpleNamespace(add=lambda dataset_instance: added.append(dataset_instance))

    running_instance = SimpleNamespace(id=3, state=Dataset.states.RUNNING, dataset=SimpleNamespace(id=3))
    queued_instance = SimpleNamespace(id=4, state=Dataset.states.QUEUED, dataset=SimpleNamespace(id=4))
    ok_instance = SimpleNamespace(id=5, state=Dataset.states.OK, dataset=SimpleNamespace(id=5))
    association = _dataset_assoc_with_instances(running_instance, queued_instance, ok_instance)

    job = SimpleNamespace(id=9)
    wrapper._normalize_successful_output_association_states(job, [association])

    assert running_instance.state == Dataset.states.OK
    assert queued_instance.state == Dataset.states.OK
    assert ok_instance.state == Dataset.states.OK
    assert added == [running_instance, queued_instance]


def test_normalize_successful_output_association_states_leaves_non_pending_unchanged():
    wrapper = JobWrapper.__new__(JobWrapper)
    added = []
    wrapper.sa_session = SimpleNamespace(add=lambda dataset_instance: added.append(dataset_instance))

    ok_instance = SimpleNamespace(id=11, state=Dataset.states.OK, dataset=SimpleNamespace(id=11))
    failed_meta_instance = SimpleNamespace(
        id=12,
        state=Dataset.states.FAILED_METADATA,
        dataset=SimpleNamespace(id=12),
    )
    association = _dataset_assoc_with_instances(ok_instance, failed_meta_instance)

    job = SimpleNamespace(id=12)
    wrapper._normalize_successful_output_association_states(job, [association])

    assert ok_instance.state == Dataset.states.OK
    assert failed_meta_instance.state == Dataset.states.FAILED_METADATA
    assert added == []


def test_verify_crypt4gh_pre_success_evidence_calls_verifier_with_working_directory(monkeypatch):
    wrapper = JobWrapper.__new__(JobWrapper)
    wrapper._MinimalJobWrapper__working_directory = "/tmp/workdir"

    captured = {}

    def _fake_verifier(*, working_directory, output_dataset_associations):
        captured["working_directory"] = working_directory
        captured["output_dataset_associations"] = output_dataset_associations

    monkeypatch.setattr(galaxy_jobs, "verify_crypt4gh_pre_success_output_evidence", _fake_verifier)

    associations = [SimpleNamespace(name="out")]
    wrapper._verify_crypt4gh_pre_success_evidence(SimpleNamespace(id=1), associations)

    assert captured == {
        "working_directory": "/tmp/workdir",
        "output_dataset_associations": associations,
    }


def test_verify_crypt4gh_pre_success_evidence_wraps_crypt4gh_error(monkeypatch):
    wrapper = JobWrapper.__new__(JobWrapper)
    wrapper._MinimalJobWrapper__working_directory = "/tmp/workdir"

    def _raising_verifier(*, working_directory, output_dataset_associations):
        del working_directory
        del output_dataset_associations
        raise Crypt4GHRemoteExecutionError("payload marker missing for dataset_id=99")

    monkeypatch.setattr(galaxy_jobs, "verify_crypt4gh_pre_success_output_evidence", _raising_verifier)

    with pytest.raises(RuntimeError, match="payload marker missing"):
        wrapper._verify_crypt4gh_pre_success_evidence(SimpleNamespace(id=1), [])


def test_verify_crypt4gh_pre_success_evidence_fails_for_tracked_payload_outside_job_scope(monkeypatch, tmp_path):
    wrapper = JobWrapper.__new__(JobWrapper)
    working_directory = tmp_path / "job"
    working_directory.mkdir(parents=True, exist_ok=True)
    wrapper._MinimalJobWrapper__working_directory = str(working_directory)

    outside_payload_path = Path("/tmp") / "crypt4gh-outside-scope-payload.dat"

    def _should_not_be_called(*, working_directory, output_dataset_associations):
        del working_directory
        del output_dataset_associations
        raise AssertionError("pre-success verifier should fail closed before delegating to verifier")

    monkeypatch.setattr(galaxy_jobs, "verify_crypt4gh_pre_success_output_evidence", _should_not_be_called)

    crypt4gh_dataset = SimpleNamespace(
        ext="tabular.c4gh",
        metadata=SimpleNamespace(crypt4gh_header="header"),
        dataset=SimpleNamespace(id=99, get_file_name=lambda sync_cache=False: str(outside_payload_path)),
    )
    association = SimpleNamespace(name="out", dataset=crypt4gh_dataset)

    with pytest.raises(RuntimeError, match="outside job scope roots"):
        wrapper._verify_crypt4gh_pre_success_evidence(SimpleNamespace(id=1), [association])


def test_current_output_dataset_associations_include_late_discovered_outputs():
    wrapper = JobWrapper.__new__(JobWrapper)

    declared = SimpleNamespace(name="declared")
    discovered = SimpleNamespace(name="__new_primary_file_out|sample__")
    job = SimpleNamespace(
        output_datasets=[declared],
        output_library_datasets=[],
    )

    stale_associations = job.output_datasets + job.output_library_datasets
    job.output_datasets.append(discovered)

    current_associations = wrapper._current_output_dataset_associations(job)

    assert discovered not in stale_associations
    assert current_associations == [declared, discovered]


def test_discover_outputs_and_refresh_associations_returns_new_discovered_associations():
    wrapper = JobWrapper.__new__(JobWrapper)
    declared = SimpleNamespace(name="declared")
    discovered = SimpleNamespace(name="__new_primary_file_out|sample__")
    job = SimpleNamespace(
        output_datasets=[declared],
        output_library_datasets=[],
    )

    def _discover_outputs(*_args, **_kwargs):
        job.output_datasets.append(discovered)

    wrapper.discover_outputs = _discover_outputs

    refreshed_associations = wrapper._discover_outputs_and_refresh_associations(
        job,
        inp_data={},
        out_data={},
        out_collections={},
        final_job_state="ok",
    )

    assert refreshed_associations == [declared, discovered]
