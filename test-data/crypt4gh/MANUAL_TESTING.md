# Manual UI Testing Guide: Crypt4GH Support

This guide walks through manually testing the Phase 1 and Phase 2 crypt4gh
changes in a running Galaxy instance.

All commands assume you are in the **Galaxy root directory**
(`/home/dlopez/dev/gx-version/testing`) with the venv active:

```bash
cd /home/dlopez/dev/gx-version/testing
source .venv/bin/activate
```

---

## Prerequisites

- Galaxy checked out on the `explore-crypt4gh-support` branch
- The `crypt4gh` Python package installed in the venv (`pip show crypt4gh`)

> **Phase 2 full end-to-end** (the actual FUSE mount) additionally requires
> `crypt4ghfs` to be installed. The staging logic and generated job script can
> be inspected without it — the job will simply fail at the `crypt4ghfs` step.
>
> `crypt4ghfs` depends on `fuse3` system headers. Install them first:
>
> ```bash
> sudo apt-get install fuse3 libfuse3-dev pkg-config
> ```
>
> Then install the Python package into the venv:
>
> ```bash
> pip install crypt4ghfs
> ```

---

## Step 1 — Create the crypt4ghfs key config file

This file tells `crypt4ghfs` where to find the compute node's private key. In
production this lives on the compute node; for local testing we use the test
key from this repository.

```bash
cat > /tmp/crypt4ghfs_test.conf << EOF
[CRYPT4GH]
seckey = $(pwd)/test-data/crypt4gh/compute_key.sec

[FUSE]
options = ro,default_permissions
EOF
chmod 600 /tmp/crypt4ghfs_test.conf
```

> **Note:** `rootdir` and `extension` are _not_ set here — Galaxy generates a
> per-dataset conf file at staging time that inherits these settings and injects
> the correct values for each job.

Verify:

```bash
cat /tmp/crypt4ghfs_test.conf
# [CRYPT4GH]
# seckey = /home/.../test-data/crypt4gh/compute_key.sec
#
# [FUSE]
# options = ro,default_permissions
```

---

## Step 2 — Start the mock re-encryptor service

The mock service re-encrypts crypt4gh headers on-the-fly using the test key
pair. It implements the same HTTP API as the real ELIXIR re-encryptor service.

Open a **dedicated terminal** and leave it running:

```bash
cd /home/dlopez/dev/gx-version/testing
source .venv/bin/activate

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

## Step 3 — Configure `config/galaxy.yml`

Add (or uncomment) these three keys under the `galaxy:` section:

```yaml
galaxy:
  enable_crypt4gh_transparent_staging: true
  crypt4gh_reencryption_service_url: "http://127.0.0.1:54321" # port from Step 2
  crypt4gh_compute_key_config_path: "/tmp/crypt4ghfs_test.conf"
```

---

## Step 4 — Start Galaxy

```bash
./run.sh
```

Wait until you see `Starting server in PID ...` and the UI is accessible at
`http://localhost:8080`.

---

## Step 5 — Upload a crypt4gh-encrypted file (Phase 1)

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

## Step 6 — Run a tool with the encrypted input (Phase 2)

1. In the tool search box type **FastQC** (or any tool that accepts
   `fastqsanger` input).
2. In the input dataset selector, the `test.fastqsanger.crypt4gh` dataset
   should appear (because `enable_crypt4gh_transparent_staging: true` enables
   the `matches_any` gate).
3. Select it and click **Run Tool**.

### What to verify (Phase 2 — job script inspection)

While (or after) the job runs, find the job working directory:

```bash
ls database/jobs_directory/000/
# e.g.: 1  2  3  ...
JOB_ID=1   # replace with actual job ID shown in the history
cat database/jobs_directory/000/${JOB_ID}/tool_script.sh
```

Look for the crypt4ghfs staging block **before** the tool command:

```bash
# Mount crypt4gh input 0 (dataset original: .../dataset_NNN.dat)
crypt4ghfs -f --conf '/path/_c4gh_stage/ds_N/crypt4ghfs.conf' '/path/_c4gh_mnt/ds_N' &
_CRYPT4GHFS_PID_0=$!
_c4gh_wait=0
until [ -e '/path/_c4gh_mnt/ds_N/input' ] || [ $_c4gh_wait -ge 150 ]; do
    ...
done
```

And the unmount block **after**:

```bash
_CRYPT4GH_TOOL_EXIT=$?
# Unmount crypt4gh input 0
fusermount -u '/path/_c4gh_mnt/ds_N' 2>/dev/null || true
kill $_CRYPT4GHFS_PID_0 2>/dev/null || true
exit $_CRYPT4GH_TOOL_EXIT
```

Also check that the staged file was created:

```bash
find database/jobs_directory/000/${JOB_ID} -name "*.crypt4gh"
# Should print: .../_c4gh_stage/ds_N/input.crypt4gh

# Verify the staged file starts with crypt4gh magic and has a NEW header
# (encrypted for the compute key, not the user key):
python - << 'EOF'
import sys, base64, io
import crypt4gh.header, crypt4gh.lib
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

> If `crypt4ghfs` is **not** installed the job will fail at the mount step, but
> you can still confirm the staged file exists and is correctly re-encrypted
> using the script above.

---

## Step 7 — Verify the staging gate (Phase 1 / Phase 2 interaction)

To confirm the gate works, temporarily disable staging and check that the
dataset disappears from tool inputs:

1. Set `enable_crypt4gh_transparent_staging: false` in `config/galaxy.yml`.
2. Restart Galaxy (`./run.sh`).
3. Open the same FastQC tool — the `fastqsanger.crypt4gh` dataset should **not**
   appear in the input drop-down.
4. Re-enable the flag and restart to restore normal behaviour.

---

## Key files reference

| File                                           | Purpose                                       |
| ---------------------------------------------- | --------------------------------------------- |
| `test-data/crypt4gh/user_key.sec`              | User's private key (decrypts the test file)   |
| `test-data/crypt4gh/user_key.pub`              | User's public key                             |
| `test-data/crypt4gh/compute_key.sec`           | Compute node private key (used by crypt4ghfs) |
| `test-data/crypt4gh/compute_key.pub`           | Compute node public key (re-encryptor target) |
| `test-data/crypt4gh/test.fastqsanger.crypt4gh` | Test FASTQ file encrypted with `user_key.pub` |
| `test/unit/jobs/mock_recryptor_service.py`     | Mock re-encryptor service (FastAPI + uvicorn) |
| `lib/galaxy/jobs/crypt4gh_staging.py`          | Staging utility called from `prepare_job`     |
