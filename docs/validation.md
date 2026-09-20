# Batch and background-job validation

Development snapshot: `0.2.0.dev0`, validated on macOS arm64 with Python 3.12 and AVL 3.52.
No new GitHub Release is created. [Machine-readable evidence](batch-jobs-validation.json)
records the tested implementation hash, tool coverage and host-specific timings.

- 57 tests pass with the native AVL executable enabled; Ruff and wheel build pass.
- Plane Vanilla and Bubble Dancer shared-process cases agree with isolated runs to
  the tests' absolute tolerance of 1e-10, including Mach regrouping, reference
  overrides, nonzero body rates, controls, and resetting them to zero.
- A 130-condition run covers the former 25-case limit and the 128-case process boundary.
- Deterministic solver fixtures exercise partial failure/restart, timeout, two-worker
  scheduling, queued/running cancellation, worker interruption, saved-result corruption,
  selective queries, and rejection of changed input/snapshot/solver/job settings.
- A normally installed wheel passes all ten tools over real MCP stdio. A 256-condition
  official-model background job survives termination of the first MCP process,
  supports cancellation/resumption from a new connection, and finishes all cases.
  Querying an unrequested output reports an explicit error without another solver run.
- Desktop registration and the user's active MCP environment are not changed by this validation.

For 24 prescribed conditions at Mach 0.2 with total output only, three repetitions
on unchanged official meshes gave median speedups of **2.60x (Plane Vanilla)** and
**2.65x (Bubble Dancer)**. Every compared total-output field matched exactly in this
benchmark. These are end-to-end runner timings on this host, including input staging,
process startup and parsing; they are not a performance guarantee for other workloads.

Reproduce the benchmark with:

```sh
python scripts/benchmark_batch.py --avl-bin /absolute/path/to/avl \
  --work-root /absolute/path/to/benchmark --examples examples/official
```

## Earlier phase-1 validation

Scope: AVL 3.52 prescribed-condition analysis and official example geometries.
Host: macOS Apple Silicon, double-precision GNU Fortran build of upstream 3.52.
Source archive SHA-256:
`0b588ecea9222f5b625d0af0c87ae31daf3cdba1532cf0bbb36f93d6e854849b`.
No aerodynamic solver source was patched.

## Acceptance gates

- Valid startup and exit without DISPLAY; executable/version/dependency checks.
- Official Plane Vanilla and Bubble Dancer cases with valid MRF outputs.
- Total/surface/strip count consistency; source hashes unchanged.
- Explicit flight/reference/control settings match the values used by AVL.
- Angle, rate and control derivatives agree with small central perturbations.
- Invalid inputs, missing dependencies, output corruption and nonconvergence fail closed.
- Timeouts kill the solver and preserve partial evidence.
- A normal installed wheel exposes and executes all five tools over actual MCP stdio.

The local acceptance summary and selected numerical results are generated in
`official-validation-summary.json`. Detailed logs and run manifests reside in the
explicit work root; they are not embedded in the distributable Python package.

## Reference snapshot

Plane Vanilla: Mach 0.2, alpha 3 deg, beta 0, zero controls/rates; Sref=9,
Cref=0.9, Bref=10, Xref=0.5, Yref=Zref=0 (geometry units).

| Quantity | Local upstream reference |
|---|---:|
| CLtot | 0.6754170403902354 |
| Cmtot | 0.1199155980932566 |
| CDind, near-field induced | 0.01173105158566566 |
| CDff, Trefftz induced | 0.01298629740605130 |
| CLa, per radian | 4.980084799923054 |
| Cma, per radian | -0.6348591303240443 |

These values are local regression evidence, not experimentally validated aircraft data.
Bubble Dancer retains its official BODY and supplied CDp. Its coefficients must not
be interpreted as a match to the differently shaped Plane Vanilla model.

## What is not established

- Aerodynamic agreement with measured data, CFD or VSPAERO.
- General mesh independence or uncertainty bounds; any grid check is case-specific.
- HS_UAV geometry equivalence or control effectiveness.
- Trim, eigenmodes, correct mass properties or full flight-dynamics models.
- Windows/Linux execution or a running desktop client's registry refresh.

The standalone MCP handshake and the desktop client's registration are separate gates.

## Derivative and grid evidence

Eight Plane Vanilla checks cover alpha/beta, all three body angular rates, elevator
pitch effectiveness, and aileron roll effectiveness. The largest relative difference
between reported and central-difference derivatives is approximately `2.04e-8`.
This tests internal numerical consistency at one prescribed condition.

At alpha 3 degrees and Mach 0.2, multiplying both panel directions yields:

| Vortices | CLtot | Cmtot | CDff |
|---:|---:|---:|---:|
| 294 | 0.67541704 | 0.11991560 | 0.01298630 |
| 1176 | 0.68109363 | 0.09333507 | 0.01327104 |
| 2646 | 0.68516690 | 0.07790373 | 0.01345035 |

Pitching moment is still sensitive to this refinement. These three meshes do
**not** establish mesh independence. The shipped official model is retained as the
regression fixture; derived meshes are separate local validation inputs.

Generate the detailed local report and plots with:

```sh
python scripts/validate_official.py \
  --avl-bin /absolute/path/to/avl \
  --work-root /absolute/path/to/project/AVL \
  --examples examples/official
```
