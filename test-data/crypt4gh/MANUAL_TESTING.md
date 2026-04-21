# Manual UI Testing Guide: Crypt4GH Support

This guide walks through manually testing the Phase 1 and Phase 2 crypt4gh
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

## Step 1 — Start the mock re-encryptor service

The mock service re-encrypts crypt4gh headers on-the-fly using the test key
pair. It implements the same HTTP API as the real ELIXIR re-encryptor service.

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

Add (or uncomment) these three keys under the `galaxy:` section.
`crypt4gh_compute_key_path` points directly to the compute node's private key
file (`.sec` format produced by `crypt4gh-keygen`):

```yaml
galaxy:
  enable_crypt4gh_transparent_staging: true
  crypt4gh_reencryption_service_url: "http://127.0.0.1:54321" # port from Step 1
  crypt4gh_compute_key_path: "/absolute/path/to/test-data/crypt4gh/compute_key.sec"
```

Replace the path above with the absolute path on your system
(`realpath test-data/crypt4gh/compute_key.sec`).

If the private key file is passphrase-protected, also set:

```yaml
crypt4gh_compute_key_passphrase_env: "C4GH_PASSPHRASE"
```

and export `C4GH_PASSPHRASE` in the environment before starting Galaxy.
The test key in this repository has no passphrase, so this option can be
omitted for local testing.

---

## Step 3 — Start Galaxy

```bash
./run.sh
```

Wait until you see `Starting server in PID ...` and the UI is accessible at
`http://localhost:8080`.

---

## Step 4 — Upload a crypt4gh-encrypted file (Phase 1)

The file `test-data/crypt4gh/test.fastqsanger.crypt4gh` is a real crypt4gh file
containing a short FASTQ snippet, encrypted with the test user key.

1. Open `http://localhost:8080` and log in (or use the default admin account).
2. Click the **Upload** button (top-left of the tool panel).
3. Click **Choose local file** and select:
   ```
   test-data/crypt4gh/test.fastqsanger.crypt4gh
   ```
4. In the **Type** column leave it as `Auto-detect` — Galaxy should sniff the
   crypt4gh magic bytes and assign the type automatically.
5. Click **Start**, then **Close**.

### What to verify (Phase 1)

After upload completes, click the dataset name in the history to expand it:

- **Type** should be `fastqsanger.crypt4gh` (not `binary` or `data`).
- Click the **ⓘ (info)** icon → **Dataset Details**. Under **Metadata** you
  should see a `crypt4gh_header` field containing a long base64-encoded string.
- The peek / content view will show the file is encrypted (no readable text) —
  this is expected.

---

## Step 5 — Run a tool with the encrypted input (Phase 2)

1. In the tool search box type **FastQC** (or any tool that accepts
   `fastqsanger` input).
2. In the input dataset selector, the `test.fastqsanger.crypt4gh` dataset
   should appear (because `enable_crypt4gh_transparent_staging: true` enables
   the `matches_any` gate).
3. Select it and click **Run Tool**.

### What to verify (Phase 2 — job script inspection)

While (or after) the job runs, find the job working directory:

Note: you may need to set the `cleanup_job` setting in `config/galaxy.yml` to `never` to prevent job directories from being deleted immediately after job completion. If you change this setting, remember to restart Galaxy.

```bash
ls database/jobs_directory/000/
# e.g.: 1  2  3  ...
JOB_ID=1   # replace with actual job ID shown in the history
cat database/jobs_directory/000/${JOB_ID}/galaxy_${JOB_ID}.sh
```

Look for the Python decrypt block **before** the tool command:

```bash
"${GALAXY_VIRTUAL_ENV}/bin/python" -c "
import crypt4gh.lib, crypt4gh.keys, os, sys
sk = crypt4gh.keys.get_private_key('/path/to/compute_key.sec', lambda: b'')
with open('/path/_c4gh_stage/ds_N/input.crypt4gh', 'rb') as inf, \
     open('/path/_c4gh_stage/ds_N/input', 'wb') as outf:
    crypt4gh.lib.decrypt([(0, sk, None)], inf, outf)
" || { echo 'crypt4gh decryption failed'; exit 1; }
```

And the cleanup block **after** the tool command:

```bash
_CRYPT4GH_TOOL_EXIT=$?
rm -f '/path/_c4gh_stage/ds_N/input'
exit $_CRYPT4GH_TOOL_EXIT
```

Also verify the staged (re-encrypted) file and the decrypted file:

```bash
find database/jobs_directory/000/${JOB_ID} -name "*.crypt4gh"
# Should print: .../_c4gh_stage/ds_N/input.crypt4gh

# Manually decrypt the staged file to confirm it is valid:
python - << 'EOF'
import sys, io
import crypt4gh.lib
from crypt4gh.keys import get_private_key

staged = 'database/jobs_directory/000/1/_c4gh_stage/ds_1/input.crypt4gh'  # adjust path
compute_sk = get_private_key('test-data/crypt4gh/compute_key.sec', lambda: b'')

with open(staged, 'rb') as f:
    out = io.BytesIO()
    crypt4gh.lib.decrypt([(0, compute_sk, None)], f, out)

print("Decrypted content:", out.getvalue())
# Should print the original FASTQ plaintext:
# b'@read1\nACTGACTG\n+\nIIIIIIII\n'
EOF
```

---

## Step 6 — Verify the staging gate (Phase 1 / Phase 2 interaction)

To confirm the gate works, temporarily disable staging and check that the
dataset disappears from tool inputs:

1. Set `enable_crypt4gh_transparent_staging: false` in `config/galaxy.yml`.
2. Restart Galaxy (`./run.sh`).
3. Open the same FastQC tool — the `fastqsanger.crypt4gh` dataset should **not**
   appear in the input drop-down.
4. Re-enable the flag and restart to restore normal behaviour.

---

## Key files reference

| File                                           | Purpose                                                       |
| ---------------------------------------------- | ------------------------------------------------------------- |
| `test-data/crypt4gh/user_key.sec`              | User's private key (decrypts the test file)                   |
| `test-data/crypt4gh/user_key.pub`              | User's public key                                             |
| `test-data/crypt4gh/compute_key.sec`           | Compute node private key (set as `crypt4gh_compute_key_path`) |
| `test-data/crypt4gh/compute_key.pub`           | Compute node public key (re-encryptor target)                 |
| `test-data/crypt4gh/test.fastqsanger.crypt4gh` | Test FASTQ file encrypted with `user_key.pub`                 |
| `test/unit/jobs/mock_recryptor_service.py`     | Mock re-encryptor service (FastAPI + uvicorn)                 |
| `lib/galaxy/jobs/crypt4gh_staging.py`          | Staging utility called from `prepare_job`                     |
