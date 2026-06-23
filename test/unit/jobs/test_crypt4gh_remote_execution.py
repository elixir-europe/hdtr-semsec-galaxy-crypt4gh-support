from datetime import datetime
import subprocess

import pytest

from galaxy.jobs.runners import BaseJobRunner
from galaxy.tools.crypt4gh_remote_execution import (
    build_crypt4gh_cleanup_wrapped_command,
    CRYPT4GH_CLEANUP_FAILED_MARKER,
    Crypt4GHRemoteExecutionError,
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


class _RunnerJobWrapper:
    def __init__(self, *, enable_crypt4gh_transparent_staging):
        self.app = _RunnerApp(enable_crypt4gh_transparent_staging=enable_crypt4gh_transparent_staging)


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


def test_prepare_job_freezes_old_staging_path_when_remote_strategy_is_enabled():
    job_wrapper = _RunnerJobWrapper(enable_crypt4gh_transparent_staging=True)

    command_line = BaseJobRunner._apply_crypt4gh_staging(object(), job_wrapper, "echo hello")

    assert command_line == "echo hello"


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
