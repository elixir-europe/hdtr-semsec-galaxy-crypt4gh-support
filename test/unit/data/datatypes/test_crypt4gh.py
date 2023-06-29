from base64 import b64encode
from contextlib import contextmanager
from datetime import datetime
from textwrap import dedent

import pytest
from typing import NamedTuple, Optional, Generator

from galaxy.datatypes import sniff
from galaxy.datatypes.binary import Crypt4ghEncryptedArchive
from galaxy.datatypes.protocols import DatasetProtocol

from .util import (
    get_input_files, MockDataset, MockDatasetDataset, MockMetadata,
)

ORIG_HEADER_SHA256 = "0491065aa0f4245a7a958fdefc487b9b31327ad594e009ea01d5f8b6b1bb3d8a"
RECRYPTED_HEADER_SHA256 = "b15c077a3f07f970a04f57c4204fe756307f73b309c922a230696b793d12eee6"

InputFileInfo = NamedTuple("InputFileInfo", [('dataset', DatasetProtocol),
                                             ('file_prefix', sniff.FilePrefix),
                                             ('contents', Optional[bytes])])

@contextmanager
def _get_input_file_info(file_name: str, dataset_id: int, read_contents: bool) -> Generator[InputFileInfo, None, None]:
    with get_input_files(file_name) as input_files:
        input_file_path = input_files[0]

        dataset = MockDataset(dataset_id)
        dataset.file_name = input_file_path
        dataset.dataset = MockDatasetDataset(dataset.file_name)
        dataset.metadata = MockMetadata()

        file_prefix = sniff.FilePrefix(input_file_path)

        if read_contents:
            with open(input_file_path, "rb") as input_file:
                contents = input_file.read()
        else:
            contents = None

        yield InputFileInfo(dataset=dataset, file_prefix=file_prefix, contents=contents)  # type:ignore


@pytest.fixture
def c4gh_loader() -> Crypt4ghEncryptedArchive:
    return Crypt4ghEncryptedArchive()


@pytest.fixture
def c4gh_data_complete() -> Generator[InputFileInfo, None, None]:
    return _get_input_file_info("data-complete.c4gh", 1, read_contents=False)


@pytest.fixture
def c4gh_data_header() -> Generator[InputFileInfo, None, None]:
    return _get_input_file_info("data-header.c4gh", 2, read_contents=True)


@pytest.fixture
def c4gh_data_payload() -> Generator[InputFileInfo, None, None]:
    return _get_input_file_info("data-payload.c4gh", 3, read_contents=True)


@pytest.fixture
def c4gh_data_header_recrypted() -> Generator[InputFileInfo, None, None]:
    return _get_input_file_info("data-header-recrypted.c4gh", 4, read_contents=True)


def test_crypt4gh_peek(c4gh_loader, c4gh_data_complete):
    with c4gh_data_complete as data_complete:
        dataset = data_complete.dataset
        c4gh_loader.set_peek(dataset)
        assert dataset.peek == "Crypt4GH encrypted dataset"
        assert dataset.blurb == "2.8 KB"


def test_crypt4gh_sniff_prefix(c4gh_loader, c4gh_data_complete, c4gh_data_header, c4gh_data_payload):
    with c4gh_data_complete as data_complete:
        assert c4gh_loader.sniff_prefix(data_complete.file_prefix) is True

    with c4gh_data_header as data_header:
        assert c4gh_loader.sniff_prefix(data_header.file_prefix) is False

    with c4gh_data_payload as data_payload:
        assert c4gh_loader.sniff_prefix(data_payload.file_prefix) is False


def test_crypt4gh_set_meta(c4gh_loader, c4gh_data_complete, c4gh_data_header, c4gh_data_header_recrypted):
    with c4gh_data_complete as data_complete:
        dataset = data_complete.dataset

        with c4gh_data_header as data_header:
            c4gh_loader.set_meta(dataset=dataset)

            assert dataset.metadata.crypt4gh_header == b64encode(data_header.contents)
            assert dataset.metadata.crypt4gh_metadata_header_sha256 == ORIG_HEADER_SHA256
            assert dataset.metadata.crypt4gh_dataset_header_sha256 == ORIG_HEADER_SHA256
            assert dataset.metadata.crypt4gh_compute_keypair_id == ""
            assert data_complete.dataset.metadata.crypt4gh_compute_keypair_expiration_date == ""

        with c4gh_data_header_recrypted as data_header_recrypted:
            c4gh_loader.set_meta(dataset=dataset, crypt4gh_header=data_header_recrypted.contents)

            assert dataset.metadata.crypt4gh_header == b64encode(data_header_recrypted.contents)
            assert dataset.metadata.crypt4gh_metadata_header_sha256 == RECRYPTED_HEADER_SHA256
            assert dataset.metadata.crypt4gh_dataset_header_sha256 == ORIG_HEADER_SHA256
            assert dataset.metadata.crypt4gh_compute_keypair_id == ""
            assert data_complete.dataset.metadata.crypt4gh_compute_keypair_expiration_date == ""

        c4gh_loader.set_meta(dataset=dataset)
        assert dataset.metadata.crypt4gh_metadata_header_sha256 == RECRYPTED_HEADER_SHA256


def test_crypt4gh_compute_keypair_set_meta_and_peek(c4gh_loader, c4gh_data_complete, c4gh_data_header_recrypted):
    with c4gh_data_complete as data_complete:
        dataset = data_complete.dataset

        # Dataset representing job output
        # Retains info about compute keypair used for decryption to allow mapping to
        # job-specific private key on the user side

        date = datetime(2023, 6, 30, 12, 15)
        c4gh_loader.set_meta(dataset=dataset,
                             crypt4gh_compute_keypair_id='cn:b38ac81f',
                             crypt4gh_compute_keypair_expiration_date=date)
        assert dataset.metadata.crypt4gh_compute_keypair_id == 'cn:b38ac81f'
        assert dataset.metadata.crypt4gh_compute_keypair_expiration_date == '2023-06-30T12:15:00'

        c4gh_loader.set_meta(dataset=dataset)
        assert dataset.metadata.crypt4gh_compute_keypair_id == 'cn:b38ac81f'
        assert dataset.metadata.crypt4gh_compute_keypair_expiration_date == '2023-06-30T12:15:00'

        c4gh_loader.set_peek(dataset)
        assert dataset.peek == dedent("""\
            Crypt4GH encrypted dataset
            Encrypted to allow de/recryption by user
            Compute keypair id: cn:b38ac81f
            Compute keypair expires: 2023-06-30T12:15:00""")
        assert dataset.blurb == "2.8 KB"

        # Dataset is recrypted for another analysis job with new compute keypair

        date = datetime(2023, 6, 30, 13, 4)
        with c4gh_data_header_recrypted as data_header_recrypted:
            c4gh_loader.set_meta(dataset=dataset,
                                 crypt4gh_header=data_header_recrypted.contents,
                                 crypt4gh_compute_keypair_id='cn:148fa23c7',
                                 crypt4gh_compute_keypair_expiration_date=date)
            assert dataset.metadata.crypt4gh_compute_keypair_id == 'cn:148fa23c7'
            assert dataset.metadata.crypt4gh_compute_keypair_expiration_date == '2023-06-30T13:04:00'

        c4gh_loader.set_peek(dataset)
        assert dataset.peek == dedent("""\
            Crypt4GH encrypted dataset
            Recrypted to allow decryption at compute node
            Metadata header overrides header in dataset
            Compute keypair id: cn:148fa23c7
            Compute keypair expires: 2023-06-30T13:04:00""")

        assert dataset.blurb == "2.8 KB"



def test_crypt4gh_compute_keypair_fail_if_only_id_or_date(c4gh_loader, c4gh_data_complete):
    with c4gh_data_complete as data_complete:
        dataset = data_complete.dataset

        date = datetime(2023, 6, 30, 12, 15)

        with pytest.raises(ValueError):
            c4gh_loader.set_meta(dataset=dataset, crypt4gh_compute_keypair_id='cn:b38ac81f')

        with pytest.raises(ValueError):
            c4gh_loader.set_meta(dataset=dataset, crypt4gh_compute_keypair_expiration_date=date)


def test_crypt4gh_set_meta_not_crypt4gh_data(c4gh_loader, c4gh_data_payload, c4gh_data_header):
    with c4gh_data_payload as data_payload:
        with pytest.raises(ValueError):
            c4gh_loader.set_meta(dataset=data_payload.dataset)
        _assert_c4gh_metadata_not_set(data_payload.dataset)

    with c4gh_data_header as data_header:
        with pytest.raises(ValueError):
            c4gh_loader.set_meta(dataset=data_header.dataset)
        _assert_c4gh_metadata_not_set(data_header.dataset)


def test_crypt4gh_set_meta_with_bad_recrypt_header(c4gh_loader, c4gh_data_complete, c4gh_data_payload):
    with c4gh_data_complete as data_complete:
        c4gh_loader.set_meta(dataset=data_complete.dataset)

        assert data_complete.dataset.metadata.crypt4gh_header
        assert data_complete.dataset.metadata.crypt4gh_metadata_header_sha256
        assert data_complete.dataset.metadata.crypt4gh_dataset_header_sha256
        assert data_complete.dataset.metadata.crypt4gh_compute_keypair_id == ""
        assert data_complete.dataset.metadata.crypt4gh_compute_keypair_expiration_date == ""

        with c4gh_data_payload as data_payload:
            with pytest.raises(ValueError):
                c4gh_loader.set_meta(dataset=data_complete.dataset, crypt4gh_header=data_payload.contents)

            _assert_c4gh_metadata_not_set(data_complete.dataset)


def _assert_c4gh_metadata_not_set(dataset: DatasetProtocol):
    assert dataset.metadata.crypt4gh_header == ""
    assert dataset.metadata.crypt4gh_metadata_header_sha256 == ""
    assert dataset.metadata.crypt4gh_dataset_header_sha256 == ""
    assert dataset.metadata.crypt4gh_compute_keypair_id == ""
    assert dataset.metadata.crypt4gh_compute_keypair_expiration_date == ""
