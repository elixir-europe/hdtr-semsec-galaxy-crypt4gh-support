import subprocess
import sys
from pathlib import Path

from galaxy.tools.remote_tool_eval import (
    _crypt4gh_cleanup_command,
    _crypt4gh_finalize_postrun_command,
    _python_executable_for_embedded_commands,
)


def test_cleanup_command_best_effort_removes_plaintext_even_when_import_fails(tmp_path):
    bad_python = tmp_path / "bad_python.sh"
    bad_python.write_text("#!/bin/sh\nexec /usr/bin/python3 -S \"$@\"\n")
    bad_python.chmod(0o755)

    working_directory = tmp_path / "job"
    plaintext_inputs = working_directory / "_crypt" / "inputs" / "ds_1"
    plaintext_outputs = working_directory / "_crypt" / "outputs" / "ds_2"
    plaintext_inputs.mkdir(parents=True)
    plaintext_outputs.mkdir(parents=True)
    (plaintext_inputs / "plaintext").write_text("secret-in")
    (plaintext_outputs / "plaintext").write_text("secret-out")

    command = _crypt4gh_cleanup_command(
        galaxy_lib_for_finalize="/tmp/galaxy/lib",
        working_directory=str(working_directory),
        python_executable=str(bad_python),
    )

    completed = subprocess.run(["/bin/bash", "-c", command], check=False, capture_output=True, text=True)

    assert completed.returncode != 0
    assert not (working_directory / "_crypt" / "inputs").exists()
    assert not (working_directory / "_crypt" / "outputs").exists()


def test_finalize_command_purges_plaintext_outputs_even_when_import_fails(tmp_path):
    bad_python = tmp_path / "bad_python.sh"
    bad_python.write_text("#!/bin/sh\nexec /usr/bin/python3 -S \"$@\"\n")
    bad_python.chmod(0o755)

    output_path = tmp_path / "dataset.dat"
    dataset_output_path = tmp_path / "dataset_real.dat"
    marker_path = tmp_path / "marker.encrypted"
    extra_files_dir = tmp_path / "dataset.dat_extra"
    extra_manifest_path = tmp_path / "extra_files_manifest.json"

    output_path.write_text("PLAINTEXT")
    dataset_output_path.write_text("PLAINTEXT_REAL")
    extra_files_dir.mkdir(parents=True)
    (extra_files_dir / "payload.txt").write_text("EXTRA")
    extra_manifest_path.write_text("{}")

    metadata_params_path = tmp_path / "metadata" / "params.json"
    metadata_params_path.parent.mkdir(parents=True)
    metadata_params_path.write_text('{"outputs": {}}')

    command = _crypt4gh_finalize_postrun_command(
        output_targets=[
            {
                "association_name": "out1",
                "output_path": str(output_path),
                "dataset_output_path": str(dataset_output_path),
                "plaintext_path": str(tmp_path / "_crypt" / "outputs" / "ds_1" / "plaintext"),
                "encrypted_marker_path": str(marker_path),
                "encrypted_ext": "txt.c4gh",
                "extra_files_output_path": str(extra_files_dir),
                "extra_files_manifest_path": str(extra_manifest_path),
                "clear_compute_keypair": True,
            }
        ],
        metadata_params_path=str(metadata_params_path),
        galaxy_lib_for_finalize="/tmp/galaxy/lib",
        reencryption_service_url="http://127.0.0.1:36667",
        compute_public_key="public-key",
        compute_keypair_id="mock-keypair",
        compute_keypair_expiration_date="2099-01-01T00:00:00+00:00",
        python_executable=str(bad_python),
        allowed_root_paths=[str(tmp_path.resolve())],
    )

    completed = subprocess.run(["/bin/bash", "-c", command], check=False, capture_output=True, text=True)

    assert completed.returncode != 0
    assert not output_path.exists()
    assert not dataset_output_path.exists()
    assert not marker_path.exists()
    assert not extra_files_dir.exists()
    assert not extra_manifest_path.exists()


def test_finalize_command_rejects_output_targets_outside_allowed_roots(tmp_path):
    galaxy_lib_for_finalize = str(Path(__file__).resolve().parents[3] / "lib")

    safe_root = tmp_path / "safe"
    safe_root.mkdir(parents=True, exist_ok=True)
    unsafe_output_path = tmp_path / "outside" / "dataset.dat"
    unsafe_output_path.parent.mkdir(parents=True, exist_ok=True)
    unsafe_output_path.write_text("PLAINTEXT")

    metadata_params_path = tmp_path / "metadata" / "params.json"
    metadata_params_path.parent.mkdir(parents=True)
    metadata_params_path.write_text('{"outputs": {}}')

    command = _crypt4gh_finalize_postrun_command(
        output_targets=[
            {
                "association_name": "out1",
                "output_path": str(unsafe_output_path),
                "plaintext_path": str(safe_root / "_crypt" / "outputs" / "ds_1" / "plaintext"),
                "encrypted_marker_path": str(safe_root / "_c4gh_stage" / "outputs" / "ds_1.encrypted"),
                "encrypted_ext": "txt.c4gh",
                "clear_compute_keypair": True,
            }
        ],
        metadata_params_path=str(metadata_params_path),
        galaxy_lib_for_finalize=galaxy_lib_for_finalize,
        reencryption_service_url="http://127.0.0.1:36667",
        compute_public_key="public-key",
        compute_keypair_id="mock-keypair",
        compute_keypair_expiration_date="2099-01-01T00:00:00+00:00",
        python_executable=sys.executable,
        allowed_root_paths=[str(safe_root.resolve())],
    )

    completed = subprocess.run(["/bin/bash", "-c", command], check=False, capture_output=True, text=True)

    assert completed.returncode != 0
    assert "outside allowed roots" in completed.stderr


def test_python_executable_for_embedded_commands_preserves_invocation_path(monkeypatch):
    invocation_path = "/tmp/mock-venv/bin/python"
    monkeypatch.setattr(sys, "executable", invocation_path)

    resolved = _python_executable_for_embedded_commands()

    assert resolved == invocation_path
