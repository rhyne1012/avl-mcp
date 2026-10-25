# Trim from mass or weight, speed and CG

`avl.prepare_trim` converts dimensional inputs to an existing `TrimRequest` and
`References` without invoking AVL. `avl.trim_physical` performs that conversion,
solves the resulting longitudinal trim, and independently replays the accepted
condition. Both require declared geometry and CG units; neither guesses them from
a filename, model title or adjacent mass file.

```json
{
  "model_path": "/absolute/path/to/model.avl",
  "request": {
    "mass_kg": 12,
    "density_kg_m3": 1.225,
    "speed_m_s": 25,
    "geometry_length_unit": "m",
    "cg": {"x": 0.6, "y": 0, "z": 0.03, "length_unit": "m"},
    "mach": 0.0735,
    "control": "elevator",
    "alpha_bounds_deg": [-10, 15],
    "control_bounds": [-30, 30]
  },
  "outputs": ["total"]
}
```

This is a syntax example; mass, area, CG and conditions must describe the user's
model. `outputs`, `case_name` and `timeout_seconds` apply to `avl.trim_physical`;
omit them when calling the read-only preparation tool.

| Input | Units and meaning |
|---|---|
| `mass_kg` or `weight_n` | Exactly one positive value: mass in kg or weight force in N |
| `gravity_m_s2` | Positive acceleration, default 9.80665 m/s²; used with mass |
| `density_kg_m3` | Positive air density in kg/m³ |
| `speed_m_s` | Positive airspeed in m/s |
| `geometry_length_unit` | Required `m`, `ft` or `in`; applies to geometry and native reference dimensions |
| `cg` | Required x/y/z in AVL geometry axes (X aft, Y right, Z up), with its own `length_unit` |
| `mach` | Required independent Mach for AVL, in `[0, 0.7)` |
| Control/bounds/tolerances | Same CONTROL gain semantics and acceptance rules as [coefficient trim](trim.md) |

The conversion uses `W = mass_kg * gravity_m_s2` (or the supplied `weight_n`),
`q = 0.5 * density_kg_m3 * speed_m_s²`, and `target_cl = W / (q * Sref_m²)`.
One international foot is 0.3048 m and one inch is 0.0254 m; area is converted with
the square of the length factor. Geometry coordinates are not rescaled.
CG coordinates are converted into geometry units and replace `xref/yref/zref`.
`target_cm` is fixed to zero about this CG. Density and speed do not determine
Mach without thermodynamic information; supply mutually consistent values.

An optional complete `references` object supplies Sref/Cref/Bref in native geometry
units. Its moment-reference coordinates are recorded but replaced by the explicit
CG. Without an override, the geometry header supplies those three dimensions.
All original inputs, selected reference values, conversion factors, source hashes,
dynamic pressure, weight and computed targets are retained under `physical_trim`.
Native execution saves this provenance with the manifest and result, including
numerically rejected trim attempts.

Preparation returns `trim_request`, `references` and `geometry_length_unit`.
These can be passed as `request`, `references` and `length_unit` to `avl.trim`, or
collected into `trim_requests` for `avl.sweep`/`avl.submit`. A batch has one shared
reference object, so varying CG requires separate batches. Save preparation
responses alongside such batches: the coefficient-only batch API does not carry
the dimensional preparation provenance automatically. Use `avl.trim_physical`
when the conversion and native result should be saved together in one operation.

The physical assumption is steady, unbanked level flight with lift equal to
weight, zero sideslip/body rates, and zero aerodynamic pitching moment about CG.
It does not solve thrust/drag balance, thrust-induced moments, banked/climbing
flight, lateral trim, mass/inertia dynamics, static stability or eigenmodes.
Acceptance bounds reject out-of-range roots; they do not constrain AVL iterations
or establish the model's validity at the solved angles.
