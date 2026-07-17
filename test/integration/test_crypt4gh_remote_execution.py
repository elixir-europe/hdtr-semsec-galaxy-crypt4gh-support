from __future__ import annotations

import base64
import io
import json
import os
import socket
import threading
import time
from datetime import (
    datetime,
    timedelta,
    timezone,
)
from pathlib import Path
from typing import (
    Any,
    Callable,
)

import crypt4gh.header
from crypt4gh.keys import get_private_key
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from fastapi import (
    FastAPI,
    HTTPException,
)
from pydantic import BaseModel
import requests
from uvicorn import (
    Config,
    Server,
)

from galaxy import model  # type: ignore[import-not-found]
from galaxy_test.base.populators import DatasetPopulator  # type: ignore[import-not-found]
from galaxy_test.driver import integration_util  # type: ignore[import-not-found]

from galaxy.tools.crypt4gh_remote_execution import CRYPT4GH_PLAINTEXT_CLEANUP_FAILED_MARKER


class _RecryptToJobKeyRequest(BaseModel):
    crypt4gh_header: str
    crypt4gh_compute_keypair_id: str
    crypt4gh_job_public_key: str


class _RecryptToJobKeyResponse(BaseModel):
    crypt4gh_header: str
    crypt4gh_compute_public_key: str
    crypt4gh_compute_keypair_id: str
    crypt4gh_compute_keypair_expiration_date: str


class _RecryptToUserKeyRequest(BaseModel):
    crypt4gh_header: str
    crypt4gh_compute_keypair_id: str


class _RecryptToUserKeyResponse(BaseModel):
    crypt4gh_header: str
    crypt4gh_compute_keypair_id: str
    crypt4gh_compute_keypair_expiration_date: str


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_port(port: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"Mock compute recryptor did not start on port {port} within {timeout}s")


def _to_raw_public_key_bytes(public_key_pem: str) -> bytes:
    lines = [line.strip() for line in public_key_pem.splitlines() if line.strip()]
    if len(lines) < 3:
        raise ValueError("Invalid CRYPT4GH public key payload")
    return base64.b64decode("".join(lines[1:-1]))


def _build_compute_recryptor_app(
    *,
    user_private_key_path: str,
    user_public_key_path: str,
    compute_private_key_path: str,
    compute_public_key_path: str,
    compute_keypair_id: str,
    compute_keypair_expiration_date: str,
    should_fail_recrypt_to_user_key: Callable[[], bool],
) -> FastAPI:
    user_private_key = get_private_key(user_private_key_path, lambda: b"")
    user_public_key = _to_raw_public_key_bytes(Path(user_public_key_path).read_text())
    compute_private_key = get_private_key(compute_private_key_path, lambda: b"")
    compute_public_key_text = Path(compute_public_key_path).read_text()

    app = FastAPI(title="mock-compute-crypt4gh-recryptor", version="test")
    app.state.compute_keypair_expiration_date = compute_keypair_expiration_date

    @app.get("/info")
    def info() -> dict[str, str]:
        return {"name": "mock-compute-crypt4gh-recryptor", "version": "test"}

    @app.post("/recrypt_header_to_job_key", response_model=_RecryptToJobKeyResponse)
    def recrypt_header_to_job_key(params: _RecryptToJobKeyRequest) -> _RecryptToJobKeyResponse:
        if params.crypt4gh_compute_keypair_id != compute_keypair_id:
            raise HTTPException(status_code=404, detail="Unknown crypt4gh_compute_keypair_id")

        try:
            in_header_bytes = base64.b64decode(params.crypt4gh_header)
            packet_stream = io.BytesIO(in_header_bytes)
            packets = list(crypt4gh.header.parse(packet_stream))
        except Exception as exc:
            raise HTTPException(status_code=422, detail="Malformed or undecryptable crypt4gh_header") from exc

        try:
            job_public_key = _to_raw_public_key_bytes(params.crypt4gh_job_public_key)
            ephemeral_private_key = X25519PrivateKey.generate().private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption(),
            )
            recrypted_packets = list(
                crypt4gh.header.reencrypt(
                    packets,
                    keys=[(0, user_private_key, None)],
                    recipient_keys=[(0, ephemeral_private_key, job_public_key)],
                )
            )
            recrypted_header_bytes = crypt4gh.header.serialize(recrypted_packets)
        except Exception as exc:
            raise HTTPException(status_code=422, detail="Malformed or undecryptable crypt4gh_header") from exc

        return _RecryptToJobKeyResponse(
            crypt4gh_header=base64.b64encode(recrypted_header_bytes).decode("ascii"),
            crypt4gh_compute_public_key=compute_public_key_text,
            crypt4gh_compute_keypair_id=compute_keypair_id,
            crypt4gh_compute_keypair_expiration_date=app.state.compute_keypair_expiration_date,
        )

    @app.post("/recrypt_header_to_user_key", response_model=_RecryptToUserKeyResponse)
    def recrypt_header_to_user_key(params: _RecryptToUserKeyRequest) -> _RecryptToUserKeyResponse:
        if params.crypt4gh_compute_keypair_id != compute_keypair_id:
            raise HTTPException(status_code=404, detail="Unknown crypt4gh_compute_keypair_id")

        if should_fail_recrypt_to_user_key():
            raise HTTPException(status_code=500, detail="forced recrypt_header_to_user_key failure")

        try:
            in_header_bytes = base64.b64decode(params.crypt4gh_header)
            packet_stream = io.BytesIO(in_header_bytes)
            packets = list(crypt4gh.header.parse(packet_stream))
        except Exception as exc:
            raise HTTPException(status_code=422, detail="Malformed or undecryptable crypt4gh_header") from exc

        try:
            ephemeral_private_key = X25519PrivateKey.generate().private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption(),
            )
            recrypted_packets = list(
                crypt4gh.header.reencrypt(
                    packets,
                    keys=[(0, compute_private_key, None)],
                    recipient_keys=[(0, ephemeral_private_key, user_public_key)],
                )
            )
            recrypted_header_bytes = crypt4gh.header.serialize(recrypted_packets)
        except Exception as exc:
            raise HTTPException(status_code=422, detail="Malformed or undecryptable crypt4gh_header") from exc

        return _RecryptToUserKeyResponse(
            crypt4gh_header=base64.b64encode(recrypted_header_bytes).decode("ascii"),
            crypt4gh_compute_keypair_id=compute_keypair_id,
            crypt4gh_compute_keypair_expiration_date=app.state.compute_keypair_expiration_date,
        )

    return app


class _MockComputeRecryptorServer:
    def __init__(
        self,
        *,
        user_private_key_path: str,
        user_public_key_path: str,
        compute_private_key_path: str,
        compute_public_key_path: str,
        compute_keypair_id: str,
        compute_keypair_expiration_date: str,
    ) -> None:
        self.port = _find_free_port()
        self._server: Server | None = None
        self._thread: threading.Thread | None = None
        self.fail_recrypt_to_user_key = False
        self._app = _build_compute_recryptor_app(
            user_private_key_path=user_private_key_path,
            user_public_key_path=user_public_key_path,
            compute_private_key_path=compute_private_key_path,
            compute_public_key_path=compute_public_key_path,
            compute_keypair_id=compute_keypair_id,
            compute_keypair_expiration_date=compute_keypair_expiration_date,
            should_fail_recrypt_to_user_key=lambda: self.fail_recrypt_to_user_key,
        )

    @property
    def compute_keypair_expiration_date(self) -> str:
        return str(self._app.state.compute_keypair_expiration_date)

    @compute_keypair_expiration_date.setter
    def compute_keypair_expiration_date(self, expiration: str) -> None:
        self._app.state.compute_keypair_expiration_date = expiration

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        config = Config(self._app, host="127.0.0.1", port=self.port, log_level="warning")
        self._server = Server(config)

        def _run_server() -> None:
            import asyncio

            asyncio.run(self._server.serve())  # type: ignore[union-attr]

        self._thread = threading.Thread(target=_run_server, daemon=True)
        self._thread.start()
        _wait_for_port(self.port)

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True


class TestCrypt4GHRemoteExecutionIntegration(integration_util.IntegrationTestCase):
    framework_tool_and_types = True
    dataset_populator: DatasetPopulator

    _default_mock_compute_keypair_id = "mock-compute-key-1"
    _default_mock_compute_keypair_expiration_date = "2099-01-01T00:00:00+00:00"
    _mock_compute_keypair_id = "mock-compute-key-1"
    _mock_compute_keypair_expiration_date = "2099-01-01T00:00:00+00:00"
    _mock_compute_recryptor_server: _MockComputeRecryptorServer
    _external_reencryption_service_url: str | None = None
    _live_compute_public_key: str | None = None

    @classmethod
    def _prepare_galaxy(cls) -> None:
        cls._external_reencryption_service_url = os.environ.get("GALAXY_TEST_CRYPT4GH_REENCRYPTION_SERVICE_URL")
        user_private_key = os.path.join("test-data", "crypt4gh", "user_key.sec")
        user_public_key = os.path.join("test-data", "crypt4gh", "user_key.pub")

        if cls._external_reencryption_service_url:
            cls._refresh_live_compute_keypair_metadata(user_public_key_path=user_public_key)
            return

        cls._live_compute_public_key = None
        cls._mock_compute_keypair_id = cls._default_mock_compute_keypair_id
        cls._mock_compute_keypair_expiration_date = cls._default_mock_compute_keypair_expiration_date
        compute_private_key = os.path.join("test-data", "crypt4gh", "compute_key.sec")
        compute_public_key = os.path.join("test-data", "crypt4gh", "compute_key.pub")
        cls._mock_compute_recryptor_server = _MockComputeRecryptorServer(
            user_private_key_path=user_private_key,
            user_public_key_path=user_public_key,
            compute_private_key_path=compute_private_key,
            compute_public_key_path=compute_public_key,
            compute_keypair_id=cls._mock_compute_keypair_id,
            compute_keypair_expiration_date=cls._mock_compute_keypair_expiration_date,
        )
        cls._mock_compute_recryptor_server.start()

    @classmethod
    def _refresh_live_compute_keypair_metadata(cls, *, user_public_key_path: str) -> None:
        service_url = cls._external_reencryption_service_url
        if not service_url:
            raise RuntimeError("Live-service URL must be set before refreshing compute keypair metadata")

        endpoint = f"{service_url.rstrip('/')}/get_compute_key_info"
        payload = {"crypt4gh_user_public_key": Path(user_public_key_path).read_text()}
        try:
            response = requests.post(endpoint, json=payload, timeout=30)
        except requests.RequestException as exc:
            raise RuntimeError(f"Unable to contact live recryptor service at {endpoint}: {exc}") from exc

        if not response.ok:
            raise RuntimeError(
                f"Live recryptor service returned HTTP {response.status_code} for {endpoint}: {response.text}"
            )

        try:
            response_json = response.json()
            cls._mock_compute_keypair_id = response_json["crypt4gh_compute_keypair_id"]
            cls._mock_compute_keypair_expiration_date = response_json["crypt4gh_compute_keypair_expiration_date"]
            cls._live_compute_public_key = response_json["crypt4gh_compute_public_key"]
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError("Live recryptor service returned invalid /get_compute_key_info payload") from exc

    @classmethod
    def _header_recrypted_to_live_compute_key(cls, header: str) -> str:
        if not cls._external_reencryption_service_url:
            return header

        compute_public_key = cls._live_compute_public_key
        if not compute_public_key:
            raise RuntimeError("Live compute public key is unavailable for header rewriting")

        user_private_key = get_private_key(os.path.join("test-data", "crypt4gh", "user_key.sec"), lambda: b"")
        compute_public_key_bytes = _to_raw_public_key_bytes(compute_public_key)

        in_header_bytes = base64.b64decode(header)
        packet_stream = io.BytesIO(in_header_bytes)
        packets = list(crypt4gh.header.parse(packet_stream))
        ephemeral_private_key = X25519PrivateKey.generate().private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        recrypted_packets = list(
            crypt4gh.header.reencrypt(
                packets,
                keys=[(0, user_private_key, None)],
                recipient_keys=[(0, ephemeral_private_key, compute_public_key_bytes)],
            )
        )
        return base64.b64encode(crypt4gh.header.serialize(recrypted_packets)).decode("ascii")

    @classmethod
    def _set_input_compute_metadata(cls, hda: model.HistoryDatasetAssociation) -> None:
        hda.metadata.crypt4gh_compute_keypair_id = cls._mock_compute_keypair_id
        hda.metadata.crypt4gh_compute_keypair_expiration_date = cls._mock_compute_keypair_expiration_date

        if cls._external_reencryption_service_url:
            source_header = hda.metadata.crypt4gh_header
            assert source_header
            hda.metadata.crypt4gh_header = cls._header_recrypted_to_live_compute_key(source_header)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._external_reencryption_service_url is None and hasattr(cls, "_mock_compute_recryptor_server"):
            cls._mock_compute_recryptor_server.stop()
        super().tearDownClass()

    @classmethod
    def handle_galaxy_config_kwds(cls, config: dict[str, Any]) -> None:
        super().handle_galaxy_config_kwds(config)
        config["enable_celery_tasks"] = False
        config["metadata_strategy"] = "extended"
        config["enable_crypt4gh_transparent_input_matching"] = True
        config["enable_crypt4gh_remote_execution_staging"] = True
        config["tool_evaluation_strategy"] = "remote"
        if cls._external_reencryption_service_url:
            config["crypt4gh_reencryption_service_url"] = cls._external_reencryption_service_url
        else:
            config["crypt4gh_reencryption_service_url"] = cls._mock_compute_recryptor_server.url
        config["cleanup_job"] = "never"

    def setUp(self) -> None:
        super().setUp()
        self.dataset_populator = DatasetPopulator(self.galaxy_interactor)

    def test_output_format_declared_outputs_are_encrypted_for_crypt4gh_jobs(self) -> None:
        history_id = self.dataset_populator.new_history()
        with open(self.test_data_resolver.get_filename("crypt4gh/test.fastqsanger.c4gh"), "rb") as encrypted_input:
            input_dataset = self.dataset_populator.new_dataset(
                history_id,
                content=encrypted_input,
                file_type="fastqsanger.c4gh",
                fetch_data=False,
                wait=True,
            )

        input_dataset_id = input_dataset["id"]
        input_hda_database_id = self._app.security.decode_id(input_dataset_id)
        sa_session = self._app.model.session
        input_hda = sa_session.get(model.HistoryDatasetAssociation, input_hda_database_id)
        assert input_hda is not None
        self._set_input_compute_metadata(input_hda)
        sa_session.commit()

        run_response = self.dataset_populator.run_tool(
            "output_format",
            {
                "input_data_1": {"src": "hda", "id": input_dataset_id},
                "input_data_2": {"src": "hda", "id": input_dataset_id},
                "input_text": "not_foo_or_bar",
            },
            history_id,
        )
        job_api_id = run_response["jobs"][0]["id"]
        self.dataset_populator.wait_for_job(job_api_id, assert_ok=True)

        job_database_id = self._app.security.decode_id(job_api_id)
        job = sa_session.get(model.Job, job_database_id)
        assert job is not None

        direct_output_assoc = next(output_assoc for output_assoc in job.output_datasets if output_assoc.name == "direct_output")
        direct_output_hda = direct_output_assoc.dataset
        assert direct_output_hda is not None
        assert direct_output_hda.dataset is not None

        output_details = self.dataset_populator.get_history_dataset_details(
            history_id,
            dataset_id=self._app.security.encode_id(direct_output_hda.id),
        )
        assert output_details["state"] == "ok", output_details
        assert output_details["extension"].endswith(".c4gh"), output_details

        output_dataset_table_id = direct_output_hda.dataset.id
        assert output_dataset_table_id is not None
        input_dataset_table_id = input_hda.dataset.id
        assert input_dataset_table_id is not None
        job_working_directory = self._app.object_store.get_filename(job, base_dir="job_work", dir_only=True, obj_dir=True)
        assert job_working_directory is not None

        plaintext_output_path = Path(job_working_directory) / "_crypt" / "outputs" / f"ds_{output_dataset_table_id}" / "plaintext"
        plaintext_input_path = Path(job_working_directory) / "_crypt" / "inputs" / f"ds_{input_dataset_table_id}" / "plaintext"
        assert not plaintext_output_path.exists(), f"Expected output plaintext path {plaintext_output_path} to be removed"
        assert not plaintext_input_path.exists(), f"Expected input plaintext path {plaintext_input_path} to be removed"

        output_dataset_path = Path(direct_output_hda.dataset.get_file_name())
        with output_dataset_path.open("rb") as output_stream:
            assert output_stream.read(8) == b"crypt4gh"

        sa_session.refresh(direct_output_hda)
        assert direct_output_hda.metadata.crypt4gh_header
        assert direct_output_hda.metadata.crypt4gh_compute_keypair_id == ""
        assert direct_output_hda.metadata.crypt4gh_compute_keypair_expiration_date == ""

    def test_finalization_failure_marks_job_error_without_plaintext_cleanup_marker(self) -> None:
        history_id = self.dataset_populator.new_history()
        with open(self.test_data_resolver.get_filename("crypt4gh/test.fastqsanger.c4gh"), "rb") as encrypted_input:
            input_dataset = self.dataset_populator.new_dataset(
                history_id,
                content=encrypted_input,
                file_type="fastqsanger.c4gh",
                fetch_data=False,
                wait=True,
            )

        input_dataset_id = input_dataset["id"]
        input_hda_database_id = self._app.security.decode_id(input_dataset_id)
        sa_session = self._app.model.session
        input_hda = sa_session.get(model.HistoryDatasetAssociation, input_hda_database_id)
        assert input_hda is not None
        self._set_input_compute_metadata(input_hda)
        sa_session.commit()

        self._mock_compute_recryptor_server.fail_recrypt_to_user_key = True
        try:
            run_response = self.dataset_populator.run_tool(
                "output_format",
                {
                    "input_data_1": {"src": "hda", "id": input_dataset_id},
                    "input_data_2": {"src": "hda", "id": input_dataset_id},
                    "input_text": "not_foo_or_bar",
                },
                history_id,
            )
            job_api_id = run_response["jobs"][0]["id"]
            self.dataset_populator.wait_for_job(job_api_id, assert_ok=False)
            job = self.dataset_populator.get_job_details(job_api_id, full=True).json()
        finally:
            self._mock_compute_recryptor_server.fail_recrypt_to_user_key = False

        assert job["state"] == "error"
        tool_stderr = job.get("tool_stderr", "")
        assert CRYPT4GH_PLAINTEXT_CLEANUP_FAILED_MARKER not in tool_stderr
        assert "Compute-side recryptor B returned HTTP 500" in tool_stderr

        job_database_id = self._app.security.decode_id(job_api_id)
        job_model = sa_session.get(model.Job, job_database_id)
        assert job_model is not None
        direct_output_assoc = next(output_assoc for output_assoc in job_model.output_datasets if output_assoc.name == "direct_output")
        direct_output_hda = direct_output_assoc.dataset
        assert direct_output_hda is not None
        assert direct_output_hda.dataset is not None

        output_dataset_file_name = direct_output_hda.dataset.get_file_name(sync_cache=False)
        if output_dataset_file_name:
            output_dataset_path = Path(output_dataset_file_name)
            assert not (
                output_dataset_path.exists()
                and output_dataset_path.is_file()
                and os.access(output_dataset_path, os.R_OK)
            ), (
                "Expected output dataset path to be absent or non-readable after "
                f"Crypt4GH finalization failure, but found readable file at {output_dataset_path}"
            )
        else:
            assert not self._app.object_store.exists(direct_output_hda.dataset), (
                "Expected output dataset path to be absent after Crypt4GH finalization failure"
            )

    def test_discovered_dataset_outputs_are_encrypted_for_crypt4gh_jobs(self) -> None:
        history_id = self.dataset_populator.new_history()
        with open(self.test_data_resolver.get_filename("crypt4gh/test.fastqsanger.c4gh"), "rb") as encrypted_input:
            input_dataset = self.dataset_populator.new_dataset(
                history_id,
                content=encrypted_input,
                file_type="fastqsanger.c4gh",
                fetch_data=False,
                wait=True,
            )

        input_dataset_id = input_dataset["id"]
        input_hda_database_id = self._app.security.decode_id(input_dataset_id)
        sa_session = self._app.model.session
        input_hda = sa_session.get(model.HistoryDatasetAssociation, input_hda_database_id)
        assert input_hda is not None
        self._set_input_compute_metadata(input_hda)
        sa_session.commit()

        run_response = self.dataset_populator.run_tool(
            "multi_output_assign_primary",
            {"num_param": 7, "input": {"src": "hda", "id": input_dataset_id}},
            history_id,
        )
        job_api_id = run_response["jobs"][0]["id"]
        self.dataset_populator.wait_for_job(job_api_id, assert_ok=True)

        job_database_id = self._app.security.decode_id(job_api_id)
        job = sa_session.get(model.Job, job_database_id)
        assert job is not None
        job_working_directory = self._app.object_store.get_filename(job, base_dir="job_work", dir_only=True, obj_dir=True)
        assert job_working_directory is not None
        marker_dir = Path(job_working_directory) / "_c4gh_stage" / "outputs"
        assert marker_dir.exists(), marker_dir
        assert list(marker_dir.glob("path_*.encrypted")) == []

        history_contents = self.dataset_populator.get_history_contents(history_id)
        sample_entry = next(
            item for item in history_contents if item["history_content_type"] == "dataset" and item["hid"] == 2
        )
        sample2_entry = next(
            item for item in history_contents if item["history_content_type"] == "dataset" and item["hid"] == 3
        )
        sample3_entry = next(
            item for item in history_contents if item["history_content_type"] == "dataset" and item["hid"] == 4
        )

        sample_details = self.dataset_populator.get_history_dataset_details(history_id, dataset_id=sample_entry["id"])
        sample2_details = self.dataset_populator.get_history_dataset_details(history_id, dataset_id=sample2_entry["id"])
        sample3_details = self.dataset_populator.get_history_dataset_details(history_id, dataset_id=sample3_entry["id"])
        assert sample_details["extension"].endswith(".c4gh"), sample_details
        assert sample2_details["extension"].endswith(".c4gh"), sample2_details
        assert sample3_details["extension"].endswith(".c4gh"), sample3_details

        sample_hda = sa_session.get(model.HistoryDatasetAssociation, self._app.security.decode_id(sample_entry["id"]))
        sample2_hda = sa_session.get(model.HistoryDatasetAssociation, self._app.security.decode_id(sample2_entry["id"]))
        sample3_hda = sa_session.get(model.HistoryDatasetAssociation, self._app.security.decode_id(sample3_entry["id"]))
        assert sample_hda is not None and sample_hda.dataset is not None
        assert sample2_hda is not None and sample2_hda.dataset is not None
        assert sample3_hda is not None and sample3_hda.dataset is not None

        for hda in (sample_hda, sample2_hda, sample3_hda):
            dataset_path = Path(hda.dataset.get_file_name())
            with dataset_path.open("rb") as dataset_stream:
                assert dataset_stream.read(8) == b"crypt4gh"

    def test_discovered_dataset_extra_files_are_encrypted_and_manifested_for_crypt4gh_jobs(self) -> None:
        history_id = self.dataset_populator.new_history()
        with open(self.test_data_resolver.get_filename("crypt4gh/test.fastqsanger.c4gh"), "rb") as encrypted_input:
            input_dataset = self.dataset_populator.new_dataset(
                history_id,
                content=encrypted_input,
                file_type="fastqsanger.c4gh",
                fetch_data=False,
                wait=True,
            )

        input_dataset_id = input_dataset["id"]
        input_hda_database_id = self._app.security.decode_id(input_dataset_id)
        sa_session = self._app.model.session
        input_hda = sa_session.get(model.HistoryDatasetAssociation, input_hda_database_id)
        assert input_hda is not None
        self._set_input_compute_metadata(input_hda)
        sa_session.commit()

        run_response = self.dataset_populator.run_tool(
            "tool_provided_metadata_12",
            {"input": {"src": "hda", "id": input_dataset_id}},
            history_id,
        )
        job_api_id = run_response["jobs"][0]["id"]
        self.dataset_populator.wait_for_job(job_api_id, assert_ok=True)

        history_contents = self.dataset_populator.get_history_contents(history_id)
        sample_entry = next(
            item
            for item in history_contents
            if item["history_content_type"] == "dataset" and item.get("name") == "cool name 1"
        )

        sample_hda = sa_session.get(model.HistoryDatasetAssociation, self._app.security.decode_id(sample_entry["id"]))
        assert sample_hda is not None and sample_hda.dataset is not None

        sample_details = self.dataset_populator.get_history_dataset_details(history_id, dataset_id=sample_entry["id"])
        assert sample_details["extension"].endswith(".c4gh"), sample_details

        dataset_path = Path(sample_hda.dataset.get_file_name())
        with dataset_path.open("rb") as dataset_stream:
            assert dataset_stream.read(8) == b"crypt4gh"

        dataset_id = sample_hda.dataset.id
        assert dataset_id is not None
        extra_files_path = Path(sample_hda.dataset.extra_files_path)
        with (extra_files_path / "foo").open("rb") as extra_stream:
            assert extra_stream.read(8) == b"crypt4gh"
        with (extra_files_path / "bar").open("rb") as extra_stream:
            assert extra_stream.read(8) == b"crypt4gh"

        job_database_id = self._app.security.decode_id(job_api_id)
        job = sa_session.get(model.Job, job_database_id)
        assert job is not None
        job_working_directory = self._app.object_store.get_filename(job, base_dir="job_work", dir_only=True, obj_dir=True)
        assert job_working_directory is not None
        marker_dir = Path(job_working_directory) / "_c4gh_stage" / "outputs"
        assert marker_dir.exists(), marker_dir

        manifest_path = marker_dir / f"ds_{dataset_id}.extra_files_manifest.json"
        assert manifest_path.exists(), manifest_path
        manifest_payload = json.loads(manifest_path.read_text())
        assert sorted(manifest_payload["files"].keys()) == ["bar", "foo"]

        designation_map_path = marker_dir / "discovered_designations.json"
        assert designation_map_path.exists(), designation_map_path
        designation_payload = json.loads(designation_map_path.read_text())
        assert designation_payload.get("sample1", "").endswith(".c4gh")

    def test_framework_control_artifacts_remain_plaintext_readable_when_payloads_are_enforced(self) -> None:
        history_id = self.dataset_populator.new_history()
        with open(self.test_data_resolver.get_filename("crypt4gh/test.fastqsanger.c4gh"), "rb") as encrypted_input:
            input_dataset = self.dataset_populator.new_dataset(
                history_id,
                content=encrypted_input,
                file_type="fastqsanger.c4gh",
                fetch_data=False,
                wait=True,
            )

        input_dataset_id = input_dataset["id"]
        input_hda_database_id = self._app.security.decode_id(input_dataset_id)
        sa_session = self._app.model.session
        input_hda = sa_session.get(model.HistoryDatasetAssociation, input_hda_database_id)
        assert input_hda is not None
        self._set_input_compute_metadata(input_hda)
        sa_session.commit()

        run_response = self.dataset_populator.run_tool(
            "output_format",
            {
                "input_data_1": {"src": "hda", "id": input_dataset_id},
                "input_data_2": {"src": "hda", "id": input_dataset_id},
                "input_text": "not_foo_or_bar",
            },
            history_id,
        )
        job_api_id = run_response["jobs"][0]["id"]
        self.dataset_populator.wait_for_job(job_api_id, assert_ok=True)

        job_database_id = self._app.security.decode_id(job_api_id)
        job = sa_session.get(model.Job, job_database_id)
        assert job is not None
        direct_output_assoc = next(output_assoc for output_assoc in job.output_datasets if output_assoc.name == "direct_output")
        direct_output_hda = direct_output_assoc.dataset
        assert direct_output_hda is not None
        assert direct_output_hda.dataset is not None

        job_working_directory = self._app.object_store.get_filename(job, base_dir="job_work", dir_only=True, obj_dir=True)
        assert job_working_directory is not None
        working_path = Path(job_working_directory)
        outputs_dir = working_path / "outputs"

        tool_stdout_path = outputs_dir / "tool_stdout"
        tool_stderr_path = outputs_dir / "tool_stderr"
        assert tool_stdout_path.exists() and tool_stdout_path.is_file() and os.access(tool_stdout_path, os.R_OK)
        assert tool_stderr_path.exists() and tool_stderr_path.is_file() and os.access(tool_stderr_path, os.R_OK)
        tool_stdout_path.read_text(encoding="utf-8", errors="ignore")
        tool_stderr_path.read_text(encoding="utf-8", errors="ignore")

        marker_dir = working_path / "_c4gh_stage" / "outputs"
        output_dataset_table_id = direct_output_hda.dataset.id
        assert output_dataset_table_id is not None
        marker_path = marker_dir / f"ds_{output_dataset_table_id}.encrypted"
        assert marker_path.exists()
        assert marker_path.read_text().strip().endswith(".c4gh")

        assert not (working_path / "_crypt" / "inputs").exists()
        assert not (working_path / "_crypt" / "outputs").exists()

        output_dataset_path = Path(direct_output_hda.dataset.get_file_name())
        with output_dataset_path.open("rb") as output_stream:
            assert output_stream.read(8) == b"crypt4gh"

    def test_stdout_stderr_are_only_plaintext_files_in_job_outputs_directory(self) -> None:
        history_id = self.dataset_populator.new_history()
        with open(self.test_data_resolver.get_filename("crypt4gh/test.fastqsanger.c4gh"), "rb") as encrypted_input:
            input_dataset = self.dataset_populator.new_dataset(
                history_id,
                content=encrypted_input,
                file_type="fastqsanger.c4gh",
                fetch_data=False,
                wait=True,
            )

        input_dataset_id = input_dataset["id"]
        input_hda_database_id = self._app.security.decode_id(input_dataset_id)
        sa_session = self._app.model.session
        input_hda = sa_session.get(model.HistoryDatasetAssociation, input_hda_database_id)
        assert input_hda is not None
        self._set_input_compute_metadata(input_hda)
        sa_session.commit()

        run_response = self.dataset_populator.run_tool(
            "output_format",
            {
                "input_data_1": {"src": "hda", "id": input_dataset_id},
                "input_data_2": {"src": "hda", "id": input_dataset_id},
                "input_text": "not_foo_or_bar",
            },
            history_id,
        )
        job_api_id = run_response["jobs"][0]["id"]
        self.dataset_populator.wait_for_job(job_api_id, assert_ok=True)

        job_database_id = self._app.security.decode_id(job_api_id)
        job = sa_session.get(model.Job, job_database_id)
        assert job is not None
        direct_output_assoc = next(output_assoc for output_assoc in job.output_datasets if output_assoc.name == "direct_output")
        direct_output_hda = direct_output_assoc.dataset
        assert direct_output_hda is not None
        assert direct_output_hda.dataset is not None

        job_working_directory = self._app.object_store.get_filename(job, base_dir="job_work", dir_only=True, obj_dir=True)
        assert job_working_directory is not None
        outputs_dir = Path(job_working_directory) / "outputs"
        output_files = sorted(path.name for path in outputs_dir.iterdir() if path.is_file())
        assert output_files == ["tool_stderr", "tool_stdout"]

        for output_file in output_files:
            output_path = outputs_dir / output_file
            with output_path.open("rb") as output_stream:
                assert output_stream.read(8) != b"crypt4gh"

        output_dataset_path = Path(direct_output_hda.dataset.get_file_name())
        with output_dataset_path.open("rb") as output_stream:
            assert output_stream.read(8) == b"crypt4gh"

    def test_remote_helper_fails_before_launch_when_stored_ttl_below_threshold(self) -> None:
        history_id = self.dataset_populator.new_history()
        with open(self.test_data_resolver.get_filename("crypt4gh/test.fastqsanger.c4gh"), "rb") as encrypted_input:
            input_dataset = self.dataset_populator.new_dataset(
                history_id,
                content=encrypted_input,
                file_type="fastqsanger.c4gh",
                fetch_data=False,
                wait=True,
            )

        input_dataset_id = input_dataset["id"]
        input_hda_database_id = self._app.security.decode_id(input_dataset_id)
        sa_session = self._app.model.session
        input_hda = sa_session.get(model.HistoryDatasetAssociation, input_hda_database_id)
        assert input_hda is not None
        self._set_input_compute_metadata(input_hda)
        too_soon = datetime.now(timezone.utc) + timedelta(minutes=10)
        input_hda.metadata.crypt4gh_compute_keypair_expiration_date = too_soon.isoformat()
        sa_session.commit()

        run_response = self.dataset_populator.run_tool(
            "inheritance_simple",
            {"input1": {"src": "hda", "id": input_dataset_id}},
            history_id,
        )
        job_api_id = run_response["jobs"][0]["id"]
        self.dataset_populator.wait_for_job(job_api_id, assert_ok=False)
        job = self.dataset_populator.get_job_details(job_api_id, full=True).json()

        assert job["state"] == "error", job
        assert "minimum TTL requirement before remote call" in job.get("tool_stderr", "")

    def test_output_finalization_fails_closed_when_compute_key_expires_mid_run(self) -> None:
        history_id = self.dataset_populator.new_history()
        with open(self.test_data_resolver.get_filename("crypt4gh/test.fastqsanger.c4gh"), "rb") as encrypted_input:
            input_dataset = self.dataset_populator.new_dataset(
                history_id,
                content=encrypted_input,
                file_type="fastqsanger.c4gh",
                fetch_data=False,
                wait=True,
            )

        input_dataset_id = input_dataset["id"]
        input_hda_database_id = self._app.security.decode_id(input_dataset_id)
        sa_session = self._app.model.session
        input_hda = sa_session.get(model.HistoryDatasetAssociation, input_hda_database_id)
        assert input_hda is not None
        self._set_input_compute_metadata(input_hda)
        sa_session.commit()

        previous_expiration = self._mock_compute_recryptor_server.compute_keypair_expiration_date
        self._mock_compute_recryptor_server.compute_keypair_expiration_date = (
            datetime.now(timezone.utc) - timedelta(minutes=1)
        ).isoformat()
        try:
            run_response = self.dataset_populator.run_tool(
                "output_format",
                {
                    "input_data_1": {"src": "hda", "id": input_dataset_id},
                    "input_data_2": {"src": "hda", "id": input_dataset_id},
                    "input_text": "not_foo_or_bar",
                },
                history_id,
            )
            job_api_id = run_response["jobs"][0]["id"]
            self.dataset_populator.wait_for_job(job_api_id, assert_ok=False)
            job = self.dataset_populator.get_job_details(job_api_id, full=True).json()
        finally:
            self._mock_compute_recryptor_server.compute_keypair_expiration_date = previous_expiration

        assert job["state"] == "error"
        assert "compute key expired before output finalization" in job.get("tool_stderr", "")

instance = integration_util.integration_module_instance(TestCrypt4GHRemoteExecutionIntegration)

test_tools = integration_util.integration_tool_runner(["inheritance_simple", "output_format", "tool_provided_metadata_12"])
