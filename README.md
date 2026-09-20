# avl-mcp

Run reproducible [AVL](https://web.mit.edu/drela/Public/web/avl/) aerodynamic analyses
through the Model Context Protocol. The server validates input, runs AVL in an
isolated directory, and returns structured results with the original solver files.

**Version: 0.2.0 (no GitHub Release is created for this version).** Supports AVL **3.52** only. Native execution is
tested on macOS Apple Silicon; other operating systems are not yet verified.
This project is an independent wrapper, not an official MIT or AVL release.

[繁體中文說明](README.zh-TW.md) · [Validation](docs/validation.md)

## Tools

| Tool | Purpose |
|---|---|
| `avl.health` | Diagnose Python/native installation, directory access and headless startup |
| `avl.inspect` | Read geometry, references, controls and file dependencies |
| `avl.validate` | Check supported grammar, dependency paths and mesh budget |
| `avl.run` | Calculate one prescribed condition, loads and ST/SB derivatives |
| `avl.sweep` | Calculate 1-10000 explicit conditions with shared AVL processes and save CSV |
| `avl.submit` | Snapshot inputs and start a detached background job |
| `avl.status` | Read progress, counts and the actual job outcome |
| `avl.cancel` | Cancel a queued/running job while retaining completed cases |
| `avl.resume` | Verify saved inputs/results and recompute only missing or failed cases |
| `avl.results` | Page and filter saved results without running AVL |

The connector does not expose trim, eigenmodes, geometry editing, interactive graphics,
CAD conversion, or automatic OpenVSP comparison. The model validator checks input
structure and basic invariants; it does not prove freedom from intersections or
aerodynamic suitability. A successful process exit alone is never accepted as a result.

## Installation

Requirements: Python 3.11 or newer and a separately installed AVL 3.52 executable.
The Python package does **not** download or bundle AVL, install system libraries,
or modify any MCP client configuration.

```sh
python3 -m venv /absolute/local/path/avl-mcp-env
/absolute/local/path/avl-mcp-env/bin/python -m pip install .
/absolute/local/path/avl-mcp-env/bin/avl-mcp --version
```

For development, install `.[dev]` in a local environment. Build release artifacts
with `python -m build`; install the resulting wheel for the stdio acceptance test.
Keep virtual environments and native libraries on each machine's local disk.
Research inputs, results and logs can be stored in a separate project directory.

A compatible prebuilt AVL 3.52 executable can also be used. The private project
retains an optional [macOS arm64 native bundle](docs/native-installation.md); compiling
AVL is not an inherent MCP requirement.

### Building AVL on macOS Apple Silicon

The upstream 3.52 source archive is available at
<https://web.mit.edu/drela/Public/web/avl/avl3.52.tgz>.
The archive used for this release has SHA-256
`0b588ecea9222f5b625d0af0c87ae31daf3cdba1532cf0bbb36f93d6e854849b`.

Upstream requires Fortran/C compilers, a plotting library and numerical libraries.
This project includes a helper for a task-local conda-forge environment containing
`gfortran_osx-arm64=14`, `xorg-libx11`, and **`xorg-xorgproto`**. The last package
provides the X11 protocol headers needed at build time. Xcode Command Line Tools
must already be available. A platform-specific dependency lock records the tested
compiler environment in `docs/native-dependencies-osx-arm64.lock`.

```sh
python scripts/build_avl_macos.py \
  --source /absolute/path/to/extracted/AVL3.52rel09032025 \
  --compiler-prefix /absolute/local/path/to/compiler-environment \
  --logs /absolute/path/to/project/logs
```

The helper modifies only the disposable build copy's plot-library configuration,
and overrides compiler/linker flags. It does not patch the numerical solver.
`scripts/bundle_avl_macos.py` can collect the required dylibs into a **new** local
directory and update their relative load paths. The current execution path disables
graphics, so a running X server is not required. X11 libraries remain link dependencies.
Interactive plotting is not tested or exposed by this MCP.

## MCP client configuration

Example Codex configuration, using absolute paths chosen for your machine:

```toml
[mcp_servers.avl]
command = "/absolute/local/path/avl-mcp-env/bin/avl-mcp"
args = [
  "--avl-bin", "/absolute/local/path/avl-3.52/bin/avl",
  "--work-root", "/absolute/path/to/project/AVL",
  "--input-root", "/absolute/path/to/approved/model-directory"
]
tool_timeout_sec = 660
```

`--input-root` is optional. If supplied, model files must resolve inside that root.
Dependencies must remain inside the model's own directory, even without this option.
For synchronous calls, set the client timeout above the requested analysis time budget.
For long analyses, use `avl.submit`; its short response does not wait for the solver.
Equivalent environment variables are `AVL_BIN`, `AVL_MCP_WORK_ROOT`, and
`AVL_MCP_INPUT_ROOT`. The default budget is 5,000 estimated vortices per case;
`--max-vortices` can explicitly raise it up to 20,000. This is an estimate, not a RAM guarantee.

Installing a wheel and testing a standalone MCP process do not register tools in
an already running desktop session. Client registration/reload is a separate step.

For connection troubleshooting, run `avl-mcp --diagnose` or `avl-mcp --check-mcp`
with the same AVL/work-root arguments. They print structured evidence without
changing client settings. [Diagnostic layers and workflow](docs/diagnostics.md)
distinguish native startup, numerical tests, fresh stdio and desktop registration.

## Example requests

Inspect an official example:

```json
{"model_path": "/absolute/path/to/avl-mcp/examples/official/vanilla/vanilla.avl"}
```

Run a prescribed condition with `avl.run`:

```json
{
  "model_path": "/absolute/path/to/vanilla.avl",
  "condition": {
    "alpha_deg": 3.0,
    "beta_deg": 0.0,
    "mach": 0.2,
    "pb_2v": 0.0,
    "qc_2v": 0.0,
    "rb_2v": 0.0,
    "controls": {"elevator": 1.0}
  },
  "case_name": "vanilla-elevator",
  "timeout_seconds": 120,
  "length_unit": "unspecified"
}
```

`controls` maps the **CONTROL variable names** in the geometry to their values.
Local deflection in degrees equals the variable value multiplied by that section's
gain; it is not necessarily the physical deflection of every connected surface.
Unknown control names are rejected. Omitted controls and angular rates are zero.
Omitted Mach uses the geometry header. Nearby `.run` and `.mass` files are
intentionally not loaded in prescribed-condition analyses.

Use `avl.sweep` with `model_path` and a `conditions` array. Its execution budget is
300 seconds by default (one total budget, not a separate allowance per condition).
Cases are grouped by Mach to reuse AVL's matrix factorization and returned in the
original request order. Each native process handles at most 128 conditions; the
connector explicitly resets every angular rate and control at every point.
Execution stops on the first error unless `stop_on_error=false`; remaining cases
then use a fresh process. Timeout/cancellation always stops the attempt.
The MCP response includes at most 25 case summaries; use `avl.results` to page the rest.
Completed cases and failed-attempt logs remain available. A partial synchronous
sweep returns `isError=true`. Full results are in `result.json`.

### Select outputs

`avl.run`, `avl.sweep` and `avl.submit` accept `outputs`. Omit it to retain the
previous complete output set. Use `["total"]` for forces/moments only, or e.g.
`["total", "body"]` to add body-axis derivatives. Supported tables are `total`,
`stability`, `body`, `surfaces`, `strips`. Total output is always retained to verify
that AVL used the requested conditions. Skipped tables are not generated or
parsed; querying one later reports `FIELD_NOT_AVAILABLE` rather than rerunning AVL.

### Durable background jobs

Call `avl.submit` with the same model/conditions/output selection as a sweep.
It returns a `job_id`; use that ID with `avl.status`, `avl.cancel`, `avl.resume`
and `avl.results`. Workers run independently of the MCP connection and keep their
state under `<work-root>/jobs/`. At most two background jobs execute at once per
work root; additional workers wait. Synchronous calls are outside this queue.

`avl.status` returns `queued`, `running`, `completed`, `failed`, `cancelled` or
`interrupted`, completed/failed counts, and the last error. `success=true` means
**the status query succeeded**, not that the solver job passed. Cancellation is
cooperative: query until acknowledged. Only that worker's owned AVL process is
stopped. Completed cases and all native attempts are retained.

An inactive job can be resumed from another MCP connection using the same work
root and implementation. Resume checks original geometry and dependencies,
staged input hashes, settings, executable hash, and the MCP implementation hash.
Changed inputs/solver/code require a new job. Valid successful case checkpoints
are reused; missing/damaged outputs and failed cases are recomputed. Timeouts
apply per execution attempt (excluding queue time); resume grants the same budget
again. A worker lost during startup may take up to 30 seconds to be identified.
Do not edit/remove active job files. Cross-machine/network-filesystem locking is
not established; use one host per work root.

Query coefficients without invoking a solver:

```json
{
  "job_id": "<id returned by submit, run or sweep>",
  "indices": [0, 3, 8],
  "fields": ["total.fields.CLtot", "body.derivatives.Cmq"],
  "offset": 0,
  "limit": 20
}
```

`avl.results` pages cases in original index order (limit 1-100); `indices` filters
that list. Dotted field names preserve case, e.g. `stability.control_derivatives.elevator.Cm`.
With no `fields`, each row includes total coefficients and saved artifact paths,
including strip CSV/JSON when requested. Unfinished cases are absent, not zeros.
Large responses are rejected with a request to narrow the selection. Results can
be read even when the AVL executable is unavailable. For older runs, the directory
basename is the run ID. Job control tools apply only to background jobs.

Optional `references` must specify all six values: `sref`, `cref`, `bref`, `xref`,
`yref`, `zref`. Overrides affect only the staged copy. Cref, Bref, and the three
coordinates use the geometry's length unit; Sref uses its square. `length_unit`
labels the data (`m`, `ft`, `in`, or `unspecified`); it does **not** convert geometry.
For the official Bubble Dancer example, the supplied mass file documents inches.

## Results and conventions

Every successful case adds a versioned `result_contract`, also saved as
`result-contract.json`: actual reference dimensions/point, units, axes, coefficient
normalizations, ST/SB derivative variables and section control gains. Compact
`result_context` accompanies sweep summaries and selected saved-result queries.
Unknown units stay `Lunit`; native coefficients are unchanged. Separately calculated
`derived.wind_forces` records the rotation of native stability totals.

See the [complete result contract](docs/result-contract.md), including two upstream
3.52 caveats exposed by tests: constant CDp with sideslip can make native stability
and body force projections differ, and ST alpha derivatives at nonzero p/r can
differ from fixed-stability-rate perturbations. Applicable cases carry explicit
limitation codes and warnings; no native number is silently corrected.


Synchronous executions use `<work-root>/runs/`; background jobs use `<work-root>/jobs/`.
Each job retains native process folders and separate per-case outputs:

- Original input snapshots, staged geometry and dependencies, and SHA-256 records.
- `manifest.json`, exact `commands.txt`, `stdout.log`, `stderr.log`, `process.json`.
- Raw `total.mrf` and the other selected MRF tables.
- Parsed `result.json`; selected strips add `strips.json`/`strips.csv`, synchronous sweeps add `summary.csv`.
- Background input snapshots, atomic per-case checkpoints, attempt records and worker logs.

The parser requires MRF `VERSION 1.0`, complete tables and finite numerical values.
It distinguishes missing/truncated output, solver diagnostics, version mismatch,
timeouts, changed input, and requested-versus-actual condition mismatch.
Logs go to files, never to the MCP protocol's stdout channel.

| Quantity | Convention |
|---|---|
| Geometry coordinates | X aft, Y right, Z up |
| Input angular rates | Standard **body** axes: X forward, Y right, Z down; pb/(2V), qc/(2V), rb/(2V) |
| `CXtot`, `CYtot`, `CZtot`, `Cltot`, `Cmtot`, `Cntot` | Standard body-axis force/moment coefficients |
| `CLtot`, `CDtot`; `Cl'tot`, `Cn'tot` | Stability-axis lift/drag; stability-axis roll/yaw moments |
| `stability` (`ST`) | Stability-axis derivatives; alpha/beta derivatives per radian |
| `body` (`SB`) | Body-axis derivatives; normalized velocity and angular-rate variables |
| Control derivatives | Per CONTROL variable unit, including geometry gains |
| Moment reference | Xref/Yref/Zref; not necessarily a mass-derived aircraft CG |
| `CDind` | Near-field induced contribution |
| `CDff` | Trefftz-plane induced drag; keep separate from CDind |
| `CDvis` | Supplied profile-drag model; not a viscous flow solution |

Raw key case is preserved: **CL** is lift and **Cl** is roll moment. At nonzero
beta, stability axes and full wind axes differ. Undefined AVL diagnostic sentinels
for neutral point/spiral parameter are returned as JSON `null`, not physical values.

## Limits and validation

AVL is a thin lifting-surface, slender-body and quasi-steady potential-flow model.
It does not establish stall, separated-flow or transonic accuracy. The connector accepts
0 <= Mach < 0.7 and warns on large angles/high Mach; those limits do not certify
physical accuracy. Numerical agreement between wrappers or solvers is not flight validation.

See [docs/validation.md](docs/validation.md) for the tested conditions and scope.
The included geometries come from the official AVL archive. Regression snapshots
are generated outputs on the tested build, not published experimental truth.

```sh
AVL_BIN=/absolute/path/to/avl python -m pytest -q
python scripts/verify_stdio.py \
  --command /absolute/path/to/avl-mcp-env/bin/avl-mcp \
  --avl-bin /absolute/path/to/avl \
  --work-root /absolute/path/to/project/AVL \
  --vanilla examples/official/vanilla/vanilla.avl \
  --bd examples/official/bd/bd.avl
```

Native tests are explicitly skipped if `AVL_BIN` is absent. Use
`AVL_TEST_WORK_ROOT` to retain native pytest run artifacts in a chosen directory.

## Roadmap

- Add trim and eigenmode analysis with appropriate mass/inertia validation.
- Add controlled geometry generation and previews after the analysis interface stabilizes.

Research aircraft models and CFD data are not part of this repository.

## License and acknowledgements

This repository is released under **GPL-2.0-or-later**; see [LICENSE](LICENSE).
AVL is by Mark Drela and Harold Youngren. The official source and MRF readers carry
GPL-2.0-or-later notices, with additional contributor credits in their source files.
Example inputs retain their upstream content; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
The numerical solver is installed separately. Distributing native binaries requires
preserving their applicable notices, licenses and corresponding-source obligations,
including those of bundled libraries. No native binary is included in the Python wheel.
