from datetime import (
    datetime,
    timedelta,
)
from pathlib import Path
import subprocess

import pytest

from galaxy.jobs.runners import BaseJobRunner
from galaxy.tools.crypt4gh_remote_execution import (
    build_crypt4gh_remote_compute_environment,
    build_crypt4gh_cleanup_wrapped_command,
    collect_declared_crypt4gh_output_targets,
    CRYPT4GH_CLEANUP_FAILED_MARKER,
    Crypt4GHRemoteExecutionError,
    finalize_declared_crypt4gh_outputs,
    should_run_crypt4gh_remote_execution,
)


class _DatasetMetadata:
    def __init__(self, *, crypt4gh_header=None, expiration=None):
        self.crypt4gh_header = crypt4gh_header
        self.crypt4gh_compute_keypair_expiration_date = expiration


class _Dataset:
    def __init__(self, metadata):
        self.metadata = metadata


class _JobIO:
    def __init__(self, datasets):
        self._datasets = datasets

    def get_input_datasets(self):
        return self._datasets


class _Config:
    def __init__(self, enable_crypt4gh_transparent_staging):
        self.enable_crypt4gh_transparent_staging = enable_crypt4gh_transparent_staging


class _RunnerApp:
    def __init__(self, *, enable_crypt4gh_transparent_staging):
        self.config = _Config(enable_crypt4gh_transparent_staging)


class _DatasetWrapper:
    def __init__(self, *, dataset_id):
        self.id = dataset_id


class _BuildDataset:
    def __init__(self, *, dataset_id, metadata):
        self.dataset = _DatasetWrapper(dataset_id=dataset_id)
        self.metadata = metadata


@pytest.fixture
def crypt4gh_dataset():
    return _Dataset(_DatasetMetadata(crypt4gh_header="header", expiration="2026-06-02T12:00:00+00:00"))


def test_top_level_gate_disables_remote_helper_setup(crypt4gh_dataset):
    result = should_run_crypt4gh_remote_execution(
        job_io=_JobIO([crypt4gh_dataset]),
        app_config=_Config(enable_crypt4gh_transparent_staging=False),
        destination_params={"tool_evaluation_strategy": "remote"},
    )

    assert result is False


def test_helper_path_requires_remote_tool_evaluation_strategy(crypt4gh_dataset):
    result = should_run_crypt4gh_remote_execution(
        job_io=_JobIO([crypt4gh_dataset]),
        app_config=_Config(enable_crypt4gh_transparent_staging=True),
        destination_params={"tool_evaluation_strategy": "local"},
    )

    assert result is False


def test_helper_setup_failures_incorrect_expiration_fail_closed():
    dataset = _Dataset(_DatasetMetadata(crypt4gh_header="header", expiration="not-a-date"))

    with pytest.raises(Crypt4GHRemoteExecutionError, match="Invalid Crypt4GH compute key expiration timestamp"):
        should_run_crypt4gh_remote_execution(
            job_io=_JobIO([dataset]),
            app_config=_Config(enable_crypt4gh_transparent_staging=True),
            destination_params={"tool_evaluation_strategy": "remote"},
        )

def test_helper_setup_failures_no_expiration_time_zone_fail_closed():
    dataset = _Dataset(_DatasetMetadata(crypt4gh_header="header", expiration="2024-06-02T12:00:00"))

    with pytest.raises(Crypt4GHRemoteExecutionError, match="timezone"):
        should_run_crypt4gh_remote_execution(
            job_io=_JobIO([dataset]),
            app_config=_Config(enable_crypt4gh_transparent_staging=True),
            destination_params={"tool_evaluation_strategy": "remote"},
        )

def test_helper_setup_no_failures(crypt4gh_dataset):
    result = should_run_crypt4gh_remote_execution(
        job_io=_JobIO([crypt4gh_dataset]),
        app_config=_Config(enable_crypt4gh_transparent_staging=True),
        destination_params={"tool_evaluation_strategy": "remote"},
        now=datetime.fromisoformat("2026-06-01T11:00:00+00:00")
    )
    assert result is True


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
    assert CRYPT4GH_CLEANUP_FAILED_MARKER in completed.stderr


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
            job=object(),
            working_directory="/tmp",
            reencryption_service_url="http://example.invalid",
            minimum_ttl=timedelta(days=1),
            now=datetime.fromisoformat("2026-06-01T00:00:00+00:00"),
        )

    assert recrypt_attempted is False


def test_finalize_discovered_outputs_writes_path_markers(tmp_path, monkeypatch):
    discover_directory = tmp_path / "discover"
    discover_directory.mkdir(parents=True, exist_ok=True)
    first_discovered = discover_directory / "sample1.txt"
    second_discovered = discover_directory / "sample2.txt"
    first_discovered.write_text("one\n")
    second_discovered.write_text("two\n")
    marker_directory = tmp_path / "markers"
    marker_directory.mkdir(parents=True, exist_ok=True)

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

    finalize_declared_crypt4gh_outputs(
        output_targets=[
            {
                "discover_pattern": r".+\.txt",
                "discover_directory": str(discover_directory),
                "assign_primary_output": False,
                "encrypted_ext": "txt.c4gh",
                "marker_dir": str(marker_directory),
            }
        ],
        reencryption_service_url="http://example.invalid",
        compute_public_key="unused",
        compute_keypair_id="unused",
    )

    marker_files = sorted(marker_directory.glob("path_*.encrypted"))
    assert len(marker_files) == 2
    assert all(marker.read_text() == "txt.c4gh\n" for marker in marker_files)


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

    assert len(targets) == 1
    assert targets[0]["output_path"] == str(output_path)
    assert targets[0]["encrypted_ext"] == "tabular.c4gh"
    assert targets[0]["encrypted_marker_path"] == str(tmp_path / "_c4gh_stage" / "outputs" / "ds_4.encrypted")
