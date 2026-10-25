"""Verify all fourteen tools using an installed MCP command and official cases."""

import argparse
import asyncio
import csv
import hashlib
import json
import os
import subprocess
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from avl_mcp import __version__


def source_fingerprints(model_path):
    """Retain independent hashes for the fixture and its local supporting files."""
    return {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(model_path.resolve().parent.rglob("*"))
        if path.is_file()
    }


async def recorded_call(session, records, name, arguments):
    result = await session.call_tool(name, arguments)
    records.append({
        "tool": name, "arguments": arguments, "response": result.model_dump(mode="json"),
    })
    return result


async def verify_mesh_sensitivity(session, records, args):
    before = source_fingerprints(args.vanilla)
    arguments = {
        "model_path": str(args.vanilla.resolve()),
        "request": {
            "condition": {"alpha_deg": 3, "mach": 0.2},
            "factors": [1, 2],
            "absolute_tolerances": {"CLtot": 0.01, "Cmtot": 0.01},
        },
        "case_name": "stdio-vanilla-mesh-sensitivity",
    }
    response = await recorded_call(session, records, "avl.mesh_sensitivity", arguments)
    assert not response.isError, response
    report = response.structuredContent
    assert report["success"] and report["analysis_type"] == "mesh_sensitivity", report
    assert report["requested_count"] == report["completed_count"] == 2, report
    levels = report["levels"]
    assert [level["factor"] for level in levels] == [1, 2], levels
    assert [level["counts"]["vortices"] for level in levels] == [294, 1176], levels
    assert [level["estimated_vortices"] for level in levels] == [294, 1176], levels
    assert all(level["success"] and level["status"] == "success" for level in levels), levels
    baseline, refined = levels
    assert abs(baseline["coefficients"]["CLtot"] - 0.6754170403902354) < 1e-8, baseline
    assert refined["adjacent_deltas"] == refined["baseline_deltas"], refined
    for metric in ("CLtot", "CDtot", "CDff", "Cmtot"):
        expected = refined["coefficients"][metric] - baseline["coefficients"][metric]
        delta = refined["baseline_deltas"][metric]
        assert abs(delta["signed"] - expected) < 1e-12, delta
        assert abs(delta["absolute"] - abs(expected)) < 1e-12, delta
    assert refined["baseline_deltas"]["CLtot"]["absolute"] > 0.001, refined
    assert refined["baseline_deltas"]["Cmtot"]["absolute"] > 0.02, refined
    tolerance = report["last_adjacent_tolerance_check"]
    assert tolerance["evaluated"] and tolerance["within_tolerance"] is False, tolerance
    assert tolerance["metrics"]["CLtot"]["within_tolerance"] is True, tolerance
    assert tolerance["metrics"]["Cmtot"]["within_tolerance"] is False, tolerance
    for key in ("summary_csv", "report_markdown", "result_json"):
        assert Path(report[key]).is_file() and Path(report[key]).stat().st_size > 0, report
    saved = json.loads(Path(report["result_json"]).read_text())
    assert saved["levels"] == levels, saved
    with Path(report["summary_csv"]).open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert [int(row["factor"]) for row in rows] == [1, 2], rows
    for row, level in zip(rows, levels, strict=True):
        assert int(row["actual_vortices"]) == level["counts"]["vortices"], row
        for metric in ("CLtot", "CDtot", "CDff", "Cmtot"):
            assert float(row[metric]) == level["coefficients"][metric], row
            assert (
                float(row[f"{metric}_baseline_absolute"])
                == level["baseline_deltas"][metric]["absolute"]
            ), row
    assert report["source_preserved"] is True, report
    assert source_fingerprints(args.vanilla) == before
    for path, fingerprint in report["sources"].items():
        assert before[path] == fingerprint, report
    invalid = await recorded_call(
        session, records, "avl.mesh_sensitivity",
        {**arguments, "request": {**arguments["request"], "factors": [1, 1]}},
    )
    assert invalid.isError, invalid
    return {"report": report, "summary_csv_rows": rows, "source_hashes_unchanged": True}


async def verify_physical_trim(session, records, args, trim_request):
    before = source_fingerprints(args.vanilla)
    # Synthetic dimensional fixture: declaring Vanilla lengths as metres here is
    # an acceptance-test assumption, not a claim about the official model's mass.
    weight_n = 0.6 * 0.5 * 1.225 * 30**2 * 9
    request = {
        key: value for key, value in trim_request.items() if key not in ("target_cl", "target_cm")
    }
    request.update({
        "mass_kg": weight_n / 9.80665,
        "density_kg_m3": 1.225,
        "speed_m_s": 30,
        "geometry_length_unit": "m",
        "cg": {"x": 0.6, "y": 0, "z": 0.03, "length_unit": "m"},
    })
    arguments = {"model_path": str(args.vanilla.resolve()), "request": request}
    prepared = await recorded_call(session, records, "avl.prepare_trim", arguments)
    assert not prepared.isError, prepared
    preparation = prepared.structuredContent
    assert preparation["success"], preparation
    assert preparation["solver_executed"] is False, preparation
    coefficient_request = preparation["trim_request"]
    assert abs(coefficient_request["target_cl"] - 0.6) < 1e-12, preparation
    assert coefficient_request["target_cm"] == 0 and coefficient_request["mach"] == 0.2
    references = preparation["references"]
    assert references == {"sref": 9, "cref": 0.9, "bref": 10, "xref": 0.6,
                          "yref": 0, "zref": 0.03}, references
    provenance = preparation["physical_trim"]
    conversion = provenance["conversion"]
    assert conversion["reference_area_m2"] == 9, conversion
    assert abs(conversion["dynamic_pressure_pa"] - 551.25) < 1e-12, conversion
    assert abs(conversion["weight_n"] - weight_n) < 1e-10, conversion
    assert conversion["cg_native"] == {"x": 0.6, "y": 0, "z": 0.03}, conversion
    physical = await recorded_call(session, records, "avl.trim_physical", {
        **arguments, "case_name": "stdio-vanilla-physical-trim", "outputs": ["total"],
    })
    assert not physical.isError, physical
    result = physical.structuredContent
    assert_accepted_trim(result, coefficient_request)
    assert result["physical_trim"] == provenance, result
    run_directory = Path(result["run_directory"])
    saved = json.loads((run_directory / "result.json").read_text())
    assert saved["physical_trim"] == provenance, saved
    assert saved["trim"]["verification"]["status"] == "passed", saved
    assert json.loads((run_directory / "physical-trim.json").read_text()) == provenance
    query_fields = [
        "physical_trim.conversion.target_cl",
        "physical_trim.conversion.reference_area_m2",
        "physical_trim.conversion.cg_native.x",
        "physical_trim.conversion.weight_n",
        "trim.verification.status",
        "total.fields.CLtot",
    ]
    query = await recorded_call(session, records, "avl.results", {
        "job_id": result["job_id"], "fields": query_fields,
    })
    assert not query.isError, query
    saved_query = query.structuredContent
    assert saved_query["solver_executed"] is False and len(saved_query["rows"]) == 1
    fields = saved_query["rows"][0]["fields"]
    for field in query_fields:
        expected = result
        for part in field.split("."):
            expected = expected[part]
        assert fields[field] == expected, fields
    invalid = await recorded_call(session, records, "avl.prepare_trim", {
        **arguments, "request": {**request, "weight_n": weight_n},
    })
    assert invalid.isError, invalid
    assert source_fingerprints(args.vanilla) == before
    return {
        "preparation": preparation,
        "result": result,
        "saved_query": saved_query,
        "source_hashes_unchanged": True,
        "fixture_assumption": "Vanilla lengths are declared as metres for this synthetic test.",
    }


def assert_accepted_trim(result, request):
    """Require evidence from both the native trim and independent replay."""
    assert result["success"], result
    assert result["analysis_type"] == "longitudinal_trim", result
    trim = result["trim"]
    assert trim["accepted"] and trim["within_bounds"] and trim["native_converged"], trim
    assert trim["verification"]["status"] == "passed", trim
    assert result["condition"] == trim["solved_condition"], trim
    solved = trim["solved_condition"]
    assert solved["mach"] == request["mach"], trim
    assert request["alpha_bounds_deg"][0] <= solved["alpha_deg"] <= request["alpha_bounds_deg"][1]
    control_value = solved["controls"][request["control"]]
    assert request["control_bounds"][0] <= control_value <= request["control_bounds"][1]
    for control, value in request["fixed_controls"].items():
        assert solved["controls"][control] == value, trim
    for name, tolerance in (("CL", "cl_tolerance"), ("Cm", "cm_tolerance")):
        assert abs(trim["residuals"][name]) <= request[tolerance], trim
        assert abs(trim["verification"]["residuals"][name]) <= request[tolerance], trim
    for name in ("target_cl", "target_cm", "mach", "control"):
        assert trim["request"][name] == request[name], trim


def assert_trim_summary(path, requests, results):
    """Check the exported CSV against saved per-case evidence, including failures."""
    with Path(path).open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == len(requests), rows
    for index, (row, request, result) in enumerate(zip(rows, requests, results, strict=True)):
        assert int(row["index"]) == index, row
        assert row["success"].lower() == str(result["success"]).lower(), row
        assert float(row["target_cl"]) == request["target_cl"], row
        assert float(row["target_cm"]) == request["target_cm"], row
        assert float(row["Mach"]) == request["mach"], row
        assert row["control"] == request["control"], row
        trim = result["trim"]
        assert row["verification_status"] == trim["verification"]["status"], row
        if result["success"]:
            solved = trim["solved_condition"]
            assert float(row["alpha_deg"]) == solved["alpha_deg"], row
            assert float(row["control_value"]) == solved["controls"][request["control"]], row
            for name, suffix in (("CL", "cl"), ("Cm", "cm")):
                assert float(row[f"residual_{suffix}"]) == trim["residuals"][name], row
                assert (
                    float(row[f"verified_residual_{suffix}"])
                    == trim["verification"]["residuals"][name]
                ), row
        else:
            assert row["error_code"] == result["error"]["code"], row
    return rows


async def resume_after_worker_exit(session, job_id):
    # A terminal status can be observed just before the worker releases its lock.
    for _ in range(100):
        resumed = await session.call_tool("avl.resume", {"job_id": job_id})
        if not resumed.isError:
            return resumed.structuredContent
        assert resumed.structuredContent["error"]["code"] == "JOB_BUSY", resumed
        await asyncio.sleep(0.025)
    raise AssertionError(f"Worker for {job_id} did not release its lock after cancellation.")


async def verify_background_trim(params, err, args, trim_request):
    requests = [
        {**trim_request, "target_cl": 0.4 + 0.025 * (i % 12), "mach": 0.1 + 0.05 * (i // 12)}
        for i in range(24)
    ]
    # Initialize the observer before submitting: interpreter startup must not consume
    # the cancellation window on a fast machine. Only the submitting MCP process exits.
    async with stdio_client(params, errlog=err) as (reader, writer):
        async with ClientSession(reader, writer) as observer:
            await observer.initialize()
            async with stdio_client(params, errlog=err) as (reader, writer):
                async with ClientSession(reader, writer) as submitter:
                    await submitter.initialize()
                    submitted = await submitter.call_tool(
                        "avl.submit",
                        {
                            "model_path": str(args.vanilla.resolve()),
                            "trim_requests": requests,
                            "outputs": ["total"],
                            "case_name": "stdio-background-trim",
                            "timeout_seconds": 120,
                        },
                    )
                    assert not submitted.isError, submitted
                    job_id = submitted.structuredContent["job_id"]
                    for _ in range(400):
                        status = await submitter.call_tool("avl.status", {"job_id": job_id})
                        assert not status.isError, status
                        state = status.structuredContent
                        assert state["state"] in ("queued", "running"), state
                        if state["completed_count"] > 0:
                            break
                        await asyncio.sleep(0.01)
                    assert state["state"] == "running", state
                    assert 0 < state["completed_count"] < len(requests), state
                    before_disconnect = state["completed_count"]
            status = await observer.call_tool("avl.status", {"job_id": job_id})
            assert not status.isError, status
            state = status.structuredContent
            assert state["state"] == "running", state
            assert before_disconnect <= state["completed_count"] < len(requests), state
            cancelled = await observer.call_tool("avl.cancel", {"job_id": job_id})
            assert not cancelled.isError, cancelled
            for _ in range(400):
                status = await observer.call_tool("avl.status", {"job_id": job_id})
                assert not status.isError, status
                state = status.structuredContent
                if state["state"] == "cancelled":
                    break
                assert state["state"] in ("queued", "running"), state
                await asyncio.sleep(0.025)
            assert state["state"] == "cancelled", state
            assert 0 < state["completed_count"] < len(requests), state
            cancelled_count = state["completed_count"]
            checkpoint_dir = Path(state["run_directory"]) / "cases"
            preserved = {
                path: (path.read_bytes(), path.stat().st_mtime_ns)
                for path in checkpoint_dir.glob("*.json")
                if json.loads(path.read_text())["result"]["success"]
            }
            assert len(preserved) == cancelled_count, state
            await resume_after_worker_exit(observer, job_id)
            for _ in range(2400):
                status = await observer.call_tool("avl.status", {"job_id": job_id})
                assert not status.isError, status
                state = status.structuredContent
                if state["state"] == "completed":
                    break
                assert state["state"] in ("queued", "running"), state
                await asyncio.sleep(0.05)
            assert state["state"] == "completed", state
            assert state["completed_count"] == len(requests) and state["failed_count"] == 0, state
            assert state["analysis_type"] == "longitudinal_trim_batch", state
            for path, (content, modified) in preserved.items():
                assert path.read_bytes() == content, path
                assert path.stat().st_mtime_ns == modified, path
            query = await observer.call_tool(
                "avl.results", {"job_id": job_id, "limit": len(requests)}
            )
            assert not query.isError, query
            saved = query.structuredContent
            assert saved["solver_executed"] is False and saved["next_offset"] is None, saved
            assert saved["matched_count"] == len(requests), saved
            assert [row["index"] for row in saved["rows"]] == list(range(len(requests))), saved
            for row, request in zip(saved["rows"], requests, strict=True):
                assert_accepted_trim(row, request)
            summary = assert_trim_summary(state["summary_csv"], requests, saved["rows"])
            return {
                "job_id": job_id,
                "requested_count": len(requests),
                "worker_survived_mcp_disconnect": True,
                "completed_before_disconnect": before_disconnect,
                "cancellation_acknowledged": True,
                "completed_at_cancellation": cancelled_count,
                "preserved_checkpoint_count": len(preserved),
                "final_status": state,
                "query": saved,
                "summary_csv_rows": summary,
            }


async def verify(args):
    version = subprocess.check_output([str(args.command), "--version"], text=True).strip()
    assert version == f"avl-mcp {__version__}", version
    root = args.work_root.resolve()
    (root / "reports").mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(exist_ok=True)
    params = StdioServerParameters(
        command=str(args.command.resolve()),
        args=["--avl-bin", str(args.avl_bin.resolve()), "--work-root", str(root)],
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    )
    records = []
    names = {
        "avl.health",
        "avl.inspect",
        "avl.validate",
        "avl.run",
        "avl.trim",
        "avl.mesh_sensitivity",
        "avl.prepare_trim",
        "avl.trim_physical",
        "avl.sweep",
        "avl.submit",
        "avl.status",
        "avl.cancel",
        "avl.resume",
        "avl.results",
    }
    trim_request = {
        "control": "elevator",
        "target_cl": 0.6,
        "target_cm": 0.0,
        "mach": 0.2,
        "alpha_bounds_deg": [-10, 15],
        "control_bounds": [-30, 30],
        "fixed_controls": {"flap": 2.0},
        "cl_tolerance": 1e-6,
        "cm_tolerance": 1e-6,
    }
    with (root / "logs/mcp-stderr.log").open("w") as err:
        async with stdio_client(params, errlog=err) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                initialized = await session.initialize()
                listing = await session.list_tools()
                assert {t.name for t in listing.tools} == names
                (root / "reports/mcp-tool-schemas.json").write_text(
                    listing.model_dump_json(indent=2) + "\n"
                )
                cases = [
                    ("avl.health", {}, True),
                    ("avl.inspect", {"model_path": str(args.vanilla.resolve())}, True),
                    ("avl.validate", {"model_path": str(args.bd.resolve())}, True),
                    (
                        "avl.trim",
                        {
                            "model_path": str(args.vanilla.resolve()),
                            "request": trim_request,
                            "case_name": "mcp-vanilla-trim",
                            "outputs": ["total"],
                        },
                        True,
                    ),
                    (
                        "avl.run",
                        {
                            "model_path": str(args.vanilla.resolve()),
                            "condition": {"alpha_deg": 3, "mach": 0.2},
                            "case_name": "mcp-vanilla",
                        },
                        True,
                    ),
                    (
                        "avl.sweep",
                        {
                            "model_path": str(args.bd.resolve()),
                            "conditions": [{"alpha_deg": a, "mach": 0.1} for a in (0, 2)],
                            "case_name": "mcp-bd-sweep",
                            "length_unit": "in",
                        },
                        True,
                    ),
                    (
                        "avl.run",
                        {
                            "model_path": str(args.vanilla.resolve()),
                            "condition": {"controls": {"absent-control": 1}},
                        },
                        False,
                    ),
                ]
                for name, arguments, expected in cases:
                    result = await session.call_tool(name, arguments)
                    record = result.model_dump(mode="json")
                    records.append({"tool": name, "arguments": arguments, "response": record})
                    assert result.isError is not expected, record
                    assert result.structuredContent["success"] is expected, record
                    if name == "avl.health":
                        diagnostic = result.structuredContent
                        assert diagnostic["verification"]["native_startup"]["status"] == "passed"
                        assert diagnostic["verification"]["numerical_case"]["status"] == "not_run"
                        assert (
                            diagnostic["verification"]["mcp_connection"]["status"]
                            == "request_received"
                        )
                        assert (
                            diagnostic["verification"]["desktop_registration"]["status"]
                            == "not_tested"
                        )
                    if name == "avl.sweep" and expected:
                        for row in result.structuredContent["results"]:
                            assert row["result_context"]["units"]["length"] == "in"
                    if name == "avl.run" and expected:
                        contract = result.structuredContent["result_contract"]
                        assert contract["schema_version"] == "1.0.0"
                        assert contract["references"]["Sref"] == 9
                        assert (
                            contract["derivative_tables"]["stability"]["fields"]["CLa"]["unit"]
                            == "rad^-1"
                        )
                        total = result.structuredContent["total"]["fields"]
                        assert abs(total["CLtot"] - 0.6754170403902354) < 1e-8
                    if name == "avl.trim" and expected:
                        trim_result = result.structuredContent
                        trim = trim_result["trim"]
                        assert trim_result["analysis_type"] == "longitudinal_trim"
                        assert trim["schema_version"] == "1.0.0"
                        assert trim["accepted"] and trim["within_bounds"], trim
                        assert trim["verification"]["status"] == "passed", trim
                        assert abs(trim["residuals"]["CL"]) <= trim_request["cl_tolerance"]
                        assert abs(trim["residuals"]["Cm"]) <= trim_request["cm_tolerance"]
                        solved = trim_result["condition"]
                        assert solved == trim["solved_condition"]
                        assert solved["mach"] == 0.2
                        assert solved["controls"]["flap"] == 2.0
                        assert all(solved[key] == 0 for key in (
                            "beta_deg", "pb_2v", "qc_2v", "rb_2v"
                        ))
                        assert trim_result["result_contract"]["schema_version"] == "1.0.0"
                mesh_sensitivity = await verify_mesh_sensitivity(session, records, args)
                physical_trim = await verify_physical_trim(session, records, args, trim_request)
                trim_query = await session.call_tool(
                    "avl.results",
                    {
                        "job_id": trim_result["job_id"],
                        "fields": [
                            "condition.controls.elevator",
                            "trim.residuals.CL",
                            "trim.residuals.Cm",
                            "trim.accepted",
                            "trim.verification.status",
                        ],
                    },
                )
                assert not trim_query.isError, trim_query
                saved_trim = trim_query.structuredContent
                assert saved_trim["solver_executed"] is False
                assert len(saved_trim["rows"]) == 1
                saved_fields = saved_trim["rows"][0]["fields"]
                assert saved_fields["trim.accepted"] is True
                assert saved_fields["trim.verification.status"] == "passed"
                for name in ("CL", "Cm"):
                    assert (
                        saved_fields[f"trim.residuals.{name}"]
                        == trim_result["trim"]["residuals"][name]
                    )
                solved_control = trim_result["condition"]["controls"]["elevator"]
                assert saved_fields["condition.controls.elevator"] == solved_control
                # Change only the acceptance interval so it excludes the native solution.
                rejected_args = {
                    "model_path": str(args.vanilla.resolve()),
                    "request": {
                        **trim_request,
                        "control_bounds": [solved_control + 1, solved_control + 2],
                    },
                    "case_name": "mcp-vanilla-trim-out-of-bounds",
                    "outputs": ["total"],
                }
                rejected = await session.call_tool("avl.trim", rejected_args)
                records.append({
                    "tool": "avl.trim", "arguments": rejected_args,
                    "response": rejected.model_dump(mode="json"),
                })
                assert rejected.isError, rejected
                rejection = rejected.structuredContent
                assert rejection["success"] is False
                assert rejection["error"]["code"] == "TRIM_OUT_OF_BOUNDS", rejection
                assert rejection["trim"]["accepted"] is False
                assert rejection["trim"]["within_bounds"] is False
                rejected_query = await session.call_tool(
                    "avl.results", {"job_id": rejection["job_id"]}
                )
                assert not rejected_query.isError, rejected_query
                saved_rejection = rejected_query.structuredContent
                assert saved_rejection["solver_executed"] is False
                assert len(saved_rejection["rows"]) == 1
                failed_row = saved_rejection["rows"][0]
                assert failed_row["success"] is False
                assert failed_row["error"]["code"] == "TRIM_OUT_OF_BOUNDS"
                assert failed_row["trim"]["accepted"] is False
                batch_requests = [
                    trim_request,
                    rejected_args["request"],
                    {**trim_request, "target_cl": 0.7, "mach": 0.15},
                ]
                batch_args = {
                    "model_path": str(args.vanilla.resolve()),
                    "trim_requests": batch_requests,
                    "case_name": "mcp-vanilla-trim-batch",
                    "outputs": ["total"],
                    "stop_on_error": False,
                }
                batch = await session.call_tool("avl.sweep", batch_args)
                records.append({
                    "tool": "avl.sweep", "arguments": batch_args,
                    "response": batch.model_dump(mode="json"),
                })
                assert batch.isError, batch
                batch_result = batch.structuredContent
                assert batch_result["success"] is False, batch_result
                assert batch_result["analysis_type"] == "longitudinal_trim_batch", batch_result
                assert batch_result["requested_count"] == 3, batch_result
                assert batch_result["attempted_count"] == 3, batch_result
                assert batch_result["completed_count"] == 2, batch_result
                assert [row["index"] for row in batch_result["results"]] == [0, 1, 2]
                for index in (0, 2):
                    assert_accepted_trim(batch_result["results"][index], batch_requests[index])
                failed_batch_row = batch_result["results"][1]
                assert failed_batch_row["success"] is False, failed_batch_row
                assert failed_batch_row["error"]["code"] == "TRIM_OUT_OF_BOUNDS", failed_batch_row
                assert failed_batch_row["trim"]["accepted"] is False, failed_batch_row
                batch_summary = assert_trim_summary(
                    batch_result["summary_csv"], batch_requests, batch_result["results"]
                )
                batch_query = await session.call_tool(
                    "avl.results",
                    {
                        "job_id": batch_result["job_id"],
                        "indices": [0, 2],
                        "fields": [
                            "condition.alpha_deg",
                            "condition.controls.elevator",
                            "trim.request.target_cl",
                            "trim.residuals.CL",
                            "trim.residuals.Cm",
                            "trim.accepted",
                            "trim.verification.status",
                        ],
                    },
                )
                assert not batch_query.isError, batch_query
                saved_batch = batch_query.structuredContent
                assert saved_batch["solver_executed"] is False, saved_batch
                assert [row["index"] for row in saved_batch["rows"]] == [0, 2], saved_batch
                for row in saved_batch["rows"]:
                    index = row["index"]
                    assert_accepted_trim(row, batch_requests[index])
                    original = batch_result["results"][index]
                    for field, actual in row["fields"].items():
                        expected = original
                        for part in field.split("."):
                            expected = expected[part]
                        assert actual == expected, row
                for name in ("avl.sweep", "avl.submit"):
                    for selectors in ({}, {"conditions": [{}], "trim_requests": [trim_request]}):
                        arguments = {"model_path": str(args.vanilla.resolve()), **selectors}
                        invalid = await session.call_tool(name, arguments)
                        records.append({
                            "tool": name, "arguments": arguments,
                            "response": invalid.model_dump(mode="json"),
                        })
                        assert invalid.isError, invalid
                        assert invalid.structuredContent["success"] is False, invalid
                        assert invalid.structuredContent["error"]["code"] == "BATCH_INPUT_REQUIRED"
                submitted = await session.call_tool(
                    "avl.submit",
                    {
                        "model_path": str(args.vanilla.resolve()),
                        "conditions": [{"alpha_deg": i % 7, "mach": 0.2} for i in range(256)],
                        "outputs": ["total"],
                        "case_name": "stdio-background",
                        "timeout_seconds": 120,
                    },
                )
                assert not submitted.isError, submitted
                job_id = submitted.structuredContent["job_id"]
                for _ in range(200):
                    state = (
                        await session.call_tool("avl.status", {"job_id": job_id})
                    ).structuredContent
                    if state["completed_count"] > 0:
                        break
                    await asyncio.sleep(0.025)
                assert state["state"] == "running", state
                before_disconnect = state["completed_count"]
        # Destroy the first MCP process; the detached worker must remain accessible.
        async with stdio_client(params, errlog=err) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                state = (
                    await session.call_tool("avl.status", {"job_id": job_id})
                ).structuredContent
                assert state["completed_count"] >= before_disconnect
                cancelled = await session.call_tool("avl.cancel", {"job_id": job_id})
                assert not cancelled.isError
                for _ in range(400):
                    state = (
                        await session.call_tool("avl.status", {"job_id": job_id})
                    ).structuredContent
                    if state["state"] in ("cancelled", "completed"):
                        break
                    assert state["state"] in ("queued", "running"), state
                    await asyncio.sleep(0.025)
                assert state["state"] == "cancelled", state
                cancelled_count = state["completed_count"]
                await resume_after_worker_exit(session, job_id)
                for _ in range(1200):
                    state = (
                        await session.call_tool("avl.status", {"job_id": job_id})
                    ).structuredContent
                    if state["state"] == "completed":
                        break
                    assert state["state"] in ("queued", "running"), state
                    await asyncio.sleep(0.05)
                assert state["state"] == "completed" and state["completed_count"] == 256, state
                query = await session.call_tool(
                    "avl.results",
                    {"job_id": job_id, "indices": [0, 3], "fields": ["total.fields.CLtot"]},
                )
                assert not query.isError and len(query.structuredContent["rows"]) == 2
                assert all(
                    row["result_context"]["schema_version"] == "1.0.0"
                    for row in query.structuredContent["rows"]
                )
                assert (
                    abs(
                        query.structuredContent["rows"][1]["fields"]["total.fields.CLtot"]
                        - 0.6754170403902354
                    )
                    < 1e-8
                )
                absent = await session.call_tool(
                    "avl.results", {"job_id": job_id, "fields": ["body.derivatives.Cmq"]}
                )
                assert (
                    absent.isError
                    and absent.structuredContent["error"]["code"] == "FIELD_NOT_AVAILABLE"
                )
                background_trim = await verify_background_trim(params, err, args, trim_request)
                report = {
                    "success": True,
                    "package_version": __version__,
                    "server_info": initialized.serverInfo.model_dump(),
                    "command": params.command,
                    "args": params.args,
                    "tools_discovered": sorted(names),
                    "calls": records,
                    "mesh_sensitivity": mesh_sensitivity,
                    "physical_trim": physical_trim,
                    "trim_saved_query": saved_trim,
                    "rejected_trim_saved_query": saved_rejection,
                    "trim_batch_saved_query": saved_batch,
                    "trim_batch_summary_csv_rows": batch_summary,
                    "background_trim": background_trim,
                    "background_job_id": job_id,
                    "worker_survived_mcp_disconnect": True,
                    "completed_before_disconnect": before_disconnect,
                    "cancellation_acknowledged": True,
                    "completed_at_cancellation": cancelled_count,
                    "final_status": state,
                    "query": query.structuredContent,
                    "desktop_registration_tested": False,
                }
                (root / "reports/mcp-stdio-validation.json").write_text(
                    json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
                )
                print(
                    json.dumps(
                        {
                            "success": True,
                            "tools": sorted(names),
                            "background_cases": 256,
                            "trim_verified": True,
                            "mesh_sensitivity_verified": True,
                            "physical_trim_verified": True,
                            "invalid_dimensional_inputs_rejected": True,
                            "out_of_bounds_trim_rejected": True,
                            "mixed_trim_batch_verified": True,
                            "background_trim_cases": background_trim["requested_count"],
                            "background_trim_checkpoints_preserved": True,
                            "reconnection": True,
                        }
                    )
                )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("command", "avl-bin", "work-root", "vanilla", "bd"):
        p.add_argument("--" + name, type=Path, required=True)
    asyncio.run(verify(p.parse_args()))


if __name__ == "__main__":
    main()
