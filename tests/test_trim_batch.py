"""Batch trim retains single-point acceptance through cancellation and resume."""

import csv
import json
import shutil
import sys
import time
from pathlib import Path

import pytest

from avl_mcp.geometry import sha256
from avl_mcp.jobs import JobManager, busy, digest, read_json
from avl_mcp.models import AVLFailure, FlightCondition, References, TrimRequest
from avl_mcp.outputs import parse_total
from avl_mcp.runner import AVLRunner

FIXTURE = Path(__file__).parent / "fixtures/total.mrf"


def request_for(**changes):
    fields = parse_total(FIXTURE)["fields"]
    return TrimRequest(
        **(
            {
                "control": "elevator",
                "target_cl": fields["CLtot"],
                "target_cm": fields["Cmtot"],
                "mach": 0.2,
                "alpha_bounds_deg": (-10, 15),
                "control_bounds": (-30, 30),
            }
            | changes
        )
    )


def fake_manager(tmp_path, vanilla, delay=0, pause_index=None):
    """A real subprocess with deterministic output and an optional one-time pause."""
    count, paused = tmp_path / "count", tmp_path / "paused"
    binary = tmp_path / "fake-avl"
    binary.write_text(f"""#!{sys.executable}
import sys,time
from pathlib import Path
sys.stdin.read()
count=Path({str(count)!r})
index=int(count.read_text()) if count.exists() else 0
count.write_text(str(index+1))
paused=Path({str(paused)!r})
print('Athena Vortex Lattice Program Version 3.52', flush=True)
if index=={pause_index!r} and not paused.exists():
    paused.write_text(str(Path.cwd()))
    time.sleep(20)
time.sleep({delay!r})
Path('total.mrf').write_text({FIXTURE.read_text()!r})
""")
    binary.chmod(0o755)
    shutil.copytree(vanilla.parent, tmp_path / "model")
    return (
        JobManager(AVLRunner(str(binary), str(tmp_path / "work"))),
        tmp_path / "model/vanilla.avl",
        count,
        paused,
    )


def wait_for(manager, job_id, predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = manager.status(job_id)
        if predicate(state):
            return state
        time.sleep(0.02)
    raise AssertionError(state)


def finished(manager, job_id):
    state = wait_for(
        manager,
        job_id,
        lambda state: state["state"] in {"completed", "failed", "cancelled", "interrupted"},
    )
    deadline = time.monotonic() + 5
    while busy(manager.directory(job_id) / "worker.lock"):
        assert time.monotonic() < deadline
        time.sleep(0.01)
    return state


@pytest.mark.parametrize("method", ["sweep", "submit"])
@pytest.mark.parametrize("selector", ["neither", "both", "empty", "empty_conditions"])
def test_exactly_one_nonempty_batch_selector_before_execution(tmp_path, vanilla, method, selector):
    engine = AVLRunner("/not-a-binary", str(tmp_path))
    target = engine if method == "sweep" else JobManager(engine)
    kwargs = {
        "neither": {},
        "both": {"conditions": [FlightCondition()], "trim_requests": [request_for()]},
        "empty": {"trim_requests": []},
        "empty_conditions": {"conditions": [], "trim_requests": [request_for()]},
    }[selector]
    with pytest.raises(AVLFailure) as exc:
        getattr(target, method)(str(vanilla), **kwargs)
    assert exc.value.code == ("SWEEP_LIMIT" if selector == "empty" else "BATCH_INPUT_REQUIRED")
    assert not (tmp_path / "runs").exists() and not (tmp_path / "jobs").exists()


@pytest.mark.parametrize("method", ["sweep", "submit"])
@pytest.mark.parametrize(
    "changes,code",
    [
        ({"control": "unknown"}, "UNKNOWN_CONTROL"),
        ({"fixed_controls": {"elevator": 1}}, "TRIM_CONTROL_CONFLICT"),
    ],
)
def test_every_trim_request_is_validated_before_execution(tmp_path, vanilla, method, changes, code):
    engine = AVLRunner("/not-a-binary", str(tmp_path))
    target = engine if method == "sweep" else JobManager(engine)
    with pytest.raises(AVLFailure) as exc:
        getattr(target, method)(
            str(vanilla),
            trim_requests=[request_for(), request_for(**changes)],
        )
    assert exc.value.code == code
    assert not (tmp_path / "runs").exists() and not (tmp_path / "jobs").exists()


@pytest.mark.parametrize("stop_on_error", [True, False])
def test_trim_partial_failure_retains_requests_order_and_csv(tmp_path, vanilla, stop_on_error):
    manager, model, count, _ = fake_manager(tmp_path, vanilla)
    requests = [request_for(), request_for(alpha_bounds_deg=(-1, 1)), request_for()]
    before = {p: sha256(p) for p in model.parent.iterdir() if p.is_file()}
    batch = manager.runner.sweep(
        str(model),
        trim_requests=requests,
        outputs=["total"],
        stop_on_error=stop_on_error,
    )
    assert not batch["success"] and batch["error"]["code"] == "TRIM_OUT_OF_BOUNDS"
    expected = [True, False] if stop_on_error else [True, False, True]
    assert [item["success"] for item in batch["results"]] == expected
    assert [item["index"] for item in batch["results"]] == list(range(len(expected)))
    assert int(count.read_text()) == (3 if stop_on_error else 5)
    folder = Path(batch["run_directory"])
    assert (folder / "inputs/originals/model.avl").read_bytes() == model.read_bytes()
    assert all(sha256(path) == checksum for path, checksum in before.items())
    for item, request in zip(batch["results"], requests, strict=False):
        assert item["trim"]["request"] == request.model_dump(mode="json")
        assert Path(item["run_directory"]).is_relative_to(folder)
        assert read_json(Path(item["artifacts"]["result.json"])) == item
        if item["success"]:
            assert item["trim"]["verification"]["status"] == "passed"
            assert item["outputs"] == ["total"] and "result_context" in item
    rows = list(csv.DictReader(Path(batch["summary_csv"]).open()))
    assert rows[0]["verification_status"] == "passed"
    assert float(rows[0]["alpha_deg"]) == 3 and float(rows[0]["control_value"]) == 0
    assert float(rows[0]["residual_cl"]) == 0 and float(rows[0]["verified_residual_cm"]) == 0
    assert rows[1]["error_code"] == "TRIM_OUT_OF_BOUNDS"
    # Paging uses the atomic per-case records, even when aggregate JSON is unavailable.
    (folder / "result.json").write_text("aggregate deliberately unavailable")
    page = manager.results(batch["job_id"], limit=1, offset=1)
    assert page["rows"][0]["index"] == 1
    assert page["rows"][0]["trim"]["request"] == requests[1].model_dump(mode="json")
    assert page["solver_executed"] is False


def test_batch_budget_can_exceed_single_trim_cap(tmp_path, vanilla):
    manager, model, _, _ = fake_manager(tmp_path, vanilla)
    with pytest.raises(AVLFailure) as exc:
        manager.runner.trim(str(model), request_for(), timeout_seconds=601)
    assert exc.value.code == "INVALID_TIMEOUT"
    batch = manager.runner.sweep(
        str(model),
        trim_requests=[request_for()],
        timeout_seconds=601,
        outputs=["total"],
    )
    assert batch["success"], batch


def test_whole_batch_timeout_stops_even_when_continuing_on_error(tmp_path, vanilla):
    manager, model, _, _ = fake_manager(tmp_path, vanilla, delay=0.1)
    started = time.monotonic()
    batch = manager.runner.sweep(
        str(model),
        trim_requests=[request_for()] * 8,
        timeout_seconds=0.65,
        stop_on_error=False,
        outputs=["total"],
    )
    assert time.monotonic() - started < 3
    assert batch["error"]["code"] == "TIMEOUT"
    assert 0 < batch["completed_count"] < batch["attempted_count"] < 8
    failed = batch["results"][-1]
    assert failed["trim"]["accepted"] is False
    assert failed["trim"]["request"] == request_for().model_dump(mode="json")


@pytest.mark.parametrize("pause_index", [2, 3])
def test_cancel_during_candidate_or_verification_and_resume(tmp_path, vanilla, pause_index):
    manager, model, _, paused = fake_manager(tmp_path, vanilla, pause_index=pause_index)
    submitted = manager.submit(
        str(model),
        trim_requests=[request_for()] * 4,
        outputs=["total"],
        stop_on_error=False,
    )
    job_id = submitted["job_id"]
    assert submitted["analysis_type"] == "longitudinal_trim_batch"
    try:
        wait_for(manager, job_id, lambda state: paused.exists())
        assert (Path(paused.read_text()).name == "verification") == (pause_index == 3)
        started = time.monotonic()
        manager.cancel(job_id)
        state = finished(manager, job_id)
        assert time.monotonic() - started < 3
        assert state["state"] == "cancelled" and state["completed_count"] == 1
        job = manager.directory(job_id)
        kept = sha256(job / "cases/00000.json")
        result = read_json(job / "result.json")
        assert result["results"][1]["error"]["code"] == "CANCELLED"
        trim = result["results"][1]["trim"]
        assert trim["accepted"] is False
        assert trim["verification"]["status"] == ("failed" if pause_index == 3 else "not_run")
        manager.resume(job_id)
        state = finished(manager, job_id)
        assert state["state"] == "completed" and state["completed_count"] == 4, state
        assert sha256(job / "cases/00000.json") == kept
        assert Path(state["summary_csv"]).is_file()
        page = manager.results(job_id, indices=[3, 1], limit=1, fields=["trim.residuals.CL"])
        assert page["rows"][0]["index"] == 1 and page["next_offset"] == 1
        assert page["rows"][0]["trim"]["verification"]["status"] == "passed"
        assert page["solver_executed"] is False
    finally:
        manager.cancel(job_id)
        finished(manager, job_id)


@pytest.mark.parametrize("damage", ["candidate", "verification", "metadata"])
def test_resume_reuses_only_verified_intact_results(tmp_path, vanilla, damage):
    manager, model, _, _ = fake_manager(tmp_path, vanilla)
    job_id = manager.submit(
        str(model),
        trim_requests=[request_for()] * 3,
        outputs=["total"],
    )["job_id"]
    assert finished(manager, job_id)["state"] == "completed"
    job = manager.directory(job_id)
    keep = sha256(job / "cases/00000.json")
    record = read_json(job / "cases/00001.json")
    if damage == "metadata":
        record["result"]["trim"]["verification"]["status"] = "not_run"
        record["result_sha256"] = digest(record["result"])
        (job / "cases/00001.json").write_text(json.dumps(record))
    else:
        key = "total.mrf" if damage == "candidate" else "verification/total.mrf"
        Path(record["result"]["artifacts"][key]).write_text("damaged")
    manager.resume(job_id)
    assert finished(manager, job_id)["state"] == "completed"
    assert sha256(job / "cases/00000.json") == keep
    assert len(list((job / "records").glob("0002-*.json"))) == 1
    assert (
        read_json(job / "cases/00001.json")["result"]["trim"]["verification"]["status"] == "passed"
    )


@pytest.mark.parametrize(
    "change,code",
    [
        ("source", "SOURCE_CHANGED"),
        ("snapshot", "SOURCE_CHANGED"),
        ("request", "JOB_SPEC_CHANGED"),
        ("settings", "JOB_SPEC_CHANGED"),
    ],
)
def test_resume_rejects_changed_trim_inputs(tmp_path, vanilla, change, code):
    manager, model, _, _ = fake_manager(tmp_path, vanilla)
    job_id = manager.submit(str(model), trim_requests=[request_for()], outputs=["total"])["job_id"]
    assert finished(manager, job_id)["state"] == "completed"
    job = manager.directory(job_id)
    if change in {"request", "settings"}:
        spec = read_json(job / "spec.json")
        if change == "request":
            spec["trim_requests"][0]["target_cl"] += 0.1
        else:
            spec["length_unit"] = "m"
        (job / "spec.json").write_text(json.dumps(spec))
    else:
        path = model if change == "source" else job / "inputs/model.avl"
        path.write_text(path.read_text() + "\nchanged\n")
    with pytest.raises(AVLFailure) as exc:
        manager.resume(job_id)
    assert exc.value.code == code


@pytest.mark.native
def test_batch_trim_matches_independent_single_points_and_preserves_overrides(runner, vanilla):
    requests = [
        request_for(target_cl=0.6, target_cm=0, mach=mach, fixed_controls={"flap": 2})
        for mach in (0.2, 0.1, 0.3)
    ]
    refs = References(sref=10, cref=1, bref=11, xref=0.6, yref=0, zref=0.03)
    batch = runner.sweep(str(vanilla), references=refs, trim_requests=requests, length_unit="m")
    assert batch["success"], batch
    assert [item["index"] for item in batch["results"]] == [0, 1, 2]
    for item, request in zip(batch["results"], requests, strict=True):
        single = runner.trim(str(vanilla), request, references=refs, length_unit="m")
        assert single["success"], single
        assert item["total"]["fields"] == pytest.approx(single["total"]["fields"], abs=1e-10)
        for name in ("stability", "body"):
            assert item[name]["derivatives"] == pytest.approx(
                single[name]["derivatives"], abs=1e-10
            )
        assert item["trim"]["verification"]["status"] == "passed"
        assert item["condition"]["controls"]["flap"] == 2
        manifest = read_json(Path(item["artifacts"]["manifest.json"]))
        assert manifest["references_override"] == refs.model_dump()
        assert item["geometry_length_unit"] == "m" and "result_context" in item


@pytest.mark.native
def test_background_trim_reference_overrides_and_complete_resume(runner, vanilla):
    manager = JobManager(runner)
    refs = References(sref=10, cref=1, bref=11, xref=0.6, yref=0, zref=0.03)
    requests = [request_for(target_cl=cl, target_cm=0) for cl in (0.4, 0.7)]
    job_id = manager.submit(
        str(vanilla),
        trim_requests=requests,
        references=refs,
        outputs=["total"],
        length_unit="m",
    )["job_id"]
    state = finished(manager, job_id)
    assert state["state"] == "completed", state
    job = manager.directory(job_id)
    before = {p.name: sha256(p) for p in (job / "cases").glob("*.json")}
    result = read_json(job / "result.json")
    for item in result["results"]:
        assert item["total"]["fields"]["Sref"] == refs.sref
        manifest = read_json(Path(item["artifacts"]["manifest.json"]))
        assert manifest["references_override"] == refs.model_dump()
    # A fully verified resume regenerates aggregate/CSV without executing any solver.
    (job / "summary.csv").unlink()
    manager.resume(job_id)
    assert manager.status(job_id)["attempt"] == 1
    assert Path(manager.status(job_id)["summary_csv"]).is_file()
    assert all(sha256(job / "cases" / name) == value for name, value in before.items())
