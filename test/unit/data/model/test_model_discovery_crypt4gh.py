from types import SimpleNamespace

import pytest
from galaxy.util.crypt4gh import CRYPT4GH_DEFAULT_EXT

from galaxy.model.store.discover import (
    _maybe_finalize_crypt4gh_about_to_persist_payload,
    _resolve_discovered_crypt4gh_extension,
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
