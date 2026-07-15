"""Crypt4GH helpers for remote tool execution and output finalization."""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import io
import json
import os
import re
import shutil
import ssl
from dataclasses import dataclass
from datetime import (
    datetime,
    timedelta,
    timezone,
)
from pathlib import Path
from urllib.parse import urlsplit
from collections.abc import (
    Mapping,
    Sequence,
)
from typing import (
    Any,
    BinaryIO,
    Optional,
    Protocol,
    TYPE_CHECKING,
    cast,
)
from logging import getLogger


import crypt4gh.header
import crypt4gh.lib
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from dateutil.parser import isoparse
import aiohttp
from galaxy.job_execution.compute_environment import (
    dataset_path_to_extra_path,
    SharedComputeEnvironment,
)

try:
    import truststore as _optional_truststore
except Exception:
    _optional_truststore = None

if TYPE_CHECKING:
    from galaxy.job_execution.setup import JobIO
    from galaxy.model import (
        DatasetInstance,
        Job,
    )

log = getLogger(__name__)


@dataclass(frozen=True)
class _ReencryptionHttpResponse:
    status_code: int
    text: str
    json_payload: Any

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


def _is_dev_style_https_reencryption_url(*, reencryption_service_url: str) -> bool:
    parsed_url = urlsplit(reencryption_service_url)
    if parsed_url.scheme.lower() != "https":
        return False

    hostname = parsed_url.hostname
    if not hostname:
        return False

    if hostname == "localhost":
        return True

    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return "." not in hostname


def _ssl_context_for_reencryption_url(
    *,
    reencryption_service_url: str,
    truststore_module: Any,
) -> Optional[ssl.SSLContext]:
    if not _is_dev_style_https_reencryption_url(reencryption_service_url=reencryption_service_url):
        return None

    if truststore_module is None:
        log.warning(
            "Crypt4GH re-encryption service URL %s matches local/dev HTTPS pattern, "
            "but truststore is unavailable; falling back to default TLS handling",
            reencryption_service_url,
        )
        return None

    try:
        ssl_context = truststore_module.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        log.warning(
            "Failed to initialize truststore SSL context for Crypt4GH re-encryption service URL %s; "
            "falling back to default TLS handling",
            reencryption_service_url,
            exc_info=True,
        )
        return None

    return ssl_context


async def _post_reencryption_json_async(
    *,
    endpoint: str,
    payload: dict[str, str],
    ssl_context: Optional[ssl.SSLContext],
) -> _ReencryptionHttpResponse:
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as request_session:
        async with request_session.post(endpoint, json=payload, ssl=ssl_context) as response:
            response_text = await response.text()
            try:
                response_json: Any = json.loads(response_text) if response_text else None
            except ValueError:
                response_json = None

            return _ReencryptionHttpResponse(
                status_code=response.status,
                text=response_text,
                json_payload=response_json,
            )


async def _post_many_reencryption_json_async(
    *,
    endpoint: str,
    payloads: Sequence[dict[str, str]],
    ssl_context: Optional[ssl.SSLContext],
) -> list[_ReencryptionHttpResponse]:
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as request_session:
        requests_to_send = [
            request_session.post(endpoint, json=payload, ssl=ssl_context)
            for payload in payloads
        ]
        responses = await asyncio.gather(*requests_to_send)
        try:
            results: list[_ReencryptionHttpResponse] = []
            for response in responses:
                response_text = await response.text()
                try:
                    response_json: Any = json.loads(response_text) if response_text else None
                except ValueError:
                    response_json = None

                results.append(
                    _ReencryptionHttpResponse(
                        status_code=response.status,
                        text=response_text,
                        json_payload=response_json,
                    )
                )
            return results
        finally:
            for response in responses:
                response.release()


def _post_reencryption_json(*, reencryption_service_url: str, endpoint: str, payload: dict[str, str]) -> _ReencryptionHttpResponse:
    ssl_context = _ssl_context_for_reencryption_url(
        reencryption_service_url=reencryption_service_url,
        truststore_module=_optional_truststore,
    )
    return asyncio.run(
        _post_reencryption_json_async(
            endpoint=endpoint,
            payload=payload,
            ssl_context=ssl_context,
        )
    )


def _post_many_reencryption_json(
    *,
    reencryption_service_url: str,
    endpoint: str,
    payloads: Sequence[dict[str, str]],
) -> list[_ReencryptionHttpResponse]:
    ssl_context = _ssl_context_for_reencryption_url(
        reencryption_service_url=reencryption_service_url,
        truststore_module=_optional_truststore,
    )
    return asyncio.run(
        _post_many_reencryption_json_async(
            endpoint=endpoint,
            payloads=payloads,
            ssl_context=ssl_context,
        )
    )

class _Crypt4GHAppConfig(Protocol):
    enable_crypt4gh_transparent_staging: bool


class Crypt4GHRemoteExecutionError(Exception):
    """Raised when execution-side Crypt4GH setup must fail closed."""


CRYPT4GH_PLAINTEXT_CLEANUP_FAILED_MARKER = "CRYPT4GH_PLAINTEXT_CLEANUP_FAILED"
_DEFAULT_MINIMUM_TTL = timedelta(days=1)
_DESTINATION_WALLTIME_BUFFER = timedelta(hours=1)


class _HeaderThenBodyStream:
    """Stream wrapper that prepends a replacement Crypt4GH header."""

    def __init__(self, *, header_bytes: bytes, body_stream: BinaryIO) -> None:
        self._header = memoryview(header_bytes)
        self._header_pos = 0
        self._body_stream = body_stream

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            header_remainder = self._header[self._header_pos :].tobytes()
            self._header_pos = len(self._header)
            body_remainder = self._body_stream.read()
            return header_remainder + body_remainder

        if size == 0:
            return b""

        chunks: list[bytes] = []
        header_remaining = len(self._header) - self._header_pos
        if header_remaining > 0:
            take = min(size, header_remaining)
            chunks.append(self._header[self._header_pos : self._header_pos + take].tobytes())
            self._header_pos += take

        body_remaining = size - sum(len(chunk) for chunk in chunks)
        if body_remaining > 0:
            chunks.append(self._body_stream.read(body_remaining))

        return b"".join(chunks)

    def readinto(self, buffer: bytearray) -> int:
        data = self.read(len(buffer))
        bytes_read = len(data)
        if bytes_read:
            buffer[:bytes_read] = data
        return bytes_read


@dataclass(frozen=True)
class _RecryptToJobKeyResult:
    crypt4gh_header: str
    crypt4gh_compute_public_key: str
    crypt4gh_compute_keypair_id: str
    crypt4gh_compute_keypair_expiration_date: str


@dataclass(frozen=True)
class _DeclaredCrypt4GHOutputTarget:
    association_name: str
    output_path: str
    dataset_output_path: Optional[str]
    extra_files_output_path: Optional[str]
    extra_files_manifest_path: Optional[str]
    plaintext_path: str
    encrypted_marker_path: str
    encrypted_ext: str
    clear_compute_keypair: bool


@dataclass(frozen=True)
class _ComputeKeyContext:
    """Compute-side key context inferred from recrypt responses."""

    public_key: str
    keypair_id: str
    keypair_expiration_date: str


class Crypt4GHRemoteComputeEnvironment(SharedComputeEnvironment):
    """Compute environment that rewrites remote input paths for Crypt4GH jobs."""

    def __init__(
        self,
        *,
        job_io: JobIO,
        job: Job,
        input_path_overrides_by_dataset_id: Mapping[int, str],
        compute_public_key: Optional[str],
        compute_keypair_id: Optional[str],
        compute_keypair_expiration_date: Optional[str],
    ) -> None:
        super().__init__(job_io=job_io, job=job)
        self._input_path_overrides_by_dataset_id = dict(input_path_overrides_by_dataset_id)
        self.compute_public_key = compute_public_key
        self.compute_keypair_id = compute_keypair_id
        self.compute_keypair_expiration_date = compute_keypair_expiration_date

    def input_path_rewrite(self, dataset: DatasetInstance) -> str:
        """Return plaintext override path for staged Crypt4GH input datasets."""

        dataset_object = getattr(dataset, "dataset", None)
        dataset_id = getattr(dataset_object, "id", None)
        if isinstance(dataset_id, int):
            rewritten_path = self._input_path_overrides_by_dataset_id.get(dataset_id)
            if rewritten_path:
                return rewritten_path
        return super().input_path_rewrite(dataset)


def build_crypt4gh_remote_compute_environment(
    *,
    job_io: JobIO,
    job: Job,
    working_directory: str,
    reencryption_service_url: str,
    minimum_ttl: Optional[timedelta] = None,
    now: Optional[datetime] = None,
) -> Crypt4GHRemoteComputeEnvironment:
    """Build remote compute environment with staged plaintext Crypt4GH inputs."""

    crypt4gh_inputs = _collect_crypt4gh_inputs(job_io)
    if not crypt4gh_inputs:
        raise Crypt4GHRemoteExecutionError(
            "Crypt4GH remote execution requested but no Crypt4GH inputs were detected"
        )

    destination_params = dict(getattr(job, "destination_params", {}) or {})
    effective_minimum_ttl = _minimum_ttl_for_destination(
        destination_params=destination_params,
        fallback_minimum_ttl=minimum_ttl or _DEFAULT_MINIMUM_TTL,
    )
    current_time = now or datetime.now(timezone.utc)
    _assert_minimum_ttl(datasets=crypt4gh_inputs, minimum_ttl=effective_minimum_ttl, now=current_time)

    crypt_inputs_workspace = _ensure_crypt4gh_inputs_workspace(working_directory)
    job_private_key, job_public_key = _generate_job_keypair()
    input_path_overrides_by_dataset_id, compute_context = _prepare_plaintext_inputs(
        datasets=crypt4gh_inputs,
        crypt_inputs_workspace=crypt_inputs_workspace,
        reencryption_service_url=reencryption_service_url,
        job_public_key=job_public_key,
        job_private_key=job_private_key,
    )

    return Crypt4GHRemoteComputeEnvironment(
        job_io=job_io,
        job=job,
        input_path_overrides_by_dataset_id=input_path_overrides_by_dataset_id,
        compute_public_key=compute_context.public_key,
        compute_keypair_id=compute_context.keypair_id,
        compute_keypair_expiration_date=compute_context.keypair_expiration_date,
    )


def _prepare_plaintext_inputs(
    *,
    datasets: Sequence[DatasetInstance],
    crypt_inputs_workspace: Path,
    reencryption_service_url: str,
    job_public_key: str,
    job_private_key: bytes,
) -> tuple[dict[int, str], _ComputeKeyContext]:
    endpoint = f"{reencryption_service_url.rstrip('/')}/recrypt_header_to_job_key"
    recrypt_payloads = _build_recrypt_payloads(datasets=datasets, job_public_key=job_public_key)
    responses = _post_many_reencryption_json(
        reencryption_service_url=reencryption_service_url,
        endpoint=endpoint,
        payloads=[payload["request_payload"] for payload in recrypt_payloads],
    )
    if len(responses) != len(recrypt_payloads):
        raise Crypt4GHRemoteExecutionError(
            "Compute-side recryptor B must return one response per dataset for /recrypt_header_to_job_key"
        )

    input_path_overrides_by_dataset_id: dict[int, str] = {}
    compute_context: Optional[_ComputeKeyContext] = None

    for payload, response in zip(recrypt_payloads, responses):
        recrypt_result = _parse_recrypt_header_to_job_key_response(
            response=response,
            endpoint=endpoint,
        )
        dataset_id, plaintext_path = _prepare_plaintext_input_for_dataset(
            dataset=cast(Any, payload["dataset"]),
            source_header=cast(str, payload["source_header"]),
            recrypt_header=recrypt_result.crypt4gh_header,
            crypt_inputs_workspace=crypt_inputs_workspace,
            job_private_key=job_private_key,
        )
        input_path_overrides_by_dataset_id[dataset_id] = plaintext_path

        candidate_context = _ComputeKeyContext(
            public_key=recrypt_result.crypt4gh_compute_public_key,
            keypair_id=recrypt_result.crypt4gh_compute_keypair_id,
            keypair_expiration_date=recrypt_result.crypt4gh_compute_keypair_expiration_date,
        )
        if compute_context is None:
            compute_context = candidate_context
        else:
            _assert_consistent_compute_key_context(existing=compute_context, candidate=candidate_context)

    if compute_context is None:
        raise Crypt4GHRemoteExecutionError(
            "Crypt4GH remote execution requested but no Crypt4GH inputs were detected"
        )

    return input_path_overrides_by_dataset_id, compute_context


def _build_recrypt_payloads(
    *,
    datasets: Sequence[DatasetInstance],
    job_public_key: str,
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for dataset in datasets:
        dataset_object = getattr(dataset, "dataset", None)
        dataset_id = getattr(dataset_object, "id", None)
        if not isinstance(dataset_id, int):
            raise Crypt4GHRemoteExecutionError("Crypt4GH input dataset is missing a persisted dataset id")

        metadata = getattr(dataset, "metadata", None)
        header = getattr(metadata, "crypt4gh_header", None)
        keypair_id = getattr(metadata, "crypt4gh_compute_keypair_id", None)
        if not header or not keypair_id:
            raise Crypt4GHRemoteExecutionError(
                "Crypt4GH input metadata must include crypt4gh_header and crypt4gh_compute_keypair_id"
            )

        payloads.append(
            {
                "dataset": dataset,
                "dataset_id": dataset_id,
                "source_header": cast(str, header),
                "request_payload": {
                    "crypt4gh_header": cast(str, header),
                    "crypt4gh_compute_keypair_id": cast(str, keypair_id),
                    "crypt4gh_job_public_key": job_public_key,
                },
            }
        )
    return payloads


def _parse_recrypt_header_to_job_key_response(
    *,
    response: _ReencryptionHttpResponse,
    endpoint: str,
) -> _RecryptToJobKeyResult:
    if not response.ok:
        raise Crypt4GHRemoteExecutionError(
            f"Compute-side recryptor B returned HTTP {response.status_code} for {endpoint}: "
            f"{_summarize_http_error_response(response)}"
        )

    try:
        response_json = response.json_payload
        if not isinstance(response_json, dict):
            raise ValueError("Response body is not a JSON object")

        return _RecryptToJobKeyResult(
            crypt4gh_header=cast(str, response_json["crypt4gh_header"]),
            crypt4gh_compute_public_key=cast(str, response_json["crypt4gh_compute_public_key"]),
            crypt4gh_compute_keypair_id=cast(str, response_json["crypt4gh_compute_keypair_id"]),
            crypt4gh_compute_keypair_expiration_date=cast(str, response_json["crypt4gh_compute_keypair_expiration_date"]),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise Crypt4GHRemoteExecutionError(
            "Compute-side recryptor B returned an invalid /recrypt_header_to_job_key payload"
        ) from exc


def _assert_consistent_compute_key_context(*, existing: _ComputeKeyContext, candidate: _ComputeKeyContext) -> None:
    if existing.public_key != candidate.public_key:
        raise Crypt4GHRemoteExecutionError(
            "Crypt4GH job inputs reference multiple compute public keys; mixed key contexts are unsupported"
        )
    if existing.keypair_id != candidate.keypair_id:
        raise Crypt4GHRemoteExecutionError(
            "Crypt4GH job inputs reference multiple compute keypair ids; mixed key contexts are unsupported"
        )
    if existing.keypair_expiration_date != candidate.keypair_expiration_date:
        raise Crypt4GHRemoteExecutionError(
            "Crypt4GH job inputs reference multiple compute key expiry timestamps; mixed key contexts are unsupported"
        )


def should_run_crypt4gh_remote_execution(
    *,
    job_io: JobIO,
    app_config: _Crypt4GHAppConfig,
    destination_params: dict[str, Any],
    minimum_ttl: Optional[timedelta] = None,
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

    crypt4gh_inputs = _collect_crypt4gh_inputs(job_io)
    if not crypt4gh_inputs:
        return False

    effective_minimum_ttl = _minimum_ttl_for_destination(
        destination_params=destination_params,
        fallback_minimum_ttl=minimum_ttl or _DEFAULT_MINIMUM_TTL,
    )
    current_time = now or datetime.now(timezone.utc)
    _assert_minimum_ttl(datasets=crypt4gh_inputs, minimum_ttl=effective_minimum_ttl, now=current_time)
    return True


def _minimum_ttl_for_destination(*, destination_params: Mapping[str, Any], fallback_minimum_ttl: timedelta) -> timedelta:
    walltime_value = destination_params.get("walltime")
    walltime_delta = _parse_destination_walltime(walltime_value)
    if walltime_delta is None:
        return fallback_minimum_ttl
    return walltime_delta + _DESTINATION_WALLTIME_BUFFER


def _parse_destination_walltime(walltime_value: Any) -> Optional[timedelta]:
    if not isinstance(walltime_value, str):
        return None
    parts = walltime_value.strip().split(":")
    if len(parts) != 3:
        return None
    try:
        hours, minutes, seconds = (int(part) for part in parts)
    except ValueError:
        return None
    if hours < 0 or minutes < 0 or seconds < 0:
        return None
    if minutes >= 60 or seconds >= 60:
        return None
    return timedelta(hours=hours, minutes=minutes, seconds=seconds)


def _collect_crypt4gh_inputs(job_io: JobIO) -> list[DatasetInstance]:
    datasets: list[DatasetInstance] = []
    for dataset in job_io.get_input_datasets():
        metadata = getattr(dataset, "metadata", None)
        if metadata and getattr(metadata, "crypt4gh_header", None):
            datasets.append(dataset)
    return datasets


def _ensure_crypt4gh_inputs_workspace(working_directory: str) -> Path:
    workspace = Path(working_directory) / "_crypt" / "inputs"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def cleanup_crypt4gh_plaintext_artifacts(*, working_directory: str) -> None:
    """Delete staged Crypt4GH plaintext input and output directories."""

    crypt_root = Path(working_directory) / "_crypt"
    for child_name in ("inputs", "outputs"):
        child_path = crypt_root / child_name
        if child_path.exists():
            shutil.rmtree(child_path)


def _format_crypt4gh_public_key(public_key: bytes) -> str:
    encoded_public_key = base64.b64encode(public_key).decode("ascii")
    return "\n".join(
        [
            "-----BEGIN CRYPT4GH PUBLIC KEY-----",
            encoded_public_key,
            "-----END CRYPT4GH PUBLIC KEY-----",
        ]
    )


def _generate_job_keypair() -> tuple[bytes, str]:
    private_key = X25519PrivateKey.generate()
    private_key_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_key_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private_key_bytes, _format_crypt4gh_public_key(public_key_bytes)


def _prepare_plaintext_input_for_dataset(
    *,
    dataset: DatasetInstance,
    source_header: str,
    recrypt_header: str,
    crypt_inputs_workspace: Path,
    job_private_key: bytes,
) -> tuple[int, str]:
    dataset_object = getattr(dataset, "dataset", None)
    dataset_id = getattr(dataset_object, "id", None)
    if not isinstance(dataset_id, int):
        raise Crypt4GHRemoteExecutionError("Crypt4GH input dataset is missing a persisted dataset id")

    dataset_workspace = crypt_inputs_workspace / f"ds_{dataset_id}"
    dataset_workspace.mkdir(parents=True, exist_ok=True)
    plaintext_path = dataset_workspace / "plaintext"

    source_dataset_path = Path(dataset.get_file_name())
    _decrypt_recrypted_input(
        source_dataset_path=source_dataset_path,
        source_header=source_header,
        recrypted_header=recrypt_header,
        plaintext_path=plaintext_path,
        job_private_key=job_private_key,
    )
    return dataset_id, str(plaintext_path)


def _recrypt_header_to_job_key(
    *,
    reencryption_service_url: str,
    crypt4gh_header: str,
    compute_keypair_id: str,
    job_public_key: str,
) -> _RecryptToJobKeyResult:
    endpoint = f"{reencryption_service_url.rstrip('/')}/recrypt_header_to_job_key"
    payload = {
        "crypt4gh_header": crypt4gh_header,
        "crypt4gh_compute_keypair_id": compute_keypair_id,
        "crypt4gh_job_public_key": job_public_key,
    }
    try:
        response = _post_reencryption_json(
            reencryption_service_url=reencryption_service_url,
            endpoint=endpoint,
            payload=payload,
        )
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise Crypt4GHRemoteExecutionError(
            f"Failed to contact compute-side recryptor B at {endpoint}: {exc}"
        ) from exc

    if not response.ok:
        raise Crypt4GHRemoteExecutionError(
            f"Compute-side recryptor B returned HTTP {response.status_code} for {endpoint}: "
            f"{_summarize_http_error_response(response)}"
        )

    try:
        response_json = response.json_payload
        if not isinstance(response_json, dict):
            raise ValueError("Response body is not a JSON object")
        return _RecryptToJobKeyResult(
            crypt4gh_header=cast(str, response_json["crypt4gh_header"]),
            crypt4gh_compute_public_key=cast(str, response_json["crypt4gh_compute_public_key"]),
            crypt4gh_compute_keypair_id=cast(str, response_json["crypt4gh_compute_keypair_id"]),
            crypt4gh_compute_keypair_expiration_date=cast(str, response_json["crypt4gh_compute_keypair_expiration_date"]),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise Crypt4GHRemoteExecutionError(
            "Compute-side recryptor B returned an invalid /recrypt_header_to_job_key payload"
        ) from exc


def collect_declared_crypt4gh_output_targets(
    *,
    job_io: JobIO,
    tool_outputs: Mapping[str, Any],
    datatypes_registry: Any,
    working_directory: str,
) -> list[dict[str, Any]]:
    """Collect declared non-discovery outputs that require Crypt4GH finalization."""

    targets: list[dict[str, Any]] = []
    marker_dir = Path(working_directory) / "_c4gh_stage" / "outputs"
    plaintext_root = Path(working_directory) / "_crypt" / "outputs"
    tool_working_directory = Path(working_directory) / "working"

    for output_name, (dataset, dataset_path) in job_io.get_output_hdas_and_fnames().items():
        if output_name.startswith("__new_primary_file_"):
            continue

        output_name_for_tool_lookup = _resolve_output_name_for_tool_lookup(
            output_name=output_name,
            tool_outputs=tool_outputs,
        )
        if output_name_for_tool_lookup is None:
            raise Crypt4GHRemoteExecutionError(
                f"Crypt4GH output target '{output_name}' has no matching tool output declaration"
            )
        dataset_object = getattr(dataset, "dataset", None)
        dataset_id = getattr(dataset_object, "id", None)
        if not isinstance(dataset_id, int):
            raise Crypt4GHRemoteExecutionError(
                f"Crypt4GH output target '{output_name}' is missing a persisted dataset id"
            )

        tool_output = tool_outputs.get(output_name_for_tool_lookup)
        if _is_discovery_routed_output(tool_output):
            continue

        base_ext = _resolve_base_output_extension(dataset=dataset, tool_output=tool_output)
        if base_ext is None:
            raise Crypt4GHRemoteExecutionError(
                f"Crypt4GH output target '{output_name}' could not resolve encrypted output extension"
            )

        encrypted_ext = _ensure_crypt4gh_output_datatype(
            datatypes_registry=datatypes_registry,
            base_ext=base_ext,
        )
        output_path = _resolve_output_path(
            dataset_path=dataset_path,
            tool_output=tool_output,
            tool_working_directory=tool_working_directory,
        )

        target = _DeclaredCrypt4GHOutputTarget(
            association_name=output_name,
            output_path=output_path,
            dataset_output_path=cast(Optional[str], getattr(dataset_path, "real_path", None)),
            extra_files_output_path=cast(Optional[str], getattr(dataset_path, "false_extra_files_path", None))
            or dataset_path_to_extra_path(output_path),
            extra_files_manifest_path=str(marker_dir / f"ds_{dataset_id}.extra_files_manifest.json"),
            plaintext_path=str(plaintext_root / f"ds_{dataset_id}" / "plaintext"),
            encrypted_marker_path=str(marker_dir / f"ds_{dataset_id}.encrypted"),
            encrypted_ext=encrypted_ext,
            clear_compute_keypair=True,
        )
        targets.append(_declared_output_target_to_mapping(target))

    return targets


def _is_discovery_routed_output(tool_output: Any) -> bool:
    if tool_output is None:
        return False
    collectors = list(getattr(tool_output, "dataset_collector_descriptions", []) or [])
    return any(bool(getattr(collector, "assign_primary_output", False)) for collector in collectors)


def _resolve_output_name_for_tool_lookup(*, output_name: str, tool_outputs: Mapping[str, Any]) -> Optional[str]:
    if output_name in tool_outputs:
        return output_name
    if output_name.startswith("__new_primary_file_"):
        return output_name[len("__new_primary_file_") :].split("|", 1)[0]
    return None


def _resolve_base_output_extension(*, dataset: Any, tool_output: Any) -> Optional[str]:
    base_ext = cast(str, getattr(dataset, "ext", "") or "")
    log.warning(base_ext)
    if not base_ext:
        return None

    if base_ext.endswith(".c4gh"):
        base_ext = base_ext[: -len(".c4gh")]

    if base_ext in ("auto", "data", "_sniff_"):
        declared_ext = getattr(tool_output, "format", None) if tool_output else None
        if declared_ext and declared_ext not in ("auto", "data", "_sniff_", "input"):
            return cast(str, declared_ext)
        return None
    return base_ext


def _ensure_crypt4gh_output_datatype(*, datatypes_registry: Any, base_ext: str) -> str:
    encrypted_ext = f"{base_ext}.c4gh"
    if datatypes_registry.get_datatype_by_extension(encrypted_ext) is None:
        datatypes_registry.get_or_create_crypt4gh_datatype(base_ext)
    return encrypted_ext


def _resolve_output_path(*, dataset_path: Any, tool_output: Any, tool_working_directory: Path) -> str:
    from_work_dir = getattr(tool_output, "from_work_dir", None) if tool_output else None
    if from_work_dir:
        from_work_dir_path = Path(str(from_work_dir))
        if from_work_dir_path.is_absolute():
            return str(from_work_dir_path)
        return str(tool_working_directory / from_work_dir_path)
    return cast(
        str,
        getattr(dataset_path, "false_path", None)
        or getattr(dataset_path, "real_path", None)
        or str(dataset_path),
    )


def _declared_output_target_to_mapping(target: _DeclaredCrypt4GHOutputTarget) -> dict[str, Any]:
    mapping = {
        "association_name": target.association_name,
        "output_path": target.output_path,
        "plaintext_path": target.plaintext_path,
        "encrypted_marker_path": target.encrypted_marker_path,
        "encrypted_ext": target.encrypted_ext,
        "clear_compute_keypair": target.clear_compute_keypair,
    }
    if target.dataset_output_path:
        mapping["dataset_output_path"] = target.dataset_output_path
    if target.extra_files_output_path:
        mapping["extra_files_output_path"] = target.extra_files_output_path
    if target.extra_files_manifest_path:
        mapping["extra_files_manifest_path"] = target.extra_files_manifest_path
    return mapping


def finalize_declared_crypt4gh_outputs(
    *,
    output_targets: Sequence[Mapping[str, Any]],
    reencryption_service_url: str,
    compute_public_key: str,
    compute_keypair_id: str,
    compute_keypair_expiration_date: Optional[str] = None,
    now: Optional[datetime] = None,
) -> None:
    """Encrypt declared outputs to user keys and persist extension markers."""

    current_time = now or datetime.now(timezone.utc)
    if compute_keypair_expiration_date:
        _assert_key_valid_for_output_finalization(
            compute_keypair_expiration_date=compute_keypair_expiration_date,
            now=current_time,
        )

    resolved_targets = _iter_unique_existing_output_targets(output_targets)
    try:
        for concrete_target, output_path in resolved_targets:
            _finalize_output_target(
                concrete_target=concrete_target,
                output_path=output_path,
                reencryption_service_url=reencryption_service_url,
                compute_public_key=compute_public_key,
                compute_keypair_id=compute_keypair_id,
            )
            _finalize_extra_files_payloads(
                concrete_target=concrete_target,
                reencryption_service_url=reencryption_service_url,
                compute_public_key=compute_public_key,
                compute_keypair_id=compute_keypair_id,
            )
    except Exception:
        _purge_output_targets_after_finalization_failure(resolved_targets)
        raise


def finalize_about_to_persist_crypt4gh_payload(
    *,
    output_path: str,
    plaintext_path: str,
    encrypted_ext: str,
    reencryption_service_url: str,
    compute_public_key: str,
    compute_keypair_id: str,
    compute_keypair_expiration_date: Optional[str] = None,
    encrypted_marker_path: str = "",
    dataset_output_path: str = "",
    designation: str = "",
    discovered_marker_map_path: str = "",
    extra_files_output_path: str = "",
    extra_files_manifest_path: str = "",
    clear_compute_keypair: bool = True,
) -> None:
    concrete_target: dict[str, Any] = {
        "output_path": output_path,
        "plaintext_path": plaintext_path,
        "encrypted_ext": encrypted_ext,
        "encrypted_marker_path": encrypted_marker_path,
        "clear_compute_keypair": clear_compute_keypair,
    }
    if dataset_output_path:
        concrete_target["dataset_output_path"] = dataset_output_path
    if designation:
        concrete_target["designation"] = designation
    if discovered_marker_map_path:
        concrete_target["discovered_marker_map_path"] = discovered_marker_map_path
    if extra_files_output_path:
        concrete_target["extra_files_output_path"] = extra_files_output_path
    if extra_files_manifest_path:
        concrete_target["extra_files_manifest_path"] = extra_files_manifest_path

    if compute_keypair_expiration_date:
        _assert_key_valid_for_output_finalization(
            compute_keypair_expiration_date=compute_keypair_expiration_date,
            now=datetime.now(timezone.utc),
        )

    _finalize_output_target(
        concrete_target=concrete_target,
        output_path=Path(output_path),
        reencryption_service_url=reencryption_service_url,
        compute_public_key=compute_public_key,
        compute_keypair_id=compute_keypair_id,
    )


def _purge_output_targets_after_finalization_failure(
    resolved_targets: Sequence[tuple[dict[str, Any], Path]],
) -> None:
    for concrete_target, output_path in resolved_targets:
        candidate_paths = [output_path]
        dataset_output_path_value = str(concrete_target.get("dataset_output_path", "") or "")
        if dataset_output_path_value:
            candidate_paths.append(Path(dataset_output_path_value))
        for candidate_path in candidate_paths:
            try:
                if candidate_path.exists():
                    candidate_path.unlink()
            except Exception:
                log.exception("Failed to remove plaintext output candidate %s after Crypt4GH finalization failure", candidate_path)

        marker_path_value = str(concrete_target.get("encrypted_marker_path", "") or "")
        if marker_path_value:
            marker_path = Path(marker_path_value)
            try:
                if marker_path.exists():
                    marker_path.unlink()
            except Exception:
                log.exception(
                    "Failed to remove encrypted marker %s after Crypt4GH finalization failure",
                    marker_path,
                )

        extra_files_output_path_value = str(concrete_target.get("extra_files_output_path", "") or "")
        if extra_files_output_path_value:
            extra_files_output_path = Path(extra_files_output_path_value)
            try:
                if extra_files_output_path.exists() and extra_files_output_path.is_dir():
                    shutil.rmtree(extra_files_output_path)
            except Exception:
                log.exception(
                    "Failed to remove plaintext extra_files candidate %s after Crypt4GH finalization failure",
                    extra_files_output_path,
                )

        extra_files_manifest_path_value = str(concrete_target.get("extra_files_manifest_path", "") or "")
        if extra_files_manifest_path_value:
            extra_files_manifest_path = Path(extra_files_manifest_path_value)
            try:
                if extra_files_manifest_path.exists():
                    extra_files_manifest_path.unlink()
            except Exception:
                log.exception(
                    "Failed to remove extra_files manifest %s after Crypt4GH finalization failure",
                    extra_files_manifest_path,
                )


def _iter_unique_existing_output_targets(
    output_targets: Sequence[Mapping[str, Any]],
) -> list[tuple[dict[str, Any], Path]]:
    encrypted_paths: set[str] = set()
    resolved_targets: list[tuple[dict[str, Any], Path]] = []
    for target in output_targets:
        declared_output_path = target.get("output_path")
        if declared_output_path:
            declared_output = Path(str(declared_output_path))
            if not declared_output.exists():
                raise Crypt4GHRemoteExecutionError(
                    f"Crypt4GH declared output path does not exist: {declared_output}"
                )

        for concrete_target in _resolve_output_targets(target):
            output_path = Path(concrete_target["output_path"])
            if not output_path.exists():
                continue

            output_path_key = str(output_path)
            if output_path_key in encrypted_paths:
                continue

            encrypted_paths.add(output_path_key)
            resolved_targets.append((concrete_target, output_path))
    return resolved_targets


def _finalize_output_target(
    *,
    concrete_target: Mapping[str, Any],
    output_path: Path,
    reencryption_service_url: str,
    compute_public_key: str,
    compute_keypair_id: str,
) -> None:
    plaintext_path = Path(concrete_target["plaintext_path"])
    plaintext_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(output_path, plaintext_path)
    dataset_output_path_value = str(concrete_target.get("dataset_output_path", "") or "")
    dataset_output_path = Path(dataset_output_path_value) if dataset_output_path_value else None

    compute_encrypted_path = Path(f"{output_path}.compute.c4gh")
    final_tmp_path = Path(f"{output_path}.c4gh.tmp")
    output_replaced = False
    try:
        _encrypt_plaintext_to_compute_key(
            plaintext_path=plaintext_path,
            compute_encrypted_path=compute_encrypted_path,
            compute_public_key=compute_public_key,
        )
        _rewrite_output_header_to_user_key(
            compute_encrypted_path=compute_encrypted_path,
            final_output_tmp_path=final_tmp_path,
            reencryption_service_url=reencryption_service_url,
            compute_keypair_id=compute_keypair_id,
        )
        os.replace(final_tmp_path, output_path)
        output_replaced = True
        _write_output_markers(concrete_target)
    except Exception as exc:
        if not output_replaced and output_path.exists():
            output_path.unlink()
        if not output_replaced and dataset_output_path and dataset_output_path.exists():
            dataset_output_path.unlink()
        raise Crypt4GHRemoteExecutionError(
            f"Failed to finalize encrypted Crypt4GH output at {output_path}: {exc}"
        ) from exc
    finally:
        if compute_encrypted_path.exists():
            compute_encrypted_path.unlink()
        if final_tmp_path.exists():
            final_tmp_path.unlink()


def _write_output_markers(concrete_target: Mapping[str, Any]) -> None:
    marker_path_value = concrete_target.get("encrypted_marker_path")
    if marker_path_value:
        marker_path = Path(marker_path_value)
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(f"{concrete_target['encrypted_ext']}\n")

    designation = str(concrete_target.get("designation", "") or "")
    discovered_marker_map_path = str(concrete_target.get("discovered_marker_map_path", "") or "")
    if designation and discovered_marker_map_path:
        _write_discovered_designation_marker(
            marker_map_path=Path(discovered_marker_map_path),
            designation=designation,
            encrypted_ext=concrete_target["encrypted_ext"],
        )


def _resolve_output_targets(target: Mapping[str, Any]) -> list[dict[str, Any]]:
    output_path = target.get("output_path")
    if output_path:
        concrete_target = {
            "output_path": str(output_path),
            "plaintext_path": str(target["plaintext_path"]),
            "encrypted_ext": str(target["encrypted_ext"]),
            "encrypted_marker_path": str(target.get("encrypted_marker_path", "")),
            "clear_compute_keypair": bool(target.get("clear_compute_keypair", False)),
        }
        dataset_output_path = target.get("dataset_output_path")
        if dataset_output_path:
            concrete_target["dataset_output_path"] = str(dataset_output_path)
        extra_files_output_path = target.get("extra_files_output_path")
        if extra_files_output_path:
            concrete_target["extra_files_output_path"] = str(extra_files_output_path)
        extra_files_manifest_path = target.get("extra_files_manifest_path")
        if extra_files_manifest_path:
            concrete_target["extra_files_manifest_path"] = str(extra_files_manifest_path)
        return [concrete_target]

    if target.get("discover_pattern") is not None:
        raise Crypt4GHRemoteExecutionError(
            "Crypt4GH declared output finalization requires explicit output_path targets; "
            "discovered payloads must be finalized via discovery/persistence hooks"
        )

    return []


def _write_discovered_designation_marker(*, marker_map_path: Path, designation: str, encrypted_ext: str) -> None:
    marker_map_path.parent.mkdir(parents=True, exist_ok=True)
    marker_map: dict[str, str] = {}
    if marker_map_path.exists():
        try:
            loaded = json.loads(marker_map_path.read_text())
            if isinstance(loaded, dict):
                marker_map = {str(key): str(value) for key, value in loaded.items()}
        except Exception:
            marker_map = {}
    marker_map[designation] = encrypted_ext
    marker_map_path.write_text(json.dumps(marker_map))


def _finalize_extra_files_payloads(
    *,
    concrete_target: Mapping[str, Any],
    reencryption_service_url: str,
    compute_public_key: str,
    compute_keypair_id: str,
) -> None:
    extra_files_output_path_value = str(concrete_target.get("extra_files_output_path", "") or "")
    if not extra_files_output_path_value:
        return

    extra_files_output_path = Path(extra_files_output_path_value)
    if not extra_files_output_path.exists() or not extra_files_output_path.is_dir():
        return

    extra_files_manifest_path_value = str(concrete_target.get("extra_files_manifest_path", "") or "")
    if not extra_files_manifest_path_value:
        raise Crypt4GHRemoteExecutionError("Crypt4GH extra_files finalization requires an extra_files manifest path")

    extra_files_manifest_path = Path(extra_files_manifest_path_value)
    if extra_files_manifest_path.exists():
        extra_files_manifest_path.unlink()

    base_plaintext_path = Path(str(concrete_target["plaintext_path"]))
    expected_entries: set[str] = set()
    for root, _dirs, files in os.walk(extra_files_output_path):
        root_path = Path(root)
        for file_name in files:
            source_path = root_path / file_name
            relative_path = os.path.relpath(source_path, extra_files_output_path)
            normalized_relative_path = relative_path.replace(os.sep, "/")
            expected_entries.add(normalized_relative_path)

            extra_file_target = dict(concrete_target)
            extra_file_target["output_path"] = str(source_path)
            extra_file_target["plaintext_path"] = str(
                base_plaintext_path.parent / "extra_files" / normalized_relative_path / "plaintext"
            )
            extra_file_target["encrypted_marker_path"] = ""
            extra_file_target.pop("dataset_output_path", None)

            _finalize_output_target(
                concrete_target=extra_file_target,
                output_path=source_path,
                reencryption_service_url=reencryption_service_url,
                compute_public_key=compute_public_key,
                compute_keypair_id=compute_keypair_id,
            )
            _write_extra_files_manifest_entry(
                manifest_path=extra_files_manifest_path,
                relative_path=normalized_relative_path,
                encrypted_ext=str(concrete_target["encrypted_ext"]),
            )
            _assert_crypt4gh_payload_header(path=source_path)

    _assert_extra_files_manifest_complete(
        manifest_path=extra_files_manifest_path,
        expected_entries=expected_entries,
    )


def _write_extra_files_manifest_entry(*, manifest_path: Path, relative_path: str, encrypted_ext: str) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_payload: dict[str, Any] = {"files": {}}
    if manifest_path.exists():
        try:
            loaded_payload = json.loads(manifest_path.read_text())
            if isinstance(loaded_payload, dict):
                manifest_payload = loaded_payload
        except Exception:
            manifest_payload = {"files": {}}

    files_payload = manifest_payload.get("files")
    if not isinstance(files_payload, dict):
        files_payload = {}
        manifest_payload["files"] = files_payload

    files_payload[relative_path] = encrypted_ext
    manifest_path.write_text(json.dumps(manifest_payload))


def _assert_extra_files_manifest_complete(*, manifest_path: Path, expected_entries: set[str]) -> None:
    if not expected_entries:
        return

    if not manifest_path.exists():
        raise Crypt4GHRemoteExecutionError("Crypt4GH extra_files manifest missing entries")

    try:
        manifest_payload = json.loads(manifest_path.read_text())
    except Exception as exc:
        raise Crypt4GHRemoteExecutionError(
            f"Crypt4GH extra_files manifest is unreadable: {manifest_path}"
        ) from exc

    files_payload = manifest_payload.get("files") if isinstance(manifest_payload, dict) else None
    if not isinstance(files_payload, dict):
        raise Crypt4GHRemoteExecutionError(
            f"Crypt4GH extra_files manifest has invalid structure: {manifest_path}"
        )

    missing_entries = expected_entries - set(files_payload.keys())
    if missing_entries:
        raise Crypt4GHRemoteExecutionError("Crypt4GH extra_files manifest missing entries")


def _assert_crypt4gh_payload_header(*, path: Path) -> None:
    with path.open("rb") as payload_stream:
        if payload_stream.read(8) != b"crypt4gh":
            raise Crypt4GHRemoteExecutionError(f"Crypt4GH extra_files payload remained plaintext: {path}")


def build_crypt4gh_cleanup_wrapped_command(
    *, tool_command: str, cleanup_command: str, postrun_command: str = ""
) -> str:
    """Wrap tool command with postrun and cleanup steps that preserve failures."""

    postrun_command = postrun_command.strip()
    cleanup_command = cleanup_command.strip()
    if not cleanup_command:
        if not postrun_command:
            return tool_command
        lines = _build_tool_and_postrun_shell_lines(
            tool_command=tool_command,
            postrun_command=postrun_command,
            include_exit_checks=True,
        )
        return "\n".join(lines)

    lines = _build_tool_and_postrun_shell_lines(
        tool_command=tool_command,
        postrun_command=postrun_command,
        include_exit_checks=False,
    )
    marker_line = (
        f"    echo '{CRYPT4GH_PLAINTEXT_CLEANUP_FAILED_MARKER}: cleanup failed with exit code "
        "${_CRYPT4GH_CLEANUP_EXIT}' >&2"
    )
    lines.extend(
        [
            "if (",
            cleanup_command,
            "); then",
            "    _CRYPT4GH_CLEANUP_EXIT=0",
            "else",
            "    _CRYPT4GH_CLEANUP_EXIT=$?",
            marker_line,
            "fi",
            "if [ $_CRYPT4GH_TOOL_EXIT -ne 0 ]; then",
            "    exit $_CRYPT4GH_TOOL_EXIT",
            "fi",
            "if [ $_CRYPT4GH_POSTRUN_EXIT -ne 0 ]; then",
            "    exit $_CRYPT4GH_POSTRUN_EXIT",
            "fi",
            "if [ $_CRYPT4GH_CLEANUP_EXIT -ne 0 ]; then",
            "    exit $_CRYPT4GH_CLEANUP_EXIT",
            "fi",
        ]
    )
    return "\n".join(lines)


def _build_tool_and_postrun_shell_lines(
    *, tool_command: str, postrun_command: str, include_exit_checks: bool
) -> list[str]:
    lines = [
        "if (",
        tool_command,
        "); then",
        "    _CRYPT4GH_TOOL_EXIT=0",
        "else",
        "    _CRYPT4GH_TOOL_EXIT=$?",
        "fi",
    ]
    if postrun_command:
        lines.extend(
            [
                "if [ $_CRYPT4GH_TOOL_EXIT -eq 0 ]; then",
                "    if (",
                postrun_command,
                "    ); then",
                "        _CRYPT4GH_POSTRUN_EXIT=0",
                "    else",
                "        _CRYPT4GH_POSTRUN_EXIT=$?",
                "    fi",
                "else",
                "    _CRYPT4GH_POSTRUN_EXIT=0",
                "fi",
            ]
        )
    else:
        lines.append("_CRYPT4GH_POSTRUN_EXIT=0")

    if include_exit_checks:
        lines.extend(
            [
                "if [ $_CRYPT4GH_TOOL_EXIT -ne 0 ]; then",
                "    exit $_CRYPT4GH_TOOL_EXIT",
                "fi",
                "if [ $_CRYPT4GH_POSTRUN_EXIT -ne 0 ]; then",
                "    exit $_CRYPT4GH_POSTRUN_EXIT",
                "fi",
            ]
        )
    return lines


def _encrypt_plaintext_to_compute_key(
    *,
    plaintext_path: Path,
    compute_encrypted_path: Path,
    compute_public_key: str,
) -> None:
    recipient_key = _parse_crypt4gh_public_key(compute_public_key)
    ephemeral_private_key = X25519PrivateKey.generate().private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    with plaintext_path.open("rb") as plaintext_stream, compute_encrypted_path.open("wb") as encrypted_stream:
        crypt4gh.lib.encrypt([(0, ephemeral_private_key, recipient_key)], plaintext_stream, encrypted_stream)


def _rewrite_output_header_to_user_key(
    *,
    compute_encrypted_path: Path,
    final_output_tmp_path: Path,
    reencryption_service_url: str,
    compute_keypair_id: str,
) -> None:
    with compute_encrypted_path.open("rb") as encrypted_stream:
        list(crypt4gh.header.parse(encrypted_stream))
        header_length = encrypted_stream.tell()
        encrypted_stream.seek(0)
        encrypted_header = encrypted_stream.read(header_length)

    recrypted_header = _recrypt_header_to_user_key(
        reencryption_service_url=reencryption_service_url,
        crypt4gh_header=base64.b64encode(encrypted_header).decode("ascii"),
        compute_keypair_id=compute_keypair_id,
    )
    recrypted_header_bytes = _decode_header(recrypted_header)
    _header_length(recrypted_header_bytes)

    with compute_encrypted_path.open("rb") as encrypted_stream, final_output_tmp_path.open("wb") as final_stream:
        encrypted_stream.seek(header_length)
        final_stream.write(recrypted_header_bytes)
        shutil.copyfileobj(encrypted_stream, final_stream)


def _recrypt_header_to_user_key(
    *,
    reencryption_service_url: str,
    crypt4gh_header: str,
    compute_keypair_id: str,
) -> str:
    endpoint = f"{reencryption_service_url.rstrip('/')}/recrypt_header_to_user_key"
    payload = {
        "crypt4gh_header": crypt4gh_header,
        "crypt4gh_compute_keypair_id": compute_keypair_id,
    }
    try:
        response = _post_reencryption_json(
            reencryption_service_url=reencryption_service_url,
            endpoint=endpoint,
            payload=payload,
        )
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise Crypt4GHRemoteExecutionError(
            f"Failed to contact compute-side recryptor B at {endpoint}: {exc}"
        ) from exc

    if not response.ok:
        raise Crypt4GHRemoteExecutionError(
            f"Compute-side recryptor B returned HTTP {response.status_code} for {endpoint}: "
            f"{_summarize_http_error_response(response)}"
        )

    try:
        response_json = response.json_payload
        if not isinstance(response_json, dict):
            raise ValueError("Response body is not a JSON object")
        return cast(str, response_json["crypt4gh_header"])
    except (ValueError, KeyError, TypeError) as exc:
        raise Crypt4GHRemoteExecutionError(
            "Compute-side recryptor B returned an invalid /recrypt_header_to_user_key payload"
        ) from exc


def _parse_crypt4gh_public_key(public_key_pem: str) -> bytes:
    lines = [line.strip() for line in public_key_pem.splitlines() if line.strip()]
    if len(lines) < 3:
        raise Crypt4GHRemoteExecutionError("Invalid CRYPT4GH public key payload")
    try:
        return base64.b64decode("".join(lines[1:-1]))
    except ValueError as exc:
        raise Crypt4GHRemoteExecutionError("Invalid CRYPT4GH public key payload") from exc


def _summarize_http_error_response(response: _ReencryptionHttpResponse) -> str:
    detail: Any = None
    payload = response.json_payload

    if isinstance(payload, dict):
        for key in ("detail", "message", "error"):
            candidate = payload.get(key)
            if candidate:
                detail = candidate
                break
        if detail is None and payload:
            detail = payload
    elif isinstance(payload, list):
        detail = payload
    elif isinstance(payload, str):
        detail = payload
    else:
        detail = response.text

    summary = re.sub(r"\s+", " ", str(detail or "")).strip()
    if not summary:
        return "no error details provided"
    if len(summary) > 200:
        return f"{summary[:200]}..."
    return summary


def _decrypt_recrypted_input(
    *,
    source_dataset_path: Path,
    source_header: str,
    recrypted_header: str,
    plaintext_path: Path,
    job_private_key: bytes,
) -> None:
    source_header_bytes = _decode_header(source_header)
    recrypted_header_bytes = _decode_header(recrypted_header)
    source_header_length = _header_length(source_header_bytes)
    try:
        with source_dataset_path.open("rb") as source_stream, plaintext_path.open("wb") as plaintext_stream:
            source_stream.seek(source_header_length)
            staged_stream = _HeaderThenBodyStream(header_bytes=recrypted_header_bytes, body_stream=source_stream)
            crypt4gh.lib.decrypt([(0, job_private_key, None)], staged_stream, plaintext_stream)
    except OSError as exc:
        raise Crypt4GHRemoteExecutionError(
            f"Failed to read or materialize Crypt4GH input for source dataset {source_dataset_path}: {exc}"
        ) from exc
    except Exception as exc:
        raise Crypt4GHRemoteExecutionError(f"Failed to decrypt staged Crypt4GH input {source_dataset_path}") from exc


def _decode_header(encoded_header: str) -> bytes:
    try:
        return base64.b64decode(encoded_header)
    except (ValueError, TypeError) as exc:
        raise Crypt4GHRemoteExecutionError("Invalid base64-encoded Crypt4GH header") from exc


def _header_length(header_bytes: bytes) -> int:
    try:
        stream = io.BytesIO(header_bytes)
        list(crypt4gh.header.parse(stream))
        return stream.tell()
    except Exception as exc:
        raise Crypt4GHRemoteExecutionError("Invalid Crypt4GH header payload") from exc


def _assert_minimum_ttl(*, datasets: Sequence[DatasetInstance], minimum_ttl: timedelta, now: datetime) -> None:
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


def _parse_expiration(expires_raw: datetime | str) -> datetime:
    if isinstance(expires_raw, datetime):
        expires_at = expires_raw
    elif isinstance(expires_raw, str):
        try:
            expires_at = isoparse(expires_raw)
        except ValueError as exc:
            raise Crypt4GHRemoteExecutionError("Invalid Crypt4GH compute key expiration timestamp") from exc
    else:
        raise Crypt4GHRemoteExecutionError("Invalid Crypt4GH compute key expiration timestamp")

    if expires_at.tzinfo is None:
        raise Crypt4GHRemoteExecutionError("Invalid Crypt4GH compute key expiration timestamp - timezone is lacking")

    return expires_at


def _assert_key_valid_for_output_finalization(*, compute_keypair_expiration_date: str, now: datetime) -> None:
    expires_at = _parse_expiration(compute_keypair_expiration_date)
    if expires_at <= now:
        raise Crypt4GHRemoteExecutionError(
            "Crypt4GH compute key expired before output finalization; failing closed"
        )
