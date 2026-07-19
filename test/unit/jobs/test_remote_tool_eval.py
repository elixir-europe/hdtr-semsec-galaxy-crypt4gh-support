import os
import subprocess
import sys
from pathlib import Path

from galaxy.tools.remote_tool_eval import (
    _crypt4gh_cleanup_command,
    _crypt4gh_finalize_postrun_command,
    _mark_outputs_for_compute_keypair_clearance,
    _python_executable_for_embedded_commands,
)


def _python_with_sitecustomize(*, tmp_path: Path, sitecustomize_source: str) -> str:
    injection_root = tmp_path / "sitecustomize_injection"
    injection_root.mkdir(parents=True, exist_ok=True)
    (injection_root / "sitecustomize.py").write_text(sitecustomize_source)

    wrapped_python = tmp_path / "wrapped_python.sh"
    wrapped_python.write_text(
        "#!/bin/sh\n"
        f"export PYTHONPATH={str(injection_root)}:$PYTHONPATH\n"
        f"exec {sys.executable} \"$@\"\n"
    )
    wrapped_python.chmod(0o755)
    return str(wrapped_python)


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


def test_finalize_command_best_effort_purge_skips_paths_outside_allowed_roots_on_import_failure(tmp_path):
    bad_python = tmp_path / "bad_python.sh"
    bad_python.write_text("#!/bin/sh\nexec /usr/bin/python3 -S \"$@\"\n")
    bad_python.chmod(0o755)

    safe_root = tmp_path / "safe"
    safe_root.mkdir(parents=True, exist_ok=True)

    outside_output_path = tmp_path / "outside" / "dataset.dat"
    outside_output_path.parent.mkdir(parents=True, exist_ok=True)
    outside_output_path.write_text("PLAINTEXT")

    metadata_params_path = tmp_path / "metadata" / "params.json"
    metadata_params_path.parent.mkdir(parents=True)
    metadata_params_path.write_text('{"outputs": {}}')

    command = _crypt4gh_finalize_postrun_command(
        output_targets=[
            {
                "association_name": "out1",
                "output_path": str(outside_output_path),
                "plaintext_path": str(safe_root / "_crypt" / "outputs" / "ds_1" / "plaintext"),
                "encrypted_marker_path": str(safe_root / "_c4gh_stage" / "outputs" / "ds_1.encrypted"),
                "encrypted_ext": "txt.c4gh",
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
        allowed_root_paths=[str(safe_root.resolve())],
    )

    completed = subprocess.run(["/bin/bash", "-c", command], check=False, capture_output=True, text=True)

    assert completed.returncode != 0
    assert outside_output_path.exists()
    assert "outside allowed roots" in completed.stderr


def test_finalize_command_best_effort_purge_unlinks_extra_files_directory_symlink_on_import_failure(tmp_path):
    bad_python = tmp_path / "bad_python.sh"
    bad_python.write_text("#!/bin/sh\nexec /usr/bin/python3 -S \"$@\"\n")
    bad_python.chmod(0o755)

    working_root = tmp_path / "job"
    working_root.mkdir(parents=True, exist_ok=True)

    output_path = working_root / "dataset.dat"
    output_path.write_text("PLAINTEXT")

    real_extra_files = working_root / "real_extra_files"
    real_extra_files.mkdir(parents=True, exist_ok=True)
    (real_extra_files / "payload.txt").write_text("secret")

    extra_files_symlink = working_root / "dataset.dat_extra"
    extra_files_symlink.symlink_to(real_extra_files, target_is_directory=True)

    extra_manifest_path = working_root / "extra_files_manifest.json"
    extra_manifest_path.write_text("{}")

    metadata_params_path = tmp_path / "metadata" / "params.json"
    metadata_params_path.parent.mkdir(parents=True)
    metadata_params_path.write_text('{"outputs": {}}')

    command = _crypt4gh_finalize_postrun_command(
        output_targets=[
            {
                "association_name": "out1",
                "output_path": str(output_path),
                "plaintext_path": str(working_root / "_crypt" / "outputs" / "ds_1" / "plaintext"),
                "encrypted_marker_path": str(working_root / "_c4gh_stage" / "outputs" / "ds_1.encrypted"),
                "encrypted_ext": "txt.c4gh",
                "extra_files_output_path": str(extra_files_symlink),
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
        allowed_root_paths=[str(working_root.resolve())],
    )

    completed = subprocess.run(["/bin/bash", "-c", command], check=False, capture_output=True, text=True)

    assert completed.returncode != 0
    assert not extra_files_symlink.exists()
    assert real_extra_files.exists()
    assert (real_extra_files / "payload.txt").exists()


def test_finalize_command_best_effort_purge_logs_concurrent_mutation_for_unlink_race(tmp_path):
    galaxy_lib_for_finalize = "/tmp/galaxy/lib"

    working_root = tmp_path / "job"
    working_root.mkdir(parents=True, exist_ok=True)
    output_path = working_root / "dataset.dat"
    output_path.write_text("PLAINTEXT")
    metadata_params_path = tmp_path / "metadata" / "params.json"
    metadata_params_path.parent.mkdir(parents=True)
    metadata_params_path.write_text('{"outputs": {}}')

    wrapped_python = _python_with_sitecustomize(
        tmp_path=tmp_path,
        sitecustomize_source=(
            "import os\n"
            "_TARGET = os.environ.get('CRYPT4GH_TEST_UNLINK_RACE_TARGET', '')\n"
            "_ORIGINAL_UNLINK = os.unlink\n"
            "def _patched_unlink(path, *args, **kwargs):\n"
            "    if not isinstance(path, (str, bytes, os.PathLike)) and args:\n"
            "        path, args = args[0], args[1:]\n"
            "    if _TARGET and os.path.abspath(path) == os.path.abspath(_TARGET):\n"
            "        try:\n"
            "            _ORIGINAL_UNLINK(path, *args, **kwargs)\n"
            "        except FileNotFoundError:\n"
            "            pass\n"
            "        raise FileNotFoundError('simulated concurrent mutation')\n"
            "    return _ORIGINAL_UNLINK(path, *args, **kwargs)\n"
            "os.unlink = _patched_unlink\n"
        ),
    )

    command = _crypt4gh_finalize_postrun_command(
        output_targets=[
            {
                "association_name": "out1",
                "output_path": str(output_path),
                "plaintext_path": str(working_root / "_crypt" / "outputs" / "ds_1" / "plaintext"),
                "encrypted_marker_path": str(working_root / "_c4gh_stage" / "outputs" / "ds_1.encrypted"),
                "encrypted_ext": "txt.c4gh",
                "clear_compute_keypair": True,
            }
        ],
        metadata_params_path=str(metadata_params_path),
        galaxy_lib_for_finalize=galaxy_lib_for_finalize,
        reencryption_service_url="http://127.0.0.1:36667",
        compute_public_key="invalid-public-key",
        compute_keypair_id="mock-keypair",
        compute_keypair_expiration_date="2099-01-01T00:00:00+00:00",
        python_executable=wrapped_python,
        allowed_root_paths=[str(working_root.resolve())],
    )

    completed = subprocess.run(
        ["/bin/bash", "-c", command],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "CRYPT4GH_TEST_UNLINK_RACE_TARGET": str(output_path),
        },
    )

    assert completed.returncode != 0
    assert "ModuleNotFoundError" in completed.stderr
    assert "best-effort purge observed concurrent mutation" in completed.stderr
    assert "FileNotFoundError" in completed.stderr


def test_finalize_command_best_effort_purge_logs_permission_errors_without_masking_finalize_failure(tmp_path):
    galaxy_lib_for_finalize = str(Path(__file__).resolve().parents[3] / "lib")

    working_root = tmp_path / "job"
    working_root.mkdir(parents=True, exist_ok=True)
    output_path = working_root / "dataset.dat"
    output_path.write_text("PLAINTEXT")

    extra_files_dir = working_root / "dataset.dat_extra"
    extra_files_dir.mkdir(parents=True, exist_ok=True)
    (extra_files_dir / "payload.txt").write_text("EXTRA")

    metadata_params_path = tmp_path / "metadata" / "params.json"
    metadata_params_path.parent.mkdir(parents=True)
    metadata_params_path.write_text('{"outputs": {}}')

    wrapped_python = _python_with_sitecustomize(
        tmp_path=tmp_path,
        sitecustomize_source=(
            "import os\n"
            "import shutil\n"
            "_TARGET = os.environ.get('CRYPT4GH_TEST_RMTREE_PERMISSION_TARGET', '')\n"
            "_ORIGINAL_RMTREE = shutil.rmtree\n"
            "def _patched_rmtree(path, *args, **kwargs):\n"
            "    if _TARGET and os.path.abspath(path) == os.path.abspath(_TARGET):\n"
            "        raise PermissionError('simulated permission denied during purge')\n"
            "    return _ORIGINAL_RMTREE(path, *args, **kwargs)\n"
            "shutil.rmtree = _patched_rmtree\n"
        ),
    )

    command = _crypt4gh_finalize_postrun_command(
        output_targets=[
            {
                "association_name": "out1",
                "output_path": str(output_path),
                "plaintext_path": str(working_root / "_crypt" / "outputs" / "ds_1" / "plaintext"),
                "encrypted_marker_path": str(working_root / "_c4gh_stage" / "outputs" / "ds_1.encrypted"),
                "encrypted_ext": "txt.c4gh",
                "extra_files_output_path": str(extra_files_dir),
                "clear_compute_keypair": True,
            }
        ],
        metadata_params_path=str(metadata_params_path),
        galaxy_lib_for_finalize=galaxy_lib_for_finalize,
        reencryption_service_url="http://127.0.0.1:36667",
        compute_public_key="public-key",
        compute_keypair_id="mock-keypair",
        compute_keypair_expiration_date="2099-01-01T00:00:00+00:00",
        python_executable=wrapped_python,
        allowed_root_paths=[str(working_root.resolve())],
    )

    completed = subprocess.run(
        ["/bin/bash", "-c", command],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "CRYPT4GH_TEST_RMTREE_PERMISSION_TARGET": str(extra_files_dir),
        },
    )

    assert completed.returncode != 0
    assert "best-effort purge failed" in completed.stderr
    assert "PermissionError" in completed.stderr


def test_finalize_command_best_effort_purge_reports_partial_outcome_for_concurrent_mutation_stress(tmp_path):
    galaxy_lib_for_finalize = "/tmp/galaxy/lib"

    working_root = tmp_path / "job"
    working_root.mkdir(parents=True, exist_ok=True)
    output_path = working_root / "dataset.dat"
    dataset_output_path = working_root / "dataset_real.dat"
    marker_path = working_root / "marker.encrypted"
    output_path.write_text("PLAINTEXT")
    dataset_output_path.write_text("PLAINTEXT_REAL")
    marker_path.write_text("marker")

    metadata_params_path = tmp_path / "metadata" / "params.json"
    metadata_params_path.parent.mkdir(parents=True)
    metadata_params_path.write_text('{"outputs": {}}')

    wrapped_python = _python_with_sitecustomize(
        tmp_path=tmp_path,
        sitecustomize_source=(
            "import os\n"
            "_TARGETS = set(filter(None, os.environ.get('CRYPT4GH_TEST_UNLINK_RACE_TARGETS', '').split(os.pathsep)))\n"
            "_ORIGINAL_UNLINK = os.unlink\n"
            "def _patched_unlink(path, *args, **kwargs):\n"
            "    if not isinstance(path, (str, bytes, os.PathLike)) and args:\n"
            "        path, args = args[0], args[1:]\n"
            "    candidate = os.path.abspath(path)\n"
            "    if _TARGETS and candidate in {os.path.abspath(p) for p in _TARGETS}:\n"
            "        try:\n"
            "            _ORIGINAL_UNLINK(path, *args, **kwargs)\n"
            "        except FileNotFoundError:\n"
            "            pass\n"
            "        raise FileNotFoundError('simulated concurrent mutation')\n"
            "    return _ORIGINAL_UNLINK(path, *args, **kwargs)\n"
            "os.unlink = _patched_unlink\n"
        ),
    )

    command = _crypt4gh_finalize_postrun_command(
        output_targets=[
            {
                "association_name": "out1",
                "output_path": str(output_path),
                "dataset_output_path": str(dataset_output_path),
                "plaintext_path": str(working_root / "_crypt" / "outputs" / "ds_1" / "plaintext"),
                "encrypted_marker_path": str(marker_path),
                "encrypted_ext": "txt.c4gh",
                "clear_compute_keypair": True,
            }
        ],
        metadata_params_path=str(metadata_params_path),
        galaxy_lib_for_finalize=galaxy_lib_for_finalize,
        reencryption_service_url="http://127.0.0.1:36667",
        compute_public_key="invalid-public-key",
        compute_keypair_id="mock-keypair",
        compute_keypair_expiration_date="2099-01-01T00:00:00+00:00",
        python_executable=wrapped_python,
        allowed_root_paths=[str(working_root.resolve())],
    )

    completed = subprocess.run(
        ["/bin/bash", "-c", command],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "CRYPT4GH_TEST_UNLINK_RACE_TARGETS": os.pathsep.join([str(output_path), str(dataset_output_path)]),
        },
    )

    assert completed.returncode != 0
    assert completed.stderr.count("best-effort purge observed concurrent mutation") >= 2
    assert "best-effort purge partial outcome" in completed.stderr
    assert "concurrent_mutation=2" in completed.stderr


def test_finalize_command_best_effort_purge_reports_partial_outcome_for_permission_denied_stress(tmp_path):
    galaxy_lib_for_finalize = str(Path(__file__).resolve().parents[3] / "lib")

    working_root = tmp_path / "job"
    working_root.mkdir(parents=True, exist_ok=True)
    output_path = working_root / "dataset.dat"
    output_path.write_text("PLAINTEXT")

    extra_files_dir = working_root / "dataset.dat_extra"
    extra_files_dir.mkdir(parents=True, exist_ok=True)
    (extra_files_dir / "payload.txt").write_text("EXTRA")

    marker_path = working_root / "marker.encrypted"
    marker_path.write_text("marker")

    metadata_params_path = tmp_path / "metadata" / "params.json"
    metadata_params_path.parent.mkdir(parents=True)
    metadata_params_path.write_text('{"outputs": {}}')

    wrapped_python = _python_with_sitecustomize(
        tmp_path=tmp_path,
        sitecustomize_source=(
            "import os\n"
            "import shutil\n"
            "_UNLINK_TARGETS = set(filter(None, os.environ.get('CRYPT4GH_TEST_PERMISSION_UNLINK_TARGETS', '').split(os.pathsep)))\n"
            "_RMTREE_TARGETS = set(filter(None, os.environ.get('CRYPT4GH_TEST_PERMISSION_RMTREE_TARGETS', '').split(os.pathsep)))\n"
            "_ORIGINAL_UNLINK = os.unlink\n"
            "_ORIGINAL_RMTREE = shutil.rmtree\n"
            "def _patched_unlink(path, *args, **kwargs):\n"
            "    if not isinstance(path, (str, bytes, os.PathLike)) and args:\n"
            "        path, args = args[0], args[1:]\n"
            "    candidate = os.path.abspath(path)\n"
            "    if candidate in {os.path.abspath(p) for p in _UNLINK_TARGETS}:\n"
            "        raise PermissionError('simulated permission denied during unlink purge')\n"
            "    return _ORIGINAL_UNLINK(path, *args, **kwargs)\n"
            "def _patched_rmtree(path, *args, **kwargs):\n"
            "    candidate = os.path.abspath(path)\n"
            "    if candidate in {os.path.abspath(p) for p in _RMTREE_TARGETS}:\n"
            "        raise PermissionError('simulated permission denied during rmtree purge')\n"
            "    return _ORIGINAL_RMTREE(path, *args, **kwargs)\n"
            "os.unlink = _patched_unlink\n"
            "shutil.rmtree = _patched_rmtree\n"
        ),
    )

    command = _crypt4gh_finalize_postrun_command(
        output_targets=[
            {
                "association_name": "out1",
                "output_path": str(output_path),
                "plaintext_path": str(working_root / "_crypt" / "outputs" / "ds_1" / "plaintext"),
                "encrypted_marker_path": str(marker_path),
                "encrypted_ext": "txt.c4gh",
                "extra_files_output_path": str(extra_files_dir),
                "clear_compute_keypair": True,
            }
        ],
        metadata_params_path=str(metadata_params_path),
        galaxy_lib_for_finalize=galaxy_lib_for_finalize,
        reencryption_service_url="http://127.0.0.1:36667",
        compute_public_key="public-key",
        compute_keypair_id="mock-keypair",
        compute_keypair_expiration_date="2099-01-01T00:00:00+00:00",
        python_executable=wrapped_python,
        allowed_root_paths=[str(working_root.resolve())],
    )

    completed = subprocess.run(
        ["/bin/bash", "-c", command],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "CRYPT4GH_TEST_PERMISSION_UNLINK_TARGETS": os.pathsep.join([str(output_path), str(marker_path)]),
            "CRYPT4GH_TEST_PERMISSION_RMTREE_TARGETS": str(extra_files_dir),
        },
    )

    assert completed.returncode != 0
    assert completed.stderr.count("best-effort purge failed") >= 3
    assert "best-effort purge partial outcome" in completed.stderr
    assert "failed=3" in completed.stderr


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


def test_finalize_command_preserves_target_specific_allowed_roots(tmp_path):
    galaxy_lib_for_finalize = str(Path(__file__).resolve().parents[3] / "lib")

    working_root = tmp_path / "job_work"
    working_root.mkdir(parents=True, exist_ok=True)
    object_store_root = tmp_path / "objects"
    object_store_root.mkdir(parents=True, exist_ok=True)

    output_path = working_root / "working" / "1"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("PLAINTEXT")

    dataset_output_path = object_store_root / "dataset_real.dat"
    plaintext_path = working_root / "_crypt" / "outputs" / "ds_1" / "plaintext"
    marker_path = working_root / "_c4gh_stage" / "outputs" / "ds_1.encrypted"

    metadata_params_path = tmp_path / "metadata" / "params.json"
    metadata_params_path.parent.mkdir(parents=True)
    metadata_params_path.write_text('{"outputs": {}}')

    command = _crypt4gh_finalize_postrun_command(
        output_targets=[
            {
                "association_name": "out1",
                "output_path": str(output_path),
                "dataset_output_path": str(dataset_output_path),
                "plaintext_path": str(plaintext_path),
                "encrypted_marker_path": str(marker_path),
                "encrypted_ext": "txt.c4gh",
                "clear_compute_keypair": True,
                "allowed_root_paths": [
                    str(working_root.resolve()),
                    str(dataset_output_path.parent.resolve()),
                ],
            }
        ],
        metadata_params_path=str(metadata_params_path),
        galaxy_lib_for_finalize=galaxy_lib_for_finalize,
        reencryption_service_url="http://127.0.0.1:36667",
        compute_public_key="invalid-public-key",
        compute_keypair_id="mock-keypair",
        compute_keypair_expiration_date="2099-01-01T00:00:00+00:00",
        python_executable=sys.executable,
        allowed_root_paths=[str(working_root.resolve())],
    )

    completed = subprocess.run(["/bin/bash", "-c", command], check=False, capture_output=True, text=True)

    assert completed.returncode != 0
    assert "outside allowed roots" not in completed.stderr


def test_python_executable_for_embedded_commands_preserves_invocation_path(monkeypatch):
    invocation_path = "/tmp/mock-venv/bin/python"
    monkeypatch.setattr(sys, "executable", invocation_path)

    resolved = _python_executable_for_embedded_commands()

    assert resolved == invocation_path


def test_mark_outputs_for_compute_keypair_clearance_falls_back_to_output_path_when_dataset_path_differs(tmp_path):
    metadata_params_path = tmp_path / "metadata" / "params.json"
    metadata_params_path.parent.mkdir(parents=True)
    metadata_params_path.write_text(
        """
        {
          "outputs": {
            "out1": {
              "filename_override": "/tmp/job/working/outputs/1",
              "clear_crypt4gh_compute_keypair": false
            }
          }
        }
        """.strip()
    )

    _mark_outputs_for_compute_keypair_clearance(
        metadata_params_path=str(metadata_params_path),
        output_targets=[
            {
                "association_name": "out1",
                "clear_compute_keypair": True,
                "dataset_output_path": "/tmp/object_store/dataset_1.dat",
                "output_path": "/tmp/job/working/outputs/1",
            }
        ],
    )

    updated = metadata_params_path.read_text()
    assert '"clear_crypt4gh_compute_keypair": true' in updated
