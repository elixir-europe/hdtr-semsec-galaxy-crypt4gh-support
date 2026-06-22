from datetime import (
    datetime,
    timedelta,
    timezone,
)
from typing import (
    Any,
    Optional,
)

from dateutil.parser import isoparse


class Crypt4GHRemoteExecutionError(Exception):
    """Raised when execution-side Crypt4GH setup must fail closed."""


def should_run_crypt4gh_remote_execution(
    *,
    job_io,
    app_config,
    destination_params: dict[str, Any],
    minimum_ttl: timedelta = timedelta(days=1),
    now: Optional[datetime] = None,
) -> bool:
    """Decide whether execution-side Crypt4GH setup is allowed for this job.

    The helper is intentionally small for the Task 3 contract:
    - top-level gate: ``enable_crypt4gh_transparent_staging``
    - execution path only when ``tool_evaluation_strategy == "remote"``
    - only if at least one Crypt4GH input dataset is present
    - setup failures must fail closed
    """

    if not bool(getattr(app_config, "enable_crypt4gh_transparent_staging", False)):
        return False

    if destination_params.get("tool_evaluation_strategy") != "remote":
        return False

    crypt4gh_inputs = tuple(_crypt4gh_inputs(job_io))
    if not crypt4gh_inputs:
        return False

    current_time = now or datetime.now(timezone.utc)
    _assert_minimum_ttl(datasets=crypt4gh_inputs, minimum_ttl=minimum_ttl, now=current_time)
    return True

def _crypt4gh_inputs(job_io):
    for dataset in job_io.get_input_datasets():
        metadata = getattr(dataset, "metadata", None)
        if metadata and getattr(metadata, "crypt4gh_header", None):
            yield dataset


def _assert_minimum_ttl(*, datasets, minimum_ttl: timedelta, now: datetime) -> None:
    for dataset in datasets:
        metadata = getattr(dataset, "metadata", None)
        expires_raw = getattr(metadata, "crypt4gh_compute_keypair_expiration_date", None)
        if not expires_raw:
            raise Crypt4GHRemoteExecutionError(
                "Crypt4GH key metadata missing; minimum TTL cannot be validated before remote call"
            )

        expires_at = _parse_expiration(expires_raw)
        ttl_left = expires_at - now
        if ttl_left < minimum_ttl:
            raise Crypt4GHRemoteExecutionError(
                "Crypt4GH compute key violates minimum TTL requirement before remote call"
            )


def _parse_expiration(expires_raw: Any) -> datetime:
    if isinstance(expires_raw, datetime):
        expires_at = expires_raw
    elif isinstance(expires_raw, str):
        normalized = expires_raw.replace("Z", "+00:00")
        try:
            expires_at = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise Crypt4GHRemoteExecutionError("Invalid Crypt4GH compute key expiration timestamp") from exc
    else:
        raise Crypt4GHRemoteExecutionError("Invalid Crypt4GH compute key expiration timestamp")

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    return expires_at.astimezone(timezone.utc)
