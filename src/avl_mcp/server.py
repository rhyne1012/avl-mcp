"""Ten tools over MCP stdio; solver output is always captured to files."""

import functools
import json

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from .jobs import JobManager
from .models import AVLFailure, FlightCondition, LengthUnit, OutputKind, References
from .runner import AVLRunner


def make_server(runner: AVLRunner) -> FastMCP:
    jobs = JobManager(runner)
    server = FastMCP(
        "avl-mcp",
        instructions=(
            "AVL 3.52 prescribed-condition aerodynamics. Use avl.validate before runs. "
            "Inputs: geometry X aft/Y right/Z up; rates in standard BODY axes. "
            "No implicit .run/.mass loading, trim or mode analysis. "
            "Use avl.submit/status/cancel/resume for durable long-running jobs; "
            "avl.results reads saved results without invoking AVL. "
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
                        "index",
                        "outputs",
                        "error",
                        "condition",
                        "run_directory",
                        "total",
                        "source_preserved",
                        "result_context",
                        "derived",
                    )
                }
                for item in result["results"][:25]
            ]
            result["returned_count"] = len(result["results"])
            result["results_truncated"] = result.get("attempted_count", 0) > 25
            result["next_offset"] = 25 if result["results_truncated"] else None
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
        """Diagnose Python/native dependencies, architecture, directory permissions and AVL startup.
        Save evidence. Numerical validation and desktop registry refresh remain separate.
        """

        def connected_health():
            from .diagnostics import save_report

            result = runner.health()
            result["verification"]["mcp_connection"] = {
                "status": "request_received",
                "scope": "This MCP handler received the health call; "
                "The client must confirm receipt. Other clients/registries are not audited.",
            }
            if (
                "run_directory" in result
                and result["checks"]["work_directory"]["status"] == "passed"
            ):
                save_report(result)
            return result

        return await invoke(connected_health)

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
        outputs: list[OutputKind] | None = None,
    ) -> CallToolResult:
        """Run one prescribed flight condition. Return forces, ST/SB derivatives, surface
        loads and links to strip CSV/JSON. Angles in degrees, rates nondimensional BODY
        axes; CONTROL values use gains defined in geometry. Default controls/rates zero.
        A references override changes only the staged copy. Ignores nearby .run/.mass.
        """
        return await invoke(
            runner.run,
            model_path,
            condition,
            references,
            case_name,
            timeout_seconds,
            length_unit,
            outputs,
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
        outputs: list[OutputKind] | None = None,
    ) -> CallToolResult:
        """Run 1-10000 conditions in shared AVL processes, grouped by Mach, within one budget.
        Return cases in requested order; reset all rates and controls at every condition.
        Select outputs (total is always retained); None preserves all previous tables.
        Preserve successful and failed cases and write summary.csv. For long jobs use submit.
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
            outputs,
        )

    @server.tool(
        name="avl.submit",
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
        ),
    )
    async def submit(
        model_path: str,
        conditions: list[FlightCondition],
        references: References | None = None,
        case_name: str = "job",
        timeout_seconds: float = 1800,
        length_unit: LengthUnit = "unspecified",
        stop_on_error: bool = True,
        outputs: list[OutputKind] | None = None,
    ) -> CallToolResult:
        """Snapshot inputs and start a detached local AVL job; return job_id immediately.
        1-10000 cases, at most two background workers execute per work root. Workers survive
        MCP client disconnection. Timeout is the execution budget per attempt, excluding queue.
        outputs=['total'] skips derivatives/surface/strip output; None requests all tables.
        """
        return await invoke(
            jobs.submit,
            model_path,
            conditions,
            references,
            case_name,
            timeout_seconds,
            length_unit,
            stop_on_error,
            outputs,
        )

    @server.tool(
        name="avl.status",
        annotations=ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
        ),
    )
    async def status(job_id: str) -> CallToolResult:
        """Read queued/running/completed/failed/cancelled/interrupted state and case counts.
        success=true means the status was read; inspect state/error for solver outcome.
        """
        return await invoke(jobs.status, job_id)

    @server.tool(
        name="avl.cancel",
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
        ),
    )
    async def cancel(job_id: str) -> CallToolResult:
        """Request cooperative cancellation of this job's worker and its owned AVL process.
        Completed cases and logs remain. Query status until cancellation is acknowledged.
        """
        return await invoke(jobs.cancel, job_id)

    @server.tool(
        name="avl.resume",
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
        ),
    )
    async def resume(job_id: str) -> CallToolResult:
        """Resume an inactive job after verifying input, solver, implementation and output hashes.
        Recompute only failed, missing or damaged cases. Active jobs cannot be resumed.
        """
        return await invoke(jobs.resume, job_id)

    @server.tool(
        name="avl.results",
        annotations=ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
        ),
    )
    async def results(
        job_id: str,
        offset: int = 0,
        limit: int = 20,
        fields: list[str] | None = None,
        indices: list[int] | None = None,
    ) -> CallToolResult:
        """Page saved background or run/sweep results without running AVL. Limit 1-100 cases.
        Filter by original zero-based indices and dotted fields, e.g. total.fields.CLtot,
        body.derivatives.Cmq, stability.control_derivatives.elevator.Cm. Missing tables
        return FIELD_NOT_AVAILABLE; raw/strip artifact paths remain available by default.
        """
        return await invoke(jobs.results, job_id, offset, limit, fields, indices)

    return server
