import base64
import io

import crypt4gh.header

from galaxy.datatypes import sniff
from galaxy.datatypes.registry import example_datatype_registry_for_sample
from .util import (
    get_input_files,
    MockDataset,
    MockDatasetDataset,
)


def test_matches_any():
    datatypes_registry = example_datatype_registry_for_sample()
    data_datatype = datatypes_registry.get_datatype_by_extension("data")
    fasta_datatype = datatypes_registry.get_datatype_by_extension("fasta")
    fastq_datatype = datatypes_registry.get_datatype_by_extension("fastq")
    fastqsanger_datatype = datatypes_registry.get_datatype_by_extension("fastqsanger")
    fastqgz_datatype = datatypes_registry.get_datatype_by_extension("fastq.gz")
    fastqbz2_datatype = datatypes_registry.get_datatype_by_extension("fastq.bz2")
    fastqsangergz_datatype = datatypes_registry.get_datatype_by_extension("fastqsanger.gz")
    fastqsangerbz2_datatype = datatypes_registry.get_datatype_by_extension("fastqsanger.bz2")
    h5_datatype = datatypes_registry.get_datatype_by_extension("h5")
    mz5_datatype = datatypes_registry.get_datatype_by_extension("mz5")

    # Test empty list behavior.
    assert not fasta_datatype.matches_any([])

    # Test direct match with self.
    assert fastq_datatype.matches_any([fastq_datatype])
    assert fastq_datatype.matches_any([fastq_datatype.__class__])

    # Test plain mismatches.
    assert not fasta_datatype.matches_any([fastq_datatype])
    assert not fasta_datatype.matches_any([fastq_datatype.__class__])
    assert not fastq_datatype.matches_any([fasta_datatype])
    assert not fastq_datatype.matches_any([fasta_datatype.__class__])

    # Test simple match behavior of data.
    assert fasta_datatype.matches_any([data_datatype])
    assert not data_datatype.matches_any([fasta_datatype])

    # Test inheritance.
    assert fastqsanger_datatype.matches_any([fastq_datatype])
    assert fastqsanger_datatype.matches_any([fastq_datatype.__class__])
    assert not fastq_datatype.matches_any([fastqsanger_datatype])
    assert not fastq_datatype.matches_any([fastqsanger_datatype.__class__])

    # Test dynamic subclass handling.
    assert not h5_datatype.matches_any([mz5_datatype])
    assert mz5_datatype.matches_any([h5_datatype])

    # Test compressed datatype handling - matching direct.
    assert fastqsangergz_datatype.matches_any([fastqsangergz_datatype])
    assert fastqsangergz_datatype.matches_any([fastqsangergz_datatype.__class__])
    assert fastqsangerbz2_datatype.matches_any([fastqsangerbz2_datatype])
    assert fastqsangerbz2_datatype.matches_any([fastqsangerbz2_datatype.__class__])

    # Test compressed datatype handling - matching in compressed hierarchy.
    assert fastqsangergz_datatype.matches_any([fastqgz_datatype])
    assert fastqsangergz_datatype.matches_any([fastqgz_datatype.__class__])
    assert fastqsangerbz2_datatype.matches_any([fastqbz2_datatype])
    assert fastqsangerbz2_datatype.matches_any([fastqbz2_datatype.__class__])

    # Test compressed datatype handling - matching as raw compressed data.
    assert fastqsangergz_datatype.matches_any([data_datatype])
    assert fastqsangergz_datatype.matches_any([data_datatype.__class__])
    assert fastqsangerbz2_datatype.matches_any([data_datatype])
    assert fastqsangerbz2_datatype.matches_any([data_datatype.__class__])

    # Test compressed datatype handling - everything else mismatches.
    assert not data_datatype.matches_any([fastqsangergz_datatype])
    assert not fastq_datatype.matches_any([fastqsangergz_datatype])
    assert not fastqsanger_datatype.matches_any([fastqsangergz_datatype])
    assert not fastqgz_datatype.matches_any([fastqsangergz_datatype])
    assert not fastqgz_datatype.matches_any([fastqsangergz_datatype.__class__])
    assert not fastqsangergz_datatype.matches_any([fastqsanger_datatype])
    assert not fastqsangerbz2_datatype.matches_any([fastqgz_datatype])
    assert not fastqsangerbz2_datatype.matches_any([fastqgz_datatype.__class__])
    assert not fastqsangerbz2_datatype.matches_any([fastqsangergz_datatype])
    assert not fastqsangerbz2_datatype.matches_any([fastqsangergz_datatype.__class__])

    # Test matches after first arg.
    assert fasta_datatype.matches_any([fastq_datatype, data_datatype])
    assert fasta_datatype.matches_any([fastq_datatype, h5_datatype, data_datatype])
    assert fastqsangerbz2_datatype.matches_any([fastqsangergz_datatype.__class__, fastqsangerbz2_datatype])
    assert fastqsangerbz2_datatype.matches_any([fastqsangergz_datatype.__class__, fastqsangerbz2_datatype.__class__])

    # Test mismatches in multiple args.
    assert not fasta_datatype.matches_any([fastq_datatype, h5_datatype])
    assert not fasta_datatype.matches_any([fastq_datatype.__class__, h5_datatype.__class__])


def test_sniff_compressed_dynamic_datatypes_default_on():
    # With auto sniffing on, verify the sniffers work and the files match what is expected
    # when coming from guess_ext.
    datatypes_registry = example_datatype_registry_for_sample(sniff_compressed_dynamic_datatypes_default=True)

    fastqsangergz_datatype = datatypes_registry.get_datatype_by_extension("fastqsanger.gz")
    fname = sniff.get_test_fname("1.fastqsanger.gz")
    assert fastqsangergz_datatype.sniff(fname)

    sniff_order = datatypes_registry.sniff_order
    fname = sniff.get_test_fname("1.fastqsanger.gz")
    assert sniff.guess_ext(fname, sniff_order) == "fastqsanger.gz"
    fname = sniff.get_test_fname("1.fastqsanger.bz2")
    assert sniff.guess_ext(fname, sniff_order) == "fastqsanger.bz2"


def test_sniff_compressed_dynamic_datatypes_default_off():
    # Redo last tests with auto compressed sniffing disabled and they should not longer result from guess_ext.
    datatypes_registry = example_datatype_registry_for_sample(sniff_compressed_dynamic_datatypes_default=False)

    # sniffer still returns True for these files...
    fastqsangergz_datatype = datatypes_registry.get_datatype_by_extension("fastqsanger.gz")
    fname = sniff.get_test_fname("1.fastqsanger.gz")
    assert fastqsangergz_datatype.sniff(fname)

    # but they don't report as matching the specified sniff_order.
    sniff_order = datatypes_registry.sniff_order
    fname = sniff.get_test_fname("1.fastqsanger.gz")
    assert "fastq" not in sniff.guess_ext(fname, sniff_order)
    fname = sniff.get_test_fname("1.fastqsanger.bz2")
    assert "fastq" not in sniff.guess_ext(fname, sniff_order)


def test_crypt4gh_dynamic_datatypes_registration_and_converters():
    datatypes_registry = example_datatype_registry_for_sample()
    assert "fastqsanger.crypt4gh" in datatypes_registry.datatypes_by_extension
    assert "fastqsanger.gz.crypt4gh" in datatypes_registry.datatypes_by_extension
    assert "fastqsanger.bz2.crypt4gh" in datatypes_registry.datatypes_by_extension

    fastq_gz_crypt4gh = datatypes_registry.get_datatype_by_extension("fastqsanger.gz.crypt4gh")
    assert fastq_gz_crypt4gh.uncompressed_datatype_instance.file_ext == "fastqsanger.gz"

    converters_with_crypt4gh = [converter for converter in datatypes_registry.converters if "crypt4gh" in converter[0]]
    assert converters_with_crypt4gh == []


def test_crypt4gh_matches_any_staging_gate():
    default_registry = example_datatype_registry_for_sample(enable_crypt4gh_transparent_staging=False)
    fastqsanger = default_registry.get_datatype_by_extension("fastqsanger")
    fastqsanger_crypt4gh = default_registry.get_datatype_by_extension("fastqsanger.crypt4gh")
    assert not fastqsanger_crypt4gh.matches_any([fastqsanger])

    staging_registry = example_datatype_registry_for_sample(enable_crypt4gh_transparent_staging=True)
    fastqsanger_staging = staging_registry.get_datatype_by_extension("fastqsanger")
    fastqsanger_crypt4gh_staging = staging_registry.get_datatype_by_extension("fastqsanger.crypt4gh")
    assert fastqsanger_crypt4gh_staging.matches_any([fastqsanger_staging])


def test_crypt4gh_set_meta_stores_header_only():
    datatypes_registry = example_datatype_registry_for_sample()
    crypt4gh_datatype = datatypes_registry.get_datatype_by_extension("fastqsanger.crypt4gh")
    with get_input_files("1.fastqsanger.crypt4gh") as input_files:
        dataset = MockDataset(1)
        dataset.set_file_name(input_files[0])
        dataset.dataset = MockDatasetDataset(dataset.get_file_name())
        crypt4gh_datatype.set_meta(dataset)

    encoded_header = dataset.metadata.crypt4gh_header
    header_bytes = base64.b64decode(encoded_header, validate=True)
    assert header_bytes.startswith(b"crypt4gh")
    # Verify the stored header is exactly the crypt4gh header (magic + version +
    # packet_count + packet data) — the body (encrypted data) must not be included.
    hstream = io.BytesIO(header_bytes)
    packets = list(crypt4gh.header.parse(hstream))
    assert len(packets) >= 1, "Header must contain at least one packet"
    # After parsing all packets, the stream should be at EOF — no body bytes mixed in
    assert hstream.read() == b"", "crypt4gh_header metadata must not include body bytes"
    assert not hasattr(dataset.metadata, "sequences")
    assert not hasattr(dataset.metadata, "data_lines")
