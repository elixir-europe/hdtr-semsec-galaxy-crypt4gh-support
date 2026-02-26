"""
Utilities for per-job crypt4gh header re-encryption and file staging.

Phase 2 of crypt4gh Galaxy support.  Galaxy never holds any user or compute
private key; the external re-encryptor service re-encrypts the crypt4gh header
for the destination compute node's key pair.  Only the header bytes travel over
the network — the (potentially enormous) encrypted body is streamed locally.
"""

import base64
import io
import logging
import os
from configparser import RawConfigParser
from dataclasses import dataclass
from typing import TYPE_CHECKING

import crypt4gh.header
import requests

if TYPE_CHECKING:
    from galaxy.model import DatasetInstance

log = logging.getLogger(__name__)

# Basename of the staged crypt4gh file placed inside each per-dataset staging
# directory.  crypt4ghfs strips the `.crypt4gh` extension when presenting the
# decrypted file through the FUSE mount, so the mounted (decrypted) file will
# appear as ``_STAGED_INNER_NAME``.
_STAGED_NAME = "input.crypt4gh"
_STAGED_INNER_NAME = "input"  # name after crypt4ghfs strips the .crypt4gh suffix


class JobPreparationException(Exception):
    """Raised when crypt4gh staging cannot be completed before a job starts."""


@dataclass
class StagedCrypt4GHInput:
    """All paths required to mount a staged crypt4gh input for a single dataset."""

    #: Absolute path to the re-encrypted staged ``.crypt4gh`` file
    staged_path: str
    #: Absolute path to the source directory (contains the staged file)
    stage_dir: str
    #: Absolute path to the directory where crypt4ghfs will expose the decrypted file
    mount_dir: str
    #: Absolute path of the decrypted file as seen by the tool after mounting
    mounted_path: str
    #: Original (encrypted) dataset path — used to rewrite the job command line
    original_dataset_path: str
    #: Compute key-pair ID returned by the re-encryptor service (for auditing)
    compute_keypair_id: str
    #: Per-dataset crypt4ghfs conf file (base conf + ``rootdir = stage_dir``)
    dataset_conf_path: str


def _write_dataset_conf(key_config_path: str, stage_dir: str, output_path: str) -> None:
    """Write a per-dataset crypt4ghfs conf file that inherits the base
    configuration and sets ``rootdir`` to ``stage_dir``.

    crypt4ghfs requires ``rootdir`` (in the ``[DEFAULT]`` section) to know where
    the encrypted source files live.  We generate a fresh conf per dataset so
    that each mount points to its own staging directory.

    The file is created with mode 0o600 because crypt4ghfs refuses to load a
    conf file that is readable by group or world.

    Raises
    ------
    JobPreparationException
        If the base conf is missing a ``[CRYPT4GH]`` section or ``seckey`` key.
    """
    conf = RawConfigParser()
    conf.read([key_config_path])

    # Validate that the base conf contains the required key reference.
    if not conf.has_section("CRYPT4GH") or not conf.get("CRYPT4GH", "seckey", fallback=None):
        raise JobPreparationException(
            f"Base key config {key_config_path!r} is missing [CRYPT4GH] seckey. "
            "This must point to the compute node's crypt4gh private key."
        )

    conf.set("DEFAULT", "rootdir", stage_dir)
    # Always force Galaxy's .crypt4gh extension convention.  The crypt4ghfs
    # default is .c4gh — if we allow that to slip through from the base conf,
    # crypt4ghfs will find no matching files in stage_dir and the mountpoint
    # will appear empty to the tool.
    conf.set("DEFAULT", "extension", ".crypt4gh")
    # Ensure [FUSE] section is explicit so crypt4ghfs's custom getset() converter
    # is invoked rather than returning the fallback string unchanged (which would
    # cause pyfuse3 to iterate over each character of the options string).
    if not conf.has_section("FUSE"):
        conf.add_section("FUSE")
        conf.set("FUSE", "options", "ro,default_permissions")
    with open(output_path, "w") as fp:
        conf.write(fp)
    os.chmod(output_path, 0o600)


def prepare_crypt4gh_input(
    dataset: "DatasetInstance",
    reencryption_service_url: str,
    working_directory: str,
    key_config_path: str,
) -> StagedCrypt4GHInput:
    """Re-encrypt the crypt4gh header via the user-mode re-encryptor service and
    write ``new_header + original_encrypted_body`` to a staging directory.

    Parameters
    ----------
    dataset:
        The input HDA/LDDA whose ``file_ext`` ends in ``.crypt4gh``.
    reencryption_service_url:
        Base URL of the crypt4gh re-encryptor service user-mode endpoint.
    working_directory:
        Job working directory; staging and mount subdirectories are created here.
    key_config_path:
        **Required.**  Absolute path to a crypt4ghfs configuration file that
        contains a ``[CRYPT4GH]`` section with a ``seckey`` entry pointing to
        the compute node's Crypt4GH private key.  The file must be readable by
        the Galaxy job runner and, for non-local runners (e.g. Pulsar), must be
        accessible on the remote compute node via a shared filesystem or
        pre-staged out-of-band.  The key must be passphrase-free or the
        ``C4GH_PASSPHRASE`` environment variable must be set in the job
        environment.  This path must not be empty.

    Returns
    -------
    StagedCrypt4GHInput
        Paths needed to inject the crypt4ghfs mount commands into the job script
        and to rewrite the dataset path in the tool command line.

    Raises
    ------
    JobPreparationException
        If ``key_config_path`` is empty, if the dataset is missing the required
        ``crypt4gh_header`` metadata, if the base conf lacks ``[CRYPT4GH]
        seckey``, if the re-encryptor service call fails, or if the staged file
        cannot be written.
    """
    if not key_config_path:
        raise JobPreparationException(
            "key_config_path is required for crypt4gh staging "
            "(must point to a crypt4ghfs conf containing [CRYPT4GH] seckey)."
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
    staged_path = os.path.join(stage_dir, _STAGED_NAME)
    dataset_conf_path = os.path.join(stage_dir, "crypt4ghfs.conf")
    _write_dataset_conf(key_config_path, stage_dir, dataset_conf_path)

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

    # ── 4. Prepare FUSE mount directory ──────────────────────────────────────
    # crypt4ghfs CLI: crypt4ghfs [-f|--foreground] [--conf conf_file] <mountpoint>
    # rootdir (the directory containing the encrypted source files) is configured
    # in the conf file, not passed on the command line.  The tool sees a
    # decrypted file at mount_dir/<name_without_.crypt4gh>.
    mount_dir = os.path.join(working_directory, "_c4gh_mnt", f"ds_{ds_id}")
    os.makedirs(mount_dir, exist_ok=True)
    mounted_path = os.path.join(mount_dir, _STAGED_INNER_NAME)

    log.debug(
        "Staged crypt4gh input for dataset %s: staged=%s mount=%s compute_keypair=%s",
        ds_id,
        staged_path,
        mount_dir,
        compute_keypair_id,
    )

    return StagedCrypt4GHInput(
        staged_path=staged_path,
        stage_dir=stage_dir,
        mount_dir=mount_dir,
        mounted_path=mounted_path,
        original_dataset_path=dataset_path,
        compute_keypair_id=compute_keypair_id,
        dataset_conf_path=dataset_conf_path,
    )


def build_crypt4gh_pre_commands(
    staged_inputs: list[StagedCrypt4GHInput],
    mount_timeout: int = 30,
) -> str:
    """Return shell commands to mount all staged crypt4gh inputs before the tool runs.

    Parameters
    ----------
    staged_inputs:
        List of :class:`StagedCrypt4GHInput` instances produced by
        :func:`prepare_crypt4gh_input`.  Each instance carries its own
        ``dataset_conf_path`` — a per-dataset crypt4ghfs conf that sets
        ``rootdir`` to the corresponding staging directory.
    mount_timeout:
        Maximum seconds to wait for the FUSE mount to become ready.
        The script polls every 0.2 s; default matches ``crypt4gh_mount_timeout``
        in ``galaxy.yml``.

    Returns
    -------
    str
        Shell snippet (one or more commands, newline-separated) to be injected
        before ``$command`` in the job script.
    """
    lines = [
        # Ensure tool_stdout / tool_stderr exist even if the script exits early
        # (e.g. mount timeout), so the Galaxy runner finish handler can open them.
        "mkdir -p outputs",
        "touch outputs/tool_stdout outputs/tool_stderr",
    ]
    for i, si in enumerate(staged_inputs):
        var_pid = f"_CRYPT4GHFS_PID_{i}"
        lines.append(f"# Mount crypt4gh input {i} (dataset original: {si.original_dataset_path})")
        # Run crypt4ghfs in foreground mode (-f) so the shell's background PID
        # ($!) refers to the actual long-running FUSE process.  Without -f,
        # crypt4ghfs forks/daemonizes and the tracked PID exits immediately,
        # causing the kill -0 liveness check below to break the loop too early.
        # Use ${GALAXY_VIRTUAL_ENV}/bin/crypt4ghfs (already exported in the job
        # script) rather than a bare 'crypt4ghfs' — the binary is only in the
        # Galaxy venv, not on the system PATH seen by the job shell.
        # Redirect stdout/stderr to /dev/null so crypt4ghfs log output does not
        # pollute the job's stderr and confuse Galaxy's finish-handler parsing.
        lines.append(
            f'"${{GALAXY_VIRTUAL_ENV}}/bin/crypt4ghfs" -f --conf {_shell_quote(si.dataset_conf_path)} '
            f"{_shell_quote(si.mount_dir)} >/dev/null 2>&1 &"
        )
        lines.append(f"{var_pid}=$!")
        # Poll until the FUSE-exposed file appears (up to mount_timeout seconds)
        # rather than relying on a fixed sleep, which may not be long enough.
        poll_iters = max(1, round(mount_timeout / 0.2))
        lines.append("_c4gh_wait=0")
        lines.append(f"until [ -e {_shell_quote(si.mounted_path)} ] || [ $_c4gh_wait -ge {poll_iters} ]; do")
        lines.append("    sleep 0.2")
        lines.append("    _c4gh_wait=$((_c4gh_wait + 1))")
        lines.append(f"    kill -0 ${var_pid} 2>/dev/null || break  # crypt4ghfs process died")
        lines.append("done")
        lines.append(f"if [ ! -e {_shell_quote(si.mounted_path)} ]; then")
        lines.append(f"    echo 'ERROR: crypt4ghfs mount did not appear at {si.mounted_path}' >&2")
        lines.append("    exit 1")
        lines.append("fi")
    return "\n".join(lines)


def build_crypt4gh_post_commands(staged_inputs: list[StagedCrypt4GHInput]) -> str:
    """Return shell commands to unmount all crypt4ghfs mounts after the tool finishes.

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
        var_pid = f"_CRYPT4GHFS_PID_{i}"
        lines.append(f"# Unmount crypt4gh input {i}")
        lines.append(f"fusermount -u {_shell_quote(si.mount_dir)} 2>/dev/null || true")
        lines.append(f"kill ${var_pid} 2>/dev/null || true")
    return "\n".join(lines)


def _shell_quote(path: str) -> str:
    """Minimally quote a path for safe shell interpolation (single-quote wrap)."""
    # Replace any single-quotes in the path itself (uncommon but possible)
    return "'" + path.replace("'", "'\\''") + "'"
