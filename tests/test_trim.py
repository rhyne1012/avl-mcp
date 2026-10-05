"""Longitudinal trim must satisfy its targets and survive a fresh prescribed solve."""

import json
import re
import shutil
import sys
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from avl_mcp.geometry import sha256
from avl_mcp.jobs import JobManager
from avl_mcp.models import AVLFailure, TrimRequest
from avl_mcp.outputs import parse_total
from avl_mcp.runner import AVLRunner

FIXTURE = Path(__file__).parent / "fixtures/total.mrf"


def request_for(**changes):
    values = {
        "control": "elevator",
        "target_cl": 0.6,
        "target_cm": 0.0,
        "mach": 0.2,
        "alpha_bounds_deg": (-10, 15),
        "control_bounds": (-30, 30),
    }
    return TrimRequest(**(values | changes))


@pytest.mark.parametrize(
    "change",
    [
        {"control": ""},
        {"target_cl": float("nan")},
        {"target_cl": float("inf")},
        {"target_cm": float("nan")},
        {"mach": float("nan")},
        {"mach": -0.1},
        {"mach": 0.7},
        {"alpha_bounds_deg": (5, -5)},
        {"alpha_bounds_deg": (5, 5)},
        {"alpha_bounds_deg": (-31, 10)},
        {"alpha_bounds_deg": (-10, 31)},
        {"alpha_bounds_deg": (float("nan"), 10)},
        {"alpha_bounds_deg": (-10, float("inf"))},
        {"control_bounds": (30, -30)},
        {"control_bounds": (0, 0)},
        {"control_bounds": (-181, 30)},
        {"control_bounds": (-30, 181)},
        {"control_bounds": (float("nan"), 30)},
        {"control_bounds": (-30, float("inf"))},
        {"cl_tolerance": 0},
        {"cl_tolerance": float("inf")},
        {"cm_tolerance": -1},
        {"cm_tolerance": float("nan")},
        {"fixed_controls": {"flap": float("nan")}},
        {"fixed_controls": {"flap": 181}},
        {"alpha_deg": 2},
        {"beta_deg": 1},
        {"qc_2v": 0.01},
    ],
)
def test_trim_request_rejects_invalid_or_unspecified_degrees_of_freedom(change):
    with pytest.raises(ValidationError):
        request_for(**change)


@pytest.mark.parametrize(
    "name", ["control", "target_cl", "mach", "alpha_bounds_deg", "control_bounds"]
)
def test_trim_safety_inputs_are_explicit(name):
    values = request_for().model_dump()
    del values[name]
    with pytest.raises(ValidationError):
        TrimRequest(**values)


@pytest.mark.parametrize(
    "change",
    [
        {"control": "missing_elevator"},
        {"fixed_controls": {"missing_flap": 1}},
        {"control": "elevator\nQUIT"},
        {"fixed_controls": {"flap\nQUIT": 0}},
    ],
)
def test_trim_unknown_control_fails_before_execution(tmp_path, vanilla, change):
    engine = AVLRunner("/not-a-binary", str(tmp_path))
    with pytest.raises(AVLFailure) as exc:
        engine.trim(str(vanilla), request_for(**change))
    assert exc.value.code == "UNKNOWN_CONTROL"
    assert not (tmp_path / "runs").exists()


def test_trim_control_cannot_also_be_fixed(tmp_path, vanilla):
    engine = AVLRunner("/not-a-binary", str(tmp_path))
    with pytest.raises(AVLFailure) as exc:
        engine.trim(str(vanilla), request_for(fixed_controls={"elevator": 0}))
    assert exc.value.code == "TRIM_CONTROL_CONFLICT"
    assert not (tmp_path / "runs").exists()


def assert_accepted(result, request):
    assert result["success"], result
    assert result["analysis_type"] == "longitudinal_trim"
    trim = result["trim"]
    assert trim["schema_version"] == "1.0.0"
    assert trim["accepted"] and trim["within_bounds"]
    assert trim["verification"]["status"] == "passed"
    assert abs(trim["residuals"]["CL"]) <= request.cl_tolerance
    assert abs(trim["residuals"]["Cm"]) <= request.cm_tolerance
    fields = result["total"]["fields"]
    assert fields["CLtot"] == pytest.approx(request.target_cl, abs=request.cl_tolerance)
    assert fields["Cmtot"] == pytest.approx(request.target_cm, abs=request.cm_tolerance)
    for name in ("Beta", "pb/2V", "qc/2V", "rb/2V"):
        assert fields[name] == pytest.approx(0, abs=1e-12)
    assert fields["Mach"] == pytest.approx(request.mach, abs=1e-12)
    condition = result["condition"]
    assert trim["solved_condition"] == condition
    assert request.alpha_bounds_deg[0] <= condition["alpha_deg"] <= request.alpha_bounds_deg[1]
    assert request.control_bounds[0] <= condition["controls"][request.control]
    assert condition["controls"][request.control] <= request.control_bounds[1]
    assert all(Path(path).is_file() for path in result["artifacts"].values())
    assert result["source_preserved"]


@pytest.mark.native
@pytest.mark.parametrize("fixture_name,target_cm", [("vanilla", 0), ("bd", 0), ("vanilla", 0.03)])
def test_official_trim_is_verified_and_preserves_sources(runner, fixture_name, target_cm, request):
    model = request.getfixturevalue(fixture_name)
    before = {p: sha256(p) for p in model.parent.iterdir() if p.is_file()}
    target = request_for(target_cm=target_cm)
    result = runner.trim(str(model), target, case_name=f"trim-{fixture_name}")
    assert_accepted(result, target)
    assert all(sha256(path) == checksum for path, checksum in before.items())
    # A separate process must receive fixed alpha and CONTROL commands for verification.
    verification = result["trim"]["verification"]
    folder = Path(verification["run_directory"])
    assert folder != Path(result["run_directory"])
    assert (folder / "commands.txt").is_file()
    verified = parse_total(folder / "total.mrf")
    assert verified["fields"]["CLtot"] == pytest.approx(target.target_cl, abs=target.cl_tolerance)
    assert verified["fields"]["Cmtot"] == pytest.approx(target.target_cm, abs=target.cm_tolerance)


@pytest.mark.native
def test_trim_reference_override_and_fixed_flap(runner, vanilla):
    from avl_mcp.models import References

    references = References(sref=10, cref=1, bref=11, xref=0.6, yref=0, zref=0.03)
    target = request_for(fixed_controls={"flap": 3})
    result = runner.trim(str(vanilla), target, references=references, length_unit="m")
    assert_accepted(result, target)
    fields = result["total"]["fields"]
    for name, value in references.model_dump().items():
        assert fields[name[0].upper() + name[1:]] == value
    assert result["total"]["controls"]["flap"] == 3
    assert result["total"]["controls"]["aileron"] == 0
    assert result["total"]["controls"]["rudder"] == 0
    assert result["geometry_length_unit"] == "m"


@pytest.mark.native
@pytest.mark.parametrize("gain", [2, -2])
def test_trim_control_values_respect_section_gain(runner, vanilla, tmp_path, gain):
    base = runner.trim(str(vanilla), request_for(), outputs=["total"], case_name="unit-gain")
    assert_accepted(base, request_for())
    model_folder = tmp_path / f"gain-{gain}"
    shutil.copytree(vanilla.parent, model_folder)
    model = model_folder / vanilla.name
    text, count = re.subn(r"(elevator\s+)1\.0", rf"\g<1>{gain:.1f}", model.read_text())
    assert count == 2
    model.write_text(text)
    changed = runner.trim(str(model), request_for(), outputs=["total"], case_name="scaled-gain")
    assert_accepted(changed, request_for())
    assert changed["condition"]["alpha_deg"] == pytest.approx(
        base["condition"]["alpha_deg"], abs=1e-7
    )
    assert gain * changed["condition"]["controls"]["elevator"] == pytest.approx(
        base["condition"]["controls"]["elevator"], abs=1e-7
    )


@pytest.mark.native
def test_native_trim_rejects_insufficient_control_authority(runner, vanilla, tmp_path):
    folder = tmp_path / "no-authority"
    shutil.copytree(vanilla.parent, folder)
    model = folder / vanilla.name
    model.write_text(re.sub(r"(elevator\s+)1\.0", r"\g<1>0.0", model.read_text()))
    result = runner.trim(str(model), request_for(), outputs=["total"])
    assert not result["success"]
    assert result["trim"]["accepted"] is False
    assert result["error"]["code"] in {"SOLVER_REPORTED_ERROR", "TRIM_RESIDUAL"}
    assert (Path(result["run_directory"]) / "stdout.log").is_file()


def fake_binary(tmp_path, body):
    path = tmp_path / "fake-avl"
    path.write_text(f"#!{sys.executable}\n" + body)
    path.chmod(0o755)
    return str(path)


def fake_totals_runner(tmp_path, totals):
    """Return one genuine fixture (possibly damaged) per newly launched AVL process."""
    counter = tmp_path / "process-count"
    body = (
        "import sys\nfrom pathlib import Path\nsys.stdin.read()\n"
        f"counter = Path({str(counter)!r})\n"
        "index = int(counter.read_text()) if counter.exists() else 0\n"
        "counter.write_text(str(index + 1))\n"
        f"outputs = {totals!r}\n"
        "Path('total.mrf').write_text(outputs[min(index, len(outputs) - 1)])\n"
        "print('Athena Vortex Lattice Program Version 3.52')\n"
    )
    return AVLRunner(fake_binary(tmp_path, body), str(tmp_path)), counter


def fixture_request(**changes):
    fields = parse_total(FIXTURE)["fields"]
    return request_for(target_cl=fields["CLtot"], target_cm=fields["Cmtot"], **changes)


def assert_saved_failure(result, code):
    assert not result["success"], result
    assert result["error"]["code"] == code, result
    assert result["analysis_type"] == "longitudinal_trim"
    assert result["trim"]["accepted"] is False
    folder = Path(result["run_directory"])
    assert json.loads((folder / "result.json").read_text()) == result
    assert (folder / "manifest.json").is_file()
    assert (folder / "commands.txt").is_file()
    assert (folder / "stdout.log").is_file()


def test_trim_runs_fresh_verification_and_results_remain_queryable(tmp_path, vanilla):
    engine, count = fake_totals_runner(tmp_path, [FIXTURE.read_text()])
    target = fixture_request()
    result = engine.trim(str(vanilla), target, outputs=["total"])
    assert_accepted(result, target)
    assert count.read_text() == "2"
    manager = JobManager(engine)
    for fields in (None, ["trim.residuals.CL", "condition.alpha_deg"]):
        page = manager.results(result["job_id"], fields=fields)
        assert page["solver_executed"] is False
        row = page["rows"][0]
        assert row["analysis_type"] == "longitudinal_trim"
        assert row["trim"] == result["trim"]
        if fields:
            assert row["fields"]["trim.residuals.CL"] == 0
            assert row["fields"]["condition.alpha_deg"] == 3
    assert count.read_text() == "2"


@pytest.mark.parametrize(
    "changes", [{"alpha_bounds_deg": (-1, 1)}, {"control_bounds": (1, 10)}]
)
def test_trim_rejects_bounds_without_clamping_solution(tmp_path, vanilla, changes):
    engine, count = fake_totals_runner(tmp_path, [FIXTURE.read_text()])
    result = engine.trim(str(vanilla), fixture_request(**changes), outputs=["total"])
    assert_saved_failure(result, "TRIM_OUT_OF_BOUNDS")
    assert result["trim"]["within_bounds"] is False
    assert result["trim"]["solved_condition"]["alpha_deg"] == 3
    assert result["trim"]["solved_condition"]["controls"]["elevator"] == 0
    assert count.read_text() == "1"


@pytest.mark.parametrize("field", ["target_cl", "target_cm"])
def test_trim_rejects_residual_even_after_zero_exit(tmp_path, vanilla, field):
    engine, count = fake_totals_runner(tmp_path, [FIXTURE.read_text()])
    values = fixture_request().model_dump()
    values[field] += 0.01
    result = engine.trim(str(vanilla), TrimRequest(**values), outputs=["total"])
    assert_saved_failure(result, "TRIM_RESIDUAL")
    assert count.read_text() == "1"


@pytest.mark.parametrize(
    "label,index,value",
    [
        ("Beta, qc/2V", 0, 1),
        ("Beta, qc/2V", 1, 0.01),
        ("Alpha, pb/2V, p'b/2V", 1, 0.01),
        ("Mach, rb/2V, r'b/2V", 0, 0.3),
        ("Mach, rb/2V, r'b/2V", 1, 0.01),
        ("Xref, Yref, Zref", 0, 0.75),
    ],
)
def test_trim_rejects_changed_fixed_conditions(tmp_path, vanilla, label, index, value):
    lines = FIXTURE.read_text().splitlines()
    row = next(i for i, line in enumerate(lines) if "| " + label == line[line.find("|") :])
    values, marker = lines[row].split("|", 1)
    values = values.split()
    values[index] = str(value)
    lines[row] = " ".join(values) + " |" + marker
    engine, count = fake_totals_runner(tmp_path, ["\n".join(lines) + "\n"])
    result = engine.trim(str(vanilla), fixture_request(), outputs=["total"])
    assert_saved_failure(result, "CONDITION_MISMATCH")
    assert count.read_text() == "1"


def test_trim_rejects_changed_fixed_control(tmp_path, vanilla):
    changed = FIXTURE.read_text().replace("0.000000000000000E+00  flap", "1.0  flap")
    engine, count = fake_totals_runner(tmp_path, [changed])
    result = engine.trim(str(vanilla), fixture_request(), outputs=["total"])
    assert_saved_failure(result, "CONDITION_MISMATCH")
    assert count.read_text() == "1"


def test_trim_fails_when_independent_recomputation_disagrees(tmp_path, vanilla):
    changed = FIXTURE.read_text().replace("6.754170403902354E-01", "7.754170403902354E-01")
    engine, count = fake_totals_runner(tmp_path, [FIXTURE.read_text(), changed])
    result = engine.trim(str(vanilla), fixture_request(), outputs=["total"])
    assert_saved_failure(result, "TRIM_VERIFICATION_FAILED")
    assert count.read_text() == "2"
    assert result["trim"]["verification"]["status"] == "failed"


@pytest.mark.parametrize(
    "damage",
    [
        lambda text: text.replace("  0.000000000000000E+00  elevator", "  NaN  elevator"),
        lambda text: text.replace("  0.000000000000000E+00  elevator", "  0  wrong_elevator"),
        lambda text: text[: text.index("DESIGN")],
    ],
)
def test_trim_never_accepts_malformed_total_output(tmp_path, vanilla, damage):
    engine, count = fake_totals_runner(tmp_path, [damage(FIXTURE.read_text())])
    result = engine.trim(str(vanilla), fixture_request(), outputs=["total"])
    assert not result["success"]
    assert result["trim"]["accepted"] is False
    folder = Path(result["run_directory"])
    assert json.loads((folder / "result.json").read_text()) == result
    assert (folder / "total.mrf").is_file()
    assert count.read_text() == "1"


def test_trim_timeout_retains_request_and_failure_evidence(tmp_path, vanilla):
    binary = fake_binary(tmp_path, "import sys,time\nsys.stdin.read()\ntime.sleep(20)\n")
    engine = AVLRunner(binary, str(tmp_path))
    started = time.monotonic()
    result = engine.trim(str(vanilla), request_for(), timeout_seconds=0.1)
    assert time.monotonic() - started < 5
    assert_saved_failure(result, "TIMEOUT")
    assert result["trim"]["request"]["target_cl"] == 0.6


def test_trim_verification_timeout_retains_unaccepted_candidate(tmp_path, vanilla):
    counter = tmp_path / "process-count"
    body = (
        "import sys,time\nfrom pathlib import Path\nsys.stdin.read()\n"
        f"counter = Path({str(counter)!r})\n"
        "index = int(counter.read_text()) if counter.exists() else 0\n"
        "counter.write_text(str(index + 1))\n"
        "if index: time.sleep(20)\n"
        f"Path('total.mrf').write_text({FIXTURE.read_text()!r})\n"
        "print('Athena Vortex Lattice Program Version 3.52')\n"
    )
    engine = AVLRunner(fake_binary(tmp_path, body), str(tmp_path))
    started = time.monotonic()
    result = engine.trim(str(vanilla), fixture_request(), outputs=["total"], timeout_seconds=1)
    assert time.monotonic() - started < 5
    assert_saved_failure(result, "TIMEOUT")
    assert counter.read_text() == "2"
    assert result["trim"]["solved_condition"] is not None
    assert result["trim"]["verification"]["status"] == "failed"
    candidate = Path(result["run_directory"]) / "candidate.json"
    assert json.loads(candidate.read_text())["total"]["fields"]["CLtot"] > 0


@pytest.mark.native
@pytest.mark.parametrize("after_process,filename", [(1, "vanilla.avl"), (2, "sd7037.dat")])
def test_trim_rejects_source_changes_during_execution(
    runner, vanilla, tmp_path, monkeypatch, after_process, filename
):
    model_folder = tmp_path / "changing-input"
    shutil.copytree(vanilla.parent, model_folder)
    model = model_folder / vanilla.name
    source = model_folder / filename
    original_bytes = source.read_bytes()
    process = runner._process
    process_count = 0

    def change_source_after_process(directory, commands, timeout):
        nonlocal process_count
        result = process(directory, commands, timeout)
        process_count += 1
        if process_count == after_process:
            source.write_bytes(original_bytes + b"\n# changed during trim test\n")
        return result

    monkeypatch.setattr(runner, "_process", change_source_after_process)
    result = runner.trim(str(model), request_for(), outputs=["total"])
    assert_saved_failure(result, "SOURCE_CHANGED")
    assert not result.get("source_preserved", False)
    assert process_count == after_process
    assert source.read_bytes() != original_bytes
    folder = Path(result["run_directory"])
    manifest = json.loads((folder / "manifest.json").read_text())
    staged_name = (
        "model.avl" if source == model else manifest["dependency_mapping"][str(source)]
    )
    assert (folder / "originals" / staged_name).read_bytes() == original_bytes
    assert (folder / "candidate.json").is_file()
    if after_process == 1:
        assert result["trim"]["verification"]["status"] == "not_run"
    else:
        assert result["trim"]["verification"]["status"] == "failed"
        assert (folder / "verification" / "originals" / staged_name).read_bytes() == original_bytes


def test_trim_native_nonconvergence_message_is_failure(tmp_path, vanilla):
    body = (
        "import sys\nsys.stdin.read()\n"
        "print('Athena Vortex Lattice Program Version 3.52')\n"
        "print('Trim convergence failed')\n"
    )
    engine = AVLRunner(fake_binary(tmp_path, body), str(tmp_path))
    result = engine.trim(str(vanilla), request_for())
    assert_saved_failure(result, "SOLVER_REPORTED_ERROR")
