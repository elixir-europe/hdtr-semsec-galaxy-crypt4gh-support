# Manual UI Testing Guide: Crypt4GH Support

This guide walks through manually testing the Phase 1, Phase 2, and Phase 3 Crypt4GH
changes in a running Galaxy instance.

All commands assume you are in the **Galaxy root directory** with the venv active:

```bash
cd /path/to/galaxy
source .venv/bin/activate
```

---

## Prerequisites

- Galaxy checked out on the `explore-crypt4gh-library-support` branch
- The `crypt4gh` Python package installed in the venv (`pip show crypt4gh`)

---

## Step 1 — Start the mock compute-side recryptor B service

The mock service re-encrypts Crypt4GH headers on-the-fly using the test key pair.
It mirrors the compute-side recryptor B API used by remote execution and only handles
Crypt4GH header material (never encrypted payload bodies).

Open a **dedicated terminal** and leave it running:

```bash
python - << 'EOF'
import sys, time
sys.path.insert(0, 'test/unit/jobs')
from mock_recryptor_service import MockRecryptorServer

srv = MockRecryptorServer(
    user_private_key_path='test-data/crypt4gh/user_key.sec',
    compute_public_key_path='test-data/crypt4gh/compute_key.pub',
)
srv.start()
print(f"\nRe-encryptor running at: {srv.url}\n")
while True:
    time.sleep(60)
EOF
```

Note the URL printed (e.g. `http://127.0.0.1:54321`) — you need it in the next step.

---

## Step 2 — Configure `config/galaxy.yml`

Add (or uncomment) these keys under the `galaxy:` section:

```yaml
galaxy:
    metadata_strategy: "extended"
    enable_crypt4gh_transparent_staging: true
    crypt4gh_reencryption_service_url: "http://127.0.0.1:54321" # port from Step 1
```

`metadata_strategy: "extended"` is required for remote tool evaluation mode.

---

## Step 3 — Start Galaxy

```bash
./run.sh
```

Wait until you see `Starting server in PID ...` and the UI is accessible at
`http://localhost:8080`.

---

## Step 4 — Upload a Crypt4GH-encrypted file (Phase 1)

The file `test-data/crypt4gh/test.fastqsanger.c4gh` is a real Crypt4GH file
containing a short FASTQ snippet, encrypted with the test user key.

1. Open `http://localhost:8080` and log in (or use the default admin account).
2. Click the **Upload** button (top-left of the tool panel).
3. Click **Choose local file** and select:
   ```
   test-data/crypt4gh/test.fastqsanger.c4gh
   ```
4. In the **Type** column leave it as `Auto-detect` — Galaxy should sniff the
   Crypt4GH magic bytes and assign the type automatically.
5. Click **Start**, then **Close**.

### What to verify (Phase 1)

After upload completes, click the dataset name in the history to expand it:

- **Type** should be `fastqsanger.c4gh` (not `binary` or `data`).
- Click the **ⓘ (info)** icon → **Dataset Details**. Under **Metadata** you
  should see a `crypt4gh_header` field containing a long base64-encoded string.
- The peek/content view will show encrypted content (no readable plaintext) —
  this is expected.

---

## Step 5 — Run a tool with the encrypted input (Phase 2)

1. In the tool search box type **FastQC** (or any tool that accepts
   `fastqsanger` input).
2. In the input dataset selector, the `test.fastqsanger.c4gh` dataset
   should appear (because `enable_crypt4gh_transparent_staging: true` enables
   the `matches_any` gate).
3. Select it and click **Run Tool**.

### What to verify (Phase 2 — execution-side `_crypt/` model)

While (or after) the job runs, find the job working directory:

```bash
ls database/jobs_directory/000/
# e.g.: 1  2  3  ...
JOB_ID=1   # replace with actual job ID shown in the history
cat database/jobs_directory/000/${JOB_ID}/galaxy_${JOB_ID}.sh
```

Verify the input is materialized under `_crypt/inputs` and that no legacy
`_c4gh_stage` path is created:

```bash
find database/jobs_directory/000/${JOB_ID} -path "*/*_crypt/inputs/*/plaintext" -type f
# Should print: .../_crypt/inputs/ds_N/plaintext

find database/jobs_directory/000/${JOB_ID} -path "*/*_c4gh_stage/*"
# Should print nothing
```

The generated command should reference `_crypt/inputs/ds_<id>/plaintext`
instead of the encrypted source path.

### Optional operator check — header-only B interactions

When request logging is enabled on the mock service, you should only see
header re-encryption endpoints (`/recrypt_header_to_job_key` and
`/recrypt_header_to_user_key`) and no bulk payload-transfer endpoints.

---

## Step 6 — Verify output re-encryption (Phase 3)

After the job in Step 5 completes:

1. Confirm the history output dataset type ends with `.c4gh`.
2. Open dataset details and verify `metadata.crypt4gh_header` is populated.
3. Confirm plaintext output files are handled in `_crypt/outputs/`.
4. Confirm persisted output has Crypt4GH magic bytes.

Example checks:

```bash
JOB_ID=1  # replace

find database/jobs_directory/000/${JOB_ID} -path "*/*_crypt/outputs/*" -type f

OUT_DATASET_PATH=$(readlink -f database/files/*/*/*/*/* 2>/dev/null | head -n 1)
python - "$OUT_DATASET_PATH" << 'EOF'
import sys
from pathlib import Path

p = Path(sys.argv[1])
with p.open('rb') as f:
   print('magic:', f.read(8))
EOF
# Expected: b'crypt4gh'
```

To verify ciphertext decrypts correctly with the user private key:

```bash
python - << 'EOF'
import io
import crypt4gh.lib
from crypt4gh.keys import get_private_key

encrypted_path = 'REPLACE_WITH_OUTPUT_DATASET_PATH'
user_sk = get_private_key('test-data/crypt4gh/user_key.sec', lambda: b'')

with open(encrypted_path, 'rb') as f:
   out = io.BytesIO()
   crypt4gh.lib.decrypt([(0, user_sk, None)], f, out)

print('Decrypted output bytes:', out.getvalue()[:200])
EOF
```

---

## Step 7 — Verify the staging gate behavior

To confirm the gate works, temporarily disable staging and check that the
dataset disappears from tool inputs:

1. Set `enable_crypt4gh_transparent_staging: false` in `config/galaxy.yml`.
2. Restart Galaxy (`./run.sh`).
3. Open the same FastQC tool — the `fastqsanger.c4gh` dataset should **not**
   appear in the input drop-down.
4. Run a tool and verify outputs are no longer re-encrypted to `.c4gh`.
5. Re-enable the flag and restart to restore normal behavior.

---

## Key files reference

| File                                       | Purpose                                                         |
|--------------------------------------------|-----------------------------------------------------------------|
| `test-data/crypt4gh/user_key.sec`          | User private key (decrypts test file)                           |
| `test-data/crypt4gh/user_key.pub`          | User public key                                                 |
| `test-data/crypt4gh/compute_key.sec`       | Compute test private key used by mock compute-side service      |
| `test-data/crypt4gh/compute_key.pub`       | Compute test public key used by mock compute-side service       |
| `test-data/crypt4gh/test.fastqsanger.c4gh` | Test FASTQ encrypted with `user_key.pub`                        |
| `test/unit/jobs/mock_recryptor_service.py` | Mock compute-side recryptor service (FastAPI + uvicorn)         |
| `lib/galaxy/tools/crypt4gh_remote_execution.py` | Execution-side `_crypt/` staging/finalization logic       |
