"""Verify all ten tools using an actual installed MCP command and official cases."""

import argparse
import asyncio
import json
import os
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def verify(args):
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
        "avl.sweep",
        "avl.submit",
        "avl.status",
        "avl.cancel",
        "avl.resume",
        "avl.results",
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
                assert state["state"] in ("cancelled", "completed"), state
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
                    "server_info": initialized.serverInfo.model_dump(),
                    "command": params.command,
                    "args": params.args,
                    "tools_discovered": sorted(names),
                    "calls": records,
                    "background_job_id": job_id,
                    "worker_survived_mcp_disconnect": True,
                    "completed_before_disconnect": before_disconnect,
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
