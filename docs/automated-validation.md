# Automated regression and release acceptance

[Validation workflow](../.github/workflows/validation.yml) runs on pull requests,
main pushes, version tags, and manual dispatch. It has two independent layers:

| Job | What it establishes |
|---|---|
| Python 3.11 / 3.12 on Ubuntu 24.04 | Ruff, non-native regression, wheel/sdist build, clean wheel install and dependency check |
| AVL 3.52 on macOS 15 arm64 / Python 3.12 | Verified upstream source, fresh native build, full regression with no skips, installed-wheel eleven-tool stdio including trim and a 256-case disconnect/cancel/resume job |
| Release acceptance | Both preceding layers succeeded; skipped, cancelled or failed layers cannot pass |

The Python-only job intentionally deselects native tests and is not native
acceptance. The native job requires executable `AVL_BIN` and native test
collection (`--require-native`). The JUnit gate rejects missing/empty reports,
failures, collection errors and any skipped tests. No `continue-on-error` or
optional native-job shortcut is used. A source download or compiler failure is
an incomplete acceptance, never a successful numerical test.

Third-party Actions are pinned to commit SHAs, run with read-only repository
permissions, and use GitHub-hosted machines without personal research data.
The native job uses the versioned macOS arm64 compiler lock and micromamba 2.9.0.
The official archive is verified against SHA-256
`0b588ecea9222f5b625d0af0c87ae31daf3cdba1532cf0bbb36f93d6e854849b`.
Only regular files/directories are extracted; upstream development symlinks are
omitted. The numerical solver source is not patched. Build overrides are the
existing macOS build helper's compiler/linker settings.

Evidence artifacts retain JUnit reports, build logs, packages and stdio reports
for 14 days. Keep the acceptance evidence with a release when publishing it.
The workflow validates packages; it does not publish releases, alter client
configuration, or replace an installed MCP runtime. Branch protection is a
separate repository setting; maintainers must require/check `Release acceptance`
for the exact PR head before merging or publishing.

## Run locally

Use a machine-local test environment. `.[test]` installs the verification tools;
`.[dev]` also includes plotting dependencies used by historical research scripts.

```sh
python -m pip install '.[test]'
python -m ruff check .
AVL_BIN=/absolute/path/to/avl python -m pytest -q --require-native \
  --junitxml=reports/native.xml
python scripts/check_test_report.py reports/native.xml
python -m build
python -m venv /absolute/local/path/to/acceptance-env
/absolute/local/path/to/acceptance-env/bin/python -m pip install dist/*.whl
/absolute/local/path/to/acceptance-env/bin/python -m pip check
/absolute/local/path/to/acceptance-env/bin/python scripts/verify_stdio.py \
  --command /absolute/local/path/to/acceptance-env/bin/avl-mcp \
  --avl-bin /absolute/path/to/avl --work-root /absolute/path/to/acceptance-results \
  --vanilla examples/official/vanilla/vanilla.avl --bd examples/official/bd/bd.avl
```

The stdio test checks the installed console version, real official coefficients,
all eleven tools, longitudinal trim with independent verification, rejection of
an out-of-bounds trim solution, saved trim queries, a detached job across server
disconnection, acknowledged cancellation, resume and saved-result queries.
The native regression also exercises trim control gains, fixed controls, reference
points and failure paths. Consult the exact commit's JUnit report and checks for
executed cases; earlier version reports are historical evidence.
Tests establish software and
numerical consistency on the tested platform, not aircraft accuracy, mesh
independence or an existing desktop client's registry refresh.
