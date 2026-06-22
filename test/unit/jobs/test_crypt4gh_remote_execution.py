from datetime import datetime, timedelta, timezone

import pytest

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


def test_top_level_gate_disables_remote_helper_setup():
    called = False

    def before_remote_call():
        nonlocal called
        called = True

    result = setup_crypt4gh_remote_execution(
        job_io=_JobIO([]),
        app_config=_Config(enable_crypt4gh_transparent_staging=False),
        destination_params={"tool_evaluation_strategy": "remote"},
        before_remote_call=before_remote_call,
    )

    assert result.enabled is False
    assert called is False


def test_helper_path_requires_remote_tool_evaluation_strategy():
    result = setup_crypt4gh_remote_execution(
        job_io=_JobIO([]),
        app_config=_Config(enable_crypt4gh_transparent_staging=True),
        destination_params={"tool_evaluation_strategy": "local"},
    )

    assert result.enabled is False


def test_minimum_ttl_gate_runs_before_any_remote_call():
    called = False

    def before_remote_call():
        nonlocal called
        called = True

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    too_soon = (now + timedelta(minutes=5)).isoformat()
    dataset = _Dataset(_DatasetMetadata(crypt4gh_header="header", expiration=too_soon))

    with pytest.raises(Crypt4GHRemoteExecutionError, match="minimum TTL"):
        setup_crypt4gh_remote_execution(
            job_io=_JobIO([dataset]),
            app_config=_Config(enable_crypt4gh_transparent_staging=True),
            destination_params={"tool_evaluation_strategy": "remote"},
            minimum_ttl=timedelta(minutes=10),
            now=now,
            before_remote_call=before_remote_call,
        )

    assert called is False


def test_helper_setup_failures_fail_closed():
    def before_remote_call():
        raise RuntimeError("boom")

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    valid = (now + timedelta(hours=3)).isoformat()
    dataset = _Dataset(_DatasetMetadata(crypt4gh_header="header", expiration=valid))

    with pytest.raises(Crypt4GHRemoteExecutionError, match="Failed to initialize"):
        setup_crypt4gh_remote_execution(
            job_io=_JobIO([dataset]),
            app_config=_Config(enable_crypt4gh_transparent_staging=True),
            destination_params={"tool_evaluation_strategy": "remote"},
            minimum_ttl=timedelta(minutes=10),
            now=now,
            before_remote_call=before_remote_call,
        )
