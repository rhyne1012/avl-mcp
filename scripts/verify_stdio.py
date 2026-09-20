"""Verify all five tools using an actual installed MCP command and official cases."""

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
    names = {"avl.health", "avl.inspect", "avl.validate", "avl.run", "avl.sweep"}
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
                    if name == "avl.run" and expected:
                        total = result.structuredContent["total"]["fields"]
                        assert abs(total["CLtot"] - 0.6754170403902354) < 1e-8
                report = {
                    "success": True,
                    "server_info": initialized.serverInfo.model_dump(),
                    "command": params.command,
                    "args": params.args,
                    "tools_discovered": sorted(names),
                    "calls": records,
                    "desktop_registration_tested": False,
                }
                (root / "reports/mcp-stdio-validation.json").write_text(
                    json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
                )
                print(json.dumps({"success": True, "tools": sorted(names), "calls": len(records)}))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("command", "avl-bin", "work-root", "vanilla", "bd"):
        p.add_argument("--" + name, type=Path, required=True)
    asyncio.run(verify(p.parse_args()))


if __name__ == "__main__":
    main()
