"""Verify all eleven tools using an installed MCP command and official cases."""

import argparse
import asyncio
import json
import os
import subprocess
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from avl_mcp import __version__


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
                await asyncio.sleep(0.05)
                resumed = await session.call_tool("avl.resume", {"job_id": job_id})
                assert not resumed.isError, resumed
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
                report = {
                    "success": True,
                    "package_version": __version__,
                    "server_info": initialized.serverInfo.model_dump(),
                    "command": params.command,
                    "args": params.args,
                    "tools_discovered": sorted(names),
                    "calls": records,
                    "trim_saved_query": saved_trim,
                    "rejected_trim_saved_query": saved_rejection,
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
                            "out_of_bounds_trim_rejected": True,
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
