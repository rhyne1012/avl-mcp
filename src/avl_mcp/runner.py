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
from .outputs import parse_derivatives, parse_strips, parse_surfaces, parse_total

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
        job = self._new_job("health")
        try:
            metadata = self._process(job, "PLOP\nG\n\nQUIT\n", 10)
            result = {
                "success": True,
                "package_version": __version__,
                "solver": metadata,
                "work_root": str(self.work_root),
                "run_directory": str(job),
                "scope": "startup, version and headless exit; not a numerical solver test",
            }
        except AVLFailure as exc:
            result = exc.result() | {"run_directory": str(job)}
        except OSError as exc:
            result = AVLFailure("EXECUTION_OR_IO_ERROR", str(exc)).result() | {
                "run_directory": str(job)
            }
        dump(job / "result.json", result)
        return result

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
            raise AVLFailure("CONTROL_LIMIT", "At most 30 controls are supported in phase 1.")

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
    def _commands(obj: Geometry, condition: FlightCondition) -> str:
        mach = obj.mach if condition.mach is None else condition.mach
        # Fresh process with no argv prevents implicit .run/.mass loading. Upstream 3.52
        # starts in NASA stability-rate axes; O/R explicitly selects NASA body-rate axes.
        lines = [
            "PLOP",
            "G",
            "",
            "LOAD model.avl",
            "CINI",
            "OPER",
            "O",
            "R",
            "",
            "M",
            f"MN {mach:.15g}",
            "",
            f"A A {condition.alpha_deg:.15g}",
            f"B B {condition.beta_deg:.15g}",
            f"R R {condition.pb_2v:.15g}",
            f"P P {condition.qc_2v:.15g}",
            f"Y Y {condition.rb_2v:.15g}",
        ]
        lines.extend(
            f"D{i} D{i} {condition.controls.get(name, 0):.15g}"
            for i, name in enumerate(obj.controls, 1)
        )
        lines += [
            "MRF",
            "X",
            "FT total.mrf",
            "ST stability.mrf",
            "SB body.mrf",
            "FN surface.mrf",
            "FS strips.mrf",
            "",
            "QUIT",
            "",
        ]
        return "\n".join(lines)

    def run(
        self,
        model_path: str,
        condition: FlightCondition | None = None,
        references: References | None = None,
        case_name="case",
        timeout_seconds=120.0,
        length_unit="unspecified",
    ) -> dict:
        if not math.isfinite(timeout_seconds) or not 0.1 <= timeout_seconds <= 600:
            raise AVLFailure("INVALID_TIMEOUT", "Timeout must be between 0.1 and 600 seconds.")
        if length_unit not in ("m", "ft", "in", "unspecified"):
            raise AVLFailure("INVALID_UNITS", "Length unit must be m, ft, in or unspecified.")
        obj = inspect_geometry(model_path, self.input_root)
        self._check_model(obj)
        condition = condition or FlightCondition()
        unknown = set(condition.controls) - set(obj.controls)
        if unknown:
            raise AVLFailure(
                "UNKNOWN_CONTROL",
                f"Unknown controls: {sorted(unknown)}",
                available_controls=obj.controls,
            )
        mach = obj.mach if condition.mach is None else condition.mach
        if not 0 <= mach < 0.7:
            raise AVLFailure("MACH_LIMIT", "Phase 1 requires 0 <= Mach < 0.7.")
        job = self._new_job(case_name)
        warnings = list(obj.warnings)
        if abs(condition.alpha_deg) > 10 or abs(condition.beta_deg) > 5 or mach > 0.6:
            warnings.append(
                "Condition approaches/exceeds small-disturbance assumptions; "
                "no stall, separation or transonic accuracy is implied."
            )
        manifest = {
            "package_version": __version__,
            "model": obj.report(),
            "condition": condition.model_dump(),
            "references_override": references.model_dump() if references else None,
            "geometry_length_unit": length_unit,
            "case_name": case_name,
            "timeout_seconds": timeout_seconds,
            "run_directory": str(job),
        }
        try:
            manifest.update(self._stage(obj, job, references))
            dump(job / "manifest.json", manifest)
            process = self._process(job, self._commands(obj, condition), timeout_seconds)
            total = parse_total(job / "total.mrf")
            stability = parse_derivatives(job / "stability.mrf", "DERMATS")
            body = parse_derivatives(job / "body.mrf", "DERMATB")
            surfaces = parse_surfaces(job / "surface.mrf")
            strips = parse_strips(job / "strips.mrf")
            actual = total["fields"]
            expected = {
                "Alpha": condition.alpha_deg,
                "Beta": condition.beta_deg,
                "Mach": mach,
                "pb/2V": condition.pb_2v,
                "qc/2V": condition.qc_2v,
                "rb/2V": condition.rb_2v,
            }
            refs = references.model_dump() if references else obj.references
            expected.update({k[0].upper() + k[1:]: v for k, v in refs.items()})
            for name, value in expected.items():
                if not math.isclose(actual[name], value, rel_tol=1e-9, abs_tol=1e-10):
                    raise AVLFailure(
                        "CONDITION_MISMATCH",
                        f"Requested {name}={value}, solver used {actual[name]}.",
                    )
            desired_controls = {name: condition.controls.get(name, 0) for name in obj.controls}
            if set(total["controls"]) != set(desired_controls) or any(
                not math.isclose(total["controls"][k], v, rel_tol=1e-12, abs_tol=1e-12)
                for k, v in desired_controls.items()
            ):
                raise AVLFailure(
                    "CONDITION_MISMATCH", "Solver control settings do not match request."
                )
            if (
                len(surfaces) != total["counts"]["surfaces"]
                or sum(len(s["rows"]) for s in strips) != total["counts"]["strips"]
            ):
                raise AVLFailure("OUTPUT_FORMAT", "Inconsistent output table dimensions.")
            changed = [
                p
                for p, h in manifest["sources"].items()
                if not Path(p).is_file() or sha256(Path(p)) != h
            ]
            if changed:
                raise AVLFailure(
                    "SOURCE_CHANGED", "Original source changed during the run.", paths=changed
                )
            dump(job / "strips.json", strips)
            with (job / "strips.csv").open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=["surface"] + list(strips[0]["rows"][0]))
                writer.writeheader()
                for surf in strips:
                    writer.writerows({"surface": surf["name"], **row} for row in surf["rows"])
            result = {
                "success": True,
                "run_directory": str(job),
                "solver": process,
                "condition": condition.model_dump(),
                "geometry_length_unit": length_unit,
                "total": total,
                "stability": stability,
                "body": body,
                "surfaces": surfaces,
                "strip_count": total["counts"]["strips"],
                "warnings": warnings,
                "conventions": {
                    "geometry": "X aft, Y right, Z up",
                    "input_rates": "standard body axes: X forward, Y right, Z down",
                    "totals": "CX/CY/CZ and Cl/Cm/Cn: standard body; CL/CD: stability; "
                    "Cl'/Cn': standard stability",
                    "drag": "CDind is near-field induced; CDff is Trefftz induced; "
                    "CDvis uses supplied profile drag, not a viscous flow solution",
                    "moment_reference": "Xref/Yref/Zref; not necessarily aircraft CG",
                },
                "artifacts": {
                    name: str(job / name)
                    for name in (
                        "manifest.json",
                        "commands.txt",
                        "stdout.log",
                        "stderr.log",
                        "total.mrf",
                        "stability.mrf",
                        "body.mrf",
                        "surface.mrf",
                        "strips.mrf",
                        "strips.csv",
                        "strips.json",
                        "result.json",
                    )
                },
                "source_preserved": True,
            }
        except AVLFailure as exc:
            result = exc.result() | {
                "run_directory": str(job),
                "manifest": str(job / "manifest.json"),
            }
        except (OSError, UnicodeError, ValueError, IndexError, KeyError) as exc:
            result = AVLFailure("EXECUTION_OR_PARSE_ERROR", str(exc)).result() | {
                "run_directory": str(job)
            }
        dump(job / "result.json", result)
        return result

    def sweep(
        self,
        model_path: str,
        conditions: list[FlightCondition],
        references: References | None = None,
        case_name="sweep",
        timeout_seconds=300.0,
        length_unit="unspecified",
        stop_on_error=True,
    ) -> dict:
        if not 1 <= len(conditions) <= 25:
            raise AVLFailure("SWEEP_LIMIT", "Supply 1-25 explicit conditions.")
        if not math.isfinite(timeout_seconds) or not 1 <= timeout_seconds <= 1800:
            raise AVLFailure("INVALID_TIMEOUT", "Sweep timeout must be between 1 and 1800 seconds.")
        obj = inspect_geometry(model_path, self.input_root)
        self._check_model(obj)
        for c in conditions:
            if set(c.controls) - set(obj.controls):
                raise AVLFailure(
                    "UNKNOWN_CONTROL", "A sweep condition references unknown controls."
                )
        job = self._new_job(case_name)
        baseline = {str(obj.path): obj.source_hash} | {
            d["path"]: d["sha256"] for d in obj.dependencies
        }
        dump(
            job / "manifest.json",
            {
                "model_path": str(obj.path),
                "source_hashes": baseline,
                "conditions": [c.model_dump() for c in conditions],
                "total_timeout_seconds": timeout_seconds,
            },
        )
        started = time.monotonic()
        results = []
        error = None
        for i, c in enumerate(conditions):
            remaining = timeout_seconds - (time.monotonic() - started)
            if remaining < 0.1:
                error = {"code": "TIMEOUT", "message": "Total sweep time limit reached."}
                break
            if any(not Path(p).is_file() or sha256(Path(p)) != h for p, h in baseline.items()):
                error = {"code": "SOURCE_CHANGED", "message": "Input changed between sweep cases."}
                break
            try:
                item = self.run(
                    model_path,
                    c,
                    references,
                    f"{case_name[:40]}_{i:03d}",
                    min(120.0, remaining),
                    length_unit,
                )
            except AVLFailure as exc:
                item = exc.result()
            results.append(item)
            if not item["success"] and stop_on_error:
                error = item["error"]
                break
        complete = len(results) == len(conditions)
        result = {
            "success": complete and all(x["success"] for x in results),
            "requested_count": len(conditions),
            "attempted_count": len(results),
            "completed_count": sum(x["success"] for x in results),
            "results": results,
            "run_directory": str(job),
            "elapsed_seconds": time.monotonic() - started,
            "summary_csv": str(job / "summary.csv"),
        }
        if error:
            result["error"] = error
        elif not result["success"]:
            result["error"] = {"code": "PARTIAL_FAILURE", "message": "Some sweep cases failed."}
        with (job / "summary.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=[
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
                ],
            )
            writer.writeheader()
            for i, item in enumerate(results):
                vals = item.get("total", {}).get("fields", {})
                writer.writerow(
                    {
                        "index": i,
                        "success": item["success"],
                        "alpha_deg": conditions[i].alpha_deg,
                        "beta_deg": conditions[i].beta_deg,
                        **{
                            k: vals.get(k)
                            for k in (
                                "Mach",
                                "CLtot",
                                "CDtot",
                                "CDff",
                                "Cmtot",
                                "CYtot",
                                "Cltot",
                                "Cntot",
                            )
                        },
                        "run_directory": item.get("run_directory"),
                    }
                )
        dump(job / "result.json", result)
        return result
