# Optional AVL 3.52 native bundle for macOS arm64

The Python MCP package accepts an independently installed AVL 3.52 executable.
Building AVL yourself is optional when a compatible executable is available.
The historical v0.1.0a1 release retains a tested native bundle. The v0.2.0 Python
release uses a separate AVL installation and does not bundle the solver.

## Contents

`avl-3.52-macos-arm64.tar.gz` contains the AVL executable, seven required dylibs,
upstream AVL source archive, build helpers, dependency metadata, licenses, and
SHA-256 records. Numerical AVL source is unmodified. Mach-O load paths are relative
and binaries are ad-hoc signed. This is not an official upstream binary release.
It was tested on Apple Silicon with macOS 27.0. Other macOS versions and Intel
machines have not been verified. The Python wheel does not contain these binaries.

## Local installation

1. Download the bundle and `SHA256SUMS.txt` from the historical v0.1.0a1 GitHub release, together with the
   `avl-3.52-native-notices.zip` license/source supplement.
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
the retained recipe metadata identifies its upstream sources. The companion [source and license information](native-sources.md) identifies the
exact upstream sources, retained patches/build recipes and full GPLv3 supplement.
Keep all notices and corresponding source information when redistributing.

No research aircraft geometry, private flight model, CFD workbook, credentials,
or personal MCP configuration is included in the GitHub release.
