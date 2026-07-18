from datetime import (
    datetime,
    timedelta,
)
import json
from pathlib import Path
import subprocess
from typing import cast

import pytest

from galaxy.jobs.runners import BaseJobRunner
import galaxy.tools.crypt4gh_remote_execution as crypt4gh_remote_execution
from galaxy.tools.crypt4gh_remote_execution import (
    assert_crypt4gh_job_readiness,
    build_crypt4gh_remote_compute_environment,
    build_crypt4gh_cleanup_wrapped_command,
    collect_declared_crypt4gh_output_targets,
    CRYPT4GH_PLAINTEXT_CLEANUP_FAILED_MARKER,
    Crypt4GHRemoteExecutionError,
    finalize_about_to_persist_crypt4gh_payload,
    finalize_declared_crypt4gh_outputs,
    should_run_crypt4gh_remote_execution,
)


class _DatasetMetadata:
    def __init__(self, *, crypt4gh_header=None, expiration=None):
        self.crypt4gh_header = crypt4gh_header
        self.crypt4gh_compute_keypair_expiration_date = expiration


class _Dataset:
    def __init__(self, metadata, ext="data"):
        self.metadata = metadata
        self.ext = ext


class _JobIO:
    def __init__(self, datasets):
        self._datasets = datasets

    def get_input_datasets(self):
        return self._datasets


class _Config:
    def __init__(
        self,
        enable_crypt4gh_remote_execution_staging,
        enable_crypt4gh_transparent_input_matching=True,
        outputs_to_working_directory=True,
        metadata_strategy="extended",
        crypt4gh_reencryption_service_url="http://127.0.0.1:9999",
        tool_evaluation_strategy="remote",
    ):
        self.enable_crypt4gh_remote_execution_staging = enable_crypt4gh_remote_execution_staging
        self.enable_crypt4gh_transparent_input_matching = enable_crypt4gh_transparent_input_matching
        self.outputs_to_working_directory = outputs_to_working_directory
        self.metadata_strategy = metadata_strategy
        self.crypt4gh_reencryption_service_url = crypt4gh_reencryption_service_url
        self.tool_evaluation_strategy = tool_evaluation_strategy


class _RunnerApp:
    def __init__(self, *, enable_crypt4gh_remote_execution_staging):
        self.config = _Config(enable_crypt4gh_remote_execution_staging)


class _DatasetWrapper:
    def __init__(self, *, dataset_id):
        self.id = dataset_id


class _BuildDataset:
    def __init__(self, *, dataset_id, metadata):
        self.dataset = _DatasetWrapper(dataset_id=dataset_id)
        self.metadata = metadata


class _BuildJob:
    def __init__(self, destination_params=None):
        self.destination_params = destination_params or {}


class _InputDatasetAssociation:
    def __init__(self, *, name, dataset):
        self.name = name
        self.dataset = dataset


class _ReadinessJob:
    def __init__(self, input_datasets):
        self.input_datasets = list(input_datasets)
        self.input_library_datasets = []


class _ReadinessJobIO:
    def __init__(self, input_associations):
        self.job = _ReadinessJob(input_associations)

    def get_input_datasets(self):
        datasets = []
        for association in self.job.input_datasets:
            if association.dataset is not None:
                datasets.append(association.dataset)
        return datasets


class _ReadinessToolInput:
    def __init__(self, extensions):
        self.extensions = extensions


class _ReadinessTool:
    def __init__(self, inputs):
        self.inputs = inputs


@pytest.fixture
def crypt4gh_dataset():
    return _Dataset(_DatasetMetadata(crypt4gh_header="header", expiration="2026-06-02T12:00:00+00:00"))


def test_top_level_gate_disables_remote_helper_setup(crypt4gh_dataset):
    result = should_run_crypt4gh_remote_execution(
        job_io=_JobIO([crypt4gh_dataset]),
        app_config=_Config(enable_crypt4gh_remote_execution_staging=False),
        destination_params={"tool_evaluation_strategy": "remote"},
    )

    assert result is False


def test_helper_path_requires_remote_tool_evaluation_strategy(crypt4gh_dataset):
    result = should_run_crypt4gh_remote_execution(
        job_io=_JobIO([crypt4gh_dataset]),
        app_config=_Config(enable_crypt4gh_remote_execution_staging=True),
        destination_params={"tool_evaluation_strategy": "local"},
    )

    assert result is False


def test_helper_setup_failures_incorrect_expiration_fail_closed():
    dataset = _Dataset(_DatasetMetadata(crypt4gh_header="header", expiration="not-a-date"))

    with pytest.raises(Crypt4GHRemoteExecutionError, match="Invalid Crypt4GH compute key expiration timestamp"):
        should_run_crypt4gh_remote_execution(
            job_io=_JobIO([dataset]),
            app_config=_Config(enable_crypt4gh_remote_execution_staging=True),
            destination_params={"tool_evaluation_strategy": "remote"},
        )

def test_helper_setup_failures_no_expiration_time_zone_fail_closed():
    dataset = _Dataset(_DatasetMetadata(crypt4gh_header="header", expiration="2024-06-02T12:00:00"))

    with pytest.raises(Crypt4GHRemoteExecutionError, match="timezone"):
        should_run_crypt4gh_remote_execution(
            job_io=_JobIO([dataset]),
            app_config=_Config(enable_crypt4gh_remote_execution_staging=True),
            destination_params={"tool_evaluation_strategy": "remote"},
        )

def test_helper_setup_no_failures(crypt4gh_dataset):
    result = should_run_crypt4gh_remote_execution(
        job_io=_JobIO([crypt4gh_dataset]),
        app_config=_Config(enable_crypt4gh_remote_execution_staging=True),
        destination_params={"tool_evaluation_strategy": "remote"},
        now=datetime.fromisoformat("2026-06-01T11:00:00+00:00")
    )
    assert result is True


def test_helper_path_uses_global_remote_tool_evaluation_strategy_when_destination_setting_missing(crypt4gh_dataset):
    app_config = _Config(enable_crypt4gh_remote_execution_staging=True)
    app_config.tool_evaluation_strategy = "remote"

    result = should_run_crypt4gh_remote_execution(
        job_io=_JobIO([crypt4gh_dataset]),
        app_config=app_config,
        destination_params={},
        now=datetime.fromisoformat("2026-06-01T11:00:00+00:00"),
    )

    assert result is True


def test_readiness_rejects_remote_execution_staging_without_input_matching():
    crypt4gh_dataset = _Dataset(
        _DatasetMetadata(crypt4gh_header="header", expiration="2026-06-02T12:00:00+00:00"),
        ext="fastqsanger.c4gh",
    )
    input_association = _InputDatasetAssociation(name="input_data", dataset=crypt4gh_dataset)

    with pytest.raises(Crypt4GHRemoteExecutionError, match="requires enable_crypt4gh_transparent_input_matching"):
        assert_crypt4gh_job_readiness(
            job_io=_ReadinessJobIO([input_association]),
            tool=None,
            app_config=_Config(
                enable_crypt4gh_remote_execution_staging=True,
                enable_crypt4gh_transparent_input_matching=False,
            ),
            destination_params={"tool_evaluation_strategy": "remote"},
            metadata_strategy="extended",
        )


def test_readiness_rejects_crypt4gh_inputs_without_remote_execution_staging_even_when_input_accepts_c4gh():
    crypt4gh_dataset = _Dataset(
        _DatasetMetadata(crypt4gh_header="header", expiration="2026-06-02T12:00:00+00:00"),
        ext="fastqsanger.c4gh",
    )
    input_association = _InputDatasetAssociation(name="input_data", dataset=crypt4gh_dataset)
    tool = _ReadinessTool(inputs={"input_data": _ReadinessToolInput(["fastqsanger.c4gh"])})

    with pytest.raises(Crypt4GHRemoteExecutionError, match="requires enable_crypt4gh_remote_execution_staging"):
        assert_crypt4gh_job_readiness(
            job_io=_ReadinessJobIO([input_association]),
            tool=tool,
            app_config=_Config(
                enable_crypt4gh_remote_execution_staging=False,
                enable_crypt4gh_transparent_input_matching=True,
            ),
            destination_params={"tool_evaluation_strategy": "local"},
            metadata_strategy="directory",
        )


def test_readiness_rejects_transparent_adapted_inputs_without_remote_staging_gate():
    crypt4gh_dataset = _Dataset(
        _DatasetMetadata(crypt4gh_header="header", expiration="2026-06-02T12:00:00+00:00"),
        ext="fastqsanger.c4gh",
    )
    input_association = _InputDatasetAssociation(name="input_data", dataset=crypt4gh_dataset)
    tool = _ReadinessTool(inputs={"input_data": _ReadinessToolInput(["fastqsanger"])})

    with pytest.raises(Crypt4GHRemoteExecutionError, match="requires enable_crypt4gh_remote_execution_staging"):
        assert_crypt4gh_job_readiness(
            job_io=_ReadinessJobIO([input_association]),
            tool=tool,
            app_config=_Config(
                enable_crypt4gh_remote_execution_staging=False,
                enable_crypt4gh_transparent_input_matching=True,
            ),
            destination_params={"tool_evaluation_strategy": "remote"},
            metadata_strategy="extended",
        )


def test_readiness_rejects_transparent_adapted_inputs_without_remote_evaluation():
    crypt4gh_dataset = _Dataset(
        _DatasetMetadata(crypt4gh_header="header", expiration="2026-06-02T12:00:00+00:00"),
        ext="fastqsanger.c4gh",
    )
    input_association = _InputDatasetAssociation(name="input_data", dataset=crypt4gh_dataset)
    tool = _ReadinessTool(inputs={"input_data": _ReadinessToolInput(["fastqsanger"])})

    with pytest.raises(Crypt4GHRemoteExecutionError, match="tool_evaluation_strategy = remote"):
        assert_crypt4gh_job_readiness(
            job_io=_ReadinessJobIO([input_association]),
            tool=tool,
            app_config=_Config(enable_crypt4gh_remote_execution_staging=True),
            destination_params={"tool_evaluation_strategy": "local"},
            metadata_strategy="extended",
        )


def test_readiness_rejects_transparent_adapted_inputs_without_extended_metadata_strategy():
    crypt4gh_dataset = _Dataset(
        _DatasetMetadata(crypt4gh_header="header", expiration="2026-06-02T12:00:00+00:00"),
        ext="fastqsanger.c4gh",
    )
    input_association = _InputDatasetAssociation(name="input_data", dataset=crypt4gh_dataset)
    tool = _ReadinessTool(inputs={"input_data": _ReadinessToolInput(["fastqsanger"])})

    with pytest.raises(Crypt4GHRemoteExecutionError, match="metadata_strategy = extended"):
        assert_crypt4gh_job_readiness(
            job_io=_ReadinessJobIO([input_association]),
            tool=tool,
            app_config=_Config(enable_crypt4gh_remote_execution_staging=True),
            destination_params={"tool_evaluation_strategy": "remote"},
            metadata_strategy="directory",
        )


def test_readiness_rejects_transparent_adapted_inputs_without_outputs_to_working_directory():
    crypt4gh_dataset = _Dataset(
        _DatasetMetadata(crypt4gh_header="header", expiration="2026-06-02T12:00:00+00:00"),
        ext="fastqsanger.c4gh",
    )
    input_association = _InputDatasetAssociation(name="input_data", dataset=crypt4gh_dataset)
    tool = _ReadinessTool(inputs={"input_data": _ReadinessToolInput(["fastqsanger"])})

    with pytest.raises(Crypt4GHRemoteExecutionError, match="outputs_to_working_directory = true"):
        assert_crypt4gh_job_readiness(
            job_io=_ReadinessJobIO([input_association]),
            tool=tool,
            app_config=_Config(
                enable_crypt4gh_remote_execution_staging=True,
                outputs_to_working_directory=False,
            ),
            destination_params={"tool_evaluation_strategy": "remote"},
            metadata_strategy="extended",
        )


def test_readiness_rejects_transparent_adapted_inputs_without_reencryption_service_url():
    crypt4gh_dataset = _Dataset(
        _DatasetMetadata(crypt4gh_header="header", expiration="2026-06-02T12:00:00+00:00"),
        ext="fastqsanger.c4gh",
    )
    input_association = _InputDatasetAssociation(name="input_data", dataset=crypt4gh_dataset)
    tool = _ReadinessTool(inputs={"input_data": _ReadinessToolInput(["fastqsanger"])})

    with pytest.raises(Crypt4GHRemoteExecutionError, match="crypt4gh_reencryption_service_url"):
        assert_crypt4gh_job_readiness(
            job_io=_ReadinessJobIO([input_association]),
            tool=tool,
            app_config=_Config(
                enable_crypt4gh_remote_execution_staging=True,
                crypt4gh_reencryption_service_url="",
            ),
            destination_params={"tool_evaluation_strategy": "remote"},
            metadata_strategy="extended",
            reencryption_service_url="",
        )


def test_readiness_allows_transparent_adapted_inputs_when_remote_prerequisites_are_met():
    crypt4gh_dataset = _Dataset(
        _DatasetMetadata(crypt4gh_header="header", expiration="2026-06-02T12:00:00+00:00"),
        ext="fastqsanger.c4gh",
    )
    input_association = _InputDatasetAssociation(name="input_data", dataset=crypt4gh_dataset)
    tool = _ReadinessTool(inputs={"input_data": _ReadinessToolInput(["fastqsanger"])})

    assert_crypt4gh_job_readiness(
        job_io=_ReadinessJobIO([input_association]),
        tool=tool,
        app_config=_Config(enable_crypt4gh_remote_execution_staging=True),
        destination_params={"tool_evaluation_strategy": "remote"},
        metadata_strategy="extended",
        reencryption_service_url="http://127.0.0.1:9999",
    )


def test_readiness_uses_global_remote_strategy_when_destination_setting_missing_for_transparent_inputs():
    crypt4gh_dataset = _Dataset(
        _DatasetMetadata(crypt4gh_header="header", expiration="2026-06-02T12:00:00+00:00"),
        ext="fastqsanger.c4gh",
    )
    input_association = _InputDatasetAssociation(name="input_data", dataset=crypt4gh_dataset)
    tool = _ReadinessTool(inputs={"input_data": _ReadinessToolInput(["fastqsanger"])})
    app_config = _Config(enable_crypt4gh_remote_execution_staging=True)
    app_config.tool_evaluation_strategy = "remote"

    assert_crypt4gh_job_readiness(
        job_io=_ReadinessJobIO([input_association]),
        tool=tool,
        app_config=app_config,
        destination_params={},
        metadata_strategy="extended",
        reencryption_service_url="http://127.0.0.1:9999",
    )


def test_readiness_rejects_explicit_crypt4gh_tool_inputs_without_remote_path():
    crypt4gh_dataset = _Dataset(
        _DatasetMetadata(crypt4gh_header="header", expiration="2026-06-02T12:00:00+00:00"),
        ext="fastqsanger.c4gh",
    )
    input_association = _InputDatasetAssociation(name="input_data", dataset=crypt4gh_dataset)
    tool = _ReadinessTool(inputs={"input_data": _ReadinessToolInput(["fastqsanger.c4gh"])})

    with pytest.raises(Crypt4GHRemoteExecutionError, match="requires enable_crypt4gh_remote_execution_staging"):
        assert_crypt4gh_job_readiness(
            job_io=_ReadinessJobIO([input_association]),
            tool=tool,
            app_config=_Config(
                enable_crypt4gh_remote_execution_staging=False,
                enable_crypt4gh_transparent_input_matching=True,
            ),
            destination_params={"tool_evaluation_strategy": "local"},
            metadata_strategy="directory",
        )


def test_readiness_allows_non_crypt4gh_inputs_without_remote_path():
    plain_dataset = _Dataset(_DatasetMetadata(crypt4gh_header=None, expiration=None), ext="fastqsanger")
    input_association = _InputDatasetAssociation(name="input_data", dataset=plain_dataset)
    tool = _ReadinessTool(inputs={"input_data": _ReadinessToolInput(["fastqsanger"])})

    assert_crypt4gh_job_readiness(
        job_io=_ReadinessJobIO([input_association]),
        tool=tool,
        app_config=_Config(
            enable_crypt4gh_remote_execution_staging=False,
            enable_crypt4gh_transparent_input_matching=False,
        ),
        destination_params={"tool_evaluation_strategy": "local"},
        metadata_strategy="directory",
    )


def test_readiness_error_lists_all_remote_crypt4gh_requirements_for_transparent_inputs():
    crypt4gh_dataset = _Dataset(
        _DatasetMetadata(crypt4gh_header="header", expiration="2026-06-02T12:00:00+00:00"),
        ext="fastqsanger.c4gh",
    )
    input_association = _InputDatasetAssociation(name="input_data", dataset=crypt4gh_dataset)
    tool = _ReadinessTool(inputs={"input_data": _ReadinessToolInput(["fastqsanger"])})

    with pytest.raises(Crypt4GHRemoteExecutionError) as exc_info:
        assert_crypt4gh_job_readiness(
            job_io=_ReadinessJobIO([input_association]),
            tool=tool,
            app_config=_Config(
                enable_crypt4gh_remote_execution_staging=False,
                enable_crypt4gh_transparent_input_matching=False,
                crypt4gh_reencryption_service_url="",
            ),
            destination_params={"tool_evaluation_strategy": "local"},
            metadata_strategy="directory",
            reencryption_service_url="",
        )

    message = str(exc_info.value)
    assert "enable_crypt4gh_transparent_input_matching = true" in message
    assert "enable_crypt4gh_remote_execution_staging = true" in message
    assert "tool_evaluation_strategy = remote" in message
    assert "outputs_to_working_directory = true" in message
    assert "metadata_strategy = extended" in message
    assert "crypt4gh_reencryption_service_url" in message


def test_prepare_job_no_longer_exposes_legacy_staging_hook():
    assert not hasattr(BaseJobRunner, "_apply_crypt4gh_staging")


def test_cleanup_wrapper_runs_after_tool_failure_and_preserves_diagnostics(tmp_path):
    cleanup_marker = tmp_path / "cleanup-ran"
    tool_command = "python -c \"raise RuntimeError('ORIGINAL_TOOL_EXCEPTION')\""
    cleanup_command = (
        "python -c \"from pathlib import Path; "
        f"Path({str(cleanup_marker)!r}).write_text('yes'); "
        "raise RuntimeError('CLEANUP_EXCEPTION')\""
    )
    wrapped_command = build_crypt4gh_cleanup_wrapped_command(
        tool_command=tool_command,
        cleanup_command=cleanup_command,
    )

    completed = subprocess.run(["/bin/bash", "-c", wrapped_command], capture_output=True, text=True, check=False)

    assert completed.returncode == 1
    assert cleanup_marker.exists()
    assert "ORIGINAL_TOOL_EXCEPTION" in completed.stderr
    assert "CLEANUP_EXCEPTION" in completed.stderr
    assert CRYPT4GH_PLAINTEXT_CLEANUP_FAILED_MARKER in completed.stderr


def test_cleanup_wrapper_expands_cleanup_exit_code_in_marker_message():
    wrapped_command = build_crypt4gh_cleanup_wrapped_command(
        tool_command="python -c \"print('ok')\"",
        cleanup_command="python -c \"import sys; sys.exit(17)\"",
    )

    completed = subprocess.run(["/bin/bash", "-c", wrapped_command], capture_output=True, text=True, check=False)

    assert completed.returncode == 17
    assert f"{CRYPT4GH_PLAINTEXT_CLEANUP_FAILED_MARKER}: cleanup failed with exit code 17" in completed.stderr
    assert "${_CRYPT4GH_CLEANUP_EXIT}" not in completed.stderr


def test_cleanup_wrapper_reports_postrun_errors_without_cleanup_failure_marker(tmp_path):
    cleanup_marker = tmp_path / "cleanup-ran"
    wrapped_command = build_crypt4gh_cleanup_wrapped_command(
        tool_command="python -c \"print('ok')\"",
        postrun_command="python -c \"raise RuntimeError('POSTRUN_EXCEPTION')\"",
        cleanup_command=(
            "python -c \"from pathlib import Path; "
            f"Path({str(cleanup_marker)!r}).write_text('yes')\""
        ),
    )

    completed = subprocess.run(["/bin/bash", "-c", wrapped_command], capture_output=True, text=True, check=False)

    assert completed.returncode == 1
    assert cleanup_marker.exists()
    assert "POSTRUN_EXCEPTION" in completed.stderr
    assert CRYPT4GH_PLAINTEXT_CLEANUP_FAILED_MARKER not in completed.stderr


def test_local_minimum_ttl_gate_runs_before_any_recrypt_b_call(monkeypatch):
    dataset = _BuildDataset(
        dataset_id=1,
        metadata=_DatasetMetadata(
            crypt4gh_header="header",
            expiration="2000-01-01T00:00:00+00:00",
        ),
    )

    recrypt_attempted = False

    def _sentinel_prepare_plaintext_input_for_dataset(**_kwargs):
        nonlocal recrypt_attempted
        recrypt_attempted = True
        raise AssertionError("should not call recrypt path when minimum TTL gate fails")

    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._prepare_plaintext_input_for_dataset",
        _sentinel_prepare_plaintext_input_for_dataset,
    )

    with pytest.raises(Crypt4GHRemoteExecutionError, match="minimum TTL requirement before remote call"):
        build_crypt4gh_remote_compute_environment(
            job_io=_JobIO([dataset]),
            job=_BuildJob(),
            working_directory="/tmp",
            reencryption_service_url="http://example.invalid",
            minimum_ttl=timedelta(days=1),
            now=datetime.fromisoformat("2026-06-01T00:00:00+00:00"),
        )

    assert recrypt_attempted is False


def test_should_run_uses_destination_walltime_plus_one_hour_buffer():
    dataset = _Dataset(
        _DatasetMetadata(
            crypt4gh_header="header",
            expiration="2026-06-01T01:00:00+00:00",
        )
    )

    with pytest.raises(Crypt4GHRemoteExecutionError, match="minimum TTL requirement before remote call"):
        should_run_crypt4gh_remote_execution(
            job_io=_JobIO([dataset]),
            app_config=_Config(enable_crypt4gh_remote_execution_staging=True),
            destination_params={"tool_evaluation_strategy": "remote", "walltime": "00:15:00"},
            now=datetime.fromisoformat("2026-06-01T00:00:00+00:00"),
        )


def test_should_run_preserves_default_ttl_floor_when_walltime_is_short():
    dataset = _Dataset(
        _DatasetMetadata(
            crypt4gh_header="header",
            expiration="2026-06-01T03:00:00+00:00",
        )
    )

    with pytest.raises(Crypt4GHRemoteExecutionError, match="minimum TTL requirement before remote call"):
        should_run_crypt4gh_remote_execution(
            job_io=_JobIO([dataset]),
            app_config=_Config(enable_crypt4gh_remote_execution_staging=True),
            destination_params={"tool_evaluation_strategy": "remote", "walltime": "00:15:00"},
            now=datetime.fromisoformat("2026-06-01T00:00:00+00:00"),
        )


def test_should_run_falls_back_to_24h_when_walltime_is_unparseable():
    dataset = _Dataset(
        _DatasetMetadata(
            crypt4gh_header="header",
            expiration="2026-06-01T06:00:00+00:00",
        )
    )

    with pytest.raises(Crypt4GHRemoteExecutionError, match="minimum TTL requirement before remote call"):
        should_run_crypt4gh_remote_execution(
            job_io=_JobIO([dataset]),
            app_config=_Config(enable_crypt4gh_remote_execution_staging=True),
            destination_params={"tool_evaluation_strategy": "remote", "walltime": "invalid"},
            now=datetime.fromisoformat("2026-06-01T00:00:00+00:00"),
        )


def test_should_run_rejects_negative_destination_walltime_and_falls_back_to_default_minimum_ttl():
    dataset = _Dataset(
        _DatasetMetadata(
            crypt4gh_header="header",
            expiration="2026-06-01T03:00:00+00:00",
        )
    )

    with pytest.raises(Crypt4GHRemoteExecutionError, match="minimum TTL requirement before remote call"):
        should_run_crypt4gh_remote_execution(
            job_io=_JobIO([dataset]),
            app_config=_Config(enable_crypt4gh_remote_execution_staging=True),
            destination_params={"tool_evaluation_strategy": "remote", "walltime": "-01:30:00"},
            now=datetime.fromisoformat("2026-06-01T00:00:00+00:00"),
        )


def test_should_run_rejects_out_of_range_destination_walltime_and_falls_back_to_default_minimum_ttl():
    dataset = _Dataset(
        _DatasetMetadata(
            crypt4gh_header="header",
            expiration="2026-06-01T03:00:00+00:00",
        )
    )

    with pytest.raises(Crypt4GHRemoteExecutionError, match="minimum TTL requirement before remote call"):
        should_run_crypt4gh_remote_execution(
            job_io=_JobIO([dataset]),
            app_config=_Config(enable_crypt4gh_remote_execution_staging=True),
            destination_params={"tool_evaluation_strategy": "remote", "walltime": "01:99:00"},
            now=datetime.fromisoformat("2026-06-01T00:00:00+00:00"),
        )


def test_should_run_rejects_exact_default_ttl_boundary_to_avoid_race_window():
    dataset = _Dataset(
        _DatasetMetadata(
            crypt4gh_header="header",
            expiration="2026-06-02T00:00:00+00:00",
        )
    )

    with pytest.raises(Crypt4GHRemoteExecutionError, match="minimum TTL requirement before remote call"):
        should_run_crypt4gh_remote_execution(
            job_io=_JobIO([dataset]),
            app_config=_Config(enable_crypt4gh_remote_execution_staging=True),
            destination_params={"tool_evaluation_strategy": "remote"},
            now=datetime.fromisoformat("2026-06-01T00:00:00+00:00"),
        )


def test_should_run_rejects_exact_destination_derived_ttl_boundary_to_avoid_race_window():
    dataset = _Dataset(
        _DatasetMetadata(
            crypt4gh_header="header",
            expiration="2026-06-02T03:00:00+00:00",
        )
    )

    with pytest.raises(Crypt4GHRemoteExecutionError, match="minimum TTL requirement before remote call"):
        should_run_crypt4gh_remote_execution(
            job_io=_JobIO([dataset]),
            app_config=_Config(enable_crypt4gh_remote_execution_staging=True),
            destination_params={"tool_evaluation_strategy": "remote", "walltime": "26:00:00"},
            now=datetime.fromisoformat("2026-06-01T00:00:00+00:00"),
        )


def test_build_environment_uses_job_destination_walltime_before_any_recrypt_call(monkeypatch):
    dataset = _BuildDataset(
        dataset_id=1,
        metadata=_DatasetMetadata(
            crypt4gh_header="header",
            expiration="2026-06-01T00:30:00+00:00",
        ),
    )

    recrypt_attempted = False

    def _sentinel_prepare_plaintext_input_for_dataset(**_kwargs):
        nonlocal recrypt_attempted
        recrypt_attempted = True
        raise AssertionError("should not call recrypt path when derived minimum TTL gate fails")

    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._prepare_plaintext_input_for_dataset",
        _sentinel_prepare_plaintext_input_for_dataset,
    )

    with pytest.raises(Crypt4GHRemoteExecutionError, match="minimum TTL requirement before remote call"):
        build_crypt4gh_remote_compute_environment(
            job_io=_JobIO([dataset]),
            job=_BuildJob(destination_params={"walltime": "00:10:00"}),
            working_directory="/tmp",
            reencryption_service_url="http://example.invalid",
            now=datetime.fromisoformat("2026-06-01T00:00:00+00:00"),
        )

    assert recrypt_attempted is False


def test_build_environment_preserves_default_ttl_floor_when_walltime_is_short(monkeypatch):
    dataset = _BuildDataset(
        dataset_id=1,
        metadata=_DatasetMetadata(
            crypt4gh_header="header",
            expiration="2026-06-01T03:00:00+00:00",
        ),
    )

    recrypt_attempted = False

    def _sentinel_prepare_plaintext_input_for_dataset(**_kwargs):
        nonlocal recrypt_attempted
        recrypt_attempted = True
        raise AssertionError("should not call recrypt path when default minimum TTL floor fails")

    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._prepare_plaintext_input_for_dataset",
        _sentinel_prepare_plaintext_input_for_dataset,
    )

    with pytest.raises(Crypt4GHRemoteExecutionError, match="minimum TTL requirement before remote call"):
        build_crypt4gh_remote_compute_environment(
            job_io=_JobIO([dataset]),
            job=_BuildJob(destination_params={"walltime": "00:10:00"}),
            working_directory="/tmp",
            reencryption_service_url="http://example.invalid",
            now=datetime.fromisoformat("2026-06-01T00:00:00+00:00"),
        )

    assert recrypt_attempted is False


def test_recrypt_http_errors_do_not_expose_raw_response_text(monkeypatch):
    raw_response_text = "TOP-SECRET\n" + ("x" * 500)

    def _fake_post_reencryption_json(**_kwargs):
        return crypt4gh_remote_execution._ReencryptionHttpResponse(
            status_code=500,
            text=raw_response_text,
            json_payload=None,
        )

    monkeypatch.setattr(crypt4gh_remote_execution, "_post_reencryption_json", _fake_post_reencryption_json)

    with pytest.raises(Crypt4GHRemoteExecutionError) as exc_info:
        crypt4gh_remote_execution._recrypt_header_to_user_key(
            reencryption_service_url="http://example.invalid",
            crypt4gh_header="Zm9v",
            compute_keypair_id="compute-key",
        )

    message = str(exc_info.value)
    assert "Compute-side recryptor B returned HTTP 500" in message
    assert raw_response_text not in message


def test_localhost_https_uses_truststore_ssl_context_when_available(monkeypatch):
    class _FakeSSLContext:
        pass

    class _FakeTruststore:
        @staticmethod
        def SSLContext(*_args, **_kwargs):
            return _FakeSSLContext()

    ssl_context = crypt4gh_remote_execution._ssl_context_for_reencryption_url(
        reencryption_service_url="https://localhost:8443",
        truststore_module=_FakeTruststore(),
    )

    assert isinstance(ssl_context, _FakeSSLContext)


def test_non_dev_https_url_uses_default_ssl_handling(monkeypatch):
    class _FakeTruststore:
        @staticmethod
        def SSLContext():
            raise AssertionError("Should not be called for non-dev host")

    ssl_context = crypt4gh_remote_execution._ssl_context_for_reencryption_url(
        reencryption_service_url="https://reencryptor.example.org",
        truststore_module=_FakeTruststore(),
    )

    assert ssl_context is None


def test_dev_https_url_without_truststore_logs_warning_and_falls_back(caplog):
    with caplog.at_level("WARNING"):
        ssl_context = crypt4gh_remote_execution._ssl_context_for_reencryption_url(
            reencryption_service_url="https://reencryptor:8443",
            truststore_module=None,
        )

    assert ssl_context is None
    assert "truststore is unavailable" in caplog.text


def test_http_url_does_not_attempt_truststore_even_on_dev_host():
    class _FakeTruststore:
        @staticmethod
        def SSLContext():
            raise AssertionError("Should not be called for non-https URL")

    ssl_context = crypt4gh_remote_execution._ssl_context_for_reencryption_url(
        reencryption_service_url="http://localhost:8443",
        truststore_module=_FakeTruststore(),
    )

    assert ssl_context is None


def test_prepare_plaintext_inputs_batches_recrypt_calls_in_single_async_run(monkeypatch):
    datasets = [
        _BuildDataset(
            dataset_id=1,
            metadata=_DatasetMetadata(crypt4gh_header="header-1", expiration="2099-01-01T00:00:00+00:00"),
        ),
        _BuildDataset(
            dataset_id=2,
            metadata=_DatasetMetadata(crypt4gh_header="header-2", expiration="2099-01-01T00:00:00+00:00"),
        ),
    ]

    async_run_calls = []
    recrypt_calls = []

    def _fake_decrypt_recrypted_input(**kwargs):
        del kwargs

    monkeypatch.setattr(
        crypt4gh_remote_execution,
        "_decrypt_recrypted_input",
        _fake_decrypt_recrypted_input,
    )
    monkeypatch.setattr(
        crypt4gh_remote_execution,
        "_prepare_plaintext_input_for_dataset",
        lambda **kwargs: (cast(int, kwargs["dataset"].dataset.id), f"/tmp/crypt-inputs/ds_{kwargs['dataset'].dataset.id}/plaintext"),
    )

    def _fake_build_recrypt_payloads(*, datasets, job_public_key):
        del job_public_key
        payloads = []
        for dataset in datasets:
            payloads.append(
                {
                    "dataset": dataset,
                    "dataset_id": cast(int, dataset.dataset.id),
                    "source_header": cast(str, dataset.metadata.crypt4gh_header),
                    "compute_keypair_id": "compute-key",
                    "request_payload": {
                        "crypt4gh_header": cast(str, dataset.metadata.crypt4gh_header),
                        "crypt4gh_compute_keypair_id": "compute-key",
                        "crypt4gh_job_public_key": "job-public",
                    },
                }
            )
        return payloads

    def _fake_post_many_reencryption_json(**kwargs):
        recrypt_calls.append(kwargs)
        return [
            crypt4gh_remote_execution._ReencryptionHttpResponse(
                status_code=200,
                text="",
                json_payload={
                    "crypt4gh_header": "recrypted-header-1",
                    "crypt4gh_compute_public_key": "compute-pub",
                    "crypt4gh_compute_keypair_id": "compute-key-id",
                    "crypt4gh_compute_keypair_expiration_date": "2099-01-01T00:00:00+00:00",
                },
            ),
            crypt4gh_remote_execution._ReencryptionHttpResponse(
                status_code=200,
                text="",
                json_payload={
                    "crypt4gh_header": "recrypted-header-2",
                    "crypt4gh_compute_public_key": "compute-pub",
                    "crypt4gh_compute_keypair_id": "compute-key-id",
                    "crypt4gh_compute_keypair_expiration_date": "2099-01-01T00:00:00+00:00",
                },
            ),
        ]

    monkeypatch.setattr(
        crypt4gh_remote_execution,
        "_build_recrypt_payloads",
        _fake_build_recrypt_payloads,
    )
    monkeypatch.setattr(
        crypt4gh_remote_execution,
        "_post_many_reencryption_json",
        _fake_post_many_reencryption_json,
    )

    input_path_overrides_by_dataset_id, compute_context = crypt4gh_remote_execution._prepare_plaintext_inputs(
        datasets=datasets,
        crypt_inputs_workspace=Path("/tmp/crypt-inputs"),
        reencryption_service_url="https://localhost:8443",
        job_public_key="job-public",
        job_private_key=b"job-private",
    )

    assert len(async_run_calls) == 0
    assert len(recrypt_calls) == 1
    assert set(input_path_overrides_by_dataset_id.keys()) == {1, 2}
    assert compute_context.public_key == "compute-pub"


def test_prepare_plaintext_inputs_detects_mismatched_batch_response_count(monkeypatch):
    datasets = [
        _BuildDataset(
            dataset_id=1,
            metadata=_DatasetMetadata(crypt4gh_header="header-1", expiration="2099-01-01T00:00:00+00:00"),
        ),
        _BuildDataset(
            dataset_id=2,
            metadata=_DatasetMetadata(crypt4gh_header="header-2", expiration="2099-01-01T00:00:00+00:00"),
        ),
    ]

    def _fake_build_recrypt_payloads(*, datasets, job_public_key):
        del job_public_key
        return [
            {
                "dataset": dataset,
                "dataset_id": cast(int, dataset.dataset.id),
                "source_header": cast(str, dataset.metadata.crypt4gh_header),
                "compute_keypair_id": "compute-key",
                "request_payload": {
                    "crypt4gh_header": cast(str, dataset.metadata.crypt4gh_header),
                    "crypt4gh_compute_keypair_id": "compute-key",
                    "crypt4gh_job_public_key": "job-public",
                },
            }
            for dataset in datasets
        ]

    def _fake_post_many_reencryption_json(**kwargs):
        del kwargs
        return [
            crypt4gh_remote_execution._ReencryptionHttpResponse(
                status_code=200,
                text="",
                json_payload={
                    "crypt4gh_header": "recrypted-header-1",
                    "crypt4gh_compute_public_key": "compute-pub",
                    "crypt4gh_compute_keypair_id": "compute-key-id",
                    "crypt4gh_compute_keypair_expiration_date": "2099-01-01T00:00:00+00:00",
                },
            )
        ]

    monkeypatch.setattr(
        crypt4gh_remote_execution,
        "_build_recrypt_payloads",
        _fake_build_recrypt_payloads,
    )
    monkeypatch.setattr(
        crypt4gh_remote_execution,
        "_post_many_reencryption_json",
        _fake_post_many_reencryption_json,
    )

    with pytest.raises(Crypt4GHRemoteExecutionError, match="one response per dataset"):
        crypt4gh_remote_execution._prepare_plaintext_inputs(
            datasets=datasets,
            crypt_inputs_workspace=Path("/tmp/crypt-inputs"),
            reencryption_service_url="https://localhost:8443",
            job_public_key="job-public",
            job_private_key=b"job-private",
        )


def test_finalize_declared_outputs_rejects_legacy_discovered_selector_targets(tmp_path):
    with pytest.raises(Crypt4GHRemoteExecutionError, match="explicit output_path"):
        finalize_declared_crypt4gh_outputs(
            output_targets=[
                {
                    "discover_pattern": r".+\\.txt",
                    "discover_directory": str(tmp_path / "discover"),
                    "encrypted_ext": "txt.c4gh",
                }
            ],
            reencryption_service_url="http://example.invalid",
            compute_public_key="unused",
            compute_keypair_id="unused",
        )


def test_collect_declared_targets_includes_new_primary_discovered_outputs(tmp_path):
    class _OutputDataset:
        def __init__(self):
            self.dataset = _DatasetWrapper(dataset_id=4)
            self.ext = "tabular"

    class _DatasetPath:
        def __init__(self, path: str):
            self.false_path = path
            self.real_path = path

    class _OutputJobIO:
        def __init__(self, output_path: str):
            self._outputs = {
                "__new_primary_file_sample|sample1__": (
                    _OutputDataset(),
                    _DatasetPath(output_path),
                )
            }

        def get_output_hdas_and_fnames(self):
            return self._outputs

    class _DatatypesRegistry:
        def get_datatype_by_extension(self, ext):
            if ext == "tabular.c4gh":
                return object()
            return None

        def get_or_create_crypt4gh_datatype(self, ext):
            if ext == "tabular":
                return object()
            return None

    output_path = tmp_path / "dataset_4.dat"
    output_path.write_text("sample\n")

    class _ToolOutput:
        format = "tabular"
        from_work_dir = None

    targets = collect_declared_crypt4gh_output_targets(
        job_io=_OutputJobIO(str(output_path)),
        tool_outputs={"sample": _ToolOutput()},
        datatypes_registry=_DatatypesRegistry(),
        working_directory=str(tmp_path),
    )

    assert targets == []


def test_collect_declared_targets_fails_closed_when_tool_output_lookup_is_missing(tmp_path):
    class _OutputDataset:
        def __init__(self):
            self.dataset = _DatasetWrapper(dataset_id=4)
            self.ext = "tabular"

    class _DatasetPath:
        def __init__(self, path: str):
            self.false_path = path
            self.real_path = path

    class _OutputJobIO:
        def __init__(self, output_path: str):
            self._outputs = {
                "sample": (
                    _OutputDataset(),
                    _DatasetPath(output_path),
                )
            }

        def get_output_hdas_and_fnames(self):
            return self._outputs

    class _DatatypesRegistry:
        def get_datatype_by_extension(self, _ext):
            return object()

        def get_or_create_crypt4gh_datatype(self, _ext):
            return object()

    output_path = tmp_path / "dataset_4.dat"
    output_path.write_text("sample\n")

    with pytest.raises(Crypt4GHRemoteExecutionError, match="no matching tool output"):
        collect_declared_crypt4gh_output_targets(
            job_io=_OutputJobIO(str(output_path)),
            tool_outputs={},
            datatypes_registry=_DatatypesRegistry(),
            working_directory=str(tmp_path),
        )


def test_collect_declared_targets_fails_closed_when_dataset_id_is_missing(tmp_path):
    class _OutputDataset:
        def __init__(self):
            self.dataset = _DatasetWrapper(dataset_id=None)
            self.ext = "tabular"

    class _DatasetPath:
        def __init__(self, path: str):
            self.false_path = path
            self.real_path = path

    class _OutputJobIO:
        def __init__(self, output_path: str):
            self._outputs = {
                "sample": (
                    _OutputDataset(),
                    _DatasetPath(output_path),
                )
            }

        def get_output_hdas_and_fnames(self):
            return self._outputs

    class _DatatypesRegistry:
        def get_datatype_by_extension(self, _ext):
            return object()

        def get_or_create_crypt4gh_datatype(self, _ext):
            return object()

    output_path = tmp_path / "dataset_4.dat"
    output_path.write_text("sample\n")

    class _ToolOutput:
        format = "tabular"
        from_work_dir = None

    with pytest.raises(Crypt4GHRemoteExecutionError, match="missing a persisted dataset id"):
        collect_declared_crypt4gh_output_targets(
            job_io=_OutputJobIO(str(output_path)),
            tool_outputs={"sample": _ToolOutput()},
            datatypes_registry=_DatatypesRegistry(),
            working_directory=str(tmp_path),
        )


def test_collect_declared_targets_fails_closed_when_base_extension_cannot_be_resolved(tmp_path):
    class _OutputDataset:
        def __init__(self):
            self.dataset = _DatasetWrapper(dataset_id=4)
            self.ext = "auto"

    class _DatasetPath:
        def __init__(self, path: str):
            self.false_path = path
            self.real_path = path

    class _OutputJobIO:
        def __init__(self, output_path: str):
            self._outputs = {
                "sample": (
                    _OutputDataset(),
                    _DatasetPath(output_path),
                )
            }

        def get_output_hdas_and_fnames(self):
            return self._outputs

    class _DatatypesRegistry:
        def get_datatype_by_extension(self, _ext):
            return object()

        def get_or_create_crypt4gh_datatype(self, _ext):
            return object()

    output_path = tmp_path / "dataset_4.dat"
    output_path.write_text("sample\n")

    class _ToolOutput:
        format = "input"
        from_work_dir = None

    with pytest.raises(Crypt4GHRemoteExecutionError, match="could not resolve encrypted output extension"):
        collect_declared_crypt4gh_output_targets(
            job_io=_OutputJobIO(str(output_path)),
            tool_outputs={"sample": _ToolOutput()},
            datatypes_registry=_DatatypesRegistry(),
            working_directory=str(tmp_path),
        )


def test_collect_declared_targets_ignores_legacy_discovered_collectors(tmp_path):
    class _OutputDataset:
        def __init__(self):
            self.dataset = _DatasetWrapper(dataset_id=4)
            self.ext = "tabular"

    class _DatasetPath:
        def __init__(self, path: str):
            self.false_path = path
            self.real_path = path

    class _OutputJobIO:
        def __init__(self, output_path: str):
            self._outputs = {
                "sample": (
                    _OutputDataset(),
                    _DatasetPath(output_path),
                )
            }

        def get_output_hdas_and_fnames(self):
            return self._outputs

    class _DatatypesRegistry:
        def get_datatype_by_extension(self, _ext):
            return object()

        def get_or_create_crypt4gh_datatype(self, _ext):
            return object()

    class _Collector:
        discover_via = "tool_provided_metadata"
        directory = "outputs"
        pattern = r".*"
        assign_primary_output = False

    output_path = tmp_path / "dataset_4.dat"
    output_path.write_text("sample\n")

    class _ToolOutput:
        format = "tabular"
        from_work_dir = None
        dataset_collector_descriptions = [_Collector()]

    targets = collect_declared_crypt4gh_output_targets(
        job_io=_OutputJobIO(str(output_path)),
        tool_outputs={"sample": _ToolOutput()},
        datatypes_registry=_DatatypesRegistry(),
        working_directory=str(tmp_path),
    )

    assert len(targets) == 1
    assert "discover_pattern" not in targets[0]


def test_collect_declared_targets_skips_discovery_container_outputs_without_resolved_extension(tmp_path):
    class _OutputDataset:
        def __init__(self):
            self.dataset = _DatasetWrapper(dataset_id=4)
            self.ext = "auto"

    class _DatasetPath:
        def __init__(self, path: str):
            self.false_path = path
            self.real_path = path

    class _OutputJobIO:
        def __init__(self, output_path: str):
            self._outputs = {
                "sample": (
                    _OutputDataset(),
                    _DatasetPath(output_path),
                )
            }

        def get_output_hdas_and_fnames(self):
            return self._outputs

    class _DatatypesRegistry:
        def get_datatype_by_extension(self, _ext):
            return object()

        def get_or_create_crypt4gh_datatype(self, _ext):
            return object()

    class _Collector:
        discover_via = "tool_provided_metadata"
        directory = "outputs"
        pattern = r".*"
        assign_primary_output = False

    class _ToolOutput:
        format = "input"
        from_work_dir = None
        dataset_collector_descriptions = [_Collector()]

    output_path = tmp_path / "dataset_4.dat"
    output_path.write_text("sample\n")

    targets = collect_declared_crypt4gh_output_targets(
        job_io=_OutputJobIO(str(output_path)),
        tool_outputs={"sample": _ToolOutput()},
        datatypes_registry=_DatatypesRegistry(),
        working_directory=str(tmp_path),
    )

    assert targets == []


def test_collect_declared_targets_prefers_false_path_and_tracks_real_path(tmp_path):
    class _OutputDataset:
        def __init__(self):
            self.dataset = _DatasetWrapper(dataset_id=42)
            self.ext = "tabular"

    class _DatasetPath:
        def __init__(self, false_path: str, real_path: str):
            self.false_path = false_path
            self.real_path = real_path

    class _OutputJobIO:
        def __init__(self, false_path: str, real_path: str):
            self._outputs = {
                "sample": (
                    _OutputDataset(),
                    _DatasetPath(false_path, real_path),
                )
            }

        def get_output_hdas_and_fnames(self):
            return self._outputs

    class _DatatypesRegistry:
        def get_datatype_by_extension(self, _ext):
            return object()

        def get_or_create_crypt4gh_datatype(self, _ext):
            return object()

    false_path = tmp_path / "working" / "dataset_42.dat"
    false_path.parent.mkdir(parents=True, exist_ok=True)
    false_path.write_text("sample\n")
    real_path = tmp_path / "object_store" / "dataset_42.dat"
    real_path.parent.mkdir(parents=True, exist_ok=True)
    real_path.write_text("sample\n")

    class _ToolOutput:
        format = "tabular"
        from_work_dir = None

    targets = collect_declared_crypt4gh_output_targets(
        job_io=_OutputJobIO(str(false_path), str(real_path)),
        tool_outputs={"sample": _ToolOutput()},
        datatypes_registry=_DatatypesRegistry(),
        working_directory=str(tmp_path),
    )

    assert len(targets) == 1
    assert targets[0]["output_path"] == str(false_path)
    assert targets[0]["dataset_output_path"] == str(real_path)
    assert targets[0]["allowed_root_paths"] == [
        str(tmp_path.resolve()),
        str(real_path.parent.resolve()),
    ]


def test_collect_declared_targets_does_not_emit_debug_resolution_payload_by_default(tmp_path, capsys):
    class _OutputDataset:
        def __init__(self):
            self.dataset = _DatasetWrapper(dataset_id=42)
            self.ext = "tabular"

    class _DatasetPath:
        def __init__(self, false_path: str, real_path: str):
            self.false_path = false_path
            self.real_path = real_path

    class _OutputJobIO:
        def __init__(self, false_path: str, real_path: str):
            self._outputs = {
                "sample": (
                    _OutputDataset(),
                    _DatasetPath(false_path, real_path),
                )
            }

        def get_output_hdas_and_fnames(self):
            return self._outputs

    class _DatatypesRegistry:
        def get_datatype_by_extension(self, _ext):
            return object()

        def get_or_create_crypt4gh_datatype(self, _ext):
            return object()

    false_path = tmp_path / "working" / "dataset_42.dat"
    false_path.parent.mkdir(parents=True, exist_ok=True)
    false_path.write_text("sample\n")
    real_path = tmp_path / "object_store" / "dataset_42.dat"
    real_path.parent.mkdir(parents=True, exist_ok=True)
    real_path.write_text("sample\n")

    class _ToolOutput:
        format = "tabular"
        from_work_dir = None

    collect_declared_crypt4gh_output_targets(
        job_io=_OutputJobIO(str(false_path), str(real_path)),
        tool_outputs={"sample": _ToolOutput()},
        datatypes_registry=_DatatypesRegistry(),
        working_directory=str(tmp_path),
    )

    captured = capsys.readouterr()
    debug_lines = [line for line in captured.out.splitlines() if line.startswith("CRYPT4GH_DEBUG ")]
    assert debug_lines == []


def test_collect_declared_targets_emits_debug_resolution_payload_when_opted_in(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("GALAXY_CRYPT4GH_DEBUG", "1")

    class _OutputDataset:
        def __init__(self):
            self.dataset = _DatasetWrapper(dataset_id=42)
            self.ext = "tabular"

    class _DatasetPath:
        def __init__(self, false_path: str, real_path: str):
            self.false_path = false_path
            self.real_path = real_path

    class _OutputJobIO:
        def __init__(self, false_path: str, real_path: str):
            self._outputs = {
                "sample": (
                    _OutputDataset(),
                    _DatasetPath(false_path, real_path),
                )
            }

        def get_output_hdas_and_fnames(self):
            return self._outputs

    class _DatatypesRegistry:
        def get_datatype_by_extension(self, _ext):
            return object()

        def get_or_create_crypt4gh_datatype(self, _ext):
            return object()

    false_path = tmp_path / "working" / "dataset_42.dat"
    false_path.parent.mkdir(parents=True, exist_ok=True)
    false_path.write_text("sample\n")
    real_path = tmp_path / "object_store" / "dataset_42.dat"
    real_path.parent.mkdir(parents=True, exist_ok=True)
    real_path.write_text("sample\n")

    class _ToolOutput:
        format = "tabular"
        from_work_dir = None

    collect_declared_crypt4gh_output_targets(
        job_io=_OutputJobIO(str(false_path), str(real_path)),
        tool_outputs={"sample": _ToolOutput()},
        datatypes_registry=_DatatypesRegistry(),
        working_directory=str(tmp_path),
    )

    captured = capsys.readouterr()
    debug_lines = [line for line in captured.out.splitlines() if line.startswith("CRYPT4GH_DEBUG ")]
    assert len(debug_lines) == 1

    debug_payload = json.loads(debug_lines[0].split(" ", 1)[1])
    assert debug_payload["event"] == "crypt4gh_declared_output_target"
    assert debug_payload["output_name"] == "sample"
    assert debug_payload["tool_output_name"] == "sample"
    assert debug_payload["output_path"] == str(false_path)
    assert debug_payload["output_path_source"] == "false_path"
    assert debug_payload["false_path"] == str(false_path)
    assert debug_payload["real_path"] == str(real_path)


def test_collect_declared_targets_allows_dataset_output_path_parent_for_containment(tmp_path):
    class _OutputDataset:
        def __init__(self):
            self.dataset = _DatasetWrapper(dataset_id=7)
            self.ext = "tabular"

    class _DatasetPath:
        def __init__(self, false_path: str, real_path: str):
            self.false_path = false_path
            self.real_path = real_path

    class _OutputJobIO:
        def __init__(self, false_path: str, real_path: str):
            self._outputs = {
                "sample": (
                    _OutputDataset(),
                    _DatasetPath(false_path, real_path),
                )
            }

        def get_output_hdas_and_fnames(self):
            return self._outputs

    class _DatatypesRegistry:
        def get_datatype_by_extension(self, _ext):
            return object()

        def get_or_create_crypt4gh_datatype(self, _ext):
            return object()

    false_path = tmp_path / "jobs_directory" / "000" / "35" / "outputs" / "dataset_uuid.dat"
    false_path.parent.mkdir(parents=True, exist_ok=True)
    false_path.write_text("sample\n")

    real_path = tmp_path / "objects" / "d" / "0" / "a" / "dataset_uuid.dat"
    real_path.parent.mkdir(parents=True, exist_ok=True)
    real_path.write_text("sample\n")

    class _ToolOutput:
        format = "tabular"
        from_work_dir = None

    targets = collect_declared_crypt4gh_output_targets(
        job_io=_OutputJobIO(str(false_path), str(real_path)),
        tool_outputs={"sample": _ToolOutput()},
        datatypes_registry=_DatatypesRegistry(),
        working_directory=str(tmp_path / "jobs_directory" / "000" / "35"),
    )

    assert len(targets) == 1
    assert targets[0]["output_path"] == str(false_path)
    assert targets[0]["dataset_output_path"] == str(real_path)
    assert str((tmp_path / "jobs_directory" / "000" / "35").resolve()) in targets[0]["allowed_root_paths"]
    assert str(real_path.parent.resolve()) in targets[0]["allowed_root_paths"]


def test_collect_declared_targets_fails_when_plaintext_output_path_resolves_outside_working_directory(tmp_path):
    class _OutputDataset:
        def __init__(self):
            self.dataset = _DatasetWrapper(dataset_id=77)
            self.ext = "tabular"

    class _DatasetPath:
        def __init__(self, *, false_path, real_path: str):
            self.false_path = false_path
            self.real_path = real_path

    class _OutputJobIO:
        def __init__(self, *, false_path, real_path: str):
            self._outputs = {
                "sample": (
                    _OutputDataset(),
                    _DatasetPath(false_path=false_path, real_path=real_path),
                )
            }

        def get_output_hdas_and_fnames(self):
            return self._outputs

    class _DatatypesRegistry:
        def get_datatype_by_extension(self, _ext):
            return object()

        def get_or_create_crypt4gh_datatype(self, _ext):
            return object()

    working_directory = tmp_path / "jobs_directory" / "000" / "35"
    working_directory.mkdir(parents=True, exist_ok=True)

    real_path = tmp_path / "objects" / "d" / "0" / "a" / "dataset_uuid.dat"
    real_path.parent.mkdir(parents=True, exist_ok=True)
    real_path.write_text("sample\n")

    class _ToolOutput:
        format = "tabular"
        from_work_dir = None

    with pytest.raises(Crypt4GHRemoteExecutionError, match="outside job working directory"):
        collect_declared_crypt4gh_output_targets(
            job_io=_OutputJobIO(false_path=None, real_path=str(real_path)),
            tool_outputs={"sample": _ToolOutput()},
            datatypes_registry=_DatatypesRegistry(),
            working_directory=str(working_directory),
        )


def test_collect_declared_targets_does_not_log_extensions_as_warnings(tmp_path, caplog):
    class _OutputDataset:
        def __init__(self):
            self.dataset = _DatasetWrapper(dataset_id=4)
            self.ext = "tabular"

    class _DatasetPath:
        def __init__(self, path: str):
            self.false_path = path
            self.real_path = path

    class _OutputJobIO:
        def __init__(self, output_path: str):
            self._outputs = {
                "sample": (
                    _OutputDataset(),
                    _DatasetPath(output_path),
                )
            }

        def get_output_hdas_and_fnames(self):
            return self._outputs

    class _DatatypesRegistry:
        def get_datatype_by_extension(self, _ext):
            return object()

        def get_or_create_crypt4gh_datatype(self, _ext):
            return object()

    class _ToolOutput:
        format = "tabular"
        from_work_dir = None

    output_path = tmp_path / "dataset_4.dat"
    output_path.write_text("sample\n")

    with caplog.at_level("WARNING", logger=crypt4gh_remote_execution.__name__):
        targets = collect_declared_crypt4gh_output_targets(
            job_io=_OutputJobIO(str(output_path)),
            tool_outputs={"sample": _ToolOutput()},
            datatypes_registry=_DatatypesRegistry(),
            working_directory=str(tmp_path),
        )

    assert len(targets) == 1
    warning_records = [
        record
        for record in caplog.records
        if record.name == crypt4gh_remote_execution.__name__ and record.levelname == "WARNING"
    ]
    assert warning_records == []


def test_finalize_declared_outputs_fails_closed_when_declared_output_path_is_missing(tmp_path):
    output_path = tmp_path / "missing.dat"

    with pytest.raises(Crypt4GHRemoteExecutionError, match="does not exist"):
        finalize_declared_crypt4gh_outputs(
            output_targets=[
                {
                    "output_path": str(output_path),
                    "plaintext_path": str(tmp_path / "plaintext"),
                    "encrypted_marker_path": str(tmp_path / "marker.encrypted"),
                    "encrypted_ext": "tabular.c4gh",
                }
            ],
            reencryption_service_url="http://example.invalid",
            compute_public_key="unused",
            compute_keypair_id="unused",
        )


def test_finalize_declared_outputs_deletes_plaintext_output_when_encryption_fails(tmp_path, monkeypatch):
    output_path = tmp_path / "dataset_4.dat"
    output_path.write_text("plain\n")

    def _fail_encrypt(*, plaintext_path, compute_encrypted_path, compute_public_key):
        del plaintext_path
        del compute_encrypted_path
        del compute_public_key
        raise RuntimeError("encrypt failed")

    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._encrypt_plaintext_to_compute_key",
        _fail_encrypt,
    )

    with pytest.raises(Crypt4GHRemoteExecutionError, match="Failed to finalize encrypted Crypt4GH output"):
        finalize_declared_crypt4gh_outputs(
            output_targets=[
                {
                    "output_path": str(output_path),
                    "plaintext_path": str(tmp_path / "plaintext"),
                    "encrypted_marker_path": str(tmp_path / "marker.encrypted"),
                    "encrypted_ext": "tabular.c4gh",
                }
            ],
            reencryption_service_url="http://example.invalid",
            compute_public_key="unused",
            compute_keypair_id="unused",
        )

    assert not output_path.exists()


def test_collect_declared_targets_marks_outputs_for_compute_keypair_clearance(tmp_path):
    class _OutputDataset:
        def __init__(self):
            self.dataset = _DatasetWrapper(dataset_id=4)
            self.ext = "tabular"

    class _DatasetPath:
        def __init__(self, path: str):
            self.false_path = path
            self.real_path = path

    class _OutputJobIO:
        def __init__(self, output_path: str):
            self._outputs = {
                "sample": (
                    _OutputDataset(),
                    _DatasetPath(output_path),
                )
            }

        def get_output_hdas_and_fnames(self):
            return self._outputs

    class _DatatypesRegistry:
        def get_datatype_by_extension(self, _ext):
            return object()

        def get_or_create_crypt4gh_datatype(self, _ext):
            return object()

    output_path = tmp_path / "dataset_4.dat"
    output_path.write_text("sample\n")

    class _ToolOutput:
        format = "tabular"
        from_work_dir = None

    targets = collect_declared_crypt4gh_output_targets(
        job_io=_OutputJobIO(str(output_path)),
        tool_outputs={"sample": _ToolOutput()},
        datatypes_registry=_DatatypesRegistry(),
        working_directory=str(tmp_path),
    )

    assert targets[0]["clear_compute_keypair"] is True


def test_collect_declared_targets_records_output_association_name(tmp_path):
    class _OutputDataset:
        def __init__(self):
            self.dataset = _DatasetWrapper(dataset_id=9)
            self.ext = "tabular"

    class _DatasetPath:
        def __init__(self, path: str):
            self.false_path = path
            self.real_path = path

    class _OutputJobIO:
        def __init__(self, output_path: str):
            self._outputs = {
                "sample": (
                    _OutputDataset(),
                    _DatasetPath(output_path),
                )
            }

        def get_output_hdas_and_fnames(self):
            return self._outputs

    class _DatatypesRegistry:
        def get_datatype_by_extension(self, _ext):
            return object()

        def get_or_create_crypt4gh_datatype(self, _ext):
            return object()

    class _ToolOutput:
        format = "tabular"
        from_work_dir = None

    output_path = tmp_path / "dataset_9.dat"
    output_path.write_text("sample\n")

    targets = collect_declared_crypt4gh_output_targets(
        job_io=_OutputJobIO(str(output_path)),
        tool_outputs={"sample": _ToolOutput()},
        datatypes_registry=_DatatypesRegistry(),
        working_directory=str(tmp_path),
    )

    assert len(targets) == 1
    assert targets[0]["association_name"] == "sample"


def test_collect_declared_targets_include_extra_files_manifest_for_dataset(tmp_path):
    class _OutputDataset:
        def __init__(self):
            self.dataset = _DatasetWrapper(dataset_id=15)
            self.ext = "tabular"

    class _DatasetPath:
        def __init__(self, path: str, extra_files_path: str):
            self.false_path = path
            self.real_path = path
            self.false_extra_files_path = extra_files_path

    class _OutputJobIO:
        def __init__(self, output_path: str, extra_files_path: str):
            self._outputs = {
                "sample": (
                    _OutputDataset(),
                    _DatasetPath(output_path, extra_files_path),
                )
            }

        def get_output_hdas_and_fnames(self):
            return self._outputs

    class _DatatypesRegistry:
        def get_datatype_by_extension(self, _ext):
            return object()

        def get_or_create_crypt4gh_datatype(self, _ext):
            return object()

    class _ToolOutput:
        format = "tabular"
        from_work_dir = None

    output_path = tmp_path / "dataset_15.dat"
    output_path.write_text("sample\n")
    extra_files_path = tmp_path / "dataset_15_files"
    extra_files_path.mkdir(parents=True)

    targets = collect_declared_crypt4gh_output_targets(
        job_io=_OutputJobIO(str(output_path), str(extra_files_path)),
        tool_outputs={"sample": _ToolOutput()},
        datatypes_registry=_DatatypesRegistry(),
        working_directory=str(tmp_path),
    )

    assert len(targets) == 1
    assert targets[0]["extra_files_output_path"] == str(extra_files_path)
    assert targets[0]["extra_files_manifest_path"].endswith("_c4gh_stage/outputs/ds_15.extra_files_manifest.json")


def test_finalize_declared_outputs_encrypts_extra_files_and_writes_manifest(tmp_path, monkeypatch):
    output_path = tmp_path / "working" / "dataset_22.dat"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("plain\n")

    extra_files_root = tmp_path / "working" / "dataset_22_files"
    (extra_files_root / "nested").mkdir(parents=True, exist_ok=True)
    (extra_files_root / "foo.txt").write_text("foo\n")
    (extra_files_root / "nested" / "bar.txt").write_text("bar\n")

    marker_dir = tmp_path / "_c4gh_stage" / "outputs"
    manifest_path = marker_dir / "ds_22.extra_files_manifest.json"

    def _fake_encrypt_plaintext_to_compute_key(*, plaintext_path, compute_encrypted_path, compute_public_key):
        del compute_public_key
        payload = Path(plaintext_path).read_bytes()
        Path(compute_encrypted_path).write_bytes(b"crypt4gh" + payload)

    def _fake_rewrite_output_header_to_user_key(
        *,
        compute_encrypted_path,
        final_output_tmp_path,
        reencryption_service_url,
        compute_keypair_id,
    ):
        del reencryption_service_url
        del compute_keypair_id
        Path(final_output_tmp_path).write_bytes(Path(compute_encrypted_path).read_bytes())

    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._encrypt_plaintext_to_compute_key",
        _fake_encrypt_plaintext_to_compute_key,
    )
    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._rewrite_output_header_to_user_key",
        _fake_rewrite_output_header_to_user_key,
    )

    finalize_declared_crypt4gh_outputs(
        output_targets=[
            {
                "output_path": str(output_path),
                "plaintext_path": str(tmp_path / "_crypt" / "outputs" / "ds_22" / "plaintext"),
                "encrypted_marker_path": str(marker_dir / "ds_22.encrypted"),
                "encrypted_ext": "tabular.c4gh",
                "extra_files_output_path": str(extra_files_root),
                "extra_files_manifest_path": str(manifest_path),
            }
        ],
        reencryption_service_url="http://example.invalid",
        compute_public_key="unused",
        compute_keypair_id="unused",
    )

    with output_path.open("rb") as output_stream:
        assert output_stream.read(8) == b"crypt4gh"

    with (extra_files_root / "foo.txt").open("rb") as extra_stream:
        assert extra_stream.read(8) == b"crypt4gh"
    with (extra_files_root / "nested" / "bar.txt").open("rb") as extra_stream:
        assert extra_stream.read(8) == b"crypt4gh"

    manifest_payload = json.loads(manifest_path.read_text())
    assert sorted(manifest_payload["files"].keys()) == ["foo.txt", "nested/bar.txt"]


def test_finalize_declared_outputs_fail_closed_when_extra_files_manifest_missing_entries(tmp_path, monkeypatch):
    output_path = tmp_path / "working" / "dataset_23.dat"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("plain\n")

    extra_files_root = tmp_path / "working" / "dataset_23_files"
    extra_files_root.mkdir(parents=True, exist_ok=True)
    (extra_files_root / "foo.txt").write_text("foo\n")

    marker_dir = tmp_path / "_c4gh_stage" / "outputs"
    manifest_path = marker_dir / "ds_23.extra_files_manifest.json"

    def _fake_encrypt_plaintext_to_compute_key(*, plaintext_path, compute_encrypted_path, compute_public_key):
        del compute_public_key
        payload = Path(plaintext_path).read_bytes()
        Path(compute_encrypted_path).write_bytes(b"crypt4gh" + payload)

    def _fake_rewrite_output_header_to_user_key(
        *,
        compute_encrypted_path,
        final_output_tmp_path,
        reencryption_service_url,
        compute_keypair_id,
    ):
        del reencryption_service_url
        del compute_keypair_id
        Path(final_output_tmp_path).write_bytes(Path(compute_encrypted_path).read_bytes())

    def _drop_manifest_entry(*, manifest_path, relative_path, encrypted_ext):
        del relative_path
        del encrypted_ext
        Path(manifest_path).parent.mkdir(parents=True, exist_ok=True)
        Path(manifest_path).write_text(json.dumps({"files": {}}))

    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._encrypt_plaintext_to_compute_key",
        _fake_encrypt_plaintext_to_compute_key,
    )
    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._rewrite_output_header_to_user_key",
        _fake_rewrite_output_header_to_user_key,
    )
    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._write_extra_files_manifest_entry",
        _drop_manifest_entry,
    )

    with pytest.raises(Crypt4GHRemoteExecutionError, match="extra_files manifest missing entries"):
        finalize_declared_crypt4gh_outputs(
            output_targets=[
                {
                    "output_path": str(output_path),
                    "plaintext_path": str(tmp_path / "_crypt" / "outputs" / "ds_23" / "plaintext"),
                    "encrypted_marker_path": str(marker_dir / "ds_23.encrypted"),
                    "encrypted_ext": "tabular.c4gh",
                    "extra_files_output_path": str(extra_files_root),
                    "extra_files_manifest_path": str(manifest_path),
                }
            ],
            reencryption_service_url="http://example.invalid",
            compute_public_key="unused",
            compute_keypair_id="unused",
        )


def test_finalize_declared_outputs_rejects_extra_files_symlink_traversal(tmp_path, monkeypatch):
    working_root = tmp_path / "working"

    output_path = working_root / "dataset_24.dat"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("plain\n")

    outside_secret = tmp_path / "outside" / "secret.txt"
    outside_secret.parent.mkdir(parents=True, exist_ok=True)
    outside_secret.write_text("outside-secret\n")

    extra_files_root = working_root / "dataset_24_files"
    extra_files_root.mkdir(parents=True, exist_ok=True)
    (extra_files_root / "linked.txt").symlink_to(outside_secret)

    marker_dir = working_root / "_c4gh_stage" / "outputs"
    manifest_path = marker_dir / "ds_24.extra_files_manifest.json"

    def _fake_encrypt_plaintext_to_compute_key(*, plaintext_path, compute_encrypted_path, compute_public_key):
        del compute_public_key
        payload = Path(plaintext_path).read_bytes()
        Path(compute_encrypted_path).write_bytes(b"crypt4gh" + payload)

    def _fake_rewrite_output_header_to_user_key(
        *,
        compute_encrypted_path,
        final_output_tmp_path,
        reencryption_service_url,
        compute_keypair_id,
    ):
        del reencryption_service_url
        del compute_keypair_id
        Path(final_output_tmp_path).write_bytes(Path(compute_encrypted_path).read_bytes())

    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._encrypt_plaintext_to_compute_key",
        _fake_encrypt_plaintext_to_compute_key,
    )
    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._rewrite_output_header_to_user_key",
        _fake_rewrite_output_header_to_user_key,
    )

    with pytest.raises(Crypt4GHRemoteExecutionError, match="outside allowed roots"):
        finalize_declared_crypt4gh_outputs(
            output_targets=[
                {
                    "output_path": str(output_path),
                    "plaintext_path": str(working_root / "_crypt" / "outputs" / "ds_24" / "plaintext"),
                    "encrypted_marker_path": str(marker_dir / "ds_24.encrypted"),
                    "encrypted_ext": "tabular.c4gh",
                    "extra_files_output_path": str(extra_files_root),
                    "extra_files_manifest_path": str(manifest_path),
                    "allowed_root_paths": [str(working_root)],
                }
            ],
            reencryption_service_url="http://example.invalid",
            compute_public_key="unused",
            compute_keypair_id="unused",
        )

    assert outside_secret.exists()
    assert not output_path.exists()
    assert not extra_files_root.exists()


def test_pre_success_verifier_fails_for_missing_payload_marker(tmp_path):
    marker_dir = tmp_path / "_c4gh_stage" / "outputs"
    marker_dir.mkdir(parents=True, exist_ok=True)

    dataset_path = tmp_path / "objects" / "dataset_31.dat"
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    dataset_path.write_bytes(b"crypt4ghpayload")

    class _DatasetObject:
        def __init__(self, dataset_id: int, file_name: str):
            self.id = dataset_id
            self._file_name = file_name

        def get_file_name(self, sync_cache=False):
            del sync_cache
            return self._file_name

    class _DatasetAssociation:
        def __init__(self, name: str, dataset_object):
            self.name = name
            self.dataset = type("_DatasetInstance", (), {"dataset": dataset_object})

    with pytest.raises(Crypt4GHRemoteExecutionError, match="payload marker missing"):
        crypt4gh_remote_execution.verify_crypt4gh_pre_success_output_evidence(
            working_directory=str(tmp_path),
            output_dataset_associations=[
                _DatasetAssociation("direct_output", _DatasetObject(31, str(dataset_path))),
            ],
        )


def test_pre_success_verifier_fails_when_marker_exists_but_payload_is_plaintext(tmp_path):
    marker_dir = tmp_path / "_c4gh_stage" / "outputs"
    marker_dir.mkdir(parents=True, exist_ok=True)
    (marker_dir / "ds_32.encrypted").write_text("tabular.c4gh\n")

    dataset_path = tmp_path / "objects" / "dataset_32.dat"
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    dataset_path.write_bytes(b"PLAINTEXT")

    class _DatasetObject:
        def __init__(self, dataset_id: int, file_name: str):
            self.id = dataset_id
            self._file_name = file_name

        def get_file_name(self, sync_cache=False):
            del sync_cache
            return self._file_name

    class _DatasetAssociation:
        def __init__(self, name: str, dataset_object):
            self.name = name
            self.dataset = type("_DatasetInstance", (), {"dataset": dataset_object})

    with pytest.raises(Crypt4GHRemoteExecutionError, match="payload remained plaintext"):
        crypt4gh_remote_execution.verify_crypt4gh_pre_success_output_evidence(
            working_directory=str(tmp_path),
            output_dataset_associations=[
                _DatasetAssociation("direct_output", _DatasetObject(32, str(dataset_path))),
            ],
        )


def test_pre_success_verifier_fails_for_missing_discovered_mapping(tmp_path):
    marker_dir = tmp_path / "_c4gh_stage" / "outputs"
    marker_dir.mkdir(parents=True, exist_ok=True)

    (marker_dir / "ds_41.encrypted").write_text("tabular.c4gh\n")

    dataset_path = tmp_path / "objects" / "dataset_41.dat"
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    dataset_path.write_bytes(b"crypt4ghpayload")

    class _DatasetObject:
        def __init__(self, dataset_id: int, file_name: str):
            self.id = dataset_id
            self._file_name = file_name

        def get_file_name(self, sync_cache=False):
            del sync_cache
            return self._file_name

    class _DatasetAssociation:
        def __init__(self, name: str, dataset_object):
            self.name = name
            self.dataset = type("_DatasetInstance", (), {"dataset": dataset_object})

    with pytest.raises(Crypt4GHRemoteExecutionError, match="discovered-output mapping missing"):
        crypt4gh_remote_execution.verify_crypt4gh_pre_success_output_evidence(
            working_directory=str(tmp_path),
            output_dataset_associations=[
                _DatasetAssociation("__new_primary_file_output|sample1__", _DatasetObject(41, str(dataset_path))),
            ],
        )


def test_pre_success_verifier_requires_exact_discovered_mapping_key(tmp_path):
    marker_dir = tmp_path / "_c4gh_stage" / "outputs"
    marker_dir.mkdir(parents=True, exist_ok=True)
    (marker_dir / "discovered_designations.json").write_text(json.dumps({"sample2_suffix": "tabular.c4gh"}))

    dataset_path = tmp_path / "objects" / "dataset_42.dat"
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    dataset_path.write_bytes(b"crypt4ghpayload")

    class _DatasetObject:
        def __init__(self, dataset_id: int, file_name: str):
            self.id = dataset_id
            self._file_name = file_name

        def get_file_name(self, sync_cache=False):
            del sync_cache
            return self._file_name

    class _DatasetAssociation:
        def __init__(self, name: str, dataset_object):
            self.name = name
            self.dataset = type("_DatasetInstance", (), {"dataset": dataset_object})

    with pytest.raises(Crypt4GHRemoteExecutionError, match="discovered-output mapping missing"):
        crypt4gh_remote_execution.verify_crypt4gh_pre_success_output_evidence(
            working_directory=str(tmp_path),
            output_dataset_associations=[
                _DatasetAssociation("__new_primary_file_output|sample2__", _DatasetObject(42, str(dataset_path))),
            ],
        )


def test_pre_success_verifier_allows_discovered_outputs_without_dataset_marker_when_mapping_present(tmp_path):
    marker_dir = tmp_path / "_c4gh_stage" / "outputs"
    marker_dir.mkdir(parents=True, exist_ok=True)
    (marker_dir / "discovered_designations.json").write_text(json.dumps({"sample1": "txt.c4gh"}))

    dataset_path = tmp_path / "objects" / "dataset_61.dat"
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    dataset_path.write_bytes(b"crypt4ghpayload")

    class _DatasetObject:
        def __init__(self, dataset_id: int, file_name: str):
            self.id = dataset_id
            self._file_name = file_name

        def get_file_name(self, sync_cache=False):
            del sync_cache
            return self._file_name

    class _DatasetAssociation:
        def __init__(self, name: str, dataset_object):
            self.name = name
            self.dataset = type("_DatasetInstance", (), {"dataset": dataset_object})

    crypt4gh_remote_execution.verify_crypt4gh_pre_success_output_evidence(
        working_directory=str(tmp_path),
        output_dataset_associations=[
            _DatasetAssociation("__new_primary_file_output|sample1__", _DatasetObject(61, str(dataset_path))),
        ],
    )


def test_pre_success_verifier_fails_for_plaintext_discovered_payload_even_when_mapping_present(tmp_path):
    marker_dir = tmp_path / "_c4gh_stage" / "outputs"
    marker_dir.mkdir(parents=True, exist_ok=True)
    (marker_dir / "discovered_designations.json").write_text(json.dumps({"sample1": "txt.c4gh"}))

    dataset_path = tmp_path / "objects" / "dataset_62.dat"
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    dataset_path.write_bytes(b"PLAINTEXT")

    class _DatasetObject:
        def __init__(self, dataset_id: int, file_name: str):
            self.id = dataset_id
            self._file_name = file_name

        def get_file_name(self, sync_cache=False):
            del sync_cache
            return self._file_name

    class _DatasetAssociation:
        def __init__(self, name: str, dataset_object):
            self.name = name
            self.dataset = type("_DatasetInstance", (), {"dataset": dataset_object})

    with pytest.raises(Crypt4GHRemoteExecutionError, match="payload remained plaintext"):
        crypt4gh_remote_execution.verify_crypt4gh_pre_success_output_evidence(
            working_directory=str(tmp_path),
            output_dataset_associations=[
                _DatasetAssociation("__new_primary_file_output|sample1__", _DatasetObject(62, str(dataset_path))),
            ],
        )


def test_pre_success_verifier_fails_for_missing_extra_files_manifest(tmp_path):
    marker_dir = tmp_path / "_c4gh_stage" / "outputs"
    marker_dir.mkdir(parents=True, exist_ok=True)

    (marker_dir / "ds_51.encrypted").write_text("tabular.c4gh\n")

    dataset_path = tmp_path / "objects" / "dataset_51.dat"
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    dataset_path.write_bytes(b"crypt4ghpayload")

    extra_files_path = tmp_path / "objects" / "dataset_51_files"
    extra_files_path.mkdir(parents=True, exist_ok=True)
    (extra_files_path / "foo.txt").write_bytes(b"crypt4ghextra")

    class _DatasetObject:
        def __init__(self, dataset_id: int, file_name: str):
            self.id = dataset_id
            self._file_name = file_name

        def get_file_name(self, sync_cache=False):
            del sync_cache
            return self._file_name

    class _DatasetAssociation:
        def __init__(self, name: str, dataset_object):
            self.name = name
            self.dataset = type("_DatasetInstance", (), {"dataset": dataset_object})

    with pytest.raises(Crypt4GHRemoteExecutionError, match="extra_files manifest missing"):
        crypt4gh_remote_execution.verify_crypt4gh_pre_success_output_evidence(
            working_directory=str(tmp_path),
            output_dataset_associations=[
                _DatasetAssociation("direct_output", _DatasetObject(51, str(dataset_path))),
            ],
        )


def test_pre_success_verifier_fails_for_plaintext_extra_files_payload_even_when_manifest_entries_exist(tmp_path):
    marker_dir = tmp_path / "_c4gh_stage" / "outputs"
    marker_dir.mkdir(parents=True, exist_ok=True)
    (marker_dir / "ds_52.encrypted").write_text("tabular.c4gh\n")
    (marker_dir / "ds_52.extra_files_manifest.json").write_text(
        json.dumps({"files": {"foo.txt": "tabular.c4gh"}})
    )

    dataset_path = tmp_path / "objects" / "dataset_52.dat"
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    dataset_path.write_bytes(b"crypt4ghpayload")

    extra_files_path = tmp_path / "objects" / "dataset_52_files"
    extra_files_path.mkdir(parents=True, exist_ok=True)
    (extra_files_path / "foo.txt").write_bytes(b"PLAINTEXT")

    class _DatasetObject:
        def __init__(self, dataset_id: int, file_name: str):
            self.id = dataset_id
            self._file_name = file_name

        def get_file_name(self, sync_cache=False):
            del sync_cache
            return self._file_name

    class _DatasetAssociation:
        def __init__(self, name: str, dataset_object):
            self.name = name
            self.dataset = type("_DatasetInstance", (), {"dataset": dataset_object})

    with pytest.raises(Crypt4GHRemoteExecutionError, match="extra_files payload remained plaintext"):
        crypt4gh_remote_execution.verify_crypt4gh_pre_success_output_evidence(
            working_directory=str(tmp_path),
            output_dataset_associations=[
                _DatasetAssociation("direct_output", _DatasetObject(52, str(dataset_path))),
            ],
        )


def test_pre_success_verifier_fails_for_symlinked_extra_files_payload_even_when_manifest_entries_exist(tmp_path):
    marker_dir = tmp_path / "_c4gh_stage" / "outputs"
    marker_dir.mkdir(parents=True, exist_ok=True)
    (marker_dir / "ds_53.encrypted").write_text("tabular.c4gh\n")
    (marker_dir / "ds_53.extra_files_manifest.json").write_text(
        json.dumps({"files": {"foo.txt": "tabular.c4gh"}})
    )

    dataset_path = tmp_path / "objects" / "dataset_53.dat"
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    dataset_path.write_bytes(b"crypt4ghpayload")

    outside_payload_path = tmp_path / "outside" / "foo.txt"
    outside_payload_path.parent.mkdir(parents=True, exist_ok=True)
    outside_payload_path.write_bytes(b"crypt4ghoutside")

    extra_files_path = tmp_path / "objects" / "dataset_53_files"
    extra_files_path.mkdir(parents=True, exist_ok=True)
    (extra_files_path / "foo.txt").symlink_to(outside_payload_path)

    class _DatasetObject:
        def __init__(self, dataset_id: int, file_name: str):
            self.id = dataset_id
            self._file_name = file_name

        def get_file_name(self, sync_cache=False):
            del sync_cache
            return self._file_name

    class _DatasetAssociation:
        def __init__(self, name: str, dataset_object):
            self.name = name
            self.dataset = type("_DatasetInstance", (), {"dataset": dataset_object})

    with pytest.raises(Crypt4GHRemoteExecutionError, match="extra_files payload symlink"):
        crypt4gh_remote_execution.verify_crypt4gh_pre_success_output_evidence(
            working_directory=str(tmp_path),
            output_dataset_associations=[
                _DatasetAssociation("direct_output", _DatasetObject(53, str(dataset_path))),
            ],
        )


def test_pre_success_verifier_fails_for_missing_extra_files_payload_when_manifest_entries_exist(tmp_path):
    marker_dir = tmp_path / "_c4gh_stage" / "outputs"
    marker_dir.mkdir(parents=True, exist_ok=True)
    (marker_dir / "ds_54.encrypted").write_text("tabular.c4gh\n")
    (marker_dir / "ds_54.extra_files_manifest.json").write_text(
        json.dumps({"files": {"foo.txt": "tabular.c4gh"}})
    )

    dataset_path = tmp_path / "objects" / "dataset_54.dat"
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    dataset_path.write_bytes(b"crypt4ghpayload")

    extra_files_path = tmp_path / "objects" / "dataset_54_files"
    extra_files_path.mkdir(parents=True, exist_ok=True)

    class _DatasetObject:
        def __init__(self, dataset_id: int, file_name: str):
            self.id = dataset_id
            self._file_name = file_name

        def get_file_name(self, sync_cache=False):
            del sync_cache
            return self._file_name

    class _DatasetAssociation:
        def __init__(self, name: str, dataset_object):
            self.name = name
            self.dataset = type("_DatasetInstance", (), {"dataset": dataset_object})

    with pytest.raises(Crypt4GHRemoteExecutionError, match="extra_files payload missing"):
        crypt4gh_remote_execution.verify_crypt4gh_pre_success_output_evidence(
            working_directory=str(tmp_path),
            output_dataset_associations=[
                _DatasetAssociation("direct_output", _DatasetObject(54, str(dataset_path))),
            ],
        )


def test_pre_success_verifier_fails_for_unreadable_extra_files_payload_under_scan_race(tmp_path, monkeypatch):
    marker_dir = tmp_path / "_c4gh_stage" / "outputs"
    marker_dir.mkdir(parents=True, exist_ok=True)
    (marker_dir / "ds_55.encrypted").write_text("tabular.c4gh\n")
    (marker_dir / "ds_55.extra_files_manifest.json").write_text(
        json.dumps({"files": {"foo.txt": "tabular.c4gh"}})
    )

    dataset_path = tmp_path / "objects" / "dataset_55.dat"
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    dataset_path.write_bytes(b"crypt4ghpayload")

    extra_files_path = tmp_path / "objects" / "dataset_55_files"
    extra_files_path.mkdir(parents=True, exist_ok=True)
    payload_path = extra_files_path / "foo.txt"
    payload_path.write_bytes(b"crypt4ghpayload")

    def _scan_race_walk(_path, *args, **kwargs):
        del _path
        del args
        del kwargs
        return []

    original_open = Path.open

    def _permission_denied_open(path_self, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if path_self == payload_path and mode == "rb":
            raise PermissionError("simulated permission denied")
        return original_open(path_self, *args, **kwargs)

    monkeypatch.setattr(crypt4gh_remote_execution.os, "walk", _scan_race_walk)
    monkeypatch.setattr(Path, "open", _permission_denied_open)

    class _DatasetObject:
        def __init__(self, dataset_id: int, file_name: str):
            self.id = dataset_id
            self._file_name = file_name

        def get_file_name(self, sync_cache=False):
            del sync_cache
            return self._file_name

    class _DatasetAssociation:
        def __init__(self, name: str, dataset_object):
            self.name = name
            self.dataset = type("_DatasetInstance", (), {"dataset": dataset_object})

    with pytest.raises(Crypt4GHRemoteExecutionError, match="extra_files payload unreadable"):
        crypt4gh_remote_execution.verify_crypt4gh_pre_success_output_evidence(
            working_directory=str(tmp_path),
            output_dataset_associations=[
                _DatasetAssociation("direct_output", _DatasetObject(55, str(dataset_path))),
            ],
        )


def test_pre_success_verifier_fails_for_missing_extra_files_manifest_for_discovered_output(tmp_path):
    marker_dir = tmp_path / "_c4gh_stage" / "outputs"
    marker_dir.mkdir(parents=True, exist_ok=True)
    (marker_dir / "ds_71.encrypted").write_text("tabular.c4gh\n")
    (marker_dir / "discovered_designations.json").write_text(json.dumps({"sample1": "tabular.c4gh"}))

    dataset_path = tmp_path / "objects" / "dataset_71.dat"
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    dataset_path.write_bytes(b"crypt4ghpayload")

    extra_files_path = tmp_path / "objects" / "dataset_71_files"
    extra_files_path.mkdir(parents=True, exist_ok=True)
    (extra_files_path / "child.txt").write_bytes(b"crypt4ghextra")

    class _DatasetObject:
        def __init__(self, dataset_id: int, file_name: str):
            self.id = dataset_id
            self._file_name = file_name

        def get_file_name(self, sync_cache=False):
            del sync_cache
            return self._file_name

    class _DatasetAssociation:
        def __init__(self, name: str, dataset_object):
            self.name = name
            self.dataset = type("_DatasetInstance", (), {"dataset": dataset_object})

    with pytest.raises(Crypt4GHRemoteExecutionError, match="extra_files manifest missing"):
        crypt4gh_remote_execution.verify_crypt4gh_pre_success_output_evidence(
            working_directory=str(tmp_path),
            output_dataset_associations=[
                _DatasetAssociation("__new_primary_file_output|sample1__", _DatasetObject(71, str(dataset_path))),
            ],
        )


def test_pre_success_verifier_fails_for_residual_plaintext_staging_artifact_outside_tracked_outputs(tmp_path):
    marker_dir = tmp_path / "_c4gh_stage" / "outputs"
    marker_dir.mkdir(parents=True, exist_ok=True)
    (marker_dir / "ds_91.encrypted").write_text("tabular.c4gh\n")

    dataset_path = tmp_path / "objects" / "dataset_91.dat"
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    dataset_path.write_bytes(b"crypt4ghpayload")

    leaked_plaintext_path = tmp_path / "_crypt" / "outputs" / "untracked" / "plaintext"
    leaked_plaintext_path.parent.mkdir(parents=True, exist_ok=True)
    leaked_plaintext_path.write_text("stale plaintext")

    class _DatasetObject:
        def __init__(self, dataset_id: int, file_name: str):
            self.id = dataset_id
            self._file_name = file_name

        def get_file_name(self, sync_cache=False):
            del sync_cache
            return self._file_name

    class _DatasetAssociation:
        def __init__(self, name: str, dataset_object):
            self.name = name
            self.dataset = type("_DatasetInstance", (), {"dataset": dataset_object})

    with pytest.raises(Crypt4GHRemoteExecutionError, match="plaintext staging artifact remained"):
        crypt4gh_remote_execution.verify_crypt4gh_pre_success_output_evidence(
            working_directory=str(tmp_path),
            output_dataset_associations=[
                _DatasetAssociation("direct_output", _DatasetObject(91, str(dataset_path))),
            ],
        )


def test_pre_success_verifier_fails_for_residual_plaintext_input_staging_artifact(tmp_path):
    marker_dir = tmp_path / "_c4gh_stage" / "outputs"
    marker_dir.mkdir(parents=True, exist_ok=True)
    (marker_dir / "ds_92.encrypted").write_text("tabular.c4gh\n")

    dataset_path = tmp_path / "objects" / "dataset_92.dat"
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    dataset_path.write_bytes(b"crypt4ghpayload")

    leaked_plaintext_path = tmp_path / "_crypt" / "inputs" / "in_1" / "plaintext"
    leaked_plaintext_path.parent.mkdir(parents=True, exist_ok=True)
    leaked_plaintext_path.write_text("input plaintext")

    class _DatasetObject:
        def __init__(self, dataset_id: int, file_name: str):
            self.id = dataset_id
            self._file_name = file_name

        def get_file_name(self, sync_cache=False):
            del sync_cache
            return self._file_name

    class _DatasetAssociation:
        def __init__(self, name: str, dataset_object):
            self.name = name
            self.dataset = type("_DatasetInstance", (), {"dataset": dataset_object})

    with pytest.raises(Crypt4GHRemoteExecutionError, match="plaintext staging artifact remained"):
        crypt4gh_remote_execution.verify_crypt4gh_pre_success_output_evidence(
            working_directory=str(tmp_path),
            output_dataset_associations=[
                _DatasetAssociation("direct_output", _DatasetObject(92, str(dataset_path))),
            ],
        )


def test_pre_success_verifier_accepts_designation_extra_files_manifest_for_discovered_output(tmp_path):
    marker_dir = tmp_path / "_c4gh_stage" / "outputs"
    marker_dir.mkdir(parents=True, exist_ok=True)
    (marker_dir / "ds_81.encrypted").write_text("tabular.c4gh\n")
    (marker_dir / "discovered_designations.json").write_text(json.dumps({"sample1": "tabular.c4gh"}))
    (marker_dir / "designation_sample1.extra_files_manifest.json").write_text(
        json.dumps({"files": {"child.txt": "tabular.c4gh"}})
    )

    dataset_path = tmp_path / "objects" / "dataset_81.dat"
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    dataset_path.write_bytes(b"crypt4ghpayload")

    extra_files_path = tmp_path / "objects" / "dataset_81_files"
    extra_files_path.mkdir(parents=True, exist_ok=True)
    (extra_files_path / "child.txt").write_bytes(b"crypt4ghextra")

    class _DatasetObject:
        def __init__(self, dataset_id: int, file_name: str):
            self.id = dataset_id
            self._file_name = file_name

        def get_file_name(self, sync_cache=False):
            del sync_cache
            return self._file_name

    class _DatasetAssociation:
        def __init__(self, name: str, dataset_object):
            self.name = name
            self.dataset = type("_DatasetInstance", (), {"dataset": dataset_object})

    crypt4gh_remote_execution.verify_crypt4gh_pre_success_output_evidence(
        working_directory=str(tmp_path),
        output_dataset_associations=[
            _DatasetAssociation("__new_primary_file_output|sample1__", _DatasetObject(81, str(dataset_path))),
        ],
    )


def test_pre_success_verifier_ignores_placeholder_container_output_without_payload_marker(tmp_path):
    marker_dir = tmp_path / "_c4gh_stage" / "outputs"
    marker_dir.mkdir(parents=True, exist_ok=True)
    (marker_dir / "discovered_designations.json").write_text(json.dumps({"sample1": "tabular.c4gh"}))

    container_dataset_path = tmp_path / "objects" / "dataset_90.dat"
    container_dataset_path.parent.mkdir(parents=True, exist_ok=True)
    container_dataset_path.write_bytes(b"container")

    discovered_dataset_path = tmp_path / "objects" / "dataset_91.dat"
    discovered_dataset_path.write_bytes(b"crypt4ghpayload")

    class _DatasetObject:
        def __init__(self, dataset_id: int, file_name: str, designation: str = ""):
            self.id = dataset_id
            self._file_name = file_name
            self.designation = designation

        def get_file_name(self, sync_cache=False):
            del sync_cache
            return self._file_name

    class _DatasetAssociation:
        def __init__(self, name: str, dataset_object):
            self.name = name
            self.dataset = type("_DatasetInstance", (), {"dataset": dataset_object})

    crypt4gh_remote_execution.verify_crypt4gh_pre_success_output_evidence(
        working_directory=str(tmp_path),
        output_dataset_associations=[
            _DatasetAssociation("output", _DatasetObject(90, str(container_dataset_path))),
            _DatasetAssociation("__new_primary_file_output|sample1__", _DatasetObject(91, str(discovered_dataset_path))),
        ],
    )


def test_pre_success_verifier_accepts_discovered_markers_written_under_working_subdir(tmp_path):
    root_marker_dir = tmp_path / "_c4gh_stage" / "outputs"
    root_marker_dir.mkdir(parents=True, exist_ok=True)
    (root_marker_dir / "ds_2.encrypted").write_text("txt.c4gh\n")

    working_marker_dir = tmp_path / "working" / "_c4gh_stage" / "outputs"
    working_marker_dir.mkdir(parents=True, exist_ok=True)
    (working_marker_dir / "ds_3.encrypted").write_text("tabular.c4gh\n")
    (working_marker_dir / "discovered_designations.json").write_text(
        json.dumps(
            {
                "sample2": "tabular.c4gh",
                "sample3": "tabular.c4gh",
            }
        )
    )

    dataset_path = tmp_path / "objects" / "dataset_payload.dat"
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    dataset_path.write_bytes(b"crypt4ghpayload")

    class _DatasetObject:
        def __init__(self, dataset_id: int, file_name: str):
            self.id = dataset_id
            self._file_name = file_name

        def get_file_name(self, sync_cache=False):
            del sync_cache
            return self._file_name

    class _DatasetAssociation:
        def __init__(self, name: str, dataset_object):
            self.name = name
            self.dataset = type("_DatasetInstance", (), {"dataset": dataset_object})

    crypt4gh_remote_execution.verify_crypt4gh_pre_success_output_evidence(
        working_directory=str(tmp_path),
        output_dataset_associations=[
            _DatasetAssociation("other", _DatasetObject(2, str(dataset_path))),
            _DatasetAssociation("sample", _DatasetObject(3, str(dataset_path))),
            _DatasetAssociation("__new_primary_file_sample|sample2__", _DatasetObject(4, str(dataset_path))),
            _DatasetAssociation("__new_primary_file_sample|sample3__", _DatasetObject(5, str(dataset_path))),
        ],
    )


def test_discovered_crypt4gh_metadata_path_clears_compute_keypair_without_generic_set_meta(monkeypatch):
    from galaxy.model.store.discover import ModelPersistenceContext

    class _Metadata:
        def __init__(self):
            self.loaded = None

        def from_JSON_dict(self, json_dict):
            self.loaded = json_dict

    class _Datatype:
        def __init__(self):
            self.calls = []

        def set_meta(self, dataset, **kwd):
            self.calls.append((dataset, kwd))

    class _PrimaryData:
        states = type("States", (), {"OK": "ok", "FAILED_METADATA": "failed_metadata"})

        def __init__(self):
            self.name = "sample"
            self.info = ""
            self.dbkey = "?"
            self.job_working_directory = "/tmp/jobdir"
            self.extension = "tabular.c4gh"
            self.state = "ok"
            self.metadata = _Metadata()
            self.datatype = _Datatype()
            self.peek_calls = 0
            self.total_size_calls = 0

        def set_meta(self):
            raise AssertionError("generic set_meta should not run for crypt4gh discovered outputs")

        def set_peek(self):
            self.peek_calls += 1

        def set_total_size(self):
            self.total_size_calls += 1

    primary_data = _PrimaryData()
    dataset_attributes = {"ext": "tabular", "clear_crypt4gh_compute_keypair": True}

    monkeypatch.setattr(
        "galaxy.model.store.discover._resolve_discovered_crypt4gh_extension",
        lambda *, ext, job_working_directory, require_crypt4gh_extension=False: "tabular.c4gh",
    )

    ModelPersistenceContext.set_datasets_metadata([primary_data], [dataset_attributes])

    assert primary_data.datatype.calls == [
        (
            primary_data,
            {"crypt4gh_clear_compute_keypair": True},
        )
    ]
    assert primary_data.peek_calls == 1
    assert primary_data.total_size_calls == 1


def test_finalize_declared_outputs_deletes_dataset_destination_when_encryption_fails(tmp_path, monkeypatch):
    output_path = tmp_path / "working" / "1"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("plain\n")

    dataset_output_path = tmp_path / "objects" / "dataset_1.dat"
    dataset_output_path.parent.mkdir(parents=True, exist_ok=True)
    dataset_output_path.write_text("plain\n")

    def _fail_encrypt(*, plaintext_path, compute_encrypted_path, compute_public_key):
        del plaintext_path
        del compute_encrypted_path
        del compute_public_key
        raise RuntimeError("encrypt failed")

    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._encrypt_plaintext_to_compute_key",
        _fail_encrypt,
    )

    with pytest.raises(Crypt4GHRemoteExecutionError, match="Failed to finalize encrypted Crypt4GH output"):
        finalize_declared_crypt4gh_outputs(
            output_targets=[
                {
                    "output_path": str(output_path),
                    "dataset_output_path": str(dataset_output_path),
                    "plaintext_path": str(tmp_path / "plaintext"),
                    "encrypted_marker_path": str(tmp_path / "marker.encrypted"),
                    "encrypted_ext": "tabular.c4gh",
                }
            ],
            reencryption_service_url="http://example.invalid",
            compute_public_key="unused",
            compute_keypair_id="unused",
        )

    assert not output_path.exists()
    assert not dataset_output_path.exists()


def test_finalize_declared_outputs_unlinks_extra_files_symlink_when_encryption_fails(tmp_path, monkeypatch):
    output_path = tmp_path / "working" / "1"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("plain\n")

    real_extra_files = tmp_path / "real_extra_files"
    real_extra_files.mkdir(parents=True, exist_ok=True)
    (real_extra_files / "payload.txt").write_text("secret\n")

    extra_files_symlink = tmp_path / "working" / "1_files"
    extra_files_symlink.symlink_to(real_extra_files, target_is_directory=True)

    extra_files_manifest_path = tmp_path / "_c4gh_stage" / "outputs" / "ds_1.extra_files_manifest.json"
    extra_files_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    extra_files_manifest_path.write_text("{}")

    def _fail_encrypt(*, plaintext_path, compute_encrypted_path, compute_public_key):
        del plaintext_path
        del compute_encrypted_path
        del compute_public_key
        raise RuntimeError("encrypt failed")

    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._encrypt_plaintext_to_compute_key",
        _fail_encrypt,
    )

    with pytest.raises(Crypt4GHRemoteExecutionError, match="Failed to finalize encrypted Crypt4GH output"):
        finalize_declared_crypt4gh_outputs(
            output_targets=[
                {
                    "output_path": str(output_path),
                    "plaintext_path": str(tmp_path / "plaintext"),
                    "encrypted_marker_path": str(tmp_path / "marker.encrypted"),
                    "encrypted_ext": "tabular.c4gh",
                    "extra_files_output_path": str(extra_files_symlink),
                    "extra_files_manifest_path": str(extra_files_manifest_path),
                }
            ],
            reencryption_service_url="http://example.invalid",
            compute_public_key="unused",
            compute_keypair_id="unused",
        )

    assert not output_path.exists()
    assert not extra_files_symlink.exists()
    assert not extra_files_symlink.is_symlink()
    assert real_extra_files.exists()
    assert (real_extra_files / "payload.txt").exists()


def test_finalize_declared_outputs_logs_permission_denied_diagnostics_for_extra_files_purge_failures(
    tmp_path,
    monkeypatch,
    caplog,
):
    output_path = tmp_path / "working" / "1"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("plain\n")

    extra_files_output_path = tmp_path / "working" / "1_files"
    extra_files_output_path.mkdir(parents=True, exist_ok=True)
    (extra_files_output_path / "payload.txt").write_text("secret\n")

    extra_files_manifest_path = tmp_path / "_c4gh_stage" / "outputs" / "ds_1.extra_files_manifest.json"
    extra_files_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    extra_files_manifest_path.write_text("{}")

    def _fail_encrypt(*, plaintext_path, compute_encrypted_path, compute_public_key):
        del plaintext_path
        del compute_encrypted_path
        del compute_public_key
        raise RuntimeError("encrypt failed")

    original_rmtree = crypt4gh_remote_execution.shutil.rmtree

    def _permission_denied_rmtree(path, *args, **kwargs):
        if Path(path) == extra_files_output_path:
            raise PermissionError("permission denied while removing extra files")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._encrypt_plaintext_to_compute_key",
        _fail_encrypt,
    )
    monkeypatch.setattr(crypt4gh_remote_execution.shutil, "rmtree", _permission_denied_rmtree)

    caplog.set_level("ERROR", logger="galaxy.tools.crypt4gh_remote_execution")
    with pytest.raises(Crypt4GHRemoteExecutionError, match="Failed to finalize encrypted Crypt4GH output"):
        finalize_declared_crypt4gh_outputs(
            output_targets=[
                {
                    "output_path": str(output_path),
                    "plaintext_path": str(tmp_path / "plaintext"),
                    "encrypted_marker_path": str(tmp_path / "marker.encrypted"),
                    "encrypted_ext": "tabular.c4gh",
                    "extra_files_output_path": str(extra_files_output_path),
                    "extra_files_manifest_path": str(extra_files_manifest_path),
                }
            ],
            reencryption_service_url="http://example.invalid",
            compute_public_key="unused",
            compute_keypair_id="unused",
        )

    assert extra_files_output_path.exists()
    assert any(
        "PermissionError" in record.getMessage() and str(extra_files_output_path) in record.getMessage()
        for record in caplog.records
    )


def test_finalize_declared_outputs_logs_concurrent_mutation_diagnostics_for_manifest_purge_race(
    tmp_path,
    monkeypatch,
    caplog,
):
    output_path = tmp_path / "working" / "1"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("plain\n")

    extra_files_manifest_path = tmp_path / "_c4gh_stage" / "outputs" / "ds_1.extra_files_manifest.json"
    extra_files_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    extra_files_manifest_path.write_text("{}")

    def _fail_encrypt(*, plaintext_path, compute_encrypted_path, compute_public_key):
        del plaintext_path
        del compute_encrypted_path
        del compute_public_key
        raise RuntimeError("encrypt failed")

    original_unlink = Path.unlink

    def _race_unlink(self, *args, **kwargs):
        if self == extra_files_manifest_path:
            if self.exists():
                original_unlink(self, *args, **kwargs)
            raise FileNotFoundError("simulated concurrent manifest removal")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._encrypt_plaintext_to_compute_key",
        _fail_encrypt,
    )
    monkeypatch.setattr(Path, "unlink", _race_unlink)

    caplog.set_level("WARNING", logger="galaxy.tools.crypt4gh_remote_execution")
    with pytest.raises(Crypt4GHRemoteExecutionError, match="Failed to finalize encrypted Crypt4GH output"):
        finalize_declared_crypt4gh_outputs(
            output_targets=[
                {
                    "output_path": str(output_path),
                    "plaintext_path": str(tmp_path / "plaintext"),
                    "encrypted_marker_path": str(tmp_path / "marker.encrypted"),
                    "encrypted_ext": "tabular.c4gh",
                    "extra_files_manifest_path": str(extra_files_manifest_path),
                }
            ],
            reencryption_service_url="http://example.invalid",
            compute_public_key="unused",
            compute_keypair_id="unused",
        )

    assert any(
        "concurrent mutation" in record.getMessage().lower()
        and "FileNotFoundError" in record.getMessage()
        and str(extra_files_manifest_path) in record.getMessage()
        for record in caplog.records
    )


def test_finalize_declared_outputs_logs_concurrent_mutation_diagnostics_for_output_payload_purge_race(
    tmp_path,
    monkeypatch,
    caplog,
):
    output_path = tmp_path / "working" / "1"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("plain\n")

    marker_path = tmp_path / "_c4gh_stage" / "outputs" / "ds_1.encrypted"

    def _fake_encrypt_plaintext_to_compute_key(*, plaintext_path, compute_encrypted_path, compute_public_key):
        del compute_public_key
        Path(compute_encrypted_path).write_bytes(Path(plaintext_path).read_bytes())

    def _fake_rewrite_output_header_to_user_key(
        *,
        compute_encrypted_path,
        final_output_tmp_path,
        reencryption_service_url,
        compute_keypair_id,
    ):
        del reencryption_service_url
        del compute_keypair_id
        Path(final_output_tmp_path).write_bytes(Path(compute_encrypted_path).read_bytes())

    def _fail_extra_files_finalize(**kwargs):
        del kwargs
        raise RuntimeError("simulated extra-files finalize race")

    original_unlink = Path.unlink

    def _race_unlink(self, *args, **kwargs):
        if self == output_path:
            if self.exists():
                original_unlink(self, *args, **kwargs)
            raise FileNotFoundError("simulated concurrent output payload removal")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._encrypt_plaintext_to_compute_key",
        _fake_encrypt_plaintext_to_compute_key,
    )
    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._rewrite_output_header_to_user_key",
        _fake_rewrite_output_header_to_user_key,
    )
    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._finalize_extra_files_payloads",
        _fail_extra_files_finalize,
    )
    monkeypatch.setattr(Path, "unlink", _race_unlink)

    caplog.set_level("WARNING", logger="galaxy.tools.crypt4gh_remote_execution")
    with pytest.raises(RuntimeError, match="simulated extra-files finalize race"):
        finalize_declared_crypt4gh_outputs(
            output_targets=[
                {
                    "output_path": str(output_path),
                    "plaintext_path": str(tmp_path / "plaintext"),
                    "encrypted_marker_path": str(marker_path),
                    "encrypted_ext": "tabular.c4gh",
                }
            ],
            reencryption_service_url="http://example.invalid",
            compute_public_key="unused",
            compute_keypair_id="unused",
        )

    assert any(
        "concurrent mutation" in record.getMessage().lower()
        and "FileNotFoundError" in record.getMessage()
        and str(output_path) in record.getMessage()
        for record in caplog.records
    )


def test_finalize_declared_outputs_logs_concurrent_mutation_diagnostics_for_marker_purge_race(
    tmp_path,
    monkeypatch,
    caplog,
):
    output_path = tmp_path / "working" / "1"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("plain\n")

    marker_path = tmp_path / "_c4gh_stage" / "outputs" / "ds_1.encrypted"

    def _fake_encrypt_plaintext_to_compute_key(*, plaintext_path, compute_encrypted_path, compute_public_key):
        del compute_public_key
        Path(compute_encrypted_path).write_bytes(Path(plaintext_path).read_bytes())

    def _fake_rewrite_output_header_to_user_key(
        *,
        compute_encrypted_path,
        final_output_tmp_path,
        reencryption_service_url,
        compute_keypair_id,
    ):
        del reencryption_service_url
        del compute_keypair_id
        Path(final_output_tmp_path).write_bytes(Path(compute_encrypted_path).read_bytes())

    def _fail_extra_files_finalize(**kwargs):
        del kwargs
        raise RuntimeError("simulated extra-files finalize race")

    original_unlink = Path.unlink

    def _race_unlink(self, *args, **kwargs):
        if self == marker_path:
            if self.exists():
                original_unlink(self, *args, **kwargs)
            raise FileNotFoundError("simulated concurrent marker removal")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._encrypt_plaintext_to_compute_key",
        _fake_encrypt_plaintext_to_compute_key,
    )
    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._rewrite_output_header_to_user_key",
        _fake_rewrite_output_header_to_user_key,
    )
    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._finalize_extra_files_payloads",
        _fail_extra_files_finalize,
    )
    monkeypatch.setattr(Path, "unlink", _race_unlink)

    caplog.set_level("WARNING", logger="galaxy.tools.crypt4gh_remote_execution")
    with pytest.raises(RuntimeError, match="simulated extra-files finalize race"):
        finalize_declared_crypt4gh_outputs(
            output_targets=[
                {
                    "output_path": str(output_path),
                    "plaintext_path": str(tmp_path / "plaintext"),
                    "encrypted_marker_path": str(marker_path),
                    "encrypted_ext": "tabular.c4gh",
                }
            ],
            reencryption_service_url="http://example.invalid",
            compute_public_key="unused",
            compute_keypair_id="unused",
        )

    assert any(
        "concurrent mutation" in record.getMessage().lower()
        and "FileNotFoundError" in record.getMessage()
        and str(marker_path) in record.getMessage()
        for record in caplog.records
    )


def test_finalize_declared_outputs_logs_concurrent_mutation_diagnostics_for_extra_files_directory_purge_race(
    tmp_path,
    monkeypatch,
    caplog,
):
    output_path = tmp_path / "working" / "1"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("plain\n")

    extra_files_output_path = tmp_path / "working" / "1_files"
    extra_files_output_path.mkdir(parents=True, exist_ok=True)
    (extra_files_output_path / "payload.txt").write_text("secret\n")

    def _fail_encrypt(*, plaintext_path, compute_encrypted_path, compute_public_key):
        del plaintext_path
        del compute_encrypted_path
        del compute_public_key
        raise RuntimeError("encrypt failed")

    original_rmtree = crypt4gh_remote_execution.shutil.rmtree

    def _race_rmtree(path, *args, **kwargs):
        if Path(path) == extra_files_output_path:
            raise FileNotFoundError("simulated concurrent extra-files directory removal")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._encrypt_plaintext_to_compute_key",
        _fail_encrypt,
    )
    monkeypatch.setattr(crypt4gh_remote_execution.shutil, "rmtree", _race_rmtree)

    caplog.set_level("WARNING", logger="galaxy.tools.crypt4gh_remote_execution")
    with pytest.raises(Crypt4GHRemoteExecutionError, match="Failed to finalize encrypted Crypt4GH output"):
        finalize_declared_crypt4gh_outputs(
            output_targets=[
                {
                    "output_path": str(output_path),
                    "plaintext_path": str(tmp_path / "plaintext"),
                    "encrypted_marker_path": str(tmp_path / "marker.encrypted"),
                    "encrypted_ext": "tabular.c4gh",
                    "extra_files_output_path": str(extra_files_output_path),
                }
            ],
            reencryption_service_url="http://example.invalid",
            compute_public_key="unused",
            compute_keypair_id="unused",
        )

    assert any(
        "concurrent mutation" in record.getMessage().lower()
        and "FileNotFoundError" in record.getMessage()
        and str(extra_files_output_path) in record.getMessage()
        for record in caplog.records
    )


def test_finalize_declared_outputs_deletes_unprocessed_plaintext_outputs_when_any_target_fails(tmp_path, monkeypatch):
    first_output_path = tmp_path / "working" / "1"
    first_output_path.parent.mkdir(parents=True, exist_ok=True)
    first_output_path.write_text("plain-one\n")

    second_output_path = tmp_path / "working" / "2"
    second_output_path.write_text("plain-two\n")

    def _fail_first_encrypt(*, plaintext_path, compute_encrypted_path, compute_public_key):
        del compute_encrypted_path
        del compute_public_key
        if str(plaintext_path).endswith("plaintext.1"):
            raise RuntimeError("encrypt failed")

    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._encrypt_plaintext_to_compute_key",
        _fail_first_encrypt,
    )

    with pytest.raises(Crypt4GHRemoteExecutionError, match="Failed to finalize encrypted Crypt4GH output"):
        finalize_declared_crypt4gh_outputs(
            output_targets=[
                {
                    "output_path": str(first_output_path),
                    "plaintext_path": str(tmp_path / "plaintext.1"),
                    "encrypted_marker_path": str(tmp_path / "marker.1.encrypted"),
                    "encrypted_ext": "tabular.c4gh",
                },
                {
                    "output_path": str(second_output_path),
                    "plaintext_path": str(tmp_path / "plaintext.2"),
                    "encrypted_marker_path": str(tmp_path / "marker.2.encrypted"),
                    "encrypted_ext": "tabular.c4gh",
                },
            ],
            reencryption_service_url="http://example.invalid",
            compute_public_key="unused",
            compute_keypair_id="unused",
        )

    assert not first_output_path.exists()
    assert not second_output_path.exists()


def test_finalize_declared_outputs_rejects_targets_outside_allowed_roots(tmp_path, monkeypatch):
    allowed_root = tmp_path / "job_work"
    allowed_root.mkdir(parents=True, exist_ok=True)

    unsafe_output_path = tmp_path / "outside" / "leaked_output.txt"
    unsafe_output_path.parent.mkdir(parents=True, exist_ok=True)
    unsafe_output_path.write_text("plain\n")

    def _encrypt_should_not_run(*, plaintext_path, compute_encrypted_path, compute_public_key):
        del plaintext_path
        del compute_encrypted_path
        del compute_public_key
        raise AssertionError("encryption should not run for out-of-scope output targets")

    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._encrypt_plaintext_to_compute_key",
        _encrypt_should_not_run,
    )

    with pytest.raises(Crypt4GHRemoteExecutionError, match="outside allowed roots"):
        finalize_declared_crypt4gh_outputs(
            output_targets=[
                {
                    "output_path": str(unsafe_output_path),
                    "plaintext_path": str(allowed_root / "_crypt" / "outputs" / "ds_1" / "plaintext"),
                    "encrypted_marker_path": str(allowed_root / "_c4gh_stage" / "outputs" / "ds_1.encrypted"),
                    "encrypted_ext": "tabular.c4gh",
                    "allowed_root_paths": [str(allowed_root)],
                }
            ],
            reencryption_service_url="http://example.invalid",
            compute_public_key="unused",
            compute_keypair_id="unused",
        )

    assert unsafe_output_path.exists()


def test_finalize_declared_outputs_rejects_traversal_output_target_via_parent_segments(tmp_path, monkeypatch):
    allowed_root = tmp_path / "job_work"
    allowed_root.mkdir(parents=True, exist_ok=True)

    outside_root = tmp_path / "outside"
    outside_root.mkdir(parents=True, exist_ok=True)
    outside_output_path = outside_root / "leaked_output.txt"
    outside_output_path.write_text("plain\n")

    (allowed_root / "working").mkdir(parents=True, exist_ok=True)

    traversal_output_path = allowed_root / "working" / ".." / ".." / "outside" / "leaked_output.txt"

    def _encrypt_should_not_run(*, plaintext_path, compute_encrypted_path, compute_public_key):
        del plaintext_path
        del compute_encrypted_path
        del compute_public_key
        raise AssertionError("encryption should not run for traversal output targets")

    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._encrypt_plaintext_to_compute_key",
        _encrypt_should_not_run,
    )

    with pytest.raises(Crypt4GHRemoteExecutionError, match="outside allowed roots"):
        finalize_declared_crypt4gh_outputs(
            output_targets=[
                {
                    "output_path": str(traversal_output_path),
                    "plaintext_path": str(allowed_root / "_crypt" / "outputs" / "ds_1" / "plaintext"),
                    "encrypted_marker_path": str(allowed_root / "_c4gh_stage" / "outputs" / "ds_1.encrypted"),
                    "encrypted_ext": "tabular.c4gh",
                    "allowed_root_paths": [str(allowed_root)],
                }
            ],
            reencryption_service_url="http://example.invalid",
            compute_public_key="unused",
            compute_keypair_id="unused",
        )

    assert outside_output_path.exists()


def test_finalize_about_to_persist_payload_writes_discovered_designation_map(tmp_path, monkeypatch):
    output_path = tmp_path / "discover" / "sample1.tsv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("sample\n")

    marker_dir = tmp_path / "_c4gh_stage" / "outputs"
    map_path = marker_dir / "discovered_designations.json"

    def _fake_encrypt_plaintext_to_compute_key(*, plaintext_path, compute_encrypted_path, compute_public_key):
        del compute_public_key
        Path(compute_encrypted_path).write_bytes(Path(plaintext_path).read_bytes())

    def _fake_rewrite_output_header_to_user_key(
        *,
        compute_encrypted_path,
        final_output_tmp_path,
        reencryption_service_url,
        compute_keypair_id,
    ):
        del reencryption_service_url
        del compute_keypair_id
        Path(final_output_tmp_path).write_bytes(Path(compute_encrypted_path).read_bytes())

    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._encrypt_plaintext_to_compute_key",
        _fake_encrypt_plaintext_to_compute_key,
    )
    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._rewrite_output_header_to_user_key",
        _fake_rewrite_output_header_to_user_key,
    )

    finalize_about_to_persist_crypt4gh_payload(
        output_path=str(output_path),
        plaintext_path=str(tmp_path / "_crypt" / "outputs" / "ds_1" / "plaintext"),
        encrypted_ext="tabular.c4gh",
        reencryption_service_url="http://example.invalid",
        compute_public_key="unused",
        compute_keypair_id="unused",
        encrypted_marker_path=str(marker_dir / "ds_1.encrypted"),
        designation="sample1",
        discovered_marker_map_path=str(map_path),
        allowed_root_paths=[str(tmp_path.resolve())],
    )

    assert map_path.exists()
    assert map_path.read_text() == '{"sample1": "tabular.c4gh"}'
