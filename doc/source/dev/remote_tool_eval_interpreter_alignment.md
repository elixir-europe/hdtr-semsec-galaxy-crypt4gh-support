# remote_tool_eval interpreter alignment (upstream `dev`)

## Context

`remote_tool_eval.py` is framework code from `lib/galaxy/tools/` and must run with the Galaxy framework Python environment.

On upstream `dev`, the remote-tool bootstrap in `lib/galaxy/jobs/command_factory.py` previously invoked:

```bash
PYTHONPATH="$GALAXY_LIB:$PYTHONPATH" python "$GALAXY_LIB"/galaxy/tools/remote_tool_eval.py
```

That `python` resolves from `PATH` at job runtime and can point to a tool/dependency interpreter instead of the Galaxy framework interpreter.

## Why this is a bug

If `python` resolves to a non-framework interpreter, `remote_tool_eval.py` may fail before tool execution because framework dependencies are unavailable (or incompatible). This is a pre-existing launcher issue and is independent of Crypt4GH-specific code.

The failure mode can vary by environment, for example:

- missing framework dependencies,
- incompatible interpreter/runtime behavior.

## Fix in commit `193846275d`

The launcher now uses Galaxy's selected interpreter variable, matching existing Galaxy patterns:

```bash
PYTHONPATH="$GALAXY_LIB:$PYTHONPATH" "${GALAXY_PYTHON:-python}" "$GALAXY_LIB"/galaxy/tools/remote_tool_eval.py
```

This aligns the remote-tool bootstrap with other framework-side command launchers (for example set-metadata).

## Scope and non-goals

- **In scope:** upstream `dev` launcher alignment for `remote_tool_eval.py`.
- **Out of scope:** Crypt4GH branch-specific cleanup/finalization hardening and tests.

Crypt4GH-specific changes remain only on the Crypt4GH worktree/branch, as requested.

## Verification summary

For upstream `dev` scope, verification used:

1. Unit assertion in `test/unit/app/jobs/test_command_factory.py` that the generated command contains `"${GALAXY_PYTHON:-python}"` and not plain `python`.
2. Direct command-build check confirming:
   - `HAS_GALAXY_PYTHON=True`
   - `HAS_PLAIN_PYTHON=False`

## PR notes

This document is intentionally separate from the code fix commit and references it explicitly.

- Code fix commit: `193846275d`
