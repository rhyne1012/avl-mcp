"""Explicit dimensional inputs for verified, lift-equals-weight longitudinal trim.

Geometry coordinates are never rescaled. Only the lift target and moment reference
are derived; Mach remains an independent, explicitly supplied aerodynamic input.
"""

import json
import math
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .execution import validate_request
from .geometry import inspect_geometry
from .models import AVLFailure, References, TrimRequest
from .runner import dump
from .trim import fixed_condition, run_trim

PhysicalLengthUnit = Literal["m", "ft", "in"]
METRES_PER_UNIT = {"m": 1.0, "ft": 0.3048, "in": 0.0254}


class CenterOfGravity(BaseModel):
    """CG in the geometry coordinate system: X aft, Y right, Z up."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    x: float
    y: float
    z: float
    length_unit: PhysicalLengthUnit


class PhysicalTrimRequest(BaseModel):
    """SI loading/flight inputs with explicitly declared geometry and CG units."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    mass_kg: float | None = Field(
        default=None, gt=0, description="Supply exactly one of mass_kg or weight_n."
    )
    weight_n: float | None = Field(
        default=None,
        gt=0,
        description="Force in newtons; supply exactly one of weight_n or mass_kg.",
    )
    gravity_m_s2: float = Field(
        default=9.80665, gt=0, description="Applied only when converting mass_kg into weight."
    )
    density_kg_m3: float = Field(gt=0)
    speed_m_s: float = Field(gt=0)
    geometry_length_unit: PhysicalLengthUnit = Field(
        description="Native geometry/reference length unit; Sref uses its square. No rescaling."
    )
    cg: CenterOfGravity
    control: str = Field(min_length=1, max_length=80)
    mach: float = Field(
        ge=0,
        lt=0.7,
        description="Independent aerodynamic Mach input; not inferred from speed or density.",
    )
    alpha_bounds_deg: tuple[float, float]
    control_bounds: tuple[float, float]
    fixed_controls: dict[str, float] = Field(default_factory=dict)
    cl_tolerance: float = Field(default=1e-6, gt=0)
    cm_tolerance: float = Field(default=1e-6, gt=0)

    @model_validator(mode="after")
    def validate_loading_and_trim(self):
        if (self.mass_kg is None) == (self.weight_n is None):
            raise ValueError("Supply exactly one of mass_kg or weight_n.")
        # Reuse the ordinary trim contract, including bounds and CONTROL limits.
        self.coefficient_request(1.0)
        return self

    def coefficient_request(self, target_cl):
        return TrimRequest(
            control=self.control,
            target_cl=target_cl,
            target_cm=0.0,
            mach=self.mach,
            alpha_bounds_deg=self.alpha_bounds_deg,
            control_bounds=self.control_bounds,
            fixed_controls=self.fixed_controls,
            cl_tolerance=self.cl_tolerance,
            cm_tolerance=self.cm_tolerance,
        )


def _finite(value, name, *, positive=False):
    if not math.isfinite(value) or (positive and value <= 0):
        raise AVLFailure(
            "PHYSICAL_INPUT_RANGE",
            f"Derived {name} is not representable as a finite"
            f"{' positive' if positive else ''} number.",
        )
    return value


def _prepare(runner, model_path, request, references):
    # The same inspected bytes are passed to run_trim: staging checks their hashes
    # rather than silently re-reading a different model after deriving CL from Sref.
    obj = inspect_geometry(model_path, runner.input_root)
    initial = references.model_dump() if references is not None else dict(obj.references)
    geometry_scale = METRES_PER_UNIT[request.geometry_length_unit]
    cg_scale = METRES_PER_UNIT[request.cg.length_unit]
    area = _finite(initial["sref"] * geometry_scale * geometry_scale, "Sref_m2", positive=True)
    chord = _finite(initial["cref"] * geometry_scale, "Cref_m", positive=True)
    span = _finite(initial["bref"] * geometry_scale, "Bref_m", positive=True)
    cg_m = {
        name: _finite(getattr(request.cg, name) * cg_scale, f"CG_{name}_m")
        for name in ("x", "y", "z")
    }
    cg_native = {
        name: _finite(value / geometry_scale, f"CG_{name}_native") for name, value in cg_m.items()
    }
    effective = References(**(initial | {f"{name}ref": value for name, value in cg_native.items()}))
    weight = _finite(
        request.weight_n
        if request.weight_n is not None
        else request.mass_kg * request.gravity_m_s2,
        "weight_n",
        positive=True,
    )
    q = _finite(
        0.5 * request.density_kg_m3 * request.speed_m_s * request.speed_m_s,
        "dynamic_pressure_pa",
        positive=True,
    )
    q_area = _finite(q * area, "q_sref_n", positive=True)
    target = request.coefficient_request(_finite(weight / q_area, "target_cl", positive=True))
    # A preparation is useful only if the resulting ordinary request is valid for
    # this model. No binary is required, and no run directories are created.
    validate_request(
        runner,
        obj,
        [fixed_condition(target)],
        effective,
        ["total"],
        request.geometry_length_unit,
        120,
    )
    sources = {str(obj.path): obj.source_hash}
    sources.update({item["path"]: item["sha256"] for item in obj.dependencies})
    runner._check_sources(sources)
    provenance = {
        "schema_version": "1.0.0",
        "input": request.model_dump(mode="json"),
        "geometry_length_unit": request.geometry_length_unit,
        "source_inputs": sources,
        "reference_source": "references_override" if references is not None else "geometry_header",
        "geometry_header_references": dict(obj.references),
        "references_before_cg": initial,
        "references": effective.model_dump(),
        "conversion": {
            "geometry_length_to_m": geometry_scale,
            "cg_length_to_m": cg_scale,
            "reference_area_m2": area,
            "reference_chord_m": chord,
            "reference_span_m": span,
            "cg_m": cg_m,
            "cg_native": cg_native,
            "weight_n": weight,
            "weight_source": "weight_n"
            if request.weight_n is not None
            else "mass_kg_times_gravity",
            "gravity_applied": request.mass_kg is not None,
            "dynamic_pressure_pa": q,
            "q_sref_n": q_area,
            "target_cl": target.target_cl,
            "target_cm": 0.0,
            "lift_formula": "CL = weight_n / (0.5 * density_kg_m3 * speed_m_s^2 * Sref_m2)",
        },
        "assumptions": [
            "Unbanked steady level flight: required aerodynamic lift equals supplied weight.",
            "Zero aerodynamic pitching moment about the supplied CG; beta and body rates are zero.",
            "CG uses geometry axes X aft, Y right, Z up; geometry coordinates are not rescaled.",
            "Mach is supplied independently; no atmosphere or speed-of-sound consistency check.",
            "No thrust/drag balance, lateral trim, mass/inertia model or stability certification.",
            "Bounds accept or reject the solved condition; native iterations are unconstrained.",
        ],
    }
    return obj, target, effective, provenance


def prepare_trim(runner, model_path, request: PhysicalTrimRequest, references=None):
    """Return coefficient-trim inputs and auditable conversions without running AVL."""
    _, target, effective, provenance = _prepare(runner, model_path, request, references)
    return {
        "success": True,
        "solver_executed": False,
        "trim_request": target.model_dump(mode="json"),
        "references": effective.model_dump(),
        "geometry_length_unit": request.geometry_length_unit,
        "physical_trim": provenance,
    }


def run_physical_trim(
    runner,
    model_path,
    request: PhysicalTrimRequest,
    references=None,
    case_name="physical-trim",
    timeout_seconds=120.0,
    outputs=None,
):
    """Derive the target once, then perform native trim and independent replay."""
    obj, target, effective, provenance = _prepare(runner, model_path, request, references)
    if not math.isfinite(timeout_seconds) or not 0.1 <= timeout_seconds <= 600:
        raise AVLFailure("INVALID_TIMEOUT", "Timeout must be between 0.1 and 600 seconds.")
    validate_request(
        runner,
        obj,
        [fixed_condition(target)],
        effective,
        outputs,
        request.geometry_length_unit,
        timeout_seconds,
    )
    folder = runner._new_job(case_name)
    # Save dimensional inputs before launching the solver, including failed attempts.
    dump(folder / "physical-trim.json", provenance)
    result = run_trim(
        runner,
        model_path,
        target,
        effective,
        case_name,
        timeout_seconds,
        request.geometry_length_unit,
        outputs,
        folder=folder,
        geometry=obj,
    )
    result["physical_trim"] = provenance
    manifest_path = folder / "manifest.json"
    manifest = (
        json.loads(manifest_path.read_text())
        if manifest_path.exists()
        else {
            "analysis_type": "longitudinal_trim",
            "trim_request": target.model_dump(mode="json"),
            "model": obj.report(),
            "references_override": effective.model_dump(),
            "geometry_length_unit": request.geometry_length_unit,
            "run_directory": str(folder),
        }
    )
    manifest["physical_trim"] = provenance
    dump(manifest_path, manifest)
    result["artifacts"].update(
        {
            "physical-trim.json": str(folder / "physical-trim.json"),
            "manifest.json": str(manifest_path),
        }
    )
    dump(Path(result["run_directory"]) / "result.json", result)
    return result
