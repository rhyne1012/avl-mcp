# Mesh sensitivity

`avl.mesh_sensitivity` runs the same prescribed flight condition on staged copies
with progressively finer surface meshes. It accepts any geometry supported by
`avl.validate`, including section-based span counts and local airfoil dependencies.
Original files remain unchanged. This is a sensitivity study, not a mesh-independence
certificate or a test of geometric/aerodynamic validity.

```json
{
  "model_path": "/absolute/path/to/model.avl",
  "request": {
    "condition": {"alpha_deg": 3, "mach": 0.2},
    "factors": [1, 2, 3],
    "metrics": ["CLtot", "Cmtot", "CDff"],
    "absolute_tolerances": {"CLtot": 0.005, "Cmtot": 0.005}
  },
  "case_name": "mesh-study",
  "timeout_seconds": 300,
  "length_unit": "m"
}
```

Factors are integers from 1–100, strictly increasing, start at 1, and contain 2–8 levels.
Each factor multiplies chordwise counts and the active spanwise counts: surface
counts when present, otherwise each interval's section count. Spacing parameters,
geometry coordinates, controls, airfoil data and reference dimensions stay fixed.
Duplicated surfaces remain duplicated. Body station counts stay fixed and are
reported as such; this does not assess sensitivity to body discretization.

All levels are checked against the configured vortex budget before a solver is
started. A factor of 3 generally multiplies lifting-surface vortices by 9; the
default budget is 5,000 per level. One timeout budget covers the entire study.
Native output must match each requested vortex count; silently clipped AVL grids
are rejected and their diagnostic files retained.
The call accepts an optional complete `references` override, applied identically
at every level, and retains the normal explicit `length_unit` convention.

Results include per-level coefficients and native counts, original/staged input
hashes, raw solver artifacts, `result_json`, `summary_csv` and `report_markdown`.
Absolute and relative changes are measured both from the baseline and from the
previous level. Relative change is null when its reference coefficient is zero;
an absolute difference still remains meaningful. Coefficients available for
comparison are `CLtot`, `CDtot`, `CDff`, `Cmtot`, `CYtot`, `Cltot` and `Cntot`.

Optional absolute tolerances assess only the final adjacent pair for the named
metrics. Without supplied tolerances no threshold pass is inferred. A small final
difference alone does not prove asymptotic convergence, convergence of unselected
outputs, or aircraft accuracy. No Richardson extrapolation is performed.
`success` means all requested analyses completed; consult the separate tolerance
assessment. Failed studies retain completed levels and diagnostic artifacts.

For trim-related studies, first obtain an accepted trim condition and use its
saved `condition` here. This tool holds that condition fixed across meshes; it
does not re-trim each mesh. A separate trim at each resolution answers a different
question about changes in the required alpha and control input.
