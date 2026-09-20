"""Isolated, bounded AVL subprocess jobs with retained evidence for every execution."""

import csv
import hashlib
import json
import math
import os
import re
import signal
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .geometry import Geometry, inspect_geometry, sha256
from .models import AVLFailure, FlightCondition, References

SUPPORTED_AVL = "3.52"
ERROR_PATTERNS = (
    "trim convergence failed",
    "cannot trim",
    "flow solution is not possible",
    "file not processed",
    "execute flow calculation first",
    "fortran runtime error",
    "segmentation fault",
    "floating-point exception",
    "* filename error *",
    "* data not written",
    "singular matrix",
    "unrecognized command",
    "not recognized",
)


def dump(path: Path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


class AVLRunner:
    def __init__(
        self,
        executable: str,
        work_root: str,
        input_root: str | None = None,
        max_vortices: int = 5000,
    ):
        self.executable = Path(executable).expanduser().resolve()
        self.work_root = Path(work_root).expanduser().resolve()
        self.input_root = Path(input_root).expanduser().resolve() if input_root else None
        self.max_vortices = max_vortices

    def _new_job(self, label):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,47}", label):
            raise AVLFailure("INVALID_CASE_NAME", "Use 1-48 ASCII letters, numbers, '_' or '-'.")
        now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        directory = self.work_root / "runs" / f"{now}_{label}_{uuid.uuid4().hex[:8]}"
        directory.mkdir(parents=True, exist_ok=False)
        return directory

    def _process(self, directory: Path, commands: str, timeout: float) -> dict:
        if not self.executable.is_file() or not os.access(self.executable, os.X_OK):
            raise AVLFailure(
                "EXECUTABLE_NOT_FOUND", f"AVL executable is unavailable: {self.executable}"
            )
        (directory / "commands.txt").write_text(commands)
        env = dict(os.environ, LC_ALL="C", LANG="C")
        # Batch operations must not need a display or inherit a graphical session.
        env.pop("DISPLAY", None)
        started = time.monotonic()
        proc = None
        failure = None
        stdout, stderr = directory / "stdout.log", directory / "stderr.log"
        try:
            with stdout.open("wb") as out, stderr.open("wb") as err:
                proc = subprocess.Popen(
                    [str(self.executable)],
                    cwd=directory,
                    env=env,
                    stdin=subprocess.PIPE,
                    stdout=out,
                    stderr=err,
                    start_new_session=(os.name != "nt"),
                )
                try:
                    proc.stdin.write(commands.encode("ascii"))
                    proc.stdin.close()
                except BrokenPipeError:
                    pass
                while proc.poll() is None:
                    if time.monotonic() - started > timeout:
                        failure = AVLFailure("TIMEOUT", f"AVL exceeded {timeout:g} seconds.")
                        break
                    if stdout.stat().st_size + stderr.stat().st_size > 32_000_000:
                        failure = AVLFailure("OUTPUT_LIMIT", "AVL exceeded the 32 MB log limit.")
                        break
                    time.sleep(0.02)
        finally:
            if proc and proc.poll() is None:
                if os.name != "nt":
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
                proc.wait()
        text = stdout.read_text(errors="replace")
        errors = stderr.read_text(errors="replace")
        match = re.search(r"Athena Vortex Lattice\s+Program\s+Version\s+(\d+\.\d+)", text)
        metadata = {
            "returncode": proc.returncode,
            "elapsed_seconds": time.monotonic() - started,
            "version": match.group(1) if match else None,
            "display_unset": True,
            "executable": str(self.executable),
            "executable_sha256": sha256(self.executable),
        }
        dump(directory / "process.json", metadata)
        if failure:
            raise failure
        if proc.returncode != 0:
            raise AVLFailure(
                "SOLVER_EXIT",
                f"AVL exited with code {proc.returncode}.",
                stderr_tail=errors[-1500:],
            )
        if metadata["version"] != SUPPORTED_AVL:
            raise AVLFailure(
                "UNSUPPORTED_VERSION",
                f"Require AVL {SUPPORTED_AVL}; detected {metadata['version']!r}.",
            )
        combined = (text + "\n" + errors).lower()
        found = [p for p in ERROR_PATTERNS if p in combined]
        if found:
            raise AVLFailure(
                "SOLVER_REPORTED_ERROR", "AVL reported an unsuccessful operation.", markers=found
            )
        return metadata

    def health(self) -> dict:
        from .diagnostics import diagnose

        return diagnose(
            str(self.executable),
            str(self.work_root),
            str(self.input_root) if self.input_root else None,
            self.max_vortices,
        )

    def inspect(self, model_path: str) -> dict:
        obj = inspect_geometry(model_path, self.input_root)
        return {"success": True, "geometry": obj.report()}

    def validate(self, model_path: str) -> dict:
        obj = inspect_geometry(model_path, self.input_root)
        self._check_model(obj)
        return {"success": True, "geometry": obj.report(), "ready_for_prescribed_analysis": True}

    def _check_model(self, obj: Geometry):
        if obj.panel_estimate > self.max_vortices:
            raise AVLFailure(
                "MESH_LIMIT",
                f"Estimated vortices {obj.panel_estimate} exceed "
                f"configured limit {self.max_vortices}.",
            )
        if len(obj.controls) > 30:
            raise AVLFailure("CONTROL_LIMIT", "At most 30 controls are supported.")

    def _stage(self, obj: Geometry, job: Path, references: References | None) -> dict:
        lines = list(obj.raw_lines)
        if sha256(obj.path) != obj.source_hash:
            raise AVLFailure("SOURCE_CHANGED", "Geometry changed after inspection.")
        sources = {str(obj.path): obj.source_hash}
        originals = job / "originals"
        originals.mkdir()
        (originals / "model.avl").write_bytes(obj.source_bytes)
        replacements = {}
        for item in obj.dependencies:
            path = Path(item["path"])
            if str(path) not in replacements:
                name = f"asset{len(replacements):03d}.dat"
                data = path.read_bytes()
                if hashlib.sha256(data).hexdigest() != item["sha256"]:
                    raise AVLFailure(
                        "SOURCE_CHANGED", "An input dependency changed during staging."
                    )
                (job / name).write_bytes(data)
                (originals / name).write_bytes(data)
                replacements[str(path)] = name
                sources[str(path)] = item["sha256"]
            lines[item["line"]] = replacements[str(path)]
        if references:
            lines[obj.header_lines[3]] = " ".join(
                format(getattr(references, k), ".15g") for k in ("sref", "cref", "bref")
            )
            lines[obj.header_lines[4]] = " ".join(
                format(getattr(references, k), ".15g") for k in ("xref", "yref", "zref")
            )
        (job / "model.avl").write_text("\n".join(lines) + "\n")
        return {
            "sources": sources,
            "dependency_mapping": replacements,
            "staged_geometry_sha256": sha256(job / "model.avl"),
        }

    @staticmethod
    def _commands(obj, condition, outputs=None):
        from .execution import condition_commands, startup_commands
        from .models import output_selection

        return "\n".join(
            startup_commands()
            + condition_commands(obj, condition, output_selection(outputs))
            + ["", "QUIT", ""]
        )

    def run(
        self,
        model_path,
        condition=None,
        references=None,
        case_name="case",
        timeout_seconds=120.0,
        length_unit="unspecified",
        outputs=None,
    ):
        from .execution import collect_case, validate_request

        if not math.isfinite(timeout_seconds) or not 0.1 <= timeout_seconds <= 600:
            raise AVLFailure("INVALID_TIMEOUT", "Timeout must be between 0.1 and 600 seconds.")
        obj = inspect_geometry(model_path, self.input_root)
        condition = condition or FlightCondition()
        selected = validate_request(
            self, obj, [condition], references, outputs, length_unit, timeout_seconds
        )
        job = self._new_job(case_name)
        manifest = {
            "package_version": __version__,
            "model": obj.report(),
            "condition": condition.model_dump(),
            "outputs": selected,
            "references_override": references.model_dump() if references else None,
            "geometry_length_unit": length_unit,
            "case_name": case_name,
            "timeout_seconds": timeout_seconds,
            "run_directory": str(job),
        }
        try:
            manifest.update(self._stage(obj, job, references))
            dump(job / "manifest.json", manifest)
            process = self._process(job, self._commands(obj, condition, selected), timeout_seconds)
            result = collect_case(job, obj, condition, references, selected, length_unit)
            self._check_sources(manifest["sources"])
            result.update(solver=process, source_preserved=True)
            result["artifacts"].update(
                {
                    name: str(job / name)
                    for name in (
                        "manifest.json",
                        "commands.txt",
                        "stdout.log",
                        "stderr.log",
                        "result.json",
                    )
                }
            )
        except AVLFailure as exc:
            result = exc.result() | {"run_directory": str(job)}
        except (OSError, UnicodeError, ValueError, IndexError, KeyError) as exc:
            result = AVLFailure("EXECUTION_OR_PARSE_ERROR", str(exc)).result() | {
                "run_directory": str(job)
            }
        result["job_id"] = job.name
        dump(job / "result.json", result)
        return result

    @staticmethod
    def _check_sources(sources):
        changed = [p for p, h in sources.items() if not Path(p).is_file() or sha256(Path(p)) != h]
        if changed:
            raise AVLFailure("SOURCE_CHANGED", "Original input changed.", paths=changed)

    def sweep(
        self,
        model_path,
        conditions,
        references=None,
        case_name="sweep",
        timeout_seconds=300.0,
        length_unit="unspecified",
        stop_on_error=True,
        outputs=None,
    ):
        from .execution import execute_cases, validate_request

        obj = inspect_geometry(model_path, self.input_root)
        selected = validate_request(
            self, obj, conditions, references, outputs, length_unit, timeout_seconds
        )
        job = self._new_job(case_name)
        sources = {str(obj.path): obj.source_hash} | {
            d["path"]: d["sha256"] for d in obj.dependencies
        }
        dump(
            job / "manifest.json",
            {
                "model_path": str(obj.path),
                "source_hashes": sources,
                "conditions": [c.model_dump() for c in conditions],
                "outputs": selected,
                "references_override": references.model_dump() if references else None,
                "length_unit": length_unit,
                "total_timeout_seconds": timeout_seconds,
            },
        )
        started = time.monotonic()

        def checkpoint(index, item):
            self._check_sources(sources)
            path = job / f"case-{index:05d}.json"
            dump(path, item)

        try:
            result = execute_cases(
                self,
                job,
                obj,
                conditions,
                references,
                selected,
                length_unit,
                timeout_seconds,
                stop_on_error,
                on_case=checkpoint,
            )
            self._check_sources(sources)
        except (AVLFailure, OSError) as exc:
            failure = (
                exc
                if isinstance(exc, AVLFailure)
                else AVLFailure("EXECUTION_OR_IO_ERROR", str(exc))
            )
            result = failure.result() | {"run_directory": str(job), "results": []}
            for path in sorted(job.glob("case-*.json")):
                result["results"].append(json.loads(path.read_text()))
            result.update(
                requested_count=len(conditions),
                attempted_count=len(result["results"]),
                completed_count=sum(r["success"] for r in result["results"]),
            )
        result["elapsed_seconds"] = time.monotonic() - started
        result["summary_csv"] = str(job / "summary.csv")
        fields = [
            "index",
            "success",
            "alpha_deg",
            "beta_deg",
            "Mach",
            "CLtot",
            "CDtot",
            "CDff",
            "Cmtot",
            "CYtot",
            "Cltot",
            "Cntot",
            "run_directory",
        ]
        with (job / "summary.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for item in result["results"]:
                i = item["index"]
                vals = item.get("total", {}).get("fields", {})
                writer.writerow(
                    {
                        "index": i,
                        "success": item["success"],
                        "alpha_deg": conditions[i].alpha_deg,
                        "beta_deg": conditions[i].beta_deg,
                        **{k: vals.get(k) for k in fields[4:-1]},
                        "run_directory": item.get("run_directory"),
                    }
                )
        result["job_id"] = job.name
        dump(job / "result.json", result)
        return result
