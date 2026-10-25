"""Ordered trim batches with fresh independent replay and durable per-point results."""

import csv
import time
import uuid

from .execution import validate_request
from .geometry import inspect_geometry
from .models import AVLFailure
from .runner import dump
from .trim import fixed_condition, run_trim, trim_metadata


def batch_mode(conditions, trim_requests):
    if (conditions is None) == (trim_requests is None):
        raise AVLFailure(
            "BATCH_INPUT_REQUIRED", "Supply exactly one of conditions or trim_requests."
        )
    return "longitudinal_trim_batch" if trim_requests is not None else "prescribed"


def validate_trim_requests(runner, obj, requests, references, outputs, length_unit, timeout):
    if not 1 <= len(requests) <= 10000:
        raise AVLFailure("SWEEP_LIMIT", "Supply 1-10000 explicit trim requests.")
    return validate_request(
        runner,
        obj,
        [fixed_condition(r) for r in requests],
        references,
        outputs,
        length_unit,
        timeout,
    )


def write_trim_summary(folder, result):
    """Write candidate and replay residuals even when a point was rejected."""
    fields = [
        "index",
        "success",
        "target_cl",
        "target_cm",
        "Mach",
        "alpha_deg",
        "control",
        "control_value",
        "residual_cl",
        "residual_cm",
        "verification_status",
        "verified_residual_cl",
        "verified_residual_cm",
        "run_directory",
        "error_code",
    ]
    path = folder / "summary.csv"
    temporary = folder / ("summary-" + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for item in result["results"]:
                trim = item["trim"]
                request = trim["request"]
                solved = trim["solved_condition"] or {}
                residual = trim["residuals"] or {}
                verification = trim["verification"]
                verified = verification.get("residuals", {})
                writer.writerow(
                    {
                        "index": item["index"],
                        "success": item["success"],
                        "target_cl": request["target_cl"],
                        "target_cm": request["target_cm"],
                        "Mach": request["mach"],
                        "alpha_deg": solved.get("alpha_deg"),
                        "control": request["control"],
                        "control_value": solved.get("controls", {}).get(request["control"]),
                        "residual_cl": residual.get("CL"),
                        "residual_cm": residual.get("Cm"),
                        "verification_status": verification["status"],
                        "verified_residual_cl": verified.get("CL"),
                        "verified_residual_cm": verified.get("Cm"),
                        "run_directory": item.get("run_directory"),
                        "error_code": item.get("error", {}).get("code"),
                    }
                )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    result["summary_csv"] = str(path)


def execute_trim_cases(
    runner,
    folder,
    obj,
    requests,
    references,
    outputs,
    length_unit,
    timeout,
    stop_on_error=True,
    completed=None,
    on_case=None,
    cancelled=None,
):
    """A single attempt budget includes all candidates and independent replays.

    Trim points deliberately do not reuse a native session or reorder by Mach.
    Only accepted, independently verified checkpoints are supplied in completed.
    """
    results = dict(completed or {})
    deadline = time.monotonic() + timeout
    error = None
    for index, request in enumerate(requests):
        if index in results:
            continue
        point = None
        try:
            if cancelled and cancelled():
                raise AVLFailure("CANCELLED", "Cancellation requested.")
            if time.monotonic() >= deadline:
                raise AVLFailure("TIMEOUT", "Batch execution time budget reached.")
            point = folder / f"trim-{index:05d}-{uuid.uuid4().hex}"
            point.mkdir()
            item = run_trim(
                runner,
                str(obj.path),
                request,
                references,
                "trim",
                timeout,
                length_unit,
                outputs,
                folder=point,
                geometry=obj,
                deadline=deadline,
                cancelled=cancelled,
            )
        except (AVLFailure, OSError, UnicodeError, ValueError, IndexError, KeyError) as exc:
            failure = (
                exc
                if isinstance(exc, AVLFailure)
                else AVLFailure("EXECUTION_OR_PARSE_ERROR", str(exc))
            )
            item = failure.result() | {
                "analysis_type": "longitudinal_trim",
                "trim": trim_metadata(request),
            }
            if point is not None:
                item["run_directory"] = str(point)
        item["index"] = index
        if point is not None:
            # Index is part of the immutable per-point evidence hashed by the job manager.
            dump(point / "result.json", item)
        results[index] = item
        if not item["success"]:
            error = item["error"]
        if on_case:
            on_case(index, item)
        if not item["success"] and (
            stop_on_error or error["code"] in ("TIMEOUT", "CANCELLED", "SOURCE_CHANGED")
        ):
            break
    complete = len(results) == len(requests) and all(item["success"] for item in results.values())
    result = {
        "success": complete,
        "analysis_type": "longitudinal_trim_batch",
        "requested_count": len(requests),
        "attempted_count": len(results),
        "completed_count": sum(item["success"] for item in results.values()),
        "results": [results[index] for index in sorted(results)],
        "run_directory": str(folder),
    }
    if not complete:
        result["error"] = error or {"code": "PARTIAL_FAILURE", "message": "Some cases failed."}
    write_trim_summary(folder, result)
    return result


def sweep_trim(
    runner,
    model_path,
    requests,
    references,
    case_name,
    timeout_seconds,
    length_unit,
    stop_on_error,
    outputs,
):
    # Local import avoids the runner/jobs import cycle, while sharing atomic checkpoints.
    from .jobs import atomic_json, read_json

    obj = inspect_geometry(model_path, runner.input_root)
    selected = validate_trim_requests(
        runner,
        obj,
        requests,
        references,
        outputs,
        length_unit,
        timeout_seconds,
    )
    job = runner._new_job(case_name)
    snapshot = job / "inputs"
    snapshot.mkdir()
    staged = runner._stage(obj, snapshot, references)
    atomic_json(
        job / "manifest.json",
        {
            "analysis_type": "longitudinal_trim_batch",
            "model_path": str(obj.path),
            **staged,
            "trim_requests": [r.model_dump(mode="json") for r in requests],
            "outputs": selected,
            "references_override": references.model_dump() if references else None,
            "length_unit": length_unit,
            "total_timeout_seconds": timeout_seconds,
        },
    )
    snapshot_obj = inspect_geometry(str(snapshot / "model.avl"))
    started = time.monotonic()

    def checkpoint(index, item):
        runner._check_sources(staged["sources"])
        atomic_json(job / f"case-{index:05d}.json", item)

    try:
        result = execute_trim_cases(
            runner,
            job,
            snapshot_obj,
            requests,
            references,
            selected,
            length_unit,
            timeout_seconds,
            stop_on_error,
            on_case=checkpoint,
        )
        runner._check_sources(staged["sources"])
    except (AVLFailure, OSError) as exc:
        failure = (
            exc if isinstance(exc, AVLFailure) else AVLFailure("EXECUTION_OR_IO_ERROR", str(exc))
        )
        items = [read_json(path) for path in sorted(job.glob("case-*.json"))]
        result = failure.result() | {
            "analysis_type": "longitudinal_trim_batch",
            "run_directory": str(job),
            "results": items,
            "requested_count": len(requests),
            "attempted_count": len(items),
            "completed_count": sum(item["success"] for item in items),
        }
        write_trim_summary(job, result)
    result.update(job_id=job.name, elapsed_seconds=time.monotonic() - started)
    atomic_json(job / "result.json", result)
    return result
