# Single-point longitudinal aerodynamic trim

`avl.trim` solves angle of attack and one geometry CONTROL variable so that AVL's
total lift coefficient and body-axis pitching-moment coefficient match requested
targets. It uses AVL 3.52's native constraints, then verifies an accepted solution
with a new prescribed-condition AVL process. It does not modify the native solver.

## Request

The tool accepts `model_path`, `request`, optional `references`, `case_name`
(default `trim`), `timeout_seconds` (default 120), `length_unit` (default
`unspecified`) and `outputs`. Input-root restrictions, geometry validation,
dependency snapshots and output selection follow `avl.run`.
The timeout must be between 0.1 and 600 seconds and is one budget covering both
native trim and its independent replay.

| `request` field | Meaning |
|---|---|
| `control` | Required exact CONTROL variable name to solve; no automatic elevator selection |
| `target_cl` | Required finite target for total lift coefficient `CLtot` |
| `target_cm` | Target for total pitch coefficient `Cmtot`; default 0 |
| `mach` | Required fixed Mach, `0 <= mach < 0.7` |
| `alpha_bounds_deg` | Required two-element `[lower, upper]` acceptance interval in degrees |
| `control_bounds` | Required two-element acceptance interval in CONTROL variable units |
| `fixed_controls` | Other named controls held fixed; omitted controls are zero |
| `cl_tolerance` | Positive absolute CL residual tolerance; default `1e-6` |
| `cm_tolerance` | Positive absolute Cm residual tolerance; default `1e-6` |

Intervals must have distinct ordered endpoints: alpha endpoints within `[-30, 30]`
degrees and CONTROL endpoints within `[-180, 180]` variable units. Fixed control
values have the same CONTROL domain. All values must be finite. The solved control cannot also
appear in `fixed_controls`. Unknown control names are rejected. Beta and
`pb_2v`, `qc_2v`, `rb_2v` are zero; these are not request parameters for trim.

Example for the included Plane Vanilla geometry:

```json
{
  "model_path": "/absolute/path/to/vanilla.avl",
  "request": {
    "control": "elevator",
    "target_cl": 0.6,
    "target_cm": 0.0,
    "mach": 0.2,
    "alpha_bounds_deg": [-10.0, 15.0],
    "control_bounds": [-30.0, 30.0],
    "fixed_controls": {"flap": 2.0},
    "cl_tolerance": 0.000001,
    "cm_tolerance": 0.000001
  },
  "case_name": "vanilla-trim",
  "outputs": ["total", "stability"]
}
```

Bounds describe what the caller will accept. They do not restrict AVL's iterations
and are not a bounded optimization problem. A solution outside either interval is
rejected without clamping it. Bounds do not establish that a model remains valid
at those angles or that an actuator can achieve those deflections.

## Control and reference conventions

CONTROL values are geometry variables. Local surface deflection in degrees is
the variable value multiplied by that section's gain, with the geometry's sign
and duplication conventions retained. A single name can drive multiple surfaces,
including a model's existing shared V-tail elevator. This API does not infer or
create a mixing rule between two independent left/right controls.

Cm uses `Xref/Yref/Zref` from the geometry or the explicit `references` override.
Zero Cm means zero pitch moment about that point. It means moment equilibrium
about the aircraft CG only if that point actually is the CG. Reference overrides
must supply all six fields (`sref`, `cref`, `bref`, `xref`, `yref`, `zref`) and
affect only the staged copy. `length_unit` labels dimensions without converting
geometry. Adjacent `.mass` and `.run` files are not loaded.

The requested CL is a coefficient, not a weight or dimensional lift requirement.
Computing a suitable CL from mass, density, speed and area is outside this tool.
The solution does not impose thrust/drag or weight balance, symmetric forces,
lateral trim, static stability or dynamic-mode requirements.

## Acceptance and saved results

Success requires all of the following:

1. A complete, finite native result and the required fixed Mach, beta, rates,
   controls and references.
2. Signed residuals `CLtot - target_cl` and `Cmtot - target_cm` within their
   absolute tolerances, and solved alpha/control within both acceptance intervals.
3. A fresh fixed-condition calculation at the solved condition that reproduces
   the target coefficients and satisfies the independent verification checks.

Replay totals are compared to the native trim totals with relative tolerance
`1e-7` and absolute tolerance `1e-9`; the replay must also satisfy the requested
CL/Cm residual tolerances. These numerical tolerances are not an aerodynamic
accuracy claim or a guarantee of a unique trim root.

The normal `condition` describes the solved condition, not the requested targets.
Results add `analysis_type: "longitudinal_trim"` and a `trim` object containing:

- `schema_version: "1.0.0"` for the trim metadata itself.
- `request` and `solved_condition` for requested and achieved input conditions.
- Signed `residuals.CL` and `residuals.Cm`.
- `within_bounds` and `accepted`.
- `native_converged`, distinguished from final acceptance of the solution.
- `verification`, whose status is `passed` only after the independent run passes.

The existing physical `result_contract` remains version 1.0.0. Native coefficient
values and derivatives are not corrected or converted by trim. Derivatives and
loads, when requested, describe the solved condition. ST/SB derivatives retain
their native held-fixed conventions; they are not derivatives along a re-trimmed
CL/Cm-constrained path. See [the derivative contract](result-contract.md#trim-results).
Selecting only `total`
still performs acceptance and independent verification.

Each executed attempt retains input copies, commands, native stdout/stderr,
raw tables and structured results under `<work-root>/runs/`. Verification retains
its own independent run evidence. Executed failures retain a run identifier and
the available trim diagnostics. A failure before a solution exists cannot provide
a solved condition or numerical residuals.

Read saved trim metadata with `avl.results` using the returned `job_id`:

```json
{
  "job_id": "<returned trim run identifier>",
  "fields": [
    "condition.alpha_deg",
    "condition.controls.elevator",
    "trim.residuals.CL",
    "trim.residuals.Cm",
    "trim.accepted",
    "trim.verification.status"
  ]
}
```

The query does not rerun AVL. Its outer `success` describes the query; inspect
the row's `success`, `error` and `trim` to determine whether that analysis passed.
Failed executed trims remain queryable even when they have no accepted coefficient
fields. Background `status`, `cancel` and `resume` do not apply to this synchronous
tool. Submit a new trim request to try different targets or acceptance limits.

## Failures and validation scope

Input validation, unavailable/ineffective controls, solver nonconvergence,
out-of-bounds solutions (`TRIM_OUT_OF_BOUNDS`), unmet coefficient tolerances,
timeouts, malformed output and independent-verification failures are unsuccessful
analyses. A successful process exit alone never establishes trim acceptance.

The [automated acceptance](automated-validation.md) includes real official-case
trim and saved queries in the installed-wheel MCP test. Regression checks cover
gain/sign conventions, fixed controls, reference points and negative paths.
These checks establish software and numerical consistency on the tested build;
they do not prove physical aircraft trim or flightworthiness.

Batch/background trim, user-defined control mixing, automatic mass-file loading,
dimensional flight equilibrium and eigenmodes are outside 0.4.0. The native
constraint interface is documented in the [official AVL user guide](https://web.mit.edu/drela/Public/web/avl/avl_doc.txt).
