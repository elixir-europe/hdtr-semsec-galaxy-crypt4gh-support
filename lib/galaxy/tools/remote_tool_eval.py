"""Remote tool evaluation entrypoint with optional Crypt4GH staging/finalization."""

import json
import os
import shlex
import shutil
import sys
import tempfile
import traceback
from collections.abc import Callable
from typing import (
    NamedTuple,
    cast,
)
from logging import getLogger

from galaxy.datatypes.registry import Registry
from galaxy.files import ConfiguredFileSources
from galaxy.job_execution.compute_environment import SharedComputeEnvironment
from galaxy.job_execution.setup import JobIO
from galaxy.managers.dbkeys import GenomeBuilds
from galaxy.metadata.set_metadata import (
    get_metadata_params,
    get_object_store,
    validate_and_load_datatypes_config,
)
from galaxy.model import store
from galaxy.model.store import SessionlessContext
from galaxy.objectstore import BaseObjectStore
from galaxy.structured_app import MinimalToolApp
from galaxy.tools import (
    create_tool_from_representation,
    evaluation,
)
from galaxy.tools.crypt4gh_remote_execution import (
    build_crypt4gh_cleanup_wrapped_command,
    build_crypt4gh_remote_compute_environment,
    cleanup_crypt4gh_plaintext_artifacts,
    collect_declared_crypt4gh_output_targets,
    CRYPT4GH_PLAINTEXT_CLEANUP_FAILED_MARKER,
    should_run_crypt4gh_remote_execution,
)
from galaxy.tools.data import (
    from_dict,
    ToolDataTableManager,
)
from galaxy.util.bunch import Bunch


log = getLogger("galaxy.tools.remote_tool_eval")


class ToolAppConfig(NamedTuple):
    """Configuration container for ``ToolApp``."""

    name: str
    tool_data_path: str
    galaxy_data_manager_data_path: str
    nginx_upload_path: str
    len_file_path: str
    builds_file_path: str
    root: str
    is_admin_user: Callable
    enable_crypt4gh_remote_execution_staging: bool = False
    admin_users: list = []


class ToolApp(MinimalToolApp):
    """Dummy App that allows loading tools"""

    name = "tool_app"
    is_webapp = False

    def __init__(
        self,
        sa_session: SessionlessContext,
        tool_app_config: ToolAppConfig,
        datatypes_registry: Registry,
        object_store: BaseObjectStore,
        tool_data_table_manager: ToolDataTableManager,
        file_sources: ConfiguredFileSources,
    ):
        """Initialize a minimal app with just enough state for tool evaluation."""

        # For backward compatibility we need both context and session attributes that point to sa_session.
        self.model = Bunch(context=sa_session, session=sa_session)
        self.config = tool_app_config
        self.datatypes_registry = datatypes_registry
        self.object_store = object_store
        self.genome_builds = GenomeBuilds(self)
        self._tool_data_tables = tool_data_table_manager
        self.file_sources = file_sources
        self.biotools_metadata_source = None
        self.security = None  # type: ignore[assignment]

    @property
    def tool_data_tables(self) -> ToolDataTableManager:
        """Return loaded tool data tables for evaluator access."""

        return self._tool_data_tables


def _resolve_datatypes_config_path(*, working_directory: str, metadata_params: dict) -> str:
    datatypes_config = metadata_params["datatypes_config"]
    if os.path.exists(datatypes_config):
        return datatypes_config
    return os.path.join(working_directory, "configs", datatypes_config)


def _load_tool_data_tables(import_store_directory: str) -> ToolDataTableManager:
    with open(os.path.join(import_store_directory, "tool_data_tables.json")) as data_tables_json:
        return from_dict(json.load(data_tables_json))


def _build_tool_app(
    *,
    tmpdir: str,
    job_io: JobIO,
    metadata_params: dict,
    datatypes_registry: Registry,
    object_store: BaseObjectStore,
    sa_session: SessionlessContext,
    tool_data_table_manager: ToolDataTableManager,
) -> ToolApp:
    tool_app_config = ToolAppConfig(
        name="tool_app",
        tool_data_path=job_io.tool_data_path,
        galaxy_data_manager_data_path=job_io.galaxy_data_manager_data_path,
        nginx_upload_path=tmpdir,
        len_file_path=job_io.len_file_path,
        builds_file_path=job_io.builds_file_path,
        root=tmpdir,
        is_admin_user=lambda _: job_io.user_context.is_admin,
        enable_crypt4gh_remote_execution_staging=bool(
            metadata_params.get("enable_crypt4gh_remote_execution_staging", False)
        ),
    )
    return ToolApp(
        sa_session=sa_session,
        tool_app_config=tool_app_config,
        datatypes_registry=datatypes_registry,
        object_store=object_store,
        tool_data_table_manager=tool_data_table_manager,
        file_sources=job_io.file_sources,
    )


def _destination_params_for_remote_eval(job_io: JobIO) -> dict:
    destination_params = dict(job_io.job.destination_params or {})
    # This entrypoint is only prepended for remote tool evaluation, but a
    # globally configured tool_evaluation_strategy may not be persisted into
    # per-job destination params in integration test setups.
    destination_params.setdefault("tool_evaluation_strategy", "remote")
    return destination_params


def _python_executable_for_embedded_commands() -> str:
    """Return the active interpreter path without resolving venv symlinks.

    Resolving ``sys.executable`` to a real path escapes virtualenv shims and can
    drop environment-provided dependencies for embedded postrun/cleanup helpers.
    """

    return sys.executable


def _render_embedded_python_command(*, galaxy_lib_for_finalize: str, python_executable: str, script: str) -> str:
    return (
        f"PYTHONPATH={shlex.quote(galaxy_lib_for_finalize)}:$PYTHONPATH "
        f"{shlex.quote(python_executable)} -c {shlex.quote(script)}"
    )


def _crypt4gh_cleanup_command(
    *,
    galaxy_lib_for_finalize: str,
    working_directory: str,
    python_executable: str,
) -> str:
    cleanup_script = (
        "import os\n"
        "import shutil\n"
        "\n"
        f"_WORKING_DIRECTORY = {json.dumps(working_directory)}\n"
        "\n"
        "def _best_effort_cleanup() -> None:\n"
        "    crypt_root = os.path.join(_WORKING_DIRECTORY, '_crypt')\n"
        "    for child_name in ('inputs', 'outputs'):\n"
        "        child_path = os.path.join(crypt_root, child_name)\n"
        "        if os.path.exists(child_path):\n"
        "            shutil.rmtree(child_path, ignore_errors=True)\n"
        "\n"
        "try:\n"
        "    from galaxy.tools.crypt4gh_remote_execution import cleanup_crypt4gh_plaintext_artifacts as _cleanup\n"
        "    _cleanup(working_directory=_WORKING_DIRECTORY)\n"
        "except Exception:\n"
        "    _best_effort_cleanup()\n"
        "    raise\n"
    )
    return _render_embedded_python_command(
        galaxy_lib_for_finalize=galaxy_lib_for_finalize,
        python_executable=python_executable,
        script=cleanup_script,
    )


def _mark_outputs_for_compute_keypair_clearance(
    *, metadata_params_path: str, output_targets: list[dict[str, object]]
) -> None:
    with open(metadata_params_path) as metadata_stream:
        metadata_params = json.load(metadata_stream)

    outputs_by_path: dict[str, dict[str, object]] = {}
    for output_metadata in metadata_params.get("outputs", {}).values():
        filename_override = output_metadata.get("filename_override")
        if filename_override:
            outputs_by_path[str(filename_override)] = output_metadata

    for output_target in output_targets:
        if not output_target.get("clear_compute_keypair"):
            continue
        candidate_paths = [
            output_target.get("dataset_output_path"),
            output_target.get("output_path"),
        ]
        for candidate_path in candidate_paths:
            if not candidate_path:
                continue
            output_metadata = outputs_by_path.get(str(candidate_path))
            if output_metadata is not None:
                output_metadata["clear_crypt4gh_compute_keypair"] = True
                break

    with open(metadata_params_path, "w") as metadata_stream:
        json.dump(metadata_params, metadata_stream)


def _record_crypt4gh_compute_context(
    *,
    metadata_params_path: str,
    compute_public_key: str,
    compute_keypair_id: str,
    compute_keypair_expiration_date: object,
) -> None:
    with open(metadata_params_path) as metadata_stream:
        metadata_params = json.load(metadata_stream)

    metadata_params["crypt4gh_compute_public_key"] = compute_public_key
    metadata_params["crypt4gh_compute_keypair_id"] = compute_keypair_id
    metadata_params["crypt4gh_compute_keypair_expiration_date"] = str(compute_keypair_expiration_date or "")

    with open(metadata_params_path, "w") as metadata_stream:
        json.dump(metadata_params, metadata_stream)


def _crypt4gh_finalize_postrun_command(
    *,
    output_targets: list[dict[str, object]],
    metadata_params_path: str,
    galaxy_lib_for_finalize: str,
    reencryption_service_url: str,
    compute_public_key: str,
    compute_keypair_id: str,
    compute_keypair_expiration_date: object,
    python_executable: str,
    allowed_root_paths: list[str],
) -> str:
    finalize_script = (
        "import json\n"
        "import os\n"
        "import shutil\n"
        "import sys\n"
        "\n"
        f"_TARGETS = json.loads({json.dumps(json.dumps(output_targets))})\n"
        f"_ALLOWED_ROOTS = json.loads({json.dumps(json.dumps(allowed_root_paths))})\n"
        "if _ALLOWED_ROOTS:\n"
        "    for _target in _TARGETS:\n"
        "        if isinstance(_target, dict):\n"
        "            _existing_roots = _target.get('allowed_root_paths')\n"
        "            _merged_roots = []\n"
        "            _seen_roots = set()\n"
        "            if isinstance(_existing_roots, (list, tuple)):\n"
        "                for _root in _existing_roots:\n"
        "                    _root_text = str(_root or '')\n"
        "                    if _root_text and _root_text not in _seen_roots:\n"
        "                        _seen_roots.add(_root_text)\n"
        "                        _merged_roots.append(_root_text)\n"
        "            for _root in _ALLOWED_ROOTS:\n"
        "                _root_text = str(_root or '')\n"
        "                if _root_text and _root_text not in _seen_roots:\n"
        "                    _seen_roots.add(_root_text)\n"
        "                    _merged_roots.append(_root_text)\n"
        "            _target['allowed_root_paths'] = _merged_roots\n"
        "\n"
        "def _is_within_allowed_roots(path: str, roots) -> bool:\n"
        "    if not roots:\n"
        "        return True\n"
        "    candidate_path = os.path.abspath(path)\n"
        "    for root_path in roots:\n"
        "        try:\n"
        "            if os.path.commonpath([candidate_path, os.path.abspath(str(root_path))]) == os.path.abspath(str(root_path)):\n"
        "                return True\n"
        "        except Exception:\n"
        "            continue\n"
        "    return False\n"
        "\n"
        "def _safe_remove(path: str, roots) -> None:\n"
        "    if not path:\n"
        "        return\n"
        "    if not _is_within_allowed_roots(path, roots):\n"
        "        print(f'Crypt4GH best-effort purge path is outside allowed roots: {path}', file=sys.stderr)\n"
        "        return\n"
        "    if not os.path.lexists(path):\n"
        "        return\n"
        "    if os.path.islink(path):\n"
        "        os.unlink(path)\n"
        "        return\n"
        "    if os.path.isdir(path):\n"
        "        shutil.rmtree(path, ignore_errors=True)\n"
        "        return\n"
        "    try:\n"
        "        os.unlink(path)\n"
        "    except Exception:\n"
        "        if os.path.isdir(path):\n"
        "            shutil.rmtree(path, ignore_errors=True)\n"
        "\n"
        "def _purge_plaintext_payloads(targets):\n"
        "    for target in targets:\n"
        "        if not isinstance(target, dict):\n"
        "            continue\n"
        "        _roots = target.get('allowed_root_paths')\n"
        "        if not isinstance(_roots, (list, tuple)) or not _roots:\n"
        "            _roots = _ALLOWED_ROOTS\n"
        "        _safe_remove(str(target.get('output_path', '') or ''), _roots)\n"
        "        _safe_remove(str(target.get('dataset_output_path', '') or ''), _roots)\n"
        "        _safe_remove(str(target.get('extra_files_output_path', '') or ''), _roots)\n"
        "        _safe_remove(str(target.get('encrypted_marker_path', '') or ''), _roots)\n"
        "        _safe_remove(str(target.get('extra_files_manifest_path', '') or ''), _roots)\n"
        "\n"
        "try:\n"
        "    from galaxy.tools.crypt4gh_remote_execution import finalize_declared_crypt4gh_outputs as _finalize\n"
        "    from galaxy.tools.remote_tool_eval import _mark_outputs_for_compute_keypair_clearance as _mark\n"
        f"    _mark(metadata_params_path={json.dumps(metadata_params_path)}, output_targets=_TARGETS)\n"
        "    _finalize(\n"
        "        output_targets=_TARGETS,\n"
        f"        reencryption_service_url={json.dumps(reencryption_service_url)},\n"
        f"        compute_public_key={json.dumps(compute_public_key)},\n"
        f"        compute_keypair_id={json.dumps(compute_keypair_id)},\n"
        f"        compute_keypair_expiration_date={json.dumps(compute_keypair_expiration_date)},\n"
        "    )\n"
        "except Exception:\n"
        "    _purge_plaintext_payloads(_TARGETS)\n"
        "    raise\n"
    )
    return _render_embedded_python_command(
        galaxy_lib_for_finalize=galaxy_lib_for_finalize,
        python_executable=python_executable,
        script=finalize_script,
    )


def _resolve_working_directory() -> str:
    working_directory = os.getcwd()
    working_parent = os.path.join(working_directory, os.path.pardir)
    if not os.path.isdir("working") and os.path.isdir(os.path.join(working_parent, "working")):
        # We're probably in pulsar
        return working_parent
    return working_directory


def _metadata_store_directories(*, working_directory: str) -> tuple[str, str]:
    metadata_directory = os.path.join(working_directory, "metadata")
    import_store_directory = os.path.join(metadata_directory, "outputs_new")
    export_store_directory = os.path.join(metadata_directory, "outputs_populated")
    return import_store_directory, export_store_directory


def _persist_failure_outputs(*, working_directory: str, export_store_directory: str, traceback_text: str) -> None:
    os.makedirs(export_store_directory, exist_ok=True)
    with open(os.path.join(export_store_directory, "traceback.txt"), "w") as out:
        out.write(traceback_text)

    outputs_directory = os.path.join(working_directory, "outputs")
    os.makedirs(outputs_directory, exist_ok=True)
    with open(os.path.join(outputs_directory, "tool_stdout"), "a"):
        pass
    with open(os.path.join(outputs_directory, "tool_stderr"), "a") as stderr:
        stderr.write(traceback_text)


def main(TMPDIR, WORKING_DIRECTORY, IMPORT_STORE_DIRECTORY) -> None:
    """Render remote tool command script and persist failure diagnostics."""

    galaxy_lib_for_finalize = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
    metadata_params = get_metadata_params(WORKING_DIRECTORY)
    datatypes_config = _resolve_datatypes_config_path(
        working_directory=WORKING_DIRECTORY,
        metadata_params=metadata_params,
    )
    datatypes_registry = validate_and_load_datatypes_config(datatypes_config)
    python_executable = _python_executable_for_embedded_commands()
    object_store = get_object_store(WORKING_DIRECTORY)
    import_store = store.imported_store_for_metadata(IMPORT_STORE_DIRECTORY)
    assert isinstance(import_store.sa_session, SessionlessContext)
    # TODO: clean up random places from which we read files in the working directory
    job_io = JobIO.from_json(os.path.join(IMPORT_STORE_DIRECTORY, "job_io.json"), sa_session=import_store.sa_session)
    app = _build_tool_app(
        tmpdir=TMPDIR,
        job_io=job_io,
        metadata_params=metadata_params,
        datatypes_registry=datatypes_registry,
        object_store=object_store,
        sa_session=import_store.sa_session,
        tool_data_table_manager=_load_tool_data_tables(IMPORT_STORE_DIRECTORY),
    )
    destination_params = _destination_params_for_remote_eval(job_io)

    is_crypt4gh_job = should_run_crypt4gh_remote_execution(
        job_io=job_io,
        app_config=app.config,
        destination_params=destination_params,
    )
    if is_crypt4gh_job:
        if job_io.tool_source is None or job_io.tool_source_class is None:
            raise Exception("remote tool evaluation requires serialized tool source information")

    # TODO: could try to serialize just a minimal tool variant instead of the whole thing ?
    tool = create_tool_from_representation(
        app=app,
        raw_tool_source=cast(str, job_io.tool_source),
        tool_dir=job_io.tool_dir,
        tool_source_class=cast(str, job_io.tool_source_class),
    )
    tool_evaluator = evaluation.RemoteToolEvaluator(
        app=app, tool=tool, job=job_io.job, local_working_directory=WORKING_DIRECTORY
    )
    reencryption_service_url: str = ""
    if is_crypt4gh_job:
        reencryption_service_url = metadata_params.get("crypt4gh_reencryption_service_url")
        if not reencryption_service_url:
            raise Exception("Crypt4GH remote execution requires crypt4gh_reencryption_service_url")
        compute_environment = build_crypt4gh_remote_compute_environment(
            job_io=job_io,
            job=job_io.job,
            working_directory=WORKING_DIRECTORY,
            reencryption_service_url=reencryption_service_url,
        )
    else:
        compute_environment = SharedComputeEnvironment(job_io=job_io, job=job_io.job)
    tool_evaluator.set_compute_environment(compute_environment=compute_environment)
    with open(os.path.join(WORKING_DIRECTORY, "tool_script.sh"), "a") as out:
        command_line, version_command_line, extra_filenames, environment_variables, *_ = tool_evaluator.build()
        postrun_command = ""
        cleanup_command = ""
        if is_crypt4gh_job:
            cleanup_command = _crypt4gh_cleanup_command(
                galaxy_lib_for_finalize=galaxy_lib_for_finalize,
                working_directory=WORKING_DIRECTORY,
                python_executable=python_executable,
            )

            compute_public_key = getattr(compute_environment, "compute_public_key", None)
            compute_keypair_id = getattr(compute_environment, "compute_keypair_id", None)
            compute_keypair_expiration_date = getattr(compute_environment, "compute_keypair_expiration_date", None)
            if not compute_public_key or not compute_keypair_id:
                raise Exception(
                    "Crypt4GH output finalization requires compute public key and compute keypair id"
                )

            _record_crypt4gh_compute_context(
                metadata_params_path=os.path.join(WORKING_DIRECTORY, "metadata", "params.json"),
                compute_public_key=cast(str, compute_public_key),
                compute_keypair_id=cast(str, compute_keypair_id),
                compute_keypair_expiration_date=compute_keypair_expiration_date,
            )

            output_targets = collect_declared_crypt4gh_output_targets(
                job_io=job_io,
                tool_outputs=tool.outputs,
                datatypes_registry=app.datatypes_registry,
                working_directory=WORKING_DIRECTORY,
            )
            if output_targets:
                postrun_command = _crypt4gh_finalize_postrun_command(
                    output_targets=cast(list[dict[str, object]], output_targets),
                    metadata_params_path=os.path.join(WORKING_DIRECTORY, "metadata", "params.json"),
                    galaxy_lib_for_finalize=galaxy_lib_for_finalize,
                    reencryption_service_url=reencryption_service_url,
                    compute_public_key=cast(str, compute_public_key),
                    compute_keypair_id=cast(str, compute_keypair_id),
                    compute_keypair_expiration_date=compute_keypair_expiration_date,
                    python_executable=python_executable,
                    allowed_root_paths=[os.path.abspath(WORKING_DIRECTORY)],
                )
        command_line = build_crypt4gh_cleanup_wrapped_command(
            tool_command=command_line or "",
            cleanup_command=cleanup_command,
            postrun_command=postrun_command,
        )
        out.write(f'{version_command_line or ""}{command_line}')


if __name__ == "__main__":
    TMPDIR = tempfile.mkdtemp()
    WORKING_DIRECTORY = _resolve_working_directory()
    IMPORT_STORE_DIRECTORY, EXPORT_STORE_DIRECTORY = _metadata_store_directories(working_directory=WORKING_DIRECTORY)
    try:
        main(TMPDIR, WORKING_DIRECTORY, IMPORT_STORE_DIRECTORY)
    except Exception:
        traceback_text = traceback.format_exc()
        cleanup_warning = ""
        try:
            cleanup_crypt4gh_plaintext_artifacts(working_directory=WORKING_DIRECTORY)
        except Exception as cleanup_exc:
            cleanup_warning = (
                f"\n{CRYPT4GH_PLAINTEXT_CLEANUP_FAILED_MARKER}: cleanup failed before tool script finalization: {cleanup_exc}\n"
            )
        traceback_text = f"{traceback_text}{cleanup_warning}"
        _persist_failure_outputs(
            working_directory=WORKING_DIRECTORY,
            export_store_directory=EXPORT_STORE_DIRECTORY,
            traceback_text=traceback_text,
        )
        raise
    finally:
        shutil.rmtree(TMPDIR, ignore_errors=True)
