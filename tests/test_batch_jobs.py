import json
import os
import shutil
import signal
import sys
import time
from pathlib import Path

import pytest

from avl_mcp.geometry import sha256
from avl_mcp.jobs import JobManager, busy, read_json
from avl_mcp.models import AVLFailure, FlightCondition, References
from avl_mcp.runner import AVLRunner


def wait_for(manager, job_id, predicate, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        state = manager.status(job_id)
        if predicate(state):
            return state
        time.sleep(0.025)
    raise AssertionError(state)


def finished(manager, job_id):
    return wait_for(
        manager, job_id, lambda s: s["state"] in ("completed", "failed", "cancelled", "interrupted")
    )


def fake_manager(tmp_path, vanilla, delay=0.025):
    fixtures = Path(__file__).parent / "fixtures"
    binary = tmp_path / "fake-avl"
    binary.write_text(f"""#!{sys.executable}
import sys,time,shutil
from pathlib import Path
print('Athena Vortex Lattice Program Version 3.52',flush=True)
names={{'FT':'total','ST':'stability','SB':'body','FN':'surface','FS':'strips'}}
for line in sys.stdin:
    words=line.split()
    if not words: continue
    if words[0]=='QUIT': break
    if words[0]=='X': time.sleep({delay!r})
    if words[0] in names:
        shutil.copyfile(Path({str(fixtures)!r})/(names[words[0]]+'.mrf'),words[1])
""")
    binary.chmod(0o755)
    shutil.copytree(vanilla.parent, tmp_path / "model")
    runner = AVLRunner(str(binary), str(tmp_path / "work"))
    return JobManager(runner), tmp_path / "model/vanilla.avl"


def conditions(count):
    return [FlightCondition(alpha_deg=3, mach=0.2) for _ in range(count)]


@pytest.mark.native
@pytest.mark.parametrize("fixture_name", ["vanilla", "bd"])
def test_shared_process_matches_independent_conditions(runner, fixture_name, request):
    model = request.getfixturevalue(fixture_name)
    controls = runner.inspect(str(model))["geometry"]["controls"]
    # Exercise Mach regrouping, resetting deflections/rates, and both signs of sideslip.
    name = controls[0]
    cases = [
        FlightCondition(
            alpha_deg=3,
            mach=0.2,
            beta_deg=4,
            controls={name: 2},
            pb_2v=0.001,
            qc_2v=0.0002,
            rb_2v=-0.002,
        ),
        FlightCondition(alpha_deg=-1, mach=0.1),
        FlightCondition(alpha_deg=0, mach=0.2),
        FlightCondition(alpha_deg=5, mach=0.1, beta_deg=-3, controls={name: -1}),
    ]
    refs = References(sref=11, cref=1.2, bref=12, xref=0.7, yref=0.01, zref=0.02)
    batch = runner.sweep(str(model), cases, refs, outputs=None)
    assert batch["success"], batch
    assert len(list(Path(batch["run_directory"]).glob("batch-*"))) == 1
    assert [r["index"] for r in batch["results"]] == list(range(4))
    for item, case in zip(batch["results"], cases, strict=True):
        single = runner.run(str(model), case, refs)
        assert single["success"], single
        assert item["total"]["fields"] == pytest.approx(single["total"]["fields"], abs=1e-10)
        for table in ("stability", "body"):
            assert item[table]["derivatives"] == pytest.approx(
                single[table]["derivatives"], abs=1e-10
            )


@pytest.mark.native
def test_more_than_25_and_chunk_boundary(runner, vanilla):
    batch = runner.sweep(str(vanilla), conditions(130), outputs=["total"])
    assert batch["success"] and batch["completed_count"] == 130
    root = Path(batch["run_directory"])
    assert len(list(root.glob("batch-*"))) == 2
    assert not list(root.rglob("body.mrf")) and not list(root.rglob("strips.mrf"))
    assert all(
        r["total"]["fields"]["CLtot"] == pytest.approx(0.6754170403902354) for r in batch["results"]
    )


@pytest.mark.native
def test_single_selective_outputs_and_query(runner, vanilla):
    out = runner.run(str(vanilla), conditions(1)[0], outputs=["body"])
    assert out["success"] and "body" in out and "stability" not in out
    folder = Path(out["run_directory"])
    assert not (folder / "strips.mrf").exists()
    result = JobManager(runner).results(out["job_id"], fields=["body.derivatives.Cmq"])
    assert result["rows"][0]["fields"]["body.derivatives.Cmq"] == out["body"]["derivatives"]["Cmq"]
    assert result["solver_executed"] is False


def test_cancel_resume_retains_successful_cases_and_queries_without_solver(tmp_path, vanilla):
    manager, model = fake_manager(tmp_path, vanilla)
    submitted = manager.submit(str(model), conditions(60), outputs=["total"])
    job_id = submitted["job_id"]
    try:
        wait_for(manager, job_id, lambda s: s["completed_count"] >= 2)
        with pytest.raises(AVLFailure, match="active|starting"):
            manager.resume(job_id)
        manager.cancel(job_id)
        state = finished(manager, job_id)
        assert state["state"] == "cancelled" and 0 < state["completed_count"] < 60
        job = manager.directory(job_id)
        while busy(job / "worker.lock"):
            time.sleep(0.01)
        before = {
            p.name: sha256(p)
            for p in (job / "cases").glob("*.json")
            if read_json(p)["result"]["success"]
        }
        manager.resume(job_id)
        state = finished(manager, job_id)
        assert state["state"] == "completed" and state["completed_count"] == 60, state
        assert all(sha256(job / "cases" / name) == h for name, h in before.items())
        # Result retrieval remains usable even if the executable is unavailable.
        manager.runner.executable.rename(tmp_path / "binary-offline")
        page = manager.results(job_id, limit=2, fields=["total.fields.CLtot"], indices=[1, 3, 5])
        assert [r["index"] for r in page["rows"]] == [1, 3] and page["next_offset"] == 2
        with pytest.raises(AVLFailure) as err:
            manager.results(job_id, fields=["body.derivatives.Cmq"])
        assert err.value.code == "FIELD_NOT_AVAILABLE"
    finally:
        manager.cancel(job_id)


def test_corrupt_artifact_recomputes_only_damaged_case(tmp_path, vanilla):
    manager, model = fake_manager(tmp_path, vanilla)
    job_id = manager.submit(str(model), conditions(3), outputs=["total"])["job_id"]
    assert finished(manager, job_id)["state"] == "completed"
    job = manager.directory(job_id)
    while busy(job / "worker.lock"):
        time.sleep(0.01)
    keep = sha256(job / "cases/00000.json")
    rec = read_json(job / "cases/00001.json")
    Path(rec["result"]["artifacts"]["total.mrf"]).write_text("damaged")
    manager.resume(job_id)
    assert finished(manager, job_id)["state"] == "completed"
    assert sha256(job / "cases/00000.json") == keep
    assert len(list((job / "records").glob("0002-*.json"))) == 1


@pytest.mark.parametrize(
    "change,code",
    [
        ("source", "SOURCE_CHANGED"),
        ("snapshot", "SOURCE_CHANGED"),
        ("binary", "SOLVER_CHANGED"),
        ("spec", "JOB_SPEC_CHANGED"),
    ],
)
def test_resume_rejects_changed_inputs(tmp_path, vanilla, change, code):
    manager, model = fake_manager(tmp_path, vanilla)
    job_id = manager.submit(str(model), conditions(1), outputs=["total"])["job_id"]
    assert finished(manager, job_id)["state"] == "completed"
    job = manager.directory(job_id)
    while busy(job / "worker.lock"):
        time.sleep(0.01)
    targets = {
        "source": model.parent / "sd7037.dat",
        "snapshot": job / "inputs/asset000.dat",
        "binary": manager.runner.executable,
        "spec": job / "spec.json",
    }
    target = targets[change]
    if change == "spec":
        spec = json.loads(target.read_text())
        spec["timeout_seconds"] += 1
        target.write_text(json.dumps(spec))
    else:
        target.write_text(target.read_text() + "\nchanged\n")
    with pytest.raises(AVLFailure) as err:
        manager.resume(job_id)
    assert err.value.code == code


@pytest.mark.skipif(os.name == "nt", reason="POSIX worker signal recovery")
def test_interrupted_worker_can_resume(tmp_path, vanilla):
    manager, model = fake_manager(tmp_path, vanilla)
    job_id = manager.submit(str(model), conditions(30), outputs=["total"])["job_id"]
    try:
        state = wait_for(manager, job_id, lambda s: s["completed_count"] >= 2)
        os.kill(state["worker_pid"], signal.SIGTERM)
        assert finished(manager, job_id)["state"] == "interrupted"
        manager.resume(job_id)
        assert finished(manager, job_id)["state"] == "completed"
    finally:
        manager.cancel(job_id)


def test_queued_cancellation_and_worker_limit(tmp_path, vanilla):
    manager, model = fake_manager(tmp_path, vanilla, delay=0.15)
    ids = []
    try:
        for _ in range(3):
            ids.append(manager.submit(str(model), conditions(30), outputs=["total"])["job_id"])
        end = time.monotonic() + 5
        while time.monotonic() < end:
            states = [manager.status(j) for j in ids]
            if sum(s["state"] == "running" for s in states) == 2:
                break
            time.sleep(0.025)
        assert sum(s["state"] == "running" for s in states) == 2
        queued = next(s["job_id"] for s in states if s["state"] == "queued")
        manager.cancel(queued)
        assert finished(manager, queued)["state"] == "cancelled"
    finally:
        for job_id in ids:
            manager.cancel(job_id)
        for job_id in ids:
            finished(manager, job_id)


@pytest.mark.parametrize("job_id", ["../escape", "/etc/passwd", "0" * 32])
def test_query_is_confined_to_work_root(tmp_path, job_id):
    manager = JobManager(AVLRunner("unused", str(tmp_path)))
    with pytest.raises(AVLFailure) as err:
        manager.results(job_id)
    assert err.value.code == "UNKNOWN_JOB"


def test_background_timeout_preserves_completed_cases(tmp_path, vanilla):
    manager, model = fake_manager(tmp_path, vanilla, delay=0.1)
    job_id = manager.submit(str(model), conditions(60), outputs=["total"], timeout_seconds=1.5)[
        "job_id"
    ]
    state = finished(manager, job_id)
    assert state["state"] == "failed" and state["error"]["code"] == "TIMEOUT"
    assert 0 < state["completed_count"] < 60


def test_unknown_output_fails_before_execution(tmp_path, vanilla):
    runner = AVLRunner("unused", str(tmp_path))
    with pytest.raises(AVLFailure) as err:
        runner.sweep(str(vanilla), conditions(1), outputs=["unknown"])
    assert err.value.code == "INVALID_OUTPUT_SELECTION"
    assert not (tmp_path / "runs").exists()


def test_partial_failure_restarts_process_without_reusing_failed_data(tmp_path, vanilla):
    manager, model = fake_manager(tmp_path, vanilla, delay=0)
    binary = manager.runner.executable
    code = binary.read_text().replace("for line in sys.stdin:", "counter=0\nfor line in sys.stdin:")
    code = code.replace(
        "if words[0]=='X': time.sleep(0)",
        "if words[0]=='X':\n        counter+=1\n"
        "        if counter==2: print('Flow solution is not possible',flush=True)",
    )
    binary.write_text(code)
    result = manager.runner.sweep(str(model), conditions(3), outputs=["total"], stop_on_error=False)
    assert not result["success"] and result["completed_count"] == 2
    assert [r["success"] for r in result["results"]] == [True, False, True]
    assert result["results"][1]["error"]["code"] == "SOLVER_REPORTED_ERROR"
    assert len(list(Path(result["run_directory"]).glob("batch-*"))) == 2
    assert Path(result["results"][0]["artifacts"]["result.json"]).is_file()


def test_sweep_query_pages_without_loading_aggregate(tmp_path):
    root = tmp_path / "runs/example"
    root.mkdir(parents=True)
    (root / "result.json").write_text("aggregate deliberately unavailable to the reader")
    for i in range(3):
        (root / f"case-{i:05d}.json").write_text(
            json.dumps({"success": True, "index": i, "total": {"fields": {"CLtot": i}}})
        )
    manager = JobManager(AVLRunner("unused", str(tmp_path)))
    out = manager.results("example", offset=1, limit=1, fields=["total.fields.CLtot"])
    assert out["matched_count"] == 3 and out["rows"][0]["index"] == 1
    assert out["rows"][0]["fields"]["total.fields.CLtot"] == 1
