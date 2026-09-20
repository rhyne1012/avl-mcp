# Installation and connection diagnostics

`avl.health` checks the configured Python environment, executable, directory access
and AVL 3.52 headless startup. It retains structured evidence and logs. Architecture
and declared library information is collected using the platform's available
inspection tools; actual native startup determines whether the loader can start
this binary. Static library listings do not prove recursive dependency resolution.
No libraries are installed and no client configuration is edited.

A diagnostic can run before the MCP server is connected:

```sh
/absolute/local/path/avl-mcp-env/bin/avl-mcp --diagnose \
  --avl-bin /absolute/local/path/avl --work-root /absolute/path/to/results
```

It prints one JSON report and exits 0 on success, 1 on failure. It checks Python
imports before importing the server, so missing MCP/Pydantic dependencies can be
reported without an MCP connection. Run `python -m pip check` separately to audit
all installed package requirements. Use an installed wheel for acceptance testing.

To additionally initialize a fresh stdio server, discover its tools and call health:

```sh
/absolute/local/path/avl-mcp-env/bin/avl-mcp --check-mcp \
  --avl-bin /absolute/local/path/avl --work-root /absolute/path/to/results
```

This launches the current Python interpreter with `-m avl_mcp` and the same explicit
AVL/work/input-root/mesh options. The exact command, tool list, package-version
check and child health outcome are recorded. A connection may pass while its
health reports a bad native executable; overall success requires both. The probe
has a 45-second protocol budget. It never assumes a desktop app was refreshed.

## Report layers

- `checks.python`: interpreter, package versions, fresh import check and repair hint.
- `checks.executable`: resolved path, existence/execute permission, host architecture,
  native file description and declared libraries when inspection is available.
- `checks.work_directory`: actual create/write/rename/read/delete probe inside the
  diagnostic's own new folder. Invalid/unwritable roots return a structured failure
  even when no report can be saved there.
- `checks.input_root`: optional configured directory accessibility, not every file.
- `verification.native_startup`: version, headless startup and loading outcome;
  identifies library, format/architecture and permission errors from retained evidence.
- `verification.numerical_case`: always `not_run`; health does not solve an example.
- `verification.mcp_connection`: `not_tested` from standalone health, `request_received`
  inside an MCP health handler, or the actual fresh stdio probe result from `--check-mcp`.
- `verification.desktop_registration`: always `not_tested`. Confirm the current
  tool list and a successful call in the actual client after registration/restart.

Missing inspection utilities mean unavailable evidence, not confirmed compatibility.
Native startup does not prove graphical rendering, all optional library features,
or numerical results. macOS arm64 is the tested native platform; Linux inspection
has a readelf path but Linux/Windows solver execution remains unverified.

## Validation sequence

1. Install the wheel in a machine-local venv and run `python -m pip check`.
2. Run `--diagnose`, then `--check-mcp` with the intended paths.
3. Run `scripts/verify_stdio.py` against the exact installed console command and
   official examples. This validates numerical results, all ten tools, result
   metadata, saved queries, background cancellation/resumption and reconnection.
4. Update the client command after backing up its local configuration; preserve
   other settings. Restart/reload the client if needed and check its tools there.

Keep old runtime and configuration rollback until deployment is accepted. Changing
MCP code invalidates background-job resume fingerprints: finish/cancel active jobs
before switching, and submit a new job under the new implementation. Old results
remain readable with the original work root. Do not migrate venvs between hosts.
