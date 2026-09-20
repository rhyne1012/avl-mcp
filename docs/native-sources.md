# Native AVL 3.52 source and license information

This document supplements the historical `avl-3.52-macos-arm64.tar.gz` asset
(SHA-256 `872529a033fc245272b12edc44218d2df6a93dab66f91dce0d1d511a8128740f`).
The v0.2.0 Python packages do not include native binaries. The historical binary
is an independent Apple Silicon build, not an official MIT/AVL binary.

## AVL

AVL 3.52 numerical source is unmodified. The complete original source archive,
GPL license and build helpers are included under `upstream/`, `LICENSE-AVL` and
`build/` in the native archive. Upstream source:
https://web.mit.edu/drela/Public/web/avl/avl3.52.tgz

SHA-256: `0b588ecea9222f5b625d0af0c87ae31daf3cdba1532cf0bbb36f93d6e854849b`.

The packaging helper changes Mach-O load paths and ad-hoc signatures; it does not
change numerical source. Compiler/package versions are retained in the archive.

## GCC runtime libraries

The archive's `libgcc` and `libgfortran5` packages are version 16.2.0, build 5,
under GPL-3.0-only WITH GCC-exception-3.1. The full GPLv3 text is supplied in the
companion `avl-3.52-native-notices.zip`; the Runtime Library Exception and recipe
metadata are retained in `licenses/<package>/` in the native archive.

Download the corresponding upstream sources without charge:

- GCC: https://ftp.gnu.org/gnu/gcc/gcc-16.2.0/gcc-16.2.0.tar.gz
  SHA-256: `071d00a097579e5ef7ce97fc4a9e58e73fd3503c0a013c765c970370a5a53b9b`.
- zlib source referenced by the retained GCC recipe:
  https://github.com/madler/zlib/releases/download/v1.3.1/zlib-1.3.1.tar.gz
  SHA-256: `9a93b2b7dfdac77ceba5a558a580e74667dd6fede4585b91eefb60f03b72df23`.
- Exact conda-forge recipe revision:
  https://github.com/conda-forge/ctng-compilers-feedstock/tree/eb8e8cd301d4fd4392d14fc2357ed1a7265020f3

Use `licenses/<package>/recipe/meta.yaml` for the source checksums and patch order;
all referenced patches and the retained parent build scripts are provided under
`licenses/<package>/recipe/parent/`. Source URLs were checked for availability;
the upstream source checksums above are taken from the retained recipe metadata.

## X11 libraries

The following MIT-licensed library packages retain their individual copyright and
permission notices in `licenses/<package>/licenses/COPYING` and package provenance
in `native-packages.json`:

- libxcb 1.17.0: https://xorg.freedesktop.org/archive/individual/lib/libxcb-1.17.0.tar.gz
- libX11 1.8.13: https://www.x.org/releases/individual/lib/libX11-1.8.13.tar.xz
- libXau 1.0.12: https://www.x.org/releases/individual/lib/libXau-1.0.12.tar.xz
- libXdmcp 1.1.5: https://www.x.org/releases/individual/lib/libXdmcp-1.1.5.tar.xz

The historical native archive and its hashes are unchanged. Keep this supplement
with it when redistributing. Research models and personal client configuration are
not part of either archive.
