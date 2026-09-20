"""Durable local jobs. Detached workers own their AVL processes and cancel cooperatively."""

import errno
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import __version__
from .execution import execute_cases, validate_request
from .geometry import inspect_geometry, sha256
from .models import AVLFailure, FlightCondition
from .runner import AVLRunner

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def atomic_json(path, value):
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with tmp.open("x") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def read_json(path):
    if path.stat().st_size > 64_000_000:
        raise AVLFailure("OUTPUT_TOO_LARGE", "JSON file exceeds 64 MB.")
    return json.loads(path.read_text())


@contextmanager
def lock(path, blocking=True):
    stream = path.open("a+b")
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt

            if not path.stat().st_size:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        acquired = True
        yield
    except OSError as exc:
        if not acquired and exc.errno in (errno.EAGAIN, errno.EACCES):
            raise AVLFailure("JOB_BUSY", "A worker already owns this job.") from exc
        raise
    finally:
        if acquired:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def busy(path):
    try:
        with lock(path, False):
            return False
    except AVLFailure as exc:
        if exc.code != "JOB_BUSY":
            raise
        return True


def implementation_hash():
    return digest({p.name: sha256(p) for p in sorted(Path(__file__).parent.glob("*.py"))})


class JobManager:
    def __init__(self, runner):
        self.runner = runner
        self.root = runner.work_root / "jobs"

    def directory(self, job_id):
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise AVLFailure("UNKNOWN_JOB", "Use a background job_id returned by avl.submit.")
        path = self.root / job_id
        if not path.is_dir() or path.is_symlink() or path.resolve().parent != self.root.resolve():
            raise AVLFailure("UNKNOWN_JOB", "Job was not found in this work root.")
        return path

    def submit(
        self,
        model_path,
        conditions,
        references=None,
        case_name="job",
        timeout_seconds=1800,
        length_unit="unspecified",
        stop_on_error=True,
        outputs=None,
    ):
        obj = inspect_geometry(model_path, self.runner.input_root)
        selected = validate_request(
            self.runner, obj, conditions, references, outputs, length_unit, timeout_seconds
        )
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,47}", case_name):
            raise AVLFailure("INVALID_CASE_NAME", "Use 1-48 ASCII letters, numbers, '_' or '-'.")
        if not self.runner.executable.is_file() or not os.access(self.runner.executable, os.X_OK):
            raise AVLFailure("EXECUTABLE_NOT_FOUND", str(self.runner.executable))
        self.root.mkdir(parents=True, exist_ok=True)
        job = self.root / uuid.uuid4().hex
        job.mkdir()
        snapshot = job / "inputs"
        snapshot.mkdir()
        staged = self.runner._stage(obj, snapshot, references)
        snapshot_hashes = {
            str(p.relative_to(job)): sha256(p) for p in snapshot.iterdir() if p.is_file()
        }
        spec = {
            "schema_version": 1,
            "package_version": __version__,
            "implementation_sha256": implementation_hash(),
            "case_name": case_name,
            "model_path": str(obj.path),
            "sources": staged["sources"],
            "snapshot_hashes": snapshot_hashes,
            "conditions": [c.model_dump() for c in conditions],
            "outputs": selected,
            "references_override": references.model_dump() if references else None,
            "length_unit": length_unit,
            "timeout_seconds": timeout_seconds,
            "stop_on_error": stop_on_error,
            "executable": str(self.runner.executable),
            "executable_sha256": sha256(self.runner.executable),
            "max_vortices": self.runner.max_vortices,
        }
        spec["fingerprint"] = digest(spec)
        atomic_json(job / "spec.json", spec)
        (job / "cases").mkdir()
        (job / "records").mkdir()
        (job / "worker.lock").touch()
        with lock(job / "control.lock"):
            self._launch(
                job,
                {
                    "job_id": job.name,
                    "requested_count": len(conditions),
                    "completed_count": 0,
                    "failed_count": 0,
                    "attempt": 0,
                },
            )
        return self.status(job.name)

    def _launch(self, job, previous):
        token = uuid.uuid4().hex
        state = previous | {
            "state": "queued",
            "attempt": previous["attempt"] + 1,
            "launch_token": token,
            "updated_at": time.time(),
            "error": None,
        }
        (job / "cancel.request").unlink(missing_ok=True)
        atomic_json(job / "state.json", state)
        env = dict(os.environ)
        # Works from both an installed wheel and a source checkout; no shared venv mutation.
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
        args = [sys.executable, "-m", "avl_mcp.worker", str(self.runner.work_root), job.name, token]
        kwargs = (
            {"start_new_session": True}
            if os.name != "nt"
            else {
                "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            }
        )
        try:
            with (job / "worker.log").open("ab") as log:
                process = subprocess.Popen(
                    args,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    env=env,
                    close_fds=True,
                    **kwargs,
                )
            threading.Thread(target=process.wait, daemon=True).start()
        except OSError as exc:
            state.update(state="failed", error={"code": "WORKER_START", "message": str(exc)})
            atomic_json(job / "state.json", state)
            raise AVLFailure("WORKER_START", str(exc), job_id=job.name) from exc

    def status(self, job_id):
        job = self.directory(job_id)
        with lock(job / "control.lock"):
            state = read_json(job / "state.json")
            # Allow startup time before diagnosing a queued worker lost before claiming its lock.
            if state["state"] not in TERMINAL and not busy(job / "worker.lock"):
                if state["state"] == "running" or time.time() - state["updated_at"] > 30:
                    state["state"] = "interrupted"
                    state["error"] = {
                        "code": "WORKER_INTERRUPTED",
                        "message": "Worker is no longer active; resume is available.",
                    }
        return {
            "success": True,
            **{k: v for k, v in state.items() if k != "launch_token"},
            "cancel_requested": (job / "cancel.request").exists(),
            "run_directory": str(job),
            "result_json": str(job / "result.json"),
        }

    def cancel(self, job_id):
        job = self.directory(job_id)
        with lock(job / "control.lock"):
            state = read_json(job / "state.json")
            if state["state"] not in TERMINAL:
                (job / "cancel.request").touch()
        return self.status(job_id)

    def verify_spec(self, job):
        spec = read_json(job / "spec.json")
        fingerprint = spec.pop("fingerprint")
        if digest(spec) != fingerprint:
            raise AVLFailure("JOB_SPEC_CHANGED", "Saved job settings were modified.")
        spec["fingerprint"] = fingerprint
        self.runner._check_sources(spec["sources"])
        for name, expected in spec["snapshot_hashes"].items():
            path = (job / name).resolve()
            if (
                not path.is_relative_to(job.resolve())
                or not path.is_file()
                or sha256(path) != expected
            ):
                raise AVLFailure("SOURCE_CHANGED", "Staged model or dependency changed.")
        if (
            str(self.runner.executable) != spec["executable"]
            or sha256(self.runner.executable) != spec["executable_sha256"]
        ):
            raise AVLFailure("SOLVER_CHANGED", "Resume requires the original AVL executable.")
        if implementation_hash() != spec["implementation_sha256"]:
            raise AVLFailure(
                "IMPLEMENTATION_CHANGED", "Resume requires the original MCP implementation."
            )
        return spec

    def completed(self, job, spec):
        results = {}
        for path in sorted((job / "cases").glob("*.json")):
            try:
                rec = read_json(path)
                item = rec["result"]
                i = item["index"]
                if (
                    not item["success"]
                    or i != int(path.stem)
                    or not rec["artifact_hashes"]
                    or rec["fingerprint"] != spec["fingerprint"]
                    or digest(item) != rec["result_sha256"]
                    or not 0 <= i < len(spec["conditions"])
                    or item["condition"] != spec["conditions"][i]
                ):
                    continue
                for name, expected in rec["artifact_hashes"].items():
                    artifact = Path(name).resolve()
                    if (
                        not artifact.is_relative_to(job.resolve())
                        or not artifact.is_file()
                        or sha256(artifact) != expected
                    ):
                        break
                else:
                    results[i] = item
            except (OSError, ValueError, KeyError, TypeError, AVLFailure):
                # Invalid/incomplete checkpoints are recomputed; attempt records stay on disk.
                continue
        return results

    def resume(self, job_id):
        job = self.directory(job_id)
        with lock(job / "control.lock"):
            if busy(job / "worker.lock"):
                raise AVLFailure("JOB_BUSY", "The job is still active.")
            state = read_json(job / "state.json")
            if state["state"] == "queued" and time.time() - state["updated_at"] < 30:
                raise AVLFailure("JOB_BUSY", "The worker is starting.")
            spec = self.verify_spec(job)
            completed = self.completed(job, spec)
            if len(completed) == len(spec["conditions"]):
                state.update(
                    state="completed", completed_count=len(completed), failed_count=0, error=None
                )
                atomic_json(
                    job / "result.json",
                    {
                        "success": True,
                        "requested_count": len(completed),
                        "attempted_count": len(completed),
                        "completed_count": len(completed),
                        "results": [completed[i] for i in sorted(completed)],
                        "run_directory": str(job),
                    },
                )
                atomic_json(job / "state.json", state)
            else:
                state.update(completed_count=len(completed), failed_count=0)
                self._launch(job, state)
        return self.status(job_id)

    def results(self, job_id, offset=0, limit=20, fields=None, indices=None):
        if not 0 <= offset or not 1 <= limit <= 100:
            raise AVLFailure("INVALID_PAGE", "offset >= 0 and 1 <= limit <= 100 are required.")
        if indices is not None and (len(indices) > 10000 or any(i < 0 for i in indices)):
            raise AVLFailure("INVALID_INDICES", "Use nonnegative case indices.")
        if fields is not None and (len(fields) > 100 or any(not f or len(f) > 160 for f in fields)):
            raise AVLFailure("INVALID_FIELDS", "Select at most 100 nonempty field paths.")
        if re.fullmatch(r"[a-f0-9]{32}", job_id):
            folder = self.directory(job_id)
            paths = sorted((folder / "cases").glob("*.json"))
            candidates = [(int(p.stem), p) for p in paths]

            def fetch(p):
                record = read_json(p)
                if digest(record["result"]) != record["result_sha256"]:
                    raise AVLFailure("CHECKPOINT_INVALID", "Saved result content changed.")
                return record["result"]
        elif re.fullmatch(r"[A-Za-z0-9_-]{1,120}", job_id):
            folder = self.runner.work_root / "runs" / job_id
            if (
                folder.is_symlink()
                or folder.resolve().parent != (self.runner.work_root / "runs").resolve()
            ):
                raise AVLFailure("UNKNOWN_JOB", "Run is outside this work root.")
            if not (folder / "result.json").is_file():
                raise AVLFailure("UNKNOWN_JOB", "Unknown run identifier.")
            checkpoints = sorted(folder.glob("case-*.json"))
            if checkpoints:
                # Large sweeps are paged from individual files, not a monolithic JSON load.
                candidates = [(int(p.stem.removeprefix("case-")), p) for p in checkpoints]

                def fetch(path):
                    return read_json(path)
            else:
                record = read_json(folder / "result.json")
                items = record.get("results", [record])
                candidates = [(r.get("index", i), r) for i, r in enumerate(items)]

                def fetch(item):
                    return item
        else:
            raise AVLFailure("UNKNOWN_JOB", "Use the returned job_id; paths are not accepted.")
        if indices is not None:
            wanted = set(indices)
            candidates = [(i, p) for i, p in candidates if i in wanted]
        selected = candidates[offset : offset + limit]
        rows = []
        for i, candidate in selected:
            item = fetch(candidate)
            row = {
                k: item[k]
                for k in (
                    "success",
                    "condition",
                    "error",
                    "outputs",
                    "run_directory",
                    "result_context",
                )
                if k in item
            }
            row["index"] = i
            row.setdefault(
                "result_context",
                {"schema_version": None, "metadata_status": "unavailable_in_saved_result"},
            )
            if fields is None:
                row["total"] = item.get("total", {}).get("fields", {})
                row["artifacts"] = item.get("artifacts", {})
            elif item["success"]:
                row["fields"] = {}
                for field in fields:
                    value = item
                    try:
                        for part in field.split("."):
                            value = value[part]
                    except (KeyError, TypeError) as exc:
                        raise AVLFailure(
                            "FIELD_NOT_AVAILABLE",
                            f"Field {field!r} was not saved for case {i}.",
                            available_outputs=item.get("outputs"),
                        ) from exc
                    row["fields"][field] = value
            rows.append(row)
        response = {
            "success": True,
            "job_id": job_id,
            "matched_count": len(candidates),
            "offset": offset,
            "next_offset": offset + limit if offset + limit < len(candidates) else None,
            "rows": rows,
            "solver_executed": False,
        }
        if len(json.dumps(response)) > 2_000_000:
            raise AVLFailure("RESPONSE_TOO_LARGE", "Use fewer cases or narrower fields.")
        return response


def run_worker(work_root, job_id, token):
    job = Path(work_root) / "jobs" / job_id
    # Use the same path validation as MCP before reading any saved settings.
    manager = JobManager(AVLRunner("/unused", work_root))
    job = manager.directory(job_id)
    # Status probes briefly acquire this lock; a worker must wait for those probes.
    with lock(job / "worker.lock"):
        with lock(job / "control.lock"):
            state = read_json(job / "state.json")
            if state["launch_token"] != token or state["state"] != "queued":
                return
            state.update(updated_at=time.time(), worker_pid=os.getpid())
            atomic_json(job / "state.json", state)

        def cancelled():
            return (job / "cancel.request").exists()

        def update(**changes):
            with lock(job / "control.lock"):
                state.update(changes, updated_at=time.time())
                atomic_json(job / "state.json", state)

        try:
            spec = read_json(job / "spec.json")
            runner = AVLRunner(spec["executable"], work_root, max_vortices=spec["max_vortices"])
            manager = JobManager(runner)
            spec = manager.verify_spec(job)
            completed = manager.completed(job, spec)
            obj = inspect_geometry(str(job / "inputs/model.avl"))
            conditions = [FlightCondition(**c) for c in spec["conditions"]]
            slots = Path(work_root) / "jobs/.slots"
            slots.mkdir(exist_ok=True)
            while True:
                if cancelled():
                    raise AVLFailure("CANCELLED", "Cancelled while queued.")
                slot = None
                for i in range(2):
                    candidate = lock(slots / f"{i}.lock", False)
                    try:
                        candidate.__enter__()
                        slot = candidate
                        break
                    except AVLFailure as exc:
                        if exc.code != "JOB_BUSY":
                            raise
                if slot:
                    break
                time.sleep(0.1)
            try:
                update(state="running", completed_count=len(completed), failed_count=0)

                def checkpoint(index, item):
                    runner._check_sources(spec["sources"])
                    if (
                        item["success"]
                        and item["solver"]["executable_sha256"] != spec["executable_sha256"]
                    ):
                        raise AVLFailure("SOLVER_CHANGED", "AVL changed after job validation.")
                    stable = {
                        p: sha256(Path(p))
                        for name, p in item.get("artifacts", {}).items()
                        if name not in ("commands.txt", "stdout.log", "stderr.log")
                    }
                    rec = {
                        "fingerprint": spec["fingerprint"],
                        "result": item,
                        "result_sha256": digest(item),
                        "artifact_hashes": stable,
                    }
                    atomic_json(job / "records" / f"{state['attempt']:04d}-{index:05d}.json", rec)
                    atomic_json(job / "cases" / f"{index:05d}.json", rec)
                    if item["success"]:
                        state["completed_count"] += 1
                    else:
                        state["failed_count"] += 1
                    update(current_index=index, error=item.get("error"))

                result = execute_cases(
                    runner,
                    job,
                    obj,
                    conditions,
                    None,
                    spec["outputs"],
                    spec["length_unit"],
                    spec["timeout_seconds"],
                    spec["stop_on_error"],
                    completed,
                    checkpoint,
                    cancelled,
                )
                runner._check_sources(spec["sources"])
                atomic_json(job / "result.json", result)
                end = (
                    "completed"
                    if result["success"]
                    else ("cancelled" if result["error"]["code"] == "CANCELLED" else "failed")
                )
                update(state=end, error=result.get("error"))
            finally:
                slot.__exit__(None, None, None)
        except Exception as exc:
            failure = exc if isinstance(exc, AVLFailure) else AVLFailure("WORKER_ERROR", str(exc))
            update(
                state="cancelled" if failure.code == "CANCELLED" else "failed",
                error=failure.result()["error"],
            )
