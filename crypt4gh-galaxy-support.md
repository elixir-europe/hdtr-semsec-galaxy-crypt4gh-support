# Plan: Crypt4GH Support in Galaxy

## Background

In human genetics, Crypt4GH is the accepted standard for file encryption ([spec](https://samtools.github.io/hts-specs/crypt4gh.pdf)).

The basic idea is that sensitive data is:

- encrypted with a symmetric key that is
- stored in a header attached to the encrypted result, but encrypted using a public/private key pair so
- only the recipient can get at the symmetric key in the header with their private key

The advantage for potentially huge genomics files is that re-encryption is simply a matter of re-encrypting the header, not the entire file.

## Goal

When a user uploads Crypt4GH-encrypted data of any relevant type (e.g. fastq, bam, vcf), Galaxy should:

- **Sniff** the crypt4gh encryption format
- **Guess** the format of the decrypted data from the file extension (or record the user-declared format, like for deferred data)
- **Make no attempt** to do anything with the contained data because it will be unreadable

This is analogous to how Galaxy handles compressed datatypes with no auto-decompression. For example: user uploads file, says it's `fastqsanger`; Galaxy finds it's actually crypt4gh; records the type as `fastqsanger.crypt4gh`.

Then, at the tool-level, if a tool accepts `fastqsanger` and `fastqsanger.gz` as input, it should automatically and transparently accept `fastqsanger.crypt4gh` and `fastqsanger.gz.crypt4gh` as well — without rewriting tool wrappers.

## Re-encryptor Service Architecture

Phase 2 relies on an external **re-encryptor service** (developed and operated by Norwegian partners in the ELIXIR project). This is a vault-like server where:

- Users deposit their Crypt4GH key pair
- Compute providers deposit their Crypt4GH key pair
- Galaxy sends a dataset's crypt4gh header (stored as dataset metadata) to the service
- The service decrypts the header with the user's private key, re-encrypts it with the compute node's public key, and returns the new header
- Galaxy writes `new_header + original_encrypted_body` to a staged path for the job

**Galaxy never holds any user or compute private key.**

---

## Phase 1 — Sniffing, Registration & Header Metadata

### 1. `is_crypt4gh` / `check_crypt4gh` in `lib/galaxy/util/checkers.py`

Add `check_crypt4gh(file_path, check_content=False)` that opens the file raw and checks that the first 8 bytes equal `b"crypt4gh"` (optionally also verify the 4-byte LE version field at bytes 8–11 equals 1). Export `is_crypt4gh` and add `"crypt4gh": check_crypt4gh` to `COMPRESSION_CHECK_FUNCTIONS`.

Unlike gzip/bz2, this function **never** attempts to open the decompressed stream (there is no key), so `check_content` is always effectively `False` — always return `(True, True)` when the magic bytes match.

**Also update `check_binary`** (same file, ~line 82). The current code short-circuits the mid-file binary probe for known compressed formats:

```python
if file_path and not is_gzip(name) and not is_zip(name) and not is_bz2(name):
    temp.seek(read_start)
    return util.is_binary(temp.read(read_length))
```

Add `and not is_crypt4gh(name)` to this guard, otherwise Galaxy will seek into the middle of the random-looking encrypted body and may make incorrect binary/text judgements.

### 2. `get_fileobj_raw` in `lib/galaxy/util/compression_utils.py`

The default `compressed_formats` list is hardcoded as `["bz2", "gzip", "xz", "zip"]` (line ~112). Add `"crypt4gh"` and insert the crypt4gh check **first in the detection chain**, before gzip, so that a crypt4gh file is never mistakenly matched by a later format check:

```python
if "crypt4gh" in compressed_formats and is_crypt4gh(filename):
    compressed_format = "crypt4gh"
    # Return a plain binary handle — no decompression, caller gets the raw encrypted bytes
    return compressed_format, open(filename, "rb")
elif "gzip" in compressed_formats and is_gzip(filename):
    ...
```

**Audit callers that pass an explicit `compressed_formats` list.** Any call site that provides a hard-coded list (e.g. `get_fileobj(path, compressed_formats=["gzip"])`) will silently skip crypt4gh detection. Search the codebase for such call sites and decide whether each one needs updating.

### 3. `Crypt4GHDynamicCompressedArchive` base class in `lib/galaxy/datatypes/binary.py`

Add alongside `GzDynamicCompressedArchive` / `Bz2DynamicCompressedArchive` (line ~403):

```python
class Crypt4GHDynamicCompressedArchive(DynamicCompressedArchive):
    compressed_format = "crypt4gh"
    compressed = True
```

> **Note on `compressed_format` naming:** gz uses `"gz"` as the XML `auto_compressed_types` token but `compressed_format = "gzip"` on the class — an existing inconsistency. Use `"crypt4gh"` for **both** the XML token and the class attribute to avoid repeating this. Ensure `COMPRESSION_CHECK_FUNCTIONS`, `get_fileobj_raw`, and `compressed_format` all use exactly the string `"crypt4gh"`.

**`set_meta` — extract the header, suppress all body metadata:**

`set_meta` must **not** be a complete no-op. The crypt4gh header is fully readable without any private key — it encodes encrypted session key packets and its structure is public per the spec. The header must be extracted and stored as a `MetadataElement` so that:

- The Phase 2 re-encryptor service call has something to send
- The Galaxy UI can expose the header to the client-side "re-encrypt" flow (as demonstrated in the ELIXIR branch)

Parse the header length from bytes 12–15 of the file (4-byte LE uint32), read that many bytes, and store the result (base64-encoded) as a `MetadataElement`. Everything beyond the header is the encrypted body — do **not** scan, index, or validate it.

```python
MetadataElement(
    name="crypt4gh_header",
    desc="Base64-encoded crypt4gh header (for re-encryption)",
    readonly=True,
    no_value=None,
)

def set_meta(self, dataset, overwrite=True, **kwd):
    with open(dataset.get_file_name(), "rb") as f:
        magic = f.read(8)
        if magic != b"crypt4gh":
            return
        f.read(4)  # version
        header_length = struct.unpack_from("<I", f.read(4))[0]
        f.seek(0)
        header_bytes = f.read(header_length)
    dataset.metadata.crypt4gh_header = base64.b64encode(header_bytes).decode("ascii")
```

**Other overrides:**

- `set_peek` → display `"Crypt4GH encrypted <inner_ext> file"` + file size
- `display_peek` → same string
- `sniff_prefix` → return `True` when `file_prefix.compressed_format == "crypt4gh"` (inner content cannot be sniffed; correctness is carried by file extension)

### 4. Registry `auto_compressed_types` support for `crypt4gh` in `lib/galaxy/datatypes/registry.py`

In the `auto_compressed_types` loop (lines ~364–422), add a branch for crypt4gh:

```python
elif auto_compressed_type == "crypt4gh":
    dynamic_parent = binary.Crypt4GHDynamicCompressedArchive
```

**Skip both converter directions.** The current code unconditionally registers `{type}_to_uncompressed.xml` for bz2 (line ~417) and also registers `uncompressed_to_gz.xml` for gz (line ~408). For crypt4gh, skip **both** appends to `self.converters` — decryption requires a private key Galaxy never holds. Be explicit: the `if auto_compressed_type == "gz":` / `elif auto_compressed_type == "bz2":` / `else: raise ConfigurationError` chain that currently raises on unknown types must be extended with the crypt4gh branch before it reaches the `raise`.

Register the type in `compressed_sniffers` and `datatypes_by_extension` as normal (no special handling needed there).

### 5. `handle_compressed_file` in `lib/galaxy/datatypes/sniff.py`

**No special-casing is needed here.** The existing flow already handles this correctly once the two preconditions are met:

1. `check_crypt4gh` is in `COMPRESSION_CHECK_FUNCTIONS` and returns `(True, True)`
2. `Crypt4GHDynamicCompressedArchive` has `compressed = True`

The existing sniffer loop (`filter(lambda d: getattr(d, "compressed", False), datatypes_registry.sniff_order)`) will find the crypt4gh datatype, set `keep_compressed = True`, and return without attempting decompression — identical to the existing BAM and gz handling paths. No additional changes to `handle_compressed_file` are required.

> **ELIXIR branch note:** The ELIXIR proof-of-concept branch includes a commit titled "Galaxy bugfix for binary datatypes with only `sniff_prefix()`, not `sniff()`". Check whether this bug still exists in the current `dev` branch. If it does, the fix must be ported before `Crypt4GHDynamicCompressedArchive.sniff_prefix` will be called reliably.

### 6. `datatypes_conf.xml.sample` at `lib/galaxy/config/sample/datatypes_conf.xml.sample`

Add `crypt4gh` to `auto_compressed_types` for:

- `fastq`, `fastqsanger`, `fastqillumina`, `fastqsolexa`, `fastqcssanger`
- `fasta`
- `vcf` (plain VCF — `vcf.crypt4gh`; `vcf.gz.crypt4gh` is addressed below)
- `cram`

Example: `<datatype extension="fastqsanger" auto_compressed_types="gz,bz2,crypt4gh" ...>`

### 7. Double-wrapped types: `fastqsanger.gz.crypt4gh`

The dynamically-generated `fastqsanger.gz` datatype is not a static `<datatype>` entry in the XML, so it does not receive `auto_compressed_types` processing in the main loop. A second registry pass is required.

**Implementation:** After the main datatype loop, iterate over all `DynamicCompressedArchive` instances (gz/bz2) that were generated from base types which had `crypt4gh` in their `auto_compressed_types` list. For each such instance (e.g. `fastqsanger.gz`), generate a `{ext}.crypt4gh` variant using `Crypt4GHDynamicCompressedArchive` as the outer compression wrapper.

**Critical — `uncompressed_datatype_instance`:** For `fastqsanger.gz.crypt4gh`, the `uncompressed_datatype_instance` attribute must point to the **`fastqsanger.gz`** instance (the gz-compressed type), not the plain `fastqsanger` instance. This ensures `matches_any` correctly checks gz compatibility when matching against gz-accepting tool inputs.

**Class hierarchy (MRO) ordering:** The generated class MRO must place the base content datatype first so that content-specific methods (e.g. FastqSanger's) take precedence:

```python
type(
    "FastqSangerGzCrypt4gh",
    (FastqSanger, GzDynamicCompressedArchive, Crypt4GHDynamicCompressedArchive),
    {
        "file_ext": "fastqsanger.gz.crypt4gh",
        "compressed_format": "crypt4gh",
        "uncompressed_datatype_instance": fastqsanger_gz_instance,
    },
)
```

`sniff_prefix` must return `True` purely on `file_prefix.compressed_format == "crypt4gh"` — no attempt to peek at the gz stream inside.

**Suffix inference:** The registry code at line ~394 automatically appends `.{auto_compressed_type}` to existing `infer_from` suffixes during the main loop. The second pass must do the same: a `.fastq.gz` suffix entry should become a `.fastq.gz.crypt4gh` entry pointing to `fastqsanger.gz.crypt4gh`. Ensure each base datatype in the XML already has the appropriate `<infer_from suffix="..."/>` child elements.

### 8. `DynamicCompressedArchive.matches_any` gate in `lib/galaxy/datatypes/binary.py`

The current `matches_any` falls through to `self.uncompressed_datatype_instance.matches_any(uncompressed_target_datatypes)`. For `fastqsanger.crypt4gh` this would check `fastqsanger.matches_any([fastqsanger])` → `True`, meaning crypt4gh datasets would silently pass to plaintext-expecting tools before any staging infrastructure exists.

Add a class-level attribute `requires_staging = True` to `Crypt4GHDynamicCompressedArchive`. Modify `matches_any` (in `DynamicCompressedArchive`) so that when `self.compressed_format == "crypt4gh"` and the Galaxy app config flag `enable_crypt4gh_transparent_staging` is **not** set, it returns `False` for any uncompressed target type.

**Also gate at the tool form level.** The `matches_any` check used for job execution is the same one used to populate tool form input drop-downs. Gating it makes crypt4gh datasets visibly unavailable in tool inputs when staging is disabled — users get a clear, early signal rather than a silent failure at job submission time. If any separate compatibility check is used for the tool form, gate that path as well.

### 9. Extension-based type inference

`guess_ext_from_file_name` in `lib/galaxy/datatypes/sniff.py` uses `datatypes_by_suffix_inferences` populated during registry load. The registry code at line ~394 automatically populates `.{suffix}.{auto_compressed_type}` entries for each `infer_from` suffix during the main loop. The second pass for double-wrapped types (Step 7) must do likewise.

Verify end-to-end that:

- `sample.fastq.gz.crypt4gh` → `fastqsanger.gz.crypt4gh`
- `sample.fastqsanger.crypt4gh` → `fastqsanger.crypt4gh`

Add explicit test assertions for both cases (see Step 11).

### 10. Test data

Generate minimal valid crypt4gh test files using the Python `crypt4gh` library and add to `test-data/`:

- A ~1 KB crypt4gh-encrypted fastqsanger snippet
- The corresponding test key files (public + private) for use in Phase 2 tests

> **Key type note:** The Crypt4GH spec and the `crypt4gh` PyPI package (EGA) use **Curve25519** (X25519) for the key encapsulation mechanism, not Ed25519. Ed25519 is a signing algorithm; X25519 is the ECDH key agreement used in crypt4gh. Generate test key pairs with `crypt4gh-keygen` or the Python library's key generation function to ensure correct key types.

### 11. Tests

- `test/unit/data/datatypes/test_sniff.py`:
  - `is_crypt4gh()` returns `True` for the test file and `False` for a plain fastq
  - `guess_ext` returns `fastqsanger.crypt4gh` for `.fastq.crypt4gh` extension
  - `guess_ext` returns `fastqsanger.gz.crypt4gh` for `.fastq.gz.crypt4gh` extension
- `test/unit/data/datatypes/test_datatypes_registry.py`:
  - `fastqsanger.crypt4gh` is registered in `datatypes_by_extension`
  - `fastqsanger.gz.crypt4gh` is registered (double-wrapped second pass)
  - `matches_any([fastqsanger])` returns `False` with staging disabled, `True` with staging enabled
  - `set_meta` populates `metadata.crypt4gh_header` (valid base64); body metadata is absent
  - No converters registered for `crypt4gh` in either direction
- `lib/galaxy_test/api/test_tools_upload.py`:
  - Upload a `.fastqsanger.crypt4gh` file; verify `file_ext == "fastqsanger.crypt4gh"`
  - Verify `metadata.crypt4gh_header` is populated and is valid base64
  - Verify no body metadata scan is triggered

---

## Phase 2 — Transparent Job-Level Decryption via crypt4ghfs

Phase 2 relies on the external re-encryptor service described above. Galaxy itself never holds any private key.

### 12. Re-encryptor service configuration

Add to `galaxy.yml`:

```yaml
# URL of the crypt4gh re-encryptor service
crypt4gh_reencryption_service_url: "https://reencryptor.example.org"
```

The re-encryptor service API accepts a crypt4gh header and a target public key, and returns a new header re-encrypted for that recipient. Authentication between Galaxy and the service is out of scope for this plan (handled by the Norwegian partners).

### 13. Per-job header re-encryption utility: `lib/galaxy/jobs/crypt4gh_staging.py`

New module providing:

```python
def prepare_crypt4gh_input(
    dataset,
    destination_public_key: bytes,
    reencryption_service_url: str,
) -> str:
    """
    Re-encrypt the crypt4gh header for the compute destination via the
    re-encryptor service, then write new_header + original_body to a
    temporary staged path. Returns the staged file path.
    """
```

The function:

1. Retrieves `dataset.metadata.crypt4gh_header` (base64 → bytes)
2. Determines the original header length (`struct.unpack_from("<I", header_bytes, 12)[0]`)
3. POSTs `{header: <base64_header>, recipient_pubkey: <base64_pubkey>}` to the re-encryptor service and receives the new re-encrypted header
4. Opens the original dataset file, seeks past `original_header_length` bytes, and streams `new_header + remaining_bytes` to a temporary staging file
5. Returns the staged file path

The encrypted body is **never touched** — only the header bytes are replaced. This is O(header size) network traffic regardless of file size.

If `crypt4gh_reencryption_service_url` is not configured or the destination has no `crypt4gh_public_key_path`, raise a clear `JobPreparationException` rather than silently passing the encrypted file to the tool.

### 14. Job destination public key config

Add `crypt4gh_public_key_path` as a per-destination parameter in `job_conf.yml`, giving each compute backend a unique key pair. The private key lives **only** on those compute nodes, never in Galaxy.

### 15. `job_wrapper.prepare()` hook

In the relevant base `prepare_job` path (e.g. `lib/galaxy/jobs/runners/__init__.py`): after normal prepare, iterate over input datasets. For any dataset where `getattr(dataset.datatype, "compressed_format", None) == "crypt4gh"`, call `prepare_crypt4gh_input()` and record the staged paths + mount directory paths for the job script.

### 16. Job script pre-job mount commands

In the `get_job_file` / shell script template, inject a pre-job block (before the tool command):

```bash
# For each crypt4gh input:
mkdir -p $CRYPT4GH_MOUNT_DIR
crypt4ghfs --conf <key_conf> <staged_crypt4gh_file> $CRYPT4GH_MOUNT_DIR &
CRYPT4GHFS_PID=$!
```

Replace the input dataset path variable with `$CRYPT4GH_MOUNT_DIR/<inner_filename>`.

Add a post-job block that unmounts:

```bash
fusermount -u $CRYPT4GH_MOUNT_DIR
kill $CRYPT4GHFS_PID
```

### 17. Galaxy config flag activation

Set `enable_crypt4gh_transparent_staging: true` in `galaxy.yml` when Phase 2 is configured. This enables the `matches_any` fallthrough from Step 8, allowing tools accepting `fastqsanger` to also accept `fastqsanger.crypt4gh` inputs.

### 18. Pulsar support

For remote Pulsar nodes, transfer the re-encrypted file (Step 13 output) to the remote node along with the crypt4ghfs mount key config via the Pulsar input staging mechanism. The compute-node private key must already be present on the remote node (out-of-band setup). The injected mount commands in the job script work identically on the remote side.

---

## Phase 3 — Job Output Re-encryption

### 19. User crypt4gh public key storage

Add a `crypt4gh_public_key` field to the Galaxy user model (or a separate `UserCrypt4GHKey` table). Provide a UI in user preferences to upload/paste their Crypt4GH public key.

### 20. Output dataset crypt4gh policy

Introduce a per-history or per-dataset flag `encrypt_outputs_with_crypt4gh` (boolean). If set and the user has a stored public key, output datasets from jobs will be encrypted.

### 21. Post-job output re-encryption hook

In the job finish path (`job_wrapper.finish()`): for each output dataset belonging to a user with `encrypt_outputs_with_crypt4gh=True`, call a new `encrypt_output_as_crypt4gh(output_path, user_public_key)` function. This uses the Python `crypt4gh` library to encrypt the plain output file in place. The output dataset's `file_ext` is updated to `{original_ext}.crypt4gh`.

**`set_meta` must run after encryption.** After the output file is encrypted, `set_meta` on the new `.crypt4gh` dataset must extract and store the header (as in Step 3). Without this, any future use of the output as a Phase 2 job input will fail because `metadata.crypt4gh_header` will be absent.

### 22. Crypt4GH re-encryption efficiency

For outputs that the tool itself wrote as crypt4gh (via a crypt4ghfs write mount — contingent on crypt4ghfs supporting writes upstream), only re-encrypt the header with the user's public key (sub-second for any file size). For plain outputs, full encryption is needed (proportional to file size). Galaxy should time-box this operation and potentially perform it asynchronously.

### 23. Write-capable crypt4ghfs (future dependency)

Monitor upstream [EGA-archive/crypt4ghfs](https://github.com/EGA-archive/crypt4ghfs) for write support. When available, the job script can mount an output directory as a crypt4ghfs write mount with a shared key, after which Galaxy only needs to re-encrypt the header for the user's key (fast path from Step 22).

---

## Key Design Decisions

| Decision                                      | Rationale                                                                                                                                                                                |
| --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **No auto converters for crypt4gh**           | Decryption requires a secret the Galaxy server never holds; both converter directions are suppressed                                                                                     |
| **Extension-only inner-type sniffing**        | Unlike gz (which decompresses the prefix to verify the inner format), there is no key available; inner type is inferred from filename extension only                                     |
| **`matches_any` gating behind config flag**   | Tools don't silently receive encrypted inputs before the staging infrastructure is ready; enabled per deployment                                                                         |
| **Header stored as metadata, not body**       | The crypt4gh header is key-agnostic (no private key needed to read it); storing it as `MetadataElement` enables the re-encryptor service flow without re-opening the file at job time    |
| **Re-encryption is header-only**              | The crypt4gh design allows header-only re-encryption (fast path for any file size), leveraged for both compute-node staging (Phase 2) and user output encryption (Phase 3)               |
| **Re-encryptor service, not Galaxy-held key** | Galaxy never holds any user or compute private key; the external re-encryptor service (ELIXIR/Norwegian partners) handles key management                                                 |
| **Detection priority**                        | The crypt4gh checker must run **before** gzip/bz2/zip in `get_fileobj_raw`, since a crypt4gh file is raw binary that would otherwise fall through to another format or `binary`          |
| **`"crypt4gh"` token used consistently**      | Both the XML `auto_compressed_types` token and the class `compressed_format` attribute use exactly `"crypt4gh"`, avoiding the `gz`/`gzip` inconsistency that already exists for gz types |

---

## Verification

- **Phase 1 unit tests**: `pytest test/unit/data/datatypes/` — sniffer, registry, `matches_any` gating, `set_meta` header extraction, no converters registered
- **Phase 1 API upload test**: upload `.fastqsanger.crypt4gh`, assert `file_ext`, `metadata.crypt4gh_header` populated, no body metadata errors: `pytest lib/galaxy_test/api/test_tools_upload.py`
- **Phase 2 integration test**: submit a job with a crypt4gh input on a local runner with a test key pair and a mock re-encryptor service; verify the tool receives plaintext via crypt4ghfs mount
- **Phase 2 `matches_any`**: `fastqsanger.crypt4gh matches_any([fastqsanger])` = `False` when staging disabled, `True` when enabled
- **Phase 3 set_meta**: after output re-encryption, verify `metadata.crypt4gh_header` is populated on the output dataset and the dataset can be used as a Phase 2 job input
