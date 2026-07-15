from types import SimpleNamespace

import pytest

from galaxy.model.store.discover import _maybe_finalize_crypt4gh_about_to_persist_payload


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
