# Optional AVL 3.52 native bundle for macOS arm64

The Python MCP package accepts an independently installed AVL 3.52 executable.
Building AVL yourself is optional when a compatible executable is available.
This private project also retains a tested native bundle for personal installation.

## Contents

`avl-3.52-macos-arm64.tar.gz` contains the AVL executable, seven required dylibs,
upstream AVL source archive, build helpers, dependency metadata, licenses, and
SHA-256 records. Numerical AVL source is unmodified. Mach-O load paths are relative
and binaries are ad-hoc signed. This is not an official upstream binary release.
It was tested on Apple Silicon with macOS 27.0. Other macOS versions and Intel
machines have not been verified. The Python wheel does not contain these binaries.

## Local installation

1. Download the bundle and `SHA256SUMS.txt` from the same private GitHub release.
2. Verify the archive against its SHA-256 entry.
3. Extract it to a machine-local folder such as `~/Developer/Codex/tools/avl/3.52`.
   Use a new destination to preserve any existing installation.
4. Install the MCP wheel into a machine-local Python virtual environment.
5. Configure `--avl-bin` to the extracted `bin/avl` and `--work-root` to your analysis folder.
6. Call `avl.health`, then run the shipped official examples before relying on this machine.

Keep each machine's virtual environment, native runtime and client settings local.
Source and small lock files may be version controlled; project data can have a
separate output location. Never copy a virtual environment between machines.

## Validation and provenance

The release includes upstream Plane Vanilla and Bubble Dancer examples. Phase-1
validation comprised 38 passing tests, real stdio calls to all five MCP tools, and
eight finite-difference derivative checks. See `validation.md` for numerical limits.
Registration in a client configuration and tool availability in an existing desktop
session are separate checks; a client reload may be required.

The bundle retains the original upstream AVL archive plus build instructions. The
native package index records each library's conda-forge package URL and checksum;
the retained recipe metadata identifies its upstream sources. Treat library licenses
individually when preparing any redistribution beyond this personal archive.

No research aircraft geometry, private flight model, CFD workbook, credentials,
or personal MCP configuration is included in the GitHub release.
