# Result contract 1.0.0

Every successful `run`, sweep case and background case saves a `result_contract`
object and `result-contract.json`. This is an additive interface: existing native
coefficient keys, signs and numbers are unchanged. Labels preserve case (`CL` versus
`Cl`). Metadata describes only tables that were requested and produced.

`result_context` is the compact companion returned in sweep summaries and every
`avl.results` row, even when selecting just one coefficient. It includes schema
version, actual references, length/area units, moment reference, derivative axes,
applicable native limitation codes and the full contract file path. Saved legacy
results remain readable; absent context is explicitly marked with a null schema
version and `metadata_status=unavailable_in_saved_result`. No metadata is invented.

The full contract can also be selected with `fields=["result_contract"]`, or a
subsection such as `fields=["result_contract.derivative_tables.body"]`. These
queries read saved files and never start AVL. Limit/response-size rules still apply.

## Contents

| Key | Meaning |
|---|---|
| `schema_version` | Semantic contract version, separate from Python package/MRF versions |
| `native_format`, `definition_sources` | Supported format and exact upstream source provenance |
| `value_origin` | Native parsed tables versus connector calculations; applied transformations |
| `axes` | Geometry/body/stability definitions and actual body-to-stability component matrix |
| `units` | Caller-declared geometry units; `Lunit` when unspecified; no silent SI assumption |
| `references` | Sref/Cref/Bref/Xref/Yref/Zref read from verified solver output |
| `moment_reference` | Coordinates in geometry axes and their units; no mass/CG inference |
| `total_fields` | Unit, axes, coefficient normalization and meaning of each native total field |
| `controls` | Variable units, gains, hinge definitions and duplicate signs by section |
| `derivative_tables` | ST/SB variables, units, axes, held-fixed convention and control/design derivatives |
| `surface_fields`, `strip_fields` | Table-specific units, axes, normalization and sentinel definitions |
| `derived_fields` | Source fields and equations for separately computed values |
| `native_limitations` | Applicable upstream exceptions; native data remains unmodified |

`length_unit` is a label: using `ft` does not scale coordinates or change force
coefficients. Dimensional force/moment is not inferred without density and speed.
The geometry reference point uses X aft/Y right/Z up even though returned body
coefficients use X forward/Y right/Z down. All moments/rates use the right-hand rule.
Roll/yaw coefficients normalize by Q*Sref*Bref, pitch by Q*Sref*Cref, forces by
Q*Sref, where Q=0.5*rho*V². Local surface/strip coefficients use their own area/chord.

ST alpha/beta derivatives are per radian. Its p/r derivatives use stability axes;
SB p/r derivatives use body axes. SB velocity derivatives are with respect to
u/V0, v/V0, w/V0 at fixed reference speed/dynamic pressure, not m/s or Mach.
CONTROL derivatives are per variable unit, with section gains and duplicate signs
already included. They are not automatically per physical radian. DESIGN derivatives
retain their native variable units; the connector keeps these variables at zero.

The strip column `ai` is upstream `DWWAKE`, a normalized far-wake downwash used as a
small-angle proxy; it is not degrees. `c_cl` is upstream chord-scaled normal loading
`CNC`, in geometry length units. `C.P.x/c=999` is retained as a native sentinel when
local cl is zero. Neutral point/spiral sentinels of magnitude at least 1e29 become
JSON null, as in earlier versions. These diagnostics are not eigenmode results.

## Separate derived forces

`derived.wind_forces` applies these equations to **native stability totals**:

- CD = CDtot cos(beta) − CYtot sin(beta)
- CY = CYtot cos(beta) + CDtot sin(beta)
- CL = CLtot

Beta is converted from degrees internally. The native tables are unchanged. No
moment/derivative rotation, unit conversion or reference-point translation occurs.
These near-field quantities remain separate from the far-wake CDff calculation.

## Upstream 3.52 exceptions identified during validation

The official source archive with SHA-256
`0b588ecea9222f5b625d0af0c87ae31daf3cdba1532cf0bbb36f93d6e854849b`
and numerical perturbations of the supplied examples support two caveats:

1. **`NATIVE_FORCE_PROJECTION_DIFFERENCE`**: with nonzero beta and constant header
   CDp, `AERO` adds CDp directly to CDtot, but adds body forces along the freestream.
   CDtot then differs from the body-force projection by CDp*(1−cos(beta)). The
   contract reports the measured residual when significant. `derived.wind_forces`
   explicitly uses the native stability totals, and can therefore differ from a
   wind projection of native body forces. Bubble Dancer exercises this case.
2. **`NATIVE_ST_ALPHA_NONZERO_RATES`**: in `DERMATS`, the WROT_A calculation omits
   the DIR factor used elsewhere for standard-axis rate conversion. With nonzero
   roll/yaw rates, native ST alpha derivatives can differ from finite differences
   holding stability rates fixed. The contract flags these conditions. If that
   particular derivative is needed, use explicit perturbed runs with the desired
   rate convention. Plane Vanilla perturbations independently reproduce the
   native rate-sensitivity sign and distinguish it from the intended fixed-rate
   derivative. This caveat does not imply failure of the zero-rate regression.

The wrapper does not patch AVL or silently replace these native numbers. Its
`success` reports completed, validated execution and output parsing, not universal
physical accuracy or consistency of every upstream derivative convention.

Definitions are checked against the [official user primer](https://web.mit.edu/drela/Public/web/avl/avl_doc.txt)
and [AVL 3.52 source](https://web.mit.edu/drela/Public/web/avl/avl3.52.tgz), especially
`aoutmrf.f`, `aoutput.f`, `aero.f` and `atpforc.f`. The online primer carries an older
version heading, so the shipped 3.52 implementation takes precedence for MRF details.
