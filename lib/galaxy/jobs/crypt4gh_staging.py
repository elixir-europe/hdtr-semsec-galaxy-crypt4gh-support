"""
Utilities for per-job crypt4gh header re-encryption and file staging.

Phase 2 of crypt4gh Galaxy support.  Galaxy never holds any user or compute
private key; the external re-encryptor service re-encrypts the crypt4gh header
for the destination compute node's key pair.  Only the header bytes travel over
the network — the (potentially enormous) encrypted body is streamed locally.

At job preparation time the staged re-encrypted file is decrypted to a
temporary file using the Python ``crypt4gh`` library.  The tool
command line is rewritten to point to the decrypted file; no FUSE mount is
required.
"""

import base64
import io
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

import crypt4gh.header
import requests

if TYPE_CHECKING:
    from galaxy.model import DatasetInstance

log = logging.getLogger(__name__)

# Filename of the decrypted file written inside each per-dataset
# staging directory before the tool runs.
_STAGED_INNER_NAME = "input"


class JobPreparationException(Exception):
    """Raised when crypt4gh staging cannot be completed before a job starts."""


@dataclass
class StagedCrypt4GHInput:
    """All paths required to decrypt a staged crypt4gh input for a single dataset."""

    #: Absolute path to the re-encrypted staged ``.crypt4gh`` file
    staged_path: str
    #: Absolute path to the source directory (contains the staged file)
    stage_dir: str
    #: Absolute path to the decrypted file the tool will read
    decrypted_path: str
    #: Original (encrypted) dataset path — used to rewrite the job command line
    original_dataset_path: str
    #: Compute key-pair ID returned by the re-encryptor service (for auditing)
    compute_keypair_id: str


def prepare_crypt4gh_input(
    dataset: "DatasetInstance",
    reencryption_service_url: str,
    working_directory: str,
    compute_key_path: str,
) -> StagedCrypt4GHInput:
    """Re-encrypt the crypt4gh header via the re-encryptor service, write
    ``new_header + original_encrypted_body`` to a staging directory, and
    record the path where the decrypted file will be written by the pre-job
    decrypt step.

    Parameters
    ----------
    dataset:
        The input HDA/LDDA whose ``file_ext`` ends in ``.crypt4gh``.
    reencryption_service_url:
        Base URL of the crypt4gh re-encryptor service user-mode endpoint.
    working_directory:
        Job working directory; the staging directory is created here.
    compute_key_path:
        Absolute path to the compute node's Crypt4GH private key (``.sec``
        file).  Galaxy reads this key in the pre-job step to decrypt the
        staged file using the ``crypt4gh`` Python library.  The key must be
        readable by the Galaxy job runner process.

    Returns
    -------
    StagedCrypt4GHInput
        Paths needed to inject the decrypt commands into the job script and
        to rewrite the dataset path in the tool command line.

    Raises
    ------
    JobPreparationException
        If ``compute_key_path`` is empty or unreadable, if the dataset is
        missing the required ``crypt4gh_header`` metadata, if the
        re-encryptor service call fails, or if the staged file cannot be
        written.
    """
    if not compute_key_path:
        raise JobPreparationException(
            "compute_key_path is required for crypt4gh staging "
            "(must point to the compute node's Crypt4GH private key .sec file)."
        )
    if not os.path.isfile(compute_key_path):
        raise JobPreparationException(
            f"crypt4gh compute key not found at {compute_key_path!r}. "
            "Ensure the file exists and is readable by the Galaxy job runner."
        )

    header_b64: str | None = getattr(dataset.metadata, "crypt4gh_header", None)
    if not header_b64:
        raise JobPreparationException(
            f"Dataset {dataset.id} is missing crypt4gh_header metadata. "
            "Ensure set_meta ran successfully after upload."
        )

    # ── 1. Contact the re-encryptor service ──────────────────────────────────
    service_url = reencryption_service_url.rstrip("/")
    try:
        response = requests.post(
            f"{service_url}/recrypt_header",
            json={"crypt4gh_header": header_b64},
            timeout=30,
        )
    except requests.RequestException as exc:
        raise JobPreparationException(
            f"Failed to contact crypt4gh re-encryptor service at {service_url}: {exc}"
        ) from exc

    if not response.ok:
        raise JobPreparationException(
            f"crypt4gh re-encryptor service returned HTTP {response.status_code} "
            f"for dataset {dataset.id}: {response.text}"
        )

    try:
        response_data = response.json()
        new_header_b64: str = response_data["crypt4gh_header"]
        compute_keypair_id: str = response_data.get("crypt4gh_compute_keypair_id", "unknown")
    except (ValueError, KeyError) as exc:
        raise JobPreparationException(f"Unexpected response from re-encryptor service: {exc}") from exc

    new_header_bytes = base64.b64decode(new_header_b64)

    # ── 2. Determine the original header length from stored metadata ─────────
    original_header_bytes = base64.b64decode(header_b64)
    try:
        stream = io.BytesIO(original_header_bytes)
        list(crypt4gh.header.parse(stream))
        original_header_length = stream.tell()
    except Exception as exc:
        raise JobPreparationException(
            f"Stored crypt4gh_header for dataset {dataset.id} is not a valid crypt4gh header: {exc}"
        ) from exc

    # ── 3. Write staged file: new header + original encrypted body ────────────
    dataset_path = dataset.get_file_name()
    ds_id = dataset.id

    stage_dir = os.path.join(working_directory, "_c4gh_stage", f"ds_{ds_id}")
    os.makedirs(stage_dir, exist_ok=True)
    staged_path = os.path.join(stage_dir, "input.crypt4gh")
    decrypted_path = os.path.join(stage_dir, _STAGED_INNER_NAME)

    try:
        with open(dataset_path, "rb") as src, open(staged_path, "wb") as dst:
            dst.write(new_header_bytes)
            src.seek(original_header_length)
            while True:
                chunk = src.read(65536)
                if not chunk:
                    break
                dst.write(chunk)
    except OSError as exc:
        raise JobPreparationException(f"Failed to write staged crypt4gh file for dataset {ds_id}: {exc}") from exc

    log.debug(
        "Staged crypt4gh input for dataset %s: staged=%s decrypted=%s compute_keypair=%s",
        ds_id,
        staged_path,
        decrypted_path,
        compute_keypair_id,
    )

    return StagedCrypt4GHInput(
        staged_path=staged_path,
        stage_dir=stage_dir,
        decrypted_path=decrypted_path,
        original_dataset_path=dataset_path,
        compute_keypair_id=compute_keypair_id,
    )


def build_crypt4gh_pre_commands(
    staged_inputs: list[StagedCrypt4GHInput],
    compute_key_path: str,
    passphrase_env: str | None = None,
) -> str:
    """Return shell commands to decrypt all staged crypt4gh inputs before the tool runs.

    Each input is decrypted from its ``staged_path`` to ``decrypted_path``
    using the Python ``crypt4gh`` library via the Galaxy virtual environment's
    Python interpreter.  No FUSE mount is required.

    Parameters
    ----------
    staged_inputs:
        List of :class:`StagedCrypt4GHInput` instances produced by
        :func:`prepare_crypt4gh_input`.
    compute_key_path:
        Absolute path to the compute node's Crypt4GH private key (``.sec``).
    passphrase_env:
        If provided, the name of the environment variable whose value is used
        as the passphrase when loading the private key.  If absent or ``None``
        the key is assumed to be passphrase-free.

    Returns
    -------
    str
        Shell snippet to be injected before ``$command`` in the job script.
    """
    lines = [
        "mkdir -p outputs",
        "touch outputs/tool_stdout outputs/tool_stderr",
    ]
    passphrase_expr = f'os.environb.get(b"{passphrase_env}", b"")' if passphrase_env else 'b""'
    for i, si in enumerate(staged_inputs):
        lines.append(f"# Decrypt crypt4gh input {i} (dataset original: {si.original_dataset_path})")
        # Inline Python one-liner: load key → decrypt staged file → write decrypted file.
        # Single-quotes wrap the -c argument; internal single-quotes are escaped.
        py_script = (
            "import crypt4gh.lib, crypt4gh.keys, os; "
            f"sk = crypt4gh.keys.get_private_key({_py_str(compute_key_path)}, lambda: {passphrase_expr}); "
            f"inf = open({_py_str(si.staged_path)}, 'rb'); "
            f"outf = open({_py_str(si.decrypted_path)}, 'wb'); "
            "crypt4gh.lib.decrypt([(0, sk, None)], inf, outf); "
            "inf.close(); outf.close()"
        )
        lines.append(f'"${{GALAXY_VIRTUAL_ENV}}/bin/python" -c {_shell_quote(py_script)}')
        lines.append(f"if [ ! -e {_shell_quote(si.decrypted_path)} ]; then")
        lines.append(f"    echo 'ERROR: crypt4gh decryption did not produce {si.decrypted_path}' >&2")
        lines.append("    exit 1")
        lines.append("fi")
    return "\n".join(lines)


def build_crypt4gh_post_commands(staged_inputs: list[StagedCrypt4GHInput]) -> str:
    """Return shell commands to clean up decrypted files after the tool finishes.

    Parameters
    ----------
    staged_inputs:
        Same list passed to :func:`build_crypt4gh_pre_commands`.

    Returns
    -------
    str
        Shell snippet to be injected after ``$command`` in the job script.
    """
    lines = []
    for i, si in enumerate(staged_inputs):
        lines.append(f"# Remove decrypted file for input {i}")
        lines.append(f"rm -f {_shell_quote(si.decrypted_path)}")
    return "\n".join(lines)


def _py_str(path: str) -> str:
    """Format a filesystem path as a Python string literal (double-quoted, escaped)."""
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _shell_quote(path: str) -> str:
    """Minimally quote a string for safe shell interpolation (single-quote wrap)."""
    return "'" + path.replace("'", "'\\''") + "'"
