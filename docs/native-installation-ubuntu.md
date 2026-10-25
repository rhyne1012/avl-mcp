# Build and validate AVL 3.52 on Ubuntu

The Python wheel uses a separately installed AVL executable. This guide builds
the pinned official AVL 3.52 source on Ubuntu 24.04 x86_64 using GNU Fortran and
system X11 libraries. The same procedure is exercised by the Linux native CI job;
the macOS job remains a separate required platform in the acceptance matrix.
No Linux solver binary is included in the Python wheel.

## Dependencies

Install the compiler, X11 development files and Python environment tools if they
are not already available:

```bash
sudo apt-get update
sudo apt-get install --no-install-recommends -y \
  build-essential gfortran libx11-dev curl python3-venv
```

The build uses upstream's bundled LAPACK/BLAS routines and EISPACK, so neither
`liblapack-dev` nor `libblas-dev` is required. X11 libraries are needed at link and
runtime, but the MCP runs AVL with graphics disabled and needs no display server
or Xvfb. The executable is linked to the system GNU Fortran and X11 runtime
libraries; rebuild on the destination machine if those libraries are incompatible.

## Build from verified source

Run these commands from an `avl-mcp` checkout. Choose a new build directory; source
preparation refuses to overwrite an existing extraction directory. Avoid spaces
in build and compiler paths because upstream makefiles do not quote every path.

```bash
AVL_BUILD_DIR="$PWD/build/native-avl-3.52-ubuntu"
mkdir -p "$AVL_BUILD_DIR"
curl --fail --location --retry 3 \
  --output "$AVL_BUILD_DIR/avl3.52.tgz" \
  https://web.mit.edu/drela/Public/web/avl/avl3.52.tgz
python3 scripts/prepare_native_source.py \
  --archive "$AVL_BUILD_DIR/avl3.52.tgz" \
  --destination "$AVL_BUILD_DIR/source"
python3 scripts/build_avl_linux.py \
  --source "$AVL_BUILD_DIR/source/AVL3.52rel09032025" \
  --logs "$AVL_BUILD_DIR/build-logs"
export AVL_BIN="$AVL_BUILD_DIR/source/AVL3.52rel09032025/bin/avl"
```

`prepare_native_source.py` verifies SHA-256
`0b588ecea9222f5b625d0af0c87ae31daf3cdba1532cf0bbb36f93d6e854849b`
before extracting the official archive. The Linux helper selects double precision,
adds the GNU Fortran argument-mismatch compatibility flag required by the older
plotting routines, and sets system X11 paths. It does not patch numerical solver
source or install system packages. Optional `--fc` and `--cc` select compiler
executables. Build logs and `build-commands.json` retain commands, compiler
versions, architecture and build exit codes, including a failed build step.

Use a fresh extracted source tree when changing platforms, compiler versions or
build flags, so stale objects cannot be reused. The first local verification used
Ubuntu 24.04 x86_64 and GNU Fortran/GCC 13.3.0. Other Linux distributions and
architectures are not covered by this CI matrix.

## Verify the native executable and installed MCP

Keep the environment and results local to the machine:

```bash
python3 -m venv "$AVL_BUILD_DIR/python-env"
"$AVL_BUILD_DIR/python-env/bin/python" -m pip install '.[test]'
"$AVL_BUILD_DIR/python-env/bin/python" -m pytest -q --require-native \
  --junitxml="$AVL_BUILD_DIR/reports/native.xml"
"$AVL_BUILD_DIR/python-env/bin/python" scripts/check_test_report.py \
  "$AVL_BUILD_DIR/reports/native.xml"
"$AVL_BUILD_DIR/python-env/bin/python" -m build --outdir "$AVL_BUILD_DIR/dist"
python3 -m venv "$AVL_BUILD_DIR/installed"
"$AVL_BUILD_DIR/installed/bin/python" -m pip install "$AVL_BUILD_DIR"/dist/*.whl
"$AVL_BUILD_DIR/installed/bin/python" -m pip check
"$AVL_BUILD_DIR/installed/bin/python" scripts/verify_stdio.py \
  --command "$AVL_BUILD_DIR/installed/bin/avl-mcp" \
  --avl-bin "$AVL_BIN" --work-root "$AVL_BUILD_DIR/stdio-acceptance" \
  --vanilla examples/official/vanilla/vanilla.avl \
  --bd examples/official/bd/bd.avl
```

The required native suite rejects a missing solver and the report gate rejects
skipped tests. It exercises the official Plane Vanilla and Bubble Dancer models,
output integrity, batch/job lifecycle, and trim behavior against the actual AVL
binary. The installed-wheel stdio check exercises all fourteen tools, including
background jobs and trim. Inspect its JSON report at
`$AVL_BUILD_DIR/stdio-acceptance/reports/mcp-stdio-validation.json`.

The CI release acceptance job requires the Python regression matrix and every
native platform to succeed. Each platform uploads separate build, test and stdio
evidence, including artifacts from failed runs. A workflow definition alone is
not evidence of a successful run. Numerical applicability and the existing mesh
sensitivity limitations are documented in [validation.md](validation.md).

For a client configuration, use the installed `avl-mcp` executable together with
absolute `--avl-bin` and `--work-root` paths, then call `avl.health`. Keep the
verified upstream archive and its license if redistributing the solver; see
[native-sources.md](native-sources.md) and [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).
