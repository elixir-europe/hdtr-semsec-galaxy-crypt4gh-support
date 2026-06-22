from datetime import datetime, timedelta, timezone

import pytest

from galaxy.jobs.runners import BaseJobRunner
from galaxy.tools.crypt4gh_remote_execution import (
    Crypt4GHRemoteExecutionError,
    setup_crypt4gh_remote_execution,
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
    def __init__(self, *, enable_crypt4gh_transparent_staging, tool_evaluation_strategy):
        self.app = _RunnerApp(enable_crypt4gh_transparent_staging=enable_crypt4gh_transparent_staging)
        self._tool_evaluation_strategy = tool_evaluation_strategy

    def get_destination_configuration(self, key, default=None):
        if key == "tool_evaluation_strategy":
            return self._tool_evaluation_strategy
        return default


def test_top_level_gate_disables_remote_helper_setup():
    result = setup_crypt4gh_remote_execution(
        job_io=_JobIO([]),
        app_config=_Config(enable_crypt4gh_transparent_staging=False),
        destination_params={"tool_evaluation_strategy": "remote"},
    )

    assert result is None


def test_helper_path_requires_remote_tool_evaluation_strategy():
    result = setup_crypt4gh_remote_execution(
        job_io=_JobIO([]),
        app_config=_Config(enable_crypt4gh_transparent_staging=True),
        destination_params={"tool_evaluation_strategy": "local"},
    )

    assert result is None


def test_helper_setup_failures_fail_closed():
    dataset = _Dataset(_DatasetMetadata(crypt4gh_header="header", expiration="not-a-date"))

    with pytest.raises(Crypt4GHRemoteExecutionError, match="Invalid Crypt4GH compute key expiration timestamp"):
        setup_crypt4gh_remote_execution(
            job_io=_JobIO([dataset]),
            app_config=_Config(enable_crypt4gh_transparent_staging=True),
            destination_params={"tool_evaluation_strategy": "remote"},
        )


def test_prepare_job_freezes_old_staging_path_when_remote_strategy_is_enabled():
    job_wrapper = _RunnerJobWrapper(enable_crypt4gh_transparent_staging=True, tool_evaluation_strategy="remote")

    command_line = BaseJobRunner._apply_crypt4gh_staging(object(), job_wrapper, "echo hello")

    assert command_line == "echo hello"
