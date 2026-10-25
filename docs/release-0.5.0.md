# AVL MCP 0.5.0

Version 0.5.0 adds durable trim batches, native Ubuntu acceptance, general mesh
sensitivity reports, and longitudinal trim from explicit mass/weight, speed and
CG inputs. Fourteen MCP tools are available. The final-version promotion changes
the version and documentation; numerical implementation is unchanged from the
tested 0.5.0.dev1 snapshot.

## Changes since 0.4.0

- `avl.sweep` and `avl.submit` accept trim targets, with per-point residual/bounds
  checks and independent replay. Background trim supports cancellation, verified
  resume, saved queries and CSV summaries. See [batch trim](batch-trim.md).
- Ubuntu 24.04 x86_64 and macOS arm64 both run a verified upstream AVL source
  build, the full regression suite and installed-wheel acceptance in CI. See
  [Ubuntu installation](native-installation-ubuntu.md).
- `avl.mesh_sensitivity` supports surface- and section-based refinement, reports
  baseline/adjacent coefficient differences, and exports JSON/CSV/Markdown.
  Actual native vortex counts must match the requested grid. BODY discretization
  stays fixed and tolerance results do not certify convergence. See
  [mesh sensitivity](mesh-sensitivity.md).
- `avl.prepare_trim` converts explicit SI loading/flight inputs and a declared CG
  into CL targets and native references. `avl.trim_physical` executes verified
  trim and saves the conversion provenance. Geometry and CG units are explicit
  (`m`, `ft`, `in`); Mach is supplied independently. See [dimensional trim](physical-trim.md).

## Release artifacts and installation

The GitHub release provides the Python wheel, source distribution, validation
summary and `SHA256SUMS.txt`. Verify their hashes, then install the wheel into a
machine-local environment with Python 3.11 or newer:

```sh
sha256sum -c SHA256SUMS.txt
python -m pip install avl_mcp-0.5.0-py3-none-any.whl
avl-mcp --version
```

A separate AVL 3.52 executable is required. No native solver, system libraries,
private research model or client configuration is included in these packages.
The optional historical macOS runtime remains in the
[0.1.0a1 release](https://github.com/rhyne1012/avl-mcp/releases/tag/v0.1.0a1).

## Validation and compatibility

The 0.5.0.dev1 implementation passed 487 tests with no failures, errors or skips,
all fourteen installed MCP tools, and the 256-condition and 24-trim background
disconnect/cancel/resume scenarios. The official script reproduced three mesh
levels and eight derivative checks. [Development evidence](mesh-physical-validation.json)
is retained as history; final-version acceptance is recorded in the Release's
validation asset and the exact release commit/tag's checks.

Existing coefficient-based tools and physical result contract 1.0.0 remain
compatible. CONTROL values retain geometry gains/signs. Bounds are post-solve
acceptance checks. Dimensional trim assumes unbanked steady level lift=weight and
zero aerodynamic pitching moment about CG; it does not solve thrust balance,
mass/inertia dynamics or eigenmodes. Numerical checks establish software
consistency, not aircraft accuracy or mesh independence.

Finish existing background jobs with their original runtime or submit new jobs
after upgrading: implementation changes invalidate resume fingerprints. Updating
the installed runtime and reloading an existing MCP client are separate local
deployment steps; publishing this Release does neither automatically.
