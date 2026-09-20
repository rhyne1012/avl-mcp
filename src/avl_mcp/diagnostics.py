"""Bounded installation diagnostics, usable from the CLI before importing MCP."""

import asyncio
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__

EXPECTED_TOOLS = {
    "avl." + k
    for k in (
        "health",
        "inspect",
        "validate",
        "run",
        "sweep",
        "submit",
        "status",
        "cancel",
        "resume",
        "results",
    )
}


def probe_command(command, timeout=10):
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, errors="replace", timeout=timeout, check=False
        )
        return {
            "status": "passed" if completed.returncode == 0 else "failed",
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout[-12000:],
            "stderr": completed.stderr[-4000:],
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "failed", "command": command, "message": str(exc)}


def interpreter_command():
    command = [sys.executable]
    if sys.flags.isolated:
        command.append("-I")
    elif sys.flags.ignore_environment:
        command.append("-E")
    if sys.flags.no_site:
        command.append("-S")
    elif sys.flags.no_user_site:
        command.append("-s")
    return command


def python_environment():
    versions = {}
    for name in ("avl-mcp", "mcp", "pydantic", "anyio"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    imports = probe_command(
        interpreter_command()
        + [
            "-c",
            "from mcp.server.fastmcp import FastMCP; from pydantic import BaseModel; import anyio",
        ]
    )
    return {
        "status": imports["status"],
        "executable": sys.executable,
        "python_version": platform.python_version(),
        "package_version": __version__,
        "installed_distributions": versions,
        "imports": imports,
        "remedy": "If imports fail, reinstall the wheel and its dependencies in this Python "
        "environment, then run python -m pip check.",
    }


def native_environment(executable):
    binary = Path(executable).expanduser().resolve()
    exists = binary.is_file()
    executable_bit = exists and os.access(binary, os.X_OK)
    result = {
        "status": "passed" if executable_bit else "failed",
        "path": str(binary),
        "exists": exists,
        "executable": executable_bit,
        "host": {"system": platform.system(), "machine": platform.machine()},
        "architecture": {"status": "not_checked"},
        "libraries": {"status": "not_checked"},
    }
    if not executable_bit:
        result["remedy"] = "Point --avl-bin to an executable AVL 3.52 binary for this host."
    inspector = shutil.which("file")
    if exists and inspector:
        result["architecture"] = probe_command([inspector, "-b", str(binary)])
        result["architecture"]["scope"] = (
            "File description only; actual startup tests host compatibility"
        )
    if exists and platform.system() == "Darwin" and shutil.which("otool"):
        result["libraries"] = probe_command([shutil.which("otool"), "-L", str(binary)])
        result["libraries"]["scope"] = (
            "Declared load commands only, not recursive path resolution. Startup verifies loading; "
            "system libraries may live in the macOS shared cache."
        )
    elif exists and platform.system() == "Linux" and shutil.which("readelf"):
        result["libraries"] = probe_command([shutil.which("readelf"), "-d", str(binary)])
        result["libraries"]["scope"] = "Declared ELF dependencies only; startup verifies loading"
    else:
        result["libraries"]["scope"] = "Static dependency inspection unavailable on this host"
    return result


def failure_advice(error, logs=""):
    text = (str(error) + "\n" + logs).lower()
    if any(
        v in text
        for v in (
            "library not loaded",
            "library not found",
            "cannot open shared object",
            "image not found",
            "dll",
        )
    ):
        return {
            "category": "native_library",
            "remedy": "Restore the libraries matching this "
            "AVL build and its loader paths; inspect stderr.log for the missing library.",
        }
    if any(
        v in text for v in ("bad cpu", "exec format", "wrong architecture", "errno 8", "errno 86")
    ):
        return {
            "category": "native_architecture_or_format",
            "remedy": "Use a native executable "
            "for this operating system and architecture; do not copy another machine's venv.",
        }
    if any(v in text for v in ("permission denied", "operation not permitted")):
        return {
            "category": "permission",
            "remedy": "Check executable, directory and OS permissions "
            "for this process; a sandbox failure alone does not establish a broken installation.",
        }
    return {
        "category": error.get("code", "startup_failure").lower(),
        "remedy": "Inspect the retained command, stdout, stderr and process metadata; "
        "confirm AVL 3.52 is configured.",
    }


def diagnose(executable, work_root, input_root=None, max_vortices=5000):
    """No numerical solves, installations, client config edits, or external network calls."""
    checks = {"python": python_environment(), "executable": native_environment(executable)}
    result = {
        "success": False,
        "diagnostic_schema_version": "1.0.0",
        "package_version": __version__,
        "work_root": str(Path(work_root).expanduser().resolve()),
        "checks": checks,
        "verification": {
            "native_startup": {"status": "not_run"},
            "numerical_case": {
                "status": "not_run",
                "reason": "Use an official-case avl.run or "
                "the repository validation suite; health never certifies coefficients.",
            },
            "mcp_connection": {
                "status": "not_tested",
                "reason": "Standalone diagnostic; use --check-mcp for a fresh stdio round trip.",
            },
            "desktop_registration": {
                "status": "not_tested",
                "reason": "Client registration "
                "and registry refresh must be checked in that client.",
            },
        },
        "scope": "Installation, permissions, dependencies and native startup; no numerical solve",
    }
    if input_root:
        root = Path(input_root).expanduser().resolve()
        readable = root.is_dir() and os.access(root, os.R_OK | os.X_OK)
        checks["input_root"] = {
            "status": "passed" if readable else "failed",
            "path": str(root),
            "scope": "Configured directory accessibility, not every model",
        }
    else:
        checks["input_root"] = {"status": "not_configured"}
    job = None
    try:
        root = Path(result["work_root"])
        root.mkdir(parents=True, exist_ok=True)
        # Avoid importing pydantic/MCP before reporting a broken Python installation.
        from datetime import datetime, timezone
        from uuid import uuid4

        job = (
            root
            / "runs"
            / (
                datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                + "_health_"
                + uuid4().hex[:8]
            )
        )
        job.mkdir(parents=True)
        probe = job / "write-probe.tmp"
        probe.write_text("avl-mcp work directory probe\n")
        moved = probe.with_suffix(".renamed")
        probe.replace(moved)
        assert moved.read_text() == "avl-mcp work directory probe\n"
        moved.unlink()
        checks["work_directory"] = {
            "status": "passed",
            "path": str(root),
            "operations": ["create", "write", "rename", "read", "delete"],
        }
        result.update(run_directory=str(job), job_id=job.name)
    except (OSError, AssertionError) as exc:
        checks["work_directory"] = {
            "status": "failed",
            "path": result["work_root"],
            "message": str(exc),
            "remedy": "Choose a writable work root "
            "accessible to the MCP process; check parent permissions.",
        }
        result["error"] = {"code": "WORK_ROOT_UNAVAILABLE", "message": str(exc)}
    if checks["python"]["status"] != "passed":
        result.setdefault(
            "error",
            {
                "code": "PYTHON_DEPENDENCIES",
                "message": "Required Python modules could not be imported by this interpreter.",
            },
        )
    elif checks["work_directory"]["status"] == "passed":
        from .models import AVLFailure
        from .runner import AVLRunner

        runner = AVLRunner(executable, work_root, input_root, max_vortices)
        try:
            result["solver"] = runner._process(job, "PLOP\nG\n\nQUIT\n", 10)
            result["verification"]["native_startup"] = {
                "status": "passed",
                "version": result["solver"]["version"],
                "dynamic_loading": "passed_for_headless_startup",
            }
        except (AVLFailure, OSError) as exc:
            error = (
                exc.result()["error"]
                if isinstance(exc, AVLFailure)
                else {"code": "EXECUTION_OR_IO_ERROR", "message": str(exc)}
            )
            stderr = job / "stderr.log"
            logs = stderr.read_text(errors="replace")[-4000:] if stderr.exists() else ""
            result["error"] = error
            result["verification"]["native_startup"] = {
                "status": "failed",
                "error": error,
                **failure_advice(error, logs),
            }
    if checks["input_root"]["status"] == "failed":
        result.setdefault(
            "error",
            {
                "code": "INPUT_ROOT_UNAVAILABLE",
                "message": "Configured input root is not an accessible directory.",
            },
        )
    result["success"] = "error" not in result
    if job and checks["work_directory"]["status"] == "passed":
        save_report(result)
    return result


def save_report(result):
    folder = Path(result["run_directory"])
    result["artifacts"] = {p.name: str(p) for p in folder.iterdir() if p.is_file()}
    result["artifacts"]["result.json"] = str(folder / "result.json")
    (folder / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")


async def _check_stdio(executable, work_root, input_root, max_vortices, log):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    args = interpreter_command()[1:] + [
        "-m",
        "avl_mcp",
        "--avl-bin",
        executable,
        "--work-root",
        work_root,
        "--max-vortices",
        str(max_vortices),
    ]
    if input_root:
        args += ["--input-root", input_root]
    params = StdioServerParameters(command=sys.executable, args=args)
    async with asyncio.timeout(45):
        with log.open("w") as err:
            async with stdio_client(params, errlog=err) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    initialized = await session.initialize()
                    listing = await session.list_tools()
                    names = {t.name for t in listing.tools}
                    health = await session.call_tool("avl.health", {})
                    data = health.structuredContent or {}
                    valid_response = (
                        isinstance(data.get("success"), bool)
                        and data.get("package_version") == __version__
                        and data.get("diagnostic_schema_version") == "1.0.0"
                    )
                    return {
                        "status": "passed"
                        if EXPECTED_TOOLS <= names and valid_response
                        else "failed",
                        "command": sys.executable,
                        "args": args,
                        "server_info": initialized.serverInfo.model_dump(),
                        "tools": sorted(names),
                        "missing_tools": sorted(EXPECTED_TOOLS - names),
                        "health_success": data.get("success"),
                        "health_report": data.get("run_directory"),
                        "scope": "Fresh subprocess initialize/list_tools/health round trip; "
                        "not desktop registration or a numerical test",
                    }


def check_stdio(result, executable, work_root, input_root=None, max_vortices=5000):
    if "run_directory" not in result or result["checks"]["python"]["status"] != "passed":
        result["verification"]["mcp_connection"] = {
            "status": "not_tested",
            "reason": "Fix installation/work-root errors first",
        }
        return result
    try:
        check = asyncio.run(
            _check_stdio(
                str(executable),
                str(work_root),
                input_root,
                max_vortices,
                Path(result["run_directory"]) / "mcp-stderr.log",
            )
        )
    except Exception as exc:
        check = {
            "status": "failed",
            "message": str(exc),
            "remedy": "Inspect mcp-stderr.log and "
            "the selected Python environment. Retry this exact command outside the client.",
        }
    result["verification"]["mcp_connection"] = check
    if check["status"] != "passed" or check.get("health_success") is not True:
        result["success"] = False
        result.setdefault(
            "error",
            {
                "code": "MCP_PROBE_FAILED",
                "message": "Fresh stdio probe "
                "or its health check failed; see verification.mcp_connection.",
            },
        )
    save_report(result)
    return result
