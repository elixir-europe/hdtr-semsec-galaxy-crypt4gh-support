from types import SimpleNamespace

import pytest

import galaxy.jobs as galaxy_jobs
from galaxy.jobs import JobWrapper
from galaxy.tools.crypt4gh_remote_execution import Crypt4GHRemoteExecutionError


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


def test_verify_crypt4gh_pre_success_evidence_allows_tracked_payload_on_object_store_path(monkeypatch, tmp_path):
    wrapper = JobWrapper.__new__(JobWrapper)
    working_directory = tmp_path / "job"
    working_directory.mkdir(parents=True, exist_ok=True)
    wrapper._MinimalJobWrapper__working_directory = str(working_directory)

    object_store_payload_path = "/opt/galaxy/database/objects/0/e/b/dataset_0eb845c1-c842-43f0-93b0-9698641f4bd8.dat"

    captured = {}

    def _fake_verifier(*, working_directory, output_dataset_associations):
        captured["working_directory"] = working_directory
        captured["output_dataset_associations"] = output_dataset_associations

    monkeypatch.setattr(galaxy_jobs, "verify_crypt4gh_pre_success_output_evidence", _fake_verifier)

    crypt4gh_dataset = SimpleNamespace(
        ext="tabular.c4gh",
        metadata=SimpleNamespace(crypt4gh_header="header"),
        dataset=SimpleNamespace(id=99, get_file_name=lambda sync_cache=False: object_store_payload_path),
    )
    association = SimpleNamespace(name="out", dataset=crypt4gh_dataset)

    wrapper._verify_crypt4gh_pre_success_evidence(SimpleNamespace(id=1), [association])

    assert captured == {
        "working_directory": str(working_directory),
        "output_dataset_associations": [association],
    }


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


def test_should_update_output_extension_from_context_handles_non_string_context_ext_for_upload1():
    wrapper = JobWrapper.__new__(JobWrapper)
    wrapper.tool = SimpleNamespace(id="upload1")

    dataset = SimpleNamespace(ext="fastqsanger")

    assert wrapper._should_update_output_extension_from_context(dataset, None) is False
    assert wrapper._should_update_output_extension_from_context(dataset, 123) is False


def test_should_update_output_extension_from_context_allows_crypt4gh_suffix_for_upload1():
    wrapper = JobWrapper.__new__(JobWrapper)
    wrapper.tool = SimpleNamespace(id="upload1")

    dataset = SimpleNamespace(ext="fastqsanger")

    assert wrapper._should_update_output_extension_from_context(dataset, "fastqsanger.c4gh") is True
