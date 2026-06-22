from datetime import datetime

import pytest

from galaxy.jobs.runners import BaseJobRunner
from galaxy.tools.crypt4gh_remote_execution import (
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
