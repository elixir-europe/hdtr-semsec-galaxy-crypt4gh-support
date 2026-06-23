from __future__ import annotations

import base64
import io
import os
import shutil
from dataclasses import dataclass
from datetime import (
    datetime,
    timedelta,
    timezone,
)
from pathlib import Path
from typing import (
    Any,
    BinaryIO,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    TYPE_CHECKING,
    cast,
)

import crypt4gh.header
import crypt4gh.lib
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from dateutil.parser import isoparse
from galaxy.job_execution.compute_environment import SharedComputeEnvironment

if TYPE_CHECKING:
    from galaxy.job_execution.setup import JobIO
    from galaxy.model import (
        DatasetInstance,
        Job,
    )


class _Crypt4GHAppConfig(Protocol):
    enable_crypt4gh_transparent_staging: bool


class Crypt4GHRemoteExecutionError(Exception):
    """Raised when execution-side Crypt4GH setup must fail closed."""


class _HeaderThenBodyStream:
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


@dataclass(frozen=True)
class _DeclaredCrypt4GHOutputTarget:
    output_path: str
    plaintext_path: str
    encrypted_marker_path: str
    encrypted_ext: str


class Crypt4GHRemoteComputeEnvironment(SharedComputeEnvironment):
    def __init__(
        self,
        *,
        job_io: JobIO,
        job: Job,
        input_path_overrides_by_dataset_id: Mapping[int, str],
        compute_public_key: Optional[str],
        compute_keypair_id: Optional[str],
    ) -> None:
        super().__init__(job_io=job_io, job=job)
        self._input_path_overrides_by_dataset_id = dict(input_path_overrides_by_dataset_id)
        self.compute_public_key = compute_public_key
        self.compute_keypair_id = compute_keypair_id

    def input_path_rewrite(self, dataset: DatasetInstance) -> str:
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
) -> Crypt4GHRemoteComputeEnvironment:
    crypt4gh_inputs = _collect_crypt4gh_inputs(job_io)
    if not crypt4gh_inputs:
        raise Crypt4GHRemoteExecutionError(
            "Crypt4GH remote execution requested but no Crypt4GH inputs were detected"
        )

    crypt_inputs_workspace = _ensure_crypt4gh_inputs_workspace(working_directory)
    job_private_key, job_public_key = _generate_job_keypair()

    input_path_overrides_by_dataset_id: dict[int, str] = {}
    compute_public_key: Optional[str] = None
    compute_keypair_id: Optional[str] = None
    for dataset in crypt4gh_inputs:
        dataset_id, plaintext_path, recrypt_result = _prepare_plaintext_input_for_dataset(
            dataset=dataset,
            crypt_inputs_workspace=crypt_inputs_workspace,
            reencryption_service_url=reencryption_service_url,
            job_public_key=job_public_key,
            job_private_key=job_private_key,
        )
        input_path_overrides_by_dataset_id[dataset_id] = plaintext_path

        if compute_public_key is None:
            compute_public_key = recrypt_result.crypt4gh_compute_public_key
        elif compute_public_key != recrypt_result.crypt4gh_compute_public_key:
            raise Crypt4GHRemoteExecutionError(
                "Crypt4GH job inputs reference multiple compute public keys; mixed key contexts are unsupported"
            )

        if compute_keypair_id is None:
            compute_keypair_id = recrypt_result.crypt4gh_compute_keypair_id
        elif compute_keypair_id != recrypt_result.crypt4gh_compute_keypair_id:
            raise Crypt4GHRemoteExecutionError(
                "Crypt4GH job inputs reference multiple compute keypair ids; mixed key contexts are unsupported"
            )

    return Crypt4GHRemoteComputeEnvironment(
        job_io=job_io,
        job=job,
        input_path_overrides_by_dataset_id=input_path_overrides_by_dataset_id,
        compute_public_key=compute_public_key,
        compute_keypair_id=compute_keypair_id,
    )


def should_run_crypt4gh_remote_execution(
    *,
    job_io: JobIO,
    app_config: _Crypt4GHAppConfig,
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

    crypt4gh_inputs = _collect_crypt4gh_inputs(job_io)
    if not crypt4gh_inputs:
        return False

    current_time = now or datetime.now(timezone.utc)
    _assert_minimum_ttl(datasets=crypt4gh_inputs, minimum_ttl=minimum_ttl, now=current_time)
    return True


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
    crypt_inputs_workspace: Path,
    reencryption_service_url: str,
    job_public_key: str,
    job_private_key: bytes,
) -> tuple[int, str, _RecryptToJobKeyResult]:
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

    recrypt_result = _recrypt_header_to_job_key(
        reencryption_service_url=reencryption_service_url,
        crypt4gh_header=cast(str, header),
        compute_keypair_id=cast(str, keypair_id),
        job_public_key=job_public_key,
    )

    dataset_workspace = crypt_inputs_workspace / f"ds_{dataset_id}"
    dataset_workspace.mkdir(parents=True, exist_ok=True)
    plaintext_path = dataset_workspace / "plaintext"

    source_dataset_path = Path(dataset.get_file_name())
    _decrypt_recrypted_input(
        source_dataset_path=source_dataset_path,
        source_header=cast(str, header),
        recrypted_header=recrypt_result.crypt4gh_header,
        plaintext_path=plaintext_path,
        job_private_key=job_private_key,
    )
    return dataset_id, str(plaintext_path), recrypt_result


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
        response = requests.post(endpoint, json=payload, timeout=30)
    except requests.RequestException as exc:
        raise Crypt4GHRemoteExecutionError(
            f"Failed to contact compute-side recryptor B at {endpoint}: {exc}"
        ) from exc

    if not response.ok:
        raise Crypt4GHRemoteExecutionError(
            f"Compute-side recryptor B returned HTTP {response.status_code} for {endpoint}: {response.text}"
        )

    try:
        response_json = response.json()
        return _RecryptToJobKeyResult(
            crypt4gh_header=cast(str, response_json["crypt4gh_header"]),
            crypt4gh_compute_public_key=cast(str, response_json["crypt4gh_compute_public_key"]),
            crypt4gh_compute_keypair_id=cast(str, response_json["crypt4gh_compute_keypair_id"]),
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
) -> list[dict[str, str]]:
    targets: list[dict[str, str]] = []
    marker_dir = Path(working_directory) / "_c4gh_stage" / "outputs"
    plaintext_root = Path(working_directory) / "_crypt" / "outputs"
    tool_working_directory = Path(working_directory) / "working"

    for output_name, (dataset, dataset_path) in job_io.get_output_hdas_and_fnames().items():
        if output_name not in tool_outputs:
            continue

        dataset_object = getattr(dataset, "dataset", None)
        dataset_id = getattr(dataset_object, "id", None)
        if not isinstance(dataset_id, int):
            continue

        base_ext = cast(str, getattr(dataset, "ext", "") or "")
        if not base_ext:
            continue
        if base_ext.endswith(".c4gh"):
            continue

        if base_ext in ("auto", "data", "_sniff_"):
            tool_output = tool_outputs.get(output_name)
            declared_ext = getattr(tool_output, "format", None) if tool_output else None
            if declared_ext and declared_ext not in ("auto", "data", "_sniff_", "input"):
                base_ext = declared_ext
            else:
                continue

        encrypted_ext = f"{base_ext}.c4gh"
        if datatypes_registry.get_datatype_by_extension(encrypted_ext) is None:
            datatypes_registry.get_or_create_crypt4gh_datatype(base_ext)

        tool_output = tool_outputs.get(output_name)
        from_work_dir = getattr(tool_output, "from_work_dir", None) if tool_output else None
        if from_work_dir:
            from_work_dir_path = Path(str(from_work_dir))
            output_path = str(
                from_work_dir_path if from_work_dir_path.is_absolute() else tool_working_directory / from_work_dir_path
            )
        else:
            output_path = (
                getattr(dataset_path, "false_path", None)
                or getattr(dataset_path, "real_path", None)
                or str(dataset_path)
            )

        target = _DeclaredCrypt4GHOutputTarget(
            output_path=str(output_path),
            plaintext_path=str(plaintext_root / f"ds_{dataset_id}" / "plaintext"),
            encrypted_marker_path=str(marker_dir / f"ds_{dataset_id}.encrypted"),
            encrypted_ext=encrypted_ext,
        )
        targets.append(
            {
                "output_path": target.output_path,
                "plaintext_path": target.plaintext_path,
                "encrypted_marker_path": target.encrypted_marker_path,
                "encrypted_ext": target.encrypted_ext,
            }
        )

    return targets


def finalize_declared_crypt4gh_outputs(
    *,
    output_targets: Sequence[Mapping[str, str]],
    reencryption_service_url: str,
    compute_public_key: str,
    compute_keypair_id: str,
) -> None:
    for target in output_targets:
        output_path = Path(target["output_path"])
        if not output_path.exists():
            continue

        plaintext_path = Path(target["plaintext_path"])
        plaintext_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(output_path, plaintext_path)

        compute_encrypted_path = Path(f"{output_path}.compute.c4gh")
        final_tmp_path = Path(f"{output_path}.c4gh.tmp")
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

            marker_path = Path(target["encrypted_marker_path"])
            marker_path.parent.mkdir(parents=True, exist_ok=True)
            marker_path.write_text(f"{target['encrypted_ext']}\n")
        except Exception as exc:
            raise Crypt4GHRemoteExecutionError(
                f"Failed to finalize encrypted Crypt4GH output at {output_path}: {exc}"
            ) from exc
        finally:
            if compute_encrypted_path.exists():
                compute_encrypted_path.unlink()
            if final_tmp_path.exists():
                final_tmp_path.unlink()


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
        response = requests.post(endpoint, json=payload, timeout=30)
    except requests.RequestException as exc:
        raise Crypt4GHRemoteExecutionError(
            f"Failed to contact compute-side recryptor B at {endpoint}: {exc}"
        ) from exc

    if not response.ok:
        raise Crypt4GHRemoteExecutionError(
            f"Compute-side recryptor B returned HTTP {response.status_code} for {endpoint}: {response.text}"
        )

    try:
        return cast(str, response.json()["crypt4gh_header"])
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
