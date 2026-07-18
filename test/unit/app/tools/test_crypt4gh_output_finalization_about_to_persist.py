import json
from pathlib import Path

import pytest

from galaxy.tools.crypt4gh_remote_execution import (
    Crypt4GHRemoteExecutionError,
    finalize_about_to_persist_crypt4gh_payload,
)


def test_finalize_about_to_persist_payload_encrypts_extra_files_and_writes_manifest(tmp_path, monkeypatch):
    output_path = tmp_path / "discover" / "sample1.tsv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("sample\n")

    extra_files_root = tmp_path / "discover" / "sample1_files"
    (extra_files_root / "nested").mkdir(parents=True, exist_ok=True)
    (extra_files_root / "foo.txt").write_text("foo\n")
    (extra_files_root / "nested" / "bar.txt").write_text("bar\n")

    marker_dir = tmp_path / "_c4gh_stage" / "outputs"
    map_path = marker_dir / "discovered_designations.json"
    manifest_path = marker_dir / "ds_1.extra_files_manifest.json"

    def _fake_encrypt_plaintext_to_compute_key(*, plaintext_path, compute_encrypted_path, compute_public_key):
        del compute_public_key
        payload = Path(plaintext_path).read_bytes()
        Path(compute_encrypted_path).write_bytes(b"crypt4gh" + payload)

    def _fake_rewrite_output_header_to_user_key(
        *,
        compute_encrypted_path,
        final_output_tmp_path,
        reencryption_service_url,
        compute_keypair_id,
    ):
        del reencryption_service_url
        del compute_keypair_id
        Path(final_output_tmp_path).write_bytes(Path(compute_encrypted_path).read_bytes())

    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._encrypt_plaintext_to_compute_key",
        _fake_encrypt_plaintext_to_compute_key,
    )
    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._rewrite_output_header_to_user_key",
        _fake_rewrite_output_header_to_user_key,
    )

    finalize_about_to_persist_crypt4gh_payload(
        output_path=str(output_path),
        dataset_output_path=str(tmp_path / "objects" / "dataset_1.dat"),
        plaintext_path=str(tmp_path / "_crypt" / "outputs" / "ds_1" / "plaintext"),
        encrypted_ext="tabular.c4gh",
        reencryption_service_url="http://example.invalid",
        compute_public_key="unused",
        compute_keypair_id="unused",
        encrypted_marker_path=str(marker_dir / "ds_1.encrypted"),
        designation="sample1",
        discovered_marker_map_path=str(map_path),
        extra_files_output_path=str(extra_files_root),
        extra_files_manifest_path=str(manifest_path),
        allowed_root_paths=[str(tmp_path.resolve())],
    )

    with output_path.open("rb") as output_stream:
        assert output_stream.read(8) == b"crypt4gh"
    with (extra_files_root / "foo.txt").open("rb") as extra_stream:
        assert extra_stream.read(8) == b"crypt4gh"
    with (extra_files_root / "nested" / "bar.txt").open("rb") as extra_stream:
        assert extra_stream.read(8) == b"crypt4gh"

    manifest_payload = json.loads(manifest_path.read_text())
    assert sorted(manifest_payload["files"].keys()) == ["foo.txt", "nested/bar.txt"]
    assert map_path.exists()
    assert map_path.read_text() == '{"sample1": "tabular.c4gh"}'


def test_finalize_about_to_persist_payload_fail_closed_when_extra_files_manifest_missing_entries(tmp_path, monkeypatch):
    output_path = tmp_path / "discover" / "sample1.tsv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("sample\n")

    dataset_output_path = tmp_path / "objects" / "dataset_1.dat"
    dataset_output_path.parent.mkdir(parents=True, exist_ok=True)
    dataset_output_path.write_text("sample\n")

    extra_files_root = tmp_path / "discover" / "sample1_files"
    extra_files_root.mkdir(parents=True, exist_ok=True)
    (extra_files_root / "foo.txt").write_text("foo\n")

    marker_dir = tmp_path / "_c4gh_stage" / "outputs"
    marker_path = marker_dir / "ds_1.encrypted"
    map_path = marker_dir / "discovered_designations.json"
    manifest_path = marker_dir / "ds_1.extra_files_manifest.json"

    def _fake_encrypt_plaintext_to_compute_key(*, plaintext_path, compute_encrypted_path, compute_public_key):
        del compute_public_key
        payload = Path(plaintext_path).read_bytes()
        Path(compute_encrypted_path).write_bytes(b"crypt4gh" + payload)

    def _fake_rewrite_output_header_to_user_key(
        *,
        compute_encrypted_path,
        final_output_tmp_path,
        reencryption_service_url,
        compute_keypair_id,
    ):
        del reencryption_service_url
        del compute_keypair_id
        Path(final_output_tmp_path).write_bytes(Path(compute_encrypted_path).read_bytes())

    def _drop_manifest_entry(*, manifest_path, relative_path, encrypted_ext):
        del relative_path
        del encrypted_ext
        Path(manifest_path).parent.mkdir(parents=True, exist_ok=True)
        Path(manifest_path).write_text(json.dumps({"files": {}}))

    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._encrypt_plaintext_to_compute_key",
        _fake_encrypt_plaintext_to_compute_key,
    )
    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._rewrite_output_header_to_user_key",
        _fake_rewrite_output_header_to_user_key,
    )
    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._write_extra_files_manifest_entry",
        _drop_manifest_entry,
    )

    with pytest.raises(Crypt4GHRemoteExecutionError, match="extra_files manifest missing entries"):
        finalize_about_to_persist_crypt4gh_payload(
            output_path=str(output_path),
            dataset_output_path=str(dataset_output_path),
            plaintext_path=str(tmp_path / "_crypt" / "outputs" / "ds_1" / "plaintext"),
            encrypted_ext="tabular.c4gh",
            reencryption_service_url="http://example.invalid",
            compute_public_key="unused",
            compute_keypair_id="unused",
            encrypted_marker_path=str(marker_path),
            designation="sample1",
            discovered_marker_map_path=str(map_path),
            extra_files_output_path=str(extra_files_root),
            extra_files_manifest_path=str(manifest_path),
            allowed_root_paths=[str(tmp_path.resolve())],
        )

    assert not output_path.exists()
    assert not dataset_output_path.exists()
    assert not marker_path.exists()
    assert not manifest_path.exists()
    assert not extra_files_root.exists()


def test_finalize_about_to_persist_payload_rejects_paths_outside_allowed_roots(tmp_path, monkeypatch):
    output_path = tmp_path / "discover" / "sample1.tsv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("sample\n")

    unsafe_dataset_output_path = tmp_path / "outside" / "dataset_1.dat"
    unsafe_dataset_output_path.parent.mkdir(parents=True, exist_ok=True)
    unsafe_dataset_output_path.write_text("sample\n")

    marker_dir = tmp_path / "_c4gh_stage" / "outputs"
    map_path = marker_dir / "discovered_designations.json"
    manifest_path = marker_dir / "ds_1.extra_files_manifest.json"

    def _encrypt_should_not_run(*, plaintext_path, compute_encrypted_path, compute_public_key):
        del plaintext_path
        del compute_encrypted_path
        del compute_public_key
        raise AssertionError("encryption should not run for paths outside allowed roots")

    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._encrypt_plaintext_to_compute_key",
        _encrypt_should_not_run,
    )

    with pytest.raises(Crypt4GHRemoteExecutionError, match="outside allowed roots"):
        finalize_about_to_persist_crypt4gh_payload(
            output_path=str(output_path),
            dataset_output_path=str(unsafe_dataset_output_path),
            plaintext_path=str(tmp_path / "_crypt" / "outputs" / "ds_1" / "plaintext"),
            encrypted_ext="tabular.c4gh",
            reencryption_service_url="http://example.invalid",
            compute_public_key="unused",
            compute_keypair_id="unused",
            encrypted_marker_path=str(marker_dir / "ds_1.encrypted"),
            designation="sample1",
            discovered_marker_map_path=str(map_path),
            extra_files_manifest_path=str(manifest_path),
            allowed_root_paths=[str((tmp_path / "discover").resolve())],
        )


def test_finalize_about_to_persist_payload_requires_explicit_allowed_root_provenance(tmp_path, monkeypatch):
    output_path = tmp_path / "discover" / "sample1.tsv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("sample\n")

    def _fake_encrypt_plaintext_to_compute_key(*, plaintext_path, compute_encrypted_path, compute_public_key):
        del compute_public_key
        payload = Path(plaintext_path).read_bytes()
        Path(compute_encrypted_path).write_bytes(payload)

    def _fake_rewrite_output_header_to_user_key(
        *,
        compute_encrypted_path,
        final_output_tmp_path,
        reencryption_service_url,
        compute_keypair_id,
    ):
        del reencryption_service_url
        del compute_keypair_id
        Path(final_output_tmp_path).write_bytes(Path(compute_encrypted_path).read_bytes())

    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._encrypt_plaintext_to_compute_key",
        _fake_encrypt_plaintext_to_compute_key,
    )
    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution._rewrite_output_header_to_user_key",
        _fake_rewrite_output_header_to_user_key,
    )

    with pytest.raises(Crypt4GHRemoteExecutionError, match="requires explicit allowed_root_paths provenance"):
        finalize_about_to_persist_crypt4gh_payload(
            output_path=str(output_path),
            plaintext_path=str(tmp_path / "_crypt" / "outputs" / "ds_1" / "plaintext"),
            encrypted_ext="tabular.c4gh",
            reencryption_service_url="http://example.invalid",
            compute_public_key="unused",
            compute_keypair_id="unused",
        )
