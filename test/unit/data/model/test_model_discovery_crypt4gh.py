from types import SimpleNamespace

import pytest
from galaxy.util.crypt4gh import CRYPT4GH_DEFAULT_EXT

from galaxy.model.store.discover import (
    _maybe_finalize_crypt4gh_about_to_persist_payload,
    _resolve_discovered_crypt4gh_extension,
    ModelPersistenceContext,
)


def test_about_to_persist_finalization_allows_missing_dataset_id_when_designation_present(tmp_path, monkeypatch):
    output_path = tmp_path / "sample1.tabular.c4gh"
    output_path.write_bytes(b"crypt4gh")

    calls = []

    def _fake_finalize_about_to_persist_crypt4gh_payload(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution.finalize_about_to_persist_crypt4gh_payload",
        _fake_finalize_about_to_persist_crypt4gh_payload,
    )

    context = SimpleNamespace(
        job_working_directory=str(tmp_path),
        crypt4gh_output_finalization_context=lambda: {
            "reencryption_service_url": "http://localhost:8000",
            "compute_public_key": "-----BEGIN CRYPT4GH PUBLIC KEY-----\nabc\n-----END CRYPT4GH PUBLIC KEY-----\n",
            "compute_keypair_id": "key-1",
            "compute_keypair_expiration_date": "",
        },
    )
    primary_data = SimpleNamespace(
        extension="tabular.c4gh",
        designation="sample1",
        job_working_directory=str(tmp_path),
        dataset=SimpleNamespace(id=None, get_file_name=lambda sync_cache=False: ""),
    )

    finalized = _maybe_finalize_crypt4gh_about_to_persist_payload(
        model_persistence_context=context,
        primary_data=primary_data,
        filename=str(output_path),
    )

    assert finalized is True
    assert len(calls) == 1
    call = calls[0]
    assert call["output_path"] == str(output_path)
    assert "dataset_output_path" not in call
    assert call["designation"] == "sample1"
    assert call["discovered_marker_map_path"].endswith("_c4gh_stage/outputs/discovered_designations.json")
    assert call["encrypted_marker_path"] == ""
    assert "path_" in call["plaintext_path"]


def test_about_to_persist_finalization_passes_extra_files_paths(tmp_path, monkeypatch):
    output_path = tmp_path / "sample1.tabular.c4gh"
    output_path.write_bytes(b"crypt4gh")

    extra_files_path = tmp_path / "sample1_files"
    extra_files_path.mkdir(parents=True, exist_ok=True)
    (extra_files_path / "nested").mkdir(parents=True, exist_ok=True)
    (extra_files_path / "nested" / "child.txt").write_text("payload")

    calls = []

    def _fake_finalize_about_to_persist_crypt4gh_payload(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution.finalize_about_to_persist_crypt4gh_payload",
        _fake_finalize_about_to_persist_crypt4gh_payload,
    )

    context = SimpleNamespace(
        job_working_directory=str(tmp_path),
        crypt4gh_output_finalization_context=lambda: {
            "reencryption_service_url": "http://localhost:8000",
            "compute_public_key": "-----BEGIN CRYPT4GH PUBLIC KEY-----\nabc\n-----END CRYPT4GH PUBLIC KEY-----\n",
            "compute_keypair_id": "key-1",
            "compute_keypair_expiration_date": "",
        },
    )
    dataset_path = tmp_path / "objects" / "dataset_7.dat"
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    dataset_path.write_text("cipher")
    primary_data = SimpleNamespace(
        extension="tabular.c4gh",
        designation="sample1",
        job_working_directory=str(tmp_path),
        dataset=SimpleNamespace(id=7, get_file_name=lambda sync_cache=False: str(dataset_path)),
    )

    finalized = _maybe_finalize_crypt4gh_about_to_persist_payload(
        model_persistence_context=context,
        primary_data=primary_data,
        filename=str(output_path),
        extra_files_path=str(extra_files_path),
    )

    assert finalized is True
    assert len(calls) == 1
    call = calls[0]
    assert call["extra_files_output_path"] == str(extra_files_path)
    assert call["extra_files_manifest_path"].endswith("_c4gh_stage/outputs/ds_7.extra_files_manifest.json")


def test_about_to_persist_finalization_resolves_dataset_id_before_extra_files_manifest(tmp_path, monkeypatch):
    output_path = tmp_path / "sample1.tabular.c4gh"
    output_path.write_bytes(b"crypt4gh")

    extra_files_path = tmp_path / "sample1_files"
    extra_files_path.mkdir(parents=True, exist_ok=True)
    (extra_files_path / "foo").write_text("payload")

    calls = []

    def _fake_finalize_about_to_persist_crypt4gh_payload(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution.finalize_about_to_persist_crypt4gh_payload",
        _fake_finalize_about_to_persist_crypt4gh_payload,
    )

    dataset_object = SimpleNamespace(id=None, get_file_name=lambda sync_cache=False: "")

    class _Session:
        def flush(self) -> None:
            dataset_object.id = 13

    context = SimpleNamespace(
        job_working_directory=str(tmp_path),
        crypt4gh_output_finalization_context=lambda: {
            "reencryption_service_url": "http://localhost:8000",
            "compute_public_key": "-----BEGIN CRYPT4GH PUBLIC KEY-----\nabc\n-----END CRYPT4GH PUBLIC KEY-----\n",
            "compute_keypair_id": "key-1",
            "compute_keypair_expiration_date": "",
        },
        sa_session=_Session(),
    )
    primary_data = SimpleNamespace(
        extension="tabular.c4gh",
        designation="sample1",
        job_working_directory=str(tmp_path),
        dataset=dataset_object,
    )

    finalized = _maybe_finalize_crypt4gh_about_to_persist_payload(
        model_persistence_context=context,
        primary_data=primary_data,
        filename=str(output_path),
        extra_files_path=str(extra_files_path),
    )

    assert finalized is True
    assert len(calls) == 1
    call = calls[0]
    assert call["extra_files_output_path"] == str(extra_files_path)
    assert call["extra_files_manifest_path"].endswith("_c4gh_stage/outputs/ds_13.extra_files_manifest.json")


def test_about_to_persist_finalization_resolves_dataset_object_created_during_flush(tmp_path, monkeypatch):
    output_path = tmp_path / "sample1.tabular.c4gh"
    output_path.write_bytes(b"crypt4gh")

    extra_files_path = tmp_path / "sample1_files"
    extra_files_path.mkdir(parents=True, exist_ok=True)
    (extra_files_path / "foo").write_text("payload")

    calls = []

    def _fake_finalize_about_to_persist_crypt4gh_payload(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution.finalize_about_to_persist_crypt4gh_payload",
        _fake_finalize_about_to_persist_crypt4gh_payload,
    )

    primary_data = SimpleNamespace(
        extension="tabular.c4gh",
        designation="sample1",
        job_working_directory=str(tmp_path),
        dataset=None,
    )

    class _Session:
        def flush(self) -> None:
            primary_data.dataset = SimpleNamespace(id=21, get_file_name=lambda sync_cache=False: "")

    context = SimpleNamespace(
        job_working_directory=str(tmp_path),
        crypt4gh_output_finalization_context=lambda: {
            "reencryption_service_url": "http://localhost:8000",
            "compute_public_key": "-----BEGIN CRYPT4GH PUBLIC KEY-----\nabc\n-----END CRYPT4GH PUBLIC KEY-----\n",
            "compute_keypair_id": "key-1",
            "compute_keypair_expiration_date": "",
        },
        sa_session=_Session(),
    )

    finalized = _maybe_finalize_crypt4gh_about_to_persist_payload(
        model_persistence_context=context,
        primary_data=primary_data,
        filename=str(output_path),
        extra_files_path=str(extra_files_path),
    )

    assert finalized is True
    assert len(calls) == 1
    call = calls[0]
    assert call["extra_files_output_path"] == str(extra_files_path)
    assert call["extra_files_manifest_path"].endswith("_c4gh_stage/outputs/ds_21.extra_files_manifest.json")


def test_about_to_persist_finalization_uses_designation_manifest_when_dataset_id_missing(tmp_path, monkeypatch):
    output_path = tmp_path / "sample1.tabular.c4gh"
    output_path.write_bytes(b"crypt4gh")

    extra_files_path = tmp_path / "sample1_files"
    extra_files_path.mkdir(parents=True, exist_ok=True)
    (extra_files_path / "foo").write_text("payload")

    calls = []

    def _fake_finalize_about_to_persist_crypt4gh_payload(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution.finalize_about_to_persist_crypt4gh_payload",
        _fake_finalize_about_to_persist_crypt4gh_payload,
    )

    context = SimpleNamespace(
        job_working_directory=str(tmp_path),
        crypt4gh_output_finalization_context=lambda: {
            "reencryption_service_url": "http://localhost:8000",
            "compute_public_key": "-----BEGIN CRYPT4GH PUBLIC KEY-----\nabc\n-----END CRYPT4GH PUBLIC KEY-----\n",
            "compute_keypair_id": "key-1",
            "compute_keypair_expiration_date": "",
        },
        sa_session=None,
    )
    primary_data = SimpleNamespace(
        extension="tabular.c4gh",
        designation="sample1",
        job_working_directory=str(tmp_path),
        dataset=SimpleNamespace(id=None, get_file_name=lambda sync_cache=False: ""),
    )

    finalized = _maybe_finalize_crypt4gh_about_to_persist_payload(
        model_persistence_context=context,
        primary_data=primary_data,
        filename=str(output_path),
        extra_files_path=str(extra_files_path),
    )

    assert finalized is True
    assert len(calls) == 1
    call = calls[0]
    assert call["extra_files_output_path"] == str(extra_files_path)
    assert call["extra_files_manifest_path"].endswith(
        "_c4gh_stage/outputs/designation_sample1.extra_files_manifest.json"
    )


def test_about_to_persist_finalization_fails_closed_when_dataset_id_and_designation_missing(tmp_path):
    output_path = tmp_path / "output.tabular.c4gh"
    output_path.write_bytes(b"crypt4gh")

    context = SimpleNamespace(
        job_working_directory=str(tmp_path),
        crypt4gh_output_finalization_context=lambda: {
            "reencryption_service_url": "http://localhost:8000",
            "compute_public_key": "-----BEGIN CRYPT4GH PUBLIC KEY-----\nabc\n-----END CRYPT4GH PUBLIC KEY-----\n",
            "compute_keypair_id": "key-1",
            "compute_keypair_expiration_date": "",
        },
    )
    primary_data = SimpleNamespace(
        extension="tabular.c4gh",
        designation="",
        job_working_directory=str(tmp_path),
        dataset=SimpleNamespace(id=None, get_file_name=lambda sync_cache=False: ""),
    )

    with pytest.raises(RuntimeError, match="missing both persisted dataset id and designation"):
        _maybe_finalize_crypt4gh_about_to_persist_payload(
            model_persistence_context=context,
            primary_data=primary_data,
            filename=str(output_path),
        )


class _RegistryForExtensionResolution:
    def __init__(self):
        self.created_from: list[str] = []

    def get_datatype_by_extension(self, extension: str):
        return None

    def get_or_create_crypt4gh_datatype(self, extension: str):
        self.created_from.append(extension)
        return object()


def test_resolve_discovered_extension_keeps_already_encrypted_extension(monkeypatch):
    registry = _RegistryForExtensionResolution()
    monkeypatch.setattr(
        "galaxy.model.store.discover._first_existing_crypt4gh_marker_directory",
        lambda **kwargs: "/tmp/marker-dir",
    )
    monkeypatch.setattr("galaxy.model._get_datatypes_registry", lambda: registry)

    resolved = _resolve_discovered_crypt4gh_extension(
        ext="tabular.c4gh",
        job_working_directory="/tmp/job-dir",
    )

    assert resolved == "tabular.c4gh"
    assert registry.created_from == []


def test_resolve_discovered_extension_keeps_generic_crypt4gh_extension(monkeypatch):
    registry = _RegistryForExtensionResolution()
    monkeypatch.setattr(
        "galaxy.model.store.discover._first_existing_crypt4gh_marker_directory",
        lambda **kwargs: "/tmp/marker-dir",
    )
    monkeypatch.setattr("galaxy.model._get_datatypes_registry", lambda: registry)

    resolved = _resolve_discovered_crypt4gh_extension(
        ext=CRYPT4GH_DEFAULT_EXT,
        job_working_directory="/tmp/job-dir",
    )

    assert resolved == CRYPT4GH_DEFAULT_EXT
    assert registry.created_from == []


def test_resolve_discovered_extension_requires_crypt4gh_without_marker_dir(monkeypatch):
    registry = _RegistryForExtensionResolution()
    monkeypatch.setattr(
        "galaxy.model.store.discover._first_existing_crypt4gh_marker_directory",
        lambda **kwargs: None,
    )
    monkeypatch.setattr("galaxy.model._get_datatypes_registry", lambda: registry)

    resolved = _resolve_discovered_crypt4gh_extension(
        ext="tabular",
        job_working_directory="/tmp/job-dir",
        require_crypt4gh_extension=True,
    )

    assert resolved == "tabular.c4gh"
    assert registry.created_from == ["tabular"]


def test_resolve_discovered_extension_does_not_force_crypt4gh_for_empty_marker_dir_without_required_context(
    tmp_path, monkeypatch
):
    registry = _RegistryForExtensionResolution()
    marker_dir = tmp_path / "job" / "_c4gh_stage" / "outputs"
    marker_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("galaxy.model._get_datatypes_registry", lambda: registry)

    resolved = _resolve_discovered_crypt4gh_extension(
        ext="tabular",
        job_working_directory=str(tmp_path / "job"),
    )

    assert resolved == "tabular"
    assert registry.created_from == []


def test_resolve_discovered_extension_uses_dataset_marker_evidence_without_required_context(tmp_path, monkeypatch):
    registry = _RegistryForExtensionResolution()
    marker_dir = tmp_path / "job" / "_c4gh_stage" / "outputs"
    marker_dir.mkdir(parents=True, exist_ok=True)
    (marker_dir / "ds_41.encrypted").write_text("tabular.c4gh\n")
    monkeypatch.setattr("galaxy.model._get_datatypes_registry", lambda: registry)

    resolved = _resolve_discovered_crypt4gh_extension(
        ext="tabular",
        job_working_directory=str(tmp_path / "job"),
    )

    assert resolved == "tabular.c4gh"
    assert registry.created_from == ["tabular"]


def test_resolve_discovered_extension_treats_marker_dir_list_race_as_no_evidence_without_required_context(monkeypatch):
    registry = _RegistryForExtensionResolution()
    marker_dir = "/tmp/marker-dir"
    monkeypatch.setattr(
        "galaxy.model.store.discover._first_existing_crypt4gh_marker_directory",
        lambda **kwargs: marker_dir,
    )
    monkeypatch.setattr("galaxy.model.store.discover.os.listdir", lambda path: (_ for _ in ()).throw(FileNotFoundError(path)))
    monkeypatch.setattr("galaxy.model._get_datatypes_registry", lambda: registry)

    resolved = _resolve_discovered_crypt4gh_extension(
        ext="tabular",
        job_working_directory="/tmp/job-dir",
    )

    assert resolved == "tabular"
    assert registry.created_from == []


def test_resolve_discovered_extension_still_requires_crypt4gh_when_marker_dir_list_races(monkeypatch):
    registry = _RegistryForExtensionResolution()
    marker_dir = "/tmp/marker-dir"
    monkeypatch.setattr(
        "galaxy.model.store.discover._first_existing_crypt4gh_marker_directory",
        lambda **kwargs: marker_dir,
    )
    monkeypatch.setattr("galaxy.model.store.discover.os.listdir", lambda path: (_ for _ in ()).throw(FileNotFoundError(path)))
    monkeypatch.setattr("galaxy.model._get_datatypes_registry", lambda: registry)

    resolved = _resolve_discovered_crypt4gh_extension(
        ext="tabular",
        job_working_directory="/tmp/job-dir",
        require_crypt4gh_extension=True,
    )

    assert resolved == "tabular.c4gh"
    assert registry.created_from == ["tabular"]


def test_resolve_discovered_extension_treats_marker_dir_oserror_race_as_no_evidence_without_required_context(monkeypatch):
    registry = _RegistryForExtensionResolution()
    marker_dir = "/tmp/marker-dir"
    monkeypatch.setattr(
        "galaxy.model.store.discover._first_existing_crypt4gh_marker_directory",
        lambda **kwargs: marker_dir,
    )
    monkeypatch.setattr("galaxy.model.store.discover.os.listdir", lambda path: (_ for _ in ()).throw(OSError(path)))
    monkeypatch.setattr("galaxy.model._get_datatypes_registry", lambda: registry)

    resolved = _resolve_discovered_crypt4gh_extension(
        ext="tabular",
        job_working_directory="/tmp/job-dir",
    )

    assert resolved == "tabular"
    assert registry.created_from == []


def test_resolve_discovered_extension_requires_crypt4gh_when_marker_dir_oserror_races(monkeypatch):
    registry = _RegistryForExtensionResolution()
    marker_dir = "/tmp/marker-dir"
    monkeypatch.setattr(
        "galaxy.model.store.discover._first_existing_crypt4gh_marker_directory",
        lambda **kwargs: marker_dir,
    )
    monkeypatch.setattr("galaxy.model.store.discover.os.listdir", lambda path: (_ for _ in ()).throw(OSError(path)))
    monkeypatch.setattr("galaxy.model._get_datatypes_registry", lambda: registry)

    resolved = _resolve_discovered_crypt4gh_extension(
        ext="tabular",
        job_working_directory="/tmp/job-dir",
        require_crypt4gh_extension=True,
    )

    assert resolved == "tabular.c4gh"
    assert registry.created_from == ["tabular"]


def test_set_datasets_metadata_can_require_crypt4gh_extension_resolution(monkeypatch):
    class _PrimaryData:
        states = type("States", (), {"OK": "ok", "FAILED_METADATA": "failed_metadata"})

        def __init__(self):
            self.extension = "tabular"
            self.name = "sample"
            self.info = ""
            self.dbkey = "?"
            self.job_working_directory = "/tmp/jobdir"
            self.state = "ok"
            self.metadata = SimpleNamespace(from_JSON_dict=lambda json_dict: None)

        def set_meta(self):
            return None

        def set_peek(self):
            return None

        def set_total_size(self):
            return None

    observed_require_flags = []

    def _fake_resolve_discovered_crypt4gh_extension(*, ext, job_working_directory, require_crypt4gh_extension=False):
        del ext
        del job_working_directory
        observed_require_flags.append(require_crypt4gh_extension)
        return "tabular.c4gh" if require_crypt4gh_extension else "tabular"

    monkeypatch.setattr(
        "galaxy.model.store.discover._resolve_discovered_crypt4gh_extension",
        _fake_resolve_discovered_crypt4gh_extension,
    )

    primary_data = _PrimaryData()
    ModelPersistenceContext.set_datasets_metadata(
        [primary_data],
        [{"ext": "tabular"}],
        require_crypt4gh_extension=True,
    )

    assert primary_data.extension == "tabular.c4gh"
    assert observed_require_flags == [True]


class _FakeObjectStore:
    def __init__(self):
        self.update_calls = []

    def update_from_file(self, dataset, file_name, create=True):
        self.update_calls.append({"dataset": dataset, "file_name": file_name, "create": create})


class _FakeDiscoveredDataset:
    def __init__(self, *, dataset_id: int, extension: str, designation: str, job_working_directory: str):
        self.dataset = SimpleNamespace(id=dataset_id, object_store_id=None, get_file_name=lambda sync_cache=False: "")
        self.extension = extension
        self.designation = designation
        self.job_working_directory = job_working_directory
        self.set_size_calls = []

    def set_size(self, *, no_extra_files: bool = False):
        self.set_size_calls.append(no_extra_files)


def test_collection_discovery_path_finalizes_crypt4gh_before_object_store_persist(tmp_path, monkeypatch):
    output_path = tmp_path / "outputs" / "sample1.tabular.c4gh"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"crypt4gh")

    finalize_calls = []

    def _fake_finalize_about_to_persist_crypt4gh_payload(**kwargs):
        finalize_calls.append(kwargs)

    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution.finalize_about_to_persist_crypt4gh_payload",
        _fake_finalize_about_to_persist_crypt4gh_payload,
    )

    object_store = _FakeObjectStore()
    context = SimpleNamespace(
        object_store=object_store,
        override_object_store_id=lambda _output_name: None,
        crypt4gh_output_finalization_context=lambda: {
            "reencryption_service_url": "http://localhost:8000",
            "compute_public_key": "-----BEGIN CRYPT4GH PUBLIC KEY-----\nabc\n-----END CRYPT4GH PUBLIC KEY-----\n",
            "compute_keypair_id": "key-1",
            "compute_keypair_expiration_date": "",
        },
        job_working_directory=str(tmp_path),
        sa_session=None,
    )
    dataset = _FakeDiscoveredDataset(
        dataset_id=41,
        extension="tabular.c4gh",
        designation="sample1",
        job_working_directory=str(tmp_path),
    )

    ModelPersistenceContext.update_object_store_with_datasets(
        context,
        datasets=[dataset],
        paths=[str(output_path)],
        extra_files=[None],
        output_name="out",
    )

    assert len(finalize_calls) == 1
    finalize_call = finalize_calls[0]
    assert finalize_call["output_path"] == str(output_path)
    assert finalize_call["encrypted_marker_path"].endswith("_c4gh_stage/outputs/ds_41.encrypted")
    assert object_store.update_calls and object_store.update_calls[0]["file_name"] == str(output_path)


def test_collection_discovery_path_fails_closed_when_crypt4gh_finalization_fails(tmp_path, monkeypatch):
    output_path = tmp_path / "outputs" / "sample1.tabular.c4gh"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"crypt4gh")

    def _raise_finalize_about_to_persist_crypt4gh_payload(**_kwargs):
        raise RuntimeError("encryption failed")

    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution.finalize_about_to_persist_crypt4gh_payload",
        _raise_finalize_about_to_persist_crypt4gh_payload,
    )

    object_store = _FakeObjectStore()
    context = SimpleNamespace(
        object_store=object_store,
        override_object_store_id=lambda _output_name: None,
        crypt4gh_output_finalization_context=lambda: {
            "reencryption_service_url": "http://localhost:8000",
            "compute_public_key": "-----BEGIN CRYPT4GH PUBLIC KEY-----\nabc\n-----END CRYPT4GH PUBLIC KEY-----\n",
            "compute_keypair_id": "key-1",
            "compute_keypair_expiration_date": "",
        },
        job_working_directory=str(tmp_path),
        sa_session=None,
    )
    dataset = _FakeDiscoveredDataset(
        dataset_id=41,
        extension="tabular.c4gh",
        designation="sample1",
        job_working_directory=str(tmp_path),
    )

    with pytest.raises(RuntimeError, match="encryption failed"):
        ModelPersistenceContext.update_object_store_with_datasets(
            context,
            datasets=[dataset],
            paths=[str(output_path)],
            extra_files=[None],
            output_name="out",
        )

    assert object_store.update_calls == []
