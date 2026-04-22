"""
Mock crypt4gh re-encryptor service for unit and integration tests.

Implements the same ``POST /recrypt_header`` HTTP API as the real user-mode
re-encryptor service (elixir-europe/crypt4gh-recryptor-service), but:

- Runs in-process as a FastAPI app on a random free port
- Performs **real** crypt4gh re-encryption using the Python ``crypt4gh`` library
  (no subprocess, no two-tier architecture, no SSL required)
- Uses fixed test key pairs from ``test-data/crypt4gh/``

Usage in pytest::

    from mock_recryptor_service import MockRecryptorServer

    @pytest.fixture(scope="session")
    def mock_recryptor():
        srv = MockRecryptorServer(
            user_private_key_path="test-data/crypt4gh/user_key.sec",
            compute_public_key_path="test-data/crypt4gh/compute_key.pub",
        )
        srv.start()
        yield srv
        srv.stop()
"""

import base64
import io
import logging
import socket
import threading
import time
from typing import Any

import crypt4gh.header
from crypt4gh.keys import (
    get_private_key,
    get_public_key,
)
from fastapi import (
    FastAPI,
    HTTPException,
)
from nacl.public import PrivateKey as NaclPrivKey
from pydantic import BaseModel
from uvicorn import (
    Config,
    Server,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request / response models (mirrors the real service contract)
# ---------------------------------------------------------------------------


class RecryptRequest(BaseModel):
    crypt4gh_header: str  # base64-encoded crypt4gh header bytes


class RecryptResponse(BaseModel):
    crypt4gh_header: str  # base64-encoded re-encrypted header
    crypt4gh_compute_keypair_id: str
    crypt4gh_compute_keypair_expiration_date: str


class UserPublicKeyResponse(BaseModel):
    crypt4gh_public_key: str  # base64-encoded raw X25519 public key bytes


# ---------------------------------------------------------------------------
# Mock app factory
# ---------------------------------------------------------------------------


def build_mock_app(user_private_key_path: str, compute_public_key_path: str, user_public_key_path: str) -> FastAPI:
    """Return a FastAPI app that re-encrypts crypt4gh headers for testing.

    The user's private key is used to decrypt the session key(s) from the
    incoming header; the compute node's public key is used to re-encrypt them.
    A fresh ephemeral key pair is generated per request (matching the real
    service behaviour).

    Parameters
    ----------
    user_private_key_path:
        Absolute or workspace-relative path to the user's unencrypted Crypt4GH
        private key (``.sec`` file, ``-----BEGIN CRYPT4GH PRIVATE KEY-----``
        format).
    compute_public_key_path:
        Absolute or workspace-relative path to the compute node's Crypt4GH
        public key (``.pub`` file).
    user_public_key_path:
        Absolute or workspace-relative path to the user's Crypt4GH public key
        (``.pub`` file) returned by the ``/user_public_key`` endpoint.
    """
    user_sk = get_private_key(user_private_key_path, lambda: b"")
    compute_pub = get_public_key(compute_public_key_path)
    user_pub = get_public_key(user_public_key_path)

    app = FastAPI(title="mock-crypt4gh-recryptor", version="test")

    @app.post("/recrypt_header", response_model=RecryptResponse)
    def recrypt_header(req: RecryptRequest) -> Any:
        try:
            in_header_bytes = base64.b64decode(req.crypt4gh_header)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid base64: {exc}") from exc

        # Validate magic bytes
        if not in_header_bytes.startswith(b"crypt4gh"):
            raise HTTPException(status_code=400, detail="Payload does not start with crypt4gh magic bytes")

        # Parse header packets from the stored header blob
        try:
            hstream = io.BytesIO(in_header_bytes)
            packets = list(crypt4gh.header.parse(hstream))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed to parse crypt4gh header: {exc}") from exc

        # Re-encrypt: decrypt session key with user's private key, encrypt for
        # the compute node's public key using a fresh ephemeral key.
        ephemeral_sk = NaclPrivKey.generate()
        try:
            new_packets = list(
                crypt4gh.header.reencrypt(
                    packets,
                    keys=[(0, user_sk, None)],
                    recipient_keys=[(0, bytes(ephemeral_sk), compute_pub)],
                )
            )
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Re-encryption failed (wrong user key?): {exc}",
            ) from exc

        new_header = crypt4gh.header.serialize(new_packets)
        new_header_b64 = base64.b64encode(new_header).decode("ascii")

        return RecryptResponse(
            crypt4gh_header=new_header_b64,
            crypt4gh_compute_keypair_id="mock-compute-key-1",
            crypt4gh_compute_keypair_expiration_date="2099-01-01T00:00:00",
        )

    @app.get("/info")
    def info() -> dict:
        return {"name": "mock-crypt4gh-recryptor", "version": "test"}

    @app.get("/user_public_key", response_model=UserPublicKeyResponse)
    def user_public_key(user_id: str) -> UserPublicKeyResponse:
        if not user_id:
            raise HTTPException(status_code=400, detail="user_id is required")
        return UserPublicKeyResponse(crypt4gh_public_key=base64.b64encode(user_pub).decode("ascii"))

    return app


# ---------------------------------------------------------------------------
# Server lifecycle helper
# ---------------------------------------------------------------------------


def _find_free_port() -> int:
    """Pick a free TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, timeout: float = 10.0) -> None:
    """Block until a TCP server is accepting connections on ``port``."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"Mock re-encryptor did not start on port {port} within {timeout}s")


class MockRecryptorServer:
    """In-process mock crypt4gh re-encryptor service.

    Runs the FastAPI app on a random free port using uvicorn in a daemon
    thread.  Start it with :meth:`start` and optionally stop it with
    :meth:`stop`.  The daemon thread exits automatically when the test process
    exits.

    Example::

        srv = MockRecryptorServer("test-data/crypt4gh/user_key.sec",
                                  "test-data/crypt4gh/compute_key.pub")
        srv.start()
        response = requests.post(srv.url + "/recrypt_header", json={...})
        srv.stop()
    """

    def __init__(
        self,
        user_private_key_path: str,
        compute_public_key_path: str,
        user_public_key_path: str | None = None,
    ) -> None:
        self.user_private_key_path = user_private_key_path
        self.compute_public_key_path = compute_public_key_path
        self.user_public_key_path = user_public_key_path or compute_public_key_path
        self.port: int = _find_free_port()
        self._server: Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        """Base URL of the mock service (no trailing slash)."""
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        """Start the mock server in a background daemon thread."""
        app = build_mock_app(self.user_private_key_path, self.compute_public_key_path, self.user_public_key_path)
        config = Config(app, host="127.0.0.1", port=self.port, log_level="warning")
        self._server = Server(config)

        def _run_server() -> None:
            import asyncio

            asyncio.run(self._server.serve())  # type: ignore[union-attr]

        self._thread = threading.Thread(target=_run_server, daemon=True)
        self._thread.start()
        _wait_for_port(self.port)
        log.debug("Mock re-encryptor started on %s", self.url)

    def stop(self) -> None:
        """Signal the server to shut down (optional — daemon thread exits with process)."""
        if self._server is not None:
            self._server.should_exit = True
