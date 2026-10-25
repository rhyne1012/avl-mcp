"""Prescribed-condition mesh sensitivity with unchanged source geometry and evidence.

AVL's active SURFACE chord/span and SECTION interval counts are refined. BODY
stations and spacing parameters stay fixed; sensitivity is not a convergence proof.
"""

import copy
import csv
import math
import re
import time
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import __version__
from .execution import collect_case, validate_request
from .geometry import Geometry, inspect_geometry
from .models import AVLFailure, FlightCondition
from .runner import dump

MeshMetric = Literal["CLtot", "CDtot", "CDff", "Cmtot", "CYtot", "Cltot", "Cntot"]
MeshFactor = Annotated[int, Field(strict=True, ge=1, le=100)]
SCOPE = (
    "Sensitivity at one fixed prescribed condition, with unchanged geometry, spacing, "
    "controls, Mach and reference quantities. No mesh convergence, aerodynamic validity "
    "or experimental agreement is certified. BODY station counts are unchanged."
)


class MeshSensitivityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    condition: FlightCondition = Field(default_factory=FlightCondition)
    factors: list[MeshFactor] = Field(default_factory=lambda: [1, 2, 3], min_length=2, max_length=8)
    metrics: list[MeshMetric] = Field(
        default_factory=lambda: ["CLtot", "CDtot", "CDff", "Cmtot"], min_length=1, max_length=7
    )
    absolute_tolerances: dict[MeshMetric, Annotated[float, Field(ge=0)]] = Field(
        default_factory=dict,
        description="Optional coefficient-difference limits for the final adjacent pair only.",
    )

    @model_validator(mode="after")
    def ordered_levels(self):
        if self.factors[0] != 1 or any(a >= b for a, b in zip(self.factors, self.factors[1:])):
            raise ValueError("Factors must start at 1 and increase strictly.")
        if len(set(self.metrics)) != len(self.metrics):
            raise ValueError("Metrics must be unique.")
        if set(self.absolute_tolerances) - set(self.metrics):
            raise ValueError("Absolute tolerances must refer to requested metrics.")
        return self


def refine_geometry(obj: Geometry, factor: int) -> Geometry:
    """Copy parsed geometry, replacing only the count tokens AVL actually uses."""
    if isinstance(factor, bool) or not isinstance(factor, int) or factor < 1:
        raise AVLFailure("INVALID_MESH_FACTOR", "Refinement factor must be a positive integer.")
    refined = copy.deepcopy(obj)
    for line_index, positions in obj.discretization_lines.items():
        line = refined.raw_lines[line_index]
        tokens = list(re.finditer(r"\S+", line))
        for position in sorted(positions, reverse=True):
            token = tokens[position]
            count = int(float(token.group().replace("D", "E").replace("d", "e")))
            line = line[: token.start()] + str(count * factor) + line[token.end() :]
        refined.raw_lines[line_index] = line
    for surface in refined.surfaces:
        surface["n_chord"] *= factor
        if "n_span" in surface:
            surface["n_span"] *= factor
        else:
            for section in surface["sections"][:-1]:
                section["n_span"] *= factor
    refined.panel_estimate *= factor**2
    return refined


def coefficient_deltas(values, reference):
    result = {}
    for metric, value in values.items():
        signed = value - reference[metric]
        if not math.isfinite(signed):
            raise AVLFailure(
                "NONFINITE_MESH_DELTA", f"Coefficient difference overflows for {metric}."
            )
        denominator = abs(reference[metric])
        relative = abs(signed) / denominator if denominator else None
        relative_status = "defined" if denominator else "undefined_zero_reference"
        if relative is not None and not math.isfinite(relative):
            relative, relative_status = None, "undefined_overflow"
        result[metric] = {
            "signed": signed,
            "absolute": abs(signed),
            "relative": relative,
            "relative_status": relative_status,
        }
    return result


def _tolerance_check(levels, request, valid=True):
    complete = valid and all(level["success"] is True for level in levels)
    evaluated = complete and bool(request.absolute_tolerances)
    metrics = {}
    if evaluated:
        for name, tolerance in request.absolute_tolerances.items():
            delta = levels[-1]["adjacent_deltas"][name]["absolute"]
            metrics[name] = {
                "absolute_delta": delta,
                "tolerance": tolerance,
                "within_tolerance": delta <= tolerance,
            }
    return {
        "evaluated": evaluated,
        "comparison": "Final requested level minus its preceding requested level only.",
        "absolute_tolerances": request.absolute_tolerances,
        "within_tolerance": all(m["within_tolerance"] for m in metrics.values())
        if evaluated
        else None,
        "metrics": metrics,
        "scope": "User-supplied sensitivity limits; passing is not a convergence certificate.",
    }


def _write_reports(job, result, request):
    columns = ["factor", "status", "estimated_vortices", "actual_vortices", "run_directory"]
    for metric in request.metrics:
        columns.extend(
            [metric]
            + [
                f"{metric}_{which}_{kind}"
                for which in ("baseline", "adjacent")
                for kind in ("signed", "absolute", "relative", "relative_status")
            ]
        )
    columns.append("error_code")
    with (job / "summary.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for level in result["levels"]:
            row = {key: level.get(key) for key in columns[:3]}
            row.update(
                actual_vortices=level.get("counts", {}).get("vortices"),
                run_directory=level.get("run_directory"),
                error_code=level.get("error", {}).get("code"),
            )
            for metric in request.metrics:
                row[metric] = level.get("coefficients", {}).get(metric)
                for which in ("baseline", "adjacent"):
                    for kind, value in level.get(f"{which}_deltas", {}).get(metric, {}).items():
                        row[f"{metric}_{which}_{kind}"] = value
            writer.writerow(row)
    lines = [
        "# Mesh sensitivity report",
        "",
        SCOPE,
        "",
        f"Completed levels: {result['completed_count']}/{result['requested_count']}",
        "",
        "Relative deltas are absolute changes divided by the absolute reference "
        "coefficient. A zero reference gives an undefined relative delta (JSON null; "
        "empty CSV cell), including zero-to-zero changes. An overflowing relative ratio "
        "is also null, with relative_status undefined_overflow.",
        "",
        "| Factor | Status | Vortices | " + " | ".join(request.metrics) + " |",
        "| --- | --- | --- | " + " | ".join("---" for _ in request.metrics) + " |",
    ]
    for level in result["levels"]:
        values = [
            format(level.get("coefficients", {}).get(metric), ".9g")
            if metric in level.get("coefficients", {})
            else "—"
            for metric in request.metrics
        ]
        lines.append(
            f"| {level['factor']} | {level['status']} | "
            f"{level.get('counts', {}).get('vortices', '—')} | " + " | ".join(values) + " |"
        )
    lines += [
        "",
        "## Absolute changes from baseline and preceding level",
        "",
        "| Factor | Metric | From baseline | From preceding level |",
        "| --- | --- | --- | --- |",
    ]
    for level in result["levels"]:
        if not level["success"]:
            continue
        for metric in request.metrics:
            baseline = level["baseline_deltas"][metric]["absolute"]
            adjacent = level["adjacent_deltas"].get(metric, {}).get("absolute")
            lines.append(
                f"| {level['factor']} | {metric} | {baseline:.9g} | "
                + (f"{adjacent:.9g}" if adjacent is not None else "—")
                + " |"
            )
    check = result["last_adjacent_tolerance_check"]
    lines += ["", "## Explicit sensitivity limits", ""]
    if check["evaluated"]:
        for name, metric in check["metrics"].items():
            lines.append(
                f"- {name}: final adjacent absolute change "
                f"{metric['absolute_delta']:.9g}; supplied limit {metric['tolerance']:.9g}; "
                + ("within limit." if metric["within_tolerance"] else "exceeds limit.")
            )
    else:
        lines.append(
            "No limits evaluated: explicit tolerances and all completed levels are required."
        )
    lines += [
        "",
        check["scope"],
        "",
        "## Inputs and evidence",
        "",
        f"- Condition: `{request.condition.model_dump_json()}`",
        f"- Geometry length unit: `{result['geometry_length_unit']}`",
        f"- Reference quantities: `{result['references']}`",
        f"- BODY discretization: unchanged ({len(result['body_discretization'])} bodies).",
        "- Per-level model.avl, native commands, stdout/stderr, process metadata and "
        "result.json are retained in each level directory.",
        "- Original geometry and dependencies are retained in inputs/originals/.",
    ]
    if result.get("error"):
        lines += ["", f"Stopped: {result['error']['code']}: {result['error']['message']}"]
    (job / "report.md").write_text("\n".join(lines) + "\n")
    dump(job / "result.json", result)


def run_mesh_sensitivity(
    runner,
    model_path,
    request: MeshSensitivityRequest,
    references=None,
    case_name="mesh-sensitivity",
    timeout_seconds=300.0,
    length_unit="unspecified",
):
    """Run independently staged meshes against one condition and one overall deadline."""
    obj = inspect_geometry(model_path, runner.input_root)
    selected = validate_request(
        runner, obj, [request.condition], references, ["total"], length_unit, timeout_seconds
    )
    geometries = [refine_geometry(obj, factor) for factor in request.factors]
    # All requested levels must fit before any execution or output directory is created.
    for geometry in geometries:
        runner._check_model(geometry)
    job = runner._new_job(case_name)
    started = time.monotonic()
    deadline = started + timeout_seconds
    sources = {str(obj.path): obj.source_hash} | {d["path"]: d["sha256"] for d in obj.dependencies}
    levels = [
        {
            "factor": factor,
            "estimated_vortices": geometry.panel_estimate,
            "status": "not_run",
            "success": None,
        }
        for factor, geometry in zip(request.factors, geometries)
    ]
    result = {
        "success": False,
        "schema_version": "1.0.0",
        "analysis_type": "mesh_sensitivity",
        "scope": SCOPE,
        "job_id": job.name,
        "run_directory": str(job),
        "request": request.model_dump(),
        "references": references.model_dump() if references else obj.references,
        "geometry_length_unit": length_unit,
        "sources": sources,
        "source_preserved": False,
        "body_discretization": [
            {"name": body["name"], "n_body": body["n_body"], "refined": False}
            for body in obj.bodies
        ],
        "levels": levels,
        "requested_count": len(levels),
        "summary_csv": str(job / "summary.csv"),
        "report_markdown": str(job / "report.md"),
        "result_json": str(job / "result.json"),
        "artifacts": {
            name: str(job / name) for name in ("result.json", "summary.csv", "report.md")
        },
    }

    def remaining():
        runner._check_sources(sources)
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise AVLFailure("TIMEOUT", "Mesh sensitivity execution time budget reached.")
        return seconds

    active = None
    try:
        remaining()
        inputs = job / "inputs"
        inputs.mkdir()
        snapshot = runner._stage(obj, inputs, references)
        dump(
            job / "manifest.json",
            {
                "package_version": __version__,
                "model": obj.report(),
                "request": request.model_dump(),
                "references_override": references.model_dump() if references else None,
                "geometry_length_unit": length_unit,
                "total_timeout_seconds": timeout_seconds,
                "refinement": "Multiply active lifting-surface chord and span counts; "
                "BODY unchanged.",
                **snapshot,
            },
        )
        for index, (level, geometry) in enumerate(zip(levels, geometries)):
            remaining()
            active = level
            folder = job / f"level-{index:02d}-factor-{level['factor']}"
            folder.mkdir()
            level["run_directory"] = str(folder)
            level["status"] = "running"
            staged = runner._stage(geometry, folder, references)
            level["staged_geometry_sha256"] = staged["staged_geometry_sha256"]
            dump(
                folder / "manifest.json",
                {
                    "factor": level["factor"],
                    "estimated_vortices": geometry.panel_estimate,
                    "condition": request.condition.model_dump(),
                    "references": result["references"],
                    "geometry_length_unit": length_unit,
                    **staged,
                },
            )
            process = runner._process(
                folder, runner._commands(geometry, request.condition, selected), remaining()
            )
            remaining()
            case = collect_case(
                folder, geometry, request.condition, references, selected, length_unit
            )
            remaining()
            # AVL can clip NC/NS counts at its internal array limits and still write
            # a valid force file. Such a level is not the mesh that was requested.
            level["counts"] = case["total"]["counts"]
            if level["counts"]["vortices"] != geometry.panel_estimate:
                raise AVLFailure(
                    "MESH_COUNT_MISMATCH",
                    "AVL's actual vortex count does not match the requested refined mesh.",
                    expected_vortices=geometry.panel_estimate,
                    actual_vortices=level["counts"]["vortices"],
                )
            case.update(solver=process, source_preserved=True)
            case["artifacts"].update(
                {
                    name: str(folder / name)
                    for name in (
                        "model.avl",
                        "manifest.json",
                        "commands.txt",
                        "stdout.log",
                        "stderr.log",
                        "process.json",
                        "result.json",
                    )
                }
            )
            coefficients = {metric: case["total"]["fields"][metric] for metric in request.metrics}
            if not all(math.isfinite(value) for value in coefficients.values()):
                raise AVLFailure("OUTPUT_FORMAT", "Mesh coefficients must be finite.")
            dump(folder / "result.json", case)
            level.update(
                success=True,
                status="success",
                coefficients=coefficients,
                counts=case["total"]["counts"],
                artifacts=case["artifacts"],
                baseline_deltas=coefficient_deltas(
                    coefficients, levels[0].get("coefficients", coefficients)
                ),
                adjacent_deltas=coefficient_deltas(coefficients, levels[index - 1]["coefficients"])
                if index
                else {},
            )
            active = None
        remaining()
        result["success"] = True
    except (AVLFailure, OSError, UnicodeError, ValueError, IndexError, KeyError) as exc:
        failure = (
            exc if isinstance(exc, AVLFailure) else AVLFailure("EXECUTION_OR_PARSE_ERROR", str(exc))
        )
        error = failure.result()["error"]
        result["error"] = error
        if active is not None:
            active.update(status="failed", success=False, error=error)
            if "run_directory" in active:
                folder = Path(active["run_directory"])
                active["artifacts"] = {
                    path.name: str(path) for path in folder.iterdir() if path.is_file()
                }
                active["artifacts"]["result.json"] = str(folder / "result.json")
                dump(folder / "result.json", active)
    try:
        runner._check_sources(sources)
        result["source_preserved"] = True
    except (AVLFailure, OSError) as exc:
        failure = (
            exc if isinstance(exc, AVLFailure) else AVLFailure("EXECUTION_OR_IO_ERROR", str(exc))
        )
        result.update(success=False, error=failure.result()["error"])
    result.update(
        attempted_count=sum(level["success"] is not None for level in levels),
        completed_count=sum(level["success"] is True for level in levels),
        elapsed_seconds=time.monotonic() - started,
        last_adjacent_tolerance_check=_tolerance_check(levels, request, result["success"]),
    )
    if (job / "manifest.json").is_file():
        result["artifacts"]["manifest.json"] = str(job / "manifest.json")
    _write_reports(job, result, request)
    return result
