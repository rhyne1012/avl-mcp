"""Five phase-1 tools over MCP stdio; solver output is always captured to files."""

import functools
import json

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from .models import AVLFailure, FlightCondition, LengthUnit, References
from .runner import AVLRunner


def make_server(runner: AVLRunner) -> FastMCP:
    server = FastMCP(
        "avl-mcp",
        instructions=(
            "AVL 3.52 prescribed-condition aerodynamics. Use avl.validate before runs. "
            "Inputs: geometry X aft/Y right/Z up; rates in standard BODY axes. "
            "No implicit .run/.mass loading, trim or mode analysis. "
            "Use returned artifacts for complete results and original solver logs."
        ),
    )

    async def invoke(method, *args, **kwargs) -> CallToolResult:
        try:
            result = await anyio.to_thread.run_sync(functools.partial(method, *args, **kwargs))
        except AVLFailure as exc:
            result = exc.result()
        except (OSError, ValueError) as exc:
            result = AVLFailure("INPUT_OR_IO_ERROR", str(exc)).result()
        if "results" in result:
            # The complete sweep remains on disk. Keep the protocol response bounded.
            result = dict(result)
            result["result_json"] = result["run_directory"] + "/result.json"
            result["results"] = [
                {
                    k: v
                    for k, v in item.items()
                    if k
                    in (
                        "success",
                        "error",
                        "condition",
                        "run_directory",
                        "total",
                        "source_preserved",
                    )
                }
                for item in result["results"]
            ]
        text = json.dumps(result, ensure_ascii=False, allow_nan=False)
        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            structuredContent=result,
            isError=not result["success"],
        )

    @server.tool(
        name="avl.health",
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
        ),
    )
    async def health() -> CallToolResult:
        """Check AVL 3.52 startup/version/headless exit; save probe logs. Not a numerical test."""
        return await invoke(runner.health)

    @server.tool(
        name="avl.inspect",
        annotations=ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
        ),
    )
    async def inspect(model_path: str) -> CallToolResult:
        """Read geometry, references, controls and dependencies; original files are unchanged."""
        return await invoke(runner.inspect, model_path)

    @server.tool(
        name="avl.validate",
        annotations=ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
        ),
    )
    async def validate(model_path: str) -> CallToolResult:
        """Validate supported text grammar, dependencies and mesh limits. No solver execution."""
        return await invoke(runner.validate, model_path)

    @server.tool(
        name="avl.run",
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
        ),
    )
    async def run(
        model_path: str,
        condition: FlightCondition | None = None,
        references: References | None = None,
        case_name: str = "case",
        timeout_seconds: float = 120,
        length_unit: LengthUnit = "unspecified",
    ) -> CallToolResult:
        """Run one prescribed flight condition. Return forces, ST/SB derivatives, surface
        loads and links to strip CSV/JSON. Angles in degrees, rates nondimensional BODY
        axes; CONTROL values use gains defined in geometry. Default controls/rates zero.
        A references override changes only the staged copy. Ignores nearby .run/.mass.
        """
        return await invoke(
            runner.run, model_path, condition, references, case_name, timeout_seconds, length_unit
        )

    @server.tool(
        name="avl.sweep",
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
        ),
    )
    async def sweep(
        model_path: str,
        conditions: list[FlightCondition],
        references: References | None = None,
        case_name: str = "sweep",
        timeout_seconds: float = 300,
        length_unit: LengthUnit = "unspecified",
        stop_on_error: bool = True,
    ) -> CallToolResult:
        """Run 1-25 explicit conditions sequentially within one total time budget.
        Preserve successful and failed cases and write summary.csv. Each case is isolated.
        Return partial results and isError=true if any requested case is unsuccessful.
        """
        return await invoke(
            runner.sweep,
            model_path,
            conditions,
            references,
            case_name,
            timeout_seconds,
            length_unit,
            stop_on_error,
        )

    return server
