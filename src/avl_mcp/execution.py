"""Native batch protocol, shared result validation and selective output collection."""

import csv
import math
import os
import re
import signal
import subprocess
import time
import uuid

from .geometry import sha256
from .models import AVLFailure, output_selection
from .outputs import parse_derivatives, parse_strips, parse_surfaces, parse_total
from .runner import ERROR_PATTERNS, SUPPORTED_AVL, dump

CONVENTIONS = {
    "geometry": "X aft, Y right, Z up",
    "input_rates": "standard body axes: X forward, Y right, Z down",
    "totals": "CX/CY/CZ and Cl/Cm/Cn: standard body; CL/CD: stability; Cl'/Cn': standard stability",
    "drag": "CDind is near-field induced; CDff is Trefftz induced; "
    "CDvis uses supplied profile drag, not a viscous flow solution",
    "moment_reference": "Xref/Yref/Zref; not necessarily aircraft CG",
}
FILENAMES = {
    "total": "total.mrf",
    "stability": "stability.mrf",
    "body": "body.mrf",
    "surfaces": "surface.mrf",
    "strips": "strips.mrf",
}


def validate_request(runner, obj, conditions, references, outputs, length_unit, timeout):
    runner._check_model(obj)
    if not 1 <= len(conditions) <= 10000:
        raise AVLFailure("SWEEP_LIMIT", "Supply 1-10000 explicit conditions.")
    if not math.isfinite(timeout) or not 0.1 <= timeout <= 86400:
        raise AVLFailure("INVALID_TIMEOUT", "Timeout must be between 0.1 and 86400 seconds.")
    if length_unit not in ("m", "ft", "in", "unspecified"):
        raise AVLFailure("INVALID_UNITS", "Length unit must be m, ft, in or unspecified.")
    for condition in conditions:
        unknown = set(condition.controls) - set(obj.controls)
        if unknown:
            raise AVLFailure(
                "UNKNOWN_CONTROL",
                f"Unknown controls: {sorted(unknown)}",
                available_controls=obj.controls,
            )
        if not 0 <= (obj.mach if condition.mach is None else condition.mach) < 0.7:
            raise AVLFailure("MACH_LIMIT", "Require 0 <= Mach < 0.7.")
    return output_selection(outputs)


def startup_commands():
    return ["PLOP", "G", "", "LOAD model.avl", "CINI", "OPER", "O", "R", "", "MRF"]


def condition_commands(obj, condition, outputs, prefix="", set_mach=True):
    lines = []
    if set_mach:
        mach = obj.mach if condition.mach is None else condition.mach
        lines += ["M", f"MN {mach:.15g}", ""]
    lines += [
        f"A A {condition.alpha_deg:.15g}",
        f"B B {condition.beta_deg:.15g}",
        f"R R {condition.pb_2v:.15g}",
        f"P P {condition.qc_2v:.15g}",
        f"Y Y {condition.rb_2v:.15g}",
    ]
    # Every control is explicit, including zeros after a deflected condition.
    lines += [
        f"D{i} D{i} {condition.controls.get(name, 0):.15g}"
        for i, name in enumerate(obj.controls, 1)
    ]
    lines += ["X"]
    for kind, command in [
        ("stability", "ST"),
        ("body", "SB"),
        ("surfaces", "FN"),
        ("strips", "FS"),
        ("total", "FT"),
    ]:
        if kind in outputs:
            lines.append(f"{command} {prefix}{FILENAMES[kind]}")
    # FT is last: its completed preamble is the per-case commit marker.
    return lines


def check_total(total, obj, condition, references):
    actual = total["fields"]
    expected = {
        "Alpha": condition.alpha_deg,
        "Beta": condition.beta_deg,
        "Mach": obj.mach if condition.mach is None else condition.mach,
        "pb/2V": condition.pb_2v,
        "qc/2V": condition.qc_2v,
        "rb/2V": condition.rb_2v,
    }
    refs = references.model_dump() if references else obj.references
    expected.update({k[0].upper() + k[1:]: v for k, v in refs.items()})
    for name, value in expected.items():
        if not math.isclose(actual[name], value, rel_tol=1e-9, abs_tol=1e-10):
            raise AVLFailure(
                "CONDITION_MISMATCH", f"Requested {name}={value}, solver used {actual[name]}."
            )
    desired = {name: condition.controls.get(name, 0) for name in obj.controls}
    if set(total["controls"]) != set(desired) or any(
        not math.isclose(total["controls"][k], v, rel_tol=1e-12, abs_tol=1e-12)
        for k, v in desired.items()
    ):
        raise AVLFailure("CONDITION_MISMATCH", "Solver control settings do not match request.")


def collect_case(folder, obj, condition, references, outputs, length_unit):
    total = parse_total(folder / "total.mrf")
    check_total(total, obj, condition, references)
    result = {
        "success": True,
        "run_directory": str(folder),
        "total": total,
        "condition": condition.model_dump(),
        "geometry_length_unit": length_unit,
        "outputs": outputs,
        "conventions": CONVENTIONS,
        "warnings": list(obj.warnings),
    }
    for kind, record in [("stability", "DERMATS"), ("body", "DERMATB")]:
        if kind in outputs:
            path = folder / FILENAMES[kind]
            check_total(parse_total(path, record), obj, condition, references)
            result[kind] = parse_derivatives(path, record)
    if "surfaces" in outputs:
        result["surfaces"] = parse_surfaces(folder / "surface.mrf")
        if len(result["surfaces"]) != total["counts"]["surfaces"]:
            raise AVLFailure("OUTPUT_FORMAT", "Surface count mismatch.")
    if "strips" in outputs:
        strips = parse_strips(folder / "strips.mrf")
        if sum(len(s["rows"]) for s in strips) != total["counts"]["strips"]:
            raise AVLFailure("OUTPUT_FORMAT", "Strip count mismatch.")
        result["strip_count"] = total["counts"]["strips"]
        dump(folder / "strips.json", strips)
        with (folder / "strips.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["surface"] + list(strips[0]["rows"][0]))
            writer.writeheader()
            for surf in strips:
                writer.writerows({"surface": surf["name"], **row} for row in surf["rows"])
    if (
        abs(condition.alpha_deg) > 10
        or abs(condition.beta_deg) > 5
        or total["fields"]["Mach"] > 0.6
    ):
        result["warnings"].append(
            "Condition approaches/exceeds small-disturbance assumptions; "
            "no stall, separation or transonic accuracy is implied."
        )
    names = [FILENAMES[k] for k in outputs]
    if "strips" in outputs:
        names += ["strips.json", "strips.csv"]
    result["artifacts"] = {name: str(folder / name) for name in names}
    return result


class NativeSession:
    """One owned AVL process; interactive writes bound input buffering and checkpoint each case."""

    def __init__(self, runner, folder, obj, references, deadline, cancelled):
        self.runner, self.folder, self.obj = runner, folder, obj
        self.deadline, self.cancelled = deadline, cancelled
        self.last_mach = None
        self.started = time.monotonic()
        self.proc = None
        self.handles = []
        runner._stage(obj, folder, references)
        if not runner.executable.is_file() or not os.access(runner.executable, os.X_OK):
            raise AVLFailure("EXECUTABLE_NOT_FOUND", str(runner.executable))
        self.sha = sha256(runner.executable)
        try:
            out = (folder / "stdout.log").open("wb")
            self.handles.append(out)
            err = (folder / "stderr.log").open("wb")
            self.handles.append(err)
            env = dict(os.environ, LC_ALL="C", LANG="C", GFORTRAN_UNBUFFERED_ALL="1")
            env.pop("DISPLAY", None)
            self.proc = subprocess.Popen(
                [str(runner.executable)],
                cwd=folder,
                env=env,
                stdin=subprocess.PIPE,
                stdout=out,
                stderr=err,
                start_new_session=(os.name != "nt"),
            )
            self.send(startup_commands())
        except BaseException:
            self.close()
            raise

    def send(self, lines):
        text = "\n".join(lines) + "\n"
        with (self.folder / "commands.txt").open("a") as stream:
            stream.write(text)
        try:
            self.proc.stdin.write(text.encode("ascii"))
            self.proc.stdin.flush()
        except BrokenPipeError as exc:
            raise AVLFailure("SOLVER_EXIT", "AVL closed its command input.") from exc

    def logs(self):
        return "\n".join(
            (self.folder / name).read_text(errors="replace")
            for name in ("stdout.log", "stderr.log")
        )

    def check_logs(self):
        text = self.logs()
        found = [p for p in ERROR_PATTERNS if p in text.lower()]
        if found:
            raise AVLFailure(
                "SOLVER_REPORTED_ERROR", "AVL reported an unsuccessful operation.", markers=found
            )
        version = re.search(r"Athena Vortex Lattice\s+Program\s+Version\s+(\d+\.\d+)", text)
        if not version or version[1] != SUPPORTED_AVL:
            raise AVLFailure("UNSUPPORTED_VERSION", "Require AVL 3.52 startup banner.")

    def check_running(self):
        if self.cancelled and self.cancelled():
            raise AVLFailure("CANCELLED", "Cancellation requested; completed cases are retained.")
        if time.monotonic() >= self.deadline:
            raise AVLFailure("TIMEOUT", "Batch execution time budget reached.")
        if sum((self.folder / n).stat().st_size for n in ("stdout.log", "stderr.log")) > 32_000_000:
            raise AVLFailure("OUTPUT_LIMIT", "AVL exceeded the 32 MB log limit.")
        if self.proc.poll() is not None:
            self.check_logs()
            code = "SOLVER_EXIT" if self.proc.returncode else "MISSING_OUTPUT"
            raise AVLFailure(code, "AVL exited before finishing the requested condition.")

    def execute(self, index, condition, references, outputs, length_unit):
        self.check_running()
        folder = self.folder / f"c{index:05d}"
        folder.mkdir()
        mach = self.obj.mach if condition.mach is None else condition.mach
        self.send(
            condition_commands(
                self.obj, condition, outputs, folder.name + "/", set_mach=mach != self.last_mach
            )
        )
        self.last_mach = mach
        marker = folder / "total.mrf"
        parse_tries = 0
        pause = 0.001
        while True:
            self.check_running()
            if marker.exists():
                try:
                    # AVL closes each earlier output file before writing FT.
                    parse_total(marker)
                    break
                except (AVLFailure, ValueError):
                    parse_tries += 1
                    if parse_tries >= 10:
                        raise
            time.sleep(pause)
            pause = min(0.05, pause * 1.5)
        self.check_logs()
        result = collect_case(folder, self.obj, condition, references, outputs, length_unit)
        result.update(index=index, source_preserved=True, solver=self.metadata())
        result["artifacts"].update(
            {name: str(self.folder / name) for name in ("commands.txt", "stdout.log", "stderr.log")}
        )
        result["artifacts"]["result.json"] = str(folder / "result.json")
        dump(folder / "result.json", result)
        return result

    def metadata(self):
        return {
            "executable": str(self.runner.executable),
            "executable_sha256": self.sha,
            "version": SUPPORTED_AVL,
            "display_unset": True,
            "elapsed_seconds": time.monotonic() - self.started,
            "returncode": self.proc.poll() if self.proc else None,
            "shared_process": True,
        }

    def close(self):
        if self.proc:
            if self.proc.poll() is None:
                try:
                    self.send(["", "QUIT", ""])
                    self.proc.stdin.close()
                    self.proc.wait(timeout=0.5)
                except (OSError, AVLFailure, subprocess.TimeoutExpired):
                    if self.proc.poll() is None:
                        if os.name != "nt":
                            os.killpg(self.proc.pid, signal.SIGKILL)
                        else:
                            self.proc.kill()
                        self.proc.wait()
            dump(self.folder / "process.json", self.metadata())
        for handle in self.handles:
            handle.close()


def execute_cases(
    runner,
    folder,
    obj,
    conditions,
    references,
    outputs,
    length_unit,
    timeout,
    stop_on_error=True,
    completed=None,
    on_case=None,
    cancelled=None,
):
    results = dict(completed or {})
    pending = sorted(
        (i for i in range(len(conditions)) if i not in results),
        key=lambda i: (obj.mach if conditions[i].mach is None else conditions[i].mach, i),
    )
    deadline = time.monotonic() + timeout
    session = None
    session_count = 0
    error = None
    try:
        for index in pending:
            try:
                if cancelled and cancelled():
                    raise AVLFailure("CANCELLED", "Cancellation requested.")
                if time.monotonic() >= deadline:
                    raise AVLFailure("TIMEOUT", "Batch execution time budget reached.")
                if session is None or session_count >= 128:
                    if session:
                        session.close()
                        session = None
                    attempt = folder / ("batch-" + uuid.uuid4().hex)
                    attempt.mkdir()
                    session = NativeSession(runner, attempt, obj, references, deadline, cancelled)
                    session_count = 0
                item = session.execute(index, conditions[index], references, outputs, length_unit)
                session_count += 1
            except (AVLFailure, OSError, ValueError, IndexError, KeyError) as exc:
                failure = (
                    exc
                    if isinstance(exc, AVLFailure)
                    else AVLFailure("EXECUTION_OR_PARSE_ERROR", str(exc))
                )
                item = failure.result() | {
                    "index": index,
                    "condition": conditions[index].model_dump(),
                }
                error = item["error"]
                if session:
                    item["run_directory"] = str(session.folder)
                    session.close()
                    session = None
            results[index] = item
            if on_case:
                on_case(index, item)
            if not item["success"] and (
                stop_on_error or error["code"] in ("TIMEOUT", "CANCELLED", "SOURCE_CHANGED")
            ):
                break
    finally:
        if session:
            session.close()
    complete = len(results) == len(conditions) and all(r["success"] for r in results.values())
    response = {
        "success": complete,
        "requested_count": len(conditions),
        "attempted_count": len(results),
        "completed_count": sum(r["success"] for r in results.values()),
        "results": [results[i] for i in sorted(results)],
        "run_directory": str(folder),
    }
    if not complete:
        response["error"] = error or {"code": "PARTIAL_FAILURE", "message": "Some cases failed."}
    return response
