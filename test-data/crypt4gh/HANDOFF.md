# Crypt4GH Phase 2 — Final Handoff Note (Task 9.7)

Date: 2026-06-24

## Scope covered

- Galaxy worktree: `explore-crypt4gh-library-support-merged-with-is-recryptor-from-26.0`
- Recryptor worktree: `work/phase2-recryptor-routes`
- Cross-repo live smoke path verified through `GALAXY_TEST_CRYPT4GH_REENCRYPTION_SERVICE_URL`.

Related commits in this delivery window:

- Galaxy: `025df89cd5` (`feat(crypt4gh): support live recryptor smoke env routing`)
- Recryptor: `e0a5f94` (`fix: normalize compute key expiry timezone and passphrase use`)

## Exact verification commands and outcomes

### Recryptor repo verification

```bash
poetry run pytest tests/test_compute_routes.py -q
```

Outcome:

- `19 passed in 0.29s`

### Galaxy repo verification

```bash
.venv/bin/pytest test/unit/jobs/test_crypt4gh_remote_execution.py test/unit/data/datatypes/test_crypt4gh.py -q
```

Outcome:

- `18 passed, 146 warnings in 3.20s`

```bash
VIRTUAL_ENV="/workspaces/dotfiles/repos/hdtr-semsec-galaxy-crypt4gh-support/work/explore-crypt4gh-library-support-merged-with-is-recryptor-from-26.0/.venv" \
GALAXY_VIRTUAL_ENV="/workspaces/dotfiles/repos/hdtr-semsec-galaxy-crypt4gh-support/work/explore-crypt4gh-library-support-merged-with-is-recryptor-from-26.0/.venv" \
.venv/bin/pytest test/integration/test_crypt4gh_remote_execution.py test/integration/test_config_schema.py -q
```

Outcome:

- `13 passed, 243 warnings in 239.09s (0:03:59)`

### Live cross-repo smoke verification

Compute-mode service start command used:

```bash
C4GH_RECRYPTOR_USE_HTTPS=false C4GH_RECRYPTOR_HOST=127.0.0.1 C4GH_RECRYPTOR_PORT=61358 poetry run python -m crypt4gh_recryptor_service.main compute
```

Health/API probe command used:

```bash
curl -s -o /tmp/recryptor_keyinfo_final.json -w "%{http_code}" -X POST "http://127.0.0.1:61358/get_compute_key_info" -H "Content-Type: application/json" -d '{"crypt4gh_user_public_key":"-----BEGIN CRYPT4GH PUBLIC KEY-----\nuser-key\n-----END CRYPT4GH PUBLIC KEY-----"}'
```

Outcome:

- HTTP status `200`

Live smoke test commands used:

```bash
VIRTUAL_ENV="/workspaces/dotfiles/repos/hdtr-semsec-galaxy-crypt4gh-support/work/explore-crypt4gh-library-support-merged-with-is-recryptor-from-26.0/.venv" \
GALAXY_VIRTUAL_ENV="/workspaces/dotfiles/repos/hdtr-semsec-galaxy-crypt4gh-support/work/explore-crypt4gh-library-support-merged-with-is-recryptor-from-26.0/.venv" \
GALAXY_TEST_CRYPT4GH_REENCRYPTION_SERVICE_URL="http://127.0.0.1:61358" \
.venv/bin/pytest test/integration/test_crypt4gh_remote_execution.py -k inheritance_simple -q
```

Outcome:

- `2 passed, 9 deselected, 192 warnings in 95.78s (0:01:35)`

```bash
VIRTUAL_ENV="/workspaces/dotfiles/repos/hdtr-semsec-galaxy-crypt4gh-support/work/explore-crypt4gh-library-support-merged-with-is-recryptor-from-26.0/.venv" \
GALAXY_VIRTUAL_ENV="/workspaces/dotfiles/repos/hdtr-semsec-galaxy-crypt4gh-support/work/explore-crypt4gh-library-support-merged-with-is-recryptor-from-26.0/.venv" \
GALAXY_TEST_CRYPT4GH_REENCRYPTION_SERVICE_URL="http://127.0.0.1:61358" \
.venv/bin/pytest test/integration/test_crypt4gh_remote_execution.py -k output_format -q
```

Outcome:

- `2 passed, 9 deselected, 192 warnings in 100.08s (0:01:40)`

## Pragmatic-programmer diagnostic

Score: **8.8 / 10**

Summary:

- **Tracer-bullet / end-to-end feedback:** live smoke hit real compute-mode endpoints and validated behavior across Galaxy + recryptor.
- **Design by contract / fail-closed:** timezone-aware expiration and passphrase handling were fixed at source; failing paths remained explicit and non-silent.
- **Orthogonality:** Galaxy now switches between mock/live service using one env hook (`GALAXY_TEST_CRYPT4GH_REENCRYPTION_SERVICE_URL`) without changing test semantics.
- **DRY:** repeated integration metadata setup was consolidated via helper methods in the integration test module.

## Clean-code review outcome

Outcome: **Pass (8.7 / 10)**

- Naming and helper extraction are clear in both repos for the touched slice.
- Added regression tests cover the two production-facing defects (timezone normalization and decryption passphrase propagation).
- No blocking readability or maintainability defects observed in the changed slice.

Non-blocking follow-up ideas (optional):

1. Add a tiny shell helper script for the long Galaxy live-smoke env bootstrap command.
2. Optionally centralize repeated absolute `.venv` path values in local developer docs to reduce copy/paste drift.

## Manual test suggestions for human operator

1. **UI upload + datatype sniff test**
   - Upload `test-data/crypt4gh/test.fastqsanger.c4gh` in Galaxy.
   - Verify datatype is `fastqsanger.c4gh` and metadata includes `crypt4gh_header`.

2. **Remote execution plaintext staging check**
   - Run `inheritance_simple` with a `.c4gh` input.
   - Inspect job work dir and verify plaintext staging under `_crypt/inputs/ds_<id>/plaintext`.

3. **Output encryption check**
   - Run `output_format` and verify output extension ends with `.c4gh`.
   - Confirm output file starts with Crypt4GH magic bytes (`crypt4gh`).

4. **Live service smoke in operator environment**
   - Start compute-mode service (command above).
   - Export `GALAXY_TEST_CRYPT4GH_REENCRYPTION_SERVICE_URL` and re-run the two targeted integration slices (`inheritance_simple`, `output_format`).

5. **TTL fail-closed behavior**
   - Force a short/expired compute key TTL and verify the job fails closed with the expected TTL/expiration error text.

6. **Cleanup failure marker behavior**
   - Trigger a forced `/recrypt_header_to_user_key` failure path and verify `CRYPT4GH_CLEANUP_FAILED` marker appears in tool stderr.

For broader step-by-step UI procedure, use `test-data/crypt4gh/MANUAL_TESTING.md`.
