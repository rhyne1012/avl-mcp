# AVL MCP 0.2.0

Run prescribed-condition AVL 3.52 analyses through ten MCP tools, with saved
inputs, native outputs and structured results that can be inspected or resumed.
This is an independent wrapper, not an official MIT/AVL release.

## Changes since 0.1.0a1

- Shared-process sweeps group conditions by Mach, reuse the influence matrix,
  reset rates and controls for every point, and preserve input order.
- Durable background jobs support status, cancellation and verified resume.
  Two background jobs can execute concurrently per work root.
- Select output tables and query saved results with filtering and pagination
  without invoking the solver.
- Result contract 1.0.0 records units, axes, reference dimensions/point and control
  definitions. Derived quantities are separate from unchanged native values.
- Layered health checks and CLI diagnostics distinguish dependency, native,
  numerical, fresh MCP-connection and desktop-client checks.

## Installation

Install the attached `avl_mcp-0.2.0-py3-none-any.whl` into a machine-local Python
virtual environment. Python 3.11+ and a separate AVL 3.52 executable are required.
Dependencies are installed by pip. The source distribution includes the official
Plane Vanilla and Bubble Dancer examples, tests and build helpers.

See the [README](../README.md) for configuration and
[native installation](native-installation.md) for the separate solver.
Verify downloaded assets against `SHA256SUMS.txt`.

## Validation and limitations

- Final-version regression: 77 tests passed, no skips, with the real AVL solver.
- Installed-wheel stdio: all ten tools, official cases and a 256-condition job
  across disconnect, cancellation, resume and saved-result queries passed.
- Tested on macOS Apple Silicon with Python 3.12 and AVL 3.52. Linux, Windows,
  Intel macOS and other Python versions are not verified by this release.
- Existing desktop clients may require a restart after a runtime upgrade. Fresh
  stdio acceptance does not prove an already-open desktop registry was refreshed.
- Prescribed conditions only: no trim, eigenmodes, CAD conversion, geometry editing
  or interactive graphics. Cross-host sharing of job locks is unverified.
- Two documented upstream behaviors are retained and flagged: force projection
  with nonzero sideslip and constant CDp, and ST alpha derivatives at nonzero p/r.
  See [result-contract details](result-contract.md).
- The Python package contains no native solver or research-model data. Numerical
  consistency tests do not establish aerodynamic accuracy or mesh independence.

The earlier measured 2.60x/2.65x batch speedups apply only to the recorded host and
official workloads; no general speedup or automatic multicore solver is promised.

License: GPL-2.0-or-later. Retain the included license and third-party notices.
