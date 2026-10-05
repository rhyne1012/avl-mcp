"""Native longitudinal trim with explicit acceptance and independent verification."""

import math
import time

from . import __version__
from .execution import (
    check_total,
    collect_case,
    condition_commands,
    startup_commands,
    validate_request,
)
from .geometry import inspect_geometry
from .models import AVLFailure, FlightCondition
from .outputs import parse_total
from .runner import dump


def trim_commands(obj, request, fixed, outputs):
    """Use native CL/PM constraints; each remaining operating variable stays explicit."""
    control_index = obj.controls.index(request.control) + 1
    control_key = f"D{control_index}"
    lines = condition_commands(obj, fixed, outputs)
    for index, line in enumerate(lines):
        if line.startswith("A A "):
            lines[index] = f"A C {request.target_cl:.15g}"
        elif line.startswith(f"{control_key} {control_key} "):
            lines[index] = f"{control_key} PM {request.target_cm:.15g}"
    return "\n".join(startup_commands() + lines + ["", "QUIT", ""])


def residuals(total, request):
    return {
        "CL": total["fields"]["CLtot"] - request.target_cl,
        "Cm": total["fields"]["Cmtot"] - request.target_cm,
    }


def residuals_accepted(values, request):
    return abs(values["CL"]) <= request.cl_tolerance and abs(values["Cm"]) <= request.cm_tolerance


def run_trim(
    runner, model_path, request, references, case_name, timeout_seconds, length_unit, outputs
):
    if not math.isfinite(timeout_seconds) or not 0.1 <= timeout_seconds <= 600:
        raise AVLFailure("INVALID_TIMEOUT", "Timeout must be between 0.1 and 600 seconds.")
    obj = inspect_geometry(model_path, runner.input_root)
    if request.control in request.fixed_controls:
        raise AVLFailure(
            "TRIM_CONTROL_CONFLICT", "The trim CONTROL cannot also have a fixed value."
        )
    fixed = FlightCondition(
        mach=request.mach, controls=request.fixed_controls | {request.control: 0}
    )
    selected = validate_request(
        runner, obj, [fixed], references, outputs, length_unit, timeout_seconds
    )
    job = runner._new_job(case_name)
    deadline = time.monotonic() + timeout_seconds
    trim = {
        "schema_version": "1.0.0",
        "request": request.model_dump(mode="json"),
        "solved_condition": None,
        "residuals": None,
        "within_bounds": None,
        "native_converged": False,
        "accepted": False,
        "bounds_semantics": "Post-solve acceptance, not bounds on native iterations",
        "verification": {"status": "not_run"},
        "scope": "CL/Cm constraints at zero beta and body rates; moment reference is not "
        "an inferred CG. No weight/thrust balance, stability certification "
        "or unique-root guarantee.",
    }
    manifest = {
        "package_version": __version__,
        "analysis_type": "longitudinal_trim",
        "trim_request": request.model_dump(mode="json"),
        "model": obj.report(),
        "fixed_condition": fixed.model_dump(),
        "outputs": selected,
        "references_override": references.model_dump() if references else None,
        "geometry_length_unit": length_unit,
        "case_name": case_name,
        "timeout_seconds": timeout_seconds,
        "run_directory": str(job),
    }
    result = {"success": False}
    verify_folder = job / "verification"

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise AVLFailure("TIMEOUT", "The total trim and verification time budget expired.")
        return value

    try:
        manifest.update(runner._stage(obj, job, references))
        dump(job / "manifest.json", manifest)
        process = runner._process(job, trim_commands(obj, request, fixed, selected), remaining())
        total = parse_total(job / "total.mrf")
        # Never infer fixed inputs from the candidate output. Release only alpha/control.
        check_total(total, obj, fixed, references, free_alpha=True, free_control=request.control)
        solved = fixed.model_dump()
        solved["alpha_deg"] = total["fields"]["Alpha"]
        solved["controls"] = dict(total["controls"])
        trim["solved_condition"] = solved
        trim["residuals"] = residuals(total, request)
        alpha_min, alpha_max = request.alpha_bounds_deg
        control_min, control_max = request.control_bounds
        trim["within_bounds"] = (
            alpha_min <= solved["alpha_deg"] <= alpha_max
            and control_min <= solved["controls"][request.control] <= control_max
        )
        # A fresh native solve produced finite output without any native failure marker.
        trim["native_converged"] = True
        if not trim["within_bounds"]:
            raise AVLFailure(
                "TRIM_OUT_OF_BOUNDS", "Native solution is outside the requested acceptance bounds."
            )
        if not residuals_accepted(trim["residuals"], request):
            raise AVLFailure(
                "TRIM_RESIDUAL", "Native CL/Cm residual exceeds the requested tolerance."
            )
        condition = FlightCondition(**solved)
        candidate = collect_case(job, obj, condition, references, selected, length_unit)
        candidate.update(solver=process)
        # Retain the native candidate separately even if verification subsequently fails.
        dump(job / "candidate.json", candidate)
        runner._check_sources(manifest["sources"])

        verify_folder.mkdir()
        trim["verification"] = {"status": "running", "run_directory": str(verify_folder)}
        verify_manifest = runner._stage(obj, verify_folder, references)
        verify_manifest.update(
            condition=condition.model_dump(),
            package_version=__version__,
            outputs=selected,
            purpose="Independent prescribed-condition replay of native trim",
        )
        dump(verify_folder / "manifest.json", verify_manifest)
        verification_process = runner._process(
            verify_folder, runner._commands(obj, condition, selected), remaining()
        )
        verified = collect_case(verify_folder, obj, condition, references, selected, length_unit)
        verified["solver"] = verification_process
        dump(verify_folder / "result.json", verified)
        differences = {
            name: verified["total"]["fields"][name] - value
            for name, value in total["fields"].items()
            if value is not None and verified["total"]["fields"].get(name) is not None
        }
        mismatches = [
            name
            for name in differences
            if not math.isclose(
                total["fields"][name],
                verified["total"]["fields"][name],
                rel_tol=1e-7,
                abs_tol=1e-9,
            )
        ]
        verification_residuals = residuals(verified["total"], request)
        trim["verification"].update(
            residuals=verification_residuals,
            total_differences=differences,
            comparison_tolerances={"relative": 1e-7, "absolute": 1e-9},
            result_json=str(verify_folder / "result.json"),
        )
        if mismatches or not residuals_accepted(verification_residuals, request):
            raise AVLFailure(
                "TRIM_VERIFICATION_FAILED",
                "A fresh prescribed solve did not reproduce the trim.",
                mismatched_fields=mismatches,
            )
        runner._check_sources(manifest["sources"])
        # The complete operation, including replay, is covered by one budget.
        remaining()
        trim["verification"]["status"] = "passed"
        trim["accepted"] = True
        result = candidate | {"source_preserved": True}
    except AVLFailure as exc:
        result = exc.result()
    except (OSError, UnicodeError, ValueError, IndexError, KeyError) as exc:
        result = AVLFailure("EXECUTION_OR_PARSE_ERROR", str(exc)).result()
    if not result["success"] and trim["verification"]["status"] == "running":
        trim["verification"].update(status="failed", error=result["error"])
    result.update(
        job_id=job.name, run_directory=str(job), analysis_type="longitudinal_trim", trim=trim
    )
    # Always link the available raw evidence and failed attempts as well as successful results.
    result.setdefault("artifacts", {}).update(
        {str(path.relative_to(job)): str(path) for path in job.rglob("*") if path.is_file()}
    )
    result["artifacts"]["result.json"] = str(job / "result.json")
    dump(job / "result.json", result)
    return result
